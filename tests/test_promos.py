"""Tests de la veille des flux — brique 1.

Aucun accès réseau : les flux sont factices, écrits pour éprouver le
filtrage anti-bruit, qui est le vrai risque de ce module.
"""

from __future__ import annotations

import unittest
from datetime import datetime, timedelta, timezone

from watcher import promos
from watcher.models import Watch


def rss(*items: str, titre: str = "Faux flux") -> str:
    return ('<?xml version="1.0" encoding="UTF-8"?><rss version="2.0"><channel>'
            f"<title>{titre}</title>" + "".join(items) + "</channel></rss>")


def item(titre: str, lien: str = "https://exemple.test/a", date: str | None = None,
         categories: tuple[str, ...] = (), description: str = "") -> str:
    quand = date or "Tue, 01 Sep 2026 10:00:00 +0000"
    cats = "".join(f"<category>{c}</category>" for c in categories)
    return (f"<item><title>{titre}</title><link>{lien}</link>"
            f"<pubDate>{quand}</pubDate><description>{description}</description>{cats}</item>")


def surveillance(wid="bangkok", origins=("BRU", "CRL", "AMS"), destinations=("BKK",),
                 enabled=True) -> Watch:
    return Watch(id=wid, label=f"Surveillance {wid}", origins=list(origins),
                 destinations=list(destinations), depart="2027-02-06", ret="2027-02-20",
                 threshold=600.0, enabled=enabled)


class TestLecture(unittest.TestCase):
    def test_entrees_completes(self):
        e = promos.parser_flux(rss(item("Vols vers Bangkok à 449 €",
                                        categories=("Europe", "cheap flights to bangkok"))), "Test")
        self.assertEqual(len(e), 1)
        self.assertEqual(e[0].titre, "Vols vers Bangkok à 449 €")
        self.assertEqual(e[0].url, "https://exemple.test/a")
        self.assertIn("cheap flights to bangkok", e[0].categories)
        self.assertEqual(e[0].publie_le.year, 2026)

    def test_flux_vide(self):
        self.assertEqual(promos.parser_flux(rss(), "Test"), [])

    def test_xml_casse(self):
        with self.assertRaises(promos.FeedError):
            promos.parser_flux("<rss><channel><item><title>pas fermé", "Test")

    def test_entree_sans_titre_ignoree(self):
        self.assertEqual(promos.parser_flux(rss("<item><link>x</link></item>"), "Test"), [])

    def test_date_absente_toleree(self):
        e = promos.parser_flux(rss("<item><title>Sans date</title></item>"), "Test")
        self.assertIsNone(e[0].publie_le)

    def test_identifiants_distincts(self):
        e = promos.parser_flux(rss(item("A", lien="https://x.test/1"),
                                   item("B", lien="https://x.test/2")), "Test")
        self.assertNotEqual(e[0].id, e[1].id)


class TestExtraction(unittest.TestCase):
    def setUp(self):
        self.voc = promos._vocabulaire()

    def lieux(self, titre, categories=(), description=""):
        e = promos.parser_flux(rss(item(titre, categories=categories,
                                        description=description)), "Test")[0]
        return promos.extraire_lieux(e, self.voc)

    def test_categorie_explicite(self):
        l = self.lieux("Peu importe", categories=("cheap flights from brussels to bangkok",))
        self.assertIn("BRU", l.origines)
        self.assertIn("BKK", l.destinations)

    def test_categorie_minuscule_acceptee(self):
        # Fly4Free écrit ses catégories en minuscules ; dans « cheap flights
        # to x », le reste EST un lieu, la capitale n'est pas requise.
        self.assertIn("BKK", self.lieux("x", categories=("cheap flights to bangkok",)).destinations)

    def test_preposition_dans_le_titre(self):
        l = self.lieux("Cheap flights from Amsterdam to Bangkok from €418")
        self.assertIn("AMS", l.origines)
        self.assertIn("BKK", l.destinations)

    def test_prix_apres_from_nest_pas_un_lieu(self):
        l = self.lieux("Vols vers Bangkok from €418")
        self.assertEqual(l.origines, set())

    def test_code_iata_exige_des_majuscules(self):
        self.assertIn("NCE", self.lieux("Vols vers NCE cet hiver").tous())
        self.assertNotIn("NCE", self.lieux("Vols vers nce cet hiver").tous())

    def test_nom_de_ville_exige_une_capitale_dans_le_titre(self):
        self.assertIn("BKK", self.lieux("Vols to Bangkok").destinations)
        self.assertNotIn("BKK", self.lieux("Vols to bangkok").destinations)

    def test_pays_donne_tous_ses_aeroports(self):
        l = self.lieux("Cheap flights to Thailand")
        self.assertIn("BKK", l.destinations)
        self.assertIn("HKT", l.destinations)

    def test_la_ville_nommee_prime_sur_son_pays(self):
        # « to Phuket, Thailand » désigne Phuket. Déployer le pays ferait
        # correspondre une surveillance Bangkok à une promo pour Phuket.
        l = self.lieux("Etihad flights from Amsterdam to Phuket, Thailand from €496")
        self.assertIn("HKT", l.destinations)
        self.assertNotIn("BKK", l.destinations)

    def test_le_pays_reste_utile_sans_ville(self):
        l = self.lieux("Cheap flights from Brussels to Thailand from €421")
        self.assertIn("BKK", l.destinations)
        self.assertIn("BRU", l.origines)

    def test_synonyme_ajoute_a_la_main(self):
        voc = promos._vocabulaire({"PTP": ["guadeloupe"]})
        e = promos.parser_flux(rss(item("Promo vers la Guadeloupe")), "Test")[0]
        self.assertIn("PTP", promos.extraire_lieux(e, voc).destinations)

    def test_nom_le_plus_long_gagne(self):
        # « New York » ne doit pas être coupé en « New ».
        self.assertIn("JFK", self.lieux("Cheap flights to New York").destinations)

    def test_le_resume_ne_sert_que_de_secours(self):
        # Le corps d'un article énumère des escales et des villes voisines ;
        # s'il complétait le titre, « from Amsterdam » ramènerait Pékin.
        l = self.lieux("Cheap flights from Amsterdam to Malaysia",
                       description="Escales possibles via Beijing, Guangzhou ou Chengdu.")
        self.assertEqual(l.origines, {"AMS"})

    def test_le_resume_est_lu_si_le_titre_est_muet(self):
        l = self.lieux("PRICE DROP 💥 Une affaire à ne pas manquer",
                       description="Vols from Brussels to Bangkok à 449 €.")
        self.assertIn("BRU", l.origines)
        self.assertIn("BKK", l.destinations)

    def test_accents_ignores(self):
        self.assertIn("GVA", self.lieux("Cheap flights from Geneve to Bangkok").origines)


class TestRecoupement(unittest.TestCase):
    def setUp(self):
        self.voc = promos._vocabulaire()
        self.watches = [surveillance()]

    def recoupe(self, titre, categories=()):
        e = promos.parser_flux(rss(item(titre, categories=categories)), "Test")[0]
        return promos.recouper(e, self.watches, self.voc)

    def test_depart_et_destination_surveilles(self):
        c = self.recoupe("Cheap flights from Amsterdam to Bangkok from €418")
        self.assertEqual(len(c), 1)
        self.assertEqual(c[0].watch.id, "bangkok")
        self.assertEqual(c[0].raison, "départ et destination surveillés")

    def test_destination_seule_sans_depart_annonce(self):
        c = self.recoupe("Erreur de tarif vers Bangkok à 199 €")
        self.assertEqual(len(c), 1)
        self.assertEqual(c[0].raison, "destination surveillée, aucun départ annoncé")

    def test_depart_etranger_est_du_bruit(self):
        # Le cœur du filtrage : un Belge ne veut pas des vols au départ de
        # New York, même vers sa destination surveillée.
        self.assertEqual(self.recoupe("Cheap flights from New York to Bangkok"), [])

    def test_destination_non_surveillee_ignoree(self):
        self.assertEqual(self.recoupe("Cheap flights from Brussels to Lisbon"), [])

    def test_surveillance_en_pause_ignoree(self):
        self.watches = [surveillance(enabled=False)]
        self.assertEqual(self.recoupe("Erreur de tarif vers Bangkok"), [])

    def test_plusieurs_surveillances_meme_destination(self):
        self.watches = [surveillance("a"), surveillance("b", origins=("CDG",))]
        c = self.recoupe("Erreur de tarif vers Bangkok")
        self.assertEqual({x.watch.id for x in c}, {"a", "b"})

    def test_entree_sans_lieu_reconnu(self):
        self.assertEqual(self.recoupe("Les dix plus beaux hôtels du monde"), [])


class TestPassageComplet(unittest.TestCase):
    def setUp(self):
        self.config = {"feeds": [{"nom": "Faux", "url": "https://faux.test/feed", "actif": True}],
                       "settings": {"max_age_hours": 48}, "synonymes": {}}
        self.watches = [surveillance()]
        self._vrai = promos.lire_flux

    def tearDown(self):
        promos.lire_flux = self._vrai

    def sert(self, entrees):
        promos.lire_flux = lambda url, source, timeout=None: promos.parser_flux(rss(*entrees), source)

    def recent(self, decalage_h=1):
        d = datetime.now(timezone.utc) - timedelta(hours=decalage_h)
        return d.strftime("%a, %d %b %Y %H:%M:%S +0000")

    def test_correspondance_trouvee(self):
        self.sert([item("Erreur de tarif vers Bangkok", date=self.recent())])
        bilan = promos.relever(self.watches, self.config, {"vus": []})
        self.assertEqual(len(bilan["correspondances"]), 1)
        self.assertEqual(bilan["erreurs"], [])

    def test_entree_deja_vue_ignoree(self):
        e = item("Erreur de tarif vers Bangkok", date=self.recent())
        self.sert([e])
        premier = promos.relever(self.watches, self.config, {"vus": []})
        vu = premier["correspondances"][0].entree.id
        second = promos.relever(self.watches, self.config, {"vus": [vu]})
        self.assertEqual(second["correspondances"], [])
        self.assertEqual(second["nouvelles"], 0)

    def test_entree_trop_ancienne_ignoree(self):
        self.sert([item("Erreur de tarif vers Bangkok", date="Mon, 01 Jan 2024 10:00:00 +0000")])
        self.assertEqual(promos.relever(self.watches, self.config, {"vus": []})["nouvelles"], 0)

    def test_flux_mort_nest_pas_fatal(self):
        def casse(url, source, timeout=None):
            raise promos.FeedError(f"{source} injoignable : connexion refusée")
        promos.lire_flux = casse
        bilan = promos.relever(self.watches, self.config, {"vus": []})
        self.assertEqual(len(bilan["erreurs"]), 1)
        self.assertEqual(bilan["correspondances"], [])

    def test_toutes_les_entrees_lues_sont_memorisees(self):
        # Sans ça, une entrée sans correspondance serait réexaminée à chaque
        # passage — et réalerterait le jour où une surveillance change.
        self.sert([item("Erreur de tarif vers Bangkok", lien="https://x.test/1", date=self.recent()),
                   item("Les dix plus beaux hôtels", lien="https://x.test/2", date=self.recent())])
        bilan = promos.relever(self.watches, self.config, {"vus": []})
        self.assertEqual(len(bilan["ids"]), 2)
        self.assertEqual(len(bilan["correspondances"]), 1)

    def test_flux_desactive_non_lu(self):
        self.config["feeds"][0]["actif"] = False
        promos.lire_flux = lambda *a, **k: (_ for _ in ()).throw(AssertionError("ne doit pas être lu"))
        self.assertEqual(promos.relever(self.watches, self.config, {"vus": []})["entrees"], 0)


class TestMessage(unittest.TestCase):
    def test_alerte_lisible(self):
        e = promos.parser_flux(rss(item("Air France : Bruxelles → Bangkok à 449 € A/R")), "Fly4Free")[0]
        c = promos.Correspondance(entree=e, watch=surveillance(), lieux=promos.Lieux(),
                                  raison="destination surveillée, aucun départ annoncé")
        msg = promos.formater_alerte(c, {"bangkok": 598.0})
        self.assertIn("Promo repérée", msg)
        self.assertIn("Fly4Free", msg)
        self.assertIn("598", msg)
        self.assertIn("Lire l'annonce", msg)

    def test_une_annonce_un_seul_message(self):
        # La même offre touchant deux surveillances ne doit pas produire deux
        # messages : c'est le doublon qui fait cesser de lire les alertes.
        e = promos.parser_flux(rss(item("Bruxelles → Bangkok à 376 €")), "Fly4Free")[0]
        lot = [promos.Correspondance(entree=e, watch=surveillance("a"), lieux=promos.Lieux(), raison="r"),
               promos.Correspondance(entree=e, watch=surveillance("b"), lieux=promos.Lieux(), raison="r")]
        groupes = promos.grouper(lot)
        self.assertEqual(len(groupes), 1)
        msg = promos.formater_alerte(groupes[0])
        self.assertEqual(msg.count("Lire l'annonce"), 1)
        self.assertIn("Surveillance a", msg)
        self.assertIn("Surveillance b", msg)

    def test_annonces_distinctes_restent_separees(self):
        e1 = promos.parser_flux(rss(item("Vers Bangkok", lien="https://x.test/1")), "F")[0]
        e2 = promos.parser_flux(rss(item("Vers Bangkok aussi", lien="https://x.test/2")), "F")[0]
        lot = [promos.Correspondance(entree=e, watch=surveillance(), lieux=promos.Lieux(), raison="r")
               for e in (e1, e2)]
        self.assertEqual(len(promos.grouper(lot)), 2)

    def test_html_du_flux_echappe(self):
        # Le gabarit du message contient lui-même du HTML : on vérifie que le
        # titre venu du flux est neutralisé, pas qu'il n'y a plus de balise.
        e = promos.parser_flux(rss(item("Vols &lt;b&gt;pas chers&lt;/b&gt; vers Bangkok")), "Test")[0]
        self.assertEqual(e.titre, "Vols <b>pas chers</b> vers Bangkok")
        c = promos.Correspondance(entree=e, watch=surveillance(), lieux=promos.Lieux(), raison="test")
        msg = promos.formater_alerte(c)
        self.assertIn("&lt;b&gt;pas chers&lt;/b&gt;", msg)
        self.assertNotIn("<b>pas chers", msg)

    def test_url_du_flux_echappee(self):
        e = promos.parser_flux(rss(item("Vers Bangkok", lien='https://x.test/?a=1&amp;b="2"')), "Test")[0]
        c = promos.Correspondance(entree=e, watch=surveillance(), lieux=promos.Lieux(), raison="test")
        self.assertNotIn('href="https://x.test/?a=1&b="2""', promos.formater_alerte(c))


if __name__ == "__main__":
    unittest.main(verbosity=2)
