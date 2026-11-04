# -*- coding: utf-8 -*-
"""Lesebilder (nicht erkannte Anzeigen des Programms) als Dateien exportieren — Wunsch Ahmad 04.11.2026: "alle Fotos
von Inseraten, die nicht erkannt wurden, holen und die Erkennung verbessern".

Schreibt je Lesebild das PNG aus dem Datei-Speicher (Ersatz: die JPEG-Vorschau aus der Datenbank, wenn die Datei
fehlt) und eine Zeile Metadaten (Grund, fehlende Felder, Rohtext der Texterkennung, gelesene Fahrzeugwerte,
Kontonummer/Kundennummer, Zeit) nach lesebilder.jsonl, dazu uebersicht.txt mit Zaehlern. Nur lesen, nichts aendern.
Lesebilder leben LESEBILD_TAGE (30) Tage — aelter gibt es nichts mehr.

Aufruf auf dem Server (im Backend-Container, dort gelten MONGO_URL/DB_NAME und der Datei-Speicher):
    cd /opt/autoschnell
    docker compose cp backend/scripts/lesebilder_export.py backend:/app/scripts/lesebilder_export.py
    docker compose exec -T backend python scripts/lesebilder_export.py --ziel /tmp/lesebilder
    docker compose cp backend:/tmp/lesebilder ./lesebilder-export
    tar czf lesebilder-export.tgz lesebilder-export
Danach vom PC holen (PowerShell):
    scp -i "$env:USERPROFILE\\.ssh\\auto_spider_hetzner" root@167.233.243.23:/opt/autoschnell/lesebilder-export.tgz "$env:USERPROFILE\\Downloads\\"
"""
import argparse
import asyncio
import base64
import json
import os
import re
import sys
from collections import Counter
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


def _sicher(text: str, n: int = 40) -> str:
    return re.sub(r"[^A-Za-z0-9_.-]+", "_", str(text or ""))[:n].strip("_") or "x"


async def main() -> int:
    p = argparse.ArgumentParser(description="Lesebilder exportieren — nur lesen")
    p.add_argument("--ziel", default="/tmp/lesebilder", help="Zielordner (wird angelegt)")
    p.add_argument("--tage", type=int, default=60, help="hoechstens so alt (Standard 60; Ablauf ist ohnehin 30 Tage)")
    p.add_argument("--limit", type=int, default=1000)
    p.add_argument("--nur-meta", action="store_true", help="keine Bilddateien, nur lesebilder.jsonl")
    args = p.parse_args()
    from deps import db
    from storage_service import load_async
    import werkzeuge as wz

    ziel = Path(args.ziel)
    ziel.mkdir(parents=True, exist_ok=True)
    seit = (datetime.now(timezone.utc) - timedelta(days=args.tage)).isoformat()
    konten, firmen = {}, {}
    async for u in db.users.find({}, {"_id": 0, "id": 1, "kontonummer": 1, "role": 1}):
        konten[u["id"]] = str(u.get("kontonummer") or "?")
    async for d in db.dealers.find({}, {"_id": 0, "id": 1, "kunden_nr": 1}):
        firmen[d["id"]] = str(d.get("kunden_nr") or "?")

    gruende, fehlt_zaehler, je_konto, quellen = Counter(), Counter(), Counter(), Counter()
    n = n_png = n_vorschau = n_ohne = 0
    with (ziel / "lesebilder.jsonl").open("w", encoding="utf-8") as aus:
        cursor = db[wz.SAMMLUNG_LESEBILDER].find({"erstellt_am": {"$gte": seit}}, {"_id": 0}) \
            .sort("erstellt_am", -1).limit(args.limit)
        async for d in cursor:
            n += 1
            fz = d.get("fahrzeug") or {}
            name = f"{n:04d}_{_sicher(d.get('grund'))}_{_sicher(fz.get('quelle'), 12)}_{str(d.get('id'))[:8]}"
            datei = ""
            if not args.nur_meta:
                roh = None
                try:
                    if d.get("key"):
                        roh = await load_async(d["key"])
                except Exception as exc:  # noqa: BLE001
                    print(f"  {name}: Datei nicht ladbar ({str(exc)[:80]})", file=sys.stderr)
                if roh:
                    datei = name + ".png"
                    (ziel / datei).write_bytes(roh)
                    n_png += 1
                elif d.get("vorschau_b64"):
                    try:
                        datei = name + "_vorschau.jpg"
                        (ziel / datei).write_bytes(base64.b64decode(d["vorschau_b64"]))
                        n_vorschau += 1
                    except Exception:  # noqa: BLE001
                        datei = ""
                if not datei:
                    n_ohne += 1
            zeile = {
                "nr": n, "datei": datei, "id": d.get("id"), "erstellt_am": d.get("erstellt_am"),
                "grund": d.get("grund"), "fehlt": d.get("fehlt") or [], "rohtext": d.get("rohtext") or "",
                "fahrzeug": fz, "vorgang_id": d.get("vorgang_id"),
                "kontonummer": konten.get(d.get("user_id"), "?"), "kunden_nr": firmen.get(d.get("dealer_id"), "?"),
                "pc_name": d.get("pc_name") or "", "groesse": d.get("groesse"),
            }
            aus.write(json.dumps(zeile, ensure_ascii=False) + "\n")
            gruende[d.get("grund") or "?"] += 1
            for f in d.get("fehlt") or []:
                fehlt_zaehler[f] += 1
            je_konto[zeile["kontonummer"]] += 1
            quellen[fz.get("quelle") or "?"] += 1

    text = [f"Lesebilder-Export {datetime.now(timezone.utc).strftime('%d.%m.%Y %H:%M')} UTC — Datenbank "
            f"{os.environ.get('DB_NAME') or '?'} — letzte {args.tage} Tage",
            f"Eintraege: {n}  (PNG aus dem Speicher: {n_png}, nur Vorschau: {n_vorschau}, ohne Bild: {n_ohne})",
            "je Grund: " + ", ".join(f"{k} {v}" for k, v in gruende.most_common()),
            "fehlende Felder: " + ", ".join(f"{k} {v}" for k, v in fehlt_zaehler.most_common(15)),
            "je Quelle: " + ", ".join(f"{k} {v}" for k, v in quellen.most_common()),
            "je Konto: " + ", ".join(f"{k} {v}" for k, v in je_konto.most_common(20))]
    (ziel / "uebersicht.txt").write_text("\n".join(text) + "\n", encoding="utf-8")
    print("\n".join(text))
    print(f"Dateien in {ziel}")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
