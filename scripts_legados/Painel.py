import tkinter as tk
from tkinter import messagebox
import subprocess
import os

# ================= CONFIGURAÇÃO DOS PROGRAMAS =================

appdata = os.getenv("APPDATA")
pasta_base = os.path.join(appdata, "Automação")
os.makedirs(pasta_base, exist_ok=True)

DIRETORIOS = {
    "Separar manifesto": os.path.join(os.getcwd(), "Separar.py"),
    "Imprimir separados": os.path.join(os.getcwd(), "imprimir_cte_manifesto.py"),
    "Liberar retenção": os.path.join(os.getcwd(), "Liberar_retencao.py"),
    "Encontrar carga": os.path.join(os.getcwd(), "Encontrar_carga.py"),
    "Gerar lista": os.path.join(os.getcwd(), "Gerar_lista.py"),
    "Triagem Retenção": os.path.join(os.getcwd(), "Triagem_Retencao.py"),
}



# ================= FUNÇÕES =================

def abrir_programa(caminho):
    if not caminho or caminho == "COLE_AQUI":
        messagebox.showwarning("Diretório não configurado", "Configure o caminho do executável.")
        return

    if not os.path.exists(caminho):
        messagebox.showerror("Erro", f"Arquivo não encontrado:\n{caminho}")
        return

    try:
        # Isso diz ao Windows: "Use o python para rodar este arquivo"
        subprocess.Popen(f'python "{caminho}"', shell=True)
    except Exception as e:
        messagebox.showerror("Erro", str(e))


def sair():
    janela.destroy()

# ================= JANELA =================

janela = tk.Tk()
janela.title("Golligo")
janela.geometry("900x600")
janela.configure(bg="#050814")
janela.resizable(False, False)

# ================= ANIMAÇÃO DO TÍTULO =================

def pulsar():
    cores = ["#00ffe1", "#00cfc1", "#00ffe1"]
    atual = titulo.cget("fg")
    novo = cores[(cores.index(atual) + 1) % len(cores)]
    titulo.config(fg=novo)
    janela.after(600, pulsar)

titulo = tk.Label(
    janela,
    text="PAINEL DE AUTOMAÇÕES",
    bg="#050814",
    fg="#00ffe1",
    font=("Segoe UI", 24, "bold")
)
titulo.pack(pady=15)

janela.after(600, pulsar)

# ================= FRAME GRID =================

frame = tk.Frame(janela, bg="#050814")
frame.pack(expand=True)

# ================= ESTILO FUTURISTA =================

def criar_card(nome, caminho, row, col):
    card = tk.Frame(
        frame,
        bg="#0b1228",
        width=260,
        height=120,
        highlightbackground="#00ffe1",
        highlightthickness=1
    )
    card.grid(row=row, column=col, padx=20, pady=20)
    card.grid_propagate(False)

    label = tk.Label(
        card,
        text=nome,
        bg="#0b1228",
        fg="#00ffe1",
        font=("Segoe UI", 12, "bold"),
        wraplength=220,
        justify="center"
    )
    label.place(relx=0.5, rely=0.5, anchor="center")

    def hover_on(event):
        card.config(bg="#111b3d", highlightthickness=2)
        label.config(bg="#111b3d")

    def hover_off(event):
        card.config(bg="#0b1228", highlightthickness=1)
        label.config(bg="#0b1228")

    def click(event):
        abrir_programa(caminho)

    card.bind("<Enter>", hover_on)
    card.bind("<Leave>", hover_off)
    card.bind("<Button-1>", click)

    label.bind("<Enter>", hover_on)
    label.bind("<Leave>", hover_off)
    label.bind("<Button-1>", click)

# ================= CRIAÇÃO GRID =================

linha = 0
coluna = 0

for nome, caminho in DIRETORIOS.items():
    criar_card(nome, caminho, linha, coluna)
    coluna += 1
    if coluna > 2:
        coluna = 0
        linha += 1

# ================= BOTÃO SAIR =================

btn_sair = tk.Button(
    janela,
    text="SAIR",
    command=sair,
    bg="#7f1d1d",
    fg="white",
    activebackground="#dc2626",
    relief="flat",
    font=("Segoe UI", 11, "bold"),
    width=15,
    cursor="hand2"
)
btn_sair.pack(pady=20)

# ================= LOOP =================

janela.mainloop()
