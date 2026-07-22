"""
Configuracao centralizada do projeto.
Credenciais sao salvas em arquivo local (fora do git).
"""

import os
import json
from pathlib import Path
from dataclasses import dataclass, field

# Pasta de dados do usuario
APPDATA = os.getenv("APPDATA", os.path.expanduser("~"))
PASTA_CONFIG = Path(APPDATA) / "automacao_sefaz"
PASTA_CONFIG.mkdir(parents=True, exist_ok=True)

ARQUIVO_CREDENCIAIS = PASTA_CONFIG / "credenciais.json"

# Pasta de downloads para PDFs (manifesto + relatorio SEFAZ)
PASTA_DOWNLOADS = Path(os.path.expanduser("~")) / "Downloads" / "automacao_sefaz"
PASTA_DOWNLOADS.mkdir(parents=True, exist_ok=True)


@dataclass
class CredenciaisNexlog:
    usuario: str = ""
    senha: str = ""
    base: str = "MCZ"


@dataclass
class CredenciaisSefaz:
    usuario: str = ""
    senha: str = ""


@dataclass
class ConfigTelegram:
    bot_token: str = ""        # Token do BotFather (@BotFather no Telegram)
    chat_id: str = ""          # ID do chat/grupo para notificacoes
    ativo: bool = False        # Liga/desliga notificacoes


@dataclass
class Config:
    nexlog: CredenciaisNexlog = field(default_factory=CredenciaisNexlog)
    sefaz: CredenciaisSefaz = field(default_factory=CredenciaisSefaz)
    telegram: ConfigTelegram = field(default_factory=ConfigTelegram)

    # URLs
    url_nexlog: str = "https://golcargo.nexlog.com/account/#/login"
    url_sefaz: str = "https://transportadoras.sefaz.al.gov.br/#/"
    url_outlook: str = "https://outlook.cloud.microsoft/mail/mczfk@voegol.com.br/"

    # Timeouts (segundos)
    timeout_padrao: int = 20
    timeout_curto: int = 5
    timeout_longo: int = 60

    # Pasta de downloads
    pasta_downloads: str = str(PASTA_DOWNLOADS)

    def salvar(self):
        """Salva credenciais em arquivo local."""
        dados = {
            "nexlog": {
                "usuario": self.nexlog.usuario,
                "senha": self.nexlog.senha,
                "base": self.nexlog.base,
            },
            "sefaz": {
                "usuario": self.sefaz.usuario,
                "senha": self.sefaz.senha,
            },
            "telegram": {
                "bot_token": self.telegram.bot_token,
                "chat_id": self.telegram.chat_id,
                "ativo": self.telegram.ativo,
            },
            "timeout_padrao": self.timeout_padrao,
        }
        with open(ARQUIVO_CREDENCIAIS, "w", encoding="utf-8") as f:
            json.dump(dados, f, indent=2, ensure_ascii=False)

    def carregar(self) -> bool:
        """Carrega credenciais do arquivo local."""
        if not ARQUIVO_CREDENCIAIS.exists():
            return False
        try:
            with open(ARQUIVO_CREDENCIAIS, "r", encoding="utf-8") as f:
                dados = json.load(f)
            self.nexlog = CredenciaisNexlog(**dados.get("nexlog", {}))
            self.sefaz = CredenciaisSefaz(**dados.get("sefaz", {}))
            tg = dados.get("telegram", {})
            if tg:
                self.telegram = ConfigTelegram(**tg)
            self.timeout_padrao = dados.get("timeout_padrao", 20)
            return True
        except (json.JSONDecodeError, TypeError):
            return False


# Instancia global
config = Config()
config.carregar()
