"""Reemplaza bronze_platform_daily_2026 desde el merge diario de Adverity, con chequeo anti-caída.

Fuente: ds_martech_gold_adverity.gl_digital_marketing_digital_merge (DV360, TikTok, Pinterest, Date >= 2026-01-01).
Antes de reemplazar compara impresiones por plataforma × mes CERRADO contra la copia actual: si en
algún mes cerrado la fuente trae más de 2% menos, no toca la tabla y reporta qué plataforma/mes cayó.
Los meses en curso y los datos nuevos no se chequean (siempre pueden crecer).

  python scripts/refresh_platform_daily.py
"""
import sys

from google.cloud import bigquery

PROJECT = "mbw-spl-brazil"
SRC = f"{PROJECT}.ds_martech_gold_adverity.gl_digital_marketing_digital_merge"
DST = f"{PROJECT}.ds_meli_reach_validation.bronze_platform_daily_2026"
FILTER = "Platform IN ('DV360','TikTok','Pinterest') AND Date >= '2026-01-01'"
MAX_DROP = 0.02

Q_CHECK = f"""
WITH cur AS (SELECT Platform p, DATE_TRUNC(Date, MONTH) m, SUM(Impressions) i FROM `{DST}` WHERE {FILTER} GROUP BY 1, 2),
src AS (SELECT Platform p, DATE_TRUNC(Date, MONTH) m, SUM(Impressions) i FROM `{SRC}` WHERE {FILTER} GROUP BY 1, 2)
SELECT cur.p, FORMAT_DATE('%Y-%m', cur.m) m, cur.i cur_i, IFNULL(src.i, 0) new_i
FROM cur LEFT JOIN src USING (p, m)
WHERE cur.m < DATE_TRUNC(CURRENT_DATE(), MONTH) AND IFNULL(src.i, 0) < cur.i * (1 - {MAX_DROP})
ORDER BY 1, 2
"""


def main():
    client = bigquery.Client(project=PROJECT)
    bad = list(client.query(Q_CHECK).result())
    if bad:
        print("NO se reemplaza bronze_platform_daily_2026: la fuente cae >2% en meses cerrados:")
        for r in bad:
            print(f"  {r.p} {r.m}: actual {r.cur_i:,.0f} -> nuevo {r.new_i:,.0f} ({(r.new_i / r.cur_i - 1) * 100:+.1f}%)")
        sys.exit(1)
    client.query(f"CREATE OR REPLACE TABLE `{DST}` AS SELECT * FROM `{SRC}` WHERE {FILTER}").result()
    n = next(iter(client.query(f"SELECT COUNT(*) n, ROUND(SUM(Impressions)) i, MAX(Date) d FROM `{DST}`").result()))
    print(f"bronze_platform_daily_2026 reemplazada: {n.n:,} filas · {n.i:,.0f} impresiones · hasta {n.d}")


if __name__ == "__main__":
    main()
