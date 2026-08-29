"""Reconstruye cuánto dinero debería tener cada usuario de la liga, a partir
de dos fuentes de dinero reales:

1. Lo que cobra cada jornada jugada — GET /board?type=roundFinished trae,
   para cada usuario, "bonus" (el dinero que ingresa esa jornada).
2. Lo que gasta/ingresa fichando y vendiendo — ya lo teníamos en
   analysis.scouting a partir de GET /board?type=transfer,market,...

reconstruct_balances() parte de un SALDO INICIAL conocido (el del principio
del periodo, no el de hoy) y va sumando/restando cada evento hacia
adelante. Ese saldo inicial normalmente viene de
`analysis.initial_budget.compute_initial_budget()` (la regla de los 40M de
presupuesto menos el valor del reparto inicial de plantilla, con precio
histórico exacto del día del reparto — ver ese módulo para el detalle y la
verificación empírica).

Si no se puede calcular el reparto inicial de un usuario (p.ej. ya no está
en la liga y su plantilla ya no es consultable), usa cumulative_flow() en
su lugar: un flujo de caja acumulado empezando en 0, útil para comparar
quién gana/gasta más que otros, pero que NO es un saldo real.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Optional


@dataclass
class MoneyEvent:
    date: int
    kind: str  # "bonus" | "compra" | "venta"
    label: str
    delta: int  # positivo = ingreso, negativo = gasto


def round_bonuses_by_user(board_items: list[dict[str, Any]]) -> dict[int, list[MoneyEvent]]:
    """A partir de GET /board?type=...,roundFinished,..., extrae el bonus
    de cada jornada por usuario."""
    events: dict[int, list[MoneyEvent]] = {}
    for item in board_items:
        if item.get("type") != "roundFinished":
            continue
        content = item.get("content") or {}
        round_name = (content.get("round") or {}).get("name", "Jornada")
        date = item.get("date")
        for result in content.get("results", []) or []:
            user = result.get("user") or {}
            uid = user.get("id")
            bonus = result.get("bonus")
            if uid is None or not bonus:
                continue
            events.setdefault(uid, []).append(
                MoneyEvent(date=date, kind="bonus", label=f"Bono de {round_name}", delta=bonus)
            )
    return events


def build_money_timeline(
    user_id: int,
    round_bonus_events: list[MoneyEvent],
    transactions: list,  # list[scouting.UserTransaction] del mismo usuario
) -> list[MoneyEvent]:
    """Combina bonus de jornada + compras/ventas en una única línea temporal
    ordenada por fecha, con el signo correcto en "delta"."""
    events = list(round_bonus_events)
    for t in transactions:
        if t.role == "compra":
            events.append(MoneyEvent(date=t.date, kind="compra", label=f"Fichaje: {t.jugador}", delta=-t.price))
        else:
            events.append(MoneyEvent(date=t.date, kind="venta", label=f"Venta: {t.jugador}", delta=t.price))
    events.sort(key=lambda e: e.date)
    return events


def net_cash_flow(events: list[MoneyEvent]) -> int:
    return sum(e.delta for e in events)


def cumulative_flow(events: list[MoneyEvent]) -> list[tuple[MoneyEvent, int]]:
    """Saldo acumulado empezando en 0 (no un saldo real): útil cuando no hay
    ningún valor conocido con el que anclar la reconstrucción, p.ej. un
    usuario cuyo reparto inicial no se pudo calcular."""
    running = 0
    out = []
    for e in events:
        running += e.delta
        out.append((e, running))
    return out


def reconstruct_balances(
    events: list[MoneyEvent], known_initial_balance: Optional[int] = None
) -> tuple[list[tuple[MoneyEvent, Optional[int]]], Optional[int]]:
    """Si known_initial_balance está disponible (el saldo real ANTES del
    primer evento — por reconciliación con tu saldo de hoy, o por el
    reparto inicial de 40M calculado en analysis.initial_budget), reproduce
    el saldo estimado después de cada evento.

    Si no lo está, devuelve el saldo como None en cada punto — usa
    cumulative_flow() en su lugar si quieres un flujo acumulado desde 0.
    """
    if known_initial_balance is None:
        return [(e, None) for e in events], None

    running = known_initial_balance
    out = []
    for e in events:
        running += e.delta
        out.append((e, running))
    return out, known_initial_balance
