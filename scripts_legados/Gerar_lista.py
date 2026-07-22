from selenium import webdriver
from tkinter import messagebox
from selenium.common.exceptions import TimeoutException
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.common.action_chains import ActionChains
from selenium.webdriver.common.keys import Keys
import tkinter as tk
import os
import tkinter.messagebox as msg
import time


appdata = os.getenv("APPDATA")
pasta = os.path.join(appdata, "documentos complementares")
os.makedirs(pasta, exist_ok=True)
caminho_txt = os.path.join(pasta, "gerar lista.txt")

def salvar_texto():
    texto = campo_texto.get("1.0", tk.END).strip()

    if not texto:
        messagebox.showwarning("Aviso", "O campo está vazio.")
        return

    try:
        with open(caminho_txt, "w", encoding="utf-8") as f:
            f.write(texto)
    except Exception as e:
        messagebox.showerror("Erro", str(e))


# --------- INTERFACE ---------
janela = tk.Tk()
janela.title("Colar códigos")
janela.geometry("300x600")

label = tk.Label(janela, text="Cole os códigos abaixo:")
label.pack(pady=5)

campo_texto = tk.Text(janela, wrap="word")
campo_texto.pack(expand=True, fill="both", padx=10, pady=5)

def salvar_e_fechar():
    salvar_texto()
    janela.destroy()

botao = tk.Button(janela, text="Salvar", command=salvar_e_fechar)
botao.pack(pady=10)

janela.mainloop()



# ======================
# INICIA NAVEGADOR
# ======================
navegador = webdriver.Chrome()
navegador.maximize_window()
wait = WebDriverWait(navegador, 20)

# ======================
# LOGIN
# ======================
navegador.get("https://golcargo.gollog.com.br/account/#/login")

campo_user = wait.until(
    EC.element_to_be_clickable((By.XPATH, "//*[@id='user']"))
)
campo_user.send_keys("12890393488")

campo_senha = wait.until(
    EC.element_to_be_clickable((By.XPATH, "//*[@id='password']"))
)
campo_senha.send_keys("Pietro*300121")

base = wait.until(
    EC.element_to_be_clickable((By.XPATH, "//*[@id='franchise']"))
)
base.send_keys("MCZ")

botao_login = wait.until(
    EC.element_to_be_clickable((By.XPATH, "//*[@id='div-btns']/button"))
)
botao_login.click()

# ======================
# MENSAGEM DE SESSÃO
# ======================
try:
    botao_continuar = WebDriverWait(navegador, 5).until(
        EC.element_to_be_clickable((By.XPATH, "/html/body/div/div/div/button[2]"))
    )
    botao_continuar.click()
except TimeoutException:
    pass

# ======================
# MENU OPERAÇÕES
# ======================
botao_operacoes = wait.until(
    EC.presence_of_element_located(
        (By.XPATH, "//*[@id='mainMenu']/div[1]/div/ul/li[4]/div/div/div[1]")
    )
)

ActionChains(navegador).move_to_element(botao_operacoes).perform()

opcao_lista = wait.until(
    EC.element_to_be_clickable(
        (By.XPATH, "//li[contains(@class,'menu-item')]//span[normalize-space()='Gerenciar coletas/entregas']")
    )
)
time.sleep(1)
opcao_lista.click()

# ======================
# FILTRO DE DATA
# ======================
time.sleep(2)

selecionar_data = wait.until(
    EC.element_to_be_clickable(
        (By.XPATH, "//*[@id='SchedulingStartDate']")
    )
)
selecionar_data.click()
selecionar_data.send_keys("01")
selecionar_data.send_keys(Keys.ENTER)

time.sleep(1)

selecionar_mes = wait.until(
    EC.element_to_be_clickable(
        (By.XPATH, "//*[@id='SchedulingEndDate']")
    )
)
selecionar_mes.click()
selecionar_mes.send_keys("30")
selecionar_mes.send_keys(Keys.ENTER)

botao_pesquisar = wait.until(
    EC.element_to_be_clickable(
        (By.XPATH, "//*[@id='searchButton']")
    )
)
botao_pesquisar.click()


xpath_input = "//label[contains(normalize-space(.), 'Pesquisar')]//input"
xpath_menu = "//div[contains(@class,'divDataTableSelection')]"
xpath_opcao_tudo = "//span[contains(@class,'DataTableSelectionAll')]"

with open(caminho_txt, "r", encoding="utf-8") as arquivo:
    for linha in arquivo:
        valor = linha.strip()
        if not valor:
            continue
        time.sleep(1)
        campo = wait.until(
            EC.element_to_be_clickable((By.XPATH, xpath_input))
        )
        time.sleep(2)
        campo.click()
        campo.send_keys(Keys.CONTROL, "a")
        campo.send_keys(Keys.BACKSPACE)
        campo.send_keys(valor)

        print(f"Linha colada: {valor}")

        # espera tabela atualizar
        time.sleep(1.5)

        menu = wait.until(
            EC.element_to_be_clickable((By.XPATH, xpath_menu))
        )
        navegador.execute_script(
            "arguments[0].scrollIntoView({block:'center'});", menu
        )
        menu.click()

        opcao = wait.until(
            EC.element_to_be_clickable((By.XPATH, xpath_opcao_tudo))
        )
        opcao.click()

        time.sleep(1)

print("✅ Arquivo TXT processado completamente")

# ======================
# MENSAGEM FINAL
# ======================
def mensagem_final():
    root = tk.Tk()
    root.withdraw()
    root.attributes("-topmost", True)

    msg.showinfo(
        "Finalizado",
        "Processo concluído com sucesso!",
        parent=root
    )
    root.destroy()

mensagem_final()

input("Pressione ENTER para encerrar...")
