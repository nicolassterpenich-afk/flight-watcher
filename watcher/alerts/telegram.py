"""Envoi des alertes et lecture des commandes via un bot Telegram."""

from __future__ import annotations

import html
import logging
import os
from typing import Any

import requests

log = logging.getLogger(__name__)
API = "https://api.telegram.org/bot{token}/{method}"


class Telegram:
    def __init__(self, token: str | None = None, chat_id: str | None = None, timeout: int = 25):
        self.token = token or os.environ.get("TELEGRAM_BOT_TOKEN", "")
        self.chat_id = chat_id or os.environ.get("TELEGRAM_CHAT_ID", "")
        self.timeout = timeout

    @property
    def configured(self) -> bool:
        return bool(self.token and self.chat_id)

    def _call(self, method: str, **params: Any) -> dict[str, Any]:
        if not self.token:
            raise RuntimeError("TELEGRAM_BOT_TOKEN manquant")
        resp = requests.post(API.format(token=self.token, method=method),
                             json=params, timeout=self.timeout)
        data = resp.json() if resp.content else {}
        if not data.get("ok"):
            raise RuntimeError(f"Telegram {method} a échoué : {data.get('description', resp.text[:200])}")
        return data

    def send(self, text: str, disable_preview: bool = True, chat_id: str | None = None) -> None:
        cible = str(chat_id or self.chat_id or "")
        if not self.token or not cible:
            log.warning("Telegram non configuré — message ignoré :\n%s", text)
            return
        # Telegram coupe à 4096 caractères
        for chunk in _split(text, 3900):
            self._call("sendMessage", chat_id=cible, text=chunk,
                       parse_mode="HTML", disable_web_page_preview=disable_preview)

    def send_to(self, text: str, destinataires: list[str] | None) -> list[str]:
        """Envoie à chaque destinataire, ou au propriétaire si la liste est vide.

        Un destinataire injoignable n'empêche pas les autres de recevoir :
        Telegram refuse d'écrire à qui n'a jamais démarré le bot, et cette
        erreur-là ne doit pas faire disparaître l'alerte pour tout le monde.
        """
        cibles = [str(c) for c in (destinataires or []) if str(c).strip()] or [self.chat_id]
        echecs: list[str] = []
        for cible in cibles:
            try:
                self.send(text, chat_id=cible)
            except Exception as exc:                # noqa: BLE001
                log.error("Alerte non remise à %s : %s", cible, exc)
                echecs.append(str(cible))
        return echecs

    def get_updates(self, offset: int | None = None) -> list[dict[str, Any]]:
        if not self.token:
            return []
        params: dict[str, Any] = {"timeout": 0, "allowed_updates": ["message"]}
        if offset:
            params["offset"] = offset
        try:
            return self._call("getUpdates", **params).get("result", [])
        except Exception as exc:
            log.warning("Lecture des commandes Telegram impossible : %s", exc)
            return []


def esc(value: Any) -> str:
    return html.escape(str(value), quote=False)


def _split(text: str, size: int) -> list[str]:
    if len(text) <= size:
        return [text]
    parts, current = [], ""
    for line in text.split("\n"):
        if len(current) + len(line) + 1 > size:
            parts.append(current)
            current = line
        else:
            current = f"{current}\n{line}" if current else line
    if current:
        parts.append(current)
    return parts
