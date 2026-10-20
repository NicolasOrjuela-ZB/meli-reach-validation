"""Regenera docs/index.html con la data actual de BigQuery.

Lee las vistas gold de mbw-spl-brazil.ds_meli_reach_validation, las embebe como JSON
en scripts/template.html y escribe docs/index.html (lo que publica GitHub Pages y carga el Hub).

Uso local:   python scripts/build_dashboard.py
En Actions:  corre semanal con la credencial GCP_CREDENTIALS (ver .github/workflows/build.yml)
"""
import datetime, json, os, re, sys
from google.cloud import bigquery

PROJECT = "mbw-spl-brazil"
DS = f"{PROJECT}.ds_meli_reach_validation"
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TEMPLATE = os.path.join(ROOT, "scripts", "template.html")
OUT = os.path.join(ROOT, "docs", "index.html")

KEYS = ["Advertiser", "Placement", "Placement_ID", "TOOL", "Country", "Chosen_Frequency",
        "Impressions_CM", "Impressions_Plataforma", "Reach_CM", "Reach_Plataforma", "IIEE_CM", "IIEE_Plataforma",
        "Fecha_inicio", "Fecha_fin", "Network_Code", "Campana", "Plataforma", "Match", "Reach_Status_CM", "En_curso"]

Q_LINES = f"""
SELECT {", ".join(KEYS)}
FROM `{DS}.gold_cm_vs_platform_cliente`
"""

Q_WEEKLY = f"""
SELECT country, platform, week_start,
  SUM(IF(impressions_platform IS NOT NULL, impressions_cm, 0)) impr_cm_m, SUM(impressions_platform) impr_pl,
  SUM(IF(reach_1_platform IS NOT NULL, reach_1_cm, 0)) reach_cm_m, SUM(reach_1_platform) reach_pl,
  SUM(IF(iiee_platform IS NOT NULL AND iiee_cm IS NOT NULL, iiee_cm, 0)) iiee_cm_m, SUM(IF(iiee_cm IS NOT NULL, iiee_platform, 0)) iiee_pl,
  COUNT(DISTINCT placement_id) lines, COUNT(DISTINCT IF(match_type='MATCH', placement_id, NULL)) lines_m
FROM `{DS}.gold_cm_vs_platform_weekly`
WHERE platform IS NOT NULL GROUP BY 1,2,3
"""

Q_WEEKLY_LINES = f"""
SELECT placement_id, STRING_AGG(CONCAT(CAST(week_start AS STRING),'|',IFNULL(CAST(impressions_cm AS STRING),''),'|',IFNULL(CAST(impressions_platform AS STRING),''),
  '|',IFNULL(CAST(reach_1_cm AS STRING),''),'|',IFNULL(CAST(reach_1_platform AS STRING),''),'|',IFNULL(CAST(iiee_cm AS STRING),''),'|',IFNULL(CAST(iiee_platform AS STRING),'')), ';' ORDER BY week_start) AS w
FROM `{DS}.gold_cm_vs_platform_weekly` GROUP BY 1
"""

Q_CUTOFF = f"SELECT MAX(report_range_end) AS cutoff FROM `{DS}.bronze_cm_reach_weekly`"


def main():
    client = bigquery.Client(project=PROJECT)
    lines = [dict(r) for r in client.query(Q_LINES).result()]
    weekly = [dict(r) for r in client.query(Q_WEEKLY).result()]
    wl = {r["placement_id"]: r["w"] for r in client.query(Q_WEEKLY_LINES).result()}
    cutoff = next(iter(client.query(Q_CUTOFF).result()))["cutoff"]
    if not lines or not weekly:
        sys.exit("Sin datos: no se regenera el dashboard")

    def cell(v):
        if isinstance(v, (datetime.date, datetime.datetime)):
            return v.isoformat()[:10]
        return v

    L = [[cell(r.get(k)) for k in KEYS] for r in lines]
    W = [[w["country"], w["platform"], cell(w["week_start"]), w["impr_cm_m"], w["impr_pl"], w["reach_cm_m"], w["reach_pl"],
          w["iiee_cm_m"], w["iiee_pl"], w["lines"], w["lines_m"]] for w in weekly]
    data = json.dumps({"keys": KEYS, "lines": L, "weekly": W, "wl": wl, "cutoff": cell(cutoff),
                       "generated": datetime.date.today().isoformat()}, separators=(",", ":"), ensure_ascii=False)
    html = open(TEMPLATE, encoding="utf-8").read()
    assert "/*__DATA__*/" in html, "template.html sin marcador /*__DATA__*/"
    html = html.replace("/*__DATA__*/", data)
    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    open(OUT, "w", encoding="utf-8").write(html)
    print(f"OK docs/index.html · {len(L)} líneas · {len(W)} filas semanales · corte {cutoff} · {len(html)//1024} KB")


if __name__ == "__main__":
    main()
