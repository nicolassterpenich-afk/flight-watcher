"""Gabarits d'URL des compagnies — brique 3."""

from __future__ import annotations

import unittest

from watcher import compagnies
from watcher.models import Quote, Watch


def vol(origin="BRU", destination="BKK", depart="2027-02-06", ret="2027-02-20",
        airlines=("British Airways",)) -> Quote:
    return Quote(watch_id="t", provider="google_flights", origin=origin, destination=destination,
                 depart=depart, ret=ret, price=598.0, currency="EUR", airlines=list(airlines))


def surveillance(adultes=1) -> Watch:
    w = Watch(id="t", origins=["BRU"], destinations=["BKK"], depart="2027-02-06", ret="2027-02-20")
    w.passengers.adults = adultes
    return w


class TestTable(unittest.TestCase):
    def test_table_lue_depuis_linterface(self):
        # Une seule source de vérité, comme pour les aéroports.
        self.assertGreater(len(compagnies.table()), 20)

    def test_genres_connus(self):
        self.assertTrue(all(g in ("meta", "deep", "home") for g, _, _ in compagnies.table()))

    def test_pas_de_doublon(self):
        noms = [n.lower() for _, n, _ in compagnies.table()]
        self.assertEqual(len(noms), len(set(noms)))

    def test_les_gabarits_profonds_portent_des_jetons(self):
        for genre, nom, gabarit in compagnies.table():
            if genre in ("deep", "meta"):
                self.assertIn("{", gabarit, f"{nom} n'a aucun jeton")
            else:
                self.assertNotIn("{", gabarit, f"{nom} est un accueil, il ne doit rien substituer")


class TestRemplissage(unittest.TestCase):
    def remplir(self, gabarit, **kw):
        return compagnies.remplir(gabarit, vol(**kw), kw.pop("adultes", 1))

    def test_jetons_de_base(self):
        u = compagnies.remplir("x/{origin}/{destination}/{depart}/{ret}", vol())
        self.assertEqual(u, "x/BRU/BKK/2027-02-06/2027-02-20")

    def test_minuscules_et_dates_compactes(self):
        u = compagnies.remplir("{origin_l}/{destination_l}/{depart_c}/{ret_c}", vol())
        self.assertEqual(u, "bru/bkk/270206/270220")

    def test_jeton_long_avant_jeton_court(self):
        # {ret_c} ne doit pas être mangé par {ret}.
        self.assertEqual(compagnies.remplir("{ret_c}|{ret}", vol()), "270220|2027-02-20")

    def test_aller_simple(self):
        u = compagnies.remplir("{est_ar}|{ret_ou_null}|{ret_c}", vol(ret=None))
        self.assertEqual(u, "false|null|")

    def test_passagers(self):
        self.assertEqual(compagnies.remplir("a={adults}", vol(), 3), "a=3")

    def test_double_slash_resorbe(self):
        # Un retour vide laisse « // » au milieu du chemin.
        u = compagnies.remplir("https://x.test/vols/{depart}/{ret}/suite", vol(ret=None))
        self.assertEqual(u, "https://x.test/vols/2027-04-10/suite".replace("2027-04-10", "2027-02-06"))

    def test_le_schema_nest_pas_abime(self):
        self.assertTrue(compagnies.remplir("https://x.test/{origin}", vol()).startswith("https://"))


class TestLiens(unittest.TestCase):
    def test_compagnie_connue_et_moteurs(self):
        res = compagnies.liens(surveillance(), vol())
        noms = [n for _, n, _ in res]
        self.assertIn("British Airways", noms)
        self.assertIn("Skyscanner", noms)
        self.assertIn("Kayak", noms)

    def test_compagnie_inconnue_ignoree(self):
        res = compagnies.liens(surveillance(), vol(airlines=("Compagnie Fantôme",)))
        self.assertEqual([n for g, n, _ in res if g != "meta"], [])

    def test_sans_compagnie_les_moteurs_restent(self):
        res = compagnies.liens(surveillance(), vol(airlines=()))
        self.assertEqual({g for g, _, _ in res}, {"meta"})

    def test_moteurs_desactivables(self):
        self.assertEqual(compagnies.liens(surveillance(), vol(airlines=()), avec_meta=False), [])

    def test_toutes_les_urls_sont_absolues(self):
        for _, _, url in compagnies.liens(surveillance(2), vol()):
            self.assertTrue(url.startswith("https://"), url)
            self.assertNotIn("{", url, "un jeton n'a pas été substitué")


if __name__ == "__main__":
    unittest.main(verbosity=2)
