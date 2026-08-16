"""
Sistema de memoria persistente para a IA local.
Registra erros, acertos, padroes e decisoes para que a IA
"aprenda" ao longo do tempo sem precisar de fine-tuning.

Funciona como um RAG simplificado:
- Salva eventos em arquivo JSON (por categoria)
- Na proxima execucao, injeta contexto relevante nos prompts da IA
- Permite detectar padroes recorrentes (ex: "voos de GRU sempre tem termos")

Armazenamento: %APPDATA%/automacao_sefaz/memoria_ia.json
"""

import json
import logging
import os
import threading
import time
from datetime import datetime, timedelta
from typing import Dict, List, Optional
from pathlib import Path
from collections import Counter

logger = logging.getLogger(__name__)

# Caminho do arquivo de memoria
APPDATA = os.getenv("APPDATA", os.path.expanduser("~"))
PASTA_DADOS = Path(APPDATA) / "automacao_sefaz"
PASTA_DADOS.mkdir(parents=True, exist_ok=True)
ARQUIVO_MEMORIA = PASTA_DADOS / "memoria_ia.json"

# Limites para nao crescer infinitamente
MAX_REGISTROS_POR_CATEGORIA = 200
MAX_DIAS_RETENCAO = 90


class CategoriaMemoria:
    """Categorias de registros na memoria."""
    ERRO_RECUPERADO = "erro_recuperado"        # Erro + solucao que funcionou
    ERRO_NAO_RECUPERADO = "erro_nao_recuperado" # Erro sem solucao
    CLASSIFICACAO_EMAIL = "classificacao_email"  # Resultado de classificacao de email
    EXTRACAO_TERMOS = "extracao_termos"          # Resultado de extracao de PDF
    PADRAO_VOO = "padrao_voo"                    # Padrao identificado em voos
    DECISAO_IA = "decisao_ia"                    # Decisoes tomadas pela IA
    EXECUCAO = "execucao"                        # Resumo de execucoes


class MemoriaIA:
    """
    Gerencia a memoria persistente da IA.
    Registra eventos e fornece contexto relevante para prompts futuros.
    """

    def __init__(self, caminho: Path = ARQUIVO_MEMORIA):
        self.caminho = caminho
        self._dados: Dict[str, List[dict]] = {}
        # Lock para escrita thread-safe (programa usa threads:
        # processamento + Telegram + domicilio podem escrever ao mesmo tempo)
        self._lock = threading.Lock()
        self._carregar()

    # ============================================================
    # PERSISTENCIA
    # ============================================================

    def _carregar(self):
        """Carrega memoria do arquivo JSON."""
        if self.caminho.exists():
            try:
                with open(self.caminho, "r", encoding="utf-8") as f:
                    self._dados = json.load(f)
                logger.debug(f"Memoria carregada: {sum(len(v) for v in self._dados.values())} registros")
            except (json.JSONDecodeError, OSError) as e:
                logger.warning(f"Erro ao carregar memoria: {e} - iniciando vazia")
                self._dados = {}
        else:
            self._dados = {}

    def _salvar(self):
        """
        Salva memoria no arquivo JSON (thread-safe).
        Escreve em arquivo temporario e renomeia — evita corromper
        o arquivo se o programa fechar no meio da escrita.
        """
        with self._lock:
            try:
                temp = self.caminho.with_suffix(".tmp")
                with open(temp, "w", encoding="utf-8") as f:
                    json.dump(self._dados, f, indent=2, ensure_ascii=False)
                # Renomeia (operacao atomica no mesmo filesystem)
                temp.replace(self.caminho)
            except OSError as e:
                logger.error(f"Erro ao salvar memoria: {e}")

    # ============================================================
    # REGISTRO DE EVENTOS
    # ============================================================

    def registrar(self, categoria: str, dados: dict):
        """
        Registra um evento na memoria.

        Args:
            categoria: Uma das categorias de CategoriaMemoria
            dados: Dict com informacoes do evento (livre)
        """
        registro = {
            "timestamp": datetime.now().isoformat(),
            **dados,
        }

        # Modificacao da estrutura protegida por lock
        with self._lock:
            if categoria not in self._dados:
                self._dados[categoria] = []

            self._dados[categoria].append(registro)

            # Limita tamanho
            if len(self._dados[categoria]) > MAX_REGISTROS_POR_CATEGORIA:
                self._dados[categoria] = self._dados[categoria][-MAX_REGISTROS_POR_CATEGORIA:]

        self._salvar()

    def registrar_erro(self, contexto: str, erro: str, solucao: str = "", resolvido: bool = False):
        """
        Registra um erro e sua solucao (se encontrada).

        Args:
            contexto: O que estava fazendo (ex: "clicar botao Pesquisar na Retencao")
            erro: Mensagem de erro
            solucao: O que resolveu (ex: "fechar modal e tentar novamente")
            resolvido: Se o erro foi resolvido automaticamente
        """
        categoria = (
            CategoriaMemoria.ERRO_RECUPERADO if resolvido
            else CategoriaMemoria.ERRO_NAO_RECUPERADO
        )
        self.registrar(categoria, {
            "contexto": contexto,
            "erro": erro[:500],
            "solucao": solucao,
            "resolvido": resolvido,
        })

    def registrar_classificacao(self, chave_mdfe: str, voo: str, resultado_regex: str,
                                 resultado_ia: str, resultado_final: str, correto: Optional[bool] = None):
        """
        Registra resultado de uma classificacao de email.

        Args:
            chave_mdfe: Chave do MDF-e consultado
            voo: Numero do voo
            resultado_regex: O que o regex disse
            resultado_ia: O que a IA disse
            resultado_final: Decisao final
            correto: Se depois confirmou que estava correto (None = nao verificado)
        """
        self.registrar(CategoriaMemoria.CLASSIFICACAO_EMAIL, {
            "chave_mdfe": chave_mdfe[:20] if chave_mdfe else "",
            "voo": voo,
            "regex": resultado_regex,
            "ia": resultado_ia,
            "final": resultado_final,
            "correto": correto,
        })

    def registrar_extracao(self, voo: str, total_declarado: int, total_regex: int,
                            total_ia: int, total_final: int):
        """Registra resultado de extracao de termos."""
        self.registrar(CategoriaMemoria.EXTRACAO_TERMOS, {
            "voo": voo,
            "total_declarado": total_declarado,
            "total_regex": total_regex,
            "total_ia": total_ia,
            "total_final": total_final,
        })

    def registrar_padrao_voo(self, voo: str, origem: str, destino: str,
                              tem_termos: bool, total_termos: int):
        """Registra padrao de um voo (pra detectar rotas problematicas)."""
        self.registrar(CategoriaMemoria.PADRAO_VOO, {
            "voo": voo,
            "origem": origem,
            "destino": destino,
            "tem_termos": tem_termos,
            "total_termos": total_termos,
        })

    def registrar_execucao(self, resumo: dict):
        """Registra resumo de uma execucao completa."""
        self.registrar(CategoriaMemoria.EXECUCAO, resumo)

    # ============================================================
    # CONSULTA DE CONTEXTO (para injetar nos prompts)
    # ============================================================

    def buscar_erros_similares(self, contexto: str, limite: int = 5) -> List[dict]:
        """
        Busca erros similares ao contexto atual.
        Usa busca simples por palavras-chave (sem vector DB).

        Args:
            contexto: Descricao do contexto atual
            limite: Max de resultados

        Returns:
            Lista de registros de erros similares (mais recentes primeiro)
        """
        palavras = set(contexto.lower().split())
        resultados = []

        for categoria in [CategoriaMemoria.ERRO_RECUPERADO, CategoriaMemoria.ERRO_NAO_RECUPERADO]:
            registros = self._dados.get(categoria, [])
            for reg in reversed(registros):  # Mais recentes primeiro
                contexto_reg = reg.get("contexto", "").lower()
                palavras_reg = set(contexto_reg.split())
                # Score simples: interseção de palavras
                score = len(palavras & palavras_reg)
                if score >= 2:  # Pelo menos 2 palavras em comum
                    resultados.append({**reg, "_score": score})

        # Ordena por score (desc) e pega os N melhores
        resultados.sort(key=lambda x: x.get("_score", 0), reverse=True)
        return resultados[:limite]

    def buscar_solucoes_para_erro(self, contexto: str) -> List[str]:
        """
        Retorna solucoes que funcionaram para erros similares.

        Returns:
            Lista de solucoes (strings)
        """
        erros = self.buscar_erros_similares(contexto)
        solucoes = []
        for err in erros:
            if err.get("resolvido") and err.get("solucao"):
                solucoes.append(err["solucao"])
        return list(dict.fromkeys(solucoes))  # Remove duplicatas mantendo ordem

    def contexto_para_prompt(self, categoria: str, limite: int = 10) -> str:
        """
        Gera texto de contexto para injetar num prompt da IA.
        Retorna resumo dos ultimos N registros da categoria.

        Args:
            categoria: Categoria de memoria
            limite: Quantos registros incluir

        Returns:
            Texto formatado para incluir no prompt
        """
        registros = self._dados.get(categoria, [])
        if not registros:
            return ""

        ultimos = registros[-limite:]
        linhas = []

        for reg in ultimos:
            ts = reg.get("timestamp", "")[:10]  # Apenas data
            # Remove timestamp do dict pra nao repetir
            dados = {k: v for k, v in reg.items() if k != "timestamp" and k != "_score"}
            linhas.append(f"[{ts}] {json.dumps(dados, ensure_ascii=False)}")

        return "\n".join(linhas)

    # ============================================================
    # ANALISE DE PADROES
    # ============================================================

    def padroes_voos(self, dias: int = 30) -> dict:
        """
        Analisa padroes de voos dos ultimos N dias.
        Identifica rotas que frequentemente tem termos.

        Returns:
            Dict com padroes identificados:
            {
                "rotas_problematicas": [{"rota": "GRU/MCZ", "taxa_termos": 0.8, "total": 20}],
                "taxa_geral_termos": 0.35,
                "total_voos": 100,
            }
        """
        registros = self._dados.get(CategoriaMemoria.PADRAO_VOO, [])
        if not registros:
            return {"rotas_problematicas": [], "taxa_geral_termos": 0, "total_voos": 0}

        # Filtra por periodo
        data_corte = (datetime.now() - timedelta(days=dias)).isoformat()
        recentes = [r for r in registros if r.get("timestamp", "") >= data_corte]

        if not recentes:
            recentes = registros[-50:]  # Fallback: ultimos 50

        # Agrupa por rota
        rotas = {}
        for reg in recentes:
            origem = reg.get("origem", "?")
            destino = reg.get("destino", "?")
            rota = f"{origem}/{destino}"

            if rota not in rotas:
                rotas[rota] = {"total": 0, "com_termos": 0}

            rotas[rota]["total"] += 1
            if reg.get("tem_termos"):
                rotas[rota]["com_termos"] += 1

        # Identifica problematicas (>50% com termos E pelo menos 3 ocorrencias)
        problematicas = []
        for rota, dados in rotas.items():
            if dados["total"] >= 3:
                taxa = dados["com_termos"] / dados["total"]
                if taxa > 0.5:
                    problematicas.append({
                        "rota": rota,
                        "taxa_termos": round(taxa, 2),
                        "total": dados["total"],
                    })

        problematicas.sort(key=lambda x: x["taxa_termos"], reverse=True)

        total_voos = len(recentes)
        total_com_termos = sum(1 for r in recentes if r.get("tem_termos"))
        taxa_geral = round(total_com_termos / total_voos, 2) if total_voos > 0 else 0

        return {
            "rotas_problematicas": problematicas,
            "taxa_geral_termos": taxa_geral,
            "total_voos": total_voos,
        }

    def taxa_acerto_ia(self, dias: int = 30) -> dict:
        """
        Calcula taxa de acerto da IA vs regex nos ultimos N dias.

        Returns:
            Dict com estatisticas de acerto
        """
        registros = self._dados.get(CategoriaMemoria.CLASSIFICACAO_EMAIL, [])
        if not registros:
            return {"total": 0, "concordancia": 0, "discordancia": 0}

        data_corte = (datetime.now() - timedelta(days=dias)).isoformat()
        recentes = [r for r in registros if r.get("timestamp", "") >= data_corte]

        if not recentes:
            recentes = registros[-30:]

        concordancia = sum(1 for r in recentes if r.get("regex") == r.get("ia"))
        discordancia = len(recentes) - concordancia

        # Quando discordaram, quem estava certo?
        ia_correta = sum(1 for r in recentes
                         if r.get("correto") is True and r.get("final") == r.get("ia"))
        regex_correta = sum(1 for r in recentes
                           if r.get("correto") is True and r.get("final") == r.get("regex"))

        return {
            "total": len(recentes),
            "concordancia": concordancia,
            "discordancia": discordancia,
            "ia_correta_confirmada": ia_correta,
            "regex_correta_confirmada": regex_correta,
        }

    def erros_frequentes(self, limite: int = 5) -> List[dict]:
        """
        Retorna os erros mais frequentes (agrupados por contexto similar).

        Returns:
            Lista dos top N erros mais frequentes com contagem
        """
        todos_erros = (
            self._dados.get(CategoriaMemoria.ERRO_RECUPERADO, []) +
            self._dados.get(CategoriaMemoria.ERRO_NAO_RECUPERADO, [])
        )

        if not todos_erros:
            return []

        # Agrupa por contexto (primeiras 5 palavras)
        chaves = []
        for err in todos_erros:
            ctx = err.get("contexto", "")
            chave = " ".join(ctx.split()[:5]).lower()
            chaves.append(chave)

        contagem = Counter(chaves)
        mais_frequentes = contagem.most_common(limite)

        resultado = []
        for chave, qtd in mais_frequentes:
            # Pega o registro mais recente desse tipo
            for err in reversed(todos_erros):
                ctx = " ".join(err.get("contexto", "").split()[:5]).lower()
                if ctx == chave:
                    resultado.append({
                        "contexto": err.get("contexto", ""),
                        "erro": err.get("erro", "")[:100],
                        "solucao": err.get("solucao", ""),
                        "ocorrencias": qtd,
                        "resolvido": err.get("resolvido", False),
                    })
                    break

        return resultado

    # ============================================================
    # LIMPEZA
    # ============================================================

    def limpar_antigos(self, dias: int = MAX_DIAS_RETENCAO):
        """Remove registros mais antigos que N dias."""
        data_corte = (datetime.now() - timedelta(days=dias)).isoformat()
        removidos = 0

        for categoria in list(self._dados.keys()):
            antes = len(self._dados[categoria])
            self._dados[categoria] = [
                r for r in self._dados[categoria]
                if r.get("timestamp", "") >= data_corte
            ]
            removidos += antes - len(self._dados[categoria])

        if removidos > 0:
            logger.info(f"Memoria: removidos {removidos} registros antigos (>{dias} dias)")
            self._salvar()

    def estatisticas(self) -> dict:
        """Retorna estatisticas gerais da memoria."""
        stats = {}
        total = 0
        for categoria, registros in self._dados.items():
            stats[categoria] = len(registros)
            total += len(registros)
        stats["_total"] = total
        return stats


# ============================================================
# INSTANCIA GLOBAL (singleton)
# ============================================================

memoria = MemoriaIA()
