from __future__ import annotations

from .base import Provider, ProviderError
from .google_flights import GoogleFlightsProvider
from .ryanair import RyanairProvider

REGISTRY: dict[str, type[Provider]] = {
    GoogleFlightsProvider.name: GoogleFlightsProvider,
    RyanairProvider.name: RyanairProvider,
}

_cache: dict[str, Provider] = {}


def get_provider(name: str) -> Provider:
    if name not in REGISTRY:
        raise ProviderError(f"Fournisseur inconnu : {name} (dispo : {', '.join(REGISTRY)})")
    if name not in _cache:
        _cache[name] = REGISTRY[name]()
    return _cache[name]


__all__ = ["Provider", "ProviderError", "REGISTRY", "get_provider"]
