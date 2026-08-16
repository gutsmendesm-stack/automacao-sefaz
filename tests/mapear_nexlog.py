"""
Mapeador do Nexlog — captura a estrutura real das paginas.

Faz login, navega em cada tela usada pela automacao, e salva:
- O HTML completo da pagina
- Um resumo dos elementos relevantes (inputs, botoes, tabelas, dropdowns)

Os arquivos ficam em: %APPDATA%/automacao_sefaz/mapeamento/
Depois disso, os HTMLs podem ser analisados pra construir seletores
precisos em vez de depender de multiplos fallbacks por tentativa e erro.

Uso: python tests/mapear_nexlog.py
"""

import os
import sys
import json
from datetime import datetime
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from selenium.webdriver.common.by import By

from config import config, pausa, PASTA_CONFIG
from modules.browser import NexlogBrowser

PASTA_MAP = PASTA_CONFIG / "mapeamento"
PASTA_MAP.mkdir(parents=True, exist_ok=True)


def salvar_html(driver, nome: str):
    """Salva o HTML completo da pagina atual."""
    caminho = PASTA_MAP / f"{nome}.html"
    try:
        with open(caminho, "w", encoding="utf-8") as f:
            f.write(driver.page_source)
        tamanho_kb = caminho.stat().st_size // 1024
        print(f"    HTML salvo: {nome}.html ({tamanho_kb} KB)")
    except Exception as e:
        print(f"    ERRO ao salvar HTML: {e}")


def resumir_elementos(driver, nome: str):
    """
    Extrai um resumo dos elementos interativos da pagina.
    Isso e o que realmente importa pra construir seletores.
    """
    resumo = {
        "pagina": nome,
        "url": driver.current_url,
        "titulo": driver.title,
        "capturado_em": datetime.now().isoformat(),
        "inputs": [],
        "botoes": [],
        "links_acao": [],
        "tabelas": [],
        "selects": [],
    }

    # --- INPUTS ---
    try:
        for inp in driver.find_elements(By.TAG_NAME, "input"):
            try:
                if not inp.is_displayed():
                    continue
                resumo["inputs"].append({
                    "id": inp.get_attribute("id") or "",
                    "name": inp.get_attribute("name") or "",
                    "type": inp.get_attribute("type") or "text",
                    "placeholder": inp.get_attribute("placeholder") or "",
                    "class": (inp.get_attribute("class") or "")[:80],
                })
            except Exception:
                continue
    except Exception:
        pass

    # --- BOTOES ---
    try:
        for btn in driver.find_elements(By.TAG_NAME, "button"):
            try:
                if not btn.is_displayed():
                    continue
                resumo["botoes"].append({
                    "id": btn.get_attribute("id") or "",
                    "texto": (btn.text or "").strip()[:50],
                    "class": (btn.get_attribute("class") or "")[:80],
                    "data_click": btn.get_attribute("data-click") or "",
                })
            except Exception:
                continue
    except Exception:
        pass

    # --- LINKS COM data-click (acoes do Nexlog) ---
    try:
        for link in driver.find_elements(By.XPATH, "//a[@data-click]"):
            try:
                resumo["links_acao"].append({
                    "data_click": link.get_attribute("data-click") or "",
                    "texto": (link.get_attribute("textContent") or "").strip()[:50],
                    "class": (link.get_attribute("class") or "")[:60],
                    "visivel": link.is_displayed(),
                })
            except Exception:
                continue
    except Exception:
        pass

    # --- TABELAS (id + cabecalhos) ---
    try:
        for tab in driver.find_elements(By.TAG_NAME, "table"):
            try:
                if not tab.is_displayed():
                    continue
                headers = []
                for th in tab.find_elements(By.TAG_NAME, "th"):
                    headers.append((th.text or "").strip()[:30])
                resumo["tabelas"].append({
                    "id": tab.get_attribute("id") or "",
                    "class": (tab.get_attribute("class") or "")[:80],
                    "colunas": headers,
                    "total_linhas": len(tab.find_elements(By.XPATH, ".//tbody//tr")),
                })
            except Exception:
                continue
    except Exception:
        pass

    # --- SELECTS / SELECT2 ---
    try:
        for sel in driver.find_elements(By.XPATH,
            "//select | //*[contains(@class,'select2')]"
        ):
            try:
                if not sel.is_displayed():
                    continue
                resumo["selects"].append({
                    "id": sel.get_attribute("id") or "",
                    "class": (sel.get_attribute("class") or "")[:80],
                    "texto": (sel.text or "").strip()[:40],
                })
            except Exception:
                continue
    except Exception:
        pass

    caminho = PASTA_MAP / f"{nome}_elementos.json"
    with open(caminho, "w", encoding="utf-8") as f:
        json.dump(resumo, f, indent=2, ensure_ascii=False)

    print(f"    Resumo: {len(resumo['inputs'])} inputs, "
          f"{len(resumo['botoes'])} botoes, "
          f"{len(resumo['links_acao'])} acoes, "
          f"{len(resumo['tabelas'])} tabelas")


def capturar(driver, nome: str):
    """Captura HTML + resumo de elementos da pagina atual."""
    print(f"  Capturando: {nome}")
    salvar_html(driver, nome)
    resumir_elementos(driver, nome)


def capturar_menu_completo(driver):
    """
    Extrai a arvore completa de navegacao do Nexlog (#mainMenu).
    Gera um catalogo de TODAS as telas do sistema com URL + label.
    Isso permite descobrir funcionalidades ainda nao automatizadas.
    """
    print("  Capturando menu completo (arvore de navegacao)...")

    itens = []
    try:
        links = driver.find_elements(By.XPATH, "//*[@id='mainMenu']//a[@href]")
        for link in links:
            try:
                href = link.get_attribute("href") or ""
                texto = (link.get_attribute("textContent") or "").strip()
                texto = " ".join(texto.split())  # normaliza espacos
                if not href or href.endswith("#") or not texto:
                    continue
                # Tenta identificar o modulo pai (ex: Vendas, Operacoes)
                modulo = ""
                try:
                    pai = link.find_element(By.XPATH,
                        "ancestor::li[contains(@class,'menu-module') or contains(@class,'dropdown')][1]"
                    )
                    modulo = (pai.get_attribute("data-module")
                              or pai.get_attribute("title") or "").strip()
                except Exception:
                    pass
                itens.append({"modulo": modulo, "tela": texto, "url": href})
            except Exception:
                continue
    except Exception as e:
        print(f"    Erro ao ler menu: {e}")

    # Remove duplicatas
    unicos = {}
    for item in itens:
        unicos[item["url"]] = item
    lista = sorted(unicos.values(), key=lambda x: (x["modulo"], x["tela"]))

    caminho = PASTA_MAP / "00_menu_completo.json"
    with open(caminho, "w", encoding="utf-8") as f:
        json.dump({"total": len(lista), "telas": lista}, f, indent=2, ensure_ascii=False)

    print(f"    {len(lista)} tela(s) mapeada(s) no menu -> 00_menu_completo.json")


def main():
    print("=" * 60)
    print("  MAPEADOR DO NEXLOG")
    print("=" * 60)
    print(f"\nSaida: {PASTA_MAP}\n")

    if not config.nexlog.usuario or not config.nexlog.senha:
        print("ERRO: Credenciais do Nexlog nao configuradas.")
        print("Abra o programa principal, va em Config e salve suas credenciais.")
        sys.exit(1)

    browser = NexlogBrowser()

    try:
        print("Iniciando navegador e fazendo login...")
        browser.iniciar()
        browser.login_nexlog()
        driver = browser.driver
        print("Login OK\n")

        # --- 1. Gerenciar Rotas (Recebimento) ---
        print("[1/5] Gerenciar Rotas (Recebimento)")
        browser.navegar_operacoes_gerenciar_rotas()
        pausa(3)
        capturar(driver, "01_gerenciar_rotas")

        # Catalogo completo do menu (todas as telas do sistema)
        capturar_menu_completo(driver)

        # Pesquisa voos de hoje pra tabela popular e dropdown existir
        hoje = datetime.now().strftime("%d/%m/%Y")
        try:
            from modules.nexlog_voos import NexlogVoos
            voos_mod = NexlogVoos(browser)
            voos = voos_mod.pesquisar_voos(hoje, hoje)
            print(f"    {len(voos)} voo(s) encontrado(s) — capturando com tabela cheia")
            capturar(driver, "02_gerenciar_rotas_com_voos")

            # Abre o dropdown de acoes do primeiro voo pra capturar a estrutura
            if voos:
                print("    Abrindo dropdown de acoes do 1o voo...")
                linha = voos_mod._encontrar_linha_voo(voos[0].numero_controle)
                if linha is not None:
                    try:
                        btn = linha.find_element(By.XPATH, ".//td[last()]//button | .//td[last()]")
                        btn.click()
                        pausa(2)
                        capturar(driver, "03_dropdown_acoes_aberto")
                    except Exception as e:
                        print(f"    Nao conseguiu abrir dropdown: {e}")
        except Exception as e:
            print(f"    Erro ao pesquisar voos: {e}")

        # --- 2. Retencao (a tela que mais da erro) ---
        print("\n[2/4] Retencao (tela de liberacao)")
        browser.navegar_vendas_retencao_lista()
        pausa(4)
        capturar(driver, "04_retencao_lista")

        # --- 3. Conhecimento / Lista ---
        print("\n[3/4] Conhecimento / Lista")
        browser.navegar_vendas_conhecimento_lista()
        pausa(4)
        capturar(driver, "05_conhecimento_lista")

        # Aba "Por referencia" (usada pra buscar AWB por CTe)
        try:
            aba = driver.find_element(By.XPATH, "//a[contains(.,'Por refer')]")
            aba.click()
            pausa(2)
            capturar(driver, "06_conhecimento_por_referencia")
        except Exception as e:
            print(f"    Aba 'Por referencia' nao encontrada: {e}")

        # --- 4. Coletas/Entregas (URL corrigida: DispatchManagement) ---
        print("\n[4/5] Coletas/Entregas (Gerenciar coletas/entregas)")
        try:
            browser.navegar_coletas_entregas()
            pausa(4)
            capturar(driver, "07_coletas_entregas")
        except Exception as e:
            print(f"    Erro: {e}")

        # --- 5. Telas extras (uteis pra novas funcionalidades) ---
        print("\n[5/5] Telas extras")
        from modules.browser import URL_BASE, ROTAS

        extras = [
            ("08_cargas_em_posse", ROTAS["cargas_em_posse"]),
            ("09_relatorio_cargas_posse", ROTAS["relatorio_cargas_posse"]),
            ("10_encontrar_carga", ROTAS["encontrar_carga"]),
            ("11_roteirizar", ROTAS["roteirizar"]),
            ("12_dashboard_entregas", ROTAS["dashboard_entregas"]),
            ("13_gerar_lista", ROTAS["gerar_lista"]),
        ]

        for nome, rota in extras:
            try:
                browser.navegar_url(f"{URL_BASE}{rota}")
                pausa(3)
                titulo = driver.title or ""
                if "cannot be found" in titulo.lower() or "error" in titulo.lower():
                    print(f"  {nome}: URL invalida ({rota}) - pulando")
                    continue
                capturar(driver, nome)
            except Exception as e:
                print(f"  {nome}: erro - {str(e)[:60]}")

        print("\n" + "=" * 60)
        print("  MAPEAMENTO CONCLUIDO")
        print("=" * 60)
        print(f"\nArquivos salvos em:\n{PASTA_MAP}\n")
        print("Arquivos gerados:")
        for arq in sorted(PASTA_MAP.iterdir()):
            print(f"  {arq.name}")

    except Exception as e:
        print(f"\nERRO: {e}")
    finally:
        try:
            browser.fechar()
        except Exception:
            pass


if __name__ == "__main__":
    main()
