"""Verifica el acceso a la API de CM360 (dfareporting) con las credenciales por defecto (ADC).

Lista los perfiles de usuario de CM360 y, por cada uno, los primeros 20 reports propios.
Requiere ADC con el scope de dfareporting:
  gcloud auth application-default login --scopes=https://www.googleapis.com/auth/dfareporting,https://www.googleapis.com/auth/cloud-platform
"""
import sys

import google.auth
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError

SCOPES = ["https://www.googleapis.com/auth/dfareporting"]
API_VERSION = "v5"  # v4 fue retirada: su endpoint responde 404


def main():
    creds, _ = google.auth.default(scopes=SCOPES)
    service = build("dfareporting", API_VERSION, credentials=creds, cache_discovery=False)
    try:
        profiles = service.userProfiles().list().execute().get("items", [])
    except HttpError as e:
        sys.exit(f"Error en userProfiles().list(): HTTP {e.status_code} · {e.reason}")

    if not profiles:
        print("La cuenta no tiene perfiles de CM360.")
        return

    for p in profiles:
        pid = p["profileId"]
        print(f"profileId={pid}  accountId={p.get('accountId')}  accountName={p.get('accountName')}")
        # v5 acepta máximo 10 por página: se pagina hasta juntar 20
        reports, token = [], None
        try:
            while len(reports) < 20:
                resp = service.reports().list(profileId=pid, scope="MINE", maxResults=10, pageToken=token).execute()
                reports += resp.get("items", [])
                token = resp.get("nextPageToken")
                if not token:
                    break
        except HttpError as e:
            print(f"  Error en reports().list(): HTTP {e.status_code} · {e.reason}")
            continue
        reports = reports[:20]
        if not reports:
            print("  (sin reports propios)")
        for r in reports:
            print(f"  id={r['id']}  type={r.get('type')}  lastModifiedTime={r.get('lastModifiedTime')}  name={r.get('name')}")


if __name__ == "__main__":
    main()
