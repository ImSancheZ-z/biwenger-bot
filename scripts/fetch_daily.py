"""Descarga el catálogo público de jugadores de LaLiga y guarda un snapshot
del día en SQLite. Pensado para ejecutarse una vez al día (cron, Task
Scheduler, GitHub Actions...).

Uso:
    python scripts/fetch_daily.py
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from biwenger.client import BiwengerClient
from biwenger.parse import parse_players
from biwenger.storage import Storage


def main() -> int:
    # El catálogo de jugadores es público: no hace falta login para esto.
    client = BiwengerClient(email="", password="")
    raw = client.get_competition_data()
    players = parse_players(raw["data"])

    with Storage() as storage:
        n = storage.save_snapshot(players)
        print(f"Snapshot guardado: {n} jugadores ({storage.db_path})")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
