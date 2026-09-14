# -*- coding: utf-8 -*-
"""Kontonummer (13.09.2026): Sperre des Konto-Limiters fuer EINE Kennung aufheben.

Nach LOGIN_KONTO_LIMIT (Standard 30) Fehlversuchen je LOGIN_KONTO_FENSTER
(Standard 900 s) antwortet die Anmeldung fuer diese Kontonummer 429 — ausser
von IPs, von denen sich das Konto schon erfolgreich angemeldet hat. Die Sperre
laeuft mit dem Fenster ab. Vorher hebt sie der Betreiber auf:
  - Chef, Sucher, Zwischenhaendler, Fahrer: im Admin "Passwort setzen"
    (hebt die Sperre mit auf),
  - der Super-Admin selbst oder ohne Passwortwechsel: dieses Skript.

Geloescht werden GENAU die Zaehler, die auch SlidingWindowRateLimiter.reset()
loescht: rate_limits._id = "login-konto:<kennung>:<fenster>" fuer die Fenster
f-1, f und f+1. <kennung> ist wie beim Login normalisiert (kontonummer.
anmeldekennung: '10023 2' -> '10023-2', ein Benutzername klein geschrieben).
Kein Muster, kein Praefix — Sonderzeichen in der Kennung treffen nie fremde
Zaehler (Audit 13.09.2026 #47).

Aufruf (im Container):
    python scripts/anmeldesperre_aufheben.py 10023-2               # Probelauf: zeigt die Treffer
    python scripts/anmeldesperre_aufheben.py 10023-2 --ausfuehren  # loescht sie
    python scripts/anmeldesperre_aufheben.py <SUPER_ADMIN_USERNAME> --ausfuehren

Nicht angefasst werden der Betriebsalarm login_konto_angegriffen (er zeigt,
dass jemand die Nummer probiert hat) und die kurzen Zaehler je IP. War die
Datenbank beim Zaehlen nicht erreichbar, zaehlt jeder Backend-Prozess lokal —
diese Zaehler loescht nur ein Neustart des Backends.

Exit 0 = erledigt (auch Probelauf / nichts gesperrt), 2 = leere Kennung.
"""
import argparse
import os
import sys
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

# wie rate_limiter.login_konto_limiter (name="login-konto")
LIMITER_NAME = "login-konto"


def _int_env(name: str, standard: int) -> int:
    """wie rate_limiter._int_env"""
    try:
        return max(0, int((os.environ.get(name) or "").strip() or standard))
    except ValueError:
        return standard


def fenster_sekunden() -> int:
    """wie rate_limiter._LOGIN_KONTO_FENSTER"""
    return _int_env("LOGIN_KONTO_FENSTER", 900) or 900


def schluessel(kennung, fenster_s: int, jetzt=None) -> list:
    """Die _id-Werte, die reset() fuer diese Kennung loescht ([] bei leerer
    Kennung). `jetzt` = Unix-Zeit in Sekunden (Standard: time.time())."""
    from kontonummer import anmeldekennung
    k = anmeldekennung(kennung or "")
    if not k:
        return []
    f = int((time.time() if jetzt is None else jetzt) // fenster_s)
    return [f"{LIMITER_NAME}:{k}:{x}" for x in (f - 1, f, f + 1)]


def main(argv=None, db=None, jetzt=None) -> int:
    ap = argparse.ArgumentParser(description="Sperre des Konto-Limiters aufheben "
                                             "(ohne --ausfuehren nur Probelauf)")
    ap.add_argument("kennung", help="Kontonummer (z.B. 10023-2) oder Benutzername des Super-Admins")
    ap.add_argument("--ausfuehren", action="store_true",
                    help="Zaehler wirklich loeschen (sonst nur anzeigen)")
    ap.add_argument("--db", default=None, help="Datenbankname (Standard: DB_NAME oder autoschnell)")
    ap.add_argument("--fenster", type=int, default=None,
                    help="Fensterlaenge in Sekunden (Standard: LOGIN_KONTO_FENSTER bzw. 900)")
    args = ap.parse_args(argv)

    if db is None:
        try:
            from dotenv import load_dotenv
            load_dotenv(Path(__file__).resolve().parents[1] / ".env")
        except ImportError:
            pass
        from pymongo import MongoClient
        url = os.environ.get("MONGO_URL") or "mongodb://127.0.0.1:27017"
        db = MongoClient(url, serverSelectionTimeoutMS=8000)[
            args.db or os.environ.get("DB_NAME") or "autoschnell"]

    from kontonummer import anmeldekennung, kaeufer_normalisieren, normalisieren
    fenster_s = args.fenster if args.fenster and args.fenster > 0 else fenster_sekunden()
    ids = schluessel(args.kennung, fenster_s, jetzt)
    if not ids:
        print("FEHLER: leere Kennung.")
        return 2
    k = anmeldekennung(args.kennung)
    grenze = _int_env("LOGIN_KONTO_LIMIT", 30)
    art = ("Kontonummer" if normalisieren(args.kennung)
           else "Kaeufer-Code" if kaeufer_normalisieren(args.kennung)
           else "Benutzername/Kennung")
    print(f"{art}: {k}   (Fenster {fenster_s} s, Grenze {grenze or 'aus (nur Alarm)'})")

    treffer = list(db.rate_limits.find({"_id": {"$in": ids}}, {"n": 1}))
    if not treffer:
        print("Keine Zaehler im aktuellen Fenster — es besteht keine Sperre "
              "(oder sie ist schon abgelaufen).")
        return 0
    for t in sorted(treffer, key=lambda d: d["_id"]):
        n = int(t.get("n", 0))
        zustand = "GESPERRT" if grenze and n >= grenze else "unter der Grenze"
        print(f"  {t['_id']}: {n} Fehlversuche ({zustand})")
    if not args.ausfuehren:
        print("PROBELAUF — nichts geloescht. Mit --ausfuehren aufheben.")
        return 0

    geloescht = db.rate_limits.delete_many({"_id": {"$in": ids}}).deleted_count
    db.activity_logs.insert_one({
        "id": str(uuid.uuid4()), "dealer_id": "", "user_id": "system",
        "action": "auth.anmeldesperre.aufgehoben",
        "meta": {("kontonummer" if art == "Kontonummer" else "username"): k[:80],
                 "zaehler": geloescht, "quelle": "scripts/anmeldesperre_aufheben.py"},
        "created_at": datetime.now(timezone.utc).isoformat(),
    })
    print(f"AUFGEHOBEN: {geloescht} Zaehler geloescht — die Anmeldung ist wieder frei.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
