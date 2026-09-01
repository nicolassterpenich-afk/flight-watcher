"""Destinataires Telegram par surveillance.

Aucun envoi réel : le transport est remplacé par un espion.
"""

from __future__ import annotations

import unittest

from watcher.alerts import Courriel, Telegram, est_une_adresse, remettre


class TelegramEspion(Telegram):
    def __init__(self, echecs=()):
        super().__init__(token="jeton", chat_id="1000")
        self.envois: list[tuple[str, str]] = []
        self.echecs = set(echecs)

    def send(self, text, disable_preview=True, chat_id=None):
        cible = str(chat_id or self.chat_id)
        if cible in self.echecs:
            raise RuntimeError("Telegram : forbidden — bot was blocked by the user")
        self.envois.append((cible, text))


class TestDestinataires(unittest.TestCase):
    def test_sans_destinataire_le_proprietaire_recoit(self):
        t = TelegramEspion()
        self.assertEqual(t.send_to("coucou", []), [])
        self.assertEqual([c for c, _ in t.envois], ["1000"])

    def test_none_equivaut_a_vide(self):
        t = TelegramEspion()
        t.send_to("coucou", None)
        self.assertEqual([c for c, _ in t.envois], ["1000"])

    def test_destinataires_explicites_remplacent_le_defaut(self):
        t = TelegramEspion()
        t.send_to("coucou", ["2000", "3000"])
        self.assertEqual([c for c, _ in t.envois], ["2000", "3000"])

    def test_un_destinataire_injoignable_nempeche_pas_les_autres(self):
        # Telegram refuse d'écrire à qui n'a jamais démarré le bot ; cette
        # erreur ne doit pas faire disparaître l'alerte pour tout le monde.
        t = TelegramEspion(echecs={"2000"})
        echecs = t.send_to("coucou", ["2000", "3000"])
        self.assertEqual(echecs, ["2000"])
        self.assertEqual([c for c, _ in t.envois], ["3000"])

    def test_tous_injoignables(self):
        t = TelegramEspion(echecs={"2000"})
        self.assertEqual(t.send_to("coucou", ["2000"]), ["2000"])
        self.assertEqual(t.envois, [])

    def test_entrees_vides_ignorees(self):
        t = TelegramEspion()
        t.send_to("coucou", ["", "  ", "2000"])
        self.assertEqual([c for c, _ in t.envois], ["2000"])


class CourrielEspion(Courriel):
    def __init__(self, echecs=()):
        super().__init__(host="smtp.test", user="moi@test", password="x")
        self.envois: list[tuple[str, str]] = []
        self.echecs = set(echecs)

    def send(self, html, destinataire, sujet=None):
        if destinataire in self.echecs:
            raise RuntimeError("SMTP : adresse refusée")
        self.envois.append((destinataire, sujet or ""))


class TestFormeDuDestinataire(unittest.TestCase):
    def test_adresses_reconnues(self):
        for a in ("thierry@exemple.be", "nicolas.sterpenich@gmail.com", "a+b@sous.domaine.fr"):
            self.assertTrue(est_une_adresse(a), a)

    def test_identifiants_telegram_ne_sont_pas_des_adresses(self):
        for t in ("1788325058", "-100987654", "", "pas une adresse", "a@b"):
            self.assertFalse(est_une_adresse(t), t)


class TestRepartition(unittest.TestCase):
    def setUp(self):
        self.tg = TelegramEspion()
        self.ml = CourrielEspion()

    def test_chaque_canal_selon_la_forme(self):
        echecs = remettre("<b>coucou</b>", ["2000", "thierry@exemple.be"], self.tg, self.ml)
        self.assertEqual(echecs, [])
        self.assertEqual([c for c, _ in self.tg.envois], ["2000"])
        self.assertEqual([c for c, _ in self.ml.envois], ["thierry@exemple.be"])

    def test_liste_vide_retombe_sur_le_proprietaire(self):
        remettre("coucou", [], self.tg, self.ml)
        self.assertEqual([c for c, _ in self.tg.envois], ["1000"])
        self.assertEqual(self.ml.envois, [])

    def test_une_adresse_fautive_nempeche_pas_les_autres(self):
        ml = CourrielEspion(echecs={"faux@exemple.be"})
        echecs = remettre("coucou", ["faux@exemple.be", "bon@exemple.be", "2000"], self.tg, ml)
        self.assertEqual(echecs, ["faux@exemple.be"])
        self.assertEqual([c for c, _ in ml.envois], ["bon@exemple.be"])
        self.assertEqual([c for c, _ in self.tg.envois], ["2000"])


class TestMiseEnFormeCourriel(unittest.TestCase):
    def test_sujet_tire_de_la_premiere_ligne(self):
        from watcher.alerts.courriel import _sujet_depuis
        self.assertEqual(_sujet_depuis("🎯 <b>Seuil atteint</b>\n\nBRU → BKK"), "🎯 Seuil atteint")

    def test_version_texte_garde_les_liens(self):
        from watcher.alerts.courriel import _texte_nu
        nu = _texte_nu('Voir <a href="https://x.test/a">ici</a>')
        self.assertIn("https://x.test/a", nu)
        self.assertNotIn("<a ", nu)


class TestConfigurationFichier(unittest.TestCase):
    def test_lecture_des_destinataires(self):
        from watcher.config import _as_list_brute
        # Un identifiant de conversation n'est pas un code d'aéroport : il ne
        # doit pas passer en majuscules, et les négatifs des groupes vivent.
        self.assertEqual(_as_list_brute("1788325058, -100987"), ["1788325058", "-100987"])
        self.assertEqual(_as_list_brute(1788325058), ["1788325058"])
        self.assertEqual(_as_list_brute(None), [])
        self.assertEqual(_as_list_brute(["1", " 2 "]), ["1", "2"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
