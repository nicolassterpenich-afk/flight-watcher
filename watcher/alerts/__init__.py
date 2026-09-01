"""Canaux d'alerte : Telegram et courriel, choisis d'après le destinataire."""

from __future__ import annotations

import logging

from .courriel import Courriel, est_une_adresse
from .telegram import Telegram, esc

log = logging.getLogger(__name__)

__all__ = ["Telegram", "Courriel", "esc", "est_une_adresse", "remettre"]


def remettre(texte: str, destinataires: list[str] | None,
             telegram: Telegram | None = None,
             courriel: Courriel | None = None) -> list[str]:
    """Remet le message à chaque destinataire, selon sa forme.

    Un nombre part sur Telegram, une adresse par courriel. Une liste vide
    retombe sur le propriétaire de la veille.

    Un destinataire injoignable n'empêche pas les autres de recevoir : c'est
    la même règle que pour Telegram seul, et elle compte davantage ici — une
    adresse mal tapée ne doit pas priver tout le monde de l'alerte.
    """
    telegram = telegram or Telegram()
    cibles = [str(c).strip() for c in (destinataires or []) if str(c).strip()]
    if not cibles:
        cibles = [telegram.chat_id] if telegram.chat_id else []

    echecs: list[str] = []
    for cible in cibles:
        try:
            if est_une_adresse(cible):
                (courriel or Courriel()).send(texte, cible)
            else:
                telegram.send(texte, chat_id=cible)
        except Exception as exc:                    # noqa: BLE001
            log.error("Alerte non remise à %s : %s", cible, exc)
            echecs.append(cible)
    return echecs
