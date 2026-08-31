"""Fournisseur Ryanair (API publique, sans clé).

Utile pour les départs de Charleroi / Bruxelles : Google Flights n'affiche pas
toujours les tarifs Ryanair les plus bas, et l'API Ryanair donne le prix réel
du jour ainsi que les dates voisines les moins chères.
"""

from __future__ import annotations

import logging
from datetime import datetime

import requests

from ..models import Quote, Watch
from .base import Provider, ProviderError

log = logging.getLogger(__name__)

# Depuis le 31/08/2026, l'endpoint de réservation renvoie 409 quelle que soit
# la route et le segment de langue — vérifié depuis WSL et depuis un runner
# GitHub, ce n'est donc pas un filtrage d'IP. On passe par le « fare finder »,
# qui lui répond sans cookie ni session. L'ancien reste en secours au cas où.
AVAILABILITY = "https://www.ryanair.com/api/booking/v4/availability"
ONE_WAY = "https://services-api.ryanair.com/farfnd/v4/oneWayFares"
ROUND_TRIP = "https://services-api.ryanair.com/farfnd/v4/roundTripFares"
HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                  "(KHTML, like Gecko) Chrome/131.0 Safari/537.36",
    "Accept": "application/json",
}


def _minutes_between(start: str | None, end: str | None) -> int | None:
    if not start or not end:
        return None
    try:
        return int((datetime.fromisoformat(end) - datetime.fromisoformat(start)).total_seconds() // 60)
    except ValueError:
        return None


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

    def _fares(self, origin: str, destination: str, depart: str,
               ret: str | None) -> list[dict]:
        """Tarif le moins cher du jour via le « fare finder » public."""
        params = {
            "departureAirportIataCode": origin,
            "arrivalAirportIataCode": destination,
            "outboundDepartureDateFrom": depart,
            "outboundDepartureDateTo": depart,
            "currency": "EUR",
        }
        if ret:
            params.update({"inboundDepartureDateFrom": ret, "inboundDepartureDateTo": ret})

        resp = requests.get(ROUND_TRIP if ret else ONE_WAY, params=params,
                            headers=HEADERS, timeout=self.timeout)
        if resp.status_code == 404:
            return []            # route non desservie
        resp.raise_for_status()

        fares = (resp.json() or {}).get("fares") or []
        if not fares:
            return []            # pas de vol ce jour-là
        fare = fares[0]

        def _leg(node: dict | None) -> dict | None:
            if not node:
                return None
            price = node.get("price") or {}
            if price.get("value") is None:
                return None
            return {
                "price": float(price["value"]),
                "currency": price.get("currencyCode", "EUR"),
                "depart_time": (node.get("departureDate") or "").replace("T", " ")[:16] or None,
                "arrival_time": (node.get("arrivalDate") or "").replace("T", " ")[:16] or None,
                "number": node.get("flightNumber"),
                "duration": _minutes_between(node.get("departureDate"), node.get("arrivalDate")),
            }

        legs = [leg for leg in (_leg(fare.get("outbound")), _leg(fare.get("inbound"))) if leg]
        # Sur un aller-retour, le total du résumé fait foi : il peut différer de
        # la somme des deux tarifs affichés.
        total = ((fare.get("summary") or {}).get("price") or {}).get("value")
        if legs and total is not None:
            legs[0] = {**legs[0], "total_override": float(total)}
        return legs

    def search(self, watch: Watch, origin: str, destination: str,
               depart: str, ret: str | None) -> list[Quote]:
        try:
            legs = self._fares(origin, destination, depart, ret)
        except requests.RequestException as exc:
            try:
                legs = self._availability(origin, destination, depart, ret, watch)
            except requests.RequestException:
                raise ProviderError(
                    f"Ryanair injoignable {origin}→{destination} {depart}: {exc}") from exc

        if not legs:
            return []
        if ret and len(legs) < 2 and "total_override" not in legs[0]:
            return []            # A/R incomplet : pas exploitable

        pax = max(1, watch.passengers.adults) + watch.passengers.children
        # Prix par adulte : le fare finder ne tarife pas les enfants, on
        # multiplie faute de mieux — c'est un indicateur de tendance, pas un devis.
        base = legs[0].get("total_override") or sum(leg["price"] for leg in legs)
        total = base * pax

        def _dur(value) -> int | None:
            if value is None:
                return None
            if isinstance(value, int):
                return value                      # déjà en minutes (fare finder)
            try:
                hh, mm = str(value).split(":")[:2]
                return int(hh) * 60 + int(mm)     # format « hh:mm » de l'ancien endpoint
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
