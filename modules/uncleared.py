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
        # SEGURANCA: nao adivinha a coluna. Se nao identificou pelo nome,
        # tenta validar o conteudo (coluna que tem AWBs no formato 127XXXXXXXX).
        # Se nenhuma coluna tem AWBs validos, falha explicitamente em vez de
        # comparar dados errados silenciosamente.
        for col in df.columns:
            amostra = df[col].dropna().head(20)
            validos = sum(1 for v in amostra if limpar_awb(str(v)))
            # Se >50% da amostra sao AWBs validos, e essa a coluna
            if len(amostra) > 0 and validos / len(amostra) > 0.5:
                col_awb = col
                logger.info(f"Coluna AWB identificada pelo conteudo: '{col_awb}'")
                break

        if col_awb is None:
            raise ValueError(
                "Coluna de AWB nao encontrada na planilha. "
                f"Colunas disponiveis: {list(df.columns)}. "
                "A coluna deve ter nome contendo 'AWB'/'documento' "
                "ou conter numeros no formato 127XXXXXXXX."
            )

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

    # Busca coluna StatusOperacional
    col_status = None
    for key, original in col_map.items():
        if 'statusoperacional' in key or 'status' in key:
            col_status = original
            break

    # Busca coluna ModalidadePagamento (para identificar FRAP)
    col_modalidade = None
    for key, original in col_map.items():
        if 'modalidadepagamento' in key or 'modalidade' in key:
            col_modalidade = original
            break

    # Aplica filtros
    df_filtrado = df.copy()

    if col_base and base_posse:
        df_filtrado = df_filtrado[
            df_filtrado[col_base].astype(str).str.upper().str.strip() == base_posse.upper()
        ]

    if col_retira and retira_entrega:
        df_filtrado = df_filtrado[
            df_filtrado[col_retira].astype(str).str.upper().str.strip() == retira_entrega.upper()
        ]

    # Extrai AWBs e monta dicionario com info adicional
    awbs_planilha = set()
    awbs_info: dict[str, dict] = {}  # AWB -> {status, frap}

    for _, row in df_filtrado.iterrows():
        awb_limpo = limpar_awb(str(row[col_awb]))
        if not awb_limpo:
            continue

        awbs_planilha.add(awb_limpo)

        status = ""
        if col_status and pd.notna(row.get(col_status, "")):
            status = str(row[col_status]).strip()

        frap = False
        if col_modalidade and pd.notna(row.get(col_modalidade, "")):
            frap = str(row[col_modalidade]).strip().upper() == "FRAP"

        # Se AWB duplicado na planilha, mantém o mais informativo
        if awb_limpo not in awbs_info:
            awbs_info[awb_limpo] = {"status": status, "frap": frap}
        elif frap:
            awbs_info[awb_limpo]["frap"] = True

    logger.info(f"Planilha carregada: {len(awbs_planilha)} AWBs unicos "
                f"(de {len(df_filtrado)} linhas filtradas)")

    # Armazena as infos no dataframe para uso posterior
    df_filtrado.attrs["col_awb"] = col_awb
    df_filtrado.attrs["col_status"] = col_status
    df_filtrado.attrs["col_modalidade"] = col_modalidade
    df_filtrado.attrs["awbs_info"] = awbs_info

    return awbs_planilha, df_filtrado


def comparar_awbs(meus_awbs: List[str], awbs_planilha: Set[str], awbs_info: dict = None) -> dict:
    """
    Compara a lista de AWBs do usuario com os AWBs da planilha do sistema.

    Resultado:
    - presentes: AWBs da minha lista que ESTAO na planilha (estao no terminal)
    - sobrando_planilha: AWBs da planilha que NAO estao na minha lista (pra baixar/investigar)
      com status e flag FRAP de cada um

    Args:
        meus_awbs: Lista de AWBs limpos do usuario (pistolados no terminal)
        awbs_planilha: Set de AWBs da planilha do sistema
        awbs_info: Dict {awb: {status, frap}} vindo da planilha

    Returns:
        Dict com resultados incluindo status e FRAP de cada AWB sobrando
    """
    if awbs_info is None:
        awbs_info = {}

    meus_set = set(meus_awbs)

    presentes = sorted(meus_set & awbs_planilha)
    sobrando = sorted(awbs_planilha - meus_set)

    # Monta lista detalhada dos que sobram na planilha
    sobrando_detalhado = []
    for awb in sobrando:
        info = awbs_info.get(awb, {})
        sobrando_detalhado.append({
            "awb": awb,
            "status": info.get("status", ""),
            "frap": info.get("frap", False),
        })

    # Monta lista detalhada dos presentes (no terminal)
    presentes_detalhado = []
    for awb in presentes:
        info = awbs_info.get(awb, {})
        presentes_detalhado.append({
            "awb": awb,
            "status": info.get("status", ""),
            "frap": info.get("frap", False),
        })

    resultado = {
        "presentes": presentes,
        "presentes_detalhado": presentes_detalhado,
        "sobrando_planilha": sobrando,
        "sobrando_detalhado": sobrando_detalhado,
        "total_meus": len(meus_set),
        "total_planilha": len(awbs_planilha),
        "total_presentes": len(presentes),
        "total_sobrando": len(sobrando),
        "total_frap": sum(1 for x in sobrando_detalhado if x["frap"]),
    }

    logger.info(
        f"Comparacao: {resultado['total_presentes']} no terminal, "
        f"{resultado['total_sobrando']} pra baixar "
        f"({resultado['total_frap']} FRAP)"
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
    - 'Pra Baixar': AWBs da planilha que NAO estao no terminal (com status e FRAP)
    - 'No Terminal': AWBs que estao no terminal (com status e FRAP)
    """
    with pd.ExcelWriter(caminho, engine='openpyxl') as writer:
        if resultado["sobrando_detalhado"]:
            rows = []
            for item in resultado["sobrando_detalhado"]:
                rows.append({
                    "AWB": item["awb"],
                    "Status": item["status"],
                    "FRAP": "SIM" if item["frap"] else "",
                })
            pd.DataFrame(rows).to_excel(writer, sheet_name="Pra Baixar", index=False)

        if resultado["presentes_detalhado"]:
            rows = []
            for item in resultado["presentes_detalhado"]:
                rows.append({
                    "AWB": item["awb"],
                    "Status": item["status"],
                    "FRAP": "SIM" if item["frap"] else "",
                })
            pd.DataFrame(rows).to_excel(writer, sheet_name="No Terminal", index=False)

    logger.info(f"Comparacao salva: {caminho}")
