"""Weiterverkauf: Inseratsentwürfe, Margen-Rechner, Status-Workflow.

Phase 1: entwurf → verkaufsbereit (kein Marktplatz, kein Kontingent).
Phase 3 ergänzt: veroeffentlicht (erst DANN zählt das Monatskontingent,
pro Inserat nur einmal je Abrechnungszeitraum — `counted_periods`).

`data` ist bewusst eine KOPIE der Fahrzeugdaten: spätere Änderungen an der
Fahrzeugakte dürfen ein bestehendes Inserat nicht unbemerkt verändern.
"""
import base64
import logging
import uuid
from typing import Annotated, Any, Dict, List, Literal, Optional

from pymongo import ReturnDocument
from dateien import signierte_datei_url   # signierte Foto-Links (Audit 09/2026)
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field, StringConstraints

from deps import (clean_doc, current_user, db, log_activity, log_activity_sicher,
                  now_iso)
from lifecycle import (ALLOWED_TRANSITIONS, LifecycleError, set_lifecycle,
                       try_set_lifecycle)
from routes.bestand import current_haendler, _clean_costs

log = logging.getLogger("autohandel")
router = APIRouter()
# Runde 17 (Nr. 289): create_draft nutzt set_lifecycle; die Best-effort-
# Variante bleibt am Modul verfuegbar (Aufrufer/Tests patchen den Namen).
_LIFECYCLE_BEST_EFFORT = try_set_lifecycle

# Nachpruefung Runde 14 (Nr. 28/29/67/89/90): verkaufte und geloeschte
# Inserate sind abgeschlossen — keine Bearbeitung, keine Fotos rein/raus.
# Verkaufte Inserate sind Beweis-Historie (Fotos, Preise, Maengel), geloeschte
# duerfen nicht "durch die Hintertuer" weiterleben.
_ABGESCHLOSSEN = ("verkauft", "geloescht")
# Nachpruefung Runde 14 (Nr. 106): alles, was noch kein Abschluss ist, gilt
# als aktives Inserat — je Fahrzeug darf es davon nur EINES geben.
_AKTIV = ("entwurf", "verkaufsbereit", "veroeffentlicht", "reserviert",
          "zurueckgezogen")
# Ein Foto ist im Storage auf MAX_IMAGE_BYTES (8 MB) begrenzt; Base64 ist
# 4/3 so gross, plus Data-URL-Praefix. Nachpruefung Runde 14 (Nr. 117):
# ohne Deckel je Einzelstring wurden 25-MB-Bloecke erst dekodiert und dann
# verworfen — jetzt scheitert der Riesenblock schon an der Validierung.
_B64_MAX_LEN = 12_000_000


# ---------- Models ----------
# Nachpruefung Runde 14 (Nr. 80): ohne allow_inf_nan=False nahm Pydantic
# Infinity/1e400 an; der Wert landete in Mongo und jede JSON-Antwort mit dem
# Inserat (auch die Liste der Firma) brach danach mit 500 ab.
class ListingUpdateIn(BaseModel):
    title: Optional[str] = Field(default=None, max_length=200)
    description: Optional[str] = Field(default=None, max_length=30000)
    # Runde 17 (Nr. 336/337): Einzelmangel und Kostenliste schon in der
    # Validierung gedeckelt — vorher lief ein Megabyte-String je Mangel bis
    # zum Kuerzen auf 300 Zeichen durch, die Kosten wurden erst in
    # _clean_costs auf 30 geschnitten.
    known_defects: Optional[List[Annotated[str, StringConstraints(max_length=300)]]] = \
        Field(default=None, max_length=50)
    photo_mode: Optional[Literal["einkauf", "neu", "beide"]] = None
    price_public: Optional[float] = Field(default=None, ge=0, allow_inf_nan=False)
    price_b2b: Optional[float] = Field(default=None, ge=0, allow_inf_nan=False)
    price_network: Optional[float] = Field(default=None, ge=0, allow_inf_nan=False)
    costs: Optional[List[Dict[str, Any]]] = Field(default=None, max_length=30)
    data: Optional[Dict[str, Any]] = None  # korrigierte Fahrzeugdaten


class PhotoUploadIn(BaseModel):
    photos_b64: List[Annotated[str, StringConstraints(max_length=_B64_MAX_LEN)]] = \
        Field(min_length=1, max_length=20)


class ListingStatusIn(BaseModel):
    status: Literal["entwurf", "verkaufsbereit", "reserviert", "verkauft",
                    "zurueckgezogen"]
    sold_price: Optional[float] = Field(default=None, ge=0, allow_inf_nan=False)


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
        # Nur vorhandene Teile ausgeben — sonst stand bei fehlendem kW
        # woertlich "None kW / 110 PS" im Inserat.
        ("Leistung", " / ".join(t for t in (
            f"{data.get('power_kw')} kW" if data.get("power_kw") else None,
            f"{data.get('power_ps')} PS" if data.get("power_ps") else None,
        ) if t) or None),
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


# ---------- Fahrzeug-Lebenszyklus (Nachpruefung Runde 14, Nr. 52/53/81) ----------
# Vorher lief jeder Inserats-Statuswechsel ueber try_set_lifecycle, das den
# LifecycleError schluckt: Inserat wechselte, Fahrzeug blieb haengen (z.B.
# dauerhaft "reserviert" ohne Inserat — kein neuer Entwurf mehr moeglich).
# Jetzt wird der Weg VOR dem Schreiben geprueft (409, wenn es keinen gibt)
# und danach mit set_lifecycle gesetzt, damit Fehler sichtbar bleiben.
#
# lifecycle.py kennt aus "reserviert" nur verkauft/veroeffentlicht. Die
# Freigabe einer Reservierung (Inserat reserviert -> verkaufsbereit bzw.
# Loeschen -> bestand) laeuft deshalb ueber den erlaubten Zwischenschritt
# "veroeffentlicht" — zwei Audit-Eintraege, aber kein stiller Desync.
# Fahrzeuge VOR dem Verkaufsblock (Altbestand ohne Entwurfs-Hook) holen den
# Schritt "verkaufsentwurf" nach; von dort aus fuehrt kein Weg direkt zu
# veroeffentlicht/reserviert — ein echter Desync bleibt also ein 409.
_LIFECYCLE_ZWISCHENSCHRITT = {
    "reserviert": "veroeffentlicht",
    "vertrag_erstellt": "verkaufsentwurf",
    "gekauft": "verkaufsentwurf",
    "abholung_geplant": "verkaufsentwurf",
    "abgeholt": "verkaufsentwurf",
    "bestand": "verkaufsentwurf",
}
# Nur in diesen Fahrzeugzustaenden gehoert das Fahrzeug "dem Inserat"; beim
# Loeschen eines Inserats wird sonst nichts zurueckgesetzt (Fahrzeug wurde
# z.B. schon archiviert/geloescht — ein 409 waere dann eine Sackgasse).
_RESALE_LIFECYCLES = ("verkaufsentwurf", "verkaufsbereit", "veroeffentlicht",
                      "reserviert")


def _lifecycle_pfad(current: Optional[str], ziel: str) -> List[str]:
    """Schrittfolge vom Fahrzeugstatus `current` zum Ziel; leer, wenn schon
    erreicht. Wirft LifecycleError, wenn weder direkt noch ueber den
    erlaubten Zwischenschritt ein Weg existiert (reine Funktion, testbar)."""
    current = current or "verglichen"
    if current == ziel:
        return []
    direkt = ALLOWED_TRANSITIONS.get(current, set())
    if ziel in direkt:
        return [ziel]
    zwischen = _LIFECYCLE_ZWISCHENSCHRITT.get(current)
    if zwischen and zwischen in direkt \
            and ziel in ALLOWED_TRANSITIONS.get(zwischen, set()):
        return [zwischen, ziel]
    raise LifecycleError(f"Übergang '{current}' → '{ziel}' ist nicht erlaubt")


async def _fahrzeug_lifecycle(vehicle_id: str, dealer_id: str) -> Optional[str]:
    """Aktueller Lebenszyklus des Fahrzeugs; None, wenn es das Fahrzeug nicht
    (mehr) gibt — dann ist nichts zu synchronisieren."""
    v = await db.vehicles.find_one({"id": vehicle_id, "dealer_id": dealer_id},
                                   {"_id": 0, "lifecycle": 1})
    if not v:
        return None
    return v.get("lifecycle") or "verglichen"


async def _lifecycle_pfad_oder_409(vehicle_id: Optional[str], dealer_id: str,
                                   ziel: str) -> List[str]:
    """Weg zum Ziel ermitteln, bevor am Inserat geschrieben wird."""
    if not vehicle_id:
        return []
    cur = await _fahrzeug_lifecycle(vehicle_id, dealer_id)
    if cur is None:
        return []
    try:
        return _lifecycle_pfad(cur, ziel)
    except LifecycleError as exc:
        raise HTTPException(409, "Fahrzeugstatus passt nicht zum Inserat: "
                                 f"{exc}")


async def _lifecycle_anwenden(vehicle_id: str, dealer_id: str,
                              pfad: List[str], user: dict) -> None:
    for schritt in pfad:
        await set_lifecycle(vehicle_id, dealer_id, schritt, user=user)


async def _anfragen_schliessen(listing_id: str, grund: str, *,
                               auch_akzeptierte: bool = False) -> int:
    """Nachpruefung Runde 14 (Nr. 54/26): Kaufanfragen eines Inserats
    beenden, das verkauft/geloescht/zurueckgezogen wird. Vorher blieben
    Verhandlungen auf geloeschten Inseraten dauerhaft "laufend" (beide
    Seiten konnten weiter kontern), und nach dem Aufheben einer Reservierung
    sah der Kaeufer weiter "fuer dich reserviert" (Status akzeptiert)."""
    from routes.marketplace import INTERESSE_OFFEN
    stati = list(INTERESSE_OFFEN)
    if auch_akzeptierte:
        stati.append("akzeptiert")
    res = await db.listing_interest.update_many(
        {"listing_id": listing_id, "status": {"$in": stati}},
        {"$set": {"status": "abgelehnt", "beendet_grund": grund,
                  "updated_at": now_iso()},
         "$push": {"history": {"von": "system", "aktion": grund,
                               "zeit": now_iso()}}})
    return res.modified_count


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
    # Nachpruefung Runde 14 (Nr. 106): vorher wurden nur entwurf/verkaufs-
    # bereit gesucht — ueber "zurueckgezogen" (Fahrzeug wieder verkaufsbereit)
    # entstand ein zweites Inserat, und beide liessen sich veroeffentlichen.
    # Jetzt zaehlt jedes aktive Inserat: reaktivierbare werden zurueck-
    # gegeben, live/reservierte blockieren mit 409. Diese Pruefung steht VOR
    # der Lebenszyklus-Pruefung, damit ein bestehendes Inserat nie hinter
    # einer irrefuehrenden "nicht verkaufsfaehig"-Meldung verschwindet.
    existing = await db.resale_listings.find_one(
        {"vehicle_id": vehicle_id, "dealer_id": user["dealer_id"],
         "status": {"$in": list(_AKTIV)}}, {"_id": 0})
    if existing:
        if existing.get("status") in ("veroeffentlicht", "reserviert"):
            raise HTTPException(409, "Fuer dieses Fahrzeug gibt es bereits ein "
                                     f"aktives Inserat (Status "
                                     f"'{existing['status']}')")
        return _with_margin(existing)

    if v.get("lifecycle") not in ("vertrag_erstellt", "gekauft",
                                  "abholung_geplant", "abgeholt", "bestand",
                                  "verkaufsentwurf", "verkaufsbereit"):
        raise HTTPException(400, "Fahrzeug ist nicht im verkaufsfähigen Zustand "
                                 f"(Status: {v.get('lifecycle')})")
    # Runde 17 (Nr. 289): den Weg zum Fahrzeugstatus "verkaufsentwurf" VOR
    # dem Insert pruefen (409 statt Entwurf ohne passendes Fahrzeug) — wie
    # in publish_listing. Vorher schluckte try_set_lifecycle den Fehler.
    pfad = await _lifecycle_pfad_oder_409(vehicle_id, user["dealer_id"],
                                          "verkaufsentwurf")

    data = dict(v.get("data") or {})

    # Abweichungen aus dem Abholbericht automatisch einarbeiten.
    # Nachpruefung Runde 14 (Nr. 45): find_one ohne Termin-/Versionsbezug
    # nahm bei mehreren Terminen den aeltesten Bericht (Kilometer/Schaeden
    # des falschen Termins). Der massgebliche Bericht (abgeholter Termin,
    # sonst juengster; hoechste Version) kommt aus abholbericht.py.
    report = None
    try:
        from abholbericht import massgeblicher_bericht
    except ImportError:            # Modul noch nicht ausgeliefert: alter Weg
        report = await db.pickup_reports.find_one(
            {"vehicle_id": vehicle_id, "dealer_id": user["dealer_id"],
             "superseded": {"$ne": True}}, {"_id": 0})
    else:
        report = await massgeblicher_bericht(db, vehicle_id, user["dealer_id"])
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
    # Runde 17 (Nr. 289): set_lifecycle statt try_ — ein Rennen am Fahrzeug
    # (zwischenzeitlich verkauft/geloescht) nimmt den Entwurf zurueck und
    # wird als 409 sichtbar, statt einen Entwurf ohne Fahrzeugbezug zu lassen.
    try:
        await _lifecycle_anwenden(vehicle_id, user["dealer_id"], pfad, user)
    except LifecycleError as exc:
        await db.resale_listings.delete_one({"id": listing["id"], "status": "entwurf"})
        raise HTTPException(409, "Fahrzeugstatus passt nicht zum Inserat: "
                                 f"{exc}")
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
    return [_mit_foto_urls(_with_margin(i)) for i in items]


@router.get("/resale/{listing_id}")
async def get_listing(listing_id: str, user=Depends(current_haendler)):
    # Nachpruefung Runde 14 (Nr. 28): geloeschte Inserate sind wie in der
    # Liste auch einzeln nicht mehr abrufbar.
    l = await db.resale_listings.find_one(
        {"id": listing_id, "dealer_id": user["dealer_id"],
         "status": {"$ne": "geloescht"}}, {"_id": 0})
    if not l:
        raise HTTPException(404, "Inserat nicht gefunden")
    return _mit_foto_urls(_with_margin(l))


def _mit_foto_urls(doc: dict) -> dict:
    """Signierte, kurzlebige Links zu den hochgeladenen Fotos (Audit 09/2026,
    Punkt 45) — die Oberflaeche baut keine /api/files-Pfade mehr selbst."""
    keys = ((doc.get("photos") or {}).get("uploaded_keys") or [])
    doc["photo_urls"] = [{"key": k, "url": signierte_datei_url(k)} for k in keys]
    return doc


@router.put("/resale/{listing_id}")
async def update_listing(listing_id: str, body: ListingUpdateIn,
                         user=Depends(current_haendler)):
    l = await db.resale_listings.find_one(
        {"id": listing_id, "dealer_id": user["dealer_id"]}, {"_id": 0})
    if not l:
        raise HTTPException(404, "Inserat nicht gefunden")
    status = l.get("status")
    # Nachpruefung Runde 14 (Nr. 28): geloeschte Inserate waren weiter
    # bearbeitbar (Titel, Preis, VIN) — jetzt wie verkaufte gesperrt.
    if status in _ABGESCHLOSSEN:
        raise HTTPException(400, "Verkaufte oder geloeschte Inserate können "
                                 "nicht bearbeitet werden")
    preis_gesendet = any(p is not None for p in (body.price_public,
                                                 body.price_b2b,
                                                 body.price_network))
    # Nachpruefung Runde 14 (Nr. 108): waehrend einer Reservierung ist der
    # Kaeufer an das gesehene Angebot gebunden — Preis, Fahrzeugdaten und
    # Maengel bleiben eingefroren; Titel/Beschreibung/Fotomodus/Kosten sind
    # weiter aenderbar. Ein Zustands-Snapshot waere ein eigenes Feature.
    if status == "reserviert" and (preis_gesendet or body.known_defects is not None
                                   or body.data is not None):
        raise HTTPException(400, "Preis, Fahrzeugdaten und Maengel sind waehrend "
                                 "einer Reservierung nicht aenderbar — zuerst die "
                                 "Reservierung aufheben")
    update: Dict[str, Any] = {"updated_at": now_iso()}
    if body.title is not None:
        update["title"] = body.title.strip()
    if body.description is not None:
        update["description"] = body.description
    if body.known_defects is not None:
        update["known_defects"] = [str(m)[:300] for m in body.known_defects]
    if body.photo_mode is not None:
        update["photos.mode"] = body.photo_mode
    # Nachpruefung Runde 14 (Nr. 91): Preise einzeln per Pfad schreiben statt
    # den ganzen Block aus dem gelesenen Stand zurueckzusetzen — zwei
    # parallele PUTs (public / network) loeschten sich sonst gegenseitig.
    prices = dict(l.get("prices") or {})
    for src, key in ((body.price_public, "public"), (body.price_b2b, "b2b"),
                     (body.price_network, "network")):
        if src is not None:
            prices[key] = round(float(src), 2) or None
            update[f"prices.{key}"] = prices[key]
    # Nachpruefung Runde 14 (Nr. 107): die Preispflicht galt nur beim Schritt
    # entwurf -> verkaufsbereit; danach machte price_public=0 aus einem
    # live sichtbaren Inserat eines "ohne Preis". Gilt fuer den gemergten
    # Wert (auch wenn nur ein anderes Preisfeld gesendet wurde).
    if status in ("verkaufsbereit", "veroeffentlicht", "reserviert") \
            and preis_gesendet and not prices.get("public"):
        raise HTTPException(400, "Ein verkaufsbereites oder veroeffentlichtes "
                                 "Inserat braucht einen oeffentlichen Verkaufspreis")
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
    # Nachpruefung Runde 14 (Nr. 89): bedingter Write auf den GELESENEN
    # Status — ein paralleler Verkauf/Loeschung/Reservierung zwischen Lesen
    # und Schreiben wird nicht mehr ueberschrieben (Lost Update).
    res = await db.resale_listings.update_one(
        {"id": listing_id, "dealer_id": user["dealer_id"], "status": status},
        {"$set": update})
    if res.matched_count == 0:
        raise HTTPException(409, "Inserat wurde zwischenzeitlich geaendert "
                                 "(verkauft, geloescht oder reserviert) — "
                                 "bitte neu laden")
    # Runde 15 (Nr. 6): Preis, Kosten, Maengel und Fahrzeugdaten eines
    # (auch live veroeffentlichten) Inserats aenderten sich ohne Spur —
    # Veroeffentlichung und Statuswechsel waren dagegen geloggt.
    felder = sorted(k for k in update if k != "updated_at")
    meta: Dict[str, Any] = {"felder": felder, "status": status}
    if any(k.startswith("prices.") for k in felder):
        meta["preise_alt"] = l.get("prices") or {}
        meta["preise_neu"] = prices
    if "data" in update:
        alt = l.get("data") or {}
        meta["fahrzeugdaten_geaendert"] = sorted(
            k for k, val in body.data.items() if k in allowed and alt.get(k) != val)
    await log_activity(user["dealer_id"], user["id"], "inserat.geaendert",
                       ref=listing_id, meta=meta)
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
    current = l.get("status")
    if current == "verkauft":
        raise HTTPException(400, "Verkaufte Inserate koennen nicht geloescht "
                                 "werden (Verkaufs-Historie).")
    if current == "geloescht":
        raise HTTPException(409, "Inserat ist bereits geloescht")
    # Nachpruefung Runde 14 (Nr. 53): Fahrzeug zurueck in den Bestand — den
    # Weg VOR dem Loeschen pruefen (aus "reserviert" ueber den
    # Zwischenschritt), statt den Fehler zu schlucken und das Fahrzeug ohne
    # Inserat in "reserviert" haengen zu lassen. Fahrzeuge ausserhalb des
    # Verkaufsblocks (archiviert, geloescht ...) werden nicht angefasst.
    vehicle_id = l.get("vehicle_id")
    pfad: List[str] = []
    if vehicle_id:
        cur = await _fahrzeug_lifecycle(vehicle_id, user["dealer_id"])
        if cur in _RESALE_LIFECYCLES:
            pfad = await _lifecycle_pfad_oder_409(vehicle_id, user["dealer_id"],
                                                  "bestand")
    # Nachpruefung Runde 14 (Nr. 90): bedingt auf den gelesenen Status — ein
    # paralleler Verkauf zwischen Lesen und Schreiben wuerde sonst durch
    # "geloescht" ueberschrieben (und das verkaufte Fahrzeug auf bestand
    # zurueckgesetzt). Lifecycle und Protokoll erst nach erfolgreichem Write.
    res = await db.resale_listings.update_one(
        {"id": listing_id, "dealer_id": user["dealer_id"], "status": current},
        {"$set": {"status": "geloescht", "deleted_at": now_iso(),
                  "updated_at": now_iso()}})
    if res.matched_count == 0:
        raise HTTPException(409, "Inserat wurde zwischenzeitlich verkauft oder "
                                 "geloescht — bitte neu laden")
    if pfad:
        try:
            await _lifecycle_anwenden(vehicle_id, user["dealer_id"], pfad, user)
        except LifecycleError as exc:
            # Rennen am Fahrzeug: Loeschung zuruecknehmen, Fehler sichtbar.
            await db.resale_listings.update_one(
                {"id": listing_id, "dealer_id": user["dealer_id"],
                 "status": "geloescht"},
                {"$set": {"status": current, "updated_at": now_iso()},
                 "$unset": {"deleted_at": ""}})
            raise HTTPException(409, "Fahrzeugstatus passt nicht zum Inserat: "
                                     f"{exc}")
    # Nachpruefung Runde 14 (Nr. 54): laufende Verhandlungen und eine
    # akzeptierte Reservierung enden mit dem Inserat.
    await _anfragen_schliessen(listing_id, "inserat_geloescht",
                               auch_akzeptierte=True)
    await log_activity(user["dealer_id"], user["id"], "inserat.geloescht",
                       ref=listing_id,
                       meta={"war_status": current,
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
        {"_id": 0, "photos": 1, "status": 1})
    if not l:
        raise HTTPException(404, "Inserat nicht gefunden")
    # Nachpruefung Runde 14 (Nr. 29): Fotos landeten auch auf geloeschten und
    # verkauften Inseraten im Storage — ohne Aufraeumer, dauerhaft.
    if l.get("status") in _ABGESCHLOSSEN:
        raise HTTPException(400, "Fuer verkaufte oder geloeschte Inserate koennen "
                                 "keine Fotos mehr hochgeladen werden")
    from storage_service import (make_key, storage, StorageError,
                                 validate_image_bytes, bild_verkleinern,
                                 loeschen_oder_vormerken, MAX_IMAGE_BYTES)
    keys = list((l.get("photos") or {}).get("uploaded_keys", []))
    if len(keys) + len(body.photos_b64) > 40:
        raise HTTPException(400, "Maximal 40 Fotos pro Inserat")
    def _alle_speichern() -> tuple:
        """Decode+Validierung+Save fuer bis zu 40 Fotos — als EIN Thread-Hop,
        damit der Event-Loop nicht sekundenlang steht (Review 09/2026).
        Liefert (gespeicherte Keys, Fehler|None); bei Fehler raeumt der
        Aufrufer die halb gespeicherten Dateien weg."""
        neu = []
        try:
            for b64 in body.photos_b64:
                # Nachpruefung Runde 14 (Nr. 117): Groesse VOR dem Decode
                # pruefen — ein zu grosser Block wird nicht erst dekodiert.
                if len(b64) > MAX_IMAGE_BYTES * 4 // 3 + 1024:
                    raise StorageError("Inserats-Foto zu gross (erlaubt "
                                       f"{MAX_IMAGE_BYTES // (1024 * 1024)} MB)")
                raw = base64.b64decode(b64.split(",")[-1], validate=False)
                # Groesse + Magic Bytes: nur echte Bilder, kein 20-MB-Blob,
                # keine umbenannten ausfuehrbaren Dateien.
                validate_image_bytes(raw, wo="Inserats-Foto")
                # Handyfotos kommen mit 4000 Bildpunkten Kante und
                # mehreren MB. Einmal verkleinern spart rund 90 Prozent
                # Speicher, ohne dass man im Inserat etwas sieht.
                raw = bild_verkleinern(raw, wo="Inserats-Foto")
                key = make_key("resale", user["dealer_id"], "foto.jpg")
                storage.save(key, raw)
                neu.append(key)
        except (StorageError, ValueError) as exc:
            return neu, exc
        return neu, None

    import asyncio as _asyncio
    added, fehler = await _asyncio.to_thread(_alle_speichern)
    if fehler is not None:
        # Halb gespeicherte wieder wegraeumen. Fehlschlaege werden NICHT
        # mehr verschluckt, sondern vorgemerkt (storage_delete_retry) und
        # vom Aufraeumjob nachgeholt (Go-Live-Audit 09/2026).
        for k in added:
            await loeschen_oder_vormerken(
                db, key=k, grund="inserat_upload_abbruch",
                dealer_id=user["dealer_id"])
        raise HTTPException(400, f"Foto konnte nicht gespeichert werden: {fehler}")
    # ATOMAR anhaengen ($push $each) statt die ganze Liste zu ueberschreiben:
    # das alte Lesen-Aendern-Schreiben verlor bei PARALLELEN Uploads aufs
    # selbe Inserat Referenzen (im Lasttest: hunderte Dateien ohne
    # DB-Eintrag). Das 40er-Limit prueft dieselbe Bedingung atomar mit —
    # der Verlierer eines Rennens raeumt seine Dateien wieder weg.
    # Nachpruefung Runde 14 (Nr. 29): Statusfilter im atomaren Write — wird
    # das Inserat waehrend des Uploads verkauft/geloescht, raeumt der
    # Verlierer seine Dateien wie beim 40er-Limit wieder weg.
    res = await db.resale_listings.update_one(
        {"id": listing_id, "dealer_id": user["dealer_id"],
         "status": {"$nin": list(_ABGESCHLOSSEN)},
         f"photos.uploaded_keys.{40 - len(added)}": {"$exists": False}},
        {"$push": {"photos.uploaded_keys": {"$each": added}},
         "$set": {"updated_at": now_iso()}})
    if res.modified_count == 0:
        for k in added:
            await loeschen_oder_vormerken(
                db, key=k, grund="inserat_foto_limit", dealer_id=user["dealer_id"])
        raise HTTPException(400, "Maximal 40 Fotos pro Inserat — oder das Inserat "
                                 "ist inzwischen verkauft/geloescht")
    doc = await db.resale_listings.find_one(
        {"id": listing_id, "dealer_id": user["dealer_id"]},
        {"_id": 0, "photos.uploaded_keys": 1})
    total = len((doc.get("photos") or {}).get("uploaded_keys") or [])
    await log_activity(user["dealer_id"], user["id"], "inserat.foto.hinzugefuegt",
                       ref=listing_id, meta={"anzahl": len(added), "gesamt": total,
                                             "status": l.get("status")})
    return {"ok": True, "uploaded": [signierte_datei_url(k) for k in added],
            "total": total}


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
        {"_id": 0, "photos": 1, "status": 1})
    if not l:
        raise HTTPException(404, "Inserat nicht gefunden")
    # Nachpruefung Runde 14 (Nr. 67, hoch): nach dem Verkauf sind die Fotos
    # Beweismaterial (Zustand bei Uebergabe) — sie duerfen weder aus dem
    # Inserat noch aus dem Storage verschwinden. Gilt auch fuer geloeschte.
    if l.get("status") in _ABGESCHLOSSEN:
        raise HTTPException(400, "Fotos verkaufter oder geloeschter Inserate "
                                 "bleiben als Historie erhalten")
    photos = l.get("photos") or {}
    if body.key:
        keys = list(photos.get("uploaded_keys", []))
        if body.key not in keys:
            raise HTTPException(404, "Foto nicht gefunden")
        keys.remove(body.key)
        from storage_service import loeschen_oder_vormerken
        # Nachpruefung Runde 14 (Nr. 67): ERST bedingt aus dem Inserat nehmen
        # ($pull mit Statusfilter, matched_count pruefen), DANN die Datei
        # loeschen — sonst loescht ein Rennen mit dem Verkauf die Datei
        # trotzdem. ATOMAR ($pull), weil parallele Loeschungen sich sonst
        # gegenseitig verdraengten.
        res = await db.resale_listings.update_one(
            {"id": listing_id, "dealer_id": user["dealer_id"],
             "status": {"$nin": list(_ABGESCHLOSSEN)},
             "photos.uploaded_keys": body.key},
            {"$pull": {"photos.uploaded_keys": body.key},
             "$set": {"updated_at": now_iso()}})
        if res.matched_count == 0:
            raise HTTPException(409, "Inserat wurde zwischenzeitlich verkauft "
                                     "oder geloescht — Foto bleibt erhalten")
        # Laesst sich die Datei nicht loeschen, wird sie vorgemerkt; der Key
        # bleibt im Inserat unter photos.loeschung_offen_keys erhalten (nicht
        # mehr sichtbar, aber nicht verloren) — die Nachholung entfernt ihn.
        ok = await loeschen_oder_vormerken(
            db, key=body.key, grund="inserat_foto_entfernt",
            dealer_id=user["dealer_id"],
            ref={"collection": "resale_listings", "id": listing_id,
                 "pull_key_from": "photos.loeschung_offen_keys"})
        if not ok:
            await db.resale_listings.update_one(
                {"id": listing_id, "dealer_id": user["dealer_id"]},
                {"$addToSet": {"photos.loeschung_offen_keys": body.key}})
        doc = await db.resale_listings.find_one(
            {"id": listing_id, "dealer_id": user["dealer_id"]},
            {"_id": 0, "photos.uploaded_keys": 1})
        # Runde 15 (Nr. 6): Foto-Entfernen ist sofort live und war nicht
        # nachvollziehbar (Hochladen ebenfalls).
        await log_activity(user["dealer_id"], user["id"], "inserat.foto.entfernt",
                           ref=listing_id, meta={"art": "upload", "key": body.key,
                                                 "status": l.get("status")})
        return {"ok": True, "uploaded_keys":
                (doc.get("photos") or {}).get("uploaded_keys") or []}
    if body.url:
        urls = list(photos.get("einkauf_urls", []))
        if body.url not in urls:
            raise HTTPException(404, "Foto nicht gefunden")
        res = await db.resale_listings.update_one(
            {"id": listing_id, "dealer_id": user["dealer_id"],
             "status": {"$nin": list(_ABGESCHLOSSEN)}},
            {"$pull": {"photos.einkauf_urls": body.url},
             "$set": {"updated_at": now_iso()}})
        if res.matched_count == 0:
            raise HTTPException(409, "Inserat wurde zwischenzeitlich verkauft "
                                     "oder geloescht — Foto bleibt erhalten")
        doc = await db.resale_listings.find_one(
            {"id": listing_id, "dealer_id": user["dealer_id"]},
            {"_id": 0, "photos.einkauf_urls": 1})
        await log_activity(user["dealer_id"], user["id"], "inserat.foto.entfernt",
                           ref=listing_id, meta={"art": "einkauf", "url": body.url[:300],
                                                 "status": l.get("status")})
        return {"ok": True, "einkauf_urls":
                (doc.get("photos") or {}).get("einkauf_urls") or []}
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
    # SERIALISIERUNG je Inserat (Runde 5): Zwei gleichzeitige Publishes
    # desselben Inserats konnten auseinanderlaufen — der zweite sah die
    # Markierung des ersten und veroeffentlichte, waehrend der erste wegen
    # ueberschrittener Quote zurueckrollte: Inserat live, aber ungezaehlt.
    # Jetzt bekommt genau EINE Anfrage die Sperre; die andere wartet nicht,
    # sondern bekommt 409 und wiederholt.
    from datetime import datetime, timedelta, timezone
    _jetzt = datetime.now(timezone.utc)
    if not await db.resale_listings.count_documents(
            {"id": listing_id, "dealer_id": user["dealer_id"]}):
        raise HTTPException(404, "Inserat nicht gefunden")
    _sperre = await db.resale_listings.find_one_and_update(
        {"id": listing_id, "dealer_id": user["dealer_id"],
         "$or": [{"publish_lock_until": {"$exists": False}},
                 {"publish_lock_until": None},
                 {"publish_lock_until": {"$lt": _jetzt}}]},
        {"$set": {"publish_lock_until": _jetzt + timedelta(seconds=30)}})
    if _sperre is None:
        raise HTTPException(409, "Dieses Inserat wird gerade veroeffentlicht — "
                                 "bitte einen Moment warten und neu laden.")
    try:
        l = await db.resale_listings.find_one(
            {"id": listing_id, "dealer_id": user["dealer_id"]}, {"_id": 0})
        if not l:
            raise HTTPException(404, "Inserat nicht gefunden")
        if l.get("status") == "veroeffentlicht":
            # Idempotent (Review 09/2026): Doppelklick oder parallele
            # Anfrage NACH dem erfolgreichen Publish ist kein Fehler.
            return {"ok": True, "status": "veroeffentlicht",
                    "visibility": l.get("visibility") or body.visibility,
                    "bereits_veroeffentlicht": True}
        if l.get("status") not in ("verkaufsbereit", "zurueckgezogen"):
            raise HTTPException(400, "Nur verkaufsbereite (oder zurückgezogene) "
                                     "Inserate können veröffentlicht werden")
        # Nachpruefung Runde 14 (Nr. 107): die Preispflicht greift sonst nur
        # bei entwurf -> verkaufsbereit; ueber zurueckgezogen -> publish kam
        # ein Inserat ohne oeffentlichen Preis live.
        if not (l.get("prices") or {}).get("public"):
            raise HTTPException(400, "Bitte zuerst einen oeffentlichen "
                                     "Verkaufspreis eintragen")
        # Nachpruefung Runde 14 (Nr. 81): Fahrzeugweg VOR Kontingent und
        # Statuswechsel pruefen — bei Desync 409 statt Inserat live und
        # Fahrzeug unveraendert (try_set_lifecycle schluckte den Fehler).
        pfad = await _lifecycle_pfad_oder_409(l.get("vehicle_id"),
                                              user["dealer_id"], "veroeffentlicht")

        from routes.team import get_sale_plan_status
        plan = await get_sale_plan_status(user["dealer_id"])
        if not plan.get("active"):
            raise HTTPException(402, "Kein Verkaufspaket aktiv. Bitte im Bereich "
                                     "'Mitarbeiter / Sucher' ein Paket anfragen.")
        period_key = plan["period_key"]
        already = period_key in (l.get("counted_periods") or [])
        # Nachpruefung Runde 14 (Nr. 81): merken, was DIESER Aufruf am
        # Kontingent beansprucht hat, um es bei einem spaeteren Abbruch
        # (Rennen am Inserat oder Fahrzeug) wieder zurueckzugeben.
        markiert = False
        slot_geholt = False

        async def _kontingent_zurueckgeben() -> None:
            if slot_geholt:
                await db.dealers.update_one(
                    {"id": user["dealer_id"]},
                    {"$inc": {f"quota_usage.{period_key}": -1}})
            if markiert:
                await db.resale_listings.update_one(
                    {"id": listing_id, "dealer_id": user["dealer_id"]},
                    {"$pull": {"counted_periods": period_key}})

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
            markiert = bool(marker.modified_count)
            if not marker.modified_count:
                # Ein GLEICHZEITIGER Publish hat die Markierung gesetzt. Zwei
                # Faelle: (a) er hat den Slot bekommen — dann ist alles gezaehlt
                # und wir duerfen mitveroeffentlichen; (b) er lag UEBER der
                # Quota und hat Markierung + Slot gerade zurueckgegeben — dann
                # duerfen wir NICHT einfach durchrutschen (vorher konnte so ein
                # Inserat ueber der Quota live gehen, ohne je gezaehlt zu
                # werden). Nachlesen entscheidet.
                nachgelesen = await db.resale_listings.find_one(
                    {"id": listing_id, "dealer_id": user["dealer_id"]},
                    {"_id": 0, "counted_periods": 1})
                if period_key not in (nachgelesen or {}).get("counted_periods", []):
                    raise HTTPException(402, "Dein monatliches Kontingent ist "
                                             "erreicht. Upgrade auf ein größeres "
                                             "Paket oder Enterprise anfragen.")
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
                slot_geholt = True
                used_now = (claimed.get("quota_usage") or {}).get(period_key, 1)
                if used_now > quota:
                    # Über der Quota → Slot und Markierung zurückgeben, ablehnen.
                    await _kontingent_zurueckgeben()
                    raise HTTPException(402, f"Dein monatliches Kontingent von "
                                             f"{quota} Fahrzeugen ist erreicht. "
                                             "Upgrade auf ein größeres Paket oder "
                                             "Enterprise anfragen.")

        # Nachpruefung Runde 14 (Nr. 81/89): bedingt auf den gelesenen Status
        # — ein paralleler Statuswechsel (verkauft/geloescht) darf nicht
        # durch "veroeffentlicht" ueberschrieben werden.
        res = await db.resale_listings.update_one(
            {"id": listing_id, "dealer_id": user["dealer_id"],
             "status": l.get("status")},
            {"$set": {"status": "veroeffentlicht",
                      "visibility": body.visibility,
                      "published_at": l.get("published_at") or now_iso(),
                      "updated_at": now_iso()}})
        if res.matched_count == 0:
            await _kontingent_zurueckgeben()
            raise HTTPException(409, "Inserat wurde zwischenzeitlich geaendert "
                                     "— bitte neu laden")
        if pfad:
            try:
                await _lifecycle_anwenden(l["vehicle_id"], user["dealer_id"],
                                          pfad, user)
            except LifecycleError as exc:
                # Rennen am Fahrzeug: Veroeffentlichung zuruecknehmen.
                await db.resale_listings.update_one(
                    {"id": listing_id, "dealer_id": user["dealer_id"],
                     "status": "veroeffentlicht"},
                    {"$set": {"status": l.get("status"),
                              "published_at": l.get("published_at"),
                              "updated_at": now_iso()}})
                await _kontingent_zurueckgeben()
                raise HTTPException(409, "Fahrzeugstatus passt nicht zum "
                                         f"Inserat: {exc}")
        # Runde 17 (Nr. 398): Inserat ist live und gezaehlt — ein Fehler
        # beim Audit darf daraus keinen 500 (und keinen Client-Retry) machen.
        await log_activity_sicher(
            user["dealer_id"], user["id"], "inserat.veroeffentlicht",
            ref=listing_id,
            meta={"sichtbarkeit": body.visibility,
                  "kontingent": f"{plan.get('used', 0) + (0 if already else 1)}/{plan.get('quota')}"})
        return {"ok": True, "status": "veroeffentlicht",
                "visibility": body.visibility}
    finally:
        await db.resale_listings.update_one(
            {"id": listing_id, "dealer_id": user["dealer_id"]},
            {"$unset": {"publish_lock_until": ""}})


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
    if current == new:
        # Runde 17 (Nr. 399): Doppelklick / Client-Retry nach erfolgreichem
        # Wechsel ist kein Fehler (vorher 400 "nicht erlaubt", obwohl der
        # Zustand laengst geschrieben war). Anfragen idempotent nachziehen.
        try:
            await _anfragen_nach_wechsel(listing_id, new=new, war_reserviert=False)
        except Exception:  # noqa: BLE001
            log.exception("Kaufanfragen zu Inserat %s nicht geschlossen", listing_id)
        return {"ok": True, "status": new, "bereits": True}
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
    unset: Dict[str, Any] = {}
    if new == "verkauft":
        # Nachpruefung Runde 14 (Nr. 79): ohne Verkaufspreis blieb sold_price
        # null — Marge/Auswertung leer, keine dokumentierte Entscheidung dazu.
        if body.sold_price is None:
            raise HTTPException(400, "Bitte den tatsaechlichen Verkaufspreis "
                                     "angeben")
        update["sold_at"] = now_iso()
        update["sold_price"] = round(float(body.sold_price), 2)
        # Nachpruefung Runde 14 (Nr. 27): Semantik explizit — der reservierte
        # Kaeufer wird als Kaeufer festgehalten, reserved_for verschwindet.
        if current == "reserviert" and l.get("reserved_for"):
            update["sold_to_user_id"] = l["reserved_for"]
    if current == "reserviert":
        # Nachpruefung Runde 14 (Nr. 26): reserved_for blieb beim Verlassen
        # von "reserviert" stehen und wanderte bis in die Veroeffentlichung.
        unset["reserved_for"] = ""

    # Nachpruefung Runde 14 (Nr. 52/81): Fahrzeugweg VOR dem Schreiben
    # pruefen (409 bei Desync) — vorher schluckte try_set_lifecycle den
    # Fehler und das Fahrzeug blieb z.B. dauerhaft "reserviert".
    vehicle_id = l.get("vehicle_id")
    pfad = await _lifecycle_pfad_oder_409(vehicle_id, user["dealer_id"],
                                          _LISTING_TO_LIFECYCLE[new])

    op: Dict[str, Any] = {"$set": update}
    if unset:
        op["$unset"] = unset
    # Bedingt auf den gelesenen Status (Nr. 89/90): paralleler Wechsel -> 409.
    res = await db.resale_listings.update_one(
        {"id": listing_id, "dealer_id": user["dealer_id"], "status": current}, op)
    if res.matched_count == 0:
        raise HTTPException(409, "Inserat wurde zwischenzeitlich geaendert — "
                                 "bitte neu laden")

    # Fahrzeug-Lebenszyklus synchron halten — Fehler sichtbar (Nr. 81).
    if pfad:
        try:
            await _lifecycle_anwenden(vehicle_id, user["dealer_id"], pfad, user)
        except LifecycleError as exc:
            # Rennen am Fahrzeug: Inserat auf den alten Status zuruecksetzen.
            # Runde 17 (Nr. 290): auch reserved_for aus dem gelesenen Stand
            # wiederherstellen — vorher verlor der Kaeufer seine Reservierung.
            zurueck: Dict[str, Any] = {"status": current, "updated_at": now_iso()}
            if current == "reserviert" and l.get("reserved_for"):
                zurueck["reserved_for"] = l["reserved_for"]
            await db.resale_listings.update_one(
                {"id": listing_id, "dealer_id": user["dealer_id"], "status": new},
                {"$set": zurueck,
                 "$unset": {"sold_at": "", "sold_price": "",
                            "sold_to_user_id": ""}})
            raise HTTPException(409, "Fahrzeugstatus passt nicht zum Inserat: "
                                     f"{exc}")

    # Nachpruefung Runde 14 (Nr. 54/26): Kaufanfragen mit dem Inserat
    # abschliessen. Runde 17 (Nr. 399): Inserat und Fahrzeug sind bereits
    # geschrieben — Folgeschritte (Anfragen, Audit) duerfen den Vorgang nicht
    # mehr mit 500 abbrechen lassen; Fehler landen im Log.
    try:
        await _anfragen_nach_wechsel(listing_id, new=new,
                                     war_reserviert=(current == "reserviert"))
    except Exception:  # noqa: BLE001
        log.exception("Kaufanfragen zu Inserat %s nach '%s' nicht geschlossen",
                      listing_id, new)
    try:
        await log_activity(user["dealer_id"], user["id"], f"inserat.{new}",
                           ref=listing_id)
    except Exception:  # noqa: BLE001
        log.exception("Audit-Eintrag inserat.%s (%s) nicht gespeichert", new, listing_id)
    return {"ok": True, "status": new}


async def _anfragen_nach_wechsel(listing_id: str, *, new: str,
                                 war_reserviert: bool) -> None:
    """Beim Verkauf bleibt eine akzeptierte Anfrage (der Kaeufer) stehen,
    alle offenen Verhandlungen enden; beim Aufheben einer Reservierung endet
    auch die akzeptierte. Idempotent (update_many auf offene Stati)."""
    if new == "verkauft":
        await _anfragen_schliessen(listing_id, "inserat_verkauft")
    elif war_reserviert:
        await _anfragen_schliessen(listing_id, "reservierung_aufgehoben",
                                   auch_akzeptierte=True)
    elif new in ("zurueckgezogen", "entwurf"):
        await _anfragen_schliessen(listing_id, f"inserat_{new}")
