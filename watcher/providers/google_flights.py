"""Fournisseur Google Flights.

S'appuie sur `faster-flights`, qui interroge l'endpoint interne de Google
Flights (protobuf) — mêmes prix que ce que tu vois sur le site, sans clé API.
"""

from __future__ import annotations

import logging
import random
import time
from typing import Any

from ..models import Quote, Watch
from .base import Provider, ProviderError

log = logging.getLogger(__name__)

_SEATS = {"economy", "premium-economy", "business", "first"}


def _fmt_dt(sd: Any) -> str | None:
    """SimpleDatetime -> '2026-11-15 10:35'."""
    if sd is None:
        return None
    try:
        y, m, d = sd.date
        hh, mm = sd.time
        return f"{y:04d}-{m:02d}-{d:02d} {hh:02d}:{mm:02d}"
    except Exception:
        return None


def _outbound_leg(segments: list[Any], destination: str) -> list[Any]:
    """Isole le trajet aller.

    Sur un aller-retour, `flights` contient les segments des deux sens à la
    suite : l'aller s'arrête au premier segment qui atterrit à destination.
    """
    if not segments:
        return []
    for i, seg in enumerate(segments):
        if getattr(getattr(seg, "to_airport", None), "code", "") == destination:
            return segments[: i + 1]
    # Destination jamais rencontrée (code de ville vs aéroport) : on retombe
    # sur une découpe en deux moitiés pour un A/R, sinon tout le trajet.
    return segments[: max(1, len(segments) // 2)] if len(segments) > 1 else segments


class GoogleFlightsProvider(Provider):
    name = "google_flights"

    def __init__(self, max_results: int = 8, retries: int = 3):
        self.max_results = max_results
        self.retries = retries

    def _build(self, watch: Watch, origin: str, destination: str,
               depart: str, ret: str | None):
        from fast_flights import FlightQuery, Passengers as FFPassengers, create_query

        legs = [FlightQuery(date=depart, from_airport=origin, to_airport=destination,
                            max_stops=watch.max_stops)]
        if ret:
            legs.append(FlightQuery(date=ret, from_airport=destination, to_airport=origin,
                                    max_stops=watch.max_stops))

        seat = watch.seat if watch.seat in _SEATS else "economy"
        return create_query(
            flights=legs,
            seat=seat,                                     # type: ignore[arg-type]
            trip="round-trip" if ret else "one-way",       # type: ignore[arg-type]
            passengers=FFPassengers(
                adults=max(1, watch.passengers.adults),
                children=watch.passengers.children,
                infants_in_seat=watch.passengers.infants_in_seat,
                infants_on_lap=watch.passengers.infants_on_lap,
            ),
            language="fr",
            currency=watch.currency or "EUR",
        )

    def search(self, watch: Watch, origin: str, destination: str,
               depart: str, ret: str | None) -> list[Quote]:
        from fast_flights import get_flights

        query = self._build(watch, origin, destination, depart, ret)
        try:
            search_url = query.url() if callable(getattr(query, "url", None)) else getattr(query, "url", None)
        except Exception:
            search_url = None

        last_err: Exception | None = None
        results = None
        for attempt in range(1, self.retries + 1):
            try:
                results = get_flights(query)
                break
            except Exception as exc:  # réseau, blocage, parsing
                last_err = exc
                if attempt < self.retries:
                    sleep = (2 ** attempt) + random.uniform(0, 1.5)
                    log.warning("google_flights %s→%s tentative %s échouée (%s), retry dans %.1fs",
                                origin, destination, attempt, exc, sleep)
                    time.sleep(sleep)

        if results is None:
            raise ProviderError(f"Google Flights injoignable pour {origin}→{destination} {depart}: {last_err}")

        quotes: list[Quote] = []
        for item in list(results)[: self.max_results]:
            price = getattr(item, "price", None)
            if not isinstance(price, (int, float)) or price <= 0:
                continue

            segments = list(getattr(item, "flights", []) or [])
            outbound = _outbound_leg(segments, destination)
            stops = max(0, len(outbound) - 1) if outbound else None
            duration = sum(getattr(s, "duration", 0) or 0 for s in outbound) or None

            quotes.append(
                Quote(
                    watch_id=watch.id,
                    provider=self.name,
                    origin=origin,
                    destination=destination,
                    depart=depart,
                    ret=ret,
                    price=float(price),
                    currency=watch.currency or "EUR",
                    airlines=[str(a) for a in (getattr(item, "airlines", []) or [])],
                    stops=stops,
                    duration_min=duration,
                    depart_time=_fmt_dt(getattr(outbound[0], "departure", None)) if outbound else None,
                    arrival_time=_fmt_dt(getattr(outbound[-1], "arrival", None)) if outbound else None,
                    booking_url=search_url,
                )
            )

        if not quotes:
            log.info("Aucune offre exploitable pour %s→%s %s", origin, destination, depart)
        return quotes
