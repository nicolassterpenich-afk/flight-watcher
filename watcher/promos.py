"""Veille éditoriale des bons plans — brique 1 de PLANPROMOS.md.

Le moteur de prix surveille un trajet précis ; il ne voit pas passer les
ventes flash ni les erreurs de tarif, qui sont des évènements annoncés
ailleurs. Ce module lit des flux RSS, en extrait les lieux, et ne retient que
ce qui recoupe une surveillance existante.

Il est volontairement isolé du moteur : aucune de ses erreurs ne doit faire
échouer un relevé de prix. Un flux mort est journalisé, jamais bloquant.

Le piège de ce module est le bruit. Une alerte sur trois qui ne correspond à
rien et l'utilisateur cesse de les lire — ce qui dévaluerait aussi les vraies
alertes de prix. Le filtrage est donc délibérément strict.
"""

from __future__ import annotations

import calendar
import hashlib
import html
import json
import logging
import os
import re
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from pathlib import Path
from typing import Any, Iterable

import requests
import yaml

from .airports import index_lieux, noms_de_pays, sans_accents
from .models import Watch

log = logging.getLogger(__name__)

RACINE = Path(__file__).resolve().parent.parent
CONFIG = Path(os.environ.get("PROMOS_FILE", RACINE / "promos.yaml"))
ETAT = RACINE / "data" / "promos.json"

TIMEOUT = (5, 25)
MAX_VUS = 500                      # au-delà, on oublie les plus anciens
ENTETES = {
    "User-Agent": "Mozilla/5.0 (compatible; flight-watcher/1.0; +https://github.com/nicolassterpenich-afk/flight-watcher)",
    "Accept": "application/rss+xml, application/xml, text/xml",
}

# Fly4Free étiquette ses articles « cheap flights from berlin », parfois en
# minuscules. Dans cette construction le reste EST un lieu : on peut donc y
# accepter une correspondance sans majuscule, ce qu'on refuse ailleurs.
CAT_FROM_TO = re.compile(r"(?i)^\s*cheap flights from (.+?) to (.+?)\s*$")
CAT_FROM = re.compile(r"(?i)^\s*cheap flights from (.+?)\s*$")
CAT_TO = re.compile(r"(?i)^\s*cheap flights to (.+?)\s*$")
CAT_X_TO_Y = re.compile(r"(?i)^\s*(.+?) to (.+?)\s*$")

DEPUIS = re.compile(r"(?i)\b(?:from|depuis|au départ de|ex)\b")
VERS = re.compile(r"(?i)\b(?:to|vers|à destination de)\b")


class FeedError(RuntimeError):
    """Flux injoignable ou illisible. Jamais fatal pour un relevé."""


# --------------------------------------------------------------------------
# Configuration
# --------------------------------------------------------------------------

def charger_config(chemin: Path | None = None) -> dict[str, Any]:
    chemin = chemin or CONFIG
    if not chemin.exists():
        return {"feeds": [], "settings": {}, "synonymes": {}}
    data = yaml.safe_load(chemin.read_text(encoding="utf-8")) or {}
    data.setdefault("feeds", [])
    data.setdefault("settings", {})
    data.setdefault("synonymes", {})
    return data


# --------------------------------------------------------------------------
# Lecture des flux
# --------------------------------------------------------------------------

@dataclass
class Entree:
    id: str
    source: str
    titre: str
    url: str
    publie_le: datetime | None
    categories: list[str] = field(default_factory=list)
    resume: str = ""

    def texte(self) -> str:
        return f"{self.titre}\n{self.resume}"


def _identifiant(url: str, titre: str) -> str:
    return hashlib.sha1((url or titre).encode("utf-8")).hexdigest()[:16]


def _date(brut: str | None) -> datetime | None:
    if not brut:
        return None
    try:
        d = parsedate_to_datetime(brut)
    except (TypeError, ValueError):
        return None
    return d if d.tzinfo else d.replace(tzinfo=timezone.utc)


def parser_flux(xml: str, source: str) -> list[Entree]:
    """RSS 2.0 et Atom, sans dépendance supplémentaire."""
    try:
        racine = ET.fromstring(xml)
    except ET.ParseError as exc:
        raise FeedError(f"{source} : XML illisible ({exc})") from exc

    entrees: list[Entree] = []
    bruts = racine.findall("./channel/item") or racine.findall("{http://www.w3.org/2005/Atom}entry")

    for brut in bruts:
        def txt(tag: str) -> str:
            n = brut.find(tag) if not tag.startswith("{") else brut.find(tag)
            return (n.text or "").strip() if n is not None else ""

        titre = txt("title") or txt("{http://www.w3.org/2005/Atom}title")
        lien = txt("link")
        if not lien:
            n = brut.find("{http://www.w3.org/2005/Atom}link")
            lien = n.get("href", "") if n is not None else ""
        if not titre:
            continue

        categories = [(c.text or "").strip() for c in brut.findall("category") if (c.text or "").strip()]
        publie = _date(txt("pubDate")) or _date(txt("{http://www.w3.org/2005/Atom}updated"))
        entrees.append(Entree(
            id=_identifiant(lien, titre),
            source=source,
            titre=titre,
            url=lien,
            publie_le=publie,
            categories=categories,
            resume=re.sub(r"<[^>]+>", " ", txt("description"))[:600],
        ))
    return entrees


def lire_flux(url: str, source: str, timeout=TIMEOUT) -> list[Entree]:
    try:
        rep = requests.get(url, headers=ENTETES, timeout=timeout)
    except requests.RequestException as exc:
        raise FeedError(f"{source} injoignable : {exc}") from exc
    if not rep.ok:
        raise FeedError(f"{source} : HTTP {rep.status_code}")
    return parser_flux(rep.text, source)


# --------------------------------------------------------------------------
# Extraction des lieux
# --------------------------------------------------------------------------

@dataclass
class Lieux:
    origines: set[str] = field(default_factory=set)
    destinations: set[str] = field(default_factory=set)

    def tous(self) -> set[str]:
        return self.origines | self.destinations


def _vocabulaire(synonymes: dict[str, list[str]] | None = None) -> dict[str, frozenset[str]]:
    """Noms de lieux connus, enrichis des synonymes saisis à la main."""
    voc = dict(index_lieux())
    for code, mots in (synonymes or {}).items():
        for mot in mots:
            cle = sans_accents(str(mot))
            voc[cle] = frozenset(voc.get(cle, frozenset()) | {code.upper()})
    return voc


def _trouver(texte: str, voc: dict[str, frozenset[str]], exiger_majuscule: bool) -> list[tuple[int, str, frozenset[str]]]:
    """Positions des lieux reconnus dans le texte, du plus long au plus court.

    Les codes IATA ne sont acceptés qu'en majuscules : « nice » est un mot
    anglais courant, « NCE » ne l'est pas. Hors des catégories explicites, on
    exige aussi une capitale sur les noms de villes — un titre écrit
    « from Amsterdam », pas « from amsterdam ».
    """
    plat = sans_accents(texte)
    trouves: list[tuple[int, str, frozenset[str]]] = []
    occupes: list[tuple[int, int]] = []

    for nom in sorted(voc, key=len, reverse=True):
        for m in re.finditer(rf"\b{re.escape(nom)}\b", plat):
            debut, fin = m.span()
            if any(d < fin and debut < f for d, f in occupes):
                continue                      # déjà couvert par un nom plus long
            original = texte[debut:fin]
            if len(nom) == 3 and nom.upper() in voc.get(nom, frozenset()):
                if original != original.upper():
                    continue                  # code IATA : majuscules obligatoires
            elif exiger_majuscule and not original[:1].isupper():
                continue
            occupes.append((debut, fin))
            trouves.append((debut, nom, voc[nom]))
    return sorted(trouves)


def _role(texte: str, position: int) -> str:
    """« from » ou « to » le plus proche avant le lieu, dans une fenêtre courte."""
    avant = texte[max(0, position - 40):position]
    d = max((m.end() for m in DEPUIS.finditer(avant)), default=-1)
    v = max((m.end() for m in VERS.finditer(avant)), default=-1)
    if d == v == -1:
        return "inconnu"
    return "origine" if d > v else "destination"


def extraire_lieux(entree: Entree, voc: dict[str, frozenset[str]] | None = None) -> Lieux:
    voc = voc if voc is not None else _vocabulaire()
    lieux = Lieux()

    pays = noms_de_pays()
    # Un pays ne se déploie sur tous ses aéroports qu'à défaut de ville nommée
    # dans le même rôle : « to Phuket, Thailand » désigne Phuket, pas Bangkok.
    villes = {"origine": set(), "destination": set()}
    contrees = {"origine": set(), "destination": set()}

    def range_le(role: str, nom: str, codes: frozenset[str]) -> None:
        (contrees if nom in pays else villes)[role].update(codes)

    # 1) Les catégories explicites de Fly4Free : le motif porte déjà le rôle.
    for cat in entree.categories:
        for motif, roles in ((CAT_FROM_TO, ("origine", "destination")),
                             (CAT_FROM, ("origine",)),
                             (CAT_TO, ("destination",)),
                             (CAT_X_TO_Y, ("origine", "destination"))):
            m = motif.match(cat)
            if not m:
                continue
            for groupe, role in zip(m.groups(), roles):
                for _, nom, codes in _trouver(groupe, voc, exiger_majuscule=False):
                    range_le(role, nom, codes)
            break

    # 2) Le titre, où le rôle se lit à la préposition qui précède.
    def balayer(texte: str) -> None:
        for position, nom, codes in _trouver(texte, voc, exiger_majuscule=True):
            role = _role(texte, position)
            if role in ("origine", "destination"):
                range_le(role, nom, codes)

    balayer(entree.titre)

    # 3) Le résumé seulement si rien n'a été trouvé plus haut. Le corps d'un
    # article énumère volontiers des escales et des villes voisines : sur
    # « from Amsterdam to Malaysia », il faisait remonter Canton et Pékin en
    # origines. Le titre annonce le trajet, le corps le commente.
    if not (villes["origine"] | villes["destination"]
            | contrees["origine"] | contrees["destination"]) and entree.resume:
        balayer(entree.resume)

    for role, cible in (("origine", lieux.origines), ("destination", lieux.destinations)):
        cible.update(villes[role] or contrees[role])
    return lieux


# --------------------------------------------------------------------------
# Période de voyage annoncée
# --------------------------------------------------------------------------

MOIS_EN = {
    "january": 1, "jan": 1, "february": 2, "feb": 2, "march": 3, "mar": 3,
    "april": 4, "apr": 4, "may": 5, "june": 6, "jun": 6, "july": 7, "jul": 7,
    "august": 8, "aug": 8, "september": 9, "sep": 9, "sept": 9, "october": 10,
    "oct": 10, "november": 11, "nov": 11, "december": 12, "dec": 12,
}
_MOIS = "|".join(sorted(MOIS_EN, key=len, reverse=True))

# « Travel dates: Wide availability in September – December 2026 » — le bloc
# est régulier chez Fly4Free ; c'est le seul endroit où la période figure, le
# résumé RSS n'étant qu'une accroche.
TRAVEL = re.compile(r"(?i)travel dates?\s*[:\-–]\s*([^\n<]{0,160})")
PLAGE = re.compile(rf"(?i)\b({_MOIS})\b\s*(\d{{4}})?\s*(?:[-–—]|to|until|jusqu)\s*\b({_MOIS})\b\s*(\d{{4}})?")
SEUL = re.compile(rf"(?i)\b({_MOIS})\b\s*(\d{{4}})")



@dataclass
class Periode:
    debut: date
    fin: date
    texte: str

    def couvre(self, jour: str) -> bool:
        try:
            d = date.fromisoformat(jour)
        except (TypeError, ValueError):
            return False
        return self.debut <= d <= self.fin

    def chevauche(self, debut: date, fin: date) -> bool:
        return self.debut <= fin and debut <= self.fin


def _fin_du_mois(annee: int, mois: int) -> int:
    # calendar plutôt qu'une table écrite à la main : la mienne portait 29 en
    # février et produisait une date invalide les années non bissextiles.
    return calendar.monthrange(annee, mois)[1]


def _phrase(fragment: str, fin: int) -> str:
    """Le libellé s'arrête à la fin de l'expression de dates.

    La page est aplatie en une seule ligne : sans cette coupe, le libellé
    emportait « Route: From: Brussels To: … Baggage allowance: … ».
    """
    return fragment[:fin].strip(" -–—:")


def periode_de_voyage(texte: str, aujourdhui: date | None = None) -> Periode | None:
    """Extrait « September – December 2026 » d'une page d'article.

    Une plage sans année de début hérite de celle de fin ; si le mois de début
    est postérieur à celui de fin, la plage franchit l'année — « October 2026
    – March 2027 » écrit « October – March 2027 ».
    """
    m = TRAVEL.search(texte)
    fragment = m.group(1) if m else ""
    if not fragment:
        return None

    plage = PLAGE.search(fragment)
    if plage:
        m1, a1, m2, a2 = plage.groups()
        mois1, mois2 = MOIS_EN[m1.lower()], MOIS_EN[m2.lower()]
        annee2 = int(a2) if a2 else (int(a1) if a1 else (aujourdhui or date.today()).year)
        annee1 = int(a1) if a1 else (annee2 - 1 if mois1 > mois2 else annee2)
        return Periode(date(annee1, mois1, 1),
                       date(annee2, mois2, _fin_du_mois(annee2, mois2)),
                       _phrase(fragment, plage.end()))

    seul = SEUL.search(fragment)
    if seul:
        mois, annee = MOIS_EN[seul.group(1).lower()], int(seul.group(2))
        return Periode(date(annee, mois, 1), date(annee, mois, _fin_du_mois(annee, mois)),
                       _phrase(fragment, seul.end()))
    return None


def texte_de_page(html_brut: str) -> str:
    """HTML d'un article → texte plat, scripts et styles retirés."""
    corps = re.sub(r"(?is)<(script|style|nav|header|footer)[^>]*>.*?</\1>", " ", html_brut)
    return re.sub(r"\s+", " ", html.unescape(re.sub(r"<[^>]+>", " ", corps)))


def enrichir(entree: Entree, timeout=TIMEOUT) -> Periode | None:
    """Va chercher la période sur la page de l'article.

    Un appel par correspondance seulement — jamais pour les cinquante entrées
    d'un flux. Toute erreur est avalée : la période est un bonus, son absence
    ne doit pas faire perdre la correspondance.
    """
    if not entree.url:
        return None
    try:
        rep = requests.get(entree.url, headers={"User-Agent": ENTETES["User-Agent"]}, timeout=timeout)
        rep.raise_for_status()
    except requests.RequestException as exc:
        log.info("Période de voyage indisponible pour %s : %s", entree.url, exc)
        return None
    return periode_de_voyage(texte_de_page(rep.text))

# --------------------------------------------------------------------------
# Recoupement avec les surveillances
# --------------------------------------------------------------------------

def fenetre_de_depart(w: Watch) -> tuple[date, date] | None:
    """Les dates de départ que la surveillance accepte réellement.

    Le pivot seul ne suffit pas : une souplesse de ± 3 jours élargit la
    fenêtre, et c'est elle qu'il faut confronter à la période de la promo.
    """
    try:
        pivot = date.fromisoformat(w.depart)
    except (TypeError, ValueError):
        return None
    marge = timedelta(days=max(int(w.flex_days or 0), 0))
    return pivot - marge, pivot + marge


@dataclass
class Correspondance:
    entree: Entree
    watch: Watch
    lieux: Lieux
    raison: str
    periode: Periode | None = None
    couvre: bool | None = None       # None = période inconnue

    def codes_communs(self) -> list[str]:
        return sorted(set(self.watch.destinations) & self.lieux.destinations)


def recouper(entree: Entree, watches: Iterable[Watch],
             voc: dict[str, frozenset[str]] | None = None) -> list[Correspondance]:
    """Ne garde une entrée que si elle parle d'un de mes départs, ou d'une de
    mes destinations sans mentionner de départ identifiable.

    Sans cette seconde condition, « vols New York → Bangkok » alerterait un
    Belge qui surveille Bangkok. C'est exactement le bruit qui fait cesser de
    lire les alertes.
    """
    voc = voc if voc is not None else _vocabulaire()
    lieux = extraire_lieux(entree, voc)
    sorties: list[Correspondance] = []

    for w in watches:
        if not w.enabled:
            continue
        origines = set(w.origins) & lieux.origines
        destinations = set(w.destinations) & lieux.destinations
        if not destinations:
            continue                     # la destination doit correspondre, toujours
        if origines:
            raison = "départ et destination surveillés"
        elif not lieux.origines:
            raison = "destination surveillée, aucun départ annoncé"
        else:
            continue                     # départ annoncé, mais pas un des miens
        sorties.append(Correspondance(entree=entree, watch=w, lieux=lieux, raison=raison))
    return sorties


# --------------------------------------------------------------------------
# Mémoire des entrées déjà vues
# --------------------------------------------------------------------------

def charger_etat(chemin: Path | None = None) -> dict[str, Any]:
    chemin = chemin or ETAT
    if not chemin.exists():
        return {"vus": []}
    try:
        data = json.loads(chemin.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError) as exc:
        log.warning("État des promos illisible (%s) — on repart de zéro", exc)
        return {"vus": []}
    data.setdefault("vus", [])
    return data


def enregistrer_etat(etat: dict[str, Any], chemin: Path | None = None) -> None:
    chemin = chemin or ETAT
    chemin.parent.mkdir(parents=True, exist_ok=True)
    etat["vus"] = etat["vus"][-MAX_VUS:]
    chemin.write_text(json.dumps(etat, ensure_ascii=False, indent=2), encoding="utf-8")


# --------------------------------------------------------------------------
# Message
# --------------------------------------------------------------------------

def pour_stockage(entree: Entree, correspondances: list[Correspondance],
                  voc: dict[str, frozenset[str]] | None = None) -> dict[str, Any]:
    """Forme attendue par la table feed_items."""
    lieux = correspondances[0].lieux if correspondances else extraire_lieux(entree, voc)
    return {
        "id": entree.id,
        "source": entree.source,
        "title": entree.titre,
        "url": entree.url,
        "published_at": entree.publie_le.isoformat() if entree.publie_le else None,
        "places": {"origines": sorted(lieux.origines), "destinations": sorted(lieux.destinations)},
        "matched_watch_id": correspondances[0].watch.id if correspondances else None,
        "reason": correspondances[0].raison if correspondances else None,
        "travel_from": p.debut.isoformat() if (p := (correspondances[0].periode if correspondances else None)) else None,
        "travel_to": p.fin.isoformat() if p else None,
        "travel_text": p.texte[:160] if p else None,
        "covers": (None if not correspondances or correspondances[0].couvre is None
                   else int(correspondances[0].couvre)),
    }


def grouper(correspondances: list[Correspondance]) -> list[list[Correspondance]]:
    """Une annonce, un message — même si elle touche plusieurs surveillances.

    Recevoir deux fois la même offre parce qu'on suit deux fois Bangkok, c'est
    le doublon qui fait cesser de lire les alertes.
    """
    par_entree: dict[str, list[Correspondance]] = {}
    for c in correspondances:
        par_entree.setdefault(c.entree.id, []).append(c)
    return list(par_entree.values())


def formater_alerte(correspondances: Correspondance | list[Correspondance],
                    prix: dict[str, float] | None = None) -> str:
    from .alerts import esc

    groupe = [correspondances] if isinstance(correspondances, Correspondance) else list(correspondances)
    entree = groupe[0].entree
    prix = prix or {}

    titres = " · ".join(esc(c.watch.display()) for c in groupe)
    lignes = [f"📰 <b>Promo repérée</b> — {titres}", ""]
    lignes.append(f"« {esc(entree.titre)} »")

    age = ""
    if entree.publie_le:
        heures = (datetime.now(timezone.utc) - entree.publie_le).total_seconds() / 3600
        age = " · à l\'instant" if heures < 1 else (
            f" · il y a {int(heures)} h" if heures < 48 else f" · il y a {int(heures // 24)} j")
    lignes.append(f"{esc(entree.source)}{age}")
    lignes.append("")

    periode = groupe[0].periode
    if periode:
        lignes.append(f"🗓 Voyage : {esc(periode.texte)}")
        lignes.append("")

    for c in groupe:
        suivi = prix.get(c.watch.id)
        detail = f" — ton meilleur prix suivi : {suivi:.0f} {c.watch.currency}" if suivi else ""
        lignes.append(f"↳ <b>{esc(c.watch.display())}</b> · {esc(c.raison)}{detail}")
        # Le point qui décide : la promo tombe-t-elle sur mes dates ?
        if c.couvre is True:
            lignes.append(f"   ✅ couvre ton départ du {esc(c.watch.depart)}")
        elif c.couvre is False:
            lignes.append(f"   ⚠️ ne couvre pas ton départ du {esc(c.watch.depart)}")

    if entree.url:
        lignes.append("")
        lignes.append(f'<a href="{esc(entree.url)}">🔗 Lire l\'annonce</a>')
    return "\n".join(lignes)


# --------------------------------------------------------------------------
# Passage complet
# --------------------------------------------------------------------------

def relever(watches: Iterable[Watch], config: dict[str, Any] | None = None,
            etat: dict[str, Any] | None = None) -> dict[str, Any]:
    """Lit les flux actifs et renvoie les correspondances nouvelles.

    N'écrit rien et n'envoie rien : l'appelant décide. Les erreurs de flux
    sont collectées, pas levées.
    """
    config = config if config is not None else charger_config()
    etat = etat if etat is not None else charger_etat()
    watches = [w for w in watches if w.enabled]

    reglages = config.get("settings") or {}
    age_max = timedelta(hours=float(reglages.get("max_age_hours", 48)))
    voc = _vocabulaire(config.get("synonymes"))
    vus = set(etat.get("vus", []))
    limite = datetime.now(timezone.utc) - age_max

    # « ids » porte toutes les entrées examinées, pas seulement celles qui ont
    # matché : sans elles, chaque passage réexaminerait tout le flux et
    # réalerterait sur les mêmes offres.
    bilan: dict[str, Any] = {"flux": [], "entrees": 0, "nouvelles": 0,
                             "correspondances": [], "erreurs": [], "ids": [], "vues": []}

    for flux in config.get("feeds", []):
        nom = flux.get("nom") or flux.get("url", "flux")
        if not flux.get("actif", True):
            continue
        try:
            entrees = lire_flux(flux["url"], nom)
        except FeedError as exc:
            log.warning("%s", exc)
            bilan["erreurs"].append(str(exc))
            bilan["flux"].append({"nom": nom, "entrees": 0, "erreur": str(exc)})
            continue

        retenues = 0
        for e in entrees:
            bilan["entrees"] += 1
            if e.publie_le and e.publie_le < limite:
                continue
            if e.id in vus:
                continue
            retenues += 1
            bilan["nouvelles"] += 1
            bilan["ids"].append(e.id)
            trouvees = recouper(e, watches, voc)
            if trouvees:
                # Une seule requête par annonce retenue, jamais pour les
                # cinquante entrées du flux.
                periode = enrichir(e)
                for c in trouvees:
                    c.periode = periode
                    fenetre = fenetre_de_depart(c.watch)
                    if periode and fenetre:
                        c.couvre = periode.chevauche(*fenetre)
            bilan["correspondances"].extend(trouvees)
            # Toutes les entrées lues sont conservées, pas seulement celles qui
            # matchent : l'interface montre le fil, la correspondance le
            # surligne.
            bilan["vues"].append(pour_stockage(e, trouvees, voc))
        bilan["flux"].append({"nom": nom, "entrees": len(entrees), "nouvelles": retenues})

    return bilan


# --------------------------------------------------------------------------
# Ligne de commande — autonome, jamais appelée par le moteur de prix
# --------------------------------------------------------------------------

def main(argv: list[str] | None = None) -> int:
    import argparse

    from .engine import load_config

    parser = argparse.ArgumentParser(
        prog="watcher.promos",
        description="Veille des flux de bons plans, recoupée avec les surveillances.")
    parser.add_argument("--notify", action="store_true",
                        help="Envoyer les correspondances sur Telegram (sinon, affichage seul)")
    parser.add_argument("--push", action="store_true",
                        help="Envoyer le fil au Worker pour affichage dans l'interface")
    parser.add_argument("--source", choices=("auto", "api", "file"), default="auto")
    parser.add_argument("-v", "--verbose", action="store_true")
    args = parser.parse_args(argv)

    logging.basicConfig(level=logging.DEBUG if args.verbose else logging.INFO,
                        format="%(asctime)s %(levelname)-7s %(name)s — %(message)s",
                        datefmt="%H:%M:%S")

    watches, _, _ = load_config(args.source)
    etat = charger_etat()
    bilan = relever(watches, charger_config(), etat)

    for f in bilan["flux"]:
        detail = f.get("erreur") or f"{f['entrees']} entrées, {f.get('nouvelles', 0)} nouvelles"
        print(f"  {f['nom']:<18} {detail}")

    correspondances = bilan["correspondances"]
    print(f"\n{len(correspondances)} correspondance(s) sur {bilan['nouvelles']} entrée(s) nouvelle(s)")

    from . import store
    etat_prix = store.load_state()
    prix = {wid: node.get("last_price") for wid, node in etat_prix.items()
            if isinstance(node, dict) and node.get("last_price")}

    if args.push and bilan["vues"]:
        from . import remote
        try:
            envoi = remote.push_feed(bilan["vues"])
            print(f"  fil envoyé au Worker : {envoi.get('recus')} entrée(s)")
        except remote.RemoteError as exc:
            log.error("Envoi du fil impossible : %s", exc)

    groupes = grouper(correspondances)
    if args.notify and groupes:
        from .alerts import remettre
        for groupe in groupes:
            # Mêmes destinataires que les alertes de prix : qui suit une
            # destination doit aussi en recevoir les bons plans.
            destinataires = sorted({c for g in groupe for c in (g.watch.destinataires or [])})
            remettre(formater_alerte(groupe, prix), destinataires)

    for groupe in groupes:
        print(f"\n--- {', '.join(c.watch.id for c in groupe)} ---\n{formater_alerte(groupe, prix)}")

    # Les entrées lues sont retenues même sans correspondance : c'est ce qui
    # évite de réexaminer, et de réalerter, à chaque passage.
    vus = set(etat.get("vus", []))
    etat["vus"] = list(vus | set(bilan["ids"]))
    enregistrer_etat(etat)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
