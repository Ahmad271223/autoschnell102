"""Weiterverkauf: Inseratsentwürfe, Margen-Rechner, Status-Workflow.

Phase 1: entwurf → verkaufsbereit (kein Marktplatz, kein Kontingent).
Phase 3 ergänzt: veroeffentlicht (erst DANN zählt das Monatskontingent,
pro Inserat nur einmal je Abrechnungszeitraum — `counted_periods`).

`data` ist bewusst eine KOPIE der Fahrzeugdaten: spätere Änderungen an der
Fahrzeugakte dürfen ein bestehendes Inserat nicht unbemerkt verändern.
"""
import base64
import uuid
from typing import Any, Dict, List, Literal, Optional

from pymongo import ReturnDocument
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from deps import clean_doc, current_user, db, log_activity, now_iso
from lifecycle import LifecycleError, set_lifecycle, try_set_lifecycle
from routes.bestand import current_haendler, _clean_costs

router = APIRouter()


# ---------- Models ----------
class ListingUpdateIn(BaseModel):
    title: Optional[str] = Field(default=None, max_length=200)
    description: Optional[str] = Field(default=None, max_length=30000)
    known_defects: Optional[List[str]] = Field(default=None, max_length=50)
    photo_mode: Optional[Literal["einkauf", "neu", "beide"]] = None
    price_public: Optional[float] = Field(default=None, ge=0)
    price_b2b: Optional[float] = Field(default=None, ge=0)
    price_network: Optional[float] = Field(default=None, ge=0)
    costs: Optional[List[Dict[str, Any]]] = None
    data: Optional[Dict[str, Any]] = None  # korrigierte Fahrzeugdaten


class PhotoUploadIn(BaseModel):
    photos_b64: List[str] = Field(min_length=1, max_length=20)


class ListingStatusIn(BaseModel):
    status: Literal["entwurf", "verkaufsbereit", "reserviert", "verkauft",
                    "zurueckgezogen"]
    sold_price: Optional[float] = Field(default=None, ge=0)


# ---------- Helpers ----------
def _build_title(data: dict) -> str:
    model = data.get("model_label") or data.get("model") or ""
    desc = data.get("model_description") or ""
    # Doppelung vermeiden, wenn die Modellbezeichnung den Modellnamen enthält.
    if model and desc and desc.lower().startswith(model.lower()):
        model = ""
    bits = [
        data.get("make_label") or data.get("make") or "",
        model,
        desc,
        data.get("gearbox_label") or "",
    ]
    feats = [f for f in (data.get("features") or [])
             if any(k in str(f).lower() for k in ("led", "navi", "pano", "ahk", "leder"))]
    title = " ".join(b for b in bits if b).strip()
    if feats:
        title += " " + " ".join(str(f).split("/")[0].strip() for f in feats[:3])
    return title[:200] or "Fahrzeug"


def _build_description(data: dict, known_defects: List[str]) -> str:
    lines = []
    name = f"{data.get('make_label') or ''} {data.get('model_label') or ''}".strip()
    if name:
        lines.append(f"{name} {data.get('model_description') or ''}".strip())
        lines.append("")
    facts = [
        ("Erstzulassung", data.get("first_registration")),
        ("Kilometerstand", f"{data.get('mileage'):,} km".replace(",", ".")
            if isinstance(data.get("mileage"), (int, float)) else data.get("mileage")),
        ("Kraftstoff", data.get("fuel_label") or data.get("fuel")),
        ("Getriebe", data.get("gearbox_label") or data.get("gearbox")),
        ("Leistung", f"{data.get('power_kw')} kW / {data.get('power_ps')} PS"
            if data.get("power_kw") or data.get("power_ps") else None),
        ("Farbe", data.get("color")),
        ("Vorbesitzer", data.get("previous_owners")),
    ]
    for label, value in facts:
        if value not in (None, "", "None kW / None PS"):
            lines.append(f"• {label}: {value}")
    feats = data.get("features") or []
    if feats:
        lines.append("")
        lines.append("Ausstattung:")
        lines.extend(f"• {f}" for f in feats[:40])
    if data.get("description"):
        lines.append("")
        lines.append(str(data["description"])[:5000])
    if known_defects:
        lines.append("")
        lines.append("Bekannte Mängel:")
        lines.extend(f"• {m}" for m in known_defects[:30])
    return "\n".join(lines)[:30000]


def _margin(listing: dict) -> dict:
    """Berechnet Kosten + erwartete Marge für die Anzeige."""
    purchase = listing.get("purchase_price") or 0
    costs = sum(c.get("amount", 0) for c in (listing.get("costs") or []))
    price = (listing.get("prices") or {}).get("public") or 0
    total_cost = round(purchase + costs, 2)
    return {
        "purchase_price": purchase,
        "costs_total": round(costs, 2),
        "total_cost": total_cost,
        "expected_margin": round(price - total_cost, 2) if price else None,
    }


def _with_margin(listing: dict) -> dict:
    listing["margin"] = _margin(listing)
    return listing


# =========================================================
#                 ENTWURF ERZEUGEN
# =========================================================
@router.post("/resale/draft/{vehicle_id}")
async def create_draft(vehicle_id: str, user=Depends(current_haendler)):
    """Erzeugt aus Fahrzeugakte + Abholbericht einen fertigen Inserats-
    entwurf (Titel, Beschreibung, Ausstattung, Fotos, bekannte Mängel).
    Existiert bereits ein aktiver Entwurf, wird dieser zurückgegeben."""
    v = await db.vehicles.find_one(
        {"id": vehicle_id, "dealer_id": user["dealer_id"]}, {"_id": 0})
    if not v:
        raise HTTPException(404, "Fahrzeug nicht gefunden")
    if v.get("lifecycle") not in ("vertrag_erstellt", "gekauft",
                                  "abholung_geplant", "abgeholt", "bestand",
                                  "verkaufsentwurf", "verkaufsbereit"):
        raise HTTPException(400, "Fahrzeug ist nicht im verkaufsfähigen Zustand "
                                 f"(Status: {v.get('lifecycle')})")

    existing = await db.resale_listings.find_one(
        {"vehicle_id": vehicle_id, "dealer_id": user["dealer_id"],
         "status": {"$in": ["entwurf", "verkaufsbereit"]}}, {"_id": 0})
    if existing:
        return _with_margin(existing)

    data = dict(v.get("data") or {})

    # Abweichungen aus dem Abholbericht automatisch einarbeiten.
    report = await db.pickup_reports.find_one(
        {"vehicle_id": vehicle_id, "dealer_id": user["dealer_id"],
         "superseded": {"$ne": True}}, {"_id": 0})
    known_defects = list(v.get("known_defects") or [])
    auto_notes = []
    if report:
        if report.get("mileage_at_pickup"):
            old_km = data.get("mileage")
            data["mileage"] = report["mileage_at_pickup"]
            if old_km and old_km != report["mileage_at_pickup"]:
                auto_notes.append(
                    f"Kilometerstand wurde von {old_km} auf "
                    f"{report['mileage_at_pickup']} aktualisiert (Abholung).")
        for d in report.get("deviations", []):
            if d.get("field") in ("damage", "tires", "warning_light", "other",
                                  "equipment", "documents"):
                txt = d.get("label") or "Abweichung"
                if d.get("actual"):
                    txt += f": {d['actual']}"
                if txt not in known_defects:
                    known_defects.append(txt)

    listing = {
        "id": str(uuid.uuid4()),
        "dealer_id": user["dealer_id"],
        "vehicle_id": vehicle_id,
        "status": "entwurf",
        "title": _build_title(data),
        "description": _build_description(data, known_defects),
        "data": data,                      # Kopie — bewusst entkoppelt
        "known_defects": known_defects,
        "auto_notes": auto_notes,
        "photos": {
            "mode": "einkauf",
            # Kleinanzeigen-Fahrzeuge speichern Fotos unter "images",
            # mobile.de/manuelle unter "image_urls" — beide Quellen nutzen.
            "einkauf_urls": list((data.get("image_urls")
                                  or data.get("images") or []))[:40],
            "uploaded_keys": [],
        },
        "prices": {"public": None, "b2b": None, "network": None},
        "purchase_price": v.get("purchase_price"),
        "costs": (v.get("bestand") or {}).get("costs") or [],
        "visibility": "public",
        "published_at": None,
        "counted_periods": [],
        "created_by": user["id"],
        "created_at": now_iso(), "updated_at": now_iso(),
    }
    await db.resale_listings.insert_one(listing)
    await try_set_lifecycle(vehicle_id, user["dealer_id"], "verkaufsentwurf",
                            user=user)
    await log_activity(user["dealer_id"], user["id"], "inserat.entwurf",
                       ref=listing["id"], meta={"vehicle_id": vehicle_id})
    return _with_margin(clean_doc(listing))


# =========================================================
#                 LESEN / BEARBEITEN
# =========================================================
@router.get("/resale")
async def list_listings(user=Depends(current_haendler), status: Optional[str] = None):
    query: Dict[str, Any] = {"dealer_id": user["dealer_id"],
                             "status": {"$ne": "geloescht"}}
    if status:
        query["status"] = status
    items = await db.resale_listings.find(query, {"_id": 0}) \
        .sort("updated_at", -1).to_list(300)
    return [_with_margin(i) for i in items]


@router.get("/resale/{listing_id}")
async def get_listing(listing_id: str, user=Depends(current_haendler)):
    l = await db.resale_listings.find_one(
        {"id": listing_id, "dealer_id": user["dealer_id"]}, {"_id": 0})
    if not l:
        raise HTTPException(404, "Inserat nicht gefunden")
    return _with_margin(l)


@router.put("/resale/{listing_id}")
async def update_listing(listing_id: str, body: ListingUpdateIn,
                         user=Depends(current_haendler)):
    l = await db.resale_listings.find_one(
        {"id": listing_id, "dealer_id": user["dealer_id"]}, {"_id": 0})
    if not l:
        raise HTTPException(404, "Inserat nicht gefunden")
    if l.get("status") in ("verkauft",):
        raise HTTPException(400, "Verkaufte Inserate können nicht bearbeitet werden")
    update: Dict[str, Any] = {"updated_at": now_iso()}
    if body.title is not None:
        update["title"] = body.title.strip()
    if body.description is not None:
        update["description"] = body.description
    if body.known_defects is not None:
        update["known_defects"] = [str(m)[:300] for m in body.known_defects]
    if body.photo_mode is not None:
        update["photos.mode"] = body.photo_mode
    prices = dict(l.get("prices") or {})
    for src, key in ((body.price_public, "public"), (body.price_b2b, "b2b"),
                     (body.price_network, "network")):
        if src is not None:
            prices[key] = round(float(src), 2) or None
    update["prices"] = prices
    if body.costs is not None:
        update["costs"] = _clean_costs(body.costs)
    if body.data is not None:
        # Nur bekannte Felder übernehmen, keine beliebigen Keys.
        allowed = {"make_label", "model_label", "model_description",
                   "first_registration", "mileage", "fuel_label",
                   "gearbox_label", "power_kw", "power_ps", "color", "vin",
                   "previous_owners", "features", "description",
                   "accident_free"}
        merged = dict(l.get("data") or {})
        for k, val in body.data.items():
            if k in allowed:
                merged[k] = val
        update["data"] = merged
    await db.resale_listings.update_one(
        {"id": listing_id, "dealer_id": user["dealer_id"]}, {"$set": update})
    fresh = await db.resale_listings.find_one(
        {"id": listing_id}, {"_id": 0})
    return _with_margin(fresh)


@router.delete("/resale/{listing_id}")
async def delete_listing(listing_id: str, user=Depends(current_haendler)):
    """Inserat loeschen (Soft-Delete). WICHTIG (Beschluss 08/2026): einmal
    veroeffentlichte Inserate zaehlen im Abrechnungszeitraum WEITER auf das
    Kontingent — Loeschen gibt den Slot NICHT frei (counted_periods bleibt).
    Verkaufte Inserate bleiben als Historie erhalten (kein Loeschen)."""
    l = await db.resale_listings.find_one(
        {"id": listing_id, "dealer_id": user["dealer_id"]}, {"_id": 0})
    if not l:
        raise HTTPException(404, "Inserat nicht gefunden")
    if l.get("status") == "verkauft":
        raise HTTPException(400, "Verkaufte Inserate koennen nicht geloescht "
                                 "werden (Verkaufs-Historie).")
    await db.resale_listings.update_one(
        {"id": listing_id, "dealer_id": user["dealer_id"]},
        {"$set": {"status": "geloescht", "deleted_at": now_iso(),
                  "updated_at": now_iso()}})
    # Fahrzeug zurueck in den Bestand (falls Uebergang erlaubt).
    if l.get("vehicle_id"):
        await try_set_lifecycle(l["vehicle_id"], user["dealer_id"], "bestand",
                                user=user)
    await log_activity(user["dealer_id"], user["id"], "inserat.geloescht",
                       ref=listing_id,
                       meta={"war_status": l.get("status"),
                             "kontingent_bleibt": bool(l.get("counted_periods"))})
    return {"ok": True, "hinweis": "Inserat geloescht. Bereits veroeffentlichte "
                                   "Inserate zaehlen im laufenden Monat weiter "
                                   "auf dein Kontingent."}


@router.post("/resale/{listing_id}/photos")
async def upload_photos(listing_id: str, body: PhotoUploadIn,
                        user=Depends(current_haendler)):
    """Neue Fotos hochladen (Storage-Abstraktion, kein Base64 in Mongo)."""
    l = await db.resale_listings.find_one(
        {"id": listing_id, "dealer_id": user["dealer_id"]},
        {"_id": 0, "photos": 1})
    if not l:
        raise HTTPException(404, "Inserat nicht gefunden")
    from storage_service import make_key, storage, StorageError
    keys = list((l.get("photos") or {}).get("uploaded_keys", []))
    if len(keys) + len(body.photos_b64) > 40:
        raise HTTPException(400, "Maximal 40 Fotos pro Inserat")
    added = []
    for b64 in body.photos_b64:
        try:
            raw = base64.b64decode(b64.split(",")[-1], validate=False)
            key = make_key("resale", user["dealer_id"], "foto.jpg")
            storage.save(key, raw)
            added.append(key)
        except (StorageError, ValueError) as exc:
            raise HTTPException(400, f"Foto konnte nicht gespeichert werden: {exc}")
    keys.extend(added)
    await db.resale_listings.update_one(
        {"id": listing_id, "dealer_id": user["dealer_id"]},
        {"$set": {"photos.uploaded_keys": keys, "updated_at": now_iso()}})
    return {"ok": True, "uploaded": [f"/api/files/{k}" for k in added],
            "total": len(keys)}


# =========================================================
#                 VERÖFFENTLICHEN (Phase 3 — Kontingent)
# =========================================================
class PublishIn(BaseModel):
    visibility: Literal["public", "private"] = "public"


class PhotoRemoveIn(BaseModel):
    # Entweder ein hochgeladener Storage-Key ODER eine Einkaufsfoto-URL.
    key: Optional[str] = Field(default=None, max_length=500)
    url: Optional[str] = Field(default=None, max_length=1000)


@router.post("/resale/{listing_id}/photos/remove")
async def remove_photo(listing_id: str, body: PhotoRemoveIn,
                       user=Depends(current_haendler)):
    """Einzelnes Bild aus dem Inserat entfernen — auch NACH der
    Veroeffentlichung (Aenderung ist sofort live). Hochgeladene Fotos
    werden zusaetzlich aus dem Storage geloescht; Einkaufsfotos werden
    nur aus dem Inserat genommen (das Original bleibt in der Akte)."""
    l = await db.resale_listings.find_one(
        {"id": listing_id, "dealer_id": user["dealer_id"]},
        {"_id": 0, "photos": 1})
    if not l:
        raise HTTPException(404, "Inserat nicht gefunden")
    photos = l.get("photos") or {}
    if body.key:
        keys = list(photos.get("uploaded_keys", []))
        if body.key not in keys:
            raise HTTPException(404, "Foto nicht gefunden")
        keys.remove(body.key)
        from storage_service import storage, StorageError
        try:
            storage.delete(body.key)
        except StorageError:
            pass
        await db.resale_listings.update_one(
            {"id": listing_id},
            {"$set": {"photos.uploaded_keys": keys, "updated_at": now_iso()}})
        return {"ok": True, "uploaded_keys": keys}
    if body.url:
        urls = list(photos.get("einkauf_urls", []))
        if body.url not in urls:
            raise HTTPException(404, "Foto nicht gefunden")
        urls.remove(body.url)
        await db.resale_listings.update_one(
            {"id": listing_id},
            {"$set": {"photos.einkauf_urls": urls, "updated_at": now_iso()}})
        return {"ok": True, "einkauf_urls": urls}
    raise HTTPException(400, "key oder url angeben")


@router.post("/resale/{listing_id}/publish")
async def publish_listing(listing_id: str, body: PublishIn,
                          user=Depends(current_haendler)):
    """Veröffentlicht ein verkaufsbereites Inserat auf dem Marktplatz.

    Kontingent-Regeln (Beschluss 05.08.2026):
    - Zählt NUR beim tatsächlichen ersten Publish im Abrechnungszeitraum.
    - Entwürfe zählen nie; Zurückziehen + Reaktivieren im selben Zeitraum
      zählt nicht erneut (counted_periods).
    - Kontingent voll → 402 mit Upgrade-Hinweis (kein Einzelkauf).
    """
    l = await db.resale_listings.find_one(
        {"id": listing_id, "dealer_id": user["dealer_id"]}, {"_id": 0})
    if not l:
        raise HTTPException(404, "Inserat nicht gefunden")
    if l.get("status") not in ("verkaufsbereit", "zurueckgezogen"):
        raise HTTPException(400, "Nur verkaufsbereite (oder zurückgezogene) "
                                 "Inserate können veröffentlicht werden")

    from routes.team import get_sale_plan_status
    plan = await get_sale_plan_status(user["dealer_id"])
    if not plan.get("active"):
        raise HTTPException(402, "Kein Verkaufspaket aktiv. Bitte im Bereich "
                                 "'Mitarbeiter / Sucher' ein Paket anfragen.")
    period_key = plan["period_key"]
    already = period_key in (l.get("counted_periods") or [])
    if not already:
        # Schritt 1: Den Abrechnungszeitraum ATOMAR am Inserat markieren.
        # Der $ne-Guard sorgt dafür, dass von BELIEBIG vielen gleichzeitigen
        # Publishes desselben Inserats genau EINER die Markierung setzt —
        # und nur DER beansprucht anschließend einen Kontingent-Slot.
        # (Vorher konnten zwei parallele Publishes desselben Inserats zwei
        # Slots ziehen — dauerhafte Überzählung.)
        marker = await db.resale_listings.update_one(
            {"id": listing_id, "dealer_id": user["dealer_id"],
             "counted_periods": {"$ne": period_key}},
            {"$addToSet": {"counted_periods": period_key}})
        quota = plan.get("quota")
        if marker.modified_count and quota:
            # Schritt 2: ATOMARE Kontingent-Beanspruchung (race-fest, auch
            # bei mehreren Worker-Prozessen): ein Zähler pro Händler+Zeitraum
            # wird atomar erhöht — jeder Gewinner bekommt eine EINDEUTIGE
            # Nummer. Wer über der Quota landet, gibt Slot UND Markierung
            # zurück und wird abgelehnt.
            did = user["dealer_id"]
            field = f"quota_usage.{period_key}"
            # Zähler einmalig aus dem Ist-Stand befüllen (idempotent, per
            # $exists-Guard gegen paralleles Doppel-Seeding). Das EIGENE
            # Inserat traegt schon die Markierung aus Schritt 1 — deshalb
            # ausklammern, sonst zaehlte es doppelt (Seed + $inc).
            seeded = await db.dealers.find_one(
                {"id": did}, {field: 1})
            if (seeded.get("quota_usage") or {}).get(period_key) is None:
                cur = await db.resale_listings.count_documents(
                    {"dealer_id": did, "counted_periods": period_key,
                     "id": {"$ne": listing_id}})
                await db.dealers.update_one(
                    {"id": did, field: {"$exists": False}},
                    {"$set": {field: cur}})
            claimed = await db.dealers.find_one_and_update(
                {"id": did},
                {"$inc": {field: 1}},
                projection={field: 1},
                return_document=ReturnDocument.AFTER)
            used_now = (claimed.get("quota_usage") or {}).get(period_key, 1)
            if used_now > quota:
                # Über der Quota → Slot und Markierung zurückgeben, ablehnen.
                await db.dealers.update_one({"id": did}, {"$inc": {field: -1}})
                await db.resale_listings.update_one(
                    {"id": listing_id, "dealer_id": user["dealer_id"]},
                    {"$pull": {"counted_periods": period_key}})
                raise HTTPException(402, f"Dein monatliches Kontingent von "
                                         f"{quota} Fahrzeugen ist erreicht. "
                                         "Upgrade auf ein größeres Paket oder "
                                         "Enterprise anfragen.")

    await db.resale_listings.update_one(
        {"id": listing_id, "dealer_id": user["dealer_id"]},
        {"$set": {"status": "veroeffentlicht",
                  "visibility": body.visibility,
                  "published_at": l.get("published_at") or now_iso(),
                  "updated_at": now_iso()}})
    if l.get("vehicle_id"):
        await try_set_lifecycle(l["vehicle_id"], user["dealer_id"],
                                "veroeffentlicht", user=user)
    await log_activity(user["dealer_id"], user["id"], "inserat.veroeffentlicht",
                       ref=listing_id,
                       meta={"sichtbarkeit": body.visibility,
                             "kontingent": f"{plan.get('used', 0) + (0 if already else 1)}/{plan.get('quota')}"})
    return {"ok": True, "status": "veroeffentlicht",
            "visibility": body.visibility}


# =========================================================
#                 STATUS-WORKFLOW
# =========================================================
_LISTING_TO_LIFECYCLE = {
    "entwurf": "verkaufsentwurf",
    "verkaufsbereit": "verkaufsbereit",
    "reserviert": "reserviert",
    "verkauft": "verkauft",
    "zurueckgezogen": "verkaufsbereit",
}


@router.post("/resale/{listing_id}/status")
async def set_listing_status(listing_id: str, body: ListingStatusIn,
                             user=Depends(current_haendler)):
    l = await db.resale_listings.find_one(
        {"id": listing_id, "dealer_id": user["dealer_id"]}, {"_id": 0})
    if not l:
        raise HTTPException(404, "Inserat nicht gefunden")
    current = l.get("status")
    new = body.status
    allowed = {
        "entwurf": {"verkaufsbereit"},
        "verkaufsbereit": {"entwurf", "reserviert", "verkauft", "zurueckgezogen"},
        "veroeffentlicht": {"reserviert", "verkauft", "zurueckgezogen"},
        "reserviert": {"verkauft", "verkaufsbereit"},
        "zurueckgezogen": {"verkaufsbereit", "entwurf"},
    }
    if new not in allowed.get(current, set()):
        raise HTTPException(400, f"Übergang '{current}' → '{new}' nicht erlaubt")

    if new == "verkaufsbereit" and current == "entwurf":
        # Mindestangaben prüfen, bevor das Inserat verkaufsfertig wird.
        if not (l.get("prices") or {}).get("public"):
            raise HTTPException(400, "Bitte zuerst einen Verkaufspreis eintragen")

    update: Dict[str, Any] = {"status": new, "updated_at": now_iso()}
    if new == "verkauft":
        update["sold_at"] = now_iso()
        if body.sold_price is not None:
            update["sold_price"] = round(float(body.sold_price), 2)
    await db.resale_listings.update_one(
        {"id": listing_id, "dealer_id": user["dealer_id"]}, {"$set": update})

    # Fahrzeug-Lebenszyklus synchron halten.
    vehicle_id = l.get("vehicle_id")
    if vehicle_id and new in _LISTING_TO_LIFECYCLE:
        await try_set_lifecycle(vehicle_id, user["dealer_id"],
                                _LISTING_TO_LIFECYCLE[new], user=user)
    await log_activity(user["dealer_id"], user["id"], f"inserat.{new}",
                       ref=listing_id)
    return {"ok": True, "status": new}
