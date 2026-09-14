"""Auth endpoints: login (Kontonummer), logout, me, Zugangs-Anfrage.

Kontonummer (13.09.2026), Schritt 5: Selbst-Registrierung und Passwort-Reset
per E-Mail gibt es nicht mehr — die Routen antworten 410 mit Hinweis, damit
gecachte alte Oberflaechen keine 404/422 zeigen."""
import uuid
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, EmailStr, Field, field_validator
from typing import Literal, Optional

from auth import (
    create_mfa_token, create_token, decode_mfa_token,
    new_session_id, verify_password_async, _DUMMY_HASH,
)
from deps import (
    current_user, db, get_subscription_status, now_iso,
    log_activity, log_activity_sicher,
)
from rate_limiter import (client_ip, SlidingWindowRateLimiter, bekannte_ip_merken,
                          konto_fehlversuch, konto_gesperrt, konto_gesperrt_text,
                          login_ip_limiter,
                          login_limiter, login_schluessel, register_limiter)
from kontonummer import (anmeldekennung, kaeufer_normalisieren, kennung_normalisieren,
                         normalisieren, nummer_bedingung)

# Zweiter Anmeldeschritt (Authenticator-Code) mit eigenem Zaehler.
login_mfa_limiter = SlidingWindowRateLimiter(max_attempts=10, window_seconds=60, name="login-mfa")

router = APIRouter()


# ---------- Models ----------


class LoginIn(BaseModel):
    """Kontonummer (13.09.2026): Anmeldung mit `kontonummer` (Chef '10023',
    Sucher '10023-2', Kaeufer '10031'); der Super-Admin nutzt dasselbe Feld
    fuer seinen Benutzernamen. `email` bleibt nur als alter FELDNAME fuer
    gecachte Oberflaechen (Alias) — gesucht wird darueber NICHT mehr per
    E-Mail-Adresse (Schritt 5)."""
    kontonummer: Optional[str] = Field(default=None, max_length=80)
    email: Optional[str] = Field(default=None, max_length=254)
    password: str


_NUMMERN_ROLLEN = ["dealer", "sucher", "b2b_buyer"]


async def _konto_fuer_login(kennung: str):
    """Kontosuche fuer /auth/login: Nummer -> Super-Admin-Benutzername.
    Kontonummer (13.09.2026), Schritt 5: KEIN E-Mail-Zweig mehr — eine
    Adresse (oder der Benutzername eines normalen Admins) findet kein Konto
    und bekommt dieselbe 401 wie ein falsches Passwort."""
    nr = normalisieren(kennung)
    if nr:
        return await db.users.find_one({"kontonummer": nummer_bedingung(nr),
                                        "role": {"$in": _NUMMERN_ROLLEN}})
    # Kaeufer-Code (14.09.2026): Zwischenhaendler duerfen sich auch hier
    # anmelden. Findet der Code kein Konto, bleibt der Benutzername-Zweig fuer
    # den Super-Admin (ein Benutzername wie 'ADMIN7' saehe sonst wie ein Code aus).
    code = kaeufer_normalisieren(kennung)
    if code:
        u = await db.users.find_one({"kontonummer": nummer_bedingung(code),
                                     "role": "b2b_buyer"})
        if u:
            return u
    if "@" not in kennung:
        return await db.users.find_one({"username": kennung, "role": "admin",
                                        "is_super_admin": True})
    return None


# Kontonummer (13.09.2026), Schritt 5: ein neutraler Text fuer alle drei
# Anmeldemasken (auch der Super-Admin sieht ihn).
LOGIN_FALSCH = "Kontonummer oder Passwort falsch"
NUR_BETREIBER_KONTEN = ("Konten legt der Betreiber an – bitte Zugang unter "
                        "/anfrage anfragen")


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
    await log_activity_sicher("", "", "zugang.anfrage",
                       ref=req_id, meta={"firma": body.company_name, "art": body.art,
                                         "email": body.email, "ip": ip})
    return {"ok": True, "hinweis": "Anfrage ist eingegangen — wir melden uns "
                                   "und schalten dein Firmen-Konto frei."}


# ---------- Endpoints ----------
@router.post("/auth/register")
async def register():
    """Kontonummer (13.09.2026), Schritt 5: Selbst-Registrierung entfernt —
    Firmen, Sucher, Kaeufer und Fahrer legt nur der Super-Admin an. 410 mit
    Hinweis auf die Zugangs-Anfrage (gecachte alte Oberflaechen)."""
    raise HTTPException(410, NUR_BETREIBER_KONTEN)


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
    await log_activity_sicher(user.get("dealer_id", ""), user["id"], "auth.login", meta=meta)
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
            await log_activity_sicher("", user["id"], "auth.login.mfa.wiederherstellungscode",
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
            await log_activity_sicher("", user["id"], "auth.login.mfa.fehlgeschlagen", meta={"ip": ip})
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
        raise HTTPException(401, LOGIN_FALSCH)
    # Suchreihenfolge: Nummer, Benutzername NUR fuer den Super-Admin.
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
        await log_activity_sicher("", "", "auth.login.fehlgeschlagen",
                           meta={"identifier": (kennung_normalisieren(identifier) or identifier)[:120],
                                 "ip": ip})
        raise HTTPException(401, LOGIN_FALSCH)
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
        await log_activity_sicher("", user["id"], "auth.login.mfa.angefordert", meta={"ip": ip})
        return {"mfa_erforderlich": True, "mfa_token": create_mfa_token(user),
                "hinweis": "Bitte den 6-stelligen Code aus der Authenticator-App eingeben."}
    return await _sitzung_ausstellen(user, ip, geraet_kurz(request))


@router.post("/auth/logout")
async def logout(user=Depends(current_user)):
    await db.users.update_one({"id": user["id"]}, {"$set": {"current_session_id": None}})
    await log_activity_sicher(user.get("dealer_id", ""), user["id"], "auth.logout",
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
#                 PASSWORT VERGESSEN
# =========================================================
# Kontonummer (13.09.2026), Schritt 5: kein Reset-Link per E-Mail mehr —
# ein neues Passwort setzt ausschliesslich der Betreiber (POST
# /admin/users/{id}/password bzw. /admin/drivers/{id}/password). Beide alten
# Routen antworten 410, ohne Konten zu suchen oder Mails zu verschicken.
NUR_BETREIBER_PASSWORT = "Ein neues Passwort vergibt der Betreiber – bitte melden"


@router.post("/auth/password-reset/request")
async def password_reset_request():
    raise HTTPException(410, NUR_BETREIBER_PASSWORT)


@router.post("/auth/password-reset/confirm")
async def password_reset_confirm():
    raise HTTPException(410, NUR_BETREIBER_PASSWORT)
