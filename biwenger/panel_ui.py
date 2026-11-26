"""HTML del panel: el mismo lenguaje visual del dashboard de referencia
(tarjetas, tipografía, tablas con foto) aplicado a los datos de Jose.
"""
from __future__ import annotations

import html
from typing import Optional

from analysis.present import format_millions
from biwenger.images import player_photo_url, team_shield_url, user_avatar_url

THEME_CSS = """
<style>
@import url('https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700&family=Barlow+Condensed:wght@600;700&display=swap');

html, body, [class*="css"] { font-family: 'Inter', sans-serif; }
[data-testid="stAppViewContainer"],
[data-testid="stAppViewContainer"] > section,
[data-testid="stMain"] { background-color: #F0F2F5 !important; }
[data-testid="stHeader"] { background: transparent !important; }
.block-container { background: #F0F2F5 !important; padding-top: 1.4rem; }

.main-header {
    background: linear-gradient(135deg, #eef2ff, #ffffff);
    border: 1px solid #DDE1E9;
    border-radius: 16px;
    padding: 28px 36px;
    margin-bottom: 18px;
    box-shadow: 0 1px 4px rgba(0,0,0,0.06);
}
.main-header h1 {
    font-family: 'Barlow Condensed', sans-serif;
    font-size: 2.4rem;
    font-weight: 700;
    color: #1A2232;
    margin: 0;
    letter-spacing: 0.5px;
}
.main-header .subtitle { color: #6B7A99; font-size: 0.9rem; margin-top: 4px; }

.kpi-row { display: flex; gap: 14px; margin-bottom: 8px; flex-wrap: wrap; }
.kpi-card {
    background: #FFFFFF;
    border: 1px solid #DDE1E9;
    border-radius: 12px;
    padding: 18px 22px;
    flex: 1;
    min-width: 150px;
    box-shadow: 0 1px 2px rgba(0,0,0,0.04);
}
.kpi-card .kpi-label {
    font-size: 0.72rem; font-weight: 600; letter-spacing: 1px;
    text-transform: uppercase; color: #6B7A99; margin-bottom: 6px;
}
.kpi-card .kpi-value {
    font-family: 'Barlow Condensed', sans-serif;
    font-size: 1.9rem; font-weight: 700; color: #1A2232; line-height: 1;
}
.kpi-card .kpi-sub { font-size: 0.78rem; color: #6B7A99; margin-top: 4px; }

.stTabs [data-baseweb="tab-list"],
.stTabs [role="tablist"] {
    gap: 2px; background: #FFFFFF; border-radius: 10px; padding: 5px;
    border: 1px solid #DDE1E9; margin-bottom: 4px;
}
.stTabs [data-baseweb="tab"],
.stTabs [data-testid="stTab"] {
    border-radius: 7px; padding: 7px 16px; font-weight: 600; font-size: 0.82rem;
    color: #6B7A99; background: transparent;
}
.stTabs [aria-selected="true"] {
    background: rgba(59,123,245,0.10) !important;
    color: #3B7BF5 !important;
    font-weight: 700 !important;
}
.stTabs [data-baseweb="tab-highlight"],
.stTabs [data-baseweb="tab-border"] { display: none !important; }

[data-testid="stHeading"] h1,
[data-testid="stHeading"] h2,
[data-testid="stHeading"] h3 {
    font-family: 'Barlow Condensed', sans-serif !important;
    color: #1A2232 !important;
    letter-spacing: 0.3px;
}
[data-testid="stDataFrame"] {
    background: #fff; border: 1px solid #DDE1E9; border-radius: 12px; overflow: hidden;
}

.section-header {
    font-family: 'Barlow Condensed', sans-serif;
    font-size: 1.45rem; font-weight: 700; color: #1A2232;
    border-left: 3px solid #3B7BF5; padding-left: 12px; margin: 8px 0 12px 0;
}
.refresh-info {
    background: #E8EBF0; border: 1px solid #DDE1E9; border-radius: 8px;
    padding: 8px 14px; font-size: 0.78rem; color: #3B7BF5; margin: 4px 0 14px;
}

.table-wrap {
    background: #fff; border: 1px solid #DDE1E9; border-radius: 12px;
    overflow: auto; margin-bottom: 8px;
}
.player-table { width: 100%; border-collapse: collapse; font-size: 0.85rem; background: #FFFFFF; }
.player-table th {
    background: #E8EBF0; color: #6B7A99; font-size: 0.7rem; font-weight: 700;
    letter-spacing: 1px; text-transform: uppercase; padding: 10px 14px;
    text-align: left; border-bottom: 1px solid #DDE1E9;
}
.player-table td {
    padding: 10px 14px; border-bottom: 1px solid #DDE1E9;
    vertical-align: middle; color: #1A2232;
}
.player-table tr:hover td { background: #F7F8FB; }
.cell-primary { color: #1A2232; font-weight: 600; }
.cell-muted { color: #6B7A99; font-size: 0.82rem; }
.cell-money { color: #3B7BF5; font-weight: 600; }
.cell-date { color: #6B7A99; font-size: 0.78rem; }
.badge {
    display: inline-block; padding: 2px 10px; border-radius: 20px;
    font-size: 0.72rem; font-weight: 600; letter-spacing: 0.4px;
}
.pos-PT, .pos-POR { background:#dbeafe; color:#1d4ed8; }
.pos-DF, .pos-DEF { background:#d1fae5; color:#047857; }
.pos-MC, .pos-MED { background:#ede9fe; color:#6d28d9; }
.pos-DL, .pos-DEL { background:#fee2e2; color:#b91c1c; }
.pos-ENT { background:#E8EBF0; color:#6B7A99; }
.var-pos { color:#059669; font-weight:600; }
.var-neg { color:#dc2626; font-weight:600; }
.var-neu { color:#6B7A99; }
img.player-photo {
    width: 40px; height: 40px; border-radius: 50%; object-fit: cover; background: #E8EBF0;
}
img.player-photo.lg { width: 56px; height: 56px; }
img.shield { width: 18px; height: 18px; object-fit: contain; vertical-align: middle; margin-right: 6px; }
img.owner-avatar {
    width: 22px; height: 22px; border-radius: 50%; object-fit: cover;
    vertical-align: middle; margin-right: 6px;
}
.soon-grid { display: flex; flex-wrap: wrap; gap: 12px; margin: 4px 0 16px; }
.soon-card {
    background: #fff; border: 1px solid #DDE1E9; border-radius: 12px;
    padding: 14px 16px; display: flex; gap: 12px; align-items: center;
    flex: 1; min-width: 280px; box-shadow: 0 1px 2px rgba(0,0,0,0.04);
}
.soon-card .count {
    margin-left: auto; text-align: right;
    font-family: 'Barlow Condensed', sans-serif;
    font-size: 1.8rem; font-weight: 700; color: #3B7BF5; line-height: 1;
}
.count-pill {
    font-family: 'Barlow Condensed', sans-serif;
    font-size: 1.2rem; font-weight: 700; color: #3B7BF5;
}
@media (max-width: 768px) {
    .main-header { padding: 18px !important; }
    .main-header h1 { font-size: 1.85rem !important; }
    .kpi-card { min-width: calc(50% - 8px); }
    .player-table { min-width: 760px; }
}
</style>
"""

_POS_CLASS = {"PT": "pos-PT", "DF": "pos-DF", "MC": "pos-MC", "DL": "pos-DL", "ENT": "pos-ENT"}


def _esc(value) -> str:
    if value is None or value == "":
        return "—"
    return html.escape(str(value))


def _attr(value) -> str:
    return html.escape(str(value), quote=True)


def _euro_dots(value: int) -> str:
    return f"{abs(int(value)):,.0f} €".replace(",", ".")


def variation_html(value) -> str:
    if value is None:
        return '<span class="var-neu">—</span>'
    if value > 0:
        return f'<span class="var-pos">▲ +{_euro_dots(value)}</span>'
    if value < 0:
        return f'<span class="var-neg">▼ -{_euro_dots(value)}</span>'
    return '<span class="var-neu">— 0 €</span>'


def position_badge(position: Optional[str]) -> str:
    label = position or "?"
    css = _POS_CLASS.get(label, "pos-ENT")
    return f'<span class="badge {css}">{_esc(label)}</span>'


def photo_img(url: Optional[str], alt: str = "", large: bool = False) -> str:
    if not url:
        return ""
    css = "player-photo lg" if large else "player-photo"
    return f'<img class="{css}" src="{_attr(url)}" alt="{_attr(alt)}">'


def shield_name(name, shield_url: Optional[str]) -> str:
    image = f'<img class="shield" src="{_attr(shield_url)}" alt="">' if shield_url else ""
    return f"{image}<span class=\"cell-muted\">{_esc(name)}</span>"


def owner_name(name, avatar_url: Optional[str]) -> str:
    image = f'<img class="owner-avatar" src="{_attr(avatar_url)}" alt="">' if avatar_url else ""
    return f"{image}<span class=\"cell-muted\">{_esc(name)}</span>"


def header_html(title: str, subtitle: str) -> str:
    return (
        '<div class="main-header"><h1>'
        + _esc(title)
        + '</h1><div class="subtitle">'
        + _esc(subtitle)
        + "</div></div>"
    )


def kpi_html(items: list[tuple[str, str, str]]) -> str:
    cards = []
    for label, value, sub in items:
        cards.append(
            '<div class="kpi-card"><div class="kpi-label">'
            + _esc(label)
            + '</div><div class="kpi-value">'
            + _esc(value)
            + '</div><div class="kpi-sub">'
            + _esc(sub)
            + "</div></div>"
        )
    return '<div class="kpi-row">' + "".join(cards) + "</div>"


def section_header(title: str) -> str:
    return f'<div class="section-header">{_esc(title)}</div>'


def note_html(text: str) -> str:
    return f'<div class="refresh-info">{_esc(text)}</div>'


def _table(headers: list[str], rows: list[list[str]]) -> str:
    head = "".join("<th></th>" if header == "" else f"<th>{_esc(header)}</th>" for header in headers)
    body = "".join("<tr>" + "".join(f"<td>{cell}</td>" for cell in row) + "</tr>" for row in rows)
    return f'<div class="table-wrap"><table class="player-table"><thead><tr>{head}</tr></thead><tbody>{body}</tbody></table></div>'


def render_soon_cards(items: list[dict]) -> str:
    if not items:
        return '<p class="cell-muted">Ninguna cláusula de tus rivales se desbloquea en ese plazo.</p>'
    cards = []
    for item in items:
        cards.append(
            '<div class="soon-card">'
            + photo_img(item.get("photo"), item.get("name") or "", large=True)
            + "<div><div>"
            + f'<strong class="cell-primary">{_esc(item.get("name"))}</strong> '
            + position_badge(item.get("position"))
            + "</div><div class=\"cell-muted\">"
            + shield_name(item.get("team"), item.get("shield"))
            + " · "
            + _esc(item.get("owner"))
            + '</div><div class="cell-date">Desbloqueo '
            + _esc(item.get("unlock"))
            + " · cláusula "
            + _esc(item.get("clause"))
            + "</div></div>"
            + f'<div class="count">{_esc(item.get("remaining"))}</div></div>'
        )
    return '<div class="soon-grid">' + "".join(cards) + "</div>"


def render_clause_table(items: list[dict], *, countdown: bool) -> str:
    if not items:
        message = (
            "Ninguna cláusula se desbloquea en ese plazo."
            if countdown
            else "Ninguna cláusula rival está ejecutable con estos filtros."
        )
        return f'<p class="cell-muted">{message}</p>'
    headers = ["", "Jugador", "Pos", "Equipo", "Propietario", "Cláusula", "Valor", "Variación"]
    if countdown:
        headers += ["Queda", "Fecha"]
    rows = []
    for item in items:
        row = [
            photo_img(item.get("photo"), item.get("name") or ""),
            f'<strong class="cell-primary">{_esc(item.get("name"))}</strong>',
            position_badge(item.get("position")),
            shield_name(item.get("team"), item.get("shield")),
            owner_name(item.get("owner"), item.get("owner_icon")),
            f'<span class="cell-money">{_esc(item.get("clause"))}</span>',
            _esc(item.get("price")),
            variation_html(item.get("variation")),
        ]
        if countdown:
            row.append(f'<span class="count-pill">{_esc(item.get("remaining"))}</span>')
            row.append(f'<span class="cell-date">{_esc(item.get("unlock"))}</span>')
        rows.append(row)
    return _table(headers, rows)


def render_riser_table(players) -> str:
    if not players:
        return '<p class="cell-muted">Hoy ningún jugador del catálogo sube de precio.</p>'
    rows = []
    for index, player in enumerate(players, start=1):
        rows.append([
            f'<span class="cell-muted">#{index}</span>',
            photo_img(player_photo_url(player.id), player.name),
            f'<strong class="cell-primary">{_esc(player.name)}</strong>',
            position_badge(player.position_name),
            shield_name(player.team_name, team_shield_url(player.team_id)),
            _esc(format_millions(player.price)),
            variation_html(player.price_increment),
        ])
    return _table(["#", "", "Jugador", "Pos", "Equipo", "Valor", "Variación hoy"], rows)


def render_recent_clauses(moves: list[dict], players_by_id: dict) -> str:
    if not moves:
        return '<p class="cell-muted">No hay clausulazos en los últimos 7 días dentro del tablón cargado.</p>'
    rows = []
    for move in moves:
        player = players_by_id.get(move.get("player_id"))
        photo = photo_img(player_photo_url(player.id), player.name) if player else ""
        name = player.name if player else move.get("jugador")
        when = unlock_label_from_ts(move.get("date"))
        rows.append([
            f'<span class="cell-date">{_esc(when)}</span>',
            photo,
            f'<strong class="cell-primary">{_esc(name)}</strong>',
            _esc(move.get("de")),
            _esc(move.get("a")),
            f'<span class="cell-money">{_esc(format_millions(move.get("importe")))}</span>',
        ])
    return _table(["Fecha", "", "Jugador", "De", "A", "Importe"], rows)


def unlock_label_from_ts(timestamp) -> str:
    from analysis.present import unlock_datetime_label

    return unlock_datetime_label(timestamp)


def clause_visual(opportunity, owner_icon, now_ts: int) -> dict:
    from analysis.present import clause_countdown

    player = opportunity.player
    timing = clause_countdown(opportunity.clause_locked_until, now_ts)
    return {
        "photo": player_photo_url(player.id),
        "shield": team_shield_url(player.team_id),
        "name": player.name,
        "position": player.position_name,
        "team": player.team_name,
        "owner": opportunity.owner_name,
        "owner_icon": user_avatar_url(owner_icon, opportunity.owner_id),
        "clause": format_millions(opportunity.clause),
        "price": format_millions(player.price),
        "variation": player.price_increment,
        "remaining": timing["remaining"],
        "unlock": timing["unlock"],
    }
