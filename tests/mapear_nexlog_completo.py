"""
Mapeador COMPLETO do Nexlog — visita TODAS as telas do menu.

Le o catalogo gerado pelo mapeamento anterior (00_menu_completo.json)
e visita cada URL que e uma pagina navegavel real (ignora JavaScript,
links de download, links de acao que alteram dados).

Uso: python tests/mapear_nexlog_completo.py

SEGURANCA: Apenas navega e le. Nao clica em botoes de acao,
nao preenche campos, nao submete formularios.
"""

import os
import sys
import json
import re
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from config import config, pausa, PASTA_CONFIG
from modules.browser import NexlogBrowser, URL_BASE

PASTA_MAP = PASTA_CONFIG / "mapeamento"
PASTA_MAP.mkdir(parents=True, exist_ok=True)
CATALOGO = PASTA_MAP / "00_menu_completo.json"


def eh_url_navegavel(url: str) -> bool:
    """
    Filtra URLs que sao paginas reais (navegaveis).
    Exclui:
    - JavaScript (funcoes, nao paginas)
    - Links de download de apps
    - Links que alteram dados (Create, Edit, etc que nao sejam listas)
    """
    if not url:
        return False
    # Exclui JavaScript embutido
    if "javascript:" in url or "window." in url or "Backoffice.Controllers" in url:
        return False
    if "appController" in url or "setLanguage" in url or "downloadApp" in url:
        return False
    # Exclui paginas de criacao/edicao (podem alterar dados se clicar em algo)
    # Mantemos apenas as que tem /Create no final pra visualizacao (tipo Encontrar carga)
    urls_criar_ok = ["/Identification/Create", "/Damage/Create"]
    if "/Create" in url and not any(ok in url for ok in urls_criar_ok):
        return False
    # Exclui paginas legacy ASP (instáveis)
    if "_legacy" in url:
        return False
    # Exclui URLs que nao comecam com a base do nexlog
    if not url.startswith("https://golcargo.nexlog.com"):
        return False
    return True


def nome_seguro(tela: str, url: str) -> str:
    """Gera nome de arquivo seguro a partir da tela + URL."""
    # Usa o path da URL como base
    path = url.replace("https://golcargo.nexlog.com", "").strip("/")
    path = path.replace("/", "_").replace("#", "_").replace("?", "_")
    # Limpa caracteres invalidos
    path = re.sub(r'[^\w\-]', '_', path)
    # Limita tamanho
    if len(path) > 50:
        path = path[:50]
    return path


def salvar_html(driver, nome: str):
    """Salva o HTML completo da pagina atual."""
    caminho = PASTA_MAP / f"{nome}.html"
    try:
        with open(caminho, "w", encoding="utf-8") as f:
            f.write(driver.page_source)
        tamanho_kb = caminho.stat().st_size // 1024
        print(f"      HTML: {tamanho_kb} KB")
    except Exception as e:
        print(f"      ERRO HTML: {e}")


def resumir_elementos(driver, nome: str):
    """Extrai resumo dos elementos interativos."""
    from selenium.webdriver.common.by import By

    resumo = {
        "url": driver.current_url,
        "titulo": driver.title,
        "inputs": [],
        "botoes": [],
        "tabelas": [],
        "selects": [],
    }

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
                })
            except Exception:
                continue
    except Exception:
        pass

    try:
        for btn in driver.find_elements(By.TAG_NAME, "button"):
            try:
                if not btn.is_displayed():
                    continue
                resumo["botoes"].append({
                    "id": btn.get_attribute("id") or "",
                    "texto": (btn.text or "").strip()[:50],
                    "class": (btn.get_attribute("class") or "")[:60],
                })
            except Exception:
                continue
    except Exception:
        pass

    try:
        for tab in driver.find_elements(By.TAG_NAME, "table"):
            try:
                if not tab.is_displayed():
                    continue
                headers = [th.text.strip()[:30] for th in tab.find_elements(By.TAG_NAME, "th")]
                resumo["tabelas"].append({
                    "id": tab.get_attribute("id") or "",
                    "colunas": headers[:15],
                })
            except Exception:
                continue
    except Exception:
        pass

    try:
        for sel in driver.find_elements(By.TAG_NAME, "select"):
            try:
                if not sel.is_displayed():
                    continue
                resumo["selects"].append({
                    "id": sel.get_attribute("id") or "",
                    "name": sel.get_attribute("name") or "",
                })
            except Exception:
                continue
    except Exception:
        pass

    caminho = PASTA_MAP / f"{nome}_elementos.json"
    with open(caminho, "w", encoding="utf-8") as f:
        json.dump(resumo, f, indent=2, ensure_ascii=False)

    n_inputs = len(resumo["inputs"])
    n_botoes = len(resumo["botoes"])
    n_tabelas = len(resumo["tabelas"])
    print(f"      Elementos: {n_inputs} inputs, {n_botoes} botoes, {n_tabelas} tabelas")


def main():
    print("=" * 60)
    print("  MAPEADOR COMPLETO DO NEXLOG")
    print("=" * 60)

    if not CATALOGO.exists():
        print(f"\nERRO: Catalogo nao encontrado em {CATALOGO}")
        print("Rode primeiro: python tests/mapear_nexlog.py")
        sys.exit(1)

    with open(CATALOGO, "r", encoding="utf-8") as f:
        catalogo = json.load(f)

    todas_urls = catalogo.get("telas", [])

    # Filtra apenas URLs navegaveis
    urls_validas = [(item["tela"], item["url"]) for item in todas_urls if eh_url_navegavel(item["url"])]

    print(f"\nTotal no catalogo: {len(todas_urls)}")
    print(f"URLs navegaveis: {len(urls_validas)}")
    print(f"Saida: {PASTA_MAP}\n")

    if not config.nexlog.usuario or not config.nexlog.senha:
        print("ERRO: Credenciais do Nexlog nao configuradas.")
        sys.exit(1)

    browser = NexlogBrowser()

    try:
        print("Iniciando navegador e fazendo login...")
        browser.iniciar()
        browser.login_nexlog()
        driver = browser.driver
        print("Login OK\n")

        sucesso = 0
        erros = 0

        for i, (tela, url) in enumerate(urls_validas, 1):
            nome = f"full_{i:02d}_{nome_seguro(tela, url)}"
            print(f"  [{i}/{len(urls_validas)}] {tela}")
            print(f"      URL: {url}")

            try:
                driver.get(url)
                pausa(4)

                # Verifica se a pagina carregou (nao e 404)
                titulo = driver.title or ""
                if "cannot be found" in titulo.lower() or "error" in titulo.lower():
                    print(f"      PULADO (404/erro)")
                    erros += 1
                    continue

                salvar_html(driver, nome)
                resumir_elementos(driver, nome)
                sucesso += 1

            except Exception as e:
                print(f"      ERRO: {str(e)[:60]}")
                erros += 1

        print(f"\n{'=' * 60}")
        print(f"  MAPEAMENTO COMPLETO CONCLUIDO")
        print(f"  Sucesso: {sucesso} | Erros/Pulados: {erros}")
        print(f"{'=' * 60}")
        print(f"\nArquivos em: {PASTA_MAP}")

    except Exception as e:
        print(f"\nERRO CRITICO: {e}")
    finally:
        try:
            browser.fechar()
        except Exception:
            pass


if __name__ == "__main__":
    main()
