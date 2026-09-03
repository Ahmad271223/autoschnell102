"""Phase 2: Sucher-Unteraccounts + Verkaufspakete.

- Der Händler-Hauptaccount ist kostenlos und verwaltet seine Sucher.
- Jeder Sucher braucht ein persönliches Sucher-Abo (subscriptions mit
  subject_user_id) — vergeben durch den Admin, bis Online-Zahlung existiert.
- Verkaufspakete: rollierender Abrechnungszeitraum ab Buchungsdatum;
  gezählt werden NUR tatsächlich veröffentlichte Fahrzeuge (Phase 3),
  pro Inserat höchstens einmal je Zeitraum (counted_periods).
"""
import uuid
from datetime import datetime, timedelta, timezone
from typing import Optional

from fastapi import APIRouter, Body, Depends, HTTPException
from pydantic import BaseModel, EmailStr, Field, field_validator

import re

from auth import hash_password_async
from deps import (current_firma, current_user, db, get_subscription_status, log_activity, now_iso,
)
from routes.auth import _check_password_strength
from routes.bestand import current_haendler

router = APIRouter()


# ---------- Verkaufspakete (Preise Stand 05.08.2026) ----------
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
    import os
    return os.environ.get("SELF_SIGNUP", "false" if os.environ.get("APP_ENV", "").strip().lower() == "production" else "true").strip().lower() not in (
        "false", "0", "no", "off")


_NUR_BETREIBER = ("Sucher-Konten verwaltet der Betreiber. Bitte melde dich "
                  "bei uns — wir legen Zugänge an, setzen Passwörter und "
                  "entfernen Konten.")


@router.post("/dealer/sucher")
async def create_sucher(body: SucherIn, user=Depends(current_haendler)):
    if not _chef_verwaltung_erlaubt():
        raise HTTPException(403, _NUR_BETREIBER)
    existing = await db.users.find_one(
        {"email": {"$regex": f"^{re.escape(body.email)}$", "$options": "i"}})
    if existing:
        raise HTTPException(409, "E-Mail ist bereits registriert")
    sucher_id = str(uuid.uuid4())
    await db.users.insert_one({
        "id": sucher_id,
        "email": body.email,
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
async def list_sucher(user=Depends(current_haendler)):
    """Alle Sucher des Händlers inkl. Abo-Status und Monats-Statistik."""
    items = await db.users.find(
        {"dealer_id": user["dealer_id"], "role": "sucher"},
        {"_id": 0, "password_hash": 0},
    ).sort("created_at", 1).to_list(100)
    month_start = datetime.now(timezone.utc).replace(
        day=1, hour=0, minute=0, second=0, microsecond=0).isoformat()
    # ALLE Zusatzdaten in 3 Sammelabfragen statt 3 Abfragen JE SUCHER
    # (vorher: ~301 Einzelabfragen bei 100 Suchern).
    ids = [s["id"] for s in items]
    from deps import sub_status_from_doc
    subs = {}
    async for row in db.subscriptions.aggregate([
        {"$match": {"subject_user_id": {"$in": ids}}},
        {"$sort": {"created_at": -1}},
        {"$group": {"_id": "$subject_user_id", "sub": {"$first": "$$ROOT"}}},
    ]):
        subs[row["_id"]] = row["sub"]
    purchases = {row["_id"]: row["n"] async for row in db.generated_pdfs.aggregate([
        {"$match": {"user_id": {"$in": ids},
                    "created_at": {"$gte": month_start}}},
        {"$group": {"_id": "$user_id", "n": {"$sum": 1}}},
    ])}
    comparisons = {row["_id"]: row["n"] async for row in db.vehicle_comparisons.aggregate([
        {"$match": {"user_id": {"$in": ids},
                    "created_at": {"$gte": month_start}}},
        {"$group": {"_id": "$user_id", "n": {"$sum": 1}}},
    ])}
    return [{**s,
             "subscription": sub_status_from_doc(subs.get(s["id"])),
             "stats_month": {"kaeufe": purchases.get(s["id"], 0),
                             "vergleiche": comparisons.get(s["id"], 0)}}
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
    await db.users.delete_one({"id": sucher_id})
    # Persoenliche Reste mitloeschen (PR-Review 09/2026): das Sucher-Abo
    # blieb sonst bestehen und konnte Status-/Kuendigungslogik verwirren.
    await db.subscriptions.delete_many({"subject_user_id": sucher_id})
    if s.get("email"):
        await db.password_resets.delete_many({"email": s["email"]})
    await log_activity(user["dealer_id"], user["id"], "sucher.geloescht",
                       ref=sucher_id, meta={"email": s.get("email", "")})
    return {"ok": True}


# =========================================================
#                    VERKAUFSPAKETE
# =========================================================
def _current_period(period_start_iso: str) -> tuple:
    """Rollierender 30-Tage-Zeitraum ab Buchungsdatum. Liefert
    (period_key, start_iso, end_iso) für JETZT."""
    start = datetime.fromisoformat(period_start_iso)
    now = datetime.now(timezone.utc)
    if start.tzinfo is None:
        start = start.replace(tzinfo=timezone.utc)
    while start + timedelta(days=30) <= now:
        start = start + timedelta(days=30)
    end = start + timedelta(days=30)
    return (start.strftime("%Y%m%d"), start.isoformat(), end.isoformat())


async def get_sale_plan_status(dealer_id: str) -> dict:
    """Paket + Verbrauch des aktuellen Abrechnungszeitraums."""
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
            if vu.tzinfo is None:
                vu = vu.replace(tzinfo=timezone.utc)
            if vu < datetime.now(timezone.utc):
                return {"active": False, "expired": True, "tier": tier,
                        "label": meta.get("label", tier),
                        "valid_until": valid_until,
                        "quota": 0, "used": 0, "remaining": 0,
                        "period_start": None, "period_end": None,
                        "plans": SALE_PLANS}
        except (TypeError, ValueError):
            pass
    quota = plan.get("custom_quota") or meta.get("quota") or 0
    period_key, p_start, p_end = _current_period(plan.get("period_start", now_iso()))
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


@router.post("/dealer/sale-plan/upgrade-request")
async def sale_plan_upgrade_request(body: UpgradeRequestIn,
                                    user=Depends(current_haendler)):
    """Upgrade-/Enterprise-Anfrage — landet beim Admin mit Händler-ID,
    aktuellem Verbrauch, Wunschvolumen und Kontaktdaten."""
    status = await get_sale_plan_status(user["dealer_id"])
    dealer = await db.dealers.find_one({"id": user["dealer_id"]}, {"_id": 0})
    req_id = str(uuid.uuid4())
    await db.plan_requests.insert_one({
        "id": req_id,
        "dealer_id": user["dealer_id"],
        "company_name": (dealer or {}).get("company_name", ""),
        "contact_email": user.get("email", ""),
        "contact_phone": (dealer or {}).get("phone", ""),
        "current_tier": status.get("tier"),
        "current_usage": status.get("used"),
        "wanted_tier": body.wanted_tier,
        "message": body.message,
        "status": "offen",
        "created_at": now_iso(),
    })
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
    offen = await db.plan_requests.find_one(
        {"type": "sucher_abo", "subject_user_id": user["id"], "status": "offen"},
        {"_id": 0, "id": 1, "wanted_plan": 1})
    if offen:
        if offen.get("wanted_plan") != plan:
            await db.plan_requests.update_one(
                {"id": offen["id"]},
                {"$set": {"wanted_plan": plan,
                          "wanted": SUCHER_PLANS[plan]["label"] + " (eigener Zugang)",
                          "price": SUCHER_PLANS[plan]["price"],
                          "updated_at": now_iso()}})
        return {"ok": True, "request_id": offen["id"], "bereits_offen": True,
                "hinweis": "Deine Anfrage liegt bereits beim Betreiber."}
    dealer = await db.dealers.find_one({"id": user["dealer_id"]}, {"_id": 0})
    ist_sucher = user.get("role") == "sucher"
    name = (f"{user.get('first_name', '')} {user.get('last_name', '')}".strip()
            if ist_sucher else ((dealer or {}).get("contact_person") or "Chef"))
    req_id = str(uuid.uuid4())
    await db.plan_requests.insert_one({
        "id": req_id, "type": "sucher_abo",
        "dealer_id": user["dealer_id"],
        "subject_user_id": user["id"],
        "subject_role": user.get("role", "dealer"),
        "sucher_name": name or user.get("email", ""),
        "sucher_email": user.get("email", ""),
        "company_name": (dealer or {}).get("company_name", ""),
        "kunden_nr": (dealer or {}).get("kunden_nr"),
        "contact_email": user.get("email", ""),
        "contact_phone": (dealer or {}).get("phone", ""),
        "wanted": SUCHER_PLANS[plan]["label"] + " (eigener Zugang)",
        "wanted_plan": plan,
        "price": SUCHER_PLANS[plan]["price"],
        "status": "offen", "created_at": now_iso(),
    })
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
    Admin (manuelle Bezahlung/Freischaltung)."""
    plan = body.get("plan", "monthly")
    if plan not in SUCHER_PLANS:
        raise HTTPException(400, "Unbekannter Abo-Zeitraum")
    sucher = await db.users.find_one(
        {"id": sucher_id, "dealer_id": user["dealer_id"], "role": "sucher"},
        {"_id": 0, "email": 1, "first_name": 1, "last_name": 1})
    if not sucher:
        raise HTTPException(404, "Sucher nicht gefunden")
    dealer = await db.dealers.find_one({"id": user["dealer_id"]}, {"_id": 0})
    req_id = str(uuid.uuid4())
    await db.plan_requests.insert_one({
        "id": req_id, "type": "sucher_abo",
        "dealer_id": user["dealer_id"],
        "subject_user_id": sucher_id,
        "sucher_name": f"{sucher.get('first_name','')} {sucher.get('last_name','')}".strip(),
        "sucher_email": sucher.get("email", ""),
        "company_name": (dealer or {}).get("company_name", ""),
        "contact_email": user.get("email", ""),
        "contact_phone": (dealer or {}).get("phone", ""),
        "wanted": SUCHER_PLANS[plan]["label"],
        "wanted_plan": plan,
        "price": SUCHER_PLANS[plan]["price"],
        "status": "offen", "created_at": now_iso(),
    })
    await log_activity(user["dealer_id"], user["id"], "sucher.abo.anfrage",
                       ref=req_id, meta={"sucher": sucher_id, "plan": plan})
    return {"ok": True, "request_id": req_id,
            "hinweis": "Anfrage wurde an den Administrator übermittelt."}
