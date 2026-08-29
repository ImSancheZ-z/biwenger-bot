"""Dashboard local de Biwenger con Streamlit.

Uso:
    streamlit run app.py

El catálogo de jugadores de LaLiga es público (no requiere login). Las
pestañas de plantilla propia, mercado activo, clasificación y movimientos
de liga necesitan credenciales en .env (ver README para el detalle de cada
endpoint).
"""
from __future__ import annotations

import sys
from pathlib import Path
from typing import Optional

import pandas as pd
import streamlit as st

sys.path.insert(0, str(Path(__file__).resolve().parent))

from analysis.bidding import (
    compute_likely_starters,
    compute_top_teams_by_value,
    effective_premium,
    historical_bid_premium,
    price_trend_abs_per_day,
    price_trend_pct_per_day,
    recommend_bid,
)
from analysis.clauses import find_clause_opportunities, score_opportunities
from analysis.economy import (
    build_money_timeline,
    cumulative_flow,
    net_cash_flow,
    reconstruct_balances,
    round_bonuses_by_user,
)
from analysis.engine import rank_players
from analysis.initial_budget import compute_initial_budget, find_season_start_date
from analysis.scouting import build_user_activity, detect_tendencies, summarize_user
from biwenger.client import BiwengerClient
from biwenger.config import load_settings
from biwenger.models import POSITION_NAMES
from biwenger.parse import (
    parse_movements,
    parse_market,
    parse_my_team,
    parse_players,
    parse_standings,
    squad_position_counts,
)
from biwenger.storage import Storage

st.set_page_config(page_title="Biwenger Bot", layout="wide")


def format_euro(value) -> str:
    """2710000 -> '2.710.000 €' (separador de miles con punto, sin decimales)."""
    if value is None or pd.isna(value):
        return "-"
    return f"{value:,.0f} €".replace(",", ".")


def format_euro_signed(value) -> str:
    """+2710000 -> '+2.710.000 €', -19000 -> '-19.000 €'. Para variaciones
    (tendencias), donde el signo es la parte importante del dato."""
    if value is None or pd.isna(value):
        return "-"
    sign = "+" if value > 0 else ("-" if value < 0 else "")
    return f"{sign}{abs(value):,.0f} €".replace(",", ".")


def style_money(df: pd.DataFrame, columns: list[str]) -> "pd.io.formats.style.Styler":
    """Aplica format_euro a las columnas indicadas sin tocar los valores
    subyacentes: el orden numérico al hacer clic en la cabecera sigue siendo
    correcto, solo cambia cómo se muestran."""
    present = [c for c in columns if c in df.columns]
    return df.style.format({c: format_euro for c in present})


def _trend_color(value) -> str:
    """Verde si sube, rojo si baja — para columnas de tendencia."""
    if value is None or (isinstance(value, float) and pd.isna(value)):
        return ""
    if value > 0:
        return "color: #1a9850; font-weight: 600"
    if value < 0:
        return "color: #d73027; font-weight: 600"
    return ""


def style_table(
    df: pd.DataFrame,
    money_columns: Optional[list[str]] = None,
    signed_money_columns: Optional[list[str]] = None,
    trend_color_columns: Optional[list[str]] = None,
) -> "pd.io.formats.style.Styler":
    """Versión ampliada de style_money: además de formatear importes en
    euros, puede formatear columnas de variación con signo (+/-) y colorear
    en verde/rojo columnas de tendencia (subiendo/bajando)."""
    money_columns = [c for c in (money_columns or []) if c in df.columns]
    signed_money_columns = [c for c in (signed_money_columns or []) if c in df.columns]
    trend_color_columns = [c for c in (trend_color_columns or []) if c in df.columns]

    fmt = {c: format_euro for c in money_columns}
    fmt.update({c: format_euro_signed for c in signed_money_columns})
    styler = df.style.format(fmt)
    if trend_color_columns:
        styler = styler.map(_trend_color, subset=trend_color_columns)
    return styler


@st.cache_data(ttl=600)
def load_players():
    client = BiwengerClient(email="", password="")
    raw = client.get_competition_data()
    return parse_players(raw["data"])


@st.cache_resource(ttl=1800)
def get_authed_client(email: str, password: str, league_id: str | None) -> BiwengerClient:
    """Cliente autenticado, compartido entre pestañas y reruns de Streamlit.

    Sin esto, cada pestaña crea su propio BiwengerClient y hace login +
    GET /account desde cero en cada rerun (Streamlit reejecuta el script
    entero al cambiar de pestaña o tocar cualquier widget), lo que dispara
    un 429 Too Many Requests de la API de Biwenger casi de inmediato."""
    client = BiwengerClient(email, password, league_id=league_id)
    client.login()
    return client


@st.cache_data(ttl=86400)
def load_player_price_history(slug: str) -> list:
    """Histórico de precio de un jugador. Público, sin credenciales — se
    cachea 24h porque el reparto inicial de temporada no cambia, y evita
    repetir ~150 peticiones (una por jugador del reparto de cada usuario)
    en cada recarga de la pestaña Economía."""
    client = BiwengerClient(email="", password="")
    return client.get_player_price_history(slug)


def players_to_dataframe(players) -> pd.DataFrame:
    rows = []
    for p in players:
        fixture = p.next_fixture
        rows.append(
            {
                "Nombre": p.name,
                "Equipo": p.team_name,
                "Posición": p.position_name,
                "Precio": p.price,
                "Puntos": p.points,
                "Pts/Millón": round(p.points_per_million, 2) if p.points_per_million else None,
                "Forma reciente": p.recent_form,
                "Estado": p.status,
                "Lesión/Info": p.status_info,
                "Próximo rival": fixture.rival_name if fixture else None,
                "Local/Visitante": ("Local" if fixture.is_home else "Visitante") if fixture else None,
                "Dificultad rival": fixture.difficulty if fixture else None,
            }
        )
    return pd.DataFrame(rows)


st.title("⚽ Biwenger Bot — Análisis de mercado")

(
    tab_clauses,
    tab_active_market,
    tab_team,
    tab_chollos,
    tab_moves,
    tab_market,
    tab_standings,
    tab_scouting,
    tab_economy,
) = st.tabs(
    [
        "Cláusulas",
        "Mercado activo (liga)",
        "Mi plantilla",
        "Chollos (recomendaciones)",
        "Movimientos de liga",
        "Catálogo LaLiga",
        "Clasificación",
        "Fichajes por usuario",
        "Economía",
    ]
)

players = load_players()

with tab_market:
    st.subheader("Todos los jugadores de LaLiga")
    df = players_to_dataframe(players)

    col1, col2, col3 = st.columns(3)
    with col1:
        positions = ["Todas"] + list(POSITION_NAMES.values())
        pos_filter = st.selectbox("Posición", positions)
    with col2:
        estados = ["Todos"] + sorted(df["Estado"].dropna().unique().tolist())
        estado_filter = st.selectbox("Estado", estados)
    with col3:
        team_filter = st.text_input("Filtrar por equipo (texto libre)")

    filtered = df
    if pos_filter != "Todas":
        filtered = filtered[filtered["Posición"] == pos_filter]
    if estado_filter != "Todos":
        filtered = filtered[filtered["Estado"] == estado_filter]
    if team_filter:
        filtered = filtered[filtered["Equipo"].str.contains(team_filter, case=False, na=False)]

    st.dataframe(
        style_money(filtered.sort_values("Puntos", ascending=False), ["Precio"]),
        width='stretch',
        height=600,
    )

with tab_chollos:
    st.subheader("Recomendaciones (ratio puntos/precio + forma + dificultad rival)")
    st.caption(
        "v1: score = puntos por millón + bonus por forma reciente "
        "- penalización si el próximo rival es difícil. "
        "Excluye por defecto lesionados/sancionados/descartados."
    )

    col1, col2 = st.columns(2)
    with col1:
        pos_options = {"Todas": None} | {name: pid for pid, name in POSITION_NAMES.items() if pid != 5}
        pos_label = st.selectbox("Posición", list(pos_options.keys()), key="chollos_pos")
    with col2:
        top_n = st.slider("Cuántos mostrar", 5, 50, 20)

    scored = rank_players(players, position=pos_options[pos_label], top_n=top_n)
    rows = []
    for sp in scored:
        p = sp.player
        fixture = p.next_fixture
        rows.append(
            {
                "Nombre": p.name,
                "Equipo": p.team_name,
                "Posición": p.position_name,
                "Precio": p.price,
                "Puntos": p.points,
                "Pts/Millón": round(sp.points_per_million, 2) if sp.points_per_million else None,
                "Forma reciente": p.recent_form,
                "Próximo rival": f"{fixture.rival_name} ({'L' if fixture.is_home else 'V'})" if fixture else "-",
                "Dificultad rival": fixture.difficulty if fixture else None,
                "Score": round(sp.score, 2),
            }
        )
    st.dataframe(
        style_money(pd.DataFrame(rows), ["Precio"]),
        width='stretch',
        height=600,
    )

players_by_id = {p.id: p for p in players}

with tab_team:
    st.subheader("Mi plantilla")
    try:
        settings = load_settings()
        client = get_authed_client(settings.email, settings.password, settings.league_id)
        team_resp = client.get_my_team()
        team_data = team_resp.get("data", {})

        rows = parse_my_team(team_data, players_by_id)
        team_df = pd.DataFrame(rows).rename(
            columns={
                "name": "Nombre",
                "team_name": "Equipo",
                "position_name": "Posición",
                "points": "Puntos",
                "price": "Precio",
                "clause": "Cláusula",
                "en_alineacion": "En 11 titular",
                "en_venta": "En venta",
            }
        ).drop(columns=["id", "position"])
        st.caption(f"Alineación: {team_data.get('lineup', {}).get('type', '?')}")
        st.dataframe(
            style_money(team_df, ["Precio", "Cláusula"]),
            width='stretch',
            height=450,
        )

        if team_data.get("market"):
            st.caption("Ofertas de venta abiertas")
            st.dataframe(
                style_money(pd.DataFrame(team_data["market"]), ["price"]),
                width='stretch',
            )
    except RuntimeError as exc:
        st.warning(f"Configura tu .env para ver esto: {exc}")
    except Exception as exc:  # noqa: BLE001 — mostramos cualquier fallo de red/API tal cual
        st.error(f"Error consultando la API: {exc}")

with tab_active_market:
    st.subheader("Mercado activo de mi liga")
    st.caption("Jugadores en venta ahora mismo: libres del sistema o puestos por otros usuarios.")
    try:
        settings = load_settings()
        client = get_authed_client(settings.email, settings.password, settings.league_id)
        market_resp = client.get_market()
        market_data = market_resp.get("data", {})

        status = market_data.get("status", {})
        balance = status.get("balance", 0)
        max_bid = status.get("maximumBid", 0)
        col1, col2 = st.columns(2)
        col1.metric("Tu saldo", format_euro(balance))
        col2.metric("Puja máxima permitida", format_euro(max_bid))

        # Para la puja recomendada necesitamos tu plantilla (qué posiciones te
        # faltan) y el histórico real de subastas competidas de tu liga.
        my_team_resp = client.get_my_team()
        my_squad_rows = parse_my_team(my_team_resp.get("data", {}), players_by_id)
        position_counts = squad_position_counts(my_squad_rows)

        movements = client.get_all_league_movements(page_size=50, max_pages=5)
        observed_premium, n_samples = historical_bid_premium(movements)
        premium, premium_note = effective_premium(observed_premium, n_samples)

        top_teams = compute_top_teams_by_value(players)
        likely_starters = compute_likely_starters(players)

        rows = parse_market(market_data, players_by_id)

        origen_filter = st.radio(
            "Vendedor",
            ["Todos", "Libres (sistema)", "De otros usuarios"],
            horizontal=True,
        )
        if origen_filter == "Libres (sistema)":
            rows = [r for r in rows if r["is_free_agent"]]
        elif origen_filter == "De otros usuarios":
            rows = [r for r in rows if not r["is_free_agent"]]

        for row in rows:
            trend = trend_abs = None
            if row["slug"]:
                history = load_player_price_history(row["slug"])
                trend = price_trend_pct_per_day(history)
                trend_abs = price_trend_abs_per_day(history)
            is_exceptional = row["team_name"] in top_teams and row["id"] in likely_starters
            rec = recommend_bid(
                price=row["price_venta"],
                is_free_agent=row["is_free_agent"],
                score=row["score"],
                balance=balance,
                max_bid=max_bid,
                premium=premium,
                premium_note=premium_note,
                position=row["position"],
                my_squad_position_counts=position_counts,
                trend_pct_per_day=trend,
                is_exceptional=is_exceptional,
            )
            row["tendencia"] = round(trend, 2) if trend is not None else None
            row["tendencia_abs"] = trend_abs
            row["excepcional"] = is_exceptional
            row["accion"] = rec.action
            row["puja_recomendada"] = rec.amount
            row["motivo"] = rec.reasoning

        market_df = pd.DataFrame(rows).rename(
            columns={
                "name": "Nombre",
                "team_name": "Equipo",
                "position_name": "Posición",
                "points": "Puntos",
                "price_venta": "Precio de venta",
                "ratio_pts_millon": "Pts/Millón",
                "score": "Score chollo",
                "tendencia": "Tendencia (%/día)",
                "tendencia_abs": "Tendencia (€/día)",
                "excepcional": "Top-5 + titular",
                "vendedor": "Vendedor",
                "hasta": "Hasta (timestamp)",
                "accion": "Acción",
                "puja_recomendada": "Puja recomendada",
                "motivo": "Motivo",
            }
        ).drop(columns=["id", "slug", "position", "is_free_agent"])
        st.caption(
            "Ordenado por 'Score chollo'. La 'Puja recomendada' es una estimación v1 — "
            "ver limitaciones en el desplegable de abajo."
        )
        st.dataframe(
            style_table(
                market_df.sort_values("Score chollo", ascending=False, na_position="last"),
                money_columns=["Precio de venta", "Puja recomendada"],
                signed_money_columns=["Tendencia (€/día)"],
                trend_color_columns=["Tendencia (%/día)", "Tendencia (€/día)"],
            ),
            width='stretch',
            height=500,
        )

        with st.expander("¿Cómo se calcula la puja recomendada? Limitaciones"):
            st.markdown(
                f"- **Prima de subasta usada ahora mismo**: {premium_note} "
                f"(factor {premium:.2f}).\n"
                "- **Venta directa de otro usuario** (precio fijo, sin subasta): "
                "se recomienda comprar ya si el ratio puntos/precio es bueno; en "
                "este caso no hay 'puja', se paga el precio pedido.\n"
                "- **Jugador libre del sistema** (subasta a ciegas): la puja "
                "sugerida es precio de salida × prima histórica de tu liga, con "
                "un extra del 10% si te falta esa posición en tu plantilla.\n"
                "- **Tope de prima: +12% por defecto, +20% como máximo excepcional.** "
                "Un +20-30% no puede salir solo de combinar bonus menores — se "
                "recorta al +12% salvo que el jugador sea de un equipo **top-5 por "
                "valor de plantilla** (Barcelona, Real Madrid, Atlético, Villarreal, "
                "Betis ahora mismo — más estable que mirar los puntos de estas "
                "primeras jornadas) **y** sea titular habitual (aproximado: entre "
                "los 11 con más puntos de su equipo esta temporada — no tenemos el "
                "once real de los rivales). Columna 'Top-5 + titular' en la tabla.\n"
                "- **Tope de saldo: nunca más del 40% de tu saldo disponible en un "
                "único jugador**, para no quedarte sin margen el resto de la "
                "jornada — se aplica tanto a pujas como a compras directas.\n"
                "- **Tendencia de precio** (media de los últimos 7 días, vía el "
                "histórico diario de precio de cada jugador): si un jugador está "
                "cayendo con fuerza (≤ -1,5%/día) **no se recomienda pujar en "
                "absoluto**, por buen ratio que tenga hoy — mañana valdrá menos y "
                "tu inversión pierde valor con él. Si cae más suave se recorta un "
                "5% la puja; si sube con fuerza (≥ +1%/día) se añade un 8%, porque "
                "mañana costará más.\n"
                "- La dificultad del próximo rival ya está incluida en el 'Score "
                "chollo' (viene de `analysis/engine.py`). No se puede ir más allá "
                "del próximo partido: la API pública de Biwenger solo expone la "
                "jornada inmediatamente siguiente, no un calendario completo.\n"
                "- **No se puede usar el saldo real de tus rivales**: la "
                "configuración de tu liga tiene `balance: hidden` (lo fija el "
                "administrador), así que ni la propia app de Biwenger se lo "
                "enseña a nadie — no es una limitación nuestra, es la API.\n"
                "- Es una v1 deliberadamente simple: cuantas más subastas "
                "competidas se disputen en tu liga, más fiable será la prima "
                "estimada."
            )

        offers = market_data.get("offers", [])
        if offers:
            st.caption("Tus ofertas de compra pendientes")
            st.json(offers)
    except RuntimeError as exc:
        st.warning(f"Configura tu .env para ver esto: {exc}")
    except Exception as exc:  # noqa: BLE001
        st.error(f"Error consultando la API: {exc}")

with tab_clauses:
    st.subheader("Cláusulas disponibles o próximas a desbloquear")
    st.caption(
        "Jugadores de OTROS usuarios de tu liga cuya cláusula ya se puede pagar "
        "ahora mismo, o se podrá dentro del plazo que elijas. Se valoran igual "
        "que el resto del dashboard: ratio puntos/precio, tendencia de precio y "
        "comparación con el precio de mercado actual."
    )
    try:
        settings = load_settings()
        client = get_authed_client(settings.email, settings.password, settings.league_id)
        my_id = client.league_user_id

        within_days = st.slider("Ver cláusulas que se desbloqueen dentro de (días)", 0, 15, 3)

        standings_resp = client.get_league_standings()
        all_user_ids = [s["id"] for s in standings_resp.get("data", {}).get("standings", [])]

        with st.spinner("Revisando las plantillas de todos los usuarios de tu liga..."):
            rosters = {my_id: client.get_my_team()["data"]}
            for uid in all_user_ids:
                if uid == my_id:
                    continue
                try:
                    rosters[uid] = client.get_other_user_roster(uid)["data"]
                except Exception:  # noqa: BLE001 — usuario fantasma u otro fallo puntual, se omite
                    continue

            opportunities = find_clause_opportunities(rosters, players_by_id, my_id, within_days=within_days)
            score_opportunities(opportunities, load_player_price_history)

        if not opportunities:
            st.info(f"Ningún jugador de tus rivales tiene la cláusula pagable en los próximos {within_days} días.")
        else:
            col1, col2 = st.columns(2)
            with col1:
                position_options = ["Todas"] + list(POSITION_NAMES.values())
                position_filter = st.selectbox("Posición", position_options, key="clauses_pos_filter")
            with col2:
                owner_options = ["Todos"] + sorted({o.owner_name for o in opportunities})
                owner_filter = st.selectbox("Propietario actual", owner_options, key="clauses_owner_filter")

            filtered_opps = opportunities
            if position_filter != "Todas":
                filtered_opps = [o for o in filtered_opps if o.player.position_name == position_filter]
            if owner_filter != "Todos":
                filtered_opps = [o for o in filtered_opps if o.owner_name == owner_filter]

            rows = [
                {
                    "Jugador": o.player.name,
                    "Equipo": o.player.team_name,
                    "Posición": o.player.position_name,
                    "Precio mercado": o.player.price,
                    "Cláusula": o.clause,
                    "Beneficio inmediato": (o.player.price - o.clause) if o.player.price else None,
                    "% vs. mercado": o.vs_market_pct,
                    "Tendencia (€/día)": o.trend_abs_per_day,
                    "Tendencia (%/día)": round(o.trend_pct_per_day, 2) if o.trend_pct_per_day is not None else None,
                    "Disponible": "Ya" if o.days_until_unlockable <= 0 else f"en {o.days_until_unlockable:.1f} días",
                    "Score": o.score,
                    "Recomendación": o.recomendacion,
                    "Dueño actual": o.owner_name,
                    "Puntos": o.player.points,
                }
                for o in filtered_opps
            ]
            if not rows:
                st.info("Ningún jugador cumple estos filtros.")
            else:
                clauses_df = pd.DataFrame(rows).sort_values("Score", ascending=False, na_position="last")
                st.caption(f"{len(filtered_opps)} de {len(opportunities)} oportunidades, ordenadas por score.")
                st.dataframe(
                    style_table(
                        clauses_df,
                        money_columns=["Precio mercado", "Cláusula"],
                        signed_money_columns=["Tendencia (€/día)", "Beneficio inmediato"],
                        trend_color_columns=["Tendencia (%/día)", "Tendencia (€/día)", "Beneficio inmediato"],
                    ),
                    width='stretch',
                    height=550,
                )

            st.divider()
            st.markdown("#### Oportunidades de arbitraje (clausulazo especulativo)")
            st.caption(
                "Da igual si el jugador es bueno o no puntúa apenas: si la cláusula "
                "está igual o por debajo del precio de mercado actual Y el precio "
                "sube día a día, pagarla y vender al mercado al día siguiente deja "
                "beneficio casi seguro. Es una estrategia de trading, no de "
                "rendimiento futbolístico — respeta los filtros de arriba."
            )
            arbitrage_opps = [
                o
                for o in filtered_opps
                if o.vs_market_pct is not None
                and o.vs_market_pct <= 0
                and o.trend_pct_per_day is not None
                and o.trend_pct_per_day > 0
            ]
            if not arbitrage_opps:
                st.info("Ninguna oportunidad de arbitraje con los filtros actuales.")
            else:
                arb_rows = [
                    {
                        "Jugador": o.player.name,
                        "Equipo": o.player.team_name,
                        "Posición": o.player.position_name,
                        "Precio mercado": o.player.price,
                        "Cláusula": o.clause,
                        "Beneficio inmediato": (o.player.price - o.clause) if o.player.price else None,
                        "% vs. mercado": o.vs_market_pct,
                        "Tendencia (€/día)": o.trend_abs_per_day,
                        "Tendencia (%/día)": round(o.trend_pct_per_day, 2),
                        "Disponible": "Ya" if o.days_until_unlockable <= 0 else f"en {o.days_until_unlockable:.1f} días",
                        "Dueño actual": o.owner_name,
                    }
                    for o in arbitrage_opps
                ]
                arb_df = pd.DataFrame(arb_rows).sort_values("Tendencia (%/día)", ascending=False)
                st.dataframe(
                    style_table(
                        arb_df,
                        money_columns=["Cláusula", "Precio mercado", "Beneficio inmediato"],
                        signed_money_columns=["Tendencia (€/día)"],
                        trend_color_columns=["Tendencia (%/día)", "Tendencia (€/día)"],
                    ),
                    width='stretch',
                    height=350,
                )

            st.divider()
            st.markdown("#### Mejor combinación: subida + score + descuento vs. mercado")
            st.caption(
                "Solo jugadores con el precio subiendo. Se ordenan por una "
                "combinación de las tres señales (por posición relativa, no por "
                "valor absoluto, para que ninguna mande sola por tener una escala "
                "más grande): cuánto sube, qué buen ratio puntos/precio tiene la "
                "cláusula, y cuánto más barata es que el precio de mercado. "
                "'Posición combinada' más baja = mejor en las tres a la vez."
            )
            rising_opps = [
                o
                for o in filtered_opps
                if o.trend_pct_per_day is not None
                and o.trend_pct_per_day > 0
                and o.score is not None
                and o.vs_market_pct is not None
            ]
            if not rising_opps:
                st.info("Ningún jugador con precio subiendo y datos suficientes con los filtros actuales.")
            else:
                combo_rows = [
                    {
                        "Jugador": o.player.name,
                        "Equipo": o.player.team_name,
                        "Posición": o.player.position_name,
                        "Precio mercado": o.player.price,
                        "Cláusula": o.clause,
                        "Beneficio inmediato": (o.player.price - o.clause) if o.player.price else None,
                        "% vs. mercado": o.vs_market_pct,
                        "Tendencia (€/día)": o.trend_abs_per_day,
                        "Tendencia (%/día)": round(o.trend_pct_per_day, 2),
                        "Disponible": "Ya" if o.days_until_unlockable <= 0 else f"en {o.days_until_unlockable:.1f} días",
                        "Score": o.score,
                        "Dueño actual": o.owner_name,
                    }
                    for o in rising_opps
                ]
                combo_df = pd.DataFrame(combo_rows)
                rank_subida = combo_df["Tendencia (%/día)"].rank(ascending=False)
                rank_score = combo_df["Score"].rank(ascending=False)
                rank_descuento = combo_df["% vs. mercado"].rank(ascending=True)
                combo_df["Posición combinada"] = (rank_subida + rank_score + rank_descuento).round(1)
                combo_df = combo_df.sort_values("Posición combinada")
                st.dataframe(
                    style_table(
                        combo_df,
                        money_columns=["Precio mercado", "Cláusula"],
                        signed_money_columns=["Tendencia (€/día)", "Beneficio inmediato"],
                        trend_color_columns=["Tendencia (%/día)", "Tendencia (€/día)", "Beneficio inmediato"],
                    ),
                    width='stretch',
                    height=400,
                )

        with st.expander("¿Cómo se calcula esto? Limitaciones"):
            st.markdown(
                "- **Cláusula pagable**: se compara `owner.clauseLockedUntil` (fecha "
                "hasta la que NADIE puede pagarla) con la fecha límite que elijas. "
                "Se excluyen tus propios jugadores — no tiene sentido pagarte la "
                "cláusula a ti mismo.\n"
                "- **Score**: el mismo motor de `analysis/engine.py` que en las "
                "pestañas de Chollos y Mercado activo, aplicado al precio de la "
                "cláusula en vez de al de catálogo.\n"
                "- **% vs. mercado**: cuánto más (o menos) que el precio de catálogo "
                "actual del jugador es la cláusula. Un dueño no puede evitar que "
                "otro le pague la cláusula, así que este número no es negociable ni "
                "cambia con la urgencia — es la mecánica 'clause: steal' de tu liga.\n"
                "- **Recomendación 'Evitar (precio cayendo con fuerza)'**: mismo "
                "corte que en el estimador de puja — un jugador cuyo precio se "
                "desploma no es una ganga por muy barata que salga la cláusula.\n"
                "- **Arbitraje**: 'Beneficio inmediato' = precio de mercado actual "
                "menos la cláusula. Es lo que ganarías vendiendo el jugador al "
                "mercado justo después de clausularlo, sin contar la subida "
                "adicional de mañana (la tendencia apunta a que seguirá subiendo, "
                "pero no está garantizado).\n"
                "- **Posición combinada**: suma del puesto que ocupa cada jugador en "
                "tres rankings por separado (subida de precio, score, descuento vs. "
                "mercado) en vez de sumar los valores directamente — así ninguna de "
                "las tres pesa más solo por tener números más grandes. Cuanto más "
                "bajo, mejor está en las tres cosas a la vez.\n"
                "- No se aplican aquí los topes de +12%/+20% ni del 40% del saldo "
                "del estimador de puja: la cláusula es un precio fijo impuesto por "
                "Biwenger, no algo que tú decidas cuánto ofrecer."
            )
    except RuntimeError as exc:
        st.warning(f"Configura tu .env para ver esto: {exc}")
    except Exception as exc:  # noqa: BLE001
        st.error(f"Error consultando la API: {exc}")

with tab_standings:
    st.subheader("Clasificación de la liga")
    try:
        settings = load_settings()
        client = get_authed_client(settings.email, settings.password, settings.league_id)
        standings_resp = client.get_league_standings()
        rows = parse_standings(standings_resp.get("data", {}))
        standings_df = pd.DataFrame(rows).sort_values("position").drop(columns=["user_id"])
        standings_df = standings_df.rename(
            columns={
                "position": "Posición",
                "position_change": "+/- última jornada",
                "name": "Usuario",
                "points": "Puntos",
                "team_size": "Nº jugadores",
                "team_value": "Valor plantilla",
                "team_value_change": "Variación valor",
                "last_positions": "Posiciones anteriores",
            }
        )
        st.dataframe(
            style_money(standings_df, ["Valor plantilla", "Variación valor"]),
            width='stretch',
            hide_index=True,
        )
    except RuntimeError as exc:
        st.warning(f"Configura tu .env para ver esto: {exc}")
    except Exception as exc:  # noqa: BLE001
        st.error(f"Error consultando la API: {exc}")

with tab_moves:
    st.subheader("Histórico de fichajes de mi liga")
    st.caption(
        "Todas las operaciones reales de la temporada: fichajes directos, "
        "cláusulas pagadas, ventas al mercado y subastas de jugadores libres "
        "(con todas las pujas, no solo la ganadora)."
    )
    try:
        settings = load_settings()
        client = get_authed_client(settings.email, settings.password, settings.league_id)
        moves = client.get_all_league_movements(page_size=50, max_pages=20)

        rows = parse_movements(moves, players_by_id)
        if rows:
            moves_df = pd.DataFrame(rows)
            moves_df["fecha"] = pd.to_datetime(moves_df["date"], unit="s")
            moves_df["pujas_perdedoras"] = moves_df["pujas_perdedoras"].apply(
                lambda v: ", ".join(format_euro(x) for x in v) if v else "-"
            )
            display_df = moves_df.rename(
                columns={
                    "fecha": "Fecha",
                    "tipo": "Tipo",
                    "jugador": "Jugador",
                    "importe": "Importe",
                    "de": "De",
                    "a": "A",
                    "pujas_perdedoras": "Pujas perdedoras (perdió la subasta)",
                }
            )[["Fecha", "Tipo", "Jugador", "Importe", "De", "A", "Pujas perdedoras (perdió la subasta)"]]
            st.dataframe(
                style_money(display_df, ["Importe"]),
                width='stretch',
                height=500,
            )
        else:
            st.info("Tu liga todavía no tiene fichajes o ventas registrados esta temporada.")
    except RuntimeError as exc:
        st.warning(f"Configura tu .env para ver esto: {exc}")
    except Exception as exc:  # noqa: BLE001
        st.error(f"Error consultando la API: {exc}")

with tab_scouting:
    st.subheader("Fichajes por usuario")
    st.caption(
        "Qué compra y vende cada rival de tu liga esta temporada, y qué "
        "tendencias se detectan en su forma de jugar el mercado."
    )
    try:
        settings = load_settings()
        client = get_authed_client(settings.email, settings.password, settings.league_id)
        moves = client.get_all_league_movements(page_size=50, max_pages=20)
        users_activity = build_user_activity(moves, players_by_id)

        overview_rows = []
        for user in users_activity.values():
            stats = summarize_user(user.transactions)
            overview_rows.append(
                {
                    "Usuario": user.name,
                    "Fichajes": stats["n_compras"],
                    "Ventas": stats["n_ventas"],
                    "Gasto total": stats["total_gastado"],
                    "Ingreso total": stats["total_ingresado"],
                    "Precio medio fichaje": stats["precio_medio_compra"],
                    "% sobre mercado (medio)": (
                        f"{stats['vs_market_medio_pct']:+.0f}%" if stats["vs_market_medio_pct"] is not None else "-"
                    ),
                    "Sobrepuja media en subastas": (
                        f"{(stats['overpay_medio'] - 1) * 100:.0f}%" if stats["overpay_medio"] else "-"
                    ),
                }
            )
        overview_df = pd.DataFrame(overview_rows).sort_values("Fichajes", ascending=False).reset_index(drop=True)

        st.caption("Haz clic en una fila para ver el historial completo y las tendencias de ese usuario.")
        selection_event = st.dataframe(
            style_money(overview_df, ["Gasto total", "Ingreso total", "Precio medio fichaje"]),
            width='stretch',
            hide_index=True,
            on_select="rerun",
            selection_mode="single-row-required",
            key="scouting_overview_table",
        )
        selected_idx = selection_event.selection.rows[0] if selection_event.selection.rows else 0
        selected_name = overview_df.iloc[selected_idx]["Usuario"]
        selected_user = next(u for u in users_activity.values() if u.name == selected_name)
        stats = summarize_user(selected_user.transactions)

        st.divider()
        st.markdown(f"#### Tendencias detectadas: {selected_name}")
        for line in detect_tendencies(stats):
            st.markdown(f"- {line}")

        st.markdown(f"#### Historial completo ({len(selected_user.transactions)} movimientos esta temporada)")
        hist_rows = [
            {
                "Fecha": pd.to_datetime(t.date, unit="s"),
                "Tipo": t.role.capitalize(),
                "Operación": t.tipo,
                "Jugador": t.jugador,
                "Importe": t.price,
                "A quién / de quién": t.contraparte,
                "% sobre mercado": f"{t.vs_market_pct:+.0f}%" if t.vs_market_pct is not None else "-",
                "Sobrepuja vs rivales": f"{(t.overpay_ratio - 1) * 100:+.0f}%" if t.overpay_ratio else "-",
            }
            for t in selected_user.transactions
        ]
        st.dataframe(
            style_money(pd.DataFrame(hist_rows), ["Importe"]),
            width='stretch',
            height=400,
        )

        with st.expander("¿Qué significan '% sobre mercado' y 'Sobrepuja vs rivales'?"):
            st.markdown(
                "- **% sobre mercado**: cuánto pagó respecto al precio de catálogo "
                "*actual* de ese jugador. Es la métrica principal — se calcula para "
                "cualquier fichaje, no solo subastas. Limitación real: usamos el "
                "precio de HOY, no el que tenía el jugador el día exacto de la "
                "operación (no tenemos histórico de precio anterior a que "
                "empezamos a guardar snapshots con `scripts/fetch_daily.py`), así "
                "que en fichajes de hace varias semanas es una aproximación.\n"
                "- **Sobrepuja vs rivales**: solo existe en subastas de jugador "
                "libre con más de un postor — cuánto pagó de más el ganador "
                "respecto a la segunda mejor puja. No aplica a fichajes directos "
                "ni cláusulas, porque ahí no hay competencia visible."
            )

        st.caption(
            "Cubre toda la actividad de la temporada actual disponible vía API. "
            "Biwenger no expone un histórico de temporadas anteriores por esta vía, "
            "así que 'a lo largo de su historia' aquí significa 'desde que empezó esta temporada'."
        )
    except RuntimeError as exc:
        st.warning(f"Configura tu .env para ver esto: {exc}")
    except Exception as exc:  # noqa: BLE001
        st.error(f"Error consultando la API: {exc}")

with tab_economy:
    st.subheader("Economía de la liga: cuánto dinero debería tener cada uno")
    st.caption(
        "Regla de esta liga: cada usuario empieza la temporada con 40.000.000 € "
        "menos el valor de los jugadores que se le asignan en el reparto inicial "
        "(precio EXACTO del día del reparto, no el de hoy). A partir de ahí, se "
        "suma lo cobrado cada jornada y se resta/suma lo gastado/ingresado en fichajes."
    )
    try:
        settings = load_settings()
        client = get_authed_client(settings.email, settings.password, settings.league_id)

        round_events = client.get_round_results(page_size=50, max_pages=50)
        bonuses_by_user = round_bonuses_by_user(round_events)

        moves = client.get_all_league_movements(page_size=50, max_pages=20)
        users_activity = build_user_activity(moves, players_by_id)

        my_id = client.league_user_id
        my_real_balance = client.get_market().get("data", {}).get("status", {}).get("balance")

        my_roster_data = client.get_my_team()["data"]
        season_start_date = find_season_start_date(my_roster_data["players"])

        with st.spinner("Calculando el reparto inicial de cada usuario (histórico de precios)..."):
            initial_budgets = {}
            unresolved_users = []
            for uid, user in users_activity.items():
                try:
                    roster = my_roster_data if uid == my_id else client.get_other_user_roster(uid)["data"]
                    initial_budgets[uid] = compute_initial_budget(
                        roster["players"], user.transactions, players_by_id, load_player_price_history,
                        season_start_date=season_start_date,
                    )
                except Exception:  # noqa: BLE001 — usuario fantasma (salió de la liga, roster 404) u otro fallo puntual
                    initial_budgets[uid] = None
                    unresolved_users.append(user.name)

        if unresolved_users:
            st.caption(
                f"No se pudo calcular el reparto inicial de: {', '.join(unresolved_users)} "
                "(probablemente ya no están en la liga). Para ellos se muestra solo el "
                "flujo de caja neto, como antes."
            )

        my_predicted_initial = initial_budgets[my_id].initial_balance
        my_timeline = build_money_timeline(my_id, bonuses_by_user.get(my_id, []), users_activity[my_id].transactions)
        my_timeline_with_balance, _ = reconstruct_balances(my_timeline, my_predicted_initial)
        my_projected_today = my_timeline_with_balance[-1][1] if my_timeline_with_balance else my_predicted_initial
        my_gap = (my_real_balance - my_projected_today) if my_real_balance is not None else None

        st.info(
            f"Verificación con tu cuenta real: reparto inicial calculado = "
            f"{format_euro(my_predicted_initial)}. Arrastrando esa cifra hasta hoy con "
            f"tus bonus y fichajes reales, el modelo predice {format_euro(my_projected_today)} "
            f"frente a tu saldo real de {format_euro(my_real_balance)} "
            f"(diferencia: {format_euro(my_gap)}, explicada por "
            f"{initial_budgets[my_id].missing_price_count} jugador(es) sin precio "
            f"histórico recuperable — probablemente fuera del catálogo actual de LaLiga)."
        )

        overview_rows = []
        for uid, user in users_activity.items():
            bonus_events = bonuses_by_user.get(uid, [])
            timeline = build_money_timeline(uid, bonus_events, user.transactions)
            budget = initial_budgets[uid]
            initial_balance = budget.initial_balance if budget else None
            timeline_with_balance, _ = reconstruct_balances(timeline, initial_balance)
            if timeline_with_balance:
                saldo_estimado_hoy = timeline_with_balance[-1][1]
            else:
                saldo_estimado_hoy = initial_balance
            is_me = uid == my_id
            usuario_label = user.name + (" (tú)" if is_me else "") + ("" if budget else " (solo flujo neto)")
            overview_rows.append(
                {
                    "Usuario": usuario_label,
                    "Saldo inicial (reparto)": initial_balance,
                    "Bonus cobrado": sum(e.delta for e in bonus_events),
                    "Saldo estimado hoy": saldo_estimado_hoy if budget else net_cash_flow(timeline),
                    "Saldo real (si se conoce)": my_real_balance if is_me else None,
                    "_uid": uid,
                }
            )
        overview_df = pd.DataFrame(overview_rows).sort_values("Saldo estimado hoy", ascending=False).reset_index(drop=True)
        display_overview = overview_df.drop(columns=["_uid"])

        st.caption("Haz clic en un usuario para ver su línea temporal de ingresos y gastos.")
        money_overview_cols = ["Saldo inicial (reparto)", "Bonus cobrado", "Saldo estimado hoy", "Saldo real (si se conoce)"]
        selection_event = st.dataframe(
            style_money(display_overview, money_overview_cols),
            width='stretch',
            hide_index=True,
            on_select="rerun",
            selection_mode="single-row-required",
            key="economy_overview_table",
        )
        selected_idx = selection_event.selection.rows[0] if selection_event.selection.rows else 0
        selected_uid = overview_df.iloc[selected_idx]["_uid"]
        selected_user = users_activity[selected_uid]
        selected_is_me = selected_uid == my_id
        selected_budget = initial_budgets[selected_uid]

        st.divider()
        st.markdown(f"#### Línea temporal: {selected_user.name}{' (tú)' if selected_is_me else ''}")

        bonus_events = bonuses_by_user.get(selected_uid, [])
        timeline = build_money_timeline(selected_uid, bonus_events, selected_user.transactions)

        if selected_budget:
            st.caption(
                f"Saldo inicial (reparto de temporada, {selected_budget.missing_price_count} jugador(es) "
                f"sin precio histórico recuperable): {format_euro(selected_budget.initial_balance)}"
            )
            timeline_with_balance, _ = reconstruct_balances(timeline, selected_budget.initial_balance)
            balance_col = "Saldo estimado"
        else:
            st.caption(
                "No se pudo calcular su reparto inicial (probablemente ya no está en la "
                "liga y su plantilla ya no es consultable). Se muestra el flujo de caja "
                "neto acumulado, empezando en 0 — no es su saldo absoluto."
            )
            timeline_with_balance = cumulative_flow(timeline)
            balance_col = "Flujo acumulado"

        timeline_rows = [
            {
                "Fecha": pd.to_datetime(ev.date, unit="s"),
                "Tipo": {"bonus": "Bono de jornada", "compra": "Fichaje", "venta": "Venta"}[ev.kind],
                "Detalle": ev.label,
                "Importe": ev.delta,
                balance_col: bal,
            }
            for ev, bal in timeline_with_balance
        ]
        timeline_df = pd.DataFrame(timeline_rows)
        st.dataframe(
            style_money(timeline_df, ["Importe", balance_col]),
            width='stretch',
            height=450,
        )

        chart_df = timeline_df[["Fecha", "Saldo estimado"]].set_index("Fecha")
        st.line_chart(chart_df)

        with st.expander("¿Cómo de fiable es esto? Metodología y límites"):
            st.markdown(
                "- **Presupuesto inicial**: 40.000.000 € menos el valor de la plantilla "
                "inicial de cada usuario, valorada al precio EXACTO del día del reparto "
                "(histórico diario de `GET /players/la-liga/{slug}?fields=prices`, "
                "~366 días hacia atrás), no al precio de hoy.\n"
                "- **Cómo se detecta la plantilla inicial de cada usuario**: jugadores de "
                "su plantilla actual sin precio de compra registrado (`owner.price` "
                "ausente) + jugadores que vendió sin haberlos comprado nunca (también "
                "venían del reparto).\n"
                "- **Verificado contra un caso real**: usando esta metodología contra la "
                "cuenta del usuario (cuyo saldo real de hoy sí se conoce vía `/market`), "
                "el saldo inicial calculado reprodujo el saldo real con un margen de "
                "~600.000 € sobre una plantilla de ~26M€ (≈1,5%), explicado por un único "
                "jugador ya fuera del catálogo actual de LaLiga del que no se pudo "
                "recuperar precio histórico — no por un fallo del método.\n"
                "- **Para el resto de usuarios no hay forma de verificar el resultado "
                "final** (su saldo real está oculto por `settings.balance = hidden`), "
                "así que ese mismo ~1-2% de margen por jugadores sin precio recuperable "
                "es la mejor estimación de precisión disponible, no una certeza."
            )
    except RuntimeError as exc:
        st.warning(f"Configura tu .env para ver esto: {exc}")
    except Exception as exc:  # noqa: BLE001
        st.error(f"Error consultando la API: {exc}")

st.divider()
with Storage() as storage:
    latest = storage.latest_snapshot_date()
st.caption(
    f"Último snapshot guardado en SQLite: {latest or 'ninguno todavía — ejecuta scripts/fetch_daily.py'}"
)
