# Contexto do Projeto: Automacao SEFAZ (Aero)

## Sobre o usuario

- Nome: Gustavo
- Trabalha na **Gollog** (divisao de cargas da GOL Linhas Aereas) no terminal de **MCZ (Maceio)**
- Funcao: operacoes de recebimento e liberacao de cargas aereas
- Usa o sistema **Nexlog** (https://golcargo.nexlog.com) diariamente
- O repositorio GitHub e `gutsmendesm-stack/automacao-sefaz` na branch `feature/initial-setup`

## O que o programa faz

Ferramenta de automacao desktop (Python + Selenium + CustomTkinter) que processa voos diarios:

1. Busca voos do dia no Nexlog (tela Gerenciar Rotas)
2. Para cada voo, verifica se a SEFAZ-AL reteve cargas:
   - Extrai chave MDF-e do voo
   - Verifica email no Outlook (PF Central de Transportadoras)
   - Se necessario, consulta site da SEFAZ
3. Para cargas retidas: adiciona comentario critico no AWB
4. Para cargas livres: libera na tela de Retencao

## Sistemas envolvidos

| Sistema | URL | Funcao |
|---------|-----|--------|
| Nexlog | golcargo.nexlog.com | Sistema principal de gestao de cargas |
| SEFAZ-AL | transportadoras.sefaz.al.gov.br | Verificacao de termos de apreensao |
| Outlook Web | outlook.cloud.microsoft | Email com respostas da SEFAZ |
| Telegram Bot | API Telegram | Comandos remotos e notificacoes |

## Aba Uncleared

Funcionalidade separada para gestao de "uncleared" (cargas sem baixa):
- Gustavo pistoleia AWBs fisicamente no terminal (lista 1)
- Importa planilha do sistema com AWBs pendentes de baixa (lista 2)
- Compara: identifica o que pode ser baixado (nao esta no terminal) vs o que nao pode (ainda esta la)
- Filtros: BasePosse=MCZ, RetiraEntrega=RETIRA
- Identifica FRAPs (frete pago no destino)

## Linguagem/Stack

- Python 3.10+
- Selenium WebDriver (Chrome)
- CustomTkinter (interface dark mode)
- pdfplumber (parse de PDFs)
- pandas + openpyxl (planilhas)
- requests (Telegram bot)
