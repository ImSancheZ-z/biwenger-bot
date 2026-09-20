from pathlib import Path
import unittest
from types import SimpleNamespace
from analysis.economy import clause_costs_by_user, build_money_timeline, net_cash_flow, balance_summary
from analysis.initial_budget import find_initial_squad_ids, compute_initial_budget, find_season_start_date

class EconomyTests(unittest.TestCase):
    def test_initial_roster_includes_explicit_zero_purchase_price(self):
        roster = [{'id': 41385, 'owner': {'date': 1786016057, 'price': 0, 'invested': 700000}}]
        self.assertEqual(find_season_start_date(roster), 1786016057)
        self.assertEqual(find_initial_squad_ids(roster, []), {41385})
        result = compute_initial_budget(roster, [], {}, lambda _: [[260806, 1280000]])
        self.assertEqual(result.initial_balance, 38720000)

    def test_actual_zero_balance_takes_priority_over_estimate(self):
        summary = balance_summary(200000, 0, (1000000, '20/09/2026'))
        self.assertEqual(summary['Saldo actual'], 0)
        self.assertEqual(summary['Saldo confirmado'], 0)
        self.assertEqual(summary['Tipo de saldo'], 'Real consultado')
        self.assertEqual(summary['Comparación ±200.000 €'], 'Dentro del margen')

    def test_reported_balance_is_separate_from_current_estimate(self):
        summary = balance_summary(9560423, reported=(9460000, '20/09/2026'))
        self.assertEqual(summary['Saldo actual'], 9560423)
        self.assertEqual(summary['Saldo confirmado'], 9460000)
        self.assertEqual(summary['Diferencia (real − estimado)'], -100423)
        self.assertIn('20/09/2026', summary['Fuente de confirmación'])
        self.assertEqual(summary['Tipo de saldo'], 'Estimado')

    def test_unknown_balance_is_not_filled_with_cash_flow(self):
        summary = balance_summary(None)
        self.assertIsNone(summary['Saldo actual'])
        self.assertIsNone(summary['Saldo confirmado'])
        self.assertEqual(summary['Tipo de saldo'], 'No disponible')
    def test_player_outside_catalog_uses_id_and_exact_historical_price(self):
        from unittest.mock import Mock
        history = Mock(return_value=[[260805, 940000], [260806, 880000]])
        result = compute_initial_budget(
            [{'id': 25396, 'owner': {'date': 1786016057}}], [], {}, history,
        )
        history.assert_called_once_with('25396')
        self.assertEqual(result.initial_balance, 39120000)
        self.assertEqual(result.missing_price_count, 0)
        self.assertEqual(result.squad[0].price_on_start_date, 880000)

    def test_missing_history_is_not_replaced_by_another_days_price(self):
        result = compute_initial_budget(
            [{'id': 25396, 'owner': {'date': 1786016057}}], [], {},
            lambda _: [[260805, 940000]],
        )
        self.assertEqual(result.missing_price_count, 1)
        self.assertIsNone(result.squad[0].price_on_start_date)

    def test_clause_costs_reduce_balance_and_keep_each_payment(self):
        moves = [{'type': 'clauseIncrement', 'date': 10, 'content': [
            {'user': {'id': 1}, 'player': 7, 'amount': 400000, 'releaseClause': 3592000},
            {'user': {'id': 1}, 'player': 7, 'amount': 500000},
            {'user': {'id': 2}, 'player': 8, 'amount': 100000}]}]
        costs = clause_costs_by_user(moves)
        timeline = build_money_timeline(1, [], [], costs[1])
        self.assertEqual(net_cash_flow(timeline), -900000)
        self.assertEqual(len(timeline), 2)
        self.assertEqual(net_cash_flow(costs[2]), -100000)

    def test_initial_player_sold_then_bought_again(self):
        tx = [SimpleNamespace(player_id=1, role='compra', date=30),
              SimpleNamespace(player_id=1, role='venta', date=10),
              SimpleNamespace(player_id=2, role='compra', date=10),
              SimpleNamespace(player_id=2, role='venta', date=20)]
        self.assertEqual(find_initial_squad_ids([], tx), {1})



class EconomyUITests(unittest.TestCase):
    def test_missing_rosters_and_empty_history_do_not_break_tab(self):
        from streamlit.testing.v1 import AppTest
        source = (Path(__file__).resolve().parents[1] / "app.py").read_text(encoding="utf-8")
        block = source[source.index("with tab_economy:"):source.index("\nst.divider()\nwith Storage()", source.index("with tab_economy:"))]
        preamble = """
import streamlit as st
import pandas as pd
from unittest.mock import Mock
from analysis.economy import *
from analysis.initial_budget import *
from analysis.scouting import *
from types import SimpleNamespace
client = Mock()
client.league_user_id = 1
client.get_round_results.return_value = []
client.get_all_league_movements.return_value = []
client.get_league_standings.return_value = {'data': {'standings': [{'id': 1, 'name': 'Test'}]}}
client.get_market.return_value = {'data': {'status': {'balance': 0}}}
client.get_my_team.side_effect = RuntimeError('Roster unavailable')
load_settings = lambda: SimpleNamespace(email='', password='', league_id='1')
get_authed_client = lambda *args: client
load_player_price_history = Mock()
players_by_id = {}
format_euro = lambda value: '-' if value is None or pd.isna(value) else str(value)
style_money = lambda df, columns: df
(tab_economy,) = st.tabs(['Economia'])
"""
        app = AppTest.from_string(preamble + block).run(timeout=15)
        self.assertEqual(len(app.exception), 0)
        self.assertEqual(len(app.error), 0)
        self.assertEqual(app.dataframe[0].value.iloc[0]['Saldo confirmado'], 0)
        self.assertTrue(pd_is_missing(app.dataframe[0].value.iloc[0]['Saldo estimado hoy']))

        populated = preamble + """
client.get_my_team.side_effect = None
client.get_my_team.return_value = {'data': {'players': [{'id': 7, 'owner': {'date': 1786016057}}]}}
players_by_id = {7: SimpleNamespace(name='Player', slug='player')}
load_player_price_history.return_value = [[260806, 30000000]]
client.get_all_league_movements.return_value = [{'type': 'clauseIncrement', 'date': 1786719588, 'content': [{'user': {'id': 1}, 'player': 7, 'amount': 500000}]}]
"""
        app = AppTest.from_string(populated + block).run(timeout=15)
        self.assertEqual(len(app.exception), 0)
        self.assertEqual(len(app.error), 0)
        row = app.dataframe[0].value.iloc[0]
        self.assertEqual(row['Saldo estimado hoy'], 9500000)
        self.assertEqual(row['Blindajes'], 500000)
        self.assertEqual(row['Diferencia (real − estimado)'], -9500000)

        league = populated + """
client.get_league_standings.return_value = {'data': {'standings': [
    {'id': 1, 'name': 'Test'}, {'id': 14097561, 'name': 'UnTrollConUnPlan'}, {'id': 3, 'name': 'Rival'}]}}
client.get_other_user_roster.return_value = {'data': {'players': [{'id': 7, 'owner': {'date': 1786016057, 'price': 0}}]}}
"""
        app = AppTest.from_string(league + block).run(timeout=15)
        self.assertEqual(len(app.exception), 0)
        self.assertEqual(len(app.error), 0)
        rows = app.dataframe[0].value.set_index('Usuario')
        self.assertEqual(rows.loc['Test (tú)', 'Saldo actual'], 0)
        self.assertEqual(rows.loc['UnTrollConUnPlan', 'Saldo confirmado'], 9460000)
        self.assertEqual(rows.loc['Rival', 'Saldo actual'], 10000000)
        self.assertEqual(rows.loc['Rival', 'Tipo de saldo'], 'Estimado')
        self.assertTrue(pd_is_missing(rows.loc['Rival', 'Saldo confirmado']))



def pd_is_missing(value):
    import pandas as pd
    return pd.isna(value)


if __name__ == '__main__':
    unittest.main()
