"""Auditoría de lectura: python scripts/audit_market_v2.py. Solo imprime agregados."""
import sys
from pathlib import Path
from statistics import mean
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from analysis.market_v2 import build_auctions, backtest
from analysis.bidding import historical_bid_premium, effective_premium
from biwenger.client import BiwengerClient
from biwenger.config import load_settings
from biwenger.parse import parse_players


def main():
    settings = load_settings()
    client = BiwengerClient(settings.email, settings.password, settings.league_id)
    players = {p.id: p for p in parse_players(client.get_competition_data()["data"])}
    movements = client.get_all_league_movements(page_size=100)
    samples, skipped = build_auctions(movements, players, client.league_user_id, client.get_player_price_history)
    print("Usable:", len(samples), "Skipped:", skipped, flush=True)
    print("V2 balanced:", backtest(samples), flush=True)
    old = []
    for sample in samples:
        if sum(s.date < sample.date for s in samples) < 12:
            continue
        past = [m for m in movements if m.get("date", 0) < sample.date]
        premium, n = historical_bid_premium(past)
        multiplier, _ = effective_premium(premium, n)
        bid = sample.value * min(multiplier, 1.12)
        threshold = sample.rival_ratio * sample.value
        old.append((bid > threshold, max(0, bid-threshold)/sample.value))
    print("V1 base premium only (no historical squad/budget/start price available):",
          {"evaluated": len(old), "coverage": mean(x[0] for x in old) if old else None,
           "excess": mean(x[1] for x in old if x[0]) if any(x[0] for x in old) else None})


if __name__ == "__main__":
    main()
