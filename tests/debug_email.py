"""
Debug: abre Outlook, clica Novo Email, e lista todos os elementos interativos.
Uso: python tests/debug_email.py
"""
import sys, os, time
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from modules.browser import NexlogBrowser
from modules.outlook import OutlookWeb
from selenium.webdriver.common.by import By

browser = NexlogBrowser()
browser.iniciar()

outlook = OutlookWeb(browser.driver)
outlook.abrir_outlook()
time.sleep(3)

# Fecha email aberto se tiver
outlook._fechar_email_aberto()
time.sleep(2)

print("\n=== Clicando em Novo Email... ===")
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC

try:
    btn = WebDriverWait(browser.driver, 10).until(
        EC.element_to_be_clickable((By.XPATH,
            "//button[contains(@aria-label,'Novo email') or "
            "contains(@aria-label,'New mail') or "
            "contains(@aria-label,'Novo E-mail') or "
            "contains(@aria-label,'Nova mensagem')]"
            " | //button[contains(@title,'Novo email') or "
            "contains(@title,'New mail')]"
            " | //span[contains(text(),'Novo') or contains(text(),'New')]"
            "/ancestor::button[contains(@class,'splitButton') or contains(@class,'compose')]"
        ))
    )
    print(f"Botao encontrado: aria-label='{btn.get_attribute('aria-label')}', text='{btn.text[:30]}'")
    btn.click()
except Exception as e:
    print(f"ERRO ao achar botao Novo Email: {e}")
    # Tenta qualquer botao com "Novo"
    btns = browser.driver.find_elements(By.XPATH, "//button")
    for b in btns:
        al = b.get_attribute('aria-label') or ''
        if 'novo' in al.lower() or 'new' in al.lower():
            print(f"  Candidato: aria-label='{al}', visible={b.is_displayed()}")

time.sleep(5)

print("\n=== Verificando janelas abertas... ===")
handles = browser.driver.window_handles
print(f"Janelas: {len(handles)}")
for h in handles:
    browser.driver.switch_to.window(h)
    print(f"  - {h}: title='{browser.driver.title}', url={browser.driver.current_url[:60]}")

# Volta pra ultima janela
browser.driver.switch_to.window(handles[-1])

print("\n=== Buscando campo 'Para'... ===")
time.sleep(2)

# Busca todos os divs role=textbox
divs_textbox = browser.driver.find_elements(By.XPATH, "//div[@role='textbox']")
print(f"DIVs com role=textbox: {len(divs_textbox)}")
for d in divs_textbox:
    al = d.get_attribute('aria-label') or '(sem label)'
    vis = d.is_displayed()
    print(f"  - aria-label='{al}', visible={vis}")

# Busca inputs visiveis
inputs = browser.driver.find_elements(By.XPATH, "//input")
vis_inputs = [i for i in inputs if i.is_displayed()]
print(f"\nInputs visiveis: {len(vis_inputs)}")
for i in vis_inputs:
    al = i.get_attribute('aria-label') or ''
    ph = i.get_attribute('placeholder') or ''
    print(f"  - aria-label='{al}', placeholder='{ph}'")

print("\n=== Pressione ENTER para fechar ===")
input()
browser.fechar()
