"""Cuentas atrás, fotos del CDN y HTML del panel. Sin red y sin escrituras."""
import ast
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo
import unittest

from analysis.clauses import ClauseOpportunity
from analysis.present import (
    availability_label,
    catalog_kpis,
    clause_countdown,
    recent_clause_moves,
    remaining_label,
    spanish_datetime_label,
    split_clause_window,
    squad_kpis,
    top_daily_risers,
    unlock_datetime_label,
    within_hours,
)
from biwenger.images import player_photo_url, team_shield_url, user_avatar_url
from biwenger.panel_ui import clause_visual, position_badge, render_clause_table, variation_html
from biwenger.parse import parse_players

MADRID = ZoneInfo("Europe/Madrid")
ROOT = Path(__file__).resolve().parents[1]


def player(**fields):
    raw = {
        "id": 19441,
        "name": "Pedri",
        "position": 3,
        "price": 16_200_000,
        "teamID": 1,
        "priceIncrement": 90_000,
        "points": 20,
    }
    raw.update(fields)
    data = {"teams": {"1": {"id": 1, "name": "Barcelona"}}, "players": {str(raw["id"]): raw}}
    return parse_players(data)[0]


class ImageUrlTests(unittest.TestCase):
    def test_player_and_team_cdn_paths(self):
        self.assertEqual(player_photo_url(19441), "https://cdn.biwenger.com/i/p/19441.png")
        self.assertEqual(team_shield_url(1), "https://cdn.biwenger.com/i/t/1.png")
        self.assertIsNone(player_photo_url(None))
        self.assertIsNone(team_shield_url(None))

    def test_user_avatar_prefers_absolute_icon(self):
        facebook = "https://graph.facebook.com/1/picture"
        self.assertEqual(user_avatar_url(facebook, 9), facebook)
        self.assertEqual(user_avatar_url("/i/u/9.png", 9), "https://cdn.biwenger.com/i/u/9.png")
        self.assertEqual(user_avatar_url("11502113"), "https://cdn.biwenger.com/i/u/11502113.png")
        self.assertEqual(user_avatar_url(None, 9), "https://cdn.biwenger.com/i/u/9.png")


class CountdownTests(unittest.TestCase):
    def test_remaining_labels(self):
        self.assertEqual(remaining_label(None), "Ya")
        self.assertEqual(remaining_label(0), "Ya")
        self.assertEqual(remaining_label(-5), "Ya")
        self.assertEqual(remaining_label(30 * 60), "30 min")
        self.assertEqual(remaining_label(12 * 3600), "12 h")
        self.assertEqual(remaining_label(36 * 3600), "36 h")
        self.assertEqual(remaining_label(50 * 3600), "2 d 2 h")

    def test_unlock_uses_madrid_time(self):
        moment = datetime(2026, 11, 27, 7, 4, tzinfo=MADRID)
        timestamp = int(moment.timestamp())
        self.assertEqual(unlock_datetime_label(timestamp), "27/11 07:04")
        info = clause_countdown(timestamp, timestamp - 12 * 3600)
        self.assertEqual(info["remaining"], "12 h")
        self.assertFalse(info["open"])
        self.assertEqual(availability_label(timestamp, timestamp - 12 * 3600), "27/11 07:04 · 12 h")
        self.assertEqual(availability_label(timestamp, timestamp + 60), "Ya")
        self.assertEqual(availability_label(None, timestamp), "Ya")

    def test_spanish_header_date(self):
        moment = datetime(2026, 11, 26, 18, 30, tzinfo=MADRID)
        self.assertEqual(spanish_datetime_label(moment), "Jueves 26 de noviembre de 2026, 18:30")


class ShowcaseTests(unittest.TestCase):
    def test_catalog_kpis_and_risers_use_official_increment(self):
        players = [
            player(id=1, name="Sube", price=1_000_000, priceIncrement=80_000, points=1),
            player(id=2, name="Baja", price=2_000_000, priceIncrement=-10_000, points=9),
            player(id=3, name="Plano", price=3_000_000, priceIncrement=0, points=3),
            player(id=4, name="Sin dato", price=4_000_000, priceIncrement=None, points=4),
        ]
        labels = {label: (value, sub) for label, value, sub in catalog_kpis(players)}
        self.assertEqual(labels["Valor total mercado"][0], "10.0M")
        self.assertEqual(labels["Suben hoy"][0], "1")
        self.assertEqual(labels["Bajan hoy"][0], "1")
        self.assertEqual(labels["Sin cambio hoy"][0], "2")
        self.assertEqual([p.name for p in top_daily_risers(players, limit=5)], ["Sube"])

    def test_open_clauses_sort_by_price_and_upcoming_by_time(self):
        cheap = ClauseOpportunity(player(id=1, name="Barato", price=1_000_000), "Norte", 2, 800_000, 50, -1)
        expensive = ClauseOpportunity(player(id=2, name="Caro", price=9_000_000), "Sur", 3, 4_000_000, 10, -2)
        soon = ClauseOpportunity(player(id=3, name="Pronto", price=2_000_000), "Norte", 2, 1_500_000, 200, 0.5)
        later = ClauseOpportunity(player(id=4, name="Luego", price=2_000_000), "Sur", 3, 1_500_000, 400, 1.5)
        open_now, upcoming = split_clause_window([cheap, later, expensive, soon])
        self.assertEqual([item.player.name for item in open_now], ["Caro", "Barato"])
        self.assertEqual([item.player.name for item in upcoming], ["Pronto", "Luego"])
        self.assertTrue(within_hours(soon, 48))
        self.assertFalse(within_hours(soon, 10))
        self.assertTrue(within_hours(later, 48))

    def test_squad_kpis_count_owned_and_free_without_double_counting(self):
        players = [player(id=1), player(id=2), player(id=3)]
        rosters = {1: {"players": [{"id": 1}, {"id": 1}]}, 2: {"players": [{"id": 2}]}}
        items = dict((label, value) for label, value, _sub in squad_kpis(rosters, players, 4, 1))
        self.assertEqual(items["Cláusulas abiertas"], "4")
        self.assertEqual(items["Próximas 48 h"], "1")
        self.assertEqual(items["Con dueño"], "2")
        self.assertIn("1 libres", squad_kpis(rosters, players, 4, 1)[2][2])

    def test_recent_clauses_keep_only_the_last_week(self):
        now = 1_700_000_000
        rows = [
            {"tipo": "Cláusula", "date": now - 3600, "jugador": "Hoy"},
            {"tipo": "Cláusula", "date": now - 8 * 86400, "jugador": "Vieja"},
            {"tipo": "Fichaje", "date": now - 10, "jugador": "Compra"},
        ]
        self.assertEqual([row["jugador"] for row in recent_clause_moves(rows, now)], ["Hoy"])


class HtmlTests(unittest.TestCase):
    def test_clause_table_escapes_names_and_embeds_photo(self):
        now = 1_700_000_000
        rogue = player(id=31027, name='Arda <b>Güler</b>', price=19_100_000)
        opportunity = ClauseOpportunity(rogue, "Antuan & Killer", 7, 34_400_000, now + 12 * 3600, 0.5)
        rendered = render_clause_table([clause_visual(opportunity, "https://cdn.biwenger.com/i/u/7.png", now)], countdown=True)
        self.assertIn("https://cdn.biwenger.com/i/p/31027.png", rendered)
        self.assertIn("https://cdn.biwenger.com/i/t/1.png", rendered)
        self.assertIn("Arda &lt;b&gt;Güler&lt;/b&gt;", rendered)
        self.assertIn("Antuan &amp; Killer", rendered)
        self.assertNotIn("<b>Güler</b>", rendered)
        self.assertIn("12 h", rendered)
        self.assertIn("34.4M", rendered)
        self.assertIn("▲ +90.000 €", rendered)
        self.assertIn("pos-MC", position_badge("MC"))

    def test_variation_colors(self):
        self.assertIn("var-pos", variation_html(90_000))
        self.assertIn("var-neg", variation_html(-10_000))
        self.assertIn("var-neu", variation_html(0))
        self.assertIn("var-neu", variation_html(None))


class CachePolicyTests(unittest.TestCase):
    def test_league_reads_are_cached_for_ten_minutes(self):
        tree = ast.parse((ROOT / "app.py").read_text(encoding="utf-8"))
        cached = {}
        for node in tree.body:
            if not isinstance(node, ast.FunctionDef):
                continue
            for decorator in node.decorator_list:
                if not isinstance(decorator, ast.Call):
                    continue
                func = decorator.func
                if isinstance(func, ast.Attribute) and func.attr == "cache_data":
                    ttl = {kw.arg: kw.value.value for kw in decorator.keywords if isinstance(kw.value, ast.Constant)}
                    cached[node.name] = ttl.get("ttl")
        for name in (
            "load_my_team_response",
            "load_market_response",
            "load_standings_response",
            "load_movements_response",
            "load_rosters_response",
        ):
            self.assertEqual(cached[name], 600, name)
        self.assertEqual(cached["load_player_price_history"], 86400)
        loader = next(node for node in tree.body if isinstance(node, ast.FunctionDef) and node.name == "load_players")
        self.assertEqual(loader.decorator_list, [])


if __name__ == "__main__":
    unittest.main()
