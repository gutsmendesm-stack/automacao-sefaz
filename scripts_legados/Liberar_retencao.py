from selenium import webdriver
from tkinter import messagebox
import os
import tkinter as tk
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
caminho_txt = os.path.join(pasta, "liberar retencao.txt")

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



#abrir navegador
navegador = webdriver.Chrome()
navegador.maximize_window()
#acessar nexlog
navegador.get("https://golcargo.gollog.com.br/account/#/login")

time.sleep(4)

wait = WebDriverWait(navegador, 15)

campo_user = wait.until(
    EC.presence_of_element_located((By.XPATH, "//*[@id='user']"))
)
campo_user.click()
campo_user.send_keys("12890393488")

wait = WebDriverWait(navegador, 15)
campo_senha = navegador.find_element(By.XPATH, "//*[@id='password']")
campo_senha.click()
campo_senha.send_keys("Pietro*300121")

base = navegador.find_element(By.XPATH, "//*[@id='franchise']")
base.click()
base.send_keys("MCZ")

botao_login = navegador.find_element(By.XPATH, "//*[@id='div-btns']/button")
botao_login.click()
time.sleep(1)
try:
    wait_msg = WebDriverWait(navegador, 3)  # curto
    botao_continuar = wait_msg.until(
        EC.element_to_be_clickable(
            (By.XPATH, "/html/body/div/div/div/button[2]")
        )
    )
    botao_continuar.click()
    print("Mensagem de sessão detectada, continuando")

except TimeoutException:
    # mensagem não apareceu
    pass
time.sleep(3)


botao_operacoes = navegador.find_element(By.XPATH, "//*[@id='mainMenu']/div[1]/div/ul/li[3]/div/div/div[1]/div")
action = ActionChains(navegador)
action.move_to_element(botao_operacoes).perform()
time.sleep(1)


opcao_lista = wait.until(
    EC.element_to_be_clickable(
        (By.XPATH, "//a[@href='/Sales/Retention/' and .//span[normalize-space()='Lista']]")
    )
)
opcao_lista.click()

time.sleep(3)


selecionar_data = navegador.find_element(By.XPATH, "//*[@id='StartDate']")
selecionar_data.click()
selecionar_data.send_keys("01")
selecionar_data.send_keys(Keys.ENTER)

time.sleep(1)

wait = WebDriverWait(navegador, 20)

# 1️⃣ Abre o Select2
wait.until(
    EC.element_to_be_clickable((By.XPATH, "//span[@id='select2-Status-container']"))
).click()

# 2️⃣ Aguarda a opção "Retida" aparecer e clica
opcao_retida = wait.until(
    EC.element_to_be_clickable((
        By.XPATH,
        "//li[contains(@class,'select2-results__option') and normalize-space()='Retida']"
    ))
)

# Clique normal
opcao_retida.click()

try:
    wait_msg = WebDriverWait(navegador, 3)  # curto
    botao_continuar = wait_msg.until(
        EC.element_to_be_clickable(
            (By.XPATH, "//*[@id='searchButton']")
        )
    )
    botao_continuar.click()

except TimeoutException:
    
    pass

time.sleep(5)

xpath_input = "//*[@id='RetentionList_filter']/label/input"
xpath_menu = "//div[contains(@class,'divDataTableSelection')]"
xpath_opcao_tudo = "//span[contains(@class,'DataTableSelectionAll')]"

wait = WebDriverWait(navegador, 20)

with open(caminho_txt, "r", encoding="utf-8") as arquivo:
    for linha in arquivo:
        valor = linha.strip()

        if not valor:
            continue

        # 1️⃣ Espera o input
        campo = wait.until(
            EC.element_to_be_clickable((By.XPATH, xpath_input))
        )

        # 2️⃣ Limpa o input corretamente
        campo.click()
        campo.send_keys(Keys.CONTROL, "a")
        campo.send_keys(Keys.BACKSPACE)

        # 3️⃣ Cola a linha do TXT
        campo.send_keys(valor)

        print(f"Linha colada: {valor}")

        time.sleep(2)  # tempo para a tabela filtrar

        # 4️⃣ EXECUTA EXATAMENTE O SEU CÓDIGO DO MENU
        menu = wait.until(
            EC.element_to_be_clickable((By.XPATH, xpath_menu))
        )
        menu.click()

        opcao = wait.until(
            EC.visibility_of_element_located((By.XPATH, xpath_opcao_tudo))
        )
        opcao.click()

        print("Menu 'Tudo' clicado")

        # 5️⃣ Pequena pausa antes da próxima linha
        time.sleep(2)

print("✅ Arquivo TXT processado completamente")


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