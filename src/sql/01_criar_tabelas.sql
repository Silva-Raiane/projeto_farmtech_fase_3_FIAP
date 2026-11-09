-- ============================================================================
-- FarmTech Solutions - Fase 3 | Criacao das tabelas de trabalho (Oracle)
-- ----------------------------------------------------------------------------
-- Cinco tabelas simples, cada uma com chave primaria, que guardam os dados
-- necessarios para responder as perguntas de gestao listadas no README.
--
--   TB_PARAMETRO_CULTURA  <- data/fase2/parametros_cultura_cafe.json   (Fase 2)
--   TB_MANEJO_TALHAO      <- data/fase1/dados_fazenda.csv              (Fase 1)
--   TB_LEITURA_SENSOR     <- data/processed/leituras_tratadas.csv      (Fase 3)
--   TB_ACIONAMENTO_BOMBA  <- data/processed/acionamentos_bomba.csv     (Fase 3)
--   TB_LEITURA_BRUTA      <- data/raw/monitor_serial_*.txt (texto)     (Fase 2/3)
--
-- Convencao de nomes: DT_ data/hora | VL_ valor medido | QT_ quantidade |
--                     FL_ flag 0/1  | DS_ descricao   | NM_ nome | ID_ chave
--
-- Executar uma vez (SQL Developer, SQLcl, ou: python src/banco/carga_oracle.py
-- --criar-tabelas). Requer Oracle 12c ou superior (colunas IDENTITY).
--
-- Autora: Raiane Sirlene da Silva - RM 575984
-- ============================================================================

-- ---------------------------------------------------------------------------
-- 1. Parametros agronomicos da cultura (limites usados pelo firmware)
-- ---------------------------------------------------------------------------
CREATE TABLE TB_PARAMETRO_CULTURA (
    NM_CULTURA              VARCHAR2(40)   NOT NULL,
    NM_CIENTIFICO           VARCHAR2(60),
    VL_PH_MINIMO            NUMBER(4,2)    NOT NULL,
    VL_PH_MAXIMO            NUMBER(4,2)    NOT NULL,
    VL_UMIDADE_CRITICA      NUMBER(5,2)    NOT NULL,
    VL_UMIDADE_PREVENTIVA   NUMBER(5,2)    NOT NULL,
    VL_UMIDADE_MAXIMA       NUMBER(5,2)    NOT NULL,
    QT_VAZAO_BOMBA_L_H      NUMBER(8)      NOT NULL,
    DT_CARGA                TIMESTAMP      DEFAULT SYSTIMESTAMP,
    CONSTRAINT PK_PARAMETRO_CULTURA PRIMARY KEY (NM_CULTURA),
    CONSTRAINT CK_PARAM_PH CHECK (VL_PH_MINIMO < VL_PH_MAXIMO)
);

-- ---------------------------------------------------------------------------
-- 2. Talhoes e manejo de insumos (aplicacao Python da Fase 1)
--    A chave natural (cultura + dimensoes) impede duplicar o mesmo talhao.
-- ---------------------------------------------------------------------------
CREATE TABLE TB_MANEJO_TALHAO (
    ID_TALHAO               NUMBER GENERATED ALWAYS AS IDENTITY,
    NM_CULTURA              VARCHAR2(40)   NOT NULL,
    VL_DIM1_M               NUMBER(10,2)   NOT NULL,
    VL_DIM2_M               NUMBER(10,2)   NOT NULL,
    VL_DIM3_M               NUMBER(10,2)   DEFAULT 0 NOT NULL,
    VL_AREA_M2              NUMBER(14,2)   NOT NULL,
    VL_AREA_HA              NUMBER(12,4)   NOT NULL,
    VL_ESPACAMENTO_M        NUMBER(5,2)    NOT NULL,
    QT_RUAS                 NUMBER(6)      NOT NULL,
    QT_METROS_LINEARES      NUMBER(14,2)   NOT NULL,
    DS_INSUMO               VARCHAR2(60)   NOT NULL,
    QT_DOSE_ML_METRO        NUMBER(8,2)    NOT NULL,
    QT_LITROS_INSUMO        NUMBER(14,2)   NOT NULL,
    DT_CARGA                TIMESTAMP      DEFAULT SYSTIMESTAMP,
    CONSTRAINT PK_MANEJO_TALHAO PRIMARY KEY (ID_TALHAO),
    CONSTRAINT UK_MANEJO_TALHAO UNIQUE (NM_CULTURA, VL_DIM1_M, VL_DIM2_M, VL_DIM3_M)
);

-- ---------------------------------------------------------------------------
-- 3. Leituras horarias dos sensores S1 (umidade), S2 (pH) e S3 (N, P, K)
--    Uma linha por hora; DT_LEITURA e unica -> recarga nao duplica.
-- ---------------------------------------------------------------------------
CREATE TABLE TB_LEITURA_SENSOR (
    ID_LEITURA              NUMBER GENERATED ALWAYS AS IDENTITY,
    DT_LEITURA              TIMESTAMP      NOT NULL,
    NM_CULTURA              VARCHAR2(40)   NOT NULL,
    NR_CICLO                NUMBER(8),
    VL_UMIDADE              NUMBER(5,2)    NOT NULL,
    VL_UMIDADE_MM6H         NUMBER(5,2)    NOT NULL,
    VL_PH                   NUMBER(4,2)    NOT NULL,
    VL_PH_MM6H              NUMBER(5,3)    NOT NULL,
    VL_LDR_BRUTO            NUMBER(4),
    FL_NITROGENIO           NUMBER(1)      NOT NULL,
    FL_FOSFORO              NUMBER(1)      NOT NULL,
    FL_POTASSIO             NUMBER(1)      NOT NULL,
    FL_BOMBA_LIGADA         NUMBER(1)      NOT NULL,
    DS_MOTIVO               VARCHAR2(20),
    DS_FAIXA_UMIDADE        VARCHAR2(20),
    FL_PH_NA_FAIXA          NUMBER(1)      NOT NULL,
    QT_AGUA_APLICADA_L      NUMBER(8)      DEFAULT 0 NOT NULL,
    FL_VALOR_IMPUTADO       NUMBER(1)      DEFAULT 0 NOT NULL,
    DS_ORIGEM               VARCHAR2(12)   NOT NULL,
    DT_CARGA                TIMESTAMP      DEFAULT SYSTIMESTAMP,
    CONSTRAINT PK_LEITURA_SENSOR PRIMARY KEY (ID_LEITURA),
    CONSTRAINT UK_LEITURA_SENSOR UNIQUE (DT_LEITURA),
    CONSTRAINT FK_LEITURA_CULTURA FOREIGN KEY (NM_CULTURA)
        REFERENCES TB_PARAMETRO_CULTURA (NM_CULTURA),
    CONSTRAINT CK_LEITURA_UMIDADE CHECK (VL_UMIDADE BETWEEN 0 AND 100),
    CONSTRAINT CK_LEITURA_PH CHECK (VL_PH BETWEEN 0 AND 14),
    CONSTRAINT CK_LEITURA_FLAGS CHECK (
        FL_NITROGENIO IN (0,1) AND FL_FOSFORO IN (0,1) AND FL_POTASSIO IN (0,1)
        AND FL_BOMBA_LIGADA IN (0,1) AND FL_PH_NA_FAIXA IN (0,1)
        AND FL_VALOR_IMPUTADO IN (0,1)),
    CONSTRAINT CK_LEITURA_ORIGEM CHECK (DS_ORIGEM IN ('wokwi','simulada','reconstruida'))
);

-- ---------------------------------------------------------------------------
-- 4. Historico de acionamentos da bomba (cada bloco continuo ligado)
-- ---------------------------------------------------------------------------
CREATE TABLE TB_ACIONAMENTO_BOMBA (
    ID_ACIONAMENTO          NUMBER GENERATED ALWAYS AS IDENTITY,
    DT_INICIO               TIMESTAMP      NOT NULL,
    DT_FIM                  TIMESTAMP      NOT NULL,
    NM_CULTURA              VARCHAR2(40)   NOT NULL,
    QT_DURACAO_H            NUMBER(5)      NOT NULL,
    QT_AGUA_APLICADA_L      NUMBER(10)     NOT NULL,
    VL_UMIDADE_INICIO       NUMBER(5,2),
    VL_UMIDADE_FIM          NUMBER(5,2),
    VL_PH_MEDIO             NUMBER(4,2),
    DS_MOTIVO               VARCHAR2(20),
    DT_CARGA                TIMESTAMP      DEFAULT SYSTIMESTAMP,
    CONSTRAINT PK_ACIONAMENTO_BOMBA PRIMARY KEY (ID_ACIONAMENTO),
    CONSTRAINT UK_ACIONAMENTO_INICIO UNIQUE (DT_INICIO),
    CONSTRAINT FK_ACIONAMENTO_CULTURA FOREIGN KEY (NM_CULTURA)
        REFERENCES TB_PARAMETRO_CULTURA (NM_CULTURA),
    CONSTRAINT CK_ACIONAMENTO_PERIODO CHECK (DT_FIM > DT_INICIO)
);

-- ---------------------------------------------------------------------------
-- 5. Linhas de texto do Monitor Serial, exatamente como foram copiadas
--    (trilha de auditoria: qualquer numero tratado pode ser conferido com a
--    linha original). Arquivo + numero da linha formam a chave.
-- ---------------------------------------------------------------------------
CREATE TABLE TB_LEITURA_BRUTA (
    NM_ARQUIVO              VARCHAR2(60)   NOT NULL,
    NR_LINHA                NUMBER(8)      NOT NULL,
    DS_LINHA                VARCHAR2(200)  NOT NULL,
    DT_CARGA                TIMESTAMP      DEFAULT SYSTIMESTAMP,
    CONSTRAINT PK_LEITURA_BRUTA PRIMARY KEY (NM_ARQUIVO, NR_LINHA)
);

-- Indice para as consultas por periodo (o UNIQUE de DT_LEITURA ja indexa)
CREATE INDEX IX_LEITURA_BOMBA ON TB_LEITURA_SENSOR (FL_BOMBA_LIGADA, DT_LEITURA);

COMMENT ON TABLE TB_PARAMETRO_CULTURA IS 'Limites agronomicos e vazao da bomba por cultura (Fase 2)';
COMMENT ON TABLE TB_MANEJO_TALHAO     IS 'Talhoes, area e insumos calculados na aplicacao Python (Fase 1)';
COMMENT ON TABLE TB_LEITURA_SENSOR    IS 'Leituras horarias tratadas dos sensores S1, S2 e S3';
COMMENT ON TABLE TB_ACIONAMENTO_BOMBA IS 'Blocos continuos de bomba ligada e agua aplicada';
COMMENT ON TABLE TB_LEITURA_BRUTA     IS 'Linhas originais do Monitor Serial (auditoria)';
