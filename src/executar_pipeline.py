# -*- coding: utf-8 -*-
"""
FarmTech Solutions - Fase 3 | Executa toda a Parte 1 em sequencia

  1. Complementa o historico (gerar_leituras_simuladas.py)
  2. Exporta o CSV bruto do Monitor Serial (exportar_csv_bruto.py)
  3. Trata, calcula indicadores e gera os graficos (tratamento_dados.py)

Depois disso, a Parte 2 roda com:  python src/banco/carga_oracle.py --tudo

Uso:
    python src/executar_pipeline.py

Autora: Raiane Sirlene da Silva - RM 575984
"""

import os
import subprocess
import sys

PASTA_SRC = os.path.dirname(os.path.abspath(__file__))
ETAPAS = [
    ("Complemento do historico", os.path.join("coleta", "gerar_leituras_simuladas.py")),
    ("CSV bruto do Monitor Serial", os.path.join("coleta", "exportar_csv_bruto.py")),
    ("Tratamento, indicadores e graficos",
     os.path.join("processamento", "tratamento_dados.py")),
]

for numero, (titulo, script) in enumerate(ETAPAS, start=1):
    print(f"\n{'#' * 70}\n# ETAPA {numero} - {titulo}\n{'#' * 70}")
    resultado = subprocess.run([sys.executable, os.path.join(PASTA_SRC, script)])
    if resultado.returncode != 0:
        sys.exit(f"Falha na etapa {numero} ({script}).")

print("\nParte 1 concluida. Proximo passo: python src/banco/carga_oracle.py --tudo")
