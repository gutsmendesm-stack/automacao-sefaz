# Automacao SEFAZ

Ferramenta de automacao para operacoes aereas integradas com o sistema **Nexlog**, **SEFAZ-AL** e **Outlook**.

## O que faz

O sistema automatiza o fluxo completo de processamento de voos:

1. Busca voos por data no Nexlog
2. Para cada voo:
   - Extrai chave MDF-e (Integracao MDFe)
   - Baixa manifesto (PDF) e extrai AWBs RETIRA/ENTREGA
   - Verifica no Outlook se a SEFAZ respondeu
   - Consulta termos no site da SEFAZ ou via PDF do email
   - Para cada CTe com termo: busca AWB e adiciona comentario critico
   - Libera AWBs RETIRA sem termo na tela de Retencao

## Estrutura do Projeto

```
automacao-sefaz/
├── main.py                  # Orquestrador principal + Interface grafica (CustomTkinter)
├── config.py                # Configuracao centralizada (credenciais, URLs, timeouts)
├── requirements.txt         # Dependencias Python
├── modules/                 # Modulos de automacao
│   ├── browser.py           # Gerenciamento do navegador (Selenium/Chrome)
│   ├── consulta_cliente.py  # Consulta de clientes no Nexlog
│   ├── nexlog_cte.py        # Operacoes com CTe no Nexlog
│   ├── nexlog_liberar.py    # Liberacao de retencoes no Nexlog
│   ├── nexlog_voos.py       # Busca e processamento de voos
│   ├── outlook.py           # Integracao com Outlook Web
│   ├── parser.py            # Parsing de PDFs (manifesto, relatorio SEFAZ)
│   ├── sefaz.py             # Consulta ao site da SEFAZ-AL
│   └── telegram_bot.py      # Notificacoes via Telegram
├── models/                  # Modelos de dados
│   └── termo.py             # Dataclasses (Voo, DadosVoo, ConsultaMDFe, etc.)
├── tests/                   # Scripts de teste
│   ├── testar_consulta.py
│   ├── testar_etapas.py
│   └── testar_tade.py
└── scripts_legados/         # Scripts avulsos anteriores (referencia)
    ├── Encontrar_carga.py
    ├── Gerar_lista.py
    ├── Liberar_retencao.py
    ├── Painel.py
    ├── Separar.py
    ├── Triagem_Retencao.py
    └── imprimir_cte_manifesto.py
```

## Requisitos

- Python 3.10+
- Google Chrome instalado
- ChromeDriver compativel com a versao do Chrome

## Instalacao

```bash
pip install -r requirements.txt
```

## Configuracao

Na primeira execucao, o sistema cria a pasta de configuracao em:
- Windows: `%APPDATA%/automacao_sefaz/credenciais.json`

Preencha as credenciais pela interface grafica ou edite o JSON diretamente.

## Uso

```bash
python main.py
```

A interface permite:
- Selecionar data dos voos
- Configurar credenciais (Nexlog, SEFAZ, Telegram)
- Acompanhar progresso em tempo real
- Receber notificacoes via Telegram

## Scripts Legados

A pasta `scripts_legados/` contem os scripts avulsos originais que foram a base para o sistema atual. Sao mantidos como referencia e podem ser uteis para operacoes pontuais.

| Script | Funcao |
|--------|--------|
| `Painel.py` | Launcher dos scripts individuais |
| `Separar.py` | Extrai codigos RETIRA de PDF de manifesto |
| `imprimir_cte_manifesto.py` | Imprime CTe/manifesto via Nexlog |
| `Liberar_retencao.py` | Libera retencoes em lote |
| `Encontrar_carga.py` | Busca cargas no sistema |
| `Gerar_lista.py` | Gera listas de documentos |
| `Triagem_Retencao.py` | Triagem automatizada de retencoes |
