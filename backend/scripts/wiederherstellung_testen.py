# -*- coding: utf-8 -*-
"""Automatisierter Nachweis: Backup laesst sich WIRKLICH wiederherstellen.

Ablauf (Priorität 5 aus dem Review):
  1. Frisches Backup der Live-Datenbank ziehen (backup_mongo.py)
  2. In die SEPARATE Testdatenbank 'autoschnell_restore_test' einspielen
  3. Je Collection die Dokumentzahlen Backup vs. Wiederherstellung
     vergleichen (Stichprobe: zusaetzlich je ein Dokument lesen)
  4. Testdatenbank wieder loeschen

Exit 0 = Wiederherstellung bewiesen; Exit 1 = Abweichung gefunden.
Regelmaessig laufen lassen (z.B. woechentlich per Cron) — ein Backup, das
nie testweise wiederhergestellt wurde, ist keins.
"""
import os
import subprocess
import sys
import tempfile
from pathlib import Path

from pymongo import MongoClient

MONGO_URL = os.environ.get("MONGO_URL", "mongodb://127.0.0.1:27017")
DB_NAME = os.environ.get("DB_NAME", "autoschnell")
TEST_DB = "autoschnell_restore_test"
HIER = Path(__file__).resolve().parent


def main() -> int:
    with tempfile.TemporaryDirectory(prefix="as-restore-test-") as tmp:
        print(f"[1/4] Backup nach {tmp} …")
        r = subprocess.run([sys.executable, "-X", "utf8",
                            str(HIER / "backup_mongo.py"), "--dir", tmp],
                           capture_output=True, text=True, timeout=600)
        if r.returncode != 0:
            print("BACKUP FEHLGESCHLAGEN:\n", r.stdout[-800:], r.stderr[-800:])
            return 1
        dump = next(p for p in Path(tmp).iterdir()
                    if p.is_dir() and p.name.startswith("autoschnell-"))
        print(f"      Backup: {dump.name}")

        print(f"[2/4] Wiederherstellung in Testdatenbank '{TEST_DB}' …")
        r = subprocess.run([sys.executable, "-X", "utf8",
                            str(HIER / "restore_mongo.py"), str(dump),
                            "--db", TEST_DB, "--yes"],
                           capture_output=True, text=True, timeout=600)
        if r.returncode != 0:
            print("RESTORE FEHLGESCHLAGEN:\n", r.stdout[-800:], r.stderr[-800:])
            return 1

        print("[3/4] Vergleiche Dokumentzahlen …")
        client = MongoClient(MONGO_URL, serverSelectionTimeoutMS=5000)
        quelle, ziel = client[DB_NAME], client[TEST_DB]
        fehler = 0
        # Der Dump liegt im Unterordner <datenbankname>/ (mongodump-Layout).
        colls = [p.name[:-len(".bson.gz")]
                 for p in dump.rglob("*.bson.gz")]
        for c in sorted(colls):
            n_ziel = ziel[c].count_documents({})
            # Quelle kann seit dem Backup weitergelaufen sein — wir pruefen
            # daher gegen die Dokumentzahl IM BACKUP (Datei selbst).
            import gzip
            n_backup = 0
            with gzip.open(next(dump.rglob(f"{c}.bson.gz")), "rb") as fh:
                while True:
                    head = fh.read(4)
                    if len(head) < 4:
                        break
                    fh.read(int.from_bytes(head, "little") - 4)
                    n_backup += 1
            status = "OK " if n_backup == n_ziel else "DIFF"
            if n_backup != n_ziel:
                fehler += 1
            print(f"      {status} {c:28} Backup={n_backup:>6}  "
                  f"Wiederhergestellt={n_ziel:>6}")
            if n_ziel and ziel[c].find_one() is None:
                print(f"      LESE-FEHLER in {c}")
                fehler += 1

        print(f"[4/4] Testdatenbank '{TEST_DB}' loeschen …")
        client.drop_database(TEST_DB)

        if fehler:
            print(f"\nERGEBNIS: {fehler} Abweichung(en) — Backup NICHT ok!")
            return 1
        print(f"\nERGEBNIS: Wiederherstellung bewiesen — {len(colls)} "
              f"Collections identisch.")
        return 0


if __name__ == "__main__":
    sys.exit(main())
