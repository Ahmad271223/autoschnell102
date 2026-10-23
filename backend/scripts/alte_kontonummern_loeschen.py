# -*- coding: utf-8 -*-
"""Alte Konten mit Nummern aus der Reihe loeschen (Wunsch Ahmad 14.09.2026).

Seit 14.09.2026 bekommen Zwischenhaendler einen Kaeufer-Code ("6FE7K2M") und
Fahrer ihre Fahrer-ID ("FD-7K2M9QX4") als Kontonummer. Konten aus der Zeit
davor tragen noch eine reine Nummer aus der Reihe der Firmen ("10031"). Sie
funktionieren weiter — der Betreiber will sie aber weg haben, damit es nur
noch das neue Muster gibt (Live-Testdaten).

Geloescht wird ueber DIESELBEN Wege wie im Admin-Bereich (routes.admin:
admin_delete_user fuer Zwischenhaendler, admin_delete_driver fuer Fahrer), also
mit Netzwerk-Mitgliedschaften, Merkliste, Verknuepfungen zu Firmen, Trennung
offener Termine und Audit. Firmen und Sucher werden NIE angefasst.

Aufruf (im Container):
    python scripts/alte_kontonummern_loeschen.py                            # nur anzeigen
    python scripts/alte_kontonummern_loeschen.py --ausfuehren --erwartet 7  # wirklich loeschen

Pruefbericht 20.09.2026 (SK-13): --ausfuehren verlangt --erwartet N (die
Zahl aus dem Probelauf) — weicht die gefundene Anzahl ab, passiert nichts.
Ziel (Host ohne Passwort, Datenbank) wird vorher ausgegeben; an einem
Terminal wird zusaetzlich 'ja' abgefragt.
"""
from __future__ import annotations

import argparse
import asyncio
import os
import sys
from pathlib import Path
from urllib.parse import urlsplit

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


def _verschleiert(url: str) -> str:
    """MONGO_URL ohne Passwort (SK-13)."""
    try:
        teile = urlsplit(url)
        if teile.password:
            return url.replace(f":{teile.password}@", ":***@")
    except ValueError:
        pass
    return url


def _bestaetigt(gefunden: int) -> bool:
    """SK-13: an einem Terminal 'ja' verlangen; ohne Terminal (Pipe,
    docker compose exec -T) gilt --erwartet als Bestaetigung."""
    if not sys.stdin.isatty():
        return True
    try:
        antwort = input(f"{gefunden} Konto/Konten unwiderruflich loeschen? [ja/nein] ")
    except EOFError:
        return False
    return antwort.strip().lower() == "ja"


async def _lauf(ausfuehren: bool, erwartet: int | None = None) -> int:
    from deps import db
    from kontonummer import ist_kontonummer
    import routes.admin as ADMIN

    print(f"Ziel: {_verschleiert(os.environ.get('MONGO_URL', ''))}  "
          f"Datenbank: {os.environ.get('DB_NAME') or db.name}")
    kaeufer = [k async for k in db.users.find(
        {"role": "b2b_buyer", "kontonummer": {"$type": "string"}},
        {"_id": 0, "id": 1, "kontonummer": 1, "company_name": 1, "contact_name": 1,
         "created_at": 1})]
    fahrer = [f async for f in db.driver_accounts.find(
        {"kontonummer": {"$type": "string"}},
        {"_id": 0, "id": 1, "kontonummer": 1, "display_name": 1, "driver_code": 1,
         "created_at": 1})]
    alte_kaeufer = [k for k in kaeufer if ist_kontonummer(k["kontonummer"])]
    alte_fahrer = [f for f in fahrer if ist_kontonummer(f["kontonummer"])]

    print(f"Zwischenhaendler gesamt {len(kaeufer)}, davon mit alter Nummer: {len(alte_kaeufer)}")
    for k in alte_kaeufer:
        print(f"  Kaeufer {k['kontonummer']:>10}  {k.get('company_name') or k.get('contact_name') or ''}"
              f"  (angelegt {str(k.get('created_at') or '')[:10]})")
    print(f"Fahrer gesamt {len(fahrer)}, davon mit alter Nummer: {len(alte_fahrer)}")
    for f in alte_fahrer:
        print(f"  Fahrer  {f['kontonummer']:>10}  {f.get('display_name') or ''}  "
              f"{f.get('driver_code') or ''}  (angelegt {str(f.get('created_at') or '')[:10]})")
    if not alte_kaeufer and not alte_fahrer:
        print("Nichts zu tun — alle Zwischenhaendler und Fahrer tragen schon das neue Muster.")
        return 0
    gefunden = len(alte_kaeufer) + len(alte_fahrer)
    if not ausfuehren:
        print(f"\nPROBELAUF — nichts geloescht. Zum Loeschen: --ausfuehren --erwartet {gefunden}")
        return 0
    # SK-13: Obergrenze/Gegenprobe — nur die Anzahl aus dem Probelauf
    if erwartet is None or erwartet != gefunden:
        print(f"\nABGEBROCHEN: gefunden {gefunden}, --erwartet "
              f"{'fehlt' if erwartet is None else erwartet}. Erst Probelauf, dann "
              f"--ausfuehren --erwartet {gefunden}. Nichts geloescht.")
        return 1
    if not _bestaetigt(gefunden):
        print("Abgebrochen — nichts geloescht.")
        return 1

    # Handelnder fuer das Audit: der Super-Admin (sonst ein Skript-Platzhalter).
    sa = await db.users.find_one({"role": "admin", "is_super_admin": True},
                                 {"_id": 0, "id": 1, "dealer_id": 1, "username": 1})
    admin = sa or {"id": "skript:alte_kontonummern_loeschen", "dealer_id": ""}

    fehler = 0
    for k in alte_kaeufer:
        try:
            await ADMIN.admin_delete_user(k["id"], firma_loeschen=False, admin=admin)
            print(f"geloescht: Kaeufer {k['kontonummer']}")
        except Exception as exc:  # noqa: BLE001 — jedes Konto einzeln melden
            fehler += 1
            print(f"FEHLER bei Kaeufer {k['kontonummer']}: {exc}")
    for f in alte_fahrer:
        try:
            await ADMIN.admin_delete_driver(f["id"], admin=admin)
            print(f"geloescht: Fahrer {f['kontonummer']}")
        except Exception as exc:  # noqa: BLE001
            fehler += 1
            print(f"FEHLER bei Fahrer {f['kontonummer']}: {exc}")
    print(f"\nFertig: {len(alte_kaeufer) + len(alte_fahrer) - fehler} geloescht, {fehler} Fehler.")
    return 1 if fehler else 0


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Zwischenhaendler und Fahrer mit alter Nummer loeschen")
    ap.add_argument("--ausfuehren", action="store_true",
                    help="wirklich loeschen (ohne diese Angabe nur Probelauf)")
    ap.add_argument("--erwartet", type=int, default=None, metavar="N",
                    help="Pflicht mit --ausfuehren: Anzahl der Konten aus dem Probelauf; "
                         "weicht der Fund ab, wird nichts geloescht (SK-13)")
    args = ap.parse_args(argv)
    try:
        from dotenv import load_dotenv
        load_dotenv(Path(__file__).resolve().parents[1] / ".env")
    except ImportError:
        pass
    if not os.environ.get("MONGO_URL"):
        os.environ["MONGO_URL"] = "mongodb://127.0.0.1:27017"
    return asyncio.run(_lauf(args.ausfuehren, args.erwartet))


if __name__ == "__main__":
    sys.exit(main())
