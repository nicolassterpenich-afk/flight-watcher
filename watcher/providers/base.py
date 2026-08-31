"""Interface commune des fournisseurs de prix."""

from __future__ import annotations

from abc import ABC, abstractmethod

from ..models import Quote, Watch


class Provider(ABC):
    name: str = "base"
    needs_key: bool = False

    @abstractmethod
    def search(self, watch: Watch, origin: str, destination: str,
               depart: str, ret: str | None) -> list[Quote]:
        """Renvoie les offres trouvées pour une combinaison précise."""

    def available(self) -> bool:
        return True


class ProviderError(RuntimeError):
    pass
