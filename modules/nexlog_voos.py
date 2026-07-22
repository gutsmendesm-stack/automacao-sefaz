"""
Modulo para operacoes com voos no Nexlog:
- Buscar voos por data (Gerenciar rotas)
- Extrair chave do MDF-e (Visualizar integracao MDFe)
- Baixar manifesto do voo (PDF)
"""

import time
import logging
import re
from typing import List, Optional

from selenium.webdriver.common.by import By
from selenium.webdriver.common.keys import Keys
from selenium.webdriver.support import expected_conditions as EC
from selenium.common.exceptions import TimeoutException

from models.termo import Voo
from modules.browser import NexlogBrowser

logger = logging.getLogger(__name__)


class NexlogVoos:
    """Operacoes com voos no Nexlog (tela Gerenciar rotas/recebimento)."""

    def __init__(self, browser: NexlogBrowser):
        self.browser = browser
        self.driver = browser.driver
        self.wait = browser.wait

    def pesquisar_voos(self, data_inicial: str, data_final: str) -> List[Voo]:
        """
        Pesquisa voos na tela de Gerenciar rotas (Recebimento).

        Args:
            data_inicial: formato DD/MM/YYYY
            data_final: formato DD/MM/YYYY

        Returns:
            Lista de Voo ordenada por hora de chegada
        """
        # Navega para a pagina
        self.browser.navegar_operacoes_gerenciar_rotas()
        time.sleep(2)

        # Preenche data inicial
        campo_data_ini = self.wait.until(
            EC.element_to_be_clickable((By.XPATH,
                "//input[contains(@id,'StartDate') or contains(@id,'startDate') "
                "or contains(@name,'StartDate')]"
                " | //input[contains(@placeholder,'Data inicial')]"
            ))
        )
        campo_data_ini.click()
        campo_data_ini.send_keys(Keys.CONTROL, "a")
        campo_data_ini.send_keys(data_inicial)
        campo_data_ini.send_keys(Keys.TAB)
        time.sleep(0.5)

        # Preenche data final
        campo_data_fim = self.wait.until(
            EC.element_to_be_clickable((By.XPATH,
                "//input[contains(@id,'EndDate') or contains(@id,'endDate') "
                "or contains(@name,'EndDate')]"
                " | //input[contains(@placeholder,'Data final')]"
            ))
        )
        campo_data_fim.click()
        campo_data_fim.send_keys(Keys.CONTROL, "a")
        campo_data_fim.send_keys(data_final)
        campo_data_fim.send_keys(Keys.TAB)
        time.sleep(0.5)

        # Clica pesquisar
        botao_pesquisar = self.wait.until(
            EC.element_to_be_clickable((By.XPATH,
                "//button[contains(.,'Pesquisar')] | //button[contains(@id,'search')]"
            ))
        )
        botao_pesquisar.click()
        time.sleep(5)

        # Extrai voos da tabela
        voos = self._extrair_voos_tabela()

        # Ordena por hora de chegada
        voos.sort(key=lambda v: v.data_chegada)

        logger.info(f"Encontrados {len(voos)} voos entre {data_inicial} e {data_final}")
        return voos

    def _extrair_voos_tabela(self) -> List[Voo]:
        """Extrai dados dos voos da tabela de resultados."""
        voos = []
        try:
            # Aguarda tabela carregar
            self.wait.until(
                EC.presence_of_element_located((By.XPATH, "//table//tbody//tr"))
            )

            linhas = self.driver.find_elements(By.XPATH, "//table//tbody//tr")

            for i, linha in enumerate(linhas):
                try:
                    colunas = linha.find_elements(By.TAG_NAME, "td")
                    if len(colunas) < 6:
                        continue

                    # Mapeia colunas baseado na estrutura observada nos prints
                    # Colunas: checkbox, icones, Numero controle, Etapas, Data chegada, SLA, Assinado, Status, Acoes
                    numero_controle = ""
                    etapas = ""
                    data_chegada = ""
                    assinado = ""
                    status_recebimento = ""

                    for col in colunas:
                        texto = col.text.strip()
                        # Identifica coluna por conteudo
                        if re.match(r'G3\s*\d+', texto):
                            numero_controle = texto
                        elif re.match(r'[A-Z]{3}/[A-Z]{3}', texto):
                            etapas = texto
                        elif re.match(r'\d{2}/\d{2}/\d{4}\s+\d{2}:\d{2}', texto):
                            data_chegada = texto
                        elif "vol(s)" in texto.lower() or "kg" in texto.lower():
                            assinado = texto
                        elif texto in ["Fechado", "Aberto", "Em andamento"]:
                            status_recebimento = texto

                    if numero_controle:
                        voo = Voo(
                            numero_controle=numero_controle,
                            etapas=etapas,
                            data_chegada=data_chegada,
                            assinado=assinado,
                            status_recebimento=status_recebimento,
                            indice_tabela=i,
                        )
                        voos.append(voo)

                except Exception as e:
                    logger.debug(f"Erro ao extrair linha {i}: {e}")
                    continue

        except TimeoutException:
            logger.warning("Nenhum voo encontrado na tabela")

        return voos

    def extrair_chave_mdfe(self, voo: Voo) -> str:
        """
        Extrai a chave do MDF-e de um voo.
        Fluxo: Clica em Acoes > Visualizar integracao MDFe > Le a coluna 'Chave'
        
        Busca o voo na tabela pelo numero de controle (nao pelo indice,
        pois o indice pode mudar entre pesquisas).
        """
        try:
            # Localiza a linha do voo pelo numero de controle
            linha = self._encontrar_linha_voo(voo.numero_controle)
            if linha is None:
                logger.error(f"Voo {voo.numero_controle} nao encontrado na tabela")
                return ""

            # Procura o botao de acoes na ultima coluna (setinha/dropdown)
            try:
                botao_acoes = linha.find_element(By.XPATH,
                    ".//td[last()]//button | .//td[last()]//a[contains(@class,'dropdown')] "
                    "| .//td[last()]//*[contains(@class,'btn')] "
                    "| .//td[last()]//*[contains(@class,'action')] "
                    "| .//td[last()]//i[contains(@class,'fa')]/.."
                )
            except Exception:
                # Tenta clicar no ultimo td diretamente
                botao_acoes = linha.find_element(By.XPATH, ".//td[last()]")
            
            botao_acoes.click()
            time.sleep(2)

            # Clica em "Visualizar integracao MDFe" no menu dropdown
            time.sleep(2)  # Espera menu abrir completamente
            
            clicou_mdfe = self._clicar_visualizar_mdfe()
            if not clicou_mdfe:
                logger.error(f"Nao conseguiu clicar em 'Visualizar integracao MDFe'")
                self._debug_dropdown()
                self._fechar_dropdown()
                return ""

            time.sleep(5)

            # Le a chave da tabela no modal
            chave = self._ler_chave_modal()

            # Fecha o modal
            self._fechar_modal_integracao()

            return chave

        except Exception as e:
            logger.error(f"Erro ao extrair chave MDF-e do voo {voo.numero_controle}: {e}")
            self._fechar_modal_integracao()
            return ""

    def _clicar_visualizar_mdfe(self) -> bool:
        """
        Clica na opcao "Visualizar integracao MDFe" no dropdown de acoes.
        
        IMPORTANTE: Busca APENAS no dropdown VISIVEL/ABERTO para nao clicar
        na opcao de outro voo. Os XPaths globais (//a[...]) podem pegar
        elementos de dropdowns de OUTRAS linhas da tabela.
        """
        # Primeiro, encontra o dropdown ABERTO (visivel) para buscar DENTRO dele
        dropdown_aberto = None
        try:
            dropdowns = self.driver.find_elements(By.XPATH,
                "//ul[contains(@class,'dropdown-menu')]"
            )
            for dd in dropdowns:
                if dd.is_displayed():
                    dropdown_aberto = dd
                    break
        except Exception:
            pass

        # === Se encontrou dropdown aberto, busca DENTRO dele (relativo) ===
        if dropdown_aberto:
            # ETAPA 1: data-click com ViewMDFe DENTRO do dropdown
            try:
                opcao = dropdown_aberto.find_element(By.XPATH,
                    ".//a[contains(@data-click,'ViewMDFe')]"
                )
                self.driver.execute_script("arguments[0].click();", opcao)
                logger.info("Clicou em 'Visualizar integracao MDFe' via data-click (dropdown local)")
                return True
            except Exception:
                pass

            # ETAPA 2: classe da <li> com VIEWMDFE DENTRO do dropdown
            try:
                opcao = dropdown_aberto.find_element(By.XPATH,
                    ".//li[contains(@class,'VIEWMDFE') or contains(@class,'ViewMDFe')]//a"
                )
                self.driver.execute_script("arguments[0].click();", opcao)
                logger.info("Clicou via classe li (dropdown local)")
                return True
            except Exception:
                pass

            # ETAPA 3: texto "Visualizar" + "MDFe" DENTRO do dropdown
            try:
                opcao = dropdown_aberto.find_element(By.XPATH,
                    ".//a[contains(.,'Visualizar') and contains(.,'MDFe')]"
                )
                self.driver.execute_script("arguments[0].click();", opcao)
                logger.info("Clicou via texto 'Visualizar'+'MDFe' (dropdown local)")
                return True
            except Exception:
                pass

            # ETAPA 4: busca todos os links DENTRO do dropdown e filtra
            try:
                todos_links = dropdown_aberto.find_elements(By.TAG_NAME, "a")
                for link in todos_links:
                    texto = (link.get_attribute("textContent") or "").strip().upper()
                    data_click = (link.get_attribute("data-click") or "").upper()

                    if "MDF" in texto or "MDF" in data_click:
                        if "GERENCIAR" not in texto and "RECEBIMENTO" not in texto:
                            self.driver.execute_script("arguments[0].click();", link)
                            logger.info(f"Clicou via filtro local: '{texto.strip()}'")
                            return True
            except Exception:
                pass

        # === FALLBACK: busca global mas SOMENTE elementos visiveis ===
        logger.warning("Dropdown local nao encontrado ou sem opcao MDF, tentando global...")

        try:
            opcoes = self.driver.find_elements(By.XPATH,
                "//a[contains(@data-click,'ViewMDFe')]"
            )
            for opcao in opcoes:
                try:
                    if opcao.is_displayed():
                        self.driver.execute_script("arguments[0].click();", opcao)
                        logger.info("Clicou via data-click global (visivel)")
                        return True
                except Exception:
                    continue
        except Exception:
            pass

        # Se nada funcionou
        logger.error("NAO encontrou 'Visualizar integracao MDFe' no dropdown!")
        self._debug_dropdown()
        return False

    def _debug_dropdown(self):
        """Loga o conteudo do dropdown aberto para debug."""
        try:
            menus = self.driver.find_elements(By.XPATH,
                "//ul[contains(@class,'dropdown')]"
            )
            for menu in menus:
                if menu.is_displayed():
                    logger.warning(f"DROPDOWN ABERTO - HTML:")
                    html = menu.get_attribute("innerHTML")
                    logger.warning(html[:1000])

                    links = menu.find_elements(By.TAG_NAME, "a")
                    for link in links:
                        texto = link.text or link.get_attribute("textContent") or ""
                        dc = link.get_attribute("data-click") or ""
                        cls = link.get_attribute("class") or ""
                        logger.warning(f"  <a data-click='{dc}' class='{cls}'>{texto.strip()}</a>")
        except Exception as e:
            logger.debug(f"Erro ao debugar dropdown: {e}")

    def _fechar_dropdown(self):
        """Fecha qualquer dropdown aberto."""
        try:
            from selenium.webdriver.common.action_chains import ActionChains
            ActionChains(self.driver).send_keys(Keys.ESCAPE).perform()
            time.sleep(0.5)
        except Exception:
            pass

    def _encontrar_linha_voo(self, numero_controle: str):
        """
        Encontra a linha da tabela que contem o voo pelo numero de controle.
        Busca pelo texto (ex: 'G3 1704') dentro das linhas da tabela.
        """
        try:
            linhas = self.driver.find_elements(By.XPATH, "//table//tbody//tr")
            for linha in linhas:
                texto_linha = linha.text
                # Remove espacos extras para comparacao
                num_limpo = numero_controle.replace(" ", "")
                texto_limpo = texto_linha.replace(" ", "")
                if num_limpo in texto_limpo or numero_controle in texto_linha:
                    return linha
            return None
        except Exception:
            return None

    def _ler_chave_modal(self) -> str:
        """Le a chave do MDF-e no modal de Integracao MDFe."""
        try:
            # Aguarda modal abrir (titulo "Integracao MDFe")
            self.wait.until(
                EC.presence_of_element_located((By.XPATH,
                    "//*[contains(text(),'Integra') and contains(text(),'MDFe')]"
                    " | //div[contains(@class,'modal') and contains(@class,'show')]"
                    " | //div[contains(@class,'modal')]//table"
                ))
            )
            time.sleep(3)

            # Busca por texto de 44 digitos na pagina inteira (mais robusto)
            page_text = self.driver.find_element(By.TAG_NAME, "body").text
            match = re.search(r'\b(\d{44})\b', page_text)
            if match:
                chave = match.group(1)
                logger.info(f"Chave MDF-e encontrada: {chave[:20]}...")
                return chave

            # Alternativa: busca em celulas da tabela do modal
            try:
                celulas = self.driver.find_elements(By.XPATH,
                    "//div[contains(@class,'modal')]//td"
                )
                for celula in celulas:
                    texto = celula.text.strip()
                    if len(texto) == 44 and texto.isdigit():
                        logger.info(f"Chave MDF-e encontrada (celula): {texto[:20]}...")
                        return texto
            except Exception:
                pass

            logger.warning("Chave MDF-e nao encontrada no modal")
            return ""

        except TimeoutException:
            logger.error("Timeout aguardando modal de Integracao MDFe")
            return ""
        except Exception as e:
            logger.error(f"Erro ao ler chave do modal: {e}")
            return ""

    def _fechar_modal_integracao(self):
        """Fecha o modal de Integracao MDFe."""
        try:
            botao_fechar = self.wait.until(
                EC.element_to_be_clickable((By.XPATH,
                    "//div[contains(@class,'modal')]//button[contains(.,'Fechar')]"
                    " | //div[contains(@class,'modal')]//button[contains(@class,'close')]"
                    " | //div[contains(@class,'modal')]//button[@aria-label='Close']"
                ))
            )
            botao_fechar.click()
            time.sleep(1)
        except Exception:
            # Tenta fechar com X
            try:
                self.driver.find_element(By.XPATH,
                    "//div[contains(@class,'modal')]//button[contains(@class,'close')]"
                ).click()
            except Exception:
                pass

    def baixar_manifesto(self, voo: Voo) -> str:
        """
        Baixa o PDF do manifesto de despacho do voo.
        Fluxo: Clica nos volumes > Aba Manifestos > Acoes > Imprimir

        Returns:
            Caminho do arquivo PDF baixado, ou "" se falhar
        """
        try:
            # Encontra a linha do voo pelo numero de controle (mais robusto que indice)
            linha = self._encontrar_linha_voo(voo.numero_controle)
            if linha is None:
                logger.error(f"Voo {voo.numero_controle} nao encontrado na tabela para baixar manifesto")
                return ""

            # Procura link de volumes (texto tipo "58 vol(s), 289,023 kg")
            # Pode ser um <a> ou pode estar dentro de um <td> clicavel
            try:
                link_volumes = linha.find_element(By.XPATH,
                    ".//a[contains(.,'vol(s)')] | .//td[contains(.,'vol(s)')]//a"
                )
            except Exception:
                # Fallback: tenta clicar no td que contem "vol(s)" diretamente
                try:
                    link_volumes = linha.find_element(By.XPATH,
                        ".//td[contains(.,'vol(s)')]"
                    )
                except Exception:
                    # Ultimo fallback: tenta clicar na coluna "Assinado" (geralmente col 7 ou 8)
                    try:
                        link_volumes = linha.find_element(By.XPATH,
                            ".//td[contains(.,'kg')] | .//td[contains(.,'vol')]"
                        )
                    except Exception:
                        logger.error(f"Voo {voo.numero_controle}: coluna de volumes nao encontrada")
                        logger.debug(f"Texto da linha: {linha.text[:200]}")
                        return ""

            link_volumes.click()
            time.sleep(3)

            # Clica na aba "Manifestos"
            aba_manifestos = self.wait.until(
                EC.element_to_be_clickable((By.XPATH,
                    "//a[contains(.,'Manifestos')] | //li//a[text()='Manifestos']"
                ))
            )
            aba_manifestos.click()
            time.sleep(2)

            # Clica no botao de acoes do manifesto
            botao_acoes_manifesto = self.wait.until(
                EC.element_to_be_clickable((By.XPATH,
                    "//div[contains(@class,'modal')]//table//tbody//tr//td[last()]"
                    "//*[contains(@class,'dropdown') or contains(@class,'action') or self::button]"
                ))
            )
            botao_acoes_manifesto.click()
            time.sleep(1)

            # Clica em "Imprimir"
            opcao_imprimir = self.wait.until(
                EC.element_to_be_clickable((By.XPATH,
                    "//a[contains(.,'Imprimir')] | //button[contains(.,'Imprimir')]"
                ))
            )
            opcao_imprimir.click()

            # Aguarda download
            caminho = self.browser.aguardar_download(timeout=30)

            # Fecha o modal
            try:
                botao_fechar = self.wait.until(
                    EC.element_to_be_clickable((By.XPATH,
                        "//button[contains(.,'Fechar')]"
                    ))
                )
                botao_fechar.click()
                time.sleep(1)
            except Exception:
                pass

            logger.info(f"Manifesto baixado: {caminho}")
            return caminho

        except Exception as e:
            logger.error(f"Erro ao baixar manifesto do voo {voo.numero_controle}: {e}")
            # Tenta fechar qualquer modal aberto
            self.browser._fechar_modais()
            return ""
