#!/usr/bin/env python3
"""Migre watches.yaml + data/*.json vers la base D1 du Worker (étape 5).

Les surveillances passent par l'API (elles sont donc validées comme celles
créées depuis l'interface) ; l'historique et l'état des alertes sont injectés
en SQL, parce qu'ils n'ont pas d'endpoint d'écriture en masse.

  WORKER_URL=https://… APP_PASSWORD=… python worker/scripts/migrate-from-yaml.py [--apply]

Sans --apply, le script montre ce qu'il ferait sans rien écrire.
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from pathlib import Path

import requests

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from watcher.config import load_watches                      # noqa: E402

WORKER_URL = os.environ.get("WORKER_URL", "").rstrip("/")
APP_PASSWORD = os.environ.get("APP_PASSWORD", "")


def sql_str(value) -> str:
    if value is None:
        return "NULL"
    if isinstance(value, bool):
        return "1" if value else "0"
    if isinstance(value, (int, float)):
        return repr(value)
    return "'" + str(value).replace("'", "''") + "'"


def login() -> requests.Session:
    if not WORKER_URL or not APP_PASSWORD:
        sys.exit("WORKER_URL et APP_PASSWORD sont requis dans l'environnement.")
    session = requests.Session()
    res = session.post(f"{WORKER_URL}/api/login", json={"password": APP_PASSWORD}, timeout=20)
    if not res.ok:
        sys.exit(f"Connexion refusée par le Worker : {res.status_code} {res.text[:200]}")
    return session


def push_watches(session: requests.Session, apply: bool) -> None:
    watches, _ = load_watches()
    print(f"\n== {len(watches)} surveillance(s) dans watches.yaml ==")
    for w in watches:
        payload = {
            "id": w.id, "label": w.label, "origins": w.origins, "destinations": w.destinations,
            "depart": w.depart, "ret": w.ret, "threshold": w.threshold, "currency": w.currency,
            "seat": w.seat, "max_stops": w.max_stops, "flex_days": w.flex_days,
            "passengers": {"adults": w.passengers.adults, "children": w.passengers.children,
                           "infants_in_seat": w.passengers.infants_in_seat,
                           "infants_on_lap": w.passengers.infants_on_lap},
            "providers": w.providers, "enabled": w.enabled,
            "alert_on_drop": w.alert_on_drop, "notes": w.notes,
        }
        if not apply:
            print(f"  [simulation] POST /api/watches  {w.id}")
            continue
        res = session.post(f"{WORKER_URL}/api/watches", json=payload, timeout=20)
        if res.status_code == 409:
            print(f"  = {w.id} : déjà présente, ignorée")
        elif res.ok:
            print(f"  + {w.id} : créée")
        else:
            print(f"  ! {w.id} : {res.status_code} {res.text[:160]}")


def build_sql() -> list[str]:
    """INSERT pour l'historique et l'état des alertes."""
    statements: list[str] = []

    history = ROOT / "data" / "history.jsonl"
    rows = 0
    if history.exists():
        for line in history.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                r = json.loads(line)
            except json.JSONDecodeError:
                continue
            if not r.get("watch_id") or not r.get("price"):
                continue
            rows += 1
            statements.append(
                "INSERT OR IGNORE INTO history (watch_id, provider, origin, destination, depart, ret,"
                " price, currency, airlines, stops, duration_min, booking_url, checked_at) VALUES ("
                + ", ".join(sql_str(v) for v in (
                    r["watch_id"], r.get("provider", "unknown"), r.get("origin", ""),
                    r.get("destination", ""), r.get("depart", ""), r.get("ret"),
                    float(r["price"]), r.get("currency", "EUR"),
                    json.dumps(r.get("airlines") or [], ensure_ascii=False),
                    r.get("stops"), r.get("duration_min"), r.get("booking_url"),
                    r.get("checked_at"),
                )) + ");")
    print(f"\n== {rows} ligne(s) d'historique ==")

    state_path = ROOT / "data" / "state.json"
    if state_path.exists():
        state = json.loads(state_path.read_text(encoding="utf-8"))
        nodes = {k: v for k, v in state.items() if not k.startswith("_")}
        print(f"== {len(nodes)} état(s) d'alerte ==")
        for wid, node in nodes.items():
            statements.append(
                "INSERT INTO alert_state (watch_id, last_price, best_ever, last_alert_price,"
                " last_alert_at, last_alert_reason, last_checked_at, status) VALUES ("
                + ", ".join(sql_str(v) for v in (
                    wid, node.get("last_price"), node.get("best_ever"), node.get("last_alert_price"),
                    node.get("last_alert_at"), node.get("last_alert_reason"),
                    node.get("last_checked_at"), "ok",
                )) + ") ON CONFLICT(watch_id) DO UPDATE SET"
                " last_price=excluded.last_price, best_ever=excluded.best_ever,"
                " last_alert_price=excluded.last_alert_price, last_alert_at=excluded.last_alert_at,"
                " last_alert_reason=excluded.last_alert_reason,"
                " last_checked_at=excluded.last_checked_at;")
    return statements


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--apply", action="store_true", help="Écrit vraiment dans D1")
    args = parser.parse_args()

    session = login() if args.apply else requests.Session()
    push_watches(session, args.apply)

    statements = build_sql()
    out = ROOT / "worker" / "migration.sql"
    out.write_text("\n".join(statements) + "\n", encoding="utf-8")
    print(f"\nSQL écrit dans {out.relative_to(ROOT)} ({len(statements)} instruction(s))")

    if not args.apply:
        print("\nSimulation : rien n'a été écrit. Relancez avec --apply.")
        return 0

    print("\n== application du SQL sur la base distante ==")
    proc = subprocess.run(
        ["npx", "wrangler", "d1", "execute", "flight-watcher", "--remote", "--file=migration.sql", "-y"],
        cwd=ROOT / "worker", capture_output=True, text=True)
    print(proc.stdout[-800:] or proc.stderr[-800:])
    return proc.returncode


if __name__ == "__main__":
    sys.exit(main())
