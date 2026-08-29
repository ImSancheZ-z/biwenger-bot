"""Verifica que el login y GET /account funcionan contra la API real de Biwenger.

Uso:
    python scripts/verify_login.py

Requiere un .env con BIWENGER_EMAIL / BIWENGER_PASSWORD (ver .env.example).
No imprime el token completo ni la contraseña.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from biwenger.client import BiwengerAuthError, BiwengerClient
from biwenger.config import load_settings


def main() -> int:
    settings = load_settings()
    client = BiwengerClient(settings.email, settings.password, league_id=settings.league_id)

    print(f"[1/2] Login como {settings.email} ...")
    try:
        token = client.login()
    except BiwengerAuthError as exc:
        print(f"  FALLO: {exc}")
        return 1
    print(f"  OK. Token recibido (primeros 12 chars): {token[:12]}...")

    print("[2/2] GET /account ...")
    account = client.get_account()
    print("  OK. Claves de nivel superior en la respuesta:", list(account.keys()))

    data = account.get("data", account)
    leagues = data.get("leagues") if isinstance(data, dict) else None
    if leagues:
        print(f"  Ligas encontradas: {len(leagues)}")
        for lg in leagues:
            print(f"    - id={lg.get('id')} name={lg.get('name')}")
    else:
        print("  No se encontró 'leagues' directamente en /account. Estructura completa (recortada):")
        print(str(data)[:1500])

    print("\nTodo correcto. Login y /account funcionan.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
