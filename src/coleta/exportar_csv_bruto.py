# -*- coding: utf-8 -*-
"""
FarmTech Solutions - Fase 3 | Exportacao do Monitor Serial para CSV
===================================================================
Le os logs copiados do Monitor Serial e grava o CSV BRUTO das leituras,
sem nenhum tratamento - erros, duplicatas e linhas cortadas continuam la.
O tratamento acontece depois, em src/processamento/tratamento_dados.py.

Entradas (em data/raw/):
  - monitor_serial_wokwi.txt     -> copiado do Monitor Serial do Wokwi
  - monitor_serial_simulado.txt  -> complemento gerado em Python

Saida:
  - data/raw/leituras_brutas.csv  (coluna extra "origem": wokwi | simulada)

So entram as linhas que comecam com "DADOS," - cabecalhos, comentarios (#)
e mensagens do boot do ESP32 sao descartados aqui.

Uso:
    python src/coleta/exportar_csv_bruto.py

Autora: Raiane Sirlene da Silva - RM 575984
"""

import csv
import os

RAIZ = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
PASTA_RAW = os.path.join(RAIZ, "data", "raw")
SAIDA = os.path.join(PASTA_RAW, "leituras_brutas.csv")

LOGS = [
    ("monitor_serial_wokwi.txt", "wokwi"),
    ("monitor_serial_simulado.txt", "simulada"),
]

COLUNAS = ["data_hora", "ciclo", "umidade", "ph", "ldr_bruto", "nitrogenio",
           "fosforo", "potassio", "bomba", "motivo"]


def ler_log(caminho, origem):
    """Extrai as linhas de telemetria de um log do Monitor Serial."""
    registros = []
    with open(caminho, encoding="utf-8", errors="ignore") as arquivo:
        for linha in arquivo:
            linha = linha.strip()
            if not linha.startswith("DADOS,"):
                continue
            campos = linha.split(",")[1:]          # remove o marcador "DADOS"
            campos = (campos + [""] * len(COLUNAS))[: len(COLUNAS)]  # linha cortada
            registros.append(campos + [origem])
    return registros


def main():
    todos = []
    for nome, origem in LOGS:
        caminho = os.path.join(PASTA_RAW, nome)
        if not os.path.exists(caminho):
            print(f"[--] {nome} nao encontrado (ignorado)")
            continue
        registros = ler_log(caminho, origem)
        print(f"[ok] {nome}: {len(registros)} linhas de telemetria ({origem})")
        todos.extend(registros)

    with open(SAIDA, "w", newline="", encoding="utf-8") as arquivo:
        escritor = csv.writer(arquivo)
        escritor.writerow(COLUNAS + ["origem"])
        escritor.writerows(todos)

    print(f"CSV bruto gravado: {SAIDA} ({len(todos)} linhas)")


if __name__ == "__main__":
    main()
