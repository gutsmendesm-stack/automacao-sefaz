"""
Teste rapido da funcao de consulta de AWB.
Roda direto no terminal sem precisar do Telegram.

Uso:
    python testar_consulta.py 12739688622
"""

import sys
import os
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from config import config
from modules.browser import NexlogBrowser
from modules.consulta_cliente import ConsultaCliente


def main():
    # Pega AWB do argumento ou pede no terminal
    if len(sys.argv) > 1:
        awb = sys.argv[1].strip()
    else:
        awb = input("Digite o AWB para consultar: ").strip()

    if not awb:
        print("AWB vazio!")
        return

    print(f"\nConsultando AWB {awb}...")
    print("-" * 40)

    # Abre navegador e faz login
    browser = NexlogBrowser()
    browser.iniciar(headless=False)  # headless=False pra voce VER o que acontece
    browser.login_nexlog()
    time.sleep(3)

    # Consulta
    consulta = ConsultaCliente(browser)
    resultado = consulta.consultar_awb(awb)

    # Mostra resultado
    print("\n" + "=" * 40)
    print("RESULTADO:")
    print("=" * 40)
    print(resultado.resposta_cliente())
    print("\n" + "-" * 40)
    print(f"[DEBUG] status: {resultado.status}")
    print(f"[DEBUG] status_operacional: {resultado.status_operacional}")
    print(f"[DEBUG] local_entrega: {resultado.local_entrega}")
    print(f"[DEBUG] servico: {resultado.servico}")
    print(f"[DEBUG] ultima_etapa: {resultado.ultima_etapa}")
    print(f"[DEBUG] ultimo_local: {resultado.ultimo_local}")
    print(f"[DEBUG] ultimo_destino: {resultado.ultimo_destino}")
    print(f"[DEBUG] termos: {resultado.termos}")
    print(f"[DEBUG] tem_comentario_retido: {resultado.tem_comentario_retido}")
    print(f"[DEBUG] observacao: {resultado.observacao}")
    print(f"[DEBUG] tade_resultados: {len(resultado.tade_resultados)} termo(s) consultados")
    for i, tade in enumerate(resultado.tade_resultados):
        print(f"  [{i+1}] TA {tade['numero']}: {tade['situacao']} | {tade['valor']}")
        print(f"      PDF TA: {tade['ta_path'] or 'Nao baixou'}")
        print(f"      PDF DAR: {tade['dar_path'] or 'Nao baixou'}")
    print("-" * 40)

    # Fecha
    browser.fechar()
    print("\nFinalizado!")


if __name__ == "__main__":
    main()
