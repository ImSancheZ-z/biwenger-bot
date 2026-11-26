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
import time
from pathlib import Path
from typing import Optional

import pandas as pd
import requests
import streamlit as st

sys.path.insert(0, str(Path(__file__).resolve().parent))

from analysis.bidding import (
    compute_likely_starters,
    compute_top_teams_by_value,
    effective_premium,
    historical_bid_premium,
    price_trend_pct_per_day,
    recommend_bid,
)
from analysis.clauses import find_clause_opportunities, score_opportunities
from analysis.present import (
    availability_label,
    catalog_kpis,
    recent_clause_moves,
    spanish_datetime_label,
    split_clause_window,
    squad_kpis,
    top_daily_risers,
    within_hours,
)
from biwenger.images import player_photo_url, team_shield_url, user_avatar_url
from biwenger.panel_ui import (
    THEME_CSS,
    clause_visual,
    header_html,
    kpi_html,
    note_html,
    render_clause_table,
    render_recent_clauses,
    render_riser_table,
    render_soon_cards,
    section_header,
)
from analysis.economy import (
    REPORTED_BALANCES,
    balance_summary,
    build_money_timeline,
    clause_costs_by_user,
    cumulative_flow,
    net_cash_flow,
    reconstruct_balances,
    round_bonuses_by_user,
)
from analysis.engine import rank_players
from analysis.initial_budget import compute_initial_budget, find_season_start_date
from analysis.scouting import UserActivity, build_user_activity, detect_tendencies, summarize_user
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


def format_pct(value) -> str:
    """12.345 -> '+12.3%', -4.5 -> '-4.5%'. Un solo decimal en todas las
    columnas de porcentaje del dashboard."""
    if value is None or pd.isna(value):
        return "-"
    return f"{value:+.1f}%"


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
    pct_columns: Optional[list[str]] = None,
) -> "pd.io.formats.style.Styler":
    """Versión ampliada de style_money: además de formatear importes en
    euros, puede formatear columnas de variación con signo (+/-), colorear
    en verde/rojo columnas de tendencia (subiendo/bajando), y formatear
    columnas de porcentaje con un solo decimal."""
    money_columns = [c for c in (money_columns or []) if c in df.columns]
    signed_money_columns = [c for c in (signed_money_columns or []) if c in df.columns]
    trend_color_columns = [c for c in (trend_color_columns or []) if c in df.columns]
    pct_columns = [c for c in (pct_columns or []) if c in df.columns]

    fmt = {c: format_euro for c in money_columns}
    fmt.update({c: format_euro_signed for c in signed_money_columns})
    fmt.update({c: format_pct for c in pct_columns})
    styler = df.style.format(fmt)
    if trend_color_columns:
        styler = styler.map(_trend_color, subset=trend_color_columns)
    return styler


IMAGE_COLUMNS = {
    "Foto": st.column_config.ImageColumn("Foto", width="small"),
    "Escudo": st.column_config.ImageColumn("Escudo", width="small"),
    "Avatar": st.column_config.ImageColumn("Avatar", width="small"),
}


def show_html(fragment: str) -> None:
    st.markdown(fragment, unsafe_allow_html=True)


def show_table(
    df: pd.DataFrame,
    money_columns: Optional[list[str]] = None,
    signed_money_columns: Optional[list[str]] = None,
    trend_color_columns: Optional[list[str]] = None,
    pct_columns: Optional[list[str]] = None,
    height: Optional[int] = None,
    **kwargs,
):
    """Tabla de Streamlit con euros formateados y fotos/escudos si existen."""
    styled = style_table(
        df,
        money_columns=money_columns,
        signed_money_columns=signed_money_columns,
        trend_color_columns=trend_color_columns,
        pct_columns=pct_columns,
    )
    config = {name: column for name, column in IMAGE_COLUMNS.items() if name in df.columns}
    kwargs.setdefault("width", "stretch")
    kwargs.setdefault("hide_index", True)
    if height is not None:
        kwargs["height"] = height
    st.dataframe(styled, column_config=config, **kwargs)


@st.cache_data(ttl=600)
def load_competition_data():
    """Cachea la respuesta de la API, no objetos Player de versiones anteriores."""
    client = BiwengerClient(email="", password="")
    return client.get_competition_data()["data"]


def load_players():
    # Streamlit no invalida una función cacheada al cambiar sus dependencias
    # (parse_players/Player). Reconstruir aquí aplica siempre el modelo actual.
    return parse_players(load_competition_data())


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


@st.cache_data(ttl=600)
def load_my_team_response(email: str, password: str, league_id: str | None):
    """Plantilla propia. 10 minutos: los filtros del panel no deben reloguear."""
    return get_authed_client(email, password, league_id).get_my_team()


@st.cache_data(ttl=600)
def load_market_response(email: str, password: str, league_id: str | None):
    return get_authed_client(email, password, league_id).get_market()


@st.cache_data(ttl=600)
def load_standings_response(email: str, password: str, league_id: str | None):
    return get_authed_client(email, password, league_id).get_league_standings()


@st.cache_data(ttl=600)
def load_movements_response(email: str, password: str, league_id: str | None, page_size: int, max_pages: int):
    return get_authed_client(email, password, league_id).get_all_league_movements(
        page_size=page_size, max_pages=max_pages
    )


@st.cache_data(ttl=600)
def load_rosters_response(email: str, password: str, league_id: str | None):
    """Plantillas de la liga, reutilizadas 10 minutos entre pestañas y filtros.

    Un usuario que ya no existe se omite. El fallo de la plantilla propia
    o de la clasificación sí se propaga: sin eso no hay cláusulas que mostrar.
    """
    client = get_authed_client(email, password, league_id)
    standings = load_standings_response(email, password, league_id)
    mine = load_my_team_response(email, password, league_id)
    my_id = client.league_user_id
    rosters = {my_id: mine["data"]}
    for entry in standings.get("data", {}).get("standings", []):
        uid = entry.get("id")
        if uid is None or uid == my_id:
            continue
        try:
            rosters[uid] = client.get_other_user_roster(uid)["data"]
        except Exception:
            continue
    return rosters, my_id, standings


def players_to_dataframe(players) -> pd.DataFrame:
    rows = []
    for p in players:
        fixture = p.next_fixture
        rows.append(
            {
                "Foto": player_photo_url(p.id),
                "Nombre": p.name,
                "Escudo": team_shield_url(p.team_id),
                "Equipo": p.team_name,
                "Posición": p.position_name,
                "Precio": p.price,
                "Variación diaria": p.price_increment,
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


show_html(THEME_CSS)
show_html(header_html("Biwenger Bot", f"Análisis de mercado · {spanish_datetime_label()}"))

try:
    players = load_players()
except requests.RequestException as exc:
    response = getattr(exc, "response", None)
    status = f" (HTTP {response.status_code})" if response is not None else ""
    st.error(getattr(exc, "public_message", f"No se pudo descargar el catálogo de Biwenger{status}."))
    st.info("Las consultas se pausan al recibir HTTP 429 durante al menos 10 minutos, o más si Biwenger lo indica. Vuelve a abrir la app después de ese plazo. Recargar no elimina la pausa y no garantiza que el servidor haya levantado el límite.")
    st.stop()

show_html(kpi_html(catalog_kpis(players)))
show_html(note_html(
    "Fotos y escudos salen del CDN público de Biwenger, sin peticiones extra a la API. "
    "Plantilla, mercado, clasificación y tablón se reutilizan 10 minutos."
))

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

with tab_market:
    st.subheader("Todos los jugadores de LaLiga")
    show_html(section_header("Mayores subidas de hoy"))
    show_html(render_riser_table(top_daily_risers(players)))
    st.caption("Variación diaria oficial del catálogo (priceIncrement), no una media de los últimos días.")
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

    show_table(
        filtered.sort_values("Puntos", ascending=False),
        money_columns=["Precio"],
        signed_money_columns=["Variación diaria"],
        trend_color_columns=["Variación diaria"],
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
                "Foto": player_photo_url(p.id),
                "Nombre": p.name,
                "Escudo": team_shield_url(p.team_id),
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
    show_table(pd.DataFrame(rows), money_columns=["Precio"], height=600)

players_by_id = {p.id: p for p in players}

with tab_team:
    st.subheader("Mi plantilla")
    try:
        settings = load_settings()
        team_resp = load_my_team_response(settings.email, settings.password, settings.league_id)
        team_data = team_resp.get("data", {})

        rows = parse_my_team(team_data, players_by_id)
        team_df = pd.DataFrame(rows)
        team_df.insert(0, "Foto", team_df["id"].map(player_photo_url))
        team_df.insert(2, "Escudo", team_df["id"].map(
            lambda pid: team_shield_url(players_by_id[pid].team_id) if pid in players_by_id else None
        ))
        team_df = team_df.rename(
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
        ).drop(columns=["id", "position", "clause_locked_until"])
        st.caption(f"Alineación: {team_data.get('lineup', {}).get('type', '?')}")
        show_table(team_df, money_columns=["Precio", "Cláusula"], height=450)

        st.divider()
        st.markdown("#### Estado de las cláusulas de mi plantilla")
        st.caption(
            "Fecha y tiempo que faltan para que un rival pueda pagar la cláusula "
            "y llevárselo, y diferencia entre el precio de mercado actual y esa "
            "cláusula. Cuanto mayor sea el beneficio, más caro le sale robártelo "
            "respecto a lo que vale el jugador hoy."
        )
        now_ts = int(time.time())
        clause_rows = []
        for r in rows:
            clause = r.get("clause")
            if not clause:
                continue
            price = r.get("price")
            locked_until = r.get("clause_locked_until")
            days_left = (locked_until - now_ts) / 86400 if locked_until else -999.0
            player = players_by_id.get(r.get("id"))
            trend_abs = player.price_increment if player else None
            clause_rows.append(
                {
                    "Foto": player_photo_url(r.get("id")),
                    "Nombre": r["name"],
                    "Escudo": team_shield_url(player.team_id) if player else None,
                    "Equipo": r["team_name"],
                    "Posición": r["position_name"],
                    "Precio mercado": price,
                    "Cláusula": clause,
                    "Beneficio inmediato": (price - clause) if price else None,
                    "% vs. mercado": round((clause / price - 1) * 100, 1) if price else None,
                    "Variación diaria (€)": trend_abs,
                    "Disponible para rivales": availability_label(locked_until, now_ts),
                    "_dias": days_left,
                }
            )
        if not clause_rows:
            st.info("Ninguno de tus jugadores tiene cláusula asignada.")
        else:
            clause_status_df = pd.DataFrame(clause_rows).sort_values("_dias").drop(columns=["_dias"])
            show_table(
                clause_status_df,
                money_columns=["Precio mercado", "Cláusula"],
                signed_money_columns=["Beneficio inmediato", "Variación diaria (€)"],
                trend_color_columns=["Beneficio inmediato", "Variación diaria (€)"],
                pct_columns=["% vs. mercado"],
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
        market_resp = load_market_response(settings.email, settings.password, settings.league_id)
        market_data = market_resp.get("data", {})

        status = market_data.get("status", {})
        balance = status.get("balance", 0)
        max_bid = status.get("maximumBid", 0)
        show_html(kpi_html([
            ("Tu saldo", format_euro(balance), "disponible ahora"),
            ("Puja máxima", format_euro(max_bid), "tope que impone Biwenger"),
        ]))

        # Para la puja recomendada necesitamos tu plantilla (qué posiciones te
        # faltan) y el histórico real de subastas competidas de tu liga.
        my_team_resp = load_my_team_response(settings.email, settings.password, settings.league_id)
        my_squad_rows = parse_my_team(my_team_resp.get("data", {}), players_by_id)
        position_counts = squad_position_counts(my_squad_rows)

        movements = load_movements_response(settings.email, settings.password, settings.league_id, 50, 5)
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

        now_ts = int(time.time())
        for row in rows:
            trend = None
            player = players_by_id.get(row["id"])
            trend_abs = player.price_increment if player else None
            row["foto"] = player_photo_url(row["id"])
            row["escudo"] = team_shield_url(player.team_id) if player else None
            row["cierra"] = availability_label(row.get("hasta"), now_ts) if row.get("hasta") else "Sin cierre"
            if row["slug"]:
                history = load_player_price_history(row["slug"])
                trend = price_trend_pct_per_day(history)
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

        if not rows:
            st.info("No hay jugadores en venta con ese filtro.")
        else:
            market_df = pd.DataFrame(rows).rename(
            columns={
                "foto": "Foto",
                "name": "Nombre",
                "escudo": "Escudo",
                "team_name": "Equipo",
                "position_name": "Posición",
                "points": "Puntos",
                "price_venta": "Precio de venta",
                "ratio_pts_millon": "Pts/Millón",
                "score": "Score chollo",
                "tendencia": "Tendencia media (%/día)",
                "tendencia_abs": "Variación diaria (€)",
                "excepcional": "Top-5 + titular",
                "vendedor": "Vendedor",
                "cierra": "Cierra",
                "accion": "Acción",
                "puja_recomendada": "Puja recomendada",
                "motivo": "Motivo",
            }
            ).drop(columns=["id", "slug", "position", "is_free_agent", "hasta"])
            preferred = ["Foto", "Nombre", "Escudo", "Equipo", "Posición", "Puntos", "Precio de venta", "Cierra"]
            ordered = [c for c in preferred if c in market_df.columns] + [c for c in market_df.columns if c not in preferred]
            market_df = market_df[ordered]
            st.caption(
                "Ordenado por 'Score chollo'. 'Cierra' es la fecha límite de la puja y el tiempo que falta. "
                "La variación diaria es el último cambio oficial de Biwenger; la tendencia media usa los últimos 7 registros del histórico."
            )
            show_table(
                market_df.sort_values("Score chollo", ascending=False, na_position="last"),
                money_columns=["Precio de venta", "Puja recomendada"],
                signed_money_columns=["Variación diaria (€)"],
                trend_color_columns=["Tendencia media (%/día)", "Variación diaria (€)"],
                pct_columns=["Tendencia media (%/día)"],
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
        with st.spinner("Revisando las plantillas de todos los usuarios de tu liga..."):
            rosters, my_id, standings_resp = load_rosters_response(
                settings.email, settings.password, settings.league_id
            )
        now_ts = int(time.time())
        owner_icons = {
            entry.get("id"): entry.get("icon")
            for entry in standings_resp.get("data", {}).get("standings", [])
        }
        # La vista visual usa solo catálogo + plantillas ya descargadas.
        # El histórico de precios se pide más abajo, y solo para el plazo del análisis.
        window = find_clause_opportunities(rosters, players_by_id, my_id, within_days=2, now_ts=now_ts)
        open_now, upcoming = split_clause_window(window)
        show_html(kpi_html(squad_kpis(rosters, players, len(open_now), len(upcoming))))

        visual_owners = ["Todos"] + sorted({o.owner_name for o in open_now + upcoming})
        visual_positions = ["Todas"] + list(POSITION_NAMES.values())
        col1, col2, col3 = st.columns(3)
        with col1:
            visual_owner = st.selectbox("Propietario", visual_owners, key="clauses_visual_owner")
        with col2:
            visual_pos = st.selectbox("Posición", visual_positions, key="clauses_visual_pos")
        with col3:
            min_millions = st.number_input("Valor mínimo (M)", min_value=0.0, value=0.0, step=0.5, key="clauses_min_m")

        def _visible(opp) -> bool:
            if visual_owner != "Todos" and opp.owner_name != visual_owner:
                return False
            if visual_pos != "Todas" and opp.player.position_name != visual_pos:
                return False
            return (opp.player.price or 0) >= min_millions * 1_000_000

        open_view = [o for o in open_now if _visible(o)]
        show_html(section_header("Cláusulas ejecutables ahora"))
        st.caption(f"{len(open_view)} jugadores con cláusula ejecutable")
        show_html(render_clause_table(
            [clause_visual(o, owner_icons.get(o.owner_id), now_ts) for o in open_view],
            countdown=False,
        ))

        show_html(section_header("Próximas a poder ejecutarse"))
        max_hours = st.slider("Máximo de horas restantes", 1, 48, 48, key="clauses_max_hours")
        soon_view = [o for o in upcoming if _visible(o) and within_hours(o, max_hours)]
        st.caption(f"{len(soon_view)} cláusulas dentro del plazo. La cifra grande es el tiempo que falta.")
        show_html(render_soon_cards([clause_visual(o, owner_icons.get(o.owner_id), now_ts) for o in soon_view]))
        show_html(render_clause_table(
            [clause_visual(o, owner_icons.get(o.owner_id), now_ts) for o in soon_view],
            countdown=True,
        ))

        st.divider()
        show_html(section_header("Análisis: score, arbitraje y combinaciones"))
        within_days = st.slider("Ver cláusulas que se desbloqueen dentro de (días)", 0, 15, 3)

        with st.spinner("Valorando cláusulas con el histórico de precio..."):
            opportunities = find_clause_opportunities(
                rosters, players_by_id, my_id, within_days=within_days, now_ts=now_ts
            )
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
                    "Foto": player_photo_url(o.player.id),
                    "Jugador": o.player.name,
                    "Escudo": team_shield_url(o.player.team_id),
                    "Equipo": o.player.team_name,
                    "Posición": o.player.position_name,
                    "Precio mercado": o.player.price,
                    "Cláusula": o.clause,
                    "Beneficio inmediato": (o.player.price - o.clause) if o.player.price else None,
                    "% vs. mercado": o.vs_market_pct,
                    "Variación diaria (€)": o.trend_abs_per_day,
                    "Tendencia media (%/día)": round(o.trend_pct_per_day, 2) if o.trend_pct_per_day is not None else None,
                    "Disponible": availability_label(o.clause_locked_until, now_ts),
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
                show_table(
                    clauses_df,
                    money_columns=["Precio mercado", "Cláusula"],
                    signed_money_columns=["Variación diaria (€)", "Beneficio inmediato"],
                    trend_color_columns=["Tendencia media (%/día)", "Variación diaria (€)", "Beneficio inmediato"],
                    pct_columns=["% vs. mercado", "Tendencia media (%/día)"],
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
                        "Foto": player_photo_url(o.player.id),
                        "Jugador": o.player.name,
                        "Escudo": team_shield_url(o.player.team_id),
                        "Equipo": o.player.team_name,
                        "Posición": o.player.position_name,
                        "Precio mercado": o.player.price,
                        "Cláusula": o.clause,
                        "Beneficio inmediato": (o.player.price - o.clause) if o.player.price else None,
                        "% vs. mercado": o.vs_market_pct,
                        "Variación diaria (€)": o.trend_abs_per_day,
                        "Tendencia media (%/día)": round(o.trend_pct_per_day, 2),
                        "Disponible": availability_label(o.clause_locked_until, now_ts),
                        "Dueño actual": o.owner_name,
                    }
                    for o in arbitrage_opps
                ]
                arb_df = pd.DataFrame(arb_rows).sort_values("Tendencia media (%/día)", ascending=False)
                show_table(
                    arb_df,
                    money_columns=["Cláusula", "Precio mercado", "Beneficio inmediato"],
                    signed_money_columns=["Variación diaria (€)"],
                    trend_color_columns=["Tendencia media (%/día)", "Variación diaria (€)"],
                    pct_columns=["% vs. mercado", "Tendencia media (%/día)"],
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
                        "Foto": player_photo_url(o.player.id),
                        "Jugador": o.player.name,
                        "Escudo": team_shield_url(o.player.team_id),
                        "Equipo": o.player.team_name,
                        "Posición": o.player.position_name,
                        "Precio mercado": o.player.price,
                        "Cláusula": o.clause,
                        "Beneficio inmediato": (o.player.price - o.clause) if o.player.price else None,
                        "% vs. mercado": o.vs_market_pct,
                        "Variación diaria (€)": o.trend_abs_per_day,
                        "Tendencia media (%/día)": round(o.trend_pct_per_day, 2),
                        "Disponible": availability_label(o.clause_locked_until, now_ts),
                        "Score": o.score,
                        "Dueño actual": o.owner_name,
                    }
                    for o in rising_opps
                ]
                combo_df = pd.DataFrame(combo_rows)
                rank_subida = combo_df["Tendencia media (%/día)"].rank(ascending=False)
                rank_score = combo_df["Score"].rank(ascending=False)
                rank_descuento = combo_df["% vs. mercado"].rank(ascending=True)
                combo_df["Posición combinada"] = (rank_subida + rank_score + rank_descuento).round(1)
                combo_df = combo_df.sort_values("Posición combinada")
                show_table(
                    combo_df,
                    money_columns=["Precio mercado", "Cláusula"],
                    signed_money_columns=["Variación diaria (€)", "Beneficio inmediato"],
                    trend_color_columns=["Tendencia media (%/día)", "Variación diaria (€)", "Beneficio inmediato"],
                    pct_columns=["% vs. mercado", "Tendencia media (%/día)"],
                    height=400,
                )

            st.divider()
            st.markdown("#### Punto de mira: casi rentable y subiendo fuerte")
            st.caption(
                "Jugadores cuya cláusula está ya muy pegada al precio de mercado "
                "(poco 'Beneficio inmediato' si la pagases hoy) pero que sube de "
                "precio rápido cada día: en poco tiempo la cláusula se quedará "
                "claramente por debajo del mercado. Mismo criterio de 'posición "
                "combinada' que la tabla anterior, pero combinando el beneficio "
                "inmediato mínimo con la tendencia máxima."
            )
            tight_margin_opps = [o for o in rising_opps if o.player.price is not None]
            if not tight_margin_opps:
                st.info("Ningún jugador con precio subiendo y datos suficientes con los filtros actuales.")
            else:
                tight_rows = [
                    {
                        "Foto": player_photo_url(o.player.id),
                        "Jugador": o.player.name,
                        "Escudo": team_shield_url(o.player.team_id),
                        "Equipo": o.player.team_name,
                        "Posición": o.player.position_name,
                        "Precio mercado": o.player.price,
                        "Cláusula": o.clause,
                        "Beneficio inmediato": o.player.price - o.clause,
                        "% vs. mercado": o.vs_market_pct,
                        "Variación diaria (€)": o.trend_abs_per_day,
                        "Tendencia media (%/día)": round(o.trend_pct_per_day, 2),
                        "Disponible": availability_label(o.clause_locked_until, now_ts),
                        "Score": o.score,
                        "Dueño actual": o.owner_name,
                    }
                    for o in tight_margin_opps
                ]
                tight_df = pd.DataFrame(tight_rows)
                rank_beneficio = tight_df["Beneficio inmediato"].rank(ascending=True)
                rank_tendencia = tight_df["Tendencia media (%/día)"].rank(ascending=False)
                tight_df["Posición combinada"] = (rank_beneficio + rank_tendencia).round(1)
                tight_df = tight_df.sort_values("Posición combinada")
                show_table(
                    tight_df,
                    money_columns=["Precio mercado", "Cláusula"],
                    signed_money_columns=["Variación diaria (€)", "Beneficio inmediato"],
                    trend_color_columns=["Tendencia media (%/día)", "Variación diaria (€)", "Beneficio inmediato"],
                    pct_columns=["% vs. mercado", "Tendencia media (%/día)"],
                    height=400,
                )

        with st.expander("¿Cómo se calcula esto? Limitaciones"):
            st.markdown(
                "- **Cláusula pagable**: se compara `owner.clauseLockedUntil` (fecha "
                "hasta la que NADIE puede pagarla) con la fecha límite que elijas. "
                "Arriba, la cuenta atrás muestra la fecha en hora de Madrid y el "
                "tiempo que falta. Se excluyen tus propios jugadores — no tiene "
                "sentido pagarte la cláusula a ti mismo.\n"
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
        standings_resp = load_standings_response(settings.email, settings.password, settings.league_id)
        rows = parse_standings(standings_resp.get("data", {}))
        standings_df = pd.DataFrame(rows).sort_values("position")
        standings_df.insert(0, "Avatar", [
            user_avatar_url(icon, uid) for icon, uid in zip(standings_df["icon"], standings_df["user_id"])
        ])
        standings_df = standings_df.drop(columns=["user_id", "icon"]).rename(
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
        show_html(section_header("Valor de las plantillas"))
        chart = standings_df.set_index("Usuario")[["Valor plantilla", "Variación valor"]].apply(
            pd.to_numeric, errors="coerce"
        ).fillna(0)
        left, right = st.columns(2)
        with left:
            st.caption("Valor de plantilla")
            st.bar_chart(chart[["Valor plantilla"]], height=280)
        with right:
            st.caption("Variación de valor")
            st.bar_chart(chart[["Variación valor"]], height=280)
        show_table(
            standings_df,
            money_columns=["Valor plantilla"],
            signed_money_columns=["Variación valor"],
            trend_color_columns=["Variación valor"],
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
        moves = load_movements_response(settings.email, settings.password, settings.league_id, 50, 20)

        rows = parse_movements(moves, players_by_id)
        show_html(section_header("Clausulazos de los últimos 7 días"))
        show_html(render_recent_clauses(recent_clause_moves(rows, int(time.time())), players_by_id))
        st.caption(
            "Usa el tablón que ya carga esta pestaña. No se asume un cupo semanal: "
            "cada liga lo fija aparte y este panel no tiene ese dato confirmado."
        )
        if rows:
            moves_df = pd.DataFrame(rows)
            moves_df["fecha"] = pd.to_datetime(moves_df["date"], unit="s")
            moves_df["Foto"] = moves_df["player_id"].map(
                lambda pid: None if pid is None or (isinstance(pid, float) and pd.isna(pid)) else player_photo_url(pid)
            )
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
            )[["Foto", "Fecha", "Tipo", "Jugador", "Importe", "De", "A", "Pujas perdedoras (perdió la subasta)"]]
            show_table(display_df, money_columns=["Importe"], height=500)
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
        moves = load_movements_response(settings.email, settings.password, settings.league_id, 50, 20)
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
                        f"{stats['vs_market_medio_pct']:+.1f}%" if stats["vs_market_medio_pct"] is not None else "-"
                    ),
                    "Sobrepuja media en subastas": (
                        f"{(stats['overpay_medio'] - 1) * 100:.1f}%" if stats["overpay_medio"] else "-"
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
                "% sobre mercado": f"{t.vs_market_pct:+.1f}%" if t.vs_market_pct is not None else "-",
                "Sobrepuja vs rivales": f"{(t.overpay_ratio - 1) * 100:+.1f}%" if t.overpay_ratio else "-",
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
    st.subheader("Economía de la liga")
    st.caption("Saldo estimado = 40.000.000 € − plantilla inicial + bonus + ventas − compras − blindajes.")
    if st.button("Actualizar economía", key="refresh_economy"):
        load_player_price_history.clear()
    try:
        settings = load_settings()
        client = get_authed_client(settings.email, settings.password, settings.league_id)
        round_events = client.get_round_results()
        bonuses_by_user = round_bonuses_by_user(round_events)
        moves = client.get_all_league_movements()
        costs_by_user = clause_costs_by_user(moves, players_by_id)
        users_activity = build_user_activity(moves, players_by_id)
        for entry in client.get_league_standings().get("data", {}).get("standings", []):
            users_activity.setdefault(entry["id"], UserActivity(entry["id"], entry["name"]))
        my_id = client.league_user_id
        my_real_balance = client.get_market().get("data", {}).get("status", {}).get("balance")
        users_activity.setdefault(my_id, UserActivity(my_id, "Mi cuenta"))
        rosters = {}
        roster_errors = {}
        for uid in users_activity:
            try:
                response = client.get_my_team() if uid == my_id else client.get_other_user_roster(uid)
                rosters[uid] = response["data"]["players"]
            except Exception as exc:
                roster_errors[uid] = str(exc)
        # No depender de que nuestra cuenta conserve jugadores del reparto.
        start_dates = [find_season_start_date(roster) for roster in rosters.values()]
        season_start_date = min((date for date in start_dates if date is not None), default=None)
        initial_budgets = {}
        timelines = {}
        rows = []
        with st.spinner("Calculando saldos e histórico de precios..."):
            for uid, user in users_activity.items():
                budget = None
                if uid in rosters and season_start_date is not None:
                    try:
                        budget = compute_initial_budget(
                            rosters[uid], user.transactions, players_by_id,
                            load_player_price_history, season_start_date=season_start_date,
                        )
                    except Exception as exc:
                        roster_errors[uid] = str(exc)
                initial_budgets[uid] = budget
                timeline = build_money_timeline(uid, bonuses_by_user.get(uid, []), user.transactions, costs_by_user.get(uid, []))
                timelines[uid] = timeline
                initial = budget.initial_balance if budget else None
                estimated = initial + net_cash_flow(timeline) if initial is not None else None
                real = my_real_balance if uid == my_id else None
                rows.append({
                    "Usuario": user.name + (" (tú)" if uid == my_id else ""),
                    "Saldo inicial (reparto)": initial,
                    "Bonus": sum(e.delta for e in timeline if e.kind == "bonus"),
                    "Ventas": sum(e.delta for e in timeline if e.kind == "venta"),
                    "Compras": -sum(e.delta for e in timeline if e.kind == "compra"),
                    "Blindajes": -sum(e.delta for e in timeline if e.kind == "blindaje"),
                    "Flujo neto": net_cash_flow(timeline),
                    "Saldo estimado hoy": estimated,
                    **balance_summary(estimated, real, REPORTED_BALANCES.get(uid)),
                    "Estado": (f"Provisional: {budget.missing_price_count} precios ausentes" if budget.missing_price_count else "Estimación") if budget else "Sin reparto calculable",
                    "_uid": uid,
                })
        overview_df = pd.DataFrame(rows).sort_values("Saldo actual", ascending=False, na_position="last").reset_index(drop=True)
        first_cols = ["Usuario", "Saldo actual", "Tipo de saldo", "Saldo confirmado", "Fuente de confirmación"]
        overview_df = overview_df[first_cols + [c for c in overview_df if c not in first_cols]]
        st.caption("Saldo actual muestra el real consultado cuando está disponible y el calculado para los demás. Las confirmaciones comunicadas conservan su fecha. El margen de ±200.000 € sirve para comparar, no garantiza la precisión de todos los saldos. Sin reparto calculable, solo se muestra el flujo neto.")
        st.caption(f"Consulta actualizada: {pd.Timestamp.now(tz='Europe/Madrid').strftime('%d/%m/%Y %H:%M:%S')}")
        money_cols = ["Saldo actual", "Saldo confirmado", "Saldo inicial (reparto)", "Bonus", "Ventas", "Compras", "Blindajes", "Flujo neto", "Saldo estimado hoy", "Diferencia (real − estimado)"]
        selection = st.dataframe(style_money(overview_df.drop(columns="_uid"), money_cols), width="stretch", hide_index=True,
            on_select="rerun", selection_mode="single-row-required", key="economy_overview_table")
        selected_idx = selection.selection.rows[0] if selection.selection.rows else 0
        selected_idx = min(selected_idx, len(overview_df) - 1)
        row = overview_df.iloc[selected_idx]
        uid = int(row["_uid"])
        budget = initial_budgets[uid]
        timeline = timelines[uid]
        st.divider()
        st.markdown(f"#### Cálculo de {users_activity[uid].name}")
        st.write(f"{format_euro(row['Saldo inicial (reparto)'])} + {format_euro(row['Bonus'])} + {format_euro(row['Ventas'])} − {format_euro(row['Compras'])} − {format_euro(row['Blindajes'])} = {format_euro(row['Saldo estimado hoy'])}")
        st.write(f"Saldo actual: {format_euro(row['Saldo actual'])} ({row['Tipo de saldo']}).")
        if pd.notna(row['Saldo confirmado']):
            st.info(f"Saldo confirmado: {format_euro(row['Saldo confirmado'])}. {row['Fuente de confirmación']}. Diferencia frente al cálculo: {format_euro(row['Diferencia (real − estimado)'])}. {row['Comparación ±200.000 €']}.")
        if budget and budget.missing_price_count:
            st.warning("Faltan precios del reparto: la estimación queda sobrevalorada por su valor desconocido. No puede darse por conciliada.")
        if uid in roster_errors:
            st.warning(f"No se ha podido completar el cálculo: {roster_errors[uid]}")
        if season_start_date is None:
            st.warning("No se pudo detectar la fecha del reparto inicial en las plantillas consultadas.")
        if budget:
            st.caption(f"Reparto detectado: {pd.to_datetime(budget.season_start_date, unit='s').strftime('%d/%m/%Y')}. Valor recuperado: {format_euro(budget.total_squad_value)}.")
            with st.expander("Jugadores y precios del reparto inicial"):
                squad_df = pd.DataFrame([{"Jugador": p.name, "Precio inicial": p.price_on_start_date} for p in budget.squad], columns=["Jugador", "Precio inicial"])
                st.dataframe(style_money(squad_df, ["Precio inicial"]), hide_index=True)
            balances, _ = reconstruct_balances(timeline, budget.initial_balance)
            balance_col = "Saldo estimado"
        else:
            balances = cumulative_flow(timeline)
            balance_col = "Flujo acumulado"
        timeline_df = pd.DataFrame([{"Fecha": pd.to_datetime(e.date, unit="s"), "Tipo": e.kind, "Detalle": e.label, "Importe": e.delta, balance_col: value} for e, value in balances], columns=["Fecha", "Tipo", "Detalle", "Importe", balance_col])
        if timeline_df.empty:
            st.info("Sin movimientos registrados.")
        else:
            st.dataframe(style_money(timeline_df, ["Importe", balance_col]), width="stretch", height=450)
            st.line_chart(timeline_df.set_index("Fecha")[[balance_col]])
        with st.expander("Metodología y límites"):
            st.write("Se parte de un presupuesto de 40 millones y se resta el valor histórico del reparto. Las ventas anteriores a cualquier compra también identifican jugadores iniciales, aunque luego se recompren. Se añaden bonus y ventas y se descuentan compras y subidas de cláusula. Los precios ausentes, movimientos no recogidos o ajustes del administrador pueden producir diferencias. Una comprobación histórica no garantiza un margen de error fijo para todos los usuarios.")
    except RuntimeError as exc:
        st.warning(str(exc))
    except Exception as exc:
        st.error(f"No se pudo actualizar la economía: {exc}")

st.divider()
with Storage() as storage:
    latest = storage.latest_snapshot_date()
st.caption(
    f"Último snapshot guardado en SQLite: {latest or 'ninguno todavía — ejecuta scripts/fetch_daily.py'}"
)
