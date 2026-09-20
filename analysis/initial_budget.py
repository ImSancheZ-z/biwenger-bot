"""Estima el saldo inicial: 40M menos los precios históricos del reparto.

La plantilla inicial incluye jugadores actuales sin owner.price o con precio cero y jugadores
cuya primera operación registrada es una venta, aunque luego se recompren.
La fecha se busca entre las plantillas disponibles de la liga. Los precios
no recuperables no se inventan: se cuentan como ausentes y hacen que el
saldo resultante sea provisional y esté sobrevalorado por su importe.
"""
from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Optional

from biwenger.models import Player

INITIAL_BUDGET = 40_000_000


@dataclass
class InitialSquadPlayer:
    player_id: int
    name: str
    price_on_start_date: Optional[int]  # None si no se pudo recuperar


@dataclass
class InitialBudgetResult:
    season_start_date: Optional[int]  # timestamp unix
    squad: list[InitialSquadPlayer]
    total_squad_value: int  # solo suma los jugadores con precio recuperado
    missing_price_count: int  # jugadores del reparto sin precio recuperable
    initial_balance: int  # INITIAL_BUDGET - total_squad_value


def timestamp_to_date_code(ts: int) -> int:
    """1786016057 -> 260806 (formato AAMMDD que usa el histórico de precios)."""
    return int(datetime.fromtimestamp(ts, tz=timezone.utc).strftime("%y%m%d"))


def find_season_start_date(roster_players: list[dict[str, Any]]) -> Optional[int]:
    """roster_players = user_data["players"] de GET /user/{id} (o
    get_my_team/get_other_user_roster). Devuelve el timestamp unix del
    reparto inicial: la fecha más común entre los jugadores sin
    "owner.price" o con precio cero (si todos son distintos, no hay reparto detectable)."""
    dates = [
        entry["owner"]["date"]
        for entry in roster_players
        if (entry.get("owner") or {}).get("price") in (None, 0) and "date" in (entry.get("owner") or {})
    ]
    if not dates:
        return None
    return Counter(dates).most_common(1)[0][0]


def find_initial_squad_ids(
    roster_players: list[dict[str, Any]], user_transactions: list
) -> set[int]:
    """Combina "sigue desde el reparto" (plantilla actual sin owner.price o con precio cero)
    con "lo tenía desde el reparto y lo vendió" (venta anterior a la primera compra
    registrada), usando player_id para evitar ambigüedades de nombre."""
    current_no_price_ids = {
        entry["id"] for entry in roster_players if (entry.get("owner") or {}).get("price") in (None, 0)
    }
    first_roles = {}
    for transaction in sorted(user_transactions, key=lambda t: t.date):
        if transaction.player_id:
            first_roles.setdefault(transaction.player_id, transaction.role)
    sold_without_bought_ids = {pid for pid, role in first_roles.items() if role == "venta"}

    return current_no_price_ids | sold_without_bought_ids


def compute_initial_budget(
    roster_players: list[dict[str, Any]],
    user_transactions: list,
    players_by_id: dict[int, Player],
    price_history_fetcher,  # Callable[[str], list[list[int]]] — normalmente client.get_player_price_history
    season_start_date: Optional[int] = None,
) -> InitialBudgetResult:
    """Reconstruye la plantilla inicial de un usuario y su saldo inicial de
    temporada, usando el precio histórico exacto del día del reparto.

    `price_history_fetcher` se inyecta (en vez de importar el cliente aquí)
    para poder cachear/mockear las llamadas de red desde quien lo use.

    `season_start_date` (opcional): la fecha del reparto es la MISMA para
    toda la liga, así que lo normal es calcularla una vez (con
    find_season_start_date sobre CUALQUIER plantilla que aún conserve
    jugadores del reparto) y pasarla aquí para todos los usuarios. Si se
    omite, se intenta detectar a partir de la propia plantilla de este
    usuario — pero eso falla si ya ha vendido TODOS sus jugadores
    originales (no queda ninguno "sin owner.price" del que sacar la fecha),
    que es justo el caso que motivó este parámetro.
    """
    start_date = season_start_date or find_season_start_date(roster_players)
    squad_ids = find_initial_squad_ids(roster_players, user_transactions)
    date_code = timestamp_to_date_code(start_date) if start_date else None

    squad: list[InitialSquadPlayer] = []
    total = 0
    missing = 0
    for pid in squad_ids:
        player = players_by_id.get(pid)
        price_on_date = None
        if date_code:
            # El detalle público acepta también el ID, incluso cuando el
            # jugador ya no aparece en el catálogo de la competición.
            history = price_history_fetcher(player.slug if player else str(pid))
            match = next((p for p in history if p[0] == date_code), None)
            price_on_date = match[1] if match else None
        if price_on_date is None:
            missing += 1
        else:
            total += price_on_date
        squad.append(InitialSquadPlayer(player_id=pid, name=player.name if player else f"#{pid}", price_on_start_date=price_on_date))

    return InitialBudgetResult(
        season_start_date=start_date,
        squad=squad,
        total_squad_value=total,
        missing_price_count=missing,
        initial_balance=INITIAL_BUDGET - total,
    )
