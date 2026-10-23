"""Aplica en BigQuery las definiciones de sql/*.sql (CREATE OR REPLACE VIEW / CREATE TABLE).

  python scripts/apply_sql.py sql/gold_impressions_monthly.sql [...]   # aplica los archivos dados
  python scripts/apply_sql.py --all                                    # aplica todos los de sql/
  python scripts/apply_sql.py --dry-run sql/x.sql                      # solo valida (dry run de BigQuery)

Las vistas no guardan los comentarios del archivo, así que el .sql del repo es la fuente de verdad.
"""
import glob
import os
import sys

from google.cloud import bigquery

PROJECT = "mbw-spl-brazil"
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def main(paths, dry):
    client = bigquery.Client(project=PROJECT)
    cfg = bigquery.QueryJobConfig(dry_run=True) if dry else None
    for p in paths:
        sql = open(p, encoding="utf-8").read().strip()
        if not sql:
            continue
        job = client.query(sql, job_config=cfg)
        if not dry:
            job.result()
        print(f"{'validado' if dry else 'aplicado'}: {os.path.relpath(p, ROOT)}")


if __name__ == "__main__":
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    if "--all" in sys.argv:
        args = sorted(glob.glob(os.path.join(ROOT, "sql", "*.sql")))
    if not args:
        sys.exit(__doc__)
    main(args, "--dry-run" in sys.argv)
