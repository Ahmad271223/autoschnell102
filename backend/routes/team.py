"""Phase 2: Sucher-Unteraccounts + Verkaufspakete.

- Der Händler-Hauptaccount ist kostenlos und verwaltet seine Sucher.
- Jeder Sucher braucht ein persönliches Sucher-Abo (subscriptions mit
  subject_user_id) — vergeben durch den Admin, bis Online-Zahlung existiert.
- Verkaufspakete: rollierender Abrechnungszeitraum ab Buchungsdatum;
  gezählt werden NUR tatsächlich veröffentlichte Fahrzeuge (Phase 3),
  pro Inserat höchstens einmal je Zeitraum (counted_periods).
"""
import logging
import uuid
from datetime import datetime, timedelta, timezone
from typing import Optional

import os

from fastapi import APIRouter, Body, Depends, HTTPException, Response
from pydantic import BaseModel, EmailStr, Field, field_validator
from pymongo import ReturnDocument
from pymongo.errors import DuplicateKeyError

import re

from auth import hash_password_async
from deps import (current_firma, current_user, db, email_vergeben, get_subscription_status,
                  log_activity, now_iso,
)
from routes.auth import _check_password_strength
from routes.bestand import current_haendler

router = APIRouter()
log = logging.getLogger("autohandel")


# ---------- Verkaufspakete ----------
# Beschluss 09/2026: Das VERKAUFEN von Fahrzeugen ist kostenlos und
# unbegrenzt — jede Firma darf beliebig viele Fahrzeuge veroeffentlichen.
# Die Paket-Verwaltung bleibt im Code erhalten und laesst sich mit
# VERKAUF_KOSTENLOS=false wieder einschalten (Preise Stand 05.08.2026).
VERKAUF_KOSTENLOS = os.environ.get(
    "VERKAUF_KOSTENLOS", "true").strip().lower() not in ("0", "false", "no")

SALE_PLANS = {
    "s5":  {"label": "Verkauf 5",  "quota": 5,  "price": 10.00},
    "s10": {"label": "Verkauf 10", "quota": 10, "price": 19.99},
    "s20": {"label": "Verkauf 20", "quota": 20, "price": 28.99},
    "s30": {"label": "Verkauf 30", "quota": 30, "price": 37.99},
    "s40": {"label": "Verkauf 40", "quota": 40, "price": 45.00},
    "enterprise": {"label": "Enterprise", "quota": None, "price": None},
}


# ---------- Sucher-Abo (Preise Stand 07.08.2026) ----------
# Der Händler zahlt pro Sucher ein Abo; erst mit aktivem Abo kann der Sucher
# suchen & vergleichen. Freischaltung erfolgt manuell über den Admin.
# Preise Stand 09/2026: 150 €/Monat oder 1.500 €/Jahr, Abrechnung per
# Rechnung durch den Betreiber (kein Stripe für Firmen/Sucher).
SUCHER_PLANS = {
    "monthly": {"label": "Monatlich", "price": 150.00, "days": 30},
    "yearly":  {"label": "Jährlich",  "price": 1500.00, "days": 365},
}


# ---------- Models ----------
class SucherIn(BaseModel):
    first_name: str = Field(min_length=1, max_length=80)
    last_name: str = Field(min_length=1, max_length=80)
    email: EmailStr
    password: str = Field(min_length=8, max_length=200)
    phone: str = Field(default="", max_length=50)
    employee_id: str = Field(default="", max_length=50)

    @field_validator("password")
    @classmethod
    def _pw(cls, v):
        return _check_password_strength(v)


class SucherUpdateIn(BaseModel):
    first_name: Optional[str] = Field(default=None, max_length=80)
    last_name: Optional[str] = Field(default=None, max_length=80)
    phone: Optional[str] = Field(default=None, max_length=50)
    employee_id: Optional[str] = Field(default=None, max_length=50)
    active: Optional[bool] = None
    password: Optional[str] = Field(default=None, min_length=8, max_length=200)

    @field_validator("password")
    @classmethod
    def _pw(cls, v):
        return _check_password_strength(v) if v is not None else v


class UpgradeRequestIn(BaseModel):
    wanted_tier: str = Field(max_length=20)
    message: str = Field(default="", max_length=2000)


# =========================================================
#                SUCHER-VERWALTUNG
# =========================================================
# Beschluss 09/2026: Sucher-Konten legt NUR der Betreiber an (Admin-Bereich,
# /admin/dealers/{id}/sucher) — inkl. Anmeldename + Passwort. Der Chef sieht
# sein Team weiterhin (Liste + Statistik) und stellt Abo-Anfragen; Anlegen,
# Löschen und Passwörter laufen über den Betreiber. In Entwicklung/CI
# (SELF_SIGNUP nicht auf false) bleiben die Chef-Routen aktiv, damit die
# bestehenden Tests und lokales Ausprobieren funktionieren.
def _chef_verwaltung_erlaubt() -> bool:
    # Runde 11: dieselbe fail-closed Regel wie die Selbst-Registrierung
    # (vorher hier eine zweite Kopie der alten fail-open Logik).
    from routes.auth import _self_signup_enabled
    return _self_signup_enabled()


_NUR_BETREIBER = ("Sucher-Konten verwaltet der Betreiber. Bitte melde dich "
                  "bei uns — wir legen Zugänge an, setzen Passwörter und "
                  "entfernen Konten.")


@router.post("/dealer/sucher")
async def create_sucher(body: SucherIn, user=Depends(current_haendler)):
    if not _chef_verwaltung_erlaubt():
        raise HTTPException(403, _NUR_BETREIBER)
    # Runde 13: B5 — E-Mail wie in auth.py normalisieren und plattformweit
    # (users UND driver_accounts) pruefen; vorher nur users, unnormalisiert.
    email = body.email.strip().lower()
    if await email_vergeben(email):
        raise HTTPException(409, "E-Mail ist bereits registriert")
    sucher_id = str(uuid.uuid4())
    await db.users.insert_one({
        "id": sucher_id,
        "email": email,
        "password_hash": await hash_password_async(body.password),
        "role": "sucher",
        "active": True,
        "dealer_id": user["dealer_id"],          # gehört zum Händler
        "first_name": body.first_name,
        "last_name": body.last_name,
        "phone": body.phone,
        "employee_id": body.employee_id,
        "created_by": user["id"],
        "current_session_id": None,
        "created_at": now_iso(),
    })
    await log_activity(user["dealer_id"], user["id"], "sucher.angelegt",
                       ref=sucher_id, meta={"email": body.email})
    return {"ok": True, "sucher_id": sucher_id,
            "hinweis": "Der Sucher benötigt ein aktives Sucher-Abo, um "
                       "Fahrzeuge suchen und vergleichen zu können."}


@router.get("/dealer/sucher")
async def list_sucher(response: Response, user=Depends(current_haendler)):
    """Alle Sucher des Händlers inkl. Abo-Status und Monats-Statistik."""
    # Runde 11: feste Feldliste statt "alles ausser Passwort" — sonst
    # landen Sitzungs-ID, persoenliche Overrides und jedes kuenftige
    # Benutzerfeld automatisch beim Chef.
    # Nachpruefung Runde 14 (Nr. 72): to_list(100) liess ab dem 101. Sucher
    # Konten still verschwinden — es gibt kein Sucher-Limit je Firma. Die
    # Projektion ist klein, die Sammelabfragen skalieren ueber $in.
    items = await db.users.find(
        {"dealer_id": user["dealer_id"], "role": "sucher"},
        {"_id": 0, "id": 1, "email": 1, "role": 1, "active": 1, "dealer_id": 1,
         "first_name": 1, "last_name": 1, "phone": 1, "employee_id": 1,
         "created_by": 1, "created_at": 1, "updated_at": 1},
    ).sort("created_at", 1).to_list(1000)
    # Audit 13.09.2026 (#55): Die Obergrenze bleibt, wird aber nicht mehr
    # still gezogen — ab dem 1001. Sucher meldet die Kopfzeile X-Truncated
    # (Antwort bleibt eine Liste) und das Log eine Warnung. Zaehlen nur,
    # wenn die Grenze ueberhaupt erreicht ist.
    abgeschnitten = len(items) >= 1000 and await db.users.count_documents(
        {"dealer_id": user["dealer_id"], "role": "sucher"}) > len(items)
    response.headers["X-Truncated"] = "1" if abgeschnitten else "0"
    if abgeschnitten:
        log.warning("Sucherliste der Firma %s auf %s Eintraege gekuerzt",
                    user["dealer_id"], len(items))
    month_start = datetime.now(timezone.utc).replace(
        day=1, hour=0, minute=0, second=0, microsecond=0).isoformat()
    # ALLE Zusatzdaten in 3 Sammelabfragen statt 3 Abfragen JE SUCHER
    # (vorher: ~301 Einzelabfragen bei 100 Suchern).
    # Nachpruefung Runde 14 (Nr. 92): jede Sammelabfrage zusaetzlich auf die
    # eigene dealer_id eingrenzen — Abos, Vertraege und Vergleiche eines
    # Suchers, die (z.B. nach einem Firmenwechsel per Datenbank) noch einer
    # anderen Firma gehoeren, duerfen dem Chef nicht angezeigt werden.
    ids = [s["id"] for s in items]
    from deps import sub_status_from_doc
    subs = {}
    async for row in db.subscriptions.aggregate([
        {"$match": {"dealer_id": user["dealer_id"],
                    "subject_user_id": {"$in": ids}}},
        {"$sort": {"created_at": -1}},
        {"$group": {"_id": "$subject_user_id", "sub": {"$first": "$$ROOT"}}},
    ]):
        subs[row["_id"]] = row["sub"]
    purchases = {row["_id"]: row["n"] async for row in db.generated_pdfs.aggregate([
        {"$match": {"dealer_id": user["dealer_id"],
                    "user_id": {"$in": ids},
                    "created_at": {"$gte": month_start}}},
        {"$group": {"_id": "$user_id", "n": {"$sum": 1}}},
    ])}
    # Runde 27 (Pruefbefund 12.09.2026): vehicle_comparisons wird nach 14
    # Tagen automatisch geloescht. Ab Monatsmitte waere 'seit dem Ersten'
    # also schlicht falsch — der Chef saehe weniger, als seine Sucher
    # wirklich gearbeitet haben. Deshalb wird der Zeitraum ehrlich
    # begrenzt und mitgeliefert; die Oberflaeche schreibt ihn dazu.
    vergleiche_seit = max(
        month_start,
        (datetime.now(timezone.utc) - timedelta(days=14)).isoformat())
    comparisons = {row["_id"]: row["n"] async for row in db.vehicle_comparisons.aggregate([
        {"$match": {"dealer_id": user["dealer_id"],
                    "user_id": {"$in": ids},
                    "created_at": {"$gte": vergleiche_seit}}},
        {"$group": {"_id": "$user_id", "n": {"$sum": 1}}},
    ])}
    return [{**s,
             "subscription": sub_status_from_doc(subs.get(s["id"])),
             "stats_month": {"kaeufe": purchases.get(s["id"], 0),
                             "vergleiche": comparisons.get(s["id"], 0),
                             "vergleiche_seit": vergleiche_seit}}
            for s in items]


@router.put("/dealer/sucher/{sucher_id}")
async def update_sucher(sucher_id: str, body: SucherUpdateIn,
                        user=Depends(current_haendler)):
    if not _chef_verwaltung_erlaubt():
        raise HTTPException(403, _NUR_BETREIBER)
    s = await db.users.find_one(
        {"id": sucher_id, "dealer_id": user["dealer_id"], "role": "sucher"})
    if not s:
        raise HTTPException(404, "Sucher nicht gefunden")
    fields = {k: v for k, v in body.model_dump(exclude_none=True).items()
              if k != "password"}
    if body.password:
        fields["password_hash"] = await hash_password_async(body.password)
        fields["current_session_id"] = None
    if body.active is False:
        fields["current_session_id"] = None
    if fields:
        fields["updated_at"] = now_iso()
        await db.users.update_one({"id": sucher_id}, {"$set": fields})
    await log_activity(user["dealer_id"], user["id"], "sucher.aktualisiert",
                       ref=sucher_id, meta={"felder": sorted(fields.keys())})
    return {"ok": True}


@router.delete("/dealer/sucher/{sucher_id}")
async def delete_sucher(sucher_id: str, user=Depends(current_haendler)):
    if not _chef_verwaltung_erlaubt():
        raise HTTPException(403, _NUR_BETREIBER)
    s = await db.users.find_one(
        {"id": sucher_id, "dealer_id": user["dealer_id"], "role": "sucher"})
    if not s:
        raise HTTPException(404, "Sucher nicht gefunden")
    # Runde 29 (12.09.2026, Pruefbefund): VOR dem Loeschen die Arbeit des
    # Kontos uebernehmen. Vorher blieben die Fahrzeuge mit owner_user_id des
    # geloeschten Suchers stehen (Besitzer, den es nicht mehr gibt) und sein
    # Name stand weiter in mitbearbeiter_ids. Bricht der Vorgang hier ab,
    # existiert der Sucher noch und der Chef kann es einfach erneut
    # versuchen — nichts Halbes bleibt zurueck.
    jetzt = now_iso()
    firma = {"dealer_id": user["dealer_id"]}
    uebernommen = (await db.vehicles.update_many(
        {**firma, "owner_user_id": sucher_id},
        {"$set": {"owner_user_id": user["id"], "uebernommen_von": sucher_id,
                  "updated_at": jetzt}})).modified_count
    await db.vehicles.update_many(
        {**firma, "mitbearbeiter_ids": sucher_id},
        {"$pull": {"mitbearbeiter_ids": sucher_id}})
    # Vertraege, Kaufvorgaenge und Termine bleiben unveraendert: sie sind
    # Belege und gehoeren der Firma — der Chef sieht sie ohnehin alle, und
    # wer sie angelegt hat, gehoert zur Nachvollziehbarkeit.
    await db.users.delete_one({"id": sucher_id})
    # Persoenliche Reste mitloeschen (PR-Review 09/2026): das Sucher-Abo
    # blieb sonst bestehen und konnte Status-/Kuendigungslogik verwirren.
    await db.subscriptions.delete_many({"subject_user_id": sucher_id})
    # Runde 11: password_resets tragen user_id, keine E-Mail — der alte
    # Filter nach E-Mail traf nie. Offene Abo-Anfragen des Suchers blieben
    # beim Betreiber als verwaiste "offene" Anfrage stehen.
    await db.password_resets.delete_many({"user_id": sucher_id})
    await db.plan_requests.delete_many({"subject_user_id": sucher_id, "status": "offen"})
    # Runde 13: B8 — Nutzerkennung in Beweis-Snapshots pseudonymisieren
    # (die Snapshots selbst bleiben, s. snapshot_service).
    from snapshot_service import snapshots_pseudonymisieren
    await snapshots_pseudonymisieren(db, user_id=sucher_id)
    await log_activity(user["dealer_id"], user["id"], "sucher.geloescht",
                       ref=sucher_id, meta={"email": s.get("email", ""),
                                            "fahrzeuge_uebernommen": uebernommen})
    return {"ok": True}


# =========================================================
#                    VERKAUFSPAKETE
# =========================================================
def _current_period(period_start_iso: str) -> tuple:
    """Rollierender 30-Tage-Zeitraum ab Buchungsdatum. Liefert
    (period_key, start_iso, end_iso) für JETZT.

    Nachpruefung Runde 14 (Nr. 31): ein unlesbarer Startwert (kaputter
    Datensatz, Migration) fuehrt zu ValueError — der Aufrufer behandelt das
    Paket dann als ungueltig statt mit 500 auszusteigen. Fehlender Wert
    (None/leer) = heute."""
    if not period_start_iso:
        period_start_iso = now_iso()
    try:
        start = datetime.fromisoformat(str(period_start_iso).strip().replace("Z", "+00:00"))
    except (TypeError, ValueError) as exc:
        raise ValueError(f"Paketzeitraum ungueltig: {period_start_iso!r}") from exc
    now = datetime.now(timezone.utc)
    if start.tzinfo is None:
        start = start.replace(tzinfo=timezone.utc)
    while start + timedelta(days=30) <= now:
        start = start + timedelta(days=30)
    end = start + timedelta(days=30)
    return (start.strftime("%Y%m%d"), start.isoformat(), end.isoformat())


async def get_sale_plan_status(dealer_id: str) -> dict:
    """Paket + Verbrauch des aktuellen Abrechnungszeitraums.

    Solange VERKAUF_KOSTENLOS gilt, ist jede Firma freigeschaltet und hat
    KEIN Limit (quota None) — das Veroeffentlichen zaehlt dann nichts ab."""
    if VERKAUF_KOSTENLOS:
        period_key, p_start, p_end = _current_period(now_iso())
        return {"active": True, "tier": "kostenlos", "label": "Kostenlos",
                "kostenlos": True, "quota": None, "used": 0, "remaining": None,
                "period_key": period_key, "period_start": p_start,
                "period_end": p_end, "valid_until": None, "price": 0.0,
                "plans": {}}
    dealer = await db.dealers.find_one(
        {"id": dealer_id}, {"_id": 0, "sale_plan": 1, "quota_usage": 1})
    plan = (dealer or {}).get("sale_plan")
    if not plan:
        return {"active": False, "tier": None, "quota": 0, "used": 0,
                "remaining": 0, "period_start": None, "period_end": None,
                "plans": SALE_PLANS}
    tier = plan.get("tier")
    meta = SALE_PLANS.get(tier, {})
    # Ablauf pruefen: Pakete mit valid_until enden automatisch — danach ist
    # kein Publish mehr moeglich, bis der Admin verlaengert. Alte Pakete
    # ohne valid_until laufen unbefristet weiter (Bestandsschutz).
    valid_until = plan.get("valid_until")
    if valid_until:
        try:
            vu = datetime.fromisoformat(valid_until)
        except (TypeError, ValueError):
            # Runde 17 (Nr. 387): ein unlesbares Ablaufdatum galt bisher
            # still als "unbefristet" (except: pass) — das Paket lief
            # unbegrenzt weiter. Jetzt fail-closed wie bei period_start:
            # Paket ungueltig, kein Publish, Betreiber repariert. Der
            # Admin-Verlaengerungspfad (routes/admin.py) rechnet mit
            # seinem eigenen Parser und bleibt unberuehrt.
            log.error("Verkaufspaket %s: valid_until %r unlesbar -> ungueltig",
                      dealer_id, valid_until)
            return {"active": False, "tier": tier, "label": meta.get("label", tier),
                    "fehler": "Paketlaufzeit ungueltig — bitte den Betreiber "
                              "kontaktieren",
                    "quota": 0, "used": 0, "remaining": 0,
                    "period_start": None, "period_end": None,
                    "valid_until": valid_until, "plans": SALE_PLANS}
        if vu.tzinfo is None:
            vu = vu.replace(tzinfo=timezone.utc)
        if vu < datetime.now(timezone.utc):
            return {"active": False, "expired": True, "tier": tier,
                    "label": meta.get("label", tier),
                    "valid_until": valid_until,
                    "quota": 0, "used": 0, "remaining": 0,
                    "period_start": None, "period_end": None,
                    "plans": SALE_PLANS}
    quota = plan.get("custom_quota") or meta.get("quota") or 0
    try:
        period_key, p_start, p_end = _current_period(plan.get("period_start"))
    except ValueError:
        # Nachpruefung Runde 14 (Nr. 31): kaputtes period_start warf bisher
        # bis in /dealer/sale-plan und publish_listing durch (500). Jetzt
        # fail-closed: Paket ungueltig, kein Publish, Betreiber repariert.
        log.error("Verkaufspaket %s: period_start %r unlesbar -> ungueltig",
                  dealer_id, plan.get("period_start"))
        return {"active": False, "tier": tier, "label": meta.get("label", tier),
                "fehler": "Paketzeitraum ungueltig — bitte den Betreiber "
                          "kontaktieren",
                "quota": 0, "used": 0, "remaining": 0,
                "period_start": None, "period_end": None,
                "valid_until": plan.get("valid_until"), "plans": SALE_PLANS}
    # Verbrauch: bevorzugt der atomare Zähler (race-fest, siehe publish_listing).
    # Fallback auf die abgeleitete Zählung, solange der Zähler in diesem
    # Zeitraum noch nie gesetzt wurde (vor der ersten Veröffentlichung).
    used = (dealer.get("quota_usage") or {}).get(period_key)
    if used is None:
        used = await db.resale_listings.count_documents(
            {"dealer_id": dealer_id, "counted_periods": period_key})
    return {"active": True, "tier": tier, "label": meta.get("label", tier),
            "quota": quota, "used": used,
            "remaining": max(0, quota - used) if quota else None,
            "period_key": period_key,
            "period_start": p_start, "period_end": p_end,
            "valid_until": plan.get("valid_until"),
            "price": meta.get("price"), "plans": SALE_PLANS}


@router.get("/dealer/sale-plan")
async def sale_plan_status(user=Depends(current_haendler)):
    return await get_sale_plan_status(user["dealer_id"])


# ---------- Offene Anfragen: genau EINE je Schluessel ----------
# Nachpruefung Runde 14 (Nr. 33, 56, 57): "find_one, dann insert_one" war
# nicht rennfest — 16 gleichzeitige Klicks legten bis zu 16 offene Anfragen
# an, und zwei der drei Anfrage-Routen prueften gar nicht. Jetzt EIN Weg:
# atomarer Upsert auf den Schluessel der offenen Anfrage. Die Felder des
# Schluessels ({type, subject_user_id bzw. dealer_id, status: "offen"})
# tragen die Teil-Unique-Indizes aus server.ensure_indexes; ein
# DuplicateKeyError (zwei Upserts im selben Augenblick) wird durch einen
# zweiten Versuch aufgeloest, der dann die bestehende Zeile trifft.
async def _offene_anfrage_upsert(schluessel: dict, neu: dict,
                                 wunsch: dict) -> tuple:
    """Liefert (Anfrage-Dokument, neu_angelegt). `wunsch` (Wunschplan/-paket)
    wird immer geschrieben — eine bereits offene Anfrage uebernimmt so den
    zuletzt geaeusserten Wunsch.

    Runde 29 (12.09.2026, Pruefbefund): Aus `neu` gingen frueher AUCH
    Firmenname, Kontaktdaten und aktueller Verbrauch nur beim Anlegen in die
    Anfrage. Aenderte die Firma danach Telefon oder E-Mail, sah der Betreiber
    weiter die alten Angaben. Jetzt bleibt nur die Identitaet der Anfrage
    (id, created_at) beim Anlegen; alles andere wird jedes Mal aufgefrischt.
    """
    beim_anlegen = {k: v for k, v in neu.items() if k in ("id", "created_at")}
    aktuell = {k: v for k, v in neu.items() if k not in beim_anlegen}
    for versuch in (1, 2):
        try:
            doc = await db.plan_requests.find_one_and_update(
                schluessel,
                {"$setOnInsert": beim_anlegen, "$set": {**aktuell, **wunsch}},
                upsert=True, projection={"_id": 0},
                return_document=ReturnDocument.AFTER)
            break
        except DuplicateKeyError:
            if versuch == 2:
                raise
    return doc, doc.get("id") == neu.get("id")


@router.post("/dealer/sale-plan/upgrade-request")
async def sale_plan_upgrade_request(body: UpgradeRequestIn,
                                    user=Depends(current_haendler)):
    """Upgrade-/Enterprise-Anfrage — landet beim Admin mit Händler-ID,
    aktuellem Verbrauch, Wunschvolumen und Kontaktdaten. Idempotent: eine
    offene Anfrage der Firma wird aktualisiert, nicht verdoppelt (Nr. 57)."""
    if VERKAUF_KOSTENLOS:
        raise HTTPException(400, "Das Verkaufen von Fahrzeugen ist derzeit "
                                 "kostenlos und unbegrenzt — es ist kein Paket "
                                 "noetig.")
    status = await get_sale_plan_status(user["dealer_id"])
    dealer = await db.dealers.find_one({"id": user["dealer_id"]}, {"_id": 0})
    req_id = str(uuid.uuid4())
    doc, neu = await _offene_anfrage_upsert(
        {"type": "verkaufspaket", "dealer_id": user["dealer_id"], "status": "offen"},
        {"id": req_id,
         "company_name": (dealer or {}).get("company_name", ""),
         "contact_email": user.get("email", ""),
         "contact_phone": (dealer or {}).get("phone", ""),
         "current_tier": status.get("tier"),
         "current_usage": status.get("used"),
         "created_at": now_iso()},
        {"wanted_tier": body.wanted_tier, "message": body.message,
         "updated_at": now_iso()})
    if not neu:
        # Runde 17 (Nr. 376): der Upsert aendert den Wunsch der offenen
        # Anfrage — vorher ohne Audit-Spur (nur das Anlegen war geloggt).
        await log_activity(user["dealer_id"], user["id"],
                           "verkaufsplan.anfrage.geaendert", ref=doc["id"],
                           meta={"wunsch": body.wanted_tier})
        return {"ok": True, "request_id": doc["id"], "bereits_offen": True,
                "hinweis": "Eine Anfrage liegt bereits beim Administrator."}
    await log_activity(user["dealer_id"], user["id"], "verkaufsplan.anfrage",
                       ref=req_id, meta={"wunsch": body.wanted_tier})
    return {"ok": True, "request_id": req_id,
            "hinweis": "Anfrage wurde an den Administrator übermittelt."}


# ---------- Eigenes Abo des Chefs (Anfrage an den Betreiber) ----------
@router.post("/dealer/abo-anfrage-selbst")
async def eigenes_abo_anfrage(body: dict = Body(default={}),
                              user=Depends(current_firma)):
    """Chef ODER Sucher fragt das EIGENE Sucher-Abo an (Verlängerung/
    Neustart) — Wunsch 09/2026: der Sucher bekommt keine Fehlermeldung
    mehr, sondern die Anfrage landet beim Betreiber ("Sucher X von Firma Y
    will Abo verlängern — ja/nein"). Freischaltung ueber
    /admin/sucher/{user_id}/abo; die offene Anfrage wird dabei geschlossen.
    Idempotent: eine bereits offene Anfrage wird zurueckgegeben, nicht
    verdoppelt."""
    plan = body.get("plan", "monthly")
    if plan not in SUCHER_PLANS:
        raise HTTPException(400, "Unbekannter Abo-Zeitraum")
    dealer = await db.dealers.find_one({"id": user["dealer_id"]}, {"_id": 0})
    ist_sucher = user.get("role") == "sucher"
    name = (f"{user.get('first_name', '')} {user.get('last_name', '')}".strip()
            if ist_sucher else ((dealer or {}).get("contact_person") or "Chef"))
    req_id = str(uuid.uuid4())
    # Nachpruefung Runde 14 (Nr. 56): atomarer Upsert statt find_one+insert.
    # Runde 15 (Nr. 3): dealer_id gehoert in den Schluessel — eine offene
    # Altanfrage unter einer anderen Firma wird nie mitgeaendert; stattdessen
    # 409 (der Teil-Unique-Index laesst nur EINE offene Anfrage je Konto zu).
    try:
        doc, neu = await _offene_anfrage_upsert(
            {"type": "sucher_abo", "subject_user_id": user["id"],
             "dealer_id": user["dealer_id"], "status": "offen"},
            {"id": req_id,
             "subject_role": user.get("role", "dealer"),
         "sucher_name": name or user.get("email", ""),
         "sucher_email": user.get("email", ""),
         "company_name": (dealer or {}).get("company_name", ""),
         "kunden_nr": (dealer or {}).get("kunden_nr"),
         "contact_email": user.get("email", ""),
         "contact_phone": (dealer or {}).get("phone", ""),
         "created_at": now_iso()},
            {"wanted": SUCHER_PLANS[plan]["label"] + " (eigener Zugang)",
             "wanted_plan": plan,
             "price": SUCHER_PLANS[plan]["price"],
             "updated_at": now_iso()})
    except DuplicateKeyError:
        raise HTTPException(409, "Für dieses Konto liegt noch eine offene Anfrage "
                                 "aus einer früheren Firmenzuordnung vor — bitte "
                                 "den Betreiber kontaktieren.")
    if not neu:
        # Runde 17 (Nr. 376): geaenderter Wunsch an der offenen Anfrage
        # bekommt eine Audit-Spur.
        await log_activity(user["dealer_id"], user["id"],
                           "abo.anfrage.selbst.geaendert", ref=doc["id"],
                           meta={"plan": plan})
        return {"ok": True, "request_id": doc["id"], "bereits_offen": True,
                "hinweis": "Deine Anfrage liegt bereits beim Betreiber."}
    await log_activity(user["dealer_id"], user["id"], "abo.anfrage.selbst",
                       ref=req_id, meta={"plan": plan})
    return {"ok": True, "request_id": req_id,
            "hinweis": "Anfrage wurde an den Betreiber übermittelt."}


# ---------- Sucher-Abo ----------
@router.get("/dealer/sucher-plans")
async def sucher_plans(user=Depends(current_haendler)):
    return {"plans": SUCHER_PLANS}


@router.post("/dealer/sucher/{sucher_id}/abo-anfrage")
async def sucher_abo_request(sucher_id: str, body: dict = Body(default={}),
                             user=Depends(current_haendler)):
    """Händler fragt die Freischaltung eines Sucher-Abos an — landet beim
    Admin (manuelle Bezahlung/Freischaltung). Idempotent wie die eigene
    Anfrage: eine offene Anfrage fuer den Sucher wird zurueckgegeben, nicht
    verdoppelt (Nr. 33)."""
    plan = body.get("plan", "monthly")
    if plan not in SUCHER_PLANS:
        raise HTTPException(400, "Unbekannter Abo-Zeitraum")
    sucher = await db.users.find_one(
        {"id": sucher_id, "dealer_id": user["dealer_id"], "role": "sucher"},
        {"_id": 0, "email": 1, "first_name": 1, "last_name": 1, "active": 1})
    if not sucher:
        raise HTTPException(404, "Sucher nicht gefunden")
    # Nachpruefung Runde 14 (Nr. 104): fuer ein deaktiviertes Konto landete
    # eine Abo-Anfrage beim Betreiber, der sie freischalten (und abrechnen)
    # konnte, obwohl der Sucher gar nicht arbeiten darf.
    if sucher.get("active") is False:
        raise HTTPException(400, "Der Sucher ist deaktiviert — erst wieder "
                                 "aktivieren, dann Abo anfragen")
    dealer = await db.dealers.find_one({"id": user["dealer_id"]}, {"_id": 0})
    req_id = str(uuid.uuid4())
    # Runde 17 (Nr. 375): dealer_id gehoert in den Schluessel (wie bei der
    # eigenen Anfrage, Runde 15 Nr. 3) — vorher traf der Upsert eine offene
    # Altanfrage des Suchers unter einer FRUEHEREN Firma und schrieb den
    # Wunsch des neuen Chefs hinein (Anfrage blieb der alten Firma
    # zugeordnet). Jetzt: 409, der Teil-Unique-Index laesst nur EINE offene
    # Anfrage je Sucher zu, der Betreiber raeumt die alte weg.
    try:
        doc, neu = await _offene_anfrage_upsert(
            {"type": "sucher_abo", "subject_user_id": sucher_id,
             "dealer_id": user["dealer_id"], "status": "offen"},
            {"id": req_id,
             "sucher_name": f"{sucher.get('first_name','')} {sucher.get('last_name','')}".strip(),
             "sucher_email": sucher.get("email", ""),
             "company_name": (dealer or {}).get("company_name", ""),
             "contact_email": user.get("email", ""),
             "contact_phone": (dealer or {}).get("phone", ""),
             "created_at": now_iso()},
            {"wanted": SUCHER_PLANS[plan]["label"],
             "wanted_plan": plan,
             "price": SUCHER_PLANS[plan]["price"],
             "updated_at": now_iso()})
    except DuplicateKeyError:
        raise HTTPException(409, "Für diesen Sucher liegt noch eine offene Anfrage "
                                 "aus einer früheren Firmenzuordnung vor — bitte "
                                 "den Betreiber kontaktieren.")
    if not neu:
        # Runde 17 (Nr. 376): geaenderter Wunsch an der offenen Anfrage
        # bekommt eine Audit-Spur.
        await log_activity(user["dealer_id"], user["id"],
                           "sucher.abo.anfrage.geaendert", ref=doc["id"],
                           meta={"sucher": sucher_id, "plan": plan})
        return {"ok": True, "request_id": doc["id"], "bereits_offen": True,
                "hinweis": "Eine Anfrage fuer diesen Sucher liegt bereits beim "
                           "Administrator."}
    await log_activity(user["dealer_id"], user["id"], "sucher.abo.anfrage",
                       ref=req_id, meta={"sucher": sucher_id, "plan": plan})
    return {"ok": True, "request_id": req_id,
            "hinweis": "Anfrage wurde an den Administrator übermittelt."}
