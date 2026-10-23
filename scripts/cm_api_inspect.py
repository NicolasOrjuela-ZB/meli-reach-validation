"""Inspecciona la configuración de reports de CM360 (dfareporting) con las credenciales por defecto (ADC).

Para cada report imprime los criterios, formato, schedule y delivery; para el primero lista además sus últimos files.
Los reports REACH guardan sus criterios en `reachCriteria`; los STANDARD en `criteria`.
"""
import json
import sys

import google.auth
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError

SCOPES = ["https://www.googleapis.com/auth/dfareporting"]
API_VERSION = "v5"  # v4 fue retirada: su endpoint responde 404
PROFILE_ID = "9503241"
REPORT_IDS = ["1790317270", "1769859345"]
FILES_REPORT_ID = "1790317270"
CRITERIA_FIELDS = ["dateRange", "dimensions", "metricNames", "dimensionFilters",
                   "reachByFrequencyMetricNames", "enableAllDimensionCombinations"]


def dump(obj):
    print(json.dumps(obj, indent=2, ensure_ascii=False))


def main():
    creds, _ = google.auth.default(scopes=SCOPES)
    service = build("dfareporting", API_VERSION, credentials=creds, cache_discovery=False)

    for rid in REPORT_IDS:
        try:
            r = service.reports().get(profileId=PROFILE_ID, reportId=rid).execute()
        except HttpError as e:
            print(f"Error en reports().get({rid}): HTTP {e.status_code} · {e.reason}")
            continue
        key = "criteria" if "criteria" in r else "reachCriteria" if "reachCriteria" in r else None
        crit = r.get(key, {}) if key else {}
        print(f"===== report {rid} · {r.get('name')} · type={r.get('type')} · criterios en '{key}'")
        dump({key or "criteria": {f: crit.get(f) for f in CRITERIA_FIELDS},
              "format": r.get("format"), "schedule": r.get("schedule"), "delivery": r.get("delivery")})

    print(f"===== últimos 5 files del report {FILES_REPORT_ID}")
    try:
        files = service.reports().files().list(profileId=PROFILE_ID, reportId=FILES_REPORT_ID, maxResults=5,
                                               sortField="LAST_MODIFIED_TIME", sortOrder="DESCENDING").execute()
    except HttpError as e:
        sys.exit(f"Error en reports().files().list(): HTTP {e.status_code} · {e.reason}")
    dump([{k: f.get(k) for k in ("id", "status", "dateRange", "fileName")} for f in files.get("items", [])])


if __name__ == "__main__":
    main()
