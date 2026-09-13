import unittest

from analysis.bidding import price_trend_abs_per_day
from analysis.clauses import ClauseOpportunity, score_opportunities
from biwenger.parse import parse_players


class DailyPriceChangeTests(unittest.TestCase):
    def player(self, **fields):
        return parse_players({"players": {"15396": {
            "id": 15396, "name": "Brugué", "slug": "roger-brugue",
            "price": 2610000, **fields,
        }}})[0]

    def test_official_daily_change_is_not_historical_average(self):
        # Datos públicos consultados el 13/09/2026.
        history = [[260907, 2310000], [260908, 2400000],
                   [260909, 2460000], [260910, 2520000],
                   [260911, 2570000], [260912, 2590000],
                   [260913, 2610000]]
        player = self.player(priceIncrement=20000)
        self.assertEqual(price_trend_abs_per_day(history), 50000)
        self.assertEqual(player.price_increment, 20000)
        opportunity = ClauseOpportunity(player, "Rival", 1, 2500000, None, -1)
        score_opportunities([opportunity], lambda slug: history)
        self.assertEqual(opportunity.trend_abs_per_day, 20000)

    def test_zero_negative_and_missing_changes(self):
        for value in (0, -20000, None):
            with self.subTest(value=value):
                player = self.player(priceIncrement=value)
                self.assertEqual(player.price_increment, value)
                opportunity = ClauseOpportunity(player, "Rival", 1, 2500000, None, -1)
                score_opportunities([opportunity], lambda slug: [])
                self.assertEqual(opportunity.trend_abs_per_day, value)
        self.assertIsNone(self.player().price_increment)


if __name__ == "__main__":
    unittest.main()
