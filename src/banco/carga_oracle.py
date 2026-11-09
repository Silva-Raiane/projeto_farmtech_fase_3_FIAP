# -*- coding: utf-8 -*-
"""
FarmTech Solutions - Fase 3 | Carga dos dados no Oracle
=======================================================
Le os arquivos produzidos nas Fases 1, 2 e 3 e grava nas tabelas de trabalho
do Oracle usando a biblioteca oficial python-oracledb (modo "thin": nao exige
instalar o Oracle Client).

    Arquivo de origem                         ->  Tabela
    data/fase2/parametros_cultura_cafe.json   ->  TB_PARAMETRO_CULTURA
    data/fase1/dados_fazenda.csv              ->  TB_MANEJO_TALHAO
    data/processed/leituras_tratadas.csv      ->  TB_LEITURA_SENSOR
    data/processed/acionamentos_bomba.csv     ->  TB_ACIONAMENTO_BOMBA
    data/raw/monitor_serial_*.txt (texto)     ->  TB_LEITURA_BRUTA

CARGA REPETIVEL: toda gravacao usa MERGE sobre uma chave natural (data/hora
da leitura, inicio do acionamento, nome da cultura, dimensoes do talhao,
arquivo + linha). Rodar a carga de novo ATUALIZA o que ja existe e so insere
o que e novo - nenhum registro e duplicado. Registros que deixaram de existir
nos arquivos (ex.: acionamentos recalculados apos incluir dados do Wokwi) sao
removidos, para o banco espelhar exatamente o historico tratado.

Uso:
    python src/banco/carga_oracle.py --criar-tabelas   # DDL (1a vez)
    python src/banco/carga_oracle.py                   # carga (repetivel)
    python src/banco/carga_oracle.py --consultas       # consultas de gestao
    python src/banco/carga_oracle.py --tudo            # os tres, em ordem
    python src/banco/carga_oracle.py --dry-run         # valida os arquivos sem banco

Conexao (variaveis de ambiente ou arquivo config/oracle.env):
    ORACLE_USER=farmtech
    ORACLE_PASSWORD=********
    ORACLE_DSN=localhost:1521/FREEPDB1

Autora: Raiane Sirlene da Silva - RM 575984
"""

import argparse
import glob
import json
import math
import os
import re
import sys
from datetime import datetime

import pandas as pd

# ---------------------------------------------------------------------------
# CAMINHOS
# ---------------------------------------------------------------------------
RAIZ = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
ARQ_PARAMETROS = os.path.join(RAIZ, "data", "fase2", "parametros_cultura_cafe.json")
ARQ_TALHOES = os.path.join(RAIZ, "data", "fase1", "dados_fazenda.csv")
ARQ_LEITURAS = os.path.join(RAIZ, "data", "processed", "leituras_tratadas.csv")
ARQ_ACIONAMENTOS = os.path.join(RAIZ, "data", "processed", "acionamentos_bomba.csv")
PADRAO_LOGS = os.path.join(RAIZ, "data", "raw", "monitor_serial_*.txt")
SQL_DDL = os.path.join(RAIZ, "src", "sql", "01_criar_tabelas.sql")
SQL_CONSULTAS = os.path.join(RAIZ, "src", "sql", "02_consultas_gestao.sql")
ARQ_ENV = os.path.join(RAIZ, "config", "oracle.env")
PASTA_EVIDENCIAS = os.path.join(RAIZ, "docs", "evidencias")

TAMANHO_LOTE = 500          # linhas por executemany


# ===========================================================================
# CONEXAO
# ===========================================================================
def ler_config():
    """Variaveis de ambiente tem prioridade; config/oracle.env completa."""
    config = {}
    if os.path.exists(ARQ_ENV):
        with open(ARQ_ENV, encoding="utf-8") as arquivo:
            for linha in arquivo:
                linha = linha.strip()
                if linha and not linha.startswith("#") and "=" in linha:
                    chave, valor = linha.split("=", 1)
                    config[chave.strip()] = valor.strip()
    for chave in ("ORACLE_USER", "ORACLE_PASSWORD", "ORACLE_DSN"):
        if os.environ.get(chave):
            config[chave] = os.environ[chave]
    config.setdefault("ORACLE_DSN", "localhost:1521/FREEPDB1")
    return config


def conectar():
    try:
        import oracledb
    except ImportError:
        sys.exit("Biblioteca ausente. Instale com: pip install oracledb")

    config = ler_config()
    if not config.get("ORACLE_USER") or not config.get("ORACLE_PASSWORD"):
        sys.exit("Defina ORACLE_USER e ORACLE_PASSWORD (variaveis de ambiente "
                 "ou config/oracle.env - veja config/oracle.env.example).")

    print(f"Conectando em {config['ORACLE_DSN']} como {config['ORACLE_USER']}...")
    conexao = oracledb.connect(user=config["ORACLE_USER"],
                               password=config["ORACLE_PASSWORD"],
                               dsn=config["ORACLE_DSN"])
    print(f"Conectado - Oracle {conexao.version}\n")
    return conexao


# ===========================================================================
# LEITURA DOS ARQUIVOS SQL
# ===========================================================================
def remover_comentarios(sql):
    return "\n".join(l for l in sql.splitlines() if not l.strip().startswith("--"))


def comandos_ddl():
    """Divide o script de DDL em comandos (separados por ';' no fim)."""
    with open(SQL_DDL, encoding="utf-8") as arquivo:
        texto = remover_comentarios(arquivo.read())
    return [c.strip() for c in texto.split(";") if c.strip()]


def consultas_nomeadas():
    """Le o arquivo de consultas e devolve [(nome, descricao, sql), ...]."""
    with open(SQL_CONSULTAS, encoding="utf-8") as arquivo:
        texto = arquivo.read()
    blocos = re.split(r"^-- @consulta:\s*(\S+)\s*$", texto, flags=re.MULTILINE)
    consultas = []
    for nome, corpo in zip(blocos[1::2], blocos[2::2]):
        descricao = " ".join(
            l.strip()[3:].strip() for l in corpo.strip().splitlines()
            if l.strip().startswith("--")
        )
        sql = remover_comentarios(corpo).strip().rstrip(";").strip()
        consultas.append((nome, descricao, sql))
    return consultas


# ===========================================================================
# PREPARACAO DOS REGISTROS (arquivos -> listas de dicionarios)
# ===========================================================================
def nulo(valor):
    """NaN do Pandas vira NULL no Oracle."""
    if valor is None:
        return None
    if isinstance(valor, float) and math.isnan(valor):
        return None
    return valor


def preparar_parametros():
    with open(ARQ_PARAMETROS, encoding="utf-8") as arquivo:
        p = json.load(arquivo)
    return [{
        "nm_cultura": p["cultura"], "nm_cientifico": p.get("nome_cientifico"),
        "ph_min": p["ph_minimo"], "ph_max": p["ph_maximo"],
        "umid_crit": p["umidade_critica_pct"],
        "umid_prev": p["umidade_preventiva_pct"],
        "umid_max": p["umidade_maxima_pct"], "vazao": p["vazao_bomba_l_h"],
    }], p["cultura"]


def preparar_talhoes():
    df = pd.read_csv(ARQ_TALHOES)
    return [{
        "cultura": r.cultura, "dim1": float(r.dim1_m), "dim2": float(r.dim2_m),
        "dim3": float(r.dim3_m), "area_m2": float(r.area_m2),
        "area_ha": float(r.area_ha), "espac": float(r.espacamento_m),
        "ruas": int(r.ruas), "metros": float(r.metros_lineares),
        "insumo": r.insumo, "dose": float(r.dose_ml_por_metro),
        "litros": float(r.litros_insumo),
    } for r in df.itertuples(index=False)]


def preparar_leituras(cultura):
    df = pd.read_csv(ARQ_LEITURAS, parse_dates=["data_hora"])
    registros = []
    for r in df.itertuples(index=False):
        registros.append({
            "dt": r.data_hora.to_pydatetime(), "cultura": cultura,
            "ciclo": nulo(None if pd.isna(r.ciclo) else int(r.ciclo)),
            "umid": round(float(r.umidade), 2), "umid_mm": float(r.umidade_mm6h),
            "ph": round(float(r.ph), 2), "ph_mm": float(r.ph_mm6h),
            "ldr": None if pd.isna(r.ldr_bruto) else int(r.ldr_bruto),
            "n": int(r.nitrogenio), "p": int(r.fosforo), "k": int(r.potassio),
            "bomba": int(r.bomba), "motivo": nulo(r.motivo),
            "faixa": nulo(r.faixa_umidade), "ph_ok": int(r.ph_na_faixa),
            "agua": int(r.agua_aplicada_l),
            "imputado": int(bool(r.umidade_imputada) or bool(r.ph_imputado)),
            "origem": r.origem,
        })
    return registros


def preparar_acionamentos(cultura):
    df = pd.read_csv(ARQ_ACIONAMENTOS,
                     parse_dates=["data_hora_inicio", "data_hora_fim"])
    return [{
        "inicio": r.data_hora_inicio.to_pydatetime(),
        "fim": r.data_hora_fim.to_pydatetime(), "cultura": cultura,
        "duracao": int(r.duracao_h), "agua": int(r.agua_aplicada_l),
        "u_ini": nulo(float(r.umidade_inicio)), "u_fim": nulo(float(r.umidade_fim)),
        "ph": nulo(float(r.ph_medio)), "motivo": r.motivo,
    } for r in df.itertuples(index=False)]


def preparar_linhas_brutas():
    registros = []
    for caminho in sorted(glob.glob(PADRAO_LOGS)):
        nome = os.path.basename(caminho)
        with open(caminho, encoding="utf-8", errors="ignore") as arquivo:
            for numero, linha in enumerate(arquivo, start=1):
                linha = linha.rstrip("\r\n")
                if linha.strip():
                    registros.append({"arquivo": nome, "linha": numero,
                                      "texto": linha[:200]})
    return registros


# ===========================================================================
# COMANDOS MERGE (a garantia de carga repetivel esta aqui)
# ===========================================================================
MERGE_PARAMETRO = """
MERGE INTO TB_PARAMETRO_CULTURA t
USING (SELECT :nm_cultura AS NM_CULTURA FROM DUAL) s
   ON (t.NM_CULTURA = s.NM_CULTURA)
 WHEN MATCHED THEN UPDATE SET
      t.NM_CIENTIFICO = :nm_cientifico, t.VL_PH_MINIMO = :ph_min,
      t.VL_PH_MAXIMO = :ph_max, t.VL_UMIDADE_CRITICA = :umid_crit,
      t.VL_UMIDADE_PREVENTIVA = :umid_prev, t.VL_UMIDADE_MAXIMA = :umid_max,
      t.QT_VAZAO_BOMBA_L_H = :vazao, t.DT_CARGA = SYSTIMESTAMP
 WHEN NOT MATCHED THEN INSERT
      (NM_CULTURA, NM_CIENTIFICO, VL_PH_MINIMO, VL_PH_MAXIMO, VL_UMIDADE_CRITICA,
       VL_UMIDADE_PREVENTIVA, VL_UMIDADE_MAXIMA, QT_VAZAO_BOMBA_L_H)
      VALUES (:nm_cultura, :nm_cientifico, :ph_min, :ph_max, :umid_crit,
              :umid_prev, :umid_max, :vazao)
"""

MERGE_TALHAO = """
MERGE INTO TB_MANEJO_TALHAO t
USING (SELECT :cultura AS NM_CULTURA, :dim1 AS VL_DIM1_M, :dim2 AS VL_DIM2_M,
              :dim3 AS VL_DIM3_M FROM DUAL) s
   ON (t.NM_CULTURA = s.NM_CULTURA AND t.VL_DIM1_M = s.VL_DIM1_M
       AND t.VL_DIM2_M = s.VL_DIM2_M AND t.VL_DIM3_M = s.VL_DIM3_M)
 WHEN MATCHED THEN UPDATE SET
      t.VL_AREA_M2 = :area_m2, t.VL_AREA_HA = :area_ha,
      t.VL_ESPACAMENTO_M = :espac, t.QT_RUAS = :ruas,
      t.QT_METROS_LINEARES = :metros, t.DS_INSUMO = :insumo,
      t.QT_DOSE_ML_METRO = :dose, t.QT_LITROS_INSUMO = :litros,
      t.DT_CARGA = SYSTIMESTAMP
 WHEN NOT MATCHED THEN INSERT
      (NM_CULTURA, VL_DIM1_M, VL_DIM2_M, VL_DIM3_M, VL_AREA_M2, VL_AREA_HA,
       VL_ESPACAMENTO_M, QT_RUAS, QT_METROS_LINEARES, DS_INSUMO,
       QT_DOSE_ML_METRO, QT_LITROS_INSUMO)
      VALUES (:cultura, :dim1, :dim2, :dim3, :area_m2, :area_ha, :espac, :ruas,
              :metros, :insumo, :dose, :litros)
"""

MERGE_LEITURA = """
MERGE INTO TB_LEITURA_SENSOR t
USING (SELECT :dt AS DT_LEITURA FROM DUAL) s
   ON (t.DT_LEITURA = s.DT_LEITURA)
 WHEN MATCHED THEN UPDATE SET
      t.NM_CULTURA = :cultura, t.NR_CICLO = :ciclo, t.VL_UMIDADE = :umid,
      t.VL_UMIDADE_MM6H = :umid_mm, t.VL_PH = :ph, t.VL_PH_MM6H = :ph_mm,
      t.VL_LDR_BRUTO = :ldr, t.FL_NITROGENIO = :n, t.FL_FOSFORO = :p,
      t.FL_POTASSIO = :k, t.FL_BOMBA_LIGADA = :bomba, t.DS_MOTIVO = :motivo,
      t.DS_FAIXA_UMIDADE = :faixa, t.FL_PH_NA_FAIXA = :ph_ok,
      t.QT_AGUA_APLICADA_L = :agua, t.FL_VALOR_IMPUTADO = :imputado,
      t.DS_ORIGEM = :origem, t.DT_CARGA = SYSTIMESTAMP
 WHEN NOT MATCHED THEN INSERT
      (DT_LEITURA, NM_CULTURA, NR_CICLO, VL_UMIDADE, VL_UMIDADE_MM6H, VL_PH,
       VL_PH_MM6H, VL_LDR_BRUTO, FL_NITROGENIO, FL_FOSFORO, FL_POTASSIO,
       FL_BOMBA_LIGADA, DS_MOTIVO, DS_FAIXA_UMIDADE, FL_PH_NA_FAIXA,
       QT_AGUA_APLICADA_L, FL_VALOR_IMPUTADO, DS_ORIGEM)
      VALUES (:dt, :cultura, :ciclo, :umid, :umid_mm, :ph, :ph_mm, :ldr, :n, :p,
              :k, :bomba, :motivo, :faixa, :ph_ok, :agua, :imputado, :origem)
"""

MERGE_ACIONAMENTO = """
MERGE INTO TB_ACIONAMENTO_BOMBA t
USING (SELECT :inicio AS DT_INICIO FROM DUAL) s
   ON (t.DT_INICIO = s.DT_INICIO)
 WHEN MATCHED THEN UPDATE SET
      t.DT_FIM = :fim, t.NM_CULTURA = :cultura, t.QT_DURACAO_H = :duracao,
      t.QT_AGUA_APLICADA_L = :agua, t.VL_UMIDADE_INICIO = :u_ini,
      t.VL_UMIDADE_FIM = :u_fim, t.VL_PH_MEDIO = :ph, t.DS_MOTIVO = :motivo,
      t.DT_CARGA = SYSTIMESTAMP
 WHEN NOT MATCHED THEN INSERT
      (DT_INICIO, DT_FIM, NM_CULTURA, QT_DURACAO_H, QT_AGUA_APLICADA_L,
       VL_UMIDADE_INICIO, VL_UMIDADE_FIM, VL_PH_MEDIO, DS_MOTIVO)
      VALUES (:inicio, :fim, :cultura, :duracao, :agua, :u_ini, :u_fim, :ph, :motivo)
"""

MERGE_BRUTA = """
MERGE INTO TB_LEITURA_BRUTA t
USING (SELECT :arquivo AS NM_ARQUIVO, :linha AS NR_LINHA FROM DUAL) s
   ON (t.NM_ARQUIVO = s.NM_ARQUIVO AND t.NR_LINHA = s.NR_LINHA)
 WHEN MATCHED THEN UPDATE SET t.DS_LINHA = :texto, t.DT_CARGA = SYSTIMESTAMP
 WHEN NOT MATCHED THEN INSERT (NM_ARQUIVO, NR_LINHA, DS_LINHA)
      VALUES (:arquivo, :linha, :texto)
"""


# ===========================================================================
# EXECUCAO DA CARGA
# ===========================================================================
def contar(cursor, tabela):
    cursor.execute(f"SELECT COUNT(*) FROM {tabela}")
    return cursor.fetchone()[0]


# Campos numericos que podem vir NULL: o tipo e declarado antes do
# executemany para o driver nao "adivinhar" texto quando a 1a linha e NULL.
CAMPOS_NUMERICOS_NULAVEIS = {
    "TB_LEITURA_SENSOR": ["ciclo", "ldr"],
    "TB_ACIONAMENTO_BOMBA": ["u_ini", "u_fim", "ph"],
}


def gravar_em_lotes(cursor, tabela, comando, registros):
    import oracledb
    tipos = {campo: oracledb.DB_TYPE_NUMBER
             for campo in CAMPOS_NUMERICOS_NULAVEIS.get(tabela, [])}
    for inicio in range(0, len(registros), TAMANHO_LOTE):
        if tipos:
            cursor.setinputsizes(**tipos)
        cursor.executemany(comando, registros[inicio:inicio + TAMANHO_LOTE])


def remover_obsoletos(cursor, tabela, coluna, chaves_atuais):
    """Apaga linhas cuja chave nao existe mais nos arquivos de origem."""
    cursor.execute(f"SELECT {coluna} FROM {tabela}")
    no_banco = {linha[0] for linha in cursor.fetchall()}
    obsoletas = sorted(no_banco - set(chaves_atuais))
    if obsoletas:
        cursor.executemany(f"DELETE FROM {tabela} WHERE {coluna} = :1",
                           [(chave,) for chave in obsoletas])
    return len(obsoletas)


def preparar_tudo():
    parametros, cultura = preparar_parametros()
    return [
        ("TB_PARAMETRO_CULTURA", MERGE_PARAMETRO, parametros, None),
        ("TB_MANEJO_TALHAO", MERGE_TALHAO, preparar_talhoes(), None),
        ("TB_LEITURA_SENSOR", MERGE_LEITURA, preparar_leituras(cultura),
         ("DT_LEITURA", "dt")),
        ("TB_ACIONAMENTO_BOMBA", MERGE_ACIONAMENTO, preparar_acionamentos(cultura),
         ("DT_INICIO", "inicio")),
        ("TB_LEITURA_BRUTA", MERGE_BRUTA, preparar_linhas_brutas(), None),
    ]


def carregar(conexao):
    cursor = conexao.cursor()
    plano = preparar_tudo()
    log = [f"CARGA ORACLE - {datetime.now():%d/%m/%Y %H:%M:%S}",
           f"{'Tabela':<24}{'Arquivo':>9}{'Antes':>9}{'Depois':>9}"
           f"{'Novos':>8}{'Removidos':>11}"]

    for tabela, comando, registros, chave in plano:
        antes = contar(cursor, tabela)
        gravar_em_lotes(cursor, tabela, comando, registros)
        removidos = 0
        if chave:
            coluna, campo = chave
            removidos = remover_obsoletos(cursor, tabela, coluna,
                                          [r[campo] for r in registros])
        depois = contar(cursor, tabela)
        log.append(f"{tabela:<24}{len(registros):>9}{antes:>9}{depois:>9}"
                   f"{depois - antes + removidos:>8}{removidos:>11}")

    conexao.commit()
    log.append("Commit realizado. Rode de novo: 'Novos' fica 0 e os totais nao mudam.")
    texto = "\n".join(log)
    print(texto)

    os.makedirs(PASTA_EVIDENCIAS, exist_ok=True)
    with open(os.path.join(PASTA_EVIDENCIAS, "log_carga.txt"), "a",
              encoding="utf-8") as arquivo:
        arquivo.write(texto + "\n\n")


def criar_tabelas(conexao):
    cursor = conexao.cursor()
    for comando in comandos_ddl():
        titulo = " ".join(comando.split()[:3])
        try:
            cursor.execute(comando)
            print(f"[ok] {titulo}")
        except Exception as erro:          # ORA-00955: o objeto ja existe
            if "ORA-00955" in str(erro):
                print(f"[--] {titulo} (ja existe)")
            else:
                raise
    conexao.commit()
    print()


def executar_consultas(conexao):
    cursor = conexao.cursor()
    os.makedirs(PASTA_EVIDENCIAS, exist_ok=True)
    resumo = []
    for nome, descricao, sql in consultas_nomeadas():
        cursor.execute(sql)
        colunas = [c[0] for c in cursor.description]
        resultado = pd.DataFrame(cursor.fetchall(), columns=colunas)
        resultado.to_csv(os.path.join(PASTA_EVIDENCIAS, f"{nome}.csv"), index=False)

        bloco = f"{'=' * 78}\n{nome} - {descricao}\n{'-' * 78}\n" \
                f"{resultado.to_string(index=False)}\n"
        print(bloco)
        resumo.append(bloco)

    with open(os.path.join(PASTA_EVIDENCIAS, "resultado_consultas.txt"), "w",
              encoding="utf-8") as arquivo:
        arquivo.write(f"Executado em {datetime.now():%d/%m/%Y %H:%M:%S}\n\n")
        arquivo.write("\n".join(resumo))
    print(f"Resultados exportados em {PASTA_EVIDENCIAS}")


def dry_run():
    """Valida arquivos e SQL sem conectar - util antes de ter o Oracle pronto."""
    print("DRY-RUN: nenhuma conexao com o banco sera aberta\n")
    for tabela, _, registros, _ in preparar_tudo():
        print(f"{tabela:<24}{len(registros):>6} registros  ex.: {registros[0]}")
    print(f"\nDDL      : {len(comandos_ddl())} comandos em {os.path.basename(SQL_DDL)}")
    consultas = consultas_nomeadas()
    print(f"Consultas: {len(consultas)} em {os.path.basename(SQL_CONSULTAS)}")
    for nome, descricao, _ in consultas:
        print(f"  - {nome}: {descricao[:70]}")


def main():
    parser = argparse.ArgumentParser(description="Carga FarmTech no Oracle")
    parser.add_argument("--criar-tabelas", action="store_true")
    parser.add_argument("--consultas", action="store_true",
                        help="so executa as consultas de gestao")
    parser.add_argument("--tudo", action="store_true",
                        help="cria tabelas, carrega e consulta")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    if args.dry_run:
        dry_run()
        return

    conexao = conectar()
    try:
        if args.criar_tabelas or args.tudo:
            criar_tabelas(conexao)
        if args.tudo or not (args.criar_tabelas or args.consultas):
            carregar(conexao)
        if args.consultas or args.tudo:
            executar_consultas(conexao)
    finally:
        conexao.close()


if __name__ == "__main__":
    main()
