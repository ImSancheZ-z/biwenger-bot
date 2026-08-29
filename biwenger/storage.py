"""Persistencia en SQLite: snapshots diarios de jugadores."""
from __future__ import annotations

import sqlite3
from datetime import date
from pathlib import Path
from typing import Iterable

from biwenger.models import Player

DEFAULT_DB_PATH = Path(__file__).resolve().parent.parent / "biwenger_data.sqlite3"

SCHEMA = """
CREATE TABLE IF NOT EXISTS player_snapshots (
    snapshot_date TEXT NOT NULL,
    player_id INTEGER NOT NULL,
    name TEXT NOT NULL,
    team_id INTEGER,
    team_name TEXT,
    position INTEGER,
    price INTEGER,
    fantasy_price INTEGER,
    status TEXT,
    status_info TEXT,
    points INTEGER,
    points_home INTEGER,
    points_away INTEGER,
    fitness TEXT,
    PRIMARY KEY (snapshot_date, player_id)
);
CREATE INDEX IF NOT EXISTS idx_player_snapshots_player ON player_snapshots(player_id);
"""


class Storage:
    def __init__(self, db_path: Path | str = DEFAULT_DB_PATH):
        self.db_path = Path(db_path)
        self._conn = sqlite3.connect(self.db_path)
        self._conn.executescript(SCHEMA)
        self._conn.commit()

    def close(self) -> None:
        self._conn.close()

    def __enter__(self) -> "Storage":
        return self

    def __exit__(self, *exc) -> None:
        self.close()

    def save_snapshot(self, players: Iterable[Player], snapshot_date: date | None = None) -> int:
        snap_date = (snapshot_date or date.today()).isoformat()
        rows = [
            (
                snap_date,
                p.id,
                p.name,
                p.team_id,
                p.team_name,
                p.position,
                p.price,
                p.fantasy_price,
                p.status,
                p.status_info,
                p.points,
                p.points_home,
                p.points_away,
                ",".join(str(f) for f in p.fitness),
            )
            for p in players
        ]
        with self._conn:
            self._conn.executemany(
                """
                INSERT OR REPLACE INTO player_snapshots (
                    snapshot_date, player_id, name, team_id, team_name, position,
                    price, fantasy_price, status, status_info,
                    points, points_home, points_away, fitness
                ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                """,
                rows,
            )
        return len(rows)

    def latest_snapshot_date(self) -> str | None:
        cur = self._conn.execute("SELECT MAX(snapshot_date) FROM player_snapshots")
        row = cur.fetchone()
        return row[0] if row else None

    def price_history(self, player_id: int) -> list[tuple[str, int]]:
        cur = self._conn.execute(
            """
            SELECT snapshot_date, price FROM player_snapshots
            WHERE player_id = ? ORDER BY snapshot_date ASC
            """,
            (player_id,),
        )
        return cur.fetchall()

    def price_trend(self, player_id: int, days: int = 7) -> int | None:
        """Diferencia de precio entre el snapshot más antiguo y el más reciente
        dentro de los últimos `days` snapshots guardados para ese jugador."""
        history = self.price_history(player_id)
        if len(history) < 2:
            return None
        recent = history[-days:]
        return recent[-1][1] - recent[0][1]
