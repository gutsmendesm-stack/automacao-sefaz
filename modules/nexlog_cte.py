"""
Modulo para operacoes com CTe no Nexlog:
- Buscar CTe na aba 'Por referencia' para encontrar o AWB
- Adicionar comentario critico no AWB via busca rapida
"""

import time
import logging
import re
from typing import Optional, List, Dict

from selenium.webdriver.common.by import By
from selenium.webdriver.common.keys import Keys
from selenium.webdriver.support import expected_conditions as EC
from selenium.common.exceptions import TimeoutException

from models.termo import TermoApreensao, ConsultaMDFe
from modules.browser import NexlogBrowser

logger = logging.getLogger(__name__)


class NexlogCTeOperacoes:
    """Operacoes com CTe no Nexlog."""

    def __init__(self, browser: NexlogBrowser):
        self.browser = browser
        self.driver = browser.driver
        self.wait = browser.wait

    def buscar_awb_do_cte(self, numero_cte: str) -> str:
        """
        Busca o AWB associado a um CTe.
        Fluxo: Vendas > Conhecimento > Lista > Aba 'Por referencia'
               > Campo 'Numero integracao' > Pesquisar > Le 'N. documento'

        IMPORTANTE: NAO recarrega a pagina entre buscas do mesmo voo.
        SEMPRE clica no toogleFilter para garantir campo visivel
        (mesmo vindo de outro voo onde a URL ja era TransportOrder).

        Returns:
            Numero do AWB (127...) ou "" se nao encontrar
        """
        try:
            # Garante que estamos na aba principal do Nexlog
            self.browser.voltar_aba_principal()
            time.sleep(1)

            # Verifica se estamos na pagina certa (URL contem TransportOrder)
            url_atual = self.driver.current_url or ""
            if "TransportOrder" not in url_atual:
                # Nao estamos na pagina — navega
                self.browser.navegar_vendas_conhecimento_lista()
                time.sleep(3)

            # SEMPRE clica na aba "Por referencia" (pode estar em outra aba)
            try:
                aba = self.driver.find_element(By.XPATH,
                    "//a[contains(.,'Por refer')]"
                )
                aba.click()
                time.sleep(2)
            except Exception:
                pass

            # SEMPRE tenta abrir filtro se campo nao esta visivel
            # (apos pesquisa anterior ou ao vir de outro voo, os campos somem)
            if not self._campo_integracao_visivel():
                logger.debug("Campo nao visivel, clicando em toogleFilter...")
                self._tentar_abrir_filtro()
                time.sleep(2)

            # Preenche campo "Numero integracao"
            campo_integracao = self.wait.until(
                EC.element_to_be_clickable((By.XPATH,
                    "//input[contains(@id,'Integration') or contains(@name,'Integration') "
                    "or contains(@id,'integration')]"
                    " | //label[contains(.,'integra')]//following::input[1]"
                    " | //input[contains(@placeholder,'integra')]"
                ))
            )
            campo_integracao.click()
            campo_integracao.send_keys(Keys.CONTROL, "a")
            campo_integracao.send_keys(Keys.BACKSPACE)
            campo_integracao.send_keys(numero_cte)
            time.sleep(0.5)

            # Clica pesquisar
            botao_pesquisar = self.wait.until(
                EC.element_to_be_clickable((By.XPATH,
                    "//button[contains(.,'Pesquisar')]"
                ))
            )
            botao_pesquisar.click()
            time.sleep(4)

            # Le o AWB da coluna "N. documento" na tabela de resultados
            awb = self._ler_awb_resultado()

            if awb:
                logger.info(f"CTe {numero_cte} -> AWB {awb}")
            else:
                logger.warning(f"CTe {numero_cte}: AWB nao encontrado")

            return awb

        except TimeoutException as e:
            logger.error(f"Timeout ao buscar AWB do CTe {numero_cte}: {e}")
            return ""
        except Exception as e:
            logger.error(f"Erro ao buscar AWB do CTe {numero_cte}: {e}")
            return ""

    def _campo_integracao_visivel(self) -> bool:
        """Verifica se o campo Numero integracao esta visivel."""
        try:
            campo = self.driver.find_element(By.XPATH,
                "//input[contains(@id,'Integration') or contains(@name,'Integration') "
                "or contains(@id,'integration')]"
                " | //label[contains(.,'integra')]//following::input[1]"
                " | //input[contains(@placeholder,'integra')]"
            )
            return campo.is_displayed()
        except Exception:
            return False

    def _tentar_abrir_filtro(self) -> bool:
        """
        Clica no botao de filtro para mostrar os campos.
        HTML exato:
        <button class="toogleFilter btn btn-default" type="button">
            <i class="fal fa-filter"></i>
        </button>
        Classe e "toogleFilter" (typo com 2 'o' do desenvolvedor).
        """
        from selenium.webdriver.support.ui import WebDriverWait
        from selenium.webdriver.support import expected_conditions as EC

        # Aguarda o botao estar presente e clicavel (max 10s)
        try:
            botao = WebDriverWait(self.driver, 10).until(
                EC.element_to_be_clickable((By.CSS_SELECTOR, "button.toogleFilter"))
            )
            botao.click()
            time.sleep(2)
            if self._campo_integracao_visivel():
                logger.debug("Filtro aberto via button.toogleFilter (wait+click)")
                return True
        except Exception as e:
            logger.debug(f"Estrategia 1 falhou: {e}")

        # ESTRATEGIA 2: JavaScript direto
        try:
            self.driver.execute_script(
                "var btn = document.querySelector('button.toogleFilter'); "
                "if (btn) { btn.click(); }"
            )
            time.sleep(2)
            if self._campo_integracao_visivel():
                logger.debug("Filtro aberto via JS toogleFilter")
                return True
        except Exception as e:
            logger.debug(f"Estrategia 2 falhou: {e}")

        # ESTRATEGIA 3: Busca TODOS botoes com toogleFilter (pode ter mais de 1)
        try:
            botoes = self.driver.find_elements(By.CSS_SELECTOR, "button.toogleFilter")
            logger.debug(f"Encontrados {len(botoes)} botoes toogleFilter")
            for btn in botoes:
                try:
                    if btn.is_displayed():
                        self.driver.execute_script("arguments[0].click();", btn)
                        time.sleep(2)
                        if self._campo_integracao_visivel():
                            logger.debug("Filtro aberto via loop toogleFilter")
                            return True
                except Exception:
                    continue
        except Exception as e:
            logger.debug(f"Estrategia 3 falhou: {e}")

        logger.warning("NAO conseguiu abrir o filtro toogleFilter!")
        return False

    def _ler_awb_resultado(self) -> str:
        """
        Le o AWB (N. documento) da tabela de resultados.
        
        IMPORTANTE: CTes sao reutilizados no Nexlog. Se a busca retornar
        multiplos resultados, pega o AWB da linha com DATA MAIS RECENTE.
        """
        try:
            # Verifica se tem resultados
            try:
                msg = self.driver.find_element(By.XPATH,
                    "//*[contains(.,'Nenhum registro')]"
                )
                if msg.is_displayed():
                    return ""
            except Exception:
                pass

            # Busca todas as linhas da tabela
            linhas = self.driver.find_elements(By.XPATH,
                "//table//tbody//tr"
            )

            if not linhas:
                return ""

            # Se so tem 1 linha, pega direto
            if len(linhas) == 1:
                celulas = linhas[0].find_elements(By.TAG_NAME, "td")
                for celula in celulas:
                    texto = celula.text.strip()
                    if texto.startswith("127") and len(texto) >= 10 and texto.isdigit():
                        return texto
                return ""

            # Multiplas linhas — pega a mais recente pela data
            # Formato da data no Nexlog: "DD/MM/YYYY HH:MM:SS"
            melhor_awb = ""
            melhor_data = ""

            for linha in linhas:
                try:
                    celulas = linha.find_elements(By.TAG_NAME, "td")
                    awb_linha = ""
                    data_linha = ""

                    for celula in celulas:
                        texto = celula.text.strip()
                        # Identifica AWB (comeca com 127, 10+ digitos)
                        if texto.startswith("127") and len(texto) >= 10 and texto.isdigit():
                            awb_linha = texto
                        # Identifica data (formato DD/MM/YYYY HH:MM:SS ou DD/MM/YYYY HH:MM)
                        elif re.match(r'\d{2}/\d{2}/\d{4}\s+\d{2}:\d{2}', texto):
                            data_linha = texto

                    if awb_linha:
                        if not melhor_awb:
                            melhor_awb = awb_linha
                            melhor_data = data_linha
                        elif data_linha and melhor_data:
                            if self._data_mais_recente(data_linha, melhor_data):
                                melhor_awb = awb_linha
                                melhor_data = data_linha
                        elif data_linha and not melhor_data:
                            melhor_awb = awb_linha
                            melhor_data = data_linha

                except Exception:
                    continue

            if melhor_awb:
                if len(linhas) > 1:
                    logger.debug(f"Multiplos resultados ({len(linhas)} linhas) - "
                               f"pegou AWB mais recente: {melhor_awb} ({melhor_data})")
                return melhor_awb

            # Fallback: regex no texto da tabela
            page_text = self.driver.find_element(
                By.XPATH, "//table//tbody"
            ).text
            match = re.search(r'\b(127\d{7,})\b', page_text)
            if match:
                return match.group(1)

            return ""

        except Exception:
            return ""

    def _data_mais_recente(self, data1: str, data2: str) -> bool:
        """
        Compara duas datas no formato DD/MM/YYYY HH:MM:SS.
        Retorna True se data1 e mais recente que data2.
        """
        try:
            from datetime import datetime
            try:
                d1 = datetime.strptime(data1[:19], "%d/%m/%Y %H:%M:%S")
            except ValueError:
                d1 = datetime.strptime(data1[:16], "%d/%m/%Y %H:%M")
            try:
                d2 = datetime.strptime(data2[:19], "%d/%m/%Y %H:%M:%S")
            except ValueError:
                d2 = datetime.strptime(data2[:16], "%d/%m/%Y %H:%M")
            return d1 > d2
        except Exception:
            return data1 > data2

    def verificar_servico_awb(self, awb: str) -> str:
        """
        Verifica o servico do AWB na tabela de resultados.
        Apos buscar_awb_do_cte, a tabela mostra a linha do AWB com colunas
        incluindo "Servico" (ex: E-GOLLOG, MELI, etc.).
        
        Olha a coluna "Servico" da primeira linha visivel na tabela.
        
        Returns:
            String com o servico (ex: "E-GOLLOG", "MELI") ou "" se nao encontrar
        """
        try:
            # Tenta ler o texto da primeira linha da tabela
            linha = self.driver.find_element(By.XPATH, "//table//tbody//tr[1]")
            texto_linha = linha.text.upper()
            
            # Verifica se contem MELI
            if "MELI" in texto_linha:
                return "MELI"
            
            # Tenta buscar especificamente na coluna Servico
            celulas = linha.find_elements(By.TAG_NAME, "td")
            for celula in celulas:
                texto = celula.text.strip().upper()
                if "MELI" in texto or "BELLY" in texto:
                    return texto
            
            return ""
        except Exception:
            return ""

    def adicionar_comentario_critico(self, awb: str, texto_comentario: str) -> bool:
        """
        Adiciona um comentario critico em um AWB no Nexlog.
        Fluxo:
        1. Cola AWB no campo pesquisa rapida > clica no alvo
        2. Abre tela de Rastreio
        3. Clica "Adicionar comentarios"
        4. Digita texto no campo Comentario
        5. Marca checkbox "Critico?"
        6. Clica botao "Adicionar"
        7. Fecha modal Comentarios
        8. Fecha modal Rastreio

        Returns:
            True se adicionou com sucesso
        """
        if not awb:
            logger.warning("AWB vazio - nao pode adicionar comentario")
            return False

        try:
            # 1. Busca rapida pelo AWB
            self.browser.busca_rapida(awb)
            time.sleep(3)

            # 2. Clica em "Adicionar comentarios" (link no canto superior direito)
            link_comentario = self.wait.until(
                EC.element_to_be_clickable((By.XPATH,
                    "//a[contains(.,'Adicionar coment')]"
                    " | //*[contains(.,'Adicionar coment') and (self::a or self::button)]"
                ))
            )
            link_comentario.click()
            time.sleep(3)

            # 3. Localiza o campo de texto "Comentario"
            campo_comentario = self.wait.until(
                EC.element_to_be_clickable((By.XPATH,
                    "//textarea"
                    " | //div[contains(@class,'modal')]//textarea"
                ))
            )
            campo_comentario.clear()
            campo_comentario.send_keys(texto_comentario)
            time.sleep(0.5)

            # 4. Marca checkbox "Critico?"
            checkbox_critico = self.wait.until(
                EC.presence_of_element_located((By.XPATH,
                    "//input[@type='checkbox'][following-sibling::*[contains(.,'tico')] "
                    "or ancestor::label[contains(.,'tico')]]"
                    " | //label[contains(.,'tico')]//input[@type='checkbox']"
                    " | //input[@type='checkbox'][contains(@id,'ritico') "
                    "or contains(@id,'ritical') or contains(@name,'ritico')]"
                ))
            )
            if not checkbox_critico.is_selected():
                try:
                    checkbox_critico.click()
                except Exception:
                    self.driver.execute_script("arguments[0].click();", checkbox_critico)
            time.sleep(0.5)

            # 5. Clica botao "Adicionar" (azul com +)
            botao_adicionar = self.wait.until(
                EC.element_to_be_clickable((By.XPATH,
                    "//button[contains(.,'Adicionar')]"
                    " | //button[contains(@class,'btn-primary') and contains(@class,'add')]"
                ))
            )
            botao_adicionar.click()
            time.sleep(2)

            # 6. Fecha modal Comentarios (botao "Fechar")
            try:
                botao_fechar_comentario = self.wait.until(
                    EC.element_to_be_clickable((By.XPATH,
                        "(//button[contains(.,'Fechar')])[last()]"
                    ))
                )
                botao_fechar_comentario.click()
                time.sleep(1)
            except Exception:
                pass

            # 7. Fecha modal Rastreio
            try:
                botao_fechar_rastreio = self.driver.find_element(By.XPATH,
                    "//div[contains(@class,'modal')]//button[contains(@class,'close')]"
                    " | //div[contains(@class,'modal')]//button[contains(.,'Fechar')]"
                )
                botao_fechar_rastreio.click()
                time.sleep(1)
            except Exception:
                self.browser._fechar_modais()

            logger.info(f"Comentario adicionado: AWB {awb} -> '{texto_comentario}'")
            return True

        except TimeoutException as e:
            logger.error(f"Timeout ao adicionar comentario no AWB {awb}: {e}")
            self.browser._fechar_modais()
            return False
        except Exception as e:
            logger.error(f"Erro ao adicionar comentario no AWB {awb}: {e}")
            self.browser._fechar_modais()
            return False

    def processar_termos_voo(self, consulta: ConsultaMDFe) -> Dict[str, str]:
        """
        Processa todos os termos de um voo:
        - Para cada CTe com termo, busca o AWB
        - Adiciona comentario critico no AWB

        Returns:
            Dict mapeando CTe -> AWB (para uso posterior na liberacao)
        """
        mapa_cte_awb = {}
        ctes_processados = set()
        ctes_unicos = list(set(consulta.ctes_retidos))

        for cte in ctes_unicos:
            if cte in ctes_processados:
                continue

            logger.info(f"Processando CTe {cte}...")

            awb = self.buscar_awb_do_cte(cte)
            if not awb:
                logger.warning(f"CTe {cte}: AWB nao encontrado - pulando")
                continue

            mapa_cte_awb[cte] = awb
            comentario = consulta.comentario_para_cte(cte)
            sucesso = self.adicionar_comentario_critico(awb, comentario)

            if sucesso:
                logger.info(f"  OK: AWB {awb} <- '{comentario}'")
            else:
                logger.error(f"  FALHA: AWB {awb} <- '{comentario}'")

            ctes_processados.add(cte)
            time.sleep(1)

        return mapa_cte_awb
