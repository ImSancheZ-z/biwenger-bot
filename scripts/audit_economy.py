"""Comprueba los saldos de toda la liga con consultas de solo lectura."""
import json
import sys
from functools import lru_cache
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from analysis.economy import build_money_timeline, clause_costs_by_user, net_cash_flow, round_bonuses_by_user
from analysis.initial_budget import compute_initial_budget, find_season_start_date
from analysis.scouting import UserActivity, build_user_activity
from biwenger.client import BiwengerClient
from biwenger.config import load_settings
from biwenger.parse import parse_players


def main():
    settings = load_settings()
    client = BiwengerClient(settings.email, settings.password, settings.league_id)
    client.login()
    players = {p.id: p for p in parse_players(client.get_competition_data()["data"])}
    moves = client.get_all_league_movements()
    users = build_user_activity(moves, players)
    for entry in client.get_league_standings()["data"].get("standings", []):
        users.setdefault(entry["id"], UserActivity(entry["id"], entry["name"]))
    bonus = round_bonuses_by_user(client.get_round_results())
    costs = clause_costs_by_user(moves, players)
    rosters = {}
    for uid, user in users.items():
        try:
            response = client.get_my_team() if uid == client.league_user_id else client.get_other_user_roster(uid)
            rosters[uid] = response["data"]["players"]
        except Exception as exc:
            print(json.dumps({"name": user.name, "error": str(exc)}, ensure_ascii=True), flush=True)
    dates = [find_season_start_date(roster) for roster in rosters.values()]
    start = min((date for date in dates if date is not None), default=None)
    history = lru_cache(maxsize=None)(client.get_player_price_history)
    actual = client.get_market()["data"]["status"].get("balance")
    for uid, user in users.items():
        if uid not in rosters or start is None:
            continue
        budget = compute_initial_budget(rosters[uid], user.transactions, players, history, start)
        timeline = build_money_timeline(uid, bonus.get(uid, []), user.transactions, costs.get(uid, []))
        estimated = budget.initial_balance + net_cash_flow(timeline)
        print(json.dumps({"name": user.name, "estimated": estimated,
                          "real_api": actual if uid == client.league_user_id else None,
                          "missing_prices": budget.missing_price_count,
                          "initial_players": len(budget.squad),
                          "clause_costs": -sum(e.delta for e in costs.get(uid, []))}, ensure_ascii=True), flush=True)


if __name__ == "__main__":
    main()
