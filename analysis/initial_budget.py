"""Reconstruye el reparto inicial de plantilla de cada usuario y, con eso,
su saldo inicial real de temporada — usando el precio HISTÓRICO exacto del
día del reparto, no el precio de catálogo de hoy.

Regla de esta liga (aportada por el usuario, verificada empíricamente
contra su propio saldo real conocido): cada usuario empieza la temporada
con un presupuesto de 40.000.000 €, del que se resta el valor (precio de
aquel día) de los jugadores que se le asignan en el reparto inicial.

Verificación hecha (2026-08-28, cuenta real del usuario): usando el precio
de HOY para valorar la plantilla inicial, el saldo inicial predicho fallaba
por 14,23M€ frente al saldo real conocido — porque faltaban jugadores
vendidos sin haber sido comprados (también forman parte del reparto) y
porque los precios llevan ~3 semanas moviéndose. Sumando esos jugadores
vendidos, el fallo bajó a 1,98M€. Usando además el precio EXACTO del día
del reparto (histórico diario de `GET /players/la-liga/{slug}?fields=prices`,
366 días hacia atrás) el fallo bajó a 0,6M€ — y ese resto se explica por un
único jugador (#37714) que ya no aparece en el catálogo de LaLiga (fuera de
competición), no por un fallo del método.

Cómo se detecta qué jugadores fueron parte del reparto inicial de un
usuario (no hay un endpoint que lo diga directamente):
- Jugadores de su plantilla ACTUAL que no tienen "owner.price" en la
  respuesta de GET /user/{id} → nunca han sido comprados/vendidos por él,
  siguen desde el reparto.
- Jugadores que aparecen como VENTA suya en el tablón pero nunca como
  COMPRA suya → los tenía desde el reparto y los vendió después.

La fecha del reparto se detecta automáticamente: es la fecha compartida por
TODOS los jugadores "sin owner.price" de la plantilla actual (se comprobó
que es exactamente la misma para todos, dentro de una misma plantilla).
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
    "owner.price" (si todos son distintos, no hay reparto detectable)."""
    dates = [
        entry["owner"]["date"]
        for entry in roster_players
        if "price" not in (entry.get("owner") or {}) and "date" in (entry.get("owner") or {})
    ]
    if not dates:
        return None
    return Counter(dates).most_common(1)[0][0]


def find_initial_squad_ids(
    roster_players: list[dict[str, Any]], user_transactions: list
) -> set[int]:
    """Combina "sigue desde el reparto" (plantilla actual sin owner.price)
    con "lo tenía desde el reparto y lo vendió" (venta sin compra previa
    registrada), usando player_id para evitar ambigüedades de nombre."""
    current_no_price_ids = {
        entry["id"] for entry in roster_players if "price" not in (entry.get("owner") or {})
    }
    bought_ids = {t.player_id for t in user_transactions if t.role == "compra" and t.player_id}
    sold_ids = {t.player_id for t in user_transactions if t.role == "venta" and t.player_id}
    sold_without_bought_ids = sold_ids - bought_ids

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
        if not player:
            missing += 1
            continue
        price_on_date = None
        if date_code:
            history = price_history_fetcher(player.slug)
            match = next((p for p in history if p[0] == date_code), None)
            price_on_date = match[1] if match else None
        if price_on_date is None:
            missing += 1
        else:
            total += price_on_date
        squad.append(InitialSquadPlayer(player_id=pid, name=player.name, price_on_start_date=price_on_date))

    return InitialBudgetResult(
        season_start_date=start_date,
        squad=squad,
        total_squad_value=total,
        missing_price_count=missing,
        initial_balance=INITIAL_BUDGET - total,
    )
