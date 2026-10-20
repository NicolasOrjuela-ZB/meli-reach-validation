"""Exporta las definiciones (DDL) de todas las tablas y vistas del dataset a sql/views.sql.

Correrlo después de cambiar cualquier vista en BigQuery, para que el repo tenga la definición vigente:
    python scripts/export_views.py
"""
import os
from google.cloud import bigquery

PROJECT, DS = "mbw-spl-brazil", "ds_meli_reach_validation"
OUT = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "sql", "views.sql")

q = f"""
SELECT table_name, table_type, ddl
FROM `{PROJECT}.{DS}.INFORMATION_SCHEMA.TABLES`
ORDER BY CASE table_type WHEN 'BASE TABLE' THEN 0 ELSE 1 END,
         CASE WHEN table_name LIKE 'bronze%' THEN 0 WHEN table_name LIKE 'silver%' THEN 1 ELSE 2 END, table_name
"""
rows = list(bigquery.Client(project=PROJECT).query(q).result())
with open(OUT, "w", encoding="utf-8") as f:
    f.write(f"-- DDL de {PROJECT}.{DS} · generado por scripts/export_views.py\n")
    f.write("-- Orden: tablas bronze, luego vistas silver y gold (respeta dependencias).\n\n")
    for r in rows:
        f.write(f"-- ===== {r['table_type']}: {r['table_name']} =====\n{r['ddl'].rstrip()}\n\n")
print(f"{len(rows)} objetos -> sql/views.sql")
