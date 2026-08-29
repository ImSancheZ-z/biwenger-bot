"""Estimador v1 de cuánto pujar/pagar por un jugador del mercado activo.

Qué SÍ podemos usar (datos reales de tu cuenta):
- Tu saldo y el "maximumBid" que impone Biwenger (GET /market -> status).
- El score de chollo del jugador al precio de venta actual (analysis.engine).
- Si el jugador es libre del sistema (sujeto a subasta a ciegas con el resto
  de la liga) o lo vende otro usuario (compra directa a precio fijo, sin
  puja: gana quien pulsa antes).
- El histórico real de subastas competidas de TU liga
  (GET /league/{id}/board?type=userMovements), que trae todas las pujas
  (no solo la ganadora) -> de ahí sacamos cuánto suelen sobrepujar tus
  rivales en la práctica.
- Cuántos jugadores tienes ya en esa posición, para no recomendar pagar de
  más por algo que no necesitas.

Qué NO podemos usar, y por qué:
- El saldo actual de tus rivales: tu liga tiene la configuración
  `settings.balance = "hidden"` (lo fijó el administrador de la liga), así
  que ni la app oficial de Biwenger se lo muestra a nadie. No hay endpoint
  que lo revele.
- Un histórico grande de subastas: si tu liga lleva pocas jornadas, puede
  que solo haya 0-2 subastas competidas registradas. El estimador lo dice
  explícitamente en vez de fingir precisión que no tiene.
"""
from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from typing import Any, Optional

from biwenger.models import Player

# Si no hay histórico suficiente, asumimos que el ganador de una subasta a
# ciegas paga de media un 8% más que la segunda mejor puja (supuesto
# genérico, no específico de tu liga).
DEFAULT_PREMIUM = 1.08
MIN_SAMPLES_FOR_TRUST = 3

# Umbrales sobre el "score" de analysis.engine (mismo score que en el resto
# del dashboard) para decidir si merece la pena pujar.
SCORE_THRESHOLD_GOOD = 8.0
SCORE_THRESHOLD_MIN = 3.0

# Nº de jugadores "ideal" por posición en una plantilla de ~15 huecos.
IDEAL_SQUAD_SIZE = {1: 2, 2: 5, 3: 5, 4: 3}

# Umbrales sobre la tendencia de precio (% medio de variación diaria de los
# últimos días, ver price_trend_pct_per_day). Un jugador que pierde valor
# cada día es una mala inversión aunque su ratio puntos/precio de HOY sea
# bueno: si lo fichas, tu patrimonio (valor de plantilla) baja con él.
TREND_HARD_STOP = -1.5  # %/día: directamente no se recomienda pujar
TREND_SOFT_PENALTY = -0.5
TREND_BONUS = 1.0

# Tope sobre la prima total (historial × necesidad × tendencia). Un +20%
# debe ser excepcional (equipo top-5 con titular habitual), no algo que
# salga solo con combinar un par de bonus menores.
DEFAULT_PREMIUM_CAP = 1.12
EXCEPTIONAL_PREMIUM_CAP = 1.20

# Nunca recomendar poner más de este % de tu saldo disponible en un único
# jugador, para no quedarte sin margen el resto de la jornada.
MAX_BALANCE_SHARE_PER_BID = 0.40

# Nº de jugadores "top scorers" de un equipo que se consideran titulares
# habituales, a falta de un dato real de "once probable" (ver
# compute_likely_starters).
STARTERS_PER_TEAM = 11

# Nº de equipos considerados "top" por valor de plantilla.
TOP_TEAMS_COUNT = 5


def compute_top_teams_by_value(players: list[Player], top_n: int = TOP_TEAMS_COUNT) -> set[str]:
    """Nombres de los `top_n` equipos con más valor total de plantilla (suma
    de precio de todos sus jugadores del catálogo). Se usa como proxy de
    "equipo top" — más estable que los puntos de las primeras jornadas
    (comprobado: por puntos salían equipos irregulares como Sevilla o
    Alavés en el top, puro ruido de una temporada que acaba de empezar; por
    valor de plantilla salen los grandes habituales: Barcelona, Real
    Madrid, Atlético, Villarreal, Betis)."""
    team_value: dict[str, int] = defaultdict(int)
    for p in players:
        if p.team_name and p.price:
            team_value[p.team_name] += p.price
    ranked = sorted(team_value.items(), key=lambda kv: -kv[1])
    return {name for name, _ in ranked[:top_n]}


def compute_likely_starters(players: list[Player], starters_per_team: int = STARTERS_PER_TEAM) -> set[int]:
    """IDs de jugadores entre los N con más puntos de su equipo esta
    temporada — aproximación a "titular habitual". No es el once real (no
    tenemos esa información de rivales), pero un jugador que no es de los
    que más puntúa en su equipo probablemente tampoco es un fijo."""
    by_team: dict[str, list[Player]] = defaultdict(list)
    for p in players:
        if p.team_name:
            by_team[p.team_name].append(p)
    starters: set[int] = set()
    for team_players in by_team.values():
        ranked = sorted(team_players, key=lambda p: p.points, reverse=True)
        starters.update(p.id for p in ranked[:starters_per_team])
    return starters


def historical_bid_premium(movements: list[dict[str, Any]]) -> tuple[Optional[float], int]:
    """A partir del histórico de /board (type=userMovements incluye eventos
    "market"), calcula cuánto paga de más el ganador de una subasta libre
    respecto a la segunda mejor puja. Devuelve (media, nº de subastas
    competidas encontradas); media es None si no hay ninguna.
    """
    ratios = []
    for move in movements:
        if move.get("type") != "market":
            continue
        for item in move.get("content", []) or []:
            bids = item.get("bids") or []
            amount = item.get("amount")
            if not bids or not amount:
                continue
            top_losing_bid = max((b.get("amount") or 0) for b in bids)
            if top_losing_bid:
                ratios.append(amount / top_losing_bid)
    if not ratios:
        return None, 0
    return sum(ratios) / len(ratios), len(ratios)


def effective_premium(observed_premium: Optional[float], n: int) -> tuple[float, str]:
    """Mezcla el histórico real de tu liga con el supuesto genérico,
    dándole más peso al histórico cuantas más subastas competidas haya."""
    if n >= MIN_SAMPLES_FOR_TRUST:
        return observed_premium, f"basado en {n} subastas competidas reales de tu liga"
    if n > 0:
        blended = (observed_premium * n + DEFAULT_PREMIUM * (MIN_SAMPLES_FOR_TRUST - n)) / MIN_SAMPLES_FOR_TRUST
        return blended, (
            f"solo {n} subasta(s) competida(s) registrada(s) en tu liga todavía "
            f"— mezclado con un supuesto genérico del {(DEFAULT_PREMIUM - 1) * 100:.0f}%"
        )
    return DEFAULT_PREMIUM, (
        f"sin subastas competidas registradas todavía en tu liga "
        f"— usando un supuesto genérico del {(DEFAULT_PREMIUM - 1) * 100:.0f}%"
    )


def price_trend_pct_per_day(price_history: list[list[int]], days: int = 7) -> Optional[float]:
    """% medio de variación de precio por día en los últimos `days` días.

    `price_history` es la lista [fecha_AAMMDD, precio] tal como la devuelve
    BiwengerClient.get_player_price_history() (orden cronológico, ~366
    días). Positivo = subiendo, negativo = bajando. None si no hay
    suficiente histórico.
    """
    if not price_history or len(price_history) < 2:
        return None
    recent = price_history[-days:] if len(price_history) > days else price_history
    if len(recent) < 2:
        return None
    start_price, end_price = recent[0][1], recent[-1][1]
    n_days = len(recent) - 1
    if not start_price or not n_days:
        return None
    return (end_price - start_price) / start_price * 100 / n_days


def price_trend_abs_per_day(price_history: list[list[int]], days: int = 7) -> Optional[int]:
    """Variación media de precio en EUROS por día en los últimos `days`
    días (p.ej. +200.000 o -19.000). Misma ventana y misma fuente que
    price_trend_pct_per_day, en valor absoluto en vez de porcentaje —
    útil para ver de un vistazo cuánto dinero real supone la tendencia,
    no solo el porcentaje."""
    if not price_history or len(price_history) < 2:
        return None
    recent = price_history[-days:] if len(price_history) > days else price_history
    if len(recent) < 2:
        return None
    start_price, end_price = recent[0][1], recent[-1][1]
    n_days = len(recent) - 1
    if not n_days:
        return None
    return round((end_price - start_price) / n_days)


def _trend_adjustment(trend_pct_per_day: Optional[float]) -> tuple[float, str]:
    """Multiplicador sobre la prima/puja según la tendencia de precio, y una
    nota explicativa. No decide un "evitar" duro aquí — eso se hace en
    recommend_bid con TREND_HARD_STOP, porque ahí sí hay que cortar la
    recomendación entera, no solo ajustar la cantidad."""
    if trend_pct_per_day is None:
        return 1.0, "sin histórico de precio suficiente para valorar la tendencia"
    if trend_pct_per_day <= TREND_SOFT_PENALTY:
        return 0.95, f"precio bajando ({trend_pct_per_day:+.2f}%/día): puja algo más conservadora"
    if trend_pct_per_day >= TREND_BONUS:
        return 1.08, f"precio subiendo con fuerza ({trend_pct_per_day:+.2f}%/día): mañana costará más, merece algo más de puja"
    return 1.0, f"precio estable ({trend_pct_per_day:+.2f}%/día)"


@dataclass
class BidRecommendation:
    action: str  # "pujar" | "comprar_ya" | "evitar"
    amount: Optional[int]
    reasoning: str


def recommend_bid(
    *,
    price: int,
    is_free_agent: bool,
    score: Optional[float],
    balance: int,
    max_bid: int,
    premium: float,
    premium_note: str,
    position: Optional[int],
    my_squad_position_counts: dict[int, int],
    trend_pct_per_day: Optional[float] = None,
    is_exceptional: bool = False,
) -> BidRecommendation:
    """`is_exceptional`: True si el jugador es de un equipo top-5 (por valor
    de plantilla, ver compute_top_teams_by_value) Y es titular habitual
    (ver compute_likely_starters). Solo entonces se permite superar el tope
    de prima por defecto del +12% hasta un +20%."""
    balance_cap = round(balance * MAX_BALANCE_SHARE_PER_BID)
    hard_cap = min(balance, max_bid, balance_cap)

    if score is None:
        return BidRecommendation("evitar", None, "Sin puntos/datos suficientes para valorar al jugador.")

    if trend_pct_per_day is not None and trend_pct_per_day <= TREND_HARD_STOP:
        return BidRecommendation(
            "evitar", None,
            f"Su precio lleva cayendo con fuerza ({trend_pct_per_day:+.2f}%/día de media en la última "
            "semana). Por buen ratio que tenga hoy, no compensa: mañana valdrá menos y tu inversión "
            "pierde valor con él.",
        )

    need_bonus = 1.0
    need_note = ""
    if position is not None:
        have = my_squad_position_counts.get(position, 0)
        ideal = IDEAL_SQUAD_SIZE.get(position, 3)
        if have < ideal:
            need_bonus = 1.10
            need_note = f" Te faltan efectivos en esa posición ({have}/{ideal} en tu plantilla), así que merece un pequeño extra."

    trend_mult, trend_note = _trend_adjustment(trend_pct_per_day)

    if not is_free_agent:
        # Venta directa a precio fijo entre usuarios: no hay subasta, gana quien compra antes.
        # La tendencia no cambia CUÁNTO pagar (el precio es fijo), solo si merece la pena.
        if score < SCORE_THRESHOLD_MIN:
            return BidRecommendation(
                "evitar", None, f"Venta directa a precio fijo, pero el ratio no compensa (score {score:.1f})."
            )
        if price > hard_cap:
            return BidRecommendation(
                "evitar", None,
                f"Buen jugador (score {score:.1f}) pero su precio ({price:,}€) supera el máximo que "
                f"recomiendo poner en un único jugador ({hard_cap:,}€ = el {MAX_BALANCE_SHARE_PER_BID*100:.0f}% "
                "de tu saldo, o tu saldo/máximo permitido si son menores).",
            )
        return BidRecommendation(
            "comprar_ya", price,
            f"Venta directa a precio fijo, buen ratio (score {score:.1f}).{need_note} "
            f"Tendencia de precio: {trend_note}. Cómpralo ya al precio pedido: no hay subasta, "
            "solo gana quien compra primero.",
        )

    # Jugador libre del sistema: subasta a ciegas contra el resto de la liga.
    if score < SCORE_THRESHOLD_MIN:
        return BidRecommendation(
            "evitar", None, f"Ratio puntos/precio bajo (score {score:.1f}); no merece competir por él en subasta."
        )

    raw_premium = premium * need_bonus * trend_mult
    premium_cap = EXCEPTIONAL_PREMIUM_CAP if is_exceptional else DEFAULT_PREMIUM_CAP
    effective_premium_value = min(raw_premium, premium_cap)
    cap_note = ""
    if raw_premium > premium_cap + 1e-9:
        tope_tipo = "excepcional (equipo top-5, titular habitual)" if is_exceptional else "por defecto"
        cap_note = (
            f" La fórmula pedía ×{raw_premium:.2f}, pero se recorta al tope {tope_tipo} de "
            f"+{(premium_cap - 1) * 100:.0f}%: un +20-30% debe ser la excepción, no la norma."
        )
    elif is_exceptional:
        cap_note = " (equipo top-5 con titular habitual: se permite hasta +20% si hiciera falta)."

    suggested = round(price * effective_premium_value)
    suggested = min(suggested, hard_cap)

    if suggested < price:
        return BidRecommendation(
            "evitar", None,
            f"Ni siquiera el precio de salida ({price:,}€) cabe dentro del máximo que recomiendo poner "
            f"en un único jugador ({hard_cap:,}€ = el {MAX_BALANCE_SHARE_PER_BID*100:.0f}% de tu saldo, "
            "o tu saldo/máximo permitido si son menores).",
        )

    return BidRecommendation(
        "pujar", suggested,
        f"Subasta libre ({premium_note}).{need_note} Tendencia de precio: {trend_note}.{cap_note} "
        f"Puja sugerida = precio de salida × prima efectiva ({effective_premium_value:.2f}), "
        f"topada al {MAX_BALANCE_SHARE_PER_BID*100:.0f}% de tu saldo / tu saldo / el máximo permitido "
        f"(el menor de los tres: {hard_cap:,}€).",
    )
