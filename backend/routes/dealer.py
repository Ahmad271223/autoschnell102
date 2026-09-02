"""Dealer endpoints: settings GET/PUT, active-profile, subscription info/cancel."""
from datetime import datetime, timezone
from typing import Optional
from urllib.parse import urlparse

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, field_validator

from deps import current_user, db, get_subscription_status, now_iso, current_firma
from mobile_service import DEFAULT_RULES, DEFAULT_EXPORT_RULES

router = APIRouter()


# ---------- Models ----------
class DealerProfile(BaseModel):
    company_name: Optional[str] = None
    contact_person: Optional[str] = None
    phone: Optional[str] = None
    whatsapp_number: Optional[str] = None
    email: Optional[str] = None
    address: Optional[str] = None
    zip_code: Optional[str] = None
    city: Optional[str] = None
    logo_url: Optional[str] = None
    opening_hours: Optional[str] = None

    # Profilfelder landen im Vertrags-PDF (enge Tabellenzellen). Cap 500.
    @field_validator("*")
    @classmethod
    def _cap(cls, v):
        if isinstance(v, str) and len(v) > 500:
            raise ValueError("Profilfeld zu lang (max. 500 Zeichen)")
        return v


class DealerSettingsIn(BaseModel):
    profile: Optional[DealerProfile] = None
    comparison_rules: Optional[dict] = None
    export_rules: Optional[dict] = None
    active_profile: Optional[str] = None  # "inland" | "export"
    email_subject: Optional[str] = None
    email_template: Optional[str] = None
    whatsapp_template: Optional[str] = None
    default_terms: Optional[str] = None             # AGB (always appended to PDF)
    default_special_agreements: Optional[str] = None  # Standard-Besondere-Vereinbarungen

    # DoS-Schutz: default_terms/default_special_agreements werden in JEDES
    # Vertrags-PDF injiziert — ohne Cap koennte ein 2-MB-Wert jeden Vertrag
    # lahmlegen. Freitext-Bloecke 20.000, Kurzfelder 500 Zeichen.
    @field_validator("email_subject", "email_template", "whatsapp_template",
                     "default_terms", "default_special_agreements",
                     "active_profile")
    @classmethod
    def _cap_text(cls, v, info):
        if isinstance(v, str):
            short = {"active_profile"}
            limit = 500 if info.field_name in short else 20000
            if len(v) > limit:
                raise ValueError(f"'{info.field_name}' zu lang (max. {limit} Zeichen)")
        return v


class ActiveProfileIn(BaseModel):
    active_profile: str  # "inland" | "export"


# ---------- Endpoints ----------
@router.get("/dealer/settings")
async def get_settings(user=Depends(current_firma)):
    """Wirksame Einstellungen aus Sicht des Nutzers: Chef-Vorgaben,
    bei Suchern überlagert von den eigenen persönlichen Anpassungen."""
    from deps import effective_dealer
    dealer = await db.dealers.find_one({"id": user["dealer_id"]}, {"_id": 0})
    # Back-fill neuer Felder für Bestandshändler, damit das Frontend sich
    # keine Sorgen um Legacy-Dokumente machen muss.
    if dealer:
        patch = {}
        if not dealer.get("comparison_rules"):
            dealer["comparison_rules"] = DEFAULT_RULES
            patch["comparison_rules"] = DEFAULT_RULES
        if not dealer.get("export_rules"):
            dealer["export_rules"] = DEFAULT_EXPORT_RULES
            patch["export_rules"] = DEFAULT_EXPORT_RULES
        if not dealer.get("active_profile"):
            dealer["active_profile"] = "inland"
            patch["active_profile"] = "inland"
        if patch:
            await db.dealers.update_one({"id": user["dealer_id"]}, {"$set": patch})
    if user.get("role") == "sucher":
        return await effective_dealer(user)
    return dealer


@router.put("/dealer/active-profile")
async def set_active_profile(body: ActiveProfileIn, user=Depends(current_firma)):
    """Schneller Profil-Wechsel vom Homebildschirm aus (Inland ↔ Export).
    Sucher wechseln nur IHR eigenes Profil (Override), nicht das des Chefs."""
    if body.active_profile not in ("inland", "export"):
        raise HTTPException(400, "active_profile muss 'inland' oder 'export' sein")
    if user.get("role") == "sucher":
        await db.users.update_one(
            {"id": user["id"]},
            {"$set": {"settings_override.active_profile": body.active_profile}},
        )
    else:
        await db.dealers.update_one(
            {"id": user["dealer_id"]},
            {"$set": {"active_profile": body.active_profile, "updated_at": now_iso()}},
        )
    return {"active_profile": body.active_profile}


def _validate_logo_url(url: Optional[str]) -> Optional[str]:
    """Only allow http/https URLs (or empty) for logo_url.

    Blocks javascript:, data:, file: and other schemes that could be used
    for XSS or SSRF once the URL is rendered in the frontend or fetched
    server-side (e.g. for PDF generation).
    """
    if not url:
        return url
    # Selbst hochgeladene Logos liegen im eigenen Storage und werden über
    # /api/files/<key> ausgeliefert — dieser relative Pfad ist erlaubt.
    if url.startswith("/api/files/"):
        return url
    try:
        scheme = urlparse(url).scheme.lower()
    except Exception:
        raise HTTPException(400, "logo_url ist keine gültige URL")
    if scheme not in ("http", "https"):
        raise HTTPException(400, "logo_url muss mit http:// oder https:// beginnen")
    return url


def _collect_settings_update(body: DealerSettingsIn) -> dict:
    update = {}
    if body.profile:
        for k, v in body.profile.model_dump(exclude_none=True).items():
            if k == "logo_url":
                v = _validate_logo_url(v)
            update[k] = v
    if body.comparison_rules is not None:
        update["comparison_rules"] = body.comparison_rules
    if body.export_rules is not None:
        update["export_rules"] = body.export_rules
    if body.active_profile is not None:
        if body.active_profile not in ("inland", "export"):
            raise HTTPException(400, "active_profile muss 'inland' oder 'export' sein")
        update["active_profile"] = body.active_profile
    if body.email_subject is not None:
        update["email_subject"] = body.email_subject
    if body.email_template is not None:
        update["email_template"] = body.email_template
    if body.whatsapp_template is not None:
        update["whatsapp_template"] = body.whatsapp_template
    if body.default_terms is not None:
        update["default_terms"] = body.default_terms
    if body.default_special_agreements is not None:
        update["default_special_agreements"] = body.default_special_agreements
    return update


@router.put("/dealer/settings")
async def update_settings(body: DealerSettingsIn, user=Depends(current_firma)):
    """Chef schreibt die Händler-Vorgaben. Sucher speichern dieselben Felder
    als PERSÖNLICHEN Override (users.settings_override) — die Chef-Werte
    bleiben unverändert und dienen weiter als Vorbefüllung."""
    from deps import SUCHER_SETTINGS_FIELDS, effective_dealer
    update = _collect_settings_update(body)
    if user.get("role") == "sucher":
        override_set = {f"settings_override.{k}": v for k, v in update.items()
                        if k in SUCHER_SETTINGS_FIELDS}
        if override_set:
            await db.users.update_one({"id": user["id"]}, {"$set": override_set})
        fresh_user = await db.users.find_one({"id": user["id"]}, {"_id": 0})
        return await effective_dealer(fresh_user)
    update["updated_at"] = now_iso()
    await db.dealers.update_one({"id": user["dealer_id"]}, {"$set": update})
    dealer = await db.dealers.find_one({"id": user["dealer_id"]}, {"_id": 0})
    return dealer


class LogoUploadIn(BaseModel):
    logo_b64: str  # data-URL oder reines Base64


@router.post("/dealer/logo")
async def upload_logo(body: LogoUploadIn, user=Depends(current_firma)):
    """Firmenlogo hochladen (max. 2 MB). Speichert im Storage und setzt
    logo_url auf den ausgelieferten /api/files/<key>-Pfad. Sucher setzen
    damit nur IHR persönliches Logo (Override), nicht das des Chefs."""
    import base64
    from storage_service import make_key, storage, StorageError
    try:
        raw = base64.b64decode(body.logo_b64.split(",")[-1], validate=False)
    except (ValueError, TypeError):
        raise HTTPException(400, "Logo konnte nicht gelesen werden")
    if not raw:
        raise HTTPException(400, "Leeres Logo")
    if len(raw) > 2 * 1024 * 1024:
        raise HTTPException(400, "Logo zu groß (max. 2 MB)")
    # Magic-Bytes-Pruefung: nur echte Bildformate (JPEG/PNG/WebP/GIF) —
    # beliebige Dateien liessen sich sonst als "logo.png" ablegen.
    from storage_service import validate_image_bytes
    try:
        validate_image_bytes(raw, wo="Logo")
    except StorageError as exc:
        raise HTTPException(400, str(exc))
    try:
        key = make_key("logo", user["dealer_id"], "logo.png")
        storage.save(key, raw)
    except StorageError as exc:
        raise HTTPException(400, f"Logo konnte nicht gespeichert werden: {exc}")
    logo_url = f"/api/files/{key}"
    if user.get("role") == "sucher":
        await db.users.update_one(
            {"id": user["id"]},
            {"$set": {"settings_override.logo_url": logo_url}})
    else:
        await db.dealers.update_one(
            {"id": user["dealer_id"]},
            {"$set": {"logo_url": logo_url, "updated_at": now_iso()}})
    return {"ok": True, "logo_url": logo_url}


# =========================================================
#                  ABO / SUBSCRIPTION
# =========================================================
@router.get("/dealer/subscription")
async def dealer_subscription(user=Depends(current_firma)):
    """Liefert dem Händler den aktuellen Abo-Stand: Plan, Status, Ablaufdatum,
    verbleibende Tage und ob Kündigen/Verlängern möglich ist.

    Status-Werte:
      - "active"    : Abo läuft
      - "cancelled" : Wurde gekündigt, läuft aber noch bis expires_at
      - "expired"   : Bereits abgelaufen
      - "none"      : Kein Abo vorhanden
    """
    sub_doc = await db.subscriptions.find_one(
        {"dealer_id": user["dealer_id"]},
        {"_id": 0},
        sort=[("created_at", -1)],
    )
    status = await get_subscription_status(user["dealer_id"])

    days_remaining = None
    expires_at = sub_doc.get("expires_at") if sub_doc else None
    if expires_at and status.get("plan") != "lifetime":
        try:
            ea = datetime.fromisoformat(expires_at)
            delta = ea - datetime.now(timezone.utc)
            days_remaining = max(0, delta.days)
        except Exception:
            days_remaining = None

    is_lifetime = status.get("plan") == "lifetime"
    raw_status = (sub_doc or {}).get("status", "active") if sub_doc else "none"

    return {
        "plan": status.get("plan"),
        "status": status["status"],          # zusammengefasster Live-Status
        "raw_status": raw_status,             # roher DB-Status (active/cancelled/...)
        "active": status["active"],
        "expires_at": expires_at,
        "days_remaining": days_remaining,
        "is_lifetime": is_lifetime,
        "can_cancel": bool(
            status["active"] and not is_lifetime and raw_status == "active"
        ),
        "can_renew": True,  # Verlängern ist immer erlaubt (neuer Checkout)
        "cancelled_at": (sub_doc or {}).get("cancelled_at"),
    }


@router.post("/dealer/subscription/cancel")
async def dealer_cancel_subscription(user=Depends(current_firma)):
    """Kündigt das aktuelle Abo. Wir setzen `status='cancelled'` UND merken
    uns das Datum, lassen das Abo aber bis `expires_at` weiter aktiv. Damit
    bekommt der Händler die bezahlte Zeit zu Ende und keine sofortige
    Sperre. Verlängern bleibt jederzeit möglich (neuer Checkout)."""
    if user.get("role") == "sucher":
        raise HTTPException(403, "Nur der Händler-Hauptaccount darf Abos verwalten")
    # DIESELBE Auswahlregel wie die Statusberechnung (deps): das
    # FIRMEN-Abo hat kein subject_user_id — sonst konnte die Kuendigung
    # versehentlich das juengste PERSOENLICHE Sucher-Abo treffen.
    sub = await db.subscriptions.find_one(
        {"dealer_id": user["dealer_id"],
         "subject_user_id": {"$exists": False}},
        sort=[("created_at", -1)],
    )
    if not sub:
        sub = await db.subscriptions.find_one(
            {"dealer_id": user["dealer_id"], "subject_user_id": None},
            sort=[("created_at", -1)],
        )
    if not sub:
        raise HTTPException(404, "Kein Abo vorhanden")
    if sub.get("plan") == "lifetime":
        raise HTTPException(400, "Lifetime-Abos können nicht gekündigt werden")
    if sub.get("status") == "cancelled":
        raise HTTPException(400, "Abo ist bereits gekündigt")

    await db.subscriptions.update_one(
        {"id": sub["id"]},
        {"$set": {
            "status": "cancelled",
            "cancelled_at": now_iso(),
        }},
    )
    return {
        "ok": True,
        "expires_at": sub.get("expires_at"),
        "message": "Abo gekündigt — läuft noch bis zum Ende der bezahlten Periode.",
    }
