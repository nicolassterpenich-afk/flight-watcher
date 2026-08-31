"""Point d'entrée en ligne de commande."""

from __future__ import annotations

import argparse
import json
import logging
import sys

from . import bot, store
from .alerts import Telegram
from .config import load_watches
from .engine import DEFAULT_SETTINGS, run


def _setup_logging(verbose: bool) -> None:
    logging.basicConfig(
        level=logging.DEBUG if verbose else logging.INFO,
        format="%(asctime)s %(levelname)-7s %(name)s — %(message)s",
        datefmt="%H:%M:%S",
    )
    logging.getLogger("urllib3").setLevel(logging.WARNING)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="flight-watcher",
                                     description="Surveillance de prix de vols avec alertes Telegram")
    sub = parser.add_subparsers(dest="command", required=True)

    p_run = sub.add_parser("run", help="Relève les prix et envoie les alertes")
    p_run.add_argument("--watch", help="Ne traiter qu'une surveillance (son id)")
    p_run.add_argument("--force-notify", action="store_true", help="Alerter même si le seuil n'est pas atteint")
    p_run.add_argument("--dry-run", action="store_true", help="Afficher les alertes sans les envoyer")
    p_run.add_argument("--no-commands", action="store_true", help="Ne pas lire les commandes Telegram")

    sub.add_parser("commands", help="Traiter uniquement les commandes Telegram en attente")
    sub.add_parser("list", help="Afficher les surveillances configurées")
    sub.add_parser("selftest", help="Vérifier la config, les fournisseurs et Telegram")

    p_stats = sub.add_parser("stats", help="Statistiques d'une surveillance")
    p_stats.add_argument("watch_id")
    p_stats.add_argument("--days", type=int, default=30)

    parser.add_argument("-v", "--verbose", action="store_true")
    args = parser.parse_args(argv)
    _setup_logging(args.verbose)

    if args.command == "list":
        watches, settings = load_watches()
        print(f"{len(watches)} surveillance(s) — réglages : {json.dumps({**DEFAULT_SETTINGS, **settings})}\n")
        for w in watches:
            flag = "" if w.enabled else "  [en pause]"
            print(f"  {w.id:<24} {w.display()}  seuil={w.threshold}{flag}")
        return 0

    if args.command == "stats":
        data = store.stats(args.watch_id, days=args.days)
        print(json.dumps(data, indent=2, ensure_ascii=False))
        return 0

    if args.command == "commands":
        print(json.dumps(bot.process_commands(), indent=2, ensure_ascii=False))
        return 0

    if args.command == "selftest":
        return selftest()

    force_check = False
    if not args.no_commands:
        try:
            outcome = bot.process_commands()
            force_check = bool(outcome.get("force_check"))
            if outcome.get("processed"):
                logging.info("%s commande(s) Telegram traitée(s)", outcome["processed"])
        except Exception as exc:                    # noqa: BLE001
            logging.error("Traitement des commandes impossible : %s", exc)

    # /check (ou l'ajout d'une surveillance) attend une réponse : on notifie
    # même si le seuil n'est pas atteint, sinon la commande reste sans suite.
    if force_check:
        logging.info("Relevé demandé depuis Telegram — notification forcée")

    summary = run(force_notify=args.force_notify or force_check,
                  only=args.watch, dry_run=args.dry_run)
    print(json.dumps(summary, indent=2, ensure_ascii=False))
    return 0                                        # les erreurs partielles ne font pas échouer le job


def selftest() -> int:
    ok = True
    print("== Configuration ==")
    watches, settings = load_watches()
    print(f"  {len(watches)} surveillance(s), {sum(1 for w in watches if w.enabled)} active(s)")
    if not watches:
        print("  ⚠️  aucune surveillance dans watches.yaml")

    print("\n== Fournisseurs ==")
    from .models import Watch as W
    from .providers import get_provider

    # Une route de test connue pour être desservie par chaque fournisseur.
    ROUTES = {"google_flights": ("BRU", "MAD"), "ryanair": ("CRL", "FCO")}
    names = {p for w in watches for p in w.providers} or {"google_flights"}
    for name in sorted(names):
        origin, destination = ROUTES.get(name, ("BRU", "MAD"))
        probe = W(id="selftest", origins=[origin], destinations=[destination],
                  depart=_soon(), threshold=None)
        try:
            quotes = get_provider(name).search(probe, origin, destination, probe.depart, None)
            if quotes:
                cheapest = min(q.price for q in quotes)
                print(f"  ✅ {name} : {len(quotes)} offre(s) {origin}→{destination}, "
                      f"à partir de {cheapest:.0f} EUR")
            else:
                print(f"  ⚠️  {name} : joignable mais aucune offre {origin}→{destination}")
        except Exception as exc:                    # noqa: BLE001
            ok = False
            print(f"  ❌ {name} : {exc}")

    print("\n== Telegram ==")
    telegram = Telegram()
    if not telegram.configured:
        ok = False
        print("  ❌ TELEGRAM_BOT_TOKEN / TELEGRAM_CHAT_ID manquants")
    else:
        try:
            telegram.send("✅ <b>flight-watcher</b> — test de connexion réussi.")
            print("  ✅ message de test envoyé")
        except Exception as exc:                    # noqa: BLE001
            ok = False
            print(f"  ❌ {exc}")

    print("\n" + ("✅ Tout est prêt." if ok else "⚠️  Corrige les points ❌ ci-dessus."))
    return 0 if ok else 1


def _soon() -> str:
    from datetime import date, timedelta
    return (date.today() + timedelta(days=45)).isoformat()


if __name__ == "__main__":
    sys.exit(main())
