"""Fournisseur Wizz Air (endpoint de tarifs du site, sans clé).

Comme Ryanair, Wizz ne distribue pas toujours ses tarifs les plus bas via les
comparateurs. Depuis Charleroi c'est le premier transporteur en nombre de
destinations, et Google Flights ne le montre pas systématiquement.

L'URL de l'API porte un numéro de version qui change à chaque déploiement du
site. On la découvre sur la page d'accueil plutôt que de la figer : un numéro
en dur périmerait en quelques semaines, exactement comme l'endpoint de
réservation de Ryanair.
"""

from __future__ import annotations

import logging
import re
from datetime import datetime

import requests

from ..models import Quote, Watch
from .base import Provider, ProviderError

log = logging.getLogger(__name__)

ACCUEIL = "https://wizzair.com/en-gb"
API_REPLI = "https://be.wizzair.com/29.14.0/Api"
API_RE = re.compile(r'apiUrl:"(https://be\.wizzair\.com/[0-9.]+/Api)')
HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                  "(KHTML, like Gecko) Chrome/131.0 Safari/537.36",
    "Accept": "application/json",
    "Content-Type": "application/json",
    "Referer": "https://wizzair.com/",
}


class WizzairProvider(Provider):
    name = "wizzair"

    def __init__(self, timeout: int = 25):
        self.timeout = timeout
        self._api: str | None = None

    def _base(self) -> str:
        if self._api:
            return self._api
        try:
            page = requests.get(ACCUEIL, headers={"User-Agent": HEADERS["User-Agent"]},
                                timeout=self.timeout)
            page.raise_for_status()
            found = API_RE.search(page.text)
            self._api = found.group(1) if found else API_REPLI
            if not found:
                log.warning("Version d'API Wizz introuvable sur la page d'accueil — repli sur %s", API_REPLI)
        except requests.RequestException as exc:
            log.warning("Page d'accueil Wizz injoignable (%s) — repli sur %s", exc, API_REPLI)
            self._api = API_REPLI
        return self._api

    @staticmethod
    def _cheapest(flights: list[dict], jour: str) -> dict | None:
        """Le tarif du jour demandé — l'endpoint répond parfois plus large."""
        best = None
        for f in flights or []:
            if not str(f.get("departureDate", "")).startswith(jour):
                continue
            montant = ((f.get("price") or {}).get("amount"))
            if montant is None:
                continue
            if best is None or montant < best["amount"]:
                heures = f.get("departureDates") or []
                best = {"amount": float(montant),
                        "currency": (f.get("price") or {}).get("currencyCode", "EUR"),
                        "time": heures[0].replace("T", " ")[:16] if heures else None}
        return best

    def search(self, watch: Watch, origin: str, destination: str,
               depart: str, ret: str | None) -> list[Quote]:
        vols = [{"departureStation": origin, "arrivalStation": destination,
                 "from": depart, "to": depart}]
        if ret:
            vols.append({"departureStation": destination, "arrivalStation": origin,
                         "from": ret, "to": ret})

        payload = {
            "flightList": vols,
            "priceType": "regular",
            "adultCount": max(1, watch.passengers.adults),
            "childCount": watch.passengers.children,
            "infantCount": watch.passengers.infants_on_lap,
        }

        try:
            resp = requests.post(f"{self._base()}/search/timetable", json=payload,
                                 headers=HEADERS, timeout=self.timeout)
        except requests.RequestException as exc:
            raise ProviderError(f"Wizz Air injoignable {origin}→{destination} {depart}: {exc}") from exc

        if resp.status_code in (400, 404):
            return []                       # route non desservie
        if not resp.ok:
            raise ProviderError(f"Wizz Air {origin}→{destination} {depart}: HTTP {resp.status_code}")

        try:
            data = resp.json() or {}
        except ValueError as exc:
            raise ProviderError(f"Wizz Air : réponse illisible ({exc})") from exc

        aller = self._cheapest(data.get("outboundFlights"), depart)
        if not aller:
            return []
        retour = self._cheapest(data.get("returnFlights"), ret) if ret else None
        if ret and not retour:
            return []                       # A/R incomplet : pas exploitable

        pax = max(1, watch.passengers.adults) + watch.passengers.children
        total = (aller["amount"] + (retour["amount"] if retour else 0)) * pax

        url = (f"https://wizzair.com/en-gb/booking/select-flight/{origin}/{destination}/"
               f"{depart}/{ret or 'null'}/{max(1, watch.passengers.adults)}/"
               f"{watch.passengers.children}/{watch.passengers.infants_on_lap}/null")

        return [Quote(
            watch_id=watch.id,
            provider=self.name,
            origin=origin,
            destination=destination,
            depart=depart,
            ret=ret,
            price=round(total, 2),
            currency=aller["currency"],
            airlines=["Wizz Air"],
            stops=0,
            depart_time=aller.get("time"),
            arrival_time=(retour or {}).get("time"),
            booking_url=url,
        )]
