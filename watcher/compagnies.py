"""Liens « vérifier chez la compagnie » — brique 3 de PLANPROMOS.md.

La table vit dans `worker/src/compagnies.js` parce que l'interface en a
besoin ; on la lit ici plutôt que d'en tenir une copie, comme pour les
aéroports. Une compagnie ajoutée d'un côté apparaît des deux.

Aucun identifiant n'est stocké : ces liens s'ouvrent dans le navigateur de
l'utilisateur, avec sa session, ses miles et ses tarifs membre.
"""

from __future__ import annotations

import re
from functools import lru_cache
from pathlib import Path

from .models import Quote, Watch

RACINE = Path(__file__).resolve().parent.parent
SOURCE = RACINE / "worker" / "src" / "compagnies.js"
ENTREE_RE = re.compile(r'"(meta|deep|home)\|([^|"]+)\|([^"]+)"')


@lru_cache(maxsize=1)
def table() -> list[tuple[str, str, str]]:
    """[(genre, nom, gabarit)] — genre parmi meta, deep, home."""
    if not SOURCE.exists():
        return []
    return ENTREE_RE.findall(SOURCE.read_text(encoding="utf-8"))


def _compact(iso: str | None) -> str:
    return (iso or "")[2:].replace("-", "")


def remplir(gabarit: str, q: Quote, adultes: int = 1) -> str:
    """Les jetons les plus longs d'abord : sinon {ret_c} serait mangé par {ret}."""
    ret = q.ret or ""
    url = gabarit
    for jeton, valeur in (
        ("{origin_l}", q.origin.lower()),
        ("{destination_l}", q.destination.lower()),
        ("{depart_c}", _compact(q.depart)),
        ("{ret_c}", _compact(ret)),
        ("{ret_ou_null}", ret or "null"),
        ("{est_ar}", "true" if ret else "false"),
        ("{origin}", q.origin),
        ("{destination}", q.destination),
        ("{depart}", q.depart),
        ("{ret}", ret),
        ("{adults}", str(adultes)),
    ):
        url = url.replace(jeton, valeur)
    # Un retour vide laisse « // » au milieu du chemin ; le slash final fait
    # partie du gabarit et doit rester.
    return re.sub(r"(?<=[^:])/{2,}", "/", url)


def liens(watch: Watch, q: Quote, avec_meta: bool = True) -> list[tuple[str, str, str]]:
    """[(genre, nom, url)] pour les compagnies du vol relevé, puis les moteurs.

    Une compagnie sans gabarit vérifiable ouvre son accueil : mieux vaut une
    page d'accueil qu'un lien profond inventé qui tombe en 404.
    """
    connues = {nom.lower(): (genre, nom, gabarit) for genre, nom, gabarit in table()}
    adultes = max(1, watch.passengers.adults)
    sorties: list[tuple[str, str, str]] = []

    for nom in q.airlines or []:
        trouve = connues.get(str(nom).lower())
        if trouve:
            genre, vrai_nom, gabarit = trouve
            sorties.append((genre, vrai_nom, remplir(gabarit, q, adultes)))

    if avec_meta:
        for genre, nom, gabarit in table():
            if genre == "meta":
                sorties.append((genre, nom, remplir(gabarit, q, adultes)))
    return sorties
