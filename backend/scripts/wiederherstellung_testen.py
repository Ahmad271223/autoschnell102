# -*- coding: utf-8 -*-
"""Restore-Probe: automatisierter Nachweis, dass ein Backup sich WIRKLICH
wiederherstellen laesst.

Ablauf:
  1. Frisches Backup der Live-Datenbank ziehen (backup_mongo.py)
  2. In die SEPARATE Testdatenbank 'autoschnell_restore_test' einspielen
     (--nur-datenbank: die Live-Datei-Speicher bleiben unangetastet)
  3. Je Collection die Dokumentzahl laut Manifest mit der Testdatenbank
     vergleichen (Stichprobe: zusaetzlich je ein Dokument lesen); pruefen,
     dass der Wartungsmodus nach dem Restore wieder aus ist
  4. Testdatenbank (und ihre __vorher_/__restore_-Reste) wieder loeschen

Exit 0 = Wiederherstellung bewiesen und Backup vollstaendig
Exit 2 = Datenbank-Wiederherstellung bewiesen, aber Backup UNVOLLSTAENDIG
         (Datei-Speicher oder Offsite-Kopie fehlt) — Ursache beheben
Exit 1 = Abweichung gefunden bzw. Backup/Restore fehlgeschlagen

Monatlich laufen lassen (DEPLOYMENT.md, "Restore-Probe") — ein Backup, das
nie testweise wiederhergestellt wurde, ist keins.
"""
import json
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


def _aufraeumen(client) -> None:
    for name in client.list_database_names():
        if name == TEST_DB or name.startswith(TEST_DB + "__"):
            client.drop_database(name)


def main() -> int:
    with tempfile.TemporaryDirectory(prefix="as-restore-test-") as tmp:
        print(f"[1/4] Backup nach {tmp} …")
        r = subprocess.run([sys.executable, "-X", "utf8",
                            str(HIER / "backup_mongo.py"), "--dir", tmp],
                           capture_output=True, text=True, timeout=1800)
        if r.returncode not in (0, 2):
            print("BACKUP FEHLGESCHLAGEN:\n", r.stdout[-800:], r.stderr[-800:])
            return 1
        dump = next(p for p in Path(tmp).iterdir()
                    if p.is_dir() and p.name.startswith("autoschnell-"))
        manifest = json.loads((dump / "manifest.json").read_text(encoding="utf-8"))
        fehlend = [str(x) for x in manifest.get("unvollstaendig") or []]
        print(f"      Backup: {dump.name} (Konsistenz: "
              f"{manifest.get('konsistenz', 'unbekannt')}, Offsite: "
              f"{'ja' if manifest.get('offsite') else 'nein'})")
        if fehlend:
            print("      WARNUNG: Backup UNVOLLSTAENDIG — " + "; ".join(fehlend))

        print(f"[2/4] Wiederherstellung in Testdatenbank '{TEST_DB}' (nur Datenbank) …")
        client = MongoClient(MONGO_URL, serverSelectionTimeoutMS=5000)
        _aufraeumen(client)  # Reste einer abgebrochenen Probe
        cmd = [sys.executable, "-X", "utf8", str(HIER / "restore_mongo.py"),
               str(dump), "--db", TEST_DB, "--yes", "--nur-datenbank"]
        if fehlend:
            cmd.append("--notfall-unvollstaendig-akzeptieren")
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=1800)
        if r.returncode != 0 or "RESTORE OK" not in (r.stdout or ""):
            print("RESTORE FEHLGESCHLAGEN:\n", r.stdout[-1200:], r.stderr[-800:])
            _aufraeumen(client)
            return 1

        print("[3/4] Vergleiche Dokumentzahlen (Manifest vs. Testdatenbank) …")
        ziel = client[TEST_DB]
        erwartet = manifest.get("collections") or {}
        fehler = 0
        for c in sorted(erwartet):
            if c == "system_flags":
                continue  # Betriebs-Flags werden nie zurueckgespielt
            n_backup, n_ziel = int(erwartet[c]), ziel[c].count_documents({})
            status = "OK " if n_backup == n_ziel else "DIFF"
            if n_backup != n_ziel:
                fehler += 1
            print(f"      {status} {c:28} Backup={n_backup:>6}  "
                  f"Wiederhergestellt={n_ziel:>6}")
            if n_ziel and ziel[c].find_one() is None:
                print(f"      LESE-FEHLER in {c}")
                fehler += 1
        flag = ziel.system_flags.find_one({"_id": "wartungsmodus"})
        if flag and flag.get("aktiv"):
            print("      FEHLER: Wartungsmodus nach dem Restore noch aktiv")
            fehler += 1

        print(f"[4/4] Testdatenbank '{TEST_DB}' loeschen …")
        _aufraeumen(client)

        if fehler:
            print(f"\nERGEBNIS: {fehler} Abweichung(en) — Backup NICHT ok!")
            return 1
        if fehlend:
            print(f"\nERGEBNIS: Datenbank-Wiederherstellung bewiesen ({len(erwartet)} "
                  f"Collections), ABER das Backup ist UNVOLLSTAENDIG: "
                  + "; ".join(fehlend))
            return 2
        print(f"\nERGEBNIS: Wiederherstellung bewiesen — {len(erwartet)} "
              f"Collections identisch.")
        return 0


if __name__ == "__main__":
    sys.exit(main())
