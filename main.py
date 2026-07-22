"""
Aero - Automacao de Operacoes Aereas
Orquestrador principal + Interface grafica.

Fluxo completo:
1. Buscar voos por data no Nexlog
2. Para cada voo:
   a) Extrair chave MDF-e (Integracao MDFe)
   b) Baixar manifesto (PDF) -> extrair AWBs RETIRA/ENTREGA
   c) Verificar Outlook se SEFAZ respondeu
   d) Se tem termos -> consultar site SEFAZ ou usar PDF do email
   e) Para cada CTe com termo -> buscar AWB -> adicionar comentario critico
   f) Liberar AWBs RETIRA sem termo na tela de Retencao
3. Proximo voo...
"""

import os
import sys
import time
import logging
import threading
import tkinter as tk
from tkinter import messagebox, scrolledtext
from datetime import datetime, timedelta
from typing import List, Optional, Dict

import customtkinter as ctk

try:
    from tkcalendar import DateEntry
    HAS_CALENDAR = True
except ImportError:
    HAS_CALENDAR = False

# Adiciona o diretorio ao path
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from config import config, PASTA_CONFIG
from models.termo import (
    Voo, DadosVoo, ConsultaMDFe, ManifestoVoo,
    RespostaEmail, ResultadoProcessamento, StatusMDFe,
)
from modules.browser import NexlogBrowser
from modules.parser import parsear_relatorio_pdf, parsear_manifesto_pdf
from modules.nexlog_voos import NexlogVoos
from modules.nexlog_cte import NexlogCTeOperacoes
from modules.nexlog_liberar import NexlogLiberar
from modules.outlook import OutlookWeb
from modules.sefaz import SefazConsulta
from modules.telegram_bot import TelegramBot

# ========= CUSTOMTKINTER CONFIG =========
ctk.set_appearance_mode("dark")
ctk.set_default_color_theme("blue")

# ========= LOGGING =========
LOG_FILE = PASTA_CONFIG / "execucao.log"
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    handlers=[
        logging.FileHandler(LOG_FILE, encoding="utf-8"),
        logging.StreamHandler(),
    ],
)
logger = logging.getLogger("main")


# ========= STATUS DO VOO (PROGRESSO) =========
class StatusVoo:
    """Estado de processamento de um voo individual."""
    PENDENTE = "pendente"
    PROCESSANDO = "processando"
    CONCLUIDO = "concluido"
    ERRO = "erro"


ETAPAS_VOO = [
    "Buscando chave MDF-e",
    "Baixando manifesto",
    "Verificando Outlook",
    "Consultando SEFAZ",
    "Adicionando comentarios",
    "Liberando AWBs",
]


class AppAutomacao:
    """Interface principal da automacao."""

    def __init__(self):
        self.janela = ctk.CTk()
        self.janela.title("Aero")
        self.janela.geometry("900x720")
        self.janela.resizable(True, True)

        # Variaveis
        self.nexlog_user = tk.StringVar()
        self.nexlog_senha = tk.StringVar()
        self.nexlog_base = tk.StringVar(value="MCZ")
        self.sefaz_user = tk.StringVar()
        self.sefaz_senha = tk.StringVar()
        self.data_inicial = tk.StringVar()
        self.data_final = tk.StringVar()
        self.timeout_var = tk.IntVar(value=20)
        self.tg_token = tk.StringVar()
        self.tg_chat_id = tk.StringVar()
        self.tg_ativo = tk.BooleanVar(value=False)

        # Estado
        self._voos_encontrados: List[Voo] = []
        self._voos_checkboxes: List[tk.BooleanVar] = []
        self._processando = False

        # Estado de progresso (para a tela de progresso na aba Voos)
        self._progresso_voos: Dict[int, dict] = {}  # indice -> {status, etapa, msg}
        self._progresso_widgets: Dict[int, dict] = {}  # indice -> {icon, etapa_lbl, msg_lbl}

        # Telegram Bot
        self._telegram_bot: Optional[TelegramBot] = None
        self._cancelar_processamento = False

        # Datas padrao (hoje)
        hoje = datetime.now().strftime("%d/%m/%Y")
        self.data_inicial.set(hoje)
        self.data_final.set(hoje)

        # Carrega credenciais
        self._carregar_credenciais()
        self._criar_interface()

        # Inicia Telegram Bot se configurado (nao bloqueia se falhar)
        try:
            self._iniciar_telegram_bot()
        except Exception:
            pass

    def _carregar_credenciais(self):
        if config.carregar():
            self.nexlog_user.set(config.nexlog.usuario)
            self.nexlog_senha.set(config.nexlog.senha)
            self.nexlog_base.set(config.nexlog.base)
            self.sefaz_user.set(config.sefaz.usuario)
            self.sefaz_senha.set(config.sefaz.senha)
            self.timeout_var.set(config.timeout_padrao)
            self.tg_token.set(config.telegram.bot_token)
            self.tg_chat_id.set(config.telegram.chat_id)
            self.tg_ativo.set(config.telegram.ativo)

    def _salvar_credenciais(self):
        config.nexlog.usuario = self.nexlog_user.get()
        config.nexlog.senha = self.nexlog_senha.get()
        config.nexlog.base = self.nexlog_base.get()
        config.sefaz.usuario = self.sefaz_user.get()
        config.sefaz.senha = self.sefaz_senha.get()
        config.timeout_padrao = self.timeout_var.get()
        config.telegram.bot_token = self.tg_token.get()
        config.telegram.chat_id = self.tg_chat_id.get()
        config.telegram.ativo = self.tg_ativo.get()
        config.salvar()

    def _criar_interface(self):
        """Interface minimalista dark com CustomTkinter."""
        # Header
        header = ctk.CTkFrame(self.janela, fg_color="transparent", height=50)
        header.pack(fill="x", padx=24, pady=(18, 6))

        ctk.CTkLabel(header, text="AERO",
                     font=ctk.CTkFont(size=18, weight="bold")).pack(side="left")
        ctk.CTkLabel(header, text="Gollog",
                     font=ctk.CTkFont(size=11),
                     text_color="#6b7280").pack(side="left", padx=12)

        # Tabview (abas)
        self.tabview = ctk.CTkTabview(self.janela, corner_radius=8)
        self.tabview.pack(fill="both", expand=True, padx=24, pady=(6, 18))

        # --- ABA VOOS ---
        self.tabview.add("Voos")
        self._criar_aba_voos(self.tabview.tab("Voos"))

        # --- ABA CREDENCIAIS ---
        self.tabview.add("Config")
        self._criar_aba_credenciais(self.tabview.tab("Config"))

        # --- ABA LOG ---
        self.tabview.add("Log")
        self._criar_aba_log(self.tabview.tab("Log"))

    def _criar_aba_voos(self, parent):
        """Aba principal com busca de voos, checkboxes e painel de progresso."""
        # --- Barra de busca ---
        frame_busca = ctk.CTkFrame(parent, corner_radius=8)
        frame_busca.pack(fill="x", padx=8, pady=(8, 4))

        inner = ctk.CTkFrame(frame_busca, fg_color="transparent")
        inner.pack(fill="x", padx=16, pady=12)

        ctk.CTkLabel(inner, text="Periodo:",
                     font=ctk.CTkFont(size=12)).pack(side="left")

        if HAS_CALENDAR:
            # DateEntry com calendario clicavel (widget tkinter, funciona dentro do CTk)
            self.de_ini = DateEntry(inner, width=10, date_pattern="dd/MM/yyyy",
                                    background="#1f2937", foreground="#e5e7eb",
                                    headersbackground="#111827",
                                    headersforeground="#60a5fa",
                                    selectbackground="#3b82f6",
                                    selectforeground="#fff",
                                    font=("Segoe UI", 10))
            self.de_ini.set_date(datetime.now())
            self.de_ini.pack(side="left", padx=(10, 6))

            ctk.CTkLabel(inner, text="a",
                         font=ctk.CTkFont(size=12),
                         text_color="#6b7280").pack(side="left")

            self.de_fim = DateEntry(inner, width=10, date_pattern="dd/MM/yyyy",
                                    background="#1f2937", foreground="#e5e7eb",
                                    headersbackground="#111827",
                                    headersforeground="#60a5fa",
                                    selectbackground="#3b82f6",
                                    selectforeground="#fff",
                                    font=("Segoe UI", 10))
            self.de_fim.set_date(datetime.now())
            self.de_fim.pack(side="left", padx=(6, 16))
        else:
            # Fallback: CTkEntry se tkcalendar nao instalado
            e1 = ctk.CTkEntry(inner, textvariable=self.data_inicial, width=110,
                              placeholder_text="dd/mm/aaaa",
                              font=ctk.CTkFont(size=12))
            e1.pack(side="left", padx=(10, 6))

            ctk.CTkLabel(inner, text="a",
                         font=ctk.CTkFont(size=12),
                         text_color="#6b7280").pack(side="left")

            e2 = ctk.CTkEntry(inner, textvariable=self.data_final, width=110,
                              placeholder_text="dd/mm/aaaa",
                              font=ctk.CTkFont(size=12))
            e2.pack(side="left", padx=(6, 16))

        ctk.CTkButton(inner, text="BUSCAR", command=self._buscar_voos_thread,
                      width=100, height=32,
                      font=ctk.CTkFont(size=12, weight="bold")).pack(side="left")

        # ============================================================
        # CONTAINER CENTRAL: alterna entre lista de voos e progresso
        # ============================================================
        self._container_central = ctk.CTkFrame(parent, fg_color="transparent")
        self._container_central.pack(fill="both", expand=True, padx=8, pady=4)

        # --- PAINEL 1: Lista de voos (checkboxes) ---
        self._painel_lista = ctk.CTkFrame(self._container_central, fg_color="transparent")
        self._painel_lista.pack(fill="both", expand=True)

        # Header da lista
        frame_lista_header = ctk.CTkFrame(self._painel_lista, fg_color="transparent")
        frame_lista_header.pack(fill="x", pady=(0, 4))

        self.lbl_voos_count = ctk.CTkLabel(frame_lista_header,
                                            text="Nenhum voo encontrado",
                                            font=ctk.CTkFont(size=11),
                                            text_color="#6b7280")
        self.lbl_voos_count.pack(side="left")

        ctk.CTkButton(frame_lista_header, text="Selecionar todos",
                      command=self._selecionar_todos_voos,
                      width=110, height=26,
                      fg_color="transparent", border_width=1,
                      text_color="#60a5fa",
                      font=ctk.CTkFont(size=11)).pack(side="right")

        ctk.CTkButton(frame_lista_header, text="Nenhum",
                      command=self._desmarcar_todos_voos,
                      width=70, height=26,
                      fg_color="transparent", border_width=1,
                      text_color="#6b7280",
                      font=ctk.CTkFont(size=11)).pack(side="right", padx=(0, 8))

        # Scrollable frame para os voos
        self.frame_voos_scroll = ctk.CTkScrollableFrame(self._painel_lista, corner_radius=6)
        self.frame_voos_scroll.pack(fill="both", expand=True, pady=4)

        # --- PAINEL 2: Progresso (oculto ate iniciar processamento) ---
        self._painel_progresso = ctk.CTkFrame(self._container_central, fg_color="transparent")
        # Nao faz pack — so aparece quando inicia processamento

        # Header do progresso
        frame_progresso_header = ctk.CTkFrame(self._painel_progresso, fg_color="transparent")
        frame_progresso_header.pack(fill="x", pady=(0, 8))

        self.lbl_progresso_titulo = ctk.CTkLabel(
            frame_progresso_header, text="Processando...",
            font=ctk.CTkFont(size=14, weight="bold"))
        self.lbl_progresso_titulo.pack(side="left")

        self.lbl_etapa_global = ctk.CTkLabel(
            frame_progresso_header, text="",
            font=ctk.CTkFont(size=11),
            text_color="#60a5fa")
        self.lbl_etapa_global.pack(side="right")

        # Barra de progresso geral
        self.progress_bar = ctk.CTkProgressBar(self._painel_progresso, height=6,
                                                corner_radius=3)
        self.progress_bar.pack(fill="x", pady=(0, 12))
        self.progress_bar.set(0)

        # Label de porcentagem
        self.lbl_progresso_pct = ctk.CTkLabel(
            self._painel_progresso, text="0%",
            font=ctk.CTkFont(size=11),
            text_color="#6b7280")
        self.lbl_progresso_pct.pack(anchor="e", pady=(0, 8))

        # Scrollable frame para os status dos voos
        self.frame_progresso_scroll = ctk.CTkScrollableFrame(
            self._painel_progresso, corner_radius=6)
        self.frame_progresso_scroll.pack(fill="both", expand=True, pady=4)

        # Botao voltar (aparece quando termina)
        self.btn_voltar_lista = ctk.CTkButton(
            self._painel_progresso, text="VOLTAR PARA LISTA",
            command=self._mostrar_lista_voos,
            width=180, height=36,
            fg_color="transparent", border_width=1,
            text_color="#60a5fa",
            font=ctk.CTkFont(size=12, weight="bold"))
        # Nao faz pack — so aparece quando processamento termina

        # --- Botao Iniciar ---
        frame_bottom = ctk.CTkFrame(parent, fg_color="transparent")
        frame_bottom.pack(fill="x", padx=8, pady=(4, 8))

        self.btn_iniciar = ctk.CTkButton(
            frame_bottom, text="INICIAR PROCESSAMENTO",
            command=self._iniciar_processamento_thread,
            width=220, height=40,
            fg_color="#22c55e", hover_color="#16a34a",
            text_color="#000000",
            font=ctk.CTkFont(size=13, weight="bold"))
        self.btn_iniciar.pack(side="right")

    # ========= PROGRESSO: MOSTRAR/OCULTAR =========

    def _mostrar_progresso(self, voos: List[Voo]):
        """Troca o painel da aba Voos para exibir progresso em tempo real."""
        # Oculta a lista de voos
        self._painel_lista.pack_forget()

        # Mostra o painel de progresso
        self._painel_progresso.pack(fill="both", expand=True)

        # Desabilita botao iniciar
        self.btn_iniciar.configure(state="disabled", text="PROCESSANDO...")

        # Limpa itens anteriores
        for widget in self.frame_progresso_scroll.winfo_children():
            widget.destroy()
        self._progresso_widgets.clear()
        self._progresso_voos.clear()

        # Oculta botao voltar enquanto processa
        self.btn_voltar_lista.pack_forget()

        # Reseta barra
        self.progress_bar.set(0)
        self.lbl_progresso_pct.configure(text="0%")
        self.lbl_progresso_titulo.configure(text=f"Processando {len(voos)} voo(s)...")
        self.lbl_etapa_global.configure(text="Iniciando...")

        # Cria cards de status para cada voo
        for i, voo in enumerate(voos):
            self._progresso_voos[i] = {
                "status": StatusVoo.PENDENTE,
                "etapa": "",
                "msg": "Aguardando...",
            }

            row = ctk.CTkFrame(self.frame_progresso_scroll, corner_radius=6)
            row.pack(fill="x", pady=3, padx=2)

            inner_row = ctk.CTkFrame(row, fg_color="transparent")
            inner_row.pack(fill="x", padx=12, pady=8)

            # Icone de status (pendente = relogio)
            icon_lbl = ctk.CTkLabel(inner_row, text="\u23f3",
                                    font=ctk.CTkFont(size=16),
                                    width=24)
            icon_lbl.pack(side="left")

            # Nome do voo
            ctk.CTkLabel(inner_row, text=voo.numero_controle,
                         font=ctk.CTkFont(size=12, weight="bold")
                         ).pack(side="left", padx=(8, 0))

            ctk.CTkLabel(inner_row, text=voo.etapas,
                         font=ctk.CTkFont(size=11),
                         text_color="#60a5fa").pack(side="left", padx=(12, 0))

            # Etapa atual do voo (direita)
            etapa_lbl = ctk.CTkLabel(inner_row, text="Aguardando...",
                                     font=ctk.CTkFont(size=10),
                                     text_color="#6b7280")
            etapa_lbl.pack(side="right")

            # Mensagem de resultado (abaixo, aparece apos concluir)
            msg_lbl = ctk.CTkLabel(row, text="",
                                   font=ctk.CTkFont(size=10),
                                   text_color="#6b7280")
            msg_lbl.pack(anchor="w", padx=56, pady=(0, 4))

            self._progresso_widgets[i] = {
                "icon": icon_lbl,
                "etapa": etapa_lbl,
                "msg": msg_lbl,
                "frame": row,
            }

    def _mostrar_lista_voos(self):
        """Volta para a lista de voos (checkboxes)."""
        self._painel_progresso.pack_forget()
        self._painel_lista.pack(fill="both", expand=True)
        self.btn_iniciar.configure(state="normal", text="INICIAR PROCESSAMENTO")

    def _atualizar_progresso_voo(self, indice: int, status: str, etapa: str = "", msg: str = ""):
        """Atualiza o status visual de um voo no painel de progresso (thread-safe)."""
        def _update():
            if indice not in self._progresso_widgets:
                return

            widgets = self._progresso_widgets[indice]

            # Atualiza icone
            if status == StatusVoo.PROCESSANDO:
                widgets["icon"].configure(text="\u23f3", text_color="#f59e0b")  # Relogio amarelo
            elif status == StatusVoo.CONCLUIDO:
                widgets["icon"].configure(text="\u2713", text_color="#22c55e")  # Check verde
            elif status == StatusVoo.ERRO:
                widgets["icon"].configure(text="\u2717", text_color="#ef4444")  # X vermelho
            else:
                widgets["icon"].configure(text="\u23f3", text_color="#6b7280")  # Pendente cinza

            # Atualiza etapa
            if etapa:
                widgets["etapa"].configure(text=etapa)

            # Atualiza mensagem de resultado
            if msg:
                widgets["msg"].configure(text=msg)

            # Cor do frame baseada no status
            if status == StatusVoo.PROCESSANDO:
                widgets["frame"].configure(border_width=1, border_color="#f59e0b")
            elif status == StatusVoo.CONCLUIDO:
                widgets["frame"].configure(border_width=1, border_color="#22c55e")
            elif status == StatusVoo.ERRO:
                widgets["frame"].configure(border_width=1, border_color="#ef4444")
            else:
                widgets["frame"].configure(border_width=0)

        self.janela.after(0, _update)

    def _atualizar_barra_progresso(self, voo_atual: int, total_voos: int, etapa_texto: str = ""):
        """Atualiza barra de progresso geral e etapa global (thread-safe)."""
        def _update():
            progresso = voo_atual / total_voos if total_voos > 0 else 0
            self.progress_bar.set(progresso)
            pct = int(progresso * 100)
            self.lbl_progresso_pct.configure(text=f"{pct}%")

            if etapa_texto:
                self.lbl_etapa_global.configure(text=etapa_texto)

        self.janela.after(0, _update)

    def _finalizar_progresso(self, sucesso: bool = True):
        """Marca o processamento como finalizado e mostra botao de voltar."""
        def _update():
            if sucesso:
                self.lbl_progresso_titulo.configure(text="Processamento concluido!")
                self.lbl_etapa_global.configure(text="")
            else:
                self.lbl_progresso_titulo.configure(text="Processamento com erros")

            self.progress_bar.set(1.0)
            self.lbl_progresso_pct.configure(text="100%")

            # Mostra botao voltar
            self.btn_voltar_lista.pack(pady=(12, 0))

            # Reabilita botao iniciar
            self.btn_iniciar.configure(state="normal", text="INICIAR PROCESSAMENTO")

        self.janela.after(0, _update)

    def _criar_aba_credenciais(self, parent):
        """Aba de configuracao / credenciais."""
        frame = ctk.CTkFrame(parent, fg_color="transparent")
        frame.pack(fill="both", expand=True, padx=30, pady=20)

        # Nexlog
        self._section_label(frame, "NEXLOG", 0)
        self._field(frame, "CPF:", self.nexlog_user, 1)
        self._field(frame, "Senha:", self.nexlog_senha, 2, show="*")
        self._field(frame, "Base:", self.nexlog_base, 3)

        # SEFAZ
        self._section_label(frame, "SEFAZ-AL", 5)
        self._field(frame, "Usuario:", self.sefaz_user, 6)
        self._field(frame, "Senha:", self.sefaz_senha, 7, show="*")

        # Config
        self._section_label(frame, "GERAL", 9)
        self._field(frame, "Timeout (s):", self.timeout_var, 10, width=100)

        # Telegram
        self._section_label(frame, "TELEGRAM BOT", 12)
        self._field(frame, "Token:", self.tg_token, 13, width=320)
        self._field(frame, "Chat ID:", self.tg_chat_id, 14, width=180)

        # Checkbox ativo + botao testar
        frame_tg = ctk.CTkFrame(frame, fg_color="transparent")
        frame_tg.grid(row=15, column=0, columnspan=2, sticky="w", pady=6)

        ctk.CTkCheckBox(frame_tg, variable=self.tg_ativo,
                        text="Notificacoes ativas",
                        font=ctk.CTkFont(size=11)).pack(side="left")

        ctk.CTkButton(frame_tg, text="Testar",
                      command=self._testar_telegram,
                      width=80, height=26,
                      fg_color="transparent", border_width=1,
                      text_color="#60a5fa",
                      font=ctk.CTkFont(size=11)).pack(side="left", padx=(16, 0))

        # Salvar
        ctk.CTkButton(frame, text="SALVAR", command=self._salvar_e_confirmar,
                      width=140, height=36,
                      font=ctk.CTkFont(size=12, weight="bold")
                      ).grid(row=17, column=0, columnspan=2, pady=25)

    def _section_label(self, frame, text, row):
        ctk.CTkLabel(frame, text=text,
                     font=ctk.CTkFont(size=12, weight="bold"),
                     text_color="#60a5fa").grid(
            row=row, column=0, columnspan=2, sticky="w", pady=(18, 6))

    def _field(self, frame, label, var, row, show="", width=220):
        ctk.CTkLabel(frame, text=label,
                     font=ctk.CTkFont(size=12),
                     text_color="#9ca3af").grid(
            row=row, column=0, sticky="e", padx=(0, 12), pady=4)

        entry = ctk.CTkEntry(frame, textvariable=var, width=width,
                             show=show if show else "",
                             font=ctk.CTkFont(size=12))
        entry.grid(row=row, column=1, sticky="w", pady=4)

    def _salvar_e_confirmar(self):
        self._salvar_credenciais()
        self._log("Credenciais salvas!")
        # Reinicia o bot Telegram se configurado
        self._iniciar_telegram_bot()

    def _testar_telegram(self):
        """Testa conexao com o bot Telegram (em thread para nao travar)."""
        self._salvar_credenciais()
        token = self.tg_token.get().strip()
        chat_id = self.tg_chat_id.get().strip()

        if not token or not chat_id:
            messagebox.showwarning("Telegram", "Preencha Token e Chat ID.")
            return

        self._log("Testando conexao Telegram...")

        def _teste():
            bot = TelegramBot(token, chat_id)
            if bot.testar_conexao():
                self.janela.after(0, lambda: messagebox.showinfo(
                    "Telegram", "Conexao OK! Mensagem enviada no Telegram."))
                self._log("Telegram: teste OK!")
            else:
                self.janela.after(0, lambda: messagebox.showerror(
                    "Telegram", "Falha na conexao. Verifique o token e chat ID."))
                self._log("Telegram: teste FALHOU")

        threading.Thread(target=_teste, daemon=True).start()

    def _iniciar_telegram_bot(self):
        """Inicia ou reinicia o bot Telegram se configurado e ativo."""
        # Para bot anterior se existia
        if hasattr(self, '_telegram_bot') and self._telegram_bot:
            self._telegram_bot.parar()
            self._telegram_bot = None

        if not self.tg_ativo.get():
            return

        token = self.tg_token.get().strip()
        chat_id = self.tg_chat_id.get().strip()

        if not token or not chat_id:
            return

        self._telegram_bot = TelegramBot(token, chat_id)

        # Registra callbacks para comandos remotos
        self._telegram_bot.registrar_callbacks(
            cb_iniciar=self._telegram_cmd_iniciar,
            cb_parar=self._telegram_cmd_parar,
            cb_status=self._telegram_cmd_status,
            cb_voos=self._telegram_cmd_voos,
            cb_buscar=self._telegram_cmd_buscar,
            cb_consultar=self._telegram_cmd_consultar,
        )

        self._telegram_bot.iniciar()
        self._log("Telegram Bot iniciado!")

    def _telegram_cmd_buscar(self):
        """Callback do Telegram /buscar — busca voos de hoje."""
        if self._processando:
            return
        # Agenda no main thread
        self.janela.after(0, self._buscar_voos_telegram)

    def _buscar_voos_telegram(self):
        """Busca voos de hoje e notifica pelo Telegram."""
        threading.Thread(target=self._buscar_voos_e_notificar, daemon=True).start()

    def _buscar_voos_e_notificar(self):
        """Busca voos e envia resultado via Telegram."""
        hoje = datetime.now().strftime("%d/%m/%Y")
        self._processando = True

        try:
            browser = NexlogBrowser()
            browser.iniciar(headless=True)
            browser.login_nexlog()

            voos_mod = NexlogVoos(browser)
            voos = voos_mod.pesquisar_voos(hoje, hoje)

            self._voos_encontrados = voos
            self._atualizar_lista_voos(voos)
            browser.fechar()

            # Notifica resultado pelo Telegram
            if self._telegram_bot and voos:
                linhas = [f"Encontrados {len(voos)} voo(s):\n"]
                for v in voos[:15]:
                    linhas.append(f"  - {v.numero_controle} ({v.etapas}) {v.hora_chegada}")
                linhas.append(f"\nUse /iniciar para processar.")
                self._telegram_bot._enviar_mensagem("\n".join(linhas))
            elif self._telegram_bot:
                self._telegram_bot._enviar_mensagem("Nenhum voo encontrado para hoje.")

        except Exception as e:
            if self._telegram_bot:
                self._telegram_bot._enviar_mensagem(f"Erro ao buscar voos: {str(e)[:100]}")
        finally:
            self._processando = False

    def _telegram_cmd_consultar(self, awb: str):
        """Callback do Telegram /consultar <awb> — consulta status para cliente."""
        if self._processando:
            if self._telegram_bot:
                self._telegram_bot._enviar_mensagem(
                    "Processamento em andamento. Aguarde para consultar.")
            return
        threading.Thread(target=self._consultar_awb_telegram, args=(awb,), daemon=True).start()

    def _consultar_awb_telegram(self, awb: str):
        """Executa consulta de AWB e envia resultado pelo Telegram."""
        self._processando = True
        try:
            from modules.consulta_cliente import ConsultaCliente

            browser = NexlogBrowser()
            browser.iniciar(headless=True)
            browser.login_nexlog()

            consulta = ConsultaCliente(browser)
            resultado = consulta.consultar_awb(awb)

            # Envia resposta formatada
            resposta = resultado.resposta_cliente()
            if self._telegram_bot:
                self._telegram_bot._enviar_mensagem(resposta)

            browser.fechar()

        except Exception as e:
            if self._telegram_bot:
                self._telegram_bot._enviar_mensagem(f"Erro na consulta: {str(e)[:100]}")
            try:
                browser.fechar()
            except Exception:
                pass
        finally:
            self._processando = False

    def _telegram_cmd_iniciar(self):
        """Callback do Telegram /iniciar — dispara processamento."""
        if self._processando:
            return
        # Agenda no main thread
        self.janela.after(0, self._iniciar_processamento_thread)

    def _telegram_cmd_parar(self):
        """Callback do Telegram /parar — seta flag de cancelamento."""
        self._cancelar_processamento = True

    def _telegram_cmd_status(self) -> str:
        """Callback do Telegram /status."""
        if self._processando:
            return "\u23f3 *Processamento em andamento*"
        elif self._voos_encontrados:
            return (
                f"\U0001f4a4 *Idle*\n"
                f"Voos na memoria: {len(self._voos_encontrados)}\n"
                f"Ultimo status: pronto para processar"
            )
        else:
            return "\U0001f4a4 *Idle* - Nenhum voo buscado."

    def _telegram_cmd_voos(self) -> str:
        """Callback do Telegram /voos."""
        if not self._voos_encontrados:
            return "\u2139\ufe0f Nenhum voo encontrado. Busque na interface primeiro."

        linhas = [f"\u2708\ufe0f *Voos ({len(self._voos_encontrados)}):*\n"]
        for v in self._voos_encontrados[:15]:
            linhas.append(f"  - {v.numero_controle} ({v.etapas}) {v.data_chegada}")
        return "\n".join(linhas)

    def _criar_aba_log(self, parent):
        self.log_widget = scrolledtext.ScrolledText(
            parent, height=20, font=("Consolas", 9),
            bg="#0a0a0a", fg="#b0b0b0", insertbackground="white",
            relief="flat", borderwidth=0
        )
        self.log_widget.pack(fill="both", expand=True, padx=8, pady=8)

    def _log(self, mensagem: str):
        """Adiciona mensagem ao log visual (thread-safe)."""
        def _update():
            timestamp = datetime.now().strftime("%H:%M:%S")
            self.log_widget.insert(tk.END, f"[{timestamp}] {mensagem}\n")
            self.log_widget.see(tk.END)
        self.janela.after(0, _update)
        logger.info(mensagem)

    # ========= SELECAO DE VOOS =========

    def _atualizar_lista_voos(self, voos: List[Voo]):
        """Recria a lista de checkboxes com os voos encontrados."""
        def _update():
            # Limpa lista anterior
            for widget in self.frame_voos_scroll.winfo_children():
                widget.destroy()
            self._voos_checkboxes.clear()

            if not voos:
                self.lbl_voos_count.configure(text="Nenhum voo encontrado")
                return

            self.lbl_voos_count.configure(text=f"{len(voos)} voo(s) encontrado(s)")

            for i, v in enumerate(voos):
                var = tk.BooleanVar(value=True)  # Todos marcados por padrao
                self._voos_checkboxes.append(var)

                # Frame do voo (card)
                row = ctk.CTkFrame(self.frame_voos_scroll, corner_radius=6)
                row.pack(fill="x", pady=3, padx=2)

                inner_row = ctk.CTkFrame(row, fg_color="transparent")
                inner_row.pack(fill="x", padx=12, pady=8)

                # Checkbox com numero do voo
                cb = ctk.CTkCheckBox(inner_row, variable=var,
                                     text=v.numero_controle,
                                     font=ctk.CTkFont(size=12, weight="bold"),
                                     width=24)
                cb.pack(side="left")

                # Origem/Destino
                ctk.CTkLabel(inner_row, text=v.etapas,
                             font=ctk.CTkFont(size=11),
                             text_color="#60a5fa").pack(side="left", padx=(16, 0))

                # Data/Hora
                ctk.CTkLabel(inner_row, text=v.data_chegada,
                             font=ctk.CTkFont(size=11),
                             text_color="#6b7280").pack(side="left", padx=(16, 0))

        self.janela.after(0, _update)

    def _selecionar_todos_voos(self):
        for var in self._voos_checkboxes:
            var.set(True)
        # Update checkbox widgets
        for widget in self.frame_voos_scroll.winfo_children():
            for child in widget.winfo_children():
                for cb in child.winfo_children():
                    if isinstance(cb, ctk.CTkCheckBox):
                        cb.select()

    def _desmarcar_todos_voos(self):
        for var in self._voos_checkboxes:
            var.set(False)
        # Update checkbox widgets
        for widget in self.frame_voos_scroll.winfo_children():
            for child in widget.winfo_children():
                for cb in child.winfo_children():
                    if isinstance(cb, ctk.CTkCheckBox):
                        cb.deselect()

    def _obter_voos_selecionados(self) -> List[Voo]:
        """Retorna apenas os voos marcados com checkbox."""
        selecionados = []
        for i, var in enumerate(self._voos_checkboxes):
            if var.get() and i < len(self._voos_encontrados):
                selecionados.append(self._voos_encontrados[i])
        return selecionados

    # ========= ACOES =========

    def _buscar_voos_thread(self):
        """Busca voos em thread separada para nao travar a interface."""
        if self._processando:
            messagebox.showwarning("Aviso", "Ja existe um processo em andamento.")
            return
        threading.Thread(target=self._buscar_voos, daemon=True).start()

    def _buscar_voos(self):
        """Busca voos no Nexlog pela data selecionada."""
        self._salvar_credenciais()

        # Pega datas do DateEntry ou do campo texto
        if HAS_CALENDAR:
            data_ini = self.de_ini.get()
            data_fim = self.de_fim.get()
        else:
            data_ini = self.data_inicial.get().strip()
            data_fim = self.data_final.get().strip()

        if not data_ini or not data_fim:
            messagebox.showwarning("Aviso", "Preencha as datas.")
            return

        self._log(f"Buscando voos de {data_ini} ate {data_fim}...")
        self._processando = True

        try:
            browser = NexlogBrowser()
            # Headless = navegador invisivel (mais rapido, sem janela)
            # Se o Nexlog der problema com headless, mude para browser.iniciar()
            browser.iniciar(headless=True)
            browser.login_nexlog()

            voos_mod = NexlogVoos(browser)
            voos = voos_mod.pesquisar_voos(data_ini, data_fim)

            self._voos_encontrados = voos
            self._atualizar_lista_voos(voos)

            self._log(f"Encontrados {len(voos)} voos!")
            browser.fechar()

        except Exception as e:
            self._log(f"ERRO ao buscar voos: {e}")
            try:
                browser.fechar()
            except Exception:
                pass
            messagebox.showerror("Erro", str(e))
        finally:
            self._processando = False

    def _iniciar_processamento_thread(self):
        """Inicia processamento completo em thread separada."""
        if self._processando:
            messagebox.showwarning("Aviso", "Ja existe um processo em andamento.")
            return

        if not self._voos_encontrados:
            messagebox.showwarning("Aviso", "Busque os voos primeiro.")
            return

        # Pega apenas os voos selecionados via checkbox
        voos_selecionados = self._obter_voos_selecionados()
        if not voos_selecionados:
            messagebox.showwarning("Aviso", "Selecione pelo menos um voo.")
            return

        confirma = messagebox.askyesno(
            "Confirmar",
            f"Processar {len(voos_selecionados)} voo(s) selecionado(s)?\n\n"
            "Isso vai:\n"
            "1. Verificar termos na SEFAZ\n"
            "2. Adicionar comentarios criticos\n"
            "3. Liberar AWBs sem termo\n\n"
            "Deseja continuar?"
        )
        if not confirma:
            return

        threading.Thread(target=self._processar_voos, daemon=True).start()

    def _processar_voos(self):
        """Processamento completo dos voos selecionados com progresso visual."""
        self._salvar_credenciais()
        self._processando = True
        self._cancelar_processamento = False

        # Usa apenas os voos marcados via checkbox
        voos = self._obter_voos_selecionados()

        # Mostra painel de progresso na mesma aba (substitui lista)
        self.janela.after(0, lambda: self._mostrar_progresso(voos))
        time.sleep(0.3)  # Aguarda UI atualizar

        self._log("=" * 60)
        self._log(f"INICIANDO PROCESSAMENTO DE {len(voos)} VOOS")
        self._log("=" * 60)

        # Notificacao Telegram: inicio
        if self._telegram_bot and self._telegram_bot.configurado:
            nomes = [f"{v.numero_controle} ({v.etapas})" for v in voos]
            self._telegram_bot.notificar_inicio(len(voos), nomes)

        teve_erro = False
        total_liberados = 0
        total_retidos = 0
        voos_sucesso = 0
        voos_erro = 0

        # Acumula AWBs com termo de TODOS os voos para verificacao final
        awbs_com_termo_sessao = {}  # {awb: cte} - todos os AWBs que deveriam estar retidos

        try:
            # Inicia navegador
            browser = NexlogBrowser()
            browser.iniciar()
            browser.login_nexlog()

            # Modulos
            voos_mod = NexlogVoos(browser)
            cte_mod = NexlogCTeOperacoes(browser)
            liberar_mod = NexlogLiberar(browser)
            outlook = OutlookWeb(browser.driver)
            sefaz = SefazConsulta(browser.driver)

            # Processa cada voo
            for i, voo in enumerate(voos):
                # Verifica cancelamento remoto (Telegram /parar)
                if self._cancelar_processamento:
                    self._log("PROCESSAMENTO CANCELADO pelo usuario!")
                    self._atualizar_progresso_voo(i, StatusVoo.ERRO, "Cancelado")
                    break

                self._log(f"\n{'='*40}")
                self._log(f"VOO {i+1}/{len(voos)}: {voo.numero_controle} ({voo.etapas})")
                self._log(f"{'='*40}")

                # Atualiza progresso global
                self._atualizar_barra_progresso(
                    i, len(voos),
                    f"Voo {i+1}/{len(voos)}: {voo.numero_controle}"
                )

                # Marca voo como processando
                self._atualizar_progresso_voo(i, StatusVoo.PROCESSANDO, "Iniciando...")

                try:
                    resultado = self._processar_um_voo(
                        voo, browser, voos_mod, cte_mod, liberar_mod, outlook, sefaz,
                        indice_progresso=i
                    )
                    self._log(resultado.resumo())

                    # Marca voo como concluido ou com erro
                    if resultado.sucesso:
                        resumo_curto = (
                            f"{len(resultado.awbs_liberados)} liberados"
                            f"{f', {len(resultado.awbs_retidos)} retidos' if resultado.awbs_retidos else ''}"
                        )
                        self._atualizar_progresso_voo(
                            i, StatusVoo.CONCLUIDO, "Concluido", resumo_curto)
                        voos_sucesso += 1
                        total_liberados += len(resultado.awbs_liberados)
                        total_retidos += len(resultado.awbs_retidos)

                        # Acumula AWBs com termo para verificacao final
                        for awb in resultado.awbs_retidos:
                            awbs_com_termo_sessao[awb] = voo.numero_controle

                        # Telegram: voo concluido
                        if self._telegram_bot and self._telegram_bot.configurado:
                            self._telegram_bot.notificar_voo_concluido(
                                i + 1, len(voos), voo.numero_controle,
                                len(resultado.awbs_liberados), len(resultado.awbs_retidos))
                    else:
                        self._atualizar_progresso_voo(
                            i, StatusVoo.ERRO, "Erro",
                            resultado.erros[0] if resultado.erros else "Erro desconhecido")
                        teve_erro = True
                        voos_erro += 1

                        # Telegram: voo com erro
                        if self._telegram_bot and self._telegram_bot.configurado:
                            self._telegram_bot.notificar_voo_erro(
                                i + 1, len(voos), voo.numero_controle,
                                resultado.erros[0] if resultado.erros else "Erro desconhecido")

                except Exception as e:
                    self._log(f"ERRO no voo {voo.numero_controle}: {e}")
                    self._atualizar_progresso_voo(i, StatusVoo.ERRO, "Erro", str(e)[:60])
                    teve_erro = True
                    voos_erro += 1

                    if self._telegram_bot and self._telegram_bot.configurado:
                        self._telegram_bot.notificar_voo_erro(
                            i + 1, len(voos), voo.numero_controle, str(e)[:100])

                self._log("")

            # Finaliza
            outlook.fechar_aba()
            sefaz.fechar_aba()

            # ========= VERIFICACAO FINAL: AWBs com termo devem estar retidos =========
            if awbs_com_termo_sessao:
                self._verificar_retencao_final(browser, awbs_com_termo_sessao)

            browser.fechar()

            # Barra 100%
            self._atualizar_barra_progresso(len(voos), len(voos), "Concluido!")

            self._log("=" * 60)
            self._log("PROCESSAMENTO CONCLUIDO!")
            self._log("=" * 60)

            self._finalizar_progresso(sucesso=not teve_erro)

            # Telegram: fim do processamento
            if self._telegram_bot and self._telegram_bot.configurado:
                self._telegram_bot.notificar_fim(
                    len(voos), voos_sucesso, voos_erro,
                    total_liberados, total_retidos)

            messagebox.showinfo("Concluido", "Processamento finalizado!")

        except Exception as e:
            self._log(f"ERRO CRITICO: {e}")
            self._finalizar_progresso(sucesso=False)

            # Telegram: erro critico
            if self._telegram_bot and self._telegram_bot.configurado:
                self._telegram_bot.notificar_erro_critico(str(e))

            messagebox.showerror("Erro", str(e))
        finally:
            self._processando = False

    def _verificar_retencao_final(self, browser, awbs_com_termo: dict):
        """
        Verificacao final pos-liberacao: para cada AWB que deveria estar retido,
        consulta o status via busca rapida e confirma que esta realmente retido.
        
        Se algum AWB com termo NAO esta retido, gera ALERTA.
        Isso pega casos como: AWB liberado em voo anterior mas que teve
        novos volumes retidos em voo posterior.
        
        Args:
            browser: NexlogBrowser ja logado
            awbs_com_termo: dict {awb: voo} de AWBs que deveriam estar retidos
        """
        self._log("")
        self._log("=" * 60)
        self._log("VERIFICACAO FINAL: Conferindo AWBs com termo...")
        self._log("=" * 60)

        alertas = []

        from modules.consulta_cliente import ConsultaCliente
        consulta = ConsultaCliente(browser)

        for awb, voo_origem in awbs_com_termo.items():
            try:
                resultado = consulta.consultar_awb(awb)
                status_op = resultado.status_operacional.upper()

                if "RETID" in status_op:
                    self._log(f"  AWB {awb}: OK (Retida)")
                else:
                    # ALERTA: AWB com termo mas NAO esta retido!
                    alerta_msg = (
                        f"ALERTA: AWB {awb} (voo {voo_origem}) tem termo mas "
                        f"status = '{resultado.status_operacional}' (NAO RETIDO!)"
                    )
                    self._log(f"  {alerta_msg}")
                    alertas.append(alerta_msg)

            except Exception as e:
                self._log(f"  AWB {awb}: Erro na verificacao - {str(e)[:50]}")

        # Resumo
        if alertas:
            self._log("")
            self._log(f"{'!'*60}")
            self._log(f"  {len(alertas)} ALERTA(S) - AWBs com termo NAO retidos!")
            self._log(f"  Verificar manualmente:")
            for a in alertas:
                self._log(f"    {a}")
            self._log(f"{'!'*60}")

            # Telegram: avisa sobre alertas
            if self._telegram_bot and self._telegram_bot.configurado:
                msg = (
                    f"ALERTA POS-LIBERACAO!\n\n"
                    f"{len(alertas)} AWB(s) com termo NAO estao retidos:\n"
                )
                for a in alertas[:5]:
                    msg += f"\n{a}"
                self._telegram_bot.notificar(msg)
        else:
            self._log(f"  Todos os {len(awbs_com_termo)} AWBs com termo estao RETIDOS. OK!")

        self._log("")

    def _processar_um_voo(self, voo: Voo, browser, voos_mod, cte_mod, liberar_mod, outlook, sefaz, indice_progresso: int = -1) -> ResultadoProcessamento:
        """Processa um unico voo completo com atualizacao de progresso."""
        resultado = ResultadoProcessamento(voo=voo)

        def _prog(etapa: str):
            """Helper para atualizar progresso do voo atual."""
            if indice_progresso >= 0:
                self._atualizar_progresso_voo(indice_progresso, StatusVoo.PROCESSANDO, etapa)

        # Pega datas
        if HAS_CALENDAR:
            data_ini = self.de_ini.get()
            data_fim = self.de_fim.get()
        else:
            data_ini = self.data_inicial.get().strip()
            data_fim = self.data_final.get().strip()

        # --- ETAPA 1: Buscar chave MDF-e ---
        _prog("1/6 Chave MDF-e")
        self._log("  [1/6] Buscando chave MDF-e...")
        browser.navegar_operacoes_gerenciar_rotas()
        voos_mod.pesquisar_voos(data_ini, data_fim)
        time.sleep(2)
        chave = voos_mod.extrair_chave_mdfe(voo)

        if not chave:
            resultado.erros.append("Chave MDF-e nao encontrada")
            self._log("  ERRO: Chave MDF-e nao encontrada")
            return resultado

        self._log(f"  Chave: {chave[:20]}...")

        # --- ETAPA 2: Baixar manifesto (RETIRA/ENTREGA) ---
        _prog("2/6 Manifesto")
        self._log("  [2/6] Baixando manifesto...")
        # Garante que estamos na pagina de Operacoes > Gerenciar Rotas com a tabela visivel
        # (a etapa 1 pode ter aberto/fechado modais que prejudicam a tabela)
        browser.navegar_operacoes_gerenciar_rotas()
        voos_mod.pesquisar_voos(data_ini, data_fim)
        time.sleep(2)
        caminho_manifesto = voos_mod.baixar_manifesto(voo)
        manifesto = None
        if caminho_manifesto:
            manifesto = parsear_manifesto_pdf(caminho_manifesto)
            self._log(f"  Manifesto: {len(manifesto.awbs_retira)} RETIRA | {len(manifesto.awbs_entrega)} ENTREGA")
        else:
            self._log("  AVISO: Nao conseguiu baixar manifesto")

        # --- ETAPA 3: Verificar Outlook (tenta primeiro) ---
        _prog("3/6 Outlook")
        self._log("  [3/6] Verificando Outlook...")
        consulta = None
        resposta = RespostaEmail.INDEFINIDO
        usou_outlook = False

        try:
            outlook.abrir_outlook()
            resposta = outlook.buscar_por_chave(chave)
            self._log(f"  Resposta email: {resposta.value}")

            if resposta == RespostaEmail.SEM_TERMOS:
                # Email confirma sem termos — confiavel
                self._log("  Email diz SEM termos - voo liberado")
                usou_outlook = True
            elif resposta == RespostaEmail.COM_TERMOS:
                # Tenta baixar PDF do email
                caminho_pdf = outlook.baixar_anexo_pdf()
                if caminho_pdf:
                    consulta = parsear_relatorio_pdf(caminho_pdf)
                    # Valida que o PDF e do voo correto
                    if consulta.chave and chave and consulta.chave != chave:
                        self._log(f"  AVISO: PDF e de outro voo - descartando")
                        consulta = None
                    elif consulta.total_termos > 0:
                        self._log(f"  Relatorio do email: {consulta.total_termos} termos")
                        usou_outlook = True
                    else:
                        # PDF sem termos mas email dizia COM — inconsistente
                        self._log("  AVISO: PDF sem termos (inconsistente) - consultando site")
                        consulta = None
                else:
                    self._log("  Email sem anexo PDF - consultando site SEFAZ")
            # Se NAO_RESPONDEU ou INDEFINIDO -> vai pro site
        except Exception as e:
            self._log(f"  Outlook erro: {e}")

        outlook.voltar_para_nexlog()

        # --- ETAPA 4: Consultar site SEFAZ (se Outlook nao resolveu) ---
        if not usou_outlook and consulta is None and resposta != RespostaEmail.SEM_TERMOS:
            _prog("4/6 Site SEFAZ")
            self._log("  [4/6] Consultando site SEFAZ...")
            try:
                sefaz.abrir_sefaz()
                if not sefaz.logado:
                    sefaz.login()
                sefaz.navegar_consulta_analise_mdfe()
                consulta = sefaz.consultar_chave_mdfe(chave)

                if consulta:
                    # SEGURANCA: Bloqueia APENAS se relatorio NAO foi extraido
                    # (status desconhecido + 0 termos + lista vazia)
                    # Se o parser retornou status real (liberado, sem pendencias, etc)
                    # com 0 termos, e CONFIAVEL — pode liberar.
                    relatorio_nao_extraido = (
                        consulta.status.value == "desconhecido"
                        and consulta.total_termos == 0
                        and not consulta.termos
                        and not consulta.numero_mdfe
                    )
                    if relatorio_nao_extraido:
                        self._log("  Site SEFAZ: INCONCLUSIVO (relatorio nao extraido)")
                        self._log("  SEGURANCA: NAO libera este voo - consultar manualmente")
                        resultado.erros.append("SEFAZ inconclusivo - consultar manualmente")
                        sefaz.voltar_para_nexlog()
                        return resultado
                    else:
                        self._log(f"  Site SEFAZ: {consulta.total_termos} termos "
                                 f"(status: {consulta.status.value})")
                else:
                    self._log("  Site SEFAZ: sem resultado")
                    # Nao respondeu no email E nao tem no site — pula voo
                    if resposta == RespostaEmail.NAO_RESPONDEU:
                        self._log("  SEFAZ nao respondeu e site sem resultado - pulando voo")
                        resultado.erros.append("SEFAZ sem resposta")
                        sefaz.voltar_para_nexlog()
                        return resultado

                sefaz.voltar_para_nexlog()
            except Exception as e:
                self._log(f"  Erro SEFAZ: {e}")
                try:
                    sefaz.voltar_para_nexlog()
                except Exception:
                    pass
        else:
            _prog("4/6 Outlook resolveu")
            self._log("  [4/6] Outlook resolveu - pulando site SEFAZ")

        # --- ETAPA 5: Adicionar comentarios nos CTes retidos ---
        mapa_cte_awb = {}  # Mapeamento CTe -> AWB (usado na etapa 6 para filtrar)

        if consulta and consulta.termos:
            _prog("5/6 Comentarios")
            self._log(f"  [5/6] Adicionando comentarios ({len(consulta.ctes_retidos)} CTes)...")

            for cte in set(consulta.ctes_retidos):
                comentario = consulta.comentario_para_cte(cte)
                awb = cte_mod.buscar_awb_do_cte(cte)

                if awb:
                    mapa_cte_awb[cte] = awb  # Salva mapeamento para etapa 6

                    # Verifica se o AWB e servico MELI/Meli Belly
                    # AWBs MELI nao precisam de comentario de retido
                    servico_awb = cte_mod.verificar_servico_awb(awb)
                    if servico_awb and "MELI" in servico_awb.upper():
                        self._log(f"    AWB {awb} (CTe {cte}): servico MELI - pula comentario")
                        continue

                    sucesso = cte_mod.adicionar_comentario_critico(awb, comentario)
                    if sucesso:
                        resultado.comentarios_adicionados += 1
                    else:
                        resultado.comentarios_falha += 1
                else:
                    resultado.comentarios_falha += 1
                    self._log(f"    CTe {cte}: AWB nao encontrado")

            self._log(f"  Comentarios: {resultado.comentarios_adicionados} OK / {resultado.comentarios_falha} falhas")

            # SEGURANCA: Se teve QUALQUER falha em comentarios, marca no resultado
            if resultado.comentarios_falha > 0:
                self._log(f"  AVISO: {resultado.comentarios_falha} comentario(s) falharam")
        else:
            _prog("5/6 Sem termos")
            self._log("  [5/6] Sem termos para comentar")

        # --- ETAPA 6: Liberar AWBs ---
        _prog("6/6 Liberando")
        self._log("  [6/6] Liberando AWBs...")

        # Calcula quais AWBs liberar
        awbs_para_liberar = set()
        awbs_com_termo = set()
        awbs_meli = set()  # AWBs com servico MELI/Meli Belly (nao libera)

        if manifesto:
            if resposta == RespostaEmail.SEM_TERMOS:
                # Libera todos RETIRA (nao tem termos)
                awbs_para_liberar = manifesto.awbs_retira.copy()
            elif consulta and consulta.termos:
                # TEM termos — precisa excluir AWBs retidos pela SEFAZ
                # Usa o mapeamento CTe->AWB da etapa de comentarios
                # para saber quais AWBs NÃO devem ser liberados
                ctes_retidos = set(consulta.ctes_retidos)

                # SEGURANCA: Verifica se TODOS os CTes com termo foram mapeados
                # Se algum CTe nao foi encontrado, NAO libera nenhum AWB do voo
                ctes_nao_mapeados = [cte for cte in ctes_retidos if cte not in mapa_cte_awb]

                if ctes_nao_mapeados:
                    self._log(f"  SEGURANCA: {len(ctes_nao_mapeados)} CTe(s) com termo NAO mapeados!")
                    for cte in ctes_nao_mapeados:
                        self._log(f"    CTe {cte}: AWB nao encontrado - NAO LIBERA NENHUM")
                    self._log(f"  VOO BLOQUEADO: Nao libera nenhum AWB por seguranca")
                    resultado.erros.append(
                        f"CTe(s) com termo sem AWB: {', '.join(ctes_nao_mapeados)} - voo nao liberado")
                    awbs_para_liberar = set()  # NAO libera nada
                else:
                    # Todos os CTes com termo foram mapeados - pode filtrar normalmente
                    # Coleta AWBs que tem termo (foram mapeados na etapa 5)
                    for cte in ctes_retidos:
                        awb = mapa_cte_awb.get(cte, "")
                        if awb:
                            awbs_com_termo.add(awb)
                            self._log(f"    AWB {awb} (CTe {cte}) -> RETIDO (nao libera)")

                    # Libera apenas RETIRA que NAO tem termo
                    awbs_para_liberar = manifesto.awbs_retira - awbs_com_termo

                if awbs_com_termo:
                    self._log(f"  {len(awbs_com_termo)} AWB(s) com termo (nao libera)")
                    resultado.awbs_retidos = list(awbs_com_termo)
            else:
                # Sem consulta ou consulta sem termos — libera todos RETIRA
                awbs_para_liberar = manifesto.awbs_retira.copy()

            resultado.awbs_domicilio = list(manifesto.awbs_entrega)

            # FILTRO MELI: Verifica servico dos AWBs que seriam liberados
            # AWBs com servico MELI/Meli Belly NAO devem ser liberados
            if awbs_para_liberar:
                for awb in list(awbs_para_liberar):
                    try:
                        servico = cte_mod.verificar_servico_awb(awb)
                        if servico and "MELI" in servico.upper():
                            awbs_meli.add(awb)
                            self._log(f"    AWB {awb}: servico MELI - NAO libera")
                    except Exception:
                        pass  # Se falhar a verificacao, libera normalmente

                if awbs_meli:
                    awbs_para_liberar -= awbs_meli
                    self._log(f"  {len(awbs_meli)} AWB(s) MELI removido(s) da liberacao")

        else:
            self._log("  AVISO: Sem manifesto - nao pode liberar")

        if awbs_para_liberar:
            self._log(f"  Liberando {len(awbs_para_liberar)} AWBs...")
            res_lib = liberar_mod.liberar_awbs(awbs_para_liberar, data_ini, data_fim)
            resultado.awbs_liberados = res_lib.get("selecionados", [])
            resultado.awbs_ja_liberados = res_lib.get("ja_liberados", [])
        else:
            self._log("  Nenhum AWB para liberar")

        return resultado

    def executar(self):
        """Inicia a interface."""
        self.janela.mainloop()


# ========= ENTRY POINT =========
if __name__ == "__main__":
    app = AppAutomacao()
    app.executar()
