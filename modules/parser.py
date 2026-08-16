"""
Parser do relatorio da SEFAZ-AL e do manifesto de despacho (PDF).
- Extrai termos de apreensao, CTes e informacoes do relatorio SEFAZ
- Extrai AWBs do manifesto separando RETIRA e ENTREGA
"""

import os
import re
import logging
from typing import List, Optional, Set
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


def _extrair_termos_formato_ocr(texto: str) -> tuple:
    """
    Fallback para extrair termos de texto gerado por OCR.
    O formato OCR e diferente do relatorio digital:
    - Termos: "2752679 | Pendente 13/08/2026 GOL LINHAS..."
    - Ou: "2752679 ( Pendente 13/08/2026..."  (OCR confunde | com ( )
    - Total: "5 termos" no final
    - CTes em secao separada: "CT-e 288136"
    - NF-es em secao separada: "NF-e 654"
    
    IMPORTANTE: Um termo pode ter MULTIPLOS CTes (mesma NF-e em CTes diferentes).
    O OCR lista todos os CTes/NF-es linearmente. A associacao termo<->CTe e feita
    distribuindo os CTes unicos entre os termos (melhor esforco).

    Returns:
        (lista_termos, total) ou ([], 0) se nao encontrou
    """
    termos = []

    # Extrai total de termos (ex: "5 termos")
    match_total = re.search(r'(\d+)\s+termos?\b', texto, re.IGNORECASE)
    total_declarado = int(match_total.group(1)) if match_total else 0

    # Extrai CTes UNICOS (formato: "CT-e 288136" ou "CTe 288136")
    ctes_todos = re.findall(r'CT-?e\s+(\d{5,8})', texto, re.IGNORECASE)
    ctes_unicos = list(dict.fromkeys(ctes_todos))  # Remove duplicatas mantendo ordem

    # Extrai NF-es (formato: "NF-e 654" ou "NFe 654")
    nfes_todas = re.findall(r'NF-?e\s+(\d+)', texto, re.IGNORECASE)
    nfes_unicas = list(dict.fromkeys(nfes_todas))

    # Extrai termos (formato: "2752679 | Pendente" ou "2752679 ( Pendente")
    padrao_termo = re.findall(
        r'(\d{7})\s*[|\(\[)]?\s*(Pendente|Regularizado|pendente|regularizado)\s*'
        r'(\d{2}/\d{2}/\d{4})?',
        texto, re.IGNORECASE
    )

    if padrao_termo:
        for i, (numero, situacao, data) in enumerate(padrao_termo):
            sit = SituacaoTermo.PENDENTE if "pendente" in situacao.lower() else SituacaoTermo.REGULARIZADO

            # Associa CTe unico por posicao (melhor esforco)
            cte = ctes_unicos[i] if i < len(ctes_unicos) else ""
            nfe = nfes_unicas[i] if i < len(nfes_unicas) else ""

            termo = TermoApreensao(
                numero=numero,
                cte=cte,
                nfe=nfe,
                situacao=sit,
                data_emissao=data or "",
                tipo_fiel=TipoFielDepositario.TRANSPORTADORA,
            )
            termos.append(termo)

    # Se nao achou pelo padrao detalhado mas tem "X termos" declarado,
    # tenta pegar os numeros de 7 digitos que aparecem no texto
    if not termos and total_declarado > 0:
        numeros_7dig = re.findall(r'\b(\d{7})\b', texto)
        for num in numeros_7dig:
            contexto = texto[max(0, texto.find(num)-10):texto.find(num)+50]
            if "CNPJ" in contexto or "MDF" in contexto:
                continue
            cte = ctes_unicos[len(termos)] if len(termos) < len(ctes_unicos) else ""
            nfe = nfes_unicas[len(termos)] if len(termos) < len(nfes_unicas) else ""
            termo = TermoApreensao(
                numero=num,
                cte=cte,
                nfe=nfe,
                situacao=SituacaoTermo.PENDENTE,
                data_emissao="",
                tipo_fiel=TipoFielDepositario.TRANSPORTADORA,
            )
            termos.append(termo)
            if len(termos) >= total_declarado:
                break

    # Verifica "NAO EXISTE ANALISE" — significa sem termos
    if re.search(r'N[ÃA]O EXISTE AN[ÁA]LISE', texto, re.IGNORECASE):
        if not termos:
            return ([], 0)

    total_final = total_declarado if total_declarado > 0 else len(termos)
    return (termos, total_final)


def parsear_relatorio_sefaz(texto: str) -> ConsultaMDFe:
    """
    Parseia o texto completo do relatorio da SEFAZ.
    Funciona tanto com texto extraido da pagina web quanto do PDF.

    Estrategia: regex primeiro (rapido), depois IA complementa se necessario
    (termos sem CTe, total inconsistente, etc).
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

    # FALLBACK OCR: Se o regex nao encontrou termos mas o texto parece ter,
    # tenta extrair com padroes mais flexiveis (formato OCR com pipes/linhas separadas)
    if not termos and not relatorio_diz_zero and not nao_encontrou_termos:
        termos_ocr, total_ocr = _extrair_termos_formato_ocr(texto)
        if termos_ocr:
            termos = termos_ocr
            total_termos = total_ocr
            logger.info(f"Termos extraidos via fallback OCR: {len(termos)} termos")

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

    resultado_regex = ConsultaMDFe(
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
        f"Relatorio parseado (regex): MDF-e {numero_mdfe} | "
        f"{len(termos)} termos reais | "
        f"Total declarado: {total_termos} | "
        f"Status: {status.value}"
    )

    # Complementa com IA se necessario (termos sem CTe, total inconsistente, etc)
    try:
        from modules.ia_local import ia_local
        resultado_final = ia_local.extrair_termos_com_fallback(texto, resultado_regex)

        # Valida extracao (sanity check — remove falsos positivos)
        if resultado_final.termos:
            termos_validados = ia_local.validar_extracao(resultado_final.termos, texto)
            if len(termos_validados) != len(resultado_final.termos):
                resultado_final.termos = termos_validados
                resultado_final.total_termos = len(termos_validados)

        return resultado_final
    except Exception as e:
        logger.warning(f"Erro ao complementar com IA: {e} - usando resultado regex puro")
        return resultado_regex


def parsear_relatorio_pdf(caminho_pdf: str) -> Optional[ConsultaMDFe]:
    """
    Parseia um PDF de relatorio da SEFAZ (baixado do email ou do site).
    Extrai texto de todas as paginas e passa pro parser de texto.

    FALLBACK OCR: Se o PDF e baseado em imagem (scan/foto) e o pdfplumber
    nao extrai texto, tenta converter para imagem e rodar Tesseract OCR.

    Returns:
        ConsultaMDFe com dados extraidos, ou None se o PDF nao pode ser lido
        (corrompido, EOF inesperado, etc). None significa FALHA - nao confundir
        com ConsultaMDFe(total_termos=0) que significa "0 termos confirmado".
    """
    texto_completo = ""
    try:
        with pdfplumber.open(caminho_pdf) as pdf:
            for page in pdf.pages:
                texto_pagina = page.extract_text() or ""
                texto_completo += texto_pagina + "\n"
    except Exception as e:
        logger.error(f"Erro ao ler PDF do relatorio: {e}")
        # Tenta OCR mesmo se pdfplumber falhou (pode ser imagem)
        texto_completo = _ocr_pdf(caminho_pdf)
        if not texto_completo:
            return None

    if not texto_completo.strip():
        # PDF abriu mas sem texto extraivel - provavelmente e uma imagem/scan
        logger.info("PDF sem texto extraivel - tentando OCR (Tesseract)...")
        texto_completo = _ocr_pdf(caminho_pdf)
        if not texto_completo:
            logger.error("PDF do relatorio esta vazio e OCR nao disponivel/falhou")
            return None
        logger.info(f"OCR extraiu {len(texto_completo)} caracteres do PDF")

    return parsear_relatorio_sefaz(texto_completo)


def _ocr_pdf(caminho_pdf: str) -> str:
    """
    Converte PDF em imagem e roda OCR (Tesseract) para extrair texto.
    Usado como fallback quando o PDF e baseado em imagem (scan/foto da SEFAZ).

    Requer:
    - Tesseract instalado (tesseract-ocr)
    - Poppler instalado (poppler/bin no PATH ou configurado)
    - Libs: pytesseract, pdf2image

    Returns:
        Texto extraido via OCR, ou string vazia se falhar/indisponivel
    """
    try:
        import pytesseract
        from pdf2image import convert_from_path
    except ImportError as e:
        logger.error(f"OCR indisponivel (dependencia nao instalada): {e}")
        return ""

    try:
        from config import config

        # Configura caminho do Tesseract (auto-detecta se nao configurado)
        tesseract_cmd = config.ocr.tesseract_cmd
        if not tesseract_cmd:
            tesseract_cmd = _detectar_tesseract()
        if tesseract_cmd:
            pytesseract.pytesseract.tesseract_cmd = tesseract_cmd
        else:
            logger.error("OCR: Tesseract nao encontrado no sistema")
            return ""

        # Configura caminho do Poppler (necessario pro pdf2image no Windows)
        poppler_path = config.ocr.poppler_path
        if not poppler_path:
            poppler_path = _detectar_poppler()

        if not poppler_path:
            logger.error("OCR: Poppler nao encontrado no sistema (necessario pra converter PDF em imagem)")
            return ""

        idioma = config.ocr.idioma or "por"
        dpi = config.ocr.dpi or 300

        logger.debug(f"OCR: Tesseract={tesseract_cmd}, Poppler={poppler_path}, Idioma={idioma}, DPI={dpi}")

    except Exception as e:
        # Se config falhar, usa defaults
        tesseract_cmd = _detectar_tesseract()
        if not tesseract_cmd:
            logger.warning(f"OCR: Config falhou e Tesseract nao encontrado: {e}")
            return ""
        pytesseract.pytesseract.tesseract_cmd = tesseract_cmd
        poppler_path = _detectar_poppler()
        if not poppler_path:
            logger.warning(f"OCR: Config falhou e Poppler nao encontrado: {e}")
            return ""
        idioma = "por"
        dpi = 300

    try:
        # Converte PDF em imagens (uma por pagina)
        kwargs = {"dpi": dpi}
        if poppler_path:
            kwargs["poppler_path"] = poppler_path

        logger.info(f"OCR: Convertendo PDF em imagem (poppler={poppler_path}, dpi={dpi})...")
        imagens = convert_from_path(caminho_pdf, **kwargs)

        if not imagens:
            logger.warning("OCR: PDF nao gerou nenhuma imagem")
            return ""

        # Roda OCR em cada pagina (com pre-processamento pra melhorar precisao)
        texto_total = ""
        for i, img in enumerate(imagens):
            try:
                # Pre-processamento: melhora qualidade pra OCR
                img_processada = _preprocessar_imagem_ocr(img)

                # OCR com config otimizada pra documentos tabulares
                config_ocr = "--psm 6 --oem 3"
                texto_pagina = pytesseract.image_to_string(
                    img_processada, lang=idioma, config=config_ocr
                )
                texto_total += texto_pagina + "\n"
            except Exception as e:
                logger.warning(f"OCR: Erro na pagina {i+1}: {e}")
                continue

        texto_total = texto_total.strip()

        if texto_total:
            logger.info(f"OCR: Extraido texto de {len(imagens)} pagina(s) ({len(texto_total)} chars)")
        else:
            logger.warning("OCR: Nenhum texto extraido das imagens")

        return texto_total

    except Exception as e:
        logger.error(f"OCR falhou: {type(e).__name__}: {e}")
        return ""


def _preprocessar_imagem_ocr(img):
    """
    Pre-processa imagem do PDF antes do OCR pra melhorar precisao.
    
    Tecnicas aplicadas:
    1. Escala de cinza (remove cor que confunde o OCR)
    2. Upscale 2x (mais pixels = mais detalhes nos digitos)
    3. Aumenta contraste (texto mais nitido)
    4. Binarizacao (preto/branco puro — remove tons intermediarios)
    5. Sharpening (acentua bordas dos caracteres)
    
    Isso resolve erros comuns como 3/5, 0/8, 1/7 em fontes pequenas.
    """
    from PIL import ImageEnhance, ImageFilter

    # 1. Converte pra escala de cinza
    img_gray = img.convert("L")

    # 2. Upscale 2x (melhora resolucao efetiva)
    largura, altura = img_gray.size
    img_grande = img_gray.resize((largura * 2, altura * 2), resample=3)  # LANCZOS

    # 3. Aumenta contraste (fator 2.0 = dobro do contraste)
    enhancer = ImageEnhance.Contrast(img_grande)
    img_contraste = enhancer.enhance(2.0)

    # 4. Aumenta nitidez (sharpening)
    enhancer = ImageEnhance.Sharpness(img_contraste)
    img_nitida = enhancer.enhance(2.0)

    # 5. Binarizacao (threshold): tudo acima de 180 vira branco, abaixo vira preto
    img_bin = img_nitida.point(lambda x: 255 if x > 180 else 0, mode="1")

    # Converte de volta pra "L" (grayscale) pro Tesseract processar melhor
    img_final = img_bin.convert("L")

    return img_final


def _detectar_tesseract() -> str:
    """
    Auto-detecta o caminho do Tesseract no Windows.
    Locais comuns de instalacao.
    """
    import shutil

    # Verifica se esta no PATH
    tesseract_path = shutil.which("tesseract")
    if tesseract_path:
        return tesseract_path

    # Locais comuns no Windows
    caminhos_comuns = [
        r"C:\Program Files\Tesseract-OCR\tesseract.exe",
        r"C:\Program Files (x86)\Tesseract-OCR\tesseract.exe",
        r"C:\Users\{}\AppData\Local\Programs\Tesseract-OCR\tesseract.exe".format(
            os.environ.get("USERNAME", "")
        ),
    ]

    for caminho in caminhos_comuns:
        if os.path.isfile(caminho):
            return caminho

    return ""


def _detectar_poppler() -> str:
    """
    Auto-detecta o caminho do Poppler no Windows.
    Necessario para pdf2image converter PDF em imagem.
    """
    import shutil
    import glob

    # Verifica se pdftoppm esta no PATH (indica Poppler instalado)
    if shutil.which("pdftoppm"):
        return ""  # Nao precisa especificar, ja esta no PATH

    # Locais comuns no Windows (inclui versoes com numero)
    caminhos_comuns = [
        r"C:\Program Files\poppler\Library\bin",
        r"C:\Program Files\poppler\bin",
        r"C:\poppler\Library\bin",
        r"C:\poppler\bin",
        r"C:\tools\poppler\Library\bin",
        r"C:\Users\{}\poppler\Library\bin".format(os.environ.get("USERNAME", "")),
        r"C:\Users\{}\scoop\apps\poppler\current\Library\bin".format(
            os.environ.get("USERNAME", "")
        ),
    ]

    # Adiciona caminhos com versao (poppler-XX.XX.X)
    for pattern in [r"C:\Program Files\poppler-*\Library\bin",
                    r"C:\Program Files\poppler-*\bin",
                    r"C:\poppler-*\Library\bin",
                    r"C:\poppler-*\bin"]:
        caminhos_comuns.extend(glob.glob(pattern))

    for caminho in caminhos_comuns:
        if os.path.isdir(caminho):
            # Verifica se tem pdftoppm.exe dentro
            if os.path.isfile(os.path.join(caminho, "pdftoppm.exe")):
                return caminho

    return ""


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
        # NOTA: "DOMICILIO" = ENTREGA (nunca libera)
        if "- RETIRA" in linha_upper and ("PRODUTO" in linha_upper or "TOTAL" not in linha_upper):
            # Confirma que nao e "ENTREGA" disfarçado
            if "ENTREGA" not in linha_upper and "DOMICILIO" not in linha_upper:
                tipo_secao_atual = "RETIRA"
                continue
        if "- ENTREGA" in linha_upper and ("PRODUTO" in linha_upper or "TOTAL" not in linha_upper):
            tipo_secao_atual = "ENTREGA"
            continue
        if "DOMICILIO" in linha_upper and ("PRODUTO" in linha_upper or "-" in linha_upper):
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

def _detectar_resposta_email_regex(texto_email: str) -> str:
    """
    Classificacao via regex (rapida, mas fragil).
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
    # NOTA: "relatorio anexo" / "foi gerado o relatorio" NAO significam que
    # TEM termos — o PDF pode ter 0 termos. Esses indicadores sao tratados
    # como "tem PDF pra analisar" (via verificacao de anexo no Outlook).
    # Aqui so ficam indicadores que REALMENTE confirmam termos.
    indicadores_com_termos = [
        "TERMO DE APREEN",
        "COM PEND",
        "RETEN" + chr(199) + chr(195) + "O",  # RETENÇÃO
        "RETENCAO",
        "RETENÇÃO",
        "TERMOS EMITIDOS",
        "TERMOS DE AVERIGUA",
    ]

    for indicador in indicadores_sem_termos:
        if indicador in texto_upper:
            return "sem_termos"

    for indicador in indicadores_com_termos:
        if indicador in texto_upper:
            return "com_termos"

    return "indefinido"


def detectar_resposta_email(texto_email: str) -> str:
    """
    Analisa o texto do email de resposta da SEFAZ.
    Usa regex como primeira tentativa (rapido). Se inconclusivo ou para validacao,
    consulta a IA local (Ollama) para classificacao mais inteligente.

    Retorna: "com_termos", "sem_termos", ou "indefinido"
    """
    from modules.ia_local import ia_local

    # Passo 1: regex (rapido, ~0ms)
    resultado_regex = _detectar_resposta_email_regex(texto_email)

    # Passo 2: IA complementa/valida (1-3s se Ollama disponivel)
    try:
        resultado_final = ia_local.classificar_email_com_consenso(
            texto_email, resultado_regex
        )
        return resultado_final
    except Exception as e:
        logger.warning(f"Erro na classificacao via IA: {e} - usando resultado regex")
        return resultado_regex
