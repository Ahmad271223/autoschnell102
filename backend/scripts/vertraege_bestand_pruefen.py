# -*- coding: utf-8 -*-
"""Vorab-Pruefung vor dem Scharfschalten der Fristloeschung
(VERTRAG_LOESCHUNG_AKTIV=true) — Go-Live-Audit 09/2026.

    python -X utf8 scripts/vertraege_bestand_pruefen.py
    python -X utf8 scripts/vertraege_bestand_pruefen.py --reparieren

Zaehlt Kaufvertraege gesamt, aelter als VERTRAG_AUFBEWAHRUNG_TAGE, ohne
admin_vehicle_data_id, mit haengendem Verweis (Auto-Datensatz fehlt),
doppelte Verweise auf denselben Datensatz sowie die Auto-Datensaetze
selbst. Ohne --reparieren loescht und aendert das Skript NICHTS.

Exit-Code 1, sobald Vertraege ohne oder mit haengendem Verweis existieren:
dann den Reparaturlauf des stuendlichen Aufraeumjobs abwarten oder mit
--reparieren sofort ausfuehren (cleanup_service.auto_daten_reparieren traegt
fehlende Datensaetze nach und legt Datensaetze fuer Verweise ins Leere aus
der Vertragsfassung neu an). Der Loeschjob wuerde solche Vertraege sonst
ueberspringen und je Vertrag einen Betriebsalarm `vertrag_ohne_auto_daten`
ausloesen.

Bestandspruefung 28.09.2026 (Ahmad, prod2): 16 von 17 Live-Vertraegen
zeigten auf Datensaetze, die es nicht mehr gab. Seitdem zaehlen Vertraege,
deren Datensatz der Betreiber bewusst entfernt hat (Vermerk
auto_daten_entfernt_am — die Fristloeschung verlangt dann keinen Datensatz),
und Vertraege, deren Loeschung gerade laeuft (Grabstein), NICHT als
Hindernis; sie werden getrennt ausgewiesen.
"""
import os
import sys
from collections import Counter
from datetime import datetime, timedelta, timezone
from pathlib import Path

from pymongo import MongoClient

MONGO_URL = os.environ.get("MONGO_URL", "mongodb://127.0.0.1:27017")
DB_NAME = os.environ.get("DB_NAME", "autoschnell")
AUFBEWAHRUNG_TAGE = int(os.environ.get("VERTRAG_AUFBEWAHRUNG_TAGE", "60"))
AVD = "admin_vehicle_data"
_OHNE_ID = {"$or": [{"admin_vehicle_data_id": {"$exists": False}},
                    {"admin_vehicle_data_id": {"$in": [None, ""]}}]}


def pruefen(db) -> dict:
    cutoff = (datetime.now(timezone.utc)
              - timedelta(days=AUFBEWAHRUNG_TAGE)).isoformat()
    alt_filter = {"created_at": {"$lte": cutoff}}
    zaehler: Counter = Counter()
    alt_mit_verweis = set()
    bewusst_entfernt = bewusst_entfernt_alt = 0
    for c in db.generated_pdfs.find(
            {"admin_vehicle_data_id": {"$exists": True, "$nin": [None, ""]}},
            {"_id": 0, "id": 1, "admin_vehicle_data_id": 1, "created_at": 1,
             "auto_daten_entfernt_am": 1, "loeschung": 1}):
        alt = (c.get("created_at") or "") <= cutoff
        if c.get("auto_daten_entfernt_am"):
            # Betreiber hat den Datensatz bewusst entfernt (auto_daten.entfernen):
            # die Fristloeschung verlangt keinen Datensatz, die Reparatur legt
            # keinen neuen an — kein Hindernis.
            bewusst_entfernt += 1
            bewusst_entfernt_alt += alt
            continue
        if (c.get("loeschung") or {}).get("status") == "laeuft":
            continue                      # Grabstein: wird ohnehin zu Ende geloescht
        zaehler[c["admin_vehicle_data_id"]] += 1
        if alt:
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
        "datensatz_bewusst_entfernt": bewusst_entfernt,
        "datensatz_bewusst_entfernt_aelter_als_frist": bewusst_entfernt_alt,
        "doppelte_verweise": {i: n for i, n in zaehler.items() if n > 1},
        "auto_datensaetze": db[AVD].count_documents({}),
        "loeschung_laeuft": db.generated_pdfs.count_documents(
            {"loeschung.status": "laeuft"}),
    }


SPERRE = "auto-daten-reparatur"


def reparieren() -> int:
    """--reparieren: den Reparaturlauf des stuendlichen Aufraeumjobs
    (cleanup_service.auto_daten_reparieren) einmal sofort ausfuehren.

    Eigene, kurze Sperre — NICHT die des Aufraeumlaufs: der haelt seine Sperre
    'cleanup-cycle' bewusst die ganze Stunde (ein Lauf je Stunde ueber beide
    Server), die erste Fassung vom 28.09.2026 bekam sie deshalb auf prod2 nie
    ("Ein Aufraeumlauf laeuft gerade"). Parallel zum Stundenlauf ist die
    Reparatur ungefaehrlich: jeder Schritt ist gegen den alten Verweis bzw.
    "kein Verweis" abgesichert, ein doppelt angelegter Datensatz wird
    zurueckgerollt. Waehrend einer Schreibpause (Sicherung/Restore) laeuft
    nichts. Liefert die Zahl der reparierten Vertraege, -1 wenn gerade eine
    Reparatur laeuft, -2 bei Schreibpause."""
    import asyncio
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    from motor.motor_asyncio import AsyncIOMotorClient
    import cleanup_service
    import job_lock
    import wartung

    async def lauf() -> int:
        client = AsyncIOMotorClient(MONGO_URL, serverSelectionTimeoutMS=10000)
        try:
            db = client[DB_NAME]
            if await wartung.aktiv_async(db):
                return -2
            token = await job_lock.acquire(db, SPERRE, ttl_seconds=600)
            if not token:
                return -1
            try:
                return await cleanup_service.auto_daten_reparieren(db)
            finally:
                await job_lock.release(db, SPERRE, token=token)
        finally:
            client.close()

    return asyncio.run(lauf())


def main(argv=None) -> int:
    argv = sys.argv[1:] if argv is None else list(argv)
    if argv and argv != ["--reparieren"]:
        print(__doc__)
        return 2
    if argv:
        n = reparieren()
        if n == -2:
            print("Schreibpause: gerade laeuft eine Sicherung oder ein Restore — "
                  "bitte danach erneut versuchen.")
            return 3
        if n < 0:
            print(f"Eine Reparatur laeuft gerade (Sperre '{SPERRE}') — "
                  "bitte in ein paar Minuten erneut versuchen.")
            return 3
        print(f"Reparaturlauf: {n} Vertraege repariert (Auto-Datensatz nachgetragen "
              f"oder neu angelegt).\n")
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
    print(f"  Datensatz vom Betreiber entfernt (Vermerk, kein Hindernis): "
          f"{e['datensatz_bewusst_entfernt']}"
          f"   (davon aelter als Frist: {e['datensatz_bewusst_entfernt_aelter_als_frist']})")
    print(f"  doppelte Verweise auf einen Datensatz:  "
          f"{len(e['doppelte_verweise'])}")
    for i, n in list(e["doppelte_verweise"].items())[:20]:
        print(f"      admin_vehicle_data_id={i}: {n} Vertraege")
    print(f"  Loeschung laeuft (Grabstein):           {e['loeschung_laeuft']}")
    print(f"Auto-Datensaetze (admin_vehicle_data):   {e['auto_datensaetze']}")
    probleme = e["ohne_verweis"] + len(e["haengende_verweise"])
    if probleme:
        print(f"\nNICHT bereit: {probleme} Vertraege ohne gueltigen Auto-Datensatz. "
              "Der stuendliche Aufraeumjob traegt fehlende Datensaetze nach und legt "
              "Datensaetze fuer Verweise ins Leere aus der Vertragsfassung neu an — "
              "sofort mit\n    python -X utf8 scripts/vertraege_bestand_pruefen.py --reparieren\n"
              "danach erneut pruefen; erst dann VERTRAG_LOESCHUNG_AKTIV=true setzen. "
              "Bleibt ein Vertrag haengen, fehlt ihm die Vertragsfassung (contract_data) — "
              "den meldet die Fristloeschung als Alarm vertrag_ohne_auto_daten.")
        return 1
    print("\nBereit: jeder Vertrag verweist auf einen vorhandenen Auto-Datensatz "
          "(oder der Betreiber hat ihn bewusst entfernt).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
