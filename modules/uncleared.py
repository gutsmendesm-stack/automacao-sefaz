"""
Modulo Uncleared - Limpeza, deduplicacao e comparacao de AWBs.

Funcionalidades:
1. Limpar AWBs: manter apenas os 11 primeiros digitos (127XXXXXXXX)
2. Remover duplicados
3. Comparar com planilha do sistema (filtrada por BasePosse=MCZ e RetiraEntrega=RETIRA)

Uso:
    from modules.uncleared import limpar_awbs, comparar_com_planilha, carregar_planilha_sistema
"""

import re
import logging
import os
from typing import List, Set, Tuple, Optional
from pathlib import Path

import pandas as pd

logger = logging.getLogger(__name__)


def limpar_awb(awb: str) -> str:
    """
    Limpa um AWB: extrai apenas os 11 primeiros digitos.
    AWBs validos comecam com 127 e tem 11 digitos.

    Args:
        awb: String com o AWB (pode ter digitos extras no final)

    Returns:
        AWB limpo (11 digitos) ou "" se invalido
    """
    # Remove espacos e caracteres nao numericos
    apenas_digitos = re.sub(r'\D', '', str(awb).strip())

    # Verifica se comeca com 127 e tem pelo menos 11 digitos
    if apenas_digitos.startswith("127") and len(apenas_digitos) >= 11:
        return apenas_digitos[:11]

    # Tenta encontrar sequencia 127 + 8 digitos em qualquer posicao
    match = re.search(r'(127\d{8})', apenas_digitos)
    if match:
        return match.group(1)

    return ""


def limpar_awbs(texto: str) -> Tuple[List[str], List[str], int]:
    """
    Limpa uma lista de AWBs colada pelo usuario.
    Aceita separacao por quebra de linha, virgula, ponto-e-virgula, tab ou espaco.

    Args:
        texto: Texto colado pelo usuario com AWBs

    Returns:
        Tupla (awbs_limpos_unicos, awbs_limpos_todos, total_duplicados_removidos)
    """
    if not texto or not texto.strip():
        return [], [], 0

    # Separa por qualquer delimitador comum
    itens = re.split(r'[\n\r,;\t]+', texto.strip())

    awbs_limpos = []
    for item in itens:
        item = item.strip()
        if not item:
            continue

        awb_limpo = limpar_awb(item)
        if awb_limpo:
            awbs_limpos.append(awb_limpo)

    # Remove duplicados mantendo a ordem
    vistos = set()
    awbs_unicos = []
    for awb in awbs_limpos:
        if awb not in vistos:
            vistos.add(awb)
            awbs_unicos.append(awb)

    duplicados_removidos = len(awbs_limpos) - len(awbs_unicos)

    return awbs_unicos, awbs_limpos, duplicados_removidos


def carregar_planilha_sistema(caminho: str, base_posse: str = "MCZ",
                              retira_entrega: str = "RETIRA") -> Tuple[Set[str], pd.DataFrame]:
    """
    Carrega a planilha do sistema e filtra por BasePosse e RetiraEntrega.

    Args:
        caminho: Caminho do arquivo .xlsx ou .csv
        base_posse: Valor do filtro BasePosse (padrao: "MCZ")
        retira_entrega: Valor do filtro RetiraEntrega (padrao: "RETIRA")

    Returns:
        Tupla (set_de_awbs, dataframe_filtrado)
    """
    ext = Path(caminho).suffix.lower()

    if ext in ('.xlsx', '.xls'):
        df = pd.read_excel(caminho)
    elif ext == '.csv':
        # Tenta diferentes encodings e separadores
        try:
            df = pd.read_csv(caminho, encoding='utf-8')
        except Exception:
            try:
                df = pd.read_csv(caminho, encoding='latin-1')
            except Exception:
                df = pd.read_csv(caminho, encoding='utf-8', sep=';')
    else:
        raise ValueError(f"Formato nao suportado: {ext}. Use .xlsx, .xls ou .csv")

    # Identifica colunas (case-insensitive)
    col_map = {col.lower().replace(' ', '').replace('_', ''): col for col in df.columns}

    # Busca coluna de AWB
    col_awb = None
    for key, original in col_map.items():
        if 'numeroawb' in key or 'awb' in key or 'documento' in key:
            col_awb = original
            break

    if col_awb is None:
        # Tenta coluna D (indice 3) como fallback
        if len(df.columns) > 3:
            col_awb = df.columns[3]
            logger.warning(f"Coluna AWB nao identificada pelo nome, usando coluna D: '{col_awb}'")
        else:
            raise ValueError("Coluna de AWB nao encontrada na planilha")

    # Busca coluna BasePosse
    col_base = None
    for key, original in col_map.items():
        if 'baseposse' in key or 'base_posse' in key:
            col_base = original
            break

    # Busca coluna RetiraEntrega
    col_retira = None
    for key, original in col_map.items():
        if 'retiraentrega' in key or 'retira_entrega' in key:
            col_retira = original
            break

    # Aplica filtros
    df_filtrado = df.copy()

    if col_base and base_posse:
        df_filtrado = df_filtrado[
            df_filtrado[col_base].astype(str).str.upper().str.strip() == base_posse.upper()
        ]
        logger.info(f"Filtro BasePosse='{base_posse}': {len(df_filtrado)} linhas")

    if col_retira and retira_entrega:
        df_filtrado = df_filtrado[
            df_filtrado[col_retira].astype(str).str.upper().str.strip() == retira_entrega.upper()
        ]
        logger.info(f"Filtro RetiraEntrega='{retira_entrega}': {len(df_filtrado)} linhas")

    # Extrai AWBs e limpa
    awbs_planilha = set()
    for valor in df_filtrado[col_awb].dropna():
        awb_limpo = limpar_awb(str(valor))
        if awb_limpo:
            awbs_planilha.add(awb_limpo)

    logger.info(f"Planilha carregada: {len(awbs_planilha)} AWBs unicos "
                f"(de {len(df_filtrado)} linhas filtradas)")

    return awbs_planilha, df_filtrado


def comparar_awbs(meus_awbs: List[str], awbs_planilha: Set[str]) -> dict:
    """
    Compara a lista de AWBs do usuario com os AWBs da planilha do sistema.

    Args:
        meus_awbs: Lista de AWBs limpos do usuario
        awbs_planilha: Set de AWBs da planilha do sistema

    Returns:
        Dict com:
            - presentes: AWBs que estao nas DUAS listas
            - ausentes: AWBs que estao na minha lista MAS NAO na planilha
            - total_meus: total de AWBs na lista do usuario
            - total_planilha: total de AWBs na planilha
    """
    meus_set = set(meus_awbs)

    presentes = sorted(meus_set & awbs_planilha)
    ausentes = sorted(meus_set - awbs_planilha)

    resultado = {
        "presentes": presentes,
        "ausentes": ausentes,
        "total_meus": len(meus_set),
        "total_planilha": len(awbs_planilha),
        "total_presentes": len(presentes),
        "total_ausentes": len(ausentes),
    }

    logger.info(
        f"Comparacao: {resultado['total_presentes']} presentes, "
        f"{resultado['total_ausentes']} ausentes "
        f"(de {resultado['total_meus']} meus vs {resultado['total_planilha']} na planilha)"
    )

    return resultado


def salvar_resultado(awbs: List[str], caminho: str, formato: str = "txt"):
    """
    Salva lista de AWBs em arquivo.

    Args:
        awbs: Lista de AWBs
        caminho: Caminho do arquivo de saida
        formato: 'txt', 'csv', ou 'xlsx'
    """
    if formato == "txt":
        with open(caminho, "w", encoding="utf-8") as f:
            for awb in awbs:
                f.write(awb + "\n")

    elif formato == "csv":
        df = pd.DataFrame({"AWB": awbs})
        df.to_csv(caminho, index=False, encoding="utf-8")

    elif formato == "xlsx":
        df = pd.DataFrame({"AWB": awbs})
        df.to_excel(caminho, index=False)

    logger.info(f"Resultado salvo: {caminho} ({len(awbs)} AWBs)")


def salvar_comparacao(resultado: dict, caminho: str):
    """
    Salva resultado da comparacao em xlsx com duas abas:
    - 'Presentes': AWBs que estao nas duas listas
    - 'Ausentes': AWBs que NAO estao na planilha
    """
    with pd.ExcelWriter(caminho, engine='openpyxl') as writer:
        if resultado["presentes"]:
            df_presentes = pd.DataFrame({"AWB": resultado["presentes"]})
            df_presentes.to_excel(writer, sheet_name="Presentes", index=False)

        if resultado["ausentes"]:
            df_ausentes = pd.DataFrame({"AWB": resultado["ausentes"]})
            df_ausentes.to_excel(writer, sheet_name="Ausentes", index=False)

    logger.info(f"Comparacao salva: {caminho}")
