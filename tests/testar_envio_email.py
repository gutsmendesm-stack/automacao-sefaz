"""
Teste isolado do envio de email via Outlook Web.
Abre o Chrome, loga no Outlook, e tenta enviar um email de teste.

Uso: python tests/testar_envio_email.py
"""

import sys
import os
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from modules.browser import NexlogBrowser
from modules.outlook import OutlookWeb

# === CONFIG DO TESTE ===
EMAIL_DESTINO = ["gmmiqoption@gmail.com", "gmmiqoption@gmail.com"]
ASSUNTO_TESTE = "TESTE AUTOMACAO - Termo de Apreensao AWB 12700000000"
CORPO_TESTE = """Prezados,

O AWB 12700000000 foi retido pela SEFAZ-AL ao chegar em MCZ.

Dados da retencao:
  - Termo TA 2732679 | Situacao: Pendente | Valor: R$ 48,28

Seguem em anexo o(s) Termo(s) de Apreensao e a(s) Guia(s) de pagamento (DAR).

Favor providenciar a regularizacao para liberacao da carga.

Att,
MCZ Operacoes
"""

# Se quiser testar com anexo, coloque o caminho de um PDF aqui:
ANEXO_TESTE = [
    r"C:\Users\Gustavo\Downloads\automacao_sefaz\GOL 93749 (1).pdf",
    r"C:\Users\Gustavo\Downloads\automacao_sefaz\71cbc75a-b413-4dfe-a0f4-04517f1439bf.pdf",
]


def main():
    print("=" * 60)
    print("TESTE DE ENVIO DE EMAIL VIA OUTLOOK WEB")
    print("=" * 60)
    print(f"Destino: {EMAIL_DESTINO}")
    print(f"Assunto: {ASSUNTO_TESTE}")
    print(f"Anexo: {ANEXO_TESTE or '(nenhum)'}")
    print()

    # Abre navegador
    print("[1/4] Abrindo navegador...")
    browser = NexlogBrowser()
    browser.iniciar()
    # Nao precisa logar no Nexlog, so precisa do Chrome com perfil

    # Abre Outlook
    print("[2/4] Abrindo Outlook...")
    outlook = OutlookWeb(browser.driver)
    outlook.abrir_outlook()
    time.sleep(3)

    # Envia email
    print("[3/4] Enviando email de teste...")
    if isinstance(ANEXO_TESTE, list):
        anexos = [a for a in ANEXO_TESTE if os.path.isfile(a)]
    elif ANEXO_TESTE and os.path.isfile(ANEXO_TESTE):
        anexos = [ANEXO_TESTE]
    else:
        anexos = None

    sucesso = outlook.enviar_email(
        destinatarios=[EMAIL_DESTINO],
        assunto=ASSUNTO_TESTE,
        corpo=CORPO_TESTE,
        anexos=anexos,
    )

    if sucesso:
        print("\n[OK] Email enviado com sucesso!")
        print(f"     Verifique a caixa de entrada de {EMAIL_DESTINO}")
    else:
        print("\n[ERRO] Falha ao enviar email.")
        print("     Verifique o log para detalhes.")

    # Pausa pra voce ver a tela antes de fechar
    print("\n[4/4] Pressione ENTER para fechar o navegador...")
    input()
    browser.fechar()


if __name__ == "__main__":
    main()
