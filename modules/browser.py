"""
Modulo de login e navegacao compartilhada no Nexlog.
Gerencia sessao do navegador de forma reutilizavel por todos os modulos.
"""

import time
import logging
import os

from selenium import webdriver
from selenium.webdriver.common.by import By
from selenium.webdriver.common.keys import Keys
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.common.exceptions import TimeoutException
from selenium.webdriver.common.action_chains import ActionChains

from config import config

logger = logging.getLogger(__name__)


class NexlogBrowser:
    """Gerencia a sessao do navegador no Nexlog."""

    def __init__(self):
        self.driver = None
        self.wait = None
        self.wait_curto = None
        self._logado = False

    def iniciar(self, headless: bool = False):
        """
        Inicia o navegador Chrome com perfil dedicado para a automacao.
        
        Usa um perfil separado em APPDATA/automacao_sefaz/chrome_profile/
        Isso permite:
        - Manter sessoes salvas (Outlook, Nexlog, SEFAZ)
        - Na primeira execucao voce faz login manualmente no Outlook
        - Nas proximas execucoes ele ja vai estar logado
        - Pode usar seu Chrome pessoal normalmente ao mesmo tempo
        """
        options = webdriver.ChromeOptions()
        if headless:
            options.add_argument("--headless")
        options.add_argument("--start-maximized")
        options.add_argument("--disable-notifications")

        # Perfil dedicado para a automacao (mantem sessoes salvas)
        perfil_automacao = os.path.join(
            os.getenv("APPDATA", os.path.expanduser("~")),
            "automacao_sefaz", "chrome_profile"
        )
        os.makedirs(perfil_automacao, exist_ok=True)
        options.add_argument(f"--user-data-dir={perfil_automacao}")

        # Configura pasta de downloads
        pasta_downloads = config.pasta_downloads
        os.makedirs(pasta_downloads, exist_ok=True)

        prefs = {
            "download.default_directory": pasta_downloads,
            "download.prompt_for_download": False,
            "plugins.always_open_pdf_externally": True,  # Baixa PDF ao inves de abrir
        }
        options.add_experimental_option("prefs", prefs)

        self.driver = webdriver.Chrome(options=options)
        self.wait = WebDriverWait(self.driver, config.timeout_padrao)
        self.wait_curto = WebDriverWait(self.driver, config.timeout_curto)
        logger.info(f"Navegador iniciado (perfil: {perfil_automacao})")

    def login_nexlog(self, usuario: str = None, senha: str = None, base: str = None):
        """Faz login no Nexlog."""
        usuario = usuario or config.nexlog.usuario
        senha = senha or config.nexlog.senha
        base = base or config.nexlog.base

        if not usuario or not senha:
            raise ValueError("Credenciais do Nexlog nao configuradas.")

        self.driver.get(config.url_nexlog)
        time.sleep(3)

        # Usuario
        campo_user = self.wait.until(
            EC.element_to_be_clickable((By.ID, "user"))
        )
        campo_user.clear()
        campo_user.send_keys(usuario)

        # Senha
        campo_senha = self.wait.until(
            EC.element_to_be_clickable((By.ID, "password"))
        )
        campo_senha.clear()
        campo_senha.send_keys(senha)

        # Base/Franquia
        campo_base = self.wait.until(
            EC.element_to_be_clickable((By.ID, "franchise"))
        )
        campo_base.clear()
        campo_base.send_keys(base)

        # Botao login
        self.driver.find_element(
            By.XPATH, "//*[@id='div-btns']/button"
        ).click()

        # Tratar mensagem de sessao ativa
        self._tratar_sessao_ativa()

        time.sleep(4)
        self._logado = True
        logger.info(f"Login Nexlog realizado - base: {base}")

    def _tratar_sessao_ativa(self):
        """Clica em 'Continuar' se aparecer mensagem de sessao ativa."""
        try:
            botao = WebDriverWait(self.driver, 4).until(
                EC.element_to_be_clickable(
                    (By.XPATH, "/html/body/div/div/div/button[2]")
                )
            )
            botao.click()
            logger.info("Sessao ativa detectada - continuando")
        except TimeoutException:
            pass

    def busca_rapida(self, codigo: str):
        """
        Usa o campo de busca rapida (canto superior esquerdo) para pesquisar.
        Digita o codigo e clica no icone 'alvo' (quickTracking).
        """
        self._fechar_modais()
        time.sleep(1)

        # Localiza campo pesquisa rapida
        campo = self.wait.until(
            EC.visibility_of_element_located((By.ID, "quickSearch"))
        )
        campo.click()
        campo.send_keys(Keys.CONTROL, "a")
        campo.send_keys(Keys.BACKSPACE)
        time.sleep(0.3)
        campo.send_keys(codigo)

        # Clica no icone alvo (quickTracking)
        botao = self.wait.until(
            EC.element_to_be_clickable((By.ID, "quickTracking-icon"))
        )
        botao.click()
        time.sleep(3)

    def _fechar_modais(self):
        """Fecha qualquer modal aberto que possa bloquear a interacao."""
        try:
            ActionChains(self.driver).send_keys(Keys.ESCAPE).perform()
            time.sleep(0.5)

            self.driver.execute_script("""
                var backdrops = document.getElementsByClassName('modal-backdrop');
                while(backdrops.length > 0){ backdrops[0].parentNode.removeChild(backdrops[0]); }
                document.body.classList.remove('modal-open');
                var modals = document.getElementsByClassName('modal');
                for(var i=0; i<modals.length; i++) { modals[i].style.display = 'none'; }
            """)
        except Exception:
            pass

    def voltar_aba_principal(self):
        """
        Volta para a primeira aba do navegador (aba do Nexlog).
        Outras abas podem ser do Outlook ou SEFAZ.
        """
        try:
            abas = self.driver.window_handles
            if abas:
                self.driver.switch_to.window(abas[0])
        except Exception:
            pass

    def navegar_url(self, url: str):
        """Navega diretamente para uma URL do Nexlog."""
        # Garante que estamos na aba principal (Nexlog)
        self.voltar_aba_principal()
        self._fechar_modais()
        self.driver.get(url)
        time.sleep(3)

    def navegar_operacoes_gerenciar_rotas(self):
        """Navega para: Operacoes > Recebimento > Gerenciar rotas."""
        self.navegar_url("https://golcargo.nexlog.com/Operations/Receiving")
        time.sleep(2)
        logger.info("Nexlog: Na pagina de Gerenciar rotas (Recebimento)")

    def navegar_vendas_conhecimento_lista(self):
        """
        Navega para: Vendas > Conhecimento > Lista.
        Se ja esta na pagina, NAO faz nada (nem refresh).
        O refresh tira da aba 'Por referencia' e esconde os campos.
        """
        self.voltar_aba_principal()
        self._fechar_modais()

        url_destino = "https://golcargo.nexlog.com/Sales/TransportOrder/"
        url_atual = ""
        try:
            url_atual = self.driver.current_url
        except Exception:
            pass

        if url_destino in url_atual or "TransportOrder" in url_atual:
            # Ja esta na pagina — NAO recarrega (preserva aba/filtro)
            logger.debug("Ja esta em TransportOrder - nao recarrega")
        else:
            self.driver.get(url_destino)
            time.sleep(4)

        logger.info("Nexlog: Na pagina de Conhecimento/Lista")

    def navegar_vendas_retencao_lista(self):
        """
        Navega para: Vendas > Retencao > Lista.
        Se ja esta na pagina, NAO faz refresh (filtros ja estao configurados
        do voo anterior e o refresh causa timeout).
        """
        self.voltar_aba_principal()
        self._fechar_modais()
        url = "https://golcargo.nexlog.com/Sales/Retention/"
        url_atual = ""
        try:
            url_atual = self.driver.current_url
        except Exception:
            pass

        if "Retention" in url_atual:
            # Ja esta na pagina — NAO recarrega
            logger.debug("Ja esta em Retention - nao recarrega")
        else:
            self.driver.get(url)
            time.sleep(4)

        logger.info("Nexlog: Na pagina de Retencoes")

    def aguardar_download(self, timeout: int = 60) -> str:
        """
        Aguarda um download finalizar e retorna o caminho do arquivo.
        Detecta o arquivo mais recente na pasta de downloads.
        """
        pasta = config.pasta_downloads
        tempo_inicial = time.time()
        arquivo_antes = set(os.listdir(pasta)) if os.path.exists(pasta) else set()

        while time.time() - tempo_inicial < timeout:
            time.sleep(2)
            arquivos_atuais = set(os.listdir(pasta)) if os.path.exists(pasta) else set()
            novos = arquivos_atuais - arquivo_antes

            # Ignora arquivos temporarios (.crdownload, .tmp)
            novos_completos = [
                f for f in novos
                if not f.endswith('.crdownload') and not f.endswith('.tmp')
            ]

            if novos_completos:
                arquivo = novos_completos[0]
                caminho = os.path.join(pasta, arquivo)
                logger.info(f"Download concluido: {arquivo}")
                return caminho

        logger.warning("Timeout aguardando download")
        return ""

    def fechar(self):
        """Fecha o navegador."""
        if self.driver:
            try:
                self.driver.quit()
            except Exception:
                pass
            self.driver = None
            self._logado = False
            logger.info("Navegador fechado")

    @property
    def logado(self) -> bool:
        return self._logado

    def __enter__(self):
        self.iniciar()
        return self

    def __exit__(self, *args):
        self.fechar()
