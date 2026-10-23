# -*- coding: utf-8 -*-
"""Alle aktiven Segmente der Marktbeobachtung JETZT einmal crawlen (Wunsch Ahmad 28.09.2026: "ein Testlauf,
wo alle Autos jetzt einmalig gecrawlt werden; ab morgen wieder zu den normalen Uhrzeiten").

    python -X utf8 scripts/markt_alle_jetzt.py          # Vorschau: Segmente, geschaetzte Kosten — legt NICHTS an
    python -X utf8 scripts/markt_alle_jetzt.py --ja     # startet (kostet Apify-Budget)

Was --ja tut, in dieser Reihenfolge:
  1. Tagesplan von heute sicherstellen (markt.jobs.tagesplan, idempotent — legt nur an, was noch fehlt).
  2. Die wartenden ERSTEN Laeufe des heutigen Tagesplans (Schluessel = heute, faellig im Fenster 9-11 Uhr)
     auf "jetzt" vorziehen — kein zweiter Lauf, derselbe Job laeuft nur frueher. Zweite Laeufe des Tages
     (heute#2, 18 Uhr) bleiben, wo sie sind.
  3. Segmente, fuer die heute kein Job mehr wartet (z. B. schon gelaufen), bekommen einen Sofort-Lauf —
     derselbe Weg wie der Knopf "jetzt crawlen" am Segment (markt.jobs.job_sofort, manueller Job heute#xxxxxx).
Der Worker holt faellige Jobs innerhalb einer Minute, wenn der Crawler an ist. last_planned_tag bleibt
unberuehrt — morgen laeuft der normale Plan im eingestellten Fenster. Kein Doppel-Lauf: Segmente mit wartendem
oder laufendem Job und manuelle Laeufe der letzten 5 Minuten werden uebersprungen. Segmente pausierter
Auftraege sind nicht aktiv und kommen nicht vor. Abbruch bei Crawler AUS oder Budget 0.

Danach auswerten: Kachel "Heute gecrawlt" im Admin, oder
    python -X utf8 scripts/markt_fehlversuche.py --seit "<Startzeit>"
"""
import asyncio
import math
import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional
from zoneinfo import ZoneInfo

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from markt import jobs, konfig  # noqa: E402

MONGO_URL = os.environ.get("MONGO_URL", "mongodb://127.0.0.1:27017")
DB_NAME = os.environ.get("DB_NAME", "autoschnell")
BERLIN = ZoneInfo("Europe/Berlin")


async def kandidaten(db) -> List[Dict[str, Any]]:
    """Aktive Segmente aktiver Auftraege (dieselben Bedingungen wie job_sofort, nur vorab gefiltert)."""
    aktive_modelle = {m["id"] async for m in db[konfig.MODELLE].find({"enabled": True}, {"_id": 0, "id": 1})}
    felder = {"_id": 0, "id": 1, "model_id": 1, "label": 1, "max_items": 1}
    segs = [s async for s in db[konfig.SEGMENTE].find({"enabled": True}, felder)]
    return sorted((s for s in segs if s.get("model_id") in aktive_modelle), key=lambda s: (s.get("label") or "", s["id"]))


def kosten(segs: List[Dict[str, Any]]) -> float:
    actor = konfig.actor()
    return sum(konfig.kosten_je_lauf_usd(actor, konfig.zeilen_mit_puffer(int(s.get("max_items") or konfig.rows_je_segment())))
               for s in segs)


def _wartend_heute(heute: str, jetzt: str) -> Dict[str, Any]:
    """Erste Laeufe des heutigen Tagesplans, die noch auf ihr Fenster warten."""
    return {"status": "queued", "job_type": "daily", "tag": heute, "scheduled_at": {"$gt": jetzt}}


async def ausfuehren(db, ja: bool) -> Dict[str, Any]:
    """Vorschau (ja=False) oder Start (ja=True). Liefert Zaehler; 'grund' erklaert einen Abbruch."""
    heute, jetzt = konfig.heute_tag(), konfig.jetzt_iso()
    segs = await kandidaten(db)
    ids = [s["id"] for s in segs]
    offen = {j["segment_id"] async for j in db[konfig.JOBS].find(
        {"status": {"$in": ["queued", "running"]}, "segment_id": {"$in": ids}}, {"_id": 0, "segment_id": 1})}
    erg: Dict[str, Any] = {"segmente": len(segs), "kosten_usd": round(kosten(segs), 2),
                           "laeufe": math.ceil(len(segs) / max(1, konfig.buendel_groesse())),
                           "crawler_an": await konfig.crawler_aktiv(db),
                           "vorziehbar": await db[konfig.JOBS].count_documents(_wartend_heute(heute, jetzt)),
                           "ohne_job": sum(1 for i in ids if i not in offen),
                           "plan_neu": 0, "vorgezogen": 0, "neu": 0, "uebersprungen": 0, "fehler": 0,
                           "angelegt": False, "grund": None}
    takt = await jobs.intervall(db)
    if takt.get("ohne_budget"):
        erg["grund"] = "Monatsbudget ist 0 — erst im Admin (Bereiche & Budget) ein Budget setzen."
        return erg
    if not segs:
        erg["grund"] = "keine aktiven Segmente — Suchauftraege aktivieren (Testlauf-Pflicht) oder Segmente aufbauen."
        return erg
    if not ja:
        return erg
    if not erg["crawler_an"]:
        erg["grund"] = "Crawler ist AUS (Admin-Knopf / MARKT_AKTIV) — die Jobs wuerden nur warten. Erst einschalten."
        return erg
    # 1. heutiger Tagesplan (idempotent) — sonst kaeme er spaeter obendrauf und alles liefe heute doppelt
    plan = await jobs.tagesplan(db)
    erg["plan_neu"] = int(plan.get("neu") or 0)
    # 2. erste Laeufe von heute vorziehen (derselbe Job, nur frueher)
    jetzt = konfig.jetzt_iso()
    r = await db[konfig.JOBS].update_many(_wartend_heute(heute, jetzt), {"$set": {"scheduled_at": jetzt}})
    erg["vorgezogen"] = int(r.modified_count)
    # 3. Rest: Sofort-Lauf, wo heute nichts mehr wartet
    beispiele: List[str] = []
    for s in segs:
        try:
            await jobs.job_sofort(db, s["id"])
            erg["neu"] += 1
        except jobs.SchonEingereiht:
            erg["uebersprungen"] += 1
        except ValueError as e:                     # Segment inzwischen inaktiv o. ae.
            erg["fehler"] += 1
            if len(beispiele) < 5:
                beispiele.append(f"{s.get('label') or s['id']}: {e}")
    erg["angelegt"] = True
    erg["beispiele_fehler"] = beispiele
    return erg


def main(argv: Optional[List[str]] = None) -> int:
    argv = sys.argv[1:] if argv is None else list(argv)
    if argv not in ([], ["--ja"]):
        print(__doc__)
        return 2
    ja = bool(argv)
    if not konfig.token():
        print("APIFY_TOKEN fehlt — ohne Token kein Abruf.")
        return 2
    from motor.motor_asyncio import AsyncIOMotorClient

    async def lauf() -> Dict[str, Any]:
        client = AsyncIOMotorClient(MONGO_URL, serverSelectionTimeoutMS=10000)
        try:
            return await ausfuehren(client[DB_NAME], ja)
        finally:
            client.close()

    start = datetime.now(timezone.utc)
    e = asyncio.run(lauf())
    print(f"Datenbank: {DB_NAME}")
    print(f"Aktive Segmente (aktive Auftraege): {e['segmente']}   ->   {e['laeufe']} Apify-Laeufe "
          f"(Buendel je {konfig.buendel_groesse()}), geschaetzte Kosten {e['kosten_usd']:.2f} $")
    print(f"Crawler: {'AN' if e['crawler_an'] else 'AUS'}")
    if e["grund"]:
        print(f"\nNICHT gestartet: {e['grund']}")
        return 1
    if not e["angelegt"]:
        print(f"Wartende Plan-Jobs von heute, die vorgezogen wuerden: {e['vorziehbar']}   "
              f"Segmente ohne wartenden Job (bekaemen einen Sofort-Lauf): {e['ohne_job']}")
        print("\nVorschau — nichts gestartet. Zum Starten:  python -X utf8 scripts/markt_alle_jetzt.py --ja")
        return 0
    print(f"\nTagesplan ergaenzt: {e['plan_neu']} Jobs   vorgezogen (erster Lauf von heute ab jetzt): {e['vorgezogen']}   "
          f"zusaetzliche Sofort-Laeufe: {e['neu']}   uebersprungen (wartet/laeuft schon): {e['uebersprungen']}   "
          f"Fehler: {e['fehler']}")
    for b in e.get("beispiele_fehler") or []:
        print(f"    {b}")
    zeit = start.astimezone(BERLIN).strftime("%Y-%m-%d %H:%M")
    print("\nDer Worker holt die Jobs innerhalb einer Minute. Zweite Laeufe von heute (2x-Autos) bleiben um 18 Uhr.")
    print("Fortschritt: Kachel 'Heute gecrawlt' im Admin.")
    print(f"Auswerten, sobald durch:  python -X utf8 scripts/markt_fehlversuche.py --seit \"{zeit}\" --alle")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
