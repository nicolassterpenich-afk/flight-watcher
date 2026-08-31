"""Modèles de données du surveillant de prix."""

from __future__ import annotations

from dataclasses import dataclass, field, asdict
from typing import Any, Optional

from .timeutil import iso


@dataclass
class Passengers:
    adults: int = 1
    children: int = 0
    infants_in_seat: int = 0
    infants_on_lap: int = 0

    @property
    def total(self) -> int:
        return self.adults + self.children + self.infants_in_seat + self.infants_on_lap


@dataclass
class Watch:
    """Une surveillance : un trajet + un seuil de prix."""

    id: str
    origins: list[str]
    destinations: list[str]
    depart: str                      # YYYY-MM-DD
    ret: Optional[str] = None        # YYYY-MM-DD -> aller-retour si présent
    label: str = ""
    threshold: Optional[float] = None
    currency: str = "EUR"
    seat: str = "economy"
    max_stops: Optional[int] = None
    flex_days: int = 0               # ± N jours autour de l'aller
    flex_days_ret: Optional[int] = None   # ± N jours autour du retour ; None = comme l'aller
    passengers: Passengers = field(default_factory=Passengers)
    providers: list[str] = field(default_factory=lambda: ["google_flights"])
    enabled: bool = True
    alert_on_drop: bool = True       # alerte aussi sur chute inhabituelle
    notes: str = ""

    @property
    def is_round_trip(self) -> bool:
        return bool(self.ret)

    def display(self) -> str:
        if self.label:
            return self.label
        route = f"{'/'.join(self.origins)} → {'/'.join(self.destinations)}"
        dates = self.depart + (f" ⇄ {self.ret}" if self.ret else "")
        return f"{route} {dates}"


@dataclass
class Quote:
    """Un prix relevé pour une combinaison précise."""

    watch_id: str
    provider: str
    origin: str
    destination: str
    depart: str
    ret: Optional[str]
    price: float
    currency: str
    airlines: list[str] = field(default_factory=list)
    stops: Optional[int] = None
    duration_min: Optional[int] = None
    depart_time: Optional[str] = None
    arrival_time: Optional[str] = None
    booking_url: Optional[str] = None
    checked_at: str = field(default_factory=iso)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    def route(self) -> str:
        base = f"{self.origin}→{self.destination}"
        return base + (" A/R" if self.ret else "")


@dataclass
class WatchResult:
    watch: Watch
    quotes: list[Quote]
    errors: list[str] = field(default_factory=list)

    @property
    def best(self) -> Optional[Quote]:
        valid = [q for q in self.quotes if q.price and q.price > 0]
        return min(valid, key=lambda q: q.price) if valid else None
