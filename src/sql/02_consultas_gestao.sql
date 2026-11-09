-- ============================================================================
-- FarmTech Solutions - Fase 3 | Consultas de gestao sobre o historico
-- ----------------------------------------------------------------------------
-- Uma consulta para cada informacao relevante listada no README (secao
-- "Levantamento de requisitos"). A linha "-- @consulta:" da nome a cada uma:
-- o programa src/banco/carga_oracle.py --consultas usa esse nome para
-- executar e exportar o resultado em docs/evidencias/.
--
-- Autora: Raiane Sirlene da Silva - RM 575984
-- ============================================================================

-- @consulta: q1_agua_por_mes
-- I1. Qual foi a quantidade total de agua aplicada em cada mes?
SELECT TO_CHAR(TRUNC(DT_INICIO, 'MM'), 'YYYY-MM')   AS MES,
       COUNT(*)                                     AS ACIONAMENTOS,
       SUM(QT_DURACAO_H)                            AS HORAS_BOMBA,
       SUM(QT_AGUA_APLICADA_L)                      AS AGUA_LITROS,
       ROUND(SUM(QT_AGUA_APLICADA_L) / 1000, 1)     AS AGUA_M3
  FROM TB_ACIONAMENTO_BOMBA
 GROUP BY TRUNC(DT_INICIO, 'MM')
 ORDER BY MES;

-- @consulta: q2_variacao_ph_semanal
-- I2. Como variou o pH do solo ao longo do tempo?
SELECT TO_CHAR(TRUNC(DT_LEITURA, 'IW'), 'YYYY-MM-DD') AS SEMANA_INICIO,
       ROUND(AVG(VL_PH), 2)                           AS PH_MEDIO,
       MIN(VL_PH)                                     AS PH_MIN,
       MAX(VL_PH)                                     AS PH_MAX,
       ROUND(STDDEV(VL_PH), 3)                        AS PH_DESVIO
  FROM TB_LEITURA_SENSOR
 GROUP BY TRUNC(DT_LEITURA, 'IW')
 ORDER BY SEMANA_INICIO;

-- @consulta: q3_top10_dias_bomba
-- I3. Quais foram os dias com maior tempo de bomba ligada?
SELECT TO_CHAR(TRUNC(DT_LEITURA), 'YYYY-MM-DD')  AS DIA,
       SUM(FL_BOMBA_LIGADA)                      AS HORAS_BOMBA,
       SUM(QT_AGUA_APLICADA_L)                   AS AGUA_LITROS,
       ROUND(MIN(VL_UMIDADE_MM6H), 1)            AS UMIDADE_MIN
  FROM TB_LEITURA_SENSOR
 GROUP BY TRUNC(DT_LEITURA)
 ORDER BY HORAS_BOMBA DESC, DIA
 FETCH FIRST 10 ROWS ONLY;

-- @consulta: q4_insumos_por_cultura
-- I4. Quais culturas consomem mais insumos (total e por hectare)?
SELECT NM_CULTURA                                         AS CULTURA,
       DS_INSUMO                                          AS INSUMO,
       COUNT(*)                                           AS TALHOES,
       SUM(VL_AREA_HA)                                    AS AREA_HA,
       SUM(QT_LITROS_INSUMO)                              AS LITROS_INSUMO,
       ROUND(SUM(QT_LITROS_INSUMO) / SUM(VL_AREA_HA), 1)  AS LITROS_POR_HA
  FROM TB_MANEJO_TALHAO
 GROUP BY NM_CULTURA, DS_INSUMO
 ORDER BY LITROS_INSUMO DESC;

-- @consulta: q5_bloqueio_por_ph
-- I5. Por quanto tempo o pH fora da faixa da cultura bloqueou a irrigacao,
--     e quanto tempo o solo ficou seco nesse periodo?
--     (os limites vem da tabela de parametros, nao estao fixos no SQL)
SELECT TO_CHAR(TRUNC(L.DT_LEITURA, 'MM'), 'YYYY-MM')                AS MES,
       P.VL_PH_MINIMO || ' a ' || P.VL_PH_MAXIMO                    AS FAIXA_PH,
       SUM(CASE WHEN L.VL_PH NOT BETWEEN P.VL_PH_MINIMO AND P.VL_PH_MAXIMO
                THEN 1 ELSE 0 END)                                  AS HORAS_PH_FORA,
       SUM(CASE WHEN L.VL_PH NOT BETWEEN P.VL_PH_MINIMO AND P.VL_PH_MAXIMO
                 AND L.VL_UMIDADE < P.VL_UMIDADE_PREVENTIVA
                THEN 1 ELSE 0 END)                                  AS HORAS_SECO_E_BLOQUEADO,
       ROUND(MIN(L.VL_PH), 2)                                       AS PH_MIN_MES
  FROM TB_LEITURA_SENSOR L
  JOIN TB_PARAMETRO_CULTURA P ON P.NM_CULTURA = L.NM_CULTURA
 GROUP BY TRUNC(L.DT_LEITURA, 'MM'), P.VL_PH_MINIMO, P.VL_PH_MAXIMO
 ORDER BY MES;

-- @consulta: q6_umidade_por_faixa
-- I6. Quanto tempo o solo passou em cada faixa de umidade, mes a mes
--     (estresse hidrico < 35%, seco, adequado, saturado)?
SELECT TO_CHAR(TRUNC(DT_LEITURA, 'MM'), 'YYYY-MM')   AS MES,
       DS_FAIXA_UMIDADE                              AS FAIXA,
       COUNT(*)                                      AS HORAS,
       ROUND(100 * RATIO_TO_REPORT(COUNT(*))
             OVER (PARTITION BY TRUNC(DT_LEITURA, 'MM')), 1) AS PCT_DO_MES
  FROM TB_LEITURA_SENSOR
 GROUP BY TRUNC(DT_LEITURA, 'MM'), DS_FAIXA_UMIDADE
 ORDER BY MES, FAIXA;

-- @consulta: q7_eficacia_por_motivo
-- I7. Cada tipo de acionamento resolve o problema do solo? (duracao media
--     e ganho medio de umidade por motivo da decisao)
SELECT DS_MOTIVO                                      AS MOTIVO,
       COUNT(*)                                       AS ACIONAMENTOS,
       ROUND(AVG(QT_DURACAO_H), 1)                    AS DURACAO_MEDIA_H,
       ROUND(AVG(VL_UMIDADE_FIM - VL_UMIDADE_INICIO), 1) AS GANHO_MEDIO_UMIDADE,
       SUM(QT_AGUA_APLICADA_L)                        AS AGUA_LITROS
  FROM TB_ACIONAMENTO_BOMBA
 GROUP BY DS_MOTIVO
 ORDER BY ACIONAMENTOS DESC;

-- @consulta: q8_conferencia_carga
-- Conferencia da carga: registros por tabela (rodar 2x prova que a carga
-- e repetivel - os totais nao mudam)
SELECT 'TB_PARAMETRO_CULTURA' AS TABELA, COUNT(*) AS REGISTROS FROM TB_PARAMETRO_CULTURA
UNION ALL SELECT 'TB_MANEJO_TALHAO',     COUNT(*) FROM TB_MANEJO_TALHAO
UNION ALL SELECT 'TB_LEITURA_SENSOR',    COUNT(*) FROM TB_LEITURA_SENSOR
UNION ALL SELECT 'TB_ACIONAMENTO_BOMBA', COUNT(*) FROM TB_ACIONAMENTO_BOMBA
UNION ALL SELECT 'TB_LEITURA_BRUTA',     COUNT(*) FROM TB_LEITURA_BRUTA;
