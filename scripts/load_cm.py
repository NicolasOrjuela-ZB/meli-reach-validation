"""Carga a BigQuery los CSV exportados de CM360 y de plataformas, con validación.

Detecta solo el tipo de archivo y lo carga en la tabla bronze que corresponde:

  Reach de CM por ventana (Reach_total_Reg, sin Week)      -> bronze_cm_reach_window
  Reach de CM semanal con pivot (Pivoted_reach, con Week)  -> bronze_cm_reach_weekly
  Impresiones diarias de CM (Estandar_total_Reg, con Date) -> bronze_cm_impressions_daily
  Reach semanal de DV360 (DV_REACH_PIVOTED)                 -> bronze_platform_reach_weekly
  Reach semanal de Pinterest (Day to Day Cumulative)        -> bronze_platform_reach_weekly

Reglas de reemplazo (idempotente: correrlo dos veces con el mismo archivo no duplica):
  - Ventana: se borran las filas de ese window_id antes de insertar (refresco de líneas en curso).
  - Semanal CM: se borran las semanas que trae el archivo (CM ajusta las últimas ~3 semanas).
    La semana INICIAL recortada (el rango arranca después del domingo, como pasa siempre con
    LAST_60_DAYS) se descarta: pisaría con datos parciales la semana completa ya cargada.
  - Diario CM: se borran las fechas del rango del archivo antes de insertar.
  - Plataforma: se borran las semanas de esa plataforma que trae el archivo.
  - Semana parcial final (termina después del corte del reporte) se carga igual; la vista la marca.

Uso:  python scripts/load_cm.py <archivo.csv> [--dry-run]
      python scripts/load_cm.py carpeta/   (procesa todos los CSV de la carpeta)
Requiere: gcloud auth application-default login  (o GOOGLE_APPLICATION_CREDENTIALS)
"""
import datetime as dt, io, os, re, sys
import pandas as pd
from google.cloud import bigquery

PROJECT = "mbw-spl-brazil"
DS = "ds_meli_reach_validation"
NOW = dt.datetime.now(dt.timezone.utc).replace(microsecond=0).isoformat()
R = [f"reach_{k}" for k in range(1, 11)]


# ---------- parsers ----------
def _cm_body(path):
    L = open(path, encoding="utf-8-sig").read().splitlines()
    meta = {l.split(",")[0]: l.split(",", 1)[1] for l in L[:40] if "," in l and not l.startswith("Advertiser,Equals")}
    n = L.index("Report Fields")
    body = [l for l in L[n + 1:] if l and not l.startswith("Grand Total") and not l.startswith("A dash")]
    df = pd.read_csv(io.StringIO("\n".join(body)), na_values=["-"], dtype=str)
    s, e = [x.strip() for x in meta["Date Range"].split("-")]
    rs, re_ = pd.to_datetime(s), pd.to_datetime(e)
    return df, rs, re_, meta


def window_id(rs, re_):
    """Usa el id del plan (R01..R30) si el rango coincide; si no, un id nuevo por fechas."""
    try:
        client = bigquery.Client(project=PROJECT)
        q = f"SELECT DISTINCT window_id FROM `{PROJECT}.{DS}.bronze_cm_window_plan` WHERE range_start = '{rs.date()}' AND range_end = '{re_.date()}'"
        rows = list(client.query(q).result())
        if rows:
            return rows[0]["window_id"]
    except Exception:
        pass
    return f"W{rs:%Y%m%d}_{re_:%Y%m%d}"


def parse_cm_window(path):
    df, rs, re_, _ = _cm_body(path)
    df.columns = ["advertiser_id", "advertiser", "placement_id", "placement", "reach_1"]
    df["reach_1"] = pd.to_numeric(df.reach_1.str.replace(",", ""), errors="coerce").astype("Int64")
    out = pd.DataFrame({"window_id": window_id(rs, re_), "range_start": rs.date(), "range_end": re_.date(),
                        "advertiser_id": df.advertiser_id, "advertiser": df.advertiser, "placement_id": df.placement_id,
                        "placement": df.placement, "reach_1": df.reach_1, "source_file": os.path.basename(path), "loaded_at": NOW})
    return "bronze_cm_reach_window", out, f"window_id = '{out.window_id.iloc[0]}'"


def parse_cm_daily(path):
    """Estandar_total_Reg: impresiones por placement × día. Reemplaza las fechas del rango."""
    df, rs, re_, meta = _cm_body(path)
    df.columns = ["date", "advertiser_id", "advertiser", "placement_id", "placement", "impressions"]
    out = pd.DataFrame({"date": pd.to_datetime(df.date).dt.date,
                        "advertiser_id": df.advertiser_id, "placement_id": df.placement_id,
                        "impressions": pd.to_numeric(df.impressions.str.replace(",", ""), errors="coerce").astype("Int64"),
                        "report_timezone": meta.get("Report Time Zone", "America/Los_Angeles"),
                        "loaded_at": NOW})
    return "bronze_cm_impressions_daily", out, f"date BETWEEN '{rs.date()}' AND '{re_.date()}'"


def parse_cm_weekly(path):
    df, rs, re_, _ = _cm_body(path)
    df.columns = ["week", "advertiser_id", "advertiser", "placement_id", "placement", "impressions"] + R
    wk = pd.to_datetime(df.week)
    assert (wk.dt.weekday == 6).all(), "La columna Week de CM debe empezar en domingo"
    out = pd.DataFrame({"week_start": wk.dt.date, "week_end": (wk + pd.Timedelta(days=6)).dt.date,
                        "advertiser_id": df.advertiser_id, "advertiser": df.advertiser, "placement_id": df.placement_id, "placement": df.placement,
                        "impressions": pd.to_numeric(df.impressions.str.replace(",", ""), errors="coerce").astype("Int64")})
    for c in R:
        out[c] = pd.to_numeric(df[c].str.replace(",", ""), errors="coerce").round().astype("Int64")
    out["report_range_start"], out["report_range_end"] = rs.date(), re_.date()
    out["report_generated_at"], out["loaded_at"] = NOW, NOW
    # La primera semana del rango casi nunca arranca en domingo (LAST_60_DAYS): CM la mide
    # recortada desde el inicio del rango, y cargarla pisaría con datos parciales la semana
    # completa que ya está en la tabla. Se descarta. La parcial FINAL sí se carga (la vista la marca).
    recorte = out.week_start < rs.date()
    if recorte.any():
        print(f"  descarta la semana inicial recortada {min(out.week_start[recorte])} "
              f"({int(recorte.sum())} filas: el rango del reporte arranca el {rs.date()})")
        out = out[~recorte]
    weeks = ",".join(f"'{w}'" for w in sorted(out.week_start.unique()))
    pids = ",".join(f"'{p}'" for p in out.placement_id.unique())
    # Reemplaza solo las semanas y placements que trae el archivo (Pinterest filtrado no pisa el resto)
    return "bronze_cm_reach_weekly", out, f"week_start IN ({weeks}) AND placement_id IN ({pids})"


def parse_dv360_weekly(path):
    L = open(path, encoding="utf-8-sig").read().splitlines()
    body = [l for l in L[1:] if re.match(r"^\d{4}/\d{2}/\d{2} - ", l)]
    df = pd.read_csv(io.StringIO("\n".join([L[0]] + body)), na_values=["-"], dtype=str)
    df.columns = ["week", "advertiser", "line_name", "line_id", "impressions"] + R
    ws = pd.to_datetime(df.week.str[:10]); we = pd.to_datetime(df.week.str[13:])
    assert (ws.dt.weekday == 6).all(), "Semanas de DV360 deben empezar en domingo"
    out = pd.DataFrame({"platform": "DV360", "week_start": ws.dt.date, "week_end": we.dt.date, "advertiser": df.advertiser,
                        "line_id": df.line_id, "line_name": df.line_name,
                        "impressions": pd.to_numeric(df.impressions.str.replace(",", ""), errors="coerce").round().astype("Int64")})
    for c in R:
        out[c] = pd.to_numeric(df[c].str.replace(",", ""), errors="coerce").round().astype("Int64")
    out["report_range_start"], out["report_range_end"] = out.week_start.min(), out.week_end.max()
    out["source_file"], out["loaded_at"] = os.path.basename(path), NOW
    weeks = ",".join(f"'{w}'" for w in sorted(out.week_start.unique()))
    return "bronze_platform_reach_weekly", out, f"platform = 'DV360' AND week_start IN ({weeks})"


def parse_pinterest_weekly(path):
    d = pd.read_csv(path, dtype=str)
    rc = [c for c in d.columns if c.startswith("REACH_") and c.endswith("_WK")]
    for c in rc:
        d[c] = pd.to_numeric(d[c].str.replace(".", "", regex=False).str.replace(",", "", regex=False), errors="coerce")
    d["EVENT_DATE"] = pd.to_datetime(d.EVENT_DATE); d["ws"] = pd.to_datetime(d.WEEK_FILTER_START_DT); d["we"] = pd.to_datetime(d.WEEK_FILTER_END_DT)
    d = d.dropna(subset=["REACH_1_WK"]).sort_values("EVENT_DATE")
    last = d.groupby(["CAMPAIGN_ID", "ws"]).tail(1)  # REACH_n_WK acumula dentro de la semana: vale el último día
    out = pd.DataFrame({"platform": "Pinterest", "week_start": last.ws.dt.date, "week_end": last.we.dt.date, "advertiser": last.ACCOUNT_NAME,
                        "line_id": last.CAMPAIGN_ID, "line_name": last.CAMPAIGN_NAME.str.strip(), "impressions": pd.array([None] * len(last), dtype="Int64")})
    for k in range(1, 11):
        col = f"REACH_{k}_WK"
        out[f"reach_{k}"] = last[col].round().astype("Int64").values if col in last else pd.array([None] * len(last), dtype="Int64")
    yr = last.EVENT_DATE.dt.year
    out["report_range_start"] = pd.to_datetime(yr.astype(str) + "-01-01").dt.date.values
    out["report_range_end"] = last.EVENT_DATE.dt.date.values
    out["source_file"], out["loaded_at"] = os.path.basename(path), NOW
    weeks = ",".join(f"'{w}'" for w in sorted(out.week_start.unique()))
    return "bronze_platform_reach_weekly", out, f"platform = 'Pinterest' AND week_start IN ({weeks})"


def detect(path):
    head = open(path, encoding="utf-8-sig", errors="ignore").read(4000)
    if head.startswith("EVENT_DATE,"):
        return parse_pinterest_weekly
    if head.startswith("Week,Advertiser,Insertion Order"):
        return parse_dv360_weekly
    if "Report Fields" in open(path, encoding="utf-8-sig", errors="ignore").read():
        L = open(path, encoding="utf-8-sig").read().splitlines()
        hdr = L[L.index("Report Fields") + 1]
        return parse_cm_weekly if hdr.startswith("Week,") else parse_cm_daily if hdr.startswith("Date,") else parse_cm_window
    raise ValueError(f"No reconozco el formato de {path}")


# ---------- load ----------
def load(path, dry=False):
    parser = detect(path)
    table, df, where = parser(path)
    full = f"{PROJECT}.{DS}.{table}"
    print(f"{os.path.basename(path)} -> {table}: {len(df)} filas · reemplaza [{where[:90]}{'…' if len(where) > 90 else ''}]")
    if dry:
        return
    client = bigquery.Client(project=PROJECT)
    schema = client.get_table(full).schema
    cols = [f.name for f in schema]
    df = df[cols]
    for f in schema:
        if f.field_type == "DATE":
            df[f.name] = pd.to_datetime(df[f.name]).dt.date
        if f.field_type == "TIMESTAMP":
            df[f.name] = pd.to_datetime(df[f.name], utc=True)
    client.query(f"DELETE FROM `{full}` WHERE {where}").result()
    job = client.load_table_from_dataframe(df, full, job_config=bigquery.LoadJobConfig(schema=schema, write_disposition="WRITE_APPEND"))
    job.result()
    n = next(iter(client.query(f"SELECT COUNT(*) n FROM `{full}`").result()))["n"]
    print(f"  cargado · {table} ahora tiene {n:,} filas")


if __name__ == "__main__":
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    dry = "--dry-run" in sys.argv
    if not args:
        sys.exit(__doc__)
    paths = []
    for a in args:
        paths += sorted(os.path.join(a, f) for f in os.listdir(a) if f.lower().endswith(".csv")) if os.path.isdir(a) else [a]
    for p in paths:
        load(p, dry)
