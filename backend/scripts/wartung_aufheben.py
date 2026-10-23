# -*- coding: utf-8 -*-
"""Wartungsmodus (Sperre der Plattform, 503) von Hand aufheben.

Pruefbericht 20.09.2026 (SK-03/SK-04): Bricht eine Wiederherstellung hart ab
(Verbindung weg, Speicher voll) oder scheitert ihr Rueckbau, bleibt der Merker
OHNE Ablaufzeit stehen — die Plattform antwortet mit 503, bis ihn jemand
aufhebt. Der bisher genannte Weg (mongosh im Backend-Container) lief nie:
dort gibt es kein mongosh, und die Datenbank verlangt eine Anmeldung. Dieses
Skript nutzt die Verbindung des Backends (MONGO_URL im Container).

Aufruf auf dem Server (im Projektordner):
    docker compose exec backend python scripts/wartung_aufheben.py        # zeigt nur den Stand
    docker compose exec backend python scripts/wartung_aufheben.py --ja   # hebt auf

Vorher sicherstellen, dass KEINE Sicherung und keine Wiederherstellung mehr
laeuft — der Merker schuetzt genau diese Laeufe.
"""
import argparse
import os
import sys
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND))

import wartung  # noqa: E402


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Wartungsmodus aufheben")
    ap.add_argument("--db", default=os.environ.get("DB_NAME", "autoschnell"))
    ap.add_argument("--ja", action="store_true", help="wirklich aufheben (sonst nur anzeigen)")
    args = ap.parse_args(argv)
    from pymongo import MongoClient
    client = MongoClient(os.environ.get("MONGO_URL", "mongodb://127.0.0.1:27017"),
                         serverSelectionTimeoutMS=10000)
    try:
        coll = client[args.db][wartung.FLAG_COLLECTION]
        merker = coll.find_one({"_id": wartung.FLAG_ID})
        if not merker or not merker.get("aktiv"):
            print(f"Kein aktiver Wartungsmodus in '{args.db}' — nichts zu tun.")
            return 0
        print(f"Aktiv in '{args.db}': {wartung.beschreibung(merker)}")
        if not args.ja:
            print("Zum Aufheben erneut mit --ja aufrufen — vorher pruefen, dass keine "
                  "Sicherung oder Wiederherstellung mehr laeuft.")
            return 1
        if wartung.aufheben(coll, zwang=True):
            print("Wartungsmodus aufgehoben — die Plattform antwortet wieder.")
            return 0
        print("Merker nicht gefunden — nichts geaendert.")
        return 1
    finally:
        client.close()


if __name__ == "__main__":
    sys.exit(main())
