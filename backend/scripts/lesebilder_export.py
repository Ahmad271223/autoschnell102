# -*- coding: utf-8 -*-
"""Lesebilder (nicht erkannte Anzeigen des Programms) als Dateien exportieren — Wunsch Ahmad 04.11.2026: "alle Fotos
von Inseraten, die nicht erkannt wurden, holen und die Erkennung verbessern".

Schreibt je Lesebild das PNG aus dem Datei-Speicher (Ersatz: die JPEG-Vorschau aus der Datenbank, wenn die Datei
fehlt) und eine Zeile Metadaten (Grund, fehlende Felder, Rohtext der Texterkennung, gelesene Fahrzeugwerte,
Kontonummer/Kundennummer, Zeit) nach lesebilder.jsonl, dazu uebersicht.txt mit Zaehlern. Nur lesen, nichts aendern.
Lesebilder leben LESEBILD_TAGE (30) Tage — aelter gibt es nichts mehr.

Seit 24.11.2026 (Wunsch Ahmad: "schau ALLE nicht erkannten Inserate an") zusaetzlich:
  vergleiche_problem.jsonl — Programm-Vergleiche ohne vollen Erfolg (Modell nicht gefunden, kein Inserat-Link,
                             Nummer unsicher, keine Vergleichslinks); nur die gelesenen Fahrzeugwerte, kein Bild
  leseseiten.jsonl         — Seiten, die die Erweiterung nicht lesen konnte: nur Kennzahlen (Adresse, Grund,
                             Groesse), nie die Seite selbst
Am bequemsten alles auf einmal vom PC aus: deploy/serverpruefung.ps1 (holt auch Systemcheck und Server-Zustand).

Aufruf auf dem Server (im Backend-Container, dort gelten MONGO_URL/DB_NAME und der Datei-Speicher):
    cd /opt/autoschnell
    docker compose cp backend/scripts/lesebilder_export.py backend:/app/scripts/lesebilder_export.py
    docker compose exec -T backend python scripts/lesebilder_export.py --ziel /tmp/lesebilder
    docker compose cp backend:/tmp/lesebilder ./lesebilder-export
    tar czf lesebilder-export.tgz lesebilder-export
Danach vom PC holen (PowerShell):
    scp root@2.28.66.8:/opt/autoschnell/lesebilder-export.tgz "$env:USERPROFILE\\Downloads\\"
(der Schluessel auto_spider_hetzner gehoert zu einem anderen Projekt und gilt hier nicht)
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


#: gelesene Fahrzeugwerte eines Programm-Vergleichs, die in den Export duerfen (die Beschreibung nie)
VERGLEICH_FELDER = ("marke", "modell", "marke_modell_text", "titel", "ez_monat", "ez_jahr", "kilometer", "kw", "ps",
                    "kraftstoff", "getriebe", "preis", "zustand", "kategorie", "quelle", "inserat_id", "hash_id",
                    "inserat_id_unsicher", "inserat_url", "inserat_nummer_unsicher", "erkennung", "modell_gefunden")
#: Suchfilter: Vergleiche, bei denen etwas fehlte (gleiche Bedingungen wie vergleich_probleme)
VERGLEICH_PROBLEM_FILTER = {"$or": [{"fahrzeug.modell_gefunden": False}, {"fahrzeug.inserat_url": None},
                                    {"fahrzeug.inserat_nummer_unsicher": True}, {"links": {"$size": 0}}]}


def vergleich_probleme(d: dict) -> list:
    """Was bei einem Programm-Vergleich nicht geklappt hat (leer = alles gut)."""
    fz = d.get("fahrzeug") or {}
    probleme = []
    if fz.get("modell_gefunden") is False:
        probleme.append("modell_nicht_gefunden")
    if not fz.get("inserat_url"):
        probleme.append("ohne_inserat_link")
    if fz.get("inserat_nummer_unsicher"):
        probleme.append("nummer_unsicher")
    if not d.get("links"):
        probleme.append("keine_vergleichslinks")
    return probleme


def link_grund(fz: dict) -> str:
    """Warum ein Vergleich keinen Inserat-Link bekam — grob, fuer die Uebersicht."""
    if fz.get("inserat_url"):
        return ""
    if fz.get("inserat_nummer_unsicher"):
        return "Nummer verworfen (anderes Auto/unsicher)"
    quelle = str(fz.get("quelle") or "").strip().lower()
    if not quelle:
        return "Portal nicht gelesen"
    if "scout" in quelle and not fz.get("hash_id"):
        return "AutoScout ohne vollstaendige Hash-ID"
    if not fz.get("inserat_id") and not fz.get("hash_id"):
        return "keine Inserat-Nummer gelesen"
    return "Nummer/Portal passt nicht zusammen"


def _prozent(teil: int, gesamt: int) -> str:
    return f"{teil} ({round(100 * teil / gesamt)} %)" if gesamt else str(teil)


async def main() -> int:
    p = argparse.ArgumentParser(description="Lesebilder exportieren — nur lesen")
    p.add_argument("--ziel", default="/tmp/lesebilder", help="Zielordner (wird angelegt)")
    p.add_argument("--tage", type=int, default=60, help="hoechstens so alt (Standard 60; Ablauf ist ohnehin 30 Tage)")
    p.add_argument("--limit", type=int, default=1000)
    p.add_argument("--nur-meta", action="store_true", help="keine Bilddateien, nur lesebilder.jsonl")
    p.add_argument("--limit-vergleiche", type=int, default=5000,
                   help="hoechstens so viele Programm-Vergleiche mit Problem (Standard 5000)")
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

    # --- Programm-Vergleiche, bei denen etwas fehlte (Wunsch Ahmad 24.11.2026) — nur Text, kein Bild
    v_filt = {"erstellt_am": {"$gte": seit}, "probelauf": {"$ne": True}}
    v_gesamt = await db[wz.SAMMLUNG_VERGLEICHE].count_documents(v_filt)
    probleme_z, modell_texte, ohne_link_z, ohne_link_quelle, v_konto = (Counter() for _ in range(5))
    n_v = 0
    with (ziel / "vergleiche_problem.jsonl").open("w", encoding="utf-8") as aus:
        cursor = db[wz.SAMMLUNG_VERGLEICHE].find(
            {**v_filt, **VERGLEICH_PROBLEM_FILTER},
            {"_id": 0, "erstellt_am": 1, "user_id": 1, "dealer_id": 1, "fahrzeug": 1, "links": 1, "hinweise": 1,
             "vorab": 1, "ueber_helfer": 1}).sort("erstellt_am", -1).limit(args.limit_vergleiche)
        async for d in cursor:
            probleme = vergleich_probleme(d)
            if not probleme:
                continue
            n_v += 1
            fz = d.get("fahrzeug") or {}
            konto = konten.get(d.get("user_id"), "?")
            zeile = {"nr": n_v, "erstellt_am": d.get("erstellt_am"), "probleme": probleme,
                     "link_grund": link_grund(fz), "kontonummer": konto,
                     "kunden_nr": firmen.get(d.get("dealer_id"), "?"),
                     "fahrzeug": {k: fz.get(k) for k in VERGLEICH_FELDER if fz.get(k) not in (None, "")},
                     "links": len(d.get("links") or []), "vorab": d.get("vorab"), "ueber_helfer": d.get("ueber_helfer"),
                     "hinweise": [str(h)[:240] for h in (d.get("hinweise") or [])[:8]]}
            aus.write(json.dumps(zeile, ensure_ascii=False, default=str) + "\n")
            for pr in probleme:
                probleme_z[pr] += 1
            if "modell_nicht_gefunden" in probleme:
                modell_texte[f"{fz.get('marke_modell_text') or '?'} | {str(fz.get('titel') or '')[:60]}"] += 1
            if "ohne_inserat_link" in probleme:
                ohne_link_z[zeile["link_grund"]] += 1
                ohne_link_quelle[str(fz.get("quelle") or "?")] += 1
            v_konto[konto] += 1
    text += ["",
             f"Programm-Vergleiche im Zeitraum: {v_gesamt}, davon mit Problem: {_prozent(n_v, v_gesamt)}",
             "  je Problem: " + ", ".join(f"{k} {_prozent(v, v_gesamt)}" for k, v in probleme_z.most_common()),
             "  ohne Inserat-Link, warum: " + ", ".join(f"{k} {v}" for k, v in ohne_link_z.most_common()),
             "  ohne Inserat-Link je Portal (vom Bildschirm): "
             + ", ".join(f"{k} {v}" for k, v in ohne_link_quelle.most_common(10)),
             "  Modell nicht gefunden — gelesener Text | Titel: "
             + "; ".join(f"{k} ({v})" for k, v in modell_texte.most_common(25)),
             "  je Konto: " + ", ".join(f"{k} {v}" for k, v in v_konto.most_common(20))]

    # --- Seiten, die die Erweiterung nicht lesen konnte — nur Kennzahlen, nie die Seite (Personendaten)
    s_gruende, s_portale = Counter(), Counter()
    n_s = 0
    with (ziel / "leseseiten.jsonl").open("w", encoding="utf-8") as aus:
        cursor = db[wz.SAMMLUNG_LESESEITEN].find(
            {"erstellt_am": {"$gte": seit}},
            {"_id": 0, "id": 1, "erstellt_am": 1, "portal": 1, "url": 1, "item_id": 1, "grund": 1, "groesse": 1,
             "user_id": 1, "dealer_id": 1}).sort("erstellt_am", -1).limit(args.limit)
        async for s in cursor:
            n_s += 1
            aus.write(json.dumps({
                "nr": n_s, "id": s.get("id"), "erstellt_am": s.get("erstellt_am"), "portal": s.get("portal"),
                "url": s.get("url"), "item_id": s.get("item_id"), "grund": s.get("grund"),
                "groesse": s.get("groesse"), "kontonummer": konten.get(s.get("user_id"), "?"),
                "kunden_nr": firmen.get(s.get("dealer_id"), "?")}, ensure_ascii=False, default=str) + "\n")
            s_gruende[str(s.get("grund") or "?")[:60]] += 1
            s_portale[str(s.get("portal") or "?")] += 1
    text += ["",
             f"Leseseiten (Erweiterung konnte die Seite nicht lesen): {n_s}",
             "  je Grund: " + ", ".join(f"{k} {v}" for k, v in s_gruende.most_common()),
             "  je Portal: " + ", ".join(f"{k} {v}" for k, v in s_portale.most_common())]
    (ziel / "uebersicht.txt").write_text("\n".join(text) + "\n", encoding="utf-8")
    print("\n".join(text))
    print(f"Dateien in {ziel}")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
