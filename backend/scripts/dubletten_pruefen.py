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


def kreuz_dubletten(db) -> int:
    """Runde 13: B5 — dieselbe Login-E-Mail in users UND driver_accounts.
    Vorher wurde jede Sammlung nur fuer sich geprueft; seit Runde 13 lehnen
    alle Anlagepfade ein solches Doppelkonto ab, ein Altbestand muss aber
    von Hand bereinigt werden (Fahrer loeschen via DELETE /admin/drivers/{id}
    oder der Fahrer nimmt eine andere Adresse) — sonst findet der gemeinsame
    Passwort-Reset weiterhin beide Konten. Vergleich schreibungsunabhaengig
    (klein, getrimmt). Liefert die Zahl betroffener Adressen."""
    fahrer = {}
    for d in db.driver_accounts.find({"email": {"$type": "string"}},
                                     {"_id": 0, "id": 1, "email": 1, "created_at": 1}):
        fahrer.setdefault(d["email"].strip().lower(), []).append(d)
    n = 0
    for u in db.users.find({"email": {"$type": "string"}},
                           {"_id": 0, "id": 1, "email": 1, "role": 1, "created_at": 1}):
        schluessel = u["email"].strip().lower()
        if schluessel not in fahrer:
            continue
        n += 1
        print(f"users+driver_accounts.email = {schluessel!r}: Konto in BEIDEN Sammlungen")
        print(f"    users id={u.get('id')}  role={u.get('role')}  created_at={u.get('created_at')}")
        for e in fahrer[schluessel]:
            print(f"    driver_accounts id={e.get('id')}  created_at={e.get('created_at')}")
    return n


def doppelte_offene_termine(db) -> int:
    """Runde 15/Umbau Kaufvorgaenge: mehrere OFFENE Abholtermine je VERTRAG
    und Firma. Sie verhindern den Teil-Unique-Index termin_offen_je_vertrag (server.py
    legt ihn dann nicht an und warnt beim Start). Bereinigen im
    Terminplaner: einen der Termine abschliessen (storniert/erledigt)
    oder loeschen, danach Backend neu starten. Loescht NICHTS."""
    offen = ["offen", "verschoben", "bestätigt", "in Bearbeitung"]
    n = 0
    for d in db.appointments.aggregate([
            {"$match": {"contract_id": {"$type": "string", "$gt": ""}, "status": {"$in": offen}}},
            {"$group": {"_id": {"dealer_id": "$dealer_id", "contract_id": "$contract_id"},
                        "n": {"$sum": 1},
                        "termine": {"$push": {"id": "$id", "status": "$status",
                                              "pickup_date": "$pickup_date",
                                              "contract_id": "$contract_id",
                                              "created_at": "$created_at"}}}},
            {"$match": {"n": {"$gt": 1}}}]):
        n += 1
        print(f"appointments offen: Firma {d['_id']['dealer_id']}  Vertrag {d['_id']['contract_id']}: {d['n']}x")
        for e in d["termine"]:
            print(f"    id={e.get('id')}  status={e.get('status')}  pickup_date={e.get('pickup_date')}"
                  f"  contract_id={e.get('contract_id')}  created_at={e.get('created_at')}")
    return n


def doppelte_fahrzeuge(db) -> int:
    """Runde 17 (Nr. 396): mehrere vehicles-Dokumente je (dealer_id, id).
    Sie verhindern den Unique-Index vehicles (dealer_id, id) — server.py
    legt ihn dann nicht an und loest einen Betriebsalarm aus. Bereinigen:
    das juengere Dokument (updated_at) pruefen und von Hand entfernen bzw.
    Vertraege/Termine darauf umhaengen. Loescht NICHTS."""
    n = 0
    for d in db.vehicles.aggregate([
            {"$match": {"id": {"$type": "string"}, "dealer_id": {"$type": "string"}}},
            {"$group": {"_id": {"dealer_id": "$dealer_id", "id": "$id"},
                        "n": {"$sum": 1},
                        "docs": {"$push": {"lifecycle": "$lifecycle", "quelle": "$quelle",
                                           "mobile_ad_id": "$mobile_ad_id",
                                           "created_at": "$created_at",
                                           "updated_at": "$updated_at"}}}},
            {"$match": {"n": {"$gt": 1}}}]):
        n += 1
        print(f"vehicles (dealer_id, id): Firma {d['_id']['dealer_id']}  Fahrzeug {d['_id']['id']}: {d['n']}x")
        for e in d["docs"]:
            print(f"    lifecycle={e.get('lifecycle')}  quelle={e.get('quelle')}"
                  f"  mobile_ad_id={e.get('mobile_ad_id')}  created_at={e.get('created_at')}"
                  f"  updated_at={e.get('updated_at')}")
    return n


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
    gefunden += kreuz_dubletten(db)
    gefunden += doppelte_offene_termine(db)
    gefunden += doppelte_fahrzeuge(db)
    print("Keine Dubletten." if not gefunden else f"{gefunden} doppelte Werte — bitte bereinigen.")
    return 0 if not gefunden else 1


if __name__ == "__main__":
    raise SystemExit(main())
