"""Auth endpoints: register, login, logout, me, password-reset."""
import os
import hashlib
import re
import secrets
import uuid
from datetime import datetime, timedelta, timezone

from pymongo.errors import DuplicateKeyError
from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, EmailStr, Field, field_validator
from typing import Literal, Optional

from auth import (
    create_mfa_token, create_token, decode_mfa_token, hash_password_async,
    new_session_id, verify_password_async, _DUMMY_HASH,
)
from deps import (
    current_user, db, email_vergeben, get_subscription_status, now_iso, clean_doc,
    log_activity, log,
)
from mobile_service import DEFAULT_RULES
from rate_limiter import (client_ip, SlidingWindowRateLimiter, bekannte_ip_merken,
                          konto_fehlversuch, konto_gesperrt, konto_gesperrt_text,
                          login_ip_limiter,
                          login_limiter, login_schluessel, register_limiter)
from kontonummer import anmeldekennung, normalisieren, nummer_bedingung

# Passwort-Reset: eng limitiert (Missbrauch = E-Mail-Spam an fremde Adressen)
reset_limiter = SlidingWindowRateLimiter(max_attempts=5, window_seconds=3600, name="passwort-reset")
# Runde 11: zusaetzlich je KONTO (nicht nur je IP) — sonst konnte jeder,
# der die E-Mail kennt, mit immer neuen Anfragen den gueltigen Reset-Link
# des echten Nutzers laufend entwerten (Reset-DoS).
reset_konto_limiter = SlidingWindowRateLimiter(max_attempts=3, window_seconds=3600, name="passwort-reset-konto")
# Zweiter Anmeldeschritt (Authenticator-Code) mit eigenem Zaehler.
login_mfa_limiter = SlidingWindowRateLimiter(max_attempts=10, window_seconds=60, name="login-mfa")

router = APIRouter()


# ---------- Models ----------
# Zentrale Passwortregel (Audit 09/2026, Punkt 32) — gilt fuer ALLE Rollen.
from passwoerter import pruefe_passwort as _check_password_strength  # noqa: E402


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
    """Kontonummer (13.09.2026): Anmeldung mit `kontonummer` (Chef '10023',
    Sucher '10023-2', Kaeufer '10031'); der Super-Admin nutzt dasselbe Feld
    fuer seinen Benutzernamen. `email` bleibt als alter FELDNAME fuer
    gecachte Oberflaechen (Alias) — bis Schritt 5 auch fuer E-Mail-Adressen."""
    kontonummer: Optional[str] = Field(default=None, max_length=80)
    email: Optional[str] = Field(default=None, max_length=254)
    password: str


_NUMMERN_ROLLEN = ["dealer", "sucher", "b2b_buyer"]


async def _konto_fuer_login(kennung: str):
    """Kontosuche fuer /auth/login: Nummer -> Super-Admin-Benutzername ->
    (bis Schritt 5) alter E-Mail-Zweig fuer jede Rolle."""
    nr = normalisieren(kennung)
    if nr:
        return await db.users.find_one({"kontonummer": nummer_bedingung(nr),
                                        "role": {"$in": _NUMMERN_ROLLEN}})
    if "@" not in kennung:
        return await db.users.find_one({"username": kennung, "role": "admin",
                                        "is_super_admin": True})
    # ALTWEG – Schritt 5: Anmeldung per E-Mail (auch normale Admins, Tests).
    return await db.users.find_one(
        {"email": {"$regex": f"^{re.escape(kennung)}$", "$options": "i"}})


class TokenOut(BaseModel):
    token: str
    user: dict


class ZugangsAnfrageIn(BaseModel):
    """Öffentliche Zugangs-Anfrage von der Startseite (Beschluss 09/2026):
    Firmen registrieren sich nicht mehr selbst — sie stellen eine Anfrage,
    der Betreiber legt danach das Firmen-Konto an und schaltet frei.
    Kontonummer (13.09.2026): auch Zwischenhaendler (art=kaeufer) und Fahrer
    (art=fahrer) fragen hier an. Die E-Mail bleibt PFLICHT — sie ist der
    Rueckkanal fuer Kontonummer und Passwort."""
    art: Literal["firma", "kaeufer", "fahrer"] = "firma"
    company_name: str = Field(min_length=2, max_length=200)
    contact_person: str = Field(min_length=2, max_length=120)
    email: EmailStr
    phone: str = Field(default="", max_length=50)
    message: str = Field(default="", max_length=2000)
    sucher_anzahl: int = Field(default=0, ge=0, le=50)
    # Nur art=kaeufer: USt-IdNr./Handelsregister (Formatpruefung) und die
    # B2B-Bestaetigung (AGB §1, Pflicht)
    ust_id: str = Field(default="", max_length=40)
    gewerblich_bestaetigt: bool = False

    @field_validator("ust_id")
    @classmethod
    def _ustid(cls, v):
        from ustid import feld_pruefen
        return feld_pruefen(v)


_ANFRAGE_WUNSCH = {"firma": "Zugang zum Programm",
                   "kaeufer": "Marktplatz-Zugang (Zwischenhändler)",
                   "fahrer": "Fahrer-Zugang"}


@router.post("/zugang-anfrage")
async def zugang_anfrage(body: ZugangsAnfrageIn, request: Request):
    """Startseiten-Formular: 'Ich möchte das Programm nutzen.' Landet beim
    Betreiber unter Freischaltungen. Kein Konto, kein Passwort — der
    Betreiber legt das Firmen-Konto nach Kontaktaufnahme selbst an."""
    if body.art == "kaeufer" and not body.gewerblich_bestaetigt:
        raise HTTPException(400, "Bitte bestätige, dass du als Unternehmer handelst")
    ip = client_ip(request)
    if not await register_limiter.check(ip):
        raise HTTPException(429, "Zu viele Anfragen von dieser IP – bitte "
                                 "später erneut versuchen.")
    req_id = str(uuid.uuid4())
    sucher = body.sucher_anzahl if body.art == "firma" else 0
    doc = {
        "id": req_id, "type": "zugang", "art": body.art,
        "company_name": body.company_name.strip(),
        "contact_person": body.contact_person.strip(),
        "contact_email": body.email.strip().lower(),
        "contact_phone": body.phone.strip(),
        "message": body.message.strip(),
        "sucher_anzahl": sucher,
        "wanted": (_ANFRAGE_WUNSCH[body.art]
                   + (f" + {sucher} Sucher" if sucher else "")),
        "status": "offen", "created_at": now_iso(),
    }
    if body.art == "kaeufer":
        doc["ust_id"] = body.ust_id
    # Kontonummer (13.09.2026, Gegenpruefung): Das Formular verlangt die
    # Bestaetigung (Unternehmer, AGB, Datenschutz) fuer JEDE Art — der
    # Zeitpunkt wird festgehalten (AGB §1). Pflicht im Backend bleibt sie
    # nur fuer Zwischenhaendler.
    if body.gewerblich_bestaetigt:
        doc["gewerblich_bestaetigt_am"] = now_iso()
    await db.plan_requests.insert_one(doc)
    await log_activity("", "", "zugang.anfrage",
                       ref=req_id, meta={"firma": body.company_name, "art": body.art,
                                         "email": body.email, "ip": ip})
    return {"ok": True, "hinweis": "Anfrage ist eingegangen — wir melden uns "
                                   "und schalten dein Firmen-Konto frei."}


# Selbst-Registrierung von Firmen: seit 09/2026 standardmäßig AUS —
# der Betreiber legt Firmen-Konten nach einer Zugangs-Anfrage selbst an
# (docker-compose setzt SELF_SIGNUP=false). In Entwicklung/CI bleibt die
# Route aktiv, damit Tests und lokales Ausprobieren funktionieren.
_ENTWICKLUNGS_UMGEBUNGEN = {"development", "dev", "local", "test", "ci"}


def _self_signup_enabled() -> bool:
    """Runde 11: fail-closed. Vorher war die Selbst-Registrierung AN, sobald
    APP_ENV auf einem neuen Server fehlte — jeder konnte sich per
    POST /api/auth/register ein aktives Firmen-Konto samt JWT holen.
    Jetzt: nur mit ausdruecklichem SELF_SIGNUP=true oder in einer
    ausdruecklich als Entwicklung/Test benannten Umgebung."""
    wert = os.environ.get("SELF_SIGNUP", "").strip().lower()
    if wert:
        return wert in ("true", "1", "yes", "on")
    return os.environ.get("APP_ENV", "").strip().lower() in _ENTWICKLUNGS_UMGEBUNGEN


# ---------- Endpoints ----------
def _vertragstext_start() -> str:
    """Startertext fuer neue Firmen. Spaeter Import: pdf_service zieht
    reportlab nach und wird beim Anmelden nicht gebraucht."""
    from pdf_service import VERTRAGSTEXT_START
    return VERTRAGSTEXT_START


@router.post("/auth/register", response_model=TokenOut)
async def register(body: RegisterIn, request: Request):
    if not _self_signup_enabled():
        raise HTTPException(403, "Die Selbst-Registrierung ist deaktiviert. "
                                 "Bitte stelle eine Zugangs-Anfrage — der "
                                 "Betreiber legt dein Firmen-Konto an und "
                                 "schaltet dich frei.")
    # Rate-limit: 5 registrations per IP per hour.
    ip = client_ip(request)
    if not await register_limiter.check(ip):
        raise HTTPException(429, "Zu viele Registrierungen von dieser IP – bitte später erneut versuchen.")
    # E-Mail KANONISCH speichern (klein, getrimmt) und case-insensitiv auf
    # Duplikate pruefen — vorher konnten User@x.de und user@x.de als zwei
    # Konten entstehen, waehrend der Login case-insensitiv irgendeins fand.
    email = body.email.strip().lower()
    # Runde 13: B5 — vorher nur users; jetzt plattformweit (auch
    # driver_accounts), Meldung bewusst ohne Kontotyp.
    if await email_vergeben(email):
        raise HTTPException(409, "E-Mail bereits registriert")
    user_id = str(uuid.uuid4())
    dealer_id = str(uuid.uuid4())
    sid = new_session_id()
    user_doc = {  # kontonummer setzt kontenanlage (Chef = str(kunden_nr))
        "id": user_id, "email": email,
        "password_hash": await hash_password_async(body.password),
        "role": "dealer", "active": True,
        "dealer_id": dealer_id, "current_session_id": sid,
        "created_at": now_iso(),
    }
    dealer_doc = {
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
        # Runde 26: EIN Feld fuer Vertragsbedingungen. Der Startertext
        # enthaelt die vier Klauseln UND die frueheren AGB-Punkte.
        "default_terms": "",
        "digital_vertragstext": _vertragstext_start(),
        "default_special_agreements": "",
        "created_at": now_iso(),
    }
    # Zwei Inserts ohne Transaktion (PR-Review 09/2026): erst das Haendler-
    # profil, dann der Benutzer. Scheitert der Benutzer-Insert, wird das
    # Profil wieder entfernt — es bleibt nie ein aktives, aber unvollstaendiges
    # Konto zurueck (vorher: Benutzer ohne Haendlerprofil).
    # Kontonummer (13.09.2026): beides ueber kontenanlage (Chef bekommt
    # kontonummer = str(kunden_nr), die Firma wird bei Fehlern entfernt).
    from kontenanlage import firma_mit_chef_anlegen
    try:
        erg = await firma_mit_chef_anlegen(db, dealer_doc, user_doc)
    except DuplicateKeyError:
        # Rennen zweier Registrierungen mit derselben E-Mail
        raise HTTPException(409, "E-Mail bereits registriert")
    except Exception:
        log.exception("Registrierung: Benutzer-Insert fehlgeschlagen")
        raise HTTPException(500, "Registrierung fehlgeschlagen — bitte erneut "
                                 "versuchen.")
    user_doc["kontonummer"] = erg["kontonummer"]
    user_doc["kontonummer_basis"] = erg["kunden_nr"]
    token = create_token(user_id, sid)
    await log_activity(dealer_id, user_id, "auth.registriert",
                       meta={"email": body.email, "ip": ip,
                             "kontonummer": erg["kontonummer"]})
    user = clean_doc({k: v for k, v in user_doc.items() if k != "password_hash"})
    return TokenOut(token=token, user=user)


def geraet_kurz(request) -> str:
    """Runde 19: kurze, lesbare Geraetebeschreibung aus dem User-Agent
    ("Chrome auf Android", "Safari auf iPhone", "Firefox auf Windows") —
    fuer die Abmelde-Meldung ("dein Konto wurde erneut angemeldet von ...")
    und das Login-Protokoll. Kein Fingerprinting, nur Browser + System."""
    try:
        ua = (request.headers.get("user-agent") or "") if request is not None else ""
    except Exception:
        ua = ""
    if not ua:
        return ""
    u = ua.lower()
    if "iphone" in u:
        system = "iPhone"
    elif "ipad" in u:
        system = "iPad"
    elif "android" in u:
        system = "Android"
    elif "windows" in u:
        system = "Windows"
    elif "mac os" in u or "macintosh" in u:
        system = "Mac"
    elif "linux" in u:
        system = "Linux"
    else:
        system = "unbekanntem System"
    if "edg/" in u:
        browser = "Edge"
    elif "opr/" in u or "opera" in u:
        browser = "Opera"
    elif "samsungbrowser" in u:
        browser = "Samsung-Browser"
    elif "firefox/" in u:
        browser = "Firefox"
    elif "chrome/" in u or "crios/" in u:
        browser = "Chrome"
    elif "safari/" in u:
        browser = "Safari"
    else:
        browser = "Browser"
    return f"{browser} auf {system}"


async def _sitzung_ausstellen(user: dict, ip: str, geraet: str = "") -> dict:
    """Passwort (und ggf. 2. Faktor) sind geprueft: neue Einzel-Sitzung,
    Token, Audit. Runde 19: Zeitpunkt und Geraet der Sitzung werden am Konto
    festgehalten, damit ein verdraengtes Geraet erfaehrt, WER es verdraengt
    hat (deps.sitzung_beendet_grund)."""
    sid = new_session_id()
    await db.users.update_one({"id": user["id"]}, {"$set": {
        "current_session_id": sid, "current_session_seit": now_iso(),
        "current_session_geraet": geraet[:60], "current_session_ip": ip}})
    # Kontonummer (13.09.2026): Anmeldung ist vollstaendig (beim Super-Admin
    # nach der 2FA) — diese IP gilt fuer den Konto-Limiter ab jetzt als bekannt.
    await bekannte_ip_merken(db, "users", user["id"], ip)
    meta = {"email": user.get("email", ""), "ip": ip, "geraet": geraet[:60]}
    if user.get("kontonummer"):
        meta["kontonummer"] = user["kontonummer"]
    elif user.get("username"):
        meta["username"] = user["username"]
    await log_activity(user.get("dealer_id", ""), user["id"], "auth.login", meta=meta)
    token = create_token(user["id"], sid)
    user_clean = {k: v for k, v in user.items() if k not in ("password_hash", "_id", "mfa")}
    user_clean["current_session_id"] = sid
    user_clean["mfa_aktiv"] = bool((user.get("mfa") or {}).get("aktiv"))
    return {"token": token, "user": user_clean}


class MfaLoginIn(BaseModel):
    mfa_token: str = Field(min_length=20, max_length=1000)
    code: str = Field(min_length=6, max_length=40)


@router.post("/auth/login/mfa")
async def login_mfa(body: MfaLoginIn, request: Request):
    """Zweiter Schritt der Anmeldung fuer Konten mit Zwei-Faktor (Admin/
    Super-Admin): Authenticator-Code oder einmaliger Wiederherstellungscode.
    5 Fehlversuche -> 15 Minuten Sperre fuer den zweiten Faktor."""
    import mfa as _mfa
    ip = client_ip(request)
    # Runde 11: eigener Zaehler fuer den zweiten Schritt. Vorher kostete
    # ein Admin-Login mit MFA zwei der zehn Passwort-Versuche je Minute —
    # hinter einer Firmen-NAT sperrten sich korrekte Logins gegenseitig.
    if not await login_mfa_limiter.check(ip):
        raise HTTPException(429, "Zu viele Anmeldeversuche – bitte 60 Sekunden warten.")
    try:
        payload = decode_mfa_token(body.mfa_token)
    except Exception:
        raise HTTPException(401, "Anmeldung abgelaufen — bitte erneut mit Passwort anmelden")
    user = await db.users.find_one({"id": payload.get("sub")})
    if not user or not user.get("active"):
        raise HTTPException(401, "Anmeldung abgelaufen — bitte erneut mit Passwort anmelden")
    m = user.get("mfa") or {}
    # Runde 10: Das Zwischen-Token muss zum HEUTIGEN Kontozustand passen.
    # Vorher stellte ein altes Token nach MFA-Reset oder -Abschaltung eine
    # Sitzung ohne zweiten Faktor aus, und ein Passwortwechsel entwertete
    # es nicht. Jetzt: Zustand abweichend oder MFA inzwischen aus -> neu
    # mit Passwort anmelden.
    from auth import mfa_zustand
    if payload.get("z") != mfa_zustand(user) or not m.get("aktiv"):
        raise HTTPException(401, "Anmeldung abgelaufen — bitte erneut mit Passwort anmelden")
    sperre = m.get("gesperrt_bis")
    if sperre and sperre > now_iso():
        raise HTTPException(429, "Zweiter Faktor vorübergehend gesperrt — bitte in 15 Minuten erneut versuchen")
    secret = _mfa.entschluesseln(m.get("secret", "")) or ""
    code = body.code.strip()
    zaehler = _mfa.code_pruefen(secret, code, int(m.get("letzter_zaehler", -1))) if secret else None
    if zaehler is not None:
        # Runde 9: Verbrauch des Codes ATOMAR. Zwei gleichzeitige Anfragen
        # mit demselben Code sahen vorher beide den alten Zaehler und kamen
        # beide durch. Jetzt gewinnt genau eine: nur wer den Zaehler
        # wirklich hochsetzt, bekommt eine Sitzung.
        res = await db.users.update_one(
            {"id": user["id"],
             "$or": [{"mfa.letzter_zaehler": {"$lt": zaehler}},
                     {"mfa.letzter_zaehler": {"$exists": False}}]},
            {"$set": {"mfa.letzter_zaehler": zaehler, "mfa.fehlversuche": 0}})
        if res.modified_count == 0:
            raise HTTPException(401, "Dieser Code wurde gerade schon verwendet — bitte den nächsten Code aus der App abwarten")
    elif secret and _mfa.code_pruefen(secret, code, -1) is not None:
        # Richtiger, aber schon benutzter Code (z.B. direkt nach der
        # Einrichtung): kein Fehlversuch, sondern klarer Hinweis.
        raise HTTPException(401, "Dieser Code wurde gerade schon verwendet — bitte den nächsten Code aus der App abwarten")
    else:
        h = _mfa.code_hash(code)
        # Wiederherstellungscode ebenfalls atomar verbrauchen ($pull mit
        # Bedingung): der Code kann nur EINMAL durchgehen, auch bei zwei
        # gleichzeitigen Anfragen.
        res = await db.users.update_one(
            {"id": user["id"], "mfa.wiederherstellung": h},
            {"$pull": {"mfa.wiederherstellung": h}, "$set": {"mfa.fehlversuche": 0}})
        if res.modified_count == 1:
            uebrig = len([x for x in (m.get("wiederherstellung") or []) if x != h])
            await log_activity("", user["id"], "auth.login.mfa.wiederherstellungscode",
                               meta={"uebrig": uebrig, "ip": ip})
        else:
            # Runde 11: Fehlversuche ATOMAR zaehlen ($inc). Vorher las jeder
            # Request den alten Stand und schrieb "alt + 1" zurueck — fuenf
            # gleichzeitige falsche Codes zaehlten als einer, die Sperre
            # nach fuenf Fehlern liess sich mit parallelen Anfragen umgehen.
            from pymongo import ReturnDocument
            doc = await db.users.find_one_and_update(
                {"id": user["id"]}, {"$inc": {"mfa.fehlversuche": 1}},
                projection={"_id": 0, "mfa.fehlversuche": 1},
                return_document=ReturnDocument.AFTER)
            fehl = int(((doc or {}).get("mfa") or {}).get("fehlversuche", 1))
            if fehl >= 5:
                await db.users.update_one(
                    {"id": user["id"], "mfa.fehlversuche": {"$gte": 5}},
                    {"$set": {"mfa.gesperrt_bis": (datetime.now(timezone.utc)
                                                   + timedelta(minutes=15)).isoformat(),
                              "mfa.fehlversuche": 0}})
            await log_activity("", user["id"], "auth.login.mfa.fehlgeschlagen", meta={"ip": ip})
            raise HTTPException(401, "Code ungültig")
    return await _sitzung_ausstellen(user, ip, geraet_kurz(request))


@router.post("/auth/login")
async def login(body: LoginIn, request: Request):
    # Runde 26 (12.09.2026, Vorgabe Ahmad): Der Zaehler haengt am KONTO
    # (IP + Kennung), nicht an der IP allein — sonst sperren sich 30 Sucher
    # im selben Buero gegenseitig aus. Das weiter gefasste IP-Limit bremst
    # weiterhin Rateversuche ueber viele Konten.
    ip = client_ip(request)
    # Kontonummer (13.09.2026): neues Feld kontonummer, email als Alias.
    identifier = (body.kontonummer or body.email or "").strip()
    schluessel = login_schluessel(ip, identifier)
    if not await login_limiter.check(schluessel):
        raise HTTPException(429, "Zu viele Anmeldeversuche für dieses Konto – bitte 60 Sekunden warten.")
    if not await login_ip_limiter.check(ip):
        raise HTTPException(429, "Zu viele Anmeldeversuche aus diesem Netz – bitte 60 Sekunden warten.")

    if not identifier:
        raise HTTPException(401, "E-Mail/Benutzername oder Passwort falsch")
    # Suchreihenfolge: Nummer, Benutzername NUR fuer den Super-Admin, dann
    # (bis Schritt 5) der alte E-Mail-Zweig. Email lookup is case-insensitive.
    user = await _konto_fuer_login(identifier)
    # Konto-Limiter VOR bcrypt — gleicher Text und Weg fuer vorhandene und
    # unbekannte Kennungen (keine Aufzaehlung der Nummern).
    konto_k = anmeldekennung(identifier)
    if await konto_gesperrt(konto_k, ip, user):
        raise HTTPException(429, konto_gesperrt_text())
    # Always run bcrypt (constant-time) to prevent user-enumeration via timing.
    pw_hash = user["password_hash"] if user else _DUMMY_HASH
    if not await verify_password_async(body.password, pw_hash) or not user:
        await konto_fehlversuch(konto_k, ip)
        # Audit: fehlgeschlagener Versuch (nur Kennung + IP, nie das Passwort).
        await log_activity("", "", "auth.login.fehlgeschlagen",
                           meta={"identifier": (normalisieren(identifier) or identifier)[:120],
                                 "ip": ip})
        raise HTTPException(401, "E-Mail/Benutzername oder Passwort falsch")
    if not user.get("active"):
        raise HTTPException(403, "Account ist deaktiviert")
    # Passwort stimmte: Zaehler dieses Kontos leeren, damit fruehere
    # Fehlversuche eine richtige Anmeldung spaeter nicht blockieren.
    await login_limiter.reset(schluessel)
    # Kontonummer (13.09.2026): den Konto-Zaehler (login_konto_limiter) bei
    # Erfolg bewusst NICHT leeren — sonst bekaeme ein Angreifer, der die
    # Nummer ueber viele IPs probiert, mit jeder Anmeldung des echten Nutzers
    # wieder volle Versuche. Der Nutzer selbst ist von seinen bekannten IPs
    # ohnehin frei; der Zaehler laeuft mit dem Fenster ab, vorher hebt nur der
    # Betreiber die Sperre auf (Passwort setzen, anmeldesperre_aufheben.py).
    if (user.get("mfa") or {}).get("aktiv"):
        # Zwei-Faktor (Abo-Audit 09/2026): noch KEINE Sitzung — erst der
        # zweite Faktor in /auth/login/mfa stellt das Sitzungs-Token aus.
        await log_activity("", user["id"], "auth.login.mfa.angefordert", meta={"ip": ip})
        return {"mfa_erforderlich": True, "mfa_token": create_mfa_token(user),
                "hinweis": "Bitte den 6-stelligen Code aus der Authenticator-App eingeben."}
    return await _sitzung_ausstellen(user, ip, geraet_kurz(request))


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
    if user.get("role") == "sucher":
        # Runde 12: Sucher sehen vom Haendler-Dokument nur ihre Einstellungsfelder.
        from routes.dealer import _sucher_sicht
        dealer = _sucher_sicht(dealer)
    # Standardtext der digitalen Vertragsausfertigung fuer die Einstellungen
    from routes.dealer import _mit_digital_standard
    dealer = _mit_digital_standard(dealer)
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
    # Runde 13: B6 Passwort-Reset-Konflikt — vorher wurde driver_accounts nur
    # befragt, wenn users KEINEN Treffer hatte; wer mit derselben Adresse
    # Sucher/Haendler UND Fahrer war, konnte sein Fahrerkonto nie
    # zuruecksetzen. Jetzt: beide Sammlungen unabhaengig, jedes berechtigte
    # Konto bekommt seinen eigenen Link (Altbestand; neue Doppelkonten
    # verhindert B5). Antwort bleibt in allen Faellen generisch.
    filt = {"email": {"$regex": f"^{re.escape(body.email)}$", "$options": "i"}}
    kandidaten = []
    u = await db.users.find_one(
        filt, {"_id": 0, "id": 1, "email": 1, "role": 1, "active": 1})
    # Kein Selbst-Reset fuer Sucher — aber die gleiche generische Antwort,
    # damit Aussenstehende Rollen nicht erraten koennen; das Sucher-Konto
    # blockiert aber nicht mehr das Fahrerkonto derselben Adresse.
    if u and u.get("active", True) and u.get("role") != "sucher":
        kandidaten.append((u, "user"))
    # Fahrer-Konten leben in driver_accounts (PR-Review 09/2026: vorher
    # hatten Fahrer keinerlei Wiederherstellungsweg).
    d = await db.driver_accounts.find_one(
        filt, {"_id": 0, "id": 1, "email": 1, "active": 1})
    if d and d.get("active", True):
        kandidaten.append(({**d, "role": "driver"}, "driver"))
    if not kandidaten:
        return generic

    # Basis-Adresse NUR aus der Server-Konfiguration. Frueher kam sie aus
    # Origin/Referer — beides bestimmt der Aufrufer, wodurch ein Angreifer
    # den Reset-Link in einer fremden E-Mail auf seine eigene Seite lenken
    # konnte (Reset-Link-Poisoning).
    frontend = (os.environ.get("FRONTEND_URL")
                or "http://localhost:3000").split("?")[0].rstrip("/")
    versendet = 0
    versand_fehler = False
    for u, konto_typ in kandidaten:
        # Je Konto begrenzt — und ZAEHLT erst hier, damit Anfragen fuer
        # unbekannte Adressen den Zaehler eines echten Kontos nicht treffen.
        # (Die IDs beider Sammlungen sind eigene UUIDs — kein Schluessel-
        # Konflikt.) Scheitert ein Konto am Limit, wird das andere trotzdem
        # bedient.
        if not await reset_konto_limiter.check(f"konto:{u['id']}"):
            continue
        token = secrets.token_urlsafe(32)
        neu_id = str(uuid.uuid4())
        await db.password_resets.insert_one({
            "id": neu_id,
            "user_id": u["id"],
            "account_type": konto_typ,
            "token_hash": _hash_reset_token(token),
            "expires_at": (datetime.now(timezone.utc)
                           + timedelta(minutes=RESET_TOKEN_TTL_MINUTES)).isoformat(),
            "used": False,
            "requested_ip": ip,
            "created_at": now_iso(),
            # TTL-Index (server.ensure_indexes): Token-Hash + IP verschwinden
            # 7 Tage nach Anforderung automatisch (Runde 5).
            "loeschen_ab": datetime.now(timezone.utc) + timedelta(days=7),
        })
        link = f"{frontend}/passwort-reset?token={token}"
        # Mailtext je Kontotyp kennzeichnen, damit der Empfaenger zwei
        # Links auseinanderhalten kann.
        if konto_typ == "driver":
            betreff = "Passwort zurücksetzen – AutoSchnell Fahrer-App"
            wofuer = " für dein Fahrer-Konto"
        else:
            betreff = "Passwort zurücksetzen – AutoSchnell"
            wofuer = ""
        # Runde 11: ERST senden, DANN die aelteren Links entwerten. Vorher wurden
        # die alten Links zuerst geloescht — fiel danach der Mailversand aus,
        # hatte der Nutzer weder den alten noch einen neuen Link.
        try:
            await send_email(
                u["email"],
                betreff,
                f"Hallo,\n\nüber diesen Link kannst du{wofuer} ein neues Passwort "
                f"setzen (gültig {RESET_TOKEN_TTL_MINUTES} Minuten):\n\n{link}\n\n"
                "Wenn du das nicht angefordert hast, ignoriere diese E-Mail einfach — "
                "dein Passwort bleibt unverändert.",
                absender_name="AutoSchnell",
            )
        except Exception:
            await db.password_resets.delete_one({"id": neu_id})
            log.exception("Passwort-Reset: E-Mail-Versand fehlgeschlagen (%s)", konto_typ)
            versand_fehler = True
            continue
        versendet += 1
        await db.password_resets.delete_many(
            {"user_id": u["id"], "account_type": konto_typ, "id": {"$ne": neu_id}})
        await log_activity("", u["id"], "auth.passwort.reset.angefordert",
                           meta={"ip": ip, "konto": konto_typ})
    if versand_fehler and not versendet:
        raise HTTPException(503, "Die E-Mail konnte gerade nicht versendet werden — "
                                 "bitte in ein paar Minuten erneut versuchen.")
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
    konten = db.driver_accounts if doc.get("account_type") == "driver" else db.users
    u = await konten.find_one({"id": doc["user_id"]},
                              {"_id": 0, "id": 1, "role": 1, "active": 1})
    if not u or not u.get("active", True) or u.get("role") == "sucher":
        raise invalid
    # ATOMAR entwerten — und zwar VOR der Passwortaenderung (Runde 5):
    # nur der ERSTE parallele Bestaetiger gewinnt; vorher wurde das
    # Passwort schon geaendert, bevor der Token endgueltig verbraucht war.
    # Claim-Zustand (Audit 09/2026, Punkt 31): das Token wird zuerst nur
    # "beansprucht" (claimed_at); scheitert das Speichern des Passworts,
    # wird der Claim zurueckgesetzt und der Link bleibt gueltig. Ein Claim
    # aelter als 60 s gilt als haengengeblieben und darf uebernommen werden.
    # Parallele Doppelnutzung bleibt ausgeschlossen (atomarer Claim).
    claim_frist = (datetime.now(timezone.utc) - timedelta(seconds=60)).isoformat()
    beansprucht = await db.password_resets.find_one_and_update(
        {"id": doc["id"], "used": {"$ne": True},
         "$or": [{"claimed_at": {"$exists": False}}, {"claimed_at": None},
                 {"claimed_at": {"$lt": claim_frist}}]},
        {"$set": {"claimed_at": now_iso()}})
    if not beansprucht:
        raise invalid
    try:
        await konten.update_one(
            {"id": u["id"]},
            {"$set": {"password_hash": await hash_password_async(body.new_password),
                      # Alle bestehenden Sessions beenden (Single-Session strikt)
                      "current_session_id": None}})
    except Exception:
        await db.password_resets.update_one({"id": doc["id"]},
                                            {"$set": {"claimed_at": None}})
        log.exception("Passwort-Reset: Speichern fehlgeschlagen")
        raise HTTPException(500, "Passwort konnte nicht gespeichert werden — "
                                 "bitte den Link erneut verwenden")
    await db.password_resets.update_one(
        {"id": doc["id"]}, {"$set": {"used": True, "used_at": now_iso()}})
    await log_activity("", u["id"], "auth.passwort.reset.durchgefuehrt",
                       meta={"ip": ip})
    return {"ok": True, "hinweis": "Passwort geändert – bitte neu anmelden."}
