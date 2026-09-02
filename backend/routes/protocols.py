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
# current_firma = Chef UND seine Sucher; Admin/Kaeufer bleiben aussen vor.
from routes.bestand import current_firma as _dealer_dep

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

# Abschnitt 1 im PDF: 12 Zeilen mit "stimmt / weicht ab" (bzw. Ja/Nein/
# unbekannt). Der Fahrer kreuzt jede Zeile am Handy an und kann bei
# Abweichung den korrekten Wert eintippen.
VEHICLE_CHECK_FIELDS = [
    ("make", "Marke", ["stimmt", "weicht ab"]),
    ("model", "Modell", ["stimmt", "weicht ab"]),
    ("first_registration", "Erstzulassung", ["stimmt", "weicht ab"]),
    ("vin", "FIN (Fahrgestell-Nr.)", ["stimmt", "weicht ab"]),
    ("power", "Leistung", ["stimmt", "weicht ab"]),
    ("previous_owners", "Halter laut Schein", ["stimmt", "weicht ab"]),
    ("color", "Farbe", ["stimmt", "weicht ab"]),
    ("fuel", "Kraftstoff", ["stimmt", "weicht ab"]),
    ("hu", "HU", ["stimmt", "weicht ab"]),
    ("mileage_contract", "KM-Stand laut Vertrag", ["stimmt", "weicht ab"]),
    ("commercial", "Gewerbliche Nutzung", ["Ja", "Nein", "unbekannt"]),
    ("accident_free", "Unfallfrei laut Angabe", ["Ja", "Nein", "unbekannt"]),
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


def _vehicle_check_values(vehicle: dict, contract: dict) -> Dict[str, str]:
    """IST-Werte fuer Abschnitt 1 — identisch zur PDF-Darstellung, damit der
    Fahrer am Handy genau das sieht, was auf dem Papier steht."""
    v, c = vehicle or {}, contract or {}
    power_kw = v.get("power_kw") or v.get("kw")
    power_ps = v.get("power_ps") or v.get("ps")
    if power_ps in (None, "", 0) and power_kw not in (None, "", 0):
        try:
            power_ps = round(float(power_kw) * 1.35962)
        except (TypeError, ValueError):
            power_ps = None
    hu_val = c.get("hu_valid") or ""
    hu_until = c.get("hu_until") or v.get("hu") or ""
    hu_disp = f"{hu_val} · gültig bis {hu_until}".strip(" ·") if (hu_val or hu_until) else ""
    km = v.get("km") or v.get("mileage")
    return {
        "make": v.get("make_label") or v.get("make") or v.get("brand") or "",
        "model": v.get("model_label") or v.get("model") or "",
        "first_registration": v.get("first_registration") or v.get("ezl") or v.get("ez") or "",
        "vin": v.get("vin") or v.get("fin") or "",
        "power": (f"{power_kw or '—'} kW / {power_ps or '—'} PS"
                  if (power_kw or power_ps) else ""),
        "previous_owners": str(c.get("previous_owners")
                               or v.get("previous_owners") or ""),
        "color": v.get("color") or v.get("exterior_color") or "",
        "fuel": v.get("fuel_label") or v.get("fuel") or v.get("fuel_type") or "",
        "hu": hu_disp,
        "mileage_contract": (f"{int(float(km)):,}".replace(",", ".") + " km") if km else "",
        "commercial": c.get("commercial_since_ez") or "",
        "accident_free": c.get("accident_free") or "",
    }


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
        # WICHTIG: zusaetzlich ueber dealer_id. Fahrzeug-IDs sind
        # vorhersehbar (v_<Inserats-ID>) und nur MIT dealer_id eindeutig —
        # sonst koennte das Fahrzeug eines fremden Haendlers geladen werden.
        v = await db.vehicles.find_one(
            {"id": appt["vehicle_id"], "dealer_id": appt.get("dealer_id")},
            {"_id": 0}) or {}
        vehicle = dict(v.get("data") or {})
    contract: Dict[str, Any] = {}
    if appt.get("contract_id"):
        c = await db.generated_pdfs.find_one(
            {"id": appt["contract_id"], "dealer_id": appt.get("dealer_id")},
            {"_id": 0, "contract_data": 1}) or {}
        contract = dict(c.get("contract_data") or {})
    return {
        "protocol": doc,
        "template": {
            "vehicle_check_fields": [
                {"key": k, "label": lb, "options": opts}
                for k, lb, opts in VEHICLE_CHECK_FIELDS
            ],
            "vehicle_check_values": _vehicle_check_values(vehicle, contract),
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
    try:
        await db.pickup_protocols.insert_one(new_doc)
    except Exception:
        # Unique-Index (genau EIN aktuelles Protokoll je Termin): ein
        # GLEICHZEITIGER Request hat den Entwurf gerade angelegt — dann
        # dessen Dokument aktualisieren statt ein zweites zu erzeugen.
        vorhandenes = await _current(appt_id)
        if not vorhandenes:
            raise
        await db.pickup_protocols.update_one(
            {"id": vorhandenes["id"]},
            {"$set": {**payload, "updated_at": now_iso()}})
        return await db.pickup_protocols.find_one(
            {"id": vorhandenes["id"]}, {"_id": 0})
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
    try:
        await db.pickup_protocols.insert_one(new_doc)
    except Exception:
        # Schlaegt der Insert fehl, darf der Termin nicht OHNE aktuelle
        # Version zurueckbleiben: alte Version wieder aktiv schalten.
        await db.pickup_protocols.update_one(
            {"id": doc["id"]}, {"$set": {"superseded": False}})
        raise HTTPException(500, "Korrektur konnte nicht angelegt werden — "
                                 "bitte erneut versuchen (alte Version ist "
                                 "weiterhin gültig).")
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
        # SELBSTHEILUNG (PR-Review 09/2026): Der Abschluss besteht aus
        # mehreren Schritten (Protokoll final -> Termin abgeholt ->
        # Fahrzeug-Lebenszyklus). Brach er dazwischen ab, war das
        # Protokoll final, der Termin aber offen — und jeder neue Versuch
        # scheiterte hier mit 409. Jetzt zieht ein Wiederholungsaufruf
        # die fehlenden Status nach, statt dauerhaft zu blockieren.
        if (appt.get("status") or "") != "abgeholt":
            await db.appointments.update_one(
                {"id": appt_id},
                {"$set": {"status": "abgeholt",
                          "status_changed_at": now_iso(),
                          "protocol_id": doc["id"]}})
            if appt.get("vehicle_id"):
                await try_set_lifecycle(appt["vehicle_id"],
                                        appt.get("dealer_id", ""), "abgeholt")
            return {"ok": True, "protocol_id": doc["id"],
                    "version": doc.get("version", 1),
                    "nachgezogen": True,
                    "pdf_url": f"/api/driver/appointments/{appt_id}/protocol.pdf"}
        raise HTTPException(409, "Protokoll ist bereits abgeschlossen")

    # ---- Pflichtfelder: das Backend verlaesst sich NICHT auf die App ----
    # Abschnitt 1: alle 12 Fahrzeugdaten-Zeilen muessen beantwortet sein.
    vc = doc.get("vehicle_check") or {}
    fehlend = [label for key, label, _opts in VEHICLE_CHECK_FIELDS
               if not str((vc.get(key) or {}).get("status")
                          if isinstance(vc.get(key), dict)
                          else vc.get(key) or "").strip()]
    if fehlend:
        raise HTTPException(422, "Abschnitt 1 unvollständig — bitte noch "
                                 "ankreuzen: " + ", ".join(fehlend))
    cond = doc.get("condition") or {}
    if not str(cond.get("mileage") or "").strip():
        raise HTTPException(422, "Bitte den Kilometerstand bei Abholung "
                                 "eintragen (Abschnitt 4).")
    if not str(doc.get("keys_count") or "").strip():
        raise HTTPException(422, "Bitte die Anzahl der übergebenen "
                                 "Schlüssel eintragen (Abschnitt 2).")
    if doc.get("damages_confirmed") is None:
        raise HTTPException(422, "Bitte Abschnitt 5 (bekannte Schäden "
                                 "bestätigt) beantworten.")
    if not (body.seller_name or appt.get("seller_name") or "").strip():
        raise HTTPException(422, "Bitte den Namen des Verkäufers angeben.")
    if not (body.place or doc.get("place") or "").strip():
        raise HTTPException(422, "Bitte den Ort der Übergabe angeben.")

    # ---- Abschluss ATOMAR beanspruchen: von beliebig vielen gleichzeitigen
    # Aufrufen gewinnt genau EINER (kein doppeltes PDF, keine doppelten
    # Unterschrift-Dateien). Der Claim traegt ein ABLAUFDATUM: stirbt der
    # Prozess mittendrin (Deploy, Absturz), kann der Fahrer nach 3 Minuten
    # einfach erneut abschliessen — ohne Ablauf waere das Protokoll fuer
    # immer in 'wird_abgeschlossen' gefangen (nur per Datenbank loesbar).
    from datetime import datetime as _dt, timedelta as _td, timezone as _tz
    _jetzt = _dt.now(_tz.utc)
    claim = await db.pickup_protocols.find_one_and_update(
        {"id": doc["id"],
         "$or": [{"status": {"$nin": ["final", "wird_abgeschlossen"]}},
                 {"status": "wird_abgeschlossen",
                  "claim_bis": {"$lt": _jetzt.isoformat()}}]},
        {"$set": {"status": "wird_abgeschlossen",
                  "claim_bis": (_jetzt + _td(minutes=3)).isoformat(),
                  "updated_at": now_iso()}})
    if not claim:
        raise HTTPException(409, "Das Protokoll wird gerade abgeschlossen "
                                 "— bitte einen Moment warten.")

    async def _rollback():
        """Claim freigeben — zurueck auf 'entwurf' (den einzigen Status,
        aus dem ein Claim moeglich ist), damit der Fahrer neu abschliessen
        kann."""
        await db.pickup_protocols.update_one(
            {"id": doc["id"], "status": "wird_abgeschlossen"},
            {"$set": {"status": "entwurf"},
             "$unset": {"claim_bis": ""}})

    dealer_id = appt.get("dealer_id", "x")

    def _save_sig(b64: str, who: str) -> str:
        try:
            raw = base64.b64decode(b64.split(",")[-1], validate=False)
            from storage_service import validate_image_bytes
            validate_image_bytes(raw, wo=f"Unterschrift ({who})")
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

    try:
        sig_driver = _save_sig(body.signature_driver_b64, "fahrer")
        sig_seller = _save_sig(body.signature_seller_b64, "verkaeufer")
    except HTTPException:
        await _rollback()
        raise

    # ---- Daten für das ausgefüllte PDF zusammenstellen ----
    vehicle: Dict[str, Any] = {}
    if appt.get("vehicle_id"):
        # WICHTIG: zusaetzlich ueber dealer_id. Fahrzeug-IDs sind
        # vorhersehbar (v_<Inserats-ID>) und nur MIT dealer_id eindeutig —
        # sonst koennte das Fahrzeug eines fremden Haendlers geladen werden.
        v = await db.vehicles.find_one(
            {"id": appt["vehicle_id"], "dealer_id": appt.get("dealer_id")},
            {"_id": 0}) or {}
        vehicle = dict(v.get("data") or {})
    contract: Dict[str, Any] = {}
    if appt.get("contract_id"):
        c = await db.generated_pdfs.find_one(
            {"id": appt["contract_id"], "dealer_id": appt.get("dealer_id")},
            {"_id": 0, "contract_data": 1}) or {}
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
    import asyncio as _aio
    try:
        # Im Thread: die PDF-Erzeugung ist CPU-Arbeit und wuerde sonst den
        # ganzen Worker-Prozess blockieren.
        pdf_bytes = await _aio.to_thread(
            build_pickup_pdf,
            appointment=appt, vehicle=vehicle, contract=contract,
            dealer=dealer,
            driver={"id": driver["id"], "name": driver.get("display_name"),
                    "code": driver.get("driver_code")},
            filled=filled,
        )
        pdf_key = make_key("protocol", dealer_id, "abholprotokoll.pdf")
        storage.save(pdf_key, pdf_bytes)
    except StorageError as exc:
        await _rollback()
        raise HTTPException(400, f"PDF konnte nicht gespeichert werden: {exc}")
    except Exception:
        await _rollback()
        raise

    await db.pickup_protocols.update_one(
        {"id": doc["id"]},
        {"$unset": {"claim_bis": ""},
         "$set": {"status": "final", "pdf_path": pdf_key,
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
