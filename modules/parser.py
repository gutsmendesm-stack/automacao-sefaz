"""
Parser do relatorio da SEFAZ-AL e do manifesto de despacho (PDF).
- Extrai termos de apreensao, CTes e informacoes do relatorio SEFAZ
- Extrai AWBs do manifesto separando RETIRA e ENTREGA
"""

import re
import logging
from typing import List, Set
from pathlib import Path

import pdfplumber

from models.termo import (
    TermoApreensao,
    ConsultaMDFe,
    ManifestoVoo,
    TipoFielDepositario,
    SituacaoTermo,
    StatusMDFe,
)

logger = logging.getLogger(__name__)


# ============================================================
# PARSER DO RELATORIO SEFAZ (texto do PDF ou da pagina web)
# ============================================================

def parsear_situacao(texto: str) -> SituacaoTermo:
    """Converte texto de situacao para enum."""
    texto = texto.strip().upper()
    if "PENDENTE" in texto:
        return SituacaoTermo.PENDENTE
    elif "REGULARIZ" in texto:
        return SituacaoTermo.REGULARIZADO
    return SituacaoTermo.DESCONHECIDO


def parsear_status_mdfe(texto: str) -> StatusMDFe:
    """Converte texto de status do MDF-e para enum."""
    texto = texto.strip().upper()
    if "COM PEND" in texto:
        return StatusMDFe.ANALISADO_COM_PENDENCIAS
    elif "SEM PEND" in texto:
        return StatusMDFe.ANALISADO_SEM_PENDENCIAS
    elif "EM AN" in texto:
        return StatusMDFe.EM_ANALISE
    elif "LIBERADO" in texto:
        return StatusMDFe.SEM_ANALISE  # Liberado = sem termos/pendencias
    return StatusMDFe.DESCONHECIDO


def extrair_chave_mdfe(texto: str) -> str:
    """Extrai chave do MDF-e (44 digitos) do texto."""
    match = re.search(r'\b(\d{44})\b', texto)
    return match.group(1) if match else ""


def extrair_numero_mdfe(texto: str) -> str:
    """Extrai numero do MDF-e do texto."""
    # Padrao: chave de 44 digitos seguida do numero do MDF-e
    match = re.search(r'\d{44}\s+(\d{4,7})', texto)
    if match:
        return match.group(1)
    # Tenta padrao com label
    match = re.search(r'N[º°o]\s*MDF-?E\s*(\d+)', texto, re.IGNORECASE)
    if match:
        return match.group(1)
    # Tenta padrao alternativo
    match = re.search(r'MDF-?[eE]\s*(\d{5,7})', texto)
    return match.group(1) if match else ""


def extrair_total_termos(texto: str) -> int:
    """Extrai o numero total de termos."""
    match = re.search(r'TOTAL DE TERMOS\s*(\d+)', texto, re.IGNORECASE)
    return int(match.group(1)) if match else 0


def extrair_termos(texto: str) -> List[TermoApreensao]:
    """
    Extrai todos os termos de apreensao do texto do relatorio.
    Usa abordagem por linhas para maior robustez.
    
    Formatos conhecidos de CTe no relatorio:
    - "CTe 7398054" / "CT-e 7398054" / "CTE: 7398054"
    - Pode estar em colunas separadas (numero | situacao | data | NF-e | CT-e)
    - Pode estar em linhas adjacentes ao termo
    """
    termos = []
    linhas = texto.split('\n')
    tipo_atual = TipoFielDepositario.TRANSPORTADORA

    for i, linha in enumerate(linhas):
        linha_upper = linha.upper().strip()

        # Detecta mudanca de secao
        if "TRANSPORTADORA FIEL" in linha_upper or "TRANSPORTADORA FIEL DEPOSIT" in linha_upper:
            tipo_atual = TipoFielDepositario.TRANSPORTADORA
            continue
        elif ("DESTINAT" in linha_upper and "FIEL" in linha_upper) or \
             "DESTINATÁRIO FIEL" in linha_upper:
            tipo_atual = TipoFielDepositario.DESTINATARIO
            continue

        # Busca termos: numero de 7 digitos
        match_termo = re.search(r'\b(\d{7})\b', linha)
        
        # Padroes de CTe (mais flexiveis)
        match_cte = (
            re.search(r'CT-?[eE]\s*[:\s]*(\d+)', linha) or
            re.search(r'CTE\s*[:\s]*(\d+)', linha, re.IGNORECASE) or
            re.search(r'Conhecimento\s*[:\s]*(\d+)', linha, re.IGNORECASE)
        )
        
        # Padroes de NF-e
        match_nfe = (
            re.search(r'NF-?[eE]\s*[:\s]*(\d+)', linha) or
            re.search(r'NFE\s*[:\s]*(\d+)', linha, re.IGNORECASE) or
            re.search(r'Nota\s*Fiscal\s*[:\s]*(\d+)', linha, re.IGNORECASE)
        )
        
        match_data = re.search(r'(\d{2}/\d{2}/\d{4})', linha)
        match_situacao = re.search(r'(Pendente|Regularizado|Liberado)', linha, re.IGNORECASE)

        if match_termo and (match_cte or match_nfe):
            num_termo = match_termo.group(1)
            num_cte = match_cte.group(1) if match_cte else ""
            num_nfe = match_nfe.group(1) if match_nfe else ""

            # Pula se o "termo" e igual ao CTe ou NF-e
            if num_termo == num_cte or num_termo == num_nfe:
                continue

            termo = TermoApreensao(
                numero=num_termo,
                situacao=parsear_situacao(match_situacao.group(1)) if match_situacao else SituacaoTermo.DESCONHECIDO,
                data_emissao=match_data.group(1) if match_data else "",
                tipo_fiel=tipo_atual,
                nfe=num_nfe,
                cte=num_cte,
            )
            termos.append(termo)
            logger.debug(f"Termo encontrado: {termo.numero} - CTe: {termo.cte} - NF-e: {termo.nfe}")

            # IMPORTANTE: Um termo pode ter MULTIPLOS CTes em linhas seguintes
            # (ex: TA 2432400 com CT-e 7404445 E CT-e 239509 em linhas diferentes)
            # Busca CTes adicionais nas linhas seguintes ate encontrar novo termo
            ultimo_termo_numero = num_termo
            ultimo_termo_situacao = match_situacao.group(1) if match_situacao else ""
            ultimo_termo_data = match_data.group(1) if match_data else ""

            for j in range(i + 1, min(i + 20, len(linhas))):
                linha_seguinte = linhas[j]
                # Para se encontrar outro termo (7 digitos no inicio)
                if re.match(r'\s*\d{7}\b', linha_seguinte):
                    break

                # Busca CTes adicionais na linha seguinte
                cte_extra = (
                    re.search(r'CT-?[eE]\s*[:\s]*(\d+)', linha_seguinte) or
                    re.search(r'CTE\s*[:\s]*(\d+)', linha_seguinte, re.IGNORECASE)
                )
                nfe_extra = (
                    re.search(r'NF-?[eE]\s*[:\s]*(\d+)', linha_seguinte) or
                    re.search(r'NFE\s*[:\s]*(\d+)', linha_seguinte, re.IGNORECASE)
                )

                if cte_extra:
                    cte_num = cte_extra.group(1)
                    nfe_num = nfe_extra.group(1) if nfe_extra else ""
                    # So adiciona se CTe diferente do ja encontrado
                    ctes_ja_adicionados = set(t.cte for t in termos if t.numero == ultimo_termo_numero)
                    if cte_num not in ctes_ja_adicionados:
                        termo_extra = TermoApreensao(
                            numero=ultimo_termo_numero,
                            situacao=parsear_situacao(ultimo_termo_situacao) if ultimo_termo_situacao else SituacaoTermo.DESCONHECIDO,
                            data_emissao=ultimo_termo_data,
                            tipo_fiel=tipo_atual,
                            nfe=nfe_num,
                            cte=cte_num,
                        )
                        termos.append(termo_extra)
                        logger.debug(f"Termo {ultimo_termo_numero} CTe EXTRA: {cte_num}")

        elif match_termo and not match_cte and not match_nfe:
            # Termo encontrado SEM CTe/NF-e na mesma linha
            # Tenta buscar nas linhas seguintes (pode ter multiplos CTes)
            num_termo = match_termo.group(1)
            ctes_encontrados = []
            nfes_encontrados = []

            for offset in range(1, 20):
                idx = i + offset
                if idx >= len(linhas):
                    break
                linha_adj = linhas[idx]
                # Para se encontrar outro termo
                if re.match(r'\s*\d{7}\b', linha_adj):
                    break

                m_cte = (re.search(r'CT-?[eE]\s*[:\s]*(\d+)', linha_adj) or
                         re.search(r'CTE\s*[:\s]*(\d+)', linha_adj, re.IGNORECASE))
                m_nfe = (re.search(r'NF-?[eE]\s*[:\s]*(\d+)', linha_adj) or
                         re.search(r'NFE\s*[:\s]*(\d+)', linha_adj, re.IGNORECASE))

                if m_cte:
                    cte_val = m_cte.group(1)
                    nfe_val = m_nfe.group(1) if m_nfe else ""
                    if cte_val not in [c[0] for c in ctes_encontrados]:
                        ctes_encontrados.append((cte_val, nfe_val))

            # Cria um TermoApreensao para CADA CTe encontrado
            if ctes_encontrados:
                for cte_val, nfe_val in ctes_encontrados:
                    termo = TermoApreensao(
                        numero=num_termo,
                        situacao=parsear_situacao(match_situacao.group(1)) if match_situacao else SituacaoTermo.DESCONHECIDO,
                        data_emissao=match_data.group(1) if match_data else "",
                        tipo_fiel=tipo_atual,
                        nfe=nfe_val,
                        cte=cte_val,
                    )
                    termos.append(termo)
                    logger.debug(f"Termo encontrado (multi-cte): {num_termo} - CTe: {cte_val}")

    # Tenta regex mais complexo se nao encontrou nada
    if not termos:
        termos = _extrair_termos_regex_complexo(texto)

    # FALLBACK: se encontrou termos mas NENHUM tem CTe,
    # tenta extrair CTes globalmente e associar
    if termos and all(not t.cte for t in termos):
        logger.warning("Termos encontrados mas NENHUM com CTe. Tentando extracao global...")
        todos_ctes = re.findall(r'CT-?[eE]\s*[:\s]*(\d+)', texto)
        if not todos_ctes:
            todos_ctes = re.findall(r'CTE\s*[:\s]*(\d+)', texto, re.IGNORECASE)
        if not todos_ctes:
            # Busca numeros de 6-7 digitos que nao sejam numeros de termos
            numeros_termos = set(t.numero for t in termos)
            candidatos = re.findall(r'\b(\d{6,7})\b', texto)
            todos_ctes = [c for c in candidatos if c not in numeros_termos]

        if todos_ctes:
            ctes_unicos = list(dict.fromkeys(todos_ctes))
            for idx, termo in enumerate(termos):
                if idx < len(ctes_unicos):
                    termo.cte = ctes_unicos[idx]
                    logger.info(f"Associou CTe {ctes_unicos[idx]} ao termo {termo.numero} (fallback global)")

    return termos


def _extrair_termos_regex_complexo(texto: str) -> List[TermoApreensao]:
    """Abordagem com regex complexo para formatos mais estruturados."""
    termos = []
    tipo_atual = TipoFielDepositario.TRANSPORTADORA

    # Divide por secoes
    secoes = re.split(
        r'(Transportadora Fiel Deposit[aá]rio|Destinat[aá]rio Fiel Deposit[aá]rio)',
        texto, flags=re.IGNORECASE
    )

    for secao in secoes:
        secao_upper = secao.strip().upper()
        if "TRANSPORTADORA FIEL" in secao_upper:
            tipo_atual = TipoFielDepositario.TRANSPORTADORA
            continue
        elif "DESTINAT" in secao_upper and "FIEL" in secao_upper:
            tipo_atual = TipoFielDepositario.DESTINATARIO
            continue

        # Padrao completo
        padrao = re.compile(
            r'(\d{6,8})\s+'
            r'(Pendente|Regularizado|Liberado).*?'
            r'(\d{2}/\d{2}/\d{4}).*?'
            r'(?:NF-?e\s*(\d+))?\s*'
            r'(?:CT-?e\s*(\d+))?',
            re.IGNORECASE | re.DOTALL
        )

        for match in padrao.finditer(secao):
            termo = TermoApreensao(
                numero=match.group(1),
                situacao=parsear_situacao(match.group(2)),
                data_emissao=match.group(3),
                tipo_fiel=tipo_atual,
                nfe=match.group(4) or "",
                cte=match.group(5) or "",
            )
            termos.append(termo)

    return termos


def parsear_relatorio_sefaz(texto: str) -> ConsultaMDFe:
    """
    Parseia o texto completo do relatorio da SEFAZ.
    Funciona tanto com texto extraido da pagina web quanto do PDF.
    """
    chave = extrair_chave_mdfe(texto)
    numero_mdfe = extrair_numero_mdfe(texto)
    total_termos = extrair_total_termos(texto)
    status = parsear_status_mdfe(texto)
    termos = extrair_termos(texto)

    # IMPORTANTE: Se o relatorio diz explicitamente "TOTAL DE TERMOS 0",
    # confiamos nele e descartamos qualquer "termo" encontrado pelo parser
    # (sao falsos positivos - numeros de 7 digitos que nao sao termos reais).
    # Tambem verifica mensagens explicitas de "nao encontrados termos".
    relatorio_diz_zero = (
        total_termos == 0 and
        re.search(r'TOTAL DE TERMOS\s*0', texto, re.IGNORECASE) is not None
    )
    nao_encontrou_termos = (
        "não foram encontrados termos" in texto.lower() or
        "nao foram encontrados termos" in texto.lower() or
        "Não foram encontrados termos" in texto
    )

    if relatorio_diz_zero or nao_encontrou_termos:
        if termos:
            logger.warning(
                f"Relatorio diz 0 termos mas parser achou {len(termos)} - "
                f"descartando (falso positivo)"
            )
        termos = []
        total_termos = 0

    # Extrair data de emissao
    match_data = re.search(r'DATA DE EMISS[ÃA]O\s*(\d{2}/\d{2}/\d{4})', texto, re.IGNORECASE)
    data_emissao = match_data.group(1) if match_data else ""

    # Extrair emitente
    match_emitente = re.search(r'EMITENTE\s+(.+)', texto)
    if not match_emitente:
        match_emitente = re.search(r'RAZ[ÃA]O SOCIAL[:\s]+(.+)', texto, re.IGNORECASE)
    emitente = match_emitente.group(1).strip() if match_emitente else ""

    # Extrair CNPJ
    match_cnpj = re.search(r'CNPJ[:\s]+([\d./-]+)', texto)
    cnpj = match_cnpj.group(1).strip() if match_cnpj else ""

    # Determina total_termos final:
    # Se o relatorio informa um valor > 0, usa ele.
    # Senao, usa a quantidade de termos que o parser encontrou.
    total_final = total_termos if total_termos > 0 else len(termos)

    consulta = ConsultaMDFe(
        chave=chave,
        numero_mdfe=numero_mdfe,
        data_emissao=data_emissao,
        emitente=emitente,
        cnpj_emitente=cnpj,
        status=status,
        total_termos=total_final,
        termos=termos,
    )

    logger.info(
        f"Relatorio parseado: MDF-e {numero_mdfe} | "
        f"{len(termos)} termos reais | "
        f"Total declarado: {total_termos} | "
        f"Status: {status.value}"
    )

    return consulta


def parsear_relatorio_pdf(caminho_pdf: str) -> ConsultaMDFe:
    """
    Parseia um PDF de relatorio da SEFAZ (baixado do email ou do site).
    Extrai texto de todas as paginas e passa pro parser de texto.
    """
    texto_completo = ""
    try:
        with pdfplumber.open(caminho_pdf) as pdf:
            for page in pdf.pages:
                texto_pagina = page.extract_text() or ""
                texto_completo += texto_pagina + "\n"
    except Exception as e:
        logger.error(f"Erro ao ler PDF do relatorio: {e}")
        return ConsultaMDFe()

    return parsear_relatorio_sefaz(texto_completo)


# ============================================================
# PARSER DO MANIFESTO DE DESPACHO (PDF do Nexlog)
# ============================================================

def parsear_manifesto_pdf(caminho_pdf: str) -> ManifestoVoo:
    """
    Parseia o PDF do manifesto de despacho do Nexlog.
    Extrai AWBs separando por RETIRA e ENTREGA.

    Regra importante: uma secao so acaba quando encontra outra secao,
    mesmo que mude de pagina. Os AWBs continuam na secao anterior
    ate encontrar um novo cabecalho "Produto: ... - RETIRA/ENTREGA".
    """
    manifesto = ManifestoVoo()
    texto_completo = ""

    try:
        with pdfplumber.open(caminho_pdf) as pdf:
            for page in pdf.pages:
                texto_pagina = page.extract_text() or ""
                texto_completo += texto_pagina + "\n"
    except Exception as e:
        logger.error(f"Erro ao ler PDF do manifesto: {e}")
        return manifesto

    # Extrai info do cabecalho
    match_manifesto = re.search(r'N\.?\s*[º°o]\s*[:.]?\s*(\d+)', texto_completo)
    if match_manifesto:
        manifesto.numero_manifesto = match_manifesto.group(1)

    match_voo = re.search(r'N[uú]mero\s*V[oô]o[:.]?\s*(G3\s*\d+)', texto_completo, re.IGNORECASE)
    if match_voo:
        manifesto.numero_voo = match_voo.group(1)

    match_data = re.search(r'Data\s*V[oô]o[:.]?\s*(\d{2}/\d{2}/\d{4})', texto_completo, re.IGNORECASE)
    if match_data:
        manifesto.data_voo = match_data.group(1)

    match_origem = re.search(r'Origem[:.]?\s*(\w{3})', texto_completo, re.IGNORECASE)
    if match_origem:
        manifesto.origem = match_origem.group(1)

    match_destino = re.search(r'Destino[:.]?\s*(\w{3})', texto_completo, re.IGNORECASE)
    if match_destino:
        manifesto.destino = match_destino.group(1)

    # Extrai AWBs separando por secao RETIRA / ENTREGA
    # A secao e definida pelo cabecalho "Produto: XXXXX - RETIRA" ou "Produto: XXXXX - ENTREGA"
    tipo_secao_atual = None  # None = antes da primeira secao

    linhas = texto_completo.split('\n')
    for linha in linhas:
        linha_upper = linha.upper().strip()

        # Detecta cabecalhos de secao
        # Padroes: "Produto: TARIFARIO SBY - RETIRA", "Produto: E-GOLLOG - ENTREGA"
        # Tambem: "URGENTE FRACIONADO - RETIRA", etc.
        if "- RETIRA" in linha_upper and ("PRODUTO" in linha_upper or "TOTAL" not in linha_upper):
            tipo_secao_atual = "RETIRA"
            continue
        elif "- ENTREGA" in linha_upper and ("PRODUTO" in linha_upper or "TOTAL" not in linha_upper):
            tipo_secao_atual = "ENTREGA"
            continue

        # Pula linhas de cabecalho/total
        if "TOTAL" in linha_upper and "PRODUTO" in linha_upper:
            continue
        if "DOC. F" in linha_upper or "DOC.F" in linha_upper:
            continue

        # Busca AWBs (começam com 127 e tem pelo menos 10 digitos)
        if tipo_secao_atual:
            matches = re.findall(r'\b(127\d{7,})\b', linha)
            for awb in matches:
                if tipo_secao_atual == "RETIRA":
                    manifesto.awbs_retira.add(awb)
                else:
                    manifesto.awbs_entrega.add(awb)

    logger.info(
        f"Manifesto parseado: Voo {manifesto.numero_voo} | "
        f"RETIRA: {len(manifesto.awbs_retira)} AWBs | "
        f"ENTREGA: {len(manifesto.awbs_entrega)} AWBs"
    )

    return manifesto


# ============================================================
# DETECÇAO DE RESPOSTA DO EMAIL DA SEFAZ
# ============================================================

def detectar_resposta_email(texto_email: str) -> str:
    """
    Analisa o texto do email de resposta da SEFAZ.
    Retorna: "com_termos", "sem_termos", ou "indefinido"
    """
    texto_upper = texto_email.upper()

    # Indicadores de SEM termos (voo liberado)
    indicadores_sem_termos = [
        "SEM RETEN",
        "SEM PEND",
        "LIBERADO",
        "NAO HOUVE RETEN",
        "NÃO HOUVE RETEN",
        "SEM TERMOS",
        "NENHUM TERMO",
        "SEM IRREGULARIDADE",
        "NENHUMA IRREGULARIDADE",
        "REGULAR",
    ]

    # Indicadores de COM termos
    indicadores_com_termos = [
        "RELAT" + chr(211) + "RIO ANEXO",  # RELATÓRIO ANEXO
        "RELATÓRIO ANEXO",
        "RELATORIO ANEXO",
        "GERADO O RELAT",
        "TERMO DE APREEN",
        "COM PEND",
        "RETEN" + chr(199) + chr(195) + "O",  # RETENÇÃO
        "RETENCAO",
        "RETENÇÃO",
    ]

    for indicador in indicadores_sem_termos:
        if indicador in texto_upper:
            return "sem_termos"

    for indicador in indicadores_com_termos:
        if indicador in texto_upper:
            return "com_termos"

    return "indefinido"
