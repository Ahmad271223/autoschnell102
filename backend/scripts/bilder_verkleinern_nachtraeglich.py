# -*- coding: utf-8 -*-
"""Alte Fotos nachtraeglich verkleinern (Speicher-Audit 09/2026).

Seit dem 05.09.2026 werden Fotos schon beim Hochladen verkleinert. Die
Bilder, die VORHER hereinkamen, liegen weiter in voller Groesse im
Speicher — bei AutoSchnell waren das ueber 11 GB. Dieses Skript geht
einmal durch den Speicher und ersetzt die alten Fotos durch verkleinerte.

WICHTIG: Ohne --wirklich aendert das Skript NICHTS. Es rechnet nur vor,
wie viel Platz zu holen waere. Vor dem echten Lauf bitte eine Sicherung
ziehen (scripts/backup_mongo.py spiegelt auch den Datei-Speicher).

Aufruf (im Container, aus dem Ordner backend):

    python scripts/bilder_verkleinern_nachtraeglich.py
    python scripts/bilder_verkleinern_nachtraeglich.py --wirklich

Nuetzliche Schalter:
    --grenze 200      nur die ersten 200 Dateien ansehen (Probelauf)
    --praefix resale  nur einen Bereich (mehrfach angebbar)
    --leise           keine Zeile je Datei, nur das Ergebnis

Angefasst werden nur Bilder (jpg, jpeg, png, webp) in den Bereichen
resale/, pickup/ und logo/. Unterschriften unter protocol/ bleiben
unberuehrt: Strichzeichnungen werden durch erneutes Umwandeln schlechter,
und sie sind ohnehin klein. PDFs und Videos werden nie angefasst.

Exit 0 = fertig (auch beim Probelauf)
Exit 1 = abgebrochen, z.B. Speicher nicht erreichbar
"""
import argparse
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

BILD_ENDUNGEN = (".jpg", ".jpeg", ".png", ".webp")
STANDARD_PRAEFIXE = ("resale/", "pickup/", "logo/")


def menschlich(bytes_: int) -> str:
    """1234567 -> '1,2 MB' (deutsche Schreibweise)."""
    for einheit, teiler in (("GB", 1024 ** 3), ("MB", 1024 ** 2), ("KB", 1024)):
        if abs(bytes_) >= teiler:
            return f"{bytes_ / teiler:.1f} {einheit}".replace(".", ",")
    return f"{bytes_} B"


def schluessel_auflisten(speicher, praefixe):
    """Liefert (key, groesse) fuer jedes Bild in den Praefixen."""
    if speicher.name == "s3":
        for praefix in praefixe:
            weiter = {}
            while True:
                seite = speicher.client.list_objects_v2(
                    Bucket=speicher.bucket, Prefix=praefix, **weiter)
                for obj in seite.get("Contents", []) or []:
                    key = obj["Key"]
                    if key.lower().endswith(BILD_ENDUNGEN):
                        yield key, int(obj.get("Size", 0))
                if not seite.get("IsTruncated"):
                    break
                weiter = {"ContinuationToken": seite["NextContinuationToken"]}
    else:
        wurzel = Path(speicher.root)
        for praefix in praefixe:
            ordner = wurzel / praefix.rstrip("/")
            if not ordner.is_dir():
                continue
            for pfad in sorted(ordner.rglob("*")):
                if pfad.is_file() and pfad.suffix.lower() in BILD_ENDUNGEN:
                    yield (pfad.relative_to(wurzel).as_posix(),
                           pfad.stat().st_size)


def main() -> int:
    ap = argparse.ArgumentParser(
        description="Alte Fotos im Speicher nachtraeglich verkleinern")
    ap.add_argument("--wirklich", action="store_true",
                    help="Dateien wirklich ersetzen (ohne diesen Schalter "
                         "wird nur gerechnet)")
    ap.add_argument("--grenze", type=int, default=0,
                    help="hoechstens so viele Dateien ansehen (0 = alle)")
    ap.add_argument("--praefix", action="append", default=None,
                    help="Bereich einschraenken, z.B. resale (mehrfach moeglich)")
    ap.add_argument("--leise", action="store_true",
                    help="keine Zeile je Datei ausgeben")
    args = ap.parse_args()

    try:
        from dotenv import load_dotenv
        load_dotenv(Path(__file__).resolve().parents[1] / ".env")
    except ImportError:
        pass

    try:
        from storage_service import storage, bild_verkleinern, StorageError
    except Exception as exc:                        # noqa: BLE001
        print(f"FEHLER: Datei-Speicher nicht nutzbar: {exc}")
        return 1

    praefixe = [p if p.endswith("/") else p + "/"
                for p in (args.praefix or STANDARD_PRAEFIXE)]
    print(f"Speicher: {storage.name}   Bereiche: {', '.join(praefixe)}")
    if not args.wirklich:
        print("PROBELAUF — es wird nichts geaendert. Mit --wirklich ersetzen.")
    else:
        print("ECHTER LAUF — Dateien werden ersetzt.")
    print()

    gesehen = kleiner = uebersprungen = fehler = 0
    vorher = nachher = 0
    groesste = []

    try:
        for key, groesse in schluessel_auflisten(storage, praefixe):
            if args.grenze and gesehen >= args.grenze:
                break
            gesehen += 1
            try:
                roh = storage.load(key)
            except Exception as exc:                # noqa: BLE001
                fehler += 1
                print(f"  FEHLER laden {key}: {exc}")
                continue
            ziel = "PNG" if key.lower().endswith(".png") else "JPEG"
            try:
                neu = bild_verkleinern(roh, wo=key, ziel_format=ziel)
            except StorageError as exc:
                # Bildbombe o.ae. — nicht anfassen, aber melden.
                fehler += 1
                print(f"  UEBERSPRUNGEN {key}: {exc}")
                continue
            except Exception as exc:                # noqa: BLE001
                fehler += 1
                print(f"  FEHLER umwandeln {key}: {exc}")
                continue

            vorher += len(roh)
            if len(neu) >= len(roh):
                uebersprungen += 1
                nachher += len(roh)
                continue
            kleiner += 1
            nachher += len(neu)
            gespart = len(roh) - len(neu)
            groesste.append((gespart, key))
            if not args.leise:
                print(f"  {key}: {menschlich(len(roh))} -> {menschlich(len(neu))}")
            if args.wirklich:
                try:
                    storage.save(key, neu)
                except Exception as exc:            # noqa: BLE001
                    fehler += 1
                    print(f"  FEHLER schreiben {key}: {exc}")
    except KeyboardInterrupt:
        print("\nAbgebrochen. Bereits ersetzte Dateien bleiben ersetzt.")
    except Exception as exc:                        # noqa: BLE001
        print(f"FEHLER beim Durchgehen des Speichers: {exc}")
        return 1

    print()
    print(f"Angesehen        : {gesehen} Bilder")
    print(f"Verkleinerbar    : {kleiner}")
    print(f"Schon klein genug: {uebersprungen}")
    if fehler:
        print(f"Fehler           : {fehler}")
    print(f"Vorher           : {menschlich(vorher)}")
    print(f"Nachher          : {menschlich(nachher)}")
    if vorher:
        print(f"Ersparnis        : {menschlich(vorher - nachher)} "
              f"({(vorher - nachher) * 100 // vorher} Prozent)")
    if groesste and not args.leise:
        print("\nGroesste Einsparungen:")
        for gespart, key in sorted(groesste, reverse=True)[:10]:
            print(f"  {menschlich(gespart):>10}  {key}")
    if not args.wirklich and kleiner:
        print("\nDas war ein Probelauf. Zum wirklichen Ersetzen:")
        print("  1. Sicherung ziehen: python scripts/backup_mongo.py")
        print("  2. python scripts/bilder_verkleinern_nachtraeglich.py --wirklich")
    return 0


if __name__ == "__main__":
    sys.exit(main())
