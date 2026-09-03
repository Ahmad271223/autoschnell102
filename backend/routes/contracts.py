"""Contract endpoints: preview, create, list, get, pdf, send, delete."""
import asyncio
import base64
import logging
import re
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, Optional

from fastapi import APIRouter, Depends, HTTPException, Response
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

from deps import (
    current_firma,
    clean_doc, current_user, db, log_activity, now_iso, require_active_sub,
)
import auto_daten
from lifecycle import try_set_lifecycle
from pdf_service import generate_contract_pdf

router = APIRouter()


# ---------- Models ----------
class ContractIn(BaseModel):
    # Numbers from listings (e.g. mileage, power_kw, doors, seats) arrive
    # as JSON numbers from the frontend. Coerce them to strings instead
    # of failing the request with "Input should be a valid string".
    model_config = ConfigDict(coerce_numbers_to_str=True)

    vehicle_id: str
    seller_name: str
    seller_address: Optional[str] = ""
    seller_zip: Optional[str] = ""
    seller_city: Optional[str] = ""
    seller_phone: Optional[str] = ""
    seller_email: Optional[str] = ""
    id_document: Optional[str] = ""
    purchase_price: float = Field(ge=0, description="Kaufpreis darf nicht negativ sein")
    payment_method: Optional[str] = "Bar / Überweisung"
    pickup_date: Optional[str] = ""
    pickup_time: Optional[str] = ""
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
    damages: Optional[list] = []
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
    v = await db.vehicles.find_one(
        {"id": body.vehicle_id, "dealer_id": user["dealer_id"]}, {"_id": 0},
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
    v = await db.vehicles.find_one({"id": body.vehicle_id, "dealer_id": user["dealer_id"]}, {"_id": 0})
    if not v:
        raise HTTPException(404, "Fahrzeug nicht gefunden")
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
    await db.vehicles.update_one(
        {"id": body.vehicle_id, "dealer_id": user["dealer_id"]},
        {"$set": {"status": "Vertrag erstellt", "purchase_price": body.purchase_price}},
    )
    # Lebenszyklus: Vertrag erstellt → gekauft (Kaufpreis liegt vor).
    await try_set_lifecycle(body.vehicle_id, user["dealer_id"], "vertrag_erstellt", user=user)
    await try_set_lifecycle(body.vehicle_id, user["dealer_id"], "gekauft", user=user)
    await log_activity(user["dealer_id"], user["id"], "pdf.erstellt", ref=pdf_id)

    # Auto-create appointment if pickup_date was provided so the PDF
    # automatically appears in the Terminplaner. Avoid duplicates if an
    # appointment for the same contract already exists.
    if body.pickup_date:
        already = await db.appointments.find_one(
            {"dealer_id": user["dealer_id"], "contract_id": pdf_id}, {"_id": 0},
        )
        if not already:
            appt_id = str(uuid.uuid4())
            title = (
                f"{vehicle.get('make_label','')} {vehicle.get('model_label','')} abholen".strip()
                or "Fahrzeug abholen"
            )
            pickup_address = " ".join([
                body.seller_address or "",
                body.seller_zip or "",
                body.seller_city or "",
            ]).strip()
            await db.appointments.insert_one({
                "id": appt_id,
                "dealer_id": user["dealer_id"],
                "title": title,
                "vehicle_id": body.vehicle_id,
                "contract_id": pdf_id,
                "seller_name": body.seller_name,
                "seller_phone": body.seller_phone,
                "seller_email": body.seller_email,
                "pickup_address": pickup_address,
                "pickup_date": body.pickup_date,
                "pickup_time": body.pickup_time or "",
                "status": "offen",
                "created_at": now_iso(),
                "updated_at": now_iso(),
            })
            await db.generated_pdfs.update_one(
                {"id": pdf_id},
                {"$set": {"appointment_id": appt_id, "status": "Termin erstellt"}},
            )
            doc["appointment_id"] = appt_id
            doc["status"] = "Termin erstellt"
            await try_set_lifecycle(body.vehicle_id, user["dealer_id"],
                                    "abholung_geplant", user=user)
            await log_activity(user["dealer_id"], user["id"], "termin.auto-erstellt", ref=appt_id)

    return {**clean_doc(doc), "pdf_b64": pdf_b64}


@router.get("/contracts")
async def list_contracts(
    user=Depends(current_firma),
    q: Optional[str] = None,
    days: Optional[int] = None,
    channel: Optional[str] = None,
):
    query: Dict[str, Any] = {"dealer_id": user["dealer_id"]}
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
    ).sort("created_at", -1).to_list(500)
    if channel:
        items = [
            i for i in items
            if any(s.get("channel") == channel for s in i.get("send_status", []))
        ]
    # Alt-Verträge heilen: früher wurde `vehicle_image_urls` leer gespeichert,
    # weil die Fotos beim Fahrzeug unter `data.images` liegen (nicht
    # `image_urls`). Fehlende Listen hier einmalig aus dem Fahrzeug
    # nachziehen und dauerhaft am Vertrag speichern — so bleiben die Fotos
    # auch sichtbar, wenn das Fahrzeug später gelöscht wird.
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
                await db.generated_pdfs.update_one(
                    {"id": i["id"], "dealer_id": user["dealer_id"],
                     "vehicle_image_urls": {"$in": [None, []]}},
                    {"$set": {"vehicle_image_urls": urls}})
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
        {"id": contract_id, "dealer_id": user["dealer_id"]}, {"_id": 0},
    )
    if not c:
        raise HTTPException(404, "Vertrag nicht gefunden")
    return c


@router.get("/contracts/{contract_id}/pdf")
async def get_contract_pdf(contract_id: str, user=Depends(current_firma)):
    c = await db.generated_pdfs.find_one(
        {"id": contract_id, "dealer_id": user["dealer_id"]},
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
        {"id": contract_id, "dealer_id": user["dealer_id"]}, {"_id": 0, "id": 1})
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


@router.post("/contracts/{contract_id}/send")
async def send_contract(contract_id: str, body: SendIn, user=Depends(require_active_sub)):
    c = await db.generated_pdfs.find_one(
        {"id": contract_id, "dealer_id": user["dealer_id"]}, {"_id": 0},
    )
    if not c:
        raise HTTPException(404, "Vertrag nicht gefunden")
    # Idempotenz RESERVIEREND (Review 09/2026): Der Schluessel wurde vorher
    # erst NACH dem Senden eingetragen — zwei gleichzeitige Anfragen mit
    # demselben Schluessel konnten beide zustellen. Jetzt wird der Eintrag
    # atomar VOR dem Versand angelegt; der Verlierer bekommt das Ergebnis
    # des Gewinners, bei Sendefehler wird der Eintrag wieder entfernt.
    reserviert = False
    if body.idempotency_key:
        vorhanden = next((e for e in (c.get("send_status") or [])
                          if e.get("idempotency_key") == body.idempotency_key), None)
        if vorhanden:
            return {"channel": vorhanden.get("channel"), "status": "ok",
                    "sent_at": vorhanden.get("sent_at"),
                    "zustellung": vorhanden.get("zustellung", ""),
                    "wa_url": vorhanden.get("wa_url"), "bereits_gesendet": True}
        res = await db.generated_pdfs.update_one(
            {"id": contract_id, "dealer_id": user["dealer_id"],
             "send_status.idempotency_key": {"$ne": body.idempotency_key}},
            {"$push": {"send_status": {
                "idempotency_key": body.idempotency_key, "channel": body.channel,
                "recipient": body.recipient, "subject": body.subject,
                "sent_at": now_iso(), "zustellung": "laeuft"}}})
        if res.modified_count == 0:
            return {"channel": body.channel, "status": "ok", "sent_at": now_iso(),
                    "zustellung": "laeuft", "bereits_gesendet": True}
        reserviert = True

    async def _reservierung_zurueck():
        if reserviert:
            await db.generated_pdfs.update_one(
                {"id": contract_id, "dealer_id": user["dealer_id"]},
                {"$pull": {"send_status": {"idempotency_key": body.idempotency_key,
                                           "zustellung": "laeuft"}}})
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
                "E-Mail-Versand ist nicht eingerichtet (SMTP_* in der .env "
                "setzen) — der Vertrag wurde NICHT versendet. Alternativ per "
                "WhatsApp teilen oder das PDF herunterladen.")
        else:
            pdf_bytes = base64.b64decode(c["pdf_b64"]) if c.get("pdf_b64") else None
            ok = await email_service.send_email(
                body.recipient, body.subject or "Ihr Kaufvertrag",
                body.message or "Anbei der Kaufvertrag als PDF.",
                anhang=pdf_bytes, anhang_name=c.get("filename") or "Kaufvertrag.pdf")
            if not ok:
                await _reservierung_zurueck()
                raise HTTPException(502, "E-Mail-Versand fehlgeschlagen — "
                                         "bitte in ein paar Minuten erneut "
                                         "versuchen. Der Vertrag wurde NICHT "
                                         "als versendet markiert.")
            out["zustellung"] = "versendet"
            neuer_status = "versendet"
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
        await db.generated_pdfs.update_one(
            {"id": contract_id, "dealer_id": user["dealer_id"],
             "send_status.idempotency_key": body.idempotency_key},
            {"$set": {"send_status.$": send_entry,
                      "status": neuer_status, "updated_at": now_iso()}},
        )
    else:
        await db.generated_pdfs.update_one(
            {"id": contract_id},
            {"$push": {"send_status": send_entry},
             "$set": {"status": neuer_status, "updated_at": now_iso()}},
        )
    await log_activity(user["dealer_id"], user["id"], f"pdf.gesendet.{body.channel}", ref=contract_id)
    return out


@router.delete("/contracts/{contract_id}")
async def delete_contract(contract_id: str, user=Depends(current_firma)):
    # Berechtigungsmatrix (PR-Review 09/2026): Loeschen ist destruktiv —
    # der Chef darf alle Vertraege der Firma loeschen, ein Sucher NUR die
    # von ihm selbst erstellten.
    if user.get("role") == "sucher":
        eigener = await db.generated_pdfs.find_one(
            {"id": contract_id, "dealer_id": user["dealer_id"]},
            {"_id": 0, "user_id": 1})
        if not eigener:
            raise HTTPException(404, "Vertrag nicht gefunden")
        if eigener.get("user_id") != user["id"]:
            raise HTTPException(403, "Sucher dürfen nur ihre eigenen Verträge "
                                     "löschen — fremde Verträge löscht der "
                                     "Händler-Hauptaccount")
    res = await db.generated_pdfs.delete_one({"id": contract_id, "dealer_id": user["dealer_id"]})
    if not res.deleted_count:
        raise HTTPException(404, "Vertrag nicht gefunden")
    # Kaskade (PR-Review 09/2026): archivierte Versionen mitloeschen und
    # Termin-Verweise kappen — sonst blieben verwaiste Versionen und
    # Termine, deren "Kaufvertrag oeffnen" ins Leere zeigt.
    await db.generated_pdf_versions.delete_many(
        {"contract_id": contract_id, "dealer_id": user["dealer_id"]})
    await db.appointments.update_many(
        {"contract_id": contract_id, "dealer_id": user["dealer_id"]},
        {"$set": {"contract_id": None, "updated_at": now_iso()}})
    return {"ok": True}


async def regenerate_contract_for_pickup(
    *, contract_id: str, dealer_id: str, user: dict,
    pickup_date: Optional[str] = None, pickup_time: Optional[str] = None,
) -> bool:
    """Erzeugt das Kaufvertrags-PDF mit GEAENDERTEM Abholtermin neu.

    Wird aufgerufen, wenn im Terminkalender das Abholdatum verschoben wird —
    der Vertrag ist eine gespeicherte Datei und wuerde sonst das alte Datum
    zeigen. Der bisherige Termin wird in `pickup_history` mitgeschrieben,
    damit nachvollziehbar bleibt, was wann geaendert wurde.
    Rueckgabe: True, wenn das PDF neu erzeugt wurde.
    """
    if not contract_id or (pickup_date is None and pickup_time is None):
        return False
    doc = await db.generated_pdfs.find_one(
        {"id": contract_id, "dealer_id": dealer_id}, {"_id": 0})
    if not doc:
        return False

    alt_datum = doc.get("pickup_date")
    alt_zeit = doc.get("pickup_time")
    # Leere Werte bedeuten "nicht angegeben" — sie duerfen einen
    # vorhandenen Termin NICHT loeschen (sonst wuerde z.B. das Speichern
    # ohne Uhrzeit die Uhrzeit im Vertrag entfernen).
    neu_datum = pickup_date if (pickup_date or "").strip() else alt_datum
    neu_zeit = pickup_time if (pickup_time or "").strip() else alt_zeit
    if neu_datum == alt_datum and neu_zeit == alt_zeit:
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
    await db.generated_pdf_versions.insert_one({
        "id": str(uuid.uuid4()),
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

    await db.generated_pdfs.update_one(
        {"id": contract_id, "dealer_id": dealer_id},
        {"$set": {
            "pdf_b64": base64.b64encode(pdf_bytes).decode(),
            "contract_data": contract_dict,
            "pickup_date": neu_datum,
            "pickup_time": neu_zeit,
            "version": alte_version + 1,
            "updated_at": now_iso(),
        },
         "$push": {"pickup_history": {
             "von_datum": alt_datum, "von_zeit": alt_zeit,
             "auf_datum": neu_datum, "auf_zeit": neu_zeit,
             "geaendert_von": user.get("id"), "geaendert_am": now_iso(),
             "version_vorher": alte_version,
         }}},
    )
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
