-- ============================================================================
-- FarmTech Solutions - Fase 3 | Remove as tabelas de trabalho (recomecar do zero)
-- Ordem inversa das dependencias (filhas antes da tabela de parametros).
-- ============================================================================
DROP TABLE TB_LEITURA_BRUTA     CASCADE CONSTRAINTS PURGE;
DROP TABLE TB_ACIONAMENTO_BOMBA CASCADE CONSTRAINTS PURGE;
DROP TABLE TB_LEITURA_SENSOR    CASCADE CONSTRAINTS PURGE;
DROP TABLE TB_MANEJO_TALHAO     CASCADE CONSTRAINTS PURGE;
DROP TABLE TB_PARAMETRO_CULTURA CASCADE CONSTRAINTS PURGE;
