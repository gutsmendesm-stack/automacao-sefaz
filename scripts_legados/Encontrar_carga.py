from selenium import webdriver
import os
import tkinter as tk
from tkinter import messagebox
from selenium.common.exceptions import TimeoutException
from selenium.webdriver.common.by import By
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.common.action_chains import ActionChains
from selenium.webdriver.common.keys import Keys
import time


appdata = os.getenv("APPDATA")
pasta = os.path.join(appdata, "documentos complementares")
os.makedirs(pasta, exist_ok=True)
arquivo = os.path.join(pasta, "encontrar carga.txt")

import os
import tkinter as tk
from tkinter import messagebox

# --------- CAMINHO DO ARQUIVO (APPDATA) ---------
appdata = os.getenv("APPDATA")
pasta = os.path.join(appdata, "documentos complementares")
os.makedirs(pasta, exist_ok=True)

arquivo = os.path.join(pasta, "encontrar_carga.txt")

# --------- FUNÇÃO SALVAR ---------
def salvar_texto():
    login = login_var.get().strip()
    senha = senha_var.get().strip()
    texto = campo_texto.get("1.0", tk.END).strip()

    if not login or not senha:
        messagebox.showwarning("Aviso", "Login ou senha não podem estar vazios.")
        return

    if not texto:
        messagebox.showwarning("Aviso", "O campo de texto está vazio.")
        return

    try:
        with open(arquivo, "w", encoding="utf-8") as f:
            f.write(f"LOGIN: {login}\n")
            f.write(f"SENHA: {senha}\n")
            f.write("\nTEXTO:\n")
            f.write(texto)

        messagebox.showinfo("Sucesso", "Dados salvos com sucesso.")
        janela.destroy()

    except Exception as e:
        messagebox.showerror("Erro", str(e))

# --------- INTERFACE ---------
janela = tk.Tk()
janela.title("Colar códigos")
janela.geometry("300x600")

# --------- LOGIN ---------
login_var = tk.StringVar()
senha_var = tk.StringVar()

label_login = tk.Label(janela, text="Login:")
label_login.pack(pady=(10, 0))

entry_login = tk.Entry(janela, textvariable=login_var)
entry_login.pack(padx=10, fill="x")

label_senha = tk.Label(janela, text="Senha:")
label_senha.pack(pady=(10, 0))

entry_senha = tk.Entry(janela, textvariable=senha_var, show="*")
entry_senha.pack(padx=10, fill="x")

# --------- TEXTO ---------
label_texto = tk.Label(janela, text="Cole os códigos abaixo:")
label_texto.pack(pady=10)

campo_texto = tk.Text(janela, wrap="word")
campo_texto.pack(expand=True, fill="both", padx=10)

# --------- BOTÃO ---------
def salvar_e_fechar():
    salvar_texto()
    janela.destroy()
botao = tk.Button(janela, text="Salvar", command=salvar_e_fechar)
botao.pack(pady=15)

# --------- LOOP ---------
janela.mainloop()


# --------- ABRIR NAVEGADOR ---------
navegador = webdriver.Chrome()
navegador.maximize_window()

# --------- ACESSAR GOLLOG ---------
navegador.get("https://golcargo.gollog.com.br/account/#/login")

time.sleep(4)

wait = WebDriverWait(navegador, 15)

# --------- LOGIN ---------
campo_user = wait.until(
    EC.presence_of_element_located((By.XPATH, "//*[@id='user']"))
)
campo_user.click()
campo_user.send_keys(login_var.get())  # ✅ CORRETO

# --------- SENHA ---------
campo_senha = wait.until(
    EC.presence_of_element_located((By.XPATH, "//*[@id='password']"))
)
campo_senha.click()
campo_senha.send_keys(senha_var.get())  # ✅ CORRETO

# --------- BASE ---------
base = wait.until(
    EC.presence_of_element_located((By.XPATH, "//*[@id='franchise']"))
)
base.click()
base.send_keys("MCZ")

# --------- BOTÃO LOGIN ---------
botao_login = navegador.find_element(By.XPATH, "//*[@id='div-btns']/button")
botao_login.click()

time.sleep(1)

# --------- MENSAGEM DE SESSÃO ---------
try:
    wait_msg = WebDriverWait(navegador, 3)
    botao_continuar = wait_msg.until(
        EC.element_to_be_clickable(
            (By.XPATH, "/html/body/div/div/div/button[2]")
        )
    )
    botao_continuar.click()
    print("Mensagem de sessão detectada, continuando")

except TimeoutException:
    pass

time.sleep(3)


botao_operacoes = navegador.find_element(By.XPATH, "//*[@id='mainMenu']/div[1]/div/ul/li[4]/div/div/div[1]")
action = ActionChains(navegador)
action.move_to_element(botao_operacoes).perform()
time.sleep(1)


opcao_lista = wait.until(
    EC.element_to_be_clickable(
        (By.XPATH, "//li[contains(@class,'menu-item')]//a[.//span[normalize-space()='Encontrar carga']]")
    )
)
opcao_lista.click()

time.sleep(2)


campo = wait.until(
    EC.presence_of_element_located((By.ID, "DocumentNumber"))
)
campo.clear()

appdata = os.getenv("APPDATA")
pasta = os.path.join(appdata, "documentos complementares")
os.makedirs(pasta, exist_ok=True)

caminho_txt = os.path.join(pasta, "encontrar carga.txt")

xpath_input_document = "//*[@id='DocumentNumber']"
xpath_btn_pesquisar = "//button[contains(.,'Pesquisar')]"
xpath_checkbox = "//td[contains(@class,'td-check')]//input[@type='checkbox' and not(@disabled)]"
xpath_btn_confirmar = "//button[@id='btnSubmit']"

wait = WebDriverWait(navegador, 10)
wait_curto = WebDriverWait(navegador, 2)

with open(caminho_txt, "r", encoding="utf-8") as arquivo:
    for linha in arquivo:
        valor = linha.strip()
        if not valor:
            continue

        # Campo
        campo = wait.until(
            EC.element_to_be_clickable((By.ID, "DocumentNumber"))
        )
        campo.send_keys(Keys.CONTROL, "a", Keys.BACKSPACE)
        campo.send_keys(valor)

        # 🛑 ESPERA O LOADING SUMIR ANTES DE PESQUISAR
        try:
            wait.until(
                EC.invisibility_of_element_located(
                    (By.CLASS_NAME, "loading-new")
                )
            )
        except TimeoutException:
            pass  # se não aparecer loading, segue a vida

        # Pesquisar
        wait.until(
            EC.element_to_be_clickable((By.XPATH, xpath_btn_pesquisar))
        ).click()

        try:
            # Espera curta só para o checkbox
            checkbox = wait_curto.until(
                EC.element_to_be_clickable((By.XPATH, xpath_checkbox))
            )
            checkbox.click()

            # 🛑 ESPERA O LOADING SUMIR ANTES DE CONFIRMAR
            try:
                wait.until(
                    EC.invisibility_of_element_located(
                        (By.CLASS_NAME, "loading-new")
                    )
                )
            except TimeoutException:
                pass

            wait.until(
                EC.element_to_be_clickable((By.XPATH, xpath_btn_confirmar))
            ).click()

        except TimeoutException:
            # Não achou rápido → pula
            pass

        # Limpa campo imediatamente
        campo.send_keys(Keys.CONTROL, "a", Keys.BACKSPACE)
print("⚡ Processamento rápido finalizado")

import tkinter as tk
import tkinter.messagebox as msg

def mensagem_final():
    root = tk.Tk()
    root.withdraw()                 # janela invisível
    root.attributes("-topmost", True)  # fica na frente de tudo

    msg.showinfo(
        "Finalizado",
        "Processo concluído com sucesso!",
        parent=root
    )

    root.destroy()

# ---- FINAL DO SEU CÓDIGO ----
mensagem_final()


input()

