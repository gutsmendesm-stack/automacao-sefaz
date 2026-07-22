"""
Modulo de consulta no site da SEFAZ-AL (transportadoras.sefaz.al.gov.br).
Realiza login, consulta Analise MDF-e por chave, e extrai relatorio.

Fluxo do site:
1. Acessa transportadoras.sefaz.al.gov.br/#/
2. Clica no menu "Conta" (dropdown no header direito)
3. Clica em "Entrar" (abre modal de autenticacao)
4. Preenche "Seu usuario" e "Sua senha"
5. Clica botao "Entrar" (azul, dentro do modal)
6. Apos login: 3 botoes (Consultar TADe, Consulta Analise MDF-e, Registro de Passagem)
7. Clica "Consulta Analise MDF-e"
8. Preenche campo "Chave/Numero do MDF-e"
9. Clica "Pesquisar"
10. Resultado: tabela com Status, Data, Posto, N MDF-e, Emitente, TAs emitidos
11. Clica "Imprimir Relatorio" -> abre/baixa PDF com detalhes dos termos
"""

import time
import re
import logging
from typing import Optional

from selenium import webdriver
from selenium.webdriver.common.by import By
from selenium.webdriver.common.keys import Keys
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.common.exceptions import TimeoutException

from config import config
from models.termo import ConsultaMDFe, StatusMDFe
from modules.parser import parsear_relatorio_sefaz, parsear_relatorio_pdf

logger = logging.getLogger(__name__)


class SefazConsulta:
    """Gerencia consultas no site da SEFAZ-AL."""

    def __init__(self, driver: webdriver.Chrome):
        """Usa o mesmo driver do navegador principal."""
        self.driver = driver
        self.wait = WebDriverWait(driver, config.timeout_padrao)
        self._logado = False
        self._aba_sefaz = None
        self._aba_original = None

    def abrir_sefaz(self):
        """
        Abre o site da SEFAZ em uma aba.
        Se ja tem uma aba SEFAZ aberta, REUTILIZA (nao abre nova).
        Isso evita abrir multiplas abas e fazer login novamente.
        """
        self._aba_original = self.driver.current_window_handle

        # Verifica se ja tem aba SEFAZ aberta e ainda valida
        if self._aba_sefaz and self._aba_sefaz in self.driver.window_handles:
            # Reutiliza a aba existente (ja logada)
            self.driver.switch_to.window(self._aba_sefaz)
            logger.debug("SEFAZ: Reutilizando aba ja aberta")
            return

        # Nao tem aba aberta — abre nova
        self.driver.execute_script("window.open('');")
        time.sleep(1)
        abas = self.driver.window_handles
        self._aba_sefaz = abas[-1]
        self.driver.switch_to.window(self._aba_sefaz)

        self.driver.get(config.url_sefaz)
        time.sleep(5)

    def login(self, usuario: str = None, senha: str = None):
        """
        Faz login no portal de transportadoras da SEFAZ-AL.
        Fluxo: Conta -> Entrar -> Modal autenticacao -> Preenche -> Botao Entrar
        Se falhar, mostra pausa inteligente para login manual.
        """
        usuario = usuario or config.sefaz.usuario
        senha = senha or config.sefaz.senha

        if not usuario or not senha:
            raise ValueError("Credenciais da SEFAZ nao configuradas.")

        # Muda para aba da SEFAZ
        if self._aba_sefaz:
            self.driver.switch_to.window(self._aba_sefaz)

        try:
            # Tenta via link "login" no meio da pagina OU menu "Conta"
            try:
                # Primeiro tenta o link "login" direto na mensagem
                link_login = WebDriverWait(self.driver, 5).until(
                    EC.element_to_be_clickable((By.XPATH,
                        "//a[contains(.,'login') and not(contains(@class,'nav'))]"
                        " | //a[@href='#' and contains(.,'login')]"
                    ))
                )
                link_login.click()
                time.sleep(2)
            except TimeoutException:
                # Se nao achou, tenta via menu Conta
                menu_conta = self.wait.until(
                    EC.element_to_be_clickable((By.XPATH,
                        "//a[contains(.,'Conta')]"
                        " | //li[contains(@class,'dropdown')]//a[contains(.,'Conta')]"
                    ))
                )
                menu_conta.click()
                time.sleep(1)

                opcao_entrar = self.wait.until(
                    EC.element_to_be_clickable((By.XPATH,
                        "//a[contains(.,'Entrar')]"
                        " | //div[contains(@class,'dropdown-menu')]//a[contains(.,'Entrar')]"
                    ))
                )
                opcao_entrar.click()
                time.sleep(2)

            # Aguarda modal de autenticacao abrir
            # Campo usuario com placeholder "Seu usuario"
            campo_usuario = self.wait.until(
                EC.element_to_be_clickable((By.XPATH,
                    "//input[contains(@placeholder,'usu') or contains(@placeholder,'Usu')]"
                    " | //div[contains(@class,'modal')]//input[@type='text']"
                    " | //div[contains(@class,'modal')]//input[not(@type='password') "
                    "and not(@type='checkbox') and not(@type='hidden')]"
                ))
            )
            campo_usuario.clear()
            campo_usuario.send_keys(usuario)

            # Campo senha com placeholder "Sua senha"
            campo_senha = self.wait.until(
                EC.element_to_be_clickable((By.XPATH,
                    "//input[@type='password']"
                ))
            )
            campo_senha.clear()
            campo_senha.send_keys(senha)

            # Botao "Entrar" (azul, dentro do modal)
            botao_entrar = self.wait.until(
                EC.element_to_be_clickable((By.XPATH,
                    "//div[contains(@class,'modal')]//button[contains(.,'Entrar')]"
                    " | //button[contains(@class,'btn-primary') and contains(.,'Entrar')]"
                    " | //button[contains(.,'Entrar') and @type='submit']"
                ))
            )
            botao_entrar.click()
            time.sleep(5)

            # Verifica login bem sucedido
            try:
                WebDriverWait(self.driver, 10).until(
                    EC.presence_of_element_located((By.XPATH,
                        "//*[contains(.,'logado como')]"
                        " | //*[contains(.,'Consultar TADe')]"
                        " | //*[contains(.,'Consulta An')]"
                    ))
                )
                self._logado = True
                logger.info("SEFAZ: Login realizado com sucesso!")
            except TimeoutException:
                # Pode ter dado erro - tenta pausa inteligente
                self._aguardar_login_manual_sefaz()

        except Exception as e:
            logger.error(f"SEFAZ: Erro no login automatico: {e}")
            # Tenta pausa inteligente como fallback
            self._aguardar_login_manual_sefaz()

    def _aguardar_login_manual_sefaz(self):
        """
        Pausa inteligente para login manual na SEFAZ.
        Mostra popup e aguarda o usuario fazer login.
        """
        import tkinter as tk
        from tkinter import messagebox

        logger.warning("SEFAZ: Login automatico falhou - aguardando login manual")

        root = tk.Tk()
        root.withdraw()
        root.attributes("-topmost", True)

        messagebox.showinfo(
            "Login Necessario - SEFAZ",
            "O login automatico na SEFAZ falhou.\n\n"
            "Possiveis causas:\n"
            "- Senha alterada\n"
            "- Site fora do ar\n"
            "- Captcha ou verificacao\n\n"
            "Por favor, faca login manualmente na janela do Chrome\n"
            "e depois clique OK para continuar.",
            parent=root
        )
        root.destroy()

        # Verifica se logou
        try:
            WebDriverWait(self.driver, 30).until(
                EC.presence_of_element_located((By.XPATH,
                    "//*[contains(.,'logado como')]"
                    " | //*[contains(.,'Consultar TADe')]"
                    " | //*[contains(.,'Consulta An')]"
                ))
            )
            self._logado = True
            logger.info("SEFAZ: Login manual realizado com sucesso!")
        except TimeoutException:
            logger.error("SEFAZ: Login nao detectado apos intervencao manual")
            raise RuntimeError("Falha no login da SEFAZ")

    def navegar_consulta_analise_mdfe(self):
        """
        Clica no botao 'Consulta Analise MDF-e' (botao azul grande com icone).
        Na pagina inicial apos login, existem 3 cards azuis:
          - Consultar TADe
          - Consulta Analise MDF-e  <-- este
          - Registro de Passagem
        """
        if self._aba_sefaz:
            self.driver.switch_to.window(self._aba_sefaz)

        try:
            # Espera a pagina carregar completamente (os cards azuis)
            time.sleep(3)

            # Estrategia 1: Busca especifica pelo texto "Consulta Análise MDF-e"
            # Usa text() para nao pegar nós pai que contenham o texto em filhos
            botao_mdfe = None

            seletores = [
                # Link ou span cujo proprio texto contem "lise MDF" (Análise MDF-e)
                "//a[contains(text(),'lise MDF')]",
                "//span[contains(text(),'lise MDF')]/ancestor::a",
                "//p[contains(text(),'lise MDF')]/ancestor::a",
                # Div com texto direto
                "//div[contains(text(),'lise MDF')]",
                "//span[contains(text(),'lise MDF')]",
                # Card com texto "Consulta" e "MDF" - pega o card inteiro
                "//a[.//text()[contains(.,'MDF')]]",
                # Qualquer elemento clicavel com texto MDF-e
                "//*[contains(text(),'MDF-e') and (self::a or self::button or self::span)]",
                # Segundo card/botao azul (posicional - Consulta Analise e o do meio)
                "(//a[contains(@class,'card') or contains(@class,'btn') or contains(@class,'panel')])[2]",
            ]

            for xpath in seletores:
                try:
                    elementos = self.driver.find_elements(By.XPATH, xpath)
                    for elem in elementos:
                        if elem.is_displayed():
                            texto = (elem.text or "").upper()
                            # Confirma que NAO e "Consultar TADe" nem "Registro"
                            if "TADE" not in texto or "MDF" in texto:
                                if "REGISTRO" not in texto:
                                    botao_mdfe = elem
                                    break
                    if botao_mdfe:
                        break
                except Exception:
                    continue

            if botao_mdfe:
                try:
                    botao_mdfe.click()
                except Exception:
                    self.driver.execute_script("arguments[0].click();", botao_mdfe)
                time.sleep(3)
                logger.info("SEFAZ: Na pagina de Consulta Analise MDF-e")
            else:
                # Fallback: tenta via URL direta
                logger.warning("SEFAZ: Botao 'Consulta Analise MDF-e' nao encontrado, "
                             "tentando URL direta")
                self.driver.get(
                    "https://transportadoras.sefaz.al.gov.br/#/painel-mdfes-analisados"
                )
                time.sleep(3)

        except Exception as e:
            logger.error(f"SEFAZ: Erro ao navegar para Consulta Analise MDF-e: {e}")
            # Fallback URL direta
            self.driver.get(
                "https://transportadoras.sefaz.al.gov.br/#/painel-mdfes-analisados"
            )
            time.sleep(3)

    def _encontrar_campo_chave(self):
        """
        Encontra o campo de input da chave MDF-e na pagina de consulta.
        O site da SEFAZ-AL usa Angular e os campos sao renderizados dinamicamente.
        Tenta multiplas estrategias para encontrar o campo.
        """
        estrategias = [
            # 1. Por placeholder (mais comum em SPAs Angular)
            (By.XPATH, "//input[contains(@placeholder,'have') or contains(@placeholder,'MDF') "
                       "or contains(@placeholder,'Chave') or contains(@placeholder,'chave') "
                       "or contains(@placeholder,'mero')]"),
            # 2. Por ng-model (Angular 1.x)
            (By.XPATH, "//input[contains(@ng-model,'chave') or contains(@ng-model,'Chave') "
                       "or contains(@ng-model,'numero') or contains(@ng-model,'mdfe')]"),
            # 3. Por formControlName (Angular 2+)
            (By.XPATH, "//input[contains(@formcontrolname,'chave') or "
                       "contains(@formcontrolname,'Chave') or "
                       "contains(@formcontrolname,'numero') or "
                       "contains(@formcontrolname,'mdfe')]"),
            # 4. Por CSS class com input de texto visivel
            (By.CSS_SELECTOR, "input.form-control[type='text']"),
            # 5. Qualquer input text dentro de form-group
            (By.XPATH, "//div[contains(@class,'form-group')]//input[@type='text' or not(@type)]"),
            # 6. Input proximo a label com texto "Chave" ou "MDF"
            (By.XPATH, "//label[contains(.,'have') or contains(.,'MDF') or contains(.,'mero')]"
                       "/following::input[1]"),
            # 7. Qualquer input visivel que nao seja hidden/checkbox/radio
            (By.XPATH, "//input[not(@type='hidden') and not(@type='checkbox') "
                       "and not(@type='radio') and not(@type='password') "
                       "and not(@type='submit') and not(@type='button')]"),
        ]

        for by, selector in estrategias:
            try:
                elementos = self.driver.find_elements(by, selector)
                # Filtra apenas os visiveis
                visiveis = [e for e in elementos if e.is_displayed()]
                if visiveis:
                    # Prefere o que esta vazio ou menor (campo de input principal)
                    for elem in visiveis:
                        valor_atual = elem.get_attribute("value") or ""
                        if len(valor_atual) < 5:  # Campo vazio ou quase vazio
                            logger.debug(f"SEFAZ: Campo encontrado via estrategia: {selector[:60]}")
                            return elem
                    # Se todos tem valor, retorna o primeiro visivel
                    logger.debug(f"SEFAZ: Campo encontrado (com valor) via: {selector[:60]}")
                    return visiveis[0]
            except Exception:
                continue

        return None

    def _encontrar_botao_pesquisar(self):
        """
        Encontra o botao de pesquisar na pagina de consulta.
        Tenta multiplas estrategias.
        """
        estrategias = [
            # 1. Botao com texto "Pesquisar"
            (By.XPATH, "//button[contains(.,'Pesquisar')]"),
            # 2. Botao com texto "Consultar"
            (By.XPATH, "//button[contains(.,'Consultar')]"),
            # 3. Botao com texto "Buscar"
            (By.XPATH, "//button[contains(.,'Buscar')]"),
            # 4. Botao btn-primary (geralmente o de acao principal)
            (By.CSS_SELECTOR, "button.btn-primary"),
            # 5. Botao com icone de busca (fa-search)
            (By.XPATH, "//button[.//i[contains(@class,'fa-search') or contains(@class,'search')]]"),
            # 6. Botao type=submit
            (By.XPATH, "//button[@type='submit']"),
            # 7. Input type=submit
            (By.XPATH, "//input[@type='submit']"),
        ]

        for by, selector in estrategias:
            try:
                elementos = self.driver.find_elements(by, selector)
                visiveis = [e for e in elementos if e.is_displayed() and e.is_enabled()]
                if visiveis:
                    logger.debug(f"SEFAZ: Botao pesquisar encontrado via: {selector[:60]}")
                    return visiveis[0]
            except Exception:
                continue

        return None

    def consultar_chave_mdfe(self, chave: str) -> Optional[ConsultaMDFe]:
        """
        Consulta uma chave de MDF-e no site da SEFAZ.
        Usa estrategias multiplas para encontrar o campo de input (Angular SPA).

        Returns:
            ConsultaMDFe com dados extraidos, ou None se deu erro/nao encontrou
        """
        if self._aba_sefaz:
            self.driver.switch_to.window(self._aba_sefaz)

        try:
            # Aguarda a pagina carregar completamente
            time.sleep(3)

            # DEBUG: loga inputs visiveis para diagnostico
            self._debug_inputs_pagina()

            # Campo "Chave/Numero do MDF-e" - estrategia robusta
            campo_chave = self._encontrar_campo_chave()

            if not campo_chave:
                logger.error("SEFAZ: Campo de chave NAO encontrado! Tentando aguardar mais...")
                time.sleep(5)
                campo_chave = self._encontrar_campo_chave()

            if not campo_chave:
                logger.error("SEFAZ: Campo de chave NAO encontrado apos retry!")
                logger.error(f"SEFAZ: URL atual: {self.driver.current_url}")
                logger.error(f"SEFAZ: Titulo: {self.driver.title}")
                # Tenta renavegar para a pagina de consulta
                self.navegar_consulta_analise_mdfe()
                time.sleep(3)
                campo_chave = self._encontrar_campo_chave()

            if not campo_chave:
                logger.error("SEFAZ: FALHA TOTAL - campo de chave nao encontrado")
                return None

            # Limpa e preenche o campo
            try:
                campo_chave.click()
            except Exception:
                self.driver.execute_script("arguments[0].click(); arguments[0].focus();", campo_chave)

            time.sleep(0.3)
            campo_chave.send_keys(Keys.CONTROL, "a")
            campo_chave.send_keys(Keys.BACKSPACE)
            time.sleep(0.3)

            # Digita a chave caractere por caractere (mais seguro em SPAs)
            campo_chave.send_keys(chave)
            time.sleep(1)

            # Verifica se o valor foi preenchido corretamente
            valor_digitado = campo_chave.get_attribute("value") or ""
            if len(valor_digitado) < 40:
                logger.warning(f"SEFAZ: Campo pode nao ter recebido a chave completa. "
                             f"Valor: '{valor_digitado}' ({len(valor_digitado)} chars)")
                # Tenta via JavaScript
                self.driver.execute_script(
                    "arguments[0].value = arguments[1]; "
                    "arguments[0].dispatchEvent(new Event('input', {bubbles: true})); "
                    "arguments[0].dispatchEvent(new Event('change', {bubbles: true}));",
                    campo_chave, chave
                )
                time.sleep(1)

            # Botao "Pesquisar" - estrategia robusta
            botao_pesquisar = self._encontrar_botao_pesquisar()

            if not botao_pesquisar:
                logger.warning("SEFAZ: Botao pesquisar nao encontrado, tentando ENTER")
                campo_chave.send_keys(Keys.ENTER)
            else:
                try:
                    botao_pesquisar.click()
                except Exception:
                    self.driver.execute_script("arguments[0].click();", botao_pesquisar)

            time.sleep(8)

            # Verifica se deu erro "Nenhuma analise encontrada"
            try:
                msg_erro = self.driver.find_element(By.XPATH,
                    "//*[contains(text(),'Nenhuma an') or contains(text(),'nenhuma an') "
                    "or contains(text(),'não encontrad') or contains(text(),'nao encontrad') "
                    "or contains(text(),'Nenhum resultado')]"
                )
                if msg_erro.is_displayed():
                    logger.info(f"SEFAZ: Nenhuma analise encontrada para chave {chave[:20]}...")
                    resultado = ConsultaMDFe(chave=chave, status=StatusMDFe.SEM_ANALISE)
                    return resultado
            except Exception:
                pass

            # Verifica se apareceu uma tabela de resultados
            try:
                tabela = WebDriverWait(self.driver, 5).until(
                    EC.presence_of_element_located((By.XPATH,
                        "//table[.//th or .//td] | //div[contains(@class,'table')]"
                    ))
                )
                logger.info("SEFAZ: Tabela de resultados encontrada!")
            except TimeoutException:
                logger.warning("SEFAZ: Nenhuma tabela encontrada apos pesquisa")

            # Resultado encontrado - clica em "Imprimir Relatorio"
            texto_relatorio = self._imprimir_relatorio()

            if texto_relatorio:
                from modules.parser import parsear_relatorio_sefaz
                resultado = parsear_relatorio_sefaz(texto_relatorio)
                if not resultado.chave:
                    resultado.chave = chave
                return resultado
            else:
                # Tenta extrair dados direto da pagina (tabela de resultados)
                resultado_direto = self._extrair_resultado_tabela(chave)
                if resultado_direto:
                    return resultado_direto

                logger.warning("SEFAZ: Nao conseguiu extrair relatorio")
                return ConsultaMDFe(chave=chave, status=StatusMDFe.DESCONHECIDO)

        except Exception as e:
            logger.error(f"SEFAZ: Erro na consulta: {e}")
            # Log adicional de debug
            try:
                logger.error(f"SEFAZ: URL no erro: {self.driver.current_url}")
                logger.error(f"SEFAZ: Titulo no erro: {self.driver.title}")
            except Exception:
                pass
            return None

    def _debug_inputs_pagina(self):
        """Loga todos os inputs visiveis para debug."""
        try:
            inputs = self.driver.find_elements(By.TAG_NAME, "input")
            visiveis = [i for i in inputs if i.is_displayed()]
            logger.debug(f"SEFAZ DEBUG: {len(visiveis)} inputs visiveis na pagina:")
            for inp in visiveis[:10]:
                tipo = inp.get_attribute("type") or "text"
                ph = inp.get_attribute("placeholder") or ""
                ng = inp.get_attribute("ng-model") or ""
                fc = inp.get_attribute("formcontrolname") or ""
                cls = inp.get_attribute("class") or ""
                logger.debug(f"  <input type='{tipo}' placeholder='{ph}' "
                           f"ng-model='{ng}' formcontrolname='{fc}' "
                           f"class='{cls[:40]}'>")
        except Exception as e:
            logger.debug(f"SEFAZ DEBUG: erro ao listar inputs: {e}")

    def _extrair_resultado_tabela(self, chave: str) -> Optional[ConsultaMDFe]:
        """
        Tenta extrair o resultado diretamente da tabela exibida na pagina,
        sem precisar clicar em 'Imprimir Relatorio'.
        Util quando o botao de imprimir nao funciona.
        """
        try:
            # Busca texto da tabela
            tabelas = self.driver.find_elements(By.TAG_NAME, "table")
            texto_total = ""
            for t in tabelas:
                if t.is_displayed():
                    texto_total += t.text + "\n"

            if len(texto_total) < 20:
                return None

            # Verifica se tem info de status
            import re
            texto_upper = texto_total.upper()

            status = StatusMDFe.DESCONHECIDO
            if "COM PEND" in texto_upper:
                status = StatusMDFe.ANALISADO_COM_PENDENCIAS
            elif "SEM PEND" in texto_upper:
                status = StatusMDFe.ANALISADO_SEM_PENDENCIAS
            elif "EM AN" in texto_upper:
                status = StatusMDFe.EM_ANALISE

            # Tenta extrair numero de TAs
            match_ta = re.search(r'(\d+)\s*TA', texto_total, re.IGNORECASE)
            total_termos = int(match_ta.group(1)) if match_ta else 0

            resultado = ConsultaMDFe(
                chave=chave,
                status=status,
                total_termos=total_termos,
            )

            logger.info(f"SEFAZ: Resultado extraido da tabela: status={status.value}, "
                       f"termos={total_termos}")
            return resultado

        except Exception as e:
            logger.debug(f"SEFAZ: Erro ao extrair resultado da tabela: {e}")
            return None

    def _imprimir_relatorio(self) -> str:
        """
        Clica no botao "Imprimir Relatorio" e extrai o texto.
        
        O site EXIBE o PDF numa nova aba. Precisamos:
        1. Detectar a nova aba RAPIDO (polling 0.5s)
        2. Trocar para ela antes que feche
        3. Extrair texto do body
        4. Fechar e voltar
        Fallback: se baixou o PDF, le da pasta de downloads.
        """
        import os
        import glob

        pasta = config.pasta_downloads

        try:
            # Registra abas e PDFs ANTES de clicar
            abas_antes = set(self.driver.window_handles)
            pdfs_antes = set()
            if os.path.exists(pasta):
                pdfs_antes = set(glob.glob(os.path.join(pasta, "*.pdf")))

            # Clica "Imprimir Relatorio"
            botao_imprimir = self.wait.until(
                EC.element_to_be_clickable((By.XPATH,
                    "//button[contains(.,'Imprimir Relat')]"
                    " | //button[contains(.,'Imprimir')]"
                    " | //a[contains(.,'Imprimir Relat')]"
                ))
            )
            botao_imprimir.click()

            # Monitora abas a cada 0.5s — nova aba pode aparecer e sumir rapido
            texto_extraido = ""
            tempo_inicio = time.time()

            while time.time() - tempo_inicio < 15:
                time.sleep(0.5)
                try:
                    abas_atuais = set(self.driver.window_handles)
                    novas_abas = abas_atuais - abas_antes

                    if novas_abas:
                        # Nova aba detectada — troca IMEDIATAMENTE
                        nova_aba = list(novas_abas)[0]
                        self.driver.switch_to.window(nova_aba)
                        time.sleep(3)  # Espera PDF carregar

                        # Extrai texto
                        try:
                            texto_extraido = self.driver.find_element(
                                By.TAG_NAME, "body"
                            ).text
                        except Exception:
                            texto_extraido = ""

                        # Fecha aba do relatorio e volta
                        try:
                            self.driver.close()
                        except Exception:
                            pass

                        try:
                            abas_restantes = self.driver.window_handles
                            if self._aba_sefaz in abas_restantes:
                                self.driver.switch_to.window(self._aba_sefaz)
                            elif abas_restantes:
                                self.driver.switch_to.window(abas_restantes[0])
                        except Exception:
                            pass

                        if texto_extraido and len(texto_extraido) > 50:
                            logger.info("SEFAZ: Relatorio extraido da aba PDF")
                            return texto_extraido
                        break
                except Exception:
                    continue

            # Fallback: se baixou o PDF (caso Chrome tenha baixado)
            time.sleep(3)
            if os.path.exists(pasta):
                pdfs_atuais = set(glob.glob(os.path.join(pasta, "*.pdf")))
                novos_pdfs = pdfs_atuais - pdfs_antes
                for pdf_path in novos_pdfs:
                    if os.path.getsize(pdf_path) > 100:
                        import pdfplumber
                        logger.info(f"SEFAZ: Relatorio baixado: {os.path.basename(pdf_path)}")
                        with pdfplumber.open(pdf_path) as pdf:
                            texto = ""
                            for page in pdf.pages:
                                texto += (page.extract_text() or "") + "\n"
                            if texto and len(texto) > 50:
                                return texto

            # Volta para aba segura
            try:
                abas = self.driver.window_handles
                if self._aba_sefaz in abas:
                    self.driver.switch_to.window(self._aba_sefaz)
                elif abas:
                    self.driver.switch_to.window(abas[0])
            except Exception:
                pass

            logger.warning("SEFAZ: Nao conseguiu extrair relatorio")
            return ""

        except TimeoutException:
            logger.warning("SEFAZ: Botao Imprimir nao encontrado")
            return ""
        except Exception as e:
            logger.error(f"SEFAZ: Erro ao imprimir relatorio: {e}")
            try:
                abas = self.driver.window_handles
                if abas:
                    self.driver.switch_to.window(abas[0])
            except Exception:
                pass
            return ""

    def voltar_para_nexlog(self):
        """Volta para a aba principal do Nexlog."""
        if self._aba_original:
            self.driver.switch_to.window(self._aba_original)

    def fechar_aba(self):
        """Fecha a aba da SEFAZ e volta para o Nexlog."""
        if self._aba_sefaz:
            self.driver.switch_to.window(self._aba_sefaz)
            self.driver.close()
            self._aba_sefaz = None
        if self._aba_original:
            self.driver.switch_to.window(self._aba_original)

    @property
    def logado(self) -> bool:
        return self._logado
