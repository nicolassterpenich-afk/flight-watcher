"""Destinataires Telegram par surveillance.

Aucun envoi réel : le transport est remplacé par un espion.
"""

from __future__ import annotations

import unittest

from watcher.alerts import Telegram


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
