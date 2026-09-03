# -*- coding: utf-8 -*-
"""Alle Sitzungen ungueltig machen (Audit 09/2026, Blocker 2 — Rotation).

Nach dem Wechsel von JWT_SECRET sind alte Tokens ohnehin ungueltig; nach
dem Rotieren von Passwoertern (Admin/Super-Admin/Mongo) sollen zusaetzlich
ALLE bestehenden Anmeldungen (Firmen, Sucher, Kaeufer, Admins, Fahrer)
beendet werden, damit ein zuvor abgegriffenes Token nirgends weiterlebt.

Aufruf (im Container):  python scripts/sitzungen_widerrufen.py --yes
Protokolliert den Vorgang in activity_logs (auth.sitzungen.widerrufen).
"""
import argparse
import os
import sys
import uuid
from datetime import datetime, timezone

from pymongo import MongoClient


def main() -> int:
    ap = argparse.ArgumentParser(description="Alle Sitzungen widerrufen")
    ap.add_argument("--yes", action="store_true", help="ohne Rueckfrage")
    ap.add_argument("--db", default=os.environ.get("DB_NAME") or "autoschnell")
    args = ap.parse_args()
    url = os.environ.get("MONGO_URL") or "mongodb://127.0.0.1:27017"
    db = MongoClient(url, serverSelectionTimeoutMS=5000)[args.db]
    n_users = db.users.count_documents({"current_session_id": {"$ne": None}})
    n_driver = db.driver_accounts.count_documents({"current_session_id": {"$ne": None}})
    print(f"Aktive Sitzungen: {n_users} Konten, {n_driver} Fahrer (Datenbank {args.db})")
    if not args.yes:
        if input("Alle Sitzungen JETZT beenden? (ja/nein): ").strip().lower() != "ja":
            print("Abgebrochen — nichts veraendert.")
            return 1
    jetzt = datetime.now(timezone.utc).isoformat()
    r1 = db.users.update_many({}, {"$set": {"current_session_id": None, "sitzungen_widerrufen_am": jetzt}})
    r2 = db.driver_accounts.update_many({}, {"$set": {"current_session_id": None, "sitzungen_widerrufen_am": jetzt}})
    db.password_resets.delete_many({})
    db.activity_logs.insert_one({
        "id": str(uuid.uuid4()), "dealer_id": "", "user_id": "system",
        "action": "auth.sitzungen.widerrufen",
        "meta": {"konten": r1.modified_count, "fahrer": r2.modified_count,
                 "grund": "Rotation der Zugangsdaten"},
        "created_at": jetzt,
    })
    print(f"WIDERRUFEN: {r1.modified_count} Konten, {r2.modified_count} Fahrer — "
          "alle muessen sich neu anmelden. Offene Passwort-Reset-Links geloescht.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
