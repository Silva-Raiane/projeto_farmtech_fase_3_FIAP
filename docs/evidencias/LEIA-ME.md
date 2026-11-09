# Evidências da carga e das consultas

Gerados automaticamente por `python src/banco/carga_oracle.py --tudo`:

- `log_carga.txt` — resumo de cada carga (rode duas vezes: na 2ª, "Novos" = 0)
- `resultado_consultas.txt` — saída das 8 consultas de gestão
- `q1_agua_por_mes.csv` … `q8_conferencia_carga.csv` — cada resultado exportado

Capturas de tela a adicionar nesta pasta:

- `01_container_oracle.png` — `docker ps` / log "DATABASE IS READY TO USE!"
- `02_tabelas_criadas.png` — as 5 tabelas no SQL Developer
- `03_carga_python.png` — terminal com as duas execuções da carga
- `04_consultas.png` — resultado das consultas no SQL Developer
