import unittest
from datetime import datetime, timezone
from analysis.market_v2 import build_auctions, competitive_bid, historical_value, recommend, performance, allocate_budget
from biwenger.parse import parse_players


STAMP = int(datetime(2026, 9, 13, 10, tzinfo=timezone.utc).timestamp())


def player(**fields):
    return parse_players({"players": {"1": {"id": 1, "slug": "example", "position": 3,
        "price": 1000000, "points": 24, "playedHome": 2, "playedAway": 2,
        "fitness": [6, 6, 6, 6], "priceIncrement": 20000, **fields}}})[0]


class MarketV2Tests(unittest.TestCase):
    def test_joint_budget_does_not_exceed_available_cash(self):
        recs = [{"amount": 700}, {"amount": 600}, {"amount": 200}]
        self.assertEqual(allocate_budget(recs, 1000), 100)
        self.assertEqual([r["amount"] for r in recs], [700, None, 200])
    def samples(self, winner=2, amount=1100000, bids=None):
        return build_auctions([{"id": 1, "type": "market", "date": STAMP,
            "content": [{"player": 1, "to": {"id": winner}, "amount": amount,
                         "bids": bids or []}]}], {1: player()}, 7,
            lambda slug: [[260912, 1000000], [260913, 1200000]])[0]

    def test_excludes_own_bid_and_same_day_price(self):
        samples = self.samples(winner=7, amount=1500000,
                               bids=[{"user": {"id": 2}, "amount": 1400000}])
        self.assertEqual(samples[0].rival_ratio, 1.4)
        self.assertEqual(competitive_bid(samples, 1000000, 3, STAMP+1)[0], 1400001)

    def test_losing_own_bid_excluded(self):
        self.assertEqual(self.samples(bids=[{"user": {"id": 7}, "amount": 1050000}])[0].rival_ratio, 1.1)

    def test_own_uncontested_win_and_no_future_leakage(self):
        samples = self.samples(winner=7)
        self.assertEqual(samples[0].rival_ratio, 0)
        self.assertIsNone(competitive_bid(samples, 1000000, 3, STAMP)[0])
        self.assertIsNone(historical_value([[260913, 1]], STAMP))
        self.assertIsNone(historical_value([[260901, 1]], STAMP))

    def test_unavailable_and_no_games_not_recommended(self):
        for p in (player(status="injured"), player(status="doubt"), player(playedHome=0, playedAway=0)):
            self.assertIsNone(recommend(p, 1000000, [], self.samples(), STAMP+1, 3000000, 3000000)["amount"])

    def test_budget_limit_and_score_at_final_price(self):
        p = player()
        result = recommend(p, 1000000, [], self.samples(), STAMP+1, 3000000, 3000000)
        self.assertEqual(result["amount"], 1100001)
        self.assertEqual(result["score"], round(performance(p)/1.100001, 2))
        self.assertIsNone(recommend(p, 1000000, [], self.samples(), STAMP+1, 900000, 3000000)["amount"])

    def test_better_existing_player_and_trading_margin(self):
        self.assertIsNone(recommend(player(), 1000000, [player(points=40)], self.samples(), STAMP+1, 3000000, 3000000)["amount"])
        self.assertIsNone(recommend(player(), 1000000, [], self.samples(), STAMP+1, 3000000, 3000000, mode="Reventa")["amount"])


if __name__ == "__main__":
    unittest.main()
