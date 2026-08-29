"""Analiza el histórico de fichajes de tu liga por usuario: qué compra cada
rival, cuánto paga, en qué posiciones se refuerza y qué patrones muestra.

Se construye a partir del mismo tablón que ya usa `biwenger.parse.parse_movements`
(GET /board?type=transfer,market,adminTransfer,...), pero aquí lo agrupamos
por usuario en vez de listarlo cronológicamente.
"""
from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field
from typing import Any, Optional

from biwenger.models import POSITION_NAMES, Player


@dataclass
class UserTransaction:
    date: int
    role: str  # "compra" | "venta"
    tipo: str  # "Fichaje" | "Subasta competida" | "Cláusula" | "Transferencia admin" | ...
    jugador: str
    position: Optional[int]
    price: int
    contraparte: str  # de quién compró / a quién vendió
    player_id: Optional[int] = None  # para cruzar de forma inequívoca (el nombre podría repetirse)
    market_price: Optional[int] = None  # precio de catálogo ACTUAL del jugador (ver nota abajo)
    vs_market_pct: Optional[float] = None  # (price / market_price - 1) * 100
    overpay_ratio: Optional[float] = None  # solo subastas competidas ganadas por él


@dataclass
class UserActivity:
    user_id: int
    name: str
    transactions: list[UserTransaction] = field(default_factory=list)


def _market_comparison(player: Optional[Player], amount: Optional[int]) -> tuple[Optional[int], Optional[float]]:
    """Compara lo pagado con el precio de catálogo ACTUAL del jugador.

    Limitación real, no un descuido: es el precio de HOY, no el que tenía el
    día exacto de la operación (Biwenger no expone histórico de precio de
    fechas anteriores a que empezamos a guardar snapshots nosotros mismos
    con scripts/fetch_daily.py). Para fichajes recientes es una comparación
    razonable; para fichajes de hace semanas, tómalo como aproximado.
    """
    if not player or not player.price or not amount:
        return None, None
    pct = (amount / player.price - 1) * 100
    return player.price, pct


def build_user_activity(
    moves: list[dict[str, Any]], players_by_id: dict[int, Player]
) -> dict[int, UserActivity]:
    """Recorre el tablón completo y agrupa cada compra/venta por usuario."""
    users: dict[int, UserActivity] = {}

    def get_user(uid: int, name: str) -> UserActivity:
        if uid not in users:
            users[uid] = UserActivity(user_id=uid, name=name)
        return users[uid]

    for move in moves:
        mtype = move.get("type")
        date = move.get("date")
        if mtype not in ("transfer", "market", "adminTransfer"):
            continue

        for item in move.get("content", []) or []:
            pid = item.get("player")
            player = players_by_id.get(pid)
            pname = player.name if player else (f"#{pid}" if pid else "?")
            position = player.position if player else None
            amount = item.get("amount")
            from_user = item.get("from")
            to_user = item.get("to")
            market_price, vs_market_pct = _market_comparison(player, amount)

            if mtype == "market":
                bids = item.get("bids") or []
                tipo = "Subasta competida" if bids else "Subasta (sin rival)"
                if to_user and amount:
                    overpay = None
                    if bids:
                        top_losing = max((b.get("amount") or 0) for b in bids)
                        if top_losing:
                            overpay = amount / top_losing
                    buyer = get_user(to_user["id"], to_user["name"])
                    buyer.transactions.append(
                        UserTransaction(
                            date=date, role="compra", tipo=tipo, jugador=pname, position=position,
                            price=amount, contraparte="Libre (sistema)", player_id=pid,
                            market_price=market_price, vs_market_pct=vs_market_pct, overpay_ratio=overpay,
                        )
                    )
                continue

            if mtype == "adminTransfer":
                if to_user and amount:
                    buyer = get_user(to_user["id"], to_user["name"])
                    admin = item.get("admin") or {}
                    buyer.transactions.append(
                        UserTransaction(
                            date=date, role="compra", tipo="Transferencia admin", jugador=pname,
                            position=position, price=amount, contraparte=f"Admin ({admin.get('name', '?')})",
                            player_id=pid, market_price=market_price, vs_market_pct=vs_market_pct,
                        )
                    )
                continue

            # transfer
            tipo = "Cláusula" if item.get("type") == "clause" else ("Fichaje" if to_user else "Venta al mercado")
            if to_user and amount:
                buyer = get_user(to_user["id"], to_user["name"])
                seller_name = from_user["name"] if from_user else "Mercado (sistema)"
                buyer.transactions.append(
                    UserTransaction(
                        date=date, role="compra", tipo=tipo, jugador=pname, position=position,
                        price=amount, contraparte=seller_name, player_id=pid,
                        market_price=market_price, vs_market_pct=vs_market_pct,
                    )
                )
            if from_user and amount:
                seller = get_user(from_user["id"], from_user["name"])
                buyer_name = to_user["name"] if to_user else "Mercado (sistema)"
                seller.transactions.append(
                    UserTransaction(
                        date=date, role="venta", tipo=tipo, jugador=pname, position=position,
                        price=amount, contraparte=buyer_name, player_id=pid,
                    )
                )

    for user in users.values():
        user.transactions.sort(key=lambda t: t.date, reverse=True)
    return users


def summarize_user(transactions: list[UserTransaction]) -> dict[str, Any]:
    compras = [t for t in transactions if t.role == "compra"]
    ventas = [t for t in transactions if t.role == "venta"]

    total_gastado = sum(t.price for t in compras)
    total_ingresado = sum(t.price for t in ventas)

    pos_counter = Counter(t.position for t in compras if t.position is not None)
    posicion_favorita = pos_counter.most_common(1)[0] if pos_counter else None

    overpays = [t.overpay_ratio for t in compras if t.overpay_ratio]
    vs_market = [t.vs_market_pct for t in compras if t.vs_market_pct is not None]

    return {
        "n_compras": len(compras),
        "n_ventas": len(ventas),
        "total_gastado": total_gastado,
        "total_ingresado": total_ingresado,
        "balance_neto": total_ingresado - total_gastado,
        "precio_medio_compra": (total_gastado / len(compras)) if compras else None,
        "posicion_favorita": posicion_favorita,  # (position_id, count) | None
        "overpay_medio": (sum(overpays) / len(overpays)) if overpays else None,
        "n_subastas_competidas_ganadas": len(overpays),
        "vs_market_medio_pct": (sum(vs_market) / len(vs_market)) if vs_market else None,
        "n_con_precio_mercado": len(vs_market),
    }


def detect_tendencies(stats: dict[str, Any]) -> list[str]:
    """Frases en lenguaje natural describiendo el patrón de comportamiento
    de un usuario, a partir de summarize_user(). Deliberadamente simple: son
    reglas heurísticas sobre pocos datos, no un modelo estadístico serio —
    con más jornadas jugadas, estas frases deberían afinarse solas."""
    if stats["n_compras"] == 0 and stats["n_ventas"] == 0:
        return ["Sin actividad de mercado registrada todavía esta temporada."]

    insights: list[str] = []

    if stats["n_compras"] >= 3 and stats["n_compras"] > stats["n_ventas"] * 2:
        insights.append(
            f"Comprador neto muy activo: {stats['n_compras']} fichajes frente a solo {stats['n_ventas']} ventas."
        )
    elif stats["n_ventas"] >= 3 and stats["n_ventas"] > stats["n_compras"] * 2:
        insights.append(
            f"Vendedor neto: ha soltado {stats['n_ventas']} jugadores frente a {stats['n_compras']} fichajes."
        )

    if stats["balance_neto"] < -1_000_000:
        insights.append(f"Ha invertido {abs(stats['balance_neto']):,.0f} € netos en refuerzos esta temporada.")
    elif stats["balance_neto"] > 1_000_000:
        insights.append(f"Ha generado {stats['balance_neto']:,.0f} € netos vendiendo más de lo que ha comprado.")

    if stats["posicion_favorita"]:
        pos_id, count = stats["posicion_favorita"]
        pos_name = POSITION_NAMES.get(pos_id, "?")
        if count >= 2:
            insights.append(
                f"Se refuerza sobre todo en {pos_name.lower()} ({count} de sus {stats['n_compras']} fichajes)."
            )

    if stats["overpay_medio"]:
        pct = (stats["overpay_medio"] - 1) * 100
        if pct >= 15:
            insights.append(
                f"Puja muy agresivo en subastas: paga de media un {pct:.0f}% más que la segunda mejor oferta "
                f"({stats['n_subastas_competidas_ganadas']} subastas competidas ganadas)."
            )
        elif pct >= 5:
            insights.append(f"Puja algo por encima de la media en subastas (+{pct:.0f}% sobre la segunda mejor oferta).")
        else:
            insights.append(f"Puja ajustado en subastas: solo un {pct:.0f}% por encima de la segunda mejor oferta.")

    if stats["vs_market_medio_pct"] is not None:
        pct = stats["vs_market_medio_pct"]
        n = stats["n_con_precio_mercado"]
        if pct >= 15:
            insights.append(
                f"Paga muy por encima del precio de mercado actual de sus fichajes: +{pct:.0f}% de media "
                f"(sobre {n} fichajes con precio de catálogo disponible)."
            )
        elif pct >= 5:
            insights.append(f"Paga algo por encima del precio de mercado de sus fichajes (+{pct:.0f}% de media).")
        elif pct <= -10:
            insights.append(f"Fichajes claramente por debajo de precio de mercado (-{abs(pct):.0f}% de media): buen ojo para los chollos.")
        else:
            insights.append(f"Paga precios cercanos al valor de mercado de sus fichajes ({pct:+.0f}% de media).")

    if stats["precio_medio_compra"]:
        insights.append(f"Precio medio de fichaje: {stats['precio_medio_compra']:,.0f} €.")

    return insights or ["Actividad registrada, pero sin un patrón claro todavía."]
