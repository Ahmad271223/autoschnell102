# -*- coding: utf-8 -*-
"""Vorab-Pruefung vor dem Scharfschalten der Fristloeschung
(VERTRAG_LOESCHUNG_AKTIV=true) — Go-Live-Audit 09/2026.

    python -X utf8 scripts/vertraege_bestand_pruefen.py

Zaehlt Kaufvertraege gesamt, aelter als VERTRAG_AUFBEWAHRUNG_TAGE, ohne
admin_vehicle_data_id, mit haengendem Verweis (Auto-Datensatz fehlt),
doppelte Verweise auf denselben Datensatz sowie die Auto-Datensaetze
selbst. Loescht und aendert NICHTS.

Exit-Code 1, sobald Vertraege ohne oder mit haengendem Verweis existieren:
dann zuerst den Aufraeumjob laufen lassen (cleanup_service.auto_daten_reparieren
traegt fehlende Datensaetze nach) bzw. haengende Verweise klaeren. Der
Loeschjob wuerde solche Vertraege ueberspringen und je Vertrag einen
Betriebsalarm `vertrag_ohne_auto_daten` ausloesen.
"""
import os
from collections import Counter
from datetime import datetime, timedelta, timezone

from pymongo import MongoClient

MONGO_URL = os.environ.get("MONGO_URL", "mongodb://127.0.0.1:27017")
DB_NAME = os.environ.get("DB_NAME", "autoschnell")
AUFBEWAHRUNG_TAGE = int(os.environ.get("VERTRAG_AUFBEWAHRUNG_TAGE", "90"))
AVD = "admin_vehicle_data"
_OHNE_ID = {"$or": [{"admin_vehicle_data_id": {"$exists": False}},
                    {"admin_vehicle_data_id": {"$in": [None, ""]}}]}


def pruefen(db) -> dict:
    cutoff = (datetime.now(timezone.utc)
              - timedelta(days=AUFBEWAHRUNG_TAGE)).isoformat()
    alt_filter = {"created_at": {"$lte": cutoff}}
    zaehler: Counter = Counter()
    alt_mit_verweis = set()
    for c in db.generated_pdfs.find(
            {"admin_vehicle_data_id": {"$exists": True, "$nin": [None, ""]}},
            {"_id": 0, "id": 1, "admin_vehicle_data_id": 1, "created_at": 1}):
        zaehler[c["admin_vehicle_data_id"]] += 1
        if (c.get("created_at") or "") <= cutoff:
            alt_mit_verweis.add(c["admin_vehicle_data_id"])
    ids = list(zaehler)
    vorhanden = set()
    for i in range(0, len(ids), 1000):
        vorhanden.update(d["id"] for d in db[AVD].find(
            {"id": {"$in": ids[i:i + 1000]}}, {"_id": 0, "id": 1}))
    haengend = [i for i in ids if i not in vorhanden]
    return {
        "cutoff": cutoff,
        "vertraege_gesamt": db.generated_pdfs.count_documents({}),
        "vertraege_aelter_als_frist": db.generated_pdfs.count_documents(alt_filter),
        "ohne_verweis": db.generated_pdfs.count_documents(_OHNE_ID),
        "ohne_verweis_aelter_als_frist": db.generated_pdfs.count_documents(
            {**alt_filter, **_OHNE_ID}),
        "haengende_verweise": haengend,
        "haengende_verweise_aelter_als_frist": [
            i for i in haengend if i in alt_mit_verweis],
        "doppelte_verweise": {i: n for i, n in zaehler.items() if n > 1},
        "auto_datensaetze": db[AVD].count_documents({}),
        "loeschung_laeuft": db.generated_pdfs.count_documents(
            {"loeschung.status": "laeuft"}),
    }


def main() -> int:
    db = MongoClient(MONGO_URL, serverSelectionTimeoutMS=10000)[DB_NAME]
    e = pruefen(db)
    print(f"Datenbank: {DB_NAME}   Frist: {AUFBEWAHRUNG_TAGE} Tage "
          f"(created_at <= {e['cutoff']})")
    print(f"Kaufvertraege gesamt:                    {e['vertraege_gesamt']}")
    print(f"  davon aelter als Frist (Loeschkandidaten): "
          f"{e['vertraege_aelter_als_frist']}")
    print(f"  ohne admin_vehicle_data_id:            {e['ohne_verweis']}"
          f"   (davon aelter als Frist: {e['ohne_verweis_aelter_als_frist']})")
    print(f"  mit haengendem Verweis (Datensatz fehlt): "
          f"{len(e['haengende_verweise'])}"
          f"   (davon aelter als Frist: "
          f"{len(e['haengende_verweise_aelter_als_frist'])})")
    for i in e["haengende_verweise"][:20]:
        print(f"      admin_vehicle_data_id={i}")
    print(f"  doppelte Verweise auf einen Datensatz:  "
          f"{len(e['doppelte_verweise'])}")
    for i, n in list(e["doppelte_verweise"].items())[:20]:
        print(f"      admin_vehicle_data_id={i}: {n} Vertraege")
    print(f"  Loeschung laeuft (Grabstein):           {e['loeschung_laeuft']}")
    print(f"Auto-Datensaetze (admin_vehicle_data):   {e['auto_datensaetze']}")
    probleme = e["ohne_verweis"] + len(e["haengende_verweise"])
    if probleme:
        print(f"\nNICHT bereit: {probleme} Vertraege ohne gueltigen Auto-Datensatz. "
              "Erst Aufraeumjob laufen lassen (POST /api/admin/cleanup/run oder "
              "stuendlicher Lauf) und erneut pruefen; danach "
              "VERTRAG_LOESCHUNG_AKTIV=true setzen.")
        return 1
    print("\nBereit: jeder Vertrag verweist auf einen vorhandenen Auto-Datensatz.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
