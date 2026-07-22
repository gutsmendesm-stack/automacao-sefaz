import pdfplumber
from tkinter import Tk, filedialog
import os

Tk().withdraw()

arquivo = filedialog.askopenfilename(
    title="Escolha o PDF",
    filetypes=[("PDF", "*.pdf")]
)

codigos_127 = set()
sessao_retira = False  # 🔹 mantém estado entre páginas

with pdfplumber.open(arquivo) as pdf:
    for page in pdf.pages:
        palavras = page.extract_words(use_text_flow=True)

        for p in palavras:
            texto = p["text"].strip().upper()
            texto_limpo = texto.replace(" ", "")

            # 🔹 Detecta cabeçalho RETIRA (isolado)
            if texto == "RETIRA":
                sessao_retira = True
                continue

            # 🔹 Detecta cabeçalho ENTREGA (isolado)
            if texto == "ENTREGA":
                sessao_retira = False
                continue

            # 🔹 Coleta apenas dentro da sessão RETIRA
            if sessao_retira and texto_limpo.startswith("127") and texto_limpo.isdigit():
                codigos_127.add(texto_limpo)

# ===== CRIA O ARQUIVO TXT =====

appdata = os.getenv("APPDATA")
pasta = os.path.join(appdata, "documentos complementares")
os.makedirs(pasta, exist_ok=True)

arquivo_txt = os.path.join(pasta, "cte manifesto.txt")

with open(arquivo_txt, "w", encoding="utf-8") as f:
    for codigo in sorted(codigos_127):
        f.write(codigo + "\n")

print("\nCódigos da sessão RETIRA:\n")
for codigo in sorted(codigos_127):
    print(codigo)

print(f"\nArquivo criado com sucesso em:\n{arquivo_txt}")
