"""Chargement / écriture de watches.yaml."""

from __future__ import annotations

import os
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any

import yaml

from .models import Passengers, Watch

ROOT = Path(__file__).resolve().parent.parent
CONFIG_PATH = Path(os.environ.get("WATCHES_FILE", ROOT / "watches.yaml"))


def _as_list(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        return [v.strip().upper() for v in value.replace(";", ",").split(",") if v.strip()]
    return [str(v).strip().upper() for v in value if str(v).strip()]


def _as_list_brute(value: Any) -> list[str]:
    """Comme _as_list mais sans passer en majuscules : un identifiant de
    conversation n'est pas un code d'aéroport."""
    if value is None:
        return []
    if isinstance(value, (str, int)):
        return [v.strip() for v in str(value).replace(";", ",").split(",") if v.strip()]
    return [str(v).strip() for v in value if str(v).strip()]


def _merge(defaults: dict, raw: dict) -> dict:
    out = dict(defaults)
    out.update({k: v for k, v in raw.items() if v is not None})
    return out


def load_watches(path: Path | None = None) -> tuple[list[Watch], dict]:
    path = path or CONFIG_PATH
    if not path.exists():
        return [], {}

    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    defaults = data.get("defaults") or {}
    settings = data.get("settings") or {}

    watches: list[Watch] = []
    for i, raw in enumerate(data.get("watches") or []):
        merged = _merge(defaults, raw)
        pax_raw = merged.get("passengers") or {}
        if isinstance(pax_raw, int):
            pax_raw = {"adults": pax_raw}

        wid = str(merged.get("id") or f"watch-{i + 1}")
        watches.append(
            Watch(
                id=wid,
                label=str(merged.get("label") or ""),
                origins=_as_list(merged.get("origin") or merged.get("origins")),
                destinations=_as_list(merged.get("destination") or merged.get("destinations")),
                depart=str(merged.get("depart") or merged.get("date") or ""),
                ret=str(merged["return"]) if merged.get("return") else None,
                threshold=float(merged["threshold"]) if merged.get("threshold") is not None else None,
                currency=str(merged.get("currency") or "EUR").upper(),
                seat=str(merged.get("seat") or "economy"),
                max_stops=int(merged["max_stops"]) if merged.get("max_stops") is not None else None,
                flex_days=int(merged.get("flex_days") or 0),
                flex_days_ret=(int(merged["flex_days_ret"])
                               if merged.get("flex_days_ret") is not None else None),
                nights_min=(int(merged["nights_min"]) if merged.get("nights_min") is not None else None),
                nights_max=(int(merged["nights_max"]) if merged.get("nights_max") is not None else None),
                passengers=Passengers(
                    adults=int(pax_raw.get("adults", 1)),
                    children=int(pax_raw.get("children", 0)),
                    infants_in_seat=int(pax_raw.get("infants_in_seat", 0)),
                    infants_on_lap=int(pax_raw.get("infants_on_lap", 0)),
                ),
                providers=[str(p) for p in (merged.get("providers") or ["google_flights"])],
                enabled=bool(merged.get("enabled", True)),
                alert_on_drop=bool(merged.get("alert_on_drop", True)),
                chat_ids=[str(c).strip() for c in _as_list_brute(merged.get("chat_ids"))],
                notes=str(merged.get("notes") or ""),
            )
        )
    return watches, settings


def save_watches(watches: list[Watch], settings: dict, path: Path | None = None) -> None:
    """Réécrit watches.yaml (utilisé par les commandes Telegram)."""
    path = path or CONFIG_PATH

    # On préserve le bloc `defaults` existant : il a déjà été fusionné dans
    # chaque surveillance au chargement, mais le supprimer casserait les
    # modifications faites à la main dans le fichier.
    existing_defaults: dict[str, Any] = {}
    if path.exists():
        try:
            existing_defaults = (yaml.safe_load(path.read_text(encoding="utf-8")) or {}).get("defaults") or {}
        except yaml.YAMLError:
            existing_defaults = {}

    payload: dict[str, Any] = {}
    if settings:
        payload["settings"] = settings
    if existing_defaults:
        payload["defaults"] = existing_defaults
    payload["watches"] = []
    for w in watches:
        entry: dict[str, Any] = {
            "id": w.id,
            "origin": w.origins,
            "destination": w.destinations,
            "depart": w.depart,
        }
        if w.label:
            entry["label"] = w.label
        if w.ret:
            entry["return"] = w.ret
        if w.threshold is not None:
            entry["threshold"] = w.threshold
        if w.currency != "EUR":
            entry["currency"] = w.currency
        if w.seat != "economy":
            entry["seat"] = w.seat
        if w.max_stops is not None:
            entry["max_stops"] = w.max_stops
        if w.flex_days:
            entry["flex_days"] = w.flex_days
        if w.flex_days_ret is not None:
            entry["flex_days_ret"] = w.flex_days_ret
        if w.nights_min is not None:
            entry["nights_min"] = w.nights_min
        if w.nights_max is not None:
            entry["nights_max"] = w.nights_max
        if w.passengers.total != 1:
            entry["passengers"] = {
                "adults": w.passengers.adults,
                "children": w.passengers.children,
                "infants_in_seat": w.passengers.infants_in_seat,
                "infants_on_lap": w.passengers.infants_on_lap,
            }
        if w.providers != ["google_flights"]:
            entry["providers"] = w.providers
        if not w.enabled:
            entry["enabled"] = False
        if w.chat_ids:
            entry["chat_ids"] = w.chat_ids
        if w.notes:
            entry["notes"] = w.notes
        payload["watches"].append(entry)

    path.write_text(
        "# Surveillances de prix de vols — modifie ce fichier puis commit.\n"
        "# Doc des champs : voir README.md\n\n"
        + yaml.safe_dump(payload, allow_unicode=True, sort_keys=False, default_flow_style=False),
        encoding="utf-8",
    )


def expand_dates(base: str, flex_days: int) -> list[str]:
    """Renvoie les dates à interroger autour d'une date pivot."""
    if not base:
        return []
    try:
        pivot = datetime.strptime(base, "%Y-%m-%d").date()
    except ValueError:
        return [base]
    if flex_days <= 0:
        return [base]
    today = date.today()
    out = []
    for delta in range(-flex_days, flex_days + 1):
        d = pivot + timedelta(days=delta)
        if d >= today:
            out.append(d.isoformat())
    return out or [base]
