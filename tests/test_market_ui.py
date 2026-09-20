"""Ejecuta la pestaña real de mercado con API simulada, sin credenciales."""
from pathlib import Path
import unittest
from streamlit.testing.v1 import AppTest


class MarketUITests(unittest.TestCase):
    def test_catalog_failure_stops_without_traceback_or_retry_button(self):
        source = (Path(__file__).resolve().parents[1] / "app.py").read_text(encoding="utf-8")
        start = source.index("try:\n    players = load_players()")
        block = source[start:source.index("with tab_market:", start)]
        preamble = '''
import streamlit as st
import requests
from unittest.mock import Mock
from biwenger.client import BiwengerCatalogError
load_competition_data = Mock()
def load_players():
    raise BiwengerCatalogError("Catalogo no disponible: HTTP 403")
'''
        app = AppTest.from_string(preamble + block).run(timeout=15)
        self.assertEqual(len(app.exception), 0)
        self.assertIn("HTTP 403", app.error[0].value)
        self.assertEqual(len(app.button), 0)

