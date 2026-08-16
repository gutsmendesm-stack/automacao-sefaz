# Historico de Progresso

Registro de tudo que foi feito no projeto, em ordem cronologica.

---

## 2026-07-22 — Sessao 1: Setup Inicial

### Repositorio criado
- Migrado de `hugo2342233/nexlog` (branch `feature/automacao-sefaz`) para `gutsmendesm-stack/automacao-sefaz`
- Estrutura reorganizada: modulos em `modules/`, modelos em `models/`, testes em `tests/`, scripts antigos em `scripts_legados/`
- README.md e .gitignore configurados

### Aba Uncleared
- Criada aba "Uncleared" no app pra limpeza e comparacao de AWBs
- Funcionalidades: colar AWBs, limpar (11 digitos), deduplicar, importar planilha, comparar
- Planilha filtrada automaticamente por BasePosse=MCZ e RetiraEntrega=RETIRA
- Resultado mostra status operacional de cada AWB e flag [FRAP]
- Salvar resultado em .xlsx ou copiar pra clipboard

### Correcoes de bugs
- **Email com PDF 0 termos**: antes tratava como "inconsistente" e ia pro site SEFAZ. Agora reconhece como voo LIBERADO (confiavel)
- **SEFAZ TAs emitidos = 0**: detecta direto na tabela sem clicar "Imprimir Relatorio" (evita travamento do site)
- **Timeout SEFAZ**: aumentado de 15s para 100s no Imprimir Relatorio. Se nao extrair, NAO libera o voo (seguranca). Sem retry pra evitar loop.

### Tentativa de otimizacao de velocidade
- Tentou substituir `time.sleep()` por `WebDriverWait` inteligente
- **REVERTIDO**: o Nexlog precisa dos sleeps fixos porque DataTables/jQuery bloqueia inputs temporariamente de forma que o Selenium nao detecta
- Aprendizado: NAO mexer nos sleeps do Nexlog sem teste extensivo

### Documentacao de contexto
- Criados arquivos `.kiro/steering/` pra sessoes futuras:
  - `contexto-projeto.md` — quem e o usuario, o que faz, stack
  - `regras-negocio.md` — regras de liberacao, uncleared, seguranca
  - `regras-tecnicas.md` — restricoes do Nexlog/SEFAZ, padrao de codigo

---

## 2026-07-24 — Sessao 1 (continuacao): IA Local

### Decisao: IA local com Ollama
- PC do Gustavo: Ryzen 7 5700X, 32GB RAM, RTX 5060 8GB
- Modelo escolhido: Llama 3.1 8B (roda na GPU, ~5GB VRAM, 1-3s por resposta)
- Objetivo: substituir regex fragil por IA local pra:
  - Classificar emails (com termos / sem termos / indefinido)
  - Extrair termos/CTes de PDFs da SEFAZ
- Status: **EM ANDAMENTO** — instalacao do Ollama

---

## 2026-07-24 — Sessao 2: Integracao IA Local (Ollama)

### Modulo `modules/ia_local.py` criado
- Classe `OllamaIA` com cliente HTTP para Ollama (API REST, sem lib extra)
- Endpoint: `http://localhost:11434` | Modelo: `llama3.1:8b`
- Funcionalidades:
  - `classificar_email()` — classifica email SEFAZ via IA (com_termos / sem_termos / indefinido)
  - `extrair_termos_relatorio()` — extrai termos/CTes de texto do relatorio PDF via IA (retorna JSON estruturado)
  - `classificar_email_com_consenso()` — combina regex + IA (regex rapido, IA quando indefinido ou para validar)
  - `extrair_termos_com_fallback()` — regex como base, IA complementa quando termos sem CTe ou total inconsistente
  - `verificar_disponibilidade()` — verifica se Ollama roda e modelo esta carregado
- Instancia global `ia_local` (singleton) inicializada a partir da config

### `modules/parser.py` atualizado
- `detectar_resposta_email()` agora usa regex primeiro → se indefinido, chama IA via `classificar_email_com_consenso()`
- `parsear_relatorio_sefaz()` agora usa regex primeiro → complementa com IA via `extrair_termos_com_fallback()` se necessario
- Funcao regex original preservada como `_detectar_resposta_email_regex()` (usada internamente)
- Fallback completo: se IA indisponivel ou falhar, resultado regex e usado normalmente

### `config.py` atualizado
- Nova dataclass `ConfigOllama` (url, modelo, ativo, timeout)
- Integrado no `Config`, `salvar()` e `carregar()`
- Flag `ativo` permite desligar IA sem remover Ollama

### Decisoes tecnicas
- Sem dependencia nova (usa `requests` que ja existia no projeto)
- Imports locais em `parser.py` (`from modules.ia_local import ia_local`) para evitar circular imports
- Temperature 0.0 para classificacao (deterministico)
- Texto truncado antes de enviar ao Ollama (3000 chars email, 6000 chars relatorio)
- IA tem prioridade sobre regex em caso de discordancia (entende contexto melhor)

---

---

## 2026-07-24 — Sessao 3: IA Inteligente + Memoria

### Sistema de Memoria Persistente (`modules/ia_memoria.py`)
- Classe `MemoriaIA` com persistencia em JSON (`%APPDATA%/automacao_sefaz/memoria_ia.json`)
- Registra: erros (resolvidos/nao), classificacoes de email, extracoes de termos, padroes de voos, execucoes
- Busca por similaridade (palavras-chave) para encontrar solucoes anteriores
- Analise de padroes: identifica rotas problematicas (ex: GRU/MCZ sempre com termos)
- Estatisticas: taxa de acerto IA vs regex, erros mais frequentes
- Limpeza automatica de registros >90 dias
- Instancia global `memoria`

### Novas funcoes no `modules/ia_local.py`
- `diagnosticar_erro(contexto, erro, html_visivel)` — analisa erro de automacao e sugere acao de recuperacao (fechar_modal, aguardar, renavegar, relogar, pular). Usa historico da memoria pra dar respostas melhores.
- `isolar_resposta_email(texto)` — separa resposta mais recente de threads de email (remove citacoes, assinaturas, headers). Fallback regex se IA indisponivel.
- `validar_extracao(termos, texto)` — sanity check: confirma que numeros de 7 digitos sao realmente termos (nao CTes/NF-es confundidos). Remove falsos positivos.
- `gerar_comentario_inteligente(termos, awb)` — comentarios mais informativos: inclui situacao (Pendente/Regularizado) e NF-e quando disponivel.
- `gerar_resumo_execucao(dados)` — gera resumo em linguagem natural no final do processamento (3-5 linhas, destaca padroes e alertas).

### Integracoes nos modulos existentes
- **outlook.py**: `isolar_resposta_email()` chamado antes da classificacao (remove thread poluida)
- **parser.py**: `validar_extracao()` chamado apos combinar regex+IA (remove falsos positivos)
- **main.py**:
  - Registra padrao de cada voo na memoria (origem, destino, tem_termos)
  - Diagnostica erros via IA quando voo falha (loga explicacao e acao sugerida)
  - Gera comentarios inteligentes (com situacao) em vez do texto fixo
  - Gera resumo de execucao via IA ao final (exibido no log)
  - Registra classificacoes de email na memoria
  - Timer de execucao (`_tempo_inicio_processamento`)

### Verificacao final de retencao (correcao)
- Agora usa `consultar_awb_status_apenas()` — so checa status no Nexlog
- NAO abre a SEFAZ (era o gargalo: ~2 min por AWB, agora ~5s)
- Novo metodo `consultar_awb_status_apenas()` em `consulta_cliente.py`

### Logica de seguranca da IA (correcao)
- Regex diz `com_termos` → MANTEM sempre (IA nunca pode "relaxar")
- IA so pode "apertar" (reter) quando regex diz `sem_termos`
- Dupla validacao: regex=sem + IA=sem → libera com confianca

### Decisoes tecnicas
- Toda integracao envolta em try/except — se IA ou memoria falhar, fallback funciona normalmente
- Imports locais pra evitar circular imports e nao impactar startup
- Memoria usa busca por palavras-chave (sem vector DB — volume pequeno)
- IA "aprende" via contexto injetado no prompt (RAG simplificado), nao fine-tuning

---

---

## 2026-07-24 — Sessao 4: Velocidade + Funcoes Domicilio (integrado do Igor)

### Otimizacao de velocidade
- **`pausa()` com multiplicador** — substitui `time.sleep()` em todo o projeto. Default 0.7 = 30% mais rapido. Configuravel em `config.navegador.pausa_multiplicador`
- **`page_load_strategy = "eager"`** — Chrome nao espera imagens/scripts de terceiros. Ganha 1-3s por navegacao de pagina
- **Headless mode** — suporte completo a `--headless=new` (pra rodar sem interface, ex: agendado/servidor)
- **Chrome otimizado** — `disable-extensions`, `disable-popup-blocking`, `disable-infobars`, `log-level=3`

### Novo modulo `modules/nexlog_domicilio.py`
Funcoes do setor de entrega a domicilio (portadas do GOL PRO do Igor):
- `consultar_vencimento(awb)` — busca data de vencimento + status de uma AWB
- `preparar_vencendo_hoje(data_ini, data_fim)` — configura tela "Vencendo Hoje" (aba vencimentos, filtro Domicilio)
- `consultar_lista(numero_lista)` — abre lista de entrega, extrai motorista + AWBs
- `preparar_coletas(data_ini, data_fim)` — configura tela de Gerenciar Coletas/Entregas
- `marcar_cte_coleta(cte)` — marca checkboxes de um CTe na tela de coletas
- `finalizar_coletas()` — clica finalizar + confirma
- `marcar_cte_liberacao(cte)` — marca checkboxes na tela de Retencao (com validacao de status)
- `finalizar_liberacao()` — clica "Liberar documento" + confirma

### Atualizacoes no `config.py`
- Nova dataclass `ConfigNavegador` (headless, pausa_multiplicador, page_load_eager)
- Funcao global `pausa(segundos)` — importavel por todos os modulos
- Persistencia no JSON de credenciais

### Atualizacoes no `modules/browser.py`
- `pausa()` em vez de `time.sleep()` em todas as esperas
- `page_load_strategy = "eager"` quando configurado
- Headless com flags corretos (`--headless=new`, `--window-size`, `--remote-allow-origins`, etc.)
- `navegar_coletas_entregas()` novo metodo de navegacao
- Flags extras de performance (disable-extensions, etc.)

### Correcao: Ollama fallback CPU
- Quando Chrome ocupa toda VRAM da GPU (RTX 5060 8GB), Ollama dava OOM
- Agora detecta erro 500 + "out of memory" e muda pro modelo `llama3.1:8b-cpu`
- CPU usa `num_ctx=2048` pra caber na RAM (~16s por resposta, vs 1-3s na GPU)
- Primeira falha OOM custa ~20s (tentativa GPU + retry CPU), todas as seguintes vao direto CPU

---

## 2026-07-26 — Sessao 5: Correcao critica (PDF corrompido → liberacao indevida)

### Bug critico corrigido
- **Cenario**: PDF do email da SEFAZ vem corrompido (`Unexpected EOF`). O parser falhava e retornava `ConsultaMDFe()` vazio (total_termos=0). O main.py interpretava como "0 termos confirmado" e liberava o voo.
- **Impacto real**: Voo G3 9633 (25/07/2026) com **535 TAs emitidos** foi liberado indevidamente porque o PDF baixado do Outlook estava corrompido.
- **Correcao em `modules/parser.py`**:
  - `parsear_relatorio_pdf()` agora retorna `None` quando o PDF nao pode ser lido (excecao, EOF, texto vazio)
  - `None` = falha de leitura (nao confiavel). `ConsultaMDFe(total_termos=0)` = PDF lido com sucesso e confirma 0 termos (confiavel)
- **Correcao em `main.py`** (etapa 3 - Outlook):
  - Antes de tratar como "0 termos", verifica se `consulta is None` (parse falhou)
  - Se `None`: loga aviso e cai no fallback (site SEFAZ) em vez de liberar
  - O `else` (0 termos → libera) so roda se o PDF foi parseado com sucesso

### Bug critico: liberacao cruzada entre voos
- **Cenario**: AWB aparece no manifesto de 2 voos (transito/conexao). Voo A detecta termo e reter. Voo B (sem termos ou chave compartilhada) tenta liberar - e libera o AWB retido porque a tela de Retencao mostra TODOS os AWBs da base.
- **Impacto real**: G3 1670 liberou AWBs 12749303284 e 12749340255 que tinham sido retidos pelo G3 1668 (mesmos AWBs em ambos manifestos).
- **Correcao em `main.py`** (etapa 6):
  - Novo parametro `awbs_retidos_sessao` passado pro `_processar_um_voo`
  - Antes de liberar, cruza a lista com AWBs retidos em voos anteriores da MESMA sessao
  - Se AWB ja foi retido em outro voo, NAO libera e loga aviso
  - Resolve o problema de manifestos compartilhados

### Melhoria: desempate de voos duplicados na tabela
- `_encontrar_linha_voo()` agora usa `data_chegada` para desempatar quando o mesmo numero de voo aparece em dias diferentes (ex: G3 1708 nos dias 25 e 26)
- Log de debug quando linha e encontrada mas sem link ViewMDFe

### Fix: overlay Angular na SEFAZ
- Antes de clicar "Imprimir Relatorio", espera ate 15s pelo overlay `black-overlay` sumir
- Se click intercepted mesmo assim, fallback via JS click
- Corrige o erro `element click intercepted` que marcava voos como inconclusivos

### Bug critico: `_verificar_zero_termos_tabela` dando falso "0 TAs"
- **Cenario**: SEFAZ e Angular SPA. Ao navegar entre consultas via "URL direta" (botao nao encontrado), a tabela anterior pode ficar residual no DOM. O codigo usava `colunas[-1]` (ultima coluna) achando que era "TAs emitidos" — mas a ultima coluna pode ser um icone de acao (lupa) com texto vazio, ou a tabela pode ser de outra consulta.
- **Impacto real**: G3 1670 consultou no site SEFAZ, achou a tabela residual do voo anterior, leu "TAs = 0" e liberou 19 AWBs indevidamente (o voo TINHA termos).
- **Correcao em `modules/sefaz.py`** (`_verificar_zero_termos_tabela`):
  - Agora identifica a coluna "TAs emitidos" pelo HEADER (nunca por posicao)
  - Valida que a linha pertence a chave consultada (fingerprint dos ultimos 8 digitos)
  - Se a linha nao corresponde a chave, ignora (pode ser residual)
  - Extrai apenas a parte numerica do texto (ignora icones/links)
  - Se nao achar header ou linha correspondente, retorna None (vai pro Imprimir Relatorio)

### Principio reafirmado
- **NUNCA liberar na duvida** — se o PDF falhou, NAO assume que esta tudo OK. Vai pro site SEFAZ ou marca como inconclusivo.

### OCR para PDFs baseados em imagem (Tesseract)
- **Cenario**: Alguns PDFs da SEFAZ sao imagens (scan/foto) em vez de texto. O `pdfplumber` nao extrai nada, retorna vazio, e o programa cai pro site SEFAZ (que pode falhar tambem).
- **Solucao**: Fallback com Tesseract OCR. Se o `pdfplumber` retorna texto vazio, converte o PDF em imagem (300 DPI) e roda OCR.
- **Pre-processamento de imagem** (melhora precisao): grayscale → upscale 2x → contraste 2x → sharpening 2x → binarizacao (threshold 180). Resolveu erros de leitura 3/5, 6/8, etc.
- **Parser OCR dedicado** (`_extrair_termos_formato_ocr`): extrai termos/CTes do formato OCR (pipes, linhas separadas). Usa CTes unicos (deduplica antes de associar).
- **Fluxo**: `pdfplumber` tenta texto → se vazio → `pdf2image` converte pra PNG → pre-processamento → `pytesseract` extrai texto → `parsear_relatorio_sefaz()` + fallback OCR
- **Dependencias**: `pytesseract`, `pdf2image` (pip). Binarios: Tesseract-OCR + Poppler.
- **Auto-deteccao** de Tesseract e Poppler (inclui caminhos com versao tipo `poppler-26.02.0`).

### Envio automatico de email com TA+DAR (domicilio com termo)
- **Funcionalidade**: Para cada AWB domicilio retido pela SEFAZ, envia email pra base de origem com PDFs do Termo (TA) e Guia de pagamento (DAR) em anexo.
- **Fluxo**: Detecta AWBs domicilio com termo → extrai base de origem (Nexlog rastreio) → baixa TA+DAR (ConsultaTADe no site SEFAZ) → envia email via Outlook Web
- **Destinatarios**: `{sigla}fk@voegol.com.br` + `{sigla}fs@voegol.com.br` (montado automaticamente pela sigla da base)
- **Modo teste**: `MODO_TESTE_EMAIL=True` envia tudo pra email de teste (gmmiqoption@gmail.com)
- **Toggle na interface**: Checkbox "Enviar email TA+DAR" na aba Voos (ao lado do botao Iniciar)
- **Outlook Web**: Metodo `enviar_email()` na classe OutlookWeb:
  - Campo "Para" encontrado via Shift+Tab do Cc (Outlook novo nao expoe como input)
  - Corpo digitado antes da assinatura (Ctrl+Home)
  - Nome "Gustavo Mendes" substituido via JS (preserva imagens/icones)
  - Anexo: remove atributo `accept` do input[type=file] via JS (aceita PDFs)

### Google Forms automatico (domicilio com termo)
- AWBs domicilio com termo sao enviados pro Google Forms (entry.937340729) ao final de cada voo
- POST direto na URL do formResponse (sem abrir navegador)

### Validacao de chave tolerante a OCR
- Funcao `_chaves_compativeis()`: compara chaves com tolerancia de ate 4 digitos diferentes
- Resolve falsos rejects quando OCR erra 1-2 digitos (ex: 3→5 no inicio da chave)
- Usada nas 3 validacoes: PDF do email, site SEFAZ, e "0 termos com chave"

### Selecao de email correta no Outlook
- Prioriza emails da "PF Central de Transportadoras" ou "DIGITADORES POSTO FISCAL"
- Ignora emails enviados por MCZFK (evita clicar no email de solicitacao em vez da resposta)
- Aceita respostas "Re:" e emails novos da PF Central
- Se corpo vazio mas tem anexo: baixa o PDF (pode ser imagem → OCR)

---

## 2026-08-16 — Sessao 6: Aba SEFAZ (Envio de Manifestos DAMDFE)

### Nova aba "SEFAZ" na interface
- Aba dedicada para envio de manifestos (DAMDFE) para analise da SEFAZ-AL
- Visual: header, card de busca (datas + botao), lista de voos com checkboxes, card de execucao (progress bar, log, botoes)
- Botoes "Todos" / "Nenhum" para selecao rapida de voos
- Reutiliza navegador se ja aberto (evita relogin)

### Novo metodo `imprimir_damdfe()` em `modules/nexlog_voos.py`
- Abre modal "Integracao MDFe" (ViewMDFe via JS ou dropdown fallback)
- Le a chave de acesso (44 digitos) do modal
- Clica em "Imprimir DAMDFE" (multiplas estrategias de XPath: texto exato, fallback por conteudo, generico)
- Aguarda download do PDF (`aguardar_download(30)`)
- Fecha modal e retorna tupla `(chave_mdfe, caminho_pdf)`

### Envio automatico de email
- Destinatarios: `pfcentraldetransportadoras@sefaz.al.gov.br` + `pfdigitadores@hotmail.com`
- Assunto: `MANIFESTO {numero_manifesto} VOO {numero_voo}`
- Corpo: descricao do voo + etapas + chave de acesso completa
- Anexo: PDF do DAMDFE baixado
- Usa `OutlookWeb.enviar_email()` (ja existente, com suporte a anexos)

### Fluxo completo
1. Usuario seleciona datas e clica "Buscar Voos"
2. Voos aparecem com checkboxes (todos marcados por padrao)
3. Clica "ENVIAR MANIFESTOS"
4. Para cada voo: abre modal MDFe → extrai chave → baixa DAMDFE → envia email
5. Log em tempo real + progress bar + resumo final (enviados/erros)

### Decisoes tecnicas
- Numero do manifesto extraido da posicao 25-34 da chave MDF-e (sequencial)
- Repesquisa voos antes de iterar (garante tabela preenchida apos navegacao)
- Thread separada para nao travar a interface
- Pausa de 2s entre voos para nao sobrecarregar Outlook/Nexlog

---

## Proximos passos
- [x] Nova aba "SEFAZ" para envio de manifestos (DAMDFE) pra analise
  - Buscar voos, selecionar quais enviar
  - Abrir modal Integracao MDFe → Imprimir DAMDFE → baixar PDF
  - Enviar email pra pfcentraldetransportadoras@sefaz.al.gov.br + pfdigitadores@hotmail.com
  - Assunto: "MANIFESTO {numero} VOO {voo}"
  - Corpo: chave de acesso + anexo DAMDFE
- [ ] Mapeamento de bases sem email padrao (exceções ao formato fk/fs)
- [ ] Testar execucao completa com todas as melhorias desta sessao

## Proximos passos planejados
- [x] Instalar Ollama no PC do Gustavo
- [x] Integrar Ollama no programa (modulo `modules/ia_local.py`)
- [x] Substituir `detectar_resposta_email()` por classificacao via IA
- [x] Substituir/complementar parser de PDF SEFAZ com IA
- [x] Testar com emails e PDFs reais
- [x] Sistema de memoria/aprendizado
- [x] Diagnostico de erros e auto-recuperacao
- [x] Isolamento de thread de email
- [x] Validacao de extracao (sanity check)
- [x] Comentarios inteligentes
- [x] Resumo de execucao via IA
- [x] Otimizacao de velocidade (pausa multiplicador + eager)
- [x] Funcoes de domicilio (vencidas, listas, coletas)
- [x] Fallback CPU pro Ollama
- [ ] Testar execucao completa com todas as melhorias
- [ ] Integrar funcoes domicilio na interface grafica (nova aba)
- [ ] Novas funcionalidades no Uncleared (a definir)
