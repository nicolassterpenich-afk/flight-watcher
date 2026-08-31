"""Configuration et résultats via le Worker Cloudflare.

En fonctionnement normal le Worker est la source de vérité : l'interface web y
écrit les surveillances, le moteur les y lit et lui renvoie les prix relevés.

`watches.yaml` reste le secours. Un relevé ne doit jamais échouer parce que
l'interface est en panne : si le Worker est injoignable, on repart du fichier
et on continue — les alertes Telegram partent quand même.
"""

from __future__ import annotations

import logging
import os
from typing import Any

import requests

from .models import Passengers, Quote, Watch

log = logging.getLogger(__name__)

# (connexion, lecture) : un Worker éteint doit être constaté vite — le repli
# sur watches.yaml ne doit pas grignoter le budget temps du relevé.
TIMEOUT = (5, 20)
RETRIES = 3


class RemoteError(RuntimeError):
    """Le Worker est injoignable ou répond une erreur."""


def worker_url() -> str:
    return os.environ.get("WORKER_URL", "").rstrip("/")


def agent_token() -> str:
    return os.environ.get("AGENT_TOKEN", "")


def configured() -> bool:
    return bool(worker_url() and agent_token())


def _headers() -> dict[str, str]:
    return {
        "authorization": f"Bearer {agent_token()}",
        "content-type": "application/json",
        "user-agent": "flight-watcher-agent",
    }


def _request(method: str, path: str, payload: dict[str, Any] | None = None) -> dict[str, Any]:
    if not configured():
        raise RemoteError("WORKER_URL ou AGENT_TOKEN absent de l'environnement")
    url = f"{worker_url()}{path}"
    last: Exception | None = None

    for attempt in range(1, RETRIES + 1):
        try:
            res = requests.request(method, url, headers=_headers(), json=payload, timeout=TIMEOUT)
        except requests.RequestException as exc:
            last = exc
        else:
            if res.status_code == 401:
                raise RemoteError("jeton AGENT_TOKEN refusé par le Worker")
            if res.ok:
                try:
                    return res.json()
                except ValueError as exc:
                    raise RemoteError(f"réponse illisible du Worker : {exc}") from exc
            # 5xx : le Worker ou D1 a hoqueté, ça vaut la peine de réessayer.
            last = RemoteError(f"HTTP {res.status_code} : {res.text[:200]}")
            if res.status_code < 500:
                raise last
        log.debug("Worker %s %s : tentative %s/%s échouée (%s)", method, path, attempt, RETRIES, last)

    raise RemoteError(str(last))


# --------------------------------------------------------------------------
# Lecture de la configuration
# --------------------------------------------------------------------------

def _to_watch(raw: dict[str, Any]) -> Watch:
    pax = raw.get("passengers") or {}
    return Watch(
        id=str(raw["id"]),
        label=str(raw.get("label") or ""),
        origins=[str(c).upper() for c in raw.get("origins") or []],
        destinations=[str(c).upper() for c in raw.get("destinations") or []],
        depart=str(raw.get("depart") or ""),
        ret=str(raw["ret"]) if raw.get("ret") else None,
        threshold=float(raw["threshold"]) if raw.get("threshold") is not None else None,
        currency=str(raw.get("currency") or "EUR").upper(),
        seat=str(raw.get("seat") or "economy"),
        max_stops=int(raw["max_stops"]) if raw.get("max_stops") is not None else None,
        flex_days=int(raw.get("flex_days") or 0),
        flex_days_ret=(int(raw["flex_days_ret"])
                       if raw.get("flex_days_ret") is not None else None),
        passengers=Passengers(
            adults=int(pax.get("adults", 1)),
            children=int(pax.get("children", 0)),
            infants_in_seat=int(pax.get("infants_in_seat", 0)),
            infants_on_lap=int(pax.get("infants_on_lap", 0)),
        ),
        providers=[str(p) for p in (raw.get("providers") or ["google_flights"])],
        enabled=bool(raw.get("enabled", True)),
        alert_on_drop=bool(raw.get("alert_on_drop", True)),
        notes=str(raw.get("notes") or ""),
    )


def load_watches() -> tuple[list[Watch], dict[str, Any]]:
    """Même signature que `config.load_watches`, mais depuis le Worker."""
    data = _request("GET", "/api/agent/watches")
    raws = data.get("watches") or []
    watches: list[Watch] = []
    for raw in raws:
        try:
            watches.append(_to_watch(raw))
        except (KeyError, TypeError, ValueError) as exc:
            log.warning("Surveillance distante ignorée (%s) : %s", exc, raw)
    return watches, data.get("settings") or {}


# --------------------------------------------------------------------------
# Renvoi des résultats
# --------------------------------------------------------------------------

def push_results(ran_at: str, entries: list[dict[str, Any]],
                 quotes: list[Quote], state: dict[str, Any]) -> dict[str, Any]:
    """Renvoie au Worker le meilleur prix de chaque surveillance et son état.

    `entries` est `summary["watches"]`, `quotes` la liste des meilleurs devis
    (un par surveillance, comme dans data/history.jsonl).
    """
    by_watch = {q.watch_id: q for q in quotes}
    results = []

    for entry in entries:
        wid = entry.get("id")
        if not wid:
            continue
        node = state.get(wid, {})
        best = by_watch.get(wid)
        results.append({
            "watch_id": wid,
            "quotes": [best.to_dict()] if best else [],
            "state": {
                "last_price": node.get("last_price"),
                "best_ever": node.get("best_ever"),
                "last_alert_price": node.get("last_alert_price"),
                "last_alert_at": node.get("last_alert_at"),
                "last_alert_reason": node.get("last_alert_reason"),
                "last_checked_at": node.get("last_checked_at"),
                "status": entry.get("status"),
                "best_route": entry.get("best_route"),
                "booking_url": entry.get("booking_url"),
            },
            "errors": entry.get("errors") or [],
        })

    return _request("POST", "/api/agent/results", {"ran_at": ran_at, "results": results})
