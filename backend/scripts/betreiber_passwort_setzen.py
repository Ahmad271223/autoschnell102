# -*- coding: utf-8 -*-
"""Notweg: Passwort des Betreiberkontos (Super-Admin) auf der Konsole setzen.

Rollenprüfung 22.09.2026 (RP-551): Hatte der Betreiber sein Passwort
vergessen, gab es KEINEN Ausweg ausser einem Eingriff in die Datenbank:
  * der Admin-Reset (POST /admin/users/{id}/password) ist fuer den
    Super-Admin gesperrt,
  * /admin/me/password verlangt das alte Passwort,
  * ein neues SUPER_ADMIN_PASSWORD in der .env wird beim Start bewusst NICHT
    uebernommen (nur als Alarm "weicht ab" gemeldet),
  * anmeldesperre_aufheben.py hebt nur Sperren auf.

Aufruf (im Container, das neue Passwort wird verdeckt abgefragt — es steht
nie in der Befehlszeile oder im Shell-Verlauf):

    docker compose exec backend python scripts/betreiber_passwort_setzen.py --konto <SUPER_ADMIN_USERNAME> --ja

Ohne Terminal (z. B. "exec -T") liest das Skript das Passwort aus der ersten
Zeile der Standardeingabe.

Was passiert:
  * dieselbe Passwortregel wie ueberall (passwoerter.pruefe_passwort, keine
    eigenen Kontodaten im Passwort),
  * neues Passwort gespeichert (bcrypt), die laufende Sitzung beendet,
  * die Anmeldesperre des Kontos (Konto-Limiter) aufgehoben,
  * Eintrag im Aktivitaetsprotokoll (auth.passwort.gesetzt.betreiber_konsole),
  * stimmt das neue Passwort mit SUPER_ADMIN_PASSWORD der .env ueberein, wird
    der Alarm "super_admin_passwort_env_abweichend" geschlossen.
Die Zwei-Faktor-Anmeldung bleibt, wie sie ist. Ist auch das Handy weg:
    python scripts/mfa_pruefen.py --konto <name> --abschalten --ja

Exit 0 = gesetzt, 1 = Konto nicht gefunden / Passwort abgelehnt,
2 = ohne --ja (nichts geaendert).
"""
import argparse
import getpass
import os
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))


def _passwort_abfragen(eingabe=None) -> str:
    """Neues Passwort holen: `eingabe` (Tests), sonst verdeckt zweimal am
    Terminal, ohne Terminal die erste Zeile der Standardeingabe."""
    if eingabe is not None:
        return eingabe()
    if sys.stdin is not None and sys.stdin.isatty():
        erstes = getpass.getpass("Neues Passwort: ")
        zweites = getpass.getpass("Neues Passwort (wiederholen): ")
        if erstes != zweites:
            raise ValueError("Die beiden Eingaben stimmen nicht ueberein")
        return erstes
    return (sys.stdin.readline() if sys.stdin is not None else "").rstrip("\r\n")


def main(argv=None, db=None, eingabe=None) -> int:
    ap = argparse.ArgumentParser(description="Passwort des Betreiberkontos setzen (Notweg)")
    ap.add_argument("--konto", required=True,
                    help="Benutzername des Super-Admin-Kontos (SUPER_ADMIN_USERNAME)")
    ap.add_argument("--ja", action="store_true",
                    help="Bestaetigung — ohne --ja wird nichts geaendert")
    ap.add_argument("--db", default=None, help="Datenbankname (Standard: DB_NAME oder autoschnell)")
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

    name = args.konto.strip()
    nutzer = db.users.find_one(
        {"username": name, "role": "admin", "is_super_admin": True},
        {"_id": 0, "id": 1, "username": 1, "password_hash": 1, "email": 1,
         "company_name": 1, "active": 1})
    if not nutzer:
        print(f"FEHLER: Kein Betreiberkonto (Super-Admin) mit dem Benutzernamen '{name}'.")
        print("Vorhandene Admin-Konten (Benutzername):")
        for u in db.users.find({"role": "admin"}, {"_id": 0, "username": 1}):
            print("   ", u.get("username") or "(ohne Benutzernamen)")
        return 1
    if not args.ja:
        print(f"Konto gefunden: {nutzer.get('username')}. Es wurde NICHTS geaendert — "
              "zum Setzen mit --ja erneut aufrufen.")
        return 2
    if nutzer.get("active") is False:
        print("HINWEIS: Das Konto ist gesperrt (active=false). Das Passwort wird "
              "gesetzt, die Sperre bleibt.")

    try:
        neu = _passwort_abfragen(eingabe)
        from passwoerter import persoenliche_werte, pruefe_passwort
        pruefe_passwort(neu, persoenliche_werte(nutzer))
    except ValueError as exc:
        print(f"FEHLER: {exc}. Nichts geaendert.")
        return 1

    from auth import hash_password
    jetzt = datetime.now(timezone.utc).isoformat()
    # Compare-and-Set auf den gelesenen Hash: aendert jemand parallel das
    # Passwort (Einstellungen), wird nichts ueberschrieben.
    r = db.users.update_one(
        {"id": nutzer["id"], "password_hash": nutzer.get("password_hash")},
        {"$set": {"password_hash": hash_password(neu), "current_session_id": None,
                  "passwort_gesetzt_am": jetzt, "updated_at": jetzt}})
    if not r.matched_count:
        print("FEHLER: Das Passwort wurde gerade anderweitig geaendert — bitte erneut "
              "aufrufen. Nichts geaendert.")
        return 1

    # Anmeldesperre des Kontos aufheben (gleiche Schluessel wie
    # scripts/anmeldesperre_aufheben.py / rate_limiter.reset).
    try:
        from anmeldesperre_aufheben import fenster_sekunden, schluessel
        ids = schluessel(name, fenster_sekunden())
        if ids:
            db.rate_limits.delete_many({"_id": {"$in": ids}})
    except Exception as exc:  # noqa: BLE001 — Passwort ist gesetzt
        print(f"Hinweis: Anmeldesperre nicht aufgehoben ({exc}) — bei Bedarf "
              f"scripts/anmeldesperre_aufheben.py {name} --ausfuehren")

    db.activity_logs.insert_one({
        "id": uuid.uuid4().hex, "dealer_id": "", "user_id": nutzer["id"],
        "action": "auth.passwort.gesetzt.betreiber_konsole",
        "meta": {"skript": "betreiber_passwort_setzen.py", "username": nutzer.get("username")},
        "created_at": jetzt})

    env_pw = os.environ.get("SUPER_ADMIN_PASSWORD", "")
    if env_pw and env_pw == neu:
        db.betriebsalarme.update_many(
            {"typ": "super_admin_passwort_env_abweichend", "ref": str(nutzer["id"]),
             "offen": True},
            {"$set": {"offen": False, "quittiert_am": jetzt,
                      "quittiert_von": "system:betreiber_passwort_setzen"}})
    elif env_pw:
        print("Hinweis: SUPER_ADMIN_PASSWORD in der .env weicht vom neuen Passwort ab. "
              "Das ist erlaubt (es gilt das gespeicherte), der Start meldet es aber als "
              "Alarm — die .env bei Gelegenheit angleichen.")

    print(f"Passwort fuer '{nutzer.get('username')}' GESETZT. Die bisherige Sitzung ist "
          "beendet, die Anmeldesperre aufgehoben.")
    print("-> Jetzt mit Benutzername + neuem Passwort anmelden (Zwei-Faktor wie bisher).")
    return 0


if __name__ == "__main__":
    sys.exit(main())
