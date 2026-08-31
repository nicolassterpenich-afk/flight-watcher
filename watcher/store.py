"""Historique des prix + état des alertes.

Volontairement en fichiers texte (JSONL + JSON) : GitHub Actions les commit
dans le dépôt, ce qui donne gratuitement la persistance, l'historique versionné
et une source de données lisible par le dashboard statique.
"""

from __future__ import annotations

import json
import statistics
from datetime import timedelta
from pathlib import Path
from typing import Any, Iterable

from .models import Quote
from .timeutil import parse as parse_ts, utcnow

ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = ROOT / "data"
HISTORY = DATA_DIR / "history.jsonl"
STATE = DATA_DIR / "state.json"
LATEST = DATA_DIR / "latest.json"

MAX_HISTORY_DAYS = 400


def ensure_dirs() -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)


def append_history(quotes: Iterable[Quote]) -> int:
    """Ajoute le meilleur prix relevé (une ligne par surveillance et par run)."""
    ensure_dirs()
    rows = [q.to_dict() for q in quotes]
    if not rows:
        return 0
    with HISTORY.open("a", encoding="utf-8") as fh:
        for row in rows:
            fh.write(json.dumps(row, ensure_ascii=False) + "\n")
    return len(rows)


def read_history(watch_id: str | None = None, days: int | None = None) -> list[dict[str, Any]]:
    if not HISTORY.exists():
        return []
    cutoff = utcnow() - timedelta(days=days) if days else None
    out: list[dict[str, Any]] = []
    with HISTORY.open("r", encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                row = json.loads(line)
            except json.JSONDecodeError:
                continue
            if watch_id and row.get("watch_id") != watch_id:
                continue
            ts = parse_ts(row.get("checked_at"))
            if cutoff and (ts is None or ts < cutoff):
                continue
            out.append(row)
    return out


def prune_history() -> int:
    """Supprime les relevés trop anciens pour éviter que le fichier n'enfle."""
    if not HISTORY.exists():
        return 0
    rows = read_history()
    cutoff = utcnow() - timedelta(days=MAX_HISTORY_DAYS)
    kept = [r for r in rows if (parse_ts(r.get("checked_at")) or utcnow()) >= cutoff]
    if len(kept) == len(rows):
        return 0
    with HISTORY.open("w", encoding="utf-8") as fh:
        for row in kept:
            fh.write(json.dumps(row, ensure_ascii=False) + "\n")
    return len(rows) - len(kept)


def stats(watch_id: str, days: int = 30) -> dict[str, Any]:
    """Repères statistiques pour décider si un prix est vraiment bon."""
    prices = [r["price"] for r in read_history(watch_id, days=days)
              if isinstance(r.get("price"), (int, float)) and r["price"] > 0]
    if not prices:
        return {"count": 0}
    return {
        "count": len(prices),
        "min": min(prices),
        "max": max(prices),
        "median": statistics.median(prices),
        "avg": round(sum(prices) / len(prices), 2),
        "last": prices[-1],
    }


def load_state() -> dict[str, Any]:
    if not STATE.exists():
        return {}
    try:
        return json.loads(STATE.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return {}


def save_state(state: dict[str, Any]) -> None:
    ensure_dirs()
    STATE.write_text(json.dumps(state, indent=2, ensure_ascii=False, sort_keys=True), encoding="utf-8")


def save_latest(payload: dict[str, Any]) -> None:
    """Snapshot lisible par le dashboard."""
    ensure_dirs()
    LATEST.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
