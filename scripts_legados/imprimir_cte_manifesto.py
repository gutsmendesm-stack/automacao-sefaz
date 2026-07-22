from selenium import webdriver
from selenium.webdriver.common.by import By
from selenium.webdriver.common.keys import Keys
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.common.exceptions import TimeoutException
import time
import os
import pyautogui

# ========= CONFIG =========
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

appdata = os.getenv("APPDATA")
pasta = os.path.join(appdata, "documentos complementares")
os.makedirs(pasta, exist_ok=True)

ARQUIVO_CTES = os.path.join(pasta, "cte manifesto.txt")


# ========= LÊ OS CTES =========
with open(ARQUIVO_CTES, "r") as f:
    ctes = [linha.strip() for linha in f if linha.strip()]

# Guardar o ID da aba principal antes de começar o loop
aba_principal = navegador.current_window_handle

# Guardar o ID da aba principal antes de começar o loop
aba_principal = navegador.current_window_handle

# ========= LOOP =========
for cte in ctes:
    # 1. Volta para a aba principal
    navegador.switch_to.window(aba_principal)
    
    # Força o foco do Windows de volta para o navegador (evita erro de interatividade)
    navegador.maximize_window() 
    time.sleep(0.8)

    # 2. Pressiona ESC e aguarda
    pyautogui.press("esc")
    time.sleep(1)

    # 3. Localiza o campo com espera de visibilidade
    # Mudamos de 'element_to_be_clickable' para garantir que ele esteja visível e pronto
    campo = wait.until(EC.visibility_of_element_located((By.ID, "quickSearch")))
    
    # 4. Ações de segurança para interagir
    campo.click() # Clica para dar foco interno ao elemento
    campo.clear()
    time.sleep(0.5) # Pequena pausa para o site processar o clear
    
    # 5. Digita o CTe
    campo.send_keys(cte)

    # CLICA NO RASTREIO
    botao_rastreio = wait.until(EC.element_to_be_clickable((By.ID, "quickTracking-icon")))
    botao_rastreio.click()

    # AGUARDA BOTÃO GERAR PDF
    botao_pdf = wait.until(EC.element_to_be_clickable((By.XPATH, "//a[contains(., 'Gerar pdf')]")))
    botao_pdf.click()

    # AGUARDA ABRIR A NOVA ABA (PDF)
    time.sleep(3)
    
    # PEGA TODAS AS ABAS ABERTAS E MUDA PARA A DO PDF
    abas_abertas = navegador.window_handles
    for aba in abas_abertas:
        if aba != aba_principal:
            navegador.switch_to.window(aba)
            break

    time.sleep(5)

    # COMANDOS DE IMPRESSÃO
    pyautogui.hotkey("ctrl", "p")
    time.sleep(2)
    pyautogui.press("enter")
    time.sleep(2) 

    # FECHA A ABA DO PDF E VOLTA
    navegador.close() 
    navegador.switch_to.window(aba_principal)

print("Processo finalizado.")
input()