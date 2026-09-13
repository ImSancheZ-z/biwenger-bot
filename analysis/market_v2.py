"""Recomendaciones explicables. Sin probabilidades de victoria calibradas."""
from dataclasses import dataclass
from datetime import datetime
from zoneinfo import ZoneInfo
from math import ceil
from statistics import mean
from requests import RequestException


@dataclass
class Auction:
    date: int
    player_id: int
    position: int
    value: int
    rival_ratio: float


def historical_value(history, timestamp):
    # El precio del día del cierre puede publicarse DESPUÉS de la subasta.
    # Usamos exclusivamente el día anterior disponible, sin mirar al futuro.
    day = datetime.fromtimestamp(timestamp, ZoneInfo("Europe/Madrid")).date()
    candidates = []
    for stamp, value in history:
        recorded = datetime.strptime(str(stamp), "%y%m%d").date()
        if value and 0 < (day - recorded).days <= 3:
            candidates.append((recorded, value))
    return max(candidates)[1] if candidates else None


def build_auctions(movements, players, my_id, fetch_history):
    samples, skipped, seen, histories = [], 0, set(), {}
    for event in movements:
        if event.get("type") != "market":
            continue
        for item in event.get("content", []) or []:
            pid, stamp = item.get("player"), item.get("date") or event.get("date")
            key = (event.get("id"), pid, stamp)
            if key in seen:
                continue
            seen.add(key)
            player = players.get(pid)
            if not player or not player.slug or not stamp:
                skipped += 1
                continue
            if player.slug not in histories:
                try:
                    histories[player.slug] = fetch_history(player.slug)
                except RequestException:
                    histories[player.slug] = []
            value = historical_value(histories[player.slug], stamp)
            if not value:
                skipped += 1
                continue
            bids = []
            winner = item.get("to") or {}
            if winner.get("id") is None:
                skipped += 1
                continue
            if winner["id"] != my_id and item.get("amount"):
                bids.append(item["amount"])
            unknown = False
            for bid in item.get("bids", []) or []:
                uid = (bid.get("user") or {}).get("id")
                if uid is None:
                    unknown = True
                elif uid != my_id and bid.get("amount"):
                    bids.append(bid["amount"])
            if unknown:
                skipped += 1
                continue
            # Si ganamos solos, la competencia observada es cero.
            samples.append(Auction(stamp, pid, player.position, value,
                                   max(bids, default=0) / value))
    return sorted(samples, key=lambda s: s.date), skipped


def competitive_bid(samples, value, position, timestamp, quantile=0.65):
    past = [s for s in samples if s.date < timestamp]
    comparable = [s for s in past if s.position == position and .5 * value <= s.value <= 2 * value]
    pool = comparable if len(comparable) >= 12 else past
    if not pool:
        return None, 0, "Sin histórico"
    weighted = sorted((s.rival_ratio, 0.5 ** ((timestamp-s.date)/86400/30)) for s in pool)
    target = quantile * sum(w for _, w in weighted)
    cumulative = 0
    ratio = 0
    for ratio, weight in weighted:
        cumulative += weight
        if cumulative >= target:
            break
    effective_n = sum(w for _, w in weighted) ** 2 / sum(w*w for _, w in weighted)
    confidence = "Media" if pool is comparable and effective_n >= 20 else "Baja"
    # Superar la oferta observada sin asumir reglas de desempate.
    return ceil(value * ratio) + 1, len(pool), confidence


def performance(player):
    games = player.played_home + player.played_away
    if games <= 0:
        return None
    season = player.points / games
    recent = player.recent_form
    blended = .7 * season + .3 * (recent if recent is not None else season)
    # Regularización explícita hacia 3 puntos; hipótesis inicial, no entrenada.
    estimate = (games * blended + 5 * 3) / (games + 5)
    difficulty = player.next_fixture.difficulty if player.next_fixture else None
    if difficulty is not None:
        estimate *= 1 - .1 * ((difficulty - 50) / 50)
    return max(0, estimate)


def recommend(player, price, squad, samples, timestamp, budget, max_bid,
              horizon=3, mode="Reforzar plantilla", quantile=.65, free=True):
    result = dict(action="Evitar", amount=None, competitive=None, limit=0,
                  score=None, improvement=None, confidence="Baja", samples=0, reason="")
    if not player or not price or price <= 0 or not player.price:
        result["reason"] = "Faltan datos de precio o jugador."
        return result
    sporting = performance(player)
    peers = [p for p in squad if p.position == player.position and p.status == "ok"]
    peer_scores = [performance(p) for p in peers]
    peer_scores = [s for s in peer_scores if s is not None]
    baseline = min(peer_scores) if peer_scores else 0
    gain = sporting - baseline if sporting is not None else None
    result["improvement"] = round(gain, 2) if gain is not None else None
    competitive, count, confidence = competitive_bid(samples, player.price, player.position, timestamp, quantile)
    result.update(competitive=max(price, competitive) if competitive is not None else None,
                  samples=count, confidence=confidence)
    if not free:
        result.update(competitive=price, confidence="Precio pedido", samples=0)
    if mode == "Reforzar plantilla":
        if player.status != "ok" or sporting is None or gain <= 0:
            result["reason"] = "No mejora al efectivo disponible más débil de su posición, está en duda/no disponible o faltan partidos."
            return result
        # Máximo explícito por utilidad; no se presenta como valor entrenado.
        utility_cap = player.price * (1 + min(.20, gain * .05))
    else:
        change = player.price_increment
        if change is None or change <= 0:
            result["reason"] = "Sin subida diaria positiva para plantear reventa."
            return result
        # Escenario prudente: persiste solo la mitad de la subida, reserva 5%.
        utility_cap = (player.price + .5 * change * horizon) * .95
    limit = max(0, int(min(utility_cap, budget, max_bid)))
    result["limit"] = limit
    candidate = result["competitive"]
    if candidate is None:
        result["reason"] = "Sin subastas con precio histórico recuperable; no inventamos una prima."
        return result
    if sporting is not None:
        result["score"] = round(sporting / (candidate/1_000_000), 2)
    if candidate > limit:
        result["reason"] = "El importe competitivo supera tu límite de utilidad o presupuesto."
        return result
    result.update(action="Pujar" if free else "Valorar oferta", amount=candidate,
                  reason=("Mejora estimada frente al efectivo más débil disponible; precio dentro del límite."
                          if mode == "Reforzar plantilla" else
                          "Cabe en el escenario de reventa con subida al 50% y margen del 5%; beneficio no garantizado."))
    return result


def backtest(samples):
    """Validación temporal de competencia; no simula puntos ni saldo histórico."""
    results = []
    for sample in samples:
        earlier = [s for s in samples if s.date < sample.date]
        if len(earlier) < 12:
            continue
        bid, _, _ = competitive_bid(earlier, sample.value, sample.position, sample.date)
        threshold = sample.value * sample.rival_ratio
        results.append((bid > threshold, max(0, bid-threshold)/sample.value))
    return {"evaluated": len(results),
            "coverage": mean(r[0] for r in results) if results else None,
            "excess": mean(r[1] for r in results if r[0]) if any(r[0] for r in results) else None}


def allocate_budget(recommendations, budget):
    """Aplica un presupuesto conjunto a recomendaciones ya priorizadas."""
    remaining = max(0, budget)
    for rec in recommendations:
        if rec["amount"] is None:
            continue
        if rec["amount"] > remaining:
            rec.update(action="Esperar", amount=None,
                       reason="Presupuesto reservado para propuestas prioritarias de esta tabla.")
        else:
            remaining -= rec["amount"]
    return remaining
