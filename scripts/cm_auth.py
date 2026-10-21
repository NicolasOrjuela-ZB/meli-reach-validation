"""Autoriza el cliente OAuth de escritorio "cm_pull" contra CM360 y guarda el token en cm_token.json.

Abre el navegador una vez; el token (con refresh_token) queda en la raíz del repo y cm_pull.py lo
usa para la API de CM360. Ni client_secret.json ni cm_token.json se suben al repo (.gitignore).

  python scripts/cm_auth.py
"""
import os

from google_auth_oauthlib.flow import InstalledAppFlow

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CLIENT_SECRET = os.path.join(ROOT, "client_secret.json")
TOKEN = os.path.join(ROOT, "cm_token.json")
SCOPES = ["https://www.googleapis.com/auth/dfareporting"]


def main():
    flow = InstalledAppFlow.from_client_secrets_file(CLIENT_SECRET, scopes=SCOPES)
    creds = flow.run_local_server(host="localhost", port=0)
    with open(TOKEN, "w") as f:
        f.write(creds.to_json())
    os.chmod(TOKEN, 0o600)
    print(f"token guardado en {TOKEN} · refresh_token: {'sí' if creds.refresh_token else 'NO'}")


if __name__ == "__main__":
    main()
