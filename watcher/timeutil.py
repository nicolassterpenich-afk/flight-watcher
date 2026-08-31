"""Horodatages UTC — un seul format dans tout le projet : `2026-08-31T08:42:33Z`."""

from __future__ import annotations

from datetime import datetime, timezone


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def iso(dt: datetime | None = None) -> str:
    """Format ISO-8601 UTC suffixé `Z`, sans double décalage."""
    dt = (dt or utcnow()).astimezone(timezone.utc)
    return dt.replace(tzinfo=None).isoformat(timespec="seconds") + "Z"


def parse(value: str | None) -> datetime | None:
    """Lit un horodatage produit par `iso()`. Renvoie None si illisible."""
    if not value:
        return None
    try:
        text = value.strip()
        if text.endswith("Z"):
            text = text[:-1] + "+00:00"
        parsed = datetime.fromisoformat(text)
        return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)
    except ValueError:
        return None
