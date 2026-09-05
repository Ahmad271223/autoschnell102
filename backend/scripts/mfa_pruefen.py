# -*- coding: utf-8 -*-
"""Zwei-Faktor-Anmeldung nachmessen, statt zu raten.

Sagt fuer ein Konto, ob ein Geheimnis hinterlegt ist, wie alt es ist, und —
wenn ein Code mitgegeben wird — ob dieser Code dazu passt und um wie viel
die Uhr der App danebenliegt.

Aufruf (im Container):
    python scripts/mfa_pruefen.py --konto chef-f525c3
    python scripts/mfa_pruefen.py --konto chef-f525c3 --code 123456

Typische Ergebnisse:
  * "kein Geheimnis"      -> zuerst in den Einstellungen "Einrichten" klicken
  * "Code passt (Abstand 0)" -> alles in Ordnung
  * "Code passt, aber die Uhr der App geht N Sekunden falsch"
  * "Code passt zu KEINEM Zeitpunkt" -> die App hat ein anderes Geheimnis,
    meist weil "Einrichten" mehrfach geklickt und der alte Eintrag in der
    App behalten wurde. Loesung: Eintrag in der App loeschen, einmal neu
    einrichten, den frisch angezeigten Schluessel verwenden.

Aendert nichts an der Datenbank.
"""
import argparse
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


def main() -> int:
    ap = argparse.ArgumentParser(description="Zwei-Faktor-Anmeldung pruefen (nur lesend)")
    ap.add_argument("--konto", required=True,
                    help="Benutzername oder E-Mail des Admin-Kontos")
    ap.add_argument("--code", default="", help="6-stelliger Code aus der App")
    ap.add_argument("--fenster", type=int, default=20,
                    help="wie viele 30-Sekunden-Schritte in beide Richtungen "
                         "gesucht wird (Standard 20 = +/- 10 Minuten)")
    args = ap.parse_args()

    try:
        from dotenv import load_dotenv
        load_dotenv(Path(__file__).resolve().parents[1] / ".env")
    except ImportError:
        pass
    from pymongo import MongoClient
    import mfa

    url = os.environ.get("MONGO_URL") or "mongodb://127.0.0.1:27017"
    db = MongoClient(url, serverSelectionTimeoutMS=8000)[
        os.environ.get("DB_NAME") or "autoschnell"]

    such = args.konto.strip()
    nutzer = db.users.find_one(
        {"$or": [{"username": such}, {"email": such.lower()},
                 {"email": {"$regex": f"^{such}$", "$options": "i"}}]},
        {"_id": 0, "id": 1, "email": 1, "username": 1, "role": 1, "mfa": 1})
    if not nutzer:
        print(f"FEHLER: Kein Konto '{such}' gefunden.")
        print("Vorhandene Admin-Konten:")
        for u in db.users.find({"role": "admin"}, {"_id": 0, "email": 1, "username": 1}):
            print("   ", u.get("username") or "", u.get("email") or "")
        return 1

    print(f"Konto : {nutzer.get('username') or nutzer.get('email')}  (Rolle {nutzer.get('role')})")
    m = nutzer.get("mfa") or {}
    aktiv = bool(m.get("aktiv"))
    print(f"Status: {'AKTIV' if aktiv else 'noch nicht aktiv'}")

    quelle = "secret" if aktiv else "pending_secret"
    roh = m.get(quelle)
    if not roh:
        print(f"\nKein Geheimnis hinterlegt ({quelle} fehlt).")
        print("-> In den Einstellungen einmal 'Einrichten' klicken und den")
        print("   angezeigten Schluessel in die App uebernehmen.")
        return 1
    if m.get("pending_seit"):
        try:
            alter = (datetime.now(timezone.utc)
                     - datetime.fromisoformat(m["pending_seit"])).total_seconds() / 60
            print(f"Geheimnis erzeugt vor {alter:.0f} Minuten")
        except (TypeError, ValueError):
            pass

    secret = mfa.entschluesseln(roh)
    if not secret:
        print("\nFEHLER: Das gespeicherte Geheimnis laesst sich nicht entschluesseln.")
        print("-> Meist wurde JWT_SECRET geaendert, nachdem die Zwei-Faktor-")
        print("   Anmeldung eingerichtet wurde. Loesung: Eintrag in der App")
        print("   loeschen und neu einrichten.")
        return 1
    print(f"Geheimnis lesbar ({len(secret)} Zeichen)")
    print(f"Serverzeit: {datetime.now(timezone.utc):%Y-%m-%d %H:%M:%S} UTC")
    jetzt = int(time.time() // 30)
    print(f"Aktuell gueltiger Code: {mfa.totp(secret, jetzt)}  "
          f"(noch {30 - int(time.time() % 30)} Sekunden)")
    if m.get("letzter_zaehler", -1) >= jetzt:
        print("HINWEIS: Dieser Zeitabschnitt wurde bereits verbraucht — der")
        print("         naechste Code gilt erst im folgenden Fenster.")

    if not args.code:
        print("\nMit --code <6 Ziffern> laesst sich ein Code aus der App pruefen.")
        return 0

    code = args.code.strip().replace(" ", "")
    for delta in range(-args.fenster, args.fenster + 1):
        if mfa.totp(secret, jetzt + delta) == code:
            versatz = delta * 30
            if delta == 0:
                print(f"\nCode passt (Abstand 0) — alles in Ordnung.")
            else:
                print(f"\nCode passt, aber mit {versatz:+d} Sekunden Versatz.")
                print("-> Die Uhr des Geraets mit der App stellen "
                      "(Automatisch/Netzwerkzeit einschalten).")
            return 0
    print(f"\nCode passt zu KEINEM Zeitpunkt in +/- {args.fenster * 30 // 60} Minuten.")
    print("-> Die App hat ein ANDERES Geheimnis als der Server. Das passiert,")
    print("   wenn 'Einrichten' mehrfach geklickt wurde und in der App noch")
    print("   der erste Eintrag steht. Loesung: Eintrag in der App loeschen,")
    print("   einmal neu einrichten, den frisch angezeigten Schluessel nehmen.")
    return 1


if __name__ == "__main__":
    sys.exit(main())
