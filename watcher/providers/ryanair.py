"""Fournisseur Ryanair (API publique, sans clé).

Utile pour les départs de Charleroi / Bruxelles : Google Flights n'affiche pas
toujours les tarifs Ryanair les plus bas, et l'API Ryanair donne le prix réel
du jour ainsi que les dates voisines les moins chères.
"""

from __future__ import annotations

import logging

import requests

from ..models import Quote, Watch
from .base import Provider, ProviderError

log = logging.getLogger(__name__)

AVAILABILITY = "https://www.ryanair.com/api/booking/v4/availability"
CHEAPEST = "https://services-api.ryanair.com/farfnd/v4/oneWayFares"
HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                  "(KHTML, like Gecko) Chrome/131.0 Safari/537.36",
    "Accept": "application/json",
}


class RyanairProvider(Provider):
    name = "ryanair"

    def __init__(self, timeout: int = 25):
        self.timeout = timeout

    def _availability(self, origin: str, destination: str, depart: str,
                      ret: str | None, watch: Watch) -> list[dict]:
        params = {
            "ADT": max(1, watch.passengers.adults),
            "CHD": watch.passengers.children,
            "TEEN": 0,
            "INF": watch.passengers.infants_on_lap,
            "Origin": origin,
            "Destination": destination,
            "DateOut": depart,
            "FlexDaysBeforeOut": 0,
            "FlexDaysOut": 0,
            "RoundTrip": "true" if ret else "false",
            "IncludeConnectingFlights": "false",
            "ToUs": "AGREED",
            "promoCode": "",
        }
        if ret:
            params.update({"DateIn": ret, "FlexDaysBeforeIn": 0, "FlexDaysIn": 0})

        resp = requests.get(AVAILABILITY, params=params, headers=HEADERS, timeout=self.timeout)
        if resp.status_code == 404:
            return []            # route non desservie
        resp.raise_for_status()
        payload = resp.json()

        currency = payload.get("currency", "EUR")
        trips = payload.get("trips") or []
        legs: list[dict] = []
        for trip in trips:
            cheapest = None
            for day in trip.get("dates") or []:
                for flight in day.get("flights") or []:
                    fares = ((flight.get("regularFare") or {}).get("fares") or [])
                    if not fares or not flight.get("faresLeft", 1):
                        continue
                    amount = fares[0].get("amount")
                    if amount is None:
                        continue
                    times = flight.get("time") or flight.get("timeUTC") or []
                    cand = {
                        "price": float(amount),
                        "currency": currency,
                        "depart_time": times[0].replace("T", " ")[:16] if times else None,
                        "arrival_time": times[1].replace("T", " ")[:16] if len(times) > 1 else None,
                        "number": flight.get("flightNumber"),
                        "duration": flight.get("duration"),
                    }
                    if cheapest is None or cand["price"] < cheapest["price"]:
                        cheapest = cand
            if cheapest:
                legs.append(cheapest)
        return legs

    def search(self, watch: Watch, origin: str, destination: str,
               depart: str, ret: str | None) -> list[Quote]:
        try:
            legs = self._availability(origin, destination, depart, ret, watch)
        except requests.RequestException as exc:
            raise ProviderError(f"Ryanair injoignable {origin}→{destination} {depart}: {exc}") from exc

        if not legs:
            return []
        if ret and len(legs) < 2:
            return []            # A/R incomplet : pas exploitable

        pax = max(1, watch.passengers.adults) + watch.passengers.children
        total = sum(leg["price"] for leg in legs) * pax

        def _dur(mins: str | None) -> int | None:
            if not mins:
                return None
            try:
                hh, mm = mins.split(":")[:2]
                return int(hh) * 60 + int(mm)
            except Exception:
                return None

        durations = [d for d in (_dur(leg.get("duration")) for leg in legs) if d]

        url = (f"https://www.ryanair.com/be/fr/trip/flights/select?"
               f"adults={max(1, watch.passengers.adults)}&teens=0"
               f"&children={watch.passengers.children}&infants={watch.passengers.infants_on_lap}"
               f"&dateOut={depart}&dateIn={ret or ''}&isConnectedFlight=false"
               f"&isReturn={'true' if ret else 'false'}&discount=0"
               f"&originIata={origin}&destinationIata={destination}")

        return [
            Quote(
                watch_id=watch.id,
                provider=self.name,
                origin=origin,
                destination=destination,
                depart=depart,
                ret=ret,
                price=round(total, 2),
                currency=legs[0]["currency"],
                airlines=["Ryanair"],
                stops=0,
                duration_min=sum(durations) if durations else None,
                depart_time=legs[0].get("depart_time"),
                arrival_time=legs[-1].get("arrival_time"),
                booking_url=url,
            )
        ]
