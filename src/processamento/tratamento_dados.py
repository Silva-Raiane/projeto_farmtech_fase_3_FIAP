# -*- coding: utf-8 -*-
"""
FarmTech Solutions - Fase 3 | Tratamento, indicadores e graficos
================================================================
Transforma o CSV bruto do Monitor Serial em um historico confiavel.

Etapas:
  1. Carga do CSV bruto em um DataFrame (tudo como texto, como veio do log)
  2. Limpeza com Pandas e NumPy:
       - descarta linhas cortadas na copia do Monitor Serial
       - converte tipos (data/hora, numeros, booleanos)
       - remove leituras duplicadas
       - invalida leituras fora da faixa fisica dos sensores
       - detecta picos isolados (filtro de Hampel: mediana movel + MAD)
       - reconstroi a grade horaria e interpola lacunas curtas (ate 3 h)
  3. Reducao de ruido: media movel centrada de 6 h em umidade e pH
  4. Indicadores de manejo: horas de bomba ligada, acionamentos, agua
     aplicada, medias e extremos de umidade e pH por dia e por mes
  5. Graficos (Matplotlib + Seaborn) e matriz de correlacao

Entradas : data/raw/leituras_brutas.csv
           data/fase2/parametros_cultura_cafe.json (limites e vazao)
Saidas   : data/processed/*.csv, data/processed/relatorio_tratamento.txt
           docs/graficos/*.png

Uso:
    python src/processamento/tratamento_dados.py

Autora: Raiane Sirlene da Silva - RM 575984
"""

import json
import os

import matplotlib
matplotlib.use("Agg")                     # gera os PNGs sem abrir janela
import matplotlib.dates as mdates
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
import seaborn as sns

# ---------------------------------------------------------------------------
# CAMINHOS E PARAMETROS
# ---------------------------------------------------------------------------
RAIZ = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
CSV_BRUTO = os.path.join(RAIZ, "data", "raw", "leituras_brutas.csv")
JSON_PARAMETROS = os.path.join(RAIZ, "data", "fase2", "parametros_cultura_cafe.json")
PASTA_SAIDA = os.path.join(RAIZ, "data", "processed")
PASTA_GRAFICOS = os.path.join(RAIZ, "docs", "graficos")

with open(JSON_PARAMETROS, encoding="utf-8") as _f:
    PARAM = json.load(_f)

PH_MIN, PH_MAX = PARAM["ph_minimo"], PARAM["ph_maximo"]
UMID_CRITICA = PARAM["umidade_critica_pct"]
UMID_PREVENTIVA = PARAM["umidade_preventiva_pct"]
UMID_MAXIMA = PARAM["umidade_maxima_pct"]
VAZAO_L_H = PARAM["vazao_bomba_l_h"]      # vazao definida pelo grupo
HORAS_POR_CICLO = 1                        # relogio simulado: 1 ciclo = 1 h

JANELA_MEDIA_MOVEL = 6                     # horas
JANELA_HAMPEL = 7                          # horas (centrada)
LIMITE_INTERPOLACAO = 3                    # horas seguidas, no maximo

# Paleta (azul = umidade, laranja = pH, verde-agua = bomba)
COR_UMIDADE = "#2a78d6"
COR_PH = "#eb6834"
COR_BOMBA = "#1baf7a"
COR_REFERENCIA = "#8a8984"
COR_TEXTO = "#52514e"


# ===========================================================================
# 1. CARGA
# ===========================================================================
def carregar_bruto():
    """Le o CSV bruto mantendo tudo como texto: nada e assumido ainda."""
    bruto = pd.read_csv(CSV_BRUTO, dtype=str, keep_default_na=False)
    return bruto


# ===========================================================================
# 2. LIMPEZA
# ===========================================================================
def filtro_hampel(serie, janela, n_sigmas=3.0, piso=0.0):
    """
    Marca como pico o ponto que se afasta da mediana movel mais que
    n_sigmas desvios robustos (MAD x 1,4826). O 'piso' evita que trechos
    muito estaveis transformem variacoes pequenas em falsos picos.
    """
    mediana = serie.rolling(janela, center=True, min_periods=3).median()
    desvio_abs = (serie - mediana).abs()
    mad = desvio_abs.rolling(janela, center=True, min_periods=3).median()
    limite = np.maximum(n_sigmas * 1.4826 * mad, piso)
    return desvio_abs > limite


def limpar(bruto, relatorio):
    df = bruto.copy()
    relatorio["linhas_brutas"] = len(df)

    # --- 2.1 Linhas cortadas: faltam campos no final -----------------------
    incompletas = (df["motivo"] == "") | (df["bomba"] == "")
    relatorio["linhas_cortadas"] = int(incompletas.sum())
    df = df[~incompletas].copy()

    # --- 2.2 Conversao de tipos --------------------------------------------
    df["data_hora"] = pd.to_datetime(df["data_hora"], format="%Y-%m-%d %H:%M",
                                     errors="coerce")
    data_invalida = df["data_hora"].isna()
    relatorio["datas_invalidas"] = int(data_invalida.sum())
    df = df[~data_invalida].copy()

    # "ERRO" (falha do DHT22) vira NaN automaticamente com errors="coerce"
    relatorio["falhas_dht22"] = int((df["umidade"] == "ERRO").sum())
    for coluna in ["umidade", "ph"]:
        df[coluna] = pd.to_numeric(df[coluna], errors="coerce")
    for coluna in ["ciclo", "ldr_bruto"]:
        df[coluna] = pd.to_numeric(df[coluna], errors="coerce").astype("Int64")
    for coluna in ["nitrogenio", "fosforo", "potassio"]:
        df[coluna] = pd.to_numeric(df[coluna], errors="coerce").astype("Int8")
    df["bomba"] = np.where(df["bomba"] == "LIGA", 1, 0).astype("int8")

    # --- 2.3 Duplicatas (mesma hora copiada duas vezes) --------------------
    duplicadas = df.duplicated(subset="data_hora", keep="first")
    relatorio["linhas_duplicadas"] = int(duplicadas.sum())
    df = df[~duplicadas].sort_values("data_hora").set_index("data_hora")

    # --- 2.4 Faixa fisica dos sensores -------------------------------------
    # Umidade: 0 a 100 %. Valores exatamente 0,0 sao falha de contato.
    fora_umidade = (df["umidade"] <= 0) | (df["umidade"] > 100)
    # pH: o LDR saturado (0 ou 4095 no ADC) gera pH 0,00 ou 14,00 - nao e
    # leitura de solo, e sensor "cego". Mantemos so 0 < pH < 14.
    fora_ph = (df["ph"] <= 0) | (df["ph"] >= 14)
    relatorio["umidade_fora_faixa"] = int(fora_umidade.sum())
    relatorio["ph_fora_faixa_fisica"] = int(fora_ph.sum())
    df.loc[fora_umidade, "umidade"] = np.nan
    df.loc[fora_ph, "ph"] = np.nan

    # --- 2.5 Picos isolados (Hampel) ---------------------------------------
    pico_umidade = filtro_hampel(df["umidade"], JANELA_HAMPEL, piso=8.0)
    pico_ph = filtro_hampel(df["ph"], JANELA_HAMPEL, piso=0.5)
    relatorio["picos_umidade"] = int(pico_umidade.sum())
    relatorio["picos_ph"] = int(pico_ph.sum())
    df.loc[pico_umidade, "umidade"] = np.nan
    df.loc[pico_ph, "ph"] = np.nan

    # --- 2.6 Grade horaria completa ----------------------------------------
    grade = pd.date_range(df.index.min(), df.index.max(), freq="h")
    relatorio["horas_ausentes"] = int(len(grade) - len(df))
    df = df.reindex(grade)
    df.index.name = "data_hora"
    df["hora_reconstruida"] = df["origem"].isna()
    df["origem"] = df["origem"].fillna("reconstruida")

    # Estados discretos (bomba, nutrientes) persistem de uma hora para a
    # outra: preenchidos com o ultimo estado conhecido.
    for coluna in ["bomba", "nitrogenio", "fosforo", "potassio"]:
        df[coluna] = df[coluna].ffill().astype("int8")
    df["motivo"] = df["motivo"].ffill()

    # --- 2.7 Interpolacao de lacunas curtas --------------------------------
    df["umidade_imputada"] = df["umidade"].isna()
    df["ph_imputado"] = df["ph"].isna()
    df["umidade"] = df["umidade"].interpolate(method="time", limit=LIMITE_INTERPOLACAO)
    df["ph"] = df["ph"].interpolate(method="time", limit=LIMITE_INTERPOLACAO)
    df["umidade"] = df["umidade"].round(1)
    df["ph"] = df["ph"].round(2)
    relatorio["umidade_interpolada"] = int(df["umidade_imputada"].sum())
    relatorio["ph_interpolado"] = int(df["ph_imputado"].sum())

    sem_valor = df["umidade"].isna() | df["ph"].isna()
    relatorio["linhas_descartadas_sem_valor"] = int(sem_valor.sum())
    df = df[~sem_valor].copy()

    return df


# ===========================================================================
# 3. REDUCAO DE RUIDO E COLUNAS DERIVADAS
# ===========================================================================
def enriquecer(df, relatorio):
    # Media movel centrada de 6 h: suaviza o ruido do sensor sem atrasar a
    # curva (a janela olha 3 h para tras e 3 h para frente).
    df["umidade_mm6h"] = df["umidade"].rolling(JANELA_MEDIA_MOVEL, center=True,
                                               min_periods=1).mean().round(2)
    df["ph_mm6h"] = df["ph"].rolling(JANELA_MEDIA_MOVEL, center=True,
                                     min_periods=1).mean().round(3)

    # Quanto de ruido saiu: desvio padrao da variacao hora a hora (NumPy)
    for coluna in ["umidade", "ph"]:
        antes = np.std(np.diff(df[coluna].to_numpy()))
        depois = np.std(np.diff(df[f"{coluna}_mm6h"].to_numpy()))
        relatorio[f"ruido_{coluna}_antes"] = round(float(antes), 3)
        relatorio[f"ruido_{coluna}_depois"] = round(float(depois), 3)

    # Faixas agronomicas de umidade (mesmos limites do firmware)
    df["faixa_umidade"] = pd.cut(
        df["umidade_mm6h"],
        bins=[-np.inf, UMID_CRITICA, UMID_PREVENTIVA, UMID_MAXIMA, np.inf],
        labels=["critica (<35%)", "seca (35-50%)", "adequada (50-60%)",
                "saturada (>=60%)"],
        right=False,
    )
    df["ph_na_faixa"] = df["ph_mm6h"].between(PH_MIN, PH_MAX).astype("int8")
    df["npk_completo"] = (df["nitrogenio"] & df["fosforo"] & df["potassio"]).astype("int8")
    df["agua_aplicada_l"] = df["bomba"].astype(int) * VAZAO_L_H * HORAS_POR_CICLO
    return df


# ===========================================================================
# 4. INDICADORES DE MANEJO
# ===========================================================================
def detectar_acionamentos(df):
    """
    Um acionamento e um bloco contiguo de horas com a bomba ligada.
    np.diff sobre o estado (0/1) acha as bordas: +1 = liga, -1 = desliga.
    """
    estado = df["bomba"].to_numpy().astype(int)
    bordas = np.diff(np.concatenate(([0], estado, [0])))
    inicios = np.where(bordas == 1)[0]
    fins = np.where(bordas == -1)[0]          # posicao logo apos a ultima hora ligada

    eventos = []
    for numero, (i, f) in enumerate(zip(inicios, fins), start=1):
        bloco = df.iloc[i:f]
        horas = f - i
        eventos.append({
            "id_acionamento": numero,
            "data_hora_inicio": bloco.index[0],
            "data_hora_fim": bloco.index[-1] + pd.Timedelta(hours=1),
            "duracao_h": horas,
            "agua_aplicada_l": horas * VAZAO_L_H,
            "umidade_inicio": round(float(bloco["umidade_mm6h"].iloc[0]), 1),
            "umidade_fim": round(float(df["umidade_mm6h"].iloc[min(f, len(df) - 1)]), 1),
            "ph_medio": round(float(bloco["ph_mm6h"].mean()), 2),
            "motivo": bloco["motivo"].iloc[0],
        })
    return pd.DataFrame(eventos)


def indicadores_por_periodo(df, acionamentos, regra, rotulo):
    """Agrega o historico por dia ('D') ou por mes ('MS')."""
    grupo = df.resample(regra)
    tabela = pd.DataFrame({
        "horas_bomba_ligada": grupo["bomba"].sum(),
        "agua_aplicada_l": grupo["agua_aplicada_l"].sum(),
        "umidade_media": grupo["umidade_mm6h"].mean().round(1),
        "umidade_min": grupo["umidade_mm6h"].min().round(1),
        "umidade_max": grupo["umidade_mm6h"].max().round(1),
        "ph_medio": grupo["ph_mm6h"].mean().round(2),
        "ph_min": grupo["ph_mm6h"].min().round(2),
        "ph_max": grupo["ph_mm6h"].max().round(2),
        "horas_umidade_critica": grupo["umidade_mm6h"].apply(
            lambda s: int((s < UMID_CRITICA).sum())),
        "horas_ph_fora_faixa": grupo["ph_na_faixa"].apply(lambda s: int((s == 0).sum())),
        "pct_horas_npk_completo": (grupo["npk_completo"].mean() * 100).round(1),
    })
    if len(acionamentos):
        contagem = (acionamentos.set_index("data_hora_inicio")
                    .resample(regra)["id_acionamento"].count())
        tabela["num_acionamentos"] = contagem.reindex(tabela.index, fill_value=0)
    else:
        tabela["num_acionamentos"] = 0
    tabela["num_acionamentos"] = tabela["num_acionamentos"].astype(int)
    tabela.index.name = rotulo
    return tabela


# ===========================================================================
# 5. GRAFICOS
# ===========================================================================
def estilo():
    sns.set_theme(style="whitegrid", context="notebook")
    plt.rcParams.update({
        "axes.edgecolor": "#d6d5d0", "grid.color": "#ebeae6",
        "axes.labelcolor": COR_TEXTO, "xtick.color": COR_TEXTO,
        "ytick.color": COR_TEXTO, "axes.titleweight": "bold",
        "axes.titlesize": 12, "figure.dpi": 110, "savefig.dpi": 150,
        "savefig.bbox": "tight", "lines.linewidth": 2,
    })


def linha_referencia(eixo, valor, texto):
    eixo.axhline(valor, color=COR_REFERENCIA, linestyle="--", linewidth=1)
    eixo.annotate(texto, xy=(1.0, valor), xycoords=("axes fraction", "data"),
                  xytext=(4, 0), textcoords="offset points", va="center",
                  fontsize=8, color=COR_TEXTO)


def formatar_datas(eixo):
    eixo.xaxis.set_major_locator(mdates.MonthLocator())
    eixo.xaxis.set_minor_locator(mdates.WeekdayLocator(byweekday=0))
    eixo.xaxis.set_major_formatter(mdates.DateFormatter("%b/%Y"))


def grafico_evolucao(df):
    """G1 - Umidade e pH no tempo: leitura tratada x media movel."""
    fig, (ax1, ax2) = plt.subplots(2, 1, figsize=(12, 7), sharex=True)

    ax1.plot(df.index, df["umidade"], color=COR_UMIDADE, alpha=0.25, linewidth=0.8,
             label="Leitura horaria (tratada)")
    ax1.plot(df.index, df["umidade_mm6h"], color=COR_UMIDADE,
             label="Media movel 6 h")
    ax1.axhspan(UMID_CRITICA, UMID_MAXIMA, color=COR_UMIDADE, alpha=0.06)
    linha_referencia(ax1, UMID_CRITICA, "35% critica")
    linha_referencia(ax1, UMID_MAXIMA, "60% saturado")
    ax1.set_ylabel("Umidade do solo (%)")
    ax1.set_title("Umidade do solo - jul a set/2026")
    ax1.legend(loc="upper left", frameon=False, fontsize=9)

    ax2.plot(df.index, df["ph"], color=COR_PH, alpha=0.25, linewidth=0.8,
             label="Leitura horaria (tratada)")
    ax2.plot(df.index, df["ph_mm6h"], color=COR_PH, label="Media movel 6 h")
    ax2.axhspan(PH_MIN, PH_MAX, color=COR_PH, alpha=0.06)
    linha_referencia(ax2, PH_MIN, "pH 5,0")
    linha_referencia(ax2, PH_MAX, "pH 6,8")
    ax2.set_ylabel("pH do solo")
    ax2.set_title("pH do solo - faixa ideal do cafe sombreada")
    ax2.set_ylim(4.3, 7.0)
    ax2.legend(loc="lower right", frameon=False, fontsize=9)
    formatar_datas(ax2)

    fig.suptitle("G1 - Evolucao da umidade e do pH (leitura x media movel)",
                 x=0.01, ha="left", fontsize=13, fontweight="bold")
    fig.tight_layout()
    fig.savefig(os.path.join(PASTA_GRAFICOS, "g1_evolucao_umidade_ph.png"))
    plt.close(fig)


def grafico_bomba_x_leituras(df):
    """G2 - Relacao entre leituras e acionamentos da bomba."""
    fig = plt.figure(figsize=(12, 7.5))
    grade = fig.add_gridspec(2, 2, height_ratios=[1.1, 1], hspace=0.38, wspace=0.22)

    # (a) Recorte de 10 dias: umidade com as horas de bomba ligada sombreadas
    ax_a = fig.add_subplot(grade[0, :])
    inicio = df.index.min() + pd.Timedelta(days=5)
    recorte = df.loc[inicio: inicio + pd.Timedelta(days=10)]
    ax_a.fill_between(recorte.index, 0, 100, where=recorte["bomba"] == 1,
                      color=COR_BOMBA, alpha=0.22, step="post", linewidth=0,
                      label="Bomba ligada")
    ax_a.plot(recorte.index, recorte["umidade_mm6h"], color=COR_UMIDADE,
              label="Umidade (media movel 6 h)")
    linha_referencia(ax_a, UMID_CRITICA, "35%")
    linha_referencia(ax_a, UMID_PREVENTIVA, "50%")
    linha_referencia(ax_a, UMID_MAXIMA, "60%")
    ax_a.set_ylim(20, 80)
    ax_a.set_ylabel("Umidade do solo (%)")
    ax_a.set_title(f"(a) Recorte de 10 dias a partir de {inicio:%d/%m}: "
                   "cada faixa verde e um acionamento; o solo sobe ate ~60%")
    ax_a.xaxis.set_major_formatter(mdates.DateFormatter("%d/%m"))
    ax_a.legend(loc="upper right", frameon=False, fontsize=9, ncol=2)

    # (b) Horas por motivo da decisao: o que liga e o que bloqueia a bomba
    ax_b = fig.add_subplot(grade[1, 0])
    rotulos_motivo = {
        "EMERGENCIA": "Emergencia (<35%)", "FERTIRRIGACAO": "Fertirrigacao",
        "HISTERESE_LIGADA": "Histerese (ate 60%)", "NPK_INCOMPLETO": "NPK incompleto",
        "UMIDADE_OK": "Umidade ok", "SATURADO": "Solo saturado",
        "PH_FORA": "pH fora da faixa", "FALHA_SENSOR": "Falha do sensor",
    }
    horas = (df.groupby(["motivo", "bomba"]).size().reset_index(name="horas")
             .sort_values(["bomba", "horas"], ascending=[False, True]))
    horas["rotulo"] = horas["motivo"].map(rotulos_motivo).fillna(horas["motivo"])
    cores = np.where(horas["bomba"] == 1, COR_BOMBA, COR_REFERENCIA)
    barras = ax_b.barh(horas["rotulo"], horas["horas"], color=cores, height=0.6,
                       edgecolor="white", linewidth=2)
    ax_b.bar_label(barras, padding=3, fontsize=8, color=COR_TEXTO)
    ax_b.set_xlabel("Horas no periodo")
    ax_b.set_title("(b) Horas por motivo da decisao (verde = ligada)")
    ax_b.set_xlim(0, horas["horas"].max() * 1.15)
    ax_b.grid(axis="y", visible=False)

    # (c) Mapa umidade x pH: onde a bomba liga
    ax_c = fig.add_subplot(grade[1, 1])
    desligada = df[df["bomba"] == 0]
    ligada = df[df["bomba"] == 1]
    ax_c.scatter(desligada["ph"], desligada["umidade"], s=9,
                 color=COR_REFERENCIA, alpha=0.35, label="Desligada", linewidths=0)
    ax_c.scatter(ligada["ph"], ligada["umidade"], s=14,
                 color=COR_BOMBA, alpha=0.85, label="Ligada",
                 edgecolors="white", linewidths=0.6)
    ax_c.axvspan(PH_MIN, PH_MAX, color=COR_PH, alpha=0.06)
    ax_c.axhline(UMID_PREVENTIVA, color=COR_REFERENCIA, linestyle="--", linewidth=1)
    ax_c.set_xlabel("pH lido na hora")
    ax_c.set_ylabel("Umidade lida na hora (%)")
    ax_c.set_title("(c) Bomba ligada so dentro da faixa de pH 5,0-6,8")
    ax_c.legend(frameon=False, fontsize=9, loc="upper right")

    fig.suptitle("G2 - Leituras dos sensores x acionamento da bomba",
                 x=0.01, ha="left", fontsize=13, fontweight="bold")
    fig.savefig(os.path.join(PASTA_GRAFICOS, "g2_leituras_x_bomba.png"))
    plt.close(fig)


def grafico_distribuicoes(df):
    """G3 - Distribuicao das leituras de cada sensor."""
    fig, (ax1, ax2, ax3) = plt.subplots(1, 3, figsize=(13, 4.2),
                                        gridspec_kw={"width_ratios": [1.2, 1.2, 0.9]})

    sns.histplot(df["umidade"], bins=40, ax=ax1, color=COR_UMIDADE,
                 edgecolor="white", linewidth=0.6, kde=True)
    for limite in (UMID_CRITICA, UMID_PREVENTIVA, UMID_MAXIMA):
        ax1.axvline(limite, color=COR_REFERENCIA, linestyle="--", linewidth=1)
    ax1.set_title("S1 - Umidade do solo (%)")
    ax1.set_xlabel("Umidade (%)")
    ax1.set_ylabel("Horas")

    sns.histplot(df["ph"], bins=40, ax=ax2, color=COR_PH, edgecolor="white",
                 linewidth=0.6, kde=True)
    ax2.axvspan(PH_MIN, PH_MAX, color=COR_PH, alpha=0.08)
    ax2.set_title("S2 - pH do solo")
    ax2.set_xlabel("pH")
    ax2.set_ylabel("Horas")

    presenca = (df[["nitrogenio", "fosforo", "potassio"]].mean() * 100)
    presenca.index = ["N", "P", "K"]
    barras = ax3.bar(presenca.index, presenca.values, color=COR_UMIDADE,
                     width=0.55, edgecolor="white", linewidth=2)
    ax3.bar_label(barras, fmt="%.0f%%", padding=3, fontsize=9, color=COR_TEXTO)
    ax3.set_ylim(0, 100)
    ax3.set_title("S3 - % das horas com nutriente presente")
    ax3.set_ylabel("% das horas")

    fig.suptitle("G3 - Distribuicao das leituras de cada sensor (dados tratados)",
                 x=0.01, ha="left", fontsize=13, fontweight="bold")
    fig.tight_layout()
    fig.savefig(os.path.join(PASTA_GRAFICOS, "g3_distribuicao_sensores.png"))
    plt.close(fig)


def matriz_correlacao(df):
    """
    Pearson entre as leituras e a bomba. Alem das variaveis "cruas",
    entram tres variaveis derivadas:
      - Umidade < 35% e pH na faixa: os LIMIARES que o firmware usa. A regra
        de irrigacao e por degraus, entao a umidade continua quase nao se
        correlaciona linearmente com a bomba, mas o indicador de limiar sim.
      - Variacao da umidade na hora seguinte: mostra o EFEITO da bomba no
        solo (bomba ligada -> umidade sobe).
    """
    base = df.copy()
    base["umid_critica"] = (base["umidade"] < UMID_CRITICA).astype(int)
    base["delta_umidade_1h"] = base["umidade"].shift(-1) - base["umidade"]
    colunas = {
        "umidade": "Umidade", "ph": "pH", "nitrogenio": "N", "fosforo": "P",
        "potassio": "K", "npk_completo": "NPK completo",
        "umid_critica": "Umidade < 35%", "ph_na_faixa": "pH na faixa",
        "delta_umidade_1h": "Var. umidade (h+1)", "bomba": "Bomba ligada",
    }
    dados = base[list(colunas)].astype(float).rename(columns=colunas)
    return dados.corr(method="pearson").round(3)


def grafico_correlacao(corr):
    """G4 - Mapa de calor da matriz de correlacao."""
    fig, ax = plt.subplots(figsize=(8.8, 7.2))
    mascara = np.triu(np.ones_like(corr, dtype=bool), k=1)
    paleta = sns.diverging_palette(250, 25, s=75, l=55, as_cmap=True)
    sns.heatmap(corr, mask=mascara, cmap=paleta, vmin=-1, vmax=1, center=0,
                annot=True, fmt=".2f", linewidths=2, linecolor="white",
                square=True, cbar_kws={"shrink": 0.75, "label": "Correlacao de Pearson"},
                ax=ax, annot_kws={"fontsize": 9})
    ax.set_title("G4 - Correlacao entre sensores e acionamento da bomba",
                 loc="left", fontsize=12)
    plt.setp(ax.get_xticklabels(), rotation=35, ha="right")
    fig.tight_layout()
    fig.savefig(os.path.join(PASTA_GRAFICOS, "g4_correlacao_heatmap.png"))
    plt.close(fig)


def grafico_indicadores_diarios(diario):
    """G5 - Horas de bomba ligada e acionamentos por dia."""
    fig, (ax1, ax2) = plt.subplots(2, 1, figsize=(12, 6), sharex=True,
                                   gridspec_kw={"height_ratios": [1.4, 1]})
    ax1.bar(diario.index, diario["horas_bomba_ligada"], width=0.8,
            color=COR_BOMBA, edgecolor="white", linewidth=0.5)
    media = diario["horas_bomba_ligada"].mean()
    linha_referencia(ax1, media, f"media {media:.1f} h")
    ax1.set_ylabel("Horas de bomba ligada")
    ax1.set_title(f"Tempo de bomba ligada por dia (vazao {VAZAO_L_H:,} L/h)"
                  .replace(",", "."))

    ax2.bar(diario.index, diario["horas_ph_fora_faixa"], width=0.8,
            color=COR_PH, edgecolor="white", linewidth=0.5)
    ax2.set_ylabel("Horas com pH fora\nda faixa (bloqueio)")
    ax2.set_title("Horas por dia em que o pH bloqueou a irrigacao")
    formatar_datas(ax2)

    fig.suptitle("G5 - Indicadores diarios de manejo",
                 x=0.01, ha="left", fontsize=13, fontweight="bold")
    fig.tight_layout()
    fig.savefig(os.path.join(PASTA_GRAFICOS, "g5_indicadores_diarios.png"))
    plt.close(fig)


# ===========================================================================
# 6. EXECUCAO
# ===========================================================================
def escrever_relatorio(relatorio, df, acionamentos, mensal, corr):
    caminho = os.path.join(PASTA_SAIDA, "relatorio_tratamento.txt")
    total_agua = df["agua_aplicada_l"].sum()
    ranking = corr["Bomba ligada"].drop("Bomba ligada").abs().sort_values(ascending=False)

    linhas = [
        "FARMTECH SOLUTIONS - FASE 3 - RELATORIO DO TRATAMENTO",
        "=" * 60,
        f"Periodo analisado        : {df.index.min():%d/%m/%Y %H:%M} a "
        f"{df.index.max():%d/%m/%Y %H:%M}",
        f"Origem das linhas        : {df['origem'].value_counts().to_dict()}",
        "",
        "LIMPEZA",
        f"  Linhas no CSV bruto            : {relatorio['linhas_brutas']}",
        f"  Linhas cortadas (descartadas)  : {relatorio['linhas_cortadas']}",
        f"  Datas invalidas (descartadas)  : {relatorio['datas_invalidas']}",
        f"  Linhas duplicadas (removidas)  : {relatorio['linhas_duplicadas']}",
        f"  Falhas do DHT22 ('ERRO')       : {relatorio['falhas_dht22']}",
        f"  Umidade fora da faixa fisica   : {relatorio['umidade_fora_faixa']}",
        f"  pH com LDR saturado (0 ou 14)  : {relatorio['ph_fora_faixa_fisica']}",
        f"  Picos de umidade (Hampel)      : {relatorio['picos_umidade']}",
        f"  Picos de pH (Hampel)           : {relatorio['picos_ph']}",
        f"  Horas ausentes reconstruidas   : {relatorio['horas_ausentes']}",
        f"  Umidade interpolada (horas)    : {relatorio['umidade_interpolada']}",
        f"  pH interpolado (horas)         : {relatorio['ph_interpolado']}",
        f"  Linhas finais tratadas         : {len(df)}",
        "",
        "REDUCAO DE RUIDO (desvio padrao da variacao hora a hora)",
        f"  Umidade: {relatorio['ruido_umidade_antes']} -> "
        f"{relatorio['ruido_umidade_depois']} p.p.",
        f"  pH     : {relatorio['ruido_ph_antes']} -> {relatorio['ruido_ph_depois']}",
        "",
        "INDICADORES GERAIS",
        f"  Acionamentos da bomba          : {len(acionamentos)}",
        f"  Horas de bomba ligada          : {int(df['bomba'].sum())}",
        f"  Duracao media do acionamento   : "
        f"{acionamentos['duracao_h'].mean():.1f} h" if len(acionamentos) else "",
        f"  Agua aplicada (estimada)       : {total_agua:,.0f} L".replace(",", "."),
        f"  Umidade media / min / max      : {df['umidade_mm6h'].mean():.1f} / "
        f"{df['umidade_mm6h'].min():.1f} / {df['umidade_mm6h'].max():.1f} %",
        f"  pH medio / min / max           : {df['ph_mm6h'].mean():.2f} / "
        f"{df['ph_mm6h'].min():.2f} / {df['ph_mm6h'].max():.2f}",
        "",
        "HORAS POR FAIXA DE UMIDADE",
        df["faixa_umidade"].value_counts().sort_index().to_string(),
        "",
        "INDICADORES MENSAIS",
        mensal.to_string(),
        "",
        "CORRELACAO COM A BOMBA (|r| em ordem decrescente)",
        ranking.to_string(),
    ]
    with open(caminho, "w", encoding="utf-8") as arquivo:
        arquivo.write("\n".join(linhas) + "\n")
    return caminho


def main():
    os.makedirs(PASTA_SAIDA, exist_ok=True)
    os.makedirs(PASTA_GRAFICOS, exist_ok=True)
    estilo()
    relatorio = {}

    bruto = carregar_bruto()
    df = limpar(bruto, relatorio)
    df = enriquecer(df, relatorio)

    acionamentos = detectar_acionamentos(df)
    diario = indicadores_por_periodo(df, acionamentos, "D", "data")
    mensal = indicadores_por_periodo(df, acionamentos, "MS", "mes")
    mensal.index = mensal.index.strftime("%Y-%m")
    corr = matriz_correlacao(df)

    # --- CSVs de saida -----------------------------------------------------
    colunas_tratadas = [
        "ciclo", "umidade", "umidade_mm6h", "ph", "ph_mm6h", "ldr_bruto",
        "nitrogenio", "fosforo", "potassio", "npk_completo", "bomba", "motivo",
        "faixa_umidade", "ph_na_faixa", "agua_aplicada_l", "umidade_imputada",
        "ph_imputado", "hora_reconstruida", "origem",
    ]
    df[colunas_tratadas].to_csv(os.path.join(PASTA_SAIDA, "leituras_tratadas.csv"),
                                date_format="%Y-%m-%d %H:%M")
    acionamentos.to_csv(os.path.join(PASTA_SAIDA, "acionamentos_bomba.csv"),
                        index=False, date_format="%Y-%m-%d %H:%M")
    diario.to_csv(os.path.join(PASTA_SAIDA, "indicadores_diarios.csv"),
                  date_format="%Y-%m-%d")
    mensal.to_csv(os.path.join(PASTA_SAIDA, "indicadores_mensais.csv"))
    corr.to_csv(os.path.join(PASTA_SAIDA, "matriz_correlacao.csv"))

    # --- Graficos ----------------------------------------------------------
    grafico_evolucao(df)
    grafico_bomba_x_leituras(df)
    grafico_distribuicoes(df)
    grafico_correlacao(corr)
    grafico_indicadores_diarios(diario)

    caminho = escrever_relatorio(relatorio, df, acionamentos, mensal, corr)
    with open(caminho, encoding="utf-8") as arquivo:
        print(arquivo.read())
    print(f"Arquivos gerados em {PASTA_SAIDA} e {PASTA_GRAFICOS}")


if __name__ == "__main__":
    main()
