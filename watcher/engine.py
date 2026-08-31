"""Moteur : interroge les fournisseurs, compare aux seuils, déclenche les alertes."""

from __future__ import annotations

import logging
import random
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

from . import store
from .alerts import Telegram, esc
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

    for origin in watch.origins:
        for destination in watch.destinations:
            if origin == destination:
                continue
            if not watch.ret:
                jobs.extend((origin, destination, d, None) for d in departs)
                continue

            if settings.get("flex_mode", "shift") == "matrix":
                for d in departs:
                    for r in expand_dates(watch.ret, watch.flex_days):
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
            except Exception as exc:               # noqa: BLE001
                errors.append(f"{task[0]} {task[1]}→{task[2]} {task[3]} : {exc}")

    return WatchResult(watch=watch, quotes=quotes, errors=errors)


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
    lines.append(f"\n<i>{esc(best.provider)} • {datetime.now(TZ).strftime('%d/%m %H:%M')}</i>")
    return "\n".join(lines)


# --------------------------------------------------------------------------
# Boucle principale
# --------------------------------------------------------------------------

def run(force_notify: bool = False, only: str | None = None,
        dry_run: bool = False) -> dict[str, Any]:
    watches, raw_settings = load_watches()
    settings = {**DEFAULT_SETTINGS, **(raw_settings or {})}
    telegram = Telegram()
    state = store.load_state()

    active = [w for w in watches if w.enabled and (only is None or w.id == only)]
    log.info("%s surveillance(s) active(s) sur %s", len(active), len(watches))

    summary: dict[str, Any] = {
        "ran_at": iso(),
        "watches": [],
        "alerts_sent": 0,
        "errors": [],
    }
    to_store: list[Quote] = []

    for watch in active:
        started = time.time()
        result = run_watch(watch, settings)
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
                try:
                    telegram.send(message)
                except Exception as exc:            # noqa: BLE001
                    summary["errors"].append(f"Telegram : {exc}")
                    log.error("Envoi Telegram impossible : %s", exc)
                else:
                    if genuine:
                        node["last_alert_price"] = best.price
                        node["last_alert_at"] = iso()
                        node["last_alert_reason"] = reason
            entry["alerted"] = True
            summary["alerts_sent"] += 1

        summary["watches"].append(entry)
        log.info("[%s] meilleur %s %s (%s) — %s", watch.id, best.price, best.currency,
                 f"{best.origin}→{best.destination} {best.depart}", reason)

    if not dry_run:
        store.append_history(to_store)
        store.prune_history()
        store.save_state(state)
        store.save_latest(summary)

    return summary
