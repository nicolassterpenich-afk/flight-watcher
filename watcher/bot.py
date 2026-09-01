"""Commandes Telegram : gérer les surveillances depuis le téléphone.

Les commandes en attente sont lues au début de chaque run GitHub Actions,
appliquées à la même source que le moteur : la base du Worker quand elle
répond, `watches.yaml` sinon. Écrire dans le fichier alors que le moteur
lit la base ferait disparaître les commandes sans le dire.
"""

from __future__ import annotations

import logging
import re
from datetime import datetime
from typing import Any

from . import remote, store
from .alerts import Telegram, esc
from .config import save_watches
from .engine import load_config
from .models import Watch

log = logging.getLogger(__name__)

DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
IATA_RE = re.compile(r"^[A-Z]{3}$")

HELP = """<b>Commandes disponibles</b>

/list — tes surveillances et leurs derniers prix
/add BRU BKK 2026-11-15 2026-11-29 600
    origine, destination, aller, [retour], [seuil €]
    options : <code>flex=3</code> <code>escales=1</code> <code>pax=2</code> <code>#Mon libellé</code>
/seuil &lt;id&gt; &lt;prix&gt; — change le seuil
/pause &lt;id&gt; · /reprendre &lt;id&gt;
/monid — ton identifiant, à donner pour recevoir des alertes
/suppr &lt;id&gt; — supprime une surveillance
/stats &lt;id&gt; — historique des 30 derniers jours
/check — relève les prix maintenant
/aide — ce message

Plusieurs aéroports : <code>/add BRU,CRL BKK 2026-11-15 600</code>"""


def _slug(origins: list[str], destinations: list[str], depart: str) -> str:
    base = f"{origins[0]}-{destinations[0]}-{depart[5:]}".lower()
    return re.sub(r"[^a-z0-9-]", "", base)


def _find(watches: list[Watch], token: str) -> Watch | None:
    token = token.strip().lower()
    for w in watches:
        if w.id.lower() == token:
            return w
    matches = [w for w in watches if token in w.id.lower() or token in w.display().lower()]
    return matches[0] if len(matches) == 1 else None


def _fmt(value: float | None, currency: str = "EUR") -> str:
    if value is None:
        return "—"
    symbol = {"EUR": "€", "USD": "$", "GBP": "£"}.get(currency, f" {currency}")
    return f"{value:,.0f}{symbol}".replace(",", " ")


# --------------------------------------------------------------------------
# Commandes
# --------------------------------------------------------------------------

def cmd_list(watches: list[Watch], state: dict) -> str:
    if not watches:
        return "Aucune surveillance. Ajoute-en une avec /add — /aide pour la syntaxe."
    lines = ["<b>✈️ Tes surveillances</b>", ""]
    for w in watches:
        node = state.get(w.id, {})
        last, best = node.get("last_price"), node.get("best_ever")
        flag = "" if w.enabled else " ⏸"
        lines.append(f"<b>{esc(w.display())}</b>{flag}")
        lines.append(f"  <code>{esc(w.id)}</code> · seuil {_fmt(w.threshold, w.currency)}")
        detail = f"  actuel {_fmt(last, w.currency)}"
        if best and last and best < last:
            detail += f" · meilleur vu {_fmt(best, w.currency)}"
        if last and w.threshold:
            detail += " ✅" if last <= w.threshold else f" (−{last - w.threshold:.0f} à faire)"
        lines.append(detail)
        lines.append("")
    return "\n".join(lines).strip()


def cmd_add(args: list[str], watches: list[Watch]) -> tuple[str, bool]:
    raw = " ".join(args)
    label = ""
    if "#" in raw:
        raw, label = raw.split("#", 1)
        label = label.strip()

    options: dict[str, str] = {}
    tokens: list[str] = []
    for token in raw.split():
        if "=" in token:
            key, _, value = token.partition("=")
            options[key.strip().lower()] = value.strip()
        else:
            tokens.append(token)

    if len(tokens) < 3:
        return "❌ Il me faut au minimum : origine, destination et date d'aller.\nEx : <code>/add BRU BKK 2026-11-15 600</code>", False

    origins = [o.upper() for o in tokens[0].replace(";", ",").split(",") if o]
    destinations = [d.upper() for d in tokens[1].replace(";", ",").split(",") if d]
    for code in origins + destinations:
        if not IATA_RE.match(code):
            return f"❌ <code>{esc(code)}</code> n'est pas un code aéroport à 3 lettres (ex : BRU, CRL, BKK).", False

    dates = [t for t in tokens[2:] if DATE_RE.match(t)]
    numbers = [t for t in tokens[2:] if not DATE_RE.match(t) and t.replace(".", "").isdigit()]
    if not dates:
        return "❌ Date d'aller manquante ou mal formatée (attendu AAAA-MM-JJ).", False

    depart = dates[0]
    ret = dates[1] if len(dates) > 1 else None
    if ret and ret <= depart:
        return "❌ La date de retour doit être après l'aller.", False
    try:
        if datetime.strptime(depart, "%Y-%m-%d").date() < datetime.now().date():
            return "❌ La date d'aller est déjà passée.", False
    except ValueError:
        return "❌ Date invalide.", False

    threshold = float(numbers[0]) if numbers else (float(options["seuil"]) if "seuil" in options else None)

    wid = _slug(origins, destinations, depart)
    suffix = 2
    existing = {w.id for w in watches}
    while wid in existing:
        wid = f"{_slug(origins, destinations, depart)}-{suffix}"
        suffix += 1

    watch = Watch(
        id=wid,
        label=label,
        origins=origins,
        destinations=destinations,
        depart=depart,
        ret=ret,
        threshold=threshold,
        flex_days=int(options.get("flex", 0) or 0),
        max_stops=int(options["escales"]) if "escales" in options else None,
    )
    if "pax" in options:
        watch.passengers.adults = max(1, int(options["pax"]))

    watches.append(watch)
    return (f"✅ Surveillance ajoutée : <b>{esc(watch.display())}</b>\n"
            f"id <code>{esc(wid)}</code> · seuil {_fmt(threshold)}\n"
            f"Premier relevé dans quelques secondes."), True


def cmd_threshold(args: list[str], watches: list[Watch]) -> tuple[str, bool]:
    if len(args) < 2:
        return "❌ Usage : <code>/seuil &lt;id&gt; &lt;prix&gt;</code>", False
    watch = _find(watches, args[0])
    if not watch:
        return f"❌ Surveillance <code>{esc(args[0])}</code> introuvable.", False
    try:
        watch.threshold = float(args[1].replace(",", "."))
    except ValueError:
        return "❌ Prix invalide.", False
    return f"✅ Seuil de <b>{esc(watch.display())}</b> fixé à {_fmt(watch.threshold, watch.currency)}.", True


def cmd_toggle(args: list[str], watches: list[Watch], enable: bool) -> tuple[str, bool]:
    if not args:
        return "❌ Précise l'id de la surveillance.", False
    watch = _find(watches, args[0])
    if not watch:
        return f"❌ Surveillance <code>{esc(args[0])}</code> introuvable.", False
    watch.enabled = enable
    return f"{'▶️ Reprise' if enable else '⏸ En pause'} : <b>{esc(watch.display())}</b>", True


def cmd_delete(args: list[str], watches: list[Watch]) -> tuple[str, bool]:
    if not args:
        return "❌ Précise l'id de la surveillance.", False
    watch = _find(watches, args[0])
    if not watch:
        return f"❌ Surveillance <code>{esc(args[0])}</code> introuvable.", False
    watches.remove(watch)
    return f"🗑 Supprimée : <b>{esc(watch.display())}</b>", True


def cmd_stats(args: list[str], watches: list[Watch]) -> str:
    if not args:
        return "❌ Usage : <code>/stats &lt;id&gt;</code>"
    watch = _find(watches, args[0])
    if not watch:
        return f"❌ Surveillance <code>{esc(args[0])}</code> introuvable."
    s = store.stats(watch.id, days=30)
    if not s.get("count"):
        return f"Pas encore d'historique pour <b>{esc(watch.display())}</b>."
    rows = store.read_history(watch.id, days=30)[-8:]
    lines = [f"<b>📊 {esc(watch.display())}</b> — 30 derniers jours", "",
             f"relevés : {s['count']}",
             f"min {_fmt(s['min'], watch.currency)} · médiane {_fmt(s['median'], watch.currency)}"
             f" · max {_fmt(s['max'], watch.currency)}", "", "<b>Derniers relevés</b>"]
    for row in rows:
        when = row.get("checked_at", "")[:16].replace("T", " ")
        lines.append(f"  {esc(when)} — {_fmt(row.get('price'), watch.currency)}"
                     f" ({esc(row.get('origin'))}→{esc(row.get('destination'))} {esc(row.get('depart'))})")
    return "\n".join(lines)


# --------------------------------------------------------------------------
# Boucle de traitement
# --------------------------------------------------------------------------

def process_commands() -> dict[str, Any]:
    """Lit les commandes en attente et applique les changements."""
    telegram = Telegram()
    if not telegram.configured:
        return {"processed": 0, "force_check": False}

    state = store.load_state()
    meta = state.setdefault("_telegram", {})
    offset = meta.get("last_update_id")
    updates = telegram.get_updates(offset + 1 if offset else None)
    if not updates:
        return {"processed": 0, "force_check": False}

    watches, settings, source = load_config("auto")
    dirty = False
    force_check = False
    processed = 0
    allowed = str(telegram.chat_id)

    for update in updates:
        meta["last_update_id"] = max(meta.get("last_update_id", 0), update.get("update_id", 0))
        message = update.get("message") or {}
        text = (message.get("text") or "").strip()
        chat = str((message.get("chat") or {}).get("id", ""))
        if not text.startswith("/"):
            continue

        # Un bot Telegram ne peut écrire qu'à qui lui a déjà parlé. Pour
        # recevoir les alertes d'une destination, un proche doit donc démarrer
        # le bot puis communiquer son identifiant : /monid le lui donne. C'est
        # le seul ordre accepté d'une conversation non autorisée, et il ne
        # révèle que ce que l'appelant sait déjà de lui-même.
        if text.split()[0].lstrip("/").split("@")[0].lower() in {"monid", "myid", "id"}:
            try:
                telegram.send(
                    "Ton identifiant de conversation :\n"
                    f"<code>{esc(chat)}</code>\n\n"
                    "Transmets-le au propriétaire de la veille pour recevoir "
                    "les alertes d'une destination.",
                    chat_id=chat)
            except Exception as exc:                # noqa: BLE001
                log.error("Réponse /monid impossible : %s", exc)
            processed += 1
            continue

        if chat != allowed:
            log.warning("Commande ignorée : chat %s non autorisé", chat)
            continue

        processed += 1
        parts = text.split()
        command = parts[0].lstrip("/").split("@")[0].lower()
        args = parts[1:]
        reply, changed = "", False

        if command in {"aide", "help", "start"}:
            reply = HELP
        elif command in {"list", "liste", "ls"}:
            reply = cmd_list(watches, state)
        elif command in {"add", "ajoute", "ajouter"}:
            reply, changed = cmd_add(args, watches)
            force_check = force_check or changed
        elif command in {"seuil", "threshold"}:
            reply, changed = cmd_threshold(args, watches)
        elif command in {"pause", "stop"}:
            reply, changed = cmd_toggle(args, watches, False)
        elif command in {"reprendre", "resume", "start_watch"}:
            reply, changed = cmd_toggle(args, watches, True)
        elif command in {"suppr", "supprimer", "del", "delete", "rm"}:
            reply, changed = cmd_delete(args, watches)
        elif command in {"stats", "historique"}:
            reply = cmd_stats(args, watches)
        elif command in {"check", "verifie", "vérifie"}:
            reply, force_check = "🔄 Relevé en cours, je te réponds juste après…", True
        else:
            reply = f"Commande inconnue : <code>{esc(command)}</code>\n\n{HELP}"

        dirty = dirty or changed
        if reply:
            try:
                telegram.send(reply)
            except Exception as exc:                # noqa: BLE001
                log.error("Réponse Telegram impossible : %s", exc)

    if dirty:
        _persist(watches, settings, source, telegram)
    store.save_state(state)
    return {"processed": processed, "force_check": force_check,
            "config_changed": dirty, "source": source}


def _persist(watches: list[Watch], settings: dict[str, Any], source: str,
             telegram: Telegram) -> None:
    """Écrit là où le moteur lira, et prévient l'utilisateur si ça échoue."""
    if source != "api":
        save_watches(watches, settings)
        return

    try:
        outcome = remote.replace_watches(watches)
        log.info("Worker mis à jour : %s surveillance(s), %s supprimée(s)",
                 outcome.get("upserted"), len(outcome.get("removed") or []))
    except remote.RemoteError as exc:
        # Sans ça, la commande paraîtrait avoir abouti — la réponse Telegram
        # est déjà partie — alors que rien n'aurait été enregistré.
        log.error("Écriture des surveillances impossible : %s", exc)
        try:
            telegram.send("⚠️ Ta commande n'a pas pu être enregistrée : "
                          f"{esc(str(exc)[:200])}\nRéessaie dans un moment.")
        except Exception:                           # noqa: BLE001
            pass

