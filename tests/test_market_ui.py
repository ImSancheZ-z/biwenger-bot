"""Ejecuta la pestaña real de mercado con API simulada, sin credenciales."""
from pathlib import Path
import unittest
from streamlit.testing.v1 import AppTest


class MarketUITests(unittest.TestCase):
    def test_catalog_failure_shows_retry_without_traceback(self):
        source = (Path(__file__).resolve().parents[1] / "app.py").read_text(encoding="utf-8")
        start = source.index("try:\n    players = load_players()")
        block = source[start:source.index("with tab_market:", start)]
        preamble = '''
import streamlit as st
from unittest.mock import Mock
from biwenger.client import BiwengerCatalogError
load_competition_data = Mock()
def load_players():
    raise BiwengerCatalogError("Catalogo no disponible: HTTP 403")
'''
        app = AppTest.from_string(preamble + block).run(timeout=15)
        self.assertEqual(len(app.exception), 0)
        self.assertIn("HTTP 403", app.error[0].value)
        self.assertEqual(len(app.button), 1)
        app.button[0].click().run(timeout=15)
        self.assertEqual(len(app.exception), 0)

    def test_market_controls_and_empty_history(self):
        source = (Path(__file__).resolve().parents[1] / "app.py").read_text(encoding="utf-8")
        helpers = source[source.index("def format_euro("):source.index("@st.cache_data(ttl=600)")]
        block = source[source.index("with tab_active_market:"):source.index("with tab_clauses:")]
        preamble = '''
import streamlit as st
import pandas as pd
import time
from typing import Optional
from types import SimpleNamespace
from biwenger.parse import parse_players, parse_market
from analysis.market_v2 import build_auctions, recommend as recommend_v2, backtest, allocate_budget
players = parse_players({"players": {"1": {"id": 1, "name": "Ejemplo", "position": 3, "price": 1000000, "priceIncrement": 20000, "playedHome": 4, "points": 24}}})
players_by_id = {p.id: p for p in players}
class FakeClient:
    league_user_id = 7
    def get_market(self):
        return {"data": {"status": {"balance": 5000000, "maximumBid": 5000000}, "sales": [{"player": {"id": 1}, "price": 1000000}], "offers": []}}
    def get_my_team(self):
        return {"data": {"players": []}}
load_settings = lambda: SimpleNamespace(email="", password="", league_id="")
get_authed_client = lambda *args: FakeClient()
load_auction_movements = lambda *args: []
load_player_price_history = lambda slug: []
format_euro = str
tab_active_market = st.container()
'''
        app = AppTest.from_string(preamble + helpers + block).run(timeout=15)
        self.assertEqual(len(app.exception), 0)
        self.assertEqual(len(app.error), 0)
        self.assertEqual(len(app.dataframe), 1)
        app.radio[0].set_value("Reventa").run(timeout=15)
        self.assertEqual(len(app.exception), 0)
        self.assertEqual(len(app.error), 0)


if __name__ == "__main__":
    unittest.main()
