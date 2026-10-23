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
        "Fecha_inicio", "Fecha_fin", "Network_Code", "Campana", "Plataforma", "Match", "Reach_Status_CM", "En_curso",
        "Cruce_Dudoso", "TC_Level2", "TC_Level3"]

# Guía oficial de tipos de campaña: se embebe para el glosario y el orden del filtro
Q_GUIDE = f"""
SELECT bu, level1, level2, level3, prefix, definicion, en_guia
FROM `{DS}.bronze_taxonomy_campaign_types` ORDER BY bu, level1, level2, level3, prefix
"""

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

Q_NC = f"""
SELECT network_code, network_code_std, IFNULL(plataforma_compra,'') plataforma_compra, IFNULL(partner,'') partner, IFNULL(formato,'') formato, IFNULL(implementation,'') implementation
FROM `{DS}.silver_taxonomy_network_codes`
"""

# Pestaña Impresiones: una fila por lado × línea (los atributos son de línea; solo mes/impresiones varían)
Q_IMP = f"""
SELECT side, country, brand,
  -- DV360 · Otros son deals/medios vía DV sin network code claro: se consolidan en Deals
  REPLACE(platform_group, 'DV360 · Otros', 'DV360 · Deals') AS platform_group,
  match_type, tc_level2, tc_level3, IFNULL(tool_id,'') tool_id, line_name,
  IFNULL(network_code,'') network_code, IFNULL(partner,'') partner, IFNULL(campana,'') campana,
  ARRAY_AGG(STRUCT(FORMAT_DATE('%Y-%m', month) AS m, impressions AS i) ORDER BY month) AS ser
FROM `{DS}.gold_impressions_monthly`
GROUP BY 1,2,3,4,5,6,7,8,9,10,11,12
"""


# Facturas de CM (país × BU × mes). Pocas filas: se embeben tal cual.
# Columnas explícitas a propósito: la tabla gana campos (currency, cost_original, fx_rate,
# invoice_lines…) sin que esto cambie ni se rompa.
Q_INV = f"""
SELECT country, brand, FORMAT_DATE('%Y-%m', month) AS m, invoice_id,
  CAST(impressions_billed AS INT64) AS impressions_billed, cost_usd, cpm_usd
FROM `{DS}.bronze_cm_invoices` ORDER BY country, brand, month
"""


def b36(n):
    s, d = "", "0123456789abcdefghijklmnopqrstuvwxyz"
    while True:
        s, n = d[n % 36] + s, n // 36
        if not n:
            return s


def encode_imp(rows):
    """Codificación columnar con diccionarios: meses y dimensiones indexados, serie mensual "idx:valor36,…" (base 36)."""
    months = sorted({s["m"] for r in rows for s in r["ser"]})
    midx = {m: i for i, m in enumerate(months)}
    dims = {k: [] for k in ("country", "brand", "pg", "mt", "nc", "partner", "camp", "name", "tc")}
    didx = {k: {} for k in dims}

    def dx(k, v):
        v = v or ""
        if v not in didx[k]:
            didx[k][v] = len(dims[k])
            dims[k].append(v)
        return didx[k][v]

    lines = []
    for r in rows:
        ser = ",".join(f"{midx[s['m']]}:{b36(int(s['i']))}" for s in r["ser"])
        lines.append([0 if r["side"] == "CM" else 1, dx("country", r["country"]), dx("brand", r["brand"]),
                      dx("pg", r["platform_group"]), dx("mt", r["match_type"]), r["tool_id"],
                      dx("name", r["line_name"]), dx("nc", r["network_code"]), dx("partner", r["partner"]),
                      dx("camp", r["campana"]), ser,
                      dx("tc", f'{r["tc_level2"]} § {r["tc_level3"]}')])
    return {"months": months, "dims": dims, "lines": lines}


def main():
    client = bigquery.Client(project=PROJECT)
    lines = [dict(r) for r in client.query(Q_LINES).result()]
    weekly = [dict(r) for r in client.query(Q_WEEKLY).result()]
    wl = {r["placement_id"]: r["w"] for r in client.query(Q_WEEKLY_LINES).result()}
    cutoff = next(iter(client.query(Q_CUTOFF).result()))["cutoff"]
    nc = {r["network_code"]: [r["network_code_std"], r["plataforma_compra"], r["partner"], r["formato"], r["implementation"]]
          for r in client.query(Q_NC).result()}
    imp = encode_imp([dict(r) for r in client.query(Q_IMP).result()])
    imp["inv"] = [[r["country"], r["brand"], r["m"], r["invoice_id"], r["impressions_billed"],
                   round(r["cost_usd"], 2), round(r["cpm_usd"], 6)] for r in client.query(Q_INV).result()]
    guide = [[r["bu"], r["level1"], r["level2"], r["level3"], r["prefix"], r["definicion"], r["en_guia"]]
             for r in client.query(Q_GUIDE).result()]
    if not lines or not weekly:
        sys.exit("Sin datos: no se regenera el dashboard")

    def cell(v):
        if isinstance(v, (datetime.date, datetime.datetime)):
            return v.isoformat()[:10]
        return v

    L = [[cell(r.get(k)) for k in KEYS] for r in lines]
    W = [[w["country"], w["platform"], cell(w["week_start"]), w["impr_cm_m"], w["impr_pl"], w["reach_cm_m"], w["reach_pl"],
          w["iiee_cm_m"], w["iiee_pl"], w["lines"], w["lines_m"]] for w in weekly]
    data = json.dumps({"keys": KEYS, "lines": L, "weekly": W, "wl": wl, "nc": nc, "imp": imp, "guide": guide,
                       "cutoff": cell(cutoff),
                       "generated": datetime.date.today().isoformat()}, separators=(",", ":"), ensure_ascii=False)
    html = open(TEMPLATE, encoding="utf-8").read()
    assert "/*__DATA__*/" in html, "template.html sin marcador /*__DATA__*/"
    html = html.replace("/*__DATA__*/", data)
    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    open(OUT, "w", encoding="utf-8").write(html)
    print(f"OK docs/index.html · {len(L)} líneas · {len(W)} filas semanales · {len(imp['lines'])} líneas de impresiones "
          f"· {len(imp['inv'])} facturas · {len(guide)} tipos de campaña "
          f"({len(json.dumps(imp, separators=(',', ':')))//1024} KB) · corte {cutoff} · {len(html)//1024} KB")


if __name__ == "__main__":
    main()
