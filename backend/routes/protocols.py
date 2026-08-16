"""Digitales Abhol-Protokoll (Fahrer-App).

Dasselbe Protokoll wie das Papier-PDF — nur ausfüllbar:
  * Abschnitt 2/3: Checklisten abhaken (Dokumente, Zubehör, Ausstattung)
  * Abschnitt 4: technischer Zustand eintippen/auswählen
  * Abschnitt 7: Bemerkungen
  * Abschnitt 8: zwei Unterschriften per Finger (Fahrer + Verkäufer)

Ablauf: Entwurf laufend speichern (`PUT .../protocol`) → abschließen
(`POST .../protocol/finalize`). Beim Abschließen entsteht ein
unveränderbares, ausgefülltes PDF im Storage, der Termin wird auf
"abgeholt" gesetzt und der Fahrzeug-Lebenszyklus nachgezogen.
Korrekturen erzeugen eine NEUE Version (alte bleibt als Beweis).
"""
import base64
import uuid
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from deps import db, log_activity, now_iso
from lifecycle import try_set_lifecycle
from routes.drivers import current_driver

# Haendler-/Sucher-Zugriff auf die fertigen Protokolle (Fahrzeugakte).
from deps import current_user as _dealer_dep

router = APIRouter()


# ---------- Vorlage: dieselben Punkte wie im PDF ----------
DOCUMENT_ITEMS = [
    "Fahrzeugschein / Zulassung Teil I",
    "Fahrzeugbrief / Zulassung Teil II",
    "Letzter HU-/AU-Bericht",
    "Servicebuch / Scheckheft",
    "COC-Papiere (EG-Übereinstimmung)",
    "Bedienungsanleitung",
    "Zweitsatz Reifen",
    "Ladekabel / Adapter",
    "Werkzeug / Warndreieck / Verbandskasten",
]

CONDITION_FIELDS = [
    ("mileage", "Kilometerstand bei Abholung", "text"),
    ("fuel_level", "Tankfüllstand", ["leer", "1/4", "1/2", "3/4", "voll"]),
    ("tire_profile", "Reifenprofil VL / VR / HL / HR", "text"),
    ("driving", "Fahrverhalten (Probefahrt)", ["ok", "Mängel", "nicht gefahren"]),
    ("battery", "Batterie / Starter", ["ok", "schwach", "defekt"]),
    ("warning_lights", "Kontrollleuchten leuchten", ["nein", "ja"]),
    ("clean_inside", "Sauberkeit Innenraum", ["gut", "mittel", "schlecht"]),
    ("clean_outside", "Sauberkeit Außen", ["gut", "mittel", "schlecht"]),
]


class ProtocolIn(BaseModel):
    """Alle Felder optional — der Fahrer speichert laufend Zwischenstände."""
    vehicle_check: Optional[Dict[str, Any]] = None      # Abschnitt 1 (Korrekturen)
    documents: Optional[Dict[str, bool]] = None         # Abschnitt 2
    keys_count: Optional[str] = Field(default=None, max_length=20)
    keys_expected: Optional[str] = Field(default=None, max_length=20)
    features: Optional[Dict[str, bool]] = None          # Abschnitt 3
    condition: Optional[Dict[str, Any]] = None          # Abschnitt 4
    damages_confirmed: Optional[bool] = None            # Abschnitt 5
    # Abschnitt 6: neu entdeckte Schaeden, per Tipp auf die Fahrzeug-Skizze
    # markiert (gleiches Format wie die Kaufvertrag-Schaeden: view/zone/x/y/...)
    new_damages: Optional[List[Dict[str, Any]]] = Field(default=None, max_length=40)
    notes: Optional[str] = Field(default=None, max_length=5000)   # Abschnitt 7
    place: Optional[str] = Field(default=None, max_length=200)    # Ort (Abschnitt 8)


class FinalizeIn(BaseModel):
    signature_driver_b64: str = Field(min_length=20)
    signature_seller_b64: str = Field(min_length=20)
    seller_name: Optional[str] = Field(default=None, max_length=200)
    place: Optional[str] = Field(default=None, max_length=200)


async def _appt_or_404(appt_id: str, driver: dict) -> dict:
    appt = await db.appointments.find_one(
        {"id": appt_id, "driver_id": driver["id"]}, {"_id": 0})
    if not appt:
        raise HTTPException(404, "Termin nicht gefunden")
    return appt


async def _current(appt_id: str) -> Optional[dict]:
    return await db.pickup_protocols.find_one(
        {"appointment_id": appt_id, "superseded": {"$ne": True}}, {"_id": 0},
        sort=[("version", -1)])


@router.get("/driver/appointments/{appt_id}/protocol")
async def get_protocol(appt_id: str, driver=Depends(current_driver)):
    """Aktuellen Entwurf (oder das abgeschlossene Protokoll) + Vorlage laden."""
    appt = await _appt_or_404(appt_id, driver)
    doc = await _current(appt_id)
    vehicle: Dict[str, Any] = {}
    if appt.get("vehicle_id"):
        v = await db.vehicles.find_one({"id": appt["vehicle_id"]}, {"_id": 0}) or {}
        vehicle = dict(v.get("data") or {})
    contract: Dict[str, Any] = {}
    if appt.get("contract_id"):
        c = await db.generated_pdfs.find_one(
            {"id": appt["contract_id"]}, {"_id": 0, "contract_data": 1}) or {}
        contract = dict(c.get("contract_data") or {})
    return {
        "protocol": doc,
        "template": {
            "documents": DOCUMENT_ITEMS,
            "features": (vehicle.get("features") or [])[:20],
            "condition_fields": [
                {"key": k, "label": lb, "options": opts if isinstance(opts, list) else None}
                for k, lb, opts in CONDITION_FIELDS
            ],
        },
        "vehicle": vehicle,
        "damages": contract.get("damages") or vehicle.get("damages") or [],
        "appointment": {k: appt.get(k) for k in
                        ("id", "title", "pickup_date", "pickup_time",
                         "pickup_address", "seller_name", "status")},
    }


@router.put("/driver/appointments/{appt_id}/protocol")
async def save_protocol(appt_id: str, body: ProtocolIn,
                        driver=Depends(current_driver)):
    """Zwischenstand speichern — beliebig oft, solange nicht abgeschlossen."""
    appt = await _appt_or_404(appt_id, driver)
    doc = await _current(appt_id)
    if doc and doc.get("status") == "final":
        raise HTTPException(409, "Protokoll ist bereits abgeschlossen. Bitte eine "
                                 "Korrektur-Version starten.")
    payload = {k: v for k, v in body.model_dump(exclude_none=True).items()}
    if doc:
        await db.pickup_protocols.update_one(
            {"id": doc["id"]}, {"$set": {**payload, "updated_at": now_iso()}})
        return await db.pickup_protocols.find_one({"id": doc["id"]}, {"_id": 0})
    new_doc = {
        "id": str(uuid.uuid4()),
        "appointment_id": appt_id,
        "vehicle_id": appt.get("vehicle_id"),
        "dealer_id": appt.get("dealer_id"),
        "driver_account_id": driver["id"],
        "driver_name": driver.get("display_name", ""),
        "version": 1,
        "status": "entwurf",
        "superseded": False,
        **payload,
        "created_at": now_iso(), "updated_at": now_iso(),
    }
    await db.pickup_protocols.insert_one(new_doc)
    return {k: v for k, v in new_doc.items() if k != "_id"}


@router.post("/driver/appointments/{appt_id}/protocol/correction")
async def start_correction(appt_id: str, driver=Depends(current_driver)):
    """Neue Version anlegen: die alte bleibt als Beweis erhalten."""
    await _appt_or_404(appt_id, driver)
    doc = await _current(appt_id)
    if not doc or doc.get("status") != "final":
        raise HTTPException(400, "Es gibt kein abgeschlossenes Protokoll zum Korrigieren")
    await db.pickup_protocols.update_one({"id": doc["id"]},
                                         {"$set": {"superseded": True}})
    new_doc = {k: v for k, v in doc.items()
               if k not in ("id", "status", "pdf_path", "finalized_at",
                            "signature_driver_key", "signature_seller_key")}
    new_doc.update({
        "id": str(uuid.uuid4()),
        "version": int(doc.get("version", 1)) + 1,
        "status": "entwurf",
        "superseded": False,
        "corrects_version": doc.get("version", 1),
        "created_at": now_iso(), "updated_at": now_iso(),
    })
    await db.pickup_protocols.insert_one(new_doc)
    return {k: v for k, v in new_doc.items() if k != "_id"}


@router.post("/driver/appointments/{appt_id}/protocol/finalize")
async def finalize_protocol(appt_id: str, body: FinalizeIn,
                            driver=Depends(current_driver)):
    """Unterschriften anhängen, ausgefülltes PDF erzeugen, Termin auf
    'abgeholt' setzen."""
    from storage_service import make_key, storage, StorageError
    appt = await _appt_or_404(appt_id, driver)
    doc = await _current(appt_id)
    if not doc:
        raise HTTPException(400, "Bitte zuerst das Protokoll ausfüllen")
    if doc.get("status") == "final":
        raise HTTPException(409, "Protokoll ist bereits abgeschlossen")

    dealer_id = appt.get("dealer_id", "x")

    def _save_sig(b64: str, who: str) -> str:
        try:
            raw = base64.b64decode(b64.split(",")[-1], validate=False)
        except (ValueError, TypeError):
            raise HTTPException(400, f"Unterschrift ({who}) konnte nicht gelesen werden")
        if not raw or len(raw) > 2 * 1024 * 1024:
            raise HTTPException(400, f"Unterschrift ({who}) ungültig oder zu groß")
        try:
            key = make_key("protocol", dealer_id, f"unterschrift-{who}.png")
            storage.save(key, raw)
        except StorageError as exc:
            raise HTTPException(400, f"Unterschrift konnte nicht gespeichert werden: {exc}")
        return key

    sig_driver = _save_sig(body.signature_driver_b64, "fahrer")
    sig_seller = _save_sig(body.signature_seller_b64, "verkaeufer")

    # ---- Daten für das ausgefüllte PDF zusammenstellen ----
    vehicle: Dict[str, Any] = {}
    if appt.get("vehicle_id"):
        v = await db.vehicles.find_one({"id": appt["vehicle_id"]}, {"_id": 0}) or {}
        vehicle = dict(v.get("data") or {})
    contract: Dict[str, Any] = {}
    if appt.get("contract_id"):
        c = await db.generated_pdfs.find_one(
            {"id": appt["contract_id"]}, {"_id": 0, "contract_data": 1}) or {}
        contract = dict(c.get("contract_data") or {})
    dealer = await db.dealers.find_one({"id": dealer_id}, {"_id": 0}) or {}

    filled = {k: doc.get(k) for k in
              ("vehicle_check", "documents", "keys_count", "keys_expected",
               "features", "condition", "damages_confirmed", "new_damages",
               "notes")}
    filled["place"] = body.place or doc.get("place") or ""
    filled["seller_name"] = body.seller_name or appt.get("seller_name") or ""
    filled["driver_name"] = driver.get("display_name", "")
    filled["signature_driver"] = base64.b64decode(
        body.signature_driver_b64.split(",")[-1], validate=False)
    filled["signature_seller"] = base64.b64decode(
        body.signature_seller_b64.split(",")[-1], validate=False)
    filled["version"] = doc.get("version", 1)

    from pickup_pdf_service import build_pickup_pdf
    pdf_bytes = build_pickup_pdf(
        appointment=appt, vehicle=vehicle, contract=contract, dealer=dealer,
        driver={"id": driver["id"], "name": driver.get("display_name"),
                "code": driver.get("driver_code")},
        filled=filled,
    )
    try:
        pdf_key = make_key("protocol", dealer_id, "abholprotokoll.pdf")
        storage.save(pdf_key, pdf_bytes)
    except StorageError as exc:
        raise HTTPException(400, f"PDF konnte nicht gespeichert werden: {exc}")

    await db.pickup_protocols.update_one(
        {"id": doc["id"]},
        {"$set": {"status": "final", "pdf_path": pdf_key,
                  "signature_driver_key": sig_driver,
                  "signature_seller_key": sig_seller,
                  "seller_name": filled["seller_name"],
                  "place": filled["place"],
                  "finalized_at": now_iso(), "updated_at": now_iso()}})

    # Termin + Fahrzeug-Lebenszyklus nachziehen: abgeschlossen = abgeholt.
    await db.appointments.update_one(
        {"id": appt_id},
        {"$set": {"status": "abgeholt", "status_changed_at": now_iso(),
                  "protocol_id": doc["id"]}})
    if appt.get("vehicle_id"):
        await try_set_lifecycle(appt["vehicle_id"], dealer_id, "abgeholt")
    await log_activity(dealer_id, driver["id"], "abholprotokoll.abgeschlossen",
                       ref=appt.get("vehicle_id"),
                       meta={"version": doc.get("version", 1),
                             "appointment_id": appt_id})
    return {"ok": True, "protocol_id": doc["id"], "version": doc.get("version", 1),
            "pdf_url": f"/api/driver/appointments/{appt_id}/protocol.pdf"}


@router.get("/driver/appointments/{appt_id}/protocol.pdf")
async def driver_protocol_pdf(appt_id: str, driver=Depends(current_driver)):
    """Das ausgefüllte, abgeschlossene Protokoll als PDF (Fahrer-Ansicht)."""
    from fastapi import Response
    from snapshot_service import get_object as storage_get
    await _appt_or_404(appt_id, driver)
    doc = await _current(appt_id)
    if not doc or doc.get("status") != "final" or not doc.get("pdf_path"):
        raise HTTPException(404, "Noch kein abgeschlossenes Protokoll vorhanden")
    from storage_service import storage, StorageError
    try:
        data = storage.load(doc["pdf_path"])
    except StorageError:
        raise HTTPException(404, "PDF nicht gefunden")
    return Response(content=data, media_type="application/pdf")


# ---------- Händler-Sicht ----------
@router.get("/vehicles/{vehicle_id}/protocols")
async def dealer_list_protocols(vehicle_id: str, user=Depends(_dealer_dep)):
    """Alle Protokoll-Versionen eines Fahrzeugs (Händler/Chef)."""
    docs = await db.pickup_protocols.find(
        {"vehicle_id": vehicle_id, "dealer_id": user["dealer_id"],
         "status": "final"},
        {"_id": 0, "id": 1, "version": 1, "finalized_at": 1, "driver_name": 1,
         "seller_name": 1, "place": 1, "corrects_version": 1, "superseded": 1},
    ).sort("version", -1).to_list(20)
    return docs


@router.get("/protocols/{protocol_id}.pdf")
async def dealer_protocol_pdf(protocol_id: str, user=Depends(_dealer_dep)):
    from fastapi import Response
    doc = await db.pickup_protocols.find_one(
        {"id": protocol_id, "dealer_id": user["dealer_id"]}, {"_id": 0})
    if not doc or not doc.get("pdf_path"):
        raise HTTPException(404, "Protokoll nicht gefunden")
    from storage_service import storage, StorageError
    try:
        data = storage.load(doc["pdf_path"])
    except StorageError:
        raise HTTPException(404, "PDF nicht gefunden")
    return Response(content=data, media_type="application/pdf")
