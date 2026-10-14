# -*- coding: utf-8 -*-
"""Lasttest: 30 Sucher legen GLEICHZEITIG einen Vertrag zum SELBEN Auto an.

Pruefbericht 20.09.2026 (P1): "Fuer dasselbe Fahrzeug gibt es eine gemeinsame
Sperre. Ein wartender Vertrag versucht ~6 s lang, sie zu bekommen. Danach ein
wiederholbarer 503; das Frontend versucht bis zu zweimal erneut. Bei 20-30
Leuten exakt auf demselben Wagen gibt es keine garantierte Warteschlange."

Der Einwand stimmt — bewiesen war er nie. Dieses Skript misst es:

  * N Sucher einer Firma, EIN gemeinsames Fahrzeug (alle Mitbearbeiter)
  * alle Anfragen starten an derselben Schranke, also wirklich gleichzeitig
  * danach spielt das Skript die Wiederholung der Oberflaeche nach
    (nur bei 503 MIT X-Wiederholen, hoechstens zweimal, Retry-After beachtet)

Gemessen wird, was den Nutzer interessiert: Wie viele haetten am Ende einen
Vertrag, und wie lange haben sie gewartet?

Zum Vergleich laeuft derselbe Ansturm auf N VERSCHIEDENE Autos — dort greift
die Sperre je Fahrzeug, es darf also gar keine Wartezeit entstehen.

Aufruf (Backend laeuft, dieselbe Datenbank):
    python -X utf8 scripts/lasttest_vertraege_gleiches_auto.py
    python -X utf8 scripts/lasttest_vertraege_gleiches_auto.py --sucher 30

Exit 0 = alle Sucher bekamen ihren Vertrag, 1 = mindestens einer sah einen Fehler.
"""
import argparse
import os
import statistics
import sys
import threading
import time
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import requests  # noqa: E402

BASE = (os.environ.get("TEST_BASE_URL") or "http://127.0.0.1:8002").rstrip("/")
API = f"{BASE}/api"
MONGO_URL = os.environ.get("MONGO_URL") or "mongodb://127.0.0.1:27017"
DB_NAME = os.environ.get("DB_NAME") or "autoschnell"
SUF = uuid.uuid4().hex[:8]
PW = "Rt7Kq2Mx9-Sicher!42"
BASIS_NR = 97000 + (int(SUF[:4], 16) % 900)

# Die Oberflaeche wiederholt NUR bei 503 mit X-Wiederholen, hoechstens zweimal
# (frontend/src/lib/api.js: WIEDERHOLEN_MAX). Genau das bilden wir nach.
WIEDERHOLEN_MAX = 2


def _db():
    from pymongo import MongoClient
    return MongoClient(MONGO_URL, serverSelectionTimeoutMS=5000)[DB_NAME]


def _hash(pw: str) -> str:
    import bcrypt
    return bcrypt.hashpw(pw.encode(), bcrypt.gensalt()).decode()


def _jetzt() -> str:
    return datetime.now(timezone.utc).isoformat()


def aufbau(db, n: int) -> dict:
    """Eine Firma, n Sucher, ein gemeinsames Auto und n einzelne zum Vergleich."""
    firma = f"last-{SUF}"
    chef = f"chef-{SUF}"
    sucher = [f"such-{SUF}-{i}" for i in range(n)]
    db.dealers.insert_one({"id": firma, "company_name": f"Lasttest {SUF}",
                           "kunden_nr": BASIS_NR, "user_id": chef,
                           "active_profile": "inland", "created_at": _jetzt()})
    hash_ = _hash(PW)          # einmal rechnen — bcrypt ist absichtlich langsam
    nutzer = [{"id": chef, "username": chef, "role": "dealer", "dealer_id": firma,
               "active": True, "password_hash": hash_, "current_session_id": None,
               "kontonummer": str(BASIS_NR), "created_at": "2026-01-01T00:00:00+00:00"}]
    abos = [{"id": f"abo-{SUF}-c", "dealer_id": firma, "subject_user_id": chef,
             "plan": "yearly", "status": "active",
             "expires_at": (datetime.now(timezone.utc) + timedelta(days=300)).isoformat()}]
    for i, s in enumerate(sucher):
        nutzer.append({"id": s, "username": s, "role": "sucher", "dealer_id": firma,
                       "active": True, "password_hash": hash_,
                       "current_session_id": None,
                       "kontonummer": f"{BASIS_NR}-{i + 2}", "created_at": _jetzt()})
        abos.append({"id": f"abo-{SUF}-{i}", "dealer_id": firma, "subject_user_id": s,
                     "plan": "monthly", "status": "active",
                     "expires_at": (datetime.now(timezone.utc)
                                    + timedelta(days=20)).isoformat()})
    db.users.insert_many(nutzer)
    db.subscriptions.insert_many(abos)

    # Das GEMEINSAME Auto: jeder Sucher ist Mitbearbeiter, jeder darf einen
    # eigenen Vertrag anlegen (Wunsch Ahmad — Doppel-Abholung bleibt erlaubt).
    gemeinsam = f"auto-{SUF}-gemeinsam"
    db.vehicles.insert_one(
        {"id": gemeinsam, "dealer_id": firma, "lifecycle": "verglichen",
         "owner_user_id": sucher[0], "mitbearbeiter_ids": sucher,
         "created_at": _jetzt(),
         "data": {"make": "BMW", "model": "320d", "price": 15000}})
    # Und je ein EIGENES Auto als Gegenprobe.
    einzeln = [f"auto-{SUF}-{i}" for i in range(n)]
    db.vehicles.insert_many(
        [{"id": einzeln[i], "dealer_id": firma, "lifecycle": "verglichen",
          "owner_user_id": sucher[i], "mitbearbeiter_ids": [sucher[i]],
          "created_at": _jetzt(),
          "data": {"make": "VW", "model": "Golf", "price": 12000}}
         for i in range(n)])
    return {"firma": firma, "chef": chef, "sucher": sucher,
            "gemeinsam": gemeinsam, "einzeln": einzeln}


def anmelden(n: int) -> list:
    """Nacheinander anmelden — eine neue Anmeldung wirft die alte raus,
    parallel wuerden sich die Sucher gegenseitig abmelden."""
    token = []
    for i in range(n):
        r = requests.post(
            f"{API}/auth/login",
            json={"kontonummer": f"{BASIS_NR}-{i + 2}", "password": PW}, timeout=60)
        if r.status_code != 200:
            print(f"  Anmeldung {i} fehlgeschlagen: HTTP {r.status_code} {r.text[:120]}")
            return []
        token.append(r.json().get("token"))
    return token


def _vertrag_anlegen(tok: str, vehicle_id: str, nr: int) -> tuple:
    """Ein Versuch. Liefert (status, sekunden, wiederholbar, retry_after)."""
    t0 = time.monotonic()
    r = requests.post(
        f"{API}/contracts",
        json={"vehicle_id": vehicle_id,
              "seller_name": f"Verkaeufer {nr}",
              "seller_address": "Teststrasse 1",
              "seller_zip": "12345", "seller_city": "Teststadt",
              "purchase_price": 15000 + nr,
              "idempotency_key": f"last-{SUF}-{nr}-{uuid.uuid4().hex[:8]}"},
        headers={"Authorization": f"Bearer {tok}"}, timeout=90)
    dauer = time.monotonic() - t0
    wdh = r.headers.get("X-Wiederholen") == "1" and r.status_code == 503
    return r.status_code, dauer, wdh, r.headers.get("Retry-After"), r.text[:140]


def ansturm(token: list, autos, titel: str) -> dict:
    """Alle Sucher an derselben Schranke losschicken und die Oberflaeche
    nachspielen (bis zu zwei Wiederholungen, nur bei X-Wiederholen)."""
    n = len(token)
    schranke = threading.Barrier(n)
    ergebnis = [None] * n

    def lauf(i: int) -> None:
        auto = autos if isinstance(autos, str) else autos[i]
        schranke.wait()
        gesamt_t0 = time.monotonic()
        versuche = 0
        while True:
            status, dauer, wdh, retry_after, text = _vertrag_anlegen(token[i], auto, i)
            versuche += 1
            if status in (200, 201) or not wdh or versuche > WIEDERHOLEN_MAX:
                ergebnis[i] = {"status": status, "versuche": versuche,
                               "gesamt": time.monotonic() - gesamt_t0,
                               "letzte": dauer, "text": text}
                return
            # Genau wie die Oberflaeche: Retry-After abwarten (gedeckelt).
            try:
                warten = min(float(retry_after or 3), 30.0)
            except ValueError:
                warten = 3.0
            time.sleep(warten)

    faeden = [threading.Thread(target=lauf, args=(i,)) for i in range(n)]
    t0 = time.monotonic()
    for f in faeden:
        f.start()
    for f in faeden:
        f.join()
    gesamt = time.monotonic() - t0

    ok = [e for e in ergebnis if e and e["status"] in (200, 201)]
    fehler = [e for e in ergebnis if e and e["status"] not in (200, 201)]
    zeiten = sorted(e["gesamt"] for e in ergebnis if e)
    ohne_wdh = len([e for e in ok if e["versuche"] == 1])

    print(f"\n{titel}")
    print(f"  Sucher gleichzeitig ....... {n}")
    print(f"  Vertrag bekommen .......... {len(ok)}")
    print(f"     davon im ersten Anlauf . {ohne_wdh}")
    print(f"     davon nach Wiederholung  {len(ok) - ohne_wdh}")
    print(f"  Fehler fuer den Nutzer .... {len(fehler)}")
    if zeiten:
        print(f"  Wartezeit Mitte/langsamster {statistics.median(zeiten):.2f} s / "
              f"{zeiten[-1]:.2f} s")
    print(f"  Ansturm insgesamt ......... {gesamt:.2f} s")
    for e in fehler[:5]:
        print(f"     FEHLER HTTP {e['status']} nach {e['versuche']} Versuchen: {e['text']}")
    return {"ok": len(ok), "fehler": len(fehler), "ohne_wdh": ohne_wdh,
            "max": zeiten[-1] if zeiten else 0.0}


def abbau(db, w: dict) -> None:
    db.users.delete_many({"dealer_id": w["firma"]})
    db.dealers.delete_many({"id": w["firma"]})
    for coll in ("subscriptions", "vehicles", "generated_pdfs", "kaufvorgaenge",
                 "appointments", "activity_logs", "auto_daten", "sperren",
                 "fahrzeug_pool"):
        try:
            db[coll].delete_many({"$or": [{"dealer_id": w["firma"]},
                                          {"_id": {"$regex": SUF}},
                                          {"id": {"$regex": SUF}}]})
        except Exception:  # noqa: BLE001 — sperren hat _id als String
            db[coll].delete_many({"dealer_id": w["firma"]})


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Lasttest gleichzeitige Vertraege")
    ap.add_argument("--sucher", type=int, default=30)
    ap.add_argument("--behalten", action="store_true")
    a = ap.parse_args(argv)

    print(f"Lasttest gegen {BASE} (Datenbank {DB_NAME}, Kennung {SUF})")
    print(f"{a.sucher} Sucher, Nummernkreis {BASIS_NR}-2 .. {BASIS_NR}-{a.sucher + 1}")
    db = _db()
    w = aufbau(db, a.sucher)
    try:
        print("\nAnmeldung (nacheinander — Einzelsitzung) ...")
        token = anmelden(a.sucher)
        if len(token) != a.sucher:
            return 1
        print(f"  {len(token)} Sucher angemeldet")

        r1 = ansturm(token, w["gemeinsam"],
                     f"A) {a.sucher} Vertraege GLEICHZEITIG auf DASSELBE Auto")
        r2 = ansturm(token, w["einzeln"],
                     f"B) {a.sucher} Vertraege GLEICHZEITIG auf {a.sucher} VERSCHIEDENE Autos")

        print("\nERGEBNIS")
        schlecht = r1["fehler"] + r2["fehler"]
        if schlecht:
            print(f"  {schlecht} Sucher haetten einen Fehler gesehen — NICHT in Ordnung.")
        else:
            print("  Kein einziger Sucher sah einen Fehler.")
            print(f"  Langsamster Fall: {max(r1['max'], r2['max']):.2f} s "
                  f"(dasselbe Auto), Sperre je Fahrzeug greift wie gedacht.")
        return 1 if schlecht else 0
    finally:
        if not a.behalten:
            abbau(db, w)
            print("\nTestdaten entfernt.")


if __name__ == "__main__":
    raise SystemExit(main())
