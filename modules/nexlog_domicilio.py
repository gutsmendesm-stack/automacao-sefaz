"""
Modulo para operacoes de entrega a domicilio no Nexlog.
Funcionalidades portadas do GOL PRO (Igor) e adaptadas ao estilo do projeto.

Funcoes:
- Consultar data de vencimento de AWBs
- Vencendo Hoje: abrir listas de entrega, extrair AWBs + motorista
- Coletas/Entregas: marcar CTes para coleta e finalizar
- Liberacao: marcar CTes e confirmar liberacao de retencao
"""

import re
import logging
from typing import List, Dict, Optional, Set
from dataclasses import dataclass, field

from selenium.webdriver.common.by import By
from selenium.webdriver.common.keys import Keys
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.common.exceptions import TimeoutException, NoSuchElementException

from config import config, pausa
from modules.browser import NexlogBrowser

logger = logging.getLogger(__name__)


# ============================================================
# MODELOS DE DADOS
# ============================================================

@dataclass
class ResultadoVencimento:
    """Resultado da consulta de vencimento de uma AWB."""
    awb: str = ""
    data_vencimento: str = "N/A"
    status_carga: str = "N/A"
    erro: str = ""


@dataclass
class ResultadoLista:
    """Resultado da consulta de uma lista de entrega (Vencendo Hoje)."""
    numero_lista: str = ""
    motorista: str = ""
    awbs: List[str] = field(default_factory=list)


# ============================================================
# CLASSE PRINCIPAL
# ============================================================

class NexlogDomicilio:
    """Operacoes de entrega a domicilio no Nexlog."""

    def __init__(self, browser: NexlogBrowser):
        self.browser = browser
        self.driver = browser.driver
        self.wait = browser.wait

    # ============================================================
    # CONSULTA DE VENCIMENTO
    # ============================================================

    def consultar_vencimento(self, awb: str) -> ResultadoVencimento:
        """
        Consulta a data de vencimento e status de uma AWB.
        Usa a busca rapida (quickSearch > quickTracking) e abre os detalhes.

        Fluxo:
        1. Busca rapida pelo AWB
        2. Clica no checkbox/icone de detalhes (exibe info adicional)
        3. Le a data de vencimento e o status da carga

        Returns:
            ResultadoVencimento com data e status
        """
        resultado = ResultadoVencimento(awb=awb)

        try:
            self.browser.voltar_aba_principal()
            self.browser._fechar_modais()
            pausa(0.5)

            # Busca rapida
            campo = self.wait.until(
                EC.visibility_of_element_located((By.ID, "quickSearch"))
            )
            campo.click()
            campo.send_keys(Keys.CONTROL, "a")
            campo.send_keys(Keys.BACKSPACE)
            pausa(0.3)
            campo.send_keys(awb)
            self.driver.find_element(By.ID, "quickTracking-icon").click()
            pausa(1.5)

            # Clica no icone de detalhes (abre painel com data de vencimento)
            xpath_detalhes = (
                "/html/body/div[6]/div/div/div[2]/div[1]/div/div[1]/div[1]"
                "/div[1]/div/div/div[1]/div/div[1]/div[2]/fieldset/div[1]/div/label/i"
            )
            try:
                self.wait.until(EC.element_to_be_clickable((By.XPATH, xpath_detalhes))).click()
                pausa(1.5)
            except TimeoutException:
                # Tenta alternativa: qualquer icone/label clicavel na area de detalhes
                try:
                    self.driver.find_element(By.XPATH,
                        "//fieldset//label//i | //fieldset//div[contains(@class,'check')]//i"
                    ).click()
                    pausa(1.5)
                except Exception:
                    pass

            # Le status da carga
            try:
                xpath_status = (
                    "/html/body/div[6]/div/div/div[2]/div[1]/div/div[1]/div[1]"
                    "/div[1]/div/div/div[1]/div/div[2]/div/p"
                )
                status_el = self.driver.find_element(By.XPATH, xpath_status)
                resultado.status_carga = status_el.text.strip()
            except Exception:
                # Fallback: busca por texto de status no modal
                try:
                    modal_text = self.driver.find_element(By.XPATH,
                        "//div[contains(@class,'modal')]//p[contains(@class,'text')]"
                    ).text.strip()
                    if modal_text:
                        resultado.status_carga = modal_text
                except Exception:
                    pass

            # Le data de vencimento
            xpath_data = (
                "/html/body/div[7]/div/div/div[2]/div[1]/div/form/div[2]"
                "/fieldset/div[1]/div[2]/div/p"
            )
            try:
                data_el = self.wait.until(
                    EC.visibility_of_element_located((By.XPATH, xpath_data))
                )
                resultado.data_vencimento = data_el.text.strip()
            except TimeoutException:
                # Fallback: busca em qualquer elemento que contenha data
                try:
                    elementos = self.driver.find_elements(By.XPATH,
                        "//p[contains(text(),'/')]"
                    )
                    for el in elementos:
                        texto = el.text.strip()
                        if re.match(r'\d{2}/\d{2}/\d{4}', texto):
                            resultado.data_vencimento = texto
                            break
                except Exception:
                    pass

            logger.info(
                f"AWB {awb}: Vencimento={resultado.data_vencimento} | "
                f"Status={resultado.status_carga}"
            )

        except Exception as e:
            resultado.erro = str(e)[:100]
            logger.error(f"Erro ao consultar vencimento AWB {awb}: {e}")

        return resultado

    # ============================================================
    # VENCENDO HOJE (por Lista)
    # ============================================================

    def preparar_vencendo_hoje(self, data_ini: str, data_fim: str):
        """
        Prepara a tela 'Vencendo Hoje' (aba de vencimentos em Coletas/Entregas).
        Configura filtros: Domicilio, datas, desmarca checkboxes desnecessarios.

        Args:
            data_ini: Data inicial (DDMMAAAA)
            data_fim: Data final (DDMMAAAA)
        """
        self.browser.navegar_coletas_entregas()
        pausa(2)

        try:
            # Clica na aba de vencimentos (segunda aba)
            xpath_aba = (
                "/html/body/div[2]/div[2]/div[1]/div[2]/div/div/div/ul/li[2]/a"
            )
            self.wait.until(EC.element_to_be_clickable((By.XPATH, xpath_aba))).click()
            pausa(2)

            # Seleciona "Domicilio" no dropdown
            xpath_dropdown = (
                "/html/body/div[2]/div[2]/div[1]/div[2]/div/div/div/div/div[2]"
                "/div[1]/div[2]/div/div/div[1]/div/div[2]/fieldset/form/div/div"
                "/div/div[1]/div[2]/div/span/span[1]/span/span[1]/span"
            )
            try:
                self.wait.until(EC.element_to_be_clickable((By.XPATH, xpath_dropdown))).click()
                pausa(1)
                from selenium.webdriver.common.action_chains import ActionChains
                ActionChains(self.driver).send_keys("Domicilio").perform()
                pausa(1)
                ActionChains(self.driver).send_keys(Keys.ENTER).perform()
                pausa(1)
            except Exception as e:
                logger.warning(f"Falha ao selecionar Domicilio no dropdown: {e}")

            # Preenche data inicial
            xpath_data_ini = (
                "/html/body/div[2]/div[2]/div[1]/div[2]/div/div/div/div/div[2]"
                "/div[1]/div[2]/div/div/div[1]/div/div[2]/fieldset/form/div/div"
                "/div/div[1]/div[3]/div/input"
            )
            try:
                c_ini = self.wait.until(EC.element_to_be_clickable((By.XPATH, xpath_data_ini)))
                c_ini.click()
                c_ini.clear()
                c_ini.send_keys(data_ini)
                c_ini.send_keys(Keys.ENTER)
                pausa(1)
            except Exception as e:
                logger.warning(f"Falha ao preencher data inicial: {e}")

            # Preenche data final
            xpath_data_fim = (
                "/html/body/div[2]/div[2]/div[1]/div[2]/div/div/div/div/div[2]"
                "/div[1]/div[2]/div/div/div[1]/div/div[2]/fieldset/form/div/div"
                "/div/div[1]/div[4]/div/input"
            )
            try:
                c_fim = self.wait.until(EC.element_to_be_clickable((By.XPATH, xpath_data_fim)))
                c_fim.click()
                c_fim.clear()
                c_fim.send_keys(data_fim)
                c_fim.send_keys(Keys.ENTER)
                pausa(1)
            except Exception as e:
                logger.warning(f"Falha ao preencher data final: {e}")

            # Desmarca checkboxes desnecessarios (3o e 4o)
            xpath_chk3 = (
                "/html/body/div[2]/div[2]/div[1]/div[2]/div/div/div/div/div[2]"
                "/div[1]/div[2]/div/div/div[1]/div/div[2]/fieldset/form/div/div"
                "/div/div[1]/div[5]/div/label[3]/input"
            )
            xpath_chk4 = (
                "/html/body/div[2]/div[2]/div[1]/div[2]/div/div/div/div/div[2]"
                "/div[1]/div[2]/div/div/div[1]/div/div[2]/fieldset/form/div/div"
                "/div/div[1]/div[5]/div/label[4]/input"
            )
            for xp in (xpath_chk3, xpath_chk4):
                try:
                    chk = self.driver.find_element(By.XPATH, xp)
                    if chk.is_selected():
                        self.driver.execute_script(
                            "arguments[0].scrollIntoView({block:'center'});", chk
                        )
                        chk.click()
                except Exception:
                    pass

            # Clica em Pesquisar
            xpath_pesquisar = (
                "/html/body/div[2]/div[2]/div[1]/div[2]/div/div/div/div/div[2]"
                "/div[1]/div[2]/div/div/div[1]/div/div[2]/fieldset/form/div/div"
                "/div/div[3]/div/button"
            )
            self.wait.until(EC.element_to_be_clickable((By.XPATH, xpath_pesquisar))).click()
            pausa(3)

            logger.info("Vencendo Hoje: filtros aplicados, pronto para consultar listas")

        except Exception as e:
            logger.error(f"Erro ao preparar Vencendo Hoje: {e}")

    def consultar_lista(self, numero_lista: str) -> ResultadoLista:
        """
        Consulta uma lista de entrega na tela 'Vencendo Hoje'.
        Extrai motorista e AWBs da lista.

        Args:
            numero_lista: Numero da lista de entrega

        Returns:
            ResultadoLista com motorista e lista de AWBs
        """
        resultado = ResultadoLista(numero_lista=numero_lista)

        try:
            self.browser.voltar_aba_principal()

            # Campo de busca na tabela
            xpath_campo = (
                "/html/body/div[2]/div[2]/div[1]/div[2]/div/div/div/div/div[2]"
                "/div[1]/div[2]/div/div/div[2]/div/div/div/div[2]/div/div[1]"
                "/div[3]/div/label/input"
            )

            # Tenta localizar a lista na tabela (com retry)
            icone = None
            for tentativa in range(3):
                try:
                    campo = self.wait.until(EC.element_to_be_clickable((By.XPATH, xpath_campo)))
                    campo.click()
                    self.driver.execute_script("arguments[0].value = '';", campo)
                    campo.send_keys(numero_lista)
                    pausa(1.5 + tentativa)

                    # Busca icone de abrir na linha que contem o numero da lista
                    xpath_icone = (
                        f"//table//tbody//tr[contains(., '{numero_lista}')]//td[14]//a//i"
                        f" | //table//tbody//tr[contains(., '{numero_lista}')]//td[14]//a[1]"
                    )
                    icone = WebDriverWait(self.driver, 8).until(
                        EC.element_to_be_clickable((By.XPATH, xpath_icone))
                    )
                    break
                except TimeoutException:
                    if tentativa < 2:
                        logger.debug(f"Lista {numero_lista}: tentativa {tentativa + 1} falhou, retentando...")
                        pausa(1.5)

            if not icone:
                # Fallback: primeira linha visivel
                try:
                    xpath_fallback = "//table//tbody//tr/td[14]//a[1]//i | //table//tbody//tr/td[14]//a[1]"
                    icone = WebDriverWait(self.driver, 5).until(
                        EC.element_to_be_clickable((By.XPATH, xpath_fallback))
                    )
                except TimeoutException:
                    logger.warning(f"Lista {numero_lista} nao encontrada na tabela")
                    return resultado

            # Abre detalhe da lista
            try:
                icone.click()
            except Exception:
                self.driver.execute_script("arguments[0].click();", icone)
            pausa(2)

            # Le motorista
            xpath_motorista = (
                "/html/body/div[2]/div[2]/div[1]/div[2]/form/div[1]/div[1]"
                "/div[2]/div[2]/div/input"
            )
            try:
                el_motorista = self.wait.until(
                    EC.presence_of_element_located((By.XPATH, xpath_motorista))
                )
                valor_bruto = el_motorista.get_attribute("value") or ""
                # Limpa caracteres nao-imprimiveis (icones de fonte)
                nome_limpo = re.sub(r"[^0-9A-Za-zÀ-ÖØ-öø-ÿ\s\.\-']", "", valor_bruto).strip()
                nome_limpo = re.sub(r"\s+", " ", nome_limpo)
                if nome_limpo and re.search(r"[A-Za-zÀ-ÖØ-öø-ÿ]", nome_limpo):
                    resultado.motorista = nome_limpo
                else:
                    resultado.motorista = "SEM MOTORISTA"
            except Exception:
                resultado.motorista = "SEM MOTORISTA"

            # Le AWBs da tabela de documentos
            xpath_tbody = (
                "/html/body/div[2]/div[2]/div[1]/div[2]/form/div[2]/div/fieldset"
                "/div/div[2]/div/div[2]/table/tbody"
            )
            try:
                tbody = WebDriverWait(self.driver, 10).until(
                    EC.presence_of_element_located((By.XPATH, xpath_tbody))
                )
                # Aguarda linhas popularem
                linhas = []
                for _ in range(4):
                    linhas = tbody.find_elements(By.TAG_NAME, "tr")
                    if linhas:
                        break
                    pausa(1)

                for linha in linhas:
                    try:
                        # Coluna 6 = AWB/Documento
                        td_awb = linha.find_element(By.XPATH, "./td[6]")
                        texto = self.driver.execute_script(
                            "return arguments[0].innerText;", td_awb
                        ).strip()
                        if texto:
                            resultado.awbs.append(texto)
                    except NoSuchElementException:
                        continue
            except TimeoutException:
                logger.warning(f"Tabela de documentos nao apareceu para lista {numero_lista}")

            logger.info(
                f"Lista {numero_lista}: motorista={resultado.motorista}, "
                f"{len(resultado.awbs)} AWB(s)"
            )

            # Volta para a pagina anterior
            try:
                self.driver.back()
                pausa(2)
            except Exception:
                pass

        except Exception as e:
            logger.error(f"Erro ao consultar lista {numero_lista}: {e}")

        return resultado

    # ============================================================
    # COLETAS/ENTREGAS
    # ============================================================

    def preparar_coletas(self, data_ini: str, data_fim: str):
        """
        Prepara a tela de Gerenciar Coletas/Entregas com os filtros.

        Args:
            data_ini: Data inicial (DDMMAAAA)
            data_fim: Data final (DDMMAAAA)
        """
        self.browser.navegar_coletas_entregas()

        try:
            # Data inicial
            c_ini = self.wait.until(EC.element_to_be_clickable((By.ID, "SchedulingStartDate")))
            c_ini.click()
            c_ini.clear()
            c_ini.send_keys(data_ini)
            c_ini.send_keys(Keys.ENTER)
            pausa(2)

            # Data final
            c_fim = self.wait.until(EC.element_to_be_clickable((By.ID, "SchedulingEndDate")))
            c_fim.click()
            c_fim.clear()
            c_fim.send_keys(data_fim)
            c_fim.send_keys(Keys.ENTER)
            pausa(2)

            # Pesquisar
            self.driver.find_element(By.ID, "searchButton").click()
            pausa(5)

            logger.info("Coletas/Entregas: filtros aplicados")

        except Exception as e:
            logger.error(f"Erro ao preparar coletas: {e}")

    def marcar_cte_coleta(self, cte: str) -> int:
        """
        Pesquisa um CTe na tela de Coletas/Entregas e marca os checkboxes.
        Um CTe pode ter multiplas linhas (volumes).

        Args:
            cte: Numero do CTe

        Returns:
            Quantidade de checkboxes marcados
        """
        try:
            # Campo de busca
            campo = self.wait.until(EC.element_to_be_clickable((By.XPATH,
                "//label[contains(normalize-space(.), 'Pesquisar')]//input"
                " | //input[@type='search']"
            )))
            campo.click()
            self.driver.execute_script("arguments[0].value = '';", campo)
            campo.send_keys(cte)
            pausa(2)

            # Marca checkboxes das linhas visiveis
            marcados = 0
            linhas = self.driver.find_elements(By.XPATH, "//table//tbody//tr")
            for linha in linhas:
                try:
                    if not linha.is_displayed():
                        continue
                    texto = linha.text.strip()
                    if not texto or "nenhum" in texto.lower():
                        continue
                    checkbox = linha.find_element(By.XPATH, ".//td[1]//input[@type='checkbox']")
                    if checkbox.is_displayed() and not checkbox.is_selected():
                        self.driver.execute_script(
                            "arguments[0].scrollIntoView({block:'center'});", checkbox
                        )
                        try:
                            checkbox.click()
                        except Exception:
                            self.driver.execute_script("arguments[0].click();", checkbox)
                        marcados += 1
                except Exception:
                    continue

            if marcados:
                logger.info(f"CTe {cte}: {marcados} checkbox(es) marcado(s) para coleta")
            else:
                logger.warning(f"CTe {cte}: nenhuma linha encontrada para coleta")

            return marcados

        except Exception as e:
            logger.error(f"Erro ao marcar CTe {cte} para coleta: {e}")
            return 0

    def finalizar_coletas(self):
        """
        Clica no botao de finalizar coleta/entrega e confirma.
        Chamado uma vez apos marcar todos os CTes.
        """
        try:
            # Botao finalizar (pode variar de posicao)
            xpath_finalizar = (
                "//div[contains(@class,'dataTables_wrapper')]//a[contains(@class,'btn')]"
                " | //a[contains(.,'Finalizar') or contains(.,'Confirmar')]"
            )
            # Fallback: xpath fixo do Igor
            xpath_fixo = (
                "/html/body/div[2]/div[2]/div[1]/div[2]/div/div/div/div/div[1]"
                "/div[1]/div[2]/div/div/div[2]/div/div/div/div[2]/div/div[1]"
                "/div[4]/div/div/a[2]"
            )

            btn = None
            try:
                btn = self.wait.until(EC.element_to_be_clickable((By.XPATH, xpath_fixo)))
            except TimeoutException:
                try:
                    btn = self.driver.find_element(By.XPATH, xpath_finalizar)
                except Exception:
                    pass

            if btn:
                btn.click()
                pausa(2)

                # Tenta confirmar no modal (se aparecer)
                textos = ["Confirmar", "Confirmado", "Sim", "OK", "Coletado", "Salvar"]
                xpath_texto = " | ".join(
                    f"//button[contains(normalize-space(.), '{t}')]" for t in textos
                )
                try:
                    btn_confirmar = WebDriverWait(self.driver, 5).until(
                        EC.element_to_be_clickable((By.XPATH, xpath_texto))
                    )
                    try:
                        btn_confirmar.click()
                    except Exception:
                        self.driver.execute_script("arguments[0].click();", btn_confirmar)
                    pausa(2)
                    logger.info("Coleta/entrega confirmada com sucesso")
                except TimeoutException:
                    logger.info("Coleta/entrega finalizada (sem modal de confirmacao)")
            else:
                logger.warning("Botao de finalizar coletas nao encontrado")

        except Exception as e:
            logger.error(f"Erro ao finalizar coletas: {e}")

    # ============================================================
    # LIBERACAO DE RETENCAO (complementar ao nexlog_liberar.py)
    # ============================================================

    def marcar_cte_liberacao(self, cte: str) -> int:
        """
        Pesquisa um CTe na tela de Retencao e marca os checkboxes.

        DELEGA para NexlogLiberar (fonte unica de verdade da liberacao).
        Isso garante que as mesmas validacoes de status (Retida vs
        Parcialmente/Liberada) sao aplicadas em qualquer fluxo.

        Args:
            cte: Numero do CTe/AWB

        Returns:
            Quantidade de checkboxes marcados (0 ou 1+)
        """
        from modules.nexlog_liberar import NexlogLiberar

        lib = NexlogLiberar(self.browser)
        resultado = lib.selecionar_awbs_para_liberacao({cte})

        if cte in resultado.get("selecionados", []):
            return 1
        return 0

    def finalizar_liberacao(self):
        """
        Confirma a liberacao dos CTes marcados.
        DELEGA para NexlogLiberar.confirmar_liberacao().
        """
        from modules.nexlog_liberar import NexlogLiberar

        lib = NexlogLiberar(self.browser)
        sucesso = lib.confirmar_liberacao()

        if sucesso:
            logger.info("Liberacao de retencao confirmada com sucesso")
        else:
            logger.warning("Liberacao nao confirmada (nenhum item ou erro)")
