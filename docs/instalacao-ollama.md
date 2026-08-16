# Instalacao do Ollama (IA Local)

Guia passo a passo pra instalar o Ollama no Windows e configurar o modelo.

## Passo 1: Baixar o Ollama

Acesse: **https://ollama.com/download/windows**

Clique em "Download for Windows" e salve o arquivo.

## Passo 2: Instalar

1. Execute o arquivo `OllamaSetup.exe` que baixou
2. Siga o instalador (Next > Next > Install > Finish)
3. O Ollama vai instalar e iniciar automaticamente (icone na bandeja do sistema)

## Passo 3: Verificar instalacao

Abra o **PowerShell** (ou CMD) e digite:

```
ollama --version
```

Deve mostrar algo como: `ollama version 0.x.x`

## Passo 4: Baixar o modelo

No PowerShell, execute:

```
ollama pull llama3.1:8b
```

Isso vai baixar o modelo Llama 3.1 8B (~4.7 GB de download).
Aguarde terminar (pode levar 5-15 minutos dependendo da internet).

## Passo 5: Testar o modelo

No PowerShell, execute:

```
ollama run llama3.1:8b "Diga ola em portugues"
```

Se responder algo como "Olá! Como posso ajudar?", esta funcionando!

## Passo 6: Verificar se esta usando GPU

No PowerShell, execute:

```
ollama ps
```

Deve mostrar o modelo carregado. Se tiver "GPU" na linha, esta rodando na placa de video (rapido).

Pra confirmar a GPU:
```
nvidia-smi
```

Deve mostrar a RTX 5060 com uso de VRAM do Ollama.

## Pronto!

Depois de confirmar que funciona, o programa vai usar o Ollama automaticamente
via API local (http://localhost:11434). Nao precisa fazer mais nada manual.

---

## Comandos uteis

| Comando | O que faz |
|---------|-----------|
| `ollama list` | Lista modelos instalados |
| `ollama pull llama3.1:8b` | Baixa/atualiza modelo |
| `ollama rm llama3.1:8b` | Remove modelo (libera espaco) |
| `ollama ps` | Mostra modelo em execucao |
| `ollama stop` | Para o modelo da memoria |
| `ollama serve` | Inicia o servidor (se nao estiver rodando) |

## Troubleshooting

**"ollama nao reconhecido"**: Reinicie o PC apos instalar (precisa atualizar o PATH).

**Modelo lento**: Verifique se esta usando GPU (`ollama ps`). Se nao, pode ser que o driver NVIDIA precisa atualizar.

**Sem espaco**: O modelo ocupa ~5GB no disco. Verifique espaco em C:\Users\SEU_USUARIO\.ollama\

**Erro de memoria GPU**: Feche jogos pesados antes de rodar. O modelo precisa de ~5GB de VRAM livre.
