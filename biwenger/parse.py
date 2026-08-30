"""Convierte el JSON crudo del catálogo público de Biwenger en objetos Player.

Basado en la estructura REAL observada en:
GET https://cf.biwenger.com/api/v2/competitions/la-liga/data?lang=es&score=5
(inspeccionada en agosto 2026 — ver notas en README).

Puntos clave de la estructura real:
- data.teams: dict {team_id: {id, name, slug, nextGames: [...]}} -> nombres de equipo.
- data.activeEvents: lista de jornadas (normalmente 1, la próxima pendiente).
  Cada jornada tiene "games", y cada partido trae home/away con id, name y
  difficulty.rating (0-100). De ahí sacamos el próximo rival de cada equipo.
- data.players: dict {id: {...}}. El campo "fitness" puede mezclar enteros
  con "discarded"/null cuando el jugador no pudo puntuar esa jornada.
"""
from __future__ import annotations

from typing import Any

from biwenger.models import Fixture, Player


def build_next_fixture_map(active_events: list[dict[str, Any]]) -> dict[int, Fixture]:
    """Devuelve {team_id: Fixture} usando el partido más próximo de cada equipo."""
    fixtures: dict[int, Fixture] = {}
    for event in active_events:
        round_id = event.get("id")
        for game in event.get("games", []):
            home = game.get("home") or {}
            away = game.get("away") or {}
            game_date = game.get("date")
            pairs = [(home, away, True), (away, home, False)]
            for team, rival, is_home in pairs:
                team_id = team.get("id")
                if team_id is None:
                    continue
                existing = fixtures.get(team_id)
                if existing is not None:
                    continue  # nos quedamos con el primero (activeEvents ya viene en orden)
                difficulty = (rival.get("difficulty") or {}).get("rating")
                fixtures[team_id] = Fixture(
                    round_id=round_id,
                    team_id=team_id,
                    rival_team_id=rival.get("id"),
                    rival_name=rival.get("name", "?"),
                    is_home=is_home,
                    difficulty=difficulty,
                )
                _ = game_date  # disponible si en el futuro queremos ordenar por fecha
    return fixtures


def parse_players(competition_data: dict[str, Any]) -> list[Player]:
    """competition_data es el objeto bajo la clave "data" de la respuesta."""
    teams = competition_data.get("teams", {})
    fixtures = build_next_fixture_map(competition_data.get("activeEvents", []))

    players: list[Player] = []
    for raw in competition_data.get("players", {}).values():
        team_id = raw.get("teamID")
        team_info = teams.get(str(team_id)) or teams.get(team_id) or {}
        players.append(
            Player(
                id=raw["id"],
                name=raw.get("name", "?"),
                slug=raw.get("slug", ""),
                team_id=team_id,
                team_name=team_info.get("name"),
                position=raw.get("position", 0),
                price=raw.get("price"),
                fantasy_price=raw.get("fantasyPrice"),
                status=raw.get("status", "ok"),
                status_info=raw.get("statusInfo"),
                fitness=list(raw.get("fitness") or []),
                points=raw.get("points", 0) or 0,
                points_home=raw.get("pointsHome", 0) or 0,
                points_away=raw.get("pointsAway", 0) or 0,
                points_last_season=raw.get("pointsLastSeason", 0) or 0,
                next_fixture=fixtures.get(team_id),
            )
        )
    return players


def parse_market(market_data: dict[str, Any], players_by_id: dict[int, Player]) -> list[dict[str, Any]]:
    """A partir de la respuesta de BiwengerClient.get_market() (clave "data")
    y un índice {player_id: Player}, construye filas legibles del mercado
    activo: jugadores libres del sistema y puestos en venta por otros
    usuarios de la liga, con su ratio puntos/precio de venta y el score del
    motor de recomendación (analysis.engine) aplicado al precio de venta
    real en vez del precio de catálogo.
    """
    from analysis.engine import score_at_price  # import perezoso: evita import circular

    rows = []
    for sale in market_data.get("sales", []):
        player_raw = sale.get("player", {})
        pid = player_raw.get("id")
        player = players_by_id.get(pid)
        seller = sale.get("user")
        price = sale.get("price")

        score = ppm = None
        if player:
            score, ppm, _, _ = score_at_price(player, price)

        rows.append(
            {
                "id": pid,
                "slug": player.slug if player else None,
                "name": player.name if player else f"#{pid}",
                "team_name": player.team_name if player else None,
                "position": player.position if player else None,
                "position_name": player.position_name if player else None,
                "points": player.points if player else None,
                "price_venta": price,
                "ratio_pts_millon": round(ppm, 2) if ppm else None,
                "score": round(score, 2) if score is not None else None,
                "is_free_agent": seller is None,
                "vendedor": seller["name"] if seller else "Libre (sistema)",
                "hasta": sale.get("until"),
            }
        )
    return rows


def parse_standings(league_data: dict[str, Any]) -> list[dict[str, Any]]:
    """A partir de la respuesta de BiwengerClient.get_league_standings()
    (clave "data"), construye filas legibles de la clasificación con el
    detalle enriquecido que trae "include=all" (valor de plantilla, su
    variación reciente y el historial de posiciones)."""
    rows = []
    for entry in league_data.get("standings", []):
        rows.append(
            {
                "user_id": entry.get("id"),
                "position": entry.get("position"),
                "position_change": entry.get("positionInc"),
                "name": entry.get("name"),
                "points": entry.get("points"),
                "team_size": entry.get("teamSize"),
                "team_value": entry.get("teamValue"),
                "team_value_change": entry.get("teamValueInc"),
                "last_positions": entry.get("lastPositions"),
            }
        )
    return rows


def parse_my_team(user_data: dict[str, Any], players_by_id: dict[int, Player]) -> list[dict[str, Any]]:
    """A partir de la respuesta de BiwengerClient.get_my_team() (clave "data")
    y un índice {player_id: Player} del catálogo, construye filas legibles
    con nombre/equipo/puntos/precio de cada jugador de la plantilla.
    """
    lineup_ids = set((user_data.get("lineup") or {}).get("playersID") or [])
    on_sale_ids = {m["playerID"] for m in (user_data.get("market") or [])}

    rows = []
    for entry in user_data.get("players", []):
        pid = entry.get("id")
        player = players_by_id.get(pid)
        owner = entry.get("owner", {})
        rows.append(
            {
                "id": pid,
                "name": player.name if player else f"#{pid}",
                "team_name": player.team_name if player else None,
                "position": player.position if player else None,
                "position_name": player.position_name if player else None,
                "points": player.points if player else None,
                "price": player.price if player else None,
                "clause": owner.get("clause"),
                "clause_locked_until": owner.get("clauseLockedUntil"),
                "en_alineacion": pid in lineup_ids,
                "en_venta": pid in on_sale_ids,
            }
        )
    return rows


def parse_movements(moves: list[dict[str, Any]], players_by_id: dict[int, Player]) -> list[dict[str, Any]]:
    """A partir de BiwengerClient.get_league_movements()/get_all_league_movements(),
    construye filas legibles del histórico real de fichajes de tu liga.

    Notas sobre la estructura real (fácil de malinterpretar):
    - Un "transfer" sin "to" no es un dato que falte: es una venta AL
      MERCADO libre (el sistema te paga "amount", no hay comprador humano).
    - Un "transfer" con `content[].type == "clause"` es un pago de cláusula
      de rescate a otro usuario, no una venta pactada entre ambos.
    - Un "market" es una subasta de jugador libre. Si trae "bids", fue una
      subasta competida y ahí van TODAS las pujas perdedoras (no solo la
      ganadora) — de ahí sale el histórico real de cuánto pujan tus rivales.
    - "adminTransfer" es un movimiento forzado manualmente por un
      administrador de la liga (trae "admin" y "reason" en vez de "from").
    - Se ignoran a propósito: "roundFinished" (resumen de puntos de la
      jornada) y "clauseIncrement" (la cláusula de un jugador sube con el
      tiempo automáticamente; no es una operación de compra/venta).
    """
    rows = []
    for move in moves:
        mtype = move.get("type")
        date = move.get("date")
        if mtype not in ("transfer", "market", "adminTransfer"):
            continue

        for item in move.get("content", []) or []:
            pid = item.get("player")
            player = players_by_id.get(pid)
            amount = item.get("amount")
            from_user = item.get("from")
            to_user = item.get("to")

            if mtype == "adminTransfer":
                admin = item.get("admin") or {}
                label = "Transferencia admin"
                origen = f"Admin ({admin.get('name', '?')})"
                destino = to_user["name"] if to_user else "?"
                losing_bids = None
            elif mtype == "transfer":
                if item.get("type") == "clause":
                    label = "Cláusula"
                elif to_user is None:
                    label = "Venta al mercado"
                else:
                    label = "Fichaje"
                origen = from_user["name"] if from_user else "Mercado (sistema)"
                destino = to_user["name"] if to_user else "Mercado (sistema)"
                losing_bids = None
            else:  # market
                bids = item.get("bids") or []
                label = "Subasta competida" if bids else "Subasta (sin puja rival)"
                origen = "Libre (sistema)"
                destino = to_user["name"] if to_user else "?"
                losing_bids = [b.get("amount") for b in bids] if bids else None

            rows.append(
                {
                    "date": date,
                    "tipo": label,
                    "jugador": player.name if player else (f"#{pid}" if pid else "?"),
                    "importe": amount,
                    "de": origen,
                    "a": destino,
                    "pujas_perdedoras": losing_bids,
                }
            )

    rows.sort(key=lambda r: r["date"] or 0, reverse=True)
    return rows


def squad_position_counts(my_team_rows: list[dict[str, Any]]) -> dict[int, int]:
    """Cuenta cuántos jugadores tienes por posición, a partir de las filas
    de parse_my_team(). Se usa para no recomendar pagar de más por una
    posición en la que ya vas sobrado."""
    counts: dict[int, int] = {}
    for row in my_team_rows:
        pos = row.get("position")
        if pos is not None:
            counts[pos] = counts.get(pos, 0) + 1
    return counts
