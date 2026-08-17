"""
Modulo para operacoes com voos no Nexlog:
- Buscar voos por data (Gerenciar rotas)
- Extrair chave do MDF-e (Visualizar integracao MDFe)
- Baixar manifesto do voo (PDF)
"""

import time
import logging
import os
import re
from typing import List, Optional

from selenium.webdriver.common.by import By
from selenium.webdriver.common.keys import Keys
from selenium.webdriver.support import expected_conditions as EC
from selenium.common.exceptions import TimeoutException

from config import pausa, config
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
        pausa(2)

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
        pausa(0.5)

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
        pausa(0.5)

        # Clica pesquisar
        botao_pesquisar = self.wait.until(
            EC.element_to_be_clickable((By.XPATH,
                "//button[contains(.,'Pesquisar')] | //button[contains(@id,'search')]"
            ))
        )
        botao_pesquisar.click()
        pausa(3)

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
                        # Aceita qualquer companhia (G3, LA, AD, etc), nao so G3
                        if re.match(r'^[A-Z0-9]{2}\s*\d{3,5}$', texto):
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

        ESTRATEGIA PRINCIPAL (mapeada do HTML real do Nexlog):
        Cada linha da tabela tem um link com
            data-click="javascript:window.Controllers.ReceivingController.ViewMDFe(ID)"
        onde ID e o identificador da rota. Basta extrair esse ID e chamar a
        funcao JS diretamente - nao precisa abrir dropdown nem clicar em menu.
        Isso e muito mais confiavel que a navegacao por cliques.

        FALLBACK: fluxo antigo (abre dropdown de acoes e clica na opcao).
        """
        try:
            # Localiza a linha do voo pelo numero de controle
            linha = self._encontrar_linha_voo(voo.numero_controle, voo.data_chegada)
            if linha is None:
                logger.error(f"Voo {voo.numero_controle} nao encontrado na tabela")
                return ""

            # === ESTRATEGIA PRINCIPAL: chama ViewMDFe(ID) via JavaScript ===
            id_rota = self._extrair_id_rota(linha)
            if id_rota:
                try:
                    self.driver.execute_script(
                        f"window.Controllers.ReceivingController.ViewMDFe({id_rota});"
                    )
                    logger.info(f"Chamou ViewMDFe({id_rota}) via JS (sem dropdown)")
                    pausa(1)  # JS abre modal quase instantaneo

                    chave = self._ler_chave_modal()
                    self._fechar_modal_integracao()
                    if chave:
                        return chave
                    logger.warning("ViewMDFe via JS abriu mas nao achou chave, tentando fluxo antigo")
                except Exception as e:
                    logger.warning(f"ViewMDFe via JS falhou ({e}), tentando fluxo antigo")
            else:
                # Sem link ViewMDFe na linha - loga pra debug
                texto_linha = linha.text[:120] if linha else "(nula)"
                logger.warning(
                    f"Voo {voo.numero_controle}: linha encontrada mas sem link ViewMDFe. "
                    f"Texto da linha: '{texto_linha}'"
                )

            # === FALLBACK: fluxo antigo por cliques ===
            linha = self._encontrar_linha_voo(voo.numero_controle, voo.data_chegada)
            if linha is None:
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
            pausa(2)

            # Clica em "Visualizar integracao MDFe" no menu dropdown
            pausa(2)  # Espera menu abrir completamente
            
            clicou_mdfe = self._clicar_visualizar_mdfe()
            if not clicou_mdfe:
                logger.error(f"Nao conseguiu clicar em 'Visualizar integracao MDFe'")
                self._debug_dropdown()
                self._fechar_dropdown()
                return ""

            pausa(2)

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

        ESTRATEGIA PRINCIPAL: busca o link com data-click='ViewMDFe' que esteja
        VISIVEL na pagina. Isso e seguro porque o Nexlog mantem apenas UM
        dropdown aberto por vez - os links das outras linhas ficam ocultos.
        Essa e a estrategia que funciona na pratica (~100% dos casos).

        FALLBACKS: se por algum motivo nao achar, tenta buscar dentro do
        container do dropdown aberto (varias estruturas possiveis).
        """
        # === ESTRATEGIA 1 (principal): data-click visivel na pagina ===
        try:
            opcoes = self.driver.find_elements(By.XPATH,
                "//a[contains(@data-click,'ViewMDFe')]"
            )
            for opcao in opcoes:
                try:
                    if opcao.is_displayed():
                        self.driver.execute_script("arguments[0].click();", opcao)
                        logger.info("Clicou em 'Visualizar integracao MDFe' (data-click visivel)")
                        return True
                except Exception:
                    continue
        except Exception:
            pass

        # === FALLBACKS: busca dentro do container do dropdown aberto ===
        logger.debug("data-click visivel nao encontrado, tentando busca no container...")

        dropdown_aberto = None
        try:
            # Tenta varias estruturas de dropdown (ul, div, ou qualquer container)
            candidatos = self.driver.find_elements(By.XPATH,
                "//ul[contains(@class,'dropdown-menu')]"
                " | //div[contains(@class,'dropdown-menu')]"
                " | //*[contains(@class,'dropdown-menu')]"
            )
            for dd in candidatos:
                if dd.is_displayed():
                    dropdown_aberto = dd
                    break
        except Exception:
            pass

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

        # === ULTIMO FALLBACK: qualquer link visivel com MDF no texto ===
        try:
            links = self.driver.find_elements(By.XPATH, "//a[contains(.,'MDF')]")
            for link in links:
                try:
                    if not link.is_displayed():
                        continue
                    texto = (link.get_attribute("textContent") or "").strip().upper()
                    if "GERENCIAR" in texto or "RECEBIMENTO" in texto:
                        continue
                    self.driver.execute_script("arguments[0].click();", link)
                    logger.info(f"Clicou via texto visivel: '{texto[:40]}'")
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
            pausa(0.5)
        except Exception:
            pass

    def _extrair_id_rota(self, linha) -> str:
        """
        Extrai o ID da rota a partir do link ViewMDFe da linha.

        Estrutura real (mapeada do HTML do Nexlog):
            <a data-click="javascript:window.Controllers.ReceivingController.ViewMDFe(1621211);return false;">
                Visualizar integração MDFe
            </a>

        Returns:
            ID da rota (ex: "1621211") ou "" se nao encontrar
        """
        try:
            links = linha.find_elements(By.XPATH, ".//a[contains(@data-click,'ViewMDFe')]")
            for link in links:
                data_click = link.get_attribute("data-click") or ""
                match = re.search(r'ViewMDFe\((\d+)\)', data_click)
                if match:
                    return match.group(1)
        except Exception as e:
            logger.debug(f"Erro ao extrair ID da rota: {e}")
        return ""

    def _encontrar_linha_voo(self, numero_controle: str, data_chegada: str = ""):
        """
        Encontra a linha da tabela que contem o voo pelo numero de controle.
        Busca pelo texto (ex: 'G3 1704') dentro das linhas da tabela.

        Se data_chegada for fornecida e houver multiplas linhas com o mesmo
        numero de controle (ex: mesmo voo em dias diferentes), prefere a
        que contem a data especificada.
        """
        try:
            linhas = self.driver.find_elements(By.XPATH, "//table//tbody//tr")
            candidatas = []

            for linha in linhas:
                texto_linha = linha.text
                # Remove espacos extras para comparacao
                num_limpo = numero_controle.replace(" ", "")
                texto_limpo = texto_linha.replace(" ", "")
                if num_limpo in texto_limpo or numero_controle in texto_linha:
                    candidatas.append(linha)

            if not candidatas:
                return None

            # Se so tem uma, retorna direto
            if len(candidatas) == 1:
                return candidatas[0]

            # Multiplas linhas com mesmo voo (ex: G3 1708 nos dias 25 e 26)
            # Desempata pela data_chegada do Voo
            if data_chegada:
                # Extrai apenas a parte DD/MM/YYYY da data_chegada pra comparar
                data_parte = data_chegada[:10] if len(data_chegada) >= 10 else data_chegada
                for linha in candidatas:
                    texto_linha = linha.text
                    if data_parte in texto_linha:
                        logger.debug(
                            f"Voo {numero_controle}: {len(candidatas)} linhas encontradas, "
                            f"selecionada pela data {data_parte}"
                        )
                        return linha

            # Se nao conseguiu desempatar por data, pega a primeira
            logger.warning(
                f"Voo {numero_controle}: {len(candidatas)} linhas encontradas, "
                f"usando a primeira (sem desempate por data)"
            )
            return candidatas[0]
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
            pausa(1)  # Modal ja esta pronto, so garante render da tabela

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
            # Usa wait curto (5s) - o botao ja esta visivel
            from selenium.webdriver.support.ui import WebDriverWait
            wait_rapido = WebDriverWait(self.driver, 5)
            botao_fechar = wait_rapido.until(
                EC.element_to_be_clickable((By.XPATH,
                    "//div[contains(@class,'modal')]//button[contains(.,'Fechar')]"
                    " | //div[contains(@class,'modal')]//button[contains(@class,'close')]"
                    " | //div[contains(@class,'modal')]//button[@aria-label='Close']"
                ))
            )
            botao_fechar.click()
            pausa(0.5)
        except Exception:
            # Fallback: fecha via JS (mais rapido que procurar botao)
            try:
                self.driver.execute_script("""
                    var modals = document.querySelectorAll('.modal.show, .modal[style*="display: block"]');
                    modals.forEach(function(m) { m.style.display = 'none'; });
                    var backdrops = document.querySelectorAll('.modal-backdrop');
                    backdrops.forEach(function(b) { b.remove(); });
                    document.body.classList.remove('modal-open');
                """)
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
            linha = self._encontrar_linha_voo(voo.numero_controle, voo.data_chegada)
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
            pausa(3)

            # Clica na aba "Manifestos"
            aba_manifestos = self.wait.until(
                EC.element_to_be_clickable((By.XPATH,
                    "//a[contains(.,'Manifestos')] | //li//a[text()='Manifestos']"
                ))
            )
            aba_manifestos.click()
            pausa(2)

            # Clica no botao de acoes do manifesto
            botao_acoes_manifesto = self.wait.until(
                EC.element_to_be_clickable((By.XPATH,
                    "//div[contains(@class,'modal')]//table//tbody//tr//td[last()]"
                    "//*[contains(@class,'dropdown') or contains(@class,'action') or self::button]"
                ))
            )
            botao_acoes_manifesto.click()
            pausa(1)

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
                pausa(1)
            except Exception:
                pass

            logger.info(f"Manifesto baixado: {caminho}")
            return caminho

        except Exception as e:
            logger.error(f"Erro ao baixar manifesto do voo {voo.numero_controle}: {e}")
            # Tenta fechar qualquer modal aberto
            self.browser._fechar_modais()
            return ""

    def imprimir_damdfe(self, voo: Voo) -> tuple:
        """
        Abre o modal de Integracao MDFe do voo, extrai a chave de acesso
        e baixa o PDF do DAMDFE (documento auxiliar do MDF-e).

        Fluxo no Nexlog:
        1. Encontra a linha do voo na tabela (Gerenciar Rotas)
        2. Abre modal "Integracao MDFe" (ViewMDFe via JS ou dropdown)
        3. Le a chave de 44 digitos do modal
        4. Clica em "Imprimir DAMDFE" dentro do modal
        5. Aguarda download do PDF
        6. Fecha o modal

        Args:
            voo: Objeto Voo com numero_controle e data_chegada

        Returns:
            Tupla (chave_mdfe: str, caminho_pdf: str).
            Se falhar em qualquer etapa, retorna ("", "") ou (chave, "").
        """
        chave = ""
        caminho_pdf = ""

        try:
            # NOTA: a tabela de voos ja deve estar preenchida pelo chamador
            # (via pesquisar_voos). NAO navegar/recarregar aqui — isso limpa a tabela.

            # Localiza a linha do voo
            linha = self._encontrar_linha_voo(voo.numero_controle, voo.data_chegada)
            if linha is None:
                # Modal anterior pode ter bugado o estado — tenta voltar pra pagina
                logger.warning(f"DAMDFE: Voo {voo.numero_controle} nao encontrado, tentando recarregar tabela")
                self.browser.navegar_operacoes_gerenciar_rotas()
                pausa(2)
                # Repesquisa (precisa das datas — usa data_chegada do proprio voo)
                # Clica pesquisar se o botao estiver visivel
                try:
                    botao_pesquisar = self.driver.find_element(By.XPATH,
                        "//button[contains(.,'Pesquisar')] | //button[contains(@id,'search')]"
                    )
                    botao_pesquisar.click()
                    pausa(3)
                except Exception:
                    pass
                linha = self._encontrar_linha_voo(voo.numero_controle, voo.data_chegada)
                if linha is None:
                    logger.error(f"DAMDFE: Voo {voo.numero_controle} nao encontrado na tabela")
                    return ("", "")

            # === Abre modal Integracao MDFe (mesma logica de extrair_chave_mdfe) ===
            id_rota = self._extrair_id_rota(linha)
            modal_aberto = False

            if id_rota:
                try:
                    self.driver.execute_script(
                        f"window.Controllers.ReceivingController.ViewMDFe({id_rota});"
                    )
                    logger.info(f"DAMDFE: Chamou ViewMDFe({id_rota}) via JS")
                    pausa(2)
                    modal_aberto = True
                except Exception as e:
                    logger.warning(f"DAMDFE: ViewMDFe via JS falhou ({e}), tentando dropdown")

            # Fallback: dropdown de acoes
            if not modal_aberto:
                linha = self._encontrar_linha_voo(voo.numero_controle, voo.data_chegada)
                if linha is None:
                    return ("", "")
                try:
                    botao_acoes = linha.find_element(By.XPATH,
                        ".//td[last()]//button | .//td[last()]//a[contains(@class,'dropdown')] "
                        "| .//td[last()]//*[contains(@class,'btn')] "
                        "| .//td[last()]//*[contains(@class,'action')] "
                        "| .//td[last()]//i[contains(@class,'fa')]/.."
                    )
                except Exception:
                    botao_acoes = linha.find_element(By.XPATH, ".//td[last()]")

                botao_acoes.click()
                pausa(2)

                if not self._clicar_visualizar_mdfe():
                    logger.error(f"DAMDFE: Nao abriu modal MDFe para {voo.numero_controle}")
                    self._fechar_dropdown()
                    return ("", "")
                pausa(2)
                modal_aberto = True

            # === Le a chave do modal ===
            chave = self._ler_chave_modal()
            if not chave:
                logger.warning(f"DAMDFE: Chave nao encontrada no modal de {voo.numero_controle}")

            # === Clica em "Imprimir DAMDFE" dentro do modal ===
            # Captura snapshot da pasta ANTES do clique (download pode ser instantaneo)
            pasta_dl = config.pasta_downloads
            arquivos_antes = set(os.listdir(pasta_dl)) if os.path.exists(pasta_dl) else set()

            try:
                # Estrategia 1 (rapida): busca direta por texto dentro do modal
                # O log mostrou que o botao tem texto exato "Imprimir DAMDFE"
                pausa(1)  # Aguarda modal renderizar completamente
                opcoes = self.driver.find_elements(By.XPATH,
                    "//div[contains(@class,'modal')]"
                    "//*[contains(text(),'Imprimir DAMDFE') or "
                    "contains(text(),'Imprimir DAMDFe') or "
                    "contains(text(),'DAMDFE')]"
                )
                clicou = False
                for opcao in opcoes:
                    texto = (opcao.get_attribute("textContent") or "").strip()
                    if "DAMDFE" in texto.upper():
                        self.driver.execute_script("arguments[0].click();", opcao)
                        logger.info(f"DAMDFE: Clicou em '{texto}' para {voo.numero_controle}")
                        clicou = True
                        break

                # Estrategia 2: botao/link com classe ou data-click
                if not clicou:
                    botoes = self.driver.find_elements(By.XPATH,
                        "//div[contains(@class,'modal')]"
                        "//a[contains(@data-click,'DAMDFE') or contains(@data-click,'Damdfe')]"
                        " | //div[contains(@class,'modal')]"
                        "//button[contains(@data-click,'DAMDFE') or contains(@data-click,'Damdfe')]"
                    )
                    for btn in botoes:
                        self.driver.execute_script("arguments[0].click();", btn)
                        logger.info(f"DAMDFE: Clicou via data-click para {voo.numero_controle}")
                        clicou = True
                        break

                if not clicou:
                    raise Exception("Botao 'Imprimir DAMDFE' nao encontrado no modal")

            except Exception as e:
                logger.error(f"DAMDFE: Nao encontrou botao de impressao: {e}")
                self._fechar_modal_integracao()
                return (chave, "")

            # === Aguarda download do PDF ===
            # Usa snapshot capturado ANTES do clique (download pode ser instantaneo)
            tempo_ini = time.time()
            caminho_pdf = ""
            while time.time() - tempo_ini < 60:
                pausa(1)
                arquivos_agora = set(os.listdir(pasta_dl)) if os.path.exists(pasta_dl) else set()
                novos = [
                    f for f in (arquivos_agora - arquivos_antes)
                    if not f.endswith('.crdownload') and not f.endswith('.tmp')
                ]
                if novos:
                    arquivo = novos[0]
                    caminho_pdf = os.path.join(pasta_dl, arquivo)
                    logger.info(f"Download DAMDFE concluido: {arquivo}")
                    break
            else:
                logger.warning(f"DAMDFE: Timeout aguardando download para {voo.numero_controle}")

            if caminho_pdf:
                logger.info(f"DAMDFE baixado: {caminho_pdf}")
            else:
                logger.warning(f"DAMDFE: Download timeout para {voo.numero_controle}")

            # === Fecha o modal ===
            self._fechar_modal_integracao()

            return (chave, caminho_pdf)

        except Exception as e:
            logger.error(f"DAMDFE: Erro geral para {voo.numero_controle}: {e}")
            self._fechar_modal_integracao()
            return (chave, caminho_pdf)
