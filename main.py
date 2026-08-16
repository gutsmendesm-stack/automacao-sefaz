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

from config import config, PASTA_CONFIG, pausa
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
from modules.uncleared import limpar_awbs, carregar_planilha_sistema, comparar_awbs, salvar_resultado, salvar_comparacao

# ========= HELPER: EMAIL DOMICILIO COM TERMO (TA + DAR) =========

# Modo teste: envia pra este email em vez das bases reais
EMAIL_TESTE_DOMICILIO = "gmmiqoption@gmail.com"
MODO_TESTE_EMAIL = True  # Mude pra False pra enviar pras bases reais


def _montar_emails_base(sigla_base: str) -> list:
    """
    Monta os emails de destino com base na sigla do aeroporto.
    Padrao GOL: {sigla}fk@voegol.com.br e {sigla}fs@voegol.com.br

    Args:
        sigla_base: Sigla de 3 letras (ex: "GRU", "REC", "BSB")

    Returns:
        Lista de emails (ex: ["grufk@voegol.com.br", "grufs@voegol.com.br"])
    """
    if MODO_TESTE_EMAIL:
        return [EMAIL_TESTE_DOMICILIO]

    sigla = sigla_base.lower()
    return [f"{sigla}fk@voegol.com.br", f"{sigla}fs@voegol.com.br"]


def _processar_domicilio_com_termo(
    awb: str,
    termos_awb: list,
    browser,
    outlook,
    log_func=None,
) -> bool:
    """
    Orquestra o processo completo para um AWB domicilio com termo:
    1. Extrai base de origem do AWB (Nexlog)
    2. Para cada termo: baixa TA e DAR no site da SEFAZ
    3. Envia email com os PDFs pra base de origem

    Args:
        awb: Numero do AWB
        termos_awb: Lista de TermoApreensao associados a este AWB
        browser: NexlogBrowser ja logado
        outlook: OutlookWeb ja aberto
        log_func: Funcao de log (opcional)

    Returns:
        True se enviou email com sucesso, False se falhou
    """
    from modules.consulta_cliente import ConsultaCliente, ConsultaTADe

    def _log(msg):
        if log_func:
            log_func(msg)
        logger.info(msg)

    # 1. Extrai base de origem
    consulta = ConsultaCliente(browser)
    base_origem = consulta.extrair_base_origem(awb)

    if not base_origem:
        _log(f"    AWB {awb}: base de origem nao identificada - email nao enviado")
        return False

    _log(f"    AWB {awb}: base origem = {base_origem}")

    # 2. Baixa TA e DAR para cada termo
    tade = ConsultaTADe(browser)
    anexos = []
    termos_info = []

    for termo in termos_awb:
        _log(f"    AWB {awb}: baixando TA/DAR do termo {termo.numero}...")
        resultado_tade = tade.extrair_termo_e_dar(termo.numero)

        if resultado_tade.get("ta_path"):
            anexos.append(resultado_tade["ta_path"])
        if resultado_tade.get("dar_path"):
            anexos.append(resultado_tade["dar_path"])

        termos_info.append({
            "numero": termo.numero,
            "situacao": resultado_tade.get("situacao", termo.situacao.value),
            "valor": resultado_tade.get("valor", ""),
            "erro": resultado_tade.get("erro", ""),
        })

        if resultado_tade.get("erro"):
            _log(f"    AWB {awb}: erro no termo {termo.numero}: {resultado_tade['erro']}")

    # 3. Monta email
    destinatarios = _montar_emails_base(base_origem)
    numeros_termos = ", ".join(t["numero"] for t in termos_info)

    assunto = f"TERMO DE APREENSAO - AWB {awb} - TA {numeros_termos}"

    corpo_linhas = [
        "Prezados,",
        "",
        f"O AWB {awb} foi retido pela SEFAZ-AL ao chegar em MCZ.",
        "",
        "Dados da retencao:",
    ]

    for t in termos_info:
        linha = f"  - Termo TA {t['numero']} | Situacao: {t['situacao']}"
        if t["valor"]:
            linha += f" | Valor: {t['valor']}"
        corpo_linhas.append(linha)

    corpo_linhas.extend([
        "",
        "Seguem em anexo o(s) Termo(s) de Apreensao e a(s) Guia(s) de pagamento (DAR).",
        "",
        "Favor providenciar a regularizacao para liberacao da carga.",
        "",
        "Att,",
        "MCZ Operacoes",
    ])

    corpo = "\n".join(corpo_linhas)

    # 4. Envia email
    _log(f"    AWB {awb}: enviando email para {', '.join(destinatarios)}...")

    sucesso = outlook.enviar_email(
        destinatarios=destinatarios,
        assunto=assunto,
        corpo=corpo,
        anexos=anexos if anexos else None,
    )

    if sucesso:
        _log(f"    AWB {awb}: email enviado com sucesso!")
    else:
        _log(f"    AWB {awb}: FALHA ao enviar email")

    # Volta pro Nexlog depois de tudo
    outlook.voltar_para_nexlog()

    return sucesso


# ========= HELPER: ENVIO GOOGLE FORMS (AWBs domicilio com termo) =========

GOOGLE_FORMS_URL = (
    "https://docs.google.com/forms/d/e/"
    "1FAIpQLSfgTDhvcAVH2yzp6zZWcamznyqjlrjnVJ-ePxHpMIDJokOzUA/formResponse"
)
GOOGLE_FORMS_ENTRY = "entry.937340729"


def _enviar_awb_google_forms(awb: str) -> bool:
    """
    Envia um AWB para o Google Forms de controle de termos domicilio.
    Faz POST direto na URL do formResponse (nao precisa abrir navegador).

    Args:
        awb: Numero do AWB (11 digitos)

    Returns:
        True se enviou com sucesso, False se falhou
    """
    import requests

    try:
        dados = {GOOGLE_FORMS_ENTRY: awb}
        resp = requests.post(GOOGLE_FORMS_URL, data=dados, timeout=10)
        # Google Forms retorna 200 mesmo com campo invalido,
        # mas redireciona pra pagina de confirmacao
        return resp.status_code == 200
    except Exception as e:
        logger.warning(f"Erro ao enviar AWB {awb} pro Google Forms: {e}")
        return False


# ========= CUSTOMTKINTER CONFIG =========
ctk.set_appearance_mode("dark")
ctk.set_default_color_theme("blue")


# ========= HELPER: COMPARACAO DE CHAVES (tolerante a erros OCR) =========
def _chaves_compativeis(chave_a: str, chave_b: str, tolerancia: int = 4) -> bool:
    """
    Compara duas chaves de MDF-e com tolerancia a erros de OCR.
    O Tesseract pode confundir digitos similares (3/5, 6/8, 1/7, 0/O).
    
    Estrategia: compara caractere a caractere e permite ate N diferencas.
    Tambem verifica se o "miolo" da chave bate (posicoes 6-38 sao mais confiaveis
    porque o inicio pode ter artefatos de borda no scan).
    
    Args:
        chave_a: Chave extraida do PDF (pode ter erros OCR)
        chave_b: Chave do Nexlog (confiavel)
        tolerancia: Numero maximo de caracteres diferentes permitidos
    
    Returns:
        True se as chaves sao provavelmente do mesmo MDF-e
    """
    if not chave_a or not chave_b:
        return False
    
    # Se sao iguais, obvio
    if chave_a == chave_b:
        return True
    
    # Se tamanhos muito diferentes, nao e a mesma chave
    if abs(len(chave_a) - len(chave_b)) > 2:
        return False
    
    # Compara caractere a caractere (usa o menor comprimento)
    tamanho = min(len(chave_a), len(chave_b))
    diferencas = sum(1 for i in range(tamanho) if chave_a[i] != chave_b[i])
    
    # Se diferenca esta dentro da tolerancia, aceita
    if diferencas <= tolerancia:
        return True
    
    # Fallback: compara apenas o miolo (posicoes 6 a 38)
    # O inicio e fim da chave sao mais suscetíveis a erros de OCR
    if len(chave_a) >= 40 and len(chave_b) >= 40:
        miolo_a = chave_a[6:38]
        miolo_b = chave_b[6:38]
        dif_miolo = sum(1 for i in range(len(miolo_a)) if miolo_a[i] != miolo_b[i])
        if dif_miolo <= 2:
            return True
    
    return False

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
        self.email_domicilio_ativo = tk.BooleanVar(value=True)  # Enviar email TA+DAR pras bases

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

        # --- ABA UNCLEARED ---
        self.tabview.add("Uncleared")
        self._criar_aba_uncleared(self.tabview.tab("Uncleared"))

        # --- ABA DOMICILIO ---
        self.tabview.add("Domicilio")
        self._criar_aba_domicilio(self.tabview.tab("Domicilio"))

        # --- ABA SEFAZ (Envio de Manifestos) ---
        self.tabview.add("SEFAZ")
        self._criar_aba_sefaz(self.tabview.tab("SEFAZ"))

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

        ctk.CTkCheckBox(frame_bottom, variable=self.email_domicilio_ativo,
                        text="Enviar email TA+DAR",
                        font=ctk.CTkFont(size=11)).pack(side="left")

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

    # ========= ABA SEFAZ (Envio de Manifestos DAMDFE) =========

    def _criar_aba_sefaz(self, parent):
        """
        Aba para envio de manifestos (DAMDFE) pra SEFAZ-AL via email.
        Fluxo: busca voos do dia → seleciona quais enviar → baixa DAMDFE → envia email.
        Destinatarios: pfcentraldetransportadoras@sefaz.al.gov.br + pfdigitadores@hotmail.com
        """
        scroll = ctk.CTkScrollableFrame(parent, fg_color="transparent")
        scroll.pack(fill="both", expand=True)

        # --- HEADER ---
        header = ctk.CTkFrame(scroll, corner_radius=8, fg_color="#1f2937")
        header.pack(fill="x", padx=8, pady=(8, 4))
        h_inner = ctk.CTkFrame(header, fg_color="transparent")
        h_inner.pack(fill="x", padx=16, pady=12)
        ctk.CTkLabel(h_inner, text="Envio de Manifestos (SEFAZ)",
                     font=ctk.CTkFont(size=16, weight="bold")).pack(anchor="w")
        ctk.CTkLabel(h_inner, text="Baixa DAMDFE e envia por email para analise da SEFAZ-AL",
                     font=ctk.CTkFont(size=11), text_color="#9ca3af").pack(anchor="w")

        # --- CARD: BUSCA DE VOOS ---
        card_busca = ctk.CTkFrame(scroll, corner_radius=8)
        card_busca.pack(fill="x", padx=8, pady=4)
        b_inner = ctk.CTkFrame(card_busca, fg_color="transparent")
        b_inner.pack(fill="x", padx=16, pady=12)

        # Datas
        frame_datas = ctk.CTkFrame(b_inner, fg_color="transparent")
        frame_datas.pack(fill="x")
        ctk.CTkLabel(frame_datas, text="Data Ini:",
                     font=ctk.CTkFont(size=12)).pack(side="left")
        self.sefaz_data_ini = ctk.CTkEntry(frame_datas, width=110, placeholder_text="DD/MM/AAAA")
        self.sefaz_data_ini.pack(side="left", padx=(4, 16))
        ctk.CTkLabel(frame_datas, text="Data Fim:",
                     font=ctk.CTkFont(size=12)).pack(side="left")
        self.sefaz_data_fim = ctk.CTkEntry(frame_datas, width=110, placeholder_text="DD/MM/AAAA")
        self.sefaz_data_fim.pack(side="left", padx=(4, 16))

        ctk.CTkButton(frame_datas, text="Buscar Voos", width=120, height=32,
                      fg_color="#3b82f6", hover_color="#2563eb",
                      command=self._sefaz_buscar_voos_thread).pack(side="left", padx=(8, 0))

        # Preenche data de hoje
        hoje = datetime.now().strftime("%d/%m/%Y")
        self.sefaz_data_ini.insert(0, hoje)
        self.sefaz_data_fim.insert(0, hoje)

        # --- CARD: LISTA DE VOOS (com checkboxes) ---
        card_voos = ctk.CTkFrame(scroll, corner_radius=8)
        card_voos.pack(fill="x", padx=8, pady=4)
        v_inner = ctk.CTkFrame(card_voos, fg_color="transparent")
        v_inner.pack(fill="x", padx=16, pady=12)

        frame_titulo_voos = ctk.CTkFrame(v_inner, fg_color="transparent")
        frame_titulo_voos.pack(fill="x")
        self.sefaz_lbl_voos = ctk.CTkLabel(frame_titulo_voos, text="Voos encontrados: 0",
                                            font=ctk.CTkFont(size=12, weight="bold"))
        self.sefaz_lbl_voos.pack(side="left")

        # Botoes selecionar/deselecionar tudo
        ctk.CTkButton(frame_titulo_voos, text="Todos", width=60, height=26,
                      fg_color="#374151", hover_color="#4b5563",
                      font=ctk.CTkFont(size=10),
                      command=self._sefaz_selecionar_todos).pack(side="right", padx=(4, 0))
        ctk.CTkButton(frame_titulo_voos, text="Nenhum", width=60, height=26,
                      fg_color="#374151", hover_color="#4b5563",
                      font=ctk.CTkFont(size=10),
                      command=self._sefaz_deselecionar_todos).pack(side="right")

        # Frame scrollavel para lista de voos com checkboxes
        self.sefaz_frame_voos = ctk.CTkScrollableFrame(v_inner, height=150,
                                                        fg_color="#0f172a", corner_radius=6)
        self.sefaz_frame_voos.pack(fill="x", pady=(8, 0))

        # Estado interno
        self._sefaz_voos: list = []          # Lista de Voo
        self._sefaz_checkboxes: list = []    # Lista de tk.BooleanVar
        self._sefaz_processando = False

        # --- CARD: EXECUCAO ---
        card_exec = ctk.CTkFrame(scroll, corner_radius=8)
        card_exec.pack(fill="x", padx=8, pady=(4, 8))
        e_inner = ctk.CTkFrame(card_exec, fg_color="transparent")
        e_inner.pack(fill="x", padx=16, pady=12)

        self.sefaz_progresso = ctk.CTkProgressBar(e_inner, height=8, corner_radius=4)
        self.sefaz_progresso.pack(fill="x", pady=(0, 6))
        self.sefaz_progresso.set(0)

        self.sefaz_txt_log = ctk.CTkTextbox(e_inner, height=180, corner_radius=6,
                                             font=ctk.CTkFont(family="Consolas", size=10),
                                             fg_color="#0f172a", text_color="#4ade80")
        self.sefaz_txt_log.pack(fill="x", pady=(0, 8))

        btn_frame = ctk.CTkFrame(e_inner, fg_color="transparent")
        btn_frame.pack(fill="x")
        self.sefaz_btn_enviar = ctk.CTkButton(
            btn_frame, text="ENVIAR MANIFESTOS", width=200, height=38,
            fg_color="#10b981", hover_color="#059669",
            font=ctk.CTkFont(size=13, weight="bold"),
            command=self._sefaz_enviar_thread
        )
        self.sefaz_btn_enviar.pack(side="left")

        self.sefaz_btn_fechar = ctk.CTkButton(
            btn_frame, text="Fechar Nav", width=120, height=38,
            fg_color="#374151", hover_color="#4b5563",
            state="disabled", command=self._sefaz_fechar_nav
        )
        self.sefaz_btn_fechar.pack(side="left", padx=(8, 0))

    def _sefaz_log(self, msg: str):
        """Adiciona mensagem ao log da aba SEFAZ."""
        ts = datetime.now().strftime("%H:%M:%S")
        self.janela.after(0, lambda: self.sefaz_txt_log.insert("end", f"[{ts}] {msg}\n"))
        self.janela.after(0, lambda: self.sefaz_txt_log.see("end"))

    def _sefaz_selecionar_todos(self):
        """Marca todos os checkboxes de voos."""
        for var in self._sefaz_checkboxes:
            var.set(True)

    def _sefaz_deselecionar_todos(self):
        """Desmarca todos os checkboxes de voos."""
        for var in self._sefaz_checkboxes:
            var.set(False)

    def _sefaz_atualizar_lista_voos(self, voos):
        """Atualiza a lista de voos na aba SEFAZ com checkboxes."""
        # Limpa widgets anteriores
        for widget in self.sefaz_frame_voos.winfo_children():
            widget.destroy()

        self._sefaz_voos = voos
        self._sefaz_checkboxes = []

        for i, voo in enumerate(voos):
            var = tk.BooleanVar(value=True)  # Marcado por padrao
            self._sefaz_checkboxes.append(var)

            texto = f"{voo.numero_controle}  |  {voo.etapas}  |  {voo.data_chegada}"
            chk = ctk.CTkCheckBox(
                self.sefaz_frame_voos, text=texto, variable=var,
                font=ctk.CTkFont(family="Consolas", size=11),
                text_color="#e5e7eb"
            )
            chk.pack(anchor="w", pady=2, padx=4)

        self.sefaz_lbl_voos.configure(text=f"Voos encontrados: {len(voos)}")

    def _sefaz_obter_voos_selecionados(self):
        """Retorna lista de voos com checkbox marcado."""
        selecionados = []
        for i, var in enumerate(self._sefaz_checkboxes):
            if var.get() and i < len(self._sefaz_voos):
                selecionados.append(self._sefaz_voos[i])
        return selecionados

    def _sefaz_buscar_voos_thread(self):
        """Inicia busca de voos em thread separada."""
        if self._sefaz_processando:
            return
        threading.Thread(target=self._sefaz_buscar_voos, daemon=True).start()

    def _sefaz_buscar_voos(self):
        """Busca voos no Nexlog para a aba SEFAZ."""
        self._sefaz_processando = True
        self._sefaz_log("Buscando voos no Nexlog...")

        data_ini = self.sefaz_data_ini.get().strip()
        data_fim = self.sefaz_data_fim.get().strip()

        if not data_ini or not data_fim:
            self._sefaz_log("ERRO: Preencha as datas.")
            self._sefaz_processando = False
            return

        try:
            browser = NexlogBrowser()
            browser.iniciar()
            browser.login_nexlog()

            voos_mod = NexlogVoos(browser)
            voos = voos_mod.pesquisar_voos(data_ini, data_fim)

            self._sefaz_log(f"Encontrados {len(voos)} voos!")
            self.janela.after(0, lambda: self._sefaz_atualizar_lista_voos(voos))

            # Guarda referencia do browser pra reutilizar no envio
            self._sefaz_browser = browser
            self.janela.after(0, lambda: self.sefaz_btn_fechar.configure(state="normal"))

        except Exception as e:
            self._sefaz_log(f"ERRO ao buscar voos: {e}")
            try:
                browser.fechar()
            except Exception:
                pass
        finally:
            self._sefaz_processando = False

    def _sefaz_fechar_nav(self):
        """Fecha o navegador da aba SEFAZ."""
        try:
            if hasattr(self, '_sefaz_browser') and self._sefaz_browser:
                self._sefaz_browser.fechar()
                self._sefaz_browser = None
        except Exception:
            pass
        self.sefaz_btn_fechar.configure(state="disabled")
        self._sefaz_log("Navegador fechado.")

    def _sefaz_enviar_thread(self):
        """Inicia envio de manifestos em thread separada."""
        if self._sefaz_processando:
            return

        voos = self._sefaz_obter_voos_selecionados()
        if not voos:
            self._sefaz_log("Selecione pelo menos um voo.")
            return

        self._sefaz_processando = True
        self.sefaz_btn_enviar.configure(state="disabled", text="ENVIANDO...")
        self.sefaz_txt_log.delete("1.0", "end")
        self.sefaz_progresso.set(0)

        threading.Thread(target=self._sefaz_processar, args=(voos,), daemon=True).start()

    def _sefaz_processar(self, voos):
        """
        Processamento principal da aba SEFAZ: para cada voo selecionado,
        baixa o DAMDFE e envia email para a SEFAZ-AL.

        Destinatarios:
            - pfcentraldetransportadoras@sefaz.al.gov.br
            - pfdigitadores@hotmail.com

        Assunto: MANIFESTO {numero_manifesto} VOO {numero_voo}
        Corpo: Chave de acesso do MDF-e + anexo DAMDFE
        """
        destinatarios = [
            "pfcentraldetransportadoras@sefaz.al.gov.br",
            "pfdigitadores@hotmail.com",
        ]

        # Modo teste: envia pro email pessoal em vez da SEFAZ
        if MODO_TESTE_EMAIL:
            destinatarios = [EMAIL_TESTE_DOMICILIO]
            self._sefaz_log("** MODO TESTE: emails serao enviados para " + EMAIL_TESTE_DOMICILIO + " **")

        total = len(voos)
        enviados = 0
        erros = 0

        try:
            # Reutiliza browser se ja aberto, senao abre novo
            if not hasattr(self, '_sefaz_browser') or not self._sefaz_browser:
                self._sefaz_log("Abrindo navegador...")
                browser = NexlogBrowser()
                browser.iniciar()
                browser.login_nexlog()
                self._sefaz_browser = browser
                self.janela.after(0, lambda: self.sefaz_btn_fechar.configure(state="normal"))
            else:
                browser = self._sefaz_browser

            voos_mod = NexlogVoos(browser)
            outlook = OutlookWeb(browser.driver)

            # Navega pra Gerenciar Rotas (base pra buscar cada voo)
            browser.navegar_operacoes_gerenciar_rotas()
            pausa(2)

            # Precisa repesquisar pra tabela estar preenchida
            data_ini = self.sefaz_data_ini.get().strip()
            data_fim = self.sefaz_data_fim.get().strip()
            voos_mod.pesquisar_voos(data_ini, data_fim)
            pausa(3)  # DataTables precisa de tempo pra renderizar completamente

            for i, voo in enumerate(voos):
                self._sefaz_log(f"\n--- Voo {i+1}/{total}: {voo.numero_controle} ({voo.etapas}) ---")

                # Atualiza progresso
                progresso = (i + 1) / total
                self.janela.after(0, lambda p=progresso: self.sefaz_progresso.set(p))

                try:
                    # Baixa DAMDFE (abre modal, extrai chave, clica Imprimir DAMDFE)
                    self._sefaz_log("Abrindo modal MDFe e baixando DAMDFE...")
                    chave, caminho_pdf = voos_mod.imprimir_damdfe(voo)

                    if not chave:
                        self._sefaz_log(f"ERRO: Chave MDF-e nao encontrada para {voo.numero_controle}")
                        erros += 1
                        continue

                    if not caminho_pdf:
                        self._sefaz_log(f"ERRO: Download DAMDFE falhou para {voo.numero_controle}")
                        erros += 1
                        continue

                    self._sefaz_log(f"DAMDFE baixado: {os.path.basename(caminho_pdf)}")
                    self._sefaz_log(f"Chave: {chave}")

                    # Monta dados do email
                    # Numero do manifesto = ultimos 9 digitos da chave (sequencial MDF-e)
                    numero_manifesto = chave[25:34].lstrip("0")  # remove zeros a esquerda
                    assunto = f"MANIFESTO {numero_manifesto} VOO {voo.numero_controle}"
                    corpo_html = (
                        f"Boa noite!<br><br>"
                        f"Segue anexo manifesto: <b>{numero_manifesto}</b>.<br><br>"
                        f"Abaixo chave de acesso:<br><br>"
                        f"<b>{chave}</b>"
                    )

                    # Envia email
                    self._sefaz_log(f"Enviando email: {assunto}")
                    sucesso = outlook.enviar_email(
                        destinatarios=destinatarios,
                        assunto=assunto,
                        corpo="",
                        anexos=[caminho_pdf],
                        corpo_html=corpo_html,
                    )

                    if sucesso:
                        self._sefaz_log(f"Email enviado com sucesso!")
                        enviados += 1
                    else:
                        self._sefaz_log(f"ERRO: Falha ao enviar email para {voo.numero_controle}")
                        erros += 1

                except Exception as e:
                    self._sefaz_log(f"ERRO no voo {voo.numero_controle}: {e}")
                    erros += 1

                # Pausa entre voos pra nao sobrecarregar
                if i < total - 1:
                    pausa(2)

        except Exception as e:
            self._sefaz_log(f"ERRO GERAL: {e}")

        # Resumo final
        self._sefaz_log(f"\n{'='*40}")
        self._sefaz_log(f"RESUMO: {enviados} enviado(s), {erros} erro(s) de {total} voo(s)")
        self._sefaz_log(f"{'='*40}")

        # Restaura botao
        self.janela.after(0, lambda: self.sefaz_btn_enviar.configure(
            state="normal", text="ENVIAR MANIFESTOS"))
        self._sefaz_processando = False

    # ========= ABA DOMICILIO =========

    def _criar_aba_domicilio(self, parent):
        """
        Aba Domicilio completa: todas as funcoes de entrega a domicilio.
        Visual profissional com cards, checkboxes em grid, fila, progresso.
        """
        scroll = ctk.CTkScrollableFrame(parent, fg_color="transparent")
        scroll.pack(fill="both", expand=True)

        # --- HEADER ---
        header = ctk.CTkFrame(scroll, corner_radius=8, fg_color="#1f2937")
        header.pack(fill="x", padx=8, pady=(8, 4))
        h_inner = ctk.CTkFrame(header, fg_color="transparent")
        h_inner.pack(fill="x", padx=16, pady=12)
        ctk.CTkLabel(h_inner, text="Entrega a Domicilio",
                     font=ctk.CTkFont(size=16, weight="bold")).pack(anchor="w")
        ctk.CTkLabel(h_inner, text="Consultas, liberacoes, coletas e vencimentos",
                     font=ctk.CTkFont(size=11), text_color="#9ca3af").pack(anchor="w")

        # --- CARD: CTEs/AWBs ---
        card_ctes = ctk.CTkFrame(scroll, corner_radius=8)
        card_ctes.pack(fill="x", padx=8, pady=4)
        c_inner = ctk.CTkFrame(card_ctes, fg_color="transparent")
        c_inner.pack(fill="x", padx=16, pady=12)
        ctk.CTkLabel(c_inner, text="CTEs / AWBs / Listas (um por linha)",
                     font=ctk.CTkFont(size=12, weight="bold")).pack(anchor="w")
        self.dom_txt_ctes = ctk.CTkTextbox(c_inner, height=90, corner_radius=6,
                                            font=ctk.CTkFont(family="Consolas", size=11))
        self.dom_txt_ctes.pack(fill="x", pady=(6, 0))

        # --- CARD: ACOES ---
        card_acoes = ctk.CTkFrame(scroll, corner_radius=8)
        card_acoes.pack(fill="x", padx=8, pady=4)
        a_inner = ctk.CTkFrame(card_acoes, fg_color="transparent")
        a_inner.pack(fill="x", padx=16, pady=12)
        ctk.CTkLabel(a_inner, text="Acoes do Processo",
                     font=ctk.CTkFont(size=12, weight="bold")).pack(anchor="w", pady=(0, 6))

        grid_chk = ctk.CTkFrame(a_inner, fg_color="transparent")
        grid_chk.pack(fill="x")
        grid_chk.grid_columnconfigure((0, 1, 2), weight=1, uniform="chk")

        self.dom_chk_ta = ctk.CTkCheckBox(grid_chk, text="Consulta TA/SEFAZ")
        self.dom_chk_ta.grid(row=0, column=0, sticky="w", pady=4, padx=4)
        self.dom_chk_vencidas = ctk.CTkCheckBox(grid_chk, text="Consultar Vencidas")
        self.dom_chk_vencidas.grid(row=0, column=1, sticky="w", pady=4, padx=4)
        self.dom_chk_awb = ctk.CTkCheckBox(grid_chk, text="Gerar PDF AWB")
        self.dom_chk_awb.grid(row=0, column=2, sticky="w", pady=4, padx=4)
        self.dom_chk_lib = ctk.CTkCheckBox(grid_chk, text="Liberar Retencao")
        self.dom_chk_lib.grid(row=1, column=0, sticky="w", pady=4, padx=4)
        self.dom_chk_col = ctk.CTkCheckBox(grid_chk, text="Coletas/Entregas")
        self.dom_chk_col.grid(row=1, column=1, sticky="w", pady=4, padx=4)
        self.dom_chk_venc_hoje = ctk.CTkCheckBox(grid_chk, text="Vencendo Hoje (Lista)")
        self.dom_chk_venc_hoje.grid(row=1, column=2, sticky="w", pady=4, padx=4)

        # Datas
        frame_datas = ctk.CTkFrame(a_inner, fg_color="transparent")
        frame_datas.pack(fill="x", pady=(8, 0))
        ctk.CTkLabel(frame_datas, text="Data Ini:", font=ctk.CTkFont(size=11)).pack(side="left")
        self.dom_data_ini = ctk.CTkEntry(frame_datas, width=110, placeholder_text="DDMMAAAA")
        self.dom_data_ini.pack(side="left", padx=(4, 16))
        ctk.CTkLabel(frame_datas, text="Data Fim:", font=ctk.CTkFont(size=11)).pack(side="left")
        self.dom_data_fim = ctk.CTkEntry(frame_datas, width=110, placeholder_text="DDMMAAAA")
        self.dom_data_fim.pack(side="left", padx=4)

        # --- CARD: FILA ---
        card_fila = ctk.CTkFrame(scroll, corner_radius=8)
        card_fila.pack(fill="x", padx=8, pady=4)
        f_inner = ctk.CTkFrame(card_fila, fg_color="transparent")
        f_inner.pack(fill="x", padx=16, pady=10)
        self.dom_lbl_fila = ctk.CTkLabel(f_inner, text="Fila: 0 processo(s)",
                                          font=ctk.CTkFont(size=12, weight="bold"))
        self.dom_lbl_fila.pack(side="left")
        ctk.CTkButton(f_inner, text="+ Fila", width=100, height=30,
                      fg_color="#3b82f6", hover_color="#2563eb",
                      command=self._dom_adicionar_fila).pack(side="right", padx=(4, 0))
        ctk.CTkButton(f_inner, text="Limpar", width=80, height=30,
                      fg_color="#374151", hover_color="#4b5563",
                      command=self._dom_limpar_fila).pack(side="right")

        # --- CARD: EXECUCAO ---
        card_exec = ctk.CTkFrame(scroll, corner_radius=8)
        card_exec.pack(fill="x", padx=8, pady=(4, 8))
        e_inner = ctk.CTkFrame(card_exec, fg_color="transparent")
        e_inner.pack(fill="x", padx=16, pady=12)

        self.dom_progresso = ctk.CTkProgressBar(e_inner, height=8, corner_radius=4)
        self.dom_progresso.pack(fill="x", pady=(0, 6))
        self.dom_progresso.set(0)

        self.dom_txt_log = ctk.CTkTextbox(e_inner, height=150, corner_radius=6,
                                           font=ctk.CTkFont(family="Consolas", size=10),
                                           fg_color="#0f172a", text_color="#4ade80")
        self.dom_txt_log.pack(fill="x", pady=(0, 8))

        btn_frame = ctk.CTkFrame(e_inner, fg_color="transparent")
        btn_frame.pack(fill="x")
        self.dom_btn_iniciar = ctk.CTkButton(btn_frame, text="INICIAR", width=180, height=38,
                                              fg_color="#f97316", hover_color="#ea580c",
                                              font=ctk.CTkFont(size=13, weight="bold"),
                                              command=self._dom_iniciar)
        self.dom_btn_iniciar.pack(side="left")
        self.dom_btn_fechar = ctk.CTkButton(btn_frame, text="Fechar Nav", width=120, height=38,
                                             fg_color="#374151", hover_color="#4b5563",
                                             state="disabled", command=self._dom_fechar_nav)
        self.dom_btn_fechar.pack(side="left", padx=(8, 0))

        # Estado interno
        self._dom_fila = []
        self._dom_aut = None

    def _dom_montar_job(self) -> Optional[dict]:
        """Monta um job a partir dos campos preenchidos."""
        raw = self.dom_txt_ctes.get("1.0", "end")
        ctes = [l.strip() for l in raw.splitlines() if l.strip()]
        if not ctes:
            return None
        return {
            "ctes": ctes,
            "ta": bool(self.dom_chk_ta.get()),
            "vencidas": bool(self.dom_chk_vencidas.get()),
            "awb": bool(self.dom_chk_awb.get()),
            "lib": bool(self.dom_chk_lib.get()),
            "col": bool(self.dom_chk_col.get()),
            "venc_hoje": bool(self.dom_chk_venc_hoje.get()),
            "data_ini": self.dom_data_ini.get().strip(),
            "data_fim": self.dom_data_fim.get().strip(),
        }

    def _dom_adicionar_fila(self):
        """Adiciona processo atual a fila."""
        job = self._dom_montar_job()
        if not job:
            return
        self._dom_fila.append(job)
        self.dom_txt_ctes.delete("1.0", "end")
        self.dom_lbl_fila.configure(text=f"Fila: {len(self._dom_fila)} processo(s)")
        self._dom_log(f"+ Processo adicionado ({len(job['ctes'])} item(ns)). Fila: {len(self._dom_fila)}")

    def _dom_limpar_fila(self):
        """Limpa a fila."""
        self._dom_fila = []
        self.dom_lbl_fila.configure(text="Fila: 0 processo(s)")

    def _dom_log(self, msg: str):
        """Adiciona mensagem ao log da aba domicilio."""
        ts = datetime.now().strftime("%H:%M:%S")
        self.janela.after(0, lambda: self.dom_txt_log.insert("end", f"[{ts}] {msg}\n"))
        self.janela.after(0, lambda: self.dom_txt_log.see("end"))

    def _dom_fechar_nav(self):
        """Fecha navegador da aba domicilio."""
        if self._dom_aut:
            self._dom_aut.fechar()
            self._dom_aut = None
        self.dom_btn_fechar.configure(state="disabled")

    def _dom_iniciar(self):
        """Inicia execucao da fila de domicilio."""
        job_atual = self._dom_montar_job()
        if job_atual:
            self._dom_fila.append(job_atual)
            self.dom_txt_ctes.delete("1.0", "end")

        if not self._dom_fila:
            self._dom_log("Insira codigos e selecione ao menos uma acao.")
            return

        jobs = list(self._dom_fila)
        self._dom_fila = []
        self.dom_lbl_fila.configure(text="Fila: 0 processo(s)")
        self.dom_btn_iniciar.configure(state="disabled", text="PROCESSANDO...")
        self.dom_progresso.set(0)
        self.dom_txt_log.delete("1.0", "end")

        threading.Thread(target=self._dom_executar_fila, args=(jobs,), daemon=True).start()

    def _dom_executar_fila(self, jobs: list):
        """Executa fila de processos de domicilio."""
        from modules.nexlog_domicilio import NexlogDomicilio
        from modules.consulta_cliente import ConsultaCliente

        total_itens = sum(len(j["ctes"]) for j in jobs) or 1
        processados = 0
        sucesso = 0
        erros = 0
        resultados_excel = []

        try:
            browser = NexlogBrowser()
            browser.iniciar()
            browser.login_nexlog()
            self._dom_aut = browser

            dom = NexlogDomicilio(browser)
            consulta = ConsultaCliente(browser)

            for idx, job in enumerate(jobs, 1):
                self._dom_log(f"\n===== Processo {idx}/{len(jobs)} ({len(job['ctes'])} itens) =====")

                if job["lib"]:
                    browser.navegar_vendas_retencao_lista()
                    from modules.nexlog_liberar import NexlogLiberar
                    lib_mod = NexlogLiberar(browser)
                    try:
                        lib_mod.configurar_filtros(job["data_ini"] or "01", "")
                    except Exception:
                        pass

                if job["col"] and job["data_ini"]:
                    dom.preparar_coletas(job["data_ini"], job["data_fim"])

                if job["venc_hoje"] and job["data_ini"]:
                    dom.preparar_vencendo_hoje(job["data_ini"], job["data_fim"])

                for cte in job["ctes"]:
                    try:
                        self._dom_log(f"\n--- {cte} ---")

                        if job["ta"]:
                            res = consulta.consultar_awb(cte)
                            self._dom_log(
                                f"  Status: {res.status_operacional} | "
                                f"Termos: {', '.join(res.termos) if res.termos else 'nenhum'}"
                            )
                            resultados_excel.append({
                                "CTE": cte, "STATUS": res.status_operacional,
                                "TA": ", ".join(res.termos) if res.termos else "SEM TA",
                            })

                        if job["vencidas"]:
                            v = dom.consultar_vencimento(cte)
                            self._dom_log(f"  Vencimento: {v.data_vencimento} | Status: {v.status_carga}")

                        if job["awb"]:
                            self._dom_log(f"  PDF AWB: disponivel via /pdf no Telegram")

                        if job["lib"]:
                            marcados = dom.marcar_cte_liberacao(cte)
                            self._dom_log(f"  Liberacao: {marcados} marcado(s)")

                        if job["col"]:
                            marcados = dom.marcar_cte_coleta(cte)
                            self._dom_log(f"  Coleta: {marcados} marcado(s)")

                        if job["venc_hoje"]:
                            res_lista = dom.consultar_lista(cte)
                            self._dom_log(
                                f"  Lista: Motorista={res_lista.motorista}, "
                                f"{len(res_lista.awbs)} AWB(s)"
                            )
                            for awb in res_lista.awbs:
                                v = dom.consultar_vencimento(awb)
                                self._dom_log(f"    {awb}: {v.data_vencimento}")
                                resultados_excel.append({
                                    "LISTA": res_lista.numero_lista,
                                    "MOTORISTA": res_lista.motorista,
                                    "AWB": awb,
                                    "DATA_VENCIMENTO": v.data_vencimento,
                                })

                        sucesso += 1
                    except Exception as e:
                        self._dom_log(f"  ERRO: {e}")
                        erros += 1

                    processados += 1
                    prog = processados / total_itens
                    self.janela.after(0, lambda p=prog: self.dom_progresso.set(p))

                if job["lib"]:
                    dom.finalizar_liberacao()
                    self._dom_log("Liberacao finalizada!")
                if job["col"]:
                    dom.finalizar_coletas()
                    self._dom_log("Coletas finalizadas!")

            # Relatorio Excel
            if resultados_excel:
                try:
                    from openpyxl import Workbook
                    import os
                    desktop = os.path.join(os.path.expanduser("~"), "Desktop")
                    nome = f"Relatorio_Domicilio_{datetime.now().strftime('%Y%m%d_%H%M')}.xlsx"
                    caminho = os.path.join(desktop, nome)
                    wb = Workbook()
                    ws = wb.active
                    ws.title = "Domicilio"
                    headers = list(resultados_excel[0].keys())
                    ws.append(headers)
                    for item in resultados_excel:
                        ws.append([item.get(h, "") for h in headers])
                    wb.save(caminho)
                    self._dom_log(f"\nRelatorio salvo: {nome}")
                except Exception as e:
                    self._dom_log(f"\nErro ao gerar Excel: {e}")

            browser.fechar()
            self._dom_aut = None
            self._dom_log(f"\nCONCLUIDO! Sucesso: {sucesso} | Erros: {erros}")

        except Exception as e:
            self._dom_log(f"\nERRO CRITICO: {e}")
        finally:
            self.janela.after(0, lambda: self.dom_btn_iniciar.configure(state="normal", text="INICIAR"))
            self.janela.after(0, lambda: self.dom_btn_fechar.configure(state="normal"))
            self.janela.after(0, lambda: self.dom_progresso.set(1.0))

    # ========= ABA UNCLEARED =========

    def _criar_aba_uncleared(self, parent):
        """Aba Uncleared: limpeza, deduplicacao e comparacao de AWBs."""
        from tkinter import filedialog

        # Estado da aba
        self._uncleared_awbs_limpos: List[str] = []
        self._uncleared_awbs_planilha: set = set()
        self._uncleared_awbs_info: dict = {}
        self._uncleared_resultado_comparacao: dict = {}
        self._uncleared_arquivo_planilha: str = ""

        # --- Layout principal: dois paineis lado a lado ---
        main_frame = ctk.CTkFrame(parent, fg_color="transparent")
        main_frame.pack(fill="both", expand=True, padx=8, pady=8)

        # Painel esquerdo: Input (colar AWBs)
        left_frame = ctk.CTkFrame(main_frame, corner_radius=8)
        left_frame.pack(side="left", fill="both", expand=True, padx=(0, 4))

        # Header esquerdo
        left_header = ctk.CTkFrame(left_frame, fg_color="transparent")
        left_header.pack(fill="x", padx=12, pady=(12, 6))

        ctk.CTkLabel(left_header, text="AWBs para limpar",
                     font=ctk.CTkFont(size=13, weight="bold")).pack(side="left")

        self._uncleared_lbl_count_input = ctk.CTkLabel(
            left_header, text="0 AWBs",
            font=ctk.CTkFont(size=11), text_color="#6b7280")
        self._uncleared_lbl_count_input.pack(side="right")

        # Caixa de texto para colar AWBs
        self._uncleared_input_text = ctk.CTkTextbox(
            left_frame, corner_radius=6,
            font=ctk.CTkFont(family="Consolas", size=11),
            wrap="none")
        self._uncleared_input_text.pack(fill="both", expand=True, padx=12, pady=(0, 8))

        # Botoes de acao
        left_btns = ctk.CTkFrame(left_frame, fg_color="transparent")
        left_btns.pack(fill="x", padx=12, pady=(0, 12))

        ctk.CTkButton(left_btns, text="LIMPAR E DEDUPLICAR",
                      command=self._uncleared_processar,
                      width=180, height=34,
                      fg_color="#22c55e", hover_color="#16a34a",
                      text_color="#000000",
                      font=ctk.CTkFont(size=12, weight="bold")).pack(side="left")

        ctk.CTkButton(left_btns, text="Limpar campo",
                      command=self._uncleared_limpar_input,
                      width=100, height=34,
                      fg_color="transparent", border_width=1,
                      text_color="#ef4444",
                      font=ctk.CTkFont(size=11)).pack(side="right")

        # Painel direito: Resultado + Comparacao
        right_frame = ctk.CTkFrame(main_frame, corner_radius=8)
        right_frame.pack(side="right", fill="both", expand=True, padx=(4, 0))

        # Header direito
        right_header = ctk.CTkFrame(right_frame, fg_color="transparent")
        right_header.pack(fill="x", padx=12, pady=(12, 6))

        ctk.CTkLabel(right_header, text="Resultado",
                     font=ctk.CTkFont(size=13, weight="bold")).pack(side="left")

        self._uncleared_lbl_count_result = ctk.CTkLabel(
            right_header, text="",
            font=ctk.CTkFont(size=11), text_color="#22c55e")
        self._uncleared_lbl_count_result.pack(side="right")

        # Caixa de texto resultado (somente leitura visual)
        self._uncleared_result_text = ctk.CTkTextbox(
            right_frame, corner_radius=6,
            font=ctk.CTkFont(family="Consolas", size=11),
            wrap="none")
        self._uncleared_result_text.pack(fill="both", expand=True, padx=12, pady=(0, 8))

        # --- Secao de Comparacao ---
        compare_frame = ctk.CTkFrame(right_frame, corner_radius=6)
        compare_frame.pack(fill="x", padx=12, pady=(0, 12))

        compare_inner = ctk.CTkFrame(compare_frame, fg_color="transparent")
        compare_inner.pack(fill="x", padx=12, pady=10)

        ctk.CTkLabel(compare_inner, text="Comparar com planilha:",
                     font=ctk.CTkFont(size=11),
                     text_color="#9ca3af").pack(side="left")

        ctk.CTkButton(compare_inner, text="Importar .xlsx/.csv",
                      command=self._uncleared_importar_planilha,
                      width=140, height=28,
                      font=ctk.CTkFont(size=11)).pack(side="left", padx=(10, 0))

        ctk.CTkButton(compare_inner, text="COMPARAR",
                      command=self._uncleared_comparar,
                      width=100, height=28,
                      fg_color="#3b82f6", hover_color="#2563eb",
                      font=ctk.CTkFont(size=11, weight="bold")).pack(side="left", padx=(10, 0))

        self._uncleared_lbl_planilha = ctk.CTkLabel(
            compare_inner, text="Nenhuma planilha",
            font=ctk.CTkFont(size=10), text_color="#6b7280")
        self._uncleared_lbl_planilha.pack(side="right")

        # Botoes de salvar (abaixo da comparacao)
        save_frame = ctk.CTkFrame(right_frame, fg_color="transparent")
        save_frame.pack(fill="x", padx=12, pady=(0, 12))

        ctk.CTkButton(save_frame, text="Copiar resultado",
                      command=self._uncleared_copiar_resultado,
                      width=120, height=28,
                      fg_color="transparent", border_width=1,
                      text_color="#60a5fa",
                      font=ctk.CTkFont(size=11)).pack(side="left")

        ctk.CTkButton(save_frame, text="Salvar .xlsx",
                      command=self._uncleared_salvar_xlsx,
                      width=100, height=28,
                      fg_color="transparent", border_width=1,
                      text_color="#60a5fa",
                      font=ctk.CTkFont(size=11)).pack(side="left", padx=(8, 0))

        ctk.CTkButton(save_frame, text="Salvar comparacao",
                      command=self._uncleared_salvar_comparacao,
                      width=130, height=28,
                      fg_color="transparent", border_width=1,
                      text_color="#60a5fa",
                      font=ctk.CTkFont(size=11)).pack(side="left", padx=(8, 0))

    def _uncleared_processar(self):
        """Processa AWBs: limpa (11 digitos) e remove duplicados."""
        texto = self._uncleared_input_text.get("1.0", tk.END)

        if not texto.strip():
            messagebox.showwarning("Aviso", "Cole os AWBs no campo da esquerda.")
            return

        awbs_unicos, awbs_todos, duplicados = limpar_awbs(texto)

        if not awbs_unicos:
            messagebox.showwarning("Aviso",
                "Nenhum AWB valido encontrado.\n"
                "AWBs devem comecar com 127 e ter pelo menos 11 digitos.")
            return

        self._uncleared_awbs_limpos = awbs_unicos

        # Atualiza contadores
        self._uncleared_lbl_count_input.configure(
            text=f"{len(awbs_todos)} AWBs lidos")
        self._uncleared_lbl_count_result.configure(
            text=f"{len(awbs_unicos)} unicos ({duplicados} duplicados removidos)")

        # Mostra resultado
        self._uncleared_result_text.delete("1.0", tk.END)
        self._uncleared_result_text.insert("1.0", "\n".join(awbs_unicos))

        self._log(f"Uncleared: {len(awbs_todos)} -> {len(awbs_unicos)} AWBs "
                  f"({duplicados} duplicados removidos)")

    def _uncleared_limpar_input(self):
        """Limpa o campo de input."""
        self._uncleared_input_text.delete("1.0", tk.END)
        self._uncleared_result_text.delete("1.0", tk.END)
        self._uncleared_awbs_limpos = []
        self._uncleared_lbl_count_input.configure(text="0 AWBs")
        self._uncleared_lbl_count_result.configure(text="")

    def _uncleared_importar_planilha(self):
        """Abre dialogo para importar planilha do sistema."""
        from tkinter import filedialog

        caminho = filedialog.askopenfilename(
            title="Selecionar planilha de AWBs",
            filetypes=[
                ("Planilhas", "*.xlsx *.xls *.csv"),
                ("Excel", "*.xlsx *.xls"),
                ("CSV", "*.csv"),
            ]
        )

        if not caminho:
            return

        try:
            awbs_planilha, df_filtrado = carregar_planilha_sistema(
                caminho, base_posse="MCZ", retira_entrega="RETIRA")

            self._uncleared_awbs_planilha = awbs_planilha
            self._uncleared_awbs_info = df_filtrado.attrs.get("awbs_info", {})
            self._uncleared_arquivo_planilha = caminho

            nome_arquivo = os.path.basename(caminho)
            self._uncleared_lbl_planilha.configure(
                text=f"{nome_arquivo} ({len(awbs_planilha)} AWBs)",
                text_color="#22c55e")

            self._log(f"Uncleared: Planilha carregada - {nome_arquivo} "
                      f"({len(awbs_planilha)} AWBs MCZ/RETIRA)")

        except Exception as e:
            messagebox.showerror("Erro", f"Erro ao carregar planilha:\n{str(e)}")
            self._log(f"Uncleared: ERRO ao carregar planilha - {e}")

    def _uncleared_comparar(self):
        """Compara AWBs limpos com a planilha importada."""
        if not self._uncleared_awbs_limpos:
            messagebox.showwarning("Aviso",
                "Primeiro limpe os AWBs (botao 'Limpar e deduplicar').")
            return

        if not self._uncleared_awbs_planilha:
            messagebox.showwarning("Aviso",
                "Importe a planilha do sistema primeiro.")
            return

        resultado = comparar_awbs(self._uncleared_awbs_limpos, self._uncleared_awbs_planilha,
                                  self._uncleared_awbs_info)
        self._uncleared_resultado_comparacao = resultado

        # Mostra resultado na caixa de texto
        self._uncleared_result_text.delete("1.0", tk.END)

        linhas = []
        linhas.append(f"=== COMPARACAO ===")
        linhas.append(f"AWBs pistolados (terminal): {resultado['total_meus']}")
        linhas.append(f"Planilha uncleared (MCZ/RETIRA): {resultado['total_planilha']}")
        linhas.append(f"")

        # --- PRA BAIXAR / INVESTIGAR ---
        linhas.append(f"--- PRA BAIXAR / INVESTIGAR ({resultado['total_sobrando']}) ---")
        linhas.append(f"    (estao no uncleared mas NAO estao no terminal)")
        linhas.append(f"")
        for item in resultado["sobrando_detalhado"]:
            frap_tag = " [FRAP]" if item["frap"] else ""
            status_tag = f"  |  {item['status']}" if item["status"] else ""
            linhas.append(f"  {item['awb']}{frap_tag}{status_tag}")

        linhas.append(f"")

        # --- NO TERMINAL (nao baixar ainda) ---
        linhas.append(f"--- NO TERMINAL - NAO BAIXAR ({resultado['total_presentes']}) ---")
        linhas.append(f"    (pistolados, ainda nao foram entregues)")
        linhas.append(f"")
        for item in resultado["presentes_detalhado"]:
            frap_tag = " [FRAP]" if item["frap"] else ""
            status_tag = f"  |  {item['status']}" if item["status"] else ""
            linhas.append(f"  {item['awb']}{frap_tag}{status_tag}")

        self._uncleared_result_text.insert("1.0", "\n".join(linhas))

        # Atualiza label
        frap_info = f" | {resultado['total_frap']} FRAP" if resultado['total_frap'] > 0 else ""
        self._uncleared_lbl_count_result.configure(
            text=f"{resultado['total_sobrando']} pra baixar{frap_info} | "
                 f"{resultado['total_presentes']} no terminal")

        self._log(f"Uncleared: {resultado['total_sobrando']} pra baixar "
                  f"({resultado['total_frap']} FRAP), "
                  f"{resultado['total_presentes']} no terminal")

    def _uncleared_copiar_resultado(self):
        """Copia o conteudo do resultado para a area de transferencia."""
        texto = self._uncleared_result_text.get("1.0", tk.END).strip()
        if not texto:
            messagebox.showinfo("Info", "Nada para copiar. Processe os AWBs primeiro.")
            return

        self.janela.clipboard_clear()
        self.janela.clipboard_append(texto)
        self._log("Uncleared: Resultado copiado para area de transferencia")

    def _uncleared_salvar_xlsx(self):
        """Salva lista de AWBs limpos em .xlsx."""
        from tkinter import filedialog

        if not self._uncleared_awbs_limpos:
            messagebox.showwarning("Aviso", "Nenhum AWB processado para salvar.")
            return

        caminho = filedialog.asksaveasfilename(
            title="Salvar AWBs limpos",
            defaultextension=".xlsx",
            filetypes=[("Excel", "*.xlsx"), ("CSV", "*.csv"), ("Texto", "*.txt")],
            initialfile=f"awbs_limpos_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
        )

        if not caminho:
            return

        try:
            ext = os.path.splitext(caminho)[1].lower()
            if ext == ".csv":
                salvar_resultado(self._uncleared_awbs_limpos, caminho, formato="csv")
            elif ext == ".txt":
                salvar_resultado(self._uncleared_awbs_limpos, caminho, formato="txt")
            else:
                salvar_resultado(self._uncleared_awbs_limpos, caminho, formato="xlsx")

            self._log(f"Uncleared: AWBs salvos em {os.path.basename(caminho)}")
            messagebox.showinfo("Salvo", f"AWBs salvos em:\n{caminho}")

        except Exception as e:
            messagebox.showerror("Erro", f"Erro ao salvar:\n{str(e)}")

    def _uncleared_salvar_comparacao(self):
        """Salva resultado da comparacao em .xlsx (duas abas: Presentes e Ausentes)."""
        from tkinter import filedialog

        if not self._uncleared_resultado_comparacao:
            messagebox.showwarning("Aviso", "Faca a comparacao primeiro.")
            return

        caminho = filedialog.asksaveasfilename(
            title="Salvar comparacao",
            defaultextension=".xlsx",
            filetypes=[("Excel", "*.xlsx")],
            initialfile=f"comparacao_awbs_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
        )

        if not caminho:
            return

        try:
            salvar_comparacao(self._uncleared_resultado_comparacao, caminho)
            self._log(f"Uncleared: Comparacao salva em {os.path.basename(caminho)}")
            messagebox.showinfo("Salvo", f"Comparacao salva em:\n{caminho}")

        except Exception as e:
            messagebox.showerror("Erro", f"Erro ao salvar:\n{str(e)}")

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

        self._tempo_inicio_processamento = time.time()

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
                        indice_progresso=i,
                        awbs_retidos_sessao=awbs_com_termo_sessao,
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

                        # Registra padrao do voo na memoria (aprendizado)
                        try:
                            from modules.ia_memoria import memoria
                            etapas = voo.etapas.split("/") if "/" in voo.etapas else [voo.etapas, "MCZ"]
                            memoria.registrar_padrao_voo(
                                voo=voo.numero_controle,
                                origem=etapas[0].strip() if etapas else "",
                                destino=etapas[-1].strip() if etapas else "MCZ",
                                tem_termos=len(resultado.awbs_retidos) > 0,
                                total_termos=len(resultado.awbs_retidos),
                            )
                        except Exception:
                            pass

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

                    # IA tenta diagnosticar o erro (aprendizado)
                    try:
                        from modules.ia_local import ia_local
                        from modules.ia_memoria import memoria
                        diagnostico = ia_local.diagnosticar_erro(
                            contexto=f"processar voo {voo.numero_controle} ({voo.etapas})",
                            erro=str(e)[:300],
                        )
                        if diagnostico.get("acao") != "nenhuma":
                            self._log(f"  IA diagnosticou: {diagnostico.get('explicacao', '')}")
                            self._log(f"  Acao sugerida: {diagnostico.get('acao', '')}")
                        memoria.registrar_erro(
                            contexto=f"processar voo {voo.numero_controle}",
                            erro=str(e)[:300],
                            solucao=diagnostico.get("explicacao", ""),
                            resolvido=False,
                        )
                    except Exception:
                        pass

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

            # ========= RESUMO INTELIGENTE VIA IA =========
            try:
                from modules.ia_local import ia_local
                from modules.ia_memoria import memoria

                tempo_total = int(time.time() - self._tempo_inicio_processamento) if hasattr(self, '_tempo_inicio_processamento') else 0

                dados_execucao = {
                    "total_voos": len(voos),
                    "voos_sucesso": voos_sucesso,
                    "voos_erro": voos_erro,
                    "total_liberados": total_liberados,
                    "total_retidos": total_retidos,
                    "total_erros": voos_erro,
                    "alertas": [],
                    "tempo_total_segundos": tempo_total,
                }

                resumo = ia_local.gerar_resumo_execucao(dados_execucao)
                self._log("")
                self._log("--- RESUMO ---")
                self._log(resumo)
                self._log("")

                # Registra execucao na memoria
                memoria.registrar_execucao(dados_execucao)
                memoria.limpar_antigos()

            except Exception as e:
                logger.debug(f"Erro ao gerar resumo IA: {e}")

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
        consulta o status no NEXLOG (busca rapida) e confirma que esta realmente retido.
        
        NAO consulta a SEFAZ aqui — so verifica o status operacional no Nexlog.
        Se algum AWB com termo NAO esta retido no Nexlog, gera ALERTA.
        
        Args:
            browser: NexlogBrowser ja logado
            awbs_com_termo: dict {awb: voo} de AWBs que deveriam estar retidos
        """
        self._log("")
        self._log("=" * 60)
        self._log("VERIFICACAO FINAL: Conferindo AWBs com termo no Nexlog...")
        self._log("=" * 60)

        alertas = []

        from modules.consulta_cliente import ConsultaCliente
        consulta = ConsultaCliente(browser)

        for awb, voo_origem in awbs_com_termo.items():
            try:
                # Consulta APENAS o status no Nexlog (sem consultar TADe na SEFAZ)
                resultado = consulta.consultar_awb_status_apenas(awb)
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

    def _processar_um_voo(self, voo: Voo, browser, voos_mod, cte_mod, liberar_mod, outlook, sefaz, indice_progresso: int = -1, awbs_retidos_sessao: Dict[str, str] = None) -> ResultadoProcessamento:
        """Processa um unico voo completo com atualizacao de progresso."""
        resultado = ResultadoProcessamento(voo=voo)
        if awbs_retidos_sessao is None:
            awbs_retidos_sessao = {}

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

            # SEGURANCA 1: Valida que o manifesto e do voo correto
            # (o Nexlog pode abrir o modal errado — ja aconteceu no log)
            if manifesto.numero_voo:
                voo_manifesto = manifesto.numero_voo.replace(" ", "").upper()
                voo_esperado = voo.numero_controle.replace(" ", "").upper()
                if voo_manifesto != voo_esperado:
                    self._log(f"  ERRO SEGURANCA: Manifesto e do voo {manifesto.numero_voo} "
                             f"mas esperava {voo.numero_controle}!")
                    self._log(f"  VOO BLOQUEADO - manifesto incorreto")
                    resultado.erros.append(
                        f"Manifesto de outro voo ({manifesto.numero_voo}) - nao processado")
                    return resultado

            # SEGURANCA 2: Manifesto vazio = parser falhou ou PDF corrompido
            total_awbs = len(manifesto.awbs_retira) + len(manifesto.awbs_entrega)
            if total_awbs == 0:
                self._log(f"  ERRO SEGURANCA: Manifesto sem nenhum AWB (parser falhou?)")
                self._log(f"  VOO BLOQUEADO - verificar manifesto manualmente")
                resultado.erros.append("Manifesto sem AWBs - parser falhou, verificar manualmente")
                return resultado
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

            # Registra classificacao na memoria (aprendizado)
            try:
                from modules.ia_memoria import memoria
                memoria.registrar_classificacao(
                    chave_mdfe=chave,
                    voo=voo.numero_controle,
                    resultado_regex="",  # Nao temos acesso aqui ao intermediario
                    resultado_ia="",
                    resultado_final=resposta.value,
                )
            except Exception:
                pass

            if resposta == RespostaEmail.SEM_TERMOS:
                # Email confirma sem termos — confiavel
                self._log("  Email diz SEM termos - voo liberado")
                usou_outlook = True
            elif resposta == RespostaEmail.COM_TERMOS:
                # Tenta baixar PDF do email
                caminho_pdf = outlook.baixar_anexo_pdf()
                if caminho_pdf:
                    consulta = parsear_relatorio_pdf(caminho_pdf)
                    if consulta is None:
                        # PDF corrompido/ilegivel (ex: Unexpected EOF)
                        # NAO pode tratar como "0 termos" — fallback pro site SEFAZ
                        self._log("  AVISO: PDF do email corrompido/ilegivel - consultando site SEFAZ")
                        consulta = None
                    elif consulta.chave and chave and not _chaves_compativeis(consulta.chave, chave):
                        # Valida que o PDF e do voo correto
                        self._log(
                            f"  AVISO: PDF e de outro voo - descartando "
                            f"(PDF: {consulta.chave[:20]}... vs Nexlog: {chave[:20]}...)"
                        )
                        consulta = None
                    elif consulta.total_termos > 0:
                        self._log(f"  Relatorio do email: {consulta.total_termos} termos")
                        usou_outlook = True
                    else:
                        # PDF lido com sucesso E confirma 0 termos.
                        # SEGURANCA: so confia se a chave do PDF bate com a do voo
                        # Se o PDF nao tem chave extraivel, pode ser de outro voo
                        if consulta.chave and chave and _chaves_compativeis(consulta.chave, chave):
                            self._log("  PDF do email confirma 0 termos (chave validada) - voo LIBERADO")
                            resposta = RespostaEmail.SEM_TERMOS
                            usou_outlook = True
                        elif not consulta.chave:
                            # PDF sem chave legivel - nao pode confirmar que e deste voo
                            self._log(
                                "  AVISO: PDF diz 0 termos mas sem chave para validar. "
                                "Consultando site SEFAZ por seguranca."
                            )
                            consulta = None
                        else:
                            # Chave do PDF diferente da chave do voo
                            self._log(f"  AVISO: PDF diz 0 termos mas chave diferente - descartando")
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
                    # SEGURANCA: Valida que o resultado e da chave correta
                    # O site pode retornar dados residuais de consulta anterior (Angular SPA)
                    if consulta.chave and chave and not _chaves_compativeis(consulta.chave, chave):
                        self._log(
                            f"  SEGURANCA: Resultado SEFAZ e de outra chave! "
                            f"Esperada: {chave[:20]}... Recebida: {consulta.chave[:20]}..."
                        )
                        self._log("  NAO libera este voo - resultado nao confiavel")
                        resultado.erros.append("SEFAZ retornou chave diferente - consultar manualmente")
                        sefaz.voltar_para_nexlog()
                        return resultado

                    # SEGURANCA: Bloqueia APENAS se relatorio NAO foi extraido
                    # (status desconhecido + 0 termos + lista vazia)
                    # Se o parser retornou status real (liberado, sem pendencias, etc)
                    # com 0 termos, e CONFIAVEL - pode liberar.
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
                # Gera comentario inteligente (com situacao e mais contexto)
                try:
                    from modules.ia_local import ia_local
                    termos_cte = consulta.termos_por_cte(cte)
                    comentario = ia_local.gerar_comentario_inteligente(termos_cte, "")
                    if not comentario:
                        comentario = consulta.comentario_para_cte(cte)
                except Exception:
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

            # SEGURANCA: Double check - garante que nenhum AWB de ENTREGA
            # entrou na lista de liberacao (pode acontecer se o parser
            # do manifesto classificou errado a secao)
            awbs_domicilio_na_liberacao = awbs_para_liberar & manifesto.awbs_entrega
            if awbs_domicilio_na_liberacao:
                self._log(f"  SEGURANCA: {len(awbs_domicilio_na_liberacao)} AWB(s) de ENTREGA "
                         f"detectado(s) na lista de liberacao - REMOVENDO!")
                for awb in awbs_domicilio_na_liberacao:
                    self._log(f"    AWB {awb}: ENTREGA (domicilio) - removido da liberacao")
                awbs_para_liberar -= awbs_domicilio_na_liberacao

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

            # FILTRO SESSAO: Remove AWBs que foram retidos em voos anteriores desta sessao
            # Cenario: AWB aparece no manifesto de 2 voos (trânsito/conexão).
            # Voo A detecta termo e retém. Voo B (sem termos) tenta liberar o mesmo AWB.
            # Sem este filtro, o Voo B liberaria indevidamente.
            if awbs_para_liberar and awbs_retidos_sessao:
                awbs_retidos_em_outros_voos = awbs_para_liberar & set(awbs_retidos_sessao.keys())
                if awbs_retidos_em_outros_voos:
                    for awb in awbs_retidos_em_outros_voos:
                        voo_origem = awbs_retidos_sessao[awb]
                        self._log(
                            f"    AWB {awb}: RETIDO no voo {voo_origem} (sessao anterior) - NAO libera"
                        )
                    awbs_para_liberar -= awbs_retidos_em_outros_voos
                    self._log(
                        f"  {len(awbs_retidos_em_outros_voos)} AWB(s) bloqueado(s) "
                        f"(retidos em outros voos desta sessao)"
                    )

        else:
            self._log("  AVISO: Sem manifesto - nao pode liberar")

        if awbs_para_liberar:
            self._log(f"  Liberando {len(awbs_para_liberar)} AWBs...")
            res_lib = liberar_mod.liberar_awbs(awbs_para_liberar, data_ini, data_fim)
            resultado.awbs_liberados = res_lib.get("selecionados", [])
            resultado.awbs_ja_liberados = res_lib.get("ja_liberados", [])
        else:
            self._log("  Nenhum AWB para liberar")

        # --- ENVIO GOOGLE FORMS: AWBs domicilio com termo ---
        # Se tem AWBs de ENTREGA (domicilio) que tambem tem termo,
        # envia pro formulario de controle
        if manifesto and awbs_com_termo:
            domicilio_com_termo = set(resultado.awbs_domicilio) & awbs_com_termo
            if domicilio_com_termo:
                self._log(f"  Enviando {len(domicilio_com_termo)} AWB(s) domicilio com termo pro Forms...")
                for awb in domicilio_com_termo:
                    sucesso = _enviar_awb_google_forms(awb)
                    if sucesso:
                        self._log(f"    AWB {awb}: enviado ao Forms")
                    else:
                        self._log(f"    AWB {awb}: FALHA ao enviar ao Forms")

                # --- ENVIO EMAIL COM TA+DAR: AWBs domicilio com termo ---
                # Para cada AWB domicilio com termo, baixa TA/DAR e envia email pra base
                if self.email_domicilio_ativo.get():
                    self._log(f"  Enviando email com TA+DAR para {len(domicilio_com_termo)} AWB(s)...")
                    for awb in domicilio_com_termo:
                        try:
                            # Monta lista de termos associados a este AWB
                            # Busca no mapa_cte_awb (invertido) quais CTes pertencem a este AWB
                            termos_do_awb = []
                            if consulta and consulta.termos:
                                for cte, awb_mapeado in mapa_cte_awb.items():
                                    if awb_mapeado == awb:
                                        termos_cte = consulta.termos_por_cte(cte)
                                        termos_do_awb.extend(termos_cte)

                            # Remove termos duplicados (mesmo numero)
                            termos_unicos = {t.numero: t for t in termos_do_awb}.values()
                            termos_do_awb = list(termos_unicos)

                            if termos_do_awb:
                                _processar_domicilio_com_termo(
                                    awb=awb,
                                    termos_awb=termos_do_awb,
                                    browser=browser,
                                    outlook=outlook,
                                    log_func=self._log,
                                )
                            else:
                                self._log(f"    AWB {awb}: sem termos mapeados - email nao enviado")
                        except Exception as e:
                            self._log(f"    AWB {awb}: erro ao processar email: {str(e)[:80]}")

        return resultado

    def executar(self):
        """Inicia a interface."""
        self.janela.mainloop()


# ========= ENTRY POINT =========
if __name__ == "__main__":
    app = AppAutomacao()
    app.executar()
