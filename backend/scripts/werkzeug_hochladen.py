# -*- coding: utf-8 -*-
"""Programmdatei eines Werkzeugs hochladen (03.10.2026) — siehe backend/werkzeuge.py.

Die EXE des AutoPointer-Vergleichs (~55 MB) ist groesser als das Upload-Limit
von nginx (25 MB). Deshalb laeuft das Hochladen hier im Backend-Container —
direkt in den Datei-Speicher (S3/R2: EIN Upload reicht fuer beide Server).

Aufruf (Server, im Repo-Ordner):
    docker compose cp AutoSchnell-Vergleich.exe backend:/tmp/AutoSchnell-Vergleich.exe
    docker compose exec backend python scripts/werkzeug_hochladen.py /tmp/AutoSchnell-Vergleich.exe --version 1.0.0

Wer das Werkzeug sieht, steht NICHT hier, sondern in AUTOPOINTER_VERGLEICH_KUNDEN
(Standard 10001,10002).

Browser-Helfer (04.10.2026): dieselbe Datei-Pruefung als ZIP mit manifest.json —
    docker compose exec backend python scripts/werkzeug_hochladen.py /tmp/AutoSchnell-Helfer.zip --werkzeug browser-helfer --version 2.0.0
Freigabe in BROWSER_HELFER_KUNDEN.

Exit 0 = hochgeladen, 2 = Datei fehlt/ungueltig.
"""
import argparse
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


def main(argv=None, db=None, storage=None) -> int:
    ap = argparse.ArgumentParser(description="Programmdatei eines Werkzeugs hochladen")
    ap.add_argument("datei", help="Pfad zur .exe (Programm) bzw. .zip (Browser-Helfer)")
    ap.add_argument("--werkzeug", default="autopointer-vergleich", help="Werkzeug-ID")
    ap.add_argument("--version", default="", help="Versionsangabe, z.B. 1.0.0 (Standard: heutiges Datum)")
    ap.add_argument("--db", default=None, help="Datenbankname (Standard: DB_NAME oder autoschnell)")
    args = ap.parse_args(argv)

    pfad = Path(args.datei)
    if not pfad.is_file():
        print(f"Datei nicht gefunden: {pfad}")
        return 2

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

    import werkzeuge as wz
    try:
        meta = wz.hochladen(db, args.werkzeug, pfad.read_bytes(), args.version, storage=storage)
    except ValueError as exc:
        print(f"Nicht hochgeladen: {exc}")
        return 2
    print(f"Hochgeladen: {meta['schluessel']}  Version {meta['version']}  "
          f"{meta['groesse'] / 1024 / 1024:.1f} MB  sha256 {meta['sha256'][:16]}…")
    print("Verfügbar mit: AutoSchnell Pro")
    return 0


if __name__ == "__main__":
    sys.exit(main())
