"""Référentiel des aéroports, partagé avec l'interface.

La liste vit dans `worker/src/airports.js` parce que le navigateur en a besoin
pour l'autocomplétion. La lire ici plutôt que d'en tenir une copie évite deux
vérités concurrentes : une ville ajoutée à la saisie est aussitôt reconnue par
la veille des flux.
"""

from __future__ import annotations

import re
import unicodedata
from functools import lru_cache
from pathlib import Path

RACINE = Path(__file__).resolve().parent.parent
SOURCE = RACINE / "worker" / "src" / "airports.js"
ENTREE_RE = re.compile(r'"([A-Z]{3})\|([^|"]+)\|([^"]+)"')

# Villes dont le tiret fait partie du nom : les couper donnerait « Saint »,
# « Fort » ou « Tel ». Même liste que côté interface.
TIRET_INSECABLE = {
    "Pointe-à-Pitre", "Fort-de-France", "Saint-Denis de La Réunion", "Clermont-Ferrand",
    "Saint-Jacques-de-Compostelle", "Tel-Aviv", "Charm el-Cheikh", "Addis-Abeba",
    "Cluj-Napoca", "Saint-Domingue", "Saint-Martin", "Port-Vila", "Hô Chi Minh-Ville",
    "Ténérife-Sud", "Ténérife-Nord",
}


# Les flux de bons plans sont écrits en anglais, le référentiel en français.
# Sans cette table, « Brussels », « Thailand » ou « Cape Town » ne sont
# reconnus par rien — et la veille ne trouve jamais rien. Clé : le nom court
# français normalisé ; valeurs : les formes rencontrées dans les flux.
ALIAS = {
    # pays
    "belgique": ("belgium",), "pays-bas": ("netherlands", "holland"),
    "allemagne": ("germany",), "espagne": ("spain",), "italie": ("italy",),
    "royaume": ("united kingdom", "uk", "britain", "great britain", "england", "scotland"),
    "irlande": ("ireland",), "suisse": ("switzerland",), "autriche": ("austria",),
    "grece": ("greece",), "turquie": ("turkey", "turkiye"), "danemark": ("denmark",),
    "suede": ("sweden",), "norvege": ("norway",), "finlande": ("finland",),
    "islande": ("iceland",), "pologne": ("poland",), "tchequie": ("czechia", "czech republic"),
    "hongrie": ("hungary",), "roumanie": ("romania",), "bulgarie": ("bulgaria",),
    "croatie": ("croatia",), "serbie": ("serbia",), "slovenie": ("slovenia",),
    "slovaquie": ("slovakia",), "albanie": ("albania",), "montenegro": ("montenegro",),
    "bosnie": ("bosnia",), "macedoine du nord": ("north macedonia",),
    "estonie": ("estonia",), "lettonie": ("latvia",), "lituanie": ("lithuania",),
    "chypre": ("cyprus",), "malte": ("malta",), "maroc": ("morocco",),
    "tunisie": ("tunisia",), "algerie": ("algeria",), "egypte": ("egypt",),
    "afrique du sud": ("south africa",), "tanzanie": ("tanzania",),
    "ethiopie": ("ethiopia",), "senegal": ("senegal",), "cote d'ivoire": ("ivory coast",),
    "maurice": ("mauritius",), "namibie": ("namibia",), "ouganda": ("uganda",),
    "cameroun": ("cameroon",), "rd congo": ("dr congo",), "madagascar": ("madagascar",),
    "thailande": ("thailand",), "viet nam": ("vietnam",), "cambodge": ("cambodia",),
    "birmanie": ("myanmar", "burma"), "malaisie": ("malaysia",),
    "indonesie": ("indonesia",), "singapour": ("singapore",), "japon": ("japan",),
    "coree du sud": ("south korea", "korea"), "chine": ("china",), "taiwan": ("taiwan",),
    "inde": ("india",), "maldives": ("maldives",), "nepal": ("nepal",),
    "emirats arabes unis": ("united arab emirates", "uae"),
    "arabie saoudite": ("saudi arabia",), "jordanie": ("jordan",), "liban": ("lebanon",),
    "israel": ("israel",), "etats-unis": ("united states", "usa", "the us"),
    "mexique": ("mexico",), "bresil": ("brazil",), "argentine": ("argentina",),
    "chili": ("chile",), "perou": ("peru",), "colombie": ("colombia",),
    "equateur": ("ecuador",), "bolivie": ("bolivia",), "cuba": ("cuba",),
    "republique dominicaine": ("dominican republic",), "jamaique": ("jamaica",),
    "barbade": ("barbados",), "australie": ("australia",),
    "nouvelle-zelande": ("new zealand",), "fidji": ("fiji",),
    "polynesie francaise": ("french polynesia",), "iles cook": ("cook islands",),
    "nouvelle-caledonie": ("new caledonia",), "guyane": ("french guiana",),
    "la reunion": ("reunion",), "georgie": ("georgia",), "armenie": ("armenia",),
    "azerbaidjan": ("azerbaijan",), "ouzbekistan": ("uzbekistan",),
    # villes
    "bruxelles": ("brussels",), "anvers": ("antwerp",), "ostende": ("ostend",),
    "londres": ("london",), "edimbourg": ("edinburgh",), "vienne": ("vienna",),
    "geneve": ("geneva",), "bale": ("basel", "basle"), "francfort": ("frankfurt",),
    "hambourg": ("hamburg",), "hanovre": ("hanover",), "breme": ("bremen",),
    "cologne": ("cologne",), "sarrebruck": ("saarbrucken",), "venise": ("venice",),
    "genes": ("genoa",), "verone": ("verona",), "palerme": ("palermo",),
    "catane": ("catania",), "seville": ("seville",), "saragosse": ("zaragoza",),
    "la corogne": ("a coruna",), "saint-jacques-de-compostelle": ("santiago de compostela",),
    "palma de majorque": ("palma", "majorca", "mallorca"), "minorque": ("menorca",),
    "tenerife-sud": ("tenerife",), "tenerife-nord": ("tenerife",),
    "las palmas": ("gran canaria",), "lisbonne": ("lisbon",), "madere": ("madeira",),
    "ponta delgada": ("azores",), "athenes": ("athens",),
    "thessalonique": ("thessaloniki",), "heraklion": ("heraklion", "crete"),
    "la canee": ("chania",), "corfou": ("corfu",), "santorin": ("santorini",),
    "copenhague": ("copenhagen",), "goteborg": ("gothenburg",), "varsovie": ("warsaw",),
    "cracovie": ("krakow", "cracow"), "bucarest": ("bucharest",), "bourgas": ("burgas",),
    "le caire": ("cairo",), "marrakech": ("marrakesh",), "tanger": ("tangier",),
    "alger": ("algiers",), "le cap": ("cape town",), "pekin": ("beijing",),
    "canton": ("guangzhou",), "seoul": ("seoul",), "katmandou": ("kathmandu",),
    "calcutta": ("kolkata",), "male": ("male",), "dacca": ("dhaka",),
    "tachkent": ("tashkent",), "bakou": ("baku",), "tbilissi": ("tbilisi",),
    "erevan": ("yerevan",), "dubai": ("dubai",), "abou dabi": ("abu dhabi",),
    "djeddah": ("jeddah",), "riyad": ("riyadh",), "beyrouth": ("beirut",),
    "mexico": ("mexico city",), "la havane": ("havana",),
    "saint-domingue": ("santo domingo",), "carthagene": ("cartagena",),
    "cuzco": ("cusco",), "salvador de bahia": ("salvador",), "adelaide": ("adelaide",),
    "noumea": ("noumea",), "papeete": ("tahiti",), "hô chi minh-ville": ("ho chi minh city", "saigon"),
    "pointe-à-pitre": ("guadeloupe",), "fort-de-france": ("martinique",),
}


def sans_accents(texte: str) -> str:
    """« Genève » → « geneve », caractère pour caractère.

    La longueur est préservée à dessein : les appelants découpent le texte
    d'origine avec les indices trouvés dans le texte normalisé. Un NFD global
    décalerait ces indices dès qu'un caractère se décompose en deux.
    """
    out = []
    for c in texte:
        base = "".join(x for x in unicodedata.normalize("NFD", c)
                       if unicodedata.category(x) != "Mn")
        out.append((base[0] if base else c).lower())
    return "".join(out)


@lru_cache(maxsize=1)
def aeroports() -> dict[str, dict[str, str]]:
    """{code IATA: {"ville": …, "ville_courte": …, "pays": …}}"""
    if not SOURCE.exists():
        return {}
    texte = SOURCE.read_text(encoding="utf-8")
    out: dict[str, dict[str, str]] = {}
    for code, ville, pays in ENTREE_RE.findall(texte):
        nom = re.sub(r"\s*\(.*\)\s*$", "", ville).strip()
        courte = nom if nom in TIRET_INSECABLE else nom.split("-")[0]
        out[code] = {"ville": nom, "ville_courte": courte, "pays": pays.strip()}
    return out


@lru_cache(maxsize=1)
def noms_de_pays() -> frozenset[str]:
    """Noms normalisés désignant un pays, alias anglais compris.

    Un pays se déploie sur tous ses aéroports : utile quand un article dit
    « to Thailand », trompeur quand il dit « to Phuket, Thailand ». Les
    distinguer permet de ne déployer qu'à défaut de ville nommée.
    """
    noms: set[str] = set()
    for info in aeroports().values():
        cle = sans_accents(info["pays"])
        noms.add(cle)
        noms.update(sans_accents(a) for a in ALIAS.get(cle, ()))
    return frozenset(n for n in noms if len(n) >= 3)


@lru_cache(maxsize=1)
def index_lieux() -> dict[str, frozenset[str]]:
    """{nom de lieu normalisé: codes IATA correspondants}

    Un pays renvoie tous ses aéroports, une ville les siens. Le code IATA
    lui-même est indexé : il sert de nom autant que d'identifiant.
    """
    index: dict[str, set[str]] = {}

    def ajoute(nom: str, code: str) -> None:
        cle = sans_accents(nom).strip()
        if len(cle) >= 3:
            index.setdefault(cle, set()).add(code)

    for code, info in aeroports().items():
        ajoute(code, code)
        ajoute(info["ville"], code)
        ajoute(info["ville_courte"], code)
        ajoute(info["pays"], code)
        # Les flux sont en anglais : sans ces formes, rien n'est reconnu.
        for source in (info["ville"], info["ville_courte"], info["pays"]):
            for anglais in ALIAS.get(sans_accents(source), ()):
                ajoute(anglais, code)
    return {k: frozenset(v) for k, v in index.items()}
