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
from datetime import datetime, timedelta, timezone
from typing import Annotated, Any, Dict, List, Literal, Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field, StringConstraints

from deps import (besitzer_anreichern, besitzer_namen, clean_doc, current_chef,
                  current_firma, db, fahrzeug_bereich,
                  log_activity, log_activity_sicher, now_iso)
from lifecycle import LifecycleError, set_lifecycle

router = APIRouter()

BESTAND_RETENTION_DAYS = 50

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


class BestandUpdateIn(BaseModel):
    location: Optional[str] = Field(default=None, max_length=300)
    notes: Optional[str] = Field(default=None, max_length=10000)
    # Runde 17 (Nr. 337): Liste gedeckelt — _clean_costs schnitt zwar auf 30,
    # verarbeitete davor aber beliebig lange Eingaben.
    costs: Optional[List[Dict[str, Any]]] = Field(default=None, max_length=30)  # [{label, amount}]


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

    # Runde 17 (Nr. 261/263/287): Statuswechsel UND Zusatzfelder (Fotos
    # leeren, deleted_at, Bestandsfrist) in EINEM Write mit CAS auf den
    # gelesenen Lifecycle (set_lifecycle extra_set). Vorher gab es einen
    # Zwischenzustand ("geloescht" mit Fotos, "bestand" ohne Frist), und
    # ein paralleler Wechsel wurde ueberschrieben. LifecycleError -> 409.
    if body.decision == "loeschen":
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
        await log_activity(user["dealer_id"], user["id"],
                           "fahrzeug.entscheidung.geloescht", ref=vehicle_id,
                           meta={"inserate_geloescht": geloeschte_inserate})
        return {"ok": True, "lifecycle": "geloescht",
                "inserate_geloescht": geloeschte_inserate}

    target = "bestand" if body.decision == "bestand" else "verkaufsentwurf"
    extra: Dict[str, Any] = {}
    if body.decision == "bestand":
        expires = (datetime.now(timezone.utc)
                   + timedelta(days=BESTAND_RETENTION_DAYS)).isoformat()
        extra["bestand.saved_at"] = now_iso()
        extra["bestand.expires_at"] = expires
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
    await log_activity(user["dealer_id"], user["id"],
                       f"fahrzeug.entscheidung.{body.decision}", ref=vehicle_id)
    return {"ok": True, "lifecycle": target, "expires_at": expires}


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
    except Exception:  # noqa: BLE001
        logging.getLogger("autohandel").exception(
            "Inserate zu geloeschtem Fahrzeug %s nicht geschlossen", vehicle_id)
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
    res = await db.vehicles.update_one(
        {"id": vehicle_id, "dealer_id": user["dealer_id"],
         "lifecycle": {"$nin": list(_ABGESCHLOSSEN)}},
        {"$set": update})
    if res.matched_count == 0:
        raise HTTPException(409, "Fahrzeug wurde zwischenzeitlich abgeschlossen — "
                                 "Bestandsdaten sind eingefroren, bitte neu laden")
    # Runde 15 (Nr. 5): Kosten beeinflussen die Marge — wer wann aus 500 EUR
    # Aufbereitung 5.000 gemacht hat, muss nachvollziehbar bleiben.
    felder = [f for f in ("location", "notes", "costs") if getattr(body, f) is not None]
    await log_activity(user["dealer_id"], user["id"], "bestand.geaendert", ref=vehicle_id,
                       meta={"felder": felder,
                             "kosten_summe_alt": round(kosten_alt, 2),
                             "kosten_summe_neu": round(sum(c["amount"] for c in (b.get("costs") or [])), 2)})
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
    # Runde 16: Sucher sehen nur eigene Fahrzeuge (owner_user_id).
    query: Dict[str, Any] = {**fahrzeug_bereich(user),
                             "lifecycle": {"$nin": ["geloescht"]}}
    # Runde 17 (Nr. 285): ?lifecycle=geloescht hob den Ausschluss auf —
    # geloeschte Fahrzeuge (Fotos weg, Akte eingefroren) sind hier nie Thema.
    if lifecycle and lifecycle != "geloescht":
        query["lifecycle"] = lifecycle
    if source in ("plattform", "manuell"):
        query["source"] = source
    items = await db.vehicles.find(query, {"_id": 0}).sort(
        "lifecycle_changed_at", -1).to_list(500)
    await besitzer_anreichern(user, items)
    # Runde 23 (11.09.2026, Befund A): Sucher sehen nur den eigenen Einkaufspreis.
    import kaufvorgang as _kv
    await _kv.einkauf_fuer_sucher_maskieren(user, items)

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

    counts: Dict[str, int] = {}
    async for row in db.vehicles.aggregate([
        {"$match": {**fahrzeug_bereich(user),
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

    contracts = await db.generated_pdfs.find(
        {"vehicle_id": vehicle_id, **_vertrag_bereich(user)},
        {"_id": 0, "pdf_b64": 0, "pdf_digital_b64": 0},
    ).sort("created_at", -1).to_list(10)

    # Umbau Kaufvorgaenge 09.09.2026: Termine (Verkaeuferdaten) nur im Bereich
    # des Kontos — Sucher: eigene Vorgaenge; Chef: alle.
    from deps import termin_bereich
    appointments = await db.appointments.find(
        {"vehicle_id": vehicle_id, **await termin_bereich(user)},
        {"_id": 0},
    ).sort("created_at", -1).to_list(10)
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
    for r in pickup_reports:
        r["massgeblich"] = bool(report) and r.get("id") == report.get("id")

    comparisons = await db.vehicle_comparisons.find(
        {"mobile_ad_id": v.get("mobile_ad_id"), "dealer_id": user["dealer_id"]},
        {"_id": 0, "created_at": 1, "source": 1},
    ).sort("created_at", -1).to_list(20) if v.get("mobile_ad_id") else []

    ist_sucher = user.get("role") == "sucher"
    # Runde 12: Weiterverkauf ist Chefsache (/resale: current_haendler).
    # Die Akte lieferte Suchern trotzdem Einkaufspreis, Kosten, Status und
    # Inseratsdaten — ein zweiter Weg in den gesperrten Verkaufsbereich.
    listings = [] if ist_sucher else await db.resale_listings.find(
        {"vehicle_id": vehicle_id, "dealer_id": user["dealer_id"],
         "status": {"$ne": "geloescht"}},
        {"_id": 0},
    ).sort("created_at", -1).to_list(5)

    # Abgeschlossene Abhol-Protokolle (vom Fahrer, mit Unterschriften) —
    # in der Akte als Unterlage sichtbar für Chef UND Sucher.
    # Nachpruefung Runde 14 (Nr. 35): nur nicht-abgeloeste finale Versionen
    # und das Feld superseded mitliefern — die ersetzte v1 stand sonst
    # gleichwertig neben v2 (der Schwester-Endpunkt in protocols.py liefert
    # superseded bereits).
    protocols = await db.pickup_protocols.find(
        {"vehicle_id": vehicle_id, "dealer_id": user["dealer_id"],
         "status": "final", "superseded": {"$ne": True}, **nur_eigene},
        {"_id": 0, "id": 1, "version": 1, "finalized_at": 1, "driver_name": 1,
         "seller_name": 1, "place": 1, "corrects_version": 1, "superseded": 1},
    ).sort("version", -1).to_list(20)

    # Runde 12: Sucher sehen nur ihre eigenen Aktionen zum Fahrzeug —
    # nicht, was Chef oder Kollegen damit gemacht haben.
    # Runde 21: auch Eintraege zu den Terminen des Fahrzeugs (z.B. "Abholbericht
    # eingereicht", ref=Termin) — vorher fehlten sie in jeder Akte.
    history_filter = {"ref": {"$in": [vehicle_id, *alle_termin_ids]},
                      "dealer_id": user["dealer_id"]}
    if ist_sucher:
        history_filter = {"dealer_id": user["dealer_id"], "$or": [
            {"ref": vehicle_id, "user_id": user["id"]},
            {"ref": {"$in": alle_termin_ids}}]}
    history = await db.activity_logs.find(history_filter, {"_id": 0}) \
        .sort("created_at", -1).to_list(100)

    # Restlaufzeit
    retention_days_left = None
    exp = (v.get("bestand") or {}).get("expires_at")
    if v.get("lifecycle") == "bestand" and exp:
        # Nachpruefung Runde 14 (Nr. 94): gleicher Helfer wie in der Liste.
        try:
            retention_days_left = _resttage(exp, datetime.now(timezone.utc))
        except (ValueError, TypeError):
            pass

    # Runde 16: Besitzer sichtbar; der Chef bekommt die Konten der Firma
    # zum Umhaengen (PUT /vehicles/{id}/besitzer) gleich mit.
    owner = None
    zuweisbar_an = []
    if not ist_sucher:
        namen = await besitzer_namen(user["dealer_id"], [v.get("owner_user_id")])
        if v.get("owner_user_id"):
            owner = {"id": v["owner_user_id"],
                     "name": namen.get(v["owner_user_id"]) or "unbekanntes Konto"}
        konten = await db.users.find(
            {"dealer_id": user["dealer_id"], "role": {"$in": ["dealer", "sucher"]},
             "active": {"$ne": False}},
            {"_id": 0, "id": 1, "first_name": 1, "last_name": 1, "email": 1, "role": 1}
        ).sort("created_at", 1).to_list(1000)
        for u in konten:
            name = f"{u.get('first_name') or ''} {u.get('last_name') or ''}".strip()
            if not name:
                name = ("Händler-Hauptaccount" if u.get("role") == "dealer"
                        else (u.get("email") or u["id"]))
            zuweisbar_an.append({"id": u["id"], "name": name, "role": u.get("role")})
    mit_namen = await besitzer_namen(user["dealer_id"], v.get("mitbearbeiter_ids") or [])
    # Umbau Kaufvorgaenge: je Vertrag ein Vorgang (Sucher, Preis, Status,
    # Termin) — Chef sieht alle, Sucher nur eigene.
    import kaufvorgang as _kv
    kaufvorgaenge = await db.kaufvorgaenge.find(
        {"vehicle_id": vehicle_id, **_kv.bereich(user)}, {"_id": 0}
    ).sort("created_at", -1).to_list(50)
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
    return {
        "vehicle": v,
        "einkaufspreis": einkaufspreis,
        # Runde 21: Frist der Fahrerfotos in Tagen ab dem Hochladen (Anzeige
        # "Fotos werden am ... geloescht").
        "fahrerfoto_tage": __import__("cleanup_service").FAHRERFOTO_TAGE,
        "kaufvorgaenge": kaufvorgaenge,
        "owner": owner,
        "mitbearbeiter": [{"id": m, "name": mit_namen.get(m, m)}
                          for m in (v.get("mitbearbeiter_ids") or [])],
        "zuweisbar": not ist_sucher,
        "zuweisbar_an": zuweisbar_an,
        "retention_days_left": retention_days_left,
        "contracts": contracts,
        "appointments": appointments,
        "pickup_report": report,
        "pickup_reports": pickup_reports,
        "comparisons": comparisons,
        "listings": listings,
        "protocols": protocols,
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
                       meta={"anzahl": len(applied), "bericht": report.get("id"),
                             "termin": report.get("appointment_id")})
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
    await log_activity(user["dealer_id"], user["id"],
                       "fahrzeug.manuell.angelegt", ref=vid,
                       meta={"fahrzeug": f"{body.make_label} {body.model_label}"})
    return clean_doc(doc)


class BesitzerIn(BaseModel):
    owner_user_id: str = Field(min_length=1, max_length=100)


@router.put("/vehicles/{vehicle_id}/besitzer")
async def set_vehicle_owner(vehicle_id: str, body: BesitzerIn,
                            user=Depends(current_haendler)):
    """Runde 16: der Chef haengt ein Fahrzeug einem anderen Konto der Firma
    um (Sucher-Wechsel, Krankheit, Kollege hat es zuerst verglichen).
    Termine, Snapshots, Berichte und Protokolle folgen dem Fahrzeug
    automatisch; Vertraege bleiben bei dem Konto, das sie erstellt hat."""
    v = await db.vehicles.find_one(
        {"id": vehicle_id, "dealer_id": user["dealer_id"]},
        {"_id": 0, "owner_user_id": 1})
    if not v:
        raise HTTPException(404, "Fahrzeug nicht gefunden")
    ziel = await db.users.find_one(
        {"id": body.owner_user_id, "dealer_id": user["dealer_id"],
         "role": {"$in": ["dealer", "sucher"]}},
        {"_id": 0, "id": 1, "active": 1})
    if not ziel or ziel.get("active") is False:
        raise HTTPException(404, "Konto nicht gefunden oder nicht in deiner Firma")
    alt = v.get("owner_user_id")
    namen = await besitzer_namen(user["dealer_id"], [ziel["id"]])
    if alt == ziel["id"]:
        return {"ok": True, "owner_user_id": ziel["id"],
                "owner_name": namen.get(ziel["id"]), "unveraendert": True}
    # Der neue Hauptbearbeiter ist nicht zugleich Mitbearbeiter; andere
    # Mitbearbeiter (haben das Inserat selbst verglichen) bleiben.
    await db.vehicles.update_one(
        {"id": vehicle_id, "dealer_id": user["dealer_id"]},
        {"$set": {"owner_user_id": ziel["id"], "updated_at": now_iso()},
         "$pull": {"mitbearbeiter_ids": ziel["id"]}})
    await log_activity(user["dealer_id"], user["id"], "fahrzeug.zugewiesen",
                       ref=vehicle_id, meta={"von": alt, "nach": ziel["id"]})
    return {"ok": True, "owner_user_id": ziel["id"], "owner_name": namen.get(ziel["id"])}


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
    await db.vehicles.update_one(
        {"id": vehicle_id, "dealer_id": user["dealer_id"]},
        {"$set": {"data": body.model_dump(exclude={"purchase_price"}),
                  "purchase_price": body.purchase_price,
                  "updated_at": now_iso()}})
    await log_activity(user["dealer_id"], user["id"],
                       "fahrzeug.manuell.geaendert", ref=vehicle_id,
                       meta={"fahrzeug": f"{body.make_label} {body.model_label}",
                             "einkaufspreis_alt": v.get("purchase_price"),
                             "einkaufspreis_neu": body.purchase_price})
    return {"ok": True}
