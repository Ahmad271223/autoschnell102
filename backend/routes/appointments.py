"""Appointment endpoints: CRUD + pickup-order.pdf."""
import asyncio
import logging
import uuid
from datetime import datetime
from typing import Any, Dict, Literal, Optional

from fastapi import APIRouter, Depends, HTTPException, Response
from pydantic import BaseModel, field_validator

from deps import clean_doc, current_user, db, log_activity, now_iso, current_firma
from lifecycle import try_set_lifecycle

log = logging.getLogger("autohandel")

router = APIRouter()


class AppointmentIn(BaseModel):
    title: Optional[str] = None
    vehicle_id: Optional[str] = None
    contract_id: Optional[str] = None
    seller_name: Optional[str] = ""
    seller_phone: Optional[str] = ""
    seller_email: Optional[str] = ""
    pickup_address: Optional[str] = ""
    pickup_date: Optional[str] = ""
    pickup_time: Optional[str] = ""
    driver_id: Optional[str] = None
    status: Optional[Literal[
        "offen", "bestätigt", "in Bearbeitung",
        "abgeholt", "nicht abgeholt", "storniert",
    ]] = "offen"
    notes: Optional[str] = ""
    final_price: Optional[float] = None
    extra_costs: Optional[float] = None

    # DoS-Schutz: Termin-Felder fliessen ins Abhol-PDF (ReportLab). Cap:
    # notes als Freitext 20.000, alle uebrigen Felder (enge Zellen) 500.
    @field_validator("*")
    @classmethod
    def _cap_string_length(cls, v, info):
        if isinstance(v, str):
            limit = 20000 if info.field_name == "notes" else 500
            if len(v) > limit:
                raise ValueError(f"Feld '{info.field_name}' zu lang (max. {limit} Zeichen)")
        return v


@router.post("/appointments")
async def _fahrer_pruefen(dealer_id: str, driver_id) -> None:
    """Fahrer-Zuweisung nur an AKTUELL verknuepfte Fahrer der Firma.

    Vorher wurde driver_id ungeprueft uebernommen — wer die ID eines
    ehemaligen (entfernten) Fahrers kannte, konnte ihm weiter Termine
    samt Verkaeuferdaten zustellen (PR-Review 09/2026)."""
    if not driver_id:
        return
    if not await db.dealer_drivers.find_one(
            {"dealer_id": dealer_id, "driver_account_id": driver_id},
            {"_id": 1}):
        raise HTTPException(400, "Dieser Fahrer ist nicht (mehr) mit deiner "
                                 "Firma verknüpft — bitte zuerst unter "
                                 "'Fahrer' per Code hinzufügen.")


async def create_appointment(body: AppointmentIn, user=Depends(current_firma)):
    appt_id = str(uuid.uuid4())
    title = body.title or "Fahrzeug abholen"
    # EIGENTUEMER-PRUEFUNG: Fahrzeug- und Vertrags-IDs sind erratbar
    # (v_<Inserats-ID>). Ohne diese Pruefung koennte ein Haendler einen
    # Termin auf ein FREMDES Fahrzeug legen und dessen Daten im Fahrer-PDF
    # bzw. Protokoll sehen.
    vehicle_doc = None
    if body.vehicle_id:
        vehicle_doc = await db.vehicles.find_one(
            {"id": body.vehicle_id, "dealer_id": user["dealer_id"]}, {"_id": 0})
        if not vehicle_doc:
            raise HTTPException(404, "Fahrzeug nicht gefunden")
    if body.contract_id:
        if not await db.generated_pdfs.find_one(
                {"id": body.contract_id, "dealer_id": user["dealer_id"]}, {"_id": 1}):
            raise HTTPException(404, "Vertrag nicht gefunden")
    await _fahrer_pruefen(user["dealer_id"], body.driver_id)
    if vehicle_doc and not body.title:
        d = vehicle_doc["data"]
        title = f"{d.get('make_label','')} {d.get('model_label','')} abholen".strip()
    doc = {"id": appt_id, "dealer_id": user["dealer_id"], "title": title,
           **body.model_dump(exclude_none=True),
           "created_at": now_iso(), "updated_at": now_iso()}
    if "status" not in doc:
        doc["status"] = "offen"
    await db.appointments.insert_one(doc)
    if body.contract_id:
        await db.generated_pdfs.update_one(
            {"id": body.contract_id, "dealer_id": user["dealer_id"]},
            {"$set": {"appointment_id": appt_id, "status": "Termin erstellt"}},
        )
    if body.vehicle_id:
        await try_set_lifecycle(body.vehicle_id, user["dealer_id"],
                                "abholung_geplant", user=user)
    await log_activity(user["dealer_id"], user["id"], "termin.erstellt", ref=appt_id)
    return clean_doc(doc)


@router.get("/appointments")
async def list_appointments(user=Depends(current_firma), status: Optional[str] = None):
    query: dict = {"dealer_id": user["dealer_id"]}
    if status:
        query["status"] = status
    items = await db.appointments.find(query, {"_id": 0}).sort("pickup_date", 1).to_list(500)
    # Enrich with vehicle + driver (driver = globaler Fahrer-Account)
    drivers_map = {}
    link_ids = [
        link["driver_account_id"] async for link in
        db.dealer_drivers.find({"dealer_id": user["dealer_id"]}, {"_id": 0, "driver_account_id": 1})
    ]
    if link_ids:
        async for d in db.driver_accounts.find(
            {"id": {"$in": link_ids}},
            {"_id": 0, "id": 1, "display_name": 1, "driver_code": 1, "email": 1},
        ):
            drivers_map[d["id"]] = {
                "id": d["id"], "name": d.get("display_name"),
                "driver_code": d.get("driver_code"), "email": d.get("email"),
            }
    vehicles_map = {}
    async for v in db.vehicles.find({"dealer_id": user["dealer_id"]}, {"_id": 0}):
        vehicles_map[v["id"]] = v
    for a in items:
        if a.get("driver_id") and a["driver_id"] in drivers_map:
            a["driver"] = drivers_map[a["driver_id"]]
        if a.get("vehicle_id") and a["vehicle_id"] in vehicles_map:
            a["vehicle"] = vehicles_map[a["vehicle_id"]]
    return items


@router.get("/appointments/{appt_id}")
async def get_appointment(appt_id: str, user=Depends(current_firma)):
    a = await db.appointments.find_one({"id": appt_id, "dealer_id": user["dealer_id"]}, {"_id": 0})
    if not a:
        raise HTTPException(404, "Termin nicht gefunden")
    if a.get("vehicle_id"):
        v = await db.vehicles.find_one(
            {"id": a["vehicle_id"], "dealer_id": user["dealer_id"]}, {"_id": 0},
        )
        if v:
            a["vehicle"] = v
    if a.get("driver_id"):
        d = await db.driver_accounts.find_one(
            {"id": a["driver_id"]},
            {"_id": 0, "id": 1, "display_name": 1, "driver_code": 1, "email": 1},
        )
        if d:
            a["driver"] = {
                "id": d["id"], "name": d.get("display_name"),
                "driver_code": d.get("driver_code"), "email": d.get("email"),
            }
    return a


@router.put("/appointments/{appt_id}")
async def update_appointment(appt_id: str, body: AppointmentIn, user=Depends(current_firma)):
    existing = await db.appointments.find_one(
        {"id": appt_id, "dealer_id": user["dealer_id"]}, {"_id": 0},
    )
    if not existing:
        raise HTTPException(404, "Termin nicht gefunden")
    update = body.model_dump(exclude_none=True)
    # Auch beim Aendern: verknuepfte IDs muessen dem Haendler gehoeren.
    if update.get("vehicle_id") and not await db.vehicles.find_one(
            {"id": update["vehicle_id"], "dealer_id": user["dealer_id"]}, {"_id": 1}):
        raise HTTPException(404, "Fahrzeug nicht gefunden")
    if update.get("contract_id") and not await db.generated_pdfs.find_one(
            {"id": update["contract_id"], "dealer_id": user["dealer_id"]}, {"_id": 1}):
        raise HTTPException(404, "Vertrag nicht gefunden")
    if "driver_id" in update:
        await _fahrer_pruefen(user["dealer_id"], update.get("driver_id"))
    update["updated_at"] = now_iso()
    pickup_changed = (
        update.get("pickup_date")
        and existing.get("pickup_date")
        and update["pickup_date"] != existing["pickup_date"]
    )
    zeit_geaendert = (
        "pickup_time" in update
        and update.get("pickup_time") != existing.get("pickup_time")
    )
    # Wenn sich der Status ändert (vor allem zu "abgeholt" oder "nicht
    # abgeholt"), status_changed_at auffrischen, damit der Cleanup-Job
    # seine Fristen korrekt berechnen kann.
    if "status" in update and update["status"] != existing.get("status"):
        # Vereinheitlichter Abschluss: Ist ein FAHRER zugeteilt, entsteht
        # "abgeholt" ausschliesslich ueber dessen unterschriebenes
        # Abholprotokoll — nicht per Hand im Buero (sonst gaebe es
        # "abgeholt" ohne Beweisdokument). Termine OHNE Fahrer (Abholung
        # durch den Haendler selbst) bleiben manuell abschliessbar.
        if update["status"] == "abgeholt" and existing.get("driver_id"):
            final_proto = await db.pickup_protocols.find_one(
                {"appointment_id": appt_id, "status": "final",
                 "superseded": {"$ne": True}}, {"_id": 0, "id": 1})
            if not final_proto:
                raise HTTPException(409, "Für diesen Termin ist ein Fahrer "
                                         "eingeteilt. 'Abgeholt' entsteht "
                                         "automatisch, sobald der Fahrer das "
                                         "Abholprotokoll unterschrieben "
                                         "abschließt.")
        update["status_changed_at"] = now_iso()
        # Ein manueller Statuswechsel hebt eine evtl. bereits geleerte
        # Markierung auf, falls jemand den Status zurückdreht.
        if update["status"] not in {"abgeholt", "nicht abgeholt"}:
            update["assets_cleaned_at"] = None
    await db.appointments.update_one({"id": appt_id}, {"$set": update})
    # Lebenszyklus des Fahrzeugs nachziehen (abgeholt / nicht abgeholt).
    vehicle_id = existing.get("vehicle_id")
    if vehicle_id and update.get("status") != existing.get("status"):
        if update.get("status") == "abgeholt":
            await try_set_lifecycle(vehicle_id, user["dealer_id"], "abgeholt", user=user)
        elif update.get("status") == "nicht abgeholt":
            await try_set_lifecycle(vehicle_id, user["dealer_id"], "nicht_abgeholt", user=user)
    # Verschobener Abholtermin -> Kaufvertrag mit dem NEUEN Datum neu
    # erzeugen. Das PDF ist eine gespeicherte Datei und wuerde sonst
    # dauerhaft den alten Termin zeigen (Wunsch 08/2026).
    vertrag_aktualisiert = False
    if (pickup_changed or zeit_geaendert) and existing.get("contract_id"):
        from routes.contracts import regenerate_contract_for_pickup
        vertrag_aktualisiert = await regenerate_contract_for_pickup(
            contract_id=existing["contract_id"],
            dealer_id=user["dealer_id"],
            user=user,
            pickup_date=update.get("pickup_date"),
            pickup_time=update.get("pickup_time"),
        )

    await log_activity(user["dealer_id"], user["id"], "termin.aktualisiert", ref=appt_id,
                       meta={"pickup_changed": pickup_changed,
                             "vertrag_aktualisiert": vertrag_aktualisiert})
    return {"ok": True, "pickup_date_changed": pickup_changed,
            "contract_updated": vertrag_aktualisiert}


@router.get("/appointments/{appt_id}/report")
async def get_pickup_report(appt_id: str, versions: int = 0,
                            user=Depends(current_firma)):
    """Händler liest den Abholbericht des Fahrers (aktuelle Version).
    Mit ?versions=1 werden auch alte (ersetzte) Versionen mitgeliefert."""
    appt = await db.appointments.find_one(
        {"id": appt_id, "dealer_id": user["dealer_id"]}, {"_id": 0, "id": 1},
    )
    if not appt:
        raise HTTPException(404, "Termin nicht gefunden")
    current = await db.pickup_reports.find_one(
        {"appointment_id": appt_id, "superseded": {"$ne": True}}, {"_id": 0},
    )
    out: Dict[str, Any] = {"report": current}
    if versions:
        out["versions"] = await db.pickup_reports.find(
            {"appointment_id": appt_id}, {"_id": 0},
        ).sort("version", -1).to_list(20)
    return out


@router.delete("/appointments/{appt_id}")
async def delete_appointment(appt_id: str, user=Depends(current_firma)):
    res = await db.appointments.delete_one({"id": appt_id, "dealer_id": user["dealer_id"]})
    if not res.deleted_count:
        raise HTTPException(404, "Termin nicht gefunden")
    return {"ok": True}


@router.get("/appointments/{appt_id}/pickup-order.pdf")
async def get_pickup_order_pdf(appt_id: str, download: int = 0,
                               user=Depends(current_firma)):
    """Erzeugt das Abholprotokoll (Übergabe-PDF für den Fahrer) on-demand.

    Inhalte:
      * Kopfblock mit Händler, Fahrer, Abholort/Verkäufer
      * Fahrzeugdaten-Check (○ stimmt / ○ weicht ab) aus Vertrag + Fahrzeug
      * Dokumente- und Ausstattungs-Check
      * Skizze mit Kaufvertrags-Schäden (pre-markiert)
      * Leere Skizze + Bemerkungen + Unterschriften

    `download=1` erzwingt Content-Disposition: attachment (Download-Dialog),
    andernfalls wird das PDF inline im Browser-Viewer geöffnet (Druck
    funktioniert auf beiden Wegen identisch).
    """
    appt = await db.appointments.find_one(
        {"id": appt_id, "dealer_id": user["dealer_id"]}, {"_id": 0},
    )
    if not appt:
        raise HTTPException(404, "Termin nicht gefunden")

    vehicle: Dict[str, Any] = {}
    if appt.get("vehicle_id"):
        v_doc = await db.vehicles.find_one(
            {"id": appt["vehicle_id"], "dealer_id": user["dealer_id"]},
            {"_id": 0},
        ) or {}
        # Fahrzeugdaten liegen unter v["data"]; Top-Level enthält nur Meta
        # (id, dealer_id, …). Für den PDF-Builder brauchen wir die Sachdaten.
        vehicle = dict(v_doc.get("data") or {})
        # Sicherstellen, dass IDs durchgereicht werden, falls der PDF-Builder
        # sie braucht.
        if v_doc.get("mobile_ad_id"):
            vehicle.setdefault("mobile_ad_id", v_doc["mobile_ad_id"])

    contract: Dict[str, Any] = {}
    if appt.get("contract_id"):
        doc = await db.generated_pdfs.find_one(
            {"id": appt["contract_id"], "dealer_id": user["dealer_id"]},
            {"_id": 0, "contract_data": 1, "make": 1, "model": 1,
             "seller_name": 1, "seller_phone": 1, "seller_email": 1,
             "purchase_price": 1},
        )
        if doc:
            contract = dict(doc.get("contract_data") or {})
            # Vervollständige evtl. fehlende Top-Level-Felder.
            contract.setdefault("seller_name", doc.get("seller_name") or "")
            contract.setdefault("seller_phone", doc.get("seller_phone") or "")
            contract.setdefault("seller_email", doc.get("seller_email") or "")
            contract.setdefault("purchase_price", doc.get("purchase_price"))

    driver: Dict[str, Any] = {}
    if appt.get("driver_id"):
        da = await db.driver_accounts.find_one(
            {"id": appt["driver_id"]},
            {"_id": 0, "id": 1, "display_name": 1, "email": 1, "driver_code": 1},
        )
        if da:
            driver = {"id": da["id"], "name": da.get("display_name"),
                      "email": da.get("email"), "driver_code": da.get("driver_code")}

    dealer = await db.dealers.find_one(
        {"id": user["dealer_id"]}, {"_id": 0},
    ) or {}

    try:
        from pickup_pdf_service import build_pickup_pdf
        # CPU-gebundene PDF-Erzeugung in Thread auslagern (Event-Loop frei).
        pdf_bytes = await asyncio.to_thread(
            build_pickup_pdf,
            appointment=appt, vehicle=vehicle, contract=contract,
            dealer=dealer, driver=driver,
        )
    except Exception as exc:
        log.exception("pickup PDF build failed for %s", appt_id)
        raise HTTPException(500, "PDF-Erzeugung fehlgeschlagen.")

    await log_activity(user["dealer_id"], user["id"],
                       "abholauftrag.erzeugt", ref=appt_id)

    label = (vehicle.get("make_label") or vehicle.get("make") or "Fahrzeug")
    model_label = (vehicle.get("model_label") or vehicle.get("model") or "")
    safe = "".join(
        ch if ch.isalnum() or ch in "-_" else "_"
        for ch in f"{label}_{model_label}".strip("_")
    ) or "Abholauftrag"
    filename = f"Abholauftrag_{safe}_{datetime.now().strftime('%Y%m%d')}.pdf"
    disposition = "attachment" if download else "inline"
    return Response(
        content=pdf_bytes,
        media_type="application/pdf",
        headers={
            "Content-Disposition": f'{disposition}; filename="{filename}"',
            "Cache-Control": "no-store",
        },
    )
