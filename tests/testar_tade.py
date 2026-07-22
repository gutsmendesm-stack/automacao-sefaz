"""
Teste rapido de extracao de Termo (TA) e DAR no site da SEFAZ.
Roda direto no terminal.

Uso:
    python testar_tade.py 2426405
"""

import sys
import os
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from config import config
from modules.browser import NexlogBrowser
from modules.consulta_cliente import ConsultaTADe


def main():
    # Pega numero do termo do argumento ou pede no terminal
    if len(sys.argv) > 1:
        numero = sys.argv[1].strip()
    else:
        numero = input("Digite o numero do TADe: ").strip()

    if not numero:
        print("Numero vazio!")
        return

    print(f"\nConsultando TADe {numero} na SEFAZ...")
    print("-" * 40)

    # Abre navegador e faz login no Nexlog (necessario para sessao do browser)
    browser = NexlogBrowser()
    browser.iniciar(headless=False)  # Visivel para ver o que acontece
    browser.login_nexlog()
    time.sleep(3)

    # Consulta TADe
    tade = ConsultaTADe(browser)
    resultado = tade.extrair_termo_e_dar(numero)

    # Mostra resultado
    print("\n" + "=" * 40)
    print("RESULTADO:")
    print("=" * 40)
    print(f"Situacao: {resultado['situacao']}")
    print(f"Valor: {resultado['valor']}")
    print(f"Data: {resultado['data']}")
    print(f"")
    print(f"PDF Termo (TA): {resultado['ta_path'] or 'Nao baixou'}")
    print(f"PDF DAR: {resultado['dar_path'] or 'Nao baixou'}")
    print(f"")
    if resultado['erro']:
        print(f"ERRO: {resultado['erro']}")
    print("=" * 40)

    # Mostra onde os PDFs foram salvos
    if resultado['ta_path']:
        print(f"\nTermo salvo em: {resultado['ta_path']}")
    if resultado['dar_path']:
        print(f"DAR salvo em: {resultado['dar_path']}")

    # Fecha
    browser.fechar()
    print("\nFinalizado!")


if __name__ == "__main__":
    main()
