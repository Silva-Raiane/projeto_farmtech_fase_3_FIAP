# -*- coding: utf-8 -*-
"""
FarmTech Solutions - Fase 3 | Complemento do historico de leituras
==================================================================
O enunciado permite complementar o volume de leituras do Wokwi com linhas
geradas em Python "a partir do padrao das leituras reais", desde que essas
linhas fiquem marcadas como simuladas e as premissas documentadas.

Este script faz exatamente isso:

  1. Se existir data/raw/monitor_serial_wokwi.txt (log copiado do Monitor
     Serial do Wokwi), ele continua o historico a partir da ULTIMA leitura
     real: mesma data/hora + 1 h, mesmo numero de ciclo + 1, mesmo estado da
     bomba e dos nutrientes, e umidade/pH partindo dos ultimos valores reais.
  2. Simula a lavoura hora a hora ate 30/09/2026 23:00 (3 meses no total)
     e aplica a MESMA arvore de decisao do firmware (sketch.ino).
  3. Grava data/raw/monitor_serial_simulado.txt no MESMO formato da linha
     "DADOS,..." impressa pelo ESP32 - inclusive com as imperfeicoes tipicas
     de um log real (falhas do DHT22, picos, LDR saturado, linhas duplicadas,
     horas perdidas e linhas cortadas na copia), para que o tratamento em
     Pandas/NumPy tenha trabalho de verdade.

Premissas da simulacao (documentadas no README):
  - Inverno seco em Minas Gerais: chuva rara em julho/agosto, mais frequente
    em setembro.
  - Evapotranspiracao maior durante o dia (pico ~13 h).
  - Bomba ligada (gotejamento) eleva a umidade do solo em ~3,5 p.p. por hora.
  - Cada adubacao NPK acidifica levemente o solo (-0,35 de pH); a calagem
    eleva o pH (+1,2).
  - Os nutrientes "acabam" em dias diferentes apos cada adubacao (o K e o
    N se esgotam antes do P).

Uso:
    python src/coleta/gerar_leituras_simuladas.py

Autora: Raiane Sirlene da Silva - RM 575984
"""

import os
from datetime import datetime, timedelta

import numpy as np

# ---------------------------------------------------------------------------
# CAMINHOS
# ---------------------------------------------------------------------------
RAIZ = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
LOG_WOKWI = os.path.join(RAIZ, "data", "raw", "monitor_serial_wokwi.txt")
LOG_SIMULADO = os.path.join(RAIZ, "data", "raw", "monitor_serial_simulado.txt")

# ---------------------------------------------------------------------------
# PARAMETROS (os mesmos do firmware)
# ---------------------------------------------------------------------------
PH_MINIMO, PH_MAXIMO = 5.0, 6.8
UMIDADE_CRITICA, UMIDADE_PREVENTIVA, UMIDADE_MAXIMA = 35.0, 50.0, 60.0
ADC_MAXIMO = 4095

INICIO_PADRAO = datetime(2026, 7, 1, 0, 0)
FIM = datetime(2026, 9, 30, 23, 0)
SEMENTE = 42                       # reprodutibilidade

# Dias (contados a partir de 01/07) com adubacao NPK e com calagem
DIAS_ADUBACAO = [2, 19, 37, 54, 71, 87]
DIAS_CALAGEM = [46, 80]

# Probabilidades de imperfeicoes no log (por linha)
P_FALHA_DHT = 0.004
P_PICO_UMIDADE = 0.003
P_LDR_SATURADO = 0.003
P_DUPLICADA = 0.003
P_HORA_PERDIDA = 0.002
LINHAS_CORTADAS = 3


# ---------------------------------------------------------------------------
# DECISAO - copia fiel de decidirIrrigacao() do sketch.ino
# ---------------------------------------------------------------------------
def decidir_irrigacao(umidade, ph, n, p, k, bomba_ligada):
    if umidade is None:
        return False, "FALHA_SENSOR"
    if umidade >= UMIDADE_MAXIMA:
        return False, "SATURADO"
    if ph < PH_MINIMO or ph > PH_MAXIMO:
        return False, "PH_FORA"
    if umidade < UMIDADE_CRITICA:
        return True, "EMERGENCIA"
    if umidade < UMIDADE_PREVENTIVA:
        if n and p and k:
            return True, "FERTIRRIGACAO"
        return False, "NPK_INCOMPLETO"
    return bomba_ligada, ("HISTERESE_LIGADA" if bomba_ligada else "UMIDADE_OK")


# ---------------------------------------------------------------------------
# LEITURA DO LOG REAL (se existir) PARA CONTINUAR O HISTORICO
# ---------------------------------------------------------------------------
def estado_inicial():
    """Devolve o ponto de partida: do log real do Wokwi, ou o padrao."""
    padrao = {
        "inicio": INICIO_PADRAO, "ciclo": 0, "umidade": 52.0, "ph": 6.4,
        "n": False, "p": False, "k": False, "bomba": False, "origem": "padrao",
    }
    if not os.path.exists(LOG_WOKWI):
        return padrao

    ultima = None
    umidade = padrao["umidade"]           # ultima umidade VALIDA do log real
    with open(LOG_WOKWI, encoding="utf-8", errors="ignore") as arquivo:
        for linha in arquivo:
            campos = linha.strip().split(",")
            if len(campos) == 11 and campos[0] == "DADOS":
                try:
                    datetime.strptime(campos[1], "%Y-%m-%d %H:%M")
                    float(campos[4])
                    ultima = campos
                except ValueError:
                    continue
                if campos[3] != "ERRO" and 0 < float(campos[3]) <= 100:
                    umidade = float(campos[3])
    if ultima is None:
        return padrao

    return {
        "inicio": datetime.strptime(ultima[1], "%Y-%m-%d %H:%M") + timedelta(hours=1),
        "ciclo": int(ultima[2]),
        "umidade": umidade,
        "ph": float(ultima[4]),
        "n": ultima[6] == "1", "p": ultima[7] == "1", "k": ultima[8] == "1",
        "bomba": ultima[9] == "LIGA",
        "origem": "wokwi",
    }


# ---------------------------------------------------------------------------
# SIMULACAO DA LAVOURA HORA A HORA
# ---------------------------------------------------------------------------
def simular():
    rng = np.random.default_rng(SEMENTE)
    est = estado_inicial()

    umidade_real = est["umidade"]
    ph_real = est["ph"]
    n, p, k = est["n"], est["p"], est["k"]
    bomba = est["bomba"]
    ciclo = est["ciclo"]

    # Validade de cada nutriente (em horas) apos uma adubacao
    validade = {"n": 0, "p": 0, "k": 0}
    chuva_restante = 0.0      # mm-equivalentes de umidade ainda a entrar no solo

    linhas = []
    momento = est["inicio"]
    while momento <= FIM:
        ciclo += 1
        dia = (momento - INICIO_PADRAO).days
        hora = momento.hour

        # --- eventos de manejo (as 7 h do dia programado) -----------------
        if hora == 7 and dia in DIAS_ADUBACAO:
            n = p = k = True
            validade = {
                "n": int(rng.integers(6, 11)) * 24,
                "p": int(rng.integers(10, 15)) * 24,
                "k": int(rng.integers(7, 12)) * 24,
            }
            ph_real -= 0.35                         # acidificacao da adubacao
        if hora == 7 and dia in DIAS_CALAGEM:
            ph_real += 1.2                          # calcario corrige a acidez

        for nutriente in ("n", "p", "k"):
            if validade[nutriente] > 0:
                validade[nutriente] -= 1
                if validade[nutriente] == 0:
                    if nutriente == "n":
                        n = False
                    elif nutriente == "p":
                        p = False
                    else:
                        k = False

        # --- chuva: rara no inverno seco, mais comum em setembro ---------
        prob_chuva_dia = 0.12 if momento.month == 9 else 0.035
        if hora == 15 and rng.random() < prob_chuva_dia:
            chuva_restante += rng.uniform(8, 25)
        entrada_chuva = min(chuva_restante, 6.0)
        chuva_restante -= entrada_chuva

        # --- balanco hidrico do solo ------------------------------------
        sol = max(0.0, np.sin(np.pi * (hora - 6) / 12))   # 0 a noite, 1 as 12 h
        evapotranspiracao = 0.30 + 0.55 * sol
        irrigacao = 3.5 if bomba else 0.0
        umidade_real += irrigacao + entrada_chuva - evapotranspiracao
        umidade_real = float(np.clip(umidade_real, 15.0, 95.0))

        # --- pH: deriva lenta de acidificacao + ruido --------------------
        ph_real += rng.normal(0, 0.004) - 0.0003
        ph_real = float(np.clip(ph_real, 3.5, 9.0))

        # --- leitura dos sensores (como o ESP32 enxerga) -----------------
        umidade_lida = round(umidade_real + rng.normal(0, 1.0), 1)
        falha_dht = rng.random() < P_FALHA_DHT
        if rng.random() < P_PICO_UMIDADE:
            umidade_lida = float(rng.choice([0.0, 99.9, round(umidade_lida + 35, 1)]))
        umidade_lida = float(np.clip(umidade_lida, 0.0, 100.0))

        ldr = int(round(ph_real * ADC_MAXIMO / 14 + rng.normal(0, 8)))
        if rng.random() < P_LDR_SATURADO:
            ldr = int(rng.choice([0, ADC_MAXIMO]))
        ldr = int(np.clip(ldr, 0, ADC_MAXIMO))
        ph_lido = ldr * 14.0 / ADC_MAXIMO

        # --- decisao do firmware -----------------------------------------
        bomba, motivo = decidir_irrigacao(
            None if falha_dht else umidade_lida, ph_lido, n, p, k, bomba
        )

        texto_umidade = "ERRO" if falha_dht else f"{umidade_lida:.1f}"
        linha = (f"DADOS,{momento:%Y-%m-%d %H:%M},{ciclo},{texto_umidade},"
                 f"{ph_lido:.2f},{ldr},{int(n)},{int(p)},{int(k)},"
                 f"{'LIGA' if bomba else 'DESL'},{motivo}")

        # --- imperfeicoes de um log copiado do Monitor Serial ------------
        if rng.random() >= P_HORA_PERDIDA:
            linhas.append(linha)
            if rng.random() < P_DUPLICADA:
                linhas.append(linha)

        momento += timedelta(hours=1)

    # Algumas linhas cortadas no meio (copia/cola interrompida)
    for posicao in rng.choice(len(linhas), size=LINHAS_CORTADAS, replace=False):
        linhas[posicao] = linhas[posicao][: int(rng.integers(12, 30))]

    return linhas, est


def main():
    linhas, est = simular()
    os.makedirs(os.path.dirname(LOG_SIMULADO), exist_ok=True)
    with open(LOG_SIMULADO, "w", encoding="utf-8") as arquivo:
        arquivo.write("# FARMTECH SOLUTIONS - FASE 3 - LEITURAS SIMULADAS EM PYTHON\n")
        arquivo.write("# Linhas geradas por src/coleta/gerar_leituras_simuladas.py "
                      "(premissas no README)\n")
        arquivo.write("tipo,data_hora,ciclo,umidade,ph,ldr_bruto,nitrogenio,"
                      "fosforo,potassio,bomba,motivo\n")
        arquivo.write("\n".join(linhas) + "\n")

    print(f"Ponto de partida : {est['origem']} ({est['inicio']:%d/%m/%Y %H:%M})")
    print(f"Linhas geradas   : {len(linhas)}")
    print(f"Arquivo          : {LOG_SIMULADO}")


if __name__ == "__main__":
    main()
