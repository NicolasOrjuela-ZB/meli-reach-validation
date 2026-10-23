"""Reemplaza bronze_cm_invoices con el CSV exportado de la hoja "Facturas".

Columnas esperadas (como salen de la hoja): Factura, BU, Pais, Periodo, Fecha, Impresiones, Costo, CPM
(opcional Moneda). Una fila por línea de factura: México trae varias líneas por factura y se suman.
  - Pais: CL/UY/CO/MX/PE/AR -> MLC/MLU/MCO/MLM/MPE/MLA
  - BU: ML / MP (Mercado Libre / Mercado Pago)
  - Periodo: cualquier formato con mes y año (2026-03, mar-2026, 03/2026, marzo 2026…) -> primer día del mes
  - Moneda: si no viene, MXN para México y USD para el resto. El costo en MXN se pasa a USD con --fx
  - El CPM se recalcula sobre el total ya agrupado; avisa si alguna línea se aparta del CPM de su factura

  python scripts/load_invoices.py facturas.csv                 # reemplaza la tabla
  python scripts/load_invoices.py facturas.csv --dry-run       # solo muestra el resumen
  python scripts/load_invoices.py facturas.csv --fx 19.1       # otra tasa MXN -> USD (default 18.26)
  python scripts/load_invoices.py facturas.csv --cpm-tol 0.3   # umbral del aviso de CPM (default 0.5)

Después de cargar hay que regenerar el dashboard (scripts/build_dashboard.py).
"""
import datetime as dt
import os
import re
import sys

import numpy as np
import pandas as pd
from google.cloud import bigquery

PROJECT = "mbw-spl-brazil"
TABLE = f"{PROJECT}.ds_meli_reach_validation.bronze_cm_invoices"
COUNTRY = {"CL": "MLC", "UY": "MLU", "CO": "MCO", "MX": "MLM", "PE": "MPE", "AR": "MLA"}
FX_MXN = 18.26  # MXN por USD; se cambia con --fx
MONTHS = {m: i + 1 for i, m in enumerate(
    ["ene", "feb", "mar", "abr", "may", "jun", "jul", "ago", "sep", "oct", "nov", "dic"])}
MONTHS.update({m: i + 1 for i, m in enumerate(
    ["jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"])})


def col(df, *names):
    """Busca una columna por nombre, ignorando mayúsculas, tildes y espacios."""
    def norm(s):
        s = str(s).strip().lower()
        for a, b in zip("áéíóú", "aeiou"):
            s = s.replace(a, b)
        return re.sub(r"[^a-z0-9]", "", s)
    m = {norm(c): c for c in df.columns}
    for n in names:
        if norm(n) in m:
            return df[m[norm(n)]]
    sys.exit(f"Falta la columna {names[0]} (columnas del archivo: {list(df.columns)})")


def to_month(v):
    """Primer día del mes a partir de un periodo en cualquier formato razonable."""
    s = str(v).strip()
    if not s or s.lower() in ("nan", "none"):
        return None
    if isinstance(v, (dt.date, dt.datetime, pd.Timestamp)):
        return dt.date(v.year, v.month, 1)
    m = re.search(r"(20\d{2})\D{0,3}(\d{1,2})\b", s)          # 2026-03, 2026/3
    if m:
        return dt.date(int(m.group(1)), int(m.group(2)), 1)
    m = re.search(r"\b(\d{1,2})\D{1,3}(20\d{2})\b", s)         # 03/2026
    if m:
        return dt.date(int(m.group(2)), int(m.group(1)), 1)
    m = re.search(r"([a-záéíóú]{3})[a-záéíóú]*\.?\D{0,3}(20\d{2})", s.lower())  # mar-2026, marzo 2026
    if m and m.group(1) in MONTHS:
        return dt.date(int(m.group(2)), MONTHS[m.group(1)], 1)
    ts = pd.to_datetime(s, errors="coerce", dayfirst=True)
    return None if pd.isna(ts) else dt.date(ts.year, ts.month, 1)


def num1(v):
    """Número desde texto, aceptando formato es (1.234.567,89) y en (1,234,567.89).

    Con un solo separador seguido de 3 dígitos se asume miles (27.160 = 27160); los montos de
    factura nunca vienen con un solo decimal de 3 cifras.
    """
    t = re.sub(r"[^\d,.\-]", "", str(v))
    if not re.search(r"\d", t):
        return None
    last = max(t.rfind(","), t.rfind("."))
    if last >= 0:
        head, dec = t[:last], len(t) - last - 1
        many = t.count(t[last]) > 1
        # 3 decimales solo son decimales de verdad si la parte entera es 0 (CPM: 0,022)
        thousands = many or (dec == 3 and head.lstrip("-") not in ("", "0"))
        t = re.sub(r"[,.]", "", t) if thousands else re.sub(r"[,.]", "", head) + "." + t[last + 1:]
    return pd.to_numeric(t, errors="coerce")


def num(s):
    return pd.to_numeric(s.map(num1), errors="coerce")


def main(path, dry, fx, tol):
    raw = pd.read_csv(path, dtype=str, keep_default_na=False)
    df = pd.DataFrame({
        "invoice_id": col(raw, "Factura", "Invoice").astype(str).str.strip(),
        "brand": col(raw, "BU", "Marca").astype(str).str.strip().str.upper(),
        "country": col(raw, "Pais", "País", "Country").astype(str).str.strip().str.upper(),
        "month": col(raw, "Periodo", "Período", "Mes").map(to_month),
        "impressions_billed": num(col(raw, "Impresiones", "Impressions")),
        "cost": num(col(raw, "Costo USD", "Costo", "Cost USD", "Importe")),
        "cpm": num(col(raw, "CPM", "CPM USD")),
    })
    df["country"] = df.country.map(lambda c: COUNTRY.get(c, c))
    # Moneda: la del archivo si viene, si no MXN para México y USD para el resto
    try:
        df["currency"] = col(raw, "Moneda", "Currency").astype(str).str.strip().str.upper()
    except SystemExit:
        df["currency"] = np.where(df.country == "MLM", "MXN", "USD")
    df["currency"] = df.currency.where(df.currency.isin(["USD", "MXN"]),
                                       np.where(df.country == "MLM", "MXN", "USD"))
    df["fx_rate"] = np.where(df.currency == "MXN", fx, 1.0)

    bad = df[df.month.isna() | df.impressions_billed.isna() | df.cost.isna()
             | ~df.country.isin(COUNTRY.values()) | ~df.brand.isin(["ML", "MP"])]
    if len(bad):
        print(f"{len(bad)} filas no se pudieron interpretar (país, BU, periodo o importes):")
        print(bad.head(10).to_string(index=False))
        sys.exit("No se reemplaza la tabla: revisa el archivo.")

    # Líneas con un CPM muy distinto al de su factura (posible error de captura o de unidad)
    g = df.groupby(["country", "brand", "month"], dropna=False)
    ref = (g.cost.transform("sum") / g.impressions_billed.transform("sum") * 1000)
    line_cpm = df.cost / df.impressions_billed.replace(0, np.nan) * 1000
    odd = df[(g.impressions_billed.transform("size") > 1) & ref.gt(0)
             & (line_cpm / ref - 1).abs().gt(tol)].assign(cpm_linea=line_cpm, cpm_factura=ref)
    if len(odd):
        print(f"Aviso: {len(odd)} líneas con CPM que se aparta más de {tol:.0%} del de su factura:")
        print(odd[["invoice_id", "country", "brand", "month", "impressions_billed", "cost",
                   "cpm_linea", "cpm_factura"]].head(15).to_string(index=False))

    # Una fila por país × BU × mes: México trae varias líneas por factura
    out = (df.groupby(["country", "brand", "month"], as_index=False)
             .agg(invoice_id=("invoice_id", lambda s: " | ".join(dict.fromkeys(x for x in s if x))),
                  impressions_billed=("impressions_billed", "sum"),
                  cost_original=("cost", "sum"),
                  currency=("currency", "first"),
                  fx_rate=("fx_rate", "first"),
                  invoice_lines=("cost", "size")))
    out["cost_usd"] = out.cost_original / out.fx_rate
    out["cpm_usd"] = out.cost_usd / out.impressions_billed.replace(0, np.nan) * 1000
    out["impressions_billed"] = out.impressions_billed.round().astype("int64")
    out["invoice_lines"] = out.invoice_lines.astype("int64")
    out["source"] = f"sheet Facturas ({os.path.basename(path)})"
    out["loaded_at"] = pd.Timestamp.now("UTC")
    df = out[["country", "brand", "month", "invoice_id", "impressions_billed", "cost_usd", "cpm_usd",
              "currency", "cost_original", "fx_rate", "invoice_lines", "source", "loaded_at"]]

    mxn = df[df.currency == "MXN"]
    print(f"{os.path.basename(path)}: {len(raw)} líneas → {len(df)} facturas (país × BU × mes) · "
          f"{df.country.nunique()} países ({', '.join(sorted(df.country.unique()))}) · "
          f"{df.month.min()} → {df.month.max()} · {df.impressions_billed.sum():,} impresiones · "
          f"${df.cost_usd.sum():,.0f} USD"
          + (f" (incluye {len(mxn)} facturas en MXN a {fx} = ${mxn.cost_usd.sum():,.0f})" if len(mxn) else ""))
    if dry:
        print(df.head(10).to_string(index=False))
        return
    client = bigquery.Client(project=PROJECT)
    schema = client.get_table(TABLE).schema
    client.load_table_from_dataframe(df, TABLE, job_config=bigquery.LoadJobConfig(
        schema=schema, write_disposition="WRITE_TRUNCATE")).result()
    n = next(iter(client.query(f"SELECT COUNT(*) n FROM `{TABLE}`").result())).n
    print(f"bronze_cm_invoices reemplazada: {n} filas. Regenera el dashboard con scripts/build_dashboard.py")


if __name__ == "__main__":
    argv = sys.argv[1:]
    args = [a for a in argv if not a.startswith("--")]
    if not args:
        sys.exit(__doc__)
    opt = lambda name, dflt: float(argv[argv.index(name) + 1]) if name in argv else dflt  # noqa: E731
    main(args[0], "--dry-run" in argv, opt("--fx", FX_MXN), opt("--cpm-tol", 0.5))
