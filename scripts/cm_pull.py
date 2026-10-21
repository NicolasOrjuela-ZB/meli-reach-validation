"""Descarga reportes de CM360 por API (con la cuenta del usuario) y los carga a BigQuery.

Reutiliza los reportes que ya existen en CM (no crea ninguno): solo cambia el rango de fechas,
los ejecuta, espera el archivo, lo descarga como CSV y lo pasa por load_cm.py (misma lógica,
mismo reemplazo idempotente).

  python scripts/cm_pull.py weekly              # semanal con pivot: 4 semanas cerradas + la semana en curso hasta ayer
  python scripts/cm_pull.py weekly --weeks 8    # más semanas hacia atrás
  python scripts/cm_pull.py weekly --no-load    # solo descarga (deja el CSV en downloads/)
  python scripts/cm_pull.py weekly --file <id>  # no vuelve a correr el reporte: descarga un file ya generado

Credencial: ADC del usuario con scopes dfareporting + cloud-platform
  gcloud auth application-default login --scopes=https://www.googleapis.com/auth/dfareporting,https://www.googleapis.com/auth/cloud-platform
En Actions: secret GCP_CREDENTIALS (el mismo JSON de ADC) en GOOGLE_APPLICATION_CREDENTIALS.
"""
import datetime as dt, os, sys, time
import google.auth, urllib.request, urllib.error
from google.auth.transport.requests import AuthorizedSession, Request
from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import load_cm  # noqa: E402

API_VERSION = "v5"
PROFILE_ID = "9503241"                 # Mercado_Libre_group_deal_DCM
REPORT_WEEKLY = "1790317270"           # Pivoted_reach_total_Reg  -> bronze_cm_reach_weekly
REPORT_WINDOW = "1769859345"           # Reach_total_Reg          -> bronze_cm_reach_window (paso siguiente)
SCOPES = ["https://www.googleapis.com/auth/dfareporting", "https://www.googleapis.com/auth/cloud-platform"]
DOWNLOADS = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "downloads")
POLL_SECONDS, MAX_WAIT_MIN = 30, 45


CM_TOKEN = os.environ.get("CM_TOKEN_JSON") or os.path.join(os.path.dirname(DOWNLOADS), "cm_token.json")


def cm_credentials():
    """Token OAuth del cliente "cm_pull" (scripts/cm_auth.py) si existe; si no, el ADC como hasta ahora.
    Solo para la API de CM360: load_cm (BigQuery) sigue usando el ADC."""
    if os.path.exists(CM_TOKEN):
        creds = Credentials.from_authorized_user_file(CM_TOKEN, scopes=["https://www.googleapis.com/auth/dfareporting"])
        if creds.expired and creds.refresh_token:
            creds.refresh(Request())
        print(f"[auth] token OAuth {os.path.basename(CM_TOKEN)}", flush=True)
        return creds
    creds, _ = google.auth.default(scopes=SCOPES)
    print("[auth] ADC", flush=True)
    return creds


def service():
    creds = cm_credentials()
    return build("dfareporting", API_VERSION, credentials=creds, cache_discovery=False), creds


def download_file(creds, report_id, fid, path):
    """Descarga el CSV. CM responde con un redirect a una URL firmada de GCS: se sigue a mano y
    sin credenciales, porque cualquier header de auth o x-goog-* invalida la firma."""
    url = f"https://dfareporting.googleapis.com/dfareporting/{API_VERSION}/reports/{report_id}/files/{fid}?alt=media"
    r = AuthorizedSession(creds).get(url, allow_redirects=False, timeout=120)
    if r.status_code not in (301, 302, 303, 307, 308):
        r.raise_for_status()
        data = r.content
    else:
        loc = r.headers["Location"]
        # urllib no re-codifica la URL (requests/httplib2 sí, y eso altera la firma). Si la firma
        # viene con '+' literal, GCS lo lee como espacio: segundo intento con '+' -> '%2B'.
        data, last = None, None
        for cand in (loc, loc.replace("+", "%2B")):
            try:
                with urllib.request.urlopen(urllib.request.Request(cand, headers={"User-Agent": "cm_pull"}), timeout=600) as resp:
                    data = resp.read()
                break
            except urllib.error.HTTPError as e:
                last = f"{e.code} {e.read()[:300]!r}"
        if data is None:
            sys.exit(f"descarga GCS falló: {last}\nLocation: {loc[:120]}…")
    os.makedirs(os.path.dirname(path), exist_ok=True)
    open(path, "wb").write(data)
    return len(data)


def weekly_range(weeks, today=None):
    """Rango del semanal: desde el domingo de hace (weeks-1) semanas hasta AYER (día cerrado).
    La semana en curso entra parcial; la vista la marca con report_range_end."""
    today = today or dt.date.today()
    end = today - dt.timedelta(days=1)
    sunday = end - dt.timedelta(days=(end.weekday() + 1) % 7)   # weekday: lun=0 … dom=6
    return sunday - dt.timedelta(days=7 * (weeks - 1)), end


def run_report(svc, creds, report_id, start, end, tag, fid=None):
    """Fija el rango, ejecuta el reporte (o reutiliza un file ya generado) y descarga el CSV."""
    rep = svc.reports().get(profileId=PROFILE_ID, reportId=report_id).execute()
    t0 = time.time()
    if fid is None:
        crit = rep.get("reachCriteria") or rep.get("criteria")
        crit["dateRange"] = {"kind": "dfareporting#dateRange", "startDate": start.isoformat(), "endDate": end.isoformat()}
        rep["format"] = "CSV"
        svc.reports().update(profileId=PROFILE_ID, reportId=report_id, body=rep).execute()
        f = svc.reports().run(profileId=PROFILE_ID, reportId=report_id, synchronous=False).execute()
        fid = f["id"]
        print(f"[{tag}] {rep['name']} · {start} → {end} · file {fid} · esperando…", flush=True)
    else:
        f = svc.files().get(reportId=report_id, fileId=fid).execute()
        dr = f.get("dateRange", {})
        start, end = dt.date.fromisoformat(dr.get("startDate", start.isoformat())), dt.date.fromisoformat(dr.get("endDate", end.isoformat()))
        print(f"[{tag}] reutilizo file {fid} · {start} → {end}", flush=True)
    while True:
        f = svc.files().get(reportId=report_id, fileId=fid).execute()
        st = f["status"]
        if st == "REPORT_AVAILABLE":
            break
        if st in ("FAILED", "CANCELLED"):
            sys.exit(f"[{tag}] CM devolvió {st} para el file {fid}")
        if time.time() - t0 > MAX_WAIT_MIN * 60:
            sys.exit(f"[{tag}] el file {fid} sigue en {st} después de {MAX_WAIT_MIN} min")
        time.sleep(POLL_SECONDS)
    path = os.path.join(DOWNLOADS, f"{rep['name']}_{start:%Y%m%d}_{end:%Y%m%d}.csv")
    n = download_file(creds, report_id, fid, path)
    print(f"[{tag}] descargado {os.path.basename(path)} · {n//1024} KB · {int(time.time()-t0)} s", flush=True)
    return path


def cmd_weekly(weeks, do_load, fid=None):
    start, end = weekly_range(weeks)
    svc, creds = service()
    path = run_report(svc, creds, REPORT_WEEKLY, start, end, "weekly", fid)
    if do_load:
        load_cm.load(path)
    else:
        load_cm.load(path, dry=True)


if __name__ == "__main__":
    args = sys.argv[1:]
    if not args or args[0] not in ("weekly",):
        sys.exit(__doc__)
    weeks = int(args[args.index("--weeks") + 1]) if "--weeks" in args else 5
    fid = args[args.index("--file") + 1] if "--file" in args else None
    cmd_weekly(weeks, do_load="--no-load" not in args, fid=fid)
