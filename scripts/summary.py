"""Écrit le résumé du run dans le récapitulatif GitHub Actions."""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

SYMBOLS = {"EUR": "€", "USD": "$", "GBP": "£"}


def main() -> int:
    path = Path("data/latest.json")
    if not path.exists():
        print("Aucun résultat à résumer.")
        return 0

    data = json.loads(path.read_text(encoding="utf-8"))
    out: list[str] = [
        f"### Relevé du {data.get('ran_at', '?')}",
        "",
        f"Alertes envoyées : **{data.get('alerts_sent', 0)}**",
        "",
        "| Surveillance | Meilleur prix | Seuil | Trajet retenu | État |",
        "|---|---:|---:|---|---|",
    ]
    for w in data.get("watches", []):
        cur = SYMBOLS.get(w.get("currency", "EUR"), w.get("currency", ""))
        price = f"{w['best_price']:.0f} {cur}" if w.get("best_price") is not None else "—"
        thr = f"{w['threshold']:.0f} {cur}" if w.get("threshold") is not None else "—"
        route = " ".join(filter(None, [w.get("best_route", ""), w.get("best_depart", "")]))
        flag = "🔔 " if w.get("alerted") else ""
        out.append(f"| {w.get('label', w['id'])} | {price} | {thr} | {route or '—'} "
                   f"| {flag}{w.get('reason') or w.get('status', '')} |")

    errors = [e for e in data.get("errors", []) if e]
    if errors:
        out += ["", "<details><summary>Incidents "
                f"({len(errors)})</summary>", ""]
        out += [f"- {e}" for e in errors[:15]]
        out += ["", "</details>"]

    text = "\n".join(out) + "\n"
    summary = os.environ.get("GITHUB_STEP_SUMMARY")
    if summary:
        with open(summary, "a", encoding="utf-8") as fh:
            fh.write(text)
    print(text)
    return 0


if __name__ == "__main__":
    sys.exit(main())
