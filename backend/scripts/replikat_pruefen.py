# -*- coding: utf-8 -*-
"""Zustand des MongoDB-Replica-Sets nachsehen — nur lesend.

Sagt in einfachen Worten, ob die Daten wirklich auf zwei Servern liegen:
welche Mitglieder es gibt, wer gerade schreibt (PRIMARY), wie weit die
Kopie hinterherhaengt, und ob bei Ausfall eines Servers automatisch
weitergeschrieben wuerde (Mehrheit).

Aufruf (im Container, Ordner backend):
    python scripts/replikat_pruefen.py

Exit 0 = gesund, 1 = etwas stimmt nicht (Text sagt was).
"""
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


def main() -> int:
    try:
        from dotenv import load_dotenv
        load_dotenv(Path(__file__).resolve().parents[1] / ".env")
    except ImportError:
        pass
    from pymongo import MongoClient
    from pymongo.errors import OperationFailure, PyMongoError

    url = os.environ.get("MONGO_URL") or "mongodb://127.0.0.1:27017"
    try:
        cl = MongoClient(url, serverSelectionTimeoutMS=8000)
        st = cl.admin.command("replSetGetStatus")
    except OperationFailure as exc:
        print(f"Kein Replica Set aktiv ({exc.details.get('codeName', exc)}).")
        print("-> MONGO_EXTRA_ARGS=--replSet rs0 --keyFile ... und rs.initiate().")
        return 1
    except PyMongoError as exc:
        print(f"FEHLER: keine Verbindung ({exc}).")
        return 1

    mitglieder = st.get("members", [])
    print(f"Replica Set '{st.get('set')}' — {len(mitglieder)} Mitglied(er)")
    primary = None
    waehler = 0
    gesund = 0
    for m in mitglieder:
        zustand = m.get("stateStr", "?")
        name = m.get("name", "?")
        rueckstand = ""
        if zustand == "PRIMARY":
            primary = m
        if zustand in ("PRIMARY", "SECONDARY", "ARBITER"):
            gesund += 1
        if m.get("health") == 1 and zustand != "ARBITER" and primary is not None and zustand == "SECONDARY":
            try:
                delta = (primary["optimeDate"] - m["optimeDate"]).total_seconds()
                rueckstand = f", Rueckstand {delta:.0f} s"
            except (KeyError, TypeError):
                pass
        print(f"  {name:28s} {zustand:10s} health={m.get('health')}{rueckstand}")
    # Wahlberechtigte aus der Konfiguration
    try:
        cfg = cl.admin.command("replSetGetConfig")["config"]
        waehler = sum(1 for m in cfg.get("members", []) if m.get("votes", 1) > 0)
    except PyMongoError:
        waehler = len(mitglieder)

    print()
    fehler = []
    if primary is None:
        fehler.append("Kein PRIMARY — es kann gerade NICHT geschrieben werden.")
    sekundaere = [m for m in mitglieder if m.get("stateStr") == "SECONDARY"]
    if not sekundaere:
        fehler.append("Keine Kopie (SECONDARY) — die Daten liegen nur auf EINEM Server.")
    else:
        for m in sekundaere:
            try:
                delta = (primary["optimeDate"] - m["optimeDate"]).total_seconds()
                if delta > 60:
                    fehler.append(f"{m['name']} haengt {delta:.0f} s hinterher.")
            except (KeyError, TypeError):
                pass
    if waehler < 3:
        print(f"HINWEIS: {waehler} Waehler. Faellt ein Server aus, gibt es keine")
        print("         Mehrheit — das Schreiben stoppt, bis jemand eingreift.")
        print("         Ein Schiedsrichter (Arbiter) auf einem DRITTEN Server")
        print("         macht die Umschaltung automatisch.")
    else:
        print(f"{waehler} Waehler: Ausfall eines Servers wird automatisch abgefangen.")
    if fehler:
        for f in fehler:
            print("FEHLER:", f)
        return 1
    print("Replikat ist gesund: Daten liegen auf", gesund, "Mitgliedern.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
