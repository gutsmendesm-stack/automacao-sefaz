# Regras Tecnicas e Restricoes

## Nexlog - Comportamento do Site

O Nexlog (golcargo.nexlog.com) usa jQuery + DataTables e tem comportamentos especificos:

1. **time.sleep() sao NECESSARIOS** — o site inicializa DataTables/jQuery apos carregar o layout, bloqueando inputs temporariamente. WebDriverWait sozinho NAO funciona nessas paginas. NAO tentar substituir todos os sleeps por WebDriverWait.

2. **Navegacao duplicada e intencional** — o programa navega pra mesma pagina mais de uma vez no processamento de um voo. Isso e necessario porque modais e dropdowns bugam o estado da pagina. NAO otimizar removendo navegacoes "redundantes" sem testar extensivamente.

3. **toogleFilter** (sim, com typo) — o botao de filtro na pagina de Conhecimento tem classe CSS `toogleFilter` (nao `toggleFilter`). O desenvolvedor do Nexlog errou a grafia.

4. **URL do Nexlog**: `https://golcargo.nexlog.com` (nao `golcargo.gollog.com`)

5. **Perfil Chrome dedicado** — a automacao usa perfil em `%APPDATA%/automacao_sefaz/chrome_profile/` pra manter sessoes salvas (Outlook especialmente)

## SEFAZ-AL - Comportamento do Site

1. **Site lento** — o site `transportadoras.sefaz.al.gov.br` pode demorar ate 80-100s pra gerar relatorio PDF
2. **Timeout de 100 segundos** no Imprimir Relatorio — se nao gerar nesse tempo, desiste SEM retry
3. **TAs emitidos = 0** — detectar direto na tabela, sem clicar Imprimir (evita travamento)
4. **NAO otimizar sleeps da SEFAZ** — o site precisa dos tempos como estao
5. **Angular SPA** — campos sao renderizados dinamicamente, precisa multiplas estrategias de XPath

## Outlook Web

1. **Login manual na primeira vez** — o programa pausa e pede login. Depois o perfil Chrome salva a sessao
2. **Painel de leitura** — ao clicar num email, abre painel que cobre a busca. Precisa fechar antes de buscar de novo
3. **Anexos** — multiplas estrategias de download (clique direto, menu 3 pontos, "Baixar tudo")

## Padrao de Codigo

- Sem acentos em nomes de variaveis/funcoes (mas OK em strings/logs)
- Docstrings explicando fluxo do site (pra quem nao conhece o Nexlog)
- Logger por modulo (`logging.getLogger(__name__)`)
- Tratamento de erro: try/except generoso, nunca deixa a automacao travar
- Interface: CustomTkinter dark mode, thread separada pra processamento

## Estrutura do Repositorio

```
automacao-sefaz/
├── main.py              # Interface + orquestrador
├── config.py            # Credenciais e configuracao
├── requirements.txt     # Dependencias
├── modules/
│   ├── browser.py       # Chrome + login + navegacao Nexlog
│   ├── nexlog_voos.py   # Buscar voos, chave MDF-e, manifesto
│   ├── nexlog_cte.py    # Buscar AWB por CTe, comentarios
│   ├── nexlog_liberar.py # Liberar AWBs na tela Retencao
│   ├── outlook.py       # Busca email SEFAZ
│   ├── sefaz.py         # Consulta site SEFAZ-AL
│   ├── parser.py        # Parse de PDFs (relatorio SEFAZ, manifesto)
│   ├── telegram_bot.py  # Bot Telegram (notificacoes + comandos)
│   ├── consulta_cliente.py # Consulta AWB pra atendimento
│   └── uncleared.py     # Limpeza e comparacao de AWBs
├── models/
│   └── termo.py         # Dataclasses (Voo, TermoApreensao, etc.)
├── tests/               # Scripts de teste
├── scripts_legados/     # Scripts avulsos antigos (referencia)
└── .kiro/steering/      # Contexto pra assistente AI
```
