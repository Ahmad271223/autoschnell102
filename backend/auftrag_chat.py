# -*- coding: utf-8 -*-
"""Chat zwischen Buero und Fahrer je Abholauftrag (Wunsch Ahmad 24.11.2026).

Buero (Chef der Firma bzw. Sucher mit Zugriff auf den Termin) und der zugeteilte Fahrer schreiben sich
direkt in der App statt per WhatsApp; der Verlauf gehoert zum Termin. Die Routen stehen in
routes/chat.py, hier liegt alles, was ohne FastAPI testbar sein soll (jede Funktion bekommt `db`):

  * Sammlung `auftrag_chat`: eine Nachricht je Dokument
      {id, appointment_id, dealer_id, fahrer_id, von ("buero"|"fahrer"), von_id, von_name, text,
       schnell (bool), foto_key?, erstellt_am, gelesen_am}
    `fahrer_id` ist der Fahrer des Gespraechs (bei Buero-Nachrichten der damals zugeteilte). Ein
    neuer Fahrer sieht deshalb nie den Verlauf seines Vorgaengers; das Buero sieht alles.
    `gelesen_am` setzt die GEGENSEITE beim Oeffnen des Chats.
  * Sammlung `auftrag_chat_status`: je Termin der Zeitpunkt der letzten Meldung an das Buero —
    eine Fahrer-Nachricht erzeugt hoechstens EINE Meldung je Gespraech und Stunde.
  * Fotos liegen unter dem privaten Praefix protocol/<firma>/ (nie ueber /api/files).
  * Loeschung (chat_nach_frist_loeschen, stuendlich im Aufraeumlauf): Chat eines Termins, dessen
    Personendaten entfernt wurden (pii_geloescht_at) oder den es nicht mehr gibt, sofort; jede
    Nachricht spaetestens CHAT_AUFBEWAHRUNG_TAGE (Standard = VERTRAG_AUFBEWAHRUNG_TAGE, 60) nach dem
    Schreiben — dieselbe Frist wie Vertraege und Termin-Personendaten.
"""
from __future__ import annotations

import logging
import uuid
from datetime import datetime, timedelta, timezone
from typing import Dict, Iterable, List, Optional

from konfig import zahl_env

log = logging.getLogger("autohandel.auftrag_chat")

TEXT_MAX = 1000
NACHRICHTEN_JE_TERMIN_MAX = 500       # gegen unbegrenztes Wachsen eines Gespraechs
ANZEIGE_MAX = 200                     # so viele (die juengsten) liefert eine Abfrage
FOTO_B64_MAX = 6_000_000              # die App verkleinert vorher (1600 px, JPEG ~0,3-1 MB)
MELDUNG_ABSTAND_MIN = 60              # eine Meldung je Gespraech und Stunde
CHAT_AUFBEWAHRUNG_TAGE = zahl_env("CHAT_AUFBEWAHRUNG_TAGE",
                                  zahl_env("VERTRAG_AUFBEWAHRUNG_TAGE", 60, unten=1), unten=1)
MELDUNG_TYP = "chat_fahrer"

SCHNELL_BUERO = ("Melde mich gleich", "Rufe gleich zurück", "Passt so", "Bitte Foto schicken")
SCHNELL_FAHRER = ("Bin vor Ort", "Verkäufer nicht da", "Bitte zurückrufen", "Preis besprechen")


def jetzt_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def text_pruefen(text: Optional[str], mit_foto: bool = False) -> str:
    """Text saeubern: Steuerzeichen raus (Zeilenumbruch bleibt), hoechstens TEXT_MAX Zeichen.
    Leer nur mit Foto erlaubt. Wirft ValueError mit einem Satz fuer die Oberflaeche."""
    roh = str(text or "")
    sauber = "".join(z for z in roh if z in "\n\t" or ord(z) >= 32).replace("\t", " ").strip()
    if len(sauber) > TEXT_MAX:
        raise ValueError(f"Die Nachricht ist zu lang (höchstens {TEXT_MAX} Zeichen).")
    if not sauber and not mit_foto:
        raise ValueError("Bitte eine Nachricht eingeben.")
    return sauber


def nachricht_neu(*, appointment_id: str, dealer_id: str, fahrer_id: str, von: str, von_id: str,
                  von_name: str, text: str, schnell: bool = False,
                  foto_key: Optional[str] = None) -> dict:
    if von not in ("buero", "fahrer"):
        raise ValueError("von muss buero oder fahrer sein")
    return {"id": uuid.uuid4().hex, "appointment_id": appointment_id, "dealer_id": dealer_id,
            "fahrer_id": fahrer_id, "von": von, "von_id": von_id, "von_name": (von_name or "")[:80],
            "text": text, "schnell": bool(schnell), "foto_key": foto_key,
            "erstellt_am": jetzt_iso(), "gelesen_am": None}


def fuer_anzeige(m: dict, seite: str) -> dict:
    """Was die Oberflaeche bekommt — ohne Speicher-Key, Firmen- und Konto-IDs."""
    return {"id": m.get("id"), "von": m.get("von"), "von_name": m.get("von_name") or "",
            "text": m.get("text") or "", "schnell": bool(m.get("schnell")),
            "foto": bool(m.get("foto_key")), "erstellt_am": m.get("erstellt_am"),
            "gelesen_am": m.get("gelesen_am"), "eigene": m.get("von") == seite}


async def verlauf(db, filt: dict, seite: str) -> List[dict]:
    """Die juengsten ANZEIGE_MAX Nachrichten, aelteste zuerst."""
    docs = await db.auftrag_chat.find(filt, {"_id": 0}).sort(
        [("erstellt_am", -1), ("id", -1)]).to_list(ANZEIGE_MAX)
    docs.reverse()
    return [fuer_anzeige(m, seite) for m in docs]


async def als_gelesen(db, filt: dict) -> int:
    """Ungelesene Nachrichten der Gegenseite (filt enthaelt `von`) als gelesen markieren."""
    r = await db.auftrag_chat.update_many({**filt, "gelesen_am": None},
                                          {"$set": {"gelesen_am": jetzt_iso()}})
    return int(r.modified_count)


async def ungelesen_je_termin(db, filt: dict, limit: int = 500) -> Dict[str, int]:
    """{appointment_id: Anzahl ungelesener Nachrichten} fuer einen Filter (enthaelt `von`)."""
    raus: Dict[str, int] = {}
    async for g in db.auftrag_chat.aggregate([
            {"$match": {**filt, "gelesen_am": None}},
            {"$group": {"_id": "$appointment_id", "n": {"$sum": 1}}},
            {"$limit": limit}]):
        if g.get("_id"):
            raus[str(g["_id"])] = int(g.get("n") or 0)
    return raus


# --------------------------------------------------------------------------- Meldung ans Buero
async def meldung_beanspruchen(db, appointment_id: str, dealer_id: str,
                               jetzt: Optional[datetime] = None) -> bool:
    """True, wenn JETZT eine Meldung fuer dieses Gespraech faellig ist (die letzte liegt mindestens
    MELDUNG_ABSTAND_MIN zurueck oder es gab noch keine). Atomar: bei gleichzeitigen Nachrichten
    gewinnt genau eine (Unique-Index auf appointment_id; der Verlierer bekommt DuplicateKey)."""
    from pymongo.errors import DuplicateKeyError
    jetzt = jetzt or datetime.now(timezone.utc)
    grenze = (jetzt - timedelta(minutes=MELDUNG_ABSTAND_MIN)).isoformat()
    try:
        r = await db.auftrag_chat_status.update_one(
            {"appointment_id": appointment_id,
             "$or": [{"meldung_am": {"$lt": grenze}}, {"meldung_am": {"$in": [None, ""]}}]},
            {"$set": {"meldung_am": jetzt.isoformat(), "dealer_id": dealer_id}},
            upsert=True)
    except DuplicateKeyError:
        return False
    return bool(r.modified_count or r.upserted_id is not None)


async def buero_empfaenger(db, appt: dict) -> List[str]:
    """Wer im Buero die Meldung bekommt: der Hauptchef der Firma und die Sucher, in deren Bereich der
    Termin liegt (angelegt, eigener Vertrag, eigener Kaufvorgang — wie deps.termin_im_bereich)."""
    dealer_id = appt.get("dealer_id")
    ids = set()
    firma = await db.dealers.find_one({"id": dealer_id}, {"_id": 0, "user_id": 1})
    chef = (firma or {}).get("user_id")
    if not chef:
        alt = await db.users.find_one({"dealer_id": dealer_id, "role": "dealer"},
                                      {"_id": 0, "id": 1}, sort=[("created_at", 1)])
        chef = (alt or {}).get("id")
    if chef:
        ids.add(chef)
    kandidaten = {appt.get("created_by")}
    if appt.get("contract_id"):
        c = await db.generated_pdfs.find_one({"id": appt["contract_id"], "dealer_id": dealer_id},
                                             {"_id": 0, "user_id": 1})
        kandidaten.add((c or {}).get("user_id"))
    if appt.get("kaufvorgang_id"):
        k = await db.kaufvorgaenge.find_one({"id": appt["kaufvorgang_id"], "dealer_id": dealer_id},
                                            {"_id": 0, "user_id": 1})
        kandidaten.add((k or {}).get("user_id"))
    kandidaten.discard(None)
    kandidaten.discard("")
    if kandidaten:
        async for u in db.users.find({"id": {"$in": sorted(kandidaten)}, "dealer_id": dealer_id,
                                      "role": "sucher"}, {"_id": 0, "id": 1}):
            ids.add(u["id"])
    return sorted(ids)


def meldung_text(appt: dict, fahrer_name: str) -> str:
    """Kurz und ohne Nachrichtentext (die Meldung lebt 90 Tage, der Chat hoechstens 60)."""
    titel = (appt.get("title") or "").strip() or "Abholtermin"
    datum = ""
    d = str(appt.get("pickup_date") or "")
    if len(d) == 10 and d[4] == "-":
        datum = f" am {d[8:10]}.{d[5:7]}.{d[0:4]}"
    wer = (fahrer_name or "").strip() or "Der Fahrer"
    return f"{wer} hat eine Nachricht zu „{titel[:80]}“{datum} geschrieben."


# --------------------------------------------------------------------------- Loeschung
async def chat_loeschen(db, termin_ids: Iterable[str], grund: str) -> int:
    """Alle Nachrichten (samt Fotos), Meldungs-Merker und Chat-Meldungen dieser Termine loeschen.
    Fotos: loeschen_oder_vormerken (scheitert es, holt der Aufraeumlauf es nach). Liefert die Zahl
    der geloeschten Nachrichten. Wirft nicht wegen einzelner Fotos."""
    ids = sorted({str(t) for t in (termin_ids or []) if t})
    if not ids:
        return 0
    from storage_service import loeschen_oder_vormerken
    async for m in db.auftrag_chat.find({"appointment_id": {"$in": ids},
                                         "foto_key": {"$type": "string"}},
                                        {"_id": 0, "foto_key": 1, "dealer_id": 1}):
        try:
            await loeschen_oder_vormerken(db, key=m["foto_key"], grund=f"chat_{grund}",
                                          dealer_id=str(m.get("dealer_id") or ""))
        except Exception:  # noqa: BLE001
            log.exception("Chatfoto %s nicht geloescht (%s)", m.get("foto_key"), grund)
    r = await db.auftrag_chat.delete_many({"appointment_id": {"$in": ids}})
    await db.auftrag_chat_status.delete_many({"appointment_id": {"$in": ids}})
    await db.meldungen.delete_many({"typ": MELDUNG_TYP, "ref": {"$in": ids}})
    return int(r.deleted_count)


async def _alte_nachrichten_loeschen(db, cutoff: str) -> int:
    from storage_service import loeschen_oder_vormerken
    async for m in db.auftrag_chat.find({"erstellt_am": {"$lt": cutoff},
                                         "foto_key": {"$type": "string"}},
                                        {"_id": 0, "foto_key": 1, "dealer_id": 1}):
        try:
            await loeschen_oder_vormerken(db, key=m["foto_key"], grund="chat_frist",
                                          dealer_id=str(m.get("dealer_id") or ""))
        except Exception:  # noqa: BLE001
            log.exception("Chatfoto %s nicht geloescht (Frist)", m.get("foto_key"))
    r = await db.auftrag_chat.delete_many({"erstellt_am": {"$lt": cutoff}})
    return int(r.deleted_count)


async def chat_nach_frist_loeschen(db, now: Optional[datetime] = None, *,
                                   frist_aktiv: Optional[bool] = None,
                                   tage: Optional[int] = None) -> int:
    """Stuendlich im Aufraeumlauf:
      1) Chat zu Terminen ohne Personendaten (pii_geloescht_at: Vertragsloeschung, Termin ohne
         Vertrag nach Frist) oder zu geloeschten Terminen -> weg (immer, die Grundlage ist weg);
      2) jede Nachricht aelter als CHAT_AUFBEWAHRUNG_TAGE -> weg — nur wenn die Loeschfristen scharf
         sind (VERTRAG_LOESCHUNG_AKTIV, dieselbe Lesart wie die Vertragsloeschung)."""
    now = now or datetime.now(timezone.utc)
    n = 0
    ids = await db.auftrag_chat.distinct("appointment_id")
    for i in range(0, len(ids), 500):
        teil = [t for t in ids[i:i + 500] if t]
        da = {}
        async for a in db.appointments.find({"id": {"$in": teil}},
                                            {"_id": 0, "id": 1, "pii_geloescht_at": 1}):
            da[a["id"]] = a
        weg = [t for t in teil if t not in da or da[t].get("pii_geloescht_at")]
        if weg:
            n += await chat_loeschen(db, weg, "termin_weg")
    if frist_aktiv is None:
        from cleanup_service import vertrag_loeschung_aktiv
        frist_aktiv = vertrag_loeschung_aktiv()
    if frist_aktiv:
        cutoff = (now - timedelta(days=int(tage or CHAT_AUFBEWAHRUNG_TAGE))).isoformat()
        n += await _alte_nachrichten_loeschen(db, cutoff)
    return n


# --------------------------------------------------------------------------- PDF fuer die Akte
def _zeit_de(iso: Optional[str]) -> str:
    try:
        t = datetime.fromisoformat(str(iso))
    except (TypeError, ValueError):
        return ""
    if t.tzinfo is None:
        t = t.replace(tzinfo=timezone.utc)
    try:
        from zoneinfo import ZoneInfo
        t = t.astimezone(ZoneInfo("Europe/Berlin"))
    except Exception:  # noqa: BLE001 — ohne Zeitzonendaten in UTC
        pass
    return t.strftime("%d.%m.%Y %H:%M")


def chat_pdf(appt: dict, nachrichten: List[dict], firma: str = "") -> bytes:
    """Kurzes PDF "Nachrichten Büro/Fahrer" zu einem Termin (fuer die Akte). Fotos nur als Vermerk."""
    import io
    from xml.sax.saxutils import escape
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.styles import ParagraphStyle
    from reportlab.lib.units import mm
    from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer
    from pdf_schrift import schriften
    normal, fett, _ok = schriften()
    titel_st = ParagraphStyle("t", fontName=fett, fontSize=14, leading=18)
    klein = ParagraphStyle("k", fontName=normal, fontSize=8.5, leading=11, textColor="#555555")
    text_st = ParagraphStyle("n", fontName=normal, fontSize=10, leading=13)
    buf = io.BytesIO()
    doc = SimpleDocTemplate(buf, pagesize=A4, leftMargin=18 * mm, rightMargin=18 * mm,
                            topMargin=16 * mm, bottomMargin=16 * mm,
                            title="Nachrichten Büro/Fahrer")
    teile = [Paragraph("Nachrichten Büro/Fahrer", titel_st), Spacer(1, 3 * mm)]
    kopf = " · ".join(x for x in (firma, appt.get("title") or "Abholtermin",
                                   str(appt.get("pickup_date") or "")) if x)
    teile += [Paragraph(escape(kopf), klein),
              Paragraph(f"Erstellt am {escape(_zeit_de(jetzt_iso()))}", klein), Spacer(1, 5 * mm)]
    if not nachrichten:
        teile.append(Paragraph("Keine Nachrichten.", text_st))
    for m in nachrichten:
        wer = "Büro" if m.get("von") == "buero" else "Fahrer"
        name = m.get("von_name") or ""
        zeile = f"{_zeit_de(m.get('erstellt_am'))} · {wer}{(' (' + name + ')') if name else ''}"
        if m.get("gelesen_am"):
            zeile += f" · gelesen {_zeit_de(m.get('gelesen_am'))}"
        teile.append(Paragraph(escape(zeile), klein))
        inhalt = escape(m.get("text") or "").replace("\n", "<br/>")
        if m.get("foto"):
            inhalt = (inhalt + "<br/>" if inhalt else "") + "[Foto]"
        teile += [Paragraph(inhalt or "—", text_st), Spacer(1, 3 * mm)]
    doc.build(teile)
    return buf.getvalue()
