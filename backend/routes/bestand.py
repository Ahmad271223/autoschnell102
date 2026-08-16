"""Bestand & Fahrzeugakte (B2B-Händlermodul, Phase 1).

- Nach-Abholung-Entscheidung (nur Händler-Hauptaccount)
- Bestandsverwaltung: Standort, Notizen, Kosten, 50-Tage-Frist
- Fahrzeugakte: aggregierte Sicht über alle vorhandenen Collections
- Abweichungs-Übernahme (Diff Einkauf vs. Abholung)
- Manuell hinzugefügte Fahrzeuge (source: "manuell")
"""
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Literal, Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from deps import clean_doc, current_user, db, log_activity, now_iso
from lifecycle import LifecycleError, set_lifecycle

router = APIRouter()

BESTAND_RETENTION_DAYS = 50


async def current_haendler(user=Depends(current_user)):
    """NUR der Händler-Hauptaccount. Strikte Rollentrennung (08/2026):
    Admins verwalten die Plattform, handeln aber nicht — sonst landen z.B.
    Sucher versehentlich unter der Admin-Firma. Sucher haben hier ebenfalls
    keinen Zugriff: Bestands-/Verkaufsentscheidungen sind Chefsache."""
    if user.get("role") != "dealer":
        raise HTTPException(403, "Nur der Händler-Hauptaccount darf das "
                                 "(Admins bitte mit einem Händler-Account arbeiten)")
    return user


async def current_firma(user=Depends(current_user)):
    """Lese-Zugriff auf Bestand/Akte: Chef UND seine Sucher. Admins und
    Zwischenhändler (b2b_buyer) haben hier nichts zu suchen — strikte
    Rollentrennung, auch wenn die Liste für sie ohnehin leer wäre."""
    if user.get("role") not in ("dealer", "sucher"):
        raise HTTPException(403, "Nur für Händler-Accounts (Chef und Sucher)")
    return user


# ---------- Models ----------
class DecisionIn(BaseModel):
    decision: Literal["bestand", "verkaufsentwurf", "loeschen"]


class BestandUpdateIn(BaseModel):
    location: Optional[str] = Field(default=None, max_length=300)
    notes: Optional[str] = Field(default=None, max_length=10000)
    costs: Optional[List[Dict[str, Any]]] = None  # [{label, amount}]


class ApplyDeviationsIn(BaseModel):
    deviation_ids: List[str] = Field(min_length=1, max_length=50)


class ManualVehicleIn(BaseModel):
    make_label: str = Field(min_length=1, max_length=100)
    model_label: str = Field(min_length=1, max_length=150)
    model_description: str = Field(default="", max_length=300)
    first_registration: str = Field(default="", max_length=20)
    mileage: Optional[int] = Field(default=None, ge=0, le=3_000_000)
    fuel_label: str = Field(default="", max_length=50)
    gearbox_label: str = Field(default="", max_length=50)
    power_kw: Optional[int] = Field(default=None, ge=0, le=2000)
    power_ps: Optional[int] = Field(default=None, ge=0, le=3000)
    color: str = Field(default="", max_length=80)
    vin: str = Field(default="", max_length=30)
    previous_owners: str = Field(default="", max_length=10)
    features: List[str] = Field(default_factory=list, max_length=80)
    description: str = Field(default="", max_length=20000)
    purchase_price: Optional[float] = Field(default=None, ge=0)


def _clean_costs(costs: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    out = []
    for c in (costs or [])[:30]:
        label = str(c.get("label", "")).strip()[:100]
        try:
            amount = round(float(c.get("amount", 0)), 2)
        except (TypeError, ValueError):
            amount = 0.0
        if label:
            out.append({"label": label, "amount": amount})
    return out


# =========================================================
#            NACH-ABHOLUNG-ENTSCHEIDUNG
# =========================================================
@router.post("/vehicles/{vehicle_id}/decision")
async def vehicle_decision(vehicle_id: str, body: DecisionIn,
                           user=Depends(current_haendler)):
    """Was passiert mit dem abgeholten Fahrzeug?
    bestand = 50 Tage speichern · verkaufsentwurf = weiterverkaufen ·
    loeschen = Fotos + Fahrzeugdaten entfernen (Vertrag/Historie bleiben)."""
    v = await db.vehicles.find_one(
        {"id": vehicle_id, "dealer_id": user["dealer_id"]}, {"_id": 0})
    if not v:
        raise HTTPException(404, "Fahrzeug nicht gefunden")

    if body.decision == "loeschen":
        try:
            await set_lifecycle(vehicle_id, user["dealer_id"], "geloescht", user=user)
        except LifecycleError as exc:
            raise HTTPException(400, str(exc))
        # Fotos sofort räumen — Vertrag, Abholbericht, Historie bleiben.
        data = v.get("data") or {}
        for key in ("image_urls", "images", "photos", "pictures"):
            if data.get(key):
                data[key] = []
        await db.vehicles.update_one(
            {"id": vehicle_id, "dealer_id": user["dealer_id"]},
            {"$set": {"data": data, "deleted_at": now_iso()}},
        )
        await log_activity(user["dealer_id"], user["id"],
                           "fahrzeug.entscheidung.geloescht", ref=vehicle_id)
        return {"ok": True, "lifecycle": "geloescht"}

    target = "bestand" if body.decision == "bestand" else "verkaufsentwurf"
    try:
        await set_lifecycle(vehicle_id, user["dealer_id"], target, user=user)
    except LifecycleError as exc:
        raise HTTPException(400, str(exc))

    update: Dict[str, Any] = {}
    if body.decision == "bestand":
        expires = (datetime.now(timezone.utc)
                   + timedelta(days=BESTAND_RETENTION_DAYS)).isoformat()
        existing_bestand = v.get("bestand") or {}
        update["bestand"] = {
            **existing_bestand,
            "saved_at": now_iso(),
            "expires_at": expires,
        }
    else:
        # Weiterverkauf: keine automatische Löschfrist.
        b = v.get("bestand") or {}
        b["expires_at"] = None
        b.setdefault("saved_at", now_iso())
        update["bestand"] = b
    if update:
        await db.vehicles.update_one(
            {"id": vehicle_id, "dealer_id": user["dealer_id"]}, {"$set": update})
    await log_activity(user["dealer_id"], user["id"],
                       f"fahrzeug.entscheidung.{body.decision}", ref=vehicle_id)
    return {"ok": True, "lifecycle": target,
            "expires_at": update.get("bestand", {}).get("expires_at")}


@router.put("/vehicles/{vehicle_id}/bestand")
async def update_bestand(vehicle_id: str, body: BestandUpdateIn,
                         user=Depends(current_haendler)):
    v = await db.vehicles.find_one(
        {"id": vehicle_id, "dealer_id": user["dealer_id"]}, {"_id": 0, "bestand": 1})
    if not v:
        raise HTTPException(404, "Fahrzeug nicht gefunden")
    b = v.get("bestand") or {}
    if body.location is not None:
        b["location"] = body.location.strip()
    if body.notes is not None:
        b["notes"] = body.notes.strip()
    if body.costs is not None:
        b["costs"] = _clean_costs(body.costs)
    await db.vehicles.update_one(
        {"id": vehicle_id, "dealer_id": user["dealer_id"]},
        {"$set": {"bestand": b, "updated_at": now_iso()}})
    return {"ok": True, "bestand": b}


# =========================================================
#                     BESTANDSLISTE
# =========================================================
@router.get("/bestand")
async def list_bestand(user=Depends(current_firma),
                       lifecycle: Optional[str] = None,
                       source: Optional[str] = None):
    """Fahrzeugbestand des Händlers mit Lifecycle-/Quellen-Filter.
    Liefert zusätzlich Zählergruppen für die Dashboard-Kacheln."""
    query: Dict[str, Any] = {"dealer_id": user["dealer_id"],
                             "lifecycle": {"$nin": ["geloescht"]}}
    if lifecycle:
        query["lifecycle"] = lifecycle
    if source in ("plattform", "manuell"):
        query["source"] = source
    items = await db.vehicles.find(query, {"_id": 0}).sort(
        "lifecycle_changed_at", -1).to_list(500)

    # Restlaufzeit für Bestandsfahrzeuge berechnen (Warnstufen im Frontend).
    now = datetime.now(timezone.utc)
    for it in items:
        exp = (it.get("bestand") or {}).get("expires_at")
        if it.get("lifecycle") == "bestand" and exp:
            try:
                days_left = (datetime.fromisoformat(exp) - now).days
                it["retention_days_left"] = max(0, days_left)
            except ValueError:
                pass

    counts: Dict[str, int] = {}
    async for row in db.vehicles.aggregate([
        {"$match": {"dealer_id": user["dealer_id"],
                    "lifecycle": {"$nin": ["geloescht"]}}},
        {"$group": {"_id": "$lifecycle", "n": {"$sum": 1}}},
    ]):
        counts[row["_id"] or "unbekannt"] = row["n"]
    return {"items": items, "counts": counts}


# =========================================================
#                     FAHRZEUGAKTE
# =========================================================
@router.get("/vehicles/{vehicle_id}/akte")
async def vehicle_akte(vehicle_id: str, user=Depends(current_firma)):
    """Durchgehende Fahrzeugakte: aggregiert alle vorhandenen Informationen
    zu einem Fahrzeug — ohne Datendopplung, direkt aus den Quell-Collections."""
    v = await db.vehicles.find_one(
        {"id": vehicle_id, "dealer_id": user["dealer_id"]}, {"_id": 0})
    if not v:
        raise HTTPException(404, "Fahrzeug nicht gefunden")

    contracts = await db.generated_pdfs.find(
        {"vehicle_id": vehicle_id, "dealer_id": user["dealer_id"]},
        {"_id": 0, "pdf_b64": 0},
    ).sort("created_at", -1).to_list(10)

    appointments = await db.appointments.find(
        {"vehicle_id": vehicle_id, "dealer_id": user["dealer_id"]},
        {"_id": 0},
    ).sort("created_at", -1).to_list(10)

    report = await db.pickup_reports.find_one(
        {"vehicle_id": vehicle_id, "dealer_id": user["dealer_id"],
         "superseded": {"$ne": True}}, {"_id": 0})

    comparisons = await db.vehicle_comparisons.find(
        {"mobile_ad_id": v.get("mobile_ad_id"), "dealer_id": user["dealer_id"]},
        {"_id": 0, "created_at": 1, "source": 1},
    ).sort("created_at", -1).to_list(20) if v.get("mobile_ad_id") else []

    listings = await db.resale_listings.find(
        {"vehicle_id": vehicle_id, "dealer_id": user["dealer_id"],
         "status": {"$ne": "geloescht"}},
        {"_id": 0},
    ).sort("created_at", -1).to_list(5)

    history = await db.activity_logs.find(
        {"ref": vehicle_id, "dealer_id": user["dealer_id"]},
        {"_id": 0},
    ).sort("created_at", -1).to_list(100)

    # Restlaufzeit
    retention_days_left = None
    exp = (v.get("bestand") or {}).get("expires_at")
    if v.get("lifecycle") == "bestand" and exp:
        try:
            retention_days_left = max(
                0, (datetime.fromisoformat(exp) - datetime.now(timezone.utc)).days)
        except ValueError:
            pass

    return {
        "vehicle": v,
        "retention_days_left": retention_days_left,
        "contracts": contracts,
        "appointments": appointments,
        "pickup_report": report,
        "comparisons": comparisons,
        "listings": listings,
        "history": history,
    }


# =========================================================
#        ABWEICHUNGEN ÜBERNEHMEN (Diff Einkauf/Abholung)
# =========================================================
# Mapping Abweichungs-Feld → Fahrzeugdaten-Feld (nur strukturierte Felder
# lassen sich automatisch übernehmen; Rest landet als bekannter Mangel).
_FIELD_MAP = {"mileage": "mileage", "keys": "keys_count"}


@router.post("/vehicles/{vehicle_id}/apply-deviations")
async def apply_deviations(vehicle_id: str, body: ApplyDeviationsIn,
                           user=Depends(current_haendler)):
    """Übernimmt ausgewählte Abholungs-Abweichungen in die Fahrzeugdaten.
    Strukturiert (km, Schlüssel) → Datenfeld; alles andere → known_defects."""
    v = await db.vehicles.find_one(
        {"id": vehicle_id, "dealer_id": user["dealer_id"]}, {"_id": 0})
    if not v:
        raise HTTPException(404, "Fahrzeug nicht gefunden")
    report = await db.pickup_reports.find_one(
        {"vehicle_id": vehicle_id, "dealer_id": user["dealer_id"],
         "superseded": {"$ne": True}}, {"_id": 0})
    if not report:
        raise HTTPException(404, "Kein Abholbericht vorhanden")

    by_id = {d["id"]: d for d in report.get("deviations", [])}
    data = v.get("data") or {}
    known_defects = list(v.get("known_defects") or [])
    applied = []
    for dev_id in body.deviation_ids:
        d = by_id.get(dev_id)
        if not d:
            continue
        target_field = _FIELD_MAP.get(d.get("field"))
        if target_field == "mileage" and report.get("mileage_at_pickup"):
            data["mileage"] = report["mileage_at_pickup"]
            applied.append({"feld": "Kilometerstand",
                            "neu": report["mileage_at_pickup"]})
        elif d.get("actual") and target_field:
            data[target_field] = d["actual"]
            applied.append({"feld": d.get("label"), "neu": d["actual"]})
        else:
            txt = d.get("label") or "Abweichung"
            if d.get("actual"):
                txt += f": {d['actual']}"
            if txt not in known_defects:
                known_defects.append(txt)
            applied.append({"mangel": txt})

    # km auch ohne explizite Abweichungs-ID übernehmen, wenn gewünscht
    await db.vehicles.update_one(
        {"id": vehicle_id, "dealer_id": user["dealer_id"]},
        {"$set": {"data": data, "known_defects": known_defects,
                  "deviations_applied_at": now_iso(),
                  "updated_at": now_iso()}})
    await log_activity(user["dealer_id"], user["id"],
                       "fahrzeug.abweichungen.uebernommen", ref=vehicle_id,
                       meta={"anzahl": len(applied)})
    return {"ok": True, "applied": applied, "known_defects": known_defects}


# =========================================================
#              MANUELL HINZUGEFÜGTE FAHRZEUGE
# =========================================================
@router.post("/vehicles/manual")
async def create_manual_vehicle(body: ManualVehicleIn,
                                user=Depends(current_haendler)):
    """Händler trägt ein bereits vorhandenes / extern gekauftes Fahrzeug ein.
    Landet direkt im Bestand (source: manuell) und kann danach genau wie
    Plattform-Fahrzeuge verkauft werden."""
    vid = f"m_{uuid.uuid4().hex[:12]}"
    data = body.model_dump(exclude={"purchase_price"})
    expires = (datetime.now(timezone.utc)
               + timedelta(days=BESTAND_RETENTION_DAYS)).isoformat()
    doc = {
        "id": vid, "dealer_id": user["dealer_id"],
        "mobile_ad_id": None,
        "source": "manuell",
        "data": data,
        "purchase_price": body.purchase_price,
        "lifecycle": "bestand",
        "lifecycle_changed_at": now_iso(),
        "bestand": {"saved_at": now_iso(), "expires_at": expires},
        "status": "manuell",
        "created_at": now_iso(), "updated_at": now_iso(),
    }
    await db.vehicles.insert_one(doc)
    await log_activity(user["dealer_id"], user["id"],
                       "fahrzeug.manuell.angelegt", ref=vid,
                       meta={"fahrzeug": f"{body.make_label} {body.model_label}"})
    return clean_doc(doc)


@router.put("/vehicles/manual/{vehicle_id}")
async def update_manual_vehicle(vehicle_id: str, body: ManualVehicleIn,
                                user=Depends(current_haendler)):
    v = await db.vehicles.find_one(
        {"id": vehicle_id, "dealer_id": user["dealer_id"], "source": "manuell"},
        {"_id": 0, "id": 1})
    if not v:
        raise HTTPException(404, "Manuelles Fahrzeug nicht gefunden")
    await db.vehicles.update_one(
        {"id": vehicle_id, "dealer_id": user["dealer_id"]},
        {"$set": {"data": body.model_dump(exclude={"purchase_price"}),
                  "purchase_price": body.purchase_price,
                  "updated_at": now_iso()}})
    return {"ok": True}
