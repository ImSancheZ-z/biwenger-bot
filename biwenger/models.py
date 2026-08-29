"""Dataclasses para las entidades principales de Biwenger."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

POSITION_NAMES = {
    1: "PT",
    2: "DF",
    3: "MC",
    4: "DL",
    5: "ENT",
}

STATUS_OK = "ok"


@dataclass
class Fixture:
    """Próximo partido de un equipo, con la dificultad del rival."""

    round_id: int
    team_id: int
    rival_team_id: int
    rival_name: str
    is_home: bool
    difficulty: Optional[float]  # 0-100, más alto = más difícil


@dataclass
class Player:
    id: int
    name: str
    slug: str
    team_id: Optional[int]
    team_name: Optional[str]
    position: int
    price: Optional[int]
    fantasy_price: Optional[int]
    status: str
    status_info: Optional[str]
    # La API mezcla enteros con valores como "discarded" o null cuando el
    # jugador no pudo puntuar esa jornada (lesión, sanción...).
    fitness: list = field(default_factory=list)
    points: int = 0
    points_home: int = 0
    points_away: int = 0
    points_last_season: int = 0
    next_fixture: Optional[Fixture] = None

    @property
    def position_name(self) -> str:
        return POSITION_NAMES.get(self.position, "Desconocida")

    @property
    def is_available(self) -> bool:
        return self.status == STATUS_OK

    @property
    def recent_form(self) -> Optional[float]:
        """Media de puntos de las últimas jornadas jugadas (fitness).

        Ignora entradas no numéricas (p.ej. "discarded" o null cuando el
        jugador no pudo puntuar esa jornada).
        """
        numeric = [f for f in self.fitness if isinstance(f, (int, float))]
        if not numeric:
            return None
        return sum(numeric) / len(numeric)

    @property
    def points_per_million(self) -> Optional[float]:
        if not self.price or self.price <= 0:
            return None
        return self.points / (self.price / 1_000_000)


@dataclass
class MarketBid:
    user_id: Optional[int]
    user_name: Optional[str]
    amount: Optional[int]


@dataclass
class MarketMove:
    id: int
    type: str  # "market" | "transfer"
    date: Optional[int]
    player_id: Optional[int]
    player_name: Optional[str]
    amount: Optional[int]
    buyer_name: Optional[str]
    seller_name: Optional[str]
    bids: list[MarketBid] = field(default_factory=list)
