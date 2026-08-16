"""
Teste rapido do modulo ia_local.py com Ollama.
Roda 1 exemplo de cada funcionalidade pra validar que ta funcionando.

Uso: python tests/testar_ia_local.py
"""

import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from modules.ia_local import ia_local
from modules.parser import detectar_resposta_email, parsear_relatorio_sefaz


def separador(titulo):
    print(f"\n{'='*60}")
    print(f"  {titulo}")
    print(f"{'='*60}\n")


def testar_disponibilidade():
    """Verifica se Ollama esta rodando e modelo carregado."""
    separador("1. VERIFICANDO OLLAMA")

    disponivel = ia_local.verificar_disponibilidade()
    print(f"  URL: {ia_local.url}")
    print(f"  Modelo: {ia_local.modelo}")
    print(f"  Disponivel: {'SIM' if disponivel else 'NAO'}")

    if not disponivel:
        print("\n  ERRO: Ollama nao disponivel!")
        print("  Verifique:")
        print("    1. Ollama esta rodando? (ollama serve)")
        print("    2. Modelo baixado? (ollama pull llama3.1:8b)")
        print("    3. Porta 11434 livre?")
        return False

    print("  OK!")
    return True


def testar_classificacao_email():
    """Testa classificacao de email com 3 exemplos."""
    separador("2. CLASSIFICACAO DE EMAIL")

    # Exemplo 1: email SEM termos (voo liberado)
    email_sem_termos = """
    Prezados,

    Informamos que o MDF-e 12345678901234567890123456789012345678901234 
    foi analisado e NAO houve retencao de mercadorias.
    O voo esta liberado para prosseguir com as operacoes normais.

    Atenciosamente,
    Central de Transportadoras - SEFAZ/AL
    """

    # Exemplo 2: email COM termos (tem relatorio anexo)
    email_com_termos = """
    Prezados,

    Segue em anexo relatorio referente a analise do MDF-e.
    Foi gerado o relatorio anexo com os termos de apreensao identificados.
    Favor verificar as pendencias.

    Atenciosamente,
    Central de Transportadoras - SEFAZ/AL
    """

    # Exemplo 3: email ambiguo (pra testar caso indefinido)
    email_ambiguo = """
    Prezados,

    Acusamos o recebimento da sua solicitacao.
    O processo esta em andamento e sera finalizado em breve.

    Atenciosamente,
    Central de Transportadoras - SEFAZ/AL
    """

    casos = [
        ("SEM TERMOS (liberado)", email_sem_termos, "sem_termos"),
        ("COM TERMOS (relatorio anexo)", email_com_termos, "com_termos"),
        ("AMBIGUO (indefinido)", email_ambiguo, "indefinido"),
    ]

    acertos = 0
    for nome, texto, esperado in casos:
        resultado = detectar_resposta_email(texto)
        ok = resultado == esperado
        acertos += 1 if ok else 0
        status = "OK" if ok else "DIVERGIU"
        print(f"  [{status}] {nome}")
        print(f"        Esperado: {esperado}")
        print(f"        Obtido:   {resultado}")
        print()

    print(f"  Resultado: {acertos}/{len(casos)} corretos")
    return acertos == len(casos)


def testar_extracao_termos():
    """Testa extracao de termos de um relatorio SEFAZ simulado."""
    separador("3. EXTRACAO DE TERMOS (RELATORIO SEFAZ)")

    # Simula texto de relatorio com 2 termos
    relatorio_com_termos = """
    SECRETARIA DE ESTADO DA FAZENDA DE ALAGOAS
    RELATORIO DE ANALISE DE MDF-e

    CHAVE MDF-e: 27260507015747000164580010000456781234567890
    N MDF-e: 456789
    DATA DE EMISSAO: 15/07/2026
    EMITENTE: VRG LINHAS AEREAS S.A.
    CNPJ: 07.015.747/0001-64

    STATUS: Analisado com Pendencias

    TOTAL DE TERMOS 2

    Transportadora Fiel Depositario:

    2432400  Pendente  15/07/2026  NF-e 123456  CT-e 7398054
    2432401  Pendente  15/07/2026  NF-e 789012  CT-e 7404445
    """

    # Simula relatorio SEM termos
    relatorio_sem_termos = """
    SECRETARIA DE ESTADO DA FAZENDA DE ALAGOAS
    RELATORIO DE ANALISE DE MDF-e

    CHAVE MDF-e: 27260507015747000164580010000456781234567890
    N MDF-e: 456790

    STATUS: Analisado sem Pendencias

    TOTAL DE TERMOS 0

    Nao foram encontrados termos de apreensao para este MDF-e.
    """

    # Teste 1: COM termos
    print("  Relatorio COM termos:")
    resultado = parsear_relatorio_sefaz(relatorio_com_termos)
    print(f"    Chave: {resultado.chave[:20]}...")
    print(f"    N MDF-e: {resultado.numero_mdfe}")
    print(f"    Total termos: {resultado.total_termos}")
    print(f"    Termos encontrados: {len(resultado.termos)}")
    for t in resultado.termos:
        print(f"      TA {t.numero} | CTe {t.cte} | NF-e {t.nfe} | {t.situacao.value}")
    print(f"    Status: {resultado.status.value}")
    print()

    ok1 = resultado.total_termos >= 2 and len(resultado.termos) >= 2
    print(f"    {'OK' if ok1 else 'PROBLEMA'}: esperava >=2 termos, obteve {len(resultado.termos)}")
    print()

    # Teste 2: SEM termos
    print("  Relatorio SEM termos:")
    resultado2 = parsear_relatorio_sefaz(relatorio_sem_termos)
    print(f"    Total termos: {resultado2.total_termos}")
    print(f"    Termos encontrados: {len(resultado2.termos)}")
    print(f"    Status: {resultado2.status.value}")
    print()

    ok2 = resultado2.total_termos == 0 and len(resultado2.termos) == 0
    print(f"    {'OK' if ok2 else 'PROBLEMA'}: esperava 0 termos, obteve {len(resultado2.termos)}")

    return ok1 and ok2


def main():
    print("\n" + "="*60)
    print("  TESTE DO MODULO IA LOCAL (Ollama + llama3.1:8b)")
    print("="*60)

    # 1. Verifica Ollama
    if not testar_disponibilidade():
        print("\n  Abortando testes (Ollama indisponivel).")
        print("  Os fallbacks regex continuam funcionando normalmente.")
        sys.exit(1)

    # 2. Classificacao de email
    ok_email = testar_classificacao_email()

    # 3. Extracao de termos
    ok_termos = testar_extracao_termos()

    # Resumo
    separador("RESUMO")
    print(f"  Ollama: OK")
    print(f"  Classificacao email: {'PASSOU' if ok_email else 'PROBLEMAS'}")
    print(f"  Extracao termos: {'PASSOU' if ok_termos else 'PROBLEMAS'}")
    print()

    if ok_email and ok_termos:
        print("  TUDO OK! IA local integrada e funcionando.")
    else:
        print("  Alguns testes divergiram. Verifique os resultados acima.")
        print("  (pode ser normal — IA pode interpretar diferente do esperado)")


if __name__ == "__main__":
    main()
