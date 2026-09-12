"""Appointment endpoints: CRUD + pickup-order.pdf."""
import asyncio
import logging
import uuid
from datetime import datetime
from typing import Any, Dict, Literal, Optional, Tuple

from fastapi import APIRouter, Depends, HTTPException, Response
from pydantic import BaseModel, Field, field_validator

from pymongo.errors import DuplicateKeyError

from deps import (TERMIN_OFFEN_WERTE, clean_doc, current_user, datum_iso_pruefen, db,
                  fahrzeug_bereich, log_activity, now_iso, current_firma, termin_bereich,
                  termin_im_bereich, uhrzeit_hhmm_pruefen)
from lifecycle import try_set_lifecycle

log = logging.getLogger("autohandel")

router = APIRouter()

# Endzustaende eines Termins (dieselbe Menge wie ABGESCHLOSSEN in
# Termine.jsx). Nachpruefung Runde 14: Grundlage fuer die Chef-Sperre bei
# nachtraeglichen Aenderungen (Nr. 99/98) und die Aufraeumfrist (Nr. 114).
ABGESCHLOSSEN = frozenset({"abgeholt", "nicht abgeholt", "storniert", "erledigt"})

# Runde 17 (Nr. 4): Hinweis, wenn der Kaufvertrag nach einer Terminaenderung
# NICHT neu erzeugt werden konnte (z.B. Vertrag ausserhalb des Bereichs des
# Sucher-Kontos, PDF-Fehler). Termine.jsx zeigt data.hinweis als Warnung.
VERTRAG_VERALTET_HINWEIS = ("Termin gespeichert — der Kaufvertrag zeigt noch den "
                            "alten Abholtermin, bitte erneut speichern")
# Runde 17 (Nr. 2): Steuerflags im Eingabemodell, die NICHT in der Datenbank
# landen (siehe AppointmentIn.contract_loesen / fahrzeug_loesen).
_STEUERFELDER = frozenset({"contract_loesen", "fahrzeug_loesen"})


class AppointmentIn(BaseModel):
    """Eingabe fuer POST/PUT /appointments.

    Runde 17 (Nr. 2): Leere ID-Strings ("" oder nur Leerzeichen) fuer
    vehicle_id, contract_id und driver_id werden VOR der Validierung zu None
    — ein leeres Auswahlfeld der Oberflaeche ist kein Verweis auf "nichts".
    Weil None beim Aendern (PUT) als "nicht gesendet" verworfen wird, gibt
    es fuer das BEWUSSTE Loesen zwei Schalter:
      * contract_loesen=True  -> der Vertrag wird vom Termin getrennt; der
        alte Vertrag verliert seinen Terminverweis (Status "Termin erstellt"
        faellt auf "erstellt" zurueck).
      * fahrzeug_loesen=True  -> das Fahrzeug wird vom Termin getrennt (ohne
        Nebenwirkung auf dessen Lebenszyklus).
    Beide Schalter sind reine Steuerfelder und werden nie gespeichert.
    """
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
        "verschoben", "erledigt",       # Oberflaeche bietet beide an (09/2026)
    ]] = "offen"
    notes: Optional[str] = ""
    # Runde 17 (Nr. 1): keine negativen Betraege, kein inf/nan (Mongo
    # speichert NaN, die Oberflaeche rechnet damit weiter).
    final_price: Optional[float] = Field(default=None, ge=0, allow_inf_nan=False)
    extra_costs: Optional[float] = Field(default=None, ge=0, allow_inf_nan=False)
    # Runde 17 (Nr. 2): Steuerfelder, nur beim PUT ausgewertet (s. Docstring).
    contract_loesen: bool = False
    fahrzeug_loesen: bool = False

    # Runde 17 (Nr. 2): leerer/whitespace-String bei ID-Feldern -> None.
    @field_validator("vehicle_id", "contract_id", "driver_id", mode="before")
    @classmethod
    def _leere_id_ist_none(cls, v):
        if isinstance(v, str) and not v.strip():
            return None
        return v

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

    # Nachpruefung Runde 14 (Nr. 101): Datum und Uhrzeit nur im Format der
    # Oberflaeche (type=date / type=time). Per API kam bisher jeder String
    # durch — der Aufraeumer vergleicht pickup_date als Text, ein deutsches
    # Datum ("01.09.2026") galt dort als uralt (verfruehte Loeschung),
    # "zzzz" als ewig jung (nie geloescht). Leer bleibt erlaubt.
    # Runde 17 (Nr. 13): Pruefung zentral in deps (dieselbe Regel wie beim
    # Vertragsformular), Verhalten unveraendert.
    @field_validator("pickup_date")
    @classmethod
    def _datum_iso(cls, v):
        return datum_iso_pruefen(v)

    @field_validator("pickup_time")
    @classmethod
    def _uhrzeit_hhmm(cls, v):
        return uhrzeit_hhmm_pruefen(v)


def zusage_zuruecksetzen_wenn_geaendert(existing: dict, neu: dict) -> Tuple[dict, dict]:
    """Runde 17 (Nr. 5): Hat der Fahrer die Fahrt bereits ANGENOMMEN und
    aendert sich etwas Wesentliches (Datum, Uhrzeit, Abholadresse), gilt die
    alte Zusage nicht mehr — er muss die geaenderte Fahrt neu bestaetigen.

    Liefert ($set-Felder, $unset-Felder). Verglichen werden nur Felder, die
    in `neu` vorhanden sind (Teilaenderungen); leer und fehlend gelten als
    gleich. Wird auch aus routes/contracts.py benutzt (Vertrag verschiebt
    den Abholtermin), damit die Regel an genau EINER Stelle steht."""
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


async def _fahrzeug_passt_zum_vertrag(dealer_id: str, vehicle_id, contract_id) -> None:
    """Runde 18: Termin, Fahrzeug und Vertrag muessen ZUSAMMENGEHOEREN.
    Beide wurden bisher nur einzeln auf Zugriff geprueft — ein Termin konnte
    Fahrzeug A mit dem Vertrag fuer Fahrzeug B verbinden: der Abholauftrag
    zeigte Fahrzeug A, die Statusaenderung traf ueber den Kaufvorgang aber
    Fahrzeug B."""
    if not vehicle_id or not contract_id:
        return
    c = await db.generated_pdfs.find_one(
        {"id": contract_id, "dealer_id": dealer_id}, {"_id": 0, "vehicle_id": 1})
    if c is None:
        raise HTTPException(404, "Vertrag nicht gefunden")
    # Altvertraege ohne Fahrzeugverweis lassen sich nicht pruefen — kein Fehler.
    if c.get("vehicle_id") and c["vehicle_id"] != vehicle_id:
        raise HTTPException(409, "Der Vertrag gehört zu einem anderen Fahrzeug — "
                                 "bitte den passenden Vertrag wählen oder den "
                                 "Termin ohne Vertrag anlegen.")


async def _vertragszeiger_abgleichen(dealer_id: str, appt_id: str,
                                     contract_id: Optional[str]) -> None:
    """Runde 17 (Nr. 3): Vertragsverweise IDEMPOTENT aus dem Termin ableiten
    — bei jedem PUT, nicht nur beim erkannten Wechsel. Vorher blieb ein
    Verweis haengen, wenn der Wechsel-Write durchging, das Nachziehen aber
    abbrach (Neustart, Fehler) — der alte Vertrag zeigte weiter auf den
    Termin, SendDialog legte einen zweiten an.

      * Vertraege, die auf den Termin zeigen, aber nicht (mehr) SEIN Vertrag
        sind, verlieren den Verweis; nur ein "Termin erstellt" faellt dabei
        auf "erstellt" zurueck (andere Stati, z.B. gesendet, bleiben).
      * Der Vertrag des Termins zeigt auf den Termin (Status "Termin
        erstellt") — nur geschrieben, wenn er es noch nicht tut.
    Index (dealer_id, appointment_id) existiert (server.py)."""
    fremd: Dict[str, Any] = {"dealer_id": dealer_id, "appointment_id": appt_id}
    if contract_id:
        fremd["id"] = {"$ne": contract_id}
    await db.generated_pdfs.update_many(
        {**fremd, "status": "Termin erstellt"},
        {"$set": {"appointment_id": None, "status": "erstellt"}})
    await db.generated_pdfs.update_many(
        fremd, {"$set": {"appointment_id": None}})
    if contract_id:
        await db.generated_pdfs.update_one(
            {"id": contract_id, "dealer_id": dealer_id,
             "appointment_id": {"$ne": appt_id}},
            {"$set": {"appointment_id": appt_id, "status": "Termin erstellt"}})
    # Umbau Kaufvorgaenge: der Termin traegt den Vorgang seines Vertrags;
    # fremde Vorgaenge, die auf den Termin zeigen, verlieren den Verweis.
    import kaufvorgang as _kv
    kv = await db.kaufvorgaenge.find_one({"contract_id": contract_id}, {"_id": 0, "id": 1}) \
        if contract_id else None
    async for fremd_kv in db.kaufvorgaenge.find(
            {"dealer_id": dealer_id, "appointment_id": appt_id,
             **({"id": {"$ne": kv["id"]}} if kv else {})}, {"_id": 0, "id": 1, "status": 1}):
        await _kv.status_setzen(
            fremd_kv["id"],
            "vertrag_erstellt" if fremd_kv.get("status") == "abholung_geplant" else fremd_kv["status"],
            appointment_id=None)
    if kv:
        await db.appointments.update_one({"id": appt_id}, {"$set": {"kaufvorgang_id": kv["id"]}})
        await db.kaufvorgaenge.update_one({"id": kv["id"], "appointment_id": {"$ne": appt_id}},
                                          {"$set": {"appointment_id": appt_id, "updated_at": now_iso()}})
    else:
        await db.appointments.update_one({"id": appt_id}, {"$unset": {"kaufvorgang_id": ""}})


async def _vertrag_zeigt_termin(dealer_id: str, contract_id: str,
                                pickup_date, pickup_time) -> bool:
    """Runde 17 (Nr. 4): Steht im Vertrag bereits der wirksame Abholtermin?
    (Dann war ein 'False' von regenerate_contract_for_pickup kein Fehler,
    sondern 'nichts zu tun'.)"""
    c = await db.generated_pdfs.find_one(
        {"id": contract_id, "dealer_id": dealer_id},
        {"_id": 0, "pickup_date": 1, "pickup_time": 1})
    if not c:
        return False
    return ((c.get("pickup_date") or "") == (pickup_date or "")
            and (c.get("pickup_time") or "") == (pickup_time or ""))


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


async def _fahrer_nachpruefen(appt_id: str, dealer_id: str,
                              driver_id: Optional[str]) -> bool:
    """Runde 15 (Nr. 1): _fahrer_pruefen und der Termin-Write sind zwei
    Schritte. Entfernt der Chef den Fahrer GENAU dazwischen, laeuft seine
    Terminbereinigung (drivers.py) VOR unserem Write, und der Termin traegt
    danach einen nicht mehr verknuepften Fahrer. Deshalb nach dem Write ein
    zweites Mal pruefen und die Zuweisung zuruecknehmen, wenn die
    Verknuepfung inzwischen fehlt. Liefert False, wenn zurueckgenommen."""
    if not driver_id:
        return True
    if await db.dealer_drivers.find_one(
            {"dealer_id": dealer_id, "driver_account_id": driver_id}, {"_id": 1}):
        return True
    await db.appointments.update_one(
        {"id": appt_id, "dealer_id": dealer_id, "driver_id": driver_id},
        {"$unset": {"driver_id": ""},
         "$set": {"zuteilung": None, "updated_at": now_iso()}})
    return False


FAHRER_ENTFERNT_HINWEIS = ("Der Fahrer wurde soeben aus der Firma entfernt — "
                           "der Termin ist ohne Fahrer gespeichert.")
TERMIN_DOPPELT_HINWEIS = ("Für diesen Vertrag gibt es bereits einen offenen "
                          "Abholtermin — bitte den bestehenden Termin ändern "
                          "oder zuerst abschließen.")


async def _offener_termin_zum_vertrag(dealer_id: str, contract_id: Optional[str],
                                      ausser: Optional[str] = None) -> Optional[dict]:
    """Umbau Kaufvorgaenge 09.09.2026: offener Abholtermin desselben
    VERTRAGS (ohne den Termin `ausser`). Je Fahrzeug darf es mehrere geben —
    ein Termin je Kaufvorgang."""
    if not contract_id:
        return None
    q: Dict[str, Any] = {"dealer_id": dealer_id, "contract_id": contract_id,
                         "status": {"$in": TERMIN_OFFEN_WERTE}}
    if ausser:
        q["id"] = {"$ne": ausser}
    return await db.appointments.find_one(
        q, {"_id": 0, "id": 1, "contract_id": 1, "created_by": 1, "status": 1})


async def _offener_termin_zum_fahrzeug(dealer_id: str, vehicle_id: Optional[str],
                                       ausser: Optional[str] = None) -> Optional[dict]:
    """Runde 15 (Nr. 6): offener Abholtermin derselben Firma zu diesem
    Fahrzeug (ohne den Termin `ausser`, z.B. den gerade bearbeiteten)."""
    if not vehicle_id:
        return None
    q: Dict[str, Any] = {"dealer_id": dealer_id, "vehicle_id": vehicle_id,
                         "status": {"$in": TERMIN_OFFEN_WERTE}}
    if ausser:
        q["id"] = {"$ne": ausser}
    return await db.appointments.find_one(
        q, {"_id": 0, "id": 1, "contract_id": 1, "created_by": 1, "status": 1})


async def _fahrzeug_fuer_termin_erlaubt(user: dict, vehicle_id: str) -> bool:
    """Runde 28 (12.09.2026, Pruefbefund): Darf DIESES Konto einen Termin an
    dieses Fahrzeug haengen?

    Vorher genuegte die Firma. Fahrzeug-IDs sind aber ratbar (v_<Anzeigen-
    nummer>) — ein Sucher konnte damit einen Termin auf das Auto eines
    Kollegen setzen, dessen Fahrzeugdaten im Fahrer-PDF sehen und (ohne
    eigenen Vertrag) ueber try_set_lifecycle sogar dessen Fahrzeugstatus
    auf 'Abholung geplant' schieben.

    Erlaubt ist jetzt: das Fahrzeug liegt im eigenen Bereich (Chef: Firma;
    Sucher: selbst verglichen bzw. Mitbearbeiter) ODER es gibt einen
    EIGENEN Kaufvertrag dazu."""
    if await db.vehicles.count_documents(
            {"id": vehicle_id, **fahrzeug_bereich(user)}, limit=1):
        return True
    from routes.contracts import _vertrag_bereich
    return bool(await db.generated_pdfs.count_documents(
        {"vehicle_id": vehicle_id, **_vertrag_bereich(user)}, limit=1))


@router.post("/appointments")
async def create_appointment(body: AppointmentIn, user=Depends(current_firma)):
    appt_id = str(uuid.uuid4())
    title = body.title or "Fahrzeug abholen"
    # EIGENTUEMER-PRUEFUNG: Fahrzeug- und Vertrags-IDs sind erratbar
    # (v_<Inserats-ID>). Ohne diese Pruefung koennte ein Haendler einen
    # Termin auf ein FREMDES Fahrzeug legen und dessen Daten im Fahrer-PDF
    # bzw. Protokoll sehen.
    vehicle_doc = None
    if body.vehicle_id:
        # Umbau Kaufvorgaenge 09.09.2026: das Inserat ist firmenweit gemeinsam;
        # der Termin gehoert ueber Vertrag/Kaufvorgang dem Sucher.
        vehicle_doc = await db.vehicles.find_one(
            {"id": body.vehicle_id, "dealer_id": user["dealer_id"],
             "lifecycle": {"$ne": "geloescht"}}, {"_id": 0})
        if not vehicle_doc:
            raise HTTPException(404, "Fahrzeug nicht gefunden")
        # Runde 28: nur eigene Fahrzeuge (oder mit eigenem Vertrag).
        if not await _fahrzeug_fuer_termin_erlaubt(user, body.vehicle_id):
            raise HTTPException(404, "Fahrzeug nicht gefunden")
    vertrag_doc = None
    if body.contract_id:
        # Runde 12: der Vertrag muss im BEREICH des Kontos liegen (Sucher:
        # nur eigene). Vorher genuegte die Firma — ein Sucher konnte seinen
        # Termin an den Vertrag eines Kollegen haengen.
        from routes.contracts import _vertrag_bereich
        # Projektion MIT id: ein Vertrag ohne Verkaeuferfelder kaeme sonst als
        # leeres Dict zurueck und gaelte faelschlich als "nicht gefunden".
        vertrag_doc = await db.generated_pdfs.find_one(
            {"id": body.contract_id, **_vertrag_bereich(user)},
            {"_id": 0, "id": 1, "seller_name": 1, "seller_phone": 1, "seller_email": 1,
             "contract_data": 1, "kaufvorgang_id": 1})
        if vertrag_doc is None:
            raise HTTPException(404, "Vertrag nicht gefunden")
    await _fahrzeug_passt_zum_vertrag(user["dealer_id"], body.vehicle_id, body.contract_id)
    # Runde 18: "abgeholt" entsteht auch beim ANLEGEN nur mit unterschriebenem
    # Abholprotokoll, sobald ein Fahrer eingeteilt ist — dieselbe Regel wie
    # beim Aendern. Ein neuer Termin kann noch kein Protokoll haben, der Weg
    # ist damit geschlossen (ohne Fahrer bleibt der Buero-Abschluss erlaubt).
    if (body.status or "") == "abgeholt" and body.driver_id:
        raise HTTPException(409, "Für diesen Termin ist ein Fahrer eingeteilt. "
                                 "'Abgeholt' entsteht automatisch, sobald der "
                                 "Fahrer das Abholprotokoll unterschrieben "
                                 "abschließt.")
    await _fahrer_pruefen(user["dealer_id"], body.driver_id)
    # Umbau Kaufvorgaenge 09.09.2026: ein offener Abholtermin je VERTRAG
    # (vorher je Fahrzeug — zwei Sucher konnten dasselbe Inserat nicht
    # unabhaengig kaufen). Vorabpruefung, der Teil-Unique-Index faengt das Rennen.
    if (body.status or "offen") in TERMIN_OFFEN_WERTE \
            and await _offener_termin_zum_vertrag(user["dealer_id"], body.contract_id):
        raise HTTPException(409, TERMIN_DOPPELT_HINWEIS)
    if vehicle_doc and not body.title:
        d = vehicle_doc["data"]
        title = f"{d.get('make_label','')} {d.get('model_label','')} abholen".strip()
    doc = {"id": appt_id, "dealer_id": user["dealer_id"], "title": title,
           **body.model_dump(exclude_none=True, exclude=set(_STEUERFELDER)),
           "created_by": user["id"],
           "created_at": now_iso(), "updated_at": now_iso()}
    if "status" not in doc:
        doc["status"] = "offen"
    if vertrag_doc and vertrag_doc.get("kaufvorgang_id"):
        doc["kaufvorgang_id"] = vertrag_doc["kaufvorgang_id"]
    if vertrag_doc:
        # Runde 17 (Nr. 12): leere Verkaeuferfelder aus dem Vertrag fuellen
        # (Abholauftrag und Protokoll lesen sie vom Termin). Abweichende,
        # bewusst gesendete Werte bleiben erhalten — kein Fehler.
        cd = vertrag_doc.get("contract_data") or {}
        for feld in ("seller_name", "seller_phone", "seller_email"):
            if not (doc.get(feld) or "").strip():
                wert = vertrag_doc.get(feld) or cd.get(feld) or ""
                if wert:
                    doc[feld] = str(wert)[:500]
    if doc.get("driver_id"):
        # Wunsch 09/2026: der Fahrer bekommt die Fahrt ZUGETEILT und nimmt
        # sie in seiner App an oder lehnt sie ab.
        doc["zuteilung"] = "offen"
        doc["zuteilung_am"] = now_iso()
    try:
        await db.appointments.insert_one(doc)
    except DuplicateKeyError:
        raise HTTPException(409, TERMIN_DOPPELT_HINWEIS)
    hinweis = None
    if doc.get("driver_id") and not await _fahrer_nachpruefen(
            appt_id, user["dealer_id"], doc["driver_id"]):
        doc.pop("driver_id", None)
        doc["zuteilung"] = None
        hinweis = FAHRER_ENTFERNT_HINWEIS
    if body.contract_id:
        await db.generated_pdfs.update_one(
            {"id": body.contract_id, "dealer_id": user["dealer_id"]},
            {"$set": {"appointment_id": appt_id, "status": "Termin erstellt"}},
        )
    # Fahrzeugstatus: ueber den Kaufvorgang (Zusammenfassung aller Vorgaenge);
    # manueller Termin ohne Vertrag wie frueher direkt am Fahrzeug.
    import kaufvorgang as _kv
    if not await _kv.termin_status_uebernehmen(doc, doc.get("status") or "offen", user=user) \
            and body.vehicle_id:
        await try_set_lifecycle(body.vehicle_id, user["dealer_id"],
                                "abholung_geplant", user=user)
    if doc.get("kaufvorgang_id"):
        await db.kaufvorgaenge.update_one({"id": doc["kaufvorgang_id"]},
                                          {"$set": {"appointment_id": appt_id}})
    await log_activity(user["dealer_id"], user["id"], "termin.erstellt", ref=appt_id)
    out = clean_doc(doc)
    if hinweis:
        out["hinweis"] = hinweis
    return out


@router.get("/appointments")
async def list_appointments(response: Response, user=Depends(current_firma),
                            status: Optional[str] = None):
    # Runde 16: Sucher sehen nur Termine im eigenen Bereich (selbst angelegt,
    # eigenes Fahrzeug, eigener Vertrag); der Chef die ganze Firma.
    query: dict = await termin_bereich(user)
    if status:
        query["status"] = status
    # Nachpruefung Runde 14 (Nr. 74): Termine werden nie automatisch
    # geloescht, der Bestand waechst dauerhaft. Bei aufsteigender Sortierung
    # fielen ab 500 Terminen genau die KOMMENDEN weg. Limit 2000; die
    # Antwort bleibt eine Liste (Frontend), ein Kopf meldet den Abschnitt.
    items = await db.appointments.find(query, {"_id": 0}).sort("pickup_date", 1).to_list(2000)
    if len(items) >= 2000:
        response.headers["X-Truncated"] = "1"
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
    # Runde 15 (Nr. 3): nur die Fahrzeuge der gelisteten Termine und nur
    # die Felder, die Termine.jsx liest (vehicle.data) — vorher wurde der
    # komplette Fahrzeugbestand der Firma je Aufruf in den Speicher geladen
    # (inkl. Bestandsnotizen, Kosten, Inseratskopie).
    vehicles_map = {}
    fahrzeug_ids = list({a["vehicle_id"] for a in items if a.get("vehicle_id")})
    if fahrzeug_ids:
        async for v in db.vehicles.find(
                {"dealer_id": user["dealer_id"], "id": {"$in": fahrzeug_ids}},
                {"_id": 0, "id": 1, "data": 1, "status": 1, "lifecycle": 1,
                 "source": 1, "mobile_ad_id": 1}):
            vehicles_map[v["id"]] = v
    # Ehemalige Fahrer (Verknuepfung entfernt / Konto geloescht): nur noch
    # Name aus der Historie, keine Live-Berechtigung (Audit 09/2026, Punkt 13).
    hist_ids = [a["driver_id_hist"] for a in items
                if a.get("driver_id_hist") and not str(a["driver_id_hist"]).startswith("geloescht:")]
    hist_map = {}
    if hist_ids:
        async for d in db.driver_accounts.find({"id": {"$in": hist_ids}},
                                               {"_id": 0, "id": 1, "display_name": 1}):
            hist_map[d["id"]] = d.get("display_name")
    for a in items:
        if a.get("driver_id") and a["driver_id"] in drivers_map:
            a["driver"] = drivers_map[a["driver_id"]]
        elif a.get("driver_id_hist"):
            h = a["driver_id_hist"]
            a["driver"] = {"id": None, "ehemalig": True,
                           "name": ("Fahrer (gelöscht)" if str(h).startswith("geloescht:")
                                    else (hist_map.get(h) or "ehemaliger Fahrer"))}
        if a.get("vehicle_id") and a["vehicle_id"] in vehicles_map:
            a["vehicle"] = vehicles_map[a["vehicle_id"]]
    return items


@router.get("/appointments/{appt_id}")
async def get_appointment(appt_id: str, user=Depends(current_firma)):
    a = await db.appointments.find_one({"id": appt_id, **await termin_bereich(user)}, {"_id": 0})
    if not a:
        raise HTTPException(404, "Termin nicht gefunden")
    if a.get("vehicle_id"):
        v = await db.vehicles.find_one(
            {"id": a["vehicle_id"], "dealer_id": user["dealer_id"]}, {"_id": 0},
        )
        if v:
            # Runde 23 (11.09.2026, Gegenpruefung): Sucher sehen nur ihren
            # eigenen Einkaufspreis — auch hier nicht den des Kollegen aus dem
            # gemeinsamen Fahrzeug-Dokument (Chef unveraendert).
            await __import__("kaufvorgang").einkauf_fuer_sucher_maskieren(user, v)
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


async def _sucher_darf(user: dict, appt: dict) -> bool:
    """Ein Sucher darf nur Termine anfassen, die er angelegt hat, deren
    Vertrag ihm gehoert oder deren Kaufvorgang ihm gehoert (Umbau
    Kaufvorgaenge 09.09.2026 — das gemeinsame Fahrzeug gibt keinen
    Zugriff; Regel zentral in deps.termin_im_bereich, dieselbe wie beim
    Lesen)."""
    return await termin_im_bereich(user, appt)


@router.put("/appointments/{appt_id}")
async def update_appointment(appt_id: str, body: AppointmentIn, user=Depends(current_firma)):
    existing = await db.appointments.find_one(
        {"id": appt_id, "dealer_id": user["dealer_id"]}, {"_id": 0},
    )
    if not existing:
        raise HTTPException(404, "Termin nicht gefunden")
    if not await _sucher_darf(user, existing):
        raise HTTPException(403, "Sucher dürfen nur ihre eigenen Termine ändern "
                                 "(oder Termine zu ihren eigenen Verträgen)")
    # Audit 09/2026 (Befund "Terminaenderungen loeschen Daten"): NUR die
    # tatsaechlich mitgesendeten Felder schreiben. exclude_none=True hat
    # die Modell-Standardwerte ("" / status "offen") mitgeschrieben, sodass
    # eine Teilaenderung (z.B. nur driver_id) Verkaeuferdaten, Adresse,
    # Datum, Uhrzeit und Notizen geleert und den Status zurueckgesetzt hat.
    update = body.model_dump(exclude_unset=True)
    # Runde 17 (Nr. 2): Steuerfelder herausloesen — sie werden nie gespeichert.
    contract_loesen = bool(update.pop("contract_loesen", False))
    fahrzeug_loesen = bool(update.pop("fahrzeug_loesen", False))
    # Ein ausdruecklich gesendetes null bleibt nur dort erhalten, wo es
    # etwas bedeutet (driver_id = Fahrer entfernen); sonst wuerde null
    # Pflichtfelder auf None setzen.
    for feld in ("title", "seller_name", "seller_phone", "seller_email",
                 "pickup_address", "pickup_date", "pickup_time", "notes",
                 "status", "vehicle_id", "contract_id"):
        if feld in update and update[feld] is None:
            update.pop(feld)
    if contract_loesen and update.get("contract_id"):
        raise HTTPException(400, "contract_loesen und eine neue contract_id "
                                 "schließen sich aus")
    if fahrzeug_loesen and update.get("vehicle_id"):
        raise HTTPException(400, "fahrzeug_loesen und eine neue vehicle_id "
                                 "schließen sich aus")
    # Felder, die am Termin ENTFERNT werden ($unset).
    unset: Dict[str, Any] = {}
    # Auch beim Aendern: das Fahrzeug muss der Firma gehoeren (Umbau
    # Kaufvorgaenge: firmenweit gemeinsam, nicht mehr je Sucher).
    if update.get("vehicle_id"):
        # Runde 28: dieselbe Pruefung wie beim Anlegen — ein sauberer
        # eigener Termin darf nicht nachtraeglich auf das Auto eines
        # Kollegen umgebogen werden.
        if not await db.vehicles.find_one(
                {"id": update["vehicle_id"], "dealer_id": user["dealer_id"],
                 "lifecycle": {"$ne": "geloescht"}}, {"_id": 1}):
            raise HTTPException(404, "Fahrzeug nicht gefunden")
        if not await _fahrzeug_fuer_termin_erlaubt(user, update["vehicle_id"]):
            raise HTTPException(404, "Fahrzeug nicht gefunden")
    if update.get("contract_id"):
        # Runde 12: auch nachtraeglich nur Vertraege im eigenen Bereich —
        # vorher liess sich ein sauberer eigener Termin spaeter auf den
        # Vertrag eines Kollegen umbiegen.
        from routes.contracts import _vertrag_bereich
        if not await db.generated_pdfs.find_one(
                {"id": update["contract_id"], **_vertrag_bereich(user)}, {"_id": 1}):
            raise HTTPException(404, "Vertrag nicht gefunden")
    # Runde 18: auch nach dem Aendern muessen Fahrzeug und Vertrag, die dann
    # am Termin haengen, zusammengehoeren — geprueft, sobald eines von beiden
    # neu gesetzt wird (ein Loesen macht das Paar unvollstaendig, nichts zu
    # pruefen; unbeteiligte Aenderungen bleiben von Altdaten unbehelligt).
    if update.get("vehicle_id") or update.get("contract_id"):
        await _fahrzeug_passt_zum_vertrag(
            user["dealer_id"],
            update.get("vehicle_id") or (None if fahrzeug_loesen else existing.get("vehicle_id")),
            update.get("contract_id") or (None if contract_loesen else existing.get("contract_id")))
    # Nachpruefung Runde 14 (Nr. 99/98): Bei abgeschlossenen Terminen sind
    # Fahrer, Verkaeufer, Fahrzeug und Vertrag Beweisdaten (Protokoll,
    # driver_id_hist). Nachtraeglich aendert sie nur der Chef; ebenso den
    # Rueckweg aus einem Endstatus (Wieder-Oeffnen ist gewollte Korrektur,
    # drivers.py). Die Oberflaeche sendet immer das ganze Objekt — daher
    # zaehlt nur, was sich tatsaechlich aendert. Status/Notizen bleiben frei.
    status_neu = update.get("status", existing.get("status"))
    if existing.get("status") in ABGESCHLOSSEN and user.get("role") != "dealer":
        geschuetzt = ("driver_id", "vehicle_id", "contract_id", "seller_name",
                      "seller_phone", "seller_email", "pickup_address")
        # Runde 17 (Nr. 2): das Loesen von Vertrag/Fahrzeug ist ebenfalls
        # eine Aenderung an Beweisdaten.
        loesen_aendert = ((contract_loesen and existing.get("contract_id"))
                          or (fahrzeug_loesen and existing.get("vehicle_id")))
        if loesen_aendert or any(
                f in update and (update.get(f) or "") != (existing.get(f) or "")
                for f in geschuetzt):
            raise HTTPException(403, "Der Termin ist abgeschlossen — Fahrer, "
                                     "Verkäufer, Fahrzeug und Vertrag ändert "
                                     "danach nur der Händler-Hauptaccount")
        if status_neu not in ABGESCHLOSSEN:
            raise HTTPException(403, "Abgeschlossene Termine öffnet nur der "
                                     "Händler-Hauptaccount wieder")
    if "driver_id" in update:
        await _fahrer_pruefen(user["dealer_id"], update.get("driver_id"))
        if update.get("driver_id") and update["driver_id"] != existing.get("driver_id"):
            update["zuteilung"] = "offen"           # neuer Fahrer -> neu anfragen
            update["zuteilung_am"] = now_iso()
        elif not update.get("driver_id"):
            update["zuteilung"] = None
    # Audit 09/2026: Aendert sich nach der Zusage des Fahrers etwas
    # Wesentliches (Datum, Uhrzeit, Abholadresse), gilt die alte Zusage
    # nicht mehr — der Fahrer muss die geaenderte Fahrt neu bestaetigen.
    # Runde 17 (Nr. 5): Regel im Helfer zusage_zuruecksetzen_wenn_geaendert
    # (auch contracts.py nutzt ihn). Gilt, solange der Fahrer derselbe
    # bleibt — die Oberflaeche sendet driver_id immer mit; ein Wechsel oder
    # das Entfernen des Fahrers ist oben bereits behandelt.
    if "driver_id" not in update or update.get("driver_id") == existing.get("driver_id"):
        zusage_set, zusage_unset = zusage_zuruecksetzen_wenn_geaendert(existing, update)
        update.update(zusage_set)
        unset.update(zusage_unset)
    if contract_loesen and existing.get("contract_id"):
        # Runde 17 (Nr. 2): Vertrag bewusst vom Termin loesen; die Verweise
        # am alten Vertrag zieht _vertragszeiger_abgleichen nach dem Write
        # nach. Ein Veraltet-Merker (Nr. 4) ist damit gegenstandslos.
        unset["contract_id"] = ""
        unset["vertrag_veraltet"] = ""
    if fahrzeug_loesen and existing.get("vehicle_id"):
        # Runde 17 (Nr. 2): Fahrzeug loesen — ohne Lifecycle-Nebenwirkung
        # auf dem abgehaengten Fahrzeug (es bleibt, wie es ist).
        unset["vehicle_id"] = ""
    update["updated_at"] = now_iso()
    # Nachpruefung Runde 14 (Nr. 83/84): auch das ERSTMALIGE Setzen und das
    # bewusste Leeren des Datums gelten als Aenderung (vorher nur, wenn
    # vorher UND nachher ein Datum stand — der Vertrag blieb dann alt).
    pickup_changed = (
        "pickup_date" in update
        and (update.get("pickup_date") or "") != (existing.get("pickup_date") or "")
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
        # Nachpruefung Runde 14 (Nr. 82): gegen den Fahrer pruefen, der NACH
        # dem Update gilt — Fahrer + "abgeholt" in einem Aufruf umging die
        # Protokollpflicht.
        fahrer_effektiv = update.get("driver_id") if "driver_id" in update else existing.get("driver_id")
        if update["status"] == "abgeholt" and fahrer_effektiv:
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
        # Nachpruefung Runde 14 (Nr. 114): Die Aufraeumfrist zaehlt ab dem
        # ERSTEN Erreichen eines Endstatus (abgeschlossen_seit) und startet
        # beim Wieder-Oeffnen nicht neu; das Feld wird deshalb nie geloescht.
        # assets_cleaned_at bleibt ebenfalls stehen — die Fotos sind bereits
        # weg, ein Zuruecksetzen machte den Termin erneut zum Kandidaten.
        if update["status"] in ABGESCHLOSSEN and not existing.get("abgeschlossen_seit"):
            update["abgeschlossen_seit"] = update["status_changed_at"]
    # Runde 15 (Nr. 6): Fahrzeugwechsel oder Wieder-Oeffnen darf keinen
    # zweiten offenen Termin zum selben Fahrzeug ergeben.
    # Runde 17 (Nr. 2): das Fahrzeug, das NACH dem Update am Termin haengt
    # (bei fahrzeug_loesen keines).
    vehicle_id = update.get("vehicle_id") or (
        None if fahrzeug_loesen else existing.get("vehicle_id"))
    # Umbau Kaufvorgaenge: Vertragswechsel oder Wieder-Oeffnen darf keinen
    # zweiten offenen Termin zum selben VERTRAG ergeben (je Fahrzeug sind
    # mehrere Vorgaenge erlaubt).
    contract_pruefen = update.get("contract_id") or (
        None if contract_loesen else existing.get("contract_id"))
    if contract_pruefen and status_neu in TERMIN_OFFEN_WERTE \
            and ("contract_id" in update or "status" in update) \
            and await _offener_termin_zum_vertrag(user["dealer_id"], contract_pruefen, ausser=appt_id):
        raise HTTPException(409, TERMIN_DOPPELT_HINWEIS)
    aenderung: Dict[str, Any] = {"$set": update}
    if unset:
        aenderung["$unset"] = unset
    try:
        await db.appointments.update_one({"id": appt_id}, aenderung)
    except DuplicateKeyError:
        raise HTTPException(409, TERMIN_DOPPELT_HINWEIS)
    fahrer_entfernt = bool(update.get("driver_id")) and not await _fahrer_nachpruefen(
        appt_id, user["dealer_id"], update.get("driver_id"))
    # Nachpruefung Runde 14 (Nr. 113) / Runde 17 (Nr. 3): Vertragsverweise
    # bei JEDEM PUT idempotent aus dem Termin ableiten — sonst zeigte der
    # alte Vertrag weiter auf den Termin und der neue auf keinen (SendDialog
    # legte einen zweiten an).
    contract_gewechselt = bool(update.get("contract_id")
                               and update["contract_id"] != existing.get("contract_id"))
    contract_id = update.get("contract_id") or (
        None if contract_loesen else existing.get("contract_id"))
    await _vertragszeiger_abgleichen(user["dealer_id"], appt_id, contract_id)
    # Lebenszyklus des Fahrzeugs nachziehen (abgeholt / nicht abgeholt).
    # Nachpruefung Runde 14 (Nr. 19): das Fahrzeug, das NACH dem Update am
    # Termin haengt; ein neu verknuepftes bekommt wie beim Anlegen zuerst
    # "abholung_geplant" (vorher bekam das abgehaengte Auto den Status).
    if update.get("vehicle_id") and update["vehicle_id"] != existing.get("vehicle_id"):
        await try_set_lifecycle(update["vehicle_id"], user["dealer_id"],
                                "abholung_geplant", user=user)
    status_gewechselt = "status" in update and update["status"] != existing.get("status")
    # Befund Ahmad 10.09.2026: der vor Ort vereinbarte Preis (final_price)
    # ist der Einkaufspreis dieses Kaufvorgangs — er ueberschreibt den
    # Vertragspreis des Suchers und wird beim Abholen ans Fahrzeug
    # uebernommen (kaufvorgang.fahrzeug_status_aggregieren).
    if update.get("final_price") is not None \
            and update.get("final_price") != existing.get("final_price"):
        import kaufvorgang as _kv
        _t = await db.appointments.find_one(
            {"id": appt_id}, {"_id": 0, "id": 1, "contract_id": 1, "kaufvorgang_id": 1})
        _vorgang = await _kv.fuer_termin(_t or {})
        if _vorgang and _vorgang.get("status") in _kv.STATUS:
            await _kv.status_setzen(_vorgang["id"], _vorgang["status"], user=user,
                                    extra={"purchase_price": float(update["final_price"]),
                                           "preis_quelle": "vor_ort"})
    if status_gewechselt or contract_gewechselt:
        # Umbau Kaufvorgaenge: der Status wirkt auf den VORGANG dieses Termins;
        # das Fahrzeug bekommt nur die Zusammenfassung (nicht_abgeholt erst,
        # wenn kein anderer Vorgang mehr offen ist). Ohne Vorgang (manueller
        # Termin ohne Vertrag) wie frueher direkt am Fahrzeug.
        # Runde 18: den TATSAECHLICH gespeicherten Termin lesen — nach
        # _vertragszeiger_abgleichen traegt er den Vorgang des NEUEN Vertrags.
        # Vorher gewann die alte kaufvorgang_id aus `existing`, und ein
        # Vertragswechsel mit Statusaenderung markierte den alten Kauf als
        # abgeholt. Ein Wechsel OHNE Statusaenderung zieht den neuen Vorgang
        # jetzt ebenfalls nach.
        import kaufvorgang as _kv
        termin_nachher = await db.appointments.find_one(
            {"id": appt_id}, {"_id": 0, "id": 1, "contract_id": 1, "kaufvorgang_id": 1}) \
            or {"id": appt_id, "contract_id": contract_id}
        wirksamer_status = update.get("status", existing.get("status")) or "offen"
        if not await _kv.termin_status_uebernehmen(termin_nachher, wirksamer_status, user=user) \
                and vehicle_id and status_gewechselt:
            if update["status"] == "abgeholt":
                await try_set_lifecycle(vehicle_id, user["dealer_id"], "abgeholt", user=user)
            elif update["status"] == "nicht abgeholt":
                await try_set_lifecycle(vehicle_id, user["dealer_id"], "nicht_abgeholt", user=user)
    if status_neu in ABGESCHLOSSEN and status_neu != "abgeholt":
        # Runde 17 (Nr. 11): Termin storniert/nicht abgeholt/erledigt — ein
        # angefangener Korrektur-Entwurf des Protokolls wird verworfen und
        # die korrigierte Version ist wieder die massgebliche (sonst blieb
        # der Termin ohne aktuelles Protokoll). Best effort, wirft nie.
        from routes.protocols import korrektur_verwerfen
        await korrektur_verwerfen(appt_id)
    # Verschobener Abholtermin -> Kaufvertrag mit dem NEUEN Datum neu
    # erzeugen. Das PDF ist eine gespeicherte Datei und wuerde sonst
    # dauerhaft den alten Termin zeigen (Wunsch 08/2026).
    # Nachpruefung Runde 14 (Nr. 20): gegen den Vertrag, der NACH dem Update
    # am Termin haengt; beim Vertragswechsel bekommt der neue Vertrag den
    # wirksamen Termin (vorher wurde der abgehaengte Vertrag neu erzeugt).
    # Runde 17 (Nr. 4): scheiterte die Neuerzeugung beim letzten Mal
    # (vertrag_veraltet), wird sie jetzt auch OHNE Datumsaenderung mit dem
    # wirksamen Termin nachgeholt.
    vertrag_aktualisiert = False
    vertrag_veraltet = False
    veraltet_nachholen = bool(existing.get("vertrag_veraltet")) and bool(contract_id)
    wirksames_datum = update.get("pickup_date", existing.get("pickup_date"))
    wirksame_zeit = update.get("pickup_time", existing.get("pickup_time"))
    if (pickup_changed or zeit_geaendert or contract_gewechselt
            or veraltet_nachholen) and contract_id:
        nachziehen = contract_gewechselt or veraltet_nachholen
        from routes.contracts import regenerate_contract_for_pickup
        vertrag_aktualisiert = await regenerate_contract_for_pickup(
            contract_id=contract_id,
            dealer_id=user["dealer_id"],
            user=user,
            pickup_date=update.get("pickup_date", existing.get("pickup_date")
                                   if nachziehen else None),
            pickup_time=update.get("pickup_time", existing.get("pickup_time")
                                   if nachziehen else None),
            # Nachpruefung Runde 14 (Befund 84): ein bewusst geleertes Datum
            # oder eine geleerte Uhrzeit verschwindet auch aus dem Vertrag.
            leeren_erlaubt=True,
        )
        if vertrag_aktualisiert:
            if existing.get("vertrag_veraltet"):
                await db.appointments.update_one(
                    {"id": appt_id}, {"$unset": {"vertrag_veraltet": ""}})
        elif not await _vertrag_zeigt_termin(user["dealer_id"], contract_id,
                                             wirksames_datum, wirksame_zeit):
            # False trotz Aenderung UND der Vertrag zeigt einen anderen
            # Termin: als veraltet merken, beim naechsten PUT nachholen.
            vertrag_veraltet = True
            await db.appointments.update_one(
                {"id": appt_id}, {"$set": {"vertrag_veraltet": True}})
        elif existing.get("vertrag_veraltet"):
            # Vertrag zeigt inzwischen den wirksamen Termin (z.B. vom Chef
            # direkt neu erzeugt) — Merker aufheben.
            await db.appointments.update_one(
                {"id": appt_id}, {"$unset": {"vertrag_veraltet": ""}})

    meta = {"pickup_changed": pickup_changed,
            "vertrag_aktualisiert": vertrag_aktualisiert}
    if vertrag_veraltet:
        meta["vertrag_veraltet"] = True
    if contract_loesen and existing.get("contract_id"):
        meta["contract_geloest"] = existing["contract_id"]
    if fahrzeug_loesen and existing.get("vehicle_id"):
        meta["fahrzeug_geloest"] = existing["vehicle_id"]
    if status_gewechselt:
        # Nachpruefung Runde 14 (Nr. 98): von/nach im Log, damit ein
        # Wieder-Oeffnen nachvollziehbar bleibt.
        meta["status_von"] = existing.get("status")
        meta["status_nach"] = update["status"]
    await log_activity(user["dealer_id"], user["id"], "termin.aktualisiert", ref=appt_id,
                       meta=meta)
    out = {"ok": True, "pickup_date_changed": pickup_changed,
           "contract_updated": vertrag_aktualisiert}
    if fahrer_entfernt:
        out["hinweis"] = FAHRER_ENTFERNT_HINWEIS
    elif vertrag_veraltet:
        out["hinweis"] = VERTRAG_VERALTET_HINWEIS
    return out


@router.get("/appointments/{appt_id}/report")
async def get_pickup_report(appt_id: str, versions: int = 0,
                            user=Depends(current_firma)):
    """Händler liest den Abholbericht des Fahrers (aktuelle Version).
    Mit ?versions=1 werden auch alte (ersetzte) Versionen mitgeliefert."""
    appt = await db.appointments.find_one(
        {"id": appt_id, "dealer_id": user["dealer_id"]},
        {"_id": 0, "id": 1, "created_by": 1, "contract_id": 1, "vehicle_id": 1,
         "kaufvorgang_id": 1},
    )
    # Runde 16: Abholberichte nur im eigenen Bereich (wie der Termin selbst).
    if not appt or not await _sucher_darf(user, appt):
        raise HTTPException(404, "Termin nicht gefunden")
    # Nachpruefung Runde 14 (Befund 38): gibt es nach einem Abbruch kurz zwei
    # nicht-abgeloeste Berichte, gilt der juengste — nicht ein beliebiger.
    aktuelle = await db.pickup_reports.find(
        {"appointment_id": appt_id, "superseded": {"$ne": True}}, {"_id": 0},
    ).sort("version", -1).to_list(1)
    current = aktuelle[0] if aktuelle else None
    out: Dict[str, Any] = {"report": current}
    # Runde 21: Frist der Fahrerfotos (Tage ab dem Hochladen) fuer die Anzeige.
    from cleanup_service import FAHRERFOTO_TAGE
    out["fahrerfoto_tage"] = FAHRERFOTO_TAGE
    if versions:
        out["versions"] = await db.pickup_reports.find(
            {"appointment_id": appt_id}, {"_id": 0},
        ).sort("version", -1).to_list(20)
    return out


@router.delete("/appointments/{appt_id}")
async def delete_appointment(appt_id: str, user=Depends(current_firma)):
    # Berechtigungsmatrix (PR-Review 09/2026): firmenweite Termine loescht
    # der Chef; ein Sucher nur seine eigenen, noch offenen Termine.
    # Runde 15 (Nr. 7): fuer beide Rollen laden — der Audit-Eintrag braucht
    # den Stand VOR dem Loeschen (Status, Fahrzeug, Vertrag, Fahrer, Datum).
    appt = await db.appointments.find_one(
        {"id": appt_id, "dealer_id": user["dealer_id"]},
        {"_id": 0, "created_by": 1, "status": 1, "contract_id": 1, "kaufvorgang_id": 1,
         "vehicle_id": 1, "driver_id": 1, "pickup_date": 1, "pickup_time": 1})
    if not appt:
        raise HTTPException(404, "Termin nicht gefunden")
    if user.get("role") == "sucher":
        if not await _sucher_darf(user, appt):
            raise HTTPException(403, "Sucher dürfen nur ihre eigenen Termine "
                                     "löschen — andere löscht der Händler-"
                                     "Hauptaccount")
        if (appt.get("status") or "offen") not in ("offen", "verschoben"):
            raise HTTPException(409, "Abgeschlossene oder stornierte Termine "
                                     "löscht nur der Händler-Hauptaccount")
    # Abnahme 12.09.2026: Der Audit-Eintrag stand NACH dem Hard-Delete und
    # konnte selbst werfen — dann war der Termin weg und die Spur fehlte.
    # Jetzt vorher, und ein Fehler dabei stoppt das Loeschen nicht.
    try:
        await log_activity(user["dealer_id"], user["id"], "termin.geloescht",
                           ref=appt_id,
                           meta={"status": appt.get("status") or "offen",
                                 "vehicle_id": appt.get("vehicle_id"),
                                 "contract_id": appt.get("contract_id"),
                                 "driver_id": appt.get("driver_id"),
                                 "pickup_date": appt.get("pickup_date"),
                                 "pickup_time": appt.get("pickup_time"),
                                 "created_by": appt.get("created_by")})
    except Exception:  # noqa: BLE001
        log.exception("Audit-Eintrag zur Terminloeschung %s fehlgeschlagen", appt_id)
    # Runde 29 (12.09.2026, Pruefbefund): ZUERST die Verweise loesen, DANN
    # den Termin loeschen. Vorher war es umgekehrt — brach der Vorgang
    # dazwischen ab (Neustart, Netz weg), war der Termin weg, und Kaufvorgang
    # und Vertrag zeigten fuer immer auf einen Termin, den es nicht mehr gibt.
    # In dieser Reihenfolge ist der schlimmste Fall harmlos: der Termin steht
    # noch da und laesst sich einfach erneut loeschen.
    # Umbau Kaufvorgaenge: der Vorgang verliert den Termin (zurueck auf
    # "Vertrag erstellt"), Vertragsverweis wird geloest.
    import kaufvorgang as _kv
    await _kv.termin_loesen(appt_id)
    await db.generated_pdfs.update_many(
        {"dealer_id": user["dealer_id"], "appointment_id": appt_id},
        {"$set": {"appointment_id": None}})
    res = await db.appointments.delete_one({"id": appt_id, "dealer_id": user["dealer_id"]})
    if not res.deleted_count:
        # Jemand anderes war schneller — dessen Lauf hat dieselben Verweise
        # geloest, es bleibt nichts Halbes zurueck.
        raise HTTPException(404, "Termin nicht gefunden")
    # Die Audit-Spur (Runde 15, Nr. 7) steht oben — VOR dem Loeschen
    # (Abnahme 12.09.2026), damit sie auch bei einem Abbruch existiert.
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
    # Nachpruefung Runde 14 (Nr. 77): derselbe Bereich wie beim Aendern —
    # das PDF rendert Verkaeuferdaten und Preis aus dem Vertrag, den ein
    # Sucher ueber /contracts nicht sehen darf (Vertragstrennung Runde 12).
    if not await _sucher_darf(user, appt):
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

    # Runde 24 (11.09.2026, Befund Ahmad "AUFTRAGGEBER —"): Auftraggeber ist
    # der Kaeufer aus dem Vertrag bzw. der Ersteller mit seinen Sucher-
    # Einstellungen — nicht mehr das nackte Firmen-Dokument.
    from auftraggeber import auftraggeber_fuer_termin
    dealer = await auftraggeber_fuer_termin(appt)

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
