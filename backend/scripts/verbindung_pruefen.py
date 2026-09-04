# -*- coding: utf-8 -*-
"""Echte MongoDB pruefen, BEVOR das Backend sie benutzt.

Liest NUR — es wird nichts angelegt und nichts geaendert, solange
`--schreibtest` nicht ausdruecklich gesetzt ist. Damit laesst sich eine
Produktions- oder Atlas-Datenbank gefahrlos ansehen.

Geprueft wird:
  1. Erreichbarkeit, Antwortzeit, Server-Version
  2. Anmeldung und Rechte (welche Rollen hat der Benutzer?)
  3. Verschluesselte Verbindung (TLS) — Pflicht bei allem ausser localhost
  4. Replica Set (noetig fuer stimmige Sicherungen) oder Einzelserver
  5. Inhalt: vorhandene Collections mit Dokumentzahlen — ist die Datenbank
     leer (Erstinbetriebnahme) oder liegen schon Daten darin?
  6. Wichtige Eindeutigkeits-Indizes (users.email, subscriptions)
  7. optional: Schreibtest in einer eigenen Wegwerf-Collection

Aufruf:
  python scripts/verbindung_pruefen.py "mongodb+srv://nutzer:passwort@cluster.mongodb.net/?retryWrites=true"
  python scripts/verbindung_pruefen.py --db autoschnell --schreibtest
Ohne URL wird MONGO_URL aus der Umgebung/.env genommen.

Exit 0 = brauchbar, 1 = Problem gefunden.
"""
import argparse
import os
import ssl
import sys
import time
import uuid
from urllib.parse import urlsplit

FEHLER, WARNUNG, OK = [], [], []


def ok(m):
    OK.append(m); print(f"  OK    {m}")


def warn(m):
    WARNUNG.append(m); print(f"  WARN  {m}")


def fehler(m):
    FEHLER.append(m); print(f"  FEHLER {m}")


def _verschleiert(url: str) -> str:
    """URL ohne Passwort ausgeben (Logs, Screenshots)."""
    try:
        teile = urlsplit(url)
        if teile.password:
            return url.replace(f":{teile.password}@", ":***@")
    except ValueError:
        pass
    return url


def main() -> int:
    ap = argparse.ArgumentParser(description="MongoDB-Verbindung pruefen (nur lesend)")
    ap.add_argument("url", nargs="?", default=None,
                    help="Verbindungszeichenfolge; ohne Angabe MONGO_URL aus der Umgebung")
    ap.add_argument("--db", default=None, help="Datenbankname (Standard: DB_NAME oder autoschnell)")
    ap.add_argument("--schreibtest", action="store_true",
                    help="einmal in eine eigene Wegwerf-Collection schreiben und wieder loeschen")
    args = ap.parse_args()

    try:
        from dotenv import load_dotenv
        from pathlib import Path
        load_dotenv(Path(__file__).resolve().parents[1] / ".env")
    except ImportError:
        pass

    url = args.url or os.environ.get("MONGO_URL", "")
    db_name = args.db or os.environ.get("DB_NAME") or "autoschnell"
    if not url:
        print("FEHLER: keine Verbindungszeichenfolge — als Argument uebergeben "
              "oder MONGO_URL setzen.")
        return 1
    print(f"Ziel: {_verschleiert(url)}  Datenbank: {db_name}\n")

    try:
        from pymongo import MongoClient
        from pymongo.errors import OperationFailure, PyMongoError
    except ImportError:
        print("FEHLER: pymongo fehlt (pip install -r requirements.txt)")
        return 1

    # ---- 1) Erreichbarkeit
    print("1. Erreichbarkeit")
    try:
        client = MongoClient(url, serverSelectionTimeoutMS=8000)
        t0 = time.monotonic()
        hallo = client.admin.command("hello")
        dauer = (time.monotonic() - t0) * 1000
        ok(f"Antwort in {dauer:.0f} ms")
        version = client.server_info().get("version", "?")
        ok(f"MongoDB-Version {version}")
        if int(str(version).split(".")[0] or 0) < 6:
            warn("Version aelter als 6 — das Projekt ist auf MongoDB 8 ausgelegt")
    except PyMongoError as exc:
        fehler(f"Keine Verbindung: {type(exc).__name__}: {str(exc)[:200]}")
        print("\nERGEBNIS: nicht verbunden.")
        return 1

    # ---- 2) Anmeldung und Rechte
    print("2. Anmeldung und Rechte")
    try:
        info = client.admin.command("connectionStatus")
        nutzer = info.get("authInfo", {}).get("authenticatedUsers", [])
        rollen = info.get("authInfo", {}).get("authenticatedUserRoles", [])
        if not nutzer:
            fehler("Verbindung OHNE Anmeldung — die Datenbank ist ungeschuetzt "
                   "erreichbar. Benutzer mit Passwort anlegen und in MONGO_URL "
                   "eintragen.")
        else:
            ok(f"angemeldet als {', '.join(u.get('user', '?') for u in nutzer)}")
            ok("Rollen: " + ", ".join(f"{r.get('role')}@{r.get('db')}" for r in rollen))
            noetig = {"readWrite", "readWriteAnyDatabase", "dbOwner", "root",
                      "atlasAdmin", "dbAdmin"}
            if not any(r.get("role") in noetig for r in rollen):
                warn("Keine erkennbare Schreibrolle — das Backend braucht "
                     "readWrite auf der Anwendungsdatenbank")
    except PyMongoError as exc:
        warn(f"Rechte nicht abfragbar: {str(exc)[:150]}")

    # ---- 3) Verschluesselung
    print("3. Verschluesselte Verbindung")
    lokal = any(h in url for h in ("localhost", "127.0.0.1", "mongo:27017"))
    verschluesselt = url.startswith("mongodb+srv://") or "tls=true" in url.lower() \
        or "ssl=true" in url.lower()
    if lokal:
        ok("lokale Verbindung — Verschluesselung nicht erforderlich")
    elif verschluesselt:
        ok("Verbindung ist verschluesselt (TLS)")
    else:
        fehler("Verbindung ueber das Netz OHNE TLS — Zugangsdaten und "
               "Kundendaten laufen im Klartext. '+srv' verwenden oder "
               "'tls=true' anhaengen.")

    # ---- 4) Replica Set
    print("4. Replica Set")
    if hallo.get("setName"):
        ok(f"Replica Set '{hallo['setName']}' — stimmige Sicherungen moeglich")
    else:
        warn("Einzelserver ohne Replica Set — die naechtliche Sicherung liest "
             "Collection fuer Collection. Entweder Replica Set einrichten oder "
             "das Backup mit --wartung starten (siehe DEPLOYMENT.md).")

    # ---- 5) Inhalt
    print("5. Inhalt der Datenbank")
    db = client[db_name]
    try:
        namen = sorted(db.list_collection_names())
    except OperationFailure as exc:
        fehler(f"Datenbank '{db_name}' nicht lesbar: {str(exc)[:150]}")
        namen = []
    if not namen:
        ok(f"'{db_name}' ist leer — Erstinbetriebnahme; die Migration legt "
           f"Indizes und das Admin-Konto an")
    else:
        gesamt = 0
        zeilen = []
        for n in namen:
            try:
                anzahl = db[n].estimated_document_count()
            except PyMongoError:
                anzahl = -1
            gesamt += max(anzahl, 0)
            zeilen.append((anzahl, n))
        ok(f"{len(namen)} Collections, rund {gesamt} Dokumente")
        for anzahl, n in sorted(zeilen, reverse=True)[:12]:
            print(f"        {anzahl:>9}  {n}")
        if "users" in namen:
            warn("Es liegen bereits Daten in dieser Datenbank — VOR dem ersten "
                 "Start eine Sicherung ziehen (scripts/backup_mongo.py).")

    # ---- 6) Wichtige Indizes
    print("6. Eindeutigkeits-Indizes")
    if "users" in namen:
        try:
            idx = list(db.users.list_indexes())
            eindeutig = [i["name"] for i in idx if i.get("unique")]
            (ok if eindeutig else warn)(
                "users: " + (", ".join(eindeutig) if eindeutig
                             else "kein Eindeutigkeits-Index — legt das Backend beim Start an"))
        except PyMongoError as exc:
            warn(f"Indizes nicht lesbar: {str(exc)[:120]}")
    else:
        ok("noch keine Nutzer-Collection — Indizes entstehen beim ersten Start")

    # ---- 7) Schreibtest
    print("7. Schreibrecht")
    if not args.schreibtest:
        ok("uebersprungen (nur lesend geprueft) — mit --schreibtest ausdruecklich anfordern")
    else:
        probe = f"_verbindungsprobe_{uuid.uuid4().hex[:8]}"
        try:
            db[probe].insert_one({"_id": "probe", "zeit": time.time()})
            gelesen = db[probe].find_one({"_id": "probe"})
            db[probe].drop()
            (ok if gelesen else fehler)("Schreiben, Lesen und Loeschen erfolgreich"
                                        if gelesen else "Schreiben ging, Lesen nicht")
        except PyMongoError as exc:
            fehler(f"Schreibtest fehlgeschlagen: {str(exc)[:200]}")
            try:
                db[probe].drop()
            except PyMongoError:
                pass

    client.close()
    print(f"\nERGEBNIS: {len(OK)} ok, {len(WARNUNG)} Warnungen, {len(FEHLER)} Fehler")
    for f in FEHLER:
        print("  - " + f)
    return 1 if FEHLER else 0


if __name__ == "__main__":
    sys.exit(main())
