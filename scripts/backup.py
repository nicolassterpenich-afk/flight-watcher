#!/usr/bin/env python3
"""Sauvegarde quotidienne de la base D1 dans le dépôt.

Si D1 disparaît, `watches.yaml` est figé à la migration et l'historique JSONL
ne contient qu'un prix par relevé. Ce script verse une tranche par jour dans
`backups/` ; Git en garde toutes les versions, donc leur union couvre la
totalité sans qu'aucun fichier n'enfle.

Écrit au premier relevé de chaque jour, rien les fois suivantes — pas besoin
d'un planificateur, qui de toute façon n'est pas fiable ici.
"""

from __future__ import annotations

import gzip
import json
import sys
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from watcher import remote                                   # noqa: E402

DOSSIER = Path(__file__).resolve().parents[1] / "backups"
JOURS = 2          # léger recouvrement : un run peut manquer le changement de jour


def main() -> int:
    if not remote.configured():
        print("Worker non configuré — pas de sauvegarde.")
        return 0

    DOSSIER.mkdir(exist_ok=True)
    cible = DOSSIER / f"{date.today().isoformat()}.json.gz"
    if cible.exists():
        print(f"{cible.name} existe déjà — rien à faire.")
        return 0

    try:
        data = remote._request("GET", f"/api/agent/export?days={JOURS}")
    except remote.RemoteError as exc:
        # Une sauvegarde ratée ne doit pas faire échouer un relevé réussi.
        print(f"::warning::Sauvegarde impossible : {exc}")
        return 0

    brut = json.dumps(data, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    cible.write_bytes(gzip.compress(brut, 9))
    print(f"{cible.name} — {len(data['watches'])} surveillance(s), "
          f"{len(data['history'])} relevé(s), {cible.stat().st_size / 1024:.0f} ko compressés "
          f"({len(brut) / 1024:.0f} ko bruts)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
