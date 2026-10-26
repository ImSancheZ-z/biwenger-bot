"""Avisa por Telegram de los jugadores de TU plantilla cuya cláusula pasa a
ser pagable (clauseLockedUntil) en los próximos días.

Uso:
    python scripts/clause_alerts.py [--days 4] [--dry-run]

Variables de entorno: BIWENGER_EMAIL, BIWENGER_PASSWORD, BIWENGER_LEAGUE_ID
(opcional), TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID.
Si no hay ningún jugador en riesgo no se envía nada.
"""
from __future__ import annotations

import argparse
import math
import os
import sys
import time
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import requests

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from biwenger.client import BiwengerClient
from biwenger.config import load_settings
from biwenger.parse import parse_my_team, parse_players

SECONDS_PER_DAY = 86400
MADRID = ZoneInfo("Europe/Madrid")


def expiring_players(rows: list[dict], now_ts: int, within_days: float) -> list[dict]:
    """Filas con la cláusula aún bloqueada que se desbloquea en <= within_days,
    ordenadas por urgencia. Añade `days_left` (días naturales, redondeado hacia arriba)."""
    cutoff = now_ts + within_days * SECONDS_PER_DAY
    out = []
    for row in rows:
        until = row.get("clause_locked_until")
        if not until or not (now_ts < until <= cutoff):
            continue
        out.append({**row, "days_left": max(1, math.ceil((until - now_ts) / SECONDS_PER_DAY))})
    return sorted(out, key=lambda r: r["clause_locked_until"])


def format_message(expiring: list[dict]) -> str:
    lines = ["⚠️ Cláusulas que se desbloquean pronto en tu plantilla:", ""]
    for r in expiring:
        when = datetime.fromtimestamp(r["clause_locked_until"], MADRID).strftime("%d/%m %H:%M")
        days = "1 día" if r["days_left"] == 1 else f"{r['days_left']} días"
        clause = f" · cláusula {r['clause']:,}€".replace(",", ".") if r.get("clause") else ""
        lines.append(f"• {r['name']} ({r.get('team_name') or '?'}): {days} (hasta {when}){clause}")
    return "\n".join(lines)


def send_telegram(text: str) -> None:
    token = os.environ["TELEGRAM_BOT_TOKEN"]
    chat_id = os.environ["TELEGRAM_CHAT_ID"]
    resp = requests.post(
        f"https://api.telegram.org/bot{token}/sendMessage",
        json={"chat_id": chat_id, "text": text},
        timeout=20,
    )
    resp.raise_for_status()


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--days", type=float, default=4.0)
    parser.add_argument("--dry-run", action="store_true", help="imprime el mensaje en vez de enviarlo")
    args = parser.parse_args()

    settings = load_settings()
    client = BiwengerClient(settings.email, settings.password, league_id=settings.league_id)
    players = parse_players(client.get_competition_data()["data"])
    players_by_id = {p.id: p for p in players}
    rows = parse_my_team(client.get_my_team()["data"], players_by_id)

    expiring = expiring_players(rows, int(time.time()), args.days)
    if not expiring:
        print(f"Sin cláusulas que se desbloqueen en los próximos {args.days:g} días.")
        return 0

    message = format_message(expiring)
    if args.dry_run:
        print(message)
    else:
        send_telegram(message)
        print(f"Aviso enviado ({len(expiring)} jugadores).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
