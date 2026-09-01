"""Canaux d'alerte : Telegram et courriel, choisis d'après le destinataire."""

from __future__ import annotations

import logging

from .courriel import Courriel, est_une_adresse
from .telegram import Telegram, esc

log = logging.getLogger(__name__)

__all__ = ["Telegram", "Courriel", "esc", "est_une_adresse", "remettre"]


def remettre(texte: str, destinataires: list[str] | None,
             telegram: Telegram | None = None,
             courriel: Courriel | None = None) -> tuple[list[str], list[str]]:
    """Remet le message au propriétaire et aux destinataires, selon leur forme.

    Un nombre part sur Telegram, une adresse par courriel.

    Le propriétaire reçoit toujours : le champ s'appelle « Prévenir aussi »,
    il ajoute des destinataires, il n'en retire pas. Partager une destination
    ne doit pas vous priver de ses alertes sans que vous l'ayez demandé.

    Un destinataire injoignable n'empêche pas les autres de recevoir : une
    adresse mal tapée ne doit priver personne.

    Renvoie (remis, échecs).
    """
    telegram = telegram or Telegram()
    cibles: list[str] = [telegram.chat_id] if telegram.chat_id else []
    for c in destinataires or []:
        c = str(c).strip()
        if c and c not in cibles:
            cibles.append(c)

    remis: list[str] = []
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
        else:
            remis.append(cible)
    return remis, echecs
