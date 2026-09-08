"""Contract endpoints: preview, create, list, get, pdf, send, delete."""
import os
import asyncio
import base64
import logging
import re
import uuid
from datetime import datetime, timedelta, timezone
from typing import Annotated, Any, Dict, List, Optional, Union

from fastapi import APIRouter, Depends, HTTPException, Query, Response
from pydantic import BaseModel, ConfigDict, Field, field_validator

log = logging.getLogger("autohandel")


def _safe_filename(name: str, fallback: str = "document.pdf") -> str:
    """Strip characters that could inject extra HTTP header lines or break parsers.

    Removes control characters (CR, LF, TAB), double-quotes, and backslashes,
    then trims whitespace and limits length.
    """
    safe = re.sub(r'[\r\n\t"\\]', "", name)  # Remove newlines / quotes / backslash
    safe = safe.strip()
    return safe[:200] or fallback

from pymongo.errors import DuplicateKeyError

from deps import (
    TERMIN_OFFEN_WERTE, current_firma, fahrzeug_bereich,
    clean_doc, db, log_activity, log_activity_sicher, now_iso,
    require_active_sub, datum_iso_pruefen, uhrzeit_hhmm_pruefen,
)
import auto_daten
from cleanup_service import vertrag_endgueltig_loeschen
from lifecycle import try_set_lifecycle
from pdf_service import generate_contract_pdf

router = APIRouter()


# ---------- Models ----------
class DamageIn(BaseModel):
    """Ein Eintrag der Schadens-Skizze (DamageSelector.jsx).

    Nachpruefung Runde 14: `damages` war eine ungepruefte `list` — ein
    String-Element liess pdf_service (`d.get(...)`) mit 500 abstuerzen, und
    Anzahl/Laenge waren unbegrenzt (3000 Eintraege mit 200k-Zeichen-Zone
    wanderten komplett in contract_data). Jetzt: nur Objekte, nur die
    Schluessel, die Skizze/PDF/auto_daten/Abholprotokoll tatsaechlich lesen
    (type_label, type_key, zone; id/view/abbr/color/x/y fuer die Anzeige),
    Strings gedeckelt wie die uebrigen Tabellenfelder. Unbekannte Schluessel
    werden ignoriert, nicht abgelehnt (aeltere Oberflaechen)."""
    model_config = ConfigDict(extra="ignore", coerce_numbers_to_str=True)

    id: Optional[str] = Field(default=None, max_length=500)
    view: Optional[str] = Field(default=None, max_length=500)
    type_key: Optional[str] = Field(default="", max_length=500)
    type_label: Optional[str] = Field(default="", max_length=500)
    # Aeltere Oberflaechen/Tests schicken Freitext-Eintraege mit diesen
    # Schluesseln; auto_daten.schaeden_bereinigen liest sie weiterhin.
    label: Optional[str] = Field(default=None, max_length=500)
    type: Optional[str] = Field(default=None, max_length=500)
    kategorie: Optional[str] = Field(default=None, max_length=500)
    part_label: Optional[str] = Field(default=None, max_length=500)
    part: Optional[str] = Field(default=None, max_length=500)
    note: Optional[str] = Field(default=None, max_length=500)
    text: Optional[str] = Field(default=None, max_length=500)
    abbr: Optional[str] = Field(default=None, max_length=500)
    color: Optional[str] = Field(default=None, max_length=500)
    zone: Optional[str] = Field(default="", max_length=500)
    # Runde 17 (Nr. 338): Skizzen-Koordinaten sind Prozent-/Pixelwerte —
    # inf/nan und Riesenzahlen (JSON-Serialisierung, ReportLab-Layout)
    # werden abgelehnt statt bis ins PDF durchgereicht.
    x: Optional[float] = Field(default=None, allow_inf_nan=False, ge=-10000, le=10000)
    y: Optional[float] = Field(default=None, allow_inf_nan=False, ge=-10000, le=10000)


class ContractIn(BaseModel):
    # Numbers from listings (e.g. mileage, power_kw, doors, seats) arrive
    # as JSON numbers from the frontend. Coerce them to strings instead
    # of failing the request with "Input should be a valid string".
    model_config = ConfigDict(coerce_numbers_to_str=True)

    vehicle_id: str
    seller_name: str
    seller_address: Optional[str] = ""
    # Runde 17 (Nr. 347): PLZ/Ort landen in der Abholadresse des Termins —
    # realistische Deckel statt der allgemeinen 500 Zeichen.
    seller_zip: Optional[str] = Field(default="", max_length=20)
    seller_city: Optional[str] = Field(default="", max_length=100)
    seller_phone: Optional[str] = ""
    seller_email: Optional[str] = ""
    id_document: Optional[str] = ""
    # Runde 17 (Nr. 338): inf/nan sind kein Kaufpreis (auto_daten rechnet
    # Cent daraus, das PDF druckt ihn).
    purchase_price: float = Field(ge=0, allow_inf_nan=False,
                                  description="Kaufpreis darf nicht negativ sein")
    payment_method: Optional[str] = "Bar / Überweisung"
    pickup_date: Optional[str] = ""
    pickup_time: Optional[str] = ""

    # Runde 17 (Nr. 346): Der Vertragsweg legte den Abholtermin bisher
    # UNGEPRUEFT an (der Terminplaner prueft laengst) — Datum als
    # JJJJ-MM-TT, Uhrzeit als HH:MM, leer bleibt leer. Regel zentral in deps.
    @field_validator("pickup_date")
    @classmethod
    def _pickup_date_pruefen(cls, v):
        return datum_iso_pruefen(v)

    @field_validator("pickup_time")
    @classmethod
    def _pickup_time_pruefen(cls, v):
        return uhrzeit_hhmm_pruefen(v)

    additional_terms: Optional[str] = ""
    notes: Optional[str] = ""
    # Zusicherungen & Zustand (manuell durch Händler ergänzbar)
    tires: Optional[str] = ""              # "4-fach" | "8-fach" | "keine" | ""
    hu_valid: Optional[str] = ""           # "Ja" | "Nein" | ""
    hu_until: Optional[str] = ""           # MM/JJJJ frei
    accident_free: Optional[str] = ""      # "Ja" | "Nein" | ""
    accident_location: Optional[str] = ""  # nur wenn accident_free == "Nein"
    eu_import: Optional[str] = ""          # "Ja" | "Nein" | ""
    drivable: Optional[str] = ""           # "Ja" | "Nein" | ""
    commercial_since_ez: Optional[str] = ""  # "Ja" | "Nein" | ""
    previous_owners: Optional[str] = ""    # vom Händler manuell eingegeben (Anzahl)
    # Auto-prefilled from listing description but editable per contract.
    vehicle_description: Optional[str] = ""
    # Optional override for the dealer's default AGB block. If empty,
    # the dealer's saved AGB are used (current behaviour).
    agb_text: Optional[str] = ""
    # Schäden / Beschädigungen aus der interaktiven Skizze
    damages_text: Optional[str] = ""
    # Nachpruefung Runde 14: geprueftes Schema statt roher Liste — siehe
    # DamageIn. Erlaubt sind Skizzen-Eintraege (DamageIn) ODER reiner
    # Freitext je Schaden (beides liest auto_daten.schaeden_bereinigen, das
    # selbst auf 300 Zeichen kuerzt). Deckel: 200 Eintraege, Freitext 5000
    # Zeichen (Validator unten) — reine DoS-Bremse, bestehende Ablaeufe
    # schicken bis zu ~80 Eintraege mit bis zu 1000 Zeichen.
    damages: Optional[List[Union[DamageIn, str]]] = Field(default_factory=list, max_length=200)

    @field_validator("damages")
    @classmethod
    def _schaeden_freitext_deckeln(cls, v):
        for eintrag in v or []:
            if isinstance(eintrag, str) and len(eintrag) > 5000:
                # auto_daten kuerzt selbst auf 300 Zeichen; hier nur die DoS-Bremse
                raise ValueError("Schaden-Text zu lang (hoechstens 5000 Zeichen)")
        return v
    # Gewerblicher Verkauf: MwSt (19 %) im Vertrag ausweisen —
    # der Kaufpreis gilt dann als Brutto, das PDF rechnet Netto/MwSt aus.
    show_vat: Optional[bool] = False
    # Fahrzeugdaten — werden im Vertrags-Dialog editierbar vorbefüllt.
    # Wenn ein Feld leer ist, fällt das PDF auf den Wert aus dem
    # Vehicle-Dokument zurück, sodass alte Verträge weiter funktionieren.
    vehicle_make: Optional[str] = None
    vehicle_model: Optional[str] = None
    vehicle_category: Optional[str] = None
    vehicle_first_registration: Optional[str] = None
    vehicle_mileage: Optional[str] = None
    vehicle_fuel: Optional[str] = None
    vehicle_gearbox: Optional[str] = None
    vehicle_power_kw: Optional[str] = None
    vehicle_power_ps: Optional[str] = None
    vehicle_displacement: Optional[str] = None
    vehicle_color: Optional[str] = None
    vehicle_doors: Optional[str] = None
    vehicle_seats: Optional[str] = None
    vehicle_vin: Optional[str] = None
    vehicle_license_plate: Optional[str] = None
    vehicle_damage_note: Optional[str] = None
    # Händler-Override pro Vertrag (z.B. abweichende Telefonnummer)
    dealer_company: Optional[str] = None
    dealer_contact: Optional[str] = None
    dealer_phone: Optional[str] = None
    dealer_whatsapp: Optional[str] = None
    dealer_email: Optional[str] = None
    dealer_address: Optional[str] = None
    dealer_zip: Optional[str] = None
    dealer_city: Optional[str] = None

    # DoS- & Layout-Schutz: ReportLab braucht fuer riesige Strings extrem viel
    # CPU (2 MB seller_name = ~28 s + HTTP 500) und kann lange Strings in engen
    # Tabellenzellen gar nicht setzen (Flowable-too-large -> 500).
    # Gestaffelte Caps, sofort bei der Validierung (422):
    #   - Freitext-Bloecke (volle Breite, fliessen ueber Seiten): 20.000 Zeichen
    #   - alle uebrigen Felder (enge Tabellenzellen): 500 Zeichen
    @field_validator("*")
    @classmethod
    def _cap_string_length(cls, v, info):
        if isinstance(v, str):
            long_fields = {
                "additional_terms", "notes", "agb_text",
                "vehicle_description", "damages_text",
            }
            limit = 20000 if info.field_name in long_fields else 500
            if len(v) > limit:
                raise ValueError(
                    f"Feld '{info.field_name}' zu lang (max. {limit} Zeichen)"
                )
        return v


class SendIn(BaseModel):
    channel: str  # "whatsapp" | "email"
    recipient: str = Field(max_length=200)
    subject: Optional[str] = Field(default=None, max_length=500)
    message: str = Field(max_length=20000)
    # Doppelversand-Schutz: gleicher Schluessel -> garantiert nur EIN
    # Eintrag, auch bei Doppelklick, Netz-Wiederholung oder verlorener
    # Antwort. Das Frontend erzeugt je Klick eine UUID.
    idempotency_key: Optional[str] = Field(default=None, max_length=100)


# ---------- Helpers ----------
def _apply_contract_overrides(*, contract: dict, vehicle: dict, dealer: dict) -> tuple[dict, dict]:
    """Mergt die im Vertrags-Dialog editierten Fahrzeug- & Händler-Werte
    in die `vehicle`/`dealer`-Dicts hinein, die der PDF-Builder dann nutzt.
    So bleibt der bestehende PDF-Code unverändert.

    Werte werden NUR überschrieben, wenn der Händler im Dialog tatsächlich
    etwas eingetragen hat (nicht None und nicht leer-string)."""
    v = dict(vehicle or {})
    d = dict(dealer or {})

    def take(src_key: str) -> Optional[str]:
        val = contract.get(src_key)
        if val is None:
            return None
        s = str(val).strip()
        return s or None

    # --- Fahrzeug-Mappings (Override → Vehicle-Dict) ---
    veh_map = {
        "vehicle_make": ("make_label", "make"),
        "vehicle_model": ("model_description", "model_label", "model"),
        "vehicle_category": ("category_label", "category"),
        "vehicle_first_registration": ("first_registration", "ezl"),
        "vehicle_mileage": ("mileage", "km"),
        "vehicle_fuel": ("fuel_label", "fuel_type", "fuel"),
        "vehicle_gearbox": ("gearbox_label", "transmission", "gearbox"),
        "vehicle_power_kw": ("power_kw",),
        "vehicle_power_ps": ("power_ps",),
        "vehicle_displacement": ("displacement", "cubic_capacity"),
        "vehicle_color": ("exterior_color", "color"),
        "vehicle_doors": ("door_count", "doors"),
        "vehicle_seats": ("seat_count", "seats"),
        "vehicle_vin": ("vin", "fin"),
        "vehicle_license_plate": ("license_plate", "kennzeichen"),
        "vehicle_damage_note": ("damage_note",),
    }
    for src, targets in veh_map.items():
        val = take(src)
        if val is None:
            continue
        for t in targets:
            v[t] = val

    # --- Dealer-Mappings (Override → Dealer-Dict) ---
    deal_map = {
        "dealer_company": ("company_name",),
        "dealer_contact": ("contact_person",),
        "dealer_phone": ("phone",),
        "dealer_whatsapp": ("whatsapp_number",),
        "dealer_email": ("email",),
        "dealer_address": ("address",),
        "dealer_zip": ("zip_code",),
        "dealer_city": ("city",),
    }
    for src, targets in deal_map.items():
        val = take(src)
        if val is None:
            continue
        for t in targets:
            d[t] = val

    return v, d


def _vehicle_bild_urls(vehicle: dict) -> list:
    """Foto-URLs eines Fahrzeugs — ausgelesene Inserate speichern sie je
    nach Quelle unter `images` (Kleinanzeigen-Scraper) oder `image_urls`."""
    urls = vehicle.get("image_urls") or vehicle.get("images") or []
    return [u for u in urls if isinstance(u, str) and u.startswith("http")]


# ---------- Endpoints ----------
@router.post("/contracts/preview")
async def preview_contract(body: ContractIn, user=Depends(require_active_sub)):
    """Generate a draft Kaufvertrag PDF without persisting anything.
    Returns the PDF inline so the dealer can review it before final save."""
    # Runde 16: Sucher nur eigene Fahrzeuge (owner_user_id).
    v = await db.vehicles.find_one(
        {"id": body.vehicle_id, **fahrzeug_bereich(user)}, {"_id": 0},
    )
    if not v:
        raise HTTPException(404, "Fahrzeug nicht gefunden")
    from deps import effective_dealer
    dealer = await effective_dealer(user) or {}
    vehicle = v["data"]
    contract_dict = body.model_dump()
    if not (contract_dict.get("additional_terms") or "").strip():
        contract_dict["additional_terms"] = dealer.get("default_special_agreements", "") or ""
    # AGB: only fall back to dealer default if no override was provided.
    if not (contract_dict.get("agb_text") or "").strip():
        contract_dict["agb_text"] = dealer.get("default_terms", "") or ""
    # Vehicle description: pre-fill from the scraped listing if the user
    # didn't paste/override anything. Lets the description appear in the
    # PDF without an extra step.
    if not (contract_dict.get("vehicle_description") or "").strip():
        contract_dict["vehicle_description"] = vehicle.get("description", "") or ""
    vehicle, dealer = _apply_contract_overrides(
        contract=contract_dict, vehicle=vehicle, dealer=dealer,
    )
    # ReportLab ist CPU-gebunden -> in Thread auslagern, damit der
    # Event-Loop unter Last (200-500 Nutzer) nicht blockiert.
    # try/except: ein Layout-Fehler (z.B. pathologische Eingabe) wird zu
    # einem sauberen 400 statt einem unhandled 500.
    try:
        pdf_bytes = await asyncio.to_thread(
            generate_contract_pdf,
            dealer=dealer, vehicle=vehicle, contract=contract_dict,
        )
    except Exception:
        raise HTTPException(400, "PDF konnte mit diesen Eingaben nicht erzeugt werden.")
    return Response(
        content=pdf_bytes,
        media_type="application/pdf",
        headers={
            "Content-Disposition": "inline; filename=\"Kaufvertrag_Vorschau.pdf\"",
            "Cache-Control": "no-store",
        },
    )


@router.post("/contracts")
async def create_contract(body: ContractIn, user=Depends(require_active_sub)):
    # Runde 16: Sucher nur eigene Fahrzeuge (owner_user_id).
    v = await db.vehicles.find_one({"id": body.vehicle_id, **fahrzeug_bereich(user)}, {"_id": 0})
    if not v:
        raise HTTPException(404, "Fahrzeug nicht gefunden")
    # Runde 17 (Nr. 270): Kein neuer Kaufvertrag fuer ein Fahrzeug, das
    # bereits verkauft, geloescht oder archiviert ist — vorher entstand ein
    # Vertrag samt Auto-Datensatz, der Lebenszyklus blieb stumm stehen.
    # ("geloescht" faengt fahrzeug_bereich schon als 404 ab; die Pruefung
    # bleibt als zweite Sicherung, falls sich der Bereich einmal aendert.)
    if (v.get("lifecycle") or "") in {"verkauft", "geloescht", "archiviert"}:
        raise HTTPException(409, "Fahrzeug ist bereits verkauft/gelöscht/archiviert "
                                 "— kein neuer Kaufvertrag möglich")
    from deps import effective_dealer
    dealer = await effective_dealer(user) or {}
    vehicle = v["data"]
    # Apply dealer defaults if the form didn't override them. Both
    # special_agreements and agb_text now support a per-contract override
    # (otherwise we still fall back to the dealer's saved defaults).
    contract_dict = body.model_dump()
    if not (contract_dict.get("additional_terms") or "").strip():
        contract_dict["additional_terms"] = dealer.get("default_special_agreements", "") or ""
    if not (contract_dict.get("agb_text") or "").strip():
        contract_dict["agb_text"] = dealer.get("default_terms", "") or ""
    if not (contract_dict.get("vehicle_description") or "").strip():
        contract_dict["vehicle_description"] = vehicle.get("description", "") or ""
    vehicle, dealer = _apply_contract_overrides(
        contract=contract_dict, vehicle=vehicle, dealer=dealer,
    )
    # Vertragsnummer VOR der PDF-Erzeugung festlegen, damit sie im Dokument
    # (Kopf + Fußzeile) erscheint und im Archiv wiederauffindbar ist.
    pdf_id = str(uuid.uuid4())
    contract_no = f"KV-{datetime.now().strftime('%Y%m%d')}-{pdf_id[:6].upper()}"
    contract_dict["contract_no"] = contract_no
    # ReportLab ist CPU-gebunden -> in Thread auslagern, damit der
    # Event-Loop unter Last (200-500 Nutzer) nicht blockiert.
    # try/except: ein Layout-Fehler (z.B. pathologische Eingabe) wird zu
    # einem sauberen 400 statt einem unhandled 500.
    try:
        pdf_bytes = await asyncio.to_thread(
            generate_contract_pdf,
            dealer=dealer, vehicle=vehicle, contract=contract_dict,
        )
    except Exception:
        raise HTTPException(400, "PDF konnte mit diesen Eingaben nicht erzeugt werden.")
    pdf_b64 = base64.b64encode(pdf_bytes).decode()
    # Snapshot vehicle photo URLs at the moment the contract was created.
    # This way the dealer can still see the listing photos retrospectively
    # next to the contract PDF + Beweis-Archiv even if the original ad
    # is deleted by the seller.
    vehicle_image_urls = _vehicle_bild_urls(vehicle)
    doc = {
        "id": pdf_id, "contract_no": contract_no,
        "dealer_id": user["dealer_id"], "user_id": user["id"],
        "vehicle_id": body.vehicle_id, "mobile_ad_id": v.get("mobile_ad_id"),
        "make": vehicle.get("make_label") or vehicle.get("make"),
        "model": vehicle.get("model_description") or vehicle.get("model_label"),
        "seller_name": body.seller_name,
        "seller_phone": body.seller_phone,
        "seller_email": body.seller_email,
        "pickup_date": body.pickup_date,
        "pickup_time": body.pickup_time,
        "purchase_price": body.purchase_price,
        "contract_data": contract_dict,
        "pdf_b64": pdf_b64,
        "vehicle_image_urls": vehicle_image_urls,
        "filename": f"Kaufvertrag_{vehicle.get('make_label','')}_{vehicle.get('model_label','')}_{datetime.now().strftime('%Y%m%d')}.pdf",
        "send_status": [],
        "status": "erstellt",
        "appointment_id": None,
        "created_at": now_iso(),
        "updated_at": now_iso(),
    }
    # Dauerhafte, anonyme Auto-Daten (siehe auto_daten.py): ZUERST der
    # Datensatz, dann der Vertrag mit dessen zufaelliger id. Scheitert der
    # Vertrags-Insert, wird der Datensatz sofort wieder entfernt — es gibt
    # nie einen Vertrag ohne Auto-Daten und keinen Datensatz ohne Vertrag.
    auto_daten_id = await auto_daten.anlegen(db, contract_dict, vehicle)
    doc["admin_vehicle_data_id"] = auto_daten_id
    try:
        await db.generated_pdfs.insert_one(doc)
    except Exception:
        await auto_daten.zurueckrollen(db, auto_daten_id)
        raise
    # Runde 17 (Nr. 265): Ab hier ist der Vertrag dauerhaft. Scheitert das
    # Nachziehen von Fahrzeugstatus/Lebenszyklus (DB-Aussetzer), endete der
    # Request bisher mit 500 — der Client wiederholte und legte einen
    # ZWEITEN Vertrag an. Jetzt wie beim Termin-Block: Fehler ins Log, die
    # Antwort traegt einen Nacharbeit-Hinweis, der Vertrag bleibt gueltig.
    nacharbeit_hinweis = None
    try:
        await db.vehicles.update_one(
            {"id": body.vehicle_id, "dealer_id": user["dealer_id"]},
            {"$set": {"status": "Vertrag erstellt", "purchase_price": body.purchase_price}},
        )
        # Lebenszyklus: Vertrag erstellt → gekauft (Kaufpreis liegt vor).
        await try_set_lifecycle(body.vehicle_id, user["dealer_id"], "vertrag_erstellt", user=user)
        await try_set_lifecycle(body.vehicle_id, user["dealer_id"], "gekauft", user=user)
    except Exception:
        log.exception("Fahrzeugstatus nach Vertrag %s konnte nicht aktualisiert werden", pdf_id)
        nacharbeit_hinweis = ("Vertrag gespeichert; Fahrzeugstatus/Protokoll konnten "
                              "nicht aktualisiert werden.")
    # Audit wirft nie (log_activity_sicher) — der Vertrag steht bereits.
    if not await log_activity_sicher(user["dealer_id"], user["id"], "pdf.erstellt", ref=pdf_id):
        nacharbeit_hinweis = nacharbeit_hinweis or (
            "Vertrag gespeichert; Fahrzeugstatus/Protokoll konnten nicht aktualisiert werden.")

    # Abholtermin automatisch anlegen, wenn ein Abholdatum angegeben wurde,
    # damit der Vertrag im Terminplaner erscheint.
    # Runde 15 (Nr. 5): Der Vertrag ist gespeichert — scheitert der Termin,
    # bleibt der Vertrag gueltig und die Antwort traegt einen Hinweis; vorher
    # brach der Request mit 500 ab, und ein Wiederholen legte einen ZWEITEN
    # Vertrag an.
    termin_hinweis = None
    if body.pickup_date:
        try:
            appt_id, termin_hinweis = await _abholtermin_fuer_vertrag(
                user, body, vehicle, pdf_id)
        except Exception:
            log.exception("Auto-Termin fuer Vertrag %s fehlgeschlagen", pdf_id)
            appt_id = None
            termin_hinweis = ("Der Vertrag ist gespeichert, der Abholtermin konnte "
                              "aber nicht angelegt werden — bitte im Terminplaner "
                              "von Hand anlegen.")
        if appt_id:
            doc["appointment_id"] = appt_id
            doc["status"] = "Termin erstellt"

    out = {**clean_doc(doc), "pdf_b64": pdf_b64}
    if termin_hinweis:
        out["termin_hinweis"] = termin_hinweis
    if nacharbeit_hinweis:
        out["nacharbeit_hinweis"] = nacharbeit_hinweis
    return out


def _zusage_zuruecksetzen_fallback(existing: dict, neu: dict) -> tuple[dict, dict]:
    """Runde 17 (Nr. 355): Ersatz, falls routes.appointments den Helfer
    `zusage_zuruecksetzen_wenn_geaendert` (noch) nicht anbietet — dieselbe
    Regel wie in update_appointment: hat der Fahrer die Fahrt angenommen und
    aendern sich Datum, Uhrzeit oder Abholadresse, gilt die Zusage nicht
    mehr. Liefert ($set-Felder, $unset-Felder)."""
    if existing.get("zuteilung") != "angenommen":
        return {}, {}
    geaendert = any(
        f in neu and (neu.get(f) or "") != (existing.get(f) or "")
        for f in ("pickup_date", "pickup_time", "pickup_address"))
    if not geaendert:
        return {}, {}
    return ({"zuteilung": "offen", "zuteilung_am": now_iso(),
             "zuteilung_neu_wegen_aenderung": True},
            {"zuteilung_beantwortet_am": ""})


async def _termin_gehoert_mir(user: dict, appt: dict) -> bool:
    """Chef immer; Sucher nur, wenn er den Termin angelegt hat oder der
    verknuepfte Vertrag ihm gehoert (NICHT schon, weil ihm das Fahrzeug
    gehoert — sonst uebernaehme ein Mitbearbeiter den Termin des Kollegen)."""
    if user.get("role") != "sucher":
        return True
    if appt.get("created_by") == user["id"]:
        return True
    cid = appt.get("contract_id")
    return bool(cid) and await db.generated_pdfs.count_documents(
        {"id": cid, "user_id": user["id"]}, limit=1) > 0


async def _abholtermin_fuer_vertrag(user: dict, body, vehicle: dict, pdf_id: str):
    """Runde 15 (Nr. 6): hoechstens EIN offener Abholtermin je Fahrzeug.

    Die alte Dubletten-Pruefung suchte nach der soeben erzeugten contract_id
    und fand deshalb nie etwas — zwei Vertraege fuer dasselbe Auto (Doppel-
    klick, Korrektur, zwei Sucher) ergaben zwei Termine. Jetzt: existiert
    ein offener Termin zum Fahrzeug, wird er auf den neuen Vertrag
    umgehaengt (Datum/Uhrzeit/Verkaeufer aus dem neuen Vertrag, Fahrer
    bleibt); der alte Vertrag verliert den Verweis. Gehoert der Termin einem
    Kollegen (Sucher-Bereich), bleibt er unangetastet und der Vertrag
    bekommt nur einen Hinweis. Das Rennen zweier gleichzeitiger Anlagen
    faengt der Teil-Unique-Index termin_offen_je_fahrzeug (server.py).
    Liefert (appointment_id | None, hinweis | None)."""
    from routes.appointments import _sucher_darf
    # Runde 17 (Nr. 355): Zusage-Regel aus dem Terminplaner — lazy, weil
    # routes.appointments seinerseits routes.contracts importiert; fehlt der
    # Helfer (aelterer Stand), greift die gleichlautende Regel hier.
    try:
        from routes.appointments import zusage_zuruecksetzen_wenn_geaendert as _zusage_reset
    except (ImportError, AttributeError):
        _zusage_reset = _zusage_zuruecksetzen_fallback
    dealer_id = user["dealer_id"]
    # Runde 17 (Nr. 347): Abholadresse wie ein Terminfeld deckeln (500).
    pickup_address = " ".join([
        body.seller_address or "", body.seller_zip or "", body.seller_city or "",
    ]).strip()[:500]
    felder = {"seller_name": body.seller_name, "seller_phone": body.seller_phone,
              "seller_email": body.seller_email, "pickup_address": pickup_address,
              "pickup_date": body.pickup_date, "pickup_time": body.pickup_time or ""}
    for versuch in (1, 2):
        offen = await db.appointments.find_one(
            {"dealer_id": dealer_id, "vehicle_id": body.vehicle_id,
             "status": {"$in": TERMIN_OFFEN_WERTE}},
            # Runde 17 (Nr. 355): Fahrer/Zusage und den bisherigen Termin
            # mitlesen — beim Umhaengen mit neuem Datum/Uhrzeit/Adresse muss
            # eine angenommene Zusage zurueck auf "offen" (vorher fuhr der
            # Fahrer mit alter Zusage zum neuen Termin). vehicle_id braucht
            # _sucher_darf (Fahrzeug-Besitzer, Runde 16).
            {"_id": 0, "id": 1, "contract_id": 1, "created_by": 1, "vehicle_id": 1,
             "driver_id": 1, "zuteilung": 1, "pickup_date": 1, "pickup_time": 1,
             "pickup_address": 1})
        if offen:
            # Wunsch Ahmad 09.09.2026: Mitbearbeiter sehen den Termin des
            # Kollegen (Fahrzeug-Anker), duerfen ihn aber NICHT auf ihren
            # Vertrag umhaengen — nur der eigene Termin (selbst angelegt oder
            # eigener Vertrag) oder der Chef.
            if not await _termin_gehoert_mir(user, offen):
                return None, ("Für dieses Fahrzeug besteht bereits ein offener "
                              "Abholtermin eines Kollegen — der Vertrag wurde ohne "
                              "eigenen Termin gespeichert.")
            zusage_set, zusage_unset = _zusage_reset(offen, felder)
            umhaengen: Dict[str, Any] = {"$set": {
                **felder, "contract_id": pdf_id, "updated_at": now_iso(), **zusage_set}}
            if zusage_unset:
                umhaengen["$unset"] = dict(zusage_unset)
            await db.appointments.update_one(
                {"id": offen["id"], "dealer_id": dealer_id}, umhaengen)
            alt = offen.get("contract_id")
            if alt and alt != pdf_id:
                # Runde 17 (Nr. 356): Der alte Vertrag verliert den Termin UND
                # den Status "Termin erstellt" — er zeigte sonst dauerhaft
                # einen Termin an, der laengst am neuen Vertrag haengt.
                res = await db.generated_pdfs.update_one(
                    {"id": alt, "appointment_id": offen["id"], "status": "Termin erstellt"},
                    {"$set": {"appointment_id": None, "status": "erstellt",
                              "updated_at": now_iso()}})
                if res.matched_count == 0:
                    await db.generated_pdfs.update_one(
                        {"id": alt, "appointment_id": offen["id"]},
                        {"$set": {"appointment_id": None, "updated_at": now_iso()}})
            appt_id = offen["id"]
            aktion = "termin.auto-umgehaengt"
            break
        appt_id = str(uuid.uuid4())
        title = (f"{vehicle.get('make_label','')} {vehicle.get('model_label','')} abholen".strip()
                 or "Fahrzeug abholen")
        try:
            await db.appointments.insert_one({
                "id": appt_id, "dealer_id": dealer_id,
                "created_by": user["id"],     # Runde 10: sonst kann der Sucher ihn nie loeschen
                "title": title, "vehicle_id": body.vehicle_id, "contract_id": pdf_id,
                **felder, "status": "offen",
                "created_at": now_iso(), "updated_at": now_iso(),
            })
            aktion = "termin.auto-erstellt"
            break
        except DuplicateKeyError:
            if versuch == 2:
                raise
            continue                        # paralleler Termin gewann -> umhaengen
    await db.generated_pdfs.update_one(
        {"id": pdf_id},
        {"$set": {"appointment_id": appt_id, "status": "Termin erstellt"}},
    )
    await try_set_lifecycle(body.vehicle_id, dealer_id, "abholung_geplant", user=user)
    await log_activity(dealer_id, user["id"], aktion, ref=appt_id,
                       meta={"contract_id": pdf_id, "vehicle_id": body.vehicle_id})
    return appt_id, None


# Nach so vielen Sekunden gilt eine Zustellung "laeuft" als abgebrochen.
# Ein Versand dauert Sekunden; drei Minuten sind grosszuegig.
ZUSTELLUNG_HAENGT_NACH_SEK = int(os.environ.get("ZUSTELLUNG_HAENGT_NACH_SEK", "180"))


# Nachpruefung Runde 14: send_status waechst je Versand um einen Eintrag,
# ohne Obergrenze — bei mutwilligem Dauerversand (je Klick ein neuer
# Schluessel, kein Limiter) naeherte sich das Vertragsdokument samt pdf_b64
# der 16-MB-Grenze, und jede Vertragsliste lieferte die ganze Historie mit.
# $slice -N behaelt die juengsten N Eintraege (aelteste fallen raus); der
# frisch angehaengte Eintrag ist immer der letzte, das positionale $set und
# die Idempotenz-/Wiederaufnahme-Suche finden ihn weiter. Die vollstaendige
# Historie steht ohnehin in activity_logs (pdf.gesendet.<channel>).
SEND_STATUS_MAX = 200
# Runde 17 (Nr. 321): pickup_history (Terminverschiebungen je Vertrag)
# ebenso gedeckelt — juengste 100 Eintraege; die Vorversionen liegen
# ohnehin in generated_pdf_versions, das Audit in activity_logs.
PICKUP_HISTORY_MAX = 100


def _zustellung_haengt(eintrag: dict, jetzt=None) -> bool:
    """True, wenn eine Reservierung aelter ist als ZUSTELLUNG_HAENGT_NACH_SEK
    (oder schon als "unklar" markiert wurde)."""
    if eintrag.get("zustellung") == "unklar":
        return True
    try:
        # Nachpruefung Runde 10: Ein frischer Wiederaufnahme-Claim zaehlt wie
        # ein frischer Erstversand — sonst nehmen zehn gleichzeitige Klicks
        # denselben Eintrag alle wieder auf.
        start = datetime.fromisoformat(str(eintrag.get("wiederaufnahme_am")
                                           or eintrag.get("sent_at") or ""))
    except ValueError:
        return True
    if start.tzinfo is None:
        start = start.replace(tzinfo=timezone.utc)
    jetzt = jetzt or datetime.now(timezone.utc)
    return (jetzt - start).total_seconds() > ZUSTELLUNG_HAENGT_NACH_SEK


def _vertrag_bereich(user) -> Dict[str, Any]:
    """Welche Vertraege darf dieses Konto sehen?

    Betreiber-Entscheidung 09/2026: Der Chef sieht alle Vertraege seiner
    Firma, ein Sucher nur die, die er selbst angelegt hat. Vorher sah
    jeder Sucher die Verkaeuferdaten und PDFs seiner Kollegen — bei
    groesseren Haendlern weder gewollt noch datenschutzrechtlich sauber.

    Bewusst streng: fehlt einem alten Vertrag die Angabe, wer ihn angelegt
    hat, sieht ihn der Sucher NICHT (der Chef weiterhin schon). Lieber
    einmal zu wenig zeigen als fremde Verkaeuferdaten preisgeben.

    Runde 17 (Nr. 348): Vertraege mit Grabstein (`loeschung.status ==
    "laeuft"`, cleanup_service.vertrag_endgueltig_loeschen) sind NICHT mehr
    im Bereich — die Loeschung laeuft oder ist abgebrochen und wird vom
    Aufraeumjob zu Ende gefuehrt. Vorher waren solche Vertraege in Liste,
    Detail, PDF, Versionen und Versand weiter sichtbar/versendbar, und ein
    Termin liess sich noch daran haengen. Gilt fuer JEDEN Aufrufer
    (auch routes/appointments.py und routes/bestand.py importieren diesen
    Filter). delete_contract nutzt bewusst NUR dealer_id: das Loeschen
    bleibt idempotent und nimmt einen abgebrochenen Vorgang wieder auf."""
    bereich: Dict[str, Any] = {"dealer_id": user["dealer_id"],
                               "loeschung.status": {"$ne": "laeuft"}}
    if user.get("role") == "sucher":
        bereich["user_id"] = user["id"]
    return bereich


# Nachpruefung Runde 14: vorher stille 500 — bei mehr Vertraegen in 90 Tagen
# fehlten die aeltesten kommentarlos. Jetzt 2000, und ein Abschneiden wird
# per Kopfzeile X-Truncated signalisiert (Antwort bleibt eine Liste).
CONTRACTS_LIST_MAX = 2000


@router.get("/contracts")
async def list_contracts(
    response: Response,
    user=Depends(current_firma),
    # Runde 17 (Nr. 349/374): Suchtext und Zeitraum gedeckelt — vorher
    # liefen ein 1-MB-Regex (re.escape haelt ihn zwar harmlos, aber Mongo
    # musste ihn ueber jeden Vertrag ziehen) und days=10**9 (timedelta-
    # Ueberlauf -> 500) ungebremst durch. Annotated-Form, damit die Python-
    # Standardwerte None bleiben (In-Prozess-Aufrufer/Tests).
    q: Annotated[Optional[str], Query(max_length=200)] = None,
    days: Annotated[Optional[int], Query(ge=1, le=3650)] = None,
    channel: Optional[str] = None,
):
    query: Dict[str, Any] = _vertrag_bereich(user)
    if days:
        since = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()
        query["created_at"] = {"$gte": since}
    if q:
        # re.escape prevents ReDoS and NoSQL-regex injection via crafted patterns.
        q_safe = re.escape(q)
        query["$or"] = [
            {"make": {"$regex": q_safe, "$options": "i"}},
            {"model": {"$regex": q_safe, "$options": "i"}},
            {"seller_name": {"$regex": q_safe, "$options": "i"}},
        ]
    items = await db.generated_pdfs.find(
        query, {"_id": 0, "pdf_b64": 0},
    ).sort("created_at", -1).to_list(CONTRACTS_LIST_MAX + 1)
    abgeschnitten = len(items) > CONTRACTS_LIST_MAX
    items = items[:CONTRACTS_LIST_MAX]
    response.headers["X-Truncated"] = "1" if abgeschnitten else "0"
    if channel:
        items = [
            i for i in items
            if any(s.get("channel") == channel for s in i.get("send_status", []))
        ]
    # Alt-Vertraege ohne `vehicle_image_urls` (frueher lagen die Fotos beim
    # Fahrzeug unter `data.images`, nicht `image_urls`): die Fotos werden
    # NUR fuer die Anzeige aus dem Fahrzeug ergaenzt und als nachgetragen
    # gekennzeichnet (`bilder_nachgetragen`).
    # Nachpruefung Runde 14: vorher schrieb dieses GET den HEUTIGEN
    # Bilderstand dauerhaft an den Vertrag — ein Lesezugriff verewigte
    # Fotos, die beim Vertragsschluss womoeglich anders aussahen, als
    # unmarkierten Vertragsbeweis. Lesen bleibt Lesen; gespeichert wird der
    # Bilderstand nur noch beim Anlegen (create_contract).
    ohne_fotos = [i for i in items if not i.get("vehicle_image_urls") and i.get("vehicle_id")]
    if ohne_fotos:
        vids = list({i["vehicle_id"] for i in ohne_fotos})
        bilder = {}
        async for v in db.vehicles.find(
                {"id": {"$in": vids}, "dealer_id": user["dealer_id"]},
                {"_id": 0, "id": 1, "data.images": 1, "data.image_urls": 1}):
            urls = _vehicle_bild_urls(v.get("data") or {})
            if urls:
                bilder[v["id"]] = urls
        for i in ohne_fotos:
            urls = bilder.get(i["vehicle_id"])
            if urls:
                i["vehicle_image_urls"] = urls
                i["bilder_nachgetragen"] = True
    # Ersteller anreichern: der Chef sieht so, WELCHER Sucher den Vertrag
    # (= Einkauf) gemacht hat.
    creator_ids = list({i.get("user_id") for i in items if i.get("user_id")})
    if creator_ids:
        names = {}
        async for u in db.users.find({"id": {"$in": creator_ids}},
                                     {"_id": 0, "id": 1, "email": 1,
                                      "first_name": 1, "last_name": 1, "role": 1}):
            label = f"{u.get('first_name') or ''} {u.get('last_name') or ''}".strip() \
                    or u.get("email", "")
            names[u["id"]] = {"name": label, "role": u.get("role")}
        for i in items:
            c = names.get(i.get("user_id"))
            if c:
                i["created_by_name"] = c["name"]
                i["created_by_role"] = c["role"]
    return items


@router.get("/contracts/{contract_id}")
async def get_contract(contract_id: str, user=Depends(current_firma)):
    c = await db.generated_pdfs.find_one(
        {"id": contract_id, **_vertrag_bereich(user)}, {"_id": 0},
    )
    if not c:
        raise HTTPException(404, "Vertrag nicht gefunden")
    return c


@router.get("/contracts/{contract_id}/pdf")
async def get_contract_pdf(contract_id: str, user=Depends(current_firma)):
    c = await db.generated_pdfs.find_one(
        {"id": contract_id, **_vertrag_bereich(user)},
        {"_id": 0, "pdf_b64": 1, "filename": 1},
    )
    if not c:
        raise HTTPException(404, "Vertrag nicht gefunden")
    pdf_bytes = base64.b64decode(c["pdf_b64"])
    fname = _safe_filename(c.get("filename") or "", fallback="kaufvertrag.pdf")
    return Response(
        content=pdf_bytes,
        media_type="application/pdf",
        headers={"Content-Disposition": f'inline; filename="{fname}"'},
    )


@router.get("/contracts/{contract_id}/versions")
async def list_contract_versions(contract_id: str, user=Depends(current_firma)):
    """Archivierte Vertragsfassungen (ohne PDF-Inhalt, nur Metadaten)."""
    c = await db.generated_pdfs.find_one(
        {"id": contract_id, **_vertrag_bereich(user)}, {"_id": 0, "id": 1})
    if not c:
        raise HTTPException(404, "Vertrag nicht gefunden")
    return await db.generated_pdf_versions.find(
        {"contract_id": contract_id, "dealer_id": user["dealer_id"]},
        {"_id": 0, "pdf_b64": 0, "contract_data": 0},
    ).sort("version", 1).to_list(100)


@router.get("/contracts/{contract_id}/versions/{version}/pdf")
async def get_contract_version_pdf(contract_id: str, version: int,
                                   user=Depends(current_firma)):
    """Archivierte PDF-Fassung herunterladen (Beweissicherung)."""
    # Auch die alten Fassungen nur, wenn der Vertrag selbst sichtbar ist.
    haupt = await db.generated_pdfs.find_one(
        {"id": contract_id, **_vertrag_bereich(user)}, {"_id": 0, "id": 1})
    if not haupt:
        raise HTTPException(404, "Vertragsfassung nicht gefunden")
    v = await db.generated_pdf_versions.find_one(
        {"contract_id": contract_id, "dealer_id": user["dealer_id"],
         "version": version},
        {"_id": 0, "pdf_b64": 1, "filename": 1})
    if not v or not v.get("pdf_b64"):
        raise HTTPException(404, "Vertragsfassung nicht gefunden")
    pdf_bytes = base64.b64decode(v["pdf_b64"])
    fname = _safe_filename(v.get("filename") or "",
                           fallback=f"kaufvertrag-v{version}.pdf")
    return Response(
        content=pdf_bytes,
        media_type="application/pdf",
        headers={"Content-Disposition": f'inline; filename="{fname}"'},
    )


def _auto_schluessel(contract_id: str, c: dict, body) -> str:
    """Inhaltsschluessel fuer Aufrufer ohne eigenen Schluessel. Runde 10 nahm
    die Minute heraus (Doppelklick ueber die Minutengrenze); die Nachpruefung
    ergaenzt die Vertrags-VERSION: nach einer Neuerzeugung des PDFs
    (Terminverschiebung) ist derselbe Text ein neuer Versand — vorher hiess
    es fuer immer "bereits gesendet"."""
    import hashlib
    roh = "|".join([contract_id, str(c.get("version") or 1), body.channel,
                    body.recipient or "", body.subject or "", body.message or ""])
    return "auto-" + hashlib.sha256(roh.encode("utf-8")).hexdigest()[:24]


@router.post("/contracts/{contract_id}/send")
async def send_contract(contract_id: str, body: SendIn, user=Depends(require_active_sub)):
    # Pruefbericht Runde 8 (09/2026), hoher Befund: Lesen und PDF gingen
    # laengst ueber _vertrag_bereich, der VERSAND aber nur ueber die Firma.
    # Ein Sucher mit der Vertrags-ID eines Kollegen konnte dessen Vertrag
    # samt PDF an eine beliebige Adresse schicken und den Versandstatus
    # veraendern. Jetzt gilt beim Versand derselbe Bereich wie beim Lesen —
    # und JEDE Schreibabfrage in dieser Funktion nutzt ihn ebenfalls.
    bereich = _vertrag_bereich(user)
    c = await db.generated_pdfs.find_one({"id": contract_id, **bereich}, {"_id": 0})
    if not c:
        raise HTTPException(404, "Vertrag nicht gefunden")
    # Idempotenz RESERVIEREND (Review 09/2026): Der Schluessel wurde vorher
    # erst NACH dem Senden eingetragen — zwei gleichzeitige Anfragen mit
    # demselben Schluessel konnten beide zustellen. Jetzt wird der Eintrag
    # atomar VOR dem Versand angelegt; der Verlierer bekommt das Ergebnis
    # des Gewinners, bei Sendefehler wird der Eintrag wieder entfernt.
    # Pruefbericht Runde 8, Befund 3: Ohne Schluessel gab es keinerlei
    # Schutz — zwei identische Aufrufe stellten zweimal zu. Fehlt der
    # Schluessel, wird er jetzt aus dem Inhalt abgeleitet: dieselbe Mail an
    # denselben Empfaenger ist EIN Versand.
    # Runde 10: vorher steckte die Minute im Schluessel — ein Doppelklick um
    # 12:00:59 und 12:01:00 stellte zweimal zu. Jetzt zaehlt nur der Inhalt;
    # wer denselben Vertrag bewusst noch einmal schicken will, gibt eine
    # andere Nachricht oder einen eigenen Schluessel mit.
    if not body.idempotency_key:
        body.idempotency_key = _auto_schluessel(contract_id, c, body)
    reserviert = False
    wiederaufnahme = False
    reserviert_am = now_iso()
    claim_am = None
    if body.idempotency_key:
        eintraege = c.get("send_status") or []
        vorhanden = next((e for e in eintraege
                          if e.get("idempotency_key") == body.idempotency_key), None)
        if vorhanden is None:
            # Nachpruefung Runde 10: Die Oberflaeche schickt je Klick einen
            # NEUEN Schluessel. Haengt zu demselben Kanal und Empfaenger noch
            # ein Versand ohne Ergebnis (Prozess starb, Timeout), haette der
            # zweite Klick ein zweites Mal zugestellt. Er uebernimmt jetzt
            # den haengenden Eintrag — unter DESSEN Schluessel, damit auch
            # Resend nicht doppelt zustellt.
            haengend = next((e for e in eintraege
                             if e.get("idempotency_key")
                             and e.get("channel") == body.channel
                             and (e.get("recipient") or "") == (body.recipient or "")
                             and e.get("zustellung") in ("laeuft", "unklar")
                             and _zustellung_haengt(e)), None)
            if haengend:
                body.idempotency_key = haengend["idempotency_key"]
                vorhanden = haengend
        if (vorhanden and vorhanden.get("zustellung") in ("laeuft", "unklar")
                and _zustellung_haengt(vorhanden)):
            # Zeitpunkt des ersten Versuchs behalten: die Kopie an den Sucher
            # muss bei der Wiederaufnahme denselben Inhalt haben (Resend).
            reserviert_am = vorhanden.get("sent_at") or reserviert_am
            # Der Prozess ist zwischen Reservierung und Ergebnis gestorben.
            # Frueher hiess es hier dauerhaft "bereits gesendet" — obwohl
            # womoeglich nie etwas rausging. Jetzt wird der Versand unter
            # DEMSELBEN Schluessel wiederholt; Resend erkennt den Schluessel
            # und stellt nicht doppelt zu.
            # Nachpruefung Runde 10: Die Wiederaufnahme wird ATOMAR beansprucht
            # (Compare-and-Swap auf den gelesenen Stand). Von zehn gleich-
            # zeitigen Klicks gewinnt genau einer; die anderen bekommen
            # "laeuft / bereits_gesendet" — vorher stellten alle zu.
            claim_am = now_iso()
            res = await db.generated_pdfs.update_one(
                {"id": contract_id, **bereich,
                 "send_status": {"$elemMatch": {
                     "idempotency_key": body.idempotency_key,
                     "zustellung": {"$in": ["laeuft", "unklar"]},
                     "wiederaufnahme_am": vorhanden.get("wiederaufnahme_am")}}},
                {"$set": {"send_status.$.zustellung": "laeuft",
                          "send_status.$.wiederaufnahme_am": claim_am}})
            if res.modified_count == 0:
                return {"channel": vorhanden.get("channel"), "status": "ok",
                        "sent_at": vorhanden.get("sent_at"),
                        "zustellung": "laeuft", "bereits_gesendet": True}
            wiederaufnahme = True
            reserviert = True
            vorhanden = None
        if vorhanden:
            return {"channel": vorhanden.get("channel"), "status": "ok",
                    "sent_at": vorhanden.get("sent_at"),
                    "zustellung": vorhanden.get("zustellung", ""),
                    "wa_url": vorhanden.get("wa_url"), "bereits_gesendet": True}
    if body.idempotency_key and not wiederaufnahme:
        res = await db.generated_pdfs.update_one(
            {"id": contract_id, **bereich,
             "send_status.idempotency_key": {"$ne": body.idempotency_key}},
            {"$push": {"send_status": {"$each": [{
                "idempotency_key": body.idempotency_key, "channel": body.channel,
                "recipient": body.recipient, "subject": body.subject,
                "sent_at": reserviert_am, "zustellung": "laeuft"}],
                "$slice": -SEND_STATUS_MAX}}})
        if res.modified_count == 0:
            return {"channel": body.channel, "status": "ok", "sent_at": now_iso(),
                    "zustellung": "laeuft", "bereits_gesendet": True}
        reserviert = True

    async def _reservierung_zurueck():
        # Bei einer Wiederaufnahme bleibt der Eintrag stehen: er traegt
        # "unklar", damit der naechste Versuch wieder hier landet.
        if reserviert and wiederaufnahme:
            # nur den EIGENEN Claim zuruecksetzen — nie das Ergebnis eines
            # anderen Aufrufers ueberschreiben
            await db.generated_pdfs.update_one(
                {"id": contract_id, **bereich,
                 "send_status": {"$elemMatch": {"idempotency_key": body.idempotency_key,
                                                "wiederaufnahme_am": claim_am}}},
                {"$set": {"send_status.$.zustellung": "unklar"}})
            return
        if reserviert:
            await db.generated_pdfs.update_one(
                {"id": contract_id, **bereich},
                {"$pull": {"send_status": {"idempotency_key": body.idempotency_key,
                                           "zustellung": "laeuft"}}})
    # Runde 17 (Nr. 370): Zwischen dem Lesen oben und dem Versand kann die
    # Loeschung (Frist oder manuell) begonnen haben — der Grabstein nimmt
    # den Vertrag aus dem Bereich. Unmittelbar vor dem Versand noch einmal
    # nachsehen, sonst ginge ein PDF raus, dessen Vertrag gerade
    # verschwindet (und der Status-Vermerk liefe ins Leere).
    if not await db.generated_pdfs.find_one({"id": contract_id, **bereich}, {"_id": 1}):
        await _reservierung_zurueck()
        raise HTTPException(409, "Vertrag wird gerade gelöscht")
    # Ehrlicher Versand-Status (PR-Review 09/2026): "versendet" gibt es
    # NUR nach tatsaechlicher Zustellung an den Anbieter. WhatsApp oeffnet
    # lediglich den Chat (PDF haengt der Nutzer selbst an) -> der Vertrag
    # wird als "versand_vorbereitet" gefuehrt, nicht als versendet.
    out: dict = {"channel": body.channel, "status": "ok", "sent_at": now_iso()}
    if body.channel == "whatsapp":
        digits = "".join(ch for ch in (body.recipient or "") if ch.isdigit())
        from urllib.parse import quote_plus
        out["wa_url"] = f"https://wa.me/{digits}?text={quote_plus(body.message)}"
        out["zustellung"] = "chat_geoeffnet"
        neuer_status = "versand_vorbereitet"
    elif body.channel == "email":
        from provider_fetch import MOCK_PROVIDER_FETCH
        import email_service
        if MOCK_PROVIDER_FETCH:
            # Last-/CI-Tests: kein echter Versand, aber ehrlich markiert.
            out["zustellung"] = "mock"
            neuer_status = "versendet"
        elif not email_service.email_configured():
            await _reservierung_zurueck()
            raise HTTPException(503,
                "E-Mail-Versand ist nicht eingerichtet (RESEND_API_KEY oder "
                "SMTP_* in der .env setzen) — der Vertrag wurde NICHT "
                "versendet. Alternativ per WhatsApp teilen oder das PDF "
                "herunterladen.")
        else:
            # Versand IMMER von unserer eigenen Adresse (nur die ist beim
            # Mail-Anbieter freigeschaltet). Der Anzeigename nennt die Firma,
            # und die Antwortadresse ist der Sucher: antwortet der Verkaeufer,
            # landet die Antwort direkt bei ihm (Wunsch 09/2026).
            from vertrag_mail import kopie_mail, vertrag_mail
            pdf_bytes = base64.b64decode(c["pdf_b64"]) if c.get("pdf_b64") else None
            dateiname = c.get("filename") or "Kaufvertrag.pdf"
            # Nachpruefung Runde 14: dieselbe Firmenidentitaet wie im PDF —
            # effective_dealer legt die gewollten Sucher-Overrides (Firmen-
            # name, Telefon, Logo, E-Mail) ueber die Chef-Vorgaben. Vorher
            # las der Versand das rohe Haendler-Dokument, und Betreff, Kopf
            # und Absendername der Mail nannten den Chef statt der Filiale,
            # die auf dem angehaengten Vertrag steht.
            from deps import effective_dealer
            firma = await effective_dealer(user) or {}
            betreff, text, html = vertrag_mail(
                vertrag=c, firma=firma, sucher=user,
                nachricht=body.message, betreff=body.subject)
            sucher_mail = (user.get("email") or "").strip()
            ok, beleg = await email_service.send_email_mit_beleg(
                body.recipient, betreff, text, anhang=pdf_bytes,
                anhang_name=dateiname, html=html,
                reply_to=sucher_mail,
                absender_name=firma.get("company_name") or "",
                idempotency_key=f"vertrag-{contract_id}-{body.idempotency_key}")
            if ok and beleg:
                out["beleg"] = beleg
            if not ok:
                await _reservierung_zurueck()
                raise HTTPException(502, "E-Mail-Versand fehlgeschlagen — "
                                         "bitte in ein paar Minuten erneut "
                                         "versuchen. Der Vertrag wurde NICHT "
                                         "als versendet markiert.")
            out["zustellung"] = "versendet"
            neuer_status = "versendet"
            # Kopie an den Sucher — als Beleg, mit demselben PDF. Schlaegt
            # sie fehl, bleibt der Hauptversand gueltig; das Ergebnis steht
            # in der Antwort ("kopie").
            out["kopie"] = "nicht_moeglich"
            if email_service.gueltige_adresse(sucher_mail):
                k_betreff, k_text, k_html = kopie_mail(
                    vertrag=c, firma=firma, sucher=user,
                    empfaenger_adresse=body.recipient,
                    betreff_original=betreff, nachricht=body.message,
                    zeitpunkt=reserviert_am)
                out["kopie"] = "gesendet" if await email_service.send_email(
                    sucher_mail, k_betreff, k_text, anhang=pdf_bytes,
                    anhang_name=dateiname, html=k_html,
                    absender_name=firma.get("company_name") or "",
                    idempotency_key=f"kopie-{contract_id}-{body.idempotency_key}") else "fehlgeschlagen"
                if out["kopie"] != "gesendet":
                    log.warning("Kopie des Vertrags %s an %s fehlgeschlagen",
                                contract_id, sucher_mail)
    else:
        await _reservierung_zurueck()
        raise HTTPException(400, "Unbekannter Kanal")
    send_entry = {
        "channel": body.channel, "recipient": body.recipient,
        "subject": body.subject, "sent_at": out["sent_at"],
        "zustellung": out.get("zustellung", ""),
    }
    if reserviert:
        # Reservierten Eintrag mit dem Ergebnis fuellen (positional update).
        send_entry["idempotency_key"] = body.idempotency_key
        if out.get("wa_url"):
            send_entry["wa_url"] = out["wa_url"]
        if out.get("beleg"):
            send_entry["beleg"] = out["beleg"]
        if wiederaufnahme:
            send_entry["wiederaufgenommen"] = True
            send_entry["wiederaufnahme_am"] = claim_am
        res = await db.generated_pdfs.update_one(
            {"id": contract_id, **bereich,
             "send_status": {"$elemMatch": {
                 "idempotency_key": body.idempotency_key,
                 **({"wiederaufnahme_am": claim_am} if wiederaufnahme else {})}}},
            {"$set": {"send_status.$": send_entry,
                      "status": neuer_status, "updated_at": now_iso()}},
        )
    else:
        res = await db.generated_pdfs.update_one(
            {"id": contract_id, **bereich},
            {"$push": {"send_status": {"$each": [send_entry],
                                       "$slice": -SEND_STATUS_MAX}},
             "$set": {"status": neuer_status, "updated_at": now_iso()}},
        )
    if res.matched_count == 0:
        # Runde 17 (Nr. 372): Der Versand IST erfolgt, aber der Vertrag war
        # beim Vermerk nicht mehr im Bereich (Loeschung begonnen, Eintrag
        # durch $slice verdraengt). Vorher blieb das stumm — die Antwort
        # sagte "versendet", das Archiv wusste nichts davon. Jetzt im Log,
        # in der Antwort und als eigener Audit-Eintrag (wirft nie).
        log.warning("Vertrag %s per %s versendet, Status-Vermerk aber nicht "
                    "gespeichert (Vertrag nicht mehr im Bereich?)",
                    contract_id, body.channel)
        out["status_vermerk"] = "nicht_gespeichert"
        await log_activity_sicher(user["dealer_id"], user["id"],
                                  "pdf.gesendet.ohne_vermerk", ref=contract_id,
                                  meta={"channel": body.channel})
    await log_activity(user["dealer_id"], user["id"], f"pdf.gesendet.{body.channel}", ref=contract_id)
    return out


@router.delete("/contracts/{contract_id}")
async def delete_contract(contract_id: str, user=Depends(current_firma)):
    # Berechtigungsmatrix (PR-Review 09/2026): Loeschen ist destruktiv —
    # der Chef darf alle Vertraege der Firma loeschen, ein Sucher NUR die
    # von ihm selbst erstellten. Existenz/Eigentum wird VOR der Loeschung
    # geprueft (404/403 bleiben wie bisher).
    vorhanden = await db.generated_pdfs.find_one(
        {"id": contract_id, "dealer_id": user["dealer_id"]},
        {"_id": 0, "user_id": 1, "contract_no": 1})
    if not vorhanden:
        raise HTTPException(404, "Vertrag nicht gefunden")
    if user.get("role") == "sucher" and vorhanden.get("user_id") != user["id"]:
        raise HTTPException(403, "Sucher dürfen nur ihre eigenen Verträge "
                                 "löschen — fremde Verträge löscht der "
                                 "Händler-Hauptaccount")
    # Kaskade ueber EINE idempotente, wiederaufnehmbare Funktion (Go-Live-
    # Audit 09/2026): Grabstein am Vertrag, dann Versionen loeschen, Termin-
    # Verweise kappen, zuletzt der Vertrag. Bricht der Vorgang ab, fuehrt
    # der Aufraeumjob ihn zu Ende. Vorher wurde der Vertrag ZUERST geloescht
    # und die Kaskade konnte verwaiste Versionen/Termine hinterlassen.
    ok = await vertrag_endgueltig_loeschen(
        db, contract_id, scrub_pii=False, grund="manuell", audit=False)
    if not ok:
        raise HTTPException(404, "Vertrag nicht gefunden")
    await log_activity(user["dealer_id"], user["id"], "vertrag.geloescht.manuell",
                       ref=contract_id,
                       meta={"contract_no": vorhanden.get("contract_no")})
    return {"ok": True}


async def regenerate_contract_for_pickup(
    *, contract_id: str, dealer_id: str, user: dict,
    pickup_date: Optional[str] = None, pickup_time: Optional[str] = None,
    leeren_erlaubt: bool = False,
) -> bool:
    """Erzeugt das Kaufvertrags-PDF mit GEAENDERTEM Abholtermin neu.

    leeren_erlaubt (Nachpruefung Runde 14, Befund 84): Der Terminkalender
    unterscheidet "nicht gesendet" (None) von "bewusst geleert" (""). Nur
    mit diesem Schalter loescht ein leerer Wert Datum/Uhrzeit auch im
    Vertrag — andere Aufrufer behalten die alte Lesart "leer = unveraendert".

    Wird aufgerufen, wenn im Terminkalender das Abholdatum verschoben wird —
    der Vertrag ist eine gespeicherte Datei und wuerde sonst das alte Datum
    zeigen. Der bisherige Termin wird in `pickup_history` mitgeschrieben,
    damit nachvollziehbar bleibt, was wann geaendert wurde.
    Rueckgabe: True, wenn das PDF neu erzeugt wurde.
    """
    if not contract_id or (pickup_date is None and pickup_time is None):
        return False
    # Runde 10: derselbe Bereich wie beim Lesen — ein Sucher erzeugt kein
    # PDF fuer den Vertrag eines Kollegen, auch nicht ueber den Termin.
    # Runde 17 (Nr. 373): auch ohne user den Grabstein-Filter (Nr. 348).
    bereich = (_vertrag_bereich(user) if user
               else {"dealer_id": dealer_id, "loeschung.status": {"$ne": "laeuft"}})
    doc = await db.generated_pdfs.find_one({"id": contract_id, **bereich}, {"_id": 0})
    if not doc:
        return False

    alt_datum = doc.get("pickup_date")
    alt_zeit = doc.get("pickup_time")
    # Leere Werte bedeuten "nicht angegeben" — sie duerfen einen
    # vorhandenen Termin NICHT loeschen (sonst wuerde z.B. das Speichern
    # ohne Uhrzeit die Uhrzeit im Vertrag entfernen).
    def _neu(wert, alt):
        if wert is None:
            return alt
        if (wert or "").strip():
            return wert.strip()
        return "" if leeren_erlaubt else alt

    neu_datum = _neu(pickup_date, alt_datum)
    neu_zeit = _neu(pickup_time, alt_zeit)
    if (neu_datum or "") == (alt_datum or "") and (neu_zeit or "") == (alt_zeit or ""):
        return False

    contract_dict = dict(doc.get("contract_data") or {})
    contract_dict["pickup_date"] = neu_datum or ""
    contract_dict["pickup_time"] = neu_zeit or ""

    v = await db.vehicles.find_one(
        {"id": doc.get("vehicle_id"), "dealer_id": dealer_id}, {"_id": 0}) or {}
    vehicle = dict(v.get("data") or {})
    from deps import effective_dealer
    dealer = await effective_dealer(user) or {}
    vehicle, dealer = _apply_contract_overrides(
        contract=contract_dict, vehicle=vehicle, dealer=dealer)

    try:
        pdf_bytes = await asyncio.to_thread(
            generate_contract_pdf,
            dealer=dealer, vehicle=vehicle, contract=contract_dict,
        )
    except Exception:
        log.exception("Kaufvertrag konnte mit neuem Abholtermin nicht neu "
                      "erzeugt werden (contract=%s)", contract_id)
        return False

    # BEWEISSICHERUNG: Die bisherige PDF-Fassung wird NICHT ueberschrieben,
    # sondern als eigene Version archiviert. So bleibt belegbar, welcher
    # Vertragstext (mit welchem Abholtermin) zu jedem Zeitpunkt galt.
    alte_version = int(doc.get("version") or 1)
    archiv_id = str(uuid.uuid4())
    await db.generated_pdf_versions.insert_one({
        "id": archiv_id,
        "contract_id": contract_id,
        "dealer_id": dealer_id,
        "version": alte_version,
        "pdf_b64": doc.get("pdf_b64"),
        "contract_data": doc.get("contract_data"),
        "pickup_date": alt_datum,
        "pickup_time": alt_zeit,
        "filename": doc.get("filename"),
        "archived_at": now_iso(),
        "archived_by": user.get("id"),
        "grund": "abholtermin_geaendert",
    })

    # Runde 17 (Nr. 373): Compare-and-Swap auf die GELESENE Version — zwei
    # gleichzeitige Verschiebungen (oder eine parallele Loeschung) schrieben
    # vorher beide "Version N+1" ueber dieselbe Fassung, und eine der beiden
    # archivierten Vorversionen wiederholte die andere. Altvertraege ohne
    # Feld: `None` trifft fehlend UND null (so wurde oben auch gelesen: 1).
    # Verliert dieser Aufruf, wird die eben archivierte Fassung wieder
    # entfernt und False geliefert (der Termin bleibt korrekt gespeichert,
    # der Gewinner hat das PDF bereits neu erzeugt).
    res = await db.generated_pdfs.update_one(
        {"id": contract_id, "dealer_id": dealer_id,
         "version": doc.get("version"),
         "loeschung.status": {"$ne": "laeuft"}},
        {"$set": {
            "pdf_b64": base64.b64encode(pdf_bytes).decode(),
            "contract_data": contract_dict,
            "pickup_date": neu_datum,
            "pickup_time": neu_zeit,
            "version": alte_version + 1,
            "updated_at": now_iso(),
        },
         # Runde 17 (Nr. 321): Historie gedeckelt — die juengsten 100
         # Verschiebungen bleiben, das Dokument waechst nicht unbegrenzt.
         "$push": {"pickup_history": {"$each": [{
             "von_datum": alt_datum, "von_zeit": alt_zeit,
             "auf_datum": neu_datum, "auf_zeit": neu_zeit,
             "geaendert_von": user.get("id"), "geaendert_am": now_iso(),
             "version_vorher": alte_version,
         }], "$slice": -PICKUP_HISTORY_MAX}}},
    )
    if res.modified_count == 0:
        await db.generated_pdf_versions.delete_one({"id": archiv_id})
        log.warning("Kaufvertrag %s: Neuerzeugung verworfen — Version %s wurde "
                    "zwischenzeitlich geaendert oder der Vertrag wird geloescht",
                    contract_id, doc.get("version"))
        return False
    # Vertragskorrektur innerhalb der Frist: den BESTEHENDEN Auto-Datensatz
    # aktualisieren (nie ein zweiter); Altvertraege ohne id bekommen ihn
    # hier nachgetragen.
    if doc.get("admin_vehicle_data_id"):
        await auto_daten.aktualisieren(db, doc["admin_vehicle_data_id"],
                                       contract_dict, vehicle)
    else:
        await auto_daten.nachtragen(db, {**doc, "contract_data": contract_dict})
    await log_activity(dealer_id, user.get("id", ""), "vertrag.abholtermin.geaendert",
                       ref=contract_id,
                       meta={"von": alt_datum, "auf": neu_datum})
    return True
