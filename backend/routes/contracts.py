"""Contract endpoints: preview, create, list, get, pdf, send, delete."""
import asyncio
import base64
import re
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, Optional

from fastapi import APIRouter, Depends, HTTPException, Response
from pydantic import BaseModel, ConfigDict, Field, field_validator


def _safe_filename(name: str, fallback: str = "document.pdf") -> str:
    """Strip characters that could inject extra HTTP header lines or break parsers.

    Removes control characters (CR, LF, TAB), double-quotes, and backslashes,
    then trims whitespace and limits length.
    """
    safe = re.sub(r'[\r\n\t"\\]', "", name)  # Remove newlines / quotes / backslash
    safe = safe.strip()
    return safe[:200] or fallback

from deps import (
    clean_doc, current_user, db, log_activity, now_iso, require_active_sub,
)
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
    dealer = await db.dealers.find_one({"id": user["dealer_id"]}, {"_id": 0}) or {}
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
    dealer = await db.dealers.find_one({"id": user["dealer_id"]}, {"_id": 0}) or {}
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
    vehicle_image_urls = list(vehicle.get("image_urls") or [])
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
    await db.generated_pdfs.insert_one(doc)
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
    user=Depends(current_user),
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
    return items


@router.get("/contracts/{contract_id}")
async def get_contract(contract_id: str, user=Depends(current_user)):
    c = await db.generated_pdfs.find_one(
        {"id": contract_id, "dealer_id": user["dealer_id"]}, {"_id": 0},
    )
    if not c:
        raise HTTPException(404, "Vertrag nicht gefunden")
    return c


@router.get("/contracts/{contract_id}/pdf")
async def get_contract_pdf(contract_id: str, user=Depends(current_user)):
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


@router.post("/contracts/{contract_id}/send")
async def send_contract(contract_id: str, body: SendIn, user=Depends(require_active_sub)):
    c = await db.generated_pdfs.find_one(
        {"id": contract_id, "dealer_id": user["dealer_id"]}, {"_id": 0},
    )
    if not c:
        raise HTTPException(404, "Vertrag nicht gefunden")
    # Prepare share targets (mocked for now – real provider integration later)
    out: dict = {"channel": body.channel, "status": "ok", "sent_at": now_iso()}
    if body.channel == "whatsapp":
        digits = "".join(ch for ch in (body.recipient or "") if ch.isdigit())
        from urllib.parse import quote_plus
        out["wa_url"] = f"https://wa.me/{digits}?text={quote_plus(body.message)}"
    elif body.channel == "email":
        out["mocked"] = True  # MOCKED until SMTP/Resend key provided
    else:
        raise HTTPException(400, "Unbekannter Kanal")
    send_entry = {
        "channel": body.channel, "recipient": body.recipient,
        "subject": body.subject, "sent_at": out["sent_at"],
    }
    await db.generated_pdfs.update_one(
        {"id": contract_id},
        {"$push": {"send_status": send_entry},
         "$set": {"status": "versendet", "updated_at": now_iso()}},
    )
    await log_activity(user["dealer_id"], user["id"], f"pdf.gesendet.{body.channel}", ref=contract_id)
    return out


@router.delete("/contracts/{contract_id}")
async def delete_contract(contract_id: str, user=Depends(current_user)):
    res = await db.generated_pdfs.delete_one({"id": contract_id, "dealer_id": user["dealer_id"]})
    if not res.deleted_count:
        raise HTTPException(404, "Vertrag nicht gefunden")
    return {"ok": True}
