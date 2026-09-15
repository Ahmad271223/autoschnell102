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

Aendert nichts an der Datenbank — AUSSER mit --abschalten --ja (14.09.2026,
Betreiber ausgesperrt: "Code ungueltig", obwohl er vorher immer passte):
schaltet die Zwei-Faktor-Anmeldung des Kontos ab und beendet seine Sitzung.
Danach Anmeldung nur mit Benutzername + Passwort; in den Einstellungen
anschliessend NEU einrichten (neuer Schluessel, neue Wiederherstellungscodes).
    python scripts/mfa_pruefen.py --konto chef-f525c3 --abschalten --ja
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
    # Kontonummer (13.09.2026): Zwei-Faktor hat nur der Super-Admin, und der
    # meldet sich mit Benutzername an (Kontonummern haben kein MFA).
    ap.add_argument("--konto", required=True,
                    help="Benutzername des Super-Admin-Kontos (SUPER_ADMIN_USERNAME)")
    ap.add_argument("--code", default="", help="6-stelliger Code aus der App")
    ap.add_argument("--fenster", type=int, default=20,
                    help="wie viele 30-Sekunden-Schritte in beide Richtungen "
                         "gesucht wird (Standard 20 = +/- 10 Minuten)")
    ap.add_argument("--abschalten", action="store_true",
                    help="Zwei-Faktor-Anmeldung dieses Kontos abschalten (Notfall, "
                         "nur zusammen mit --ja)")
    ap.add_argument("--ja", action="store_true", help="Bestaetigung fuer --abschalten")
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
        {"username": such, "role": "admin"},
        {"_id": 0, "id": 1, "username": 1, "role": 1, "is_super_admin": 1, "mfa": 1})
    if not nutzer:
        print(f"FEHLER: Kein Admin-Konto mit dem Benutzernamen '{such}' gefunden.")
        from kontonummer import kennung_normalisieren
        if kennung_normalisieren(such):
            print("HINWEIS: Das ist eine Kontonummer bzw. ein Kaeufer-Code — Chef, "
                  "Sucher, Zwischenhaendler und Fahrer haben keine Zwei-Faktor-Anmeldung.")
        print("Vorhandene Admin-Konten (Benutzername):")
        for u in db.users.find({"role": "admin"}, {"_id": 0, "username": 1}):
            print("   ", u.get("username") or "(ohne Benutzernamen — Anmeldung nicht moeglich)")
        return 1

    print(f"Konto : {nutzer.get('username')}  (Rolle {nutzer.get('role')}"
          f"{', Super-Admin' if nutzer.get('is_super_admin') else ''})")
    m = nutzer.get("mfa") or {}
    aktiv = bool(m.get("aktiv"))
    print(f"Status: {'AKTIV' if aktiv else 'noch nicht aktiv'}")

    if args.abschalten:
        if not args.ja:
            print("\nABBRUCH: --abschalten braucht die Bestaetigung --ja. Nichts geaendert.")
            return 2
        if not m:
            print("\nKeine Zwei-Faktor-Daten vorhanden — nichts abzuschalten.")
            return 0
        # Geheimnis, Wiederherstellungscodes und Sperre komplett entfernen;
        # laufende Sitzung beenden (das Zwischen-Token der Anmeldung passt
        # danach ohnehin nicht mehr zum Kontozustand).
        # Runde 14 (15.09.2026): in Produktion laesst /auth/login den Super-Admin
        # ohne Zwei-Faktor nicht mehr herein — nach dem Notfall-Abschalten gilt
        # 30 Minuten Gnadenfrist (mfa.pflicht_ausgesetzt_bis), um sich mit
        # Passwort anzumelden und den zweiten Faktor neu einzurichten.
        from datetime import datetime, timedelta, timezone
        frist = (datetime.now(timezone.utc) + timedelta(minutes=30)).isoformat()
        res = db.users.update_one({"id": nutzer["id"]},
                                  {"$set": {"mfa": {"aktiv": False, "pflicht_ausgesetzt_bis": frist},
                                            "current_session_id": None}})
        print(f"Gnadenfrist ohne Zwei-Faktor bis {frist[:16].replace('T', ' ')} UTC — "
              "jetzt anmelden und den zweiten Faktor neu einrichten.")
        db.activity_logs.insert_one({
            "id": __import__("uuid").uuid4().hex, "dealer_id": "", "user_id": nutzer["id"],
            "action": "auth.mfa.abgeschaltet.betreiber",
            "meta": {"skript": "mfa_pruefen.py", "username": nutzer.get("username")},
            "created_at": datetime.now(timezone.utc).isoformat()})
        print(f"\nZwei-Faktor-Anmeldung ABGESCHALTET ({res.modified_count} Konto geaendert).")
        print("-> Jetzt mit Benutzername + Passwort anmelden, dann in den Einstellungen")
        print("   die Zwei-Faktor-Anmeldung NEU einrichten (alten Eintrag in der App loeschen).")
        return 0

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
