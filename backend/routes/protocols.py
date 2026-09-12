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
import logging
import math
import uuid
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field, field_validator
from pymongo.errors import DuplicateKeyError

import betrieb
import protokoll_vergleich as PV
from deps import (besitzer_namen, db, fahrzeug_im_bereich, ist_sucher, log_activity,
                  now_iso, termin_im_bereich)
from lifecycle import try_set_lifecycle
# Runde 17 (Nr. 10): dieselbe Schadensform wie im Kaufvertrag (contracts.py
# importiert appointments/protocols nur lazy — kein Zyklus).
from routes.contracts import DamageIn
from routes.drivers import current_driver, _zugriff_pruefen

# Haendler-/Sucher-Zugriff auf die fertigen Protokolle (Fahrzeugakte).
# current_firma = Chef UND seine Sucher; Admin/Kaeufer bleiben aussen vor.
from routes.bestand import current_firma as _dealer_dep

log = logging.getLogger("autohandel")

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
VEHICLE_CHECK_FIELDS = PV.FELDER   # Gegenpruefung 12.09.2026: protokoll_vergleich.FELDER

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

    # Runde 17 (Nr. 9): auch documents/features deckeln (vorher unbegrenzt).
    @field_validator("vehicle_check", "condition", "documents", "features", mode="before")
    @classmethod
    def _dict_deckeln(cls, v):
        """Review 09/2026: freie Dicts hatten keine Groessengrenze — max. 60
        Schluessel, Werte als Text bis 500 Zeichen (Zahlen/Bool/None ok)."""
        if v is None:
            return v
        if not isinstance(v, dict) or len(v) > 60:
            raise ValueError("zu viele oder ungueltige Felder (max. 60)")
        def _wert(k, w, tiefe=0):
            if isinstance(w, str):
                return w[:500]
            # Gegenpruefung 12.09.2026: Infinity/NaN (json.loads nimmt sie an)
            # liessen spaeter die ganze Freigaben-Liste scheitern.
            if isinstance(w, float) and not math.isfinite(w):
                raise ValueError(f"Feld {k}: ungültige Zahl")
            if isinstance(w, (int, float, bool)) or w is None:
                return w
            # Verschachtelte Eintraege wie {"make": {"status": "stimmt", "value": ...}}
            if isinstance(w, dict) and tiefe == 0 and len(w) <= 20:
                return {str(kk)[:80]: _wert(kk, ww, 1) for kk, ww in w.items()}
            if isinstance(w, list) and tiefe == 0 and len(w) <= 50:
                return [_wert(k, x, 1) for x in w]
            raise ValueError(f"Feld {k}: nur Text, Zahl oder Ja/Nein")
        out = {}
        for k, w in v.items():
            if not isinstance(k, str) or len(k) > 80:
                raise ValueError("ungueltiger Feldname")
            out[k] = _wert(k, w)
        return out
    documents: Optional[Dict[str, bool]] = None         # Abschnitt 2
    keys_count: Optional[str] = Field(default=None, max_length=20)
    keys_expected: Optional[str] = Field(default=None, max_length=20)
    features: Optional[Dict[str, bool]] = None          # Abschnitt 3
    condition: Optional[Dict[str, Any]] = None          # Abschnitt 4
    damages_confirmed: Optional[bool] = None            # Abschnitt 5
    # Abschnitt 6: neu entdeckte Schaeden, per Tipp auf die Fahrzeug-Skizze
    # markiert (gleiches Format wie die Kaufvertrag-Schaeden: view/zone/x/y/...)
    # Runde 17 (Nr. 10): typisiert wie die Vertrags-Schaeden (vorher freie
    # Dicts — ein String-Element liess pickup_pdf_service abstuerzen).
    new_damages: Optional[List[DamageIn]] = Field(default=None, max_length=40)
    notes: Optional[str] = Field(default=None, max_length=5000)   # Abschnitt 7
    place: Optional[str] = Field(default=None, max_length=200)    # Ort (Abschnitt 8)


class FinalizeIn(BaseModel):
    # Runde 17 (Nr. 8): Obergrenze fuer die Base64-Unterschriften (die
    # dekodierte PNG ist auf 2 MB begrenzt; 3 Mio. Zeichen decken das ab).
    signature_driver_b64: str = Field(min_length=20, max_length=3_000_000)
    signature_seller_b64: str = Field(min_length=20, max_length=3_000_000)
    seller_name: Optional[str] = Field(default=None, max_length=200)
    place: Optional[str] = Field(default=None, max_length=200)
    # Gegenpruefung 12.09.2026 (schwerer Befund): Der Chef kann den Preis
    # aendern, waehrend vor Ort unterschrieben wird. Die App schickt
    # deshalb den Preis mit, den sie ANGEZEIGT hat — weicht er ab, wird
    # nicht abgeschlossen. Sonst haette der Verkaeufer einen anderen
    # Betrag gesehen als den, der im PDF ueber seiner Unterschrift steht.
    # None = die App kannte noch keinen Preis (Altfassung / keine Freigabe
    # mit Preis).
    neuer_preis_gesehen: Optional[float] = Field(default=None, ge=0,
                                                 le=10_000_000)
    # Gegenpruefung 12.09.2026: Nicht nur der Preis — auch der Vermerk steht
    # ueber den Unterschriften. Die App schickt den Freigabe-Stand, den sie
    # angezeigt hat; jede Aenderung des Chefs setzt einen neuen.
    # None = aeltere App (dann nur die Preispruefung).
    freigabe_stand_gesehen: Optional[str] = Field(default=None, max_length=64)


# --------------------------------------------------------------------------
# Runde 30 (12.09.2026, Wunsch Ahmad): Freigabe durch den Chef VOR den
# Unterschriften.
#
# Ablauf vor Ort:
#   1. Der Fahrer fuellt das Protokoll vollstaendig aus und schickt es ab
#      (Status "zur_freigabe") — noch OHNE Unterschriften.
#   2. Der Chef sieht das ausgefuellte Protokoll: was weicht ab, welche
#      neuen Schaeden hat der Fahrer gefunden. Er kann den Verkaeufer
#      anrufen und nachverhandeln.
#   3. Der Chef gibt frei — mit dem NEUEN Preis, falls verhandelt wurde
#      (Status "freigegeben"). Oder er schickt das Protokoll zurueck, wenn
#      der Fahrer etwas nachtragen soll (zurueck auf "entwurf").
#   4. Erst dann unterschreiben Verkaeufer und Fahrer vor Ort, und das
#      Protokoll wird endgueltig (Status "final") — mit dem neuen Preis.
ZUR_FREIGABE = "zur_freigabe"
FREIGEGEBEN = "freigegeben"
# In diesen Staenden darf der Fahrer nichts mehr aendern (sonst saehe der
# Chef etwas anderes, als am Ende unterschrieben wird).
GESPERRT_FUER_FAHRER = (ZUR_FREIGABE, FREIGEGEBEN)


class FreigabeIn(BaseModel):
    """Freigabe des Chefs. `neuer_preis` nur, wenn nachverhandelt wurde."""
    neuer_preis: Optional[float] = Field(default=None, ge=0, le=10_000_000)
    notiz: Optional[str] = Field(default=None, max_length=2000)
    # True = zurueck an den Fahrer (er soll etwas nachtragen/korrigieren).
    zurueck: bool = False
    # Runde 33 (Gegenpruefung 12.09.2026): Den Stand mitschicken, den der
    # Bearbeiter gesehen hat (updated_at aus der Liste). Geben Chef und Sucher
    # gleichzeitig mit verschiedenen Preisen frei, gewann vorher stillschweigend
    # der Letzte. Jetzt lehnt der Server ab und sagt, wer schon freigegeben hat.
    # Ohne Stand (aeltere Oberflaeche im Rollout) bleibt es beim bisherigen Weg.
    stand: Optional[str] = Field(default=None, max_length=64)
    # Verhandelten Preis entfernen — es gilt wieder der Vertragspreis. Vorher
    # liess sich ein einmal eingetragener Preis nicht mehr loeschen.
    preis_zuruecksetzen: bool = False


def _vehicle_check_values(vehicle: dict, contract: dict) -> Dict[str, str]:
    """Soll-Werte fuer Abschnitt 1 — dieselben wie im Protokoll-PDF und im
    Freigabe-Kasten.

    Runde 33 (Analyse 12.09.2026): Das waren bisher die INSERATSWERTE.
    Korrigierte der Haendler im Vertrags-Dialog z.B. die Erstzulassung, sah
    der Fahrer vor Ort weiter den alten Wert. Jetzt gelten die Angaben aus
    dem Vertrag (protokoll_vergleich.vertragswerte)."""
    return PV.werte_als_text(PV.vertragswerte(vehicle, contract))


async def _appt_or_404(appt_id: str, driver: dict) -> dict:
    appt = await db.appointments.find_one(
        {"id": appt_id, "driver_id": driver["id"]}, {"_id": 0})
    if not appt:
        raise HTTPException(404, "Termin nicht gefunden")
    # Zuweisung allein reicht nicht: der Fahrer muss bei dieser Firma noch
    # in der Fahrerliste stehen (Pruefbericht 09/2026, routes.drivers).
    await _zugriff_pruefen(appt, driver)
    return appt


async def _dateien_verwerfen(keys: List[str], dealer_id: str) -> None:
    """Beim Abbruch eines Abschlusses bereits geschriebene Dateien
    (Unterschrift-PNGs, PDF) wieder entfernen — best effort. Schlaegt das
    Loeschen fehl, landet der Key in storage_delete_retry, damit der
    Aufraeum-Job die Loeschung nachholt statt die Datei verwaist liegen
    zu lassen (Pruefbericht 09/2026)."""
    from storage_service import delete_async
    for key in list(keys):
        try:
            await delete_async(key)
        except Exception as exc:  # noqa: BLE001
            log.warning("Protokoll-Rollback: %s konnte nicht geloescht werden (%s)",
                        key, exc)
            try:
                jetzt = now_iso()
                await db.storage_delete_retry.insert_one({
                    "id": str(uuid.uuid4()), "art": "key", "key": key,
                    "grund": "protokoll-rollback", "dealer_id": dealer_id,
                    "versuche": 0, "letzter_fehler": str(exc)[:300],
                    "created_at": jetzt, "updated_at": jetzt,
                })
            except Exception:  # noqa: BLE001
                log.exception("storage_delete_retry: Eintrag fuer %s "
                              "fehlgeschlagen", key)


async def _current(appt_id: str) -> Optional[dict]:
    return await db.pickup_protocols.find_one(
        {"appointment_id": appt_id, "superseded": {"$ne": True}}, {"_id": 0},
        sort=[("version", -1)])


# Runde 17 (Nr. 7): Termin-Zustaende, in denen der Protokoll-Abschluss den
# Termin NICHT mehr auf "abgeholt" setzen darf (Haendler hat ihn inzwischen
# geschlossen). Dieselbe Menge wie die Vorabpruefung im Finalize.
_TERMIN_GESCHLOSSEN = ["storniert", "nicht abgeholt", "erledigt"]
TERMIN_GESCHLOSSEN_HINWEIS = ("Protokoll gespeichert — der Termin wurde inzwischen "
                              "vom Händler geschlossen oder der Fahrer entfernt.")


async def _termin_abgeholt_setzen(appt_id: str, driver_id: str,
                                  setzen: Dict[str, Any]) -> bool:
    """Runde 17 (Nr. 7): Termin-Write als Compare-and-Set — nur, wenn der
    Termin noch diesem Fahrer gehoert und nicht inzwischen geschlossen
    wurde. Vorher machte der Abschluss auch einen zwischenzeitlich
    stornierten Termin (oder den eines entfernten Fahrers) zu "abgeholt".
    Liefert False (mit Betriebsalarm), wenn der Termin nicht mehr passt."""
    res = await db.appointments.update_one(
        {"id": appt_id, "driver_id": driver_id,
         "status": {"$nin": _TERMIN_GESCHLOSSEN}},
        {"$set": setzen})
    if res.matched_count == 0:
        await betrieb.alarm(db, "protokoll_final_termin_geschlossen", ref=appt_id,
                            driver_id=driver_id,
                            protocol_id=setzen.get("protocol_id"))
        return False
    # Nachpruefung Runde 14 (Befund 114): Aufraeum-Frist ab dem ERSTEN
    # Abschluss — nur setzen, wenn noch leer.
    await db.appointments.update_one(
        {"id": appt_id, "abgeschlossen_seit": {"$in": [None, ""]}},
        {"$set": {"abgeschlossen_seit": now_iso()}})
    return True


async def korrektur_verwerfen(appt_id: str) -> bool:
    """Runde 17 (Nr. 11): Schliesst der Haendler den Termin (storniert /
    nicht abgeholt / erledigt), waehrend eine Korrektur-Version des
    Protokolls als Entwurf offen ist, bleibt der Termin sonst OHNE
    massgebliches Protokoll: die korrigierte Version ist bereits abgeloest
    (superseded), der Entwurf wird nie fertig. Deshalb: Entwurf verwerfen,
    DANACH die korrigierte Version wieder als aktuell schalten (Reihenfolge
    wegen des Unique-Index 'ein aktuelles Protokoll je Termin').
    Best effort — wirft nie; True, wenn ein Entwurf verworfen wurde.

    Gegenpruefung 12.09.2026 (schwerer Befund): Seit Runde 30 kann eine
    Korrektur auch beim Chef liegen (zur_freigabe) oder freigegeben sein.
    Wurde nur nach 'entwurf' gesucht, blieb der Termin nach dem Schliessen
    OHNE massgebliches Protokoll zurueck — das unterschriebene PDF der
    Vorversion war nicht mehr erreichbar."""
    OFFENE_KORREKTUR = ["entwurf", ZUR_FREIGABE, FREIGEGEBEN]
    try:
        entwurf = await db.pickup_protocols.find_one(
            {"appointment_id": appt_id, "status": {"$in": OFFENE_KORREKTUR},
             "corrects_version": {"$exists": True}, "superseded": {"$ne": True}},
            {"_id": 0, "id": 1, "corrects_version": 1})
        if not entwurf:
            return False
        jetzt = now_iso()
        res = await db.pickup_protocols.update_one(
            {"id": entwurf["id"], "status": {"$in": OFFENE_KORREKTUR}},
            {"$set": {"superseded": True, "verworfen_am": jetzt, "updated_at": jetzt}})
        if res.matched_count == 0:
            return False
        await db.pickup_protocols.update_one(
            {"appointment_id": appt_id, "version": entwurf["corrects_version"]},
            {"$set": {"superseded": False, "updated_at": jetzt},
             "$unset": {"superseded_at": ""}})
        return True
    except Exception:  # noqa: BLE001
        log.exception("Protokoll-Korrektur zu Termin %s konnte nicht verworfen "
                      "werden", appt_id)
        return False


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
        # Gegenpruefung 12.09.2026: Infinity/NaN aus Altdaten nicht ans JSON geben.
        "protocol": PV.json_sicher(doc),
        "template": {
            "vehicle_check_fields": [
                {"key": k, "label": lb, "options": opts}
                for k, lb, opts in VEHICLE_CHECK_FIELDS
            ],
            "vehicle_check_values": _vehicle_check_values(vehicle, contract),
            # Runde 33: Eingabeart je Zeile — Erstzulassung und HU als MM/JJJJ
            # mit Zifferntastatur, Kilometer und Halter nur Ziffern.
            "vehicle_check_art": dict(PV.ARTEN),
            "documents": DOCUMENT_ITEMS,
            "features": (vehicle.get("features") or [])[:20],
            "condition_fields": [
                {"key": k, "label": lb, "options": opts if isinstance(opts, list) else None}
                for k, lb, opts in CONDITION_FIELDS
            ],
        },
        "vehicle": vehicle,
        "damages": contract.get("damages") or vehicle.get("damages") or [],
        # Runde 30: Der Fahrer sieht den Vertragspreis — und nach der
        # Freigabe den nachverhandelten Preis, den er unterschreibt.
        "preis_vertrag": contract.get("purchase_price"),
        "appointment": {k: appt.get(k) for k in
                        ("id", "title", "pickup_date", "pickup_time",
                         "pickup_address", "seller_name", "status")},
    }


# Termine, die abgeschlossen oder storniert sind, nehmen KEINE neuen
# Protokoll-Versionen mehr an (PR-Review 09/2026): sonst konnte ein Fahrer
# nach Abholung/Nichtabholung/Storno die aktuelle Beweisversion ersetzen.
# Will der Haendler eine Korrektur, setzt er den Termin im Buero wieder auf
# "offen" — erst dann darf der Fahrer erneut ans Protokoll.
_ABGESCHLOSSEN = {"abgeholt", "nicht abgeholt", "storniert", "erledigt"}


def _entwurf_filter(proto_id: str) -> dict:
    """Nachpruefung Runde 10: Schreiben darf, wer den ENTWURF trifft — oder
    ein 'wird_abgeschlossen', dessen Claim abgelaufen ist. Stirbt der
    Prozess mitten im Abschluss, blieb der Status sonst fuer immer stehen:
    jedes Zwischenspeichern der Fahrer-App bekam 409 "bereits
    abgeschlossen", und das Finalize (das den Ablauf kennt) wurde nie
    erreicht, weil die App vor dem Abschluss immer erst speichert."""
    from datetime import datetime as _dt, timezone as _tz
    jetzt = _dt.now(_tz.utc).isoformat()
    return {"id": proto_id,
            "$or": [{"status": "entwurf"},
                    {"status": "wird_abgeschlossen", "claim_bis": {"$lt": jetzt}}]}


def _entwurf_update(payload: dict) -> dict:
    return {"$set": {**payload, "status": "entwurf", "updated_at": now_iso()},
            "$unset": {"claim_bis": ""}}


async def _speichern_abgelehnt(proto_id: str):
    """Warum ging das Schreiben nicht durch? Laufender Abschluss -> warten,
    sonst ist das Protokoll fertig -> Korrektur-Version."""
    akt = await db.pickup_protocols.find_one({"id": proto_id}, {"_id": 0, "status": 1})
    if akt and akt.get("status") == "wird_abgeschlossen":
        raise HTTPException(409, "Das Protokoll wird gerade abgeschlossen "
                                 "— bitte einen Moment warten.")
    # Runde 30: abgeschickt oder freigegeben -> nicht mehr aenderbar, damit
    # der Chef genau das sieht, was am Ende unterschrieben wird.
    if akt and akt.get("status") == ZUR_FREIGABE:
        raise HTTPException(409, "Das Protokoll liegt beim Händler zur "
                                 "Freigabe und kann gerade nicht geändert "
                                 "werden.")
    if akt and akt.get("status") == FREIGEGEBEN:
        raise HTTPException(409, "Der Händler hat das Protokoll freigegeben — "
                                 "bitte jetzt unterschreiben. Für Änderungen "
                                 "muss er es zurückschicken.")
    raise HTTPException(409, "Protokoll ist bereits abgeschlossen. Bitte eine "
                             "Korrektur-Version starten.")


def _termin_offen_oder_409(appt: dict) -> None:
    if (appt.get("status") or "offen") in _ABGESCHLOSSEN:
        raise HTTPException(409, f"Termin ist '{appt.get('status')}' — das "
                                 "Protokoll ist gesperrt. Für eine Korrektur "
                                 "muss der Händler den Termin wieder öffnen.")


@router.put("/driver/appointments/{appt_id}/protocol")
async def save_protocol(appt_id: str, body: ProtocolIn,
                        driver=Depends(current_driver)):
    """Zwischenstand speichern — beliebig oft, solange nicht abgeschlossen."""
    appt = await _appt_or_404(appt_id, driver)
    _termin_offen_oder_409(appt)
    doc = await _current(appt_id)
    if doc and doc.get("status") == "final":
        raise HTTPException(409, "Protokoll ist bereits abgeschlossen. Bitte eine "
                                 "Korrektur-Version starten.")
    payload = {k: v for k, v in body.model_dump(exclude_none=True).items()}
    if doc:
        # Runde 10: Bedingt auf den Entwurf-Status schreiben. Zwischen der
        # Pruefung oben und dem Schreiben kann das Protokoll unterschrieben
        # und abgeschlossen worden sein — ein verspaetetes Autospeichern
        # haette dann Felder des FERTIGEN Protokolls ueberschrieben.
        res = await db.pickup_protocols.update_one(
            _entwurf_filter(doc["id"]), _entwurf_update(payload))
        if res.matched_count == 0:
            await _speichern_abgelehnt(doc["id"])
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
        res = await db.pickup_protocols.update_one(
            _entwurf_filter(vorhandenes["id"]), _entwurf_update(payload))
        if res.matched_count == 0:
            await _speichern_abgelehnt(vorhandenes["id"])
        return await db.pickup_protocols.find_one(
            {"id": vorhandenes["id"]}, {"_id": 0})
    return {k: v for k, v in new_doc.items() if k != "_id"}


def _pflichtfelder_pruefen(doc: dict, appt: dict, *,
                           seller_name: Optional[str] = None,
                           place: Optional[str] = None,
                           mit_uebergabe: bool = True) -> None:
    """Alles beisammen? Das Backend verlaesst sich NICHT auf die App.

    Runde 30: dieselbe Pruefung fuer das Abschicken zur Freigabe UND fuer
    den endgueltigen Abschluss — sonst koennte ein halb ausgefuelltes
    Protokoll beim Chef landen. `mit_uebergabe=False` laesst Ort und
    Verkaeufername aus (die traegt der Fahrer erst beim Unterschreiben ein).
    """
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
    if not mit_uebergabe:
        return
    if not (seller_name or doc.get("seller_name")
            or appt.get("seller_name") or "").strip():
        raise HTTPException(422, "Bitte den Namen des Verkäufers angeben.")
    if not (place or doc.get("place") or "").strip():
        raise HTTPException(422, "Bitte den Ort der Übergabe angeben.")


@router.post("/driver/appointments/{appt_id}/protocol/submit")
async def submit_protocol(appt_id: str, driver=Depends(current_driver)):
    """Runde 30 (Wunsch Ahmad): Der Fahrer schickt das ausgefuellte
    Protokoll zur FREIGABE an den Chef — noch ohne Unterschriften.

    Danach sieht der Chef, was vor Ort abweicht und welche Schaeden neu
    sind, und kann mit dem Verkaeufer nachverhandeln. Erst nach seiner
    Freigabe wird unterschrieben."""
    appt = await _appt_or_404(appt_id, driver)
    _termin_offen_oder_409(appt)
    doc = await _current(appt_id)
    if not doc:
        raise HTTPException(400, "Bitte zuerst das Protokoll ausfüllen")
    if doc.get("status") == "final":
        raise HTTPException(409, "Protokoll ist bereits abgeschlossen. Bitte "
                                 "eine Korrektur-Version starten.")
    if doc.get("status") in GESPERRT_FUER_FAHRER:
        # Schon abgeschickt — idempotent, damit ein Doppeltipp oder ein
        # Netzabbruch keine Fehlermeldung erzeugt.
        return {"ok": True, "status": doc["status"],
                "protocol_id": doc["id"], "bereits": True}
    # Vollstaendig ausgefuellt? Ort und Verkaeufername kommen erst beim
    # Unterschreiben dazu.
    _pflichtfelder_pruefen(doc, appt, mit_uebergabe=False)
    jetzt = now_iso()
    setzen: Dict[str, Any] = {"status": ZUR_FREIGABE, "abgeschickt_am": jetzt,
                              "abgeschickt_von": driver["id"], "updated_at": jetzt,
                              # Stand fuer die Konfliktpruefung der Freigabe
                              "freigabe_stand": jetzt}
    # Runde 33: Die Freigabe-Liste sortiert nach dem ERSTEN Abschicken — ein
    # nach einer Rueckfrage erneut abgeschicktes Protokoll springt sonst nach
    # oben bzw. unten, waehrend der Chef gerade daran arbeitet.
    if not doc.get("erstmals_abgeschickt_am"):
        setzen["erstmals_abgeschickt_am"] = jetzt
    res = await db.pickup_protocols.update_one(
        {"id": doc["id"], "status": "entwurf"},
        {"$set": setzen,
         "$unset": {"rueckfrage": "", "rueckfrage_am": ""}})
    if not res.matched_count:
        # Zwischen Lesen und Schreiben hat sich der Stand geaendert.
        akt = await db.pickup_protocols.find_one({"id": doc["id"]},
                                                 {"_id": 0, "status": 1})
        return {"ok": True, "status": (akt or {}).get("status", "unbekannt"),
                "protocol_id": doc["id"], "bereits": True}
    await log_activity(appt.get("dealer_id", ""), driver["id"],
                       "protokoll.zur_freigabe", ref=doc["id"],
                       meta={"appointment_id": appt_id,
                             "vehicle_id": appt.get("vehicle_id")})
    return {"ok": True, "status": ZUR_FREIGABE, "protocol_id": doc["id"]}


@router.post("/driver/appointments/{appt_id}/protocol/correction")
async def start_correction(appt_id: str, driver=Depends(current_driver)):
    """Neue Version anlegen: die alte bleibt als Beweis erhalten."""
    appt = await _appt_or_404(appt_id, driver)
    _termin_offen_oder_409(appt)
    doc = await _current(appt_id)
    if not doc or doc.get("status") != "final":
        raise HTTPException(400, "Es gibt kein abgeschlossenes Protokoll zum Korrigieren")
    # ATOMAR abloesen (Pruefbericht 09/2026): zwei gleichzeitige Korrektur-
    # Aufrufe lasen vorher beide dieselbe finale Version und legten ZWEI
    # Folgeversionen an. Nur wer das superseded-Flag selbst setzt, darf die
    # neue Version anlegen — jeder weitere Aufruf findet die Version nicht
    # mehr als aktuell vor und bekommt 409.
    abgeloest = await db.pickup_protocols.find_one_and_update(
        {"id": doc["id"], "status": "final", "superseded": {"$ne": True}},
        {"$set": {"superseded": True, "superseded_at": now_iso()}})
    if abgeloest is None:
        raise HTTPException(409, "Korrektur läuft bereits / Version nicht "
                                 "mehr aktuell")
    new_doc = {k: v for k, v in doc.items()
               if k not in ("id", "status", "pdf_path", "finalized_at",
                            "signature_driver_key", "signature_seller_key",
                            "superseded_at", "claim_bis",
                            # Gegenpruefung 12.09.2026: Die Korrektur wartet
                            # NEU — sonst stand sie in der Freigabe-Liste als
                            # "wartet seit 50 Std." ganz oben.
                            "abgeschickt_am", "abgeschickt_von", "erstmals_abgeschickt_am",
                            "freigegeben_am", "freigegeben_von", "freigabe_stand",
                            "rueckfrage", "rueckfrage_am", "rueckfrage_von")}
    new_doc.update({
        "id": str(uuid.uuid4()),
        "version": int(doc.get("version", 1)) + 1,
        "status": "entwurf",
        "superseded": False,
        "corrects_version": doc.get("version", 1),
        "created_at": now_iso(), "updated_at": now_iso(),
    })

    async def _abloesung_zuruecknehmen():
        # Schlaegt der Insert fehl, darf der Termin nicht OHNE aktuelle
        # Version zurueckbleiben: alte Version wieder aktiv schalten.
        try:
            await db.pickup_protocols.update_one(
                {"id": doc["id"]},
                {"$set": {"superseded": False},
                 "$unset": {"superseded_at": ""}})
        except Exception:  # noqa: BLE001
            log.exception("Protokoll-Korrektur: Abloesung von %s konnte "
                          "nicht zurueckgenommen werden", doc["id"])

    try:
        await db.pickup_protocols.insert_one(new_doc)
    except DuplicateKeyError:
        # Unique-Index (appointment_id, version) bzw. "ein aktuelles
        # Protokoll je Termin": ein paralleler Aufruf hat die Folgeversion
        # bereits angelegt.
        await _abloesung_zuruecknehmen()
        raise HTTPException(409, "Korrektur läuft bereits / Version nicht "
                                 "mehr aktuell")
    except Exception:
        await _abloesung_zuruecknehmen()
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
    # Review 09/2026: Der Abschluss machte auch STORNIERTE (oder als "nicht
    # abgeholt" beendete) Termine zu "abgeholt". Nur offene/verschobene
    # Termine — oder die Selbstheilung eines bereits finalen Protokolls bei
    # noch offenem Termin — duerfen hier durch.
    if (appt.get("status") or "offen") in ("storniert", "nicht abgeholt", "erledigt"):
        raise HTTPException(409, f"Termin ist '{appt.get('status')}' — ein Abschluss "
                                 "ist nicht mehr moeglich. Bitte den Haendler "
                                 "kontaktieren.")
    doc = await _current(appt_id)
    if not doc:
        raise HTTPException(400, "Bitte zuerst das Protokoll ausfüllen")
    if doc.get("status") == "final":
        # SELBSTHEILUNG (PR-Review 09/2026): Der Abschluss besteht aus
        # mehreren Schritten (Protokoll final -> Termin abgeholt ->
        # Fahrzeug-Lebenszyklus). Brach er dazwischen ab, war das
        # Protokoll final, der Termin aber offen — und jeder neue Versuch
        # scheiterte hier mit 409. Ein Wiederholungsaufruf zieht die
        # fehlenden Status nach, statt dauerhaft zu blockieren.
        # Auch wenn der Termin schon "abgeholt" ist, gibt es KEIN 409 mehr
        # (Pruefbericht 09/2026): der Aufruf ist idempotent — er stellt
        # sicher, dass protocol_id am Termin und der Fahrzeug-Lebenszyklus
        # gesetzt sind (try_set_lifecycle ist idempotent), und liefert das
        # bestehende Ergebnis. Netzabbruch oder Doppeltipp in der App
        # enden damit nicht mehr in einer Fehlermeldung.
        setzen: Dict[str, Any] = {"protocol_id": doc["id"]}
        if (appt.get("status") or "") != "abgeholt":
            setzen.update({"status": "abgeholt",
                           "status_changed_at": now_iso()})
        # Runde 17 (Nr. 7): auch die Selbstheilung nur per Compare-and-Set.
        heil_out = {"ok": True, "protocol_id": doc["id"],
                    "version": doc.get("version", 1),
                    "nachgezogen": True,
                    "pdf_url": f"/api/driver/appointments/{appt_id}/protocol.pdf"}
        if not await _termin_abgeholt_setzen(appt_id, driver["id"], setzen):
            heil_out["hinweis"] = TERMIN_GESCHLOSSEN_HINWEIS
            return heil_out
        import kaufvorgang as _kv
        if not await _kv.termin_status_uebernehmen(appt, "abgeholt") and appt.get("vehicle_id"):
            await try_set_lifecycle(appt["vehicle_id"],
                                    appt.get("dealer_id", ""), "abgeholt")
        return heil_out

    # Gegenpruefung 12.09.2026: Starb ein frueherer Abschluss mittendrin
    # (Deploy, Absturz), stand das Protokoll nach Ablauf des Claims fuer immer
    # auf "wird_abgeschlossen" — der Fahrer las "zuerst zur Freigabe
    # schicken", der Chef sah es nicht mehr. Abgelaufen = wieder freigegeben.
    if (doc.get("status") == "wird_abgeschlossen"
            and await _abgelaufene_claims_freigeben({"id": doc["id"]})):
        doc = await _current(appt_id) or doc

    # ---- Runde 30 (Wunsch Ahmad): Erst die Freigabe des Chefs ----
    # Unterschrieben wird NACH der Nachverhandlung. Ohne Freigabe gibt es
    # keine Unterschriften — sonst stuende am Ende der alte Preis im
    # Protokoll, obwohl vor Ort ein anderer vereinbart wurde.
    if doc.get("status") != FREIGEGEBEN:
        if doc.get("status") == ZUR_FREIGABE:
            raise HTTPException(409, "Das Protokoll liegt beim Händler zur "
                                     "Freigabe. Sobald er freigegeben hat, "
                                     "könnt ihr unterschreiben.")
        raise HTTPException(409, "Bitte das Protokoll zuerst zur Freigabe an "
                                 "den Händler schicken.")
    # Hat der Chef den Preis nach der Anzeige noch geaendert?
    _preis_jetzt = doc.get("neuer_preis")
    if (body.neuer_preis_gesehen is not None or _preis_jetzt is not None) and (
            body.neuer_preis_gesehen is None or _preis_jetzt is None
            or abs(float(body.neuer_preis_gesehen) - float(_preis_jetzt)) > 0.004):
        raise HTTPException(409,
                            "Der Händler hat den Preis inzwischen geändert. Bitte die Seite neu laden und den neuen Preis zeigen, bevor unterschrieben wird.")
    # Gegenpruefung 12.09.2026: auch Vermerk und Freigabe-Stand. Aenderte der
    # Chef nur den Vermerk, stand der neue Text sonst ueber Unterschriften,
    # die ihn nie gesehen hatten.
    _stand_jetzt = doc.get("freigabe_stand")
    if (body.freigabe_stand_gesehen is not None
            and body.freigabe_stand_gesehen != (_stand_jetzt or "")):
        raise HTTPException(409, STAND_GEAENDERT)

    # ---- Pflichtfelder: das Backend verlaesst sich NICHT auf die App ----
    _pflichtfelder_pruefen(doc, appt, seller_name=body.seller_name,
                           place=body.place)

    # ---- Abschluss ATOMAR beanspruchen: von beliebig vielen gleichzeitigen
    # Aufrufen gewinnt genau EINER (kein doppeltes PDF, keine doppelten
    # Unterschrift-Dateien). Der Claim traegt ein ABLAUFDATUM: stirbt der
    # Prozess mittendrin (Deploy, Absturz), kann der Fahrer nach 3 Minuten
    # einfach erneut abschliessen — ohne Ablauf waere das Protokoll fuer
    # immer in 'wird_abgeschlossen' gefangen (nur per Datenbank loesbar).
    from datetime import datetime as _dt, timedelta as _td, timezone as _tz
    _jetzt = _dt.now(_tz.utc)
    # Gegenpruefung 12.09.2026: Der Claim verlangt ausdruecklich FREIGEGEBEN.
    # Vorher genuegte 'nicht final/nicht in Arbeit' — zog der Chef die
    # Freigabe im selben Moment zurueck (zurueck=true -> entwurf), lief der
    # Abschluss trotzdem durch und das Protokoll wurde mit dem alten Preis
    # unterschrieben.
    # Runde 33 (Gegenpruefung 12.09.2026): Der Claim verlangt auch den
    # gerade geprueften Preis. Aenderte der Chef ihn genau zwischen Pruefung
    # und Claim, stand sonst ein Preis im PDF, den der Verkaeufer nie sah.
    claim = await db.pickup_protocols.find_one_and_update(
        {"id": doc["id"],
         "$or": [{"status": FREIGEGEBEN, "neuer_preis": _preis_jetzt,
                  # Gegenpruefung 12.09.2026: auch Vermerk und Stand
                  "preis_notiz": doc.get("preis_notiz"),
                  "freigabe_stand": _stand_jetzt},
                 {"status": "wird_abgeschlossen",
                  "claim_bis": {"$lt": _jetzt.isoformat()}}]},
        {"$set": {"status": "wird_abgeschlossen",
                  "claim_bis": (_jetzt + _td(minutes=3)).isoformat(),
                  "updated_at": now_iso()}})
    if not claim:
        akt = await db.pickup_protocols.find_one({"id": doc["id"]},
                                                 {"_id": 0, "status": 1, "neuer_preis": 1,
                                                  "preis_notiz": 1, "freigabe_stand": 1})
        if ((akt or {}).get("status") == FREIGEGEBEN
                and (akt or {}).get("neuer_preis") != _preis_jetzt):
            raise HTTPException(409,
                                "Der Händler hat den Preis inzwischen geändert. Bitte die Seite neu laden und den neuen Preis zeigen, bevor unterschrieben wird.")
        if ((akt or {}).get("status") == FREIGEGEBEN
                and ((akt or {}).get("preis_notiz") != doc.get("preis_notiz")
                     or (akt or {}).get("freigabe_stand") != _stand_jetzt)):
            raise HTTPException(409, STAND_GEAENDERT)
        if (akt or {}).get("status") in ("entwurf", ZUR_FREIGABE):
            raise HTTPException(409, "Der Händler hat die Freigabe gerade zurückgezogen — bitte die Seite neu laden.")
        raise HTTPException(409, "Das Protokoll wird gerade abgeschlossen "
                                 "— bitte einen Moment warten.")

    dealer_id = appt.get("dealer_id", "x")
    # Alle in DIESEM Abschluss geschriebenen Storage-Keys (Unterschrift-
    # PNGs, PDF). Bricht der Abschluss danach ab, werden sie im Rollback
    # wieder entfernt — vorher blieben sie verwaist im Storage liegen
    # (Pruefbericht 09/2026). Ein Key wird VOR dem Schreiben vorgemerkt,
    # damit auch eine halb geschriebene Datei aufgeraeumt wird.
    geschrieben: List[str] = []

    async def _rollback():
        """Claim freigeben, damit der Fahrer neu abschliessen kann — und
        bereits geschriebene Dateien verwerfen.

        Runde 30: zurueck auf FREIGEGEBEN (nicht auf 'entwurf'). Sonst
        haette ein gescheiterter Abschluss die Freigabe des Chefs geloescht
        und der Fahrer haette vor Ort erneut auf ihn warten muessen."""
        try:
            await db.pickup_protocols.update_one(
                {"id": doc["id"], "status": "wird_abgeschlossen"},
                {"$set": {"status": FREIGEGEBEN},
                 "$unset": {"claim_bis": ""}})
        except Exception:  # noqa: BLE001
            log.exception("Protokoll-Rollback: Claim von %s konnte nicht "
                          "freigegeben werden (laeuft nach 3 Min. ab)", doc["id"])
        await _dateien_verwerfen(geschrieben, dealer_id)
        geschrieben.clear()

    def _save_sig(b64: str, who: str) -> tuple:
        """Runde 17 (Nr. 8): liefert (Storage-Key, Rohbytes) — die Bytes
        werden fuer das PDF weiterverwendet statt ein zweites Mal dekodiert."""
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
            geschrieben.append(key)
            storage.save(key, raw)
        except StorageError as exc:
            raise HTTPException(400, f"Unterschrift konnte nicht gespeichert werden: {exc}")
        return key, raw

    import asyncio as _asyncio
    try:
        sig_driver, sig_driver_raw = await _asyncio.to_thread(
            _save_sig, body.signature_driver_b64, "fahrer")
        sig_seller, sig_seller_raw = await _asyncio.to_thread(
            _save_sig, body.signature_seller_b64, "verkaeufer")
    except Exception:
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
    # Runde 24 (11.09.2026, Befund Ahmad "AUFTRAGGEBER —"): derselbe
    # Auftraggeber wie im Kaufvertrag (Kaeuferfelder bzw. Sucher-Einstellungen
    # des Erstellers), nicht mehr das nackte Firmen-Dokument.
    from auftraggeber import auftraggeber_fuer_termin
    dealer = await auftraggeber_fuer_termin(appt)

    filled = {k: doc.get(k) for k in
              ("vehicle_check", "documents", "keys_count", "keys_expected",
               "features", "condition", "damages_confirmed", "new_damages",
               "notes")}
    filled["place"] = body.place or doc.get("place") or ""
    filled["seller_name"] = body.seller_name or appt.get("seller_name") or ""
    filled["driver_name"] = driver.get("display_name", "")
    # Runde 30: der nachverhandelte Preis steht im unterschriebenen PDF.
    # Gegenpruefung: aus dem FRISCH beanspruchten Dokument lesen (claim),
    # nicht aus dem vor dem Claim gelesenen Stand — sonst koennten PDF und
    # Kaufvorgang auseinanderlaufen.
    _preis_final = (claim or doc).get("neuer_preis")
    filled["neuer_preis"] = _preis_final
    filled["preis_notiz"] = (claim or doc).get("preis_notiz") or ""
    filled["signature_driver"] = sig_driver_raw
    filled["signature_seller"] = sig_seller_raw
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
        geschrieben.append(pdf_key)
        from storage_service import save_async
        await save_async(pdf_key, pdf_bytes)
    except StorageError as exc:
        await _rollback()
        raise HTTPException(400, f"PDF konnte nicht gespeichert werden: {exc}")
    except Exception:
        await _rollback()
        raise

    # Runde 17 (Nr. 7): Zwischen Vorabpruefung und diesem Punkt liegen
    # Dateischreiben und PDF-Erzeugung — wurde der Fahrer inzwischen aus
    # der Firma entfernt (oder die Firma gesperrt), darf das Protokoll
    # nicht mehr final werden: Verknuepfung erneut pruefen, sonst Rollback.
    try:
        await _zugriff_pruefen(appt, driver)
    except HTTPException:
        await _rollback()
        raise

    # Ab hier sind die Dateien im Protokoll referenziert — erst wenn DIESER
    # Schritt fehlschlaegt, waeren sie verwaist, deshalb auch hier Rollback.
    try:
        await db.pickup_protocols.update_one(
            {"id": doc["id"]},
            {"$unset": {"claim_bis": ""},
             "$set": {"status": "final", "pdf_path": pdf_key,
                      "signature_driver_key": sig_driver,
                      "signature_seller_key": sig_seller,
                      "seller_name": filled["seller_name"],
                      "place": filled["place"],
                      "finalized_at": now_iso(), "updated_at": now_iso()}})
    except Exception:
        await _rollback()
        raise

    # Termin + Fahrzeug-Lebenszyklus nachziehen: abgeschlossen = abgeholt.
    # Runde 17 (Nr. 7): Compare-and-Set — ein inzwischen geschlossener
    # Termin (oder ein entfernter Fahrer) bleibt unangetastet; das Protokoll
    # ist trotzdem gespeichert (Beweis), der Fahrer bekommt einen Hinweis.
    termin_gesetzt = await _termin_abgeholt_setzen(
        appt_id, driver["id"],
        {"status": "abgeholt", "status_changed_at": now_iso(),
         "protocol_id": doc["id"]})
    if termin_gesetzt:
        # Runde 30 (Wunsch Ahmad): Wurde vor Ort nachverhandelt, ist DAS der
        # Preis, den die Firma wirklich zahlt. Er gehoert deshalb in den
        # Kaufvorgang — sonst stuende in Akte und Auswertung weiter der alte
        # Vertragspreis. VOR der Statusuebernahme, damit die Zusammenfassung
        # gleich den richtigen Preis ans Fahrzeug schreibt.
        if _preis_final is not None and appt.get("kaufvorgang_id"):
            await db.kaufvorgaenge.update_one(
                {"id": appt["kaufvorgang_id"], "dealer_id": appt.get("dealer_id")},
                {"$set": {"purchase_price": float(_preis_final),
                          "preis_nachverhandelt": True,
                          "preis_vorher": (contract or {}).get("purchase_price"),
                          "updated_at": now_iso()}})
        # Umbau Kaufvorgaenge: abgeholt gilt fuer den VORGANG dieses Termins,
        # das Fahrzeug bekommt die Zusammenfassung (und den realisierten Preis).
        import kaufvorgang as _kv
        if not await _kv.termin_status_uebernehmen(appt, "abgeholt") and appt.get("vehicle_id"):
            await try_set_lifecycle(appt["vehicle_id"], dealer_id, "abgeholt")
    await log_activity(dealer_id, driver["id"], "abholprotokoll.abgeschlossen",
                       ref=appt.get("vehicle_id"),
                       meta={"version": doc.get("version", 1),
                             "appointment_id": appt_id,
                             "termin_gesetzt": termin_gesetzt})
    out: Dict[str, Any] = {
        "ok": True, "protocol_id": doc["id"], "version": doc.get("version", 1),
        "pdf_url": f"/api/driver/appointments/{appt_id}/protocol.pdf"}
    if not termin_gesetzt:
        out["hinweis"] = TERMIN_GESCHLOSSEN_HINWEIS
    return out


@router.get("/driver/appointments/{appt_id}/protocol.pdf")
async def driver_protocol_pdf(appt_id: str, driver=Depends(current_driver)):
    """Das ausgefüllte, abgeschlossene Protokoll als PDF (Fahrer-Ansicht)."""
    from fastapi import Response
    await _appt_or_404(appt_id, driver)
    doc = await _current(appt_id)
    if not doc or doc.get("status") != "final" or not doc.get("pdf_path"):
        raise HTTPException(404, "Noch kein abgeschlossenes Protokoll vorhanden")
    from storage_service import load_async, StorageError
    try:
        data = await load_async(doc["pdf_path"])
    except StorageError:
        raise HTTPException(404, "PDF nicht gefunden")
    return Response(content=data, media_type="application/pdf")


async def _protokoll_im_bereich(user: dict, doc: dict) -> bool:
    """Umbau Kaufvorgaenge 09.09.2026: das Protokoll gehoert zum TERMIN und
    damit zum Kaufvorgang eines Suchers — das gemeinsame Fahrzeug gibt
    keinen Zugriff mehr (Mitbearbeiter sehen sonst Verkaeufer/Unterschrift
    des Kollegen)."""
    appt = await db.appointments.find_one(
        {"id": doc.get("appointment_id"), "dealer_id": user["dealer_id"]},
        {"_id": 0, "created_by": 1, "contract_id": 1, "kaufvorgang_id": 1})
    return bool(appt) and await termin_im_bereich(user, appt)


# ---------- Händler-Sicht ----------
@router.get("/vehicles/{vehicle_id}/protocols")
async def dealer_list_protocols(vehicle_id: str, user=Depends(_dealer_dep)):
    """Alle Protokoll-Versionen eines Fahrzeugs (Händler/Chef)."""
    # Runde 16: Sucher nur zu Fahrzeugen im eigenen Bereich (verglichen
    # oder eigener Vertrag); Umbau Kaufvorgaenge: darin nur die Protokolle
    # der EIGENEN Termine.
    if ist_sucher(user) and not await fahrzeug_im_bereich(user, vehicle_id):
        raise HTTPException(404, "Fahrzeug nicht gefunden")
    docs = await db.pickup_protocols.find(
        {"vehicle_id": vehicle_id, "dealer_id": user["dealer_id"],
         "status": "final"},
        {"_id": 0, "id": 1, "version": 1, "finalized_at": 1, "driver_name": 1,
         "seller_name": 1, "place": 1, "corrects_version": 1, "superseded": 1,
         "appointment_id": 1},
    ).sort("version", -1).to_list(20)
    if ist_sucher(user):
        eigene = []
        for d in docs:
            if await _protokoll_im_bereich(user, d):
                eigene.append(d)
        docs = eigene
    for d in docs:
        d.pop("appointment_id", None)
    return docs


def _abweichungen(doc: dict, werte: Optional[Dict[str, Any]] = None) -> List[dict]:
    """Worueber laesst sich verhandeln? (Runde 30)

    Runde 33: ueber protokoll_vergleich — mit dem Wert laut Vertrag und dem
    Wert vor Ort. Ja/Nein-Zeilen zaehlen nur, wenn der Vertrag etwas anderes
    sagt ("Gewerbliche Nutzung: Nein" war vorher faelschlich eine Abweichung);
    ein alter Korrekturwert bei "stimmt" zaehlt nicht."""
    zeilen = PV.vergleich(VEHICLE_CHECK_FIELDS, doc.get("vehicle_check") or {},
                          doc.get("condition") or {}, werte or {})
    return PV.abweichungen(zeilen)


_FREIGABE_STATI = [ZUR_FREIGABE, FREIGEGEBEN]
# Obergrenze gegen Ausreisser; realistisch warten nur wenige gleichzeitig.
_FREIGABE_MAX = 500

STAND_GEAENDERT = ("Der Händler hat Preis oder Vermerk inzwischen geändert. Bitte die "
                   "Seite neu laden und dem Verkäufer den neuen Stand zeigen, bevor "
                   "unterschrieben wird.")

# Gegenpruefung 12.09.2026: Der Zaehler im Menue fragt alle 20 s je offenem
# Tab — dafuer nur die Felder, die er braucht, nicht ganze Protokolle.
_ZAEHLER_FELDER = {"_id": 0, "id": 1, "status": 1, "appointment_id": 1, "vehicle_id": 1,
                   "abgeschickt_am": 1, "erstmals_abgeschickt_am": 1}


async def _abgelaufene_claims_freigeben(bedingung: Dict[str, Any]) -> int:
    """Gegenpruefung 12.09.2026: Ein Abschluss, der mittendrin starb (Deploy,
    Absturz), hinterliess "wird_abgeschlossen" mit abgelaufenem Claim. Das
    Protokoll verschwand aus Liste und Zaehler, der Chef las dauerhaft "wird
    gerade unterschrieben". Nach Ablauf gilt wieder die Freigabe."""
    from datetime import datetime as _dt, timezone as _tz
    res = await db.pickup_protocols.update_many(
        {**bedingung, "status": "wird_abgeschlossen",
         "claim_bis": {"$lt": _dt.now(_tz.utc).isoformat()}},
        {"$set": {"status": FREIGEGEBEN}, "$unset": {"claim_bis": ""}})
    return res.modified_count


async def _wartende_protokolle(user, felder: Optional[Dict[str, int]] = None) -> List[tuple]:
    """Alle Protokolle, die beim Chef liegen — zur Freigabe oder freigegeben,
    noch nicht unterschrieben.

    Runde 33 (Analyse 12.09.2026, Wunsch Ahmad "mehrere Fahrer gleichzeitig"):
      * das am LAENGSTEN wartende zuerst (vorher: neueste zuerst)
      * keine harte Grenze von 50 VOR dem Sucher-Filter mehr — ein Sucher
        konnte seine eigenen verlieren, die am laengsten wartenden fielen raus
      * Protokolle zu abgeschlossenen oder geloeschten Terminen blieben fuer
        immer stehen — jetzt nicht mehr
      * Termine gebuendelt geladen statt einzeln je Protokoll
    Gegenpruefung 12.09.2026: Die Grenze griff ohne Sortierung — liegen
    gebliebene Protokolle geschlossener Termine (die aeltesten) verdraengten
    die Fahrer, die JETZT warten. Geladen wird deshalb das zuletzt Abgeschickte
    zuerst; angezeigt weiter das am laengsten Wartende oben."""
    await _abgelaufene_claims_freigeben({"dealer_id": user["dealer_id"]})
    docs = await db.pickup_protocols.find(
        {"dealer_id": user["dealer_id"], "status": {"$in": _FREIGABE_STATI},
         "superseded": {"$ne": True}},
        felder or {"_id": 0, "pdf_path": 0},
    ).sort("abgeschickt_am", -1).to_list(_FREIGABE_MAX)
    if len(docs) >= _FREIGABE_MAX:
        log.warning("Freigaben: Firma %s hat %s oder mehr offene Protokolle — die aeltesten "
                    "werden nicht gezeigt", user["dealer_id"], _FREIGABE_MAX)
    termin_ids = sorted({d.get("appointment_id") for d in docs if d.get("appointment_id")})
    termine: Dict[str, dict] = {}
    if termin_ids:
        async for t in db.appointments.find(
                {"id": {"$in": termin_ids}, "dealer_id": user["dealer_id"]},
                {"_id": 0, "id": 1, "status": 1, "pickup_date": 1, "pickup_time": 1,
                 "pickup_address": 1, "seller_name": 1, "vehicle_id": 1,
                 "contract_id": 1, "created_by": 1, "kaufvorgang_id": 1}):
            termine[t["id"]] = t
    paare = []
    for d in docs:
        appt = termine.get(d.get("appointment_id"))
        if not appt or (appt.get("status") or "offen") in _ABGESCHLOSSEN:
            continue
        # Runde 16/29: Sucher sehen nur ihre eigenen Vorgaenge.
        if ist_sucher(user) and not await termin_im_bereich(user, appt):
            continue
        paare.append((d, appt))
    paare.sort(key=lambda p: str(p[0].get("erstmals_abgeschickt_am")
                                 or p[0].get("abgeschickt_am") or ""))
    return paare


def _eur(preis) -> str:
    try:
        return f"{float(preis):,.2f}".replace(",", "X").replace(".", ",").replace("X", ".") + " €"
    except (TypeError, ValueError):
        return ""


@router.get("/protocols/zur-freigabe/anzahl")
async def protokolle_zur_freigabe_anzahl(user=Depends(_dealer_dep)):
    """Runde 33: Zaehler fuers Menue — der Chef merkt auf jeder Seite, dass
    ein Fahrer beim Verkaeufer auf seine Freigabe wartet."""
    paare = await _wartende_protokolle(user, _ZAEHLER_FELDER)
    wartend = [d for d, _a in paare if d.get("status") == ZUR_FREIGABE]
    return {"wartet": len(wartend),
            "freigegeben": sum(1 for d, _a in paare if d.get("status") == FREIGEGEBEN),
            # Gegenpruefung 12.09.2026: fuer den Hinweis "neues Protokoll" — die
            # Zahl allein bleibt gleich, wenn eins freigegeben und eins
            # abgeschickt wird.
            "ids": [d.get("id") for d in wartend]}


@router.get("/protocols/zur-freigabe")
async def protokolle_zur_freigabe(user=Depends(_dealer_dep)):
    """Runde 30 (Wunsch Ahmad): Was wartet gerade auf die Freigabe des Chefs?

    Liefert das ausgefuellte Protokoll — Vergleich Vertrag/vor Ort, neue
    Schaeden, Kilometerstand — damit der Chef entscheiden (und notfalls
    beim Verkaeufer anrufen) kann, ohne das ganze PDF zu oeffnen.
    Runde 33: fuer die eigene Seite "Freigaben" (siehe _wartende_protokolle)."""
    paare = await _wartende_protokolle(user)
    fz_ids = sorted({d.get("vehicle_id") or a.get("vehicle_id") for d, a in paare} - {None, ""})
    fahrzeuge: Dict[str, dict] = {}
    if fz_ids:
        # Zusaetzlich ueber dealer_id — Fahrzeug-IDs koennen firmenuebergreifend gleich sein.
        async for v in db.vehicles.find({"id": {"$in": fz_ids}, "dealer_id": user["dealer_id"]},
                                        {"_id": 0, "id": 1, "data": 1}):
            fahrzeuge[v["id"]] = v.get("data") or {}
    vertrag_ids = sorted({a.get("contract_id") for _d, a in paare} - {None, ""})
    vertraege: Dict[str, dict] = {}
    if vertrag_ids:
        async for c in db.generated_pdfs.find({"id": {"$in": vertrag_ids}, "dealer_id": user["dealer_id"]},
                                              {"_id": 0, "id": 1, "contract_data": 1}):
            vertraege[c["id"]] = c.get("contract_data") or {}
    namen = await besitzer_namen(user["dealer_id"],
                                 [d.get("freigegeben_von") for d, _a in paare]
                                 + [d.get("rueckfrage_von") for d, _a in paare])
    raus = []
    for d, appt in paare:
        # Gegenpruefung 12.09.2026: Infinity/NaN aus einem einzigen Protokoll
        # liess die ganze Liste scheitern — und damit JEDE Freigabe der Firma.
        d = PV.json_sicher(d)
        try:
            fahrzeug = fahrzeuge.get(d.get("vehicle_id") or appt.get("vehicle_id")) or {}
            vertrag = vertraege.get(appt.get("contract_id")) or {}
            werte = PV.vertragswerte(fahrzeug, vertrag)
            zeilen = PV.vergleich(VEHICLE_CHECK_FIELDS, d.get("vehicle_check") or {},
                                  d.get("condition") or {}, werte)
            km = PV.zahl((d.get("condition") or {}).get("mileage"))
            name = " ".join(str(x) for x in (fahrzeug.get("make_label") or fahrzeug.get("make"),
                                             fahrzeug.get("model_label") or fahrzeug.get("model")) if x)
            raus.append({
                "protocol_id": d["id"],
                "appointment_id": d.get("appointment_id"),
                "vehicle_id": d.get("vehicle_id"),
                "status": d.get("status"),
                # Runde 33: Stand fuer die Konfliktpruefung bei der Freigabe.
                "stand": d.get("freigabe_stand") or d.get("updated_at"),
                "fahrzeug": name,
                "abholung": " ".join(x for x in (appt.get("pickup_date"), appt.get("pickup_time")) if x),
                "abholort": appt.get("pickup_address") or "",
                "verkaeufer": d.get("seller_name") or appt.get("seller_name") or "",
                "fahrer": d.get("driver_name") or "",
                "abgeschickt_am": d.get("abgeschickt_am"),
                "erstmals_abgeschickt_am": d.get("erstmals_abgeschickt_am") or d.get("abgeschickt_am"),
                "kilometerstand": (d.get("condition") or {}).get("mileage") or "",
                "kilometerstand_text": PV.km_text(km) if km is not None else "",
                "abweichungen": PV.abweichungen(zeilen),
                "vergleich": zeilen,
                "neue_schaeden": d.get("new_damages") or [],
                "schaeden_bestaetigt": d.get("damages_confirmed"),
                "bemerkungen": d.get("notes") or "",
                # Abnahme 12.09.2026: Der Chef bekam nur einen Auszug. Er soll
                # das GANZE ausgefuellte Protokoll sehen koennen, bevor er
                # freigibt — Haken bei Dokumenten und Ausstattung inklusive.
                "dokumente": d.get("documents") or {},
                "ausstattung": d.get("features") or {},
                "zustand": d.get("condition") or {},
                "schluessel": d.get("keys_count") or "",
                "schluessel_vereinbart": d.get("keys_expected") or "",
                "fahrzeugdaten": d.get("vehicle_check") or {},
                "ort": d.get("place") or "",
                "preis_vertrag": vertrag.get("purchase_price"),
                "neuer_preis": d.get("neuer_preis"),
                "preis_notiz": d.get("preis_notiz") or "",
                "freigegeben_von_name": namen.get(d.get("freigegeben_von")) or "",
                "rueckfrage_von_name": namen.get(d.get("rueckfrage_von")) or "",
            })
        except Exception:  # noqa: BLE001
            log.exception("Freigaben: Protokoll %s liess sich nicht aufbereiten", d.get("id"))
            raus.append({
                "protocol_id": d.get("id"), "appointment_id": d.get("appointment_id"),
                "vehicle_id": d.get("vehicle_id"), "status": d.get("status"),
                "stand": d.get("freigabe_stand") or d.get("updated_at"),
                "fahrzeug": "", "abholort": appt.get("pickup_address") or "",
                "abholung": " ".join(x for x in (appt.get("pickup_date"), appt.get("pickup_time")) if x),
                "verkaeufer": d.get("seller_name") or appt.get("seller_name") or "",
                "fahrer": d.get("driver_name") or "",
                "abgeschickt_am": d.get("abgeschickt_am"),
                "erstmals_abgeschickt_am": d.get("erstmals_abgeschickt_am") or d.get("abgeschickt_am"),
                "abweichungen": [], "vergleich": [], "neue_schaeden": [],
                "neuer_preis": d.get("neuer_preis"), "preis_notiz": d.get("preis_notiz") or "",
                "ladefehler": "Dieses Protokoll enthält ungültige Werte — bitte beim Fahrer "
                              "nachfragen oder an ihn zurückschicken.",
            })
    return raus


async def _freigabe_konflikt(protocol_id: str, user: dict) -> str:
    """Warum ging die Freigabe nicht durch? Mit Name und Preis — damit am
    Telefon niemand einen Preis nennt, den ein Kollege schon ueberschrieben hat."""
    akt = await db.pickup_protocols.find_one(
        {"id": protocol_id, "dealer_id": user["dealer_id"]},
        {"_id": 0, "status": 1, "neuer_preis": 1, "freigegeben_von": 1, "rueckfrage_von": 1})
    if not akt:
        return "Das Protokoll gibt es nicht mehr."
    status = akt.get("status")
    if status == "wird_abgeschlossen":
        return "Vor Ort wird gerade unterschrieben — der Preis lässt sich jetzt nicht mehr ändern."
    if status == "final":
        return "Das Protokoll ist bereits abgeschlossen."
    wer_id = akt.get("freigegeben_von") if status == FREIGEGEBEN else akt.get("rueckfrage_von")
    # Gegenpruefung 12.09.2026: nicht sich selbst als "jemand anderes" nennen.
    if wer_id and wer_id == user.get("id"):
        if status == FREIGEGEBEN:
            return "Du hast dieses Protokoll bereits freigegeben — die Liste zeigt jetzt den aktuellen Stand."
        if status == "entwurf":
            return "Du hast das Protokoll bereits an den Fahrer zurückgeschickt."
    wer = (await besitzer_namen(user["dealer_id"], [wer_id])).get(wer_id) if wer_id else ""
    wer = wer or "jemand anderes"
    if status == FREIGEGEBEN:
        preis = (f" mit {_eur(akt['neuer_preis'])}" if akt.get("neuer_preis") is not None
                 else " zum Vertragspreis")
        return f"Inzwischen hat {wer}{preis} freigegeben — bitte die Liste prüfen."
    if status == "entwurf":
        return f"Inzwischen hat {wer} das Protokoll an den Fahrer zurückgeschickt."
    return "Der Stand hat sich gerade geändert — bitte neu laden."


@router.post("/protocols/{protocol_id}/freigabe")
async def protokoll_freigeben(protocol_id: str, body: FreigabeIn,
                              user=Depends(_dealer_dep)):
    """Runde 30: Der Chef gibt das abgeschickte Protokoll frei — mit dem
    neuen Preis, falls er nachverhandelt hat. Oder er schickt es zurueck,
    wenn der Fahrer etwas nachtragen soll.

    Erst nach der Freigabe kann vor Ort unterschrieben werden.
    Runde 33: Stand-Pruefung, Preis zuruecksetzen, klare Meldungen."""
    # Abgelaufener Claim (Abschluss abgestuerzt): wieder freigegeben, damit der
    # Chef nicht dauerhaft "wird gerade unterschrieben" liest.
    await _abgelaufene_claims_freigeben({"id": protocol_id, "dealer_id": user["dealer_id"]})
    doc = await db.pickup_protocols.find_one(
        {"id": protocol_id, "dealer_id": user["dealer_id"]}, {"_id": 0})
    if not doc:
        raise HTTPException(404, "Protokoll nicht gefunden")
    if ist_sucher(user) and not await _protokoll_im_bereich(user, doc):
        raise HTTPException(404, "Protokoll nicht gefunden")
    if doc.get("status") == "final":
        raise HTTPException(409, "Das Protokoll ist bereits abgeschlossen.")
    if doc.get("status") == "wird_abgeschlossen":
        raise HTTPException(409, "Vor Ort wird gerade unterschrieben — der Preis "
                                 "lässt sich jetzt nicht mehr ändern.")
    if doc.get("status") not in _FREIGABE_STATI:
        raise HTTPException(409, "Der Fahrer hat das Protokoll noch nicht "
                                 "abgeschickt.")
    appt = await db.appointments.find_one(
        {"id": doc.get("appointment_id"), "dealer_id": user["dealer_id"]},
        {"_id": 0, "status": 1})
    if not appt or (appt.get("status") or "offen") in _ABGESCHLOSSEN:
        raise HTTPException(409, "Der Termin ist bereits abgeschlossen oder gelöscht — "
                                 "eine Freigabe ist nicht mehr möglich.")
    jetzt = now_iso()
    bedingung: Dict[str, Any] = {"id": protocol_id, "status": {"$in": _FREIGABE_STATI}}
    if body.stand:
        # Gegenpruefung 12.09.2026: gegen den Freigabe-Stand, nicht updated_at —
        # den aendern auch Claim und Rollback eines gescheiterten Abschlusses,
        # und der Chef las dann von einer "fremden" Freigabe. Protokolle von vor
        # dieser Fassung haben noch keinen Stand: dann wie bisher.
        bedingung["freigabe_stand" if doc.get("freigabe_stand") else "updated_at"] = body.stand

    if body.zurueck:
        res = await db.pickup_protocols.update_one(
            bedingung,
            {"$set": {"status": "entwurf", "rueckfrage": (body.notiz or "").strip(),
                      "rueckfrage_am": jetzt, "rueckfrage_von": user["id"],
                      "updated_at": jetzt, "freigabe_stand": jetzt},
             "$unset": {"freigegeben_am": "", "freigegeben_von": ""}})
        if not res.matched_count:
            raise HTTPException(409, await _freigabe_konflikt(protocol_id, user))
        await log_activity(user["dealer_id"], user["id"],
                           "protokoll.zurueck_an_fahrer", ref=protocol_id,
                           meta={"notiz": (body.notiz or "")[:200]})
        return {"ok": True, "status": "entwurf", "stand": jetzt}

    if body.preis_zuruecksetzen:
        zuruecksetzen: Dict[str, Any] = {"updated_at": jetzt, "freigabe_stand": jetzt}
        if doc.get("status") == FREIGEGEBEN:
            # Gegenpruefung 12.09.2026: Wer den Preis zuruecksetzt, bestimmt jetzt,
            # was unterschrieben wird — Liste und Konfliktmeldung nannten sonst
            # den, der vorher mit dem verhandelten Preis freigegeben hatte.
            zuruecksetzen.update({"freigegeben_von": user["id"], "freigegeben_am": jetzt})
        res = await db.pickup_protocols.update_one(
            bedingung,
            {"$set": zuruecksetzen, "$unset": {"neuer_preis": "", "preis_notiz": ""}})
        if not res.matched_count:
            raise HTTPException(409, await _freigabe_konflikt(protocol_id, user))
        await log_activity(user["dealer_id"], user["id"], "protokoll.preis_zurueckgesetzt",
                           ref=protocol_id, meta={"vorher": doc.get("neuer_preis")})
        return {"ok": True, "status": doc.get("status"), "neuer_preis": None, "stand": jetzt}

    setzen: Dict[str, Any] = {"status": FREIGEGEBEN, "freigegeben_am": jetzt,
                              "freigegeben_von": user["id"], "updated_at": jetzt,
                              "freigabe_stand": jetzt}
    if body.neuer_preis is not None:
        setzen["neuer_preis"] = float(body.neuer_preis)
    if body.notiz is not None:
        setzen["preis_notiz"] = body.notiz.strip()
    res = await db.pickup_protocols.update_one(
        bedingung,
        {"$set": setzen, "$unset": {"rueckfrage": "", "rueckfrage_am": ""}})
    if not res.matched_count:
        raise HTTPException(409, await _freigabe_konflikt(protocol_id, user))
    await log_activity(user["dealer_id"], user["id"], "protokoll.freigegeben",
                       ref=protocol_id,
                       meta={"neuer_preis": body.neuer_preis,
                             "notiz": (body.notiz or "")[:200]})
    return {"ok": True, "status": FREIGEGEBEN,
            "neuer_preis": setzen.get("neuer_preis", doc.get("neuer_preis")),
            "stand": jetzt}


@router.get("/protocols/{protocol_id}.pdf")
async def dealer_protocol_pdf(protocol_id: str, user=Depends(_dealer_dep)):
    from fastapi import Response
    doc = await db.pickup_protocols.find_one(
        {"id": protocol_id, "dealer_id": user["dealer_id"]}, {"_id": 0})
    if not doc or not doc.get("pdf_path"):
        raise HTTPException(404, "Protokoll nicht gefunden")
    # Runde 16: Sucher nur im eigenen Bereich (Fahrzeug oder Termin).
    if ist_sucher(user) and not await _protokoll_im_bereich(user, doc):
        raise HTTPException(404, "Protokoll nicht gefunden")
    from storage_service import load_async, StorageError
    try:
        data = await load_async(doc["pdf_path"])
    except StorageError:
        raise HTTPException(404, "PDF nicht gefunden")
    return Response(content=data, media_type="application/pdf")
