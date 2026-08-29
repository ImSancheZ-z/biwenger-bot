"""Detecta jugadores con cláusula ya pagable, o que lo estará en los
próximos días, entre TODOS los usuarios de tu liga (no solo tú) — y los
puntúa para saber cuáles merecen la pena.

De dónde sale el dato: cada jugador de cualquier plantilla
(GET /user/{id}, vía BiwengerClient.get_my_team() / get_other_user_roster())
trae bajo "owner": {clause, clauseLockedUntil, ...}. `clauseLockedUntil` es
un timestamp: mientras no se supere, NADIE puede pagar la cláusula de ese
jugador. En cuanto se supera, cualquier usuario de la liga puede pagarla y
robárselo a su dueño actual — la mecánica "clause: steal" que ya vimos en
la configuración de tu liga (`GET /account` -> leagues[].settings.clause).

La puntuación reutiliza las mismas piezas que el resto del proyecto para
que el criterio sea consistente en toda la app:
- `analysis.engine.score_at_price`: ratio puntos/precio + forma + dificultad
  del próximo rival, aplicado al precio de la CLÁUSULA (lo que pagarías).
- `analysis.bidding.price_trend_pct_per_day` + el mismo corte duro
  (TREND_HARD_STOP): una cláusula barata de un jugador cuyo precio se
  desploma no es una ganga, es un jugador que pierde valor.
- Comparación directa cláusula vs. precio de catálogo de hoy
  (`vs_market_pct`): si la cláusula es menor que el precio de mercado
  actual, es un descuento inmediato y objetivo.
"""
from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Any, Optional

from analysis.bidding import (
    SCORE_THRESHOLD_GOOD,
    SCORE_THRESHOLD_MIN,
    TREND_HARD_STOP,
    price_trend_abs_per_day,
    price_trend_pct_per_day,
)
from analysis.engine import score_at_price
from biwenger.models import Player

SECONDS_PER_DAY = 86400


@dataclass
class ClauseOpportunity:
    player: Player
    owner_name: str
    owner_id: int
    clause: int
    clause_locked_until: Optional[int]  # timestamp; None = sin fecha de bloqueo conocida
    days_until_unlockable: float  # <= 0 significa que ya se puede pagar ahora mismo
    score: Optional[float] = None
    vs_market_pct: Optional[float] = None  # cláusula vs. precio de catálogo actual
    trend_pct_per_day: Optional[float] = None
    trend_abs_per_day: Optional[int] = None
    recomendacion: str = ""


def find_clause_opportunities(
    rosters: dict[int, dict[str, Any]],  # {user_id: user_data de GET /user/{id}}
    players_by_id: dict[int, Player],
    my_user_id: int,
    within_days: float = 3.0,
    now_ts: Optional[int] = None,
) -> list[ClauseOpportunity]:
    """Recorre las plantillas de todos los rivales (excluye la tuya: no
    tiene sentido "robarte" tu propio jugador) y devuelve los jugadores cuya
    cláusula ya está desbloqueada o lo estará dentro de `within_days` días.
    """
    now_ts = now_ts if now_ts is not None else int(time.time())
    cutoff = now_ts + within_days * SECONDS_PER_DAY

    opportunities: list[ClauseOpportunity] = []
    for uid, roster in rosters.items():
        if uid == my_user_id:
            continue
        owner_name = roster.get("name", "?")
        for entry in roster.get("players", []):
            owner = entry.get("owner") or {}
            clause = owner.get("clause")
            if not clause:
                continue
            locked_until = owner.get("clauseLockedUntil")
            if locked_until is not None and locked_until > cutoff:
                continue  # se desbloquea más tarde de lo que interesa

            player = players_by_id.get(entry.get("id"))
            if not player:
                continue

            days_until = (locked_until - now_ts) / SECONDS_PER_DAY if locked_until else -999.0
            opportunities.append(
                ClauseOpportunity(
                    player=player,
                    owner_name=owner_name,
                    owner_id=uid,
                    clause=clause,
                    clause_locked_until=locked_until,
                    days_until_unlockable=days_until,
                )
            )
    return opportunities


def score_opportunities(opportunities: list[ClauseOpportunity], price_history_fetcher) -> None:
    """Rellena score / vs_market_pct / trend_pct_per_day / recomendacion,
    modificando la lista in-place. `price_history_fetcher` se inyecta (p.ej.
    una versión cacheada de client.get_player_price_history) para no atar
    este módulo a una instancia concreta del cliente HTTP."""
    for opp in opportunities:
        score, _, _, _ = score_at_price(opp.player, opp.clause)
        opp.score = round(score, 2)

        if opp.player.price:
            opp.vs_market_pct = round((opp.clause / opp.player.price - 1) * 100, 1)

        if opp.player.slug:
            history = price_history_fetcher(opp.player.slug)
            opp.trend_pct_per_day = price_trend_pct_per_day(history)
            opp.trend_abs_per_day = price_trend_abs_per_day(history)

        opp.recomendacion = _classify(opp)


def _classify(opp: ClauseOpportunity) -> str:
    if opp.trend_pct_per_day is not None and opp.trend_pct_per_day <= TREND_HARD_STOP:
        return "Evitar (precio cayendo con fuerza)"
    if opp.score is None:
        return "Sin datos suficientes"
    if opp.score >= SCORE_THRESHOLD_GOOD:
        return "Muy interesante"
    if opp.score >= SCORE_THRESHOLD_MIN:
        return "Interesante"
    return "Descartable (ratio bajo)"
