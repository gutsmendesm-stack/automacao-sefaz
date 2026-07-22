"""
Modulo para liberar AWBs na tela de Retencao do Nexlog.
Regras:
- So libera AWBs da secao RETIRA do manifesto
- Nao libera AWBs que tem termos de apreensao
- Nao libera AWBs com status "Liberada" ou "Parcialmente liberada"
- Se o AWB nao aparece na busca = ja foi liberado/retirado -> pula
"""

import time
import logging
from typing import List, Set, Dict

from selenium.webdriver.common.by import By
from selenium.webdriver.common.keys import Keys
from selenium.webdriver.support import expected_conditions as EC
from selenium.common.exceptions import TimeoutException, NoSuchElementException

from modules.browser import NexlogBrowser

logger = logging.getLogger(__name__)


class NexlogLiberar:
    """Libera AWBs na tela de Retencao do Nexlog."""

    def __init__(self, browser: NexlogBrowser):
        self.browser = browser
        self.driver = browser.driver
        self.wait = browser.wait

    def configurar_filtros(self, data_inicial: str, data_final: str):
        """
        Configura os filtros da tela de Retencoes.
        
        No 2o voo em diante, os filtros ja podem estar configurados
        do voo anterior. Nesse caso apenas clica Pesquisar.
        
        Fluxo tolerante:
        1. Tenta ajustar data (se falhar, pula)
        2. Tenta selecionar status Retida (se ja esta, pula)
        3. SEMPRE clica Pesquisar
        """
        # Aguarda a pagina carregar (campos podem demorar apos refresh)
        time.sleep(3)

        # Tenta ajustar data inicial
        try:
            campo_data_ini = self.driver.find_element(By.XPATH,
                "//*[@id='StartDate']"
                " | //input[contains(@id,'StartDate') or contains(@name,'StartDate')]"
            )
            if campo_data_ini.is_displayed():
                campo_data_ini.click()
                campo_data_ini.send_keys(Keys.CONTROL, "a")
                campo_data_ini.send_keys("01")
                campo_data_ini.send_keys(Keys.ENTER)
                time.sleep(1)
        except Exception as e:
            logger.debug(f"Campo data nao encontrado: {e}")

        # Tenta selecionar status "Retida"
        try:
            select2_container = self.driver.find_element(By.XPATH,
                "//span[@id='select2-Status-container']"
                " | //span[contains(@id,'select2') and contains(@id,'Status')]"
            )
            texto_atual = (select2_container.text or "").strip().lower()
            if "retida" not in texto_atual:
                select2_container.click()
                time.sleep(1)
                opcao_retida = self.wait.until(
                    EC.element_to_be_clickable((By.XPATH,
                        "//li[contains(@class,'select2-results__option') "
                        "and normalize-space()='Retida']"
                    ))
                )
                opcao_retida.click()
                time.sleep(1)
        except Exception as e:
            logger.debug(f"Select2 Status: {e}")

        # SEMPRE clica Pesquisar
        try:
            botao_pesquisar = self.wait.until(
                EC.element_to_be_clickable((By.XPATH,
                    "//*[@id='searchButton']"
                    " | //button[contains(.,'Pesquisar')]"
                ))
            )
            botao_pesquisar.click()
            time.sleep(5)
            logger.info(f"Filtros configurados: data 01 + status Retida")
        except Exception as e:
            logger.error(f"Erro ao clicar Pesquisar na tela de retencao: {e}")
            raise

    def selecionar_awbs_para_liberacao(self, awbs: Set[str]) -> Dict[str, str]:
        """
        Para cada AWB, filtra na tabela e marca o CHECKBOX individual.
        
        REGRAS:
        - So marca linhas com status "Retida" (exato)
        - NAO marca: "Liberada", "Parcialmente liberada", "Retida parcialmente"
        - Um AWB pode ter MULTIPLAS linhas (volumes) — marca TODAS com status "Retida"
        - Checkboxes individuais PERSISTEM entre mudancas de filtro

        Args:
            awbs: Set de AWBs para tentar liberar

        Returns:
            Dict com resultado
        """
        resultado = {
            "selecionados": [],
            "ja_liberados": [],
            "erros": [],
        }

        # XPath do campo de filtro
        xpath_input = (
            "//*[@id='RetentionList_filter']/label/input"
            " | //*[@id='RetentionList_filter']//input"
            " | //div[contains(@id,'_filter')]//input"
            " | //input[contains(@type,'search')]"
        )

        for awb in awbs:
            try:
                # 1. Espera o campo de filtro
                campo = self.wait.until(
                    EC.element_to_be_clickable((By.XPATH, xpath_input))
                )

                # 2. Limpa e digita o AWB
                campo.click()
                campo.send_keys(Keys.CONTROL, "a")
                campo.send_keys(Keys.BACKSPACE)
                campo.send_keys(awb)

                logger.debug(f"AWB digitado: {awb}")
                time.sleep(2)  # Tempo para a tabela filtrar

                # 3. Busca todas as linhas da tabela filtrada
                linhas = self.driver.find_elements(By.XPATH,
                    "//table[@id='RetentionList']//tbody//tr"
                    " | //table//tbody//tr"
                )

                # Verifica se tem resultados
                tem_resultado = False
                for linha in linhas:
                    texto = linha.text.strip()
                    if texto and "nenhum" not in texto.lower() and "empty" not in texto.lower():
                        tem_resultado = True
                        break

                if not tem_resultado:
                    resultado["ja_liberados"].append(awb)
                    logger.info(f"AWB {awb}: nao encontrado (ja liberado/retirado)")
                    continue

                # 4. Para CADA linha visivel, verifica status e marca checkbox
                # Um AWB pode ter multiplas linhas (volumes) — marca TODAS com status "Retida"
                checkboxes_marcados = 0
                linhas_puladas = 0

                for linha in linhas:
                    try:
                        if not linha.is_displayed():
                            continue

                        texto_linha = linha.text.upper().strip()
                        if not texto_linha:
                            continue

                        # Classifica o status da linha
                        # So marca se contem "RETIDA" E NAO contem "PARCIAL" ou "LIBERADA"
                        eh_retida = "RETIDA" in texto_linha
                        eh_parcial = "PARCIAL" in texto_linha
                        eh_liberada = "LIBERADA" in texto_linha

                        if eh_retida and not eh_parcial and not eh_liberada:
                            # Status "Retida" — MARCA o checkbox
                            try:
                                checkbox = linha.find_element(By.XPATH,
                                    ".//input[@type='checkbox']"
                                    " | .//td[contains(@class,'check')]//input"
                                )
                                if checkbox.is_displayed() and not checkbox.is_selected():
                                    try:
                                        checkbox.click()
                                    except Exception:
                                        self.driver.execute_script(
                                            "arguments[0].click();", checkbox
                                        )
                                    checkboxes_marcados += 1
                                    time.sleep(0.3)
                                elif checkbox.is_selected():
                                    checkboxes_marcados += 1  # Ja estava marcado
                            except Exception as e:
                                logger.debug(f"AWB {awb}: checkbox nao encontrado na linha: {e}")
                        else:
                            # Liberada, parcial, ou outro — NAO marca
                            linhas_puladas += 1
                            if eh_liberada:
                                logger.debug(f"AWB {awb}: linha 'Liberada' - pulando")
                            elif eh_parcial:
                                logger.debug(f"AWB {awb}: linha parcial - pulando")

                    except Exception as e:
                        logger.debug(f"AWB {awb}: erro ao processar linha: {e}")

                if checkboxes_marcados > 0:
                    logger.info(f"AWB {awb}: {checkboxes_marcados} checkbox(es) marcado(s)"
                              f"{f', {linhas_puladas} puladas' if linhas_puladas else ''}")
                    resultado["selecionados"].append(awb)
                elif linhas_puladas > 0:
                    # Todas as linhas eram liberadas/parciais
                    resultado["ja_liberados"].append(awb)
                    logger.info(f"AWB {awb}: {linhas_puladas} linhas ja liberadas/parciais")
                else:
                    resultado["erros"].append(awb)
                    logger.error(f"AWB {awb}: nenhum checkbox encontrado")

                # Pausa entre AWBs
                time.sleep(1)

            except TimeoutException:
                resultado["erros"].append(awb)
                logger.error(f"AWB {awb}: timeout na busca")
            except Exception as e:
                resultado["erros"].append(awb)
                logger.error(f"AWB {awb}: erro - {e}")

        return resultado

    def confirmar_liberacao(self) -> bool:
        """
        Apos selecionar todos os AWBs, clica em "Liberar documento"
        e confirma na tela de liberacao.
        Fluxo:
        1. Clica botao "Liberar documento"
        2. Abre modal "Liberar retencao" com lista dos selecionados
        3. Campo Protocolo = vazio
        4. Campo Observacao = vazio
        5. Clica botao "Liberado" (azul com checkmark)
        """
        try:
            # Verifica se tem alguma selecao
            texto_selecionados = ""
            try:
                elem_selecionados = self.driver.find_element(By.XPATH,
                    "//*[contains(.,'selecionado')]"
                )
                texto_selecionados = elem_selecionados.text
            except Exception:
                pass

            if "0 selecionado" in texto_selecionados:
                logger.warning("Nenhum AWB selecionado para liberacao")
                return False

            # 1. Clica "Liberar documento"
            botao_liberar = self.wait.until(
                EC.element_to_be_clickable((By.XPATH,
                    "//button[contains(.,'Liberar documento')]"
                    " | //a[contains(.,'Liberar documento')]"
                ))
            )
            botao_liberar.click()
            time.sleep(4)

            # 2. Aguarda modal "Liberar retencao" abrir
            self.wait.until(
                EC.presence_of_element_located((By.XPATH,
                    "//*[contains(.,'Liberar reten') and "
                    "(self::h4 or self::h3 or self::h5 or self::div)]"
                ))
            )
            time.sleep(2)

            # 3. Protocolo e Observacao ficam vazios (nao preenche nada)

            # 4. Clica botao "Liberado" (azul)
            botao_liberado = self.wait.until(
                EC.element_to_be_clickable((By.XPATH,
                    "//button[contains(.,'Liberado')]"
                    " | //button[contains(@class,'btn-primary') and contains(.,'Libera')]"
                    " | //button[contains(@class,'btn-info') and contains(.,'Libera')]"
                ))
            )
            botao_liberado.click()
            time.sleep(5)

            logger.info("Liberacao confirmada com sucesso!")
            return True

        except TimeoutException as e:
            logger.error(f"Timeout ao confirmar liberacao: {e}")
            return False
        except Exception as e:
            logger.error(f"Erro ao confirmar liberacao: {e}")
            return False

    def liberar_awbs(self, awbs: Set[str], data_inicial: str, data_final: str) -> Dict:
        """
        Fluxo completo de liberacao:
        1. Navega para tela de Retencoes
        2. Configura filtros (datas)
        3. Para cada AWB: pesquisa, verifica status, marca checkbox
        4. Clica "Liberar documento"
        5. Confirma liberacao

        Returns:
            Dict com resultado detalhado
        """
        if not awbs:
            logger.info("Nenhum AWB para liberar")
            return {"selecionados": [], "ja_liberados": [], "erros": [], "liberacao_ok": False}

        # 1. Navega
        self.browser.navegar_vendas_retencao_lista()
        time.sleep(2)

        # 2. Configura filtros (tolerante — se falhar, tenta continuar)
        try:
            self.configurar_filtros(data_inicial, data_final)
        except Exception as e:
            logger.warning(f"Filtros falharam mas tentando continuar: {e}")
            # Tenta clicar Pesquisar diretamente (filtros podem ja estar ok)
            try:
                botao = self.driver.find_element(By.XPATH,
                    "//*[@id='searchButton'] | //button[contains(.,'Pesquisar')]"
                )
                botao.click()
                time.sleep(5)
            except Exception:
                pass

        # 3. Seleciona AWBs
        resultado = self.selecionar_awbs_para_liberacao(awbs)

        # 4 e 5. Confirma liberacao (se tem algum selecionado)
        if resultado["selecionados"]:
            liberacao_ok = self.confirmar_liberacao()
            resultado["liberacao_ok"] = liberacao_ok
        else:
            resultado["liberacao_ok"] = False
            logger.info("Nenhum AWB novo para liberar (todos ja liberados ou com erro)")

        return resultado
