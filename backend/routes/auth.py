"""Auth endpoints: register, login, logout, me, password-reset."""
import os
import hashlib
import re
import secrets
import uuid
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, EmailStr, Field, field_validator
from typing import Optional

from auth import (
    create_token, hash_password_async, new_session_id,
    verify_password_async, _DUMMY_HASH,
)
from deps import (
    current_user, db, get_subscription_status, now_iso, clean_doc,
    log_activity,
)
from mobile_service import DEFAULT_RULES
from rate_limiter import client_ip, SlidingWindowRateLimiter, login_limiter, register_limiter

# Passwort-Reset: eng limitiert (Missbrauch = E-Mail-Spam an fremde Adressen)
reset_limiter = SlidingWindowRateLimiter(max_attempts=5, window_seconds=3600, name="passwort-reset")

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
    ip = client_ip(request)
    if not await register_limiter.check(ip):
        raise HTTPException(429, "Zu viele Registrierungen von dieser IP – bitte später erneut versuchen.")
    # E-Mail KANONISCH speichern (klein, getrimmt) und case-insensitiv auf
    # Duplikate pruefen — vorher konnten User@x.de und user@x.de als zwei
    # Konten entstehen, waehrend der Login case-insensitiv irgendeins fand.
    email = body.email.strip().lower()
    existing = await db.users.find_one(
        {"email": {"$regex": f"^{re.escape(email)}$", "$options": "i"}})
    if existing:
        raise HTTPException(409, "E-Mail bereits registriert")
    user_id = str(uuid.uuid4())
    dealer_id = str(uuid.uuid4())
    sid = new_session_id()
    user_doc = {
        "id": user_id, "email": email,
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
    await log_activity(dealer_id, user_id, "auth.registriert",
                       meta={"email": body.email, "ip": ip})
    user = clean_doc({k: v for k, v in user_doc.items() if k != "password_hash"})
    return TokenOut(token=token, user=user)


@router.post("/auth/login", response_model=TokenOut)
async def login(body: LoginIn, request: Request):
    # Rate-limit by client IP (10 attempts / 60 s).
    ip = client_ip(request)
    if not await login_limiter.check(ip):
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
        # Audit: fehlgeschlagener Versuch (nur Kennung + IP, nie das Passwort).
        await log_activity("", "", "auth.login.fehlgeschlagen",
                           meta={"identifier": identifier[:120], "ip": ip})
        raise HTTPException(401, "E-Mail/Benutzername oder Passwort falsch")
    if not user.get("active"):
        raise HTTPException(403, "Account ist deaktiviert")
    sid = new_session_id()
    await db.users.update_one({"id": user["id"]}, {"$set": {"current_session_id": sid}})
    await log_activity(user.get("dealer_id", ""), user["id"], "auth.login",
                       meta={"email": user.get("email", ""), "ip": ip})
    token = create_token(user["id"], sid)
    user_clean = {k: v for k, v in user.items() if k not in ("password_hash", "_id")}
    user_clean["current_session_id"] = sid
    return TokenOut(token=token, user=user_clean)


@router.post("/auth/logout")
async def logout(user=Depends(current_user)):
    await db.users.update_one({"id": user["id"]}, {"$set": {"current_session_id": None}})
    await log_activity(user.get("dealer_id", ""), user["id"], "auth.logout",
                       meta={"email": user.get("email", "")})
    return {"ok": True}


@router.get("/auth/me")
async def me(user=Depends(current_user)):
    from deps import effective_dealer, subscription_for
    sub = await subscription_for(user)
    dealer = await effective_dealer(user)
    return {"user": user, "subscription": sub, "dealer": dealer}


# =========================================================
#                 PASSWORT VERGESSEN / RESET
# =========================================================
# Ablauf: E-Mail eingeben -> Token per Mail (1 h gültig, einmalig) -> neues
# Passwort setzen. Sucher sind AUSGENOMMEN: deren Passwort setzt nur der
# Händler-Hauptaccount zurück (Team-Seite) — bewusste Entscheidung 08/2026.
RESET_TOKEN_TTL_MINUTES = 60


class ResetRequestIn(BaseModel):
    email: EmailStr


class ResetConfirmIn(BaseModel):
    token: str = Field(min_length=20, max_length=200)
    new_password: str = Field(min_length=8, max_length=200)

    @field_validator("new_password")
    @classmethod
    def password_strength(cls, v: str) -> str:
        return _check_password_strength(v)


def _hash_reset_token(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


@router.post("/auth/password-reset/request")
async def password_reset_request(body: ResetRequestIn, request: Request):
    """Immer generische Antwort (kein User-Enumeration-Leak). Versand nur,
    wenn der Account existiert, kein Sucher ist und SMTP konfiguriert ist."""
    from email_service import email_configured, send_email
    ip = client_ip(request)
    if not await reset_limiter.check(ip):
        raise HTTPException(429, "Zu viele Anfragen – bitte später erneut versuchen.")
    if not email_configured():
        # Ehrlich statt Sackgasse: ohne SMTP kann kein Link verschickt werden.
        raise HTTPException(
            503, "Der E-Mail-Versand ist noch nicht eingerichtet. Bitte wende "
                 "dich an den Administrator, um dein Passwort zurückzusetzen.")

    generic = {"ok": True, "hinweis": "Falls die Adresse registriert ist, wurde "
                                      "eine E-Mail mit dem Reset-Link versendet."}
    u = await db.users.find_one(
        {"email": {"$regex": f"^{re.escape(body.email)}$", "$options": "i"}},
        {"_id": 0, "id": 1, "email": 1, "role": 1, "active": 1})
    if not u or not u.get("active", True):
        return generic
    if u.get("role") == "sucher":
        # Kein Selbst-Reset für Sucher — aber die gleiche generische Antwort,
        # damit Außenstehende Rollen nicht erraten können.
        return generic

    token = secrets.token_urlsafe(32)
    await db.password_resets.delete_many({"user_id": u["id"]})
    await db.password_resets.insert_one({
        "id": str(uuid.uuid4()),
        "user_id": u["id"],
        "token_hash": _hash_reset_token(token),
        "expires_at": (datetime.now(timezone.utc)
                       + timedelta(minutes=RESET_TOKEN_TTL_MINUTES)).isoformat(),
        "used": False,
        "requested_ip": ip,
        "created_at": now_iso(),
    })
    # Basis-Adresse NUR aus der Server-Konfiguration. Frueher kam sie aus
    # Origin/Referer — beides bestimmt der Aufrufer, wodurch ein Angreifer
    # den Reset-Link in einer fremden E-Mail auf seine eigene Seite lenken
    # konnte (Reset-Link-Poisoning).
    frontend = (os.environ.get("FRONTEND_URL")
                or "http://localhost:3000").split("?")[0].rstrip("/")
    link = f"{frontend}/passwort-reset?token={token}"
    await send_email(
        u["email"],
        "Passwort zurücksetzen – AutoSchnell",
        f"Hallo,\n\nüber diesen Link kannst du ein neues Passwort setzen "
        f"(gültig {RESET_TOKEN_TTL_MINUTES} Minuten):\n\n{link}\n\n"
        "Wenn du das nicht angefordert hast, ignoriere diese E-Mail einfach — "
        "dein Passwort bleibt unverändert.",
    )
    await log_activity("", u["id"], "auth.passwort.reset.angefordert",
                       meta={"ip": ip})
    return generic


@router.post("/auth/password-reset/confirm")
async def password_reset_confirm(body: ResetConfirmIn, request: Request):
    ip = client_ip(request)
    if not await reset_limiter.check(ip):
        raise HTTPException(429, "Zu viele Versuche – bitte später erneut versuchen.")
    doc = await db.password_resets.find_one(
        {"token_hash": _hash_reset_token(body.token), "used": False}, {"_id": 0})
    invalid = HTTPException(400, "Der Link ist ungültig oder abgelaufen. "
                                 "Bitte fordere einen neuen an.")
    if not doc:
        raise invalid
    try:
        if datetime.fromisoformat(doc["expires_at"]) < datetime.now(timezone.utc):
            raise invalid
    except ValueError:
        raise invalid
    u = await db.users.find_one({"id": doc["user_id"]},
                                {"_id": 0, "id": 1, "role": 1, "active": 1})
    if not u or not u.get("active", True) or u.get("role") == "sucher":
        raise invalid
    await db.users.update_one(
        {"id": u["id"]},
        {"$set": {"password_hash": await hash_password_async(body.new_password),
                  # Alle bestehenden Sessions beenden (Single-Session strikt)
                  "current_session_id": None}})
    # ATOMAR entwerten: nur der ERSTE parallele Bestaetiger gewinnt —
    # vorher konnten zwei gleichzeitige Aufrufe denselben Token nutzen.
    verbraucht = await db.password_resets.find_one_and_update(
        {"id": doc["id"], "used": {"$ne": True}},
        {"$set": {"used": True, "used_at": now_iso()}})
    if not verbraucht:
        raise invalid
    await log_activity("", u["id"], "auth.passwort.reset.durchgefuehrt",
                       meta={"ip": ip})
    return {"ok": True, "hinweis": "Passwort geändert – bitte neu anmelden."}
