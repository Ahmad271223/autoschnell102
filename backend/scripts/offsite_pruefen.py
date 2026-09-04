# -*- coding: utf-8 -*-
"""Offsite-Backup-Ziel pruefen (Go-Live-Audit 09/2026, Punkt 21).

Prueft mit den BACKUP_S3_*/S3_*-Werten aus der Umgebung:
  - Bucket erreichbar, Schreiben/Lesen/Loeschen eines Testobjekts
  - Verschluesselung (SSE) wird akzeptiert
  - Object Lock (Unveraenderbarkeit) am Bucket aktiv, wenn
    BACKUP_S3_OBJECT_LOCK_DAYS gesetzt ist
  - vorhandene Backups im Prefix (Anzahl, juengstes Datum)
  - optional: neuestes Backup herunterladen und Pruefsumme vergleichen (--laden)

Aufruf (im Container):  python scripts/offsite_pruefen.py [--laden]
Exit 0 = ok, 1 = Fehler.
"""
import argparse
import hashlib
import io
import json
import os
import sys
from pathlib import Path
import tarfile
import tempfile
from datetime import datetime, timezone


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--laden", action="store_true", help="juengstes Backup herunterladen und pruefen")
    args = ap.parse_args()
    bucket = os.environ.get("BACKUP_S3_BUCKET", "").strip()
    prefix = os.environ.get("BACKUP_S3_PREFIX", "autoschnell-backups/").strip()
    if not bucket:
        print("FEHLER: BACKUP_S3_BUCKET ist nicht gesetzt — kein Offsite-Backup konfiguriert.")
        return 1
    try:
        import boto3
        from botocore.exceptions import ClientError
    except ImportError:
        print("FEHLER: boto3 fehlt"); return 1
    kw = {}
    if os.environ.get("S3_ENDPOINT"):
        kw["endpoint_url"] = os.environ["S3_ENDPOINT"]
    if os.environ.get("S3_REGION"):
        kw["region_name"] = os.environ["S3_REGION"]
    s3 = boto3.client("s3", aws_access_key_id=os.environ.get("S3_ACCESS_KEY"),
                      aws_secret_access_key=os.environ.get("S3_SECRET_KEY"), **kw)
    fehler = 0
    # 1) Erreichbarkeit
    try:
        s3.head_bucket(Bucket=bucket)
        print(f"OK   Bucket '{bucket}' erreichbar")
    except ClientError as exc:
        print(f"FEHLER Bucket nicht erreichbar: {exc}"); return 1
    # 2) Schreiben/Lesen/Loeschen mit SSE
    key = f"{prefix.rstrip('/')}/probe-{datetime.now(timezone.utc):%Y%m%d%H%M%S}.txt"
    inhalt = b"autoschnell offsite-probe"
    try:
        sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
        from s3_kompatibel import sse_optionen
        sse = sse_optionen(os.environ.get("S3_ENDPOINT", ""))
        s3.put_object(Bucket=bucket, Key=key, Body=inhalt, **sse)
        zurueck = s3.get_object(Bucket=bucket, Key=key)["Body"].read()
        assert zurueck == inhalt
        print("OK   Schreiben/Lesen" + (" mit Verschluesselung (AES256)" if sse
              else " (Anbieter verschluesselt selbst, keine Kopfzeile noetig)"))
    except Exception as exc:  # noqa: BLE001
        print(f"FEHLER Schreib-/Lesetest: {exc}"); fehler += 1
    try:
        s3.delete_object(Bucket=bucket, Key=key)
        print("OK   Testobjekt geloescht")
    except ClientError as exc:
        # Bei Object Lock kann Loeschen verweigert werden — dann ist das gewollt.
        print(f"HINWEIS Testobjekt konnte nicht geloescht werden (Object Lock?): {exc.response.get('Error', {}).get('Code')}")
    # 3) Object Lock
    tage = os.environ.get("BACKUP_S3_OBJECT_LOCK_DAYS", "").strip()
    if tage:
        try:
            cfg = s3.get_object_lock_configuration(Bucket=bucket)
            aktiv = cfg.get("ObjectLockConfiguration", {}).get("ObjectLockEnabled") == "Enabled"
            print(("OK   " if aktiv else "FEHLER ") + f"Object Lock am Bucket: {'aktiv' if aktiv else 'NICHT aktiv'} (BACKUP_S3_OBJECT_LOCK_DAYS={tage})")
            fehler += 0 if aktiv else 1
        except ClientError as exc:
            print(f"FEHLER Object Lock nicht abfragbar/aktiv: {exc.response.get('Error', {}).get('Code')}"); fehler += 1
    else:
        print("WARN  BACKUP_S3_OBJECT_LOCK_DAYS nicht gesetzt — Backups sind nicht unveraenderbar (Ransomware-Schutz fehlt)")
    # 4) Vorhandene Backups
    objekte = []
    token = None
    while True:
        r = s3.list_objects_v2(Bucket=bucket, Prefix=prefix, **({"ContinuationToken": token} if token else {}))
        objekte += [o for o in r.get("Contents", []) if o["Key"].endswith(".tar.gz")]
        token = r.get("NextContinuationToken")
        if not token:
            break
    if not objekte:
        print("WARN  Noch keine Backups im Prefix — nach dem ersten naechtlichen Lauf erneut pruefen")
    else:
        neu = max(objekte, key=lambda o: o["LastModified"])
        alter = (datetime.now(timezone.utc) - neu["LastModified"]).total_seconds() / 3600
        print(f"OK   {len(objekte)} Backups, juengstes {neu['Key']} ({neu['Size'] / 1e6:.1f} MB, vor {alter:.1f} h)")
        if alter > 26:
            print("WARN  Juengstes Offsite-Backup ist aelter als 26 Stunden");
        if args.laden:
            with tempfile.TemporaryDirectory() as tmp:
                ziel = os.path.join(tmp, "backup.tar.gz")
                s3.download_file(bucket, neu["Key"], ziel)
                sha = hashlib.sha256(open(ziel, "rb").read()).hexdigest()
                meta = s3.head_object(Bucket=bucket, Key=neu["Key"]).get("Metadata", {})
                erwartet = meta.get("sha256")
                if erwartet and erwartet != sha:
                    print(f"FEHLER Pruefsumme weicht ab ({sha[:12]} != {erwartet[:12]})"); fehler += 1
                else:
                    print(f"OK   Heruntergeladen, Pruefsumme {sha[:12]}{' bestaetigt' if erwartet else ' (keine Referenz im Objekt)'}")
                try:
                    with tarfile.open(ziel) as t:
                        namen = t.getnames()
                        manifest = [n for n in namen if n.endswith("manifest.json")]
                        if manifest:
                            m = json.load(io.TextIOWrapper(t.extractfile(manifest[0]), encoding="utf-8"))
                            print(f"OK   Manifest: {sum(m.get('collections', {}).values())} Dokumente, unvollstaendig={m.get('unvollstaendig') or 'nein'}")
                        else:
                            print("FEHLER Kein manifest.json im Archiv"); fehler += 1
                except Exception as exc:  # noqa: BLE001
                    print(f"FEHLER Archiv nicht lesbar: {exc}"); fehler += 1
    print("\nERGEBNIS:", "OK" if not fehler else f"{fehler} Fehler")
    return 1 if fehler else 0


if __name__ == "__main__":
    sys.exit(main())
