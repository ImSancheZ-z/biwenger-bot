"""Motor de recomendación v1: puntúa jugadores para detectar "chollos".

Heurística (deliberadamente simple, iterable):

    score = puntos_por_millon
          + FORM_WEIGHT * forma_reciente
          - DIFFICULTY_WEIGHT * dificultad_normalizada(-1..1)

- puntos_por_millon: puntos totales de la temporada / (precio en millones).
  Es la señal principal: cuánto rendimiento sacas por cada euro invertido.
- forma_reciente: media de puntos de las últimas jornadas jugadas (fitness).
  Bonus para jugadores que están en racha ahora mismo, no solo en la media
  histórica de la temporada.
- dificultad_normalizada: (difficulty.rating - 50) / 50, así un rival de
  dificultad 50 (media) no penaliza ni bonifica, un rival muy difícil (100)
  penaliza al máximo y un rival muy fácil (0) da un empujón extra.

Jugadores no disponibles (lesionados, sancionados, descartados) se excluyen
por defecto de las recomendaciones porque, aunque tengan buen ratio
histórico, no van a puntuar la próxima jornada.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from biwenger.models import Player

FORM_WEIGHT = 1.5
DIFFICULTY_WEIGHT = 3.0

# Estados en los que el jugador puede jugar la próxima jornada.
AVAILABLE_STATUSES = {"ok", "doubt"}


@dataclass
class ScoredPlayer:
    player: Player
    score: float
    points_per_million: Optional[float]
    form_component: float
    difficulty_component: float


def _difficulty_normalized(difficulty: Optional[float]) -> float:
    """Mapea 0-100 a -1..1. None (sin próximo partido conocido) = neutro."""
    if difficulty is None:
        return 0.0
    return (difficulty - 50) / 50


def score_at_price(player: Player, price: Optional[int]) -> tuple[float, Optional[float], float, float]:
    """Aplica la heurística a un precio concreto (el de catálogo o el de una
    oferta de mercado). Devuelve (score, puntos_por_millon, form_component,
    difficulty_component).
    """
    ppm = (player.points / (price / 1_000_000)) if price else None
    form = player.recent_form or 0.0
    difficulty = player.next_fixture.difficulty if player.next_fixture else None

    form_component = FORM_WEIGHT * form
    difficulty_component = -DIFFICULTY_WEIGHT * _difficulty_normalized(difficulty)

    score = (ppm or 0.0) + form_component + difficulty_component
    return score, ppm, form_component, difficulty_component


def score_player(player: Player) -> ScoredPlayer:
    score, ppm, form_component, difficulty_component = score_at_price(player, player.price)
    return ScoredPlayer(
        player=player,
        score=score,
        points_per_million=ppm,
        form_component=form_component,
        difficulty_component=difficulty_component,
    )


def rank_players(
    players: list[Player],
    position: Optional[int] = None,
    only_available: bool = True,
    min_price: int = 0,
    top_n: Optional[int] = None,
) -> list[ScoredPlayer]:
    """Ordena jugadores de mayor a menor score (mejores "chollos" primero)."""
    candidates = players
    if position is not None:
        candidates = [p for p in candidates if p.position == position]
    if only_available:
        candidates = [p for p in candidates if p.status in AVAILABLE_STATUSES]
    if min_price:
        candidates = [p for p in candidates if (p.price or 0) >= min_price]

    scored = [score_player(p) for p in candidates if p.price]
    scored.sort(key=lambda sp: sp.score, reverse=True)
    return scored[:top_n] if top_n else scored
