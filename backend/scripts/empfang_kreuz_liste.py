# -*- coding: utf-8 -*-
"""Liste der Vertraege, deren aktuelles PDF noch das automatische
Schluessel-Kreuz traegt (Nachtrag 28.09.2026, empfang3). NUR LESEND.

    python -X utf8 scripts/empfang_kreuz_liste.py                 # Standard
    python -X utf8 scripts/empfang_kreuz_liste.py --mit-uebergabe # auch nach Uebergabe
    python -X utf8 scripts/empfang_kreuz_liste.py --firma <dealer_id>
    python -X utf8 scripts/empfang_kreuz_liste.py --json          # maschinenlesbar

Hintergrund: Vom 24.09.2026 (00:00 Uhr deutscher Zeit) bis zur Korrektur
stand im gedruckten Vertrag das Kaestchen "KFZ mit n Schluessel(n)" schon
angekreuzt, bevor irgendetwas uebergeben war (Schluesselanzahl eingetippt
oder aus dem Inserat uebernommen). Migration 21 leert contract_data, laesst
das gespeicherte PDF aber bewusst unveraendert (Archiv = was verschickt
wurde). Dieses Skript zeigt, welche Vertraege das betrifft, damit der
Betreiber je Vertrag entscheiden kann, ob eine neue Fassung erzeugt wird.

Geprueft wird die AKTUELLE Druckfassung (generated_pdfs.pdf_b64): steht im
Empfangs-Kasten des Kaeufers vor "KFZ mit ... Schluessel(n)" ein Haken?
Die digitale Ausfertigung (pdf_digital_b64) hat keinen Empfangs-Kasten.
Standardmaessig nur Vertraege, deren Uebergabe noch NICHT stattgefunden hat
(routes.contracts.uebergabe_erfolgt, mit Fahrzeug-Nachweis); mit
--mit-uebergabe auch die uebrigen (Spalte "Uebergabe").

Das Skript schreibt NICHTS: keine neue Fassung, kein Vermerk, kein Log.
Umgebung: MONGO_URL, DB_NAME (wie der Server).
"""
from __future__ import annotations

import argparse
import asyncio
import base64
import io
import json
import os
import re
import sys
from pathlib import Path
from typing import Optional

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from migrationen import EMPFANG_AUTOMATIK_AB  # noqa: E402

MONGO_URL = os.environ.get("MONGO_URL", "mongodb://127.0.0.1:27017")
DB_NAME = os.environ.get("DB_NAME", "autoschnell")

#: Beschriftung neben dem Kaestchen (pdf_service._empfang_block)
_SCHLUESSEL_ZEILE = re.compile(r"KFZ\s*mit.*?Schl(?:ü|ue|u)ssel", re.IGNORECASE | re.DOTALL)
#: das Kaestchen selbst ist ein kleines Quadrat (pdf_service._Kaestchen, 8 pt)
_KAESTCHEN_MAX = 14.0

_PROJEKTION = {"_id": 0, "id": 1, "dealer_id": 1, "vehicle_id": 1, "version": 1,
               "status": 1, "created_at": 1, "seller_name": 1, "kaufvorgang_id": 1,
               "contract_data.seller_name": 1, "pdf_b64": 1,
               "empfang_kaestchen_geleert": 1, "nach_abholung_protokoll_id": 1,
               "vertrag_vor_abholung": 1}


def schluessel_kreuz_im_pdf(pdf: bytes) -> Optional[bool]:
    """True: vor "KFZ mit ... Schluessel(n)" steht ein Haken. False: das
    Kaestchen ist leer. None: kein Schluessel-Kaestchen im Dokument
    (Empfangsbestaetigung nicht gedruckt) oder PDF nicht lesbar.

    pdf_service zeichnet je Zeile zuerst das Quadrat (re), bei "an" direkt
    danach den Haken als Pfad (m, l, l, S) und dann die Beschriftung. Die
    Inhaltsbefehle werden in dieser Reihenfolge durchlaufen: jedes kleine
    Quadrat beginnt ein neues Kaestchen, ein gestrichener Linienzug danach
    ist sein Haken, die folgende Beschriftung sagt, welches Kaestchen es war."""
    try:
        from pypdf import PdfReader
        reader = PdfReader(io.BytesIO(pdf))
        seiten = list(reader.pages)
    except Exception:  # noqa: BLE001 — unlesbar: nicht raten
        return None
    zustand = {"kaestchen": False, "haken": False, "punkte": 0, "text": ""}
    ergebnis: list = []

    def vor_befehl(op, args, cm, tm):
        if op == b"re" and len(args) == 4:
            try:
                w, h = abs(float(args[2])), abs(float(args[3]))
            except (TypeError, ValueError):
                return
            if 0 < w <= _KAESTCHEN_MAX and abs(w - h) < 0.5:
                zustand.update(kaestchen=True, haken=False, punkte=0, text="")
        elif not zustand["kaestchen"]:
            return
        elif op == b"m":
            zustand["punkte"] = 1
        elif op == b"l" and zustand["punkte"]:
            zustand["punkte"] += 1
        elif op in (b"S", b"s") and zustand["punkte"] >= 3:
            zustand["haken"] = True

    def bei_text(text, cm, tm, font, groesse):
        if not zustand["kaestchen"] or not text:
            return
        zustand["text"] += text
        if _SCHLUESSEL_ZEILE.search(zustand["text"]):
            ergebnis.append(zustand["haken"])
            zustand.update(kaestchen=False, haken=False, punkte=0, text="")

    try:
        for seite in seiten:
            seite.extract_text(visitor_operand_before=vor_befehl, visitor_text=bei_text)
    except Exception:  # noqa: BLE001
        return None
    if not ergebnis:
        return None
    return any(ergebnis)


def verkaeufer_kurz(name) -> str:
    """'Vera Verkauf' -> 'Vera V.' (Vorname + Initialen, nicht mehr)."""
    teile = str(name or "").split()
    if not teile:
        return "—"
    erster = teile[0][:14]
    rest = " ".join(f"{t[0]}." for t in teile[1:] if t)
    return f"{erster} {rest}".strip()


async def liste(datenbank, *, ab: str = EMPFANG_AUTOMATIK_AB, mit_uebergabe: bool = False,
                dealer_id: Optional[str] = None) -> dict:
    """Liest nur. Liefert Zaehler und die betroffenen Vertraege (aelteste zuerst)."""
    from routes.contracts import uebergabe_erfolgt
    filt: dict = {"created_at": {"$gte": ab}}
    if dealer_id:
        filt["dealer_id"] = dealer_id
    z = {"ab": ab, "geprueft": 0, "ohne_pdf": 0, "ohne_kaestchen": 0,
         "mit_kreuz": 0, "nach_uebergabe": 0, "vertraege": []}
    async for doc in datenbank.generated_pdfs.find(filt, _PROJEKTION).sort(
            "created_at", 1).batch_size(20):
        z["geprueft"] += 1
        roh = doc.pop("pdf_b64", None)
        if not roh:
            z["ohne_pdf"] += 1
            continue
        try:
            pdf = base64.b64decode(roh)
        except Exception:  # noqa: BLE001
            pdf = b""
        kreuz = schluessel_kreuz_im_pdf(pdf) if pdf else None
        if kreuz is None:
            z["ohne_kaestchen"] += 1
            continue
        if not kreuz:
            continue
        z["mit_kreuz"] += 1
        uebergeben = await uebergabe_erfolgt(datenbank, doc)
        if uebergeben:
            z["nach_uebergabe"] += 1
            if not mit_uebergabe:
                continue
        kv = await datenbank.kaufvorgaenge.find_one(
            {"$or": [{"id": doc.get("kaufvorgang_id") or "-"}, {"contract_id": doc["id"]}],
             "dealer_id": doc.get("dealer_id")},
            {"_id": 0, "status": 1})
        vorgang = (kv or {}).get("status")
        vermerk = doc.get("empfang_kaestchen_geleert") or {}
        z["vertraege"].append({
            "id": doc["id"],
            "dealer_id": doc.get("dealer_id"),
            "verkaeufer": verkaeufer_kurz(doc.get("seller_name")
                                          or (doc.get("contract_data") or {}).get("seller_name")),
            "created_at": doc.get("created_at"),
            "status": doc.get("status") or "—",
            "vorgang": vorgang or "—",
            "fassung": doc.get("version") or 1,
            "daten_geleert": vermerk.get("quelle") if isinstance(vermerk, dict) else None,
            "uebergabe": bool(uebergeben),
        })
    return z


def ausgeben(e: dict, *, mit_uebergabe: bool = False) -> None:
    print(f"Datenbank: {DB_NAME}   Vertraege angelegt ab {e['ab']} (UTC)")
    print(f"Geprueft: {e['geprueft']}   ohne PDF: {e['ohne_pdf']}   "
          f"ohne Schluessel-Kaestchen: {e['ohne_kaestchen']}")
    print(f"Aktuelles PDF mit Schluessel-Kreuz: {e['mit_kreuz']}   "
          f"davon Uebergabe schon erfolgt: {e['nach_uebergabe']}")
    zeilen = e["vertraege"]
    if not zeilen:
        print("\nKeine Vertraege zu entscheiden.")
        return
    print("\nVertrag-ID                            Firma (dealer_id)                     "
          "Verkaeufer        angelegt (UTC)     Status / Vorgang          Fassung  Daten"
          + ("  Uebergabe" if mit_uebergabe else ""))
    for r in zeilen:
        status = f"{r['status']} / {r['vorgang']}"
        daten = f"geleert ({r['daten_geleert']})" if r["daten_geleert"] else "Kreuz"
        print(f"{r['id']:<37} {str(r['dealer_id'] or '—'):<37} {r['verkaeufer']:<17} "
              f"{str(r['created_at'] or '')[:16]:<18} {status:<25} {r['fassung']:>7}  {daten}"
              + (f"  {'ja' if r['uebergabe'] else 'nein'}" if mit_uebergabe else ""))
    print(f"\n{len(zeilen)} Vertraege. Nichts wurde geaendert. Neue Fassung je Vertrag "
          "nur nach eigener Entscheidung (z. B. Termin im Terminplaner neu speichern).")


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    p.add_argument("--ab", default=EMPFANG_AUTOMATIK_AB,
                   help="created_at ab (UTC, ISO), Standard 24.09.2026 00:00 Uhr deutscher Zeit")
    p.add_argument("--mit-uebergabe", action="store_true",
                   help="auch Vertraege nach der Uebergabe zeigen")
    p.add_argument("--firma", default=None, help="nur diese dealer_id")
    p.add_argument("--json", action="store_true", help="Ausgabe als JSON")
    a = p.parse_args(argv)

    async def _lauf():
        from motor.motor_asyncio import AsyncIOMotorClient
        client = AsyncIOMotorClient(MONGO_URL, serverSelectionTimeoutMS=10000)
        try:
            return await liste(client[DB_NAME], ab=a.ab, mit_uebergabe=a.mit_uebergabe,
                               dealer_id=a.firma)
        finally:
            client.close()

    e = asyncio.run(_lauf())
    if a.json:
        print(json.dumps(e, ensure_ascii=False, indent=2))
    else:
        ausgeben(e, mit_uebergabe=a.mit_uebergabe)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
