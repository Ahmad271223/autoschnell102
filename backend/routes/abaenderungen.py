# -*- coding: utf-8 -*-
"""Abänderungsvertrag nach der Unterschrift (Wunsch Ahmad 24.11.2026).

Grundlage bleiben die Fassungen des Kaufvertrags (generated_pdfs + generated_pdf_versions): das Büro ändert wie
bisher über „Kaufvertrag ändern“ (neue Fassung), die Abholung erzeugt nach dem Protokoll eine neue Fassung. Hier
entsteht nur ein ZUSATZDOKUMENT, wenn eine frühere Fassung schon unterschrieben war:

  unterschrieben = online im Kundenportal (pdf_signiert_version), bei der Übergabe (finales Abholprotokoll; es gilt
                   die Fassung, die zu diesem Zeitpunkt aktuell war) oder als Abänderungsvertrag (Stand
                   „unterschrieben“). Massgeblich ist die JÜNGSTE dieser Fassungen.

Sammlung `vertrag_abaenderungen` (je Abänderung ein Dokument, je Vertrag + Fassung + Protokoll höchstens ein
gültiges): Änderungsliste, PDF, Stand offen | intern | gesendet | unterschrieben | ersetzt, Versandbelege.

Versand nur auf Knopfdruck, nie doppelt (Reservierung je Ziel, 409 bereits_versendet / frueherer_versand_unklar
wie beim Kaufvertrag, erneut=true für einen bewussten zweiten Versand). Online-Unterschrift über einen Link mit
Zufallsschlüssel im Adress-Anker (#…, gelangt nie in Server-Protokolle) — die Seite schickt ihn in der Kopfzeile
X-Portal-Sitzung wie das Kundenportal. Unterschrieben wird genau das geladene Dokument (Prüfsumme), die
Unterschriftsbilder kommen über portal_pdf in die Felder, der Verkäufer bekommt nie das Nachweisblatt.
"""
from __future__ import annotations

import asyncio
import base64
import hashlib
import logging
import secrets
import uuid
from datetime import datetime, timedelta, timezone
from typing import Annotated, Any, Dict, List, Optional

from fastapi import APIRouter, Depends, Header, HTTPException, Request, Response
from pydantic import BaseModel, Field
from pymongo.errors import DuplicateKeyError

import abaenderung as AB
from deps import aendern_braucht_abo, current_firma, db, log_activity_sicher, now_iso, require_active_sub
from rate_limiter import client_ip

log = logging.getLogger("autohandel.abaenderung")
router = APIRouter()

#: Wie lange der Link zur Online-Unterschrift gilt (Tage).
LINK_TAGE = 14
#: Ein Versand, der so lange auf "laeuft" steht, ist abgebrochen (Prozess starb) und gilt als unklar.
VERSAND_HAENGT_SEKUNDEN = 600
#: Anspruch beim Unterschreiben (wie Kundenportal SIGNIER_ANSPRUCH_S)
ANSPRUCH_SEKUNDEN = 60
STAND_GUELTIG = ("offen", "intern", "gesendet")

NICHT_UNTERSCHRIEBEN = ("Der Kaufvertrag ist noch nicht unterschrieben — Änderungen gelten direkt in der neuen "
                        "Fassung, ein Abänderungsvertrag ist nicht nötig.")
KEINE_AENDERUNGEN = "Keine Änderungen gegenüber dem unterschriebenen Vertrag — kein Abänderungsvertrag nötig."
LINK_UNGUELTIG = "Der Link ist ungültig oder abgelaufen. Bitte beim Autohaus einen neuen Link anfordern."

_index_fertig = False


async def _indizes() -> None:
    """Einmal je Prozess (idempotent): je Vertrag + Schlüssel höchstens EIN gültiger Abänderungsvertrag,
    Link-Schlüssel eindeutig."""
    global _index_fertig
    if _index_fertig:
        return
    try:
        await db.vertrag_abaenderungen.create_index(
            [("contract_id", 1), ("schluessel", 1)], unique=True, name="ein_gueltiger_je_stand",
            partialFilterExpression={"gueltig": True})
        await db.vertrag_abaenderungen.create_index("online.token", unique=True, sparse=True, name="link_token")
        await db.vertrag_abaenderungen.create_index([("dealer_id", 1), ("contract_id", 1)], name="firma_vertrag")
        _index_fertig = True
    except Exception:  # noqa: BLE001 — Index fehlt: Anlage bleibt ueber die Vorpruefung abgesichert
        log.exception("Indizes fuer vertrag_abaenderungen nicht angelegt")


# ---------------------------------------------------------------- Grundlage: welche Fassung ist unterschrieben?
async def _fassung_zum_zeitpunkt(doc: dict, zeitpunkt: str) -> int:
    """Welche Fassung war zu `zeitpunkt` aktuell? Fassung N wurde archiviert, als N+1 entstand — also die
    kleinste archivierte Fassung mit archived_at NACH dem Zeitpunkt, sonst die heutige."""
    a = await db.generated_pdf_versions.find_one(
        {"contract_id": doc["id"], "archived_at": {"$gt": zeitpunkt}}, {"_id": 0, "version": 1},
        sort=[("version", 1)])
    return int((a or {}).get("version") or doc.get("version") or 1)


async def _uebergabe_protokoll(doc: dict) -> Optional[dict]:
    """Das finale Abholprotokoll zum Vertrag (über seine Termine): das jüngste nicht abgelöste, dazu der Zeitpunkt
    der ERSTEN Abschluss-Unterschrift (dann wurde übergeben)."""
    termine = [a["id"] async for a in db.appointments.find(
        {"contract_id": doc["id"], "dealer_id": doc.get("dealer_id")}, {"_id": 0, "id": 1})]
    if not termine:
        return None
    alle = [p async for p in db.pickup_protocols.find(
        {"appointment_id": {"$in": termine}, "status": "final"},
        {"_id": 0, "id": 1, "finalized_at": 1, "superseded": 1, "lackmessungen": 1, "features": 1})]
    if not alle:
        return None
    zeiten = sorted(str(p.get("finalized_at") or "") for p in alle if p.get("finalized_at"))
    aktuell = [p for p in alle if not p.get("superseded")]
    aktuell.sort(key=lambda p: str(p.get("finalized_at") or ""))
    p = dict((aktuell or alle)[-1])
    p["_uebergabe_am"] = zeiten[0] if zeiten else str(p.get("finalized_at") or "")
    return p


async def _vertragsdaten(doc: dict, version: int) -> Optional[dict]:
    if int(version) == int(doc.get("version") or 1):
        return dict(doc.get("contract_data") or {})
    a = await db.generated_pdf_versions.find_one({"contract_id": doc["id"], "version": int(version)},
                                                 {"_id": 0, "contract_data": 1})
    return dict((a or {}).get("contract_data") or {}) if a is not None else None


async def _platzhalter_fuer(doc: dict):
    """Platzhalter in langen Texten ({abholdatum}, {ort} …) wie im Kaufvertrag einsetzen (ohne Uhrzeit)."""
    from pdf_service import _platzhalter_vertrag
    from vertrag_platzhalter import ersetzen
    firma = await db.dealers.find_one({"id": doc.get("dealer_id")}, {"_id": 0}) or {}

    def einsetzen(text: str, daten: dict) -> str:
        return ersetzen(text, _platzhalter_vertrag(daten, {}), firma, mit_uhrzeit=False)
    return einsetzen


async def stand_ermitteln(doc: dict) -> Dict[str, Any]:
    """{"unterschrieben": bool, "basis_version", "quelle", "version", "aenderungen", "protokoll_id", "schluessel"}.
    aenderungen leer = kein Abänderungsvertrag nötig."""
    version = int(doc.get("version") or 1)
    kandidaten: List[tuple] = []
    if doc.get("pdf_signiert_b64") and doc.get("pdf_signiert_version"):
        kandidaten.append((int(doc["pdf_signiert_version"]), "online"))
    protokoll = await _uebergabe_protokoll(doc)
    if protokoll and protokoll.get("_uebergabe_am"):
        kandidaten.append((await _fassung_zum_zeitpunkt(doc, protokoll["_uebergabe_am"]), "uebergabe"))
    unterschrieben_ab = [a async for a in db.vertrag_abaenderungen.find(
        {"contract_id": doc["id"], "dealer_id": doc.get("dealer_id"), "status": "unterschrieben"},
        {"_id": 0, "version": 1, "protokoll_id": 1})]
    for a in unterschrieben_ab:
        kandidaten.append((int(a.get("version") or 1), "abaenderung"))
    raus: Dict[str, Any] = {"unterschrieben": bool(kandidaten), "version": version, "aenderungen": [],
                            "basis_version": None, "quelle": None, "protokoll_id": None, "schluessel": None}
    if not kandidaten:
        return raus
    basis, quelle = max(kandidaten, key=lambda k: (k[0], k[1] == "abaenderung"))
    raus.update(basis_version=basis, quelle=quelle)
    bisher = await _vertragsdaten(doc, basis)
    if bisher is None:
        return raus
    # Befunde des Protokolls (fehlende Ausstattung, Nachlackierung) stehen in keinem Vertragsfeld: sie gehoeren
    # in den Abaenderungsvertrag, solange kein unterschriebener sie schon enthaelt.
    p_dazu = None
    if protokoll and not any(a.get("protokoll_id") == protokoll.get("id") for a in unterschrieben_ab):
        p_dazu = protokoll
    raus["aenderungen"] = AB.aenderungen(bisher, doc.get("contract_data") or {}, protokoll=p_dazu,
                                         text_ersetzen=await _platzhalter_fuer(doc))
    raus["protokoll_id"] = (p_dazu or {}).get("id")
    raus["schluessel"] = f"{version}:{raus['protokoll_id'] or ''}"
    return raus


# ---------------------------------------------------------------- Hilfen
def _sicht(a: dict) -> dict:
    """Ein Abänderungsvertrag für die Oberfläche — ohne PDF-Daten, Link-Schlüssel und Prüfvermerke."""
    o = a.get("online") or {}
    return {
        "id": a.get("id"), "contract_id": a.get("contract_id"), "contract_no": a.get("contract_no"),
        "version": a.get("version"), "basis_version": a.get("basis_version"), "status": a.get("status"),
        "erstellt_am": a.get("erstellt_am"), "aenderungen": a.get("aenderungen") or [],
        "zeilen": [AB.zeile_text(z) for z in a.get("aenderungen") or []],
        "gesendet_am": a.get("gesendet_am"), "unterschrieben_am": a.get("unterschrieben_am"),
        "unterschrieben_von": a.get("unterschrift_name"), "nach_unterschrift_senden": bool(a.get("nach_unterschrift_senden")),
        "link_laeuft_ab": o.get("laeuft_ab") if o.get("token") else None,
        "versand": [{k: e.get(k) for k in ("art", "recipient", "zustellung", "am", "erneut")}
                    for e in a.get("versand") or [] if isinstance(e, dict)],
        # Liste laedt ohne PDF-Daten: die Pruefsumme sagt, ob es die unterschriebene Fassung gibt
        "pdf_unterschrieben": bool(a.get("pdf_signiert_b64") or a.get("pdf_signiert_sha256")),
    }


_OHNE_PDF = {"_id": 0, "pdf_b64": 0, "pdf_signiert_b64": 0}


async def _vertrag(contract_id: str, user: dict, voll: bool = False) -> dict:
    from routes.contracts import _vertrag_bereich
    proj = {"_id": 0} if voll else {"_id": 0, "pdf_b64": 0, "pdf_digital_b64": 0, "pdf_portal_b64": 0}
    doc = await db.generated_pdfs.find_one({"id": contract_id, **_vertrag_bereich(user)}, proj)
    if not doc:
        raise HTTPException(404, "Vertrag nicht gefunden")
    return doc


async def _abaenderung(contract_id: str, aid: str, user: dict, proj: Optional[dict] = None) -> dict:
    a = await db.vertrag_abaenderungen.find_one(
        {"id": aid, "contract_id": contract_id, "dealer_id": user["dealer_id"]}, proj or {"_id": 0})
    if not a:
        raise HTTPException(404, "Abänderungsvertrag nicht gefunden")
    return a


async def _pdf_bauen(doc: dict, aenderungen: List[dict]) -> bytes:
    """Wie die Portal-Fassung des Kaufvertrags: Käufer = Ersteller des Vertrags, eingefrorene Käuferdaten, Logo."""
    import abaenderung_pdf
    from auftraggeber import kaeufer_basis
    from routes.contracts import _apply_contract_overrides, _logo_einsetzen
    cd = dict(doc.get("contract_data") or {})
    v = await db.vehicles.find_one({"id": doc.get("vehicle_id"), "dealer_id": doc["dealer_id"]}, {"_id": 0}) or {}
    vehicle = dict(v.get("data") or {})
    termin = await db.appointments.find_one({"contract_id": doc["id"], "dealer_id": doc["dealer_id"]},
                                            {"_id": 0, "created_by": 1}) or {}
    dealer = await kaeufer_basis(dealer_id=doc["dealer_id"], user_ids=(doc.get("user_id"), termin.get("created_by"))) or {}
    vehicle, dealer = _apply_contract_overrides(contract=cd, vehicle=vehicle, dealer=dealer)
    dealer = await _logo_einsetzen(dealer, cd)
    cd.setdefault("contract_no", doc.get("contract_no") or "")
    return await asyncio.to_thread(abaenderung_pdf.erzeugen, dealer=dealer, vehicle=vehicle, contract=cd,
                                   aenderungen=aenderungen, vertrag_datum=str(doc.get("created_at") or ""))


def _dateiname(a: dict, unterschrieben: bool = False) -> str:
    nr = a.get("contract_no") or a.get("contract_id") or ""
    datum = str(a.get("erstellt_am") or "")[:10]
    return f"Abaenderungsvertrag-{nr}-{datum}{'-unterschrieben' if unterschrieben else ''}.pdf"


# ---------------------------------------------------------------- Büro: Liste, anlegen, PDF
@router.get("/contracts/{contract_id}/abaenderungen")
async def abaenderungen_liste(contract_id: str, user=Depends(current_firma)):
    """Abänderungsverträge zum Vertrag + Vorschlag (was sich seit der unterschriebenen Fassung geändert hat)."""
    doc = await _vertrag(contract_id, user)
    stand = await stand_ermitteln(doc)
    liste = [_sicht(a) async for a in db.vertrag_abaenderungen.find(
        {"contract_id": contract_id, "dealer_id": user["dealer_id"]}, _OHNE_PDF).sort("erstellt_am", -1).limit(50)]
    # Ein schon angelegter Abaenderungsvertrag zu GENAU diesem Stand ersetzt den Vorschlag.
    gleich = await db.vertrag_abaenderungen.find_one(
        {"contract_id": contract_id, "dealer_id": user["dealer_id"], "schluessel": stand.get("schluessel"),
         "gueltig": True}, {"_id": 0, "id": 1}) if stand.get("schluessel") else None
    return {"unterschrieben": stand["unterschrieben"], "basis_version": stand["basis_version"],
            "quelle": stand["quelle"], "version": stand["version"],
            "vorschlag": [] if gleich else stand["aenderungen"],
            "vorschlag_zeilen": [] if gleich else [AB.zeile_text(z) for z in stand["aenderungen"]],
            "vorhanden_id": (gleich or {}).get("id"),
            "liste": liste}


class AnlegenIn(BaseModel):
    #: Wahl des Büros beim Speichern: online unterschreiben lassen, PDF per E-Mail senden oder nur intern ändern.
    #: Angelegt wird in allen drei Fällen; GESENDET wird nur über /senden (Knopf), nie hier.
    art: str = Field(default="intern", pattern=r"^(online|email|intern)$")


@router.post("/contracts/{contract_id}/abaenderungen")
async def abaenderung_anlegen(contract_id: str, body: AnlegenIn, user=Depends(aendern_braucht_abo)):
    await _indizes()
    doc = await _vertrag(contract_id, user)
    if (doc.get("loeschung") or {}).get("status") == "laeuft":
        raise HTTPException(409, "Der Vertrag wird gerade gelöscht.")
    stand = await stand_ermitteln(doc)
    if not stand["unterschrieben"]:
        raise HTTPException(409, {"code": "nicht_unterschrieben", "msg": NICHT_UNTERSCHRIEBEN})
    if not stand["aenderungen"]:
        raise HTTPException(409, {"code": "keine_aenderungen", "msg": KEINE_AENDERUNGEN})
    vorhanden = await db.vertrag_abaenderungen.find_one(
        {"contract_id": contract_id, "dealer_id": user["dealer_id"], "schluessel": stand["schluessel"], "gueltig": True},
        _OHNE_PDF)
    if vorhanden:
        return {**_sicht(vorhanden), "bereits_vorhanden": True}
    voll = await _vertrag(contract_id, user, voll=True)
    try:
        pdf = await _pdf_bauen(voll, stand["aenderungen"])
    except Exception:  # noqa: BLE001
        log.exception("Abaenderungsvertrag zu %s nicht erzeugt", contract_id)
        raise HTTPException(400, "Der Abänderungsvertrag konnte nicht erzeugt werden.")
    neu = {
        "id": uuid.uuid4().hex, "contract_id": contract_id, "dealer_id": user["dealer_id"],
        "contract_no": doc.get("contract_no") or "", "user_id": doc.get("user_id"),
        "version": stand["version"], "basis_version": stand["basis_version"], "quelle": stand["quelle"],
        "protokoll_id": stand["protokoll_id"], "schluessel": stand["schluessel"], "gueltig": True,
        "aenderungen": stand["aenderungen"], "art": body.art,
        "status": "intern" if body.art == "intern" else "offen",
        "pdf_b64": base64.b64encode(pdf).decode(), "pdf_sha256": hashlib.sha256(pdf).hexdigest(),
        "erstellt_am": now_iso(), "erstellt_von": user.get("id"), "versand": [],
    }
    try:
        await db.vertrag_abaenderungen.insert_one(dict(neu))
    except DuplicateKeyError:
        vorhanden = await db.vertrag_abaenderungen.find_one(
            {"contract_id": contract_id, "schluessel": stand["schluessel"], "gueltig": True}, _OHNE_PDF)
        if vorhanden:
            return {**_sicht(vorhanden), "bereits_vorhanden": True}
        raise HTTPException(409, "Der Abänderungsvertrag wird gerade angelegt — bitte neu laden.")
    # Fruehere, noch nicht unterschriebene Abaenderungsvertraege sind damit ueberholt (ihr Link gilt nicht mehr).
    await db.vertrag_abaenderungen.update_many(
        {"contract_id": contract_id, "dealer_id": user["dealer_id"], "id": {"$ne": neu["id"]},
         "status": {"$in": list(STAND_GUELTIG)}},
        {"$set": {"status": "ersetzt", "gueltig": False, "ersetzt_am": now_iso(), "ersetzt_durch": neu["id"]},
         "$unset": {"online.token": ""}})
    await log_activity_sicher(user["dealer_id"], user.get("id", ""), "vertrag.abaenderung.angelegt", ref=contract_id,
                              meta={"abaenderung": neu["id"], "version": neu["version"], "art": body.art,
                                    "anzahl": len(neu["aenderungen"])})
    return _sicht(neu)


@router.get("/contracts/{contract_id}/abaenderungen/{aid}/pdf")
async def abaenderung_pdf(contract_id: str, aid: str, user=Depends(current_firma)):
    """Für die Firma: das unterschriebene Dokument MIT Signaturnachweis, sonst das ungeschriebene."""
    from vertrag_dateiname import content_disposition
    await _vertrag(contract_id, user)
    a = await _abaenderung(contract_id, aid, user)
    b64 = a.get("pdf_signiert_b64") or a.get("pdf_b64")
    if not b64:
        raise HTTPException(404, "PDF fehlt")
    pdf = base64.b64decode(b64)
    if not a.get("pdf_signiert_b64"):
        # STORNIERT-Zeichen wie beim Vertrag (Paket C2): nur beim Ausliefern, nie gespeichert;
        # die unterschriebene Fassung mit Signaturnachweis bleibt unberuehrt.
        from vertrag_storno import falls_storniert
        pdf = await falls_storniert(contract_id, pdf)
    return Response(content=pdf, media_type="application/pdf",
                    headers={"Content-Disposition": content_disposition(_dateiname(a, bool(a.get("pdf_signiert_b64")))),
                             "Cache-Control": "no-store"})


# ---------------------------------------------------------------- Versand (nur per Knopf, nie doppelt)
class SendenIn(BaseModel):
    art: str = Field(pattern=r"^(online|pdf)$")
    recipient: str = Field(max_length=200)
    erneut: bool = False
    #: nur bei art=online: nach der Unterschrift die unterschriebene Fassung automatisch per E-Mail senden
    nach_unterschrift_senden: bool = False


def _link(token: str) -> str:
    from routes.contracts import _oeffentliche_basis
    return f"{_oeffentliche_basis()}/abaenderung#{token}"


def _mail(a: dict, vertrag: dict, firma: dict, art: str, link: str = "", unterschrieben: bool = False) -> tuple:
    import abaenderung_pdf
    cd = vertrag.get("contract_data") or {}
    nr = a.get("contract_no") or ""
    fahrzeug = " ".join(x for x in (cd.get("vehicle_make") or vertrag.get("make"),
                                    cd.get("vehicle_model") or vertrag.get("model")) if x) or "Ihr Fahrzeug"
    name = (cd.get("seller_name") or vertrag.get("seller_name") or "").strip()
    firmenname = (firma.get("company_name") or "").strip()
    vom = abaenderung_pdf.datum_de(vertrag.get("created_at"))
    zeilen = "\n".join(f"• {AB.zeile_text(z)}" for z in (a.get("aenderungen") or [])[:40])
    betreff = f"Abänderungsvertrag zum Kaufvertrag {nr} – {fahrzeug}".replace("  ", " ")
    kopf = f"Guten Tag{(' ' + name) if name else ''},\n\n"
    if art == "online":
        text = (f"{kopf}zu Ihrem Kaufvertrag {nr} vom {vom} über {fahrzeug} gibt es Änderungen:\n\n{zeilen}\n\n"
                f"Bitte lesen und unterschreiben Sie den Abänderungsvertrag hier im Browser (mit Finger oder Maus):\n"
                f"{link}\n\nDer Link gilt {LINK_TAGE} Tage. Alles andere im Kaufvertrag bleibt unverändert.")
    elif unterschrieben:
        betreff = f"Unterschriebener {betreff}"
        text = (f"{kopf}anbei erhalten Sie den unterschriebenen Abänderungsvertrag zu Ihrem Kaufvertrag {nr} "
                f"vom {vom} über {fahrzeug}.")
    else:
        text = (f"{kopf}anbei erhalten Sie den Abänderungsvertrag zu Ihrem Kaufvertrag {nr} vom {vom} über "
                f"{fahrzeug}. Geändert wird:\n\n{zeilen}\n\nAlles andere im Kaufvertrag bleibt unverändert. "
                "Bitte unterschreiben Sie den Abänderungsvertrag und senden Sie ihn an uns zurück.")
    text += f"\n\nMit freundlichen Grüßen\nIhr Autohaus\n{firmenname}".rstrip()
    return betreff, text


async def _haengende_als_unklar(aid: str) -> None:
    grenze = (datetime.now(timezone.utc) - timedelta(seconds=VERSAND_HAENGT_SEKUNDEN)).isoformat()
    await db.vertrag_abaenderungen.update_one(
        {"id": aid}, {"$set": {"versand.$[e].zustellung": "unklar"}},
        array_filters=[{"e.zustellung": "laeuft", "e.am": {"$lt": grenze}}])


async def versenden(a: dict, *, art: str, empfaenger: str, user: Optional[dict], erneut: bool = False,
                    vertrag: dict, firma: dict, antwort_an: str = "", zusatz: Optional[dict] = None) -> dict:
    """Eine Mail zum Abänderungsvertrag — reserviert je Ziel (online-Link bzw. PDF mit seiner Prüfsumme), sendet,
    vermerkt. 409 bereits_versendet / frueherer_versand_unklar / laeuft; 502 bei Fehlschlag."""
    import email_service
    import provider_fetch
    from routes.contracts import _mail_tag_zaehlen, _mail_tag_zurueck
    unterschrieben = bool(a.get("pdf_signiert_b64"))
    anhang, link = None, ""
    if art == "online":
        if a.get("status") not in ("offen", "intern", "gesendet"):
            raise HTTPException(409, "Dieser Abänderungsvertrag ist schon unterschrieben oder ersetzt.")
        o = a.get("online") or {}
        laeuft_ab = o.get("laeuft_ab") or ""
        if not o.get("token") or laeuft_ab <= now_iso():
            token = secrets.token_urlsafe(32)
            laeuft_ab = (datetime.now(timezone.utc) + timedelta(days=LINK_TAGE)).isoformat()
            r = await db.vertrag_abaenderungen.update_one(
                {"id": a["id"], "status": {"$in": list(STAND_GUELTIG)}},
                {"$set": {"online.token": token, "online.laeuft_ab": laeuft_ab, "online.erstellt_am": now_iso()},
                 "$unset": {"online.gelesen": ""}})
            if not r.modified_count:
                raise HTTPException(409, "Der Abänderungsvertrag wurde gerade geändert — bitte neu laden.")
            o = {"token": token, "laeuft_ab": laeuft_ab}
        link = _link(o["token"])
        ziel = f"online:{hashlib.sha256(o['token'].encode()).hexdigest()[:12]}"
    else:
        if unterschrieben:
            import portal_pdf
            anhang = await asyncio.to_thread(portal_pdf.kundenfassung, base64.b64decode(a["pdf_signiert_b64"]))
        else:
            anhang = base64.b64decode(a.get("pdf_b64") or "")
        if not anhang:
            raise HTTPException(404, "PDF fehlt")
        ziel = f"pdf:{hashlib.sha256(anhang).hexdigest()[:16]}"
    # je Empfänger (wie beim Kaufvertrag): dieselbe Mail an dieselbe Adresse nie doppelt ohne Rückfrage
    ziel += ":" + hashlib.sha256(empfaenger.strip().lower().encode()).hexdigest()[:8]
    betreff, text = _mail(a, vertrag, firma, art, link, unterschrieben)

    await _haengende_als_unklar(a["id"])
    frisch = await db.vertrag_abaenderungen.find_one({"id": a["id"]}, {"_id": 0, "versand": 1}) or {}
    eintraege = [e for e in frisch.get("versand") or [] if isinstance(e, dict) and e.get("ziel") == ziel]
    if any(e.get("zustellung") == "laeuft" for e in eintraege):
        raise HTTPException(409, {"code": "laeuft", "msg": "Der Versand läuft gerade — bitte kurz warten."},
                            headers={"Retry-After": "5"})
    if not erneut:
        fertig = next((e for e in reversed(eintraege) if e.get("zustellung") in ("versendet", "mock")), None)
        if fertig:
            raise HTTPException(409, {"code": "bereits_versendet", "am": fertig.get("am"),
                                      "recipient": fertig.get("recipient"),
                                      "msg": "Schon verschickt. Wirklich noch einmal senden?"})
        if any(e.get("zustellung") == "unklar" for e in eintraege):
            raise HTTPException(409, {"code": "frueherer_versand_unklar",
                                      "msg": "Ein früherer Versand ist unklar (vielleicht angekommen). "
                                             "Wirklich noch einmal senden?"})
    n = len(eintraege) + 1
    schluessel = f"{ziel}#{n}"
    eintrag = {"key": schluessel, "ziel": ziel, "art": art, "recipient": empfaenger, "zustellung": "laeuft",
               "am": now_iso(), "von": (user or {}).get("id"), "erneut": bool(erneut), **(zusatz or {})}
    r = await db.vertrag_abaenderungen.update_one(
        {"id": a["id"], "versand.key": {"$ne": schluessel},
         "versand": {"$not": {"$elemMatch": {"ziel": ziel, "zustellung": "laeuft"}}}},
        {"$push": {"versand": {"$each": [eintrag], "$slice": -100}}})
    if not r.modified_count:
        raise HTTPException(409, {"code": "laeuft", "msg": "Der Versand läuft gerade — bitte kurz warten."},
                            headers={"Retry-After": "5"})
    zaehler = None
    try:
        zaehler = await _mail_tag_zaehlen(user) if user else None
    except HTTPException:
        await db.vertrag_abaenderungen.update_one({"id": a["id"]}, {"$pull": {"versand": {"key": schluessel}}})
        raise
    if provider_fetch.MOCK_PROVIDER_FETCH:
        ok, beleg = True, "mock"
    else:
        try:
            ok, beleg = await email_service.send_email_mit_beleg(
                empfaenger, betreff, text, anhang=anhang,
                anhang_name=_dateiname(a, unterschrieben) if anhang else "",
                reply_to=antwort_an or None, absender_name=firma.get("company_name") or "",
                idempotency_key=f"abaenderung-{a['id']}-{schluessel}")
        except Exception:  # noqa: BLE001 — Anbieterfehler = nicht versendet
            log.exception("Abaenderungsvertrag %s: Versand fehlgeschlagen", a["id"])
            ok, beleg = False, ""
    if not ok:
        if beleg == email_service.BELEG_UNKLAR:
            await db.vertrag_abaenderungen.update_one(
                {"id": a["id"], "versand.key": schluessel}, {"$set": {"versand.$.zustellung": "unklar"}})
            raise HTTPException(502, {"code": "versand_unklar", "msg": "Unklar, ob die E-Mail angekommen ist — "
                                      "bitte später den Stand prüfen und ggf. erneut senden."})
        await db.vertrag_abaenderungen.update_one({"id": a["id"]}, {"$pull": {"versand": {"key": schluessel}}})
        await _mail_tag_zurueck(zaehler)
        raise HTTPException(502, "E-Mail-Versand fehlgeschlagen — bitte in ein paar Minuten erneut versuchen.")
    zustellung = "mock" if beleg == "mock" else "versendet"
    setzen: Dict[str, Any] = {"versand.$.zustellung": zustellung, "versand.$.beleg": beleg or ""}
    if art == "online" or not unterschrieben:
        setzen["gesendet_am"] = now_iso()
    await db.vertrag_abaenderungen.update_one({"id": a["id"], "versand.key": schluessel}, {"$set": setzen})
    # Stand "gesendet" nur aus offen/intern — ein unterschriebener bleibt unterschrieben
    await db.vertrag_abaenderungen.update_one({"id": a["id"], "status": {"$in": ["offen", "intern"]}},
                                              {"$set": {"status": "gesendet"}})
    return {"ok": True, "zustellung": zustellung, "art": art, "empfaenger": empfaenger}


@router.post("/contracts/{contract_id}/abaenderungen/{aid}/senden")
async def abaenderung_senden(contract_id: str, aid: str, body: SendenIn, user=Depends(require_active_sub)):
    """„Online unterschreiben lassen“ (Mail mit Link) oder „Per E-Mail senden“ (PDF; nach der Unterschrift die
    unterschriebene Fassung ohne Nachweisblatt). Nur auf Knopfdruck."""
    import email_service
    import provider_fetch
    from deps import effective_dealer
    from routes.contracts import _firma_des_vertrags
    from vertrag_mail import sucher_kontakt
    empfaenger = (body.recipient or "").strip()
    if not email_service.gueltige_adresse(empfaenger):
        raise HTTPException(400, "Bitte eine gültige E-Mail-Adresse angeben")
    if not provider_fetch.MOCK_PROVIDER_FETCH and not email_service.email_configured():
        raise HTTPException(503, "E-Mail-Versand ist nicht eingerichtet — die Mail wurde NICHT versendet.")
    doc = await _vertrag(contract_id, user)
    a = await _abaenderung(contract_id, aid, user)
    if a.get("status") == "ersetzt":
        raise HTTPException(409, "Dieser Abänderungsvertrag ist durch einen neueren ersetzt.")
    firma = _firma_des_vertrags(doc, await effective_dealer(user) or {})
    _, antwort = sucher_kontakt(user, firma)
    erg = await versenden(a, art=body.art, empfaenger=empfaenger, user=user, erneut=body.erneut, vertrag=doc,
                          firma=firma, antwort_an=antwort)
    if body.art == "online":
        await db.vertrag_abaenderungen.update_one(
            {"id": aid}, {"$set": {"nach_unterschrift_senden": bool(body.nach_unterschrift_senden),
                                   "nach_unterschrift_an": empfaenger}})
    await log_activity_sicher(user["dealer_id"], user.get("id", ""), f"vertrag.abaenderung.gesendet.{body.art}",
                              ref=contract_id, meta={"abaenderung": aid, "erneut": body.erneut})
    return erg


# ---------------------------------------------------------------- Verkäufer: Link öffnen, lesen, unterschreiben
LinkKopf = Annotated[str, Header(alias="X-Portal-Sitzung", max_length=200)]
_FELDER_OEFFENTLICH = {"_id": 0, "id": 1, "contract_id": 1, "dealer_id": 1, "contract_no": 1, "status": 1, "online": 1,
                       "aenderungen": 1, "erstellt_am": 1, "unterschrieben_am": 1, "unterschrift_name": 1,
                       "pdf_b64": 1, "pdf_signiert_b64": 1, "user_id": 1, "erstellt_von": 1,
                       "nach_unterschrift_senden": 1, "nach_unterschrift_an": 1, "versand": 1}


async def _per_link(token: str, request: Request) -> dict:
    from routes.kundenportal import _code_limiter_ip, _portal_limiter_ip, _zu_viele_anfragen
    ip = client_ip(request)
    if not await _portal_limiter_ip.check(ip):
        raise _zu_viele_anfragen()
    if not await _code_limiter_ip.check(ip):
        raise HTTPException(429, "Zu viele Versuche — bitte in 10 Minuten erneut.")
    t = (token or "").strip()
    a = await db.vertrag_abaenderungen.find_one({"online.token": t}, _FELDER_OEFFENTLICH) if len(t) >= 20 else None
    if not a or a.get("status") not in ("offen", "gesendet", "intern", "unterschrieben") \
            or ((a.get("online") or {}).get("laeuft_ab") or "") <= now_iso():
        raise HTTPException(404, LINK_UNGUELTIG)
    c = await db.generated_pdfs.find_one({"id": a["contract_id"], "dealer_id": a["dealer_id"],
                                          "loeschung.status": {"$ne": "laeuft"}},
                                         {"_id": 0, "id": 1, "make": 1, "model": 1, "contract_data.vehicle_make": 1,
                                          "contract_data.vehicle_model": 1, "contract_data.seller_name": 1,
                                          "created_at": 1, "seller_name": 1, "contract_data.seller_email": 1,
                                          "seller_email": 1})
    if c is None:
        raise HTTPException(404, LINK_UNGUELTIG)
    await _code_limiter_ip.erstatten(ip)
    a["_vertrag"] = c
    return a


def _kurz(a: dict, firma: dict) -> dict:
    import abaenderung_pdf
    c = a.get("_vertrag") or {}
    cd = c.get("contract_data") or {}
    return {"contract_no": a.get("contract_no") or "", "firma": firma.get("company_name") or "",
            "fahrzeug": " ".join(x for x in (cd.get("vehicle_make") or c.get("make"),
                                             cd.get("vehicle_model") or c.get("model")) if x),
            "vertrag_vom": abaenderung_pdf.datum_de(c.get("created_at")),
            "verkaeufer": cd.get("seller_name") or c.get("seller_name") or "",
            "zeilen": [AB.zeile_text(z) for z in a.get("aenderungen") or []],
            "status": "unterschrieben" if a.get("status") == "unterschrieben" else "offen",
            "unterschrieben_am": a.get("unterschrieben_am")}


@router.get("/public/abaenderung")
async def public_abaenderung(token: LinkKopf, request: Request):
    a = await _per_link(token, request)
    firma = await db.dealers.find_one({"id": a["dealer_id"]}, {"_id": 0, "company_name": 1}) or {}
    return {"abaenderung": _kurz(a, firma)}


@router.get("/public/abaenderung/pdf")
async def public_abaenderung_pdf(token: LinkKopf, request: Request):
    """Vor der Unterschrift das Dokument (die Auslieferung wird mit Prüfsumme vermerkt), danach das unterschriebene
    — immer ohne Signaturnachweis (den sieht nur die Firma)."""
    from vertrag_dateiname import content_disposition
    a = await _per_link(token, request)
    if a.get("status") == "unterschrieben" and a.get("pdf_signiert_b64"):
        import portal_pdf
        pdf = await asyncio.to_thread(portal_pdf.kundenfassung, base64.b64decode(a["pdf_signiert_b64"]))
    else:
        pdf = base64.b64decode(a.get("pdf_b64") or "")
        if pdf:
            await db.vertrag_abaenderungen.update_one(
                {"id": a["id"], "online.token": token.strip()},
                {"$set": {"online.gelesen": {"sha256": hashlib.sha256(pdf).hexdigest(), "am": now_iso()}}})
    if not pdf:
        raise HTTPException(404, "Das Dokument fehlt — bitte das Autohaus ansprechen.")
    return Response(content=pdf, media_type="application/pdf",
                    headers={"Content-Disposition": content_disposition(_dateiname(a, a.get("status") == "unterschrieben")),
                             "Cache-Control": "private, no-store", "X-Robots-Tag": "noindex"})


class UnterschreibenIn(BaseModel):
    signature_b64: str = Field(min_length=20, max_length=3_000_000)
    name: str = Field(min_length=2, max_length=120)
    einverstanden: bool = False


@router.post("/public/abaenderung/unterschreiben")
async def public_abaenderung_unterschreiben(token: LinkKopf, body: UnterschreibenIn, request: Request):
    import portal_pdf
    from routes.kundenportal import (_BERLIN, _bild_bytes, _datei_bytes, _unterschrift_limiter, _unterschrift_pruefen,
                                     meldung_anlegen)
    ip = client_ip(request)
    if not await _unterschrift_limiter.check(ip):
        raise HTTPException(429, "Zu viele Versuche — bitte später erneut.")
    if not body.einverstanden:
        raise HTTPException(400, "Bitte bestätigen, dass Sie den Abänderungsvertrag gelesen haben und ihm zustimmen.")
    a = await _per_link(token, request)
    if a.get("status") == "unterschrieben":
        raise HTTPException(409, "Dieser Abänderungsvertrag ist bereits unterschrieben.")
    original = base64.b64decode(a.get("pdf_b64") or "")
    gelesen = (a.get("online") or {}).get("gelesen") or {}
    if not original or gelesen.get("sha256") != hashlib.sha256(original).hexdigest():
        raise HTTPException(409, "Bitte zuerst den Abänderungsvertrag laden und lesen — Seite neu laden und erneut "
                                 "versuchen.")
    name = " ".join(body.name.split())
    raw = _bild_bytes(body.signature_b64, "Unterschrift", 2 * 1024 * 1024)
    _unterschrift_pruefen(raw, "Unterschrift")
    wann = datetime.now(timezone.utc)
    anspruch = secrets.token_hex(8)
    offen = {"id": a["id"], "online.token": token.strip(), "status": {"$in": list(STAND_GUELTIG)}}
    r = await db.vertrag_abaenderungen.update_one(
        {**offen, "$or": [{"online.anspruch_bis": {"$exists": False}}, {"online.anspruch_bis": None},
                          {"online.anspruch_bis": {"$lt": wann.isoformat()}}]},
        {"$set": {"online.anspruch": anspruch,
                  "online.anspruch_bis": (wann + timedelta(seconds=ANSPRUCH_SEKUNDEN)).isoformat()}})
    if not r.modified_count:
        raise HTTPException(409, "Die Unterschrift wird gerade verarbeitet oder der Abänderungsvertrag wurde "
                                 "geändert — bitte die Seite neu laden.")
    try:
        firma = await db.dealers.find_one({"id": a["dealer_id"]},
                                          {"_id": 0, "company_name": 1, "unterschrift_key": 1, "user_id": 1}) or {}
        kaeufer_png = await _datei_bytes(firma.get("unterschrift_key"))
        zeit = wann.astimezone(_BERLIN).strftime("%d.%m.%Y, %H:%M")
        pdf, _summe, in_feldern = await asyncio.to_thread(
            portal_pdf.unterschreiben, original, verkaeufer_png=raw, kaeufer_png=kaeufer_png, name=name, zeit=zeit,
            firma=(firma.get("company_name") or "Autohändler").strip(),
            vertragsnummer=f"{a.get('contract_no') or a['contract_id']} (Abänderungsvertrag)", fassung=1)
        r = await db.vertrag_abaenderungen.update_one(
            {**offen, "online.anspruch": anspruch},
            {"$set": {"status": "unterschrieben", "unterschrieben_am": wann.isoformat(), "unterschrift_name": name[:120],
                      "unterschrift_ip": ip, "unterschrift_in_feldern": bool(in_feldern),
                      "kaeufer_unterschrift": bool(kaeufer_png),
                      "pdf_signiert_b64": base64.b64encode(pdf).decode(),
                      "pdf_signiert_sha256": hashlib.sha256(pdf).hexdigest()},
             "$unset": {"online.anspruch": "", "online.anspruch_bis": ""}})
    except BaseException:
        await db.vertrag_abaenderungen.update_one({**offen, "online.anspruch": anspruch},
                                                  {"$unset": {"online.anspruch": "", "online.anspruch_bis": ""}})
        raise
    if not r.modified_count:
        raise HTTPException(409, "Der Abänderungsvertrag wurde gerade schon unterschrieben oder geändert.")
    empfaenger = [u for u in {firma.get("user_id"), a.get("user_id"), a.get("erstellt_von")} if u]
    await meldung_anlegen(a["dealer_id"], empfaenger, "abaenderung_unterschrieben",
                          f"Abänderungsvertrag zu Kaufvertrag {a.get('contract_no') or ''} wurde von {name} "
                          f"digital unterschrieben.".replace("  ", " "),
                          ref=a["contract_id"], contract_no=a.get("contract_no"))
    await log_activity_sicher(a["dealer_id"], "", "vertrag.abaenderung.unterschrieben", ref=a["contract_id"],
                              meta={"abaenderung": a["id"], "name": name[:120], "ip": ip})
    await _unterschrift_limiter.erstatten(ip)
    if a.get("nach_unterschrift_senden") and a.get("nach_unterschrift_an"):
        await _nach_unterschrift_senden(a["id"])
    return {"ok": True, "unterschrieben_am": wann.isoformat(), "contract_no": a.get("contract_no") or ""}


async def _nach_unterschrift_senden(aid: str) -> None:
    """Vom Büro beim Versand des Links eingestellt: die unterschriebene Fassung per E-Mail an den Verkäufer.
    Scheitert das, bekommt das Büro eine Meldung (der Knopf „Per E-Mail senden“ bleibt)."""
    from routes.contracts import _firma_des_vertrags
    from routes.kundenportal import meldung_anlegen
    a = await db.vertrag_abaenderungen.find_one({"id": aid}, {"_id": 0})
    if not a:
        return
    try:
        doc = await db.generated_pdfs.find_one({"id": a["contract_id"], "dealer_id": a["dealer_id"]},
                                               {"_id": 0, "pdf_b64": 0, "pdf_digital_b64": 0, "pdf_portal_b64": 0,
                                                "pdf_signiert_b64": 0}) or {}
        firma = await db.dealers.find_one({"id": a["dealer_id"]}, {"_id": 0}) or {}
        ersteller = await db.users.find_one({"id": a.get("erstellt_von")}, {"_id": 0, "id": 1, "dealer_id": 1,
                                                                            "role": 1}) if a.get("erstellt_von") else None
        await versenden(a, art="pdf", empfaenger=a["nach_unterschrift_an"], user=ersteller, vertrag=doc,
                        firma=_firma_des_vertrags(doc, firma), antwort_an=str(firma.get("email") or ""),
                        zusatz={"automatisch": True})
    except HTTPException as exc:
        if exc.status_code == 409:
            return                                      # schon verschickt / laeuft
        await meldung_anlegen(a["dealer_id"], [u for u in {a.get("erstellt_von")} if u], "abaenderung_versand_offen",
                              f"Der unterschriebene Abänderungsvertrag zu Kaufvertrag {a.get('contract_no') or ''} "
                              "konnte nicht automatisch per E-Mail gesendet werden — bitte „Per E-Mail senden“.",
                              ref=a["contract_id"], contract_no=a.get("contract_no"))
    except Exception:  # noqa: BLE001
        log.exception("Abaenderungsvertrag %s: automatischer Versand nach der Unterschrift fehlgeschlagen", aid)


async def abaenderungen_entfernen(datenbank, contract_id: str) -> int:
    """Vertragsloeschung (cleanup_service): Abaenderungsvertraege samt Unterschrift gehen mit dem Vertrag."""
    r = await datenbank.vertrag_abaenderungen.delete_many({"contract_id": contract_id})
    return int(getattr(r, "deleted_count", 0) or 0)
