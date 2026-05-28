"""Auth endpoints: register, login, logout, me."""
import re
import uuid

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, EmailStr, Field, field_validator
from typing import Optional

from auth import (
    create_token, hash_password_async, new_session_id,
    verify_password_async, _DUMMY_HASH,
)
from deps import (
    current_user, db, get_subscription_status, now_iso, clean_doc,
)
from mobile_service import DEFAULT_RULES
from rate_limiter import login_limiter, register_limiter

router = APIRouter()


# ---------- Models ----------
_SPECIAL_CHARS = set("!@#$%^&*()_+-=[]{}|;':\",./<>?")


def _check_password_strength(pw: str) -> str:
    """Enforce minimum password security requirements (shared by all auth models)."""
    if len(pw) < 8:
        raise ValueError("Passwort muss mindestens 8 Zeichen lang sein")
    has_digit = any(c.isdigit() for c in pw)
    has_special = any(c in _SPECIAL_CHARS for c in pw)
    if not has_digit and not has_special:
        raise ValueError("Passwort muss mindestens eine Ziffer oder ein Sonderzeichen enthalten")
    return pw


class RegisterIn(BaseModel):
    email: EmailStr
    password: str = Field(min_length=8)
    company_name: str
    contact_person: Optional[str] = None
    phone: Optional[str] = None

    @field_validator("password")
    @classmethod
    def password_strength(cls, v: str) -> str:
        return _check_password_strength(v)


class LoginIn(BaseModel):
    """Login akzeptiert sowohl E-Mail (für Händler) als auch Benutzername
    (z.B. für Plattform-Admins). Wir behalten den Feld-Namen `email` aus
    Backwards-Compat-Gründen — der Wert wird im Endpoint validiert/aufgelöst."""
    email: str  # email-or-username
    password: str


class TokenOut(BaseModel):
    token: str
    user: dict


# ---------- Endpoints ----------
@router.post("/auth/register", response_model=TokenOut)
async def register(body: RegisterIn, request: Request):
    # Rate-limit: 5 registrations per IP per hour.
    ip = (request.client.host if request.client else None) or "unknown"
    if not register_limiter.check(ip):
        raise HTTPException(429, "Zu viele Registrierungen von dieser IP – bitte später erneut versuchen.")
    existing = await db.users.find_one({"email": body.email})
    if existing:
        raise HTTPException(409, "E-Mail bereits registriert")
    user_id = str(uuid.uuid4())
    dealer_id = str(uuid.uuid4())
    sid = new_session_id()
    user_doc = {
        "id": user_id, "email": body.email,
        "password_hash": await hash_password_async(body.password),
        "role": "dealer", "active": True,
        "dealer_id": dealer_id, "current_session_id": sid,
        "created_at": now_iso(),
    }
    await db.users.insert_one(user_doc)
    await db.dealers.insert_one({
        "id": dealer_id, "user_id": user_id,
        "company_name": body.company_name,
        "contact_person": body.contact_person or "",
        "phone": body.phone or "",
        "email": body.email, "address": "", "zip_code": "", "city": "",
        "logo_url": "",
        "whatsapp_number": body.phone or "",
        "comparison_rules": DEFAULT_RULES,
        "email_subject": "Kaufvertrag für Ihr Fahrzeug",
        "email_template": (
            "Guten Tag,\n\nanbei sende ich Ihnen den Kaufvertrag für Ihr Fahrzeug.\n"
            "Bitte prüfen Sie die Angaben und geben Sie mir kurz Rückmeldung.\n\n"
            "Mit freundlichen Grüßen\n{händler_name}"
        ),
        "whatsapp_template": (
            "Hallo,\nhier ist der Kaufvertrag für Ihr Fahrzeug.\n"
            "Bitte einmal prüfen und kurz bestätigen. Danke!"
        ),
        "default_terms": (
            "Allgemeine Geschäftsbedingungen (AGB):\n"
            "1. Das Fahrzeug wird unter Ausschluss jeglicher Sachmängelhaftung verkauft, "
            "soweit gesetzlich zulässig (§ 444 BGB bleibt unberührt).\n"
            "2. Der Käufer ist Händler im Sinne des § 14 BGB. Der Erwerb erfolgt zum Zwecke "
            "des gewerblichen Wiederverkaufs.\n"
            "3. Eigentumsübergang erfolgt erst nach vollständigem Zahlungseingang.\n"
            "4. Mündliche Nebenabreden bestehen nicht. Änderungen oder Ergänzungen bedürfen "
            "der Schriftform.\n"
            "5. Erfüllungsort und Gerichtsstand ist der Sitz des Käufers, soweit gesetzlich "
            "zulässig."
        ),
        "default_special_agreements": "",
        "created_at": now_iso(),
    })
    token = create_token(user_id, sid)
    user = clean_doc({k: v for k, v in user_doc.items() if k != "password_hash"})
    return TokenOut(token=token, user=user)


@router.post("/auth/login", response_model=TokenOut)
async def login(body: LoginIn, request: Request):
    # Rate-limit by client IP (10 attempts / 60 s).
    ip = (request.client.host if request.client else None) or "unknown"
    if not login_limiter.check(ip):
        raise HTTPException(429, "Zu viele Anmeldeversuche – bitte 60 Sekunden warten.")

    # Accept email OR username. Email lookup is case-insensitive.
    identifier = (body.email or "").strip()
    if not identifier:
        raise HTTPException(401, "E-Mail/Benutzername oder Passwort falsch")
    user = None
    if "@" in identifier:
        user = await db.users.find_one({"email": {"$regex": f"^{re.escape(identifier)}$", "$options": "i"}})
    else:
        user = await db.users.find_one({"username": identifier})
        if not user:
            # Fallback: account where email == identifier (case-insensitive)
            user = await db.users.find_one({"email": {"$regex": f"^{re.escape(identifier)}$", "$options": "i"}})
    # Always run bcrypt (constant-time) to prevent user-enumeration via timing.
    pw_hash = user["password_hash"] if user else _DUMMY_HASH
    if not await verify_password_async(body.password, pw_hash) or not user:
        raise HTTPException(401, "E-Mail/Benutzername oder Passwort falsch")
    if not user.get("active"):
        raise HTTPException(403, "Account ist deaktiviert")
    sid = new_session_id()
    await db.users.update_one({"id": user["id"]}, {"$set": {"current_session_id": sid}})
    token = create_token(user["id"], sid)
    user_clean = {k: v for k, v in user.items() if k not in ("password_hash", "_id")}
    user_clean["current_session_id"] = sid
    return TokenOut(token=token, user=user_clean)


@router.post("/auth/logout")
async def logout(user=Depends(current_user)):
    await db.users.update_one({"id": user["id"]}, {"$set": {"current_session_id": None}})
    return {"ok": True}


@router.get("/auth/me")
async def me(user=Depends(current_user)):
    sub = await get_subscription_status(user["dealer_id"])
    dealer = await db.dealers.find_one({"id": user["dealer_id"]}, {"_id": 0})
    return {"user": user, "subscription": sub, "dealer": dealer}
