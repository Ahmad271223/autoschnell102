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
                  fahrzeug_bereich, log_activity, log_activity_sicher, now_iso,
                  current_firma, termin_bereich,
                  termin_im_bereich, uhrzeit_hhmm_pruefen)
from lifecycle import try_set_lifecycle, zustand_fuer_terminstatus

log = logging.getLogger("autohandel")

router = APIRouter()

# Endzustaende eines Termins (dieselbe Menge wie ABGESCHLOSSEN in
# Termine.jsx). Nachpruefung Runde 14: Grundlage fuer die Chef-Sperre bei
# nachtraeglichen Aenderungen (Nr. 99/98) und die Aufraeumfrist (Nr. 114).
ABGESCHLOSSEN = frozenset({"abgeholt", "nicht abgeholt", "storniert", "erledigt"})
# Pruefbericht 20.09.2026 (V-12), Entscheidung Ahmad 21.09.2026: "Darf der Chef
# einen Termin mit unterschriebenem Abholprotokoll noch auf 'storniert' oder
# 'nicht abgeholt' setzen? — ja". Mit Pflicht-Bestaetigung (ausgang_bestaetigt),
# Merker ausgang_geaendert am Termin, eigenem Verlaufseintrag und Aufraeumen
# von Fahrzeug und Vertragshinweis. "erledigt" zaehlt beim Kauf wie "abgeholt".
AUSGANG_ABGEHOLT = frozenset({"abgeholt", "erledigt"})
AUSGANG_ZURUECK = frozenset({"storniert", "nicht abgeholt"})
# Darf NICHT "neu laden" enthalten — Termine.jsx laedt bei 409 + "neu laden" den
# Termin frisch und verwirft die Eingabe.
AUSGANG_BESTAETIGEN_HINWEIS = ("Diese Abholung ist bereits abgeschlossen. Wird sie nachträglich "
                               "auf „storniert“ oder „nicht abgeholt“ gesetzt, gilt der Kauf "
                               "nicht mehr als abgeholt — bitte die Rückfrage im Terminplaner "
                               "bestätigen.")
AUSGANG_FAHRZEUG_WEITER_HINWEIS = ("Das Fahrzeug steht bereits im Bestand bzw. im Weiterverkauf "
                                   "und wurde nicht zurückgesetzt — bitte dort entscheiden.")
# Fahrzeugzustaende NACH der Entscheidung "abgeholt" — hier setzt der Rueckweg
# nichts zurueck (nur Hinweis).
_NACH_ABHOLUNG_WEITER = frozenset({"bestand", "verkaufsentwurf", "verkaufsbereit",
                                   "veroeffentlicht", "reserviert", "verkauft", "archiviert"})


class _StandVeraltet(Exception):
    """Befund 54 (16.09.2026): der Termin-Write in der Transaktion traf den
    gelesenen Stand nicht — bricht die Transaktion ab (Freigabe-Ruecknahme
    wird zurueckgerollt), die Route antwortet danach 409."""

# Runde 17 (Nr. 4): Hinweis, wenn der Kaufvertrag nach einer Terminaenderung
# NICHT neu erzeugt werden konnte (z.B. Vertrag ausserhalb des Bereichs des
# Sucher-Kontos, PDF-Fehler). Termine.jsx zeigt data.hinweis als Warnung.
VERTRAG_VERALTET_HINWEIS = ("Termin gespeichert — der Kaufvertrag zeigt noch den "
                            "alten Abholtermin, bitte erneut speichern")
# Runde 17 (Nr. 2): Steuerflags im Eingabemodell, die NICHT in der Datenbank
# landen (siehe AppointmentIn.contract_loesen / fahrzeug_loesen).
_STEUERFELDER = frozenset({"contract_loesen", "fahrzeug_loesen", "stand", "ausgang_bestaetigt"})


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
    # 14.09.2026 (Entscheidung Ahmad): kein Endpreis-Feld mehr im Termineditor —
    # der Preis kommt ueber Protokoll und Freigabe in den Vertrag. Das Feld
    # bleibt fuer aeltere Clients lesbar, 0 ist kein Preis.
    final_price: Optional[float] = Field(default=None, gt=0, allow_inf_nan=False)
    extra_costs: Optional[float] = Field(default=None, ge=0, allow_inf_nan=False)
    # Runde 17 (Nr. 2): Steuerfelder, nur beim PUT ausgewertet (s. Docstring).
    contract_loesen: bool = False
    fahrzeug_loesen: bool = False
    # Phase 2 (2.8, D3): updated_at des Termins, wie ihn die Oberflaeche geladen
    # hat — wer auf einem veralteten Stand speichert, bekommt "bitte neu laden"
    # statt die Aenderung des Kollegen (oder des Fahrers) zu ueberschreiben.
    stand: Optional[str] = None
    # Pruefbericht 20.09.2026 (V-12), Entscheidung Ahmad 21.09.2026: der Chef
    # bestaetigt ausdruecklich, dass eine abgeholte/erledigte Abholung
    # nachtraeglich storniert bzw. "nicht abgeholt" wird. Steuerfeld, nie gespeichert.
    ausgang_bestaetigt: Optional[bool] = None

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
    # Pruefung 14.09.2026 (Liste 4, Nr. 4/5): auch Fahrzeug, Vertrag und
    # Verkaeufer — ein anderes Auto oder ein anderer Verkaeufer ist eine
    # andere Fahrt, die der Fahrer neu bestaetigen muss.
    geaendert = any(
        f in neu and (neu.get(f) or "") != (existing.get(f) or "")
        for f in ("pickup_date", "pickup_time", "pickup_address", "vehicle_id",
                  "contract_id", "seller_name", "seller_phone", "seller_email"))
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
        # Befund 104 (16.09.2026): "Termin erstellt" nur, solange der Vertrag
        # noch "erstellt"/"neu erstellt" ist — ein versendeter Vertrag behaelt
        # seinen Status, bekommt aber den Terminverweis.
        await db.generated_pdfs.update_one(
            {"id": contract_id, "dealer_id": dealer_id,
             "appointment_id": {"$ne": appt_id},
             "status": {"$in": ["erstellt", "neu erstellt", None]}},
            {"$set": {"appointment_id": appt_id, "status": "Termin erstellt"}})
        await db.generated_pdfs.update_one(
            {"id": contract_id, "dealer_id": dealer_id,
             "appointment_id": {"$ne": appt_id}},
            {"$set": {"appointment_id": appt_id}})
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
    # Runde 12 (15.09.2026, Nr. 7): ein vom Betreiber deaktiviertes oder in
    # Loeschung befindliches Fahrer-Konto bekommt keine neuen Fahrten.
    konto = await db.driver_accounts.find_one({"id": driver_id}, {"_id": 0, "active": 1, "loeschung": 1})
    if konto is not None and (konto.get("active") is False
                              or (konto.get("loeschung") or {}).get("status") == "laeuft"):
        raise HTTPException(400, "Dieser Fahrer ist deaktiviert oder wird gelöscht — "
                                 "er kann keine Fahrten mehr annehmen.")


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
TERMIN_VERALTET_HINWEIS = ("Der Termin wurde inzwischen von jemand anderem geändert — "
                           "bitte neu laden und erneut speichern.")


async def _ist_replica_set() -> bool:
    from deps import ist_replica_set
    return await ist_replica_set()


async def _transaktion(fn):
    """Phase 4 (4.1) / Runde 12: gemeinsamer Helfer in deps.transaktion."""
    from deps import transaktion
    return await transaktion(fn)


async def _doppelbuchung_hinweis(appt_id: str, driver_id, datum, zeit) -> Optional[str]:
    """Phase 2 (2.10, D1/D2; Entscheidung Ahmad: nur warnen, der Fahrer nimmt
    Fahrten selbst an oder lehnt ab): gleicher Fahrer, gleiches Datum, gleiche
    Uhrzeit bei einer anderen offenen Fahrt — auch bei einer anderen Firma."""
    if not (driver_id and datum and zeit):
        return None
    try:
        n = await db.appointments.count_documents(
            {"id": {"$ne": appt_id}, "driver_id": driver_id, "pickup_date": datum,
             "pickup_time": zeit, "status": {"$nin": sorted(ABGESCHLOSSEN)}}, limit=1)
    except Exception:  # noqa: BLE001
        log.exception("Doppelbuchungs-Pruefung fuer Termin %s fehlgeschlagen", appt_id)
        return None
    if not n:
        return None
    return (f"Achtung: Der Fahrer hat am {datum} um {zeit} bereits eine andere Fahrt — "
            "er kann diese Fahrt annehmen oder ablehnen.")
# Audit 13.09.2026 (#5): Nacharbeit nach dem Anlegen gescheitert (Merker
# nacharbeit_offen am Termin, update_appointment holt sie nach).
# Nachbesserung 13.09.2026: konnte auch der Merker nicht geschrieben werden,
# holt das naechste Speichern nichts nach — dann nichts versprechen.
NACHARBEIT_FEHLGESCHLAGEN_HINWEIS = ("Termin gespeichert — Vertrag und "
                                     "Fahrzeugstatus konnten nicht aktualisiert "
                                     "werden, bitte beides prüfen.")
NACHARBEIT_HINWEIS = ("Termin gespeichert — Vertrag und Fahrzeugstatus werden "
                      "beim nächsten Speichern des Termins nachgezogen.")
# Go-Live 13.09.2026 (P3): Protokoll-Stati, in denen Vertrag/Fahrzeug des
# Termins feststehen (Werte wie routes.protocols; dort kein Import wegen Zyklus).
PROTOKOLL_LAEUFT = ("zur_freigabe", "freigegeben", "wird_abgeschlossen")
# Pruefung 14.09.2026 (C15)
TERMIN_MIT_PROTOKOLL_HINWEIS = ("Zu diesem Termin gibt es ein unterschriebenes oder gerade "
                                "laufendes Abholprotokoll — er kann nicht gelöscht werden. "
                                "Bitte den Termin stattdessen stornieren.")
PROTOKOLL_LAEUFT_HINWEIS = ("Das Abholprotokoll liegt zur Freigabe oder wird gerade "
                            "unterschrieben — Fahrer, Vertrag, Fahrzeug, Verkäufer, "
                            "Anschrift und Termin lassen sich jetzt nicht ändern. Bitte "
                            "das Protokoll erst an den Fahrer zurückschicken.")


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
             "contract_data": 1, "kaufvorgang_id": 1, "dealer_id": 1, "vehicle_id": 1,
             "user_id": 1, "purchase_price": 1, "appointment_id": 1})
        if vertrag_doc is None:
            raise HTTPException(404, "Vertrag nicht gefunden")
    await _fahrzeug_passt_zum_vertrag(user["dealer_id"], body.vehicle_id, body.contract_id)
    # Runde 18: "abgeholt" entsteht auch beim ANLEGEN nur mit unterschriebenem
    # Abholprotokoll, sobald ein Fahrer eingeteilt ist — dieselbe Regel wie
    # beim Aendern. Ein neuer Termin kann noch kein Protokoll haben, der Weg
    # ist damit geschlossen (ohne Fahrer bleibt der Buero-Abschluss erlaubt).
    # Phase 2 (2.5, A7/D7): "erledigt" wirkt wie "abgeholt" auf den Kaufvorgang
    # — mit Fahrer also nur ueber dessen unterschriebenes Protokoll.
    if (body.status or "") in ("abgeholt", "erledigt") and body.driver_id:
        raise HTTPException(409, "Für diesen Termin ist ein Fahrer eingeteilt. "
                                 "'Abgeholt' bzw. 'Erledigt' entsteht automatisch, "
                                 "sobald der Fahrer das Abholprotokoll unterschrieben "
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
    # Runde 13 (Liste 4 Nr. 4-6): ein Vertrag hat EINEN Termin. Der Unique-
    # Index deckt nur offene Termine — ein weiterer, gleich geschlossen
    # angelegter Termin (storniert, erledigt, ...) wuerde den Vertrags- und
    # Vorgangszeiger auf sich ziehen und den echten Abholprozess verwaisen.
    if body.contract_id and doc["status"] in ABGESCHLOSSEN \
            and await db.appointments.count_documents(
                {"dealer_id": user["dealer_id"], "contract_id": body.contract_id}, limit=1):
        raise HTTPException(409, "Zu diesem Vertrag gibt es bereits einen Termin — ein weiterer "
                                 "abgeschlossener Termin ist nicht möglich.")
    if vertrag_doc:
        # Pruefbericht 20.09.2026 (R2-01): den Vorgang ueber den Vertrag
        # ermitteln (prueft und heilt den Zeiger), statt den Zeiger blind zu
        # kopieren — ein falscher Zeiger setzte sonst appointment_id am
        # FREMDEN Vorgang. Ohne Treffer bleibt das Feld leer; die Nacharbeit
        # findet den Vorgang spaeter ueber contract_id.
        import kaufvorgang as _kv_termin
        try:
            _vorgang = await _kv_termin.fuer_vertrag(vertrag_doc)
        except Exception:  # noqa: BLE001
            log.exception("Kaufvorgang zu Vertrag %s nicht ermittelbar", vertrag_doc.get("id"))
            _vorgang = None
        if _vorgang:
            doc["kaufvorgang_id"] = _vorgang["id"]
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
    # Runde 19 (Nr. 37): das Fahrzeug fuer ein paar Minuten vor dem Pool-
    # Trimmen schuetzen — der Schutzscan des Trimmens laeuft sonst genau
    # zwischen "noch frei" und dem neuen Termin.
    from fahrzeugpool import kurz_schuetzen
    await kurz_schuetzen(db, user["dealer_id"], doc.get("vehicle_id"))
    try:
        await db.appointments.insert_one(doc)
    except DuplicateKeyError:
        raise HTTPException(409, TERMIN_DOPPELT_HINWEIS)
    # Befund 125/126 (16.09.2026): Nachkontrolle NACH dem Insert — wurde der
    # Vertrag genau dazwischen geloescht (Grabstein) oder das Fahrzeug
    # geloescht, wird der eben angelegte Termin wieder entfernt (409) statt
    # dass er auf einen Vertrag/ein Fahrzeug zeigt, das es nicht mehr gibt.
    nachkontrolle = None
    if body.contract_id and not await db.generated_pdfs.count_documents(
            {"id": body.contract_id, "dealer_id": user["dealer_id"],
             "loeschung.status": {"$ne": "laeuft"}}, limit=1):
        nachkontrolle = ("Der Kaufvertrag wurde inzwischen gelöscht — bitte den Termin "
                         "ohne Vertrag oder mit einem anderen Vertrag anlegen.")
    elif body.vehicle_id and not await db.vehicles.count_documents(
            {"id": body.vehicle_id, "dealer_id": user["dealer_id"],
             "lifecycle": {"$ne": "geloescht"}}, limit=1):
        nachkontrolle = "Das Fahrzeug wurde inzwischen gelöscht — bitte den Termin erneut anlegen."
    if nachkontrolle:
        await db.appointments.delete_one({"id": appt_id, "dealer_id": user["dealer_id"]})
        raise HTTPException(409, nachkontrolle)
    hinweis = None
    if doc.get("driver_id") and not await _fahrer_nachpruefen(
            appt_id, user["dealer_id"], doc["driver_id"]):
        doc.pop("driver_id", None)
        doc["zuteilung"] = None
        hinweis = FAHRER_ENTFERNT_HINWEIS
    # Audit 13.09.2026 (#5): der Termin ist ab hier dauerhaft gespeichert und
    # fuer Fahrer/Kollegen sichtbar. Scheitert die Nacharbeit (DB-Aussetzer,
    # Primary-Wechsel), gab es vorher 500 — der Client wiederholte und lief
    # in 409, Kaufvorgang und Fahrzeugstatus blieben dauerhaft alt (ein
    # normales Speichern zog sie nicht nach). Jetzt: Merker nacharbeit_offen,
    # update_appointment holt alles beim naechsten Speichern nach.
    nacharbeit_offen = False
    merker_gesetzt = False
    try:
        if body.contract_id and doc.get("status") not in ABGESCHLOSSEN:
            await db.generated_pdfs.update_one(
                {"id": body.contract_id, "dealer_id": user["dealer_id"]},
                {"$set": {"appointment_id": appt_id, "status": "Termin erstellt"}},
            )
        # Fahrzeugstatus: ueber den Kaufvorgang (Zusammenfassung aller Vorgaenge);
        # manueller Termin ohne Vertrag wie frueher direkt am Fahrzeug.
        import kaufvorgang as _kv
        # Befund 69 (16.09.2026): ein gleich geschlossen angelegter Termin
        # (erledigt, storniert ...) setzt das Fahrzeug NICHT auf "Abholung geplant".
        if not await _kv.termin_status_uebernehmen(doc, doc.get("status") or "offen", user=user) \
                and body.vehicle_id and (doc.get("status") or "offen") in TERMIN_OFFEN_WERTE:
            await try_set_lifecycle(body.vehicle_id, user["dealer_id"],
                                    "abholung_geplant", user=user)
        if doc.get("kaufvorgang_id"):
            await db.kaufvorgaenge.update_one({"id": doc["kaufvorgang_id"]},
                                              {"$set": {"appointment_id": appt_id}})
    except Exception:  # noqa: BLE001  (Termin existiert bereits — kein 500)
        log.exception("Nacharbeit nach Termin %s fehlgeschlagen", appt_id)
        nacharbeit_offen = True
        try:
            await db.appointments.update_one({"id": appt_id},
                                             {"$set": {"nacharbeit_offen": True}})
            merker_gesetzt = True
        except Exception:  # noqa: BLE001
            log.exception("Merker nacharbeit_offen fuer Termin %s nicht gesetzt", appt_id)
    # Audit 13.09.2026 (#5): Audit nach dem dauerhaften Insert darf nicht mehr
    # mit 500 abbrechen (Muster create_contract).
    await log_activity_sicher(user["dealer_id"], user["id"], "termin.erstellt", ref=appt_id)
    # Befund 135 (16.09.2026): dieselbe Sucher-Maskierung wie GET.
    out = termin_fuer_sucher(user, clean_doc(doc))
    if nacharbeit_offen:
        if merker_gesetzt:
            out["nacharbeit_offen"] = True
        text = NACHARBEIT_HINWEIS if merker_gesetzt else NACHARBEIT_FEHLGESCHLAGEN_HINWEIS
        hinweis = f"{hinweis} {text}" if hinweis else text
    dbh = await _doppelbuchung_hinweis(appt_id, doc.get("driver_id"),
                                       doc.get("pickup_date"), doc.get("pickup_time"))
    if dbh:
        out["doppelbuchung"] = True
        hinweis = f"{hinweis} {dbh}" if hinweis else dbh
    if hinweis:
        out["hinweis"] = hinweis
    return out


# Runde 19 (16.09.2026, Termine Nr. 5/12): Termin-Antworten an Sucher tragen
# keine Konto-Kennungen und internen Verweise mehr (wer angelegt/uebergeben
# hat, Kaufvorgang) — dieselbe Regel wie konten_maskieren bei Fahrzeugen.
_TERMIN_INTERN = ("created_by", "uebergeben_von", "uebergeben_am",
                  "kaufvorgang_id", "driver_id_hist")


def termin_fuer_sucher(user: dict, a: dict) -> dict:
    if user.get("role") == "sucher" and isinstance(a, dict):
        for feld in _TERMIN_INTERN:
            a.pop(feld, None)
        # Pruefbericht 20.09.2026 (V-12): Merker der Chef-Uebersteuerung ohne
        # Konto-Kennung (dieselbe Regel wie oben).
        if isinstance(a.get("ausgang_geaendert"), dict):
            a["ausgang_geaendert"] = {k: w for k, w in a["ausgang_geaendert"].items()
                                      if k != "von"}
    return a


@router.get("/appointments")
async def list_appointments(response: Response, user=Depends(current_firma),
                            status: Optional[str] = None):
    # Runde 16: Sucher sehen nur Termine im eigenen Bereich (selbst angelegt,
    # eigenes Fahrzeug, eigener Vertrag); der Chef die ganze Firma.
    query: dict = await termin_bereich(user)
    # Nachpruefung Runde 14 (Nr. 74): Termine werden nie automatisch
    # geloescht, der Bestand waechst dauerhaft. Bei aufsteigender Sortierung
    # fielen ab 500 Terminen genau die KOMMENDEN weg. Limit 2000; die
    # Antwort bleibt eine Liste (Frontend), ein Kopf meldet den Abschnitt.
    # Audit 13.09.2026 (#6): die Sortierung blieb trotzdem aufsteigend — ab
    # 2000 fielen weiter die neuen/kommenden Termine weg. Jetzt ohne Filter:
    # alle nicht abgeschlossenen ($nin — auch fehlender/unbekannter Status),
    # der Rest der Grenze mit den JUENGSTEN abgeschlossenen; mit Filter die
    # juengsten. Die Antwort bleibt aufsteigend (Termine.jsx "Kommend").
    grenze = 2000
    if status:
        # Runde 19 (Nr. 14): einen mehr lesen — bei genau 2000 Terminen hiess es
        # sonst faelschlich "gekuerzt".
        items = await db.appointments.find(
            {**query, "status": status}, {"_id": 0}).sort("pickup_date", -1).to_list(grenze + 1)
        abgeschnitten = len(items) > grenze
        items = items[:grenze]
    else:
        # Nachbesserung: auch die offenen selbst koennen die Grenze sprengen
        # (nie abgeschlossene Alttermine). Aufsteigend gekappt fielen dann
        # wieder die kommenden weg. Jetzt: datumslose (Termine.jsx zeigt sie
        # unter "Kommend") zuerst, dann die JUENGSTEN mit Datum; es fallen die
        # aeltesten offenen weg. grenze + 1 erkennt den Abschnitt genau.
        offen_q = {**query, "status": {"$nin": sorted(ABGESCHLOSSEN)}}
        # Runde 19 (Nr. 15): datumslose Termine deterministisch (juengste zuerst)
        # statt in natuerlicher Reihenfolge der Datenbank.
        items = await db.appointments.find(
            {**offen_q, "pickup_date": {"$in": ["", None]}}, {"_id": 0},
        ).sort("created_at", -1).to_list(grenze + 1)
        items += await db.appointments.find(
            {**offen_q, "pickup_date": {"$nin": ["", None]}}, {"_id": 0},
        ).sort("pickup_date", -1).to_list(max(1, grenze + 1 - len(items)))
        offen_gekappt = len(items) > grenze
        items = items[:grenze]
        rest = grenze - len(items)
        # rest + 1 (nie to_list(0) — das liefert in Motor ALLE) erkennt den Abschnitt.
        alt = await db.appointments.find(
            {**query, "status": {"$in": sorted(ABGESCHLOSSEN)}}, {"_id": 0},
        ).sort("pickup_date", -1).to_list(rest + 1)
        abgeschnitten = offen_gekappt or len(alt) > rest
        items = items + alt[:rest]
    items.sort(key=lambda a: str(a.get("pickup_date") or ""))
    if abgeschnitten:
        log.warning("Terminliste Firma %s: auf %d gekappt (Filter %r)",
                    user.get("dealer_id"), grenze, status)
        response.headers["X-Truncated"] = "1"
    # Enrich with vehicle + driver (driver = globaler Fahrer-Account)
    drivers_map = {}
    # Nachpruefung 15.09.2026 (Fahrer Nr. 15): Sucher sehen Name und Status des
    # Fahrers, aber keine Fahrer-ID und keine E-Mail (nur der Chef).
    # Pruefbericht 20.09.2026 (P0): nur der EINE Hauptchef sieht Fahrer-ID
    # und E-Mail — ein zweites dealer-Konto der Firma bekam sie bisher auch.
    from deps import ist_haupt_chef
    chef_sicht = await ist_haupt_chef(user)
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
                "driver_code": d.get("driver_code") if chef_sicht else None,
                "email": d.get("email") if chef_sicht else None,
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
            _vorschaubilder(v)
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
        termin_fuer_sucher(user, a)
    return items


def _vorschaubilder(v: dict) -> None:
    """Pruefbericht 20.09.2026 (U-102): Vorschaubilder der Inseratsfotos ueber
    den eigenen Bild-Proxy (signierte Adressen, wie im Vergleich). Die
    Terminseite lud die Fotos vorher direkt beim Portal. Wirft nie."""
    try:
        import bild_proxy
        daten = v.get("data") if isinstance(v, dict) else None
        if not isinstance(daten, dict):
            return
        bilder = [u for u in (daten.get("image_urls") or daten.get("images")
                              or daten.get("photos") or daten.get("pictures") or [])
                  if isinstance(u, str) and u][:40]
        if bilder:
            daten["images_thumbs"] = bild_proxy.thumbs(bilder)
    except Exception:  # noqa: BLE001
        pass


@router.get("/appointments/{appt_id}")
async def get_appointment(appt_id: str, user=Depends(current_firma)):
    a = await db.appointments.find_one({"id": appt_id, **await termin_bereich(user)}, {"_id": 0})
    if not a:
        raise HTTPException(404, "Termin nicht gefunden")
    if a.get("vehicle_id"):
        # Runde 19 (16.09.2026, Termine Nr. 1/2): dieselbe Projektion wie die
        # Terminliste (kein rohes Fahrzeugdokument mit Bestandsnotizen, Kosten,
        # Inseratskopie) und dieselbe Konto-Maskierung wie ueberall sonst.
        v = await db.vehicles.find_one(
            {"id": a["vehicle_id"], "dealer_id": user["dealer_id"]},
            {"_id": 0, "id": 1, "data": 1, "status": 1, "lifecycle": 1,
             "source": 1, "mobile_ad_id": 1, "purchase_price": 1,
             "abgeholt_kaufvorgang_id": 1, "owner_user_id": 1, "mitbearbeiter_ids": 1},
        )
        if v:
            # Runde 23 (11.09.2026, Gegenpruefung): Sucher sehen nur ihren
            # eigenen Einkaufspreis — auch hier nicht den des Kollegen aus dem
            # gemeinsamen Fahrzeug-Dokument (Chef unveraendert).
            await __import__("kaufvorgang").einkauf_fuer_sucher_maskieren(user, v)
            from deps import konten_maskieren
            konten_maskieren(user, v)
            _vorschaubilder(v)
            a["vehicle"] = v
    if a.get("driver_id"):
        d = await db.driver_accounts.find_one(
            {"id": a["driver_id"]},
            {"_id": 0, "id": 1, "display_name": 1, "driver_code": 1, "email": 1},
        )
        if d:
            # Nachpruefung 15.09.2026 (Fahrer Nr. 15); 20.09.2026 (P0): nur
            # der eingetragene Hauptchef, nicht jedes dealer-Konto.
            from deps import ist_haupt_chef
            chef_sicht = await ist_haupt_chef(user)
            a["driver"] = {
                "id": d["id"], "name": d.get("display_name"),
                "driver_code": d.get("driver_code") if chef_sicht else None,
                "email": d.get("email") if chef_sicht else None,
            }
    return termin_fuer_sucher(user, a)


async def _sucher_darf(user: dict, appt: dict) -> bool:
    """Ein Sucher darf nur Termine anfassen, die er angelegt hat, deren
    Vertrag ihm gehoert oder deren Kaufvorgang ihm gehoert (Umbau
    Kaufvorgaenge 09.09.2026 — das gemeinsame Fahrzeug gibt keinen
    Zugriff; Regel zentral in deps.termin_im_bereich, dieselbe wie beim
    Lesen)."""
    return await termin_im_bereich(user, appt)


def ausgang_gilt_als_abgeholt(appt: dict) -> bool:
    """Pruefung 21.09.2026 (V-12): Gilt der Termin als abgeschlossene
    ABHOLUNG? "abgeholt" immer; "erledigt" nur, wenn am Termin ein Kauf bzw.
    ein Fahrzeug haengt (vehicle_id, contract_id oder kaufvorgang_id).
    Allgemeine Termine ohne beides (Werkstatt, Besichtigung) duerfen
    "erledigt" <-> "storniert" wie vor V-12 wechseln — ohne Rueckfrage und
    ohne Sperre fuer Sucher (Termine.jsx spiegelt die Regel: hatKauf)."""
    status = (appt or {}).get("status")
    if status == "abgeholt":
        return True
    return status == "erledigt" and bool(
        appt.get("vehicle_id") or appt.get("contract_id") or appt.get("kaufvorgang_id"))


async def abholung_beleg(appt: dict, dealer_id: str, *,
                         protokoll_genuegt: bool = False) -> Optional[Dict[str, Any]]:
    """Pruefung 21.09.2026 (V-12): Gilt die Abholung dieses Termins als
    erfolgt — unabhaengig vom Terminstatus? Vorher hing die Regel nur am
    ALTEN Terminstatus: ein zur Korrektur wieder geoeffneter Termin (abgeholt
    -> offen, der Vorgang bleibt wegen D15 "abgeholt") liess sich von Sucher,
    Fahrer und Chef ohne Rueckfrage stornieren — samt Ruecknahme von
    Fahrzeug und Einkaufspreis.

    Belegt:
      * mit Kaufvorgang (wie kaufvorgang.fuer_termin: kaufvorgang_id bzw.
        Vertrag): genau dann, wenn er "abgeholt" ist — das ist der Zustand,
        den ein Storno zuruecknimmt. Waehrend einer laufenden Korrektur ist
        die finale Version abgeloest, der Vorgang aber abgeholt. Ein
        finales Protokoll an einem Termin, dessen Kauf schon zurueckgenommen
        ist (Storno, danach wieder geoeffnet), belegt nichts mehr;
      * ohne Kaufvorgang: finales, nicht abgeloestes Protokoll und das
        Fahrzeug (falls vorhanden) steht noch auf "abgeholt" bzw. danach;
      * mit `protokoll_genuegt` (Fahrer-App) zaehlt jedes finale Protokoll.
    Liefert {"protokoll": {id, version} oder None} bzw. None (nicht belegt)."""
    appt_id = (appt or {}).get("id")
    if not appt_id:
        return None
    proto = await db.pickup_protocols.find_one(
        {"appointment_id": appt_id, "status": "final", "superseded": {"$ne": True}},
        {"_id": 0, "id": 1, "version": 1})
    if proto and protokoll_genuegt:
        return {"protokoll": proto}
    oder = []
    if appt.get("kaufvorgang_id"):
        oder.append({"id": appt["kaufvorgang_id"]})
    if appt.get("contract_id"):
        oder.append({"contract_id": appt["contract_id"]})
    kv = await db.kaufvorgaenge.find_one(
        {"dealer_id": dealer_id, "$or": oder}, {"_id": 0, "id": 1, "status": 1},
        sort=[("updated_at", -1)]) if oder else None
    if kv:
        if kv.get("status") != "abgeholt":
            return None
        if not proto:
            # Beleg fuer Merker/Verlauf: die zuletzt unterschriebene Version
            # (sie wird beim Schliessen per korrektur_verwerfen wieder aktuell).
            proto = await db.pickup_protocols.find_one(
                {"appointment_id": appt_id, "status": "final"},
                {"_id": 0, "id": 1, "version": 1}, sort=[("version", -1)])
        return {"protokoll": proto}
    if not proto:
        return None
    if appt.get("vehicle_id"):
        v = await db.vehicles.find_one({"id": appt["vehicle_id"], "dealer_id": dealer_id},
                                       {"_id": 0, "lifecycle": 1})
        if (v or {}).get("lifecycle") not in ({"abgeholt"} | _NACH_ABHOLUNG_WEITER):
            return None
    return {"protokoll": proto}


async def vertrag_hinweis_aussetzen(appt_id: Optional[str], dealer_id: str, contract_id: str, *,
                                    protokoll_id: Optional[str] = None) -> bool:
    """Pruefbericht 20.09.2026 (V-12): Termin gilt nicht mehr als abgeholt —
    "Neuen Vertrag senden" (nach_abholung_versand_offen) und der Alarm
    vertrag_nach_abholung_offen entfallen; die Fassung nach der Abholung
    bleibt als Nachweis. Nicht, wenn ein anderer Termin desselben Vertrags
    weiter abgeholt/erledigt ist (dann False).

    Pruefung 21.09.2026 (V-12): Im SELBEN Write wird festgehalten, dass der
    Merker hier entfernt wurde (nach_abholung_versand_ausgesetzt) — nur dann
    stellt der Rueckweg ihn wieder her. Mit `protokoll_id` nur, wenn der
    Vertrag auf dieser Abholung steht (Compare-and-Set fuer die Aufraeumjobs).
    Idempotent."""
    if await db.appointments.count_documents(
            {"dealer_id": dealer_id, "id": {"$ne": appt_id}, "contract_id": contract_id,
             "status": {"$in": sorted(AUSGANG_ABGEHOLT)}}, limit=1):
        return False
    filt: Dict[str, Any] = {"id": contract_id, "dealer_id": dealer_id,
                            "nach_abholung_versand_offen": True}
    if protokoll_id:
        filt["nach_abholung_protokoll_id"] = protokoll_id
    await db.generated_pdfs.update_one(
        filt, {"$unset": {"nach_abholung_versand_offen": ""},
               "$set": {"nach_abholung_versand_ausgesetzt": {"am": now_iso(),
                                                             "termin_id": appt_id}}})
    import betrieb as _betrieb
    await _betrieb.alarm_schliessen(db, "vertrag_nach_abholung_offen", ref=contract_id)
    return True


async def vertrag_hinweis_wiederherstellen(appt: dict, dealer_id: str) -> None:
    """Pruefung 21.09.2026 (V-12): Rueckweg storniert/nicht abgeholt ->
    abgeholt/erledigt. Vorher blieben Hinweis und Alarm nach dem Storno fuer
    immer weg:
      a) "Neuen Vertrag senden" kommt wieder — nur, wenn das Storno den Merker
         entfernt hat (nach_abholung_versand_ausgesetzt), der Vertrag noch auf
         DIESER Abholung steht und seitdem nicht versendet wurde.
      b) War die Neuerzeugung nach der Abholung gescheitert (Alarm, den das
         Storno schloss), holt vertrag_nach_abholung_sicherstellen sie nach —
         idempotent ueber nach_abholung_protokoll_id; scheitert es, steht der
         Alarm wieder (der Alarm-Nachholer versucht es erneut).
    Wirft nie."""
    appt_id, contract_id = appt.get("id"), appt.get("contract_id")
    if not appt_id or not contract_id:
        return
    try:
        proto = await db.pickup_protocols.find_one(
            {"appointment_id": appt_id, "status": "final", "superseded": {"$ne": True}},
            {"_id": 0})
        if not proto or ("contract_id" in proto
                         and (proto.get("contract_id") or None) != contract_id):
            return
        vertrag = await db.generated_pdfs.find_one(
            {"id": contract_id, "dealer_id": dealer_id},
            {"_id": 0, "nach_abholung_versand_ausgesetzt": 1})
        aus = (vertrag or {}).get("nach_abholung_versand_ausgesetzt")
        if isinstance(aus, dict) and aus.get("am"):
            gleicher = {"id": contract_id, "dealer_id": dealer_id,
                        "nach_abholung_versand_ausgesetzt.am": aus["am"]}
            r = await db.generated_pdfs.update_one(
                {**gleicher, "nach_abholung_protokoll_id": proto["id"],
                 # kein Versand seit dem Storno (der Versand beantwortet die Rueckfrage)
                 "send_status": {"$not": {"$elemMatch": {"sent_at": {"$gt": aus["am"]}}}}},
                {"$set": {"nach_abholung_versand_offen": True},
                 "$unset": {"nach_abholung_versand_ausgesetzt": ""}})
            if not r.matched_count:
                # seitdem versendet bzw. andere Fassung: nichts wiederbeleben
                await db.generated_pdfs.update_one(
                    gleicher, {"$unset": {"nach_abholung_versand_ausgesetzt": ""}})
        termin = await db.appointments.find_one(
            {"id": appt_id, "dealer_id": dealer_id},
            {"_id": 0, "id": 1, "dealer_id": 1, "created_by": 1, "contract_id": 1,
             "vehicle_id": 1}) or {}
        from routes.protocols import vertrag_nach_abholung_sicherstellen
        await vertrag_nach_abholung_sicherstellen({**appt, **termin, "dealer_id": dealer_id}, proto)
    except Exception:  # noqa: BLE001
        log.exception("Vertragshinweis nach dem Rueckweg zu Termin %s nicht wiederhergestellt",
                      appt_id)


async def ausgang_nachziehen(appt: dict, dealer_id: str, *, hat_vorgang: bool,
                             user: Optional[dict] = None) -> Dict[str, Any]:
    """Pruefbericht 20.09.2026 (V-12), Entscheidung Ahmad 21.09.2026: Folgen,
    wenn der Chef den Ausgang einer Abholung nachtraeglich aendert. Idempotent
    (auch fuer cleanup_service.termin_nacharbeit_nachholen). `appt` braucht id,
    status (der jetzt gilt), vehicle_id, contract_id.

    Termin jetzt "storniert"/"nicht abgeholt":
      * Kaufvertrag: "Neuen Vertrag senden" (nach_abholung_versand_offen) und
        der Alarm vertrag_nach_abholung_offen entfallen — die Fassung nach der
        Abholung bleibt als Nachweis. Nicht, wenn ein anderer Termin desselben
        Vertrags weiter abgeholt/erledigt ist.
      * Fahrzeug OHNE Kaufvorgang (Terminplaner ohne Vertrag): streng aus
        "abgeholt" zuruecknehmen (lifecycle.abholung_zuruecknehmen) — nur, wenn
        keine andere Abholung am Fahrzeug mehr abgeholt ist. MIT Vorgang
        erledigt das kaufvorgang.fahrzeug_status_aggregieren.
      * Steht das Fahrzeug schon in Bestand/Weiterverkauf: nichts aendern,
        Hinweis zurueckgeben (dort entscheidet der Chef).
    Termin wieder "abgeholt"/"erledigt" (Rueckweg) ohne Vorgang: Fahrzeug
    wieder auf "abgeholt" (mit Vorgang: Zusammenfassung).

    Liefert {"fahrzeug": Lebenszyklus danach, "hinweis": Text oder None}.
    Wirft bei einem verlorenen Rueckweg (Aufrufer setzt nacharbeit_offen).

    Pruefung 21.09.2026 (V-12): Beim Rueckweg (Termin wieder abgeholt/erledigt
    mit Vertrag) kommen "Neuen Vertrag senden" und — falls die Neuerzeugung
    nach der Abholung gescheitert war — deren Nachholung samt Alarm wieder
    (vertrag_hinweis_wiederherstellen). Vorher war beides nach Storno und
    Rueckweg fuer immer weg."""
    from lifecycle import LifecycleError, abholung_zuruecknehmen
    status = appt.get("status") or "offen"
    zurueck = status in AUSGANG_ZURUECK
    vehicle_id = appt.get("vehicle_id")
    contract_id = appt.get("contract_id")
    ergebnis: Dict[str, Any] = {"fahrzeug": None, "hinweis": None}
    andere_termine = {"dealer_id": dealer_id, "id": {"$ne": appt.get("id")},
                      "status": {"$in": sorted(AUSGANG_ABGEHOLT)}}
    if zurueck and contract_id:
        await vertrag_hinweis_aussetzen(appt.get("id"), dealer_id, contract_id)
    elif status in AUSGANG_ABGEHOLT and contract_id:
        await vertrag_hinweis_wiederherstellen(appt, dealer_id)
    if not vehicle_id:
        return ergebnis
    felder = {"_id": 0, "lifecycle": 1, "abgeholt_kaufvorgang_id": 1}
    v = await db.vehicles.find_one({"id": vehicle_id, "dealer_id": dealer_id}, felder)
    if not v:
        return ergebnis

    async def _andere_abholung() -> bool:
        # Doppel-Abholung: eine andere Abholung am selben Auto ist weiter abgeholt.
        return bool(await db.appointments.count_documents(
            {**andere_termine, "vehicle_id": vehicle_id}, limit=1)
            or await db.kaufvorgaenge.count_documents(
                {"vehicle_id": vehicle_id, "dealer_id": dealer_id, "status": "abgeholt"},
                limit=1))

    async def _wieder_abgeholt(von: str) -> None:
        # Pruefung 21.09.2026 (V-12): ein uebersprungener Schritt ist kein
        # Erfolg (Phase 2, G5) — LifecycleError, der Aufrufer setzt
        # nacharbeit_offen, der Aufraeumjob wiederholt diesen Aufruf.
        import kaufvorgang as _kv
        for schritt in _kv._schritte(von, "abgeholt"):
            if not await try_set_lifecycle(vehicle_id, dealer_id, schritt, user=user):
                log.warning("Rueckweg der Abholung %s: Fahrzeug %s bleibt vor %s stehen",
                            appt.get("id"), vehicle_id, schritt)
                raise LifecycleError(f"Rueckweg der Abholung: Fahrzeug bleibt vor '{schritt}' stehen")

    lc = v.get("lifecycle")
    if not hat_vorgang:
        if zurueck and lc == "abgeholt" and not v.get("abgeholt_kaufvorgang_id") \
                and not await _andere_abholung():
            ziel = "storniert" if status == "storniert" else "nicht_abgeholt"
            if not await abholung_zuruecknehmen(vehicle_id, dealer_id, ziel, user=user):
                raise LifecycleError("Fahrzeugstatus wurde zwischenzeitlich geändert")
            # Pruefung 21.09.2026 (V-12): Pruefen und Zuruecknehmen sind nicht
            # atomar — schloss inzwischen eine andere Abholung dieses Autos ab,
            # gehoert es wieder auf "abgeholt".
            if await _andere_abholung():
                await _wieder_abgeholt(ziel)
        elif not zurueck and status in AUSGANG_ABGEHOLT and lc in ("storniert", "nicht_abgeholt"):
            await _wieder_abgeholt(lc)
        v = await db.vehicles.find_one({"id": vehicle_id, "dealer_id": dealer_id}, felder) or v
    ergebnis["fahrzeug"] = v.get("lifecycle")
    if zurueck and v.get("lifecycle") in _NACH_ABHOLUNG_WEITER and not await _andere_abholung():
        ergebnis["hinweis"] = AUSGANG_FAHRZEUG_WEITER_HINWEIS
    return ergebnis


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
    stand = update.pop("stand", None)
    # Pruefbericht 20.09.2026 (V-12): Bestaetigung des Chefs, nie gespeichert.
    ausgang_bestaetigt = bool(update.pop("ausgang_bestaetigt", False))
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
    # Pruefung 21.09.2026 (V-12): Die Rueckfrage/Sperre haengt am BELEG der
    # Abholung (Vorgang "abgeholt", ohne Vorgang das finale Protokoll; siehe
    # abholung_beleg), nicht nur am alten Terminstatus — sonst umging das
    # Wiederoeffnen (abgeholt -> offen) mit anschliessendem Storno jede
    # Bestaetigung.
    ausgang_beleg = None
    if status_neu in AUSGANG_ZURUECK and status_neu != existing.get("status") \
            and existing.get("status") not in AUSGANG_ZURUECK:
        ausgang_beleg = await abholung_beleg(existing, user["dealer_id"])
    if existing.get("status") in ABGESCHLOSSEN and user.get("role") != "dealer":
        # Runde 12 (15.09.2026, Nr. 11): auch Datum und Uhrzeit sind Beweisdaten.
        geschuetzt = ("driver_id", "vehicle_id", "contract_id", "seller_name",
                      "seller_phone", "seller_email", "pickup_address",
                      "pickup_date", "pickup_time")
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
        # Runde 12 (15.09.2026, Nr. 12): auch der Wechsel zwischen Endzustaenden
        # (abgeholt -> storniert / nicht abgeholt) ist Chefsache.
        # Pruefbericht 20.09.2026 (V-12): "erledigt" zaehlt beim Kauf wie
        # "abgeholt" — ein Sucher konnte einen erledigten Termin ohne Protokoll
        # stornieren und damit den Kauf zuruecknehmen. Ebenso bleibt ein vom
        # Chef nachtraeglich geaenderter Ausgang (ausgang_geaendert) Chefsache.
        # Pruefung 21.09.2026 (V-12): "erledigt" nur mit Kauf/Fahrzeug (allgemeine
        # Termine wie vor V-12); der Merker gilt nur bis zum Wiederoeffnen (dort
        # entfernt, unten).
        if status_neu != existing.get("status") and (
                ausgang_gilt_als_abgeholt(existing)
                or existing.get("ausgang_geaendert")
                or await db.pickup_protocols.count_documents(
                    {"appointment_id": appt_id, "superseded": {"$ne": True}, "status": "final"},
                    limit=1)):
            raise HTTPException(403, "Den Ausgang einer abgeschlossenen Abholung ändert "
                                     "nur der Händler-Hauptaccount")
    # Pruefung 21.09.2026 (V-12): auch ein zur Korrektur wieder geoeffneter
    # Termin mit belegter Abholung wird vom Sucher nicht storniert / auf "nicht
    # abgeholt" gesetzt (vorher: Rueckweg ueber den offenen Status).
    if user.get("role") != "dealer" and existing.get("status") not in ABGESCHLOSSEN \
            and ausgang_beleg:
        raise HTTPException(403, "Den Ausgang einer abgeschlossenen Abholung ändert "
                                 "nur der Händler-Hauptaccount")
    # Pruefbericht 20.09.2026 (V-12), Entscheidung Ahmad 21.09.2026: Der Chef darf
    # eine abgeholte/erledigte Abholung (auch mit unterschriebenem Protokoll)
    # nachtraeglich auf "storniert" / "nicht abgeholt" setzen — aber nur mit
    # ausdruecklicher Bestaetigung (sonst 409, nichts geschrieben). Nur der
    # Chef erreicht diese Stelle (Sucher: 403 oben). Der Rueckweg zu
    # "abgeholt"/"erledigt" nach einer solchen Aenderung wird ebenso belegt.
    # Pruefung 21.09.2026 (V-12): ebenso aus einem offenen (wieder geoeffneten)
    # Status, solange die Abholung belegt ist; "erledigt" ohne Kauf/Fahrzeug
    # nur mit finalem Protokoll (ausgang_beleg).
    ausgang_zurueck = (ausgang_beleg is not None
                       or (status_neu in AUSGANG_ZURUECK
                           and ausgang_gilt_als_abgeholt(existing)))
    ausgang_rueckweg = (existing.get("status") in AUSGANG_ZURUECK
                        and status_neu in AUSGANG_ABGEHOLT
                        and bool(existing.get("ausgang_geaendert")))
    ausgang_proto = (ausgang_beleg or {}).get("protokoll")
    if (ausgang_zurueck or ausgang_rueckweg) and not ausgang_proto:
        ausgang_proto = await db.pickup_protocols.find_one(
            {"appointment_id": appt_id, "status": "final", "superseded": {"$ne": True}},
            {"_id": 0, "id": 1, "version": 1})
    if ausgang_zurueck and not ausgang_bestaetigt:
        # Pruefung 21.09.2026 (V-12): zuerst den Stand des Dialogs pruefen —
        # zeigt er noch einen aelteren Status (der Fahrer hat inzwischen
        # abgeschlossen), fragte die Oberflaeche nie nach; mit "neu laden"
        # baut Termine.jsx den Dialog frisch auf (B11), und beim naechsten
        # Speichern erscheint die Rueckfrage.
        if stand and existing.get("updated_at") and stand != existing["updated_at"]:
            raise HTTPException(409, TERMIN_VERALTET_HINWEIS)
        raise HTTPException(409, AUSGANG_BESTAETIGEN_HINWEIS)
    # Go-Live 13.09.2026 (P3, P6-Zusatz-Umhaengen): Liegt das Protokoll beim
    # Chef, ist es freigegeben oder wird gerade unterschrieben, darf der Termin
    # nicht an einen anderen Vertrag/ein anderes Fahrzeug gehaengt werden —
    # der Abschluss wuerde sonst PDF von Vertrag A mit Termin/Vorgang B
    # vermischen. Nur ein TATSAECHLICHER Wechsel zaehlt (die Oberflaeche
    # sendet das ganze Objekt); neue Vertraege und weitere Termine je Auto
    # bleiben frei.
    # Pruefung 14.09.2026 (C11): auch der FAHRER nicht — der neue Fahrer haette
    # sonst das vom Vorgaenger ausgefuellte, freigegebene Protokoll unter
    # seinem Namen unterschrieben (Fahrername steht im PDF, C2).
    vertrag_wechsel = bool((contract_loesen and existing.get("contract_id")) or (
        "contract_id" in update
        and (update.get("contract_id") or "") != (existing.get("contract_id") or "")))
    fahrzeug_wechsel = bool((fahrzeug_loesen and existing.get("vehicle_id")) or (
        "vehicle_id" in update
        and (update.get("vehicle_id") or "") != (existing.get("vehicle_id") or "")))
    fahrer_wechsel = bool(
        "driver_id" in update
        and (update.get("driver_id") or "") != (existing.get("driver_id") or ""))
    # Pruefung 14.09.2026 (P11): auch Verkaeufer, Abholanschrift, Datum und
    # Uhrzeit stehen im unterschriebenen PDF — waehrend Freigabe/Abschluss
    # nicht aenderbar (der Abschluss arbeitet mit dem Stand vom Start).
    termindaten_wechsel = any(
        f in update and (update.get(f) or "") != (existing.get(f) or "")
        for f in ("seller_name", "seller_phone", "seller_email", "pickup_address",
                  "pickup_date", "pickup_time"))
    if (vertrag_wechsel or fahrzeug_wechsel or fahrer_wechsel or termindaten_wechsel) \
            and await db.pickup_protocols.count_documents(
            {"appointment_id": appt_id, "superseded": {"$ne": True},
             "status": {"$in": list(PROTOKOLL_LAEUFT)}}, limit=1):
        raise HTTPException(409, PROTOKOLL_LAEUFT_HINWEIS)
    # Runde 12 (15.09.2026, Nr. 13/14): Ein abgeschlossener Termin mit
    # unterschriebenem Protokoll ist ein Beweisstueck — Fahrer, Fahrzeug,
    # Vertrag, Verkaeufer, Anschrift und Zeitpunkt aendert auch der Chef nur
    # ueber Wiederoeffnen und eine Korrektur-Version. Einem geschlossenen
    # Termin wird ausserdem kein anderer Fahrer mehr zugeteilt (er saehe eine
    # Abholung samt Verkaeuferdaten, die er nie gefahren ist).
    if existing.get("status") in ABGESCHLOSSEN and status_neu in ABGESCHLOSSEN:
        if (vertrag_wechsel or fahrzeug_wechsel or fahrer_wechsel or termindaten_wechsel) \
                and await db.pickup_protocols.count_documents(
                {"appointment_id": appt_id, "superseded": {"$ne": True}, "status": "final"},
                limit=1):
            raise HTTPException(409, "Der Termin ist mit unterschriebenem Protokoll "
                                     "abgeschlossen — Fahrer, Fahrzeug, Vertrag, Verkäufer, "
                                     "Anschrift und Zeitpunkt ändert man nur über Wiederöffnen "
                                     "und eine Korrektur-Version des Protokolls.")
        if fahrer_wechsel and update.get("driver_id"):
            raise HTTPException(409, "Einem abgeschlossenen Termin wird kein anderer Fahrer "
                                     "zugeteilt — bitte den Termin zuerst wieder öffnen.")
    # Pruefung 14.09.2026 (Liste 4, Nr. 9/10): Der vor Ort vereinbarte Preis
    # kommt aus dem freigegebenen/unterschriebenen Protokoll. Liegt eines vor
    # (Freigabe, Abschluss oder final), ist final_price am Termin kein
    # zweiter Preisweg mehr.
    if update.get("final_price") is not None \
            and update.get("final_price") != existing.get("final_price") \
            and await db.pickup_protocols.count_documents(
                {"appointment_id": appt_id, "superseded": {"$ne": True},
                 "status": {"$in": [*PROTOKOLL_LAEUFT, "final"]}}, limit=1):
        raise HTTPException(409, "Der Preis wird über das Abholprotokoll festgelegt "
                                 "(Freigabe bzw. Unterschrift) — bitte dort ändern, "
                                 "nicht am Termin.")
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
        # Runde 12 (15.09.2026, Nr. 15): "Fahrer entfernen + abgeholt" im selben
        # Aufruf umging die Protokollpflicht — sie gilt, sobald VOR oder NACH
        # dem Update ein Fahrer am Termin haengt.
        fahrer_effektiv = fahrer_effektiv or existing.get("driver_id")
        # Phase 2 (2.5, A7/D7): "erledigt" setzt den Kaufvorgang wie "abgeholt"
        # — dieselbe Protokollpflicht, sobald ein Fahrer eingeteilt ist.
        if update["status"] in ("abgeholt", "erledigt") and fahrer_effektiv:
            final_proto = await db.pickup_protocols.find_one(
                {"appointment_id": appt_id, "status": "final",
                 "superseded": {"$ne": True}},
                {"_id": 0, "id": 1, "driver_account_id": 1, "vehicle_id": 1, "contract_id": 1})
            if not final_proto:
                raise HTTPException(409, "Für diesen Termin ist ein Fahrer "
                                         "eingeteilt. 'Abgeholt' bzw. 'Erledigt' "
                                         "entsteht automatisch, sobald der Fahrer "
                                         "das Abholprotokoll unterschrieben "
                                         "abschließt.")
            # Pruefbericht 20.09.2026 (V-13/R1-10): Bisher genuegte IRGENDEIN
            # altes finales Protokoll. Nach dem Wiederoeffnen liessen sich so
            # Fahrer, Fahrzeug oder Vertrag tauschen und der Termin mit dem
            # Protokoll der ALTEN Abholung wieder auf "abgeholt" setzen — ein
            # Beleg, der zu dieser Abholung gar nicht passt. Jetzt muss das
            # Protokoll zu Fahrer, Fahrzeug und Vertrag passen, die NACH dem
            # Update gelten; sonst schliesst der Fahrer eine Korrektur ab.
            fahrzeug_danach = (update.get("vehicle_id") if "vehicle_id" in update
                               else (None if fahrzeug_loesen else existing.get("vehicle_id")))
            vertrag_danach = (update.get("contract_id") if "contract_id" in update
                              else (None if contract_loesen else existing.get("contract_id")))
            abweichend = []
            if final_proto.get("driver_account_id") and \
                    final_proto["driver_account_id"] != fahrer_effektiv:
                abweichend.append("Fahrer")
            if "vehicle_id" in final_proto and \
                    (final_proto.get("vehicle_id") or None) != (fahrzeug_danach or None):
                abweichend.append("Fahrzeug")
            if "contract_id" in final_proto and \
                    (final_proto.get("contract_id") or None) != (vertrag_danach or None):
                abweichend.append("Vertrag")
            if abweichend:
                raise HTTPException(409, "Das unterschriebene Abholprotokoll gehört zu einem "
                                         f"anderen Stand ({', '.join(abweichend)} geändert). "
                                         "'Abgeholt' entsteht jetzt nur über eine Korrektur-"
                                         "Version, die der eingeteilte Fahrer abschließt.")
        update["status_changed_at"] = now_iso()
        if ausgang_zurueck or ausgang_rueckweg:
            # Pruefbericht 20.09.2026 (V-12): wer, wann, alt/neu und Protokoll —
            # im SELBEN Write wie der Status (kein Status ohne Beleg).
            update["ausgang_geaendert"] = {
                "am": update["status_changed_at"], "von": user["id"],
                "von_status": existing.get("status"), "nach_status": update["status"],
                "protokoll_id": (ausgang_proto or {}).get("id")}
        elif existing.get("ausgang_geaendert"):
            # Pruefung 21.09.2026 (V-12): Der Merker gilt nur fuer die gerade
            # geltende Uebersteuerung. Wieder geoeffnet -> weg (der Beleg bleibt
            # im Verlauf termin.ausgang.geaendert); sonst blieben Hinweis,
            # Sucher-Sperre und "Rueckweg" an spaeteren, regulaeren Abschluessen
            # haengen. Wechselt der Chef zwischen Endzustaenden (storniert <->
            # nicht abgeholt), folgt der Merker dem Status.
            if update["status"] not in ABGESCHLOSSEN:
                unset["ausgang_geaendert"] = ""
            elif isinstance(existing["ausgang_geaendert"], dict):
                update["ausgang_geaendert.nach_status"] = update["status"]
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
    # Go-Live 13.09.2026 (P6-Nachbesserung): auch beim WIEDEROEFFNEN eines
    # geschlossenen Termins gilt eine alte Freigabe nicht mehr — sonst lebte
    # sie auf, wenn der Fahrer selbst "nicht abgeholt" setzte (drivers.py nimmt
    # nichts zurueck) oder ein Abschluss-Claim im geschlossenen Termin ablief.
    # VOR dem Write: ein Abschluss darf den offenen Termin nie mit der alten
    # Freigabe sehen. "abgeholt" bleibt aussen vor (finales Protokoll, Korrektur).
    # Befund 54 (16.09.2026): Ruecknahme und Termin-Write laufen in EINER
    # Transaktion (Replica-Set) — scheitert die Stand-Pruefung des Termins
    # (409), bleibt die Freigabe erhalten. Ohne Replica-Set nacheinander wie bisher.
    wiederoeffnen = (existing.get("status") in ABGESCHLOSSEN
                     and existing.get("status") != "abgeholt"
                     and status_neu not in ABGESCHLOSSEN)
    aenderung: Dict[str, Any] = {"$set": update}
    if unset:
        aenderung["$unset"] = unset
    # Phase 2 (2.8, D3): Stand-Pruefung ueber updated_at, wenn die Oberflaeche
    # ihren geladenen Stand mitschickt.
    write_filt: Dict[str, Any] = {"id": appt_id, "dealer_id": user["dealer_id"]}
    if stand:
        write_filt["updated_at"] = stand
    # Runde 12 (15.09.2026, Nr. 19/30): aendern sich Beweisdaten (Fahrer,
    # Fahrzeug, Vertrag, Verkaeufer, Anschrift, Datum, Uhrzeit), gilt der beim
    # Lesen gesehene Stand auch OHNE Client-Angabe — ein Protokoll-Abschicken
    # (bumpt updated_at) oder ein Kollege dazwischen fuehrt zu 409.
    beweisdaten_wechsel = bool(vertrag_wechsel or fahrzeug_wechsel or fahrer_wechsel
                               or termindaten_wechsel)
    # Befund 103 (16.09.2026): auch ein STATUSWECHSEL ohne Client-Stand laeuft
    # gegen den gelesenen Stand — ein alter Aufruf ueberschreibt keinen
    # neueren Status mehr still.
    status_gewechselt_cas = "status" in update and update["status"] != existing.get("status")
    if (beweisdaten_wechsel or status_gewechselt_cas) and not stand and existing.get("updated_at"):
        write_filt["updated_at"] = existing["updated_at"]

    async def _schreiben(session=None):
        ses = {"session": session} if session is not None else {}
        if wiederoeffnen:
            from routes.protocols import freigabe_beim_schliessen_zuruecknehmen
            # Pruefung 14.09.2026 (C19): Scheitert die Ruecknahme, darf der Termin
            # NICHT mit einer alten Freigabe wieder aufgehen.
            if await freigabe_beim_schliessen_zuruecknehmen(
                    appt_id, user.get("id"), session=session) is None:
                raise HTTPException(503, "Die Freigabe des Abholprotokolls konnte nicht "
                                         "zurückgenommen werden — bitte in einem Moment "
                                         "erneut öffnen.")
        try:
            r = await db.appointments.update_one(write_filt, aenderung, **ses)
        except DuplicateKeyError:
            raise HTTPException(409, TERMIN_DOPPELT_HINWEIS)
        if r.matched_count == 0:
            raise _StandVeraltet()
        return r

    try:
        res_write = await _transaktion(_schreiben)
    except _StandVeraltet:
        if beweisdaten_wechsel and await db.pickup_protocols.count_documents(
                {"appointment_id": appt_id, "superseded": {"$ne": True},
                 "status": {"$in": list(PROTOKOLL_LAEUFT)}}, limit=1):
            raise HTTPException(409, PROTOKOLL_LAEUFT_HINWEIS)
        raise HTTPException(409, TERMIN_VERALTET_HINWEIS)
    assert res_write.matched_count
    # Phase 2 (2.4, D4-D6): Der Termin ist geschrieben — scheitert danach ein
    # Folgeschritt (Entwurf verwerfen, Vertragszeiger, Vorgang/Fahrzeugstatus,
    # Vertrag neu erzeugen), gibt es keinen 500 mehr, sondern den Merker
    # nacharbeit_offen; cleanup_service.termin_nacharbeit_nachholen und das
    # naechste Speichern holen die Schritte nach.
    fahrer_entfernt = False
    vertrag_aktualisiert = False
    vertrag_veraltet = False
    status_gewechselt = "status" in update and update["status"] != existing.get("status")
    nacharbeit_fehler = False
    merker_gesetzt = False
    # Pruefbericht 20.09.2026 (V-12): Ergebnis der Ausgangs-Nacharbeit (Verlauf, Hinweis).
    ausgang_folgen: Dict[str, Any] = {}
    ausgang_kv_id = existing.get("kaufvorgang_id")
    try:
        if vertrag_wechsel or fahrzeug_wechsel or fahrer_wechsel:
            # Pruefung 14.09.2026 (Liste 4, Nr. 1/2/3/6): ein angefangener Entwurf
            # gehoert zum alten Fahrzeug/Vertrag/Fahrer — verwerfen.
            from routes.protocols import entwurf_bei_terminaenderung_verwerfen
            await entwurf_bei_terminaenderung_verwerfen(appt_id)
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
            # Befund 57 (16.09.2026): derselbe Preis auch in die dauerhaften
            # Auto-Daten (Spalte "Preis vor Ort") — vorher nur ueber den
            # Protokollweg, der Termin-Preis liess den Datensatz alt.
            if (_t or {}).get("contract_id"):
                import auto_daten as _ad
                try:
                    await _ad.vor_ort_nachtragen(db, _t["contract_id"], user["dealer_id"],
                                                 preis=float(update["final_price"]))
                except Exception:  # noqa: BLE001
                    log.exception("Auto-Daten: Preis vor Ort zu Vertrag %s nicht nachgetragen",
                                  _t.get("contract_id"))
        # Audit 13.09.2026 (#5): scheiterte beim Anlegen die Nacharbeit (Merker
        # nacharbeit_offen), jetzt Vorgangs-/Fahrzeugstatus auch OHNE Status- oder
        # Vertragswechsel nachziehen — ein normales Speichern tat das vorher nicht.
        nacharbeit_nachholen = bool(existing.get("nacharbeit_offen"))
        if status_gewechselt or contract_gewechselt or nacharbeit_nachholen:
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
            if nacharbeit_nachholen:
                # Go-Live 13.09.2026 (P5-Nachbesserung): scheiterte der Protokoll-
                # Abschluss nach dem Termin-Write, fehlt auch der nachverhandelte
                # Preis — VOR der Statusuebernahme nachziehen (wie im Abschluss).
                from routes.protocols import preis_nachholen
                await preis_nachholen({**termin_nachher, "dealer_id": user["dealer_id"]})
            hat_vorgang = await _kv.termin_status_uebernehmen(termin_nachher, wirksamer_status, user=user)
            if not hat_vorgang and vehicle_id and status_gewechselt:
                # Nr. 37/38: dieselbe Tabelle wie die Fahrer-App. Vorher
                # fehlte hier "erledigt" ganz, waehrend die Fahrer-App es
                # als "nicht_abgeholt" verbuchte.
                zustand = zustand_fuer_terminstatus(update["status"])
                if zustand:
                    await try_set_lifecycle(vehicle_id, user["dealer_id"],
                                            zustand, user=user)
            # Pruefbericht 20.09.2026 (V-12): Ausgang nachtraeglich geaendert —
            # Fahrzeug (ohne Vorgang) und Vertragshinweis nachziehen; auch beim
            # Nachholen, wenn dieser Schritt beim letzten Mal scheiterte.
            if ausgang_zurueck or ausgang_rueckweg or (
                    nacharbeit_nachholen and existing.get("ausgang_geaendert")
                    and wirksamer_status in (AUSGANG_ZURUECK | AUSGANG_ABGEHOLT)):
                ausgang_kv_id = termin_nachher.get("kaufvorgang_id") or ausgang_kv_id
                ausgang_folgen = await ausgang_nachziehen(
                    {"id": appt_id, "status": wirksamer_status, "vehicle_id": vehicle_id,
                     "contract_id": contract_id}, user["dealer_id"],
                    hat_vorgang=hat_vorgang, user=user)
            if not hat_vorgang and vehicle_id and nacharbeit_nachholen \
                    and wirksamer_status in TERMIN_OFFEN_WERTE:
                # Audit 13.09.2026 (#5): wie beim Anlegen (manueller Termin ohne Vorgang).
                # Nachbesserung: eigenes if statt elif — ein Statuswechsel auf einen
                # OFFENEN Wert (bestätigt, verschoben ...) nahm sonst den ersten Zweig,
                # zog nichts nach und loeschte trotzdem den Merker. Abgeschlossene
                # Zielstati sind hier ausgeschlossen, der erste Zweig bleibt massgeblich.
                await try_set_lifecycle(vehicle_id, user["dealer_id"], "abholung_geplant", user=user)
            if not hat_vorgang and vehicle_id and nacharbeit_nachholen and not status_gewechselt \
                    and wirksamer_status == "abgeholt":
                # Go-Live 13.09.2026 (P5-Nachbesserung): Abschluss ohne Vorgang
                # (Termin ohne Vertrag) brach nach dem Termin-Write ab.
                await try_set_lifecycle(vehicle_id, user["dealer_id"], "abgeholt", user=user)
            if nacharbeit_nachholen:
                if hat_vorgang and termin_nachher.get("kaufvorgang_id"):
                    await db.kaufvorgaenge.update_one(
                        {"id": termin_nachher["kaufvorgang_id"], "dealer_id": user["dealer_id"]},
                        {"$set": {"appointment_id": appt_id}})
                await db.appointments.update_one(
                    {"id": appt_id},
                    {"$unset": {"nacharbeit_offen": "", "nacharbeit_protokoll_id": ""}})
        if status_neu in ABGESCHLOSSEN and status_neu != "abgeholt":
            # Runde 17 (Nr. 11): Termin storniert/nicht abgeholt/erledigt — ein
            # angefangener Korrektur-Entwurf des Protokolls wird verworfen und
            # die korrigierte Version ist wieder die massgebliche (sonst blieb
            # der Termin ohne aktuelles Protokoll). Best effort, wirft nie.
            from routes.protocols import freigabe_beim_schliessen_zuruecknehmen, korrektur_verwerfen
            await korrektur_verwerfen(appt_id)
            # Go-Live 13.09.2026 (P6): Lag das Protokoll beim Chef oder war es schon
            # freigegeben, gilt diese Freigabe nach dem Wiederoeffnen nicht mehr.
            # Pruefung 14.09.2026 (C19): Der Termin ist schon geschlossen — bei
            # einem Fehler Betriebsalarm; cleanup_service holt die Ruecknahme nach.
            if await freigabe_beim_schliessen_zuruecknehmen(appt_id, user.get("id")) is None:
                import betrieb as _betrieb
                await _betrieb.alarm(db, "protokoll_freigabe_ruecknahme_offen", ref=appt_id,
                                     dealer_id=user["dealer_id"], status=status_neu)
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
            try:
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
            except HTTPException as exc:
                # Pruefbericht 20.09.2026 (V-26): 409 = der Vertrag wird gerade
                # verschickt. Der TERMIN ist hier schon gespeichert — ein
                # durchgereichter 409 liess ihn mit neuem Datum und den Vertrag
                # mit dem alten stehen, und ein erneutes Speichern erkannte
                # keine Aenderung mehr. Jetzt: als veraltet merken (unten);
                # der Aufraeumjob bzw. das naechste Speichern erzeugt nach.
                if exc.status_code != 409:
                    raise
                vertrag_aktualisiert = False
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

    except HTTPException:
        raise
    except Exception:  # noqa: BLE001
        log.exception("Nacharbeit nach Termin-Update %s fehlgeschlagen", appt_id)
        nacharbeit_fehler = True
        try:
            await db.appointments.update_one({"id": appt_id},
                                             {"$set": {"nacharbeit_offen": True}})
            merker_gesetzt = True
        except Exception:  # noqa: BLE001
            log.exception("Merker nacharbeit_offen fuer Termin %s nicht gesetzt", appt_id)

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
    # Nachbesserung 13.09.2026 (#0): Termin, Vorgang und Merker sind hier
    # schon geschrieben — ein fehlender Audit-Eintrag darf kein 500 geben.
    await log_activity_sicher(user["dealer_id"], user["id"], "termin.aktualisiert",
                              ref=appt_id, meta=meta)
    if ausgang_zurueck or ausgang_rueckweg:
        # Pruefbericht 20.09.2026 (V-12), Entscheidung Ahmad 21.09.2026: eigener
        # Verlaufseintrag fuer die Chef-Uebersteuerung (und ihren Rueckweg) —
        # mit Protokoll, alt/neu, Konto (user_id) und Zeit (created_at).
        await log_activity_sicher(
            user["dealer_id"], user["id"], "termin.ausgang.geaendert", ref=appt_id,
            meta={"status_von": existing.get("status"), "status_nach": update["status"],
                  "protokoll_id": (ausgang_proto or {}).get("id"),
                  "protokoll_version": (ausgang_proto or {}).get("version"),
                  "vehicle_id": vehicle_id, "contract_id": contract_pruefen,
                  "kaufvorgang_id": ausgang_kv_id,
                  "fahrzeug_nachher": ausgang_folgen.get("fahrzeug"),
                  "bestaetigt": ausgang_bestaetigt})
    out = {"ok": True, "pickup_date_changed": pickup_changed,
           "contract_updated": vertrag_aktualisiert}
    hinweise = []
    if ausgang_folgen.get("hinweis"):
        hinweise.append(ausgang_folgen["hinweis"])
    if fahrer_entfernt:
        hinweise.append(FAHRER_ENTFERNT_HINWEIS)
    elif vertrag_veraltet:
        hinweise.append(VERTRAG_VERALTET_HINWEIS)
    if nacharbeit_fehler:
        hinweise.append(NACHARBEIT_HINWEIS if merker_gesetzt else NACHARBEIT_FEHLGESCHLAGEN_HINWEIS)
        if merker_gesetzt:
            out["nacharbeit_offen"] = True
    fahrer_effektiv = None if fahrer_entfernt else (
        update.get("driver_id") if "driver_id" in update else existing.get("driver_id"))
    dbh = await _doppelbuchung_hinweis(
        appt_id, fahrer_effektiv, update.get("pickup_date", existing.get("pickup_date")),
        update.get("pickup_time", existing.get("pickup_time")))
    if dbh:
        out["doppelbuchung"] = True
        hinweise.append(dbh)
    if hinweise:
        out["hinweis"] = " ".join(hinweise)
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
    current = bericht_fuer_sucher(user, aktuelle[0]) if aktuelle else None
    out: Dict[str, Any] = {"report": current}
    # Runde 21: Frist der Fahrerfotos (Tage ab dem Hochladen) fuer die Anzeige.
    from cleanup_service import FAHRERFOTO_TAGE
    out["fahrerfoto_tage"] = FAHRERFOTO_TAGE
    if versions:
        # Phase 4 (4.3): 21 lesen, 20 zeigen — die Kuerzung wird gemeldet.
        alle = await db.pickup_reports.find(
            {"appointment_id": appt_id}, {"_id": 0},
        ).sort("version", -1).to_list(21)
        out["versions"] = [bericht_fuer_sucher(user, r) for r in alle[:20]]
        out["versions_gekuerzt"] = len(alle) > 20
    return out


# Befund 137 (16.09.2026): Sucher bekommen den Abholbericht ohne Verwaltungs-
# felder (Fahrer-Konto, Firma, Ersetzt-Verweis, interne Loesch-Merker). Die
# Foto-Schluessel bleiben — die Oberflaeche laedt die Bilder darueber.
_BERICHT_INTERN = ("driver_account_id", "dealer_id", "replaces_id")
_ABWEICHUNG_INTERN = ("photo_loeschung_offen",)


def bericht_fuer_sucher(user: dict, rep: Optional[dict]) -> Optional[dict]:
    if not rep or user.get("role") != "sucher":
        return rep
    rep = {k: v for k, v in rep.items() if k not in _BERICHT_INTERN}
    if isinstance(rep.get("deviations"), list):
        rep["deviations"] = [{k: v for k, v in (d or {}).items() if k not in _ABWEICHUNG_INTERN}
                             for d in rep["deviations"]]
    return rep


@router.delete("/appointments/{appt_id}")
async def delete_appointment(appt_id: str, user=Depends(current_firma)):
    # Berechtigungsmatrix (PR-Review 09/2026): firmenweite Termine loescht
    # der Chef; ein Sucher nur seine eigenen, noch offenen Termine.
    # Runde 15 (Nr. 7): fuer beide Rollen laden — der Audit-Eintrag braucht
    # den Stand VOR dem Loeschen (Status, Fahrzeug, Vertrag, Fahrer, Datum).
    appt = await db.appointments.find_one(
        {"id": appt_id, "dealer_id": user["dealer_id"]},
        {"_id": 0, "created_by": 1, "status": 1, "contract_id": 1, "kaufvorgang_id": 1,
         "vehicle_id": 1, "driver_id": 1, "pickup_date": 1, "pickup_time": 1,
         "zuteilung": 1, "updated_at": 1})
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
    # Runde 12 (15.09.2026, Nr. 16): eine vom Fahrer angenommene Fahrt
    # verschwindet nicht einfach — erst stornieren (der Fahrer erfaehrt es),
    # dann loeschen.
    if appt.get("zuteilung") == "angenommen" and (appt.get("status") or "offen") in TERMIN_OFFEN_WERTE:
        raise HTTPException(409, "Der Fahrer hat diese Fahrt angenommen — bitte den Termin "
                                 "zuerst stornieren (Status 'storniert'), dann löschen.")
    # Pruefung 14.09.2026 (C15): Ein Termin mit unterschriebenem Protokoll
    # (Beweiskette: Unterschriften, PDF) oder mit laufender Freigabe/laufendem
    # Abschluss wird nicht geloescht — das Protokoll bliebe verwaist bzw. der
    # Abschluss schriebe auf einen Termin, den es nicht mehr gibt. Stattdessen
    # stornieren; das Protokoll bleibt als Beleg erreichbar.
    if await db.pickup_protocols.count_documents(
            {"appointment_id": appt_id,
             "status": {"$in": [*PROTOKOLL_LAEUFT, "final"]}}, limit=1):
        raise HTTPException(409, TERMIN_MIT_PROTOKOLL_HINWEIS)
    # Befund 109 (16.09.2026): der Audit-Eintrag "geloescht" entsteht erst NACH
    # dem erfolgreichen Loeschen (unten) — log_activity_sicher wirft nie, die
    # Spur geht also nicht mehr verloren, und ein 409 hinterlaesst keinen
    # Eintrag ueber eine Loeschung, die nie stattfand.
    # Runde 29 (12.09.2026, Pruefbefund): ZUERST die Verweise loesen, DANN
    # den Termin loeschen. Vorher war es umgekehrt — brach der Vorgang
    # dazwischen ab (Neustart, Netz weg), war der Termin weg, und Kaufvorgang
    # und Vertrag zeigten fuer immer auf einen Termin, den es nicht mehr gibt.
    # In dieser Reihenfolge ist der schlimmste Fall harmlos: der Termin steht
    # noch da und laesst sich einfach erneut loeschen.
    # Umbau Kaufvorgaenge: der Vorgang verliert den Termin (zurueck auf
    # "Vertrag erstellt"), Vertragsverweis wird geloest.
    import kaufvorgang as _kv
    # Phase 4 (4.1, A4/D9): Vorgang loesen, Vertragsverweis leeren und Termin
    # loeschen in EINER Transaktion, wenn die Datenbank ein Replica-Set ist
    # (Produktion); sonst nacheinander wie bisher. Die Fahrzeug-Zusammenfassung
    # laeuft danach mit Merker (Phase 2).
    betroffene: list = []

    async def _kern(session=None) -> int:
        ses = {"session": session} if session is not None else {}
        # Runde 12 (15.09.2026, Nr. 18): Protokollzustand unmittelbar vor dem
        # Loeschen erneut pruefen (im Replica-Set innerhalb der Transaktion).
        if await db.pickup_protocols.count_documents(
                {"appointment_id": appt_id,
                 "status": {"$in": [*PROTOKOLL_LAEUFT, "final"]}}, limit=1, **ses):
            raise HTTPException(409, TERMIN_MIT_PROTOKOLL_HINWEIS)
        # Befund 108 (16.09.2026): die betroffenen Vorgaenge INNERHALB der
        # Transaktion lesen — ein Vorgang, der zwischen Lesen und Loeschen an
        # den Termin gehaengt wurde, zeigte sonst auf einen geloeschten Termin.
        betroffene[:] = [kv async for kv in db.kaufvorgaenge.find(
            {"appointment_id": appt_id},
            {"_id": 0, "id": 1, "status": 1, "vehicle_id": 1, "dealer_id": 1}, **ses)]
        for kv in betroffene:
            neu = "vertrag_erstellt" if kv.get("status") == "abholung_geplant" else kv.get("status")
            await db.kaufvorgaenge.update_one(
                {"id": kv["id"], "appointment_id": appt_id},
                {"$set": {"status": neu, "appointment_id": None, "updated_at": now_iso()}}, **ses)
        await db.generated_pdfs.update_many(
            {"dealer_id": user["dealer_id"], "appointment_id": appt_id},
            {"$set": {"appointment_id": None}}, **ses)
        # Runde 12 (15.09.2026, Nr. 17): nur den Stand loeschen, der geprueft
        # wurde — setzt der Fahrer dazwischen "nicht abgeholt", greift das nicht.
        # Runde 19 (Nr. 35): auch updated_at gehoert zum geprueften Stand —
        # aendert ein paralleler Tab Vertrag, Fahrzeug oder Abholort, trifft
        # der alte Loeschaufruf nicht mehr (409 "erneut laden").
        r = await db.appointments.delete_one(
            {"id": appt_id, "dealer_id": user["dealer_id"],
             "status": appt.get("status"), "zuteilung": appt.get("zuteilung"),
             "updated_at": appt.get("updated_at")}, **ses)
        return r.deleted_count

    geloescht = await _transaktion(_kern)
    for kv in betroffene:
        ziel = await _kv.fahrzeug_status_aggregieren(kv["vehicle_id"], kv["dealer_id"], user=user)
        await _kv._nacharbeit_merken(kv, ziel)
    if not geloescht and await db.appointments.find_one(
            {"id": appt_id, "dealer_id": user["dealer_id"]}, {"_id": 1}):
        raise HTTPException(409, "Der Termin wurde inzwischen geändert (Status oder Fahrer) — "
                                 "bitte neu laden.")
    if geloescht and await db.pickup_protocols.count_documents(
            {"appointment_id": appt_id, "status": {"$in": [*PROTOKOLL_LAEUFT, "final"]}}, limit=1):
        import betrieb as _betrieb
        await _betrieb.alarm(db, "termin_geloescht_mit_protokoll", ref=appt_id,
                             dealer_id=user["dealer_id"])
    if not geloescht:
        # Jemand anderes war schneller — dessen Lauf hat dieselben Verweise
        # geloest, es bleibt nichts Halbes zurueck.
        raise HTTPException(404, "Termin nicht gefunden")
    # Befund 109: Audit nach dem erfolgreichen Loeschen (Stand VOR dem Loeschen).
    if not await log_activity_sicher(user["dealer_id"], user["id"], "termin.geloescht",
                                     ref=appt_id,
                                     meta={"status": appt.get("status") or "offen",
                                           "vehicle_id": appt.get("vehicle_id"),
                                           "contract_id": appt.get("contract_id"),
                                           "driver_id": appt.get("driver_id"),
                                           "pickup_date": appt.get("pickup_date"),
                                           "pickup_time": appt.get("pickup_time"),
                                           "created_by": appt.get("created_by")}):
        log.error("Termin %s von %s geloescht — Audit-Eintrag fehlt", appt_id, user["id"])
        import betrieb as _betrieb
        await _betrieb.alarm(db, "audit_fehlt", ref=appt_id, aktion="termin.geloescht",
                             user_id=user["id"], dealer_id=user["dealer_id"])
    # Pruefung 14.09.2026 (C15): nicht unterschriebene Entwuerfe (ohne PDF und
    # Unterschriften) haengen an nichts mehr — mit loeschen statt verwaisen.
    try:
        await db.pickup_protocols.delete_many({"appointment_id": appt_id, "status": "entwurf"})
        # Pruefung 14.09.2026 (Liste 2, Nr. 3): Abholberichte des Termins mit —
        # Fotos werden geloescht bzw. zur Nachholung vorgemerkt, danach die
        # Berichte selbst (kein verwaister Bericht mit Fahrername/Notizen).
        from cleanup_service import _fotos_eines_berichts_loeschen
        from datetime import datetime as _dt, timezone as _tz
        async for rep in db.pickup_reports.find({"appointment_id": appt_id},
                                                {"_id": 0, "id": 1, "dealer_id": 1, "deviations": 1}):
            await _fotos_eines_berichts_loeschen(db, rep, _dt.now(_tz.utc), {},
                                                 rep.get("dealer_id") or "")
        await db.pickup_reports.delete_many({"appointment_id": appt_id})
    except Exception:  # noqa: BLE001
        log.exception("Protokoll-Entwuerfe/Berichte zu Termin %s nicht geloescht", appt_id)
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

    await log_activity_sicher(user["dealer_id"], user["id"],
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
