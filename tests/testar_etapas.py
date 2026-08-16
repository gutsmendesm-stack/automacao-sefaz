"""
Teste Individual de Etapas - Automacao SEFAZ
=============================================
Permite testar cada modulo isoladamente para debugar seletores.

USO:
  python testar_etapas.py

Menu:
  1. Testar Login Nexlog
  2. Testar Busca de Voos
  3. Testar Extrair Chave MDF-e (Visualizar integracao MDFe)
  4. Testar Login SEFAZ
  5. Testar Consulta SEFAZ (campo de chave)
  6. Testar Outlook (buscar email)
  7. Testar Outlook (baixar PDF)
  8. Testar Comentario Critico (AWB)
  9. Testar Liberacao (selecionar AWBs)
  0. Fluxo completo (todos os passos)
"""

import os
import sys
import time
import logging

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from config import config, PASTA_CONFIG
from modules.browser import NexlogBrowser
from modules.nexlog_voos import NexlogVoos
from modules.nexlog_cte import NexlogCTeOperacoes
from modules.nexlog_liberar import NexlogLiberar
from modules.outlook import OutlookWeb
from modules.sefaz import SefazConsulta

# Logging detalhado
logging.basicConfig(
    level=logging.DEBUG,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    handlers=[
        logging.FileHandler(PASTA_CONFIG / "teste_etapas.log", encoding="utf-8"),
        logging.StreamHandler(),
    ],
)
logger = logging.getLogger("testar_etapas")

# Navegador global para reutilizar entre testes
browser: NexlogBrowser = None


def garantir_navegador():
    """Inicia navegador se nao estiver aberto."""
    global browser
    if browser is None or browser.driver is None:
        browser = NexlogBrowser()
        browser.iniciar()
    return browser


def garantir_login_nexlog():
    """Garante que esta logado no Nexlog."""
    b = garantir_navegador()
    if not b.logado:
        b.login_nexlog()
    return b


# ============================================================
# TESTES INDIVIDUAIS
# ============================================================

def teste_login_nexlog():
    """Teste 1: Login no Nexlog."""
    print("\n" + "=" * 60)
    print("TESTE: Login Nexlog")
    print("=" * 60)

    b = garantir_navegador()
    try:
        b.login_nexlog()
        print("\n[OK] Login realizado com sucesso!")
        print(f"  URL atual: {b.driver.current_url}")
    except Exception as e:
        print(f"\n[ERRO] {e}")
        logger.exception("Erro no login Nexlog")


def teste_buscar_voos():
    """Teste 2: Buscar voos por data."""
    print("\n" + "=" * 60)
    print("TESTE: Buscar Voos")
    print("=" * 60)

    data_ini = input("Data inicial (DD/MM/YYYY) [ontem]: ").strip()
    data_fim = input("Data final (DD/MM/YYYY) [mesma]: ").strip()

    if not data_ini:
        from datetime import datetime, timedelta
        ontem = (datetime.now() - timedelta(days=1)).strftime("%d/%m/%Y")
        data_ini = ontem
    if not data_fim:
        data_fim = data_ini

    b = garantir_login_nexlog()
    voos_mod = NexlogVoos(b)

    try:
        voos = voos_mod.pesquisar_voos(data_ini, data_fim)
        print(f"\n[OK] {len(voos)} voos encontrados:")
        for i, v in enumerate(voos):
            print(f"  {i+1}. {v.numero_controle} | {v.etapas} | {v.data_chegada} | {v.assinado}")
        return voos
    except Exception as e:
        print(f"\n[ERRO] {e}")
        logger.exception("Erro ao buscar voos")
        return []


def teste_extrair_chave_mdfe():
    """Teste 3: Extrair chave MDF-e de um voo."""
    print("\n" + "=" * 60)
    print("TESTE: Extrair Chave MDF-e")
    print("=" * 60)

    # Primeiro busca voos
    voos = teste_buscar_voos()
    if not voos:
        print("[ERRO] Nenhum voo encontrado para testar")
        return

    # Seleciona voo
    indice = input(f"\nQual voo testar? (1-{len(voos)}) [1]: ").strip()
    indice = int(indice) - 1 if indice else 0
    voo = voos[indice]

    print(f"\nTestando voo: {voo.numero_controle} ({voo.etapas})")

    b = garantir_login_nexlog()
    voos_mod = NexlogVoos(b)

    try:
        # Pesquisa voos novamente (para ter a tabela visivel)
        from datetime import datetime, timedelta
        ontem = (datetime.now() - timedelta(days=1)).strftime("%d/%m/%Y")
        voos_mod.pesquisar_voos(ontem, ontem)
        time.sleep(2)

        chave = voos_mod.extrair_chave_mdfe(voo)
        if chave:
            print(f"\n[OK] Chave MDF-e: {chave}")
        else:
            print("\n[ERRO] Chave nao encontrada!")
            print("  Possivel causa: seletor de 'Visualizar integracao MDFe' nao funciona")
            print("  Verifique o dropdown de acoes na ultima coluna da tabela")
            _debug_pagina(b)
    except Exception as e:
        print(f"\n[ERRO] {e}")
        logger.exception("Erro ao extrair chave MDF-e")
        _debug_pagina(b)


def teste_login_sefaz():
    """Teste 4: Login na SEFAZ."""
    print("\n" + "=" * 60)
    print("TESTE: Login SEFAZ")
    print("=" * 60)

    b = garantir_login_nexlog()
    sefaz = SefazConsulta(b.driver)

    try:
        sefaz.abrir_sefaz()
        print("  Site SEFAZ aberto...")
        print(f"  URL: {b.driver.current_url}")

        sefaz.login()

        if sefaz.logado:
            print("\n[OK] Login SEFAZ realizado com sucesso!")
        else:
            print("\n[AVISO] Login nao confirmado automaticamente")
            _debug_pagina(b)

        # Volta para Nexlog
        sefaz.voltar_para_nexlog()
        return sefaz

    except Exception as e:
        print(f"\n[ERRO] {e}")
        logger.exception("Erro no login SEFAZ")
        sefaz.voltar_para_nexlog()
        return None


def teste_consulta_sefaz():
    """Teste 5: Consulta SEFAZ por chave MDF-e."""
    print("\n" + "=" * 60)
    print("TESTE: Consulta SEFAZ (campo de chave)")
    print("=" * 60)

    chave = input("Chave MDF-e (44 digitos) ou ENTER para teste: ").strip()
    if not chave:
        chave = "27260417467720000192580010000012131000012130"  # Exemplo
        print(f"  Usando chave de teste: {chave}")

    b = garantir_login_nexlog()
    sefaz = SefazConsulta(b.driver)

    try:
        # Login SEFAZ
        sefaz.abrir_sefaz()
        if not sefaz.logado:
            sefaz.login()

        # Navega para consulta
        sefaz.navegar_consulta_analise_mdfe()
        print("  Na pagina de consulta...")
        print(f"  URL: {b.driver.current_url}")

        # DEBUG: mostra o HTML dos inputs na pagina
        print("\n  [DEBUG] Inputs encontrados na pagina:")
        inputs = b.driver.find_elements("tag name", "input")
        for inp in inputs:
            try:
                tipo = inp.get_attribute("type") or "?"
                ph = inp.get_attribute("placeholder") or ""
                ng_model = inp.get_attribute("ng-model") or ""
                classe = inp.get_attribute("class") or ""
                visivel = inp.is_displayed()
                if visivel and tipo not in ("hidden", "checkbox", "radio"):
                    print(f"    <input type='{tipo}' placeholder='{ph}' "
                          f"ng-model='{ng_model}' class='{classe[:50]}'>")
            except Exception:
                pass

        # Tenta consultar
        resultado = sefaz.consultar_chave_mdfe(chave)

        if resultado:
            print(f"\n[OK] Resultado: {resultado.status.value}")
            print(f"  Total termos: {resultado.total_termos}")
            if resultado.termos:
                for t in resultado.termos:
                    print(f"    TA {t.numero} | CTe {t.cte} | {t.situacao.value}")
        else:
            print("\n[ERRO] Consulta retornou None")
            print("  Possivel causa: campo de chave nao encontrado")
            _debug_pagina(b)

        sefaz.voltar_para_nexlog()

    except Exception as e:
        print(f"\n[ERRO] {e}")
        logger.exception("Erro na consulta SEFAZ")
        sefaz.voltar_para_nexlog()


def teste_outlook_buscar():
    """Teste 6: Buscar email no Outlook."""
    print("\n" + "=" * 60)
    print("TESTE: Outlook - Buscar Email")
    print("=" * 60)

    chave = input("Chave MDF-e para buscar (ou ENTER para teste): ").strip()
    if not chave:
        chave = "27260417467720000192580010000012131000012130"
        print(f"  Usando chave de teste: {chave}")

    b = garantir_login_nexlog()
    outlook = OutlookWeb(b.driver)

    try:
        outlook.abrir_outlook()
        print("  Outlook aberto...")

        resposta = outlook.buscar_por_chave(chave)
        print(f"\n[OK] Resposta: {resposta.value}")

        outlook.voltar_para_nexlog()
        return resposta

    except Exception as e:
        print(f"\n[ERRO] {e}")
        logger.exception("Erro no Outlook")
        outlook.voltar_para_nexlog()


def teste_outlook_pdf():
    """Teste 7: Baixar PDF do Outlook."""
    print("\n" + "=" * 60)
    print("TESTE: Outlook - Baixar PDF")
    print("=" * 60)
    print("  NOTA: Primeiro vai buscar o email, depois tenta baixar o anexo")

    chave = input("Chave MDF-e (ou ENTER para teste): ").strip()
    if not chave:
        chave = "27260417467720000192580010000012131000012130"

    b = garantir_login_nexlog()
    outlook = OutlookWeb(b.driver)

    try:
        outlook.abrir_outlook()
        resposta = outlook.buscar_por_chave(chave)
        print(f"  Resposta email: {resposta.value}")

        if resposta in (OutlookWeb, "com_termos"):
            caminho = outlook.baixar_anexo_pdf()
            if caminho:
                print(f"\n[OK] PDF baixado: {caminho}")
            else:
                print("\n[ERRO] Nao conseguiu baixar o PDF")
                print("  Verifique se o email tem anexo PDF")
                _debug_pagina(b)
        else:
            print("  Email nao tem termos ou nao respondeu - nao tem PDF pra baixar")

        outlook.voltar_para_nexlog()

    except Exception as e:
        print(f"\n[ERRO] {e}")
        logger.exception("Erro ao baixar PDF do Outlook")
        outlook.voltar_para_nexlog()


def teste_comentario():
    """Teste 8: Adicionar comentario critico em AWB."""
    print("\n" + "=" * 60)
    print("TESTE: Comentario Critico")
    print("=" * 60)

    awb = input("AWB (127...): ").strip()
    if not awb:
        print("[ERRO] Precisa informar um AWB!")
        return

    texto = input("Texto do comentario [RETIDO PELA SEFAZ TA 1234567]: ").strip()
    if not texto:
        texto = "RETIDO PELA SEFAZ TA 1234567"

    b = garantir_login_nexlog()
    cte_mod = NexlogCTeOperacoes(b)

    try:
        sucesso = cte_mod.adicionar_comentario_critico(awb, texto)
        if sucesso:
            print(f"\n[OK] Comentario adicionado no AWB {awb}!")
        else:
            print(f"\n[ERRO] Falha ao adicionar comentario")
            _debug_pagina(b)
    except Exception as e:
        print(f"\n[ERRO] {e}")
        logger.exception("Erro ao adicionar comentario")


def teste_liberacao():
    """Teste 9: Testar selecao de AWBs para liberacao."""
    print("\n" + "=" * 60)
    print("TESTE: Liberacao de AWBs")
    print("=" * 60)

    awbs_str = input("AWBs separados por virgula (127..., 127...): ").strip()
    if not awbs_str:
        print("[ERRO] Precisa informar pelo menos 1 AWB!")
        return

    awbs = set(a.strip() for a in awbs_str.split(",") if a.strip())
    print(f"  AWBs para testar: {awbs}")

    from datetime import datetime, timedelta
    data_ini = input("Data inicial (DD/MM/YYYY) [01 do mes]: ").strip()
    if not data_ini:
        data_ini = "01/" + datetime.now().strftime("%m/%Y")

    data_fim = input("Data final (DD/MM/YYYY) [hoje]: ").strip()
    if not data_fim:
        data_fim = datetime.now().strftime("%d/%m/%Y")

    b = garantir_login_nexlog()
    liberar_mod = NexlogLiberar(b)

    try:
        resultado = liberar_mod.liberar_awbs(awbs, data_ini, data_fim)
        print(f"\n[OK] Resultado:")
        print(f"  Selecionados: {resultado.get('selecionados', [])}")
        print(f"  Ja liberados: {resultado.get('ja_liberados', [])}")
        print(f"  Erros: {resultado.get('erros', [])}")
        print(f"  Liberacao OK: {resultado.get('liberacao_ok', False)}")
    except Exception as e:
        print(f"\n[ERRO] {e}")
        logger.exception("Erro na liberacao")


# ============================================================
# UTILIDADES DE DEBUG
# ============================================================

def _debug_pagina(b: NexlogBrowser):
    """Mostra informacoes de debug da pagina atual."""
    print("\n  --- DEBUG DA PAGINA ---")
    print(f"  URL: {b.driver.current_url}")
    print(f"  Titulo: {b.driver.title}")

    # Mostra dropdowns abertos
    dropdowns = b.driver.find_elements("css selector", ".dropdown-menu.show, .dropdown-menu[style*='display: block']")
    if dropdowns:
        print(f"  Dropdowns abertos: {len(dropdowns)}")
        for dd in dropdowns:
            try:
                html = dd.get_attribute("innerHTML")[:500]
                print(f"    HTML: {html}")
            except Exception:
                pass

    # Mostra modais abertos
    modais = b.driver.find_elements("css selector", ".modal.show, .modal[style*='display: block']")
    if modais:
        print(f"  Modais abertos: {len(modais)}")
        for m in modais:
            try:
                titulo = m.find_element("css selector", ".modal-title, h4, h5").text
                print(f"    Modal: {titulo}")
            except Exception:
                pass

    # Screenshot
    try:
        screenshot_path = str(PASTA_CONFIG / "debug_screenshot.png")
        b.driver.save_screenshot(screenshot_path)
        print(f"  Screenshot salvo: {screenshot_path}")
    except Exception:
        pass


def encerrar():
    """Fecha o navegador."""
    global browser
    if browser:
        browser.fechar()
        browser = None
    print("\nNavegador fechado. Ate!")


# ============================================================
# MENU PRINCIPAL
# ============================================================

def menu():
    print("\n" + "=" * 60)
    print("  TESTE DE ETAPAS - AUTOMACAO SEFAZ")
    print("=" * 60)
    print()
    print("  1. Login Nexlog")
    print("  2. Buscar Voos")
    print("  3. Extrair Chave MDF-e (Visualizar integracao)")
    print("  4. Login SEFAZ")
    print("  5. Consulta SEFAZ (campo de chave)")
    print("  6. Outlook - Buscar email")
    print("  7. Outlook - Baixar PDF")
    print("  8. Comentario critico (AWB)")
    print("  9. Liberacao de AWBs")
    print("  0. Sair")
    print()

    opcoes = {
        "1": teste_login_nexlog,
        "2": teste_buscar_voos,
        "3": teste_extrair_chave_mdfe,
        "4": teste_login_sefaz,
        "5": teste_consulta_sefaz,
        "6": teste_outlook_buscar,
        "7": teste_outlook_pdf,
        "8": teste_comentario,
        "9": teste_liberacao,
        "0": encerrar,
    }

    while True:
        escolha = input("\nEscolha (1-9, 0=sair): ").strip()
        if escolha == "0":
            encerrar()
            break
        elif escolha in opcoes:
            try:
                opcoes[escolha]()
            except KeyboardInterrupt:
                print("\n[Interrompido pelo usuario]")
            except Exception as e:
                print(f"\n[ERRO NAO TRATADO] {e}")
                logger.exception("Erro nao tratado")
        else:
            print("Opcao invalida!")


if __name__ == "__main__":
    # Carrega config
    if not config.carregar():
        print("AVISO: Credenciais nao encontradas.")
        print(f"Configure em: {PASTA_CONFIG / 'credenciais.json'}")
        print("Ou preencha na interface principal (main.py) primeiro.\n")

    menu()
