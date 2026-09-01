"""Moteur : interroge les fournisseurs, compare aux seuils, déclenche les alertes."""

from __future__ import annotations

import logging
import random
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

from . import compagnies, remote, store
from .alerts import Courriel, Telegram, esc, remettre
from .config import expand_dates, load_watches
from .models import Quote, Watch, WatchResult
from .providers import ProviderError, get_provider
from .timeutil import iso, parse as parse_ts, utcnow

log = logging.getLogger(__name__)
TZ = ZoneInfo("Europe/Brussels")

DEFAULT_SETTINGS: dict[str, Any] = {
    "cooldown_hours": 12,          # ne pas répéter la même alerte avant X heures
    "renotify_drop_pct": 5,        # ...sauf si le prix rebaisse de X %
    "drop_pct": 15,                # alerte "chute" si X % sous la médiane 30 j
    "drop_min_samples": 8,         # nb de relevés mini avant d'activer l'alerte chute
    "flex_mode": "shift",          # "shift" (décale l'A/R) ou "matrix" (toutes combinaisons)
    "max_queries_per_run": 120,
    "workers": 3,
    "quiet_hours": None,           # ex. [23, 7] pour couper la nuit
    "history_days": 30,
}


# --------------------------------------------------------------------------
# Génération des requêtes
# --------------------------------------------------------------------------

def build_jobs(watch: Watch, settings: dict[str, Any]) -> list[tuple[str, str, str, str | None]]:
    """(origine, destination, aller, retour) à interroger pour une surveillance."""
    departs = expand_dates(watch.depart, watch.flex_days)
    jobs: list[tuple[str, str, str, str | None]] = []

    # Une souplesse propre au retour décrit forcément des combinaisons : décaler
    # l'aller et le retour du même nombre de jours n'aurait plus de sens.
    flex_ret = watch.flex_days_ret if watch.flex_days_ret is not None else watch.flex_days
    matrix = settings.get("flex_mode", "shift") == "matrix" or flex_ret != watch.flex_days

    for origin in watch.origins:
        for destination in watch.destinations:
            if origin == destination:
                continue
            # Séjour souple : le retour se déduit de chaque date d'aller, ce
            # qui explore les durées au lieu des dates de retour. « Partir en
            # février pour 7 à 10 nuits » ne s'exprime pas autrement.
            nights = watch.nights_range
            if nights is not None:
                for d in departs:
                    dd = datetime.strptime(d, "%Y-%m-%d").date()
                    for n in nights:
                        jobs.append((origin, destination, d, (dd + timedelta(days=n)).isoformat()))
                continue

            if not watch.ret:
                jobs.extend((origin, destination, d, None) for d in departs)
                continue

            if matrix:
                for d in departs:
                    for r in expand_dates(watch.ret, flex_ret):
                        if r > d:
                            jobs.append((origin, destination, d, r))
            else:
                # décale l'aller ET le retour du même nombre de jours :
                # la durée du séjour est conservée
                base_out = datetime.strptime(watch.depart, "%Y-%m-%d").date()
                base_ret = datetime.strptime(watch.ret, "%Y-%m-%d").date()
                span = (base_ret - base_out).days
                for d in departs:
                    dd = datetime.strptime(d, "%Y-%m-%d").date()
                    jobs.append((origin, destination, d, (dd + timedelta(days=span)).isoformat()))
    return jobs


# --------------------------------------------------------------------------
# Exécution
# --------------------------------------------------------------------------

def run_watch(watch: Watch, settings: dict[str, Any]) -> WatchResult:
    jobs = build_jobs(watch, settings)
    cap = int(settings.get("max_queries_per_run", 120))
    tasks = [(p, *j) for j in jobs for p in watch.providers][:cap]
    if len(jobs) * len(watch.providers) > cap:
        log.warning("[%s] %s requêtes demandées, limitées à %s (max_queries_per_run)",
                    watch.id, len(jobs) * len(watch.providers), cap)

    quotes: list[Quote] = []
    errors: list[str] = []
    stats: dict[str, dict[str, Any]] = {}

    def _tally(provider: str, ok: bool, message: str = "") -> None:
        entry = stats.setdefault(provider, {"attempts": 0, "failures": 0, "sample": ""})
        entry["attempts"] += 1
        if not ok:
            entry["failures"] += 1
            entry["sample"] = entry["sample"] or message

    def _one(task):
        provider_name, origin, destination, depart, ret = task
        time.sleep(random.uniform(0, 0.8))          # étale les appels
        provider = get_provider(provider_name)
        return provider.search(watch, origin, destination, depart, ret)

    workers = max(1, int(settings.get("workers", 3)))
    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures = {pool.submit(_one, t): t for t in tasks}
        for fut in as_completed(futures):
            task = futures[fut]
            try:
                quotes.extend(fut.result())
            except ProviderError as exc:
                errors.append(str(exc))
                _tally(task[0], False, str(exc))
            except Exception as exc:               # noqa: BLE001
                message = f"{task[0]} {task[1]}→{task[2]} {task[3]} : {exc}"
                errors.append(message)
                _tally(task[0], False, message)
            else:
                _tally(task[0], True)

    return WatchResult(watch=watch, quotes=quotes, errors=errors, provider_stats=stats)


def best_per_combination(quotes: list[Quote]) -> list[Quote]:
    """Le meilleur prix de chaque (fournisseur, origine, destination, aller, retour).

    Un relevé interroge jusqu'à 21 combinaisons et n'en gardait qu'une : tout
    le reste était perdu, alors que c'est précisément ce qui dit quel jour
    partir. Ces lignes ne vont qu'en base — le fichier JSONL du dépôt, lui,
    garde une ligne par relevé, sinon l'historique committé enflerait d'un
    facteur vingt à chaque passage.

    Le fournisseur fait partie de la clé : sans lui, le moins cher écrase les
    autres sur un même trajet et l'on ne peut plus comparer les sources — ni
    constater qu'une compagnie vend moins cher en direct que via l'agrégateur.
    """
    best: dict[tuple, Quote] = {}
    for q in quotes:
        if not q.price or q.price <= 0:
            continue
        key = (q.provider, q.origin, q.destination, q.depart, q.ret)
        if key not in best or q.price < best[key].price:
            best[key] = q
    return sorted(best.values(), key=lambda q: (q.depart, q.origin, q.provider))

PROVIDER_ALERT_AFTER = 3        # relevés consécutifs entièrement en échec


def check_providers(tally: dict[str, dict[str, int]], state: dict[str, Any],
                    telegram: Telegram, dry_run: bool) -> list[str]:
    """Prévient quand un fournisseur cesse de répondre, et quand il revient.

    L'échec d'un fournisseur ne fait pas échouer un relevé — c'est voulu, un
    fournisseur en panne ne doit pas priver des autres. Mais Ryanair a renvoyé
    409 sur toutes ses routes pendant des semaines sans que ça se voie. On
    compte donc les relevés entièrement ratés, et on alerte au troisième.
    """
    node = state.setdefault("_providers", {})
    messages: list[str] = []

    for name, stats in tally.items():
        if not stats.get("attempts"):
            continue
        entry = node.setdefault(name, {"consecutive_failures": 0, "alerted": False})
        casse = stats["failures"] >= stats["attempts"]

        if not casse:
            if entry.get("alerted"):
                messages.append(f"✅ <b>{esc(name)}</b> répond de nouveau.")
                entry["alerted"] = False
            entry["consecutive_failures"] = 0
            continue

        entry["consecutive_failures"] = entry.get("consecutive_failures", 0) + 1
        if entry["consecutive_failures"] >= PROVIDER_ALERT_AFTER and not entry.get("alerted"):
            exemple = esc((stats.get("sample") or "")[:200])
            messages.append(
                f"⚠️ <b>{esc(name)} ne répond plus</b>\n\n"
                f"{entry['consecutive_failures']} relevés d'affilée sans une seule réponse.\n"
                + (f"\n<code>{exemple}</code>" if exemple else "")
                + "\n\nLes autres fournisseurs continuent ; ce trajet est peut-être surveillé à l'aveugle."
            )
            entry["alerted"] = True

    for message in messages:
        log.warning("Fournisseur : %s", message.split("\n")[0])
        if not dry_run:
            try:
                telegram.send(message)
            except Exception as exc:            # noqa: BLE001
                log.error("Alerte fournisseur non envoyée : %s", exc)
    return messages

# --------------------------------------------------------------------------
# Décision d'alerte
# --------------------------------------------------------------------------

def _in_quiet_hours(settings: dict[str, Any]) -> bool:
    window = settings.get("quiet_hours")
    if not window or len(window) != 2:
        return False
    start, end = int(window[0]), int(window[1])
    hour = datetime.now(TZ).hour
    return start <= hour or hour < end if start > end else start <= hour < end


def decide_alert(watch: Watch, best: Quote, state: dict[str, Any],
                 settings: dict[str, Any]) -> tuple[bool, str, dict[str, Any]]:
    """Renvoie (faut-il alerter, raison lisible, contexte)."""
    prev = state.get(watch.id, {})
    now = utcnow()
    hist = store.stats(watch.id, days=int(settings.get("history_days", 30)))
    ctx = {"stats": hist, "previous": prev}

    last_alert_price = prev.get("last_alert_price")
    last_alert_ts = prev.get("last_alert_at")
    cooldown = timedelta(hours=float(settings.get("cooldown_hours", 12)))
    renotify = float(settings.get("renotify_drop_pct", 5)) / 100.0

    previous_alert = parse_ts(last_alert_ts)
    cooled_down = previous_alert is None or (now - previous_alert) >= cooldown

    # 1) seuil atteint
    if watch.threshold is not None and best.price <= watch.threshold:
        if last_alert_price is None:
            return True, "seuil", ctx
        if best.price <= last_alert_price * (1 - renotify):
            return True, "nouveau_plus_bas", ctx
        if cooled_down:
            return True, "seuil_rappel", ctx
        return False, "seuil déjà notifié (cooldown)", ctx

    # 2) chute inhabituelle par rapport à la médiane
    if watch.alert_on_drop and hist.get("count", 0) >= int(settings.get("drop_min_samples", 8)):
        median = hist["median"]
        drop = float(settings.get("drop_pct", 15)) / 100.0
        if best.price <= median * (1 - drop):
            if last_alert_price is None or best.price <= last_alert_price * (1 - renotify) or cooled_down:
                return True, "chute", ctx
    return False, "au-dessus du seuil", ctx


# --------------------------------------------------------------------------
# Mise en forme du message
# --------------------------------------------------------------------------

REASONS = {
    "seuil": "🎯 <b>Seuil atteint</b>",
    "nouveau_plus_bas": "📉 <b>Nouveau plus bas</b>",
    "seuil_rappel": "🔔 <b>Toujours sous ton seuil</b>",
    "chute": "⚡ <b>Chute de prix inhabituelle</b>",
    "ponctuel": "📍 <b>Prix du moment</b>",
}


def _fmt_price(value: float, currency: str) -> str:
    symbol = {"EUR": "€", "USD": "$", "GBP": "£"}.get(currency, f" {currency}")
    return f"{value:,.0f}{symbol}".replace(",", " ")


def format_alert(watch: Watch, best: Quote, reason: str, ctx: dict[str, Any]) -> str:
    lines = [REASONS.get(reason, "✈️ <b>Alerte prix</b>"), ""]
    lines.append(f"<b>{esc(watch.display())}</b>")
    lines.append(f"{esc(best.origin)} → {esc(best.destination)}  •  {esc(best.depart)}"
                 + (f" ⇄ {esc(best.ret)}" if best.ret else ""))
    lines.append("")
    lines.append(f"💶 <b>{_fmt_price(best.price, best.currency)}</b>"
                 + (f"  (seuil : {_fmt_price(watch.threshold, best.currency)})" if watch.threshold else ""))

    details = []
    if best.airlines:
        details.append(esc(", ".join(best.airlines[:3])))
    if best.stops is not None:
        details.append("direct" if best.stops == 0 else f"{best.stops} escale{'s' if best.stops > 1 else ''}")
    if best.duration_min:
        details.append(f"{best.duration_min // 60}h{best.duration_min % 60:02d}")
    if details:
        lines.append("🛫 " + "  •  ".join(details))
    if best.depart_time:
        lines.append(f"🕑 {esc(best.depart_time)}" + (f" → {esc(best.arrival_time)}" if best.arrival_time else ""))

    stats = ctx.get("stats") or {}
    if stats.get("count", 0) >= 3:
        delta = ""
        if stats.get("median"):
            pct = (best.price - stats["median"]) / stats["median"] * 100
            delta = f" ({pct:+.0f}% vs médiane)"
        lines.append("")
        lines.append(f"📊 30 j : min {_fmt_price(stats['min'], best.currency)}"
                     f" • médiane {_fmt_price(stats['median'], best.currency)}"
                     f" • max {_fmt_price(stats['max'], best.currency)}{delta}")

    prev_price = (ctx.get("previous") or {}).get("last_price")
    if prev_price and prev_price != best.price:
        diff = best.price - prev_price
        lines.append(f"↕️ Depuis le dernier relevé : {diff:+.0f} {best.currency}")

    if best.booking_url:
        lines.append("")
        lines.append(f'<a href="{esc(best.booking_url)}">🔗 Ouvrir la recherche</a>')

    # Vérifier soi-même chez la compagnie : le lien s'ouvre dans le navigateur
    # de l'utilisateur, donc avec sa session et ses tarifs membre. Aucun
    # identifiant n'est stocké de notre côté.
    verifs = compagnies.liens(watch, best)
    if verifs:
        lines.append("")
        lines.append("Vérifier : " + " · ".join(
            f'<a href="{esc(url)}">{esc(nom)}</a>' for _, nom, url in verifs[:5]))
    lines.append(f"\n<i>{esc(best.provider)} • {datetime.now(TZ).strftime('%d/%m %H:%M')}</i>")
    return "\n".join(lines)


# --------------------------------------------------------------------------
# Boucle principale
# --------------------------------------------------------------------------

def load_config(source: str = "auto") -> tuple[list[Watch], dict[str, Any], str]:
    """Charge les surveillances depuis le Worker ou depuis watches.yaml.

    `auto` privilégie le Worker et retombe sur le fichier s'il est injoignable :
    une panne de l'interface ne doit pas interrompre la surveillance.
    """
    if source not in ("auto", "api", "file"):
        raise ValueError(f"source inconnue : {source}")

    if source in ("auto", "api"):
        if not remote.configured():
            if source == "api":
                raise remote.RemoteError("WORKER_URL ou AGENT_TOKEN absent de l'environnement")
        else:
            try:
                watches, settings = remote.load_watches()
                log.info("Configuration lue depuis le Worker (%s surveillance(s))", len(watches))
                return watches, settings, "api"
            except remote.RemoteError as exc:
                if source == "api":
                    raise
                log.warning("Worker injoignable (%s) — repli sur watches.yaml", exc)

    watches, settings = load_watches()
    return watches, settings, "file"


def run(force_notify: bool = False, only: str | None = None,
        dry_run: bool = False, source: str = "auto") -> dict[str, Any]:
    watches, raw_settings, config_source = load_config(source)
    settings = {**DEFAULT_SETTINGS, **(raw_settings or {})}
    telegram = Telegram()
    courriel = Courriel()
    state = store.load_state()

    active = [w for w in watches if w.enabled and (only is None or w.id == only)]
    log.info("%s surveillance(s) active(s) sur %s", len(active), len(watches))

    summary: dict[str, Any] = {
        "ran_at": iso(),
        "source": config_source,
        "watches": [],
        "alerts_sent": 0,
        "errors": [],
    }
    providers_tally: dict[str, dict[str, Any]] = {}
    to_store: list[Quote] = []      # un par surveillance — fichier JSONL et dashboard
    to_push: list[Quote] = []       # un par combinaison de dates — base D1

    for watch in active:
        started = time.time()
        result = run_watch(watch, settings)
        for name, stats in result.provider_stats.items():
            cumul = providers_tally.setdefault(name, {"attempts": 0, "failures": 0, "sample": ""})
            cumul["attempts"] += stats["attempts"]
            cumul["failures"] += stats["failures"]
            cumul["sample"] = cumul["sample"] or stats.get("sample", "")
        best = result.best
        elapsed = round(time.time() - started, 1)

        entry: dict[str, Any] = {
            "id": watch.id,
            "label": watch.display(),
            "threshold": watch.threshold,
            "currency": watch.currency,
            "elapsed_s": elapsed,
            "quotes_found": len(result.quotes),
            "errors": result.errors[:5],
        }
        summary["errors"].extend(result.errors[:3])

        if not best:
            entry["status"] = "aucun prix"
            log.warning("[%s] aucun prix trouvé (%s erreurs)", watch.id, len(result.errors))
            summary["watches"].append(entry)
            continue

        to_store.append(best)
        combinations = best_per_combination(result.quotes)
        to_push.extend(combinations)
        entry["combinations"] = len(combinations)
        should, reason, ctx = decide_alert(watch, best, state, settings)
        # Un relevé forcé (/check, "Run workflow") doit toujours répondre, mais
        # il ne doit pas se faire passer pour une vraie alerte de seuil ni
        # démarrer un cooldown qui masquerait la prochaine.
        genuine = should
        if force_notify and not should:
            should, reason = True, "ponctuel"

        entry.update({
            "status": "ok",
            "best_price": best.price,
            "best_route": f"{best.origin}→{best.destination}",
            "best_depart": best.depart,
            "best_return": best.ret,
            "provider": best.provider,
            "airlines": best.airlines,
            "booking_url": best.booking_url,
            "reason": reason,
            "alerted": False,
            "stats": ctx.get("stats"),
        })

        node = state.setdefault(watch.id, {})
        node["last_price"] = best.price
        node["last_checked_at"] = best.checked_at
        node["best_ever"] = min(best.price, node.get("best_ever", best.price))

        quiet = _in_quiet_hours(settings) and not force_notify
        if should and quiet:
            entry["status"] = "alerte reportée (heures silencieuses)"
        elif should:
            message = format_alert(watch, best, reason, ctx)
            if dry_run:
                print("\n--- ALERTE (dry-run) ---\n" + message + "\n")
            else:
                echecs = remettre(message, watch.destinataires, telegram, courriel)
                if echecs:
                    summary["errors"].append(
                        f"Telegram : alerte non remise à {', '.join(echecs)}")
                # Une remise partielle reste une alerte envoyée : on ne
                # rejouera pas la même demain sous prétexte qu'un des
                # destinataires n'a pas démarré le bot.
                if len(echecs) < len(watch.destinataires or [telegram.chat_id]):
                    if genuine:
                        node["last_alert_price"] = best.price
                        node["last_alert_at"] = iso()
                        node["last_alert_reason"] = reason
            entry["alerted"] = True
            summary["alerts_sent"] += 1

        summary["watches"].append(entry)
        log.info("[%s] meilleur %s %s (%s) — %s", watch.id, best.price, best.currency,
                 f"{best.origin}→{best.destination} {best.depart}", reason)

    summary["providers"] = providers_tally
    summary["provider_alerts"] = check_providers(providers_tally, state, telegram, dry_run)

    if not dry_run:
        store.append_history(to_store)
        store.prune_history()
        # Les stats ont été calculées avant l'enregistrement du relevé courant :
        # on les rafraîchit pour que le dashboard affiche le bon total.
        for entry in summary["watches"]:
            if entry.get("status") == "ok":
                entry["stats"] = store.stats(entry["id"],
                                             days=int(settings.get("history_days", 30)))
        store.save_state(state)
        store.save_latest(summary)

        # Le Worker reçoit les prix même quand la config vient du fichier : le
        # repli n'a pu concerner qu'un GET, et l'échec d'un envoi ne doit
        # jamais faire échouer un relevé déjà abouti.
        if remote.configured():
            try:
                pushed = remote.push_results(summary["ran_at"], summary["watches"], to_push, state)
                log.info("Worker mis à jour : %s prix sur %s surveillance(s)",
                         pushed.get("quotes"), pushed.get("watches"))
                summary["pushed"] = True
            except remote.RemoteError as exc:
                log.error("Envoi au Worker impossible : %s", exc)
                summary["errors"].append(f"Worker : {exc}")
                summary["pushed"] = False

    return summary
