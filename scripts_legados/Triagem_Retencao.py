import tkinter as tk
from tkinter import messagebox, filedialog
import os
from selenium import webdriver
from selenium.common.exceptions import TimeoutException
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.common.keys import Keys
from selenium.webdriver.common.action_chains import ActionChains
import time
import pandas as pd
from datetime import datetime
import re

# --------- CONFIGURAÇÕES DE CAMINHO ---------
appdata = os.getenv("APPDATA")
pasta = os.path.join(appdata, "documentos complementares")
os.makedirs(pasta, exist_ok=True)
arquivo_login = os.path.join(pasta, "login_salvo.txt")

# --------- FUNÇÕES DA INTERFACE (GUI) ---------
def selecionar_arquivo():
    caminho = filedialog.askopenfilename(filetypes=[
        ("Planilhas", "*.xlsx *.xls *.csv"),
        ("Arquivos Excel", "*.xlsx *.xls"),
        ("Arquivos CSV", "*.csv")
    ])
    if caminho:
        caminho_var.set(caminho)

def iniciar_processo():
    login, senha = login_var.get().strip(), senha_var.get().strip()
    caminho_arquivo = caminho_var.get().strip()
    if not all([login, senha, caminho_arquivo]):
        messagebox.showwarning("Aviso", "Preencha login, senha e selecione o arquivo.")
        return
    with open(arquivo_login, "w") as f: f.write(login)
    janela.destroy()
    executar_automacao(login, senha, caminho_arquivo)

# --------- AUTOMAÇÃO ---------
def executar_automacao(login, senha, caminho_arquivo):
    try:
        extensao = caminho_arquivo.lower()
        if extensao.endswith('.csv'):
            df_entrada = pd.read_csv(caminho_arquivo, sep=None, engine='python', encoding='utf-8-sig')
        else:
            df_entrada = pd.read_excel(caminho_arquivo)

        colunas = [c.lower() for c in df_entrada.columns]
        if 'identificação' not in colunas:
            messagebox.showerror("Erro", "A planilha deve ter uma coluna chamada 'identificação'.")
            return
        
        nome_real_coluna = df_entrada.columns[colunas.index('identificação')]
        lista_ctes = df_entrada[nome_real_coluna].dropna()
        lista_ctes = pd.to_numeric(lista_ctes, errors='coerce').fillna(0).astype(int)
        lista_ctes = [str(cte) for cte in lista_ctes.tolist() if cte > 0]
        print(f"Sucesso! {len(lista_ctes)} CTEs carregados.")
        
    except Exception as e:
        messagebox.showerror("Erro ao ler arquivo", str(e))
        return

    navegador = webdriver.Chrome()
    navegador.maximize_window()
    wait = WebDriverWait(navegador, 20)
    dados_finais = []

    try:
        # 1. LOGIN
        navegador.get("https://golcargo.gollog.com.br/account/#/login" )
        wait.until(EC.element_to_be_clickable((By.ID, "user"))).send_keys(login)
        wait.until(EC.element_to_be_clickable((By.ID, "password"))).send_keys(senha)
        wait.until(EC.element_to_be_clickable((By.ID, "franchise"))).send_keys("MCZ")
        navegador.find_element(By.XPATH, "//*[@id='div-btns']/button").click()

        try:
            WebDriverWait(navegador, 4).until(EC.element_to_be_clickable((By.XPATH, "/html/body/div/div/div/button[2]"))).click()
        except: pass

        time.sleep(8)

        # 2. LOOP DE CONSULTA
        for cte in lista_ctes:
            print(f"Consultando CTe: {cte}")
            try:
                # LIMPEZA DE MODAIS (Garante que a tela está livre)
                for _ in range(2): 
                    ActionChains(navegador).send_keys(Keys.ESCAPE).perform()
                    time.sleep(0.5)
                
                navegador.execute_script("""
                    var backdrops = document.getElementsByClassName('modal-backdrop');
                    while(backdrops.length > 0){ backdrops[0].parentNode.removeChild(backdrops[0]); }
                    document.body.classList.remove('modal-open');
                    var modals = document.getElementsByClassName('modal');
                    for(var i=0; i<modals.length; i++) { modals[i].style.display = 'none'; }
                """)
                time.sleep(2)

                # Busca rápida
                busca_xpath = "//input[contains(@placeholder, 'Pesquisa rápida')] | //input[@id='search-input']"
                busca = wait.until(EC.presence_of_element_located((By.XPATH, busca_xpath)))
                try:
                    busca.click()
                except:
                    navegador.execute_script("arguments[0].click(); arguments[0].focus();", busca)
                
                busca.send_keys(Keys.CONTROL, "a", Keys.BACKSPACE)
                busca.send_keys(cte, Keys.ENTER)
                
                time.sleep(6)

                # Clique na aba Comentários (Seu XPATH)
                xpath_comentarios = '//*[@id="trackingAWB"]/div[1]/div[1]/div/div/div[2]/div/div[3]/div/div[1]/a'
                aba = wait.until(EC.element_to_be_clickable((By.XPATH, xpath_comentarios)))
                navegador.execute_script("arguments[0].click();", aba)
                
                print("   Aguardando histórico...")
                time.sleep(4)
                
                # EXTRAÇÃO DETETIVE
                texto_acumulado = ""
                tabelas = navegador.find_elements(By.TAG_NAME, "table")
                for t in tabelas:
                    if t.is_displayed(): texto_acumulado += t.text.upper() + " "
                
                if len(texto_acumulado) < 10:
                    divs = navegador.find_elements(By.XPATH, "//div[contains(@class, 'tab-pane') and contains(@class, 'active')]")
                    for d in divs: texto_acumulado += d.text.upper() + " "

                # CLASSIFICAÇÃO (Versão Multi-Termos e Regex)
                texto_para_busca = texto_acumulado.upper()
                padrao_fiscal = r'\bTA\b|\bTERMO\b'
                
                encontrou_fiscal = "SEFAZ" in texto_para_busca or \
                                  "RETIDO SEFAZ" in texto_para_busca or \
                                  re.search(padrao_fiscal, texto_para_busca)

                status = "Retido Sefaz" if encontrou_fiscal else "Retido Analise Fiscal"
                dados_finais.append({"identificação": cte, "Status": status})
                print(f"   -> Resultado: {status}")

            except Exception as e:
                print(f"   -> Erro no CTe {cte}: {e}")
                dados_finais.append({"identificação": cte, "Status": "Erro na consulta"})

        # 3. SALVAR RESULTADO
        if dados_finais:
            df_resultado = pd.DataFrame(dados_finais)
            timestamp = datetime.now().strftime("%H%M%S")
            nome_arquivo = f"Resultado_Triagem_{timestamp}.xlsx"
            path_saida = os.path.join(os.path.expanduser("~"), "Downloads", nome_arquivo)
            df_resultado.to_excel(path_saida, index=False)
            messagebox.showinfo("Sucesso", f"Processamento concluído!\nArquivo salvo: {nome_arquivo}")

    except Exception as e:
        messagebox.showerror("Erro Crítico", str(e))
    finally:
        try: navegador.quit()
        except: pass
        os._exit(0)

# --------- INTERFACE ---------
if __name__ == "__main__":
    login_inicial = ""
    if os.path.exists(arquivo_login):
        try:
            with open(arquivo_login, "r") as f: login_inicial = f.read().strip()
        except: pass

    janela = tk.Tk()
    janela.title("Triagem Gollog (Excel/CSV)")
    janela.geometry("400x450")
    login_var, senha_var = tk.StringVar(value=login_inicial), tk.StringVar()
    caminho_var = tk.StringVar()
    tk.Label(janela, text="Login:").pack(pady=5)
    tk.Entry(janela, textvariable=login_var).pack()
    tk.Label(janela, text="Senha:").pack(pady=5)
    tk.Entry(janela, textvariable=senha_var, show="*").pack()
    tk.Label(janela, text="Arquivo de Entrada (Excel ou CSV):").pack(pady=20)
    tk.Button(janela, text="Selecionar Planilha", command=selecionar_arquivo).pack()
    tk.Label(janela, textvariable=caminho_var, fg="blue", wraplength=350).pack(pady=5)
    tk.Button(janela, text="INICIAR TRIAGEM", command=iniciar_processo, bg="green", fg="white", font=("Arial", 10, "bold")).pack(pady=30)
    janela.mainloop()
