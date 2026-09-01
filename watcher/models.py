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
    nights_min: Optional[int] = None      # séjour souple : « 7 à 10 nuits »
    nights_max: Optional[int] = None      # le retour est alors calculé, pas fixé
    passengers: Passengers = field(default_factory=Passengers)
    providers: list[str] = field(default_factory=lambda: ["google_flights"])
    enabled: bool = True
    alert_on_drop: bool = True       # alerte aussi sur chute inhabituelle
    destinataires: list[str] = field(default_factory=list)   # Telegram ou courriel ; vide = le propriétaire
    notes: str = ""

    @property
    def is_round_trip(self) -> bool:
        # Un séjour souple n'a pas de date de retour fixe, mais c'est bien un
        # aller-retour : elle est calculée depuis la durée.
        return bool(self.ret) or self.nights_min is not None

    @property
    def nights_range(self) -> Optional[range]:
        if self.nights_min is None:
            return None
        return range(self.nights_min, (self.nights_max if self.nights_max is not None else self.nights_min) + 1)

    def display(self) -> str:
        if self.label:
            return self.label
        route = f"{'/'.join(self.origins)} → {'/'.join(self.destinations)}"
        if self.nights_min is not None:
            nuits = (f"{self.nights_min} nuits" if self.nights_max in (None, self.nights_min)
                     else f"{self.nights_min}-{self.nights_max} nuits")
            dates = f"{self.depart} + {nuits}"
        else:
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
    # {fournisseur: {"attempts": n, "failures": n, "sample": "…"}}
    provider_stats: dict[str, dict[str, Any]] = field(default_factory=dict)

    @property
    def best(self) -> Optional[Quote]:
        valid = [q for q in self.quotes if q.price and q.price > 0]
        return min(valid, key=lambda q: q.price) if valid else None
