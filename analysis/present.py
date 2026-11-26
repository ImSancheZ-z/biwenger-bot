"""Presentación de datos que el panel ya tiene: cuentas atrás, KPIs y
listas visuales. No consulta la API.
"""
from __future__ import annotations

from datetime import datetime
from typing import Any, Optional
from zoneinfo import ZoneInfo

MADRID = ZoneInfo("Europe/Madrid")
WEEKDAYS = ["lunes", "martes", "miércoles", "jueves", "viernes", "sábado", "domingo"]
MONTHS = [
    "enero", "febrero", "marzo", "abril", "mayo", "junio",
    "julio", "agosto", "septiembre", "octubre", "noviembre", "diciembre",
]


def spanish_datetime_label(moment: Optional[datetime] = None) -> str:
    moment = moment or datetime.now(MADRID)
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=MADRID)
    else:
        moment = moment.astimezone(MADRID)
    return (
        f"{WEEKDAYS[moment.weekday()].capitalize()} {moment.day} de "
        f"{MONTHS[moment.month - 1]} de {moment.year}, {moment:%H:%M}"
    )


def format_millions(value) -> str:
    """19100000 -> '19.1M'. Importes grandes, sin decimal."""
    if value is None:
        return "—"
    millions = value / 1_000_000
    if abs(millions) >= 100:
        return f"{millions:.0f}M"
    return f"{millions:.1f}M"


def unlock_datetime_label(timestamp) -> str:
    if not timestamp:
        return "Sin fecha"
    moment = datetime.fromtimestamp(int(timestamp), MADRID)
    return moment.strftime("%d/%m %H:%M")


def remaining_label(seconds: Optional[float]) -> str:
    """Tiempo que falta para que una cláusula se pueda ejecutar.

    Por debajo de 48 h se expresa en horas, como una cuenta atrás corta.
    A partir de ahí, en días y horas.
    """
    if seconds is None or seconds <= 0:
        return "Ya"
    if seconds < 3600:
        return f"{max(1, int(seconds // 60))} min"
    hours = max(1, int(round(seconds / 3600)))
    if hours < 48:
        return f"{hours} h"
    days, rest = divmod(hours, 24)
    if rest == 0:
        return f"{days} d"
    return f"{days} d {rest} h"


def clause_countdown(locked_until, now_ts: int) -> dict[str, Any]:
    if not locked_until:
        return {"open": True, "seconds_left": None, "remaining": "Ya", "unlock": "Sin fecha"}
    seconds = int(locked_until) - int(now_ts)
    return {
        "open": seconds <= 0,
        "seconds_left": seconds,
        "remaining": remaining_label(seconds),
        "unlock": unlock_datetime_label(locked_until),
    }


def availability_label(locked_until, now_ts: int) -> str:
    info = clause_countdown(locked_until, now_ts)
    if info["open"]:
        return "Ya"
    return f"{info['unlock']} · {info['remaining']}"


def catalog_kpis(players) -> list[tuple[str, str, str]]:
    total = sum(p.price or 0 for p in players)
    rising = sum(1 for p in players if (p.price_increment or 0) > 0)
    falling = sum(1 for p in players if (p.price_increment or 0) < 0)
    flat = len(players) - rising - falling
    return [
        ("Valor total mercado", format_millions(total), f"{len(players)} jugadores indexados"),
        ("Suben hoy", str(rising), "variación diaria oficial"),
        ("Bajan hoy", str(falling), "variación diaria oficial"),
        ("Sin cambio hoy", str(flat), "precio igual que ayer"),
    ]


def top_daily_risers(players, limit: int = 12):
    ranked = [p for p in players if p.price_increment is not None and p.price_increment > 0]
    ranked.sort(key=lambda p: (p.price_increment, p.points or 0), reverse=True)
    return ranked[:limit]


def roster_player_ids(rosters: dict) -> set[int]:
    found: set[int] = set()
    for roster in rosters.values():
        for entry in roster.get("players") or []:
            pid = entry.get("id")
            if pid is not None:
                found.add(pid)
    return found


def squad_kpis(rosters: dict, players, open_count: int, upcoming_count: int) -> list[tuple[str, str, str]]:
    catalog_ids = {p.id for p in players}
    owned = roster_player_ids(rosters)
    owned_known = owned & catalog_ids
    free = len(catalog_ids - owned)
    return [
        ("Cláusulas abiertas", str(open_count), "ejecutables ahora"),
        ("Próximas 48 h", str(upcoming_count), "a punto de poder ejecutarse"),
        ("Con dueño", str(len(owned_known)), f"{free} libres en el catálogo"),
        ("Usuarios", str(len(rosters)), "plantillas consultadas"),
    ]


def split_clause_window(opportunities):
    open_now = [o for o in opportunities if o.days_until_unlockable <= 0]
    upcoming = [o for o in opportunities if o.days_until_unlockable > 0]
    open_now.sort(key=lambda o: (o.player.price or 0, o.clause), reverse=True)
    upcoming.sort(key=lambda o: (o.clause_locked_until or 0, o.player.name))
    return open_now, upcoming


def within_hours(opportunity, max_hours: float) -> bool:
    return 0 < opportunity.days_until_unlockable <= (max_hours / 24)


def recent_clause_moves(rows: list[dict], now_ts: int, within_days: float = 7) -> list[dict]:
    cutoff = now_ts - within_days * 86400
    picked = [
        row for row in rows
        if row.get("tipo") == "Cláusula" and (row.get("date") or 0) >= cutoff
    ]
    picked.sort(key=lambda row: row.get("date") or 0, reverse=True)
    return picked
