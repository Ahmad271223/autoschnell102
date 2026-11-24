"""Bestand & Fahrzeugakte (B2B-Händlermodul, Phase 1).

- Nach-Abholung-Entscheidung (nur Händler-Hauptaccount)
- Bestandsverwaltung: Standort, Notizen, Kosten, 50-Tage-Frist
- Fahrzeugakte: aggregierte Sicht über alle vorhandenen Collections
- Abweichungs-Übernahme (Diff Einkauf vs. Abholung)
- Manuell hinzugefügte Fahrzeuge (source: "manuell")
"""
import logging
import math
import uuid
from datetime import date, datetime, time, timedelta, timezone
from typing import Annotated, Any, Dict, List, Literal, Optional, Tuple
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field, StringConstraints

from deps import (TERMIN_OFFEN_WERTE, besitzer_anreichern, besitzer_namen, clean_doc,
                  current_chef, haupt_chef_id, konten_maskieren,
                  current_firma, db, fahrzeug_bereich, ist_sucher,
                  log_activity, log_activity_sicher, now_iso)
from lifecycle import LifecycleError, set_lifecycle

from deps import MARKTPLATZ_GESPERRT  # Go-Live-Schalter 15.09.2026
from konfig import marktplatz_aktiv

router = APIRouter()

BESTAND_RETENTION_DAYS = 50
# Audit 13.09.2026 (#53): so viele Historien-Eintraege zeigt die Akte
HISTORIE_MAX = 100

# Nachpruefung Runde 14 (Nr. 39/40/41): nach diesen Lifecycles ist das
# Fahrzeug abgeschlossen — Bestandsdaten, Abweichungen und manuelle
# Stammdaten sind dann eingefroren. Das Frontend blendet die Formulare
# zwar aus, die API liess Aenderungen aber weiterhin zu (Akte/Historie
# eines verkauften Fahrzeugs veraenderbar). "storniert" bleibt offen,
# weil es laut lifecycle.py wieder in den Zyklus zurueckfuehren kann.
_ABGESCHLOSSEN = ("verkauft", "archiviert", "geloescht")


def _abgeschlossen_sperren(v: Dict[str, Any], meldung: str) -> None:
    if (v or {}).get("lifecycle") in _ABGESCHLOSSEN:
        raise HTTPException(409, meldung)


def _als_aware(d: datetime) -> datetime:
    """Nachpruefung Runde 14 (Nr. 93/94): naive ISO-Zeitstempel (Alt-/
    Import-/Restore-Daten) als UTC annehmen — sonst scheitert die Differenz
    zu einem aware `now` mit TypeError und die ganze Liste/Akte wird 500."""
    return d if d.tzinfo else d.replace(tzinfo=timezone.utc)


def _resttage(exp: str, now: datetime) -> int:
    return max(0, (_als_aware(datetime.fromisoformat(exp)) - now).days)


async def current_haendler(user=Depends(current_chef)):
    """NUR der Händler-Hauptaccount. Strikte Rollentrennung (08/2026):
    Admins verwalten die Plattform, handeln aber nicht — sonst landen z.B.
    Sucher versehentlich unter der Admin-Firma. Sucher haben hier ebenfalls
    keinen Zugriff: Bestands-/Verkaufsentscheidungen sind Chefsache.

    Pruefbericht 09/2026: die Regel stand hier frueher noch einmal eigens
    im Code und prueft nur die Rolle, nicht die Zugehoerigkeit zu einer
    echten Firma. Jetzt haengt sie an current_chef aus deps.py — es gibt
    genau EINE Fassung dieser Regel, und sie verlangt beides."""
    return user


# Pruefbericht 09/2026: hier stand eine ZWEITE, eigene Fassung von
# current_firma. Sie pruefte nur die Rolle, nicht die Zugehoerigkeit zu
# einer echten Firma (dealer_id). Zwei Fassungen derselben Sicherheitsregel
# laufen mit der Zeit auseinander — ein Fix in deps.py haette diese Datei
# nicht erreicht. Es gilt jetzt ueberall dieselbe Regel aus deps.py.


# ---------- Models ----------
class DecisionIn(BaseModel):
    decision: Literal["bestand", "verkaufsentwurf", "loeschen"]
    # Rollenprüfung 22.09.2026 (RP-496): der Zustand, den die Oberflaeche
    # beim Klick ANGEZEIGT hat. "Nur speichern" in einem veralteten Tab setzte
    # sonst ein inzwischen veroeffentlichtes Fahrzeug still auf "bestand".
    # Optional, damit alte Oberflaechen/Skripte weiter funktionieren.
    von_lifecycle: Optional[str] = Field(default=None, max_length=40)
    # Rollenprüfung 22.09.2026 (RP-454, Welle B2): "loeschen" storniert die
    # offenen Abholtermine des Fahrzeugs NUR mit diesem Flag — die Oberflaeche
    # setzt es erst, nachdem der Chef die Termine (Datum, Fahrer) gesehen und
    # die Rueckfrage bestaetigt hat. Ohne Flag weiterhin 409 mit der Liste.
    termine_stornieren: bool = False


class BestandUpdateIn(BaseModel):
    location: Optional[str] = Field(default=None, max_length=300)
    notes: Optional[str] = Field(default=None, max_length=10000)
    # Runde 17 (Nr. 337): Liste gedeckelt — _clean_costs schnitt zwar auf 30,
    # verarbeitete davor aber beliebig lange Eingaben.
    costs: Optional[List[Dict[str, Any]]] = Field(default=None, max_length=30)  # [{label, amount}]
    # Rollenprüfung 22.09.2026 (RP-461): Stand der Bestandsdaten, den die Akte
    # geladen hat (bestand.stand; "" = noch nie gespeichert). Ein veralteter
    # Tab (Handy/PC) ueberschrieb sonst Kosten, Notizen und Standort eines
    # anderen Geraets still. None = ohne Pruefung (alte Oberflaechen).
    stand: Optional[str] = Field(default=None, max_length=64)


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
    # Nachpruefung Runde 14 (Nr. 110): jeder Eintrag gedeckelt — sonst
    # landeten Megabyte-Strings je Ausstattungsmerkmal in vehicles.data
    # und von dort im Inserat (resale.py) und Marktplatz.
    features: List[Annotated[str, StringConstraints(max_length=120)]] = Field(
        default_factory=list, max_length=80)
    description: str = Field(default="", max_length=20000)
    # Nachpruefung Runde 14 (Nr. 95): Infinity/NaN kamen durch ge=0 hindurch,
    # das Dokument stand mit inf in der DB und jede Antwort/Marge dazu war 500.
    purchase_price: Optional[float] = Field(default=None, ge=0, allow_inf_nan=False)


def _clean_costs(costs: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    out = []
    for c in (costs or [])[:30]:
        if not isinstance(c, dict):
            continue
        label = str(c.get("label", "")).strip()[:100]
        try:
            amount = round(float(c.get("amount", 0)), 2)
        except (TypeError, ValueError):
            amount = 0.0
        # Runde 15 (Nr. 4): Infinity/NaN kamen als `Any` an Pydantic vorbei,
        # standen als inf in bestand.costs und liessen jede Antwort mit
        # diesem Fahrzeug (Bestand, Akte, Termine) sowie die Marge mit 500
        # scheitern. Klar ablehnen statt still nullen.
        if not math.isfinite(amount) or abs(amount) > 1e9:
            raise HTTPException(422, f"Ungültiger Betrag bei '{label or 'Kosten'}' — "
                                     "bitte eine Zahl bis 1.000.000.000 eingeben")
        if amount < 0:
            # Pruefung 14.09.2026 (F13): negative Kosten erhoehten die Marge.
            raise HTTPException(422, f"Kosten bei '{label or 'Kosten'}' dürfen nicht "
                                     "negativ sein")
        # Rollenprüfung 22.09.2026 (RP-449): Ein Betrag ohne Bezeichnung
        # verschwand beim Speichern still — die Marge stimmte danach nicht
        # mehr mit dem ueberein, was der Chef eingetippt hatte. Jetzt bekommt
        # er die Bezeichnung "Kosten"; nur ganz leere Zeilen fallen weg.
        if not label and amount:
            label = "Kosten"
        if label:
            out.append({"label": label, "amount": amount})
    return out


# Rollenprüfung 22.09.2026 (RP-085/RP-184/RP-496): Inserate in diesen
# Zustaenden sind verkaufsbereit oder live. Solange es eines gibt, darf das
# Fahrzeug nicht per Entscheidung zurueck in den Bestand oder in einen neuen
# Entwurf — sonst stand es auf "bestand", waehrend das Inserat weiter live
# war, und Reservieren, Annehmen und Verkaufen endeten in 409 bzw. einem
# Fahrzeug/Inserat-Widerspruch mit Betriebsalarm.
_INSERAT_LIVE = ("verkaufsbereit", "veroeffentlicht", "reserviert")
_INSERAT_LIVE_TEXT = {"verkaufsbereit": "verkaufsbereit",
                      "veroeffentlicht": "auf dem Marktplatz",
                      "reserviert": "reserviert"}


# Rollenprüfung 22.09.2026 (RP-454, Welle B2): Kennung im 409-Detail, an der
# Bestand.jsx/FahrzeugAkte.jsx die Rueckfrage "Offene Termine stornieren und
# Fahrzeug löschen" erkennen (detail = {"msg", "code", "termine"}).
TERMINE_OFFEN_CODE = "termine_offen"
TERMINE_OFFEN_TEXT = ("Zu diesem Fahrzeug gibt es noch einen offenen Abholtermin — "
                      "bitte den Termin zuerst abschließen oder stornieren (Termine), "
                      "dann löschen.")


async def _offene_termine(vehicle_id: str, dealer_id: str) -> List[Dict[str, Any]]:
    """RP-454: offene Abholtermine des Fahrzeugs (Doppel-Abholung: auch der
    zweite Termin), aelteste zuerst — mit Datum, Uhrzeit und Fahrername fuer
    die Rueckfrage des Chefs. Fehlender/leerer Status zaehlt als offen."""
    termine = await db.appointments.find(
        {"dealer_id": dealer_id, "vehicle_id": vehicle_id,
         "status": {"$in": TERMIN_OFFEN_WERTE}},
        {"_id": 0, "id": 1, "pickup_date": 1, "pickup_time": 1, "driver_id": 1,
         "status": 1, "updated_at": 1}).sort("pickup_date", 1).to_list(50)
    fahrer_ids = [t["driver_id"] for t in termine if t.get("driver_id")]
    namen: Dict[str, str] = {}
    if fahrer_ids:
        async for f in db.driver_accounts.find({"id": {"$in": fahrer_ids}},
                                               {"_id": 0, "id": 1, "display_name": 1}):
            namen[f["id"]] = f.get("display_name") or ""
    return [{"id": t["id"], "pickup_date": t.get("pickup_date"),
             "pickup_time": t.get("pickup_time"), "status": t.get("status"),
             "driver_name": namen.get(t.get("driver_id") or "") or None,
             "updated_at": t.get("updated_at")} for t in termine]


async def _termine_stornieren(termine: List[Dict[str, Any]], user: dict) -> List[str]:
    """RP-454: die Termine ueber DENSELBEN Weg stornieren wie "Termine ->
    Storno" (routes.appointments.update_appointment): Verlaufseintrag
    termin.aktualisiert mit status_von/nach, Kaufvorgang und Fahrzeugstatus
    nachgezogen, Fahrer-Sichtfrist gesetzt, Freigabe/Korrektur des Protokolls
    zurueckgenommen. Der gelesene Stand (updated_at) geht als `stand` mit —
    hat der Fahrer inzwischen abgeschlossen, kommt 409 statt eines stillen
    Stornos. Bricht ein Termin ab, bleibt das Fahrzeug stehen (die bis dahin
    stornierten Termine sind im Verlauf belegt)."""
    from routes.appointments import AppointmentIn, update_appointment
    storniert: List[str] = []
    for t in termine:
        try:
            await update_appointment(
                t["id"], AppointmentIn(status="storniert", stand=t.get("updated_at")), user)
        except HTTPException as exc:
            wann = t.get("pickup_date") or "ohne Datum"
            raise HTTPException(
                exc.status_code if exc.status_code in (403, 409) else 409,
                f"Termin am {wann} konnte nicht storniert werden: "
                f"{exc.detail if isinstance(exc.detail, str) else 'bitte unter Termine prüfen'} "
                "— das Fahrzeug wurde nicht gelöscht.")
        storniert.append(t["id"])
    return storniert


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
    # Rollenprüfung 22.09.2026 (RP-496): Hat sich der Zustand seit dem
    # Anzeigen geaendert (zweiter Tab, anderes Geraet), entscheidet der Chef
    # ueber etwas, das er gar nicht sieht — 409 statt still umzuschalten.
    if body.von_lifecycle is not None and v.get("lifecycle") != body.von_lifecycle:
        raise HTTPException(409, "Der Fahrzeugstatus hat sich inzwischen geändert — "
                                 "bitte die Seite neu laden und dann entscheiden.")

    # Runde 17 (Nr. 261/263/287): Statuswechsel UND Zusatzfelder (Fotos
    # leeren, deleted_at, Bestandsfrist) in EINEM Write mit CAS auf den
    # gelesenen Lifecycle (set_lifecycle extra_set). Vorher gab es einen
    # Zwischenzustand ("geloescht" mit Fotos, "bestand" ohne Frist), und
    # ein paralleler Wechsel wurde ueberschrieben. LifecycleError -> 409.
    if body.decision == "loeschen":
        # Rollenprüfung 22.09.2026 (RP-454): nicht loeschen, solange zum
        # Fahrzeug noch ein Abholtermin laeuft (Doppel-Abholung: ein zweiter
        # Vertrag kann einen eigenen offenen Termin haben). Vorher stand der
        # Fahrer danach mit einem Termin zu einem geloeschten Fahrzeug da, und
        # der Termin liess sich weder aendern noch stornieren. Bewusst kein
        # stilles Mitstornieren — Fahrer und Verkaeufer sind schon verabredet.
        # Welle B2 (Entscheidung Ahmad 22.09.2026): Der Chef bekommt im 409
        # die Termine (Datum, Uhrzeit, Fahrer) und kann mit
        # termine_stornieren=true bewusst "Offene Termine stornieren und
        # Fahrzeug löschen" waehlen — ueber denselben Storno-Weg wie unter
        # Termine (_termine_stornieren), nie still.
        offene_termine = await _offene_termine(vehicle_id, user["dealer_id"])
        termine_storniert: List[str] = []
        if offene_termine and not body.termine_stornieren:
            raise HTTPException(409, {"msg": TERMINE_OFFEN_TEXT, "code": TERMINE_OFFEN_CODE,
                                      "termine": [{k: t.get(k) for k in
                                                   ("id", "pickup_date", "pickup_time",
                                                    "driver_name")}
                                                  for t in offene_termine]})
        if offene_termine:
            # Erst pruefen, ob das Loeschen aus diesem Status ueberhaupt geht —
            # sonst waeren die Termine storniert und das Fahrzeug bliebe stehen.
            from lifecycle import ALLOWED_TRANSITIONS
            if "geloescht" not in ALLOWED_TRANSITIONS.get(v.get("lifecycle") or "", set()):
                raise HTTPException(409, f"Ein Fahrzeug im Status „{v.get('lifecycle')}“ "
                                         "lässt sich nicht löschen — die Termine bleiben "
                                         "unverändert.")
            termine_storniert = await _termine_stornieren(offene_termine, user)
        # Fotos sofort räumen — Vertrag, Abholbericht, Historie bleiben.
        # Dotted-Paths statt Ganzobjekt: nichts anderes in data wird angefasst.
        leeren = {f"data.{key}": [] for key in ("image_urls", "images", "photos", "pictures")}
        try:
            await set_lifecycle(vehicle_id, user["dealer_id"], "geloescht", user=user,
                                extra_set={**leeren, "deleted_at": now_iso()})
        except LifecycleError as exc:
            raise HTTPException(409, str(exc))
        # Runde 17 (Nr. 283): aktive Inserate zum Fahrzeug (Entwurf,
        # verkaufsbereit, zurueckgezogen ...) enden mit dem Fahrzeug —
        # vorher blieben sie stehen und liessen sich weiter bearbeiten und
        # veroeffentlichen, obwohl das Fahrzeug geloescht war.
        geloeschte_inserate = await _inserate_zum_fahrzeug_schliessen(
            vehicle_id, user)
        # RP-454 (Welle B2): welche Termine mit dem Loeschen storniert wurden
        # (jeder einzelne steht zusaetzlich als termin.aktualisiert im Verlauf).
        await log_activity_sicher(user["dealer_id"], user["id"],
                           "fahrzeug.entscheidung.geloescht", ref=vehicle_id,
                           meta={"inserate_geloescht": geloeschte_inserate,
                                 "termine_storniert": termine_storniert})
        return {"ok": True, "lifecycle": "geloescht",
                "inserate_geloescht": geloeschte_inserate,
                "termine_storniert": termine_storniert}

    if body.decision == "verkaufsentwurf" and not marktplatz_aktiv():
        # Go-Live-Schalter (15.09.2026): "Jetzt inserieren" / "Weiterverkaufen"
        # gibt es erst, wenn der Marktplatz freigeschaltet ist.
        raise HTTPException(503, MARKTPLATZ_GESPERRT)
    # Rollenprüfung 22.09.2026 (RP-085/RP-184/RP-496): nicht zurueck in den
    # Bestand (oder in einen neuen Entwurf), solange ein Inserat
    # verkaufsbereit, live oder reserviert ist. Entwurf und "zurueckgezogen"
    # sind nicht sichtbar und blockieren nicht (create_draft nimmt sie wieder).
    return await _bestand_oder_entwurf(vehicle_id, v, body.decision, user)


async def _bestand_oder_entwurf(vehicle_id: str, v: Dict[str, Any], decision: str,
                                user: Dict[str, Any]) -> Dict[str, Any]:
    """Entscheidung "bestand" (Nur speichern / Frist erneuern) bzw.
    "verkaufsentwurf" fuer ein gelesenes Fahrzeug. Eigene Funktion seit dem
    24.11.2026 (Wunsch Ahmad, Sammeländerung in der Fahrzeugliste): die
    Sammeländerung "Status ändern auf Im Bestand" geht durch GENAU diesen Weg
    — gleiche Prüfungen (Inserat live -> 409, erlaubter Übergang), gleiche
    Frist, gleicher Verlauf je Fahrzeug."""
    live = await db.resale_listings.find_one(
        {"vehicle_id": vehicle_id, "dealer_id": user["dealer_id"],
         "status": {"$in": list(_INSERAT_LIVE)}}, {"_id": 0, "id": 1, "status": 1})
    if live:
        raise HTTPException(409, "Zu diesem Fahrzeug gibt es ein Inserat, das "
                                 f"{_INSERAT_LIVE_TEXT.get(live.get('status'), 'aktiv')} ist — "
                                 "bitte das Inserat zuerst zurückziehen "
                                 "(Inserat öffnen → „Vom Marktplatz nehmen“).")
    target = "bestand" if decision == "bestand" else "verkaufsentwurf"
    extra: Dict[str, Any] = {}
    verlaengert = False
    if decision == "bestand":
        expires = (datetime.now(timezone.utc)
                   + timedelta(days=BESTAND_RETENTION_DAYS)).isoformat()
        extra["bestand.expires_at"] = expires
        if v.get("lifecycle") == "bestand":
            # Rollenprüfung 22.09.2026 (RP-450): "bestand" auf ein Fahrzeug im
            # Bestand = Frist verlaengern (+50 Tage ab heute). Nach Ablauf
            # archiviert der Aufraeumer endgueltig (Fotos weg) — auch wenn das
            # Auto noch auf dem Hof steht. Das Aufnahmedatum bleibt stehen.
            verlaengert = True
            extra["bestand.verlaengert_am"] = now_iso()
        else:
            extra["bestand.saved_at"] = now_iso()
    else:
        # Weiterverkauf: keine automatische Löschfrist; saved_at bleibt,
        # wenn es schon eines gibt.
        expires = None
        extra["bestand.expires_at"] = None
        if not (v.get("bestand") or {}).get("saved_at"):
            extra["bestand.saved_at"] = now_iso()
    try:
        await set_lifecycle(vehicle_id, user["dealer_id"], target, user=user,
                            extra_set=extra)
    except LifecycleError as exc:
        raise HTTPException(409, str(exc))
    aktion = ("fahrzeug.bestand.verlaengert" if verlaengert
              else f"fahrzeug.entscheidung.{decision}")
    await log_activity_sicher(user["dealer_id"], user["id"], aktion, ref=vehicle_id,
                              meta={"expires_at": expires} if verlaengert else None)
    return {"ok": True, "lifecycle": target, "expires_at": expires,
            "verlaengert": verlaengert}


# Termine, die noch laufen (alles ausser diesen Zustaenden) — gleiche Liste
# wie appointments.ABGESCHLOSSEN (kein Import: appointments importiert bestand).
_TERMIN_GESCHLOSSEN = ("abgeholt", "nicht abgeholt", "storniert", "erledigt")


@router.post("/vehicles/{vehicle_id}/entfernen")
async def vehicle_fuer_sucher_entfernen(vehicle_id: str, user=Depends(current_firma)):
    """Wunsch Ahmad 14.09.2026: "Wenn ein Sucher ein Auto loescht, soll das
    nur bei ihm loeschen — nicht beim Chef."

    Der Sucher nimmt das Fahrzeug aus SEINEM Bereich: ist er Hauptbearbeiter,
    geht das Fahrzeug an den Chef (Hauptaccount) ueber; als Mitbearbeiter wird
    er ausgetragen. Fahrzeug, Fotos, Vertraege, Termine, Protokolle und
    Berichte bleiben fuer den Chef unveraendert (kein Lebenszyklus-Wechsel).
    Der Chef selbst loescht weiter ueber POST /vehicles/{id}/decision
    (Fotos weg, Lebenszyklus "geloescht"). Ein noch laufender Termin des
    Suchers blockiert (409) — sonst verloere er seinen eigenen Vorgang aus
    der Akte, waehrend der Fahrer unterwegs ist."""
    if not ist_sucher(user):
        raise HTTPException(400, "Der Chef löscht ein Fahrzeug über „Löschen“ in der "
                                 "Fahrzeugakte — dieser Weg ist für Sucher gedacht")
    v = await db.vehicles.find_one(
        {"id": vehicle_id, **fahrzeug_bereich(user)},
        {"_id": 0, "id": 1, "owner_user_id": 1, "mitbearbeiter_ids": 1})
    if not v:
        raise HTTPException(404, "Fahrzeug nicht gefunden")
    # Runde 12 (15.09.2026, Nr. 36): auch Termine, die der Chef zu einem Vertrag
    # dieses Suchers angelegt hat, zaehlen als laufender eigener Vorgang.
    eigene_vertraege = [c["id"] async for c in db.generated_pdfs.find(
        {"dealer_id": user["dealer_id"], "vehicle_id": vehicle_id, "user_id": user["id"]},
        {"_id": 0, "id": 1})]
    # Rollenprüfung 22.09.2026 (RP-276a): Termine haengen seit dem Umbau
    # Kaufvorgaenge auch NUR ueber kaufvorgang_id am Sucher (Vertrag
    # geloescht, Termin vom Chef angelegt). Die zaehlen genauso als eigener,
    # noch laufender Vorgang.
    eigene_vorgaenge = [k["id"] async for k in db.kaufvorgaenge.find(
        {"dealer_id": user["dealer_id"], "vehicle_id": vehicle_id, "user_id": user["id"]},
        {"_id": 0, "id": 1})]
    termin_wege: List[Dict[str, Any]] = [{"created_by": user["id"]}]
    if eigene_vertraege:
        termin_wege.append({"contract_id": {"$in": eigene_vertraege}})
    if eigene_vorgaenge:
        termin_wege.append({"kaufvorgang_id": {"$in": eigene_vorgaenge}})
    offen = await db.appointments.count_documents(
        {"dealer_id": user["dealer_id"], "vehicle_id": vehicle_id,
         "status": {"$nin": list(_TERMIN_GESCHLOSSEN)},
         "$or": termin_wege},
        limit=1)
    if offen:
        raise HTTPException(409, "Zu diesem Fahrzeug läuft noch ein Termin von dir — "
                                 "erst abschließen oder stornieren")
    war_besitzer = v.get("owner_user_id") == user["id"]
    filt: Dict[str, Any] = {"id": vehicle_id, "dealer_id": user["dealer_id"]}
    upd: Dict[str, Any] = {"$pull": {"mitbearbeiter_ids": user["id"]},
                           "$set": {"updated_at": now_iso()}}
    neuer_besitzer = v.get("owner_user_id")
    # Runde 19 (16.09.2026, Termine Nr. 3/4): Entfernen ist eine Uebergabe
    # des GANZEN Vorgangs an den Chef — auch die eigenen Vertraege, Kauf-
    # vorgaenge und (abgeschlossenen) Termine zu diesem Fahrzeug. Vorher
    # blieben sie beim Sucher; oeffnete der Chef einen Termin zur Korrektur
    # wieder, sah der Sucher ihn samt Verkaeuferdaten erneut.
    firma = await db.dealers.find_one({"id": user["dealer_id"]}, {"_id": 0, "user_id": 1})
    chef_id = (firma or {}).get("user_id")
    if not chef_id:
        raise HTTPException(409, "Firma ohne Hauptaccount — bitte den Betreiber informieren")
    if war_besitzer:
        filt["owner_user_id"] = user["id"]          # CAS auf den gelesenen Besitzer
        upd["$set"].update({"owner_user_id": chef_id, "entfernt_von_sucher": user["id"],
                            "entfernt_von_sucher_am": now_iso()})
        neuer_besitzer = chef_id
    res = await db.vehicles.update_one(filt, upd)
    if res.matched_count == 0:
        # Wunsch Ahmad 21.09.2026 (R1-01): der Chef haengt nichts mehr um —
        # der Besitzer kann sich nur noch durch die Pool-Begrenzung oder ein
        # paralleles Entfernen geaendert haben.
        raise HTTPException(409, "Fahrzeug wurde zwischenzeitlich geändert — bitte neu laden")
    uebergabe = await vorgang_uebergeben(user["dealer_id"], vehicle_id, user["id"], chef_id)
    await log_activity_sicher(user["dealer_id"], user["id"], "fahrzeug.sucher.entfernt",
                              ref=vehicle_id, meta={"an_chef": war_besitzer,
                                                    "owner_user_id": neuer_besitzer,
                                                    **({"uebergabe": uebergabe} if uebergabe else {})})
    # Pruefbericht 20.09.2026 (R1-02): War der Sucher nur Mitbearbeiter, blieb
    # der Hauptbearbeiter ein KOLLEGE — seine Konto-ID ging hier an den Sucher
    # zurueck, obwohl die Maskierungsregel genau das verbietet. Die Kennung
    # steht nur noch in der Antwort, wenn das Fahrzeug an den Chef ging.
    antwort = {"ok": True, "entfernt": True, "an_chef": war_besitzer, "uebergabe": uebergabe}
    if war_besitzer:
        antwort["owner_user_id"] = neuer_besitzer
    return antwort


async def _inserate_zum_fahrzeug_schliessen(vehicle_id: str, user: Dict[str, Any]) -> List[str]:
    """Runde 17 (Nr. 283): alle noch aktiven resale_listings des Fahrzeugs
    (Status nicht verkauft/geloescht) auf "geloescht" setzen, offene
    Kaufanfragen dazu beenden und je Inserat einen Audit-Eintrag schreiben.
    Liefert die Inserats-IDs. Wirft nicht — das Fahrzeug ist bereits
    geloescht, der Aufraeumer (marktplatz_rotieren) raeumt Reste spaeter."""
    jetzt = now_iso()
    ids: List[str] = []
    try:
        async for l in db.resale_listings.find(
                {"vehicle_id": vehicle_id, "dealer_id": user["dealer_id"],
                 "status": {"$nin": ["verkauft", "geloescht"]}},
                {"_id": 0, "id": 1, "status": 1}):
            res = await db.resale_listings.update_one(
                {"id": l["id"], "dealer_id": user["dealer_id"],
                 "status": {"$nin": ["verkauft", "geloescht"]}},
                {"$set": {"status": "geloescht", "deleted_at": jetzt,
                          "updated_at": jetzt,
                          "geloescht_grund": "fahrzeug_geloescht"}})
            if res.matched_count == 0:
                continue
            ids.append(l["id"])
            try:
                # Import in der Funktion: resale.py importiert dieses Modul.
                from routes.resale import _anfragen_schliessen
                await _anfragen_schliessen(l["id"], "fahrzeug_geloescht",
                                           auch_akzeptierte=True)
            except Exception:  # noqa: BLE001
                logging.getLogger("autohandel").exception(
                    "Kaufanfragen zu Inserat %s nicht geschlossen", l["id"])
            await log_activity_sicher(
                user["dealer_id"], user["id"], "inserat.geloescht", ref=l["id"],
                meta={"war_status": l.get("status"), "grund": "fahrzeug_geloescht",
                      "vehicle_id": vehicle_id})
    except Exception as exc:  # noqa: BLE001
        logging.getLogger("autohandel").exception(
            "Inserate zu geloeschtem Fahrzeug %s nicht geschlossen", vehicle_id)
        # Pruefung 14.09.2026 (M2): nicht still — Betriebsalarm, und der
        # Aufraeum-Job (cleanup_service.inserate_geloeschter_fahrzeuge_schliessen)
        # schliesst die Inserate nach.
        import betrieb as _betrieb
        await _betrieb.alarm(db, "inserat_zu_geloeschtem_fahrzeug_offen", ref=vehicle_id,
                             dealer_id=user["dealer_id"], fehler=str(exc)[:300])
    return ids


@router.put("/vehicles/{vehicle_id}/bestand")
async def update_bestand(vehicle_id: str, body: BestandUpdateIn,
                         user=Depends(current_haendler)):
    v = await db.vehicles.find_one(
        {"id": vehicle_id, "dealer_id": user["dealer_id"]},
        {"_id": 0, "bestand": 1, "lifecycle": 1})
    if not v:
        raise HTTPException(404, "Fahrzeug nicht gefunden")
    # Nachpruefung Runde 14 (Nr. 39): Standort/Notizen/Kosten eines
    # verkauften oder archivierten Fahrzeugs sind Teil der Akte — eingefroren.
    _abgeschlossen_sperren(
        v, "Fahrzeug ist abgeschlossen — Bestandsdaten sind eingefroren")
    b = v.get("bestand") or {}
    # Runde 17 (Nr. 275): nur die GESENDETEN Felder per Dotted-Path schreiben
    # (statt das ganze bestand-Objekt aus dem gelesenen Stand zurueck) — zwei
    # parallele PUTs (Standort / Kosten) loeschten sich sonst gegenseitig.
    # Der Write prueft den Lifecycle mit: ein Verkauf zwischen Lesen und
    # Schreiben wird nicht mehr ueberschrieben (matched 0 -> 409).
    update: Dict[str, Any] = {"updated_at": now_iso()}
    if body.location is not None:
        b["location"] = update["bestand.location"] = body.location.strip()
    if body.notes is not None:
        b["notes"] = update["bestand.notes"] = body.notes.strip()
    kosten_alt = sum(float(c.get("amount") or 0) for c in (b.get("costs") or [])
                     if isinstance(c, dict) and math.isfinite(float(c.get("amount") or 0)))
    if body.costs is not None:
        b["costs"] = update["bestand.costs"] = _clean_costs(body.costs)
    # Rollenprüfung 22.09.2026 (RP-461): jeder Write bekommt einen neuen
    # Stand; wer einen Stand mitschickt, schreibt nur, wenn er noch gilt.
    b["stand"] = update["bestand.stand"] = uuid.uuid4().hex[:16]
    filt: Dict[str, Any] = {"id": vehicle_id, "dealer_id": user["dealer_id"],
                            "lifecycle": {"$nin": list(_ABGESCHLOSSEN)}}
    if body.stand is not None:
        # "" = die Akte kannte noch keinen Stand ($in [None] trifft auch das
        # fehlende Feld).
        filt["bestand.stand"] = body.stand if body.stand else {"$in": [None, ""]}
    res = await db.vehicles.update_one(filt, {"$set": update})
    if res.matched_count == 0:
        jetzt = await db.vehicles.find_one(
            {"id": vehicle_id, "dealer_id": user["dealer_id"]}, {"_id": 0, "lifecycle": 1})
        if body.stand is not None and jetzt and jetzt.get("lifecycle") not in _ABGESCHLOSSEN:
            raise HTTPException(409, "Standort, Notizen oder Kosten wurden inzwischen an "
                                     "anderer Stelle geändert (zweiter Tab oder anderes "
                                     "Gerät) — bitte neu laden. Deine Eingaben bleiben "
                                     "beim Neuladen stehen.")
        raise HTTPException(409, "Fahrzeug wurde zwischenzeitlich abgeschlossen — "
                                 "Bestandsdaten sind eingefroren, bitte neu laden")
    # Runde 15 (Nr. 5): Kosten beeinflussen die Marge — wer wann aus 500 EUR
    # Aufbereitung 5.000 gemacht hat, muss nachvollziehbar bleiben.
    felder = [f for f in ("location", "notes", "costs") if getattr(body, f) is not None]
    await log_activity_sicher(user["dealer_id"], user["id"], "bestand.geaendert", ref=vehicle_id,
                       meta={"felder": felder,
                             "kosten_summe_alt": round(kosten_alt, 2),
                             "kosten_summe_neu": round(sum(c["amount"] for c in (b.get("costs") or [])), 2)})
    return {"ok": True, "bestand": b}


# =========================================================
#                     BESTANDSLISTE
# =========================================================
# Hoechstzahl der in einer Antwort gelieferten Fahrzeuge. Die Oberflaeche
# meldet, wenn mehr vorhanden sind (Runde 27).
BESTAND_MAX = 500


# Runde 32 (12.09.2026, Wunsch Ahmad): "Im Bestand nur Autos, zu denen ein
# Vertrag gespeichert oder verschickt wurde — mehr nicht", dazu die von Hand
# hinzugefuegten (Entscheidung Ahmad). Nur verglichene Autos gehoeren nicht
# hierher; Sucher finden sie weiter unter "Fahrzeuge".
#
# SUCHER sehen ein Auto, wenn
#   * ER einen Vertrag dazu gespeichert hat (Vertragsdokument vorhanden, nicht
#     im Grabstein; Verschicken setzt Speichern voraus), oder
#   * SEIN Kauf abgeholt wurde (Kaufvorgang "abgeholt" — haelt das Auto auch,
#     wenn der Vertrag danach geloescht wird), oder
#   * es von Hand angelegt ist UND ihm gehoert (Altdaten: vor dem
#     21.09.2026 vom Chef zugewiesen — das Umhaengen ist seitdem entfallen).
# Besitz allein reicht nicht: Besitzer wird schon, wer ein Inserat als Erster
# VERGLEICHT — sonst stuende das Auto, das ein Kollege gekauft hat, auch bei
# ihm im Bestand.
#
# Der CHEF sieht alles davon fuer die ganze Firma und zusaetzlich jedes Auto,
# das per Termin abgeholt wurde oder auf dem Hof bzw. im Weiterverkauf steht
# (NACH_ABHOLUNG) — auch nach einer Vertragsloeschung und fuer Altbestand ohne
# Vorgang. Er entscheidet dort, was mit dem Auto passiert.
#
# Ein vor der Abholung von Hand geloeschter Vertrag zaehlt nicht: der Kauf wurde
# zurueckgezogen. Die Fristloeschung storniert offene Vorgaenge ebenfalls; ein
# bereits abgeholtes Auto bleibt ueber seinen Vorgang bzw. beim Chef stehen.
#
# Gegenpruefung 12.09.2026 (zweimal, bestaetigte Befunde): Die erste Fassung
# zaehlte nur Kaufvorgaenge (abgeholtes Auto verschwand nach Vertragsloeschung,
# alte Loeschungen ohne Markierung hielten Autos faelschlich fest, Vertrag ohne
# Vorgang fehlte). Die zweite liess Sucher ueber Besitz oder einen stornierten
# eigenen Vorgang fremde Kaeufe sehen, und ein per Terminplaner abgeholtes Auto
# nach Vertragsloeschung fehlte beim Chef.
NACH_ABHOLUNG = ("abgeholt", "bestand", "verkaufsentwurf", "verkaufsbereit",
                 "veroeffentlicht", "reserviert", "verkauft")
TERMIN_ABGEHOLT = ("abgeholt", "erledigt")


async def bestand_filter(user) -> Dict[str, Any]:
    """Mongo-Filter fuer den Bestand. fahrzeug_bereich enthaelt fuer Sucher
    selbst ein $or — deshalb ueber $and verknuepft, nie per dict-Merge."""
    from deps import ist_sucher
    from routes.contracts import _vertrag_bereich
    sucher = ist_sucher(user)
    leer = {"$nin": [None, ""]}
    ids = set(await db.generated_pdfs.distinct(
        "vehicle_id", {**_vertrag_bereich(user), "vehicle_id": leer}))
    abholungen: Dict[str, Any] = {"dealer_id": user["dealer_id"], "vehicle_id": leer,
                                  "status": "abgeholt"}
    if sucher:
        abholungen["user_id"] = user["id"]
    ids |= set(await db.kaufvorgaenge.distinct("vehicle_id", abholungen))
    if sucher:
        wege = [{"source": "manuell", "owner_user_id": user["id"]},
                {"id": {"$in": sorted(ids)}}]
    else:
        ids |= set(await db.appointments.distinct(
            "vehicle_id", {"dealer_id": user["dealer_id"], "vehicle_id": leer,
                           "status": {"$in": list(TERMIN_ABGEHOLT)}}))
        wege = [{"source": "manuell"},
                {"id": {"$in": sorted(ids)}},
                {"lifecycle": {"$in": list(NACH_ABHOLUNG)}}]
    return {"$and": [fahrzeug_bereich(user), {"$or": wege}],
            "lifecycle": {"$nin": ["geloescht"]}}


# ---------------------------------------------------------------------
# Wunsch Ahmad 24.11.2026: Fahrzeugliste mit Filter "Gekauft im Zeitraum",
# Anzahl + "Einkauf gesamt" ueber ALLE Treffer (nicht nur die Seite) und
# Sammeländerung des Status.
# ---------------------------------------------------------------------
ZEITZONE = ZoneInfo("Europe/Berlin")
ZEITRAEUME = ("heute", "diese_woche", "letzte_woche", "dieser_monat", "letzter_monat",
              "eigener")
# Laengster eigener Zeitraum (Tippfehler wie 0202 statt 2026 abfangen).
ZEITRAUM_MAX_TAGE = 3660
# Fahrzeuge in diesen Zustaenden zaehlen in "Einkauf gesamt" nicht mit —
# der Kauf kam nicht zustande.
_OHNE_EINKAUF = ("storniert", "nicht_abgeholt")


def _tag(wert: Optional[str], feld: str) -> Optional[date]:
    if not wert:
        return None
    try:
        return date.fromisoformat(str(wert)[:10])
    except ValueError:
        raise HTTPException(422, f"{feld}: bitte ein Datum wie 2026-11-24 angeben")


def zeitraum_grenzen(zeitraum: Optional[str], von: Optional[str] = None,
                     bis: Optional[str] = None, *,
                     jetzt: Optional[datetime] = None
                     ) -> Optional[Tuple[Optional[datetime], Optional[datetime]]]:
    """Grenzen [Anfang, Ende) in UTC fuer "Gekauft im Zeitraum".

    Gerechnet wird in Europe/Berlin (Tageswechsel um Mitternacht deutscher
    Zeit, Sommer-/Winterzeit eingeschlossen), die Woche beginnt am Montag.
    "eigener": von/bis als Kalendertage, beide einschliesslich; einer darf
    fehlen (offen). None = kein Zeitraum-Filter."""
    if not zeitraum:
        return None
    if zeitraum not in ZEITRAEUME:
        raise HTTPException(422, "Unbekannter Zeitraum")
    heute = (jetzt or datetime.now(timezone.utc)).astimezone(ZEITZONE).date()
    anfang: Optional[date]
    ende: Optional[date]
    if zeitraum == "heute":
        anfang, ende = heute, heute + timedelta(days=1)
    elif zeitraum in ("diese_woche", "letzte_woche"):
        anfang = heute - timedelta(days=heute.weekday())
        if zeitraum == "letzte_woche":
            anfang -= timedelta(days=7)
        ende = anfang + timedelta(days=7)
    elif zeitraum == "dieser_monat":
        anfang = heute.replace(day=1)
        ende = (anfang + timedelta(days=32)).replace(day=1)
    elif zeitraum == "letzter_monat":
        ende = heute.replace(day=1)
        anfang = (ende - timedelta(days=1)).replace(day=1)
    else:
        anfang, bis_tag = _tag(von, "Von"), _tag(bis, "Bis")
        if anfang is None and bis_tag is None:
            raise HTTPException(422, "Eigener Zeitraum: bitte „von“ oder „bis“ angeben")
        if anfang and bis_tag and bis_tag < anfang:
            raise HTTPException(422, "Eigener Zeitraum: „bis“ liegt vor „von“")
        if anfang and bis_tag and (bis_tag - anfang).days > ZEITRAUM_MAX_TAGE:
            raise HTTPException(422, "Eigener Zeitraum: höchstens 10 Jahre")
        ende = bis_tag + timedelta(days=1) if bis_tag else None

    def _utc(t: Optional[date]) -> Optional[datetime]:
        if t is None:
            return None
        return datetime.combine(t, time(0), tzinfo=ZEITZONE).astimezone(timezone.utc)

    return _utc(anfang), _utc(ende)


def einkaufspreis_sichtbar(user) -> bool:
    """Wer bekommt Kaufpreise (und damit "Einkauf gesamt")? Entscheidung
    Ahmad 13.09.2026: Chef UND Sucher — Sucher nur die eigenen (die Summe
    rechnet fuer Sucher deshalb nur ueber eigene Kaufvorgaenge). Jede andere
    Rolle bekommt den Betrag gar nicht erst."""
    return (user or {}).get("role") in ("dealer", "sucher")


def _anreicherung(user) -> List[Dict[str, Any]]:
    """Aggregations-Stufen: Kaufdatum (gekauft_am) und Kaufpreis (_preis) je
    Fahrzeug — im Bereich des Kontos (Sucher: nur eigene Vorgaenge/Vertraege).

    gekauft_am = aeltester nicht stornierter Kaufvorgang; ohne Vorgang der
    aelteste Vertrag; ohne beides (von Hand angelegt, Altbestand) das
    Anlagedatum bzw. die Aufnahme in den Bestand.
    Kaufpreis Chef: realisierter Preis am Fahrzeug, sonst der Preis des
    juengsten Vorgangs. Sucher: Preis seines abgeholten Vorgangs, sonst
    seines juengsten (wie kaufvorgang.einkauf_fuer_sucher_maskieren —
    nie der Preis eines Kollegen oder ein vom Chef eingetragener)."""
    sucher = ist_sucher(user)
    gleich = {"$expr": {"$and": [{"$eq": ["$vehicle_id", "$$vid"]},
                                 {"$eq": ["$dealer_id", "$$did"]}]}}
    kv_match: Dict[str, Any] = {**gleich, "status": {"$ne": "storniert"}}
    vt_match: Dict[str, Any] = {**gleich, "loeschung.status": {"$ne": "laeuft"}}
    if sucher:
        kv_match["user_id"] = user["id"]
        vt_match["user_id"] = user["id"]
    kv_preis = {"$arrayElemAt": [{"$filter": {"input": "$_kv.purchase_price",
                                              "cond": {"$ne": ["$$this", None]}}}, 0]}
    if sucher:
        eigener_abgeholt = {"$arrayElemAt": [{"$map": {
            "input": {"$filter": {"input": "$_kv",
                                  "cond": {"$eq": ["$$this.id", "$abgeholt_kaufvorgang_id"]}}},
            "in": "$$this.purchase_price"}}, 0]}
        preis: Any = {"$ifNull": [eigener_abgeholt, kv_preis]}
    else:
        preis = {"$ifNull": ["$purchase_price", kv_preis]}
    ersatz = {"$cond": [{"$eq": ["$source", "manuell"]},
                        {"$ifNull": ["$created_at", "$bestand.saved_at"]},
                        {"$ifNull": ["$bestand.saved_at",
                                     {"$ifNull": ["$created_at", "$lifecycle_changed_at"]}]}]}
    return [
        {"$lookup": {"from": "kaufvorgaenge", "let": {"vid": "$id", "did": "$dealer_id"},
                     "pipeline": [{"$match": kv_match}, {"$sort": {"created_at": -1}},
                                  {"$project": {"_id": 0, "id": 1, "created_at": 1,
                                                "purchase_price": 1}}],
                     "as": "_kv"}},
        {"$lookup": {"from": "generated_pdfs", "let": {"vid": "$id", "did": "$dealer_id"},
                     "pipeline": [{"$match": vt_match},
                                  {"$project": {"_id": 0, "created_at": 1}}],
                     "as": "_vt"}},
        {"$addFields": {
            "gekauft_am": {"$ifNull": [{"$min": "$_kv.created_at"},
                                       {"$ifNull": [{"$min": "$_vt.created_at"}, ersatz]}]},
            "_preis": preis,
        }},
        {"$addFields": {"_gekauft_dt": {"$convert": {"input": "$gekauft_am", "to": "date",
                                                     "onError": None, "onNull": None}}}},
    ]


def _zeitraum_match(grenzen) -> List[Dict[str, Any]]:
    if not grenzen:
        return []
    anfang, ende = grenzen
    bed: Dict[str, Any] = {"$ne": None}
    if anfang is not None:
        bed["$gte"] = anfang
    if ende is not None:
        bed["$lt"] = ende
    return [{"$match": {"_gekauft_dt": bed}}]


async def _bestand_abfrage(user, lifecycle: Optional[str], source: Optional[str]):
    """(Grundfilter, Filter mit Lifecycle/Quelle) — gemeinsam fuer Liste und
    "Alle Treffer auswählen"."""
    # Runde 16: Sucher sehen nur eigene Fahrzeuge (owner_user_id).
    # Runde 32: und davon nur die mit Kaufvertrag oder von Hand hinzugefuegte.
    grundfilter = await bestand_filter(user)
    query: Dict[str, Any] = dict(grundfilter)
    # Runde 17 (Nr. 285): ?lifecycle=geloescht hob den Ausschluss auf —
    # geloeschte Fahrzeuge (Fotos weg, Akte eingefroren) sind hier nie Thema.
    if lifecycle and lifecycle != "geloescht":
        query["lifecycle"] = lifecycle
    if source in ("plattform", "manuell"):
        query["source"] = source
    return grundfilter, query


def _seite_lesen(seite, je_seite) -> Tuple[int, int, bool]:
    """(skip, limit, geblaettert). Ohne Angaben wie bisher: die neuesten
    BESTAND_MAX Fahrzeuge (Runde 27)."""
    if seite is None and je_seite is None:
        return 0, BESTAND_MAX, False
    try:
        s = max(1, int(seite or 1))
        j = min(BESTAND_MAX, max(1, int(je_seite or 50)))
    except (TypeError, ValueError):
        raise HTTPException(422, "Seite und Seitengröße bitte als Zahl angeben")
    return (s - 1) * j, j, True


@router.get("/bestand")
async def list_bestand(user=Depends(current_firma),
                       lifecycle: Optional[str] = None,
                       source: Optional[str] = None,
                       zeitraum: Optional[str] = None,
                       von: Optional[str] = None,
                       bis: Optional[str] = None,
                       seite: Optional[int] = None,
                       je_seite: Optional[int] = None):
    """Fahrzeugbestand des Händlers mit Lifecycle-/Quellen-/Zeitraum-Filter.
    Liefert zusätzlich Zählergruppen für die Dashboard-Kacheln und eine
    Zusammenfassung über ALLE Treffer (Anzahl, Einkauf gesamt)."""
    grundfilter, query = await _bestand_abfrage(user, lifecycle, source)
    grenzen = zeitraum_grenzen(zeitraum, von, bis)
    skip, limit, geblaettert = _seite_lesen(seite, je_seite)
    preis_sichtbar = einkaufspreis_sichtbar(user)
    zaehlt = {"$not": [{"$in": ["$lifecycle", list(_OHNE_EINKAUF)]}]}
    # Runde 27 (12.09.2026, Pruefbefund): Die Liste endete still bei 500
    # Fahrzeugen — die Gesamtzahl wird mitgeliefert. Wunsch Ahmad 24.11.2026:
    # Anzahl und Summe kommen aus DERSELBEN Aggregation wie die Seite, also
    # immer ueber alle Treffer. Die Seite liefert nur IDs (Ganzdokumente in
    # $facet koennten die 16-MB-Grenze einer Antwort sprengen); die Fahrzeuge
    # werden danach gezielt gelesen.
    pipeline = [{"$match": query}, *_anreicherung(user), *_zeitraum_match(grenzen),
                {"$facet": {
                    "seite": [{"$sort": {"lifecycle_changed_at": -1, "id": 1}},
                              {"$skip": skip}, {"$limit": limit},
                              {"$project": {"_id": 0, "id": 1, "gekauft_am": 1}}],
                    "summe": [{"$group": {
                        "_id": None, "n": {"$sum": 1},
                        "eur": {"$sum": {"$cond": [zaehlt, {"$ifNull": ["$_preis", 0]}, 0]}},
                        "ohne_preis": {"$sum": {"$cond": [
                            {"$and": [zaehlt, {"$eq": [{"$ifNull": ["$_preis", None]}, None]}]},
                            1, 0]}},
                    }}],
                }}]
    ergebnis = await db.vehicles.aggregate(pipeline, allowDiskUse=True).to_list(1)
    facet = ergebnis[0] if ergebnis else {"seite": [], "summe": []}
    seite_ids = [r["id"] for r in facet.get("seite") or []]
    kauf_am = {r["id"]: r.get("gekauft_am") for r in facet.get("seite") or []}
    summe = (facet.get("summe") or [{}])[0] if facet.get("summe") else {}
    gesamt = int(summe.get("n") or 0)

    docs: Dict[str, Dict[str, Any]] = {}
    if seite_ids:
        async for d in db.vehicles.find({"id": {"$in": seite_ids},
                                         "dealer_id": user["dealer_id"]}, {"_id": 0}):
            docs[d["id"]] = d
    items = [docs[i] for i in seite_ids if i in docs]
    for it in items:
        it["gekauft_am"] = kauf_am.get(it["id"])
    await besitzer_anreichern(user, items)
    # Runde 23 (11.09.2026, Befund A): Sucher sehen nur den eigenen Einkaufspreis.
    import kaufvorgang as _kv
    await _kv.einkauf_fuer_sucher_maskieren(user, items)
    if not preis_sichtbar:
        for it in items:
            for feld in _kv.EINKAUF_FELDER:
                it.pop(feld, None)

    # Restlaufzeit für Bestandsfahrzeuge berechnen (Warnstufen im Frontend).
    now = datetime.now(timezone.utc)
    for it in items:
        exp = (it.get("bestand") or {}).get("expires_at")
        if it.get("lifecycle") == "bestand" and exp:
            # Nachpruefung Runde 14 (Nr. 93): naiv -> UTC, TypeError mitfangen.
            try:
                it["retention_days_left"] = _resttage(exp, now)
            except (ValueError, TypeError):
                pass

    # Die Zaehler der Filterknoepfe folgen dem Zeitraum (sonst passen Knopf
    # und Liste nicht zusammen), nicht aber Lifecycle/Quelle (wie bisher).
    zaehl_pipeline: List[Dict[str, Any]] = [{"$match": grundfilter}]
    if grenzen:
        zaehl_pipeline += [*_anreicherung(user), *_zeitraum_match(grenzen)]
    zaehl_pipeline.append({"$group": {"_id": "$lifecycle", "n": {"$sum": 1}}})
    counts: Dict[str, int] = {}
    async for row in db.vehicles.aggregate(zaehl_pipeline, allowDiskUse=True):
        counts[row["_id"] or "unbekannt"] = row["n"]

    zusammenfassung: Dict[str, Any] = {"anzahl": gesamt}
    if preis_sichtbar:
        zusammenfassung["einkauf_gesamt"] = round(float(summe.get("eur") or 0), 2)
        zusammenfassung["ohne_preis"] = int(summe.get("ohne_preis") or 0)
    antwort: Dict[str, Any] = {
        "items": items, "counts": counts, "gesamt": gesamt,
        "gekuerzt": (not geblaettert) and gesamt > len(items),
        "zusammenfassung": zusammenfassung,
    }
    if geblaettert:
        antwort.update({"seite": skip // limit + 1, "je_seite": limit})
    if grenzen:
        anfang, ende = grenzen
        antwort["zeitraum"] = {"name": zeitraum,
                               "von": anfang.isoformat() if anfang else None,
                               "bis": ende.isoformat() if ende else None}
    return antwort


@router.get("/bestand/ids")
async def bestand_ids(user=Depends(current_haendler),
                      lifecycle: Optional[str] = None,
                      source: Optional[str] = None,
                      zeitraum: Optional[str] = None,
                      von: Optional[str] = None,
                      bis: Optional[str] = None):
    """Wunsch Ahmad 24.11.2026: "Alle Treffer auswählen" — die IDs ALLER
    Fahrzeuge unter den aktuellen Filtern (gleiche Regeln wie die Liste),
    hoechstens SAMMEL_MAX; die Gesamtzahl steht dabei. Nur der Chef (die
    Sammeländerung ist Chefsache)."""
    _, query = await _bestand_abfrage(user, lifecycle, source)
    grenzen = zeitraum_grenzen(zeitraum, von, bis)
    pipeline: List[Dict[str, Any]] = [{"$match": query}]
    if grenzen:
        pipeline += [*_anreicherung(user), *_zeitraum_match(grenzen)]
    pipeline.append({"$facet": {
        "ids": [{"$sort": {"lifecycle_changed_at": -1, "id": 1}}, {"$limit": SAMMEL_MAX},
                {"$project": {"_id": 0, "id": 1}}],
        "n": [{"$count": "n"}]}})
    ergebnis = await db.vehicles.aggregate(pipeline, allowDiskUse=True).to_list(1)
    facet = ergebnis[0] if ergebnis else {}
    ids = [r["id"] for r in facet.get("ids") or []]
    gesamt = int(((facet.get("n") or [{}])[0]).get("n") or 0) if facet.get("n") else 0
    return {"ids": ids, "gesamt": gesamt, "gekuerzt": gesamt > len(ids),
            "max": SAMMEL_MAX}


# ---------------------------------------------------------------------
# Sammeländerung (Wunsch Ahmad 24.11.2026)
# ---------------------------------------------------------------------
SAMMEL_MAX = 500
#: Ziele der Sammeländerung — nur Zustaende, die der Chef heute schon von
#: Hand setzt bzw. die lifecycle.ALLOWED_TRANSITIONS als Handschritt kennt.
#: Bewusst NICHT: gekauft/abgeholt/verkauft usw. — die entstehen nur aus
#: Vertrag, Termin, Protokoll oder Inserat ("abgeholt nie ohne Protokoll").
#: "geloescht" bleibt Einzelentscheidung in der Akte (offene Termine fragen nach).
SAMMEL_ZIELE = ("bestand", "archiviert", "storniert", "verglichen")
SAMMEL_ZIEL_TEXT = {"bestand": "Im Bestand", "archiviert": "Archiviert",
                    "storniert": "Storniert", "verglichen": "Wieder aktiv (verglichen)"}
#: Anzeigenamen der Zustände für Übersprung-Gründe (wie frontend/src/lib/fahrzeugStatus.js).
ZUSTAND_TEXT = {
    "gefunden": "Gefunden", "geloescht": "Gelöscht", "verglichen": "Verglichen",
    "besichtigung": "Besichtigung", "verhandlung": "Verhandlung",
    "vertrag_erstellt": "Vertrag erstellt", "gekauft": "Gekauft",
    "abholung_geplant": "Abholung geplant", "abgeholt": "Abgeholt", "bestand": "Im Bestand",
    "verkaufsentwurf": "Verkaufsentwurf", "verkaufsbereit": "Verkaufsbereit",
    "veroeffentlicht": "Veröffentlicht", "reserviert": "Reserviert", "verkauft": "Verkauft",
    "nicht_abgeholt": "Nicht abgeholt", "storniert": "Storniert", "archiviert": "Archiviert",
}


def _zustand(z) -> str:
    return ZUSTAND_TEXT.get(z or "", z or "ohne Status")


class SammelStatusIn(BaseModel):
    ids: List[Annotated[str, StringConstraints(min_length=1, max_length=120)]] = Field(
        min_length=1, max_length=SAMMEL_MAX)
    ziel: Literal["bestand", "archiviert", "storniert", "verglichen"]


def _fehlertext(exc: Exception) -> str:
    detail = getattr(exc, "detail", None)
    if isinstance(detail, dict):
        detail = detail.get("msg")
    return str(detail or exc)[:300] or "Nicht möglich"


async def _status_einzeln(vehicle_id: str, ziel: str, user: Dict[str, Any]) -> Dict[str, Any]:
    """Ein Fahrzeug der Sammeländerung. Prueft Firma (dealer_id), erlaubten
    Uebergang (lifecycle.ALLOWED_TRANSITIONS, ohne den Rueckweg aus dem
    Verkauf) und die Sperren der Einzelwege; wirft HTTPException mit dem
    Grund, wenn das Fahrzeug uebersprungen wird."""
    from lifecycle import ALLOWED_TRANSITIONS
    from kaufvorgang import OFFEN as KV_OFFEN
    v = await db.vehicles.find_one(
        {"id": vehicle_id, "dealer_id": user["dealer_id"], "lifecycle": {"$ne": "geloescht"}},
        {"_id": 0, "id": 1, "lifecycle": 1, "status": 1, "bestand": 1, "data": 1})
    if v is None:
        raise HTTPException(404, "Fahrzeug nicht gefunden (andere Firma oder gelöscht)")
    von = v.get("lifecycle") or "verglichen"
    # Bestand -> Bestand ist "Frist erneuern" und bleibt erlaubt.
    if von != ziel and ziel not in ALLOWED_TRANSITIONS.get(von, set()):
        raise HTTPException(409, f"Von „{_zustand(von)}“ nach „{_zustand(ziel)}“ ist nicht erlaubt")
    if ziel == "bestand":
        # Derselbe Weg wie "Nur speichern" / "Frist erneuern" (gleiche
        # Sperren, gleiche Frist, gleicher Verlauf).
        r = await _bestand_oder_entwurf(vehicle_id, v, "bestand", user)
        return {"id": vehicle_id, "von": von, "nach": "bestand",
                "verlaengert": bool(r.get("verlaengert"))}
    if von == ziel:
        raise HTTPException(409, f"Steht schon auf „{SAMMEL_ZIEL_TEXT.get(ziel, ziel)}“")
    extra: Dict[str, Any] = {}
    if ziel == "storniert":
        # Keine Abkuerzung: der Fahrzeugstatus fasst die Kaufvorgaenge
        # zusammen. Laeuft noch ein Vertrag oder Termin, wird dort storniert.
        if await db.kaufvorgaenge.count_documents(
                {"dealer_id": user["dealer_id"], "vehicle_id": vehicle_id,
                 "status": {"$in": list(KV_OFFEN)}}, limit=1):
            raise HTTPException(409, "Kaufvertrag läuft noch — bitte zuerst den Vertrag "
                                     "stornieren oder löschen")
        if await db.appointments.count_documents(
                {"dealer_id": user["dealer_id"], "vehicle_id": vehicle_id,
                 "status": {"$in": TERMIN_OFFEN_WERTE}}, limit=1):
            raise HTTPException(409, "Abholtermin offen — bitte unter Termine stornieren")
    if ziel == "archiviert":
        # Wie das Archivieren nach Fristablauf (cleanup_service): Fotos weg,
        # Inserats-Entwuerfe und Snapshots raeumt _archiv_nebenaufraeumen
        # (bei einem Fehler holt der Stundenlauf es ueber den Merker nach).
        from cleanup_service import _iter_photo_keys
        data = v.get("data")
        if isinstance(data, dict):
            extra.update({f"data.{k}": [] for k in _iter_photo_keys() if data.get(k)})
        extra.update({"archived_at": now_iso(), "archiv_aufraeumen_offen": True})
    try:
        await set_lifecycle(vehicle_id, user["dealer_id"], ziel, user=user,
                            extra_set=extra or None, verkauf_rueckweg=False)
    except LifecycleError as exc:
        raise HTTPException(409, str(exc))
    if ziel == "archiviert":
        await log_activity_sicher(user["dealer_id"], user["id"], "fahrzeug.archiviert.hand",
                                  ref=vehicle_id, meta={"von": von})
        try:
            from cleanup_service import _archiv_nebenaufraeumen
            await _archiv_nebenaufraeumen(db, vehicle_id, user["dealer_id"])
        except Exception:  # noqa: BLE001 — Merker bleibt, Stundenlauf holt nach
            logging.getLogger("autohandel").exception(
                "Archiv-Aufraeumen fuer %s nicht gelaufen", vehicle_id)
    return {"id": vehicle_id, "von": von, "nach": ziel}


@router.post("/bestand/status")
async def bestand_status_sammel(body: SammelStatusIn, user=Depends(current_haendler)):
    """Wunsch Ahmad 24.11.2026: Status mehrerer Fahrzeuge auf einmal aendern.
    Jedes Fahrzeug wird einzeln geprueft (Firma, Recht, erlaubter Uebergang)
    und bekommt denselben Verlauf wie eine Einzelaenderung; ein
    Fehlschlag haelt die anderen nicht auf. Antwort: geaendert /
    uebersprungen (mit Grund). Hoechstens SAMMEL_MAX Fahrzeuge je Aufruf."""
    sammel_id = uuid.uuid4().hex[:16]
    gesehen: set = set()
    ids = [i for i in body.ids if not (i in gesehen or gesehen.add(i))]
    geaendert: List[Dict[str, Any]] = []
    uebersprungen: List[Dict[str, Any]] = []
    for vid in ids:
        try:
            geaendert.append(await _status_einzeln(vid, body.ziel, user))
        except HTTPException as exc:
            uebersprungen.append({"id": vid, "grund": _fehlertext(exc)})
        except Exception as exc:  # noqa: BLE001 — ein Fahrzeug haelt die anderen nicht auf
            logging.getLogger("autohandel").exception(
                "Sammeländerung %s: Fahrzeug %s fehlgeschlagen", sammel_id, vid)
            uebersprungen.append({"id": vid, "grund": "Technischer Fehler — bitte einzeln "
                                                      "in der Fahrzeugakte versuchen"})
    await log_activity_sicher(user["dealer_id"], user["id"], "fahrzeug.sammelaenderung",
                              ref=sammel_id,
                              meta={"ziel": body.ziel, "angefragt": len(ids),
                                    "geaendert": [g["id"] for g in geaendert],
                                    "uebersprungen": len(uebersprungen)})
    return {"ok": True, "sammel_id": sammel_id, "ziel": body.ziel,
            "geaendert": geaendert, "uebersprungen": uebersprungen}


# =========================================================
#                     FAHRZEUGAKTE
# =========================================================
@router.get("/vehicles/{vehicle_id}/akte")
async def vehicle_akte(vehicle_id: str, user=Depends(current_firma)):
    # Runde 10: Vertraege nur im Bereich des Kontos (Sucher: eigene) —
    # sonst umging die Akte die Sucher-Trennung des Vertragsarchivs.
    from routes.contracts import _vertrag_bereich
    """Durchgehende Fahrzeugakte: aggregiert alle vorhandenen Informationen
    zu einem Fahrzeug — ohne Datendopplung, direkt aus den Quell-Collections."""
    # Runde 16: die Akte eines Kollegen-Fahrzeugs gibt es fuer Sucher nicht.
    # Runde 17 (Nr. 285): der Chef sieht die Akte auch nach dem Loeschen
    # (Historie: Vertrag, Bericht, Audit) — Sucher bleiben beim Standard.
    v = await db.vehicles.find_one(
        {"id": vehicle_id,
         **fahrzeug_bereich(user, mit_geloeschten=user.get("role") != "sucher")},
        {"_id": 0})
    if not v:
        raise HTTPException(404, "Fahrzeug nicht gefunden")

    # Runde 27: Die Akte zeigt die 10 neuesten Vertraege/Termine. Bei
    # mehreren Suchern am selben Auto gibt es mehr — die Gesamtzahl steht
    # jetzt dabei, damit nichts unbemerkt fehlt.
    AKTE_MAX = 10
    contracts = await db.generated_pdfs.find(
        {"vehicle_id": vehicle_id, **_vertrag_bereich(user)},
        {"_id": 0, "pdf_b64": 0, "pdf_digital_b64": 0},
    ).sort("created_at", -1).to_list(AKTE_MAX)
    contracts_gesamt = await db.generated_pdfs.count_documents(
        {"vehicle_id": vehicle_id, **_vertrag_bereich(user)})

    # Umbau Kaufvorgaenge 09.09.2026: Termine (Verkaeuferdaten) nur im Bereich
    # des Kontos — Sucher: eigene Vorgaenge; Chef: alle.
    from deps import termin_bereich
    termin_filter = {"vehicle_id": vehicle_id, **await termin_bereich(user)}
    appointments = await db.appointments.find(
        termin_filter,
        {"_id": 0},
    ).sort("created_at", -1).to_list(AKTE_MAX)
    appointments_gesamt = await db.appointments.count_documents(termin_filter)
    # Wunsch Ahmad 24.11.2026 (Kachel "Abholung/Fahrer" der neuen Akte): der
    # Fahrername zu den gezeigten Terminen (gleiche Quelle wie _offene_termine).
    fahrer_ids = sorted({a["driver_id"] for a in appointments if a.get("driver_id")})
    if fahrer_ids:
        fahrer_namen = {f["id"]: f.get("display_name") or "" async for f in db.driver_accounts.find(
            {"id": {"$in": fahrer_ids}}, {"_id": 0, "id": 1, "display_name": 1})}
        for a in appointments:
            if a.get("driver_id") and fahrer_namen.get(a["driver_id"]):
                a["driver_name"] = fahrer_namen[a["driver_id"]]
    eigene_termin_ids = [a["id"] for a in appointments]
    # Runde 21: ALLE Termine im Bereich (nicht nur die 10 neuesten) fuer
    # Bericht, Protokolle und Historie.
    alle_termin_ids = await db.appointments.distinct(
        "id", {"vehicle_id": vehicle_id, **await termin_bereich(user)})

    # Nachpruefung Runde 14 (Nr. 47): Versionierung/superseded gilt je
    # Termin — bei mehreren Terminen je Fahrzeug lieferte find_one den
    # Bericht des aeltesten (offenen) Termins statt des abgeholten. Die
    # Auswahl liegt jetzt zentral in abholbericht.py (gleiche Regel wie im
    # Verkaufsentwurf, Nr. 45/46); Import in der Funktion, weil das Modul
    # parallel entsteht und keinen Import-Zyklus mit den Routen bilden soll.
    from abholbericht import massgeblicher_bericht
    ist_sucher = user.get("role") == "sucher"
    # Runde 21: fuer Sucher der massgebliche Bericht UNTER DEN EIGENEN
    # Terminen — vorher wurde der firmenweit massgebliche Bericht (evtl. der
    # eines Kollegen) ausgeblendet und der eigene samt Fotos nie gezeigt.
    report = await massgeblicher_bericht(
        db, vehicle_id, user["dealer_id"],
        nur_termine=alle_termin_ids if ist_sucher else None)
    nur_eigene = {"appointment_id": {"$in": alle_termin_ids}} if ist_sucher else {}
    # Zusaetzlich alle Berichte je Termin (auch ersetzte), damit die Akte
    # jedem Termin seinen Bericht zuordnen kann.
    pickup_reports = await db.pickup_reports.find(
        {"vehicle_id": vehicle_id, "dealer_id": user["dealer_id"], **nur_eigene},
        {"_id": 0, "id": 1, "appointment_id": 1, "version": 1, "status": 1,
         "created_at": 1, "superseded": 1, "mileage_at_pickup": 1,
         "driver_name": 1},
    ).sort("created_at", -1).to_list(20)
    pickup_reports_gesamt = await db.pickup_reports.count_documents(
        {"vehicle_id": vehicle_id, "dealer_id": user["dealer_id"], **nur_eigene})
    for r in pickup_reports:
        r["massgeblich"] = bool(report) and r.get("id") == report.get("id")

    # Runde 29 (12.09.2026, Pruefbefund): Die Vergleichsliste der Akte lief
    # nur ueber mobile_ad_id + dealer_id — ein Sucher sah damit, WANN seine
    # Kollegen dasselbe Auto verglichen haben. Fuer Sucher zaehlt jetzt nur
    # das eigene Konto; der Chef sieht weiterhin die ganze Firma.
    vergleich_filter = {"mobile_ad_id": v.get("mobile_ad_id"),
                        "dealer_id": user["dealer_id"]}
    if ist_sucher:
        vergleich_filter["user_id"] = user["id"]
    comparisons = await db.vehicle_comparisons.find(
        vergleich_filter,
        {"_id": 0, "created_at": 1, "source": 1},
    ).sort("created_at", -1).to_list(20) if v.get("mobile_ad_id") else []
    # Runde 19 (Nr. 16): die Akte zeigt 20 Vergleiche, nennt aber die Gesamtzahl.
    comparisons_gesamt = (await db.vehicle_comparisons.count_documents(vergleich_filter)
                          if v.get("mobile_ad_id") else 0)

    ist_sucher = user.get("role") == "sucher"
    # Runde 12: Weiterverkauf ist Chefsache (/resale: current_haendler).
    # Die Akte lieferte Suchern trotzdem Einkaufspreis, Kosten, Status und
    # Inseratsdaten — ein zweiter Weg in den gesperrten Verkaufsbereich.
    listings = [] if ist_sucher else await db.resale_listings.find(
        {"vehicle_id": vehicle_id, "dealer_id": user["dealer_id"],
         "status": {"$ne": "geloescht"}},
        {"_id": 0},
    ).sort("created_at", -1).to_list(5)

    # Rollenprüfung 22.09.2026 (RP-474): Kilometerstand und neue Schaeden aus
    # dem UNTERSCHRIEBENEN Abholprotokoll (abgeholter/erledigter Termin). Die
    # Akte bot bisher nur die Abweichungen des freiwilligen Abhol-Checks zum
    # Uebernehmen an — ohne Check kamen km und Schaeden nie ins Fahrzeug.
    # Nur fuer den Chef (Uebernehmen ist Chefsache); gleiche Auswertung wie
    # beim Inseratsentwurf (resale._protokoll_befund, wirft nie).
    protokoll_befund = None
    if not ist_sucher:
        from routes.resale import _protokoll_befund
        befund = await _protokoll_befund(vehicle_id, user["dealer_id"])
        if befund.get("km") is not None or befund.get("schaeden"):
            protokoll_befund = {"km": befund.get("km"),
                                "schaeden": list(befund.get("schaeden") or [])}

    # Abgeschlossene Abhol-Protokolle (vom Fahrer, mit Unterschriften) —
    # in der Akte als Unterlage sichtbar für Chef UND Sucher.
    # Nachpruefung Runde 14 (Nr. 35): nur nicht-abgeloeste finale Versionen
    # und das Feld superseded mitliefern — die ersetzte v1 stand sonst
    # gleichwertig neben v2 (der Schwester-Endpunkt in protocols.py liefert
    # superseded bereits).
    # Runde 19 (Nr. 17/18): Versionen zaehlen je Termin — sortiert wird nach
    # dem Abschluss (wie der Protokoll-Endpunkt), und die Gesamtzahl steht dabei.
    protokoll_filter = {"vehicle_id": vehicle_id, "dealer_id": user["dealer_id"],
                        "status": "final", "superseded": {"$ne": True}, **nur_eigene}
    # Wunsch Ahmad 06.10.2026: der Chef sieht hier auch die Schadenfotos und Lackdicke-Messungen des Fahrers
    # (Fotos SCHADENFOTO_SICHT_TAGE Tage ab dem Hochladen; Sucher nicht).
    chef_felder = {} if ist_sucher else {"schaden_fotos": 1, "lackmessungen": 1, "new_damages": 1}
    protocols = await db.pickup_protocols.find(
        protokoll_filter,
        {"_id": 0, "id": 1, "version": 1, "finalized_at": 1, "driver_name": 1,
         "seller_name": 1, "place": 1, "corrects_version": 1, "superseded": 1,
         "appointment_id": 1, **chef_felder},
    ).sort([("finalized_at", -1), ("version", -1)]).to_list(20)
    if not ist_sucher:
        import schadenfotos as _sf
        for p in protocols:
            p["schaden_fotos"] = _sf.fuer_anzeige(p)
            p["markierungen"] = _sf.markierungen(p)
            p["lackmessungen"] = [{"id": m.get("id"), "zone": m.get("zone"), "wert_um": m.get("wert_um")}
                                  for m in p.get("lackmessungen") or [] if isinstance(m, dict)]
            p.pop("new_damages", None)
    protocols_gesamt = await db.pickup_protocols.count_documents(protokoll_filter)
    # Pruefbericht 20.09.2026 (R1-22): Versionen zaehlen je Termin — bei
    # mehreren Terminen zum Fahrzeug standen zwei "Version 1" gleichartig
    # nebeneinander. Jetzt mit Termin-ID und Abholdatum (eine Abfrage).
    from routes.protocols import termine_zu_protokollen
    await termine_zu_protokollen(protocols, user["dealer_id"], datenbank=db)

    # Runde 12: Sucher sehen nur ihre eigenen Aktionen zum Fahrzeug —
    # nicht, was Chef oder Kollegen damit gemacht haben.
    # Runde 21: auch Eintraege zu den Terminen des Fahrzeugs (z.B. "Abholbericht
    # eingereicht", ref=Termin) — vorher fehlten sie in jeder Akte.
    # Rollenprüfung 22.09.2026 (RP-483): auch Eintraege zu den Vertraegen
    # (erstellt, verschickt, Folge-Mail, nach Abholung aktualisiert), zu den
    # Abholprotokollen (zur Freigabe, zurueck an den Fahrer, Preis) und — nur
    # fuer den Chef — zu den Inseraten des Fahrzeugs. Vorher fehlten sie in
    # der Akte, obwohl sie genau dieses Auto betreffen. Jeweils nur im Bereich
    # des Kontos (Sucher: eigene Vertraege/Termine); der Index (dealer_id,
    # ref, created_at) traegt die groessere $in-Liste.
    vertrag_ids = await db.generated_pdfs.distinct(
        "id", {"vehicle_id": vehicle_id, **_vertrag_bereich(user)})
    protokoll_ids = await db.pickup_protocols.distinct(
        "id", {"vehicle_id": vehicle_id, "dealer_id": user["dealer_id"], **nur_eigene})
    inserat_ids = [] if ist_sucher else await db.resale_listings.distinct(
        "id", {"vehicle_id": vehicle_id, "dealer_id": user["dealer_id"]})
    bezug_ids = [*alle_termin_ids, *vertrag_ids, *protokoll_ids]
    history_filter = {"ref": {"$in": [vehicle_id, *bezug_ids, *inserat_ids]},
                      "dealer_id": user["dealer_id"]}
    if ist_sucher:
        history_filter = {"dealer_id": user["dealer_id"], "$or": [
            {"ref": vehicle_id, "user_id": user["id"]},
            {"ref": {"$in": bezug_ids}}]}
    # Audit 13.09.2026 (#53): Die Historie endete still bei 100 Eintraegen —
    # bei mehreren Suchern mit eigenen Terminen fehlten die aeltesten (Vertrag,
    # Abholung) ohne Hinweis. Einen Eintrag mehr lesen statt count_documents
    # (kein zweiter Durchlauf). Index: activity_logs "akte_historie"
    # (dealer_id, ref, created_at), angelegt in server._bestand_lese_indizes.
    history = await db.activity_logs.find(history_filter, {"_id": 0}) \
        .sort("created_at", -1).to_list(HISTORIE_MAX + 1)
    history_gekuerzt = len(history) > HISTORIE_MAX
    if history_gekuerzt:
        history = history[:HISTORIE_MAX]
    if ist_sucher:
        # Runde 19 (Nr. 6/13): Sucher bekommen nur Aktion, Bezug und Zeitpunkt —
        # keine Konto-Kennungen und keine Rohdaten (meta) anderer Akteure.
        history = [{"id": h.get("id"), "action": h.get("action"), "ref": h.get("ref"),
                    "created_at": h.get("created_at")} for h in history]
        # info statt warning: kommt bei jedem Aufruf derselben Akte wieder
        logging.getLogger("autohandel").info(
            "Akte %s/%s: Historie auf %d Eintraege gekuerzt",
            user["dealer_id"], vehicle_id, HISTORIE_MAX)

    # Restlaufzeit
    retention_days_left = None
    exp = (v.get("bestand") or {}).get("expires_at")
    if v.get("lifecycle") == "bestand" and exp:
        # Nachpruefung Runde 14 (Nr. 94): gleicher Helfer wie in der Liste.
        try:
            retention_days_left = _resttage(exp, datetime.now(timezone.utc))
        except (ValueError, TypeError):
            pass

    # Runde 16: Besitzer sichtbar (nur der Chef).
    # Wunsch Ahmad 21.09.2026 (R1-01): "man soll nie an dem sein abgeschlossenen
    # Vertrag oder sonstwas wegnehmen" — das Umhaengen per Auswahlfeld ist
    # entfallen. Die Akte liefert deshalb keine Liste zuweisbarer Konten
    # (zuweisbar/zuweisbar_an) mehr, nur noch den Bearbeiter zum Anzeigen;
    # "hauptaccount" ersetzt die Rolle aus der frueheren Auswahlliste.
    owner = None
    if not ist_sucher and v.get("owner_user_id"):
        namen = await besitzer_namen(user["dealer_id"], [v["owner_user_id"]])
        # Rollenprüfung 22.09.2026 (RP-132): "Hauptaccount" nach dem Zeiger
        # dealers.user_id (deps.haupt_chef_id, gleiche Regel wie current_chef)
        # statt nach der rohen Rolle — ein uebrig gebliebenes zweites
        # dealer-Konto (abgebrochener Chefwechsel, Altbestand) arbeitet als
        # Sucher und erschien hier trotzdem als Hauptaccount.
        owner = {"id": v["owner_user_id"],
                 "name": namen.get(v["owner_user_id"]) or "unbekanntes Konto",
                 "hauptaccount": v["owner_user_id"] == await haupt_chef_id(user["dealer_id"])}
    # Runde 29: Wer sonst noch an diesem Auto arbeitet, geht einen Sucher
    # nichts an (Regel Ahmad: Konten nicht vermischen). Nur der Chef sieht
    # die Mitbearbeiter — fuer Sucher gar keine Namensabfrage.
    mit_namen = {} if ist_sucher else await besitzer_namen(
        user["dealer_id"], v.get("mitbearbeiter_ids") or [])
    # Umbau Kaufvorgaenge: je Vertrag ein Vorgang (Sucher, Preis, Status,
    # Termin) — Chef sieht alle, Sucher nur eigene.
    import kaufvorgang as _kv
    kv_filter = {"vehicle_id": vehicle_id, **_kv.bereich(user)}
    kaufvorgaenge = await db.kaufvorgaenge.find(
        kv_filter, {"_id": 0}
    ).sort("created_at", -1).to_list(50)
    # Audit 13.09.2026 (#10): Gesamtzahl wie bei contracts_gesamt (Runde 27),
    # derselbe Bereichsfilter (Sucher: nur eigene). Index (dealer_id, vehicle_id).
    kaufvorgaenge_gesamt = await db.kaufvorgaenge.count_documents(kv_filter)
    kv_namen = await besitzer_namen(user["dealer_id"], [k.get("user_id") for k in kaufvorgaenge])
    for k in kaufvorgaenge:
        k["user_name"] = kv_namen.get(k.get("user_id"), k.get("user_id"))
    # Befund Ahmad 10.09.2026: welcher Einkaufspreis gilt gerade und woher
    # (Fahrzeug/Abholung, Vertrag des Suchers, keiner).
    # Runde 23 (11.09.2026, Befund A): fuer Sucher nur aus den EIGENEN
    # Vorgaengen (vorher firmenweit -> Vertragspreis des Kollegen sichtbar);
    # Fahrzeugpreis/abgeholter Vorgang nur, wenn es sein eigener ist.
    einkaufspreis = await _kv.einkaufspreis_vorschlag(
        vehicle_id, user["dealer_id"], v, user_id=user["id"] if ist_sucher else None)
    await _kv.einkauf_fuer_sucher_maskieren(user, v)
    # Runde 30 (Abnahme): auch die Konto-Kennungen der Kollegen raus —
    # die Akte liefert das rohe Fahrzeugdokument.
    konten_maskieren(user, v)
    if ist_sucher:
        # Runde 19 (Nr. 2): Kosten und interne Notizen sind Chefsache (der
        # Editor ist chef-only) — Sucher bekommen sie nicht mehr geliefert.
        if isinstance(v.get("bestand"), dict):
            for feld in ("costs", "notes"):
                v["bestand"].pop(feld, None)
        from routes.appointments import termin_fuer_sucher
        for a in appointments:
            termin_fuer_sucher(user, a)
    return {
        "vehicle": v,
        "einkaufspreis": einkaufspreis,
        # Runde 21: Frist der Fahrerfotos in Tagen ab dem Hochladen (Anzeige
        # "Fotos werden am ... geloescht").
        "fahrerfoto_tage": __import__("cleanup_service").FAHRERFOTO_TAGE,
        "kaufvorgaenge": kaufvorgaenge,
        "kaufvorgaenge_gesamt": kaufvorgaenge_gesamt,
        "owner": owner,
        "mitbearbeiter": [] if ist_sucher else [
            {"id": m, "name": mit_namen.get(m, m)}
            for m in (v.get("mitbearbeiter_ids") or [])],
        "retention_days_left": retention_days_left,
        "contracts": contracts,
        "contracts_gesamt": contracts_gesamt,
        "appointments": appointments,
        "appointments_gesamt": appointments_gesamt,
        "pickup_report": report,
        "protokoll_befund": protokoll_befund,
        "pickup_reports": pickup_reports,
        "pickup_reports_gesamt": pickup_reports_gesamt,
        "comparisons": comparisons,
        "comparisons_gesamt": comparisons_gesamt,
        "listings": listings,
        "protocols": protocols,
        "protocols_gesamt": protocols_gesamt,
        "history": history,
        "history_gekuerzt": history_gekuerzt,
    }


# =========================================================
#        ABWEICHUNGEN ÜBERNEHMEN (Diff Einkauf/Abholung)
# =========================================================
# Mapping Abweichungs-Feld → Fahrzeugdaten-Feld (nur strukturierte Felder
# lassen sich automatisch übernehmen; Rest landet als bekannter Mangel).
_FIELD_MAP = {"mileage": "mileage", "keys": "keys_count"}
# Rollenprüfung 22.09.2026 (RP-474): Kennung, mit der die Akte "km und Schaeden
# aus dem unterschriebenen Abholprotokoll uebernehmen" anfordert (keine
# Abweichungs-ID eines Abhol-Checks sieht so aus — die sind UUIDs/"d1").
PROTOKOLL_BEFUND_ID = "abholprotokoll"


@router.post("/vehicles/{vehicle_id}/apply-deviations")
async def apply_deviations(vehicle_id: str, body: ApplyDeviationsIn,
                           user=Depends(current_haendler)):
    """Übernimmt ausgewählte Abholungs-Abweichungen in die Fahrzeugdaten.
    Strukturiert (km, Schlüssel) → Datenfeld; alles andere → known_defects."""
    v = await db.vehicles.find_one(
        {"id": vehicle_id, "dealer_id": user["dealer_id"]}, {"_id": 0})
    if not v:
        raise HTTPException(404, "Fahrzeug nicht gefunden")
    # Nachpruefung Runde 14 (Nr. 40): nach dem Verkauf aenderten sich km
    # und known_defects der Akte noch — das Inserat haelt zwar eine Kopie,
    # die Fahrzeughistorie aber nicht.
    _abgeschlossen_sperren(
        v, "Fahrzeug abgeschlossen — Abweichungen nur bis zum Verkauf uebernehmbar")
    # Nachpruefung Runde 14 (Nr. 46): derselbe Bericht wie in der Akte
    # (massgeblicher Termin), sonst waren die dort gezeigten Abweichungs-IDs
    # hier unbekannt (applied=[]) oder ein alter Termin ueberschrieb die Daten.
    from abholbericht import massgeblicher_bericht
    report = await massgeblicher_bericht(db, vehicle_id, user["dealer_id"])
    # Rollenprüfung 22.09.2026 (RP-474): Ohne Abhol-Check kam hier 404 — km
    # und neue Schaeden aus dem UNTERSCHRIEBENEN Abholprotokoll liessen sich
    # nie ins Fahrzeug uebernehmen. Jetzt faellt die Uebernahme auf das
    # Protokoll zurueck (ohne Bericht immer, mit Bericht ueber die Kennung
    # PROTOKOLL_BEFUND_ID, die die Akte anbietet). Gleiche Auswertung wie beim
    # Inseratsentwurf: resale._protokoll_befund (wirft nie, nur Protokolle
    # abgeholter/erledigter Termine).
    befund: Dict[str, Any] = {}
    if not report or PROTOKOLL_BEFUND_ID in body.deviation_ids:
        from routes.resale import _protokoll_befund
        befund = await _protokoll_befund(vehicle_id, user["dealer_id"]) or {}
    hat_befund = befund.get("km") is not None or bool(befund.get("schaeden"))
    if not report and not hat_befund:
        raise HTTPException(404, "Kein Abholbericht und kein unterschriebenes "
                                 "Abholprotokoll mit Kilometerstand oder Schäden vorhanden")
    report = report or {}

    by_id = {d["id"]: d for d in (report.get("deviations") or [])
             if isinstance(d, dict) and d.get("id")}
    # Audit 13.09.2026 (#7): nur die tatsaechlich geaenderten data-Felder
    # sammeln und per Dotted-Path schreiben. Vorher ging das ganze data-Objekt
    # aus dem Lesestand zurueck — zwischenzeitlich geleerte Fotofelder
    # (Loeschen, Tagesregel des Aufraeumers) standen danach wieder drin.
    geaendert: Dict[str, Any] = {}
    # Pruefbericht 20.09.2026 (R2-12): neue Maengel werden gesammelt und per
    # $addToSet angehaengt statt die ganze Liste aus dem Lesestand
    # zurueckzuschreiben — zwei parallele Uebernahmen ueberschrieben sich
    # sonst still. `known_defects` bleibt die zusammengefuehrte Sicht fuer
    # die Antwort.
    bestand_maengel = v.get("known_defects")
    known_defects = list(bestand_maengel or []) if isinstance(bestand_maengel, list) else []
    neue_maengel: List[str] = []

    def _mangel(txt: str) -> bool:
        if txt in known_defects:
            return False
        known_defects.append(txt)
        neue_maengel.append(txt)
        return True

    applied = []
    for dev_id in body.deviation_ids:
        d = by_id.get(dev_id)
        if not d:
            continue
        target_field = _FIELD_MAP.get(d.get("field"))
        if target_field == "mileage" and report.get("mileage_at_pickup"):
            geaendert["mileage"] = report["mileage_at_pickup"]
            applied.append({"feld": "Kilometerstand",
                            "neu": report["mileage_at_pickup"]})
        elif d.get("actual") and target_field:
            geaendert[target_field] = d["actual"]
            eintrag = {"feld": d.get("label"), "neu": d["actual"]}
            if d.get("field") == "keys":
                # Rollenprüfung 22.09.2026 (RP-475): data.keys_count liest
                # niemand — die Abweichung "Schlüssel fehlt" verschwand damit
                # still. Sie steht jetzt zusaetzlich bei den bekannten
                # Maengeln (die gehen auch ins Inserat).
                txt = f"Schlüssel: {d['actual']}"
                if d.get("expected"):
                    txt += f" (erwartet {d['expected']})"
                _mangel(txt)
                eintrag["mangel"] = txt
            applied.append(eintrag)
        else:
            txt = d.get("label") or "Abweichung"
            if d.get("actual"):
                txt += f": {d['actual']}"
            _mangel(txt)
            applied.append({"mangel": txt})

    # RP-474: das unterschriebene Protokoll geht vor (wie im Inseratsentwurf,
    # dort ueberschreibt es ebenfalls den km-Stand des Abhol-Checks).
    if befund.get("km") is not None:
        geaendert["mileage"] = befund["km"]
        applied = [a for a in applied if a.get("feld") != "Kilometerstand"]
        applied.append({"feld": "Kilometerstand", "neu": befund["km"],
                        "quelle": "abholprotokoll"})
    for txt in befund.get("schaeden") or []:
        if _mangel(txt):
            applied.append({"mangel": txt, "quelle": "abholprotokoll"})

    update: Dict[str, Any] = {"deviations_applied_at": now_iso(),
                              "updated_at": now_iso()}
    aenderung: Dict[str, Any] = {"$set": update}
    if isinstance(bestand_maengel, list):
        # R2-12: nur die neuen Eintraege anhaengen (Mengen-Semantik, atomar).
        if neue_maengel:
            aenderung["$addToSet"] = {"known_defects": {"$each": neue_maengel}}
    else:
        # Feld fehlt oder ist kein Array ($addToSet scheitert dort) — einmalig
        # als ganze Liste; verloren gehen kann dabei nichts.
        update["known_defects"] = known_defects
    if isinstance(v.get("data"), dict):
        update.update({f"data.{k}": wert for k, wert in geaendert.items()})
    elif geaendert:
        # data null/kein Objekt: $set auf data.<feld> scheitert dort
        # ("Cannot create field") — dann einmalig als ganzes Objekt.
        update["data"] = dict(geaendert)
    # Audit 13.09.2026 (#7): Write mit Lifecycle-CAS wie update_bestand. Die
    # Sperre oben prueft nur den gelesenen Stand; dazwischen liegt das Laden
    # des Berichts — ein Verkauf/Loeschen/Archivieren in diesem Fenster wurde
    # sonst still ueberschrieben (matched 0 -> 409, kein Audit-Eintrag).
    res = await db.vehicles.update_one(
        {"id": vehicle_id, "dealer_id": user["dealer_id"],
         "lifecycle": {"$nin": list(_ABGESCHLOSSEN)}},
        aenderung)
    if res.matched_count == 0:
        raise HTTPException(409, "Fahrzeug wurde zwischenzeitlich abgeschlossen — "
                                 "Abweichungen nicht uebernommen, bitte neu laden")
    await log_activity_sicher(user["dealer_id"], user["id"],
                              "fahrzeug.abweichungen.uebernommen", ref=vehicle_id,
                              meta={"anzahl": len(applied), "bericht": report.get("id"),
                                    "termin": report.get("appointment_id"),
                                    "abholprotokoll": hat_befund})
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
        "owner_user_id": user["id"],      # Runde 16: manuell = Chef
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
    await log_activity_sicher(user["dealer_id"], user["id"],
                       "fahrzeug.manuell.angelegt", ref=vid,
                       meta={"fahrzeug": f"{body.make_label} {body.model_label}"})
    return clean_doc(doc)


async def vorgang_uebergeben(dealer_id: str, vehicle_id, von, an: str) -> dict:
    """Runde 13 (15.09.2026, Liste 3 Nr. 1-8/20 und Liste 4 Nr. 1/2): Eine
    Uebergabe ist der GANZE Vorgang, nicht nur das Fahrzeug — Kaufvorgaenge,
    Vertraege und Termine des bisherigen Bearbeiters zu diesem Fahrzeug
    (vehicle_id None: zu allen Fahrzeugen, Sucher-Loeschung) gehen an das
    neue Konto. Sonst sah der alte Sucher weiter Termine mit Verkaeuferdaten,
    und der neue bekam das Fahrzeug ohne den laufenden Abholprozess.
    Der Herkunftsvermerk (uebergeben_von) bleibt fuer die Historie.

    Wunsch Ahmad 21.09.2026 (R1-01): der Chef nimmt einem Sucher nichts mehr
    weg (PUT /vehicles/{id}/besitzer ist abgeschaltet). Aufrufer sind nur
    noch Wege, bei denen der Vorgang an den Chef geht: der Sucher entfernt
    ein Fahrzeug selbst aus SEINER Liste (vehicle_fuer_sucher_entfernen),
    der Betreiber loescht ein Sucher-Konto (routes/admin.py admin_delete_user,
    mit Nachlese in cleanup_service),
    und das Nachholen alter, abgebrochener Uebergaben (uebergabe_nachholen)."""
    if not von or von == an:
        return {}
    jetzt = now_iso()
    basis: Dict[str, Any] = {"dealer_id": dealer_id}
    if vehicle_id:
        basis["vehicle_id"] = vehicle_id
    z: Dict[str, int] = {}
    r = await db.kaufvorgaenge.update_many(
        {**basis, "user_id": von},
        {"$set": {"user_id": an, "uebergeben_von": von, "uebergeben_am": jetzt,
                  "updated_at": jetzt}})
    z["kaufvorgaenge"] = r.modified_count
    r = await db.generated_pdfs.update_many(
        {**basis, "user_id": von},
        {"$set": {"user_id": an, "uebergeben_von": von, "uebergeben_am": jetzt}})
    z["vertraege"] = r.modified_count
    r = await db.appointments.update_many(
        {**basis, "created_by": von},
        {"$set": {"created_by": an, "uebergeben_von": von, "uebergeben_am": jetzt}})
    z["termine"] = r.modified_count
    return z


async def uebergabe_nachholen(dealer_id: str, vehicle_id: str, merker) -> dict:
    """Runde 19 (Nr. 6/7): einen nach dem Fahrzeug-Write abgebrochenen
    Besitzerwechsel zu Ende bringen (Kaufvorgaenge, Vertraege, Termine) und
    den Merker entfernen. Idempotent; liefert die Zaehler oder {}.

    Wunsch Ahmad 21.09.2026 (R1-01): den Merker uebergabe_offen schrieb nur
    das abgeschaltete Umhaengen durch den Chef. Neue Merker entstehen nicht
    mehr; der Stundenlauf (cleanup_service.uebergaben_nachholen) bringt nur
    noch Merker aus der Zeit davor zu Ende — dort steht das Fahrzeug schon
    beim neuen Konto, ohne Nachholen blieben Vertrag und Termin getrennt."""
    if not isinstance(merker, dict) or not merker.get("von") or not merker.get("an"):
        return {}
    z = await vorgang_uebergeben(dealer_id, vehicle_id, merker["von"], merker["an"])
    await db.vehicles.update_one(
        {"id": vehicle_id, "dealer_id": dealer_id, "uebergabe_offen.seit": merker.get("seit")},
        {"$unset": {"uebergabe_offen": ""}})
    return z


# Wunsch Ahmad 21.09.2026 (R1-01), woertlich: "nein das soll entfernt werden
# man soll nie an dem sein abgeschlossenen Vertrag oder sonstwas wegnehmen".
# Bis dahin konnte der Chef in der Fahrzeugakte ("Bearbeiter") ein Fahrzeug
# einem anderen Konto umhaengen; mit dem Fahrzeug gingen Vertraege,
# Kaufvorgaenge und Termine des bisherigen Suchers an das neue Konto
# (vorgang_uebergeben). Das gibt es nicht mehr: Fahrzeug, Vertraege, Vorgaenge
# und Termine bleiben bei dem, der sie angelegt hat. Die Route bleibt nur
# stehen, damit alte Oberflaechen/Skripte eine klare Antwort bekommen (410
# statt eines stillen 404/405). Die Rollenpruefung bleibt davor: ohne
# Anmeldung 401, Sucher 403, erst der Chef sieht die 410.
# Weiter erlaubt (kein "Wegnehmen" durch den Chef): der Sucher entfernt ein
# Fahrzeug selbst aus seiner Liste, der Betreiber loescht ein Sucher-Konto,
# und die automatische Pool-Begrenzung (fahrzeugpool.py).
UMHAENGEN_ENTFERNT = ("Fahrzeuge umhängen gibt es nicht mehr (Entscheidung 21.09.2026): "
                      "Fahrzeug, Verträge, Vorgänge und Termine bleiben immer bei dem, "
                      "der sie angelegt hat.")


@router.put("/vehicles/{vehicle_id}/besitzer", deprecated=True)
async def fahrzeug_umhaengen_entfernt(vehicle_id: str, user=Depends(current_haendler)):
    """Abgeschaltet (Wunsch Ahmad 21.09.2026, R1-01): antwortet immer 410 und
    aendert nichts — kein Fahrzeug, kein Vertrag, kein Vorgang, kein Termin.
    Bewusst ohne Body-Modell: sonst bekaeme ein alter Client mit leerem oder
    kaputtem Body 422 statt der klaren Meldung."""
    raise HTTPException(410, UMHAENGEN_ENTFERNT)


@router.put("/vehicles/manual/{vehicle_id}")
async def update_manual_vehicle(vehicle_id: str, body: ManualVehicleIn,
                                user=Depends(current_haendler)):
    v = await db.vehicles.find_one(
        {"id": vehicle_id, "dealer_id": user["dealer_id"], "source": "manuell"},
        {"_id": 0, "id": 1, "lifecycle": 1, "purchase_price": 1})
    if not v:
        raise HTTPException(404, "Manuelles Fahrzeug nicht gefunden")
    # Nachpruefung Runde 14 (Nr. 41): Stammdaten und Einkaufspreis eines
    # verkauften/archivierten Fahrzeugs blieben per API aenderbar — die
    # Akte/Historie wich dann vom verkauften Inserat ab. Dazu ein
    # Audit-Eintrag mit dem alten Preis, den es hier bisher nicht gab.
    _abgeschlossen_sperren(
        v, "Verkaufte/archivierte Fahrzeuge koennen nicht mehr bearbeitet werden")
    # Audit 13.09.2026 (#8): Write mit Lifecycle-CAS wie update_bestand —
    # archiviert der Stundenjob (oder verkauft ein zweiter Tab) zwischen
    # Lesen und Schreiben, aenderten sich sonst Preis und Stammdaten eines
    # abgeschlossenen Fahrzeugs mit Antwort 200. data bleibt bewusst ein
    # Ganzobjekt: das Formular ersetzt die Stammdaten komplett.
    res = await db.vehicles.update_one(
        {"id": vehicle_id, "dealer_id": user["dealer_id"], "source": "manuell",
         "lifecycle": {"$nin": list(_ABGESCHLOSSEN)}},
        {"$set": {"data": body.model_dump(exclude={"purchase_price"}),
                  "purchase_price": body.purchase_price,
                  "updated_at": now_iso()}})
    if res.matched_count == 0:
        raise HTTPException(409, "Fahrzeug wurde zwischenzeitlich abgeschlossen — "
                                 "bitte neu laden")
    await log_activity_sicher(user["dealer_id"], user["id"],
                              "fahrzeug.manuell.geaendert", ref=vehicle_id,
                              meta={"fahrzeug": f"{body.make_label} {body.model_label}",
                                    "einkaufspreis_alt": v.get("purchase_price"),
                                    "einkaufspreis_neu": body.purchase_price})
    return {"ok": True}
