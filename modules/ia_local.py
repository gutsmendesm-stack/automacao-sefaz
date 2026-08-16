"""
Modulo de IA local via Ollama (llama3.1:8b).
Substitui regex fragil por classificacao inteligente para:
- Detectar resposta de email da SEFAZ (com_termos / sem_termos / indefinido)
- Extrair termos de apreensao e CTes de texto de relatorio PDF da SEFAZ

O Ollama roda localmente em http://localhost:11434.
Modelo: llama3.1:8b (GPU RTX 5060, ~1-3s por resposta).
"""

import json
import logging
import re
import time
from typing import List, Optional, Tuple

import requests

from models.termo import (
    ConsultaMDFe,
    StatusMDFe,
    SituacaoTermo,
    TermoApreensao,
    TipoFielDepositario,
)

logger = logging.getLogger(__name__)

# Timeout para chamadas ao Ollama (segundos)
OLLAMA_TIMEOUT = 60


class OllamaIA:
    """
    Cliente para o Ollama rodando localmente.
    Usa a API /api/generate para inferencia com llama3.1:8b.
    Fallback: se GPU der OOM, muda pro modelo CPU (mais lento mas funciona).
    """

    def __init__(self, url: str = "http://localhost:11434", modelo: str = "llama3.1:8b"):
        self.url = url.rstrip("/")
        self.modelo = modelo
        self.modelo_cpu = modelo.split(":")[0] + ":8b-cpu"  # Ex: llama3.1:8b-cpu
        self._modelo_ativo = modelo  # Qual esta sendo usado agora
        self._disponivel: Optional[bool] = None
        self._usando_cpu = False

    # ============================================================
    # INFRAESTRUTURA
    # ============================================================

    def verificar_disponibilidade(self) -> bool:
        """
        Verifica se o Ollama esta rodando e o modelo esta disponivel.
        Respeita flag 'ativo' da config (se desativado, nem tenta).
        Cacheia o resultado para nao ficar pingando a cada chamada.
        """
        # Verifica se IA esta desativada na config
        try:
            from config import config
            if not config.ollama.ativo:
                logger.debug("Ollama desativado na config (ollama.ativo=False)")
                self._disponivel = False
                return False
        except Exception:
            pass

        try:
            resp = requests.get(f"{self.url}/api/tags", timeout=5)
            if resp.status_code != 200:
                self._disponivel = False
                return False

            modelos = resp.json().get("models", [])
            nomes = [m.get("name", "") for m in modelos]
            # Verifica se o modelo esta na lista (com ou sem tag :latest)
            modelo_encontrado = any(
                self.modelo in nome or nome.startswith(self.modelo.split(":")[0])
                for nome in nomes
            )

            if not modelo_encontrado:
                logger.warning(
                    f"Ollama rodando mas modelo '{self.modelo}' nao encontrado. "
                    f"Modelos disponiveis: {nomes}"
                )
                self._disponivel = False
                return False

            self._disponivel = True
            return True

        except requests.ConnectionError:
            logger.warning("Ollama nao esta rodando (conexao recusada)")
            self._disponivel = False
            return False
        except Exception as e:
            logger.warning(f"Erro ao verificar Ollama: {e}")
            self._disponivel = False
            return False

    @property
    def disponivel(self) -> bool:
        """Retorna se o Ollama esta disponivel (verifica na primeira vez)."""
        if self._disponivel is None:
            self.verificar_disponibilidade()
        return self._disponivel

    def _gerar(self, prompt: str, system: str = "", temperature: float = 0.1) -> str:
        """
        Chama a API /api/generate do Ollama.
        Retorna o texto gerado ou string vazia se falhar.
        Se GPU der OOM, tenta automaticamente com modelo CPU.

        Args:
            prompt: Texto do usuario/pergunta
            system: Instrucao de sistema (define comportamento do modelo)
            temperature: Criatividade (0.0-1.0). Baixo = mais deterministico.
        """
        payload = {
            "model": self._modelo_ativo,
            "prompt": prompt,
            "stream": False,
            "options": {
                "temperature": temperature,
                "num_predict": 2048,
            },
        }
        # Se usando CPU, reduz contexto pra caber na RAM
        if self._usando_cpu:
            payload["options"]["num_ctx"] = 2048

        if system:
            payload["system"] = system

        try:
            inicio = time.time()
            resp = requests.post(
                f"{self.url}/api/generate",
                json=payload,
                timeout=OLLAMA_TIMEOUT,
            )
            duracao = time.time() - inicio

            if resp.status_code != 200:
                # Se OOM (out of memory), tenta fallback CPU
                if resp.status_code == 500 and "out of memory" in resp.text.lower():
                    if not self._usando_cpu:
                        return self._fallback_cpu(prompt, system, temperature)
                    else:
                        # Ja estava em CPU e falhou — desabilita
                        logger.error("Ollama: Falhou em CPU tambem. IA desabilitada.")
                        self._disponivel = False
                        return ""

                logger.error(f"Ollama retornou status {resp.status_code}: {resp.text[:200]}")
                return ""

            resultado = resp.json().get("response", "").strip()
            modo = "CPU" if self._usando_cpu else "GPU"
            logger.debug(f"Ollama ({modo}) respondeu em {duracao:.1f}s ({len(resultado)} chars)")
            return resultado

        except requests.Timeout:
            logger.error(f"Ollama timeout ({OLLAMA_TIMEOUT}s) - modelo pode estar carregando")
            return ""
        except requests.ConnectionError:
            logger.error("Ollama nao esta rodando (conexao recusada)")
            self._disponivel = False
            return ""
        except Exception as e:
            logger.error(f"Erro na chamada ao Ollama: {e}")
            return ""

    def _fallback_cpu(self, prompt: str, system: str, temperature: float) -> str:
        """
        Fallback: muda pro modelo CPU (num_gpu=0) quando GPU da OOM.
        O modelo CPU usa RAM do sistema (~5GB) — com 32GB nao trava.
        Usa num_ctx=2048 (contexto reduzido) pra nao estourar RAM.
        Respostas levam ~8-15s em vez de 1-3s, mas funciona.
        """
        logger.warning(
            "Ollama: GPU sem memoria (Chrome usando VRAM). "
            "Mudando para modelo CPU (mais lento mas funciona)..."
        )

        self._usando_cpu = True
        self._modelo_ativo = self.modelo_cpu

        payload = {
            "model": self._modelo_ativo,
            "prompt": prompt,
            "stream": False,
            "options": {
                "temperature": temperature,
                "num_predict": 2048,
                "num_ctx": 2048,  # Contexto reduzido pra caber na RAM
            },
        }
        if system:
            payload["system"] = system

        try:
            inicio = time.time()
            resp = requests.post(
                f"{self.url}/api/generate",
                json=payload,
                timeout=OLLAMA_TIMEOUT * 2,  # CPU e mais lento, dobra timeout
            )
            duracao = time.time() - inicio

            if resp.status_code != 200:
                logger.error(f"Ollama CPU falhou: {resp.status_code} - {resp.text[:200]}")
                self._disponivel = False
                return ""

            resultado = resp.json().get("response", "").strip()
            logger.info(f"Ollama (CPU) respondeu em {duracao:.1f}s ({len(resultado)} chars)")
            return resultado

        except requests.Timeout:
            logger.error(f"Ollama CPU timeout ({OLLAMA_TIMEOUT * 2}s)")
            self._disponivel = False
            return ""
        except Exception as e:
            logger.error(f"Ollama CPU erro: {e}")
            self._disponivel = False
            return ""

    # ============================================================
    # CLASSIFICACAO DE EMAIL DA SEFAZ
    # ============================================================

    def classificar_email(self, texto_email: str) -> str:
        """
        Classifica o email de resposta da SEFAZ usando IA.
        Substitui a funcao detectar_resposta_email() baseada em regex.

        Args:
            texto_email: Texto completo do corpo do email

        Returns:
            "com_termos" - email indica que ha termos de apreensao (PDF anexo)
            "sem_termos" - email indica que voo esta liberado
            "indefinido" - nao foi possivel classificar
        """
        if not self.disponivel:
            logger.warning("Ollama indisponivel - usando fallback regex")
            return ""

        # Limita texto para nao estourar contexto (emails sao curtos)
        texto_truncado = texto_email[:3000]

        system_prompt = (
            "Voce e um assistente que classifica emails da SEFAZ-AL (Secretaria da Fazenda de Alagoas) "
            "sobre fiscalizacao de cargas aereas.\n\n"
            "A SEFAZ responde emails sobre analise de MDF-e (Manifesto de Documentos Fiscais eletronico). "
            "As respostas podem indicar:\n"
            "1. COM_TERMOS: ha termos de apreensao/retencao. Palavras-chave: 'relatorio anexo', "
            "'gerado o relatorio', 'termo de apreensao', 'com pendencias', 'retencao'.\n"
            "2. SEM_TERMOS: voo liberado, sem retencao. Palavras-chave: 'sem retencao', "
            "'liberado', 'sem pendencias', 'sem termos', 'nenhum termo', 'regular'.\n"
            "3. INDEFINIDO: nao e possivel determinar (email nao e sobre isso, ou e ambiguo).\n\n"
            "Responda APENAS com uma dessas tres palavras: COM_TERMOS, SEM_TERMOS, ou INDEFINIDO.\n"
            "Nao explique. Nao adicione nada alem da classificacao."
        )

        prompt = f"Classifique este email da SEFAZ:\n\n{texto_truncado}"

        resposta = self._gerar(prompt, system=system_prompt, temperature=0.0)

        if not resposta:
            return ""

        # Normaliza a resposta
        resposta_upper = resposta.strip().upper()

        if "COM_TERMOS" in resposta_upper or "COM TERMOS" in resposta_upper:
            logger.info("IA classificou email: COM_TERMOS")
            return "com_termos"
        elif "SEM_TERMOS" in resposta_upper or "SEM TERMOS" in resposta_upper:
            logger.info("IA classificou email: SEM_TERMOS")
            return "sem_termos"
        else:
            logger.info(f"IA classificou email: INDEFINIDO (resposta raw: '{resposta[:50]}')")
            return "indefinido"

    # ============================================================
    # EXTRACAO DE TERMOS/CTEs DO RELATORIO SEFAZ
    # ============================================================

    def extrair_termos_relatorio(self, texto_relatorio: str) -> Optional[ConsultaMDFe]:
        """
        Usa IA para extrair termos de apreensao e CTes do texto do relatorio SEFAZ.
        Complementa o parser regex — util quando o formato muda ou e ambiguo.

        Args:
            texto_relatorio: Texto extraido do PDF/pagina do relatorio SEFAZ

        Returns:
            ConsultaMDFe com termos extraidos, ou None se IA falhar
        """
        if not self.disponivel:
            logger.warning("Ollama indisponivel - nao pode extrair termos via IA")
            return None

        # Limita texto (relatorios podem ser longos mas a info relevante esta no inicio)
        texto_truncado = texto_relatorio[:6000]

        system_prompt = (
            "Voce e um extrator de dados de relatorios da SEFAZ-AL sobre fiscalizacao de cargas.\n\n"
            "O relatorio contem informacoes sobre Termos de Apreensao (TA/TADe) emitidos "
            "contra cargas de um MDF-e (Manifesto de Documentos Fiscais eletronico).\n\n"
            "Extraia os seguintes dados em formato JSON:\n"
            "{\n"
            '  "chave_mdfe": "chave de 44 digitos se encontrar",\n'
            '  "total_termos": numero_inteiro,\n'
            '  "status": "com_pendencias" ou "sem_pendencias" ou "em_analise",\n'
            '  "termos": [\n'
            "    {\n"
            '      "numero": "numero do termo (7 digitos)",\n'
            '      "cte": "numero do CTe associado",\n'
            '      "nfe": "numero da NF-e se houver",\n'
            '      "situacao": "pendente" ou "regularizado",\n'
            '      "data": "DD/MM/AAAA se disponivel"\n'
            "    }\n"
            "  ]\n"
            "}\n\n"
            "REGRAS:\n"
            "- Se o relatorio diz 'TOTAL DE TERMOS 0' ou 'nao foram encontrados termos', "
            "retorne total_termos=0 e termos=[] (lista vazia).\n"
            "- Numeros de termo tem 7 digitos (ex: 2410845).\n"
            "- CTe tem 6-7 digitos (ex: 7398054).\n"
            "- Um mesmo termo pode estar associado a MULTIPLOS CTes.\n"
            "- Se um termo aparece com multiplos CTes, crie uma entrada para CADA par termo-cte.\n"
            "- Responda APENAS com o JSON, sem explicacoes ou markdown."
        )

        prompt = f"Extraia os termos de apreensao deste relatorio da SEFAZ:\n\n{texto_truncado}"

        resposta = self._gerar(prompt, system=system_prompt, temperature=0.0)

        if not resposta:
            return None

        # Tenta parsear o JSON da resposta
        return self._parsear_resposta_termos(resposta, texto_relatorio)

    def _parsear_resposta_termos(self, resposta_ia: str, texto_original: str) -> Optional[ConsultaMDFe]:
        """
        Parseia a resposta JSON da IA e converte para ConsultaMDFe.
        Trata casos de JSON malformado com fallbacks.
        """
        # Limpa resposta: remove markdown code blocks se houver
        resposta_limpa = resposta_ia.strip()
        if resposta_limpa.startswith("```"):
            # Remove ```json e ``` final
            linhas = resposta_limpa.split("\n")
            linhas = [l for l in linhas if not l.strip().startswith("```")]
            resposta_limpa = "\n".join(linhas)

        # Tenta encontrar JSON na resposta
        # Procura pelo primeiro { e ultimo }
        inicio_json = resposta_limpa.find("{")
        fim_json = resposta_limpa.rfind("}")

        if inicio_json == -1 or fim_json == -1:
            logger.warning(f"IA nao retornou JSON valido: '{resposta_ia[:100]}'")
            return None

        json_str = resposta_limpa[inicio_json:fim_json + 1]

        try:
            dados = json.loads(json_str)
        except json.JSONDecodeError as e:
            logger.warning(f"JSON invalido da IA: {e} - resposta: '{json_str[:200]}'")
            return None

        # Converte para ConsultaMDFe
        chave = dados.get("chave_mdfe", "")
        if not chave:
            # Tenta extrair do texto original
            match_chave = re.search(r'\b(\d{44})\b', texto_original)
            chave = match_chave.group(1) if match_chave else ""

        total_termos = int(dados.get("total_termos", 0))

        # Mapeia status
        status_str = dados.get("status", "").lower()
        if "com_pend" in status_str or "pendencia" in status_str:
            status = StatusMDFe.ANALISADO_COM_PENDENCIAS
        elif "sem_pend" in status_str or "sem pendencia" in status_str:
            status = StatusMDFe.ANALISADO_SEM_PENDENCIAS
        elif "em_an" in status_str or "analise" in status_str:
            status = StatusMDFe.EM_ANALISE
        else:
            status = StatusMDFe.DESCONHECIDO

        # Extrai termos
        termos_lista = dados.get("termos", [])
        termos = []

        for t in termos_lista:
            if not isinstance(t, dict):
                continue

            numero = str(t.get("numero", "")).strip()
            if not numero or not re.match(r'^\d{6,8}$', numero):
                continue

            cte = str(t.get("cte", "")).strip()
            nfe = str(t.get("nfe", "")).strip()
            # Limpa valores nulos/None vindos do JSON
            if cte.lower() in ("none", "null", ""):
                cte = ""
            if nfe.lower() in ("none", "null", ""):
                nfe = ""

            situacao_str = str(t.get("situacao", "")).lower()
            if "pendente" in situacao_str:
                situacao = SituacaoTermo.PENDENTE
            elif "regulariz" in situacao_str:
                situacao = SituacaoTermo.REGULARIZADO
            else:
                situacao = SituacaoTermo.DESCONHECIDO

            data = str(t.get("data", "")).strip()
            if data.lower() in ("none", "null", ""):
                data = ""

            termo = TermoApreensao(
                numero=numero,
                cte=cte,
                nfe=nfe,
                situacao=situacao,
                data_emissao=data,
                tipo_fiel=TipoFielDepositario.TRANSPORTADORA,
            )
            termos.append(termo)

        # Valida: se IA diz 0 termos mas retornou lista, confia no total
        if total_termos == 0 and termos:
            logger.warning(
                f"IA diz total_termos=0 mas retornou {len(termos)} termos. "
                f"Usando total_termos=0 (confia na IA)."
            )
            termos = []

        consulta = ConsultaMDFe(
            chave=chave,
            status=status,
            total_termos=total_termos if total_termos > 0 else len(termos),
            termos=termos,
        )

        logger.info(
            f"IA extraiu: {len(termos)} termos | total declarado: {total_termos} | "
            f"status: {status.value}"
        )

        return consulta

    # ============================================================
    # METODO COMBINADO: REGEX + IA (CONSENSO)
    # ============================================================

    def classificar_email_com_consenso(
        self, texto_email: str, resultado_regex: str
    ) -> str:
        """
        Combina resultado do regex com a classificacao da IA.
        PRIORIDADE: SEGURANCA. Nunca libera indevidamente.

        Logica de seguranca:
        - Regex diz "com_termos" → MANTEM "com_termos" (nunca relaxa pra liberar)
          Mesmo se IA disser "sem_termos" ou "indefinido", nao arrisca.
        - Regex diz "sem_termos" → consulta IA pra confirmar:
          - IA concorda "sem_termos" → libera com confianca (dupla validacao)
          - IA diz "com_termos" → usa "com_termos" (mais seguro)
          - IA diz "indefinido" ou falha → mantem regex "sem_termos"
            (regex ja detectou palavras claras de liberacao)
        - Regex diz "indefinido" → IA decide sozinha

        Args:
            texto_email: Corpo do email
            resultado_regex: Resultado da funcao regex original

        Returns:
            Classificacao final: "com_termos", "sem_termos", ou "indefinido"
        """
        # REGRA 1: Regex diz COM TERMOS → nunca muda (seguranca maxima)
        if resultado_regex == "com_termos":
            # Consulta IA apenas pra log/auditoria, mas NAO muda resultado
            if self.disponivel:
                resultado_ia = self.classificar_email(texto_email)
                if resultado_ia and resultado_ia != resultado_regex:
                    logger.info(
                        f"Email: regex=com_termos, IA={resultado_ia} → "
                        f"mantendo com_termos (seguranca: nunca libera na duvida)"
                    )
            return "com_termos"

        # REGRA 2: Regex diz SEM TERMOS → IA confirma (dupla validacao pra liberar)
        if resultado_regex == "sem_termos":
            if not self.disponivel:
                return "sem_termos"

            resultado_ia = self.classificar_email(texto_email)
            if resultado_ia == "com_termos":
                # IA discorda! Mais seguro tratar como COM termos
                logger.warning(
                    f"Email: regex=sem_termos, IA=com_termos → "
                    f"usando com_termos (IA detectou risco, priorizando seguranca)"
                )
                return "com_termos"
            elif resultado_ia == "sem_termos":
                # Ambos concordam — libera com confianca
                logger.info("Email: regex=sem_termos, IA=sem_termos → CONFIRMADO (dupla validacao)")
                return "sem_termos"
            else:
                # IA indefinida ou falhou — regex ja detectou indicador claro, confia
                logger.info(
                    f"Email: regex=sem_termos, IA={resultado_ia or 'falhou'} → "
                    f"mantendo sem_termos (regex detectou indicador claro)"
                )
                return "sem_termos"

        # REGRA 3: Regex diz INDEFINIDO → IA decide
        if self.disponivel:
            resultado_ia = self.classificar_email(texto_email)
            if resultado_ia:
                logger.info(f"Email: regex=indefinido, IA={resultado_ia} → usando IA")
                return resultado_ia

        # IA tambem falhou — mantem indefinido
        return "indefinido"

    def extrair_termos_com_fallback(
        self, texto_relatorio: str, resultado_regex: Optional[ConsultaMDFe]
    ) -> ConsultaMDFe:
        """
        Complementa extracao de termos: usa regex como base e IA para preencher lacunas.

        Estrategia:
        - Regex encontrou termos COM CTes → confia no regex (preciso quando formata certo)
        - Regex encontrou termos SEM CTes → usa IA para tentar associar
        - Regex nao encontrou nada mas texto parece ter termos → IA tenta extrair
        - Total de termos declarado != encontrado → IA tenta completar

        Args:
            texto_relatorio: Texto do relatorio PDF/pagina
            resultado_regex: Resultado do parser regex (pode ser None)

        Returns:
            ConsultaMDFe final (regex, IA, ou combinacao)
        """
        # Se regex nao rodou/falhou, tenta IA direto
        if resultado_regex is None:
            resultado_ia = self.extrair_termos_relatorio(texto_relatorio)
            return resultado_ia or ConsultaMDFe()

        # Regex diz 0 termos E texto confirma → confia, nao precisa IA
        if resultado_regex.total_termos == 0 and not resultado_regex.termos:
            return resultado_regex

        # Regex encontrou termos — verifica se precisa complementar
        termos_regex = resultado_regex.termos
        termos_sem_cte = [t for t in termos_regex if not t.cte]

        precisa_ia = False
        motivo = ""

        # Caso 1: termos encontrados mas NENHUM tem CTe
        if termos_regex and all(not t.cte for t in termos_regex):
            precisa_ia = True
            motivo = "nenhum termo tem CTe associado"

        # Caso 2: total declarado no relatorio != quantidade encontrada
        elif resultado_regex.total_termos > len(termos_regex):
            precisa_ia = True
            motivo = (
                f"total declarado ({resultado_regex.total_termos}) > "
                f"encontrados ({len(termos_regex)})"
            )

        # Caso 3: mais de 50% dos termos sem CTe
        elif termos_regex and len(termos_sem_cte) > len(termos_regex) * 0.5:
            precisa_ia = True
            motivo = f"{len(termos_sem_cte)}/{len(termos_regex)} termos sem CTe"

        if not precisa_ia:
            # Regex suficiente
            return resultado_regex

        # Precisa da IA
        if not self.disponivel:
            logger.warning(f"IA indisponivel mas seria util ({motivo}). Usando regex.")
            return resultado_regex

        logger.info(f"Chamando IA para complementar parser: {motivo}")
        resultado_ia = self.extrair_termos_relatorio(texto_relatorio)

        if resultado_ia is None:
            # IA falhou, usa regex mesmo
            return resultado_regex

        # Combina: se IA encontrou mais termos ou tem CTes onde regex nao tem
        resultado_final = self._combinar_resultados(resultado_regex, resultado_ia)
        return resultado_final

    # ============================================================
    # DIAGNOSTICO DE ERROS (auto-recuperacao)
    # ============================================================

    def diagnosticar_erro(self, contexto: str, erro: str, html_visivel: str = "") -> dict:
        """
        Analisa um erro de automacao e sugere acao de recuperacao.
        Usa historico de erros similares (memoria) para dar respostas melhores.

        Args:
            contexto: O que estava fazendo (ex: "clicar botao Pesquisar na Retencao")
            erro: Mensagem de erro do Selenium/Python
            html_visivel: Texto visivel na pagina (opcional, ajuda no diagnostico)

        Returns:
            dict com:
                "acao": str - codigo da acao sugerida:
                    "fechar_modal" - fechar modais e tentar de novo
                    "aguardar" - esperar mais tempo (site carregando)
                    "renavegar" - navegar pra pagina novamente
                    "relogar" - sessao expirou, precisa login
                    "pular" - nao tem solucao, pular este item
                    "nenhuma" - IA nao soube diagnosticar
                "explicacao": str - o que a IA acha que aconteceu
                "tempo_espera": int - segundos pra aguardar (se acao=aguardar)
        """
        if not self.disponivel:
            return {"acao": "nenhuma", "explicacao": "IA indisponivel", "tempo_espera": 0}

        # Busca solucoes anteriores na memoria
        from modules.ia_memoria import memoria
        solucoes_anteriores = memoria.buscar_solucoes_para_erro(contexto)
        contexto_memoria = ""
        if solucoes_anteriores:
            contexto_memoria = (
                "\n\nHISTORICO: Erros similares anteriores foram resolvidos com:\n"
                + "\n".join(f"- {s}" for s in solucoes_anteriores[:3])
            )

        system_prompt = (
            "Voce e um diagnosticador de erros de automacao web (Selenium + Chrome).\n"
            "O programa automatiza sites (Nexlog, SEFAZ-AL, Outlook Web).\n\n"
            "Erros comuns e solucoes:\n"
            "- Modal/popup cobrindo a tela → fechar modal e tentar de novo\n"
            "- Elemento nao encontrado mas a pagina e correta → site carregando, aguardar\n"
            "- Pagina errada / URL diferente → navegar pra pagina correta\n"
            "- Botao de login aparecendo → sessao expirou, precisa relogar\n"
            "- Chromedriver crash / stale element → renavegar\n"
            "- 'nenhum registro' / item nao existe → pular (nao e erro real)\n\n"
            "Responda APENAS com JSON:\n"
            '{"acao": "fechar_modal|aguardar|renavegar|relogar|pular|nenhuma", '
            '"explicacao": "frase curta do diagnostico", "tempo_espera": N}\n'
            "Nao adicione nada alem do JSON."
        )

        texto_pagina = html_visivel[:2000] if html_visivel else "(nao disponivel)"
        prompt = (
            f"CONTEXTO: {contexto}\n"
            f"ERRO: {erro[:500]}\n"
            f"TEXTO VISIVEL NA PAGINA: {texto_pagina}\n"
            f"{contexto_memoria}\n\n"
            f"Diagnostique e sugira acao de recuperacao."
        )

        resposta = self._gerar(prompt, system=system_prompt, temperature=0.1)

        if not resposta:
            return {"acao": "nenhuma", "explicacao": "IA nao respondeu", "tempo_espera": 0}

        # Parseia JSON da resposta
        try:
            inicio = resposta.find("{")
            fim = resposta.rfind("}")
            if inicio >= 0 and fim > inicio:
                resultado = json.loads(resposta[inicio:fim + 1])
                acao = resultado.get("acao", "nenhuma")
                # Valida acao
                acoes_validas = {"fechar_modal", "aguardar", "renavegar", "relogar", "pular", "nenhuma"}
                if acao not in acoes_validas:
                    acao = "nenhuma"
                return {
                    "acao": acao,
                    "explicacao": resultado.get("explicacao", "")[:200],
                    "tempo_espera": min(int(resultado.get("tempo_espera", 0)), 30),
                }
        except (json.JSONDecodeError, ValueError):
            pass

        return {"acao": "nenhuma", "explicacao": resposta[:100], "tempo_espera": 0}

    # ============================================================
    # ISOLAMENTO DE RESPOSTA DE EMAIL (threads)
    # ============================================================

    def isolar_resposta_email(self, texto_completo: str) -> str:
        """
        Isola a resposta mais recente de uma thread de email.
        Remove citacoes de emails anteriores, assinaturas, headers repetidos.

        Util quando o Outlook retorna a thread inteira e o texto fica poluido
        com respostas anteriores que podem confundir a classificacao.

        Args:
            texto_completo: Texto bruto extraido do painel de leitura do Outlook

        Returns:
            Texto apenas da resposta mais recente (limpo)
        """
        if not self.disponivel:
            # Fallback: tenta separar por marcadores comuns de reply
            return self._isolar_resposta_regex(texto_completo)

        # So chama IA se o texto parece ter thread (longo ou com marcadores)
        if len(texto_completo) < 500:
            return texto_completo  # Email curto, provavelmente nao tem thread

        marcadores_thread = [
            "De:", "From:", "Enviado:", "Sent:", "Em ", "On ",
            "------", "______", "Encaminhada", "Forwarded",
        ]
        tem_thread = any(m in texto_completo for m in marcadores_thread)
        if not tem_thread:
            return texto_completo

        system_prompt = (
            "Voce recebe o texto completo de uma thread de email (com respostas anteriores).\n"
            "Sua tarefa: extrair APENAS a resposta mais recente (a primeira mensagem no topo).\n\n"
            "Remova:\n"
            "- Citacoes de emails anteriores (apos 'De:', 'From:', 'Enviado:', etc.)\n"
            "- Assinaturas longas (nome, cargo, telefone, disclaimer)\n"
            "- Headers repetidos (data, assunto, para, de)\n"
            "- Linhas separadoras (---, ___, etc.)\n\n"
            "Mantenha APENAS o corpo da resposta mais recente.\n"
            "Retorne o texto limpo, sem explicacoes."
        )

        prompt = f"Isole a resposta mais recente desta thread:\n\n{texto_completo[:4000]}"

        resposta = self._gerar(prompt, system=system_prompt, temperature=0.0)

        if resposta and len(resposta) > 20:
            logger.debug(f"IA isolou resposta: {len(texto_completo)} -> {len(resposta)} chars")
            return resposta

        # Fallback
        return self._isolar_resposta_regex(texto_completo)

    def _isolar_resposta_regex(self, texto: str) -> str:
        """Fallback: tenta isolar resposta por marcadores comuns."""
        linhas = texto.split("\n")
        resultado = []

        for linha in linhas:
            linha_strip = linha.strip()
            # Para quando encontra marcador de citacao
            if any(linha_strip.startswith(m) for m in ["De:", "From:", "Enviado:", "Sent:"]):
                break
            if linha_strip.startswith("------") or linha_strip.startswith("______"):
                break
            if re.match(r'^Em \d{2}/\d{2}/\d{4}', linha_strip):
                break
            if re.match(r'^On \d{2}/\d{2}/\d{4}', linha_strip):
                break
            resultado.append(linha)

        texto_isolado = "\n".join(resultado).strip()
        return texto_isolado if len(texto_isolado) > 20 else texto

    # ============================================================
    # VALIDACAO DE EXTRACAO (sanity check)
    # ============================================================

    def validar_extracao(self, termos: List[TermoApreensao], texto_original: str) -> List[TermoApreensao]:
        """
        Valida se os termos extraidos fazem sentido no contexto do relatorio.
        Remove falsos positivos (numeros que parecem termos mas nao sao).

        Args:
            termos: Lista de termos extraidos pelo regex
            texto_original: Texto do relatorio original

        Returns:
            Lista de termos validados (pode ser menor que a original)
        """
        if not termos:
            return termos

        if not self.disponivel:
            return termos  # Sem IA, retorna como esta

        # So valida se tem suspeita de falso positivo
        # (numero de termo igual a CTe/NF-e, ou numeros muito diferentes entre si)
        numeros_termos = [t.numero for t in termos]
        numeros_ctes = [t.cte for t in termos if t.cte]
        numeros_nfes = [t.nfe for t in termos if t.nfe]

        # Detecta sobreposicao (mesmo numero como termo E como cte/nfe)
        sobreposicao = set(numeros_termos) & (set(numeros_ctes) | set(numeros_nfes))
        if not sobreposicao and len(termos) <= 20:
            return termos  # Parece OK, nao precisa validar

        system_prompt = (
            "Voce valida dados extraidos de um relatorio da SEFAZ-AL.\n"
            "Recebe uma lista de 'termos de apreensao' extraidos e o texto original.\n\n"
            "Verifique:\n"
            "1. Os numeros de termo (7 digitos) sao realmente termos de apreensao?\n"
            "2. Algum 'termo' e na verdade um numero de CT-e ou NF-e confundido?\n"
            "3. O total faz sentido com o que o relatorio diz?\n\n"
            "Retorne JSON com os numeros de termos VALIDOS:\n"
            '{"termos_validos": ["2410845", "2410848"], "removidos": ["1234567"], '
            '"motivo_remocao": "era numero de CTe"}\n'
            "Se todos sao validos, retorne todos em termos_validos e removidos=[]."
        )

        termos_str = json.dumps([
            {"numero": t.numero, "cte": t.cte, "nfe": t.nfe}
            for t in termos
        ], ensure_ascii=False)

        prompt = (
            f"Termos extraidos: {termos_str}\n\n"
            f"Texto do relatorio (trecho):\n{texto_original[:3000]}\n\n"
            f"Valide quais sao termos reais."
        )

        resposta = self._gerar(prompt, system=system_prompt, temperature=0.0)

        if not resposta:
            return termos

        try:
            inicio = resposta.find("{")
            fim = resposta.rfind("}")
            if inicio >= 0 and fim > inicio:
                dados = json.loads(resposta[inicio:fim + 1])
                validos = set(str(v) for v in dados.get("termos_validos", []))
                removidos = dados.get("removidos", [])

                if removidos:
                    logger.info(f"IA removeu {len(removidos)} falsos positivos: {removidos}")
                    termos_filtrados = [t for t in termos if t.numero in validos]
                    if termos_filtrados:
                        return termos_filtrados

        except (json.JSONDecodeError, ValueError):
            pass

        return termos  # Se falhou, mantem original

    # ============================================================
    # COMENTARIOS INTELIGENTES
    # ============================================================

    def gerar_comentario_inteligente(self, termos: List[TermoApreensao], awb: str) -> str:
        """
        Gera comentario mais informativo para adicionar no AWB.
        Em vez do padrao fixo "RETIDO PELA SEFAZ TA 2410845", gera algo com mais contexto.

        Args:
            termos: Lista de termos associados a este AWB/CTe
            awb: Numero do AWB

        Returns:
            Texto do comentario (max ~100 chars pra caber no Nexlog)
        """
        if not termos:
            return ""

        # Se so tem 1 termo, formato simples mas com situacao
        if len(termos) == 1:
            t = termos[0]
            situacao = ""
            if t.situacao == SituacaoTermo.PENDENTE:
                situacao = " (Pendente)"
            elif t.situacao == SituacaoTermo.REGULARIZADO:
                situacao = " (Regularizado)"

            base = f"RETIDO PELA SEFAZ TA {t.numero}{situacao}"
            if t.nfe:
                base += f" NF-e {t.nfe}"
            return base[:120]

        # Multiplos termos — resumo compacto
        numeros = [f"TA {t.numero}" for t in termos]

        # Verifica se todos tem mesma situacao
        situacoes = set(t.situacao for t in termos)
        sufixo = ""
        if len(situacoes) == 1:
            sit = situacoes.pop()
            if sit == SituacaoTermo.PENDENTE:
                sufixo = " (todos Pendentes)"
            elif sit == SituacaoTermo.REGULARIZADO:
                sufixo = " (todos Regularizados)"

        comentario = "RETIDO PELA SEFAZ " + ", ".join(numeros) + sufixo
        return comentario[:120]

    # ============================================================
    # RESUMO DE EXECUCAO
    # ============================================================

    def gerar_resumo_execucao(self, dados_execucao: dict) -> str:
        """
        Gera resumo em linguagem natural da execucao completa.
        Chamado ao final do processamento de todos os voos.

        Args:
            dados_execucao: Dict com dados consolidados:
                {
                    "total_voos": int,
                    "voos_processados": [{"voo": str, "termos": int, "liberados": int, ...}],
                    "total_liberados": int,
                    "total_retidos": int,
                    "total_erros": int,
                    "alertas": [str],
                    "tempo_total_segundos": int,
                }

        Returns:
            Texto de resumo (3-5 linhas, linguagem natural)
        """
        if not self.disponivel:
            # Fallback: resumo simples sem IA
            return self._resumo_simples(dados_execucao)

        system_prompt = (
            "Voce gera resumos curtos (3-5 linhas) de execucoes de automacao de cargas aereas.\n"
            "O programa processa voos, verifica termos de apreensao da SEFAZ, e libera cargas.\n\n"
            "Gere um resumo em portugues, informal mas preciso. Destaque:\n"
            "- Quantos voos processados e se tiveram problemas\n"
            "- Quantas cargas liberadas vs retidas\n"
            "- Alertas importantes (se houver)\n"
            "- Tempo de execucao\n"
            "- Padroes notaveis (ex: 'todos voos de GRU tinham termos')\n\n"
            "Seja direto, sem formalidades. Max 5 linhas."
        )

        prompt = f"Dados da execucao:\n{json.dumps(dados_execucao, ensure_ascii=False, indent=2)}"

        resposta = self._gerar(prompt, system=system_prompt, temperature=0.3)

        if resposta and len(resposta) > 20:
            return resposta

        return self._resumo_simples(dados_execucao)

    def _resumo_simples(self, dados: dict) -> str:
        """Resumo sem IA (fallback)."""
        total = dados.get("total_voos", 0)
        liberados = dados.get("total_liberados", 0)
        retidos = dados.get("total_retidos", 0)
        erros = dados.get("total_erros", 0)
        tempo = dados.get("tempo_total_segundos", 0)
        alertas = dados.get("alertas", [])

        minutos = tempo // 60
        linhas = [
            f"{total} voo(s) processado(s) em {minutos} min.",
            f"Liberados: {liberados} AWBs | Retidos: {retidos} AWBs | Erros: {erros}",
        ]
        if alertas:
            linhas.append(f"ALERTAS: {len(alertas)} AWB(s) com termo nao retidos!")

        return "\n".join(linhas)

    def _combinar_resultados(
        self, regex: ConsultaMDFe, ia: ConsultaMDFe
    ) -> ConsultaMDFe:
        """
        Combina resultados do regex e da IA de forma inteligente.
        Prioriza dados mais completos.
        """
        # Se IA encontrou 0 termos e regex encontrou > 0, confia no regex
        # (IA pode ter interpretado errado, regex pega numeros literais)
        if not ia.termos and regex.termos:
            logger.info("IA nao encontrou termos mas regex sim - mantendo regex")
            return regex

        # Se regex tem mais termos que IA, mantemos regex como base
        # mas preenchemos CTes faltantes com dados da IA
        if len(regex.termos) >= len(ia.termos):
            base = regex
            complemento = ia
        else:
            # IA encontrou mais - usa IA como base
            base = ia
            complemento = regex

        # Tenta preencher CTes faltantes nos termos da base
        termos_finais = list(base.termos)
        ctes_ia = {t.numero: t.cte for t in ia.termos if t.cte}

        for termo in termos_finais:
            if not termo.cte and termo.numero in ctes_ia:
                termo.cte = ctes_ia[termo.numero]
                logger.debug(f"Preencheu CTe do termo {termo.numero} via IA: {termo.cte}")

        resultado = ConsultaMDFe(
            chave=base.chave or regex.chave,
            numero_mdfe=base.numero_mdfe or regex.numero_mdfe,
            data_emissao=base.data_emissao or regex.data_emissao,
            emitente=base.emitente or regex.emitente,
            cnpj_emitente=base.cnpj_emitente or regex.cnpj_emitente,
            status=base.status if base.status != StatusMDFe.DESCONHECIDO else regex.status,
            total_termos=max(base.total_termos, regex.total_termos, len(termos_finais)),
            termos=termos_finais,
        )

        logger.info(
            f"Resultado combinado: {len(termos_finais)} termos "
            f"(regex: {len(regex.termos)}, IA: {len(ia.termos)})"
        )

        return resultado


# ============================================================
# INSTANCIA GLOBAL (singleton)
# ============================================================

def _criar_instancia() -> OllamaIA:
    """Cria instancia do OllamaIA usando config centralizada."""
    try:
        from config import config
        return OllamaIA(
            url=config.ollama.url,
            modelo=config.ollama.modelo,
        )
    except Exception:
        # Fallback: valores padrao se config nao carregou
        return OllamaIA()


ia_local = _criar_instancia()
