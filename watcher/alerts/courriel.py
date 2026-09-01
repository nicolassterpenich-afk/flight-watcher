"""Envoi des alertes par courriel.

Deuxième canal à côté de Telegram, pour les destinataires qui n'ont pas
Telegram — et qui n'ont alors rien à installer ni à démarrer, contrairement
au bot.

Conçu pour Gmail : `smtp.gmail.com` en STARTTLS sur le port 587, avec un mot
de passe d'application. Le mot de passe habituel du compte est refusé depuis
2022, et un mot de passe d'application exige la validation en deux étapes.
Rien de spécifique à Gmail dans le code pour autant : tout passe par la
configuration.
"""

from __future__ import annotations

import logging
import os
import re
import smtplib
from email.message import EmailMessage

log = logging.getLogger(__name__)

ADRESSE_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[A-Za-z]{2,}$")


def est_une_adresse(valeur: str) -> bool:
    return bool(ADRESSE_RE.match(str(valeur).strip()))


def _texte_nu(html: str) -> str:
    """Version lisible sans balises, pour les clients en texte seul."""
    sans_liens = re.sub(r'<a [^>]*href="([^"]+)"[^>]*>(.*?)</a>', r"\2 (\1)", html, flags=re.S)
    return re.sub(r"[ \t]+\n", "\n", re.sub(r"<[^>]+>", "", sans_liens))


class Courriel:
    def __init__(self, host=None, port=None, user=None, password=None, expediteur=None, timeout=25):
        self.host = host or os.environ.get("SMTP_HOST", "smtp.gmail.com")
        self.port = int(port or os.environ.get("SMTP_PORT", "587"))
        self.user = user or os.environ.get("SMTP_USER", "")
        self.password = password or os.environ.get("SMTP_PASSWORD", "")
        self.expediteur = expediteur or os.environ.get("SMTP_FROM", "") or self.user
        self.timeout = timeout

    @property
    def configured(self) -> bool:
        return bool(self.host and self.user and self.password)

    def send(self, html: str, destinataire: str, sujet: str | None = None) -> None:
        if not self.configured:
            log.warning("Courriel non configuré — message ignoré pour %s", destinataire)
            return

        message = EmailMessage()
        message["Subject"] = sujet or _sujet_depuis(html)
        message["From"] = self.expediteur
        message["To"] = destinataire
        message.set_content(_texte_nu(html))
        message.add_alternative(f"<div style=\"font:15px/1.55 system-ui,sans-serif\">{html}</div>",
                                subtype="html")

        with smtplib.SMTP(self.host, self.port, timeout=self.timeout) as serveur:
            serveur.starttls()
            serveur.login(self.user, self.password)
            serveur.send_message(message)


def _sujet_depuis(html: str) -> str:
    """La première ligne de l'alerte fait un bon objet."""
    premiere = _texte_nu(html).strip().splitlines()[0] if html.strip() else "Alerte vol"
    return premiere[:120] or "Alerte vol"
