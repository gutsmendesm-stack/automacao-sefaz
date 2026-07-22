"""
Modulo Telegram Bot para o Aero.
Funcionalidades:
- Enviar notificacoes de progresso (inicio, fim, erros)
- Receber comandos remotos (/status, /iniciar, /parar, /voos)
- Thread separada para polling de mensagens

Uso:
    from modules.telegram_bot import TelegramBot
    bot = TelegramBot(token, chat_id)
    bot.iniciar()
    bot.notificar("Processamento iniciado!")
    bot.parar()

Requisito: pip install requests
"""

import logging
import threading
import time
import queue
from datetime import datetime
from typing import Optional, Callable, Dict, List, Any

logger = logging.getLogger(__name__)


class TelegramBot:
    """
    Bot Telegram para notificacoes e comandos remotos do Aero.

    Notificacoes enviadas:
    - Inicio de processamento (quantos voos)
    - Progresso por voo (concluido/erro)
    - Fim do processamento (resumo)
    - Erros criticos

    Comandos aceitos:
    - /status  -> Estado atual (processando/idle, ultimo resultado)
    - /iniciar -> Dispara processamento remoto (usa datas de hoje)
    - /parar   -> Cancela processamento em andamento
    - /voos    -> Lista voos encontrados na ultima busca
    - /ajuda   -> Lista de comandos
    """

    def __init__(self, bot_token: str = "", chat_id: str = ""):
        self.bot_token = bot_token
        self.chat_id = chat_id
        self._ativo = False
        self._thread: Optional[threading.Thread] = None
        self._bot: Optional[Any] = None
        self._app: Optional[Any] = None

        # Callbacks que o main.py pode registrar
        self._callback_iniciar: Optional[Callable] = None
        self._callback_parar: Optional[Callable] = None
        self._callback_status: Optional[Callable[[], str]] = None
        self._callback_voos: Optional[Callable[[], str]] = None
        self._callback_buscar: Optional[Callable] = None
        self._callback_consultar: Optional[Callable] = None

        # Fila de comandos recebidos (para processamento no main thread)
        self.fila_comandos: queue.Queue = queue.Queue()

        # Estado interno para respostas
        self._ultimo_resumo: str = ""
        self._processando: bool = False

    @property
    def configurado(self) -> bool:
        """Verifica se o bot tem token e chat_id configurados."""
        return bool(self.bot_token and self.chat_id)

    @property
    def disponivel(self) -> bool:
        """Verifica se requests esta instalado."""
        try:
            import requests
            return True
        except ImportError:
            return False

    def registrar_callbacks(self,
                            cb_iniciar: Optional[Callable] = None,
                            cb_parar: Optional[Callable] = None,
                            cb_status: Optional[Callable[[], str]] = None,
                            cb_voos: Optional[Callable[[], str]] = None,
                            cb_buscar: Optional[Callable] = None,
                            cb_consultar: Optional[Callable] = None):
        """Registra callbacks do app principal para os comandos remotos."""
        self._callback_iniciar = cb_iniciar
        self._callback_parar = cb_parar
        self._callback_status = cb_status
        self._callback_voos = cb_voos
        self._callback_buscar = cb_buscar
        self._callback_consultar = cb_consultar

    # ========= ENVIO DE NOTIFICACOES =========

    def notificar(self, mensagem: str):
        """Envia uma mensagem de notificacao (thread-safe, nao bloqueia)."""
        if not self.configurado or not self._ativo:
            return

        threading.Thread(
            target=self._enviar_mensagem,
            args=(mensagem,),
            daemon=True
        ).start()

    def notificar_inicio(self, total_voos: int, voos_nomes: List[str]):
        """Notifica inicio de processamento."""
        lista = "\n".join(f"  - {v}" for v in voos_nomes[:10])
        msg = (
            f"\u2708\ufe0f *AERO - Processamento Iniciado*\n\n"
            f"Voos: *{total_voos}*\n"
            f"{lista}\n\n"
            f"\u23f0 {datetime.now().strftime('%H:%M:%S')}"
        )
        self.notificar(msg)
        self._processando = True

    def notificar_voo_concluido(self, indice: int, total: int,
                                 nome_voo: str, liberados: int, retidos: int):
        """Notifica conclusao de um voo."""
        msg = (
            f"\u2705 *Voo {indice}/{total}:* {nome_voo}\n"
            f"  Liberados: {liberados} | Retidos: {retidos}"
        )
        self.notificar(msg)

    def notificar_voo_erro(self, indice: int, total: int,
                            nome_voo: str, erro: str):
        """Notifica erro em um voo."""
        msg = (
            f"\u274c *Voo {indice}/{total}:* {nome_voo}\n"
            f"  Erro: {erro[:100]}"
        )
        self.notificar(msg)

    def notificar_fim(self, total_voos: int, sucesso: int, erros: int,
                       total_liberados: int, total_retidos: int):
        """Notifica fim do processamento com resumo."""
        emoji = "\u2705" if erros == 0 else "\u26a0\ufe0f"
        msg = (
            f"{emoji} *AERO - Processamento Concluido*\n\n"
            f"Voos: {total_voos} ({sucesso} OK, {erros} erros)\n"
            f"AWBs liberados: *{total_liberados}*\n"
            f"AWBs retidos: *{total_retidos}*\n\n"
            f"\u23f0 {datetime.now().strftime('%H:%M:%S')}"
        )
        self.notificar(msg)
        self._processando = False
        self._ultimo_resumo = msg

    def notificar_erro_critico(self, erro: str):
        """Notifica erro critico que interrompeu o processamento."""
        msg = (
            f"\U0001f6a8 *AERO - ERRO CRITICO*\n\n"
            f"{erro[:200]}\n\n"
            f"\u23f0 {datetime.now().strftime('%H:%M:%S')}"
        )
        self.notificar(msg)
        self._processando = False

    # ========= POLLING DE COMANDOS (LONG POLLING) =========

    def iniciar(self):
        """Inicia o bot em thread separada (polling de comandos)."""
        if not self.configurado:
            return

        self._ativo = True
        self._thread = threading.Thread(target=self._loop_polling, daemon=True)
        self._thread.start()

    def parar(self):
        """Para o bot."""
        self._ativo = False
        if self._app:
            try:
                self._app.stop()
            except Exception:
                pass

    def _loop_polling(self):
        """Loop de polling simples usando requests (sem asyncio complexo)."""
        import requests

        url_base = f"https://api.telegram.org/bot{self.bot_token}"
        offset = 0

        while self._ativo:
            try:
                resp = requests.get(
                    f"{url_base}/getUpdates",
                    params={"offset": offset, "timeout": 30},
                    timeout=35
                )

                if resp.status_code != 200:
                    time.sleep(5)
                    continue

                data = resp.json()
                if not data.get("ok"):
                    time.sleep(5)
                    continue

                for update in data.get("result", []):
                    offset = update["update_id"] + 1
                    self._processar_update(update)

            except Exception as e:
                if self._ativo:
                    time.sleep(5)

    def _processar_update(self, update: dict):
        """Processa um update recebido do Telegram."""
        msg = update.get("message", {})
        text = msg.get("text", "").strip()
        chat_id_msg = str(msg.get("chat", {}).get("id", ""))

        # Seguranca: so aceita comandos do chat_id configurado
        if chat_id_msg != str(self.chat_id):
            logger.debug(f"Telegram: mensagem de chat nao autorizado ({chat_id_msg})")
            return

        if not text.startswith("/"):
            return

        comando = text.split()[0].lower().replace("@", "").split("@")[0]

        if comando == "/status":
            self._responder_status()
        elif comando == "/iniciar":
            self._responder_iniciar()
        elif comando == "/parar":
            self._responder_parar()
        elif comando == "/voos":
            self._responder_voos()
        elif comando == "/buscar":
            self._responder_buscar()
        elif comando == "/consultar":
            self._responder_consultar(text)
        elif comando in ("/ajuda", "/help", "/start"):
            self._responder_ajuda()
        else:
            self._enviar_mensagem(
                f"Comando desconhecido: {comando}\n"
                f"Use /ajuda para ver comandos disponiveis."
            )

    # ========= RESPOSTAS A COMANDOS =========

    def _responder_status(self):
        """Responde ao /status."""
        if self._callback_status:
            try:
                texto = self._callback_status()
                self._enviar_mensagem(texto)
                return
            except Exception:
                pass

        # Fallback se nao tem callback
        if self._processando:
            self._enviar_mensagem("Processando...\nUse /parar para cancelar.")
        elif self._ultimo_resumo:
            self._enviar_mensagem(f"Ultimo resultado:\n{self._ultimo_resumo}")
        else:
            self._enviar_mensagem("Idle - Nenhum voo buscado.")

    def _responder_iniciar(self):
        """Responde ao /iniciar."""
        if self._processando:
            self._enviar_mensagem("\u26a0\ufe0f Ja existe processamento em andamento!")
            return

        if self._callback_iniciar:
            self._enviar_mensagem("\u2708\ufe0f Iniciando processamento remoto...")
            # Enfileira o comando para o main thread processar
            self.fila_comandos.put({"cmd": "iniciar"})
            try:
                self._callback_iniciar()
            except Exception as e:
                self._enviar_mensagem(f"\u274c Erro ao iniciar: {e}")
        else:
            self._enviar_mensagem(
                "\u274c Comando /iniciar nao disponivel.\n"
                "Busque os voos na interface primeiro."
            )

    def _responder_parar(self):
        """Responde ao /parar."""
        if not self._processando:
            self._enviar_mensagem("\u2139\ufe0f Nenhum processamento em andamento.")
            return

        if self._callback_parar:
            self.fila_comandos.put({"cmd": "parar"})
            try:
                self._callback_parar()
                self._enviar_mensagem("\u26d4 Solicitacao de parada enviada.")
            except Exception as e:
                self._enviar_mensagem(f"\u274c Erro ao parar: {e}")
        else:
            self._enviar_mensagem("\u274c Funcao parar nao configurada.")

    def _responder_voos(self):
        """Responde ao /voos."""
        if self._callback_voos:
            try:
                texto = self._callback_voos()
                self._enviar_mensagem(texto)
                return
            except Exception as e:
                logger.error(f"Callback voos erro: {e}")

        self._enviar_mensagem("Nenhum voo na memoria. Use /buscar primeiro.")

    def _responder_buscar(self):
        """Responde ao /buscar — busca voos de hoje remotamente."""
        if self._processando:
            self._enviar_mensagem("Ja existe processamento em andamento! Aguarde.")
            return

        if self._callback_buscar:
            self._enviar_mensagem("Buscando voos de hoje...")
            try:
                self._callback_buscar()
            except Exception as e:
                self._enviar_mensagem(f"Erro ao buscar: {e}")
        else:
            self._enviar_mensagem("Comando /buscar nao disponivel.")

    def _responder_consultar(self, texto_completo: str):
        """Responde ao /consultar <awb> — consulta status de AWB para cliente."""
        partes = texto_completo.strip().split()
        if len(partes) < 2:
            self._enviar_mensagem(
                "Uso: /consultar <numero_awb>\n"
                "Exemplo: /consultar 12712345678"
            )
            return

        awb = partes[1].strip()

        # Valida formato basico (numeros, minimo 10 digitos)
        if not awb.isdigit() or len(awb) < 10:
            self._enviar_mensagem(
                f"AWB invalido: {awb}\n"
                "O AWB deve ter pelo menos 10 digitos numericos."
            )
            return

        if self._callback_consultar:
            self._enviar_mensagem(f"Consultando AWB {awb}...")
            try:
                self._callback_consultar(awb)
            except Exception as e:
                self._enviar_mensagem(f"Erro na consulta: {e}")
        else:
            self._enviar_mensagem("Consulta nao disponivel. Abra o Aero primeiro.")

    def _responder_ajuda(self):
        """Responde ao /ajuda."""
        msg = (
            "AERO Bot - Comandos\n\n"
            "/consultar <awb> - Consultar status de AWB (cliente)\n"
            "/buscar - Buscar voos de hoje\n"
            "/iniciar - Iniciar processamento\n"
            "/parar - Cancelar processamento\n"
            "/status - Estado atual\n"
            "/voos - Listar voos encontrados\n"
            "/ajuda - Esta mensagem"
        )
        self._enviar_mensagem(msg)

    # ========= BAIXO NIVEL =========

    def _enviar_mensagem(self, texto: str):
        """Envia mensagem para o chat configurado (bloqueante)."""
        if not self.configurado:
            return

        try:
            import requests
            url = f"https://api.telegram.org/bot{self.bot_token}/sendMessage"
            payload = {
                "chat_id": self.chat_id,
                "text": texto,
            }
            requests.post(url, json=payload, timeout=10)
        except Exception:
            pass

    def testar_conexao(self) -> bool:
        """
        Testa se o bot consegue enviar mensagem.
        Envia uma mensagem de teste diretamente (sem thread).
        """
        if not self.configurado:
            return False

        try:
            import requests

            # 1. Verifica token
            url = f"https://api.telegram.org/bot{self.bot_token}/getMe"
            resp = requests.get(url, timeout=10)
            if resp.status_code != 200 or not resp.json().get("ok"):
                return False

            # 2. Envia mensagem de teste
            url_send = f"https://api.telegram.org/bot{self.bot_token}/sendMessage"
            payload = {
                "chat_id": self.chat_id,
                "text": "AERO Bot conectado!\nNotificacoes configuradas com sucesso.",
            }
            resp2 = requests.post(url_send, json=payload, timeout=10)
            return resp2.status_code == 200

        except Exception:
            return False
