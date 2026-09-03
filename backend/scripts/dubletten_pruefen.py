# -*- coding: utf-8 -*-
"""Dubletten finden, die einen Unique-Index verhindern (Runde 5).

    python -X utf8 scripts/dubletten_pruefen.py

Listet je Feld die doppelten Werte samt Dokument-IDs und Anlagedatum, damit
der Betreiber entscheiden kann, welches Konto bleibt. Loescht NICHTS.
"""
import os
from pymongo import MongoClient

MONGO_URL = os.environ.get("MONGO_URL", "mongodb://127.0.0.1:27017")
DB_NAME = os.environ.get("DB_NAME", "autoschnell")
PRUEFUNGEN = (("users", "email"), ("dealers", "user_id"),
              ("driver_accounts", "email"), ("driver_accounts", "driver_code"))


def main() -> int:
    db = MongoClient(MONGO_URL, serverSelectionTimeoutMS=10000)[DB_NAME]
    gefunden = 0
    for coll, feld in PRUEFUNGEN:
        for d in db[coll].aggregate([
                {"$match": {feld: {"$exists": True, "$ne": None}}},
                {"$group": {"_id": f"${feld}", "n": {"$sum": 1},
                            "ids": {"$push": {"id": "$id", "created_at": "$created_at"}}}},
                {"$match": {"n": {"$gt": 1}}}]):
            gefunden += 1
            print(f"{coll}.{feld} = {d['_id']!r}: {d['n']}x")
            for e in d["ids"]:
                print(f"    id={e.get('id')}  created_at={e.get('created_at')}")
    print("Keine Dubletten." if not gefunden else f"{gefunden} doppelte Werte — bitte bereinigen.")
    return 0 if not gefunden else 1


if __name__ == "__main__":
    raise SystemExit(main())
