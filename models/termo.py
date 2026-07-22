"""
Modelos de dados para a automacao de retirada de voos SEFAZ.
Estruturas para representar voos, termos, CTes e AWBs.
"""

from dataclasses import dataclass, field
from typing import Optional, List, Set
from enum import Enum
from datetime import datetime


class TipoFielDepositario(Enum):
    TRANSPORTADORA = "transportadora"
    DESTINATARIO = "destinatario"


class SituacaoTermo(Enum):
    PENDENTE = "pendente"
    REGULARIZADO = "regularizado"
    DESCONHECIDO = "desconhecido"


class StatusMDFe(Enum):
    ANALISADO_COM_PENDENCIAS = "analisado_com_pendencias"
    ANALISADO_SEM_PENDENCIAS = "analisado_sem_pendencias"
    EM_ANALISE = "em_analise"
    SEM_ANALISE = "sem_analise"  # Site SEFAZ nao encontrou analise
    DESCONHECIDO = "desconhecido"


class TipoEntrega(Enum):
    RETIRA = "retira"           # Teca / Aeroporto
    DOMICILIO = "domicilio"     # Entrega Domicilio
    DESCONHECIDO = "desconhecido"


class StatusRetencao(Enum):
    RETIDA = "retida"
    LIBERADA = "liberada"
    PARCIALMENTE_LIBERADA = "parcialmente_liberada"
    NAO_ENCONTRADA = "nao_encontrada"  # Nao aparece na busca (ja liberado/retirado)


class RespostaEmail(Enum):
    COM_TERMOS = "com_termos"           # "gerado o relatorio anexo"
    SEM_TERMOS = "sem_termos"           # "liberado" / "sem retencao"
    NAO_RESPONDEU = "nao_respondeu"     # Nao encontrou resposta
    INDEFINIDO = "indefinido"           # Nao conseguiu identificar


@dataclass
class Voo:
    """Representa um voo na lista de rotas do Nexlog."""
    numero_controle: str        # Ex: "G3 1536"
    etapas: str                 # Ex: "CGH/MCZ"
    data_chegada: str           # Ex: "06/06/2026 10:10:00"
    assinado: str               # Ex: "58 vol(s), 289,023 kg"
    status_recebimento: str     # Ex: "Fechado"
    indice_tabela: int = 0     # Posicao na tabela (para clicar)

    @property
    def hora_chegada(self) -> str:
        """Extrai apenas a hora da data de chegada."""
        try:
            parts = self.data_chegada.split(" ")
            return parts[1] if len(parts) > 1 else ""
        except Exception:
            return ""


@dataclass
class TermoApreensao:
    """Representa um Termo de Apreensao/Averiguacao da SEFAZ."""
    numero: str
    situacao: SituacaoTermo = SituacaoTermo.DESCONHECIDO
    data_emissao: str = ""
    fiel_depositario_nome: str = ""
    fiel_depositario_cnpj: str = ""
    tipo_fiel: TipoFielDepositario = TipoFielDepositario.TRANSPORTADORA
    nfe: str = ""
    cte: str = ""
    awb: Optional[str] = None  # Preenchido apos consulta no Nexlog

    @property
    def deve_reter(self) -> bool:
        """Todos com termo ficam retidos (regra de negocio)."""
        return True

    @property
    def comentario_nexlog(self) -> str:
        """Gera o texto do comentario para o Nexlog."""
        return f"RETIDO PELA SEFAZ TA {self.numero}"


@dataclass
class ConsultaMDFe:
    """Resultado de uma consulta de MDF-e no site da SEFAZ ou PDF do email."""
    chave: str = ""
    numero_mdfe: str = ""
    data_emissao: str = ""
    emitente: str = ""
    cnpj_emitente: str = ""
    status: StatusMDFe = StatusMDFe.DESCONHECIDO
    data_fim: str = ""
    posto: str = ""
    total_termos: int = 0
    termos: List[TermoApreensao] = field(default_factory=list)

    @property
    def ctes_retidos(self) -> List[str]:
        """Retorna lista de CTes que possuem termos (retidos)."""
        return list(set(t.cte for t in self.termos if t.cte))

    @property
    def tem_pendencias(self) -> bool:
        return self.total_termos > 0

    def termos_por_cte(self, cte: str) -> List[TermoApreensao]:
        """Retorna todos os termos de um CTe especifico."""
        return [t for t in self.termos if t.cte == cte]

    def comentario_para_cte(self, cte: str) -> str:
        """
        Gera o comentario completo para um CTe.
        Se o CTe tem multiplos termos, junta todos:
        "RETIDO PELA SEFAZ TA 2410845, TA 2410848, TA 2410851"
        """
        termos_cte = self.termos_por_cte(cte)
        if not termos_cte:
            return ""
        if len(termos_cte) == 1:
            return termos_cte[0].comentario_nexlog
        # Multiplos termos
        numeros = [f"TA {t.numero}" for t in termos_cte]
        return "RETIDO PELA SEFAZ " + ", ".join(numeros)


@dataclass
class ManifestoVoo:
    """Dados extraidos do manifesto PDF do voo."""
    numero_manifesto: str = ""
    numero_voo: str = ""
    data_voo: str = ""
    origem: str = ""
    destino: str = ""
    awbs_retira: Set[str] = field(default_factory=set)
    awbs_entrega: Set[str] = field(default_factory=set)

    @property
    def todos_awbs(self) -> Set[str]:
        return self.awbs_retira | self.awbs_entrega

    @property
    def total_awbs(self) -> int:
        return len(self.todos_awbs)


@dataclass
class DadosVoo:
    """Consolida todos os dados de um voo para processamento."""
    voo: Voo
    chave_mdfe: str = ""
    manifesto: Optional[ManifestoVoo] = None
    resposta_email: RespostaEmail = RespostaEmail.NAO_RESPONDEU
    consulta_sefaz: Optional[ConsultaMDFe] = None

    # AWBs classificados apos processamento
    awbs_com_termo: Set[str] = field(default_factory=set)
    awbs_para_liberar: Set[str] = field(default_factory=set)
    awbs_domicilio: Set[str] = field(default_factory=set)

    # Mapeamento CTe -> AWB (preenchido durante processamento)
    mapa_cte_awb: dict = field(default_factory=dict)

    def calcular_liberacoes(self):
        """
        Calcula quais AWBs devem ser liberados.
        Regras:
        - So libera RETIRA (nunca domicilio)
        - Nao libera os que tem termo
        - Se email diz 'sem termos' -> libera todos RETIRA
        """
        if self.manifesto is None:
            return

        self.awbs_domicilio = self.manifesto.awbs_entrega.copy()

        if self.resposta_email == RespostaEmail.SEM_TERMOS:
            # Libera todos os RETIRA
            self.awbs_para_liberar = self.manifesto.awbs_retira.copy()
            self.awbs_com_termo = set()
        else:
            # Identifica AWBs com termo
            if self.consulta_sefaz:
                for termo in self.consulta_sefaz.termos:
                    if termo.awb:
                        self.awbs_com_termo.add(termo.awb)

            # Libera apenas RETIRA sem termo
            self.awbs_para_liberar = (
                self.manifesto.awbs_retira - self.awbs_com_termo
            )


@dataclass
class ResultadoProcessamento:
    """Resultado final do processamento de um voo."""
    voo: Voo
    comentarios_adicionados: int = 0
    comentarios_falha: int = 0
    awbs_liberados: List[str] = field(default_factory=list)
    awbs_ja_liberados: List[str] = field(default_factory=list)  # Nao encontrados/ja liberados
    awbs_retidos: List[str] = field(default_factory=list)
    awbs_domicilio: List[str] = field(default_factory=list)
    erros: List[str] = field(default_factory=list)

    @property
    def sucesso(self) -> bool:
        return len(self.erros) == 0

    def resumo(self) -> str:
        return (
            f"Voo {self.voo.numero_controle}:\n"
            f"  Comentarios: {self.comentarios_adicionados} OK / {self.comentarios_falha} falhas\n"
            f"  Liberados: {len(self.awbs_liberados)}\n"
            f"  Ja liberados (pulados): {len(self.awbs_ja_liberados)}\n"
            f"  Retidos (com termo): {len(self.awbs_retidos)}\n"
            f"  Domicilio (nao libera): {len(self.awbs_domicilio)}\n"
            f"  Erros: {len(self.erros)}"
        )
