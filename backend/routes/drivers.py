"""Driver endpoints: dealer-driver linking + standalone driver-app accounts."""
import base64
import hashlib
import secrets
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Literal, Optional

from fastapi import APIRouter, Depends, HTTPException, Request, Response  # Request already imported
from fastapi.security import HTTPAuthorizationCredentials
import jwt                                   # PyJWT
from pydantic import BaseModel, EmailStr, Field, field_validator

from auth import (
    JWT_ALG, JWT_SECRET, _DUMMY_HASH, decode_token,
    hash_password_async, verify_password_async,
)
from deps import bearer, current_user, db, log_activity, now_iso, current_firma
# Zentrale Passwortregeln (Pruefbericht 09/2026, Punkt 32): dieselbe
# Pruefung wie fuer Firma/Sucher/Admin — vorher hatte drivers.py eine
# eigene, schwaechere Kopie (8 Zeichen, keine Blockliste, keine 72-Byte-
# bcrypt-Grenze).
from passwoerter import pruefe_passwort as _check_password_strength
from rate_limiter import client_ip, driver_login_limiter, driver_register_limiter
from snapshot_service import get_object as snapshot_get_object

import logging
log = logging.getLogger("autohandel")

router = APIRouter()


# ---------- Models ----------
class DriverAccountRegister(BaseModel):
    email: EmailStr
    password: str = Field(min_length=8)
    display_name: str = Field(min_length=2, max_length=120)

    @field_validator("password")
    @classmethod
    def password_strength(cls, v: str) -> str:
        return _check_password_strength(v)


class DriverAccountLogin(BaseModel):
    email: EmailStr
    password: str


class DriverProfileUpdate(BaseModel):
    display_name: Optional[str] = Field(default=None, max_length=120)


class DriverPasswordIn(BaseModel):
    """Fahrer aendert sein eigenes Passwort (PR-Review 09/2026: vorher gab es
    fuer Fahrer weder Wechsel noch Wiederherstellung)."""
    current_password: str = Field(min_length=1, max_length=200)
    new_password: str = Field(min_length=8, max_length=200)

    @field_validator("new_password")
    @classmethod
    def password_strength(cls, v: str) -> str:
        return _check_password_strength(v)


class DriverLinkIn(BaseModel):
    driver_code: str


class DriverStatusIn(BaseModel):
    """Fahrer-App: Termin als abgeholt / nicht abgeholt markieren."""
    status: Literal["abgeholt", "nicht abgeholt"]
    notes: Optional[str] = None


class DriverZuteilungIn(BaseModel):
    """Fahrer-App: zugeteilte Fahrt annehmen oder ablehnen (09/2026)."""
    action: Literal["annehmen", "ablehnen"]
    grund: Optional[str] = None


class DeviationIn(BaseModel):
    """Eine bei der Abholung festgestellte Abweichung."""
    field: Literal["mileage", "keys", "tires", "damage", "warning_light",
                   "equipment", "documents", "other"] = "other"
    label: str = Field(min_length=1, max_length=200)      # z.B. "Kratzer hinten rechts"
    expected: str = Field(default="", max_length=200)     # laut Vertrag/Inserat
    actual: str = Field(default="", max_length=200)       # vor Ort festgestellt
    note: str = Field(default="", max_length=2000)
    photo_b64: Optional[str] = Field(default=None, max_length=8_000_000)  # ~6 MB Bild


class PickupReportIn(BaseModel):
    """Digitaler Abholbericht des Fahrers (ersetzt Zettelwirtschaft)."""
    mileage_at_pickup: Optional[int] = Field(default=None, ge=0, le=3_000_000)
    keys_count: Optional[int] = Field(default=None, ge=0, le=10)
    fuel_level: Optional[Literal["leer", "1/4", "1/2", "3/4", "voll"]] = None
    deviations: list[DeviationIn] = Field(default_factory=list, max_length=30)
    notes: str = Field(default="", max_length=5000)


# ---------- Driver code generation & auth ----------
DRIVER_CODE_ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"  # ohne I,O,0,1


def generate_driver_code() -> str:
    """Public Fahrer-ID im Format `FD-XXXXXXXX` (8 Zeichen, gut lesbar)."""
    suffix = "".join(secrets.choice(DRIVER_CODE_ALPHABET) for _ in range(8))
    return f"FD-{suffix}"


async def ensure_unique_driver_code() -> str:
    for _ in range(30):
        code = generate_driver_code()
        existing = await db.driver_accounts.find_one(
            {"driver_code": code}, {"_id": 0, "id": 1},
        )
        if not existing:
            return code
    raise HTTPException(500, "Konnte keinen eindeutigen Fahrer-Code erzeugen")


def create_driver_token(driver_id: str, session_id: str) -> str:
    """JWT für Fahrer-Accounts.  Enthält jetzt eine Session-ID (sid) damit
    alte Tokens nach erneutem Login automatisch ungültig werden.
    TTL: 7 Tage (statt 30) — reduziert das Risiko bei Token-Leakage in URLs."""
    exp = datetime.now(timezone.utc) + timedelta(days=7)
    payload = {
        "sub": driver_id,
        "sid": session_id,
        "role": "driver_account",
        "exp": exp,
    }
    return jwt.encode(payload, JWT_SECRET, algorithm=JWT_ALG)


async def current_driver(request: Request, auth: Optional[str] = None,
                         creds: Optional[HTTPAuthorizationCredentials] = Depends(bearer)):
    """Token NUR via `Authorization: Bearer ...`.

    ?auth=<token> in der URL wird NICHT mehr akzeptiert: der Token landete
    damit in Browser-Verlauf, Proxy- und Server-Logs. Die Fahrer-App laedt
    PDFs seit 08/2026 per fetch mit Authorization-Header (openDriverPdf).
    """
    token = None
    if creds and creds.credentials:
        token = creds.credentials
    if not token:
        raise HTTPException(401, "Nicht authentifiziert")
    try:
        payload = decode_token(token)
    except Exception:
        raise HTTPException(401, "Token ungültig")
    if payload.get("role") != "driver_account":
        raise HTTPException(403, "Fahrer-Token erforderlich")
    driver = await db.driver_accounts.find_one(
        {"id": payload.get("sub")}, {"_id": 0, "password_hash": 0},
    )
    if not driver or not driver.get("active", True):
        raise HTTPException(401, "Fahrer-Account deaktiviert")
    # Single-Session STRIKT (wie bei deps.current_user): die Session-ID im
    # Token muss exakt der gespeicherten entsprechen. Die fruehere Toleranz
    # fuer Konten ohne gespeicherte Session machte jeden Sitzungs-Widerruf
    # (Passwortwechsel/Reset setzt die Session auf leer) wirkungslos —
    # der alte Token blieb gueltig (Negativtest 09/2026).
    token_sid = payload.get("sid")
    stored_sid = driver.get("current_session_id")
    if not stored_sid or token_sid != stored_sid:
        raise HTTPException(401, "Session beendet (anderes Gerät aktiv oder "
                                 "abgemeldet)")
    return driver


# Termine in diesen Zustaenden nehmen keine Fahrer-Aenderungen mehr an
# (PR-Review 09/2026): Status-Umschaltung und neue Berichtsversionen sind
# gesperrt; eine Korrektur laeuft ueber den Haendler (Termin wieder oeffnen).
_TERMIN_ABGESCHLOSSEN = {"abgeholt", "nicht abgeholt", "storniert", "erledigt"}
# Gegenstueck: in diesen Zustaenden darf der Fahrer noch vom Termin getrennt
# werden, ohne dass eine historische Zuordnung verloren geht. Fehlender oder
# leerer Status zaehlt als "offen" (wie ueberall: appt.get("status") or "offen").
_TERMIN_OFFEN = {"offen", "verschoben", "bestätigt", "in Bearbeitung"}
_OFFEN_WERTE: List[Any] = sorted(_TERMIN_OFFEN) + ["", None]


async def _verknuepfte_dealer_ids(driver_id: str) -> List[str]:
    """Firmen, in deren Fahrerliste der Fahrer AKTUELL steht."""
    links = await db.dealer_drivers.find(
        {"driver_account_id": driver_id}, {"_id": 0, "dealer_id": 1},
    ).to_list(500)
    return [link["dealer_id"] for link in links if link.get("dealer_id")]


async def _zugriff_pruefen(appt: dict, driver: dict) -> None:
    """Fahrer-Zugriff auf einen Termin (Pruefbericht 09/2026): die Zuweisung
    (appointments.driver_id) allein reicht NICHT. Entfernt der Haendler den
    Fahrer aus seiner Liste, behalten abgeschlossene Termine die historische
    Zuordnung als Beweis — der Fahrer selbst darf Abholauftrag, Vertrag,
    Protokoll und Bericht dieser Firma dann aber nicht mehr laden. Deshalb
    muss ZUSAETZLICH die Verknuepfung dealer_drivers aktuell bestehen.
    Antwort ist bewusst 404 (kein Hinweis, dass der Termin existiert).
    Funktioniert fuer jedes Dokument mit dealer_id (Termin, Bericht)."""
    link = await db.dealer_drivers.find_one(
        {"dealer_id": appt.get("dealer_id"), "driver_account_id": driver["id"]},
        {"_id": 1},
    )
    if not link:
        raise HTTPException(404, "Termin nicht gefunden")


# =========================================================
#   DEALER → FAHRER  (Händler verwaltet seine Fahrer-Liste
#   über die öffentlichen Fahrer-Codes der Fahrer-Accounts)
# =========================================================
async def _load_dealer_driver(dealer_id: str, driver_account_id: str) -> Optional[dict]:
    """Fahrer-Account + Dealer-Override (falls vorhanden) zusammenführen."""
    da = await db.driver_accounts.find_one(
        {"id": driver_account_id}, {"_id": 0, "password_hash": 0},
    )
    if not da:
        return None
    link = await db.dealer_drivers.find_one(
        {"dealer_id": dealer_id, "driver_account_id": driver_account_id},
        {"_id": 0},
    )
    if not link:
        return None
    return {
        "id": da["id"],
        "driver_code": da.get("driver_code"),
        "name": link.get("display_name") or da.get("display_name"),
        "email": da.get("email"),
        "active": da.get("active", True),
        "added_at": link.get("added_at"),
    }


@router.post("/drivers/add")
async def add_driver_by_code(body: DriverLinkIn, user=Depends(current_firma)):
    # Fahrer HINZUFUEGEN ist Chefsache — Sucher sehen die Liste und
    # weisen Termine zu, veraendern die Firmen-Fahrerliste aber nicht.
    if user.get("role") != "dealer":
        raise HTTPException(403, "Nur der Händler-Hauptaccount darf Fahrer "
                                 "hinzufügen")
    """Händler fügt Fahrer per öffentlichem Code hinzu."""
    code = (body.driver_code or "").strip().upper()
    if not code:
        raise HTTPException(400, "Bitte Fahrer-Code eingeben")
    da = await db.driver_accounts.find_one({"driver_code": code}, {"_id": 0})
    if not da:
        raise HTTPException(404, "Kein Fahrer mit diesem Code gefunden")
    if not da.get("active", True):
        raise HTTPException(409, "Dieser Fahrer-Account ist deaktiviert")
    existing = await db.dealer_drivers.find_one(
        {"dealer_id": user["dealer_id"], "driver_account_id": da["id"]},
    )
    if existing:
        raise HTTPException(409, "Fahrer ist bereits in deiner Liste")
    await db.dealer_drivers.insert_one({
        "id": str(uuid.uuid4()),
        "dealer_id": user["dealer_id"],
        "driver_account_id": da["id"],
        "display_name": da.get("display_name"),
        "added_at": now_iso(),
    })
    return await _load_dealer_driver(user["dealer_id"], da["id"]) or {}


@router.get("/drivers")
async def list_drivers(user=Depends(current_firma)):
    links = await db.dealer_drivers.find(
        {"dealer_id": user["dealer_id"]}, {"_id": 0},
    ).to_list(500)
    out = []
    for link in links:
        info = await _load_dealer_driver(user["dealer_id"], link["driver_account_id"])
        if info:
            out.append(info)
    out.sort(key=lambda d: (d.get("name") or "").lower())
    return out


@router.delete("/drivers/{driver_id}")
async def delete_driver(driver_id: str, user=Depends(current_firma)):
    """Verknüpfung entfernen. Der Fahrer-Account selbst bleibt bestehen."""
    if user.get("role") != "dealer":
        raise HTTPException(403, "Nur der Händler-Hauptaccount darf Fahrer "
                                 "entfernen")
    res = await db.dealer_drivers.delete_one(
        {"dealer_id": user["dealer_id"], "driver_account_id": driver_id},
    )
    if not res.deleted_count:
        raise HTTPException(404, "Fahrer nicht in deiner Liste")
    # Termine dieser Firma vom Fahrer trennen (Pruefbericht 09/2026):
    #  * OFFENE Fahrten verlieren die Zuweisung komplett — der Chef teilt
    #    neu zu; eine noch unbeantwortete Annahme-Anfrage wird mit
    #    zuteilung=None verworfen.
    #  * ABGESCHLOSSENE Fahrten behalten die Zuordnung als Beweis fuer den
    #    Chef, aber in driver_id_hist statt driver_id: driver_id ist das
    #    Zugriffsfeld der Fahrer-App, und ein entfernter Fahrer soll die
    #    Unterlagen dieser Firma nicht weiter laden koennen (zusaetzlich
    #    prueft jede Fahrer-Route die Verknuepfung, siehe _zugriff_pruefen).
    #    Vorher blieb driver_id auf "abgeholt"-Terminen stehen und der
    #    Fahrer kam weiter an Abholauftrag, Vertrag und Protokoll.
    # Die Sitzung des Fahrers wird NICHT beendet: er arbeitet ggf. fuer
    # andere Firmen weiter; die Verknuepfungspruefung reicht.
    jetzt = now_iso()
    offen = await db.appointments.update_many(
        {"dealer_id": user["dealer_id"], "driver_id": driver_id,
         "status": {"$in": _OFFEN_WERTE}},
        {"$unset": {"driver_id": ""},
         "$set": {"zuteilung": None, "updated_at": jetzt}},
    )
    # Update-Pipeline: driver_id atomar nach driver_id_hist verschieben.
    geschlossen = await db.appointments.update_many(
        {"dealer_id": user["dealer_id"], "driver_id": driver_id},
        [{"$set": {"driver_id_hist": "$driver_id",
                   "updated_at": {"$literal": jetzt}}},
         {"$unset": "driver_id"}],
    )
    return {"ok": True,
            "offene_termine_getrennt": offen.modified_count,
            "abgeschlossene_termine_archiviert": geschlossen.modified_count}


@router.get("/drivers/{driver_id}/conflicts")
async def driver_conflicts(driver_id: str, date: str, user=Depends(current_firma)):
    """Warnung: Gibt dem Händler zurück, ob der Fahrer an diesem Datum
    bereits eine Fahrt hat. Blockiert nicht – der Händler entscheidet selbst."""
    if not date:
        return {"conflicts": []}
    link = await db.dealer_drivers.find_one(
        {"dealer_id": user["dealer_id"], "driver_account_id": driver_id},
    )
    if not link:
        raise HTTPException(404, "Fahrer nicht in deiner Liste")
    # Abgeschlossene/stornierte Fahrten belegen den Fahrer nicht mehr. Der
    # alte Filter schloss nur den nie benutzten Status "abgeschlossen" aus
    # und meldete damit jede erledigte Fahrt als Konflikt (Pruefbericht 09/2026).
    conflicts = await db.appointments.find(
        {"driver_id": driver_id, "pickup_date": date,
         "status": {"$nin": sorted(_TERMIN_ABGESCHLOSSEN)}},
        {"_id": 0, "id": 1, "dealer_id": 1, "pickup_time": 1,
         "pickup_address": 1, "title": 1},
    ).to_list(50)
    for c in conflicts:
        c["is_own"] = c.get("dealer_id") == user["dealer_id"]
        if not c["is_own"]:
            c.pop("pickup_address", None)
            c["title"] = "Andere Fahrt"
        c.pop("dealer_id", None)
    return {"conflicts": conflicts, "count": len(conflicts)}


async def fahrer_konto_anonymisieren(db, driver_id: str) -> dict:
    """Spuren eines GELOESCHTEN Fahrer-Kontos pseudonymisieren (DSGVO,
    Pruefbericht 09/2026). Wird von DELETE /admin/drivers/{id} aufgerufen;
    das Konto selbst (driver_accounts) loescht die Admin-Route. `db` kommt
    als Parameter, damit der Aufrufer (Admin-Route, Tests) die Datenbank
    bestimmt.

    Das Pseudonym ist deterministisch (SHA-256 der alten ID, 12 Hex-
    Zeichen): die Historie einer Firma laesst weiterhin erkennen, dass
    mehrere Fahrten DERSELBE — inzwischen geloeschte — Fahrer erledigt
    hat, ohne Rueckschluss auf die Person.

    Unterschriften in bereits abgeschlossenen Protokoll-PDFs bleiben
    unveraendert: sie sind Teil der Beweiskette (Uebergabe-Nachweis
    gegenueber Verkaeufer und Haendler) und werden nicht nachtraeglich
    aus den Dokumenten entfernt. Nur die Kennung/der Klarname in den
    Datensaetzen wird ersetzt.

    Liefert die Anzahl geaenderter bzw. geloeschter Datensaetze je
    Collection (plus das verwendete Pseudonym)."""
    pseudonym = "geloescht:" + hashlib.sha256(
        driver_id.encode("utf-8")).hexdigest()[:12]
    jetzt = now_iso()
    # 1) Termine: offene verlieren die Zuweisung (zuteilung=None); alle mit
    #    dieser driver_id bekommen das Pseudonym in driver_id_hist, das
    #    Zugriffsfeld driver_id verschwindet. Bereits archivierte Zuordnungen
    #    (driver_id_hist aus delete_driver) werden ebenfalls ersetzt.
    r_offen = await db.appointments.update_many(
        {"driver_id": driver_id, "status": {"$in": _OFFEN_WERTE}},
        {"$set": {"driver_id_hist": pseudonym, "zuteilung": None,
                  "updated_at": jetzt},
         "$unset": {"driver_id": ""}})
    r_rest = await db.appointments.update_many(
        {"driver_id": driver_id},
        {"$set": {"driver_id_hist": pseudonym, "updated_at": jetzt},
         "$unset": {"driver_id": ""}})
    r_hist = await db.appointments.update_many(
        {"driver_id_hist": driver_id},
        {"$set": {"driver_id_hist": pseudonym}})
    # 2) Abholberichte / Protokolle: Kennung + Klarname
    ersatz = {"$set": {"driver_account_id": pseudonym,
                       "driver_name": "Fahrer (gelöscht)"}}
    r_ber = await db.pickup_reports.update_many(
        {"driver_account_id": driver_id}, ersatz)
    r_prot = await db.pickup_protocols.update_many(
        {"driver_account_id": driver_id}, ersatz)
    # 3) Audit-Log: Handelnder pseudonymisieren, personenbezogene
    #    Meta-Felder entfernen (E-Mail, Fahrer-Code, Anzeigename)
    r_log = await db.activity_logs.update_many(
        {"user_id": driver_id},
        {"$set": {"user_id": pseudonym},
         "$unset": {"meta.email": "", "meta.driver_code": "",
                    "meta.display_name": ""}})
    # 4) Reset-Tokens und Haendler-Verknuepfungen weg
    r_reset = await db.password_resets.delete_many({"user_id": driver_id})
    r_links = await db.dealer_drivers.delete_many(
        {"driver_account_id": driver_id})
    return {
        "pseudonym": pseudonym,
        "appointments": (r_offen.modified_count + r_rest.modified_count
                         + r_hist.modified_count),
        "pickup_reports": r_ber.modified_count,
        "pickup_protocols": r_prot.modified_count,
        "activity_logs": r_log.modified_count,
        "password_resets": r_reset.deleted_count,
        "dealer_drivers": r_links.deleted_count,
    }


# =========================================================
#   FAHRER-APP  (eigenständige Accounts mit E-Mail/Passwort)
# =========================================================
@router.post("/driver/register")
async def driver_register(body: DriverAccountRegister, request: Request):
    """Fahrer registriert sich in der Fahrer-App."""
    # Rate-limit: 5 new accounts per IP per hour.
    ip = client_ip(request)
    if not await driver_register_limiter.check(ip):
        raise HTTPException(429, "Zu viele Registrierungen von dieser IP – bitte später erneut versuchen.")
    email = body.email.lower().strip()
    existing = await db.driver_accounts.find_one({"email": email})
    if existing:
        raise HTTPException(409, "E-Mail ist bereits als Fahrer registriert")
    code = await ensure_unique_driver_code()
    did = str(uuid.uuid4())
    sid = str(uuid.uuid4())
    doc = {
        "id": did, "email": email,
        "password_hash": await hash_password_async(body.password),
        "display_name": body.display_name.strip(),
        "driver_code": code, "active": True,
        "current_session_id": sid,
        "created_at": now_iso(),
    }
    await db.driver_accounts.insert_one(doc)
    token = create_driver_token(did, sid)
    return {
        "token": token,
        "driver": {
            "id": did, "email": email,
            "display_name": body.display_name.strip(),
            "driver_code": code,
        },
    }


@router.post("/driver/login")
async def driver_login(body: DriverAccountLogin, request: Request):
    # Rate-limit by client IP (15 attempts / 60 s).
    ip = client_ip(request)
    if not await driver_login_limiter.check(ip):
        raise HTTPException(429, "Zu viele Anmeldeversuche – bitte 60 Sekunden warten.")
    email = body.email.lower().strip()
    da = await db.driver_accounts.find_one({"email": email})
    # Always run bcrypt (constant-time) to prevent user-enumeration via timing.
    pw_hash = da["password_hash"] if da else _DUMMY_HASH
    if not await verify_password_async(body.password, pw_hash) or not da:
        raise HTTPException(401, "E-Mail oder Passwort falsch")
    if not da.get("active", True):
        raise HTTPException(403, "Account deaktiviert")
    # Rotate session ID on every login to invalidate previous tokens.
    sid = str(uuid.uuid4())
    await db.driver_accounts.update_one(
        {"id": da["id"]}, {"$set": {"current_session_id": sid}},
    )
    token = create_driver_token(da["id"], sid)
    return {
        "token": token,
        "driver": {
            "id": da["id"], "email": da["email"],
            "display_name": da.get("display_name"),
            "driver_code": da.get("driver_code"),
        },
    }


@router.post("/driver/logout")
async def driver_logout(driver=Depends(current_driver)):
    """Sitzung serverseitig beenden (Runde 5): vorher loeschte die App nur
    den lokalen Token — ein kopierter Token blieb bis zum Ablauf gueltig."""
    await db.driver_accounts.update_one(
        {"id": driver["id"]}, {"$set": {"current_session_id": None}})
    return {"ok": True}


@router.get("/driver/me")
async def driver_me(driver=Depends(current_driver)):
    links = await db.dealer_drivers.find(
        {"driver_account_id": driver["id"]}, {"_id": 0},
    ).to_list(500)
    dealer_ids = [link["dealer_id"] for link in links]
    dealers = {}
    if dealer_ids:
        async for d in db.dealers.find(
            {"id": {"$in": dealer_ids}},
            {"_id": 0, "id": 1, "company_name": 1, "phone": 1},
        ):
            dealers[d["id"]] = d
    dealer_list = [
        {
            "id": link["dealer_id"],
            "name": (dealers.get(link["dealer_id"]) or {}).get("company_name") or "Autohaus",
            "phone": (dealers.get(link["dealer_id"]) or {}).get("phone"),
        }
        for link in links
    ]
    return {
        "id": driver["id"],
        "email": driver.get("email"),
        "display_name": driver.get("display_name"),
        "driver_code": driver.get("driver_code"),
        "dealers": dealer_list,
    }


@router.put("/driver/me")
async def driver_update_me(body: DriverProfileUpdate,
                            driver=Depends(current_driver)):
    update: Dict[str, Any] = {}
    if body.display_name is not None:
        new_name = body.display_name.strip()
        if len(new_name) < 2:
            raise HTTPException(400, "Name zu kurz")
        update["display_name"] = new_name
    if not update:
        raise HTTPException(400, "Nichts zu aktualisieren")
    update["updated_at"] = now_iso()
    await db.driver_accounts.update_one({"id": driver["id"]}, {"$set": update})
    if "display_name" in update:
        await db.dealer_drivers.update_many(
            {"driver_account_id": driver["id"]},
            {"$set": {"display_name": update["display_name"]}},
        )
    fresh = await db.driver_accounts.find_one(
        {"id": driver["id"]}, {"_id": 0, "password_hash": 0},
    )
    return await driver_me(fresh or driver)


@router.put("/driver/password")
async def driver_change_password(body: DriverPasswordIn,
                                 driver=Depends(current_driver)):
    """Eigenes Passwort aendern. Beendet danach alle Sitzungen des
    Fahrer-Kontos (Single-Session strikt) — der Fahrer meldet sich neu an."""
    konto = await db.driver_accounts.find_one(
        {"id": driver["id"]}, {"_id": 0, "password_hash": 1})
    if not konto or not await verify_password_async(
            body.current_password, konto.get("password_hash", "")):
        raise HTTPException(400, "Aktuelles Passwort ist falsch")
    if body.current_password == body.new_password:
        raise HTTPException(400, "Das neue Passwort muss sich vom alten unterscheiden")
    await db.driver_accounts.update_one(
        {"id": driver["id"]},
        {"$set": {"password_hash": await hash_password_async(body.new_password),
                  "current_session_id": None, "updated_at": now_iso()}})
    await log_activity("", driver["id"], "fahrer.passwort.geaendert")
    return {"ok": True, "hinweis": "Passwort geändert – bitte neu anmelden."}


def _termin_offen_oder_409(appt: dict) -> None:
    if (appt.get("status") or "offen") in _TERMIN_ABGESCHLOSSEN:
        raise HTTPException(409, f"Termin ist bereits '{appt.get('status')}' — "
                                 "Änderungen nur noch über den Händler.")


@router.get("/driver/appointments")
async def driver_appointments(driver=Depends(current_driver)):
    """Alle Termine (aller Händler), die diesem Fahrer-Account zugewiesen sind."""
    # Nur Firmen, in deren Fahrerliste der Fahrer AKTUELL steht: nach dem
    # Entfernen durch den Haendler verschwinden dessen Termine aus der App
    # (Pruefbericht 09/2026, siehe _zugriff_pruefen).
    dealer_ids_aktiv = await _verknuepfte_dealer_ids(driver["id"])
    if not dealer_ids_aktiv:
        return []
    appts = await db.appointments.find(
        {"driver_id": driver["id"], "dealer_id": {"$in": dealer_ids_aktiv}},
        {"_id": 0},
    ).sort("pickup_date", 1).to_list(500)

    # Fahrzeuge STRENG ueber (dealer_id, vehicle_id) laden (PR-Review
    # 09/2026): Fahrzeug-IDs leiten sich aus der Inserats-ID ab, zwei
    # Haendler koennen also dasselbe Fahrzeug mit derselben ID fuehren —
    # vorher gewann der zuletzt gelesene Datensatz, und der Fahrer sah
    # Daten/Fotos des FALSCHEN Haendlers.
    paare = {(a.get("dealer_id"), a.get("vehicle_id"))
             for a in appts if a.get("vehicle_id")}
    vehicles = {}
    if paare:
        async for v in db.vehicles.find(
                {"$or": [{"id": vid, "dealer_id": did} for did, vid in paare]},
                {"_id": 0}):
            vehicles[(v.get("dealer_id"), v["id"])] = v

    snap_map = {}
    if paare:
        async for s in db.listing_snapshots.find(
            {"$or": [{"vehicle_id": vid, "dealer_id": did} for did, vid in paare],
             "status": "ready"},
            {"_id": 0, "id": 1, "vehicle_id": 1, "dealer_id": 1, "completed_at": 1},
        ).sort("completed_at", -1):
            snap_map.setdefault((s.get("dealer_id"), s["vehicle_id"]), s["id"])

    dealer_ids = list({a.get("dealer_id") for a in appts if a.get("dealer_id")})
    dealers = {}
    if dealer_ids:
        async for d in db.dealers.find(
            {"id": {"$in": dealer_ids}},
            {"_id": 0, "id": 1, "company_name": 1, "phone": 1},
        ):
            dealers[d["id"]] = d

    out = []
    for a in appts:
        vid = a.get("vehicle_id")
        schluessel = (a.get("dealer_id"), vid)
        v = (vehicles.get(schluessel) or {}).get("data", {}) if vid else {}
        photos = (
            v.get("image_urls") or v.get("images") or v.get("photos")
            or v.get("pictures") or []
        )
        if isinstance(photos, dict):
            photos = list(photos.values())
        photos = [str(p) for p in photos if p][:20]

        d_info = dealers.get(a.get("dealer_id")) or {}
        out.append({
            "id": a.get("id"),
            "title": a.get("title"),
            "pickup_date": a.get("pickup_date"),
            "pickup_time": a.get("pickup_time"),
            "pickup_address": a.get("pickup_address"),
            "seller_name": a.get("seller_name"),
            "seller_phone": a.get("seller_phone"),
            "status": a.get("status", "offen"),
            # Alt-Termine ohne Feld gelten als angenommen (Rueckwaertskompatibel)
            "zuteilung": a.get("zuteilung") or "angenommen",
            "notes": a.get("notes"),
            "contract_id": a.get("contract_id"),
            "vehicle_id": vid,
            "dealer": {
                "id": d_info.get("id"),
                "name": d_info.get("company_name") or "Autohaus",
                "phone": d_info.get("phone"),
            },
            "vehicle": {
                "make": v.get("make_label") or v.get("make"),
                "model": v.get("model_label") or v.get("model_description") or v.get("model"),
                "ezl": v.get("ezl") or v.get("first_registration"),
                "km": v.get("km") or v.get("mileage"),
                "power_kw": v.get("power_kw"),
                "fuel": v.get("fuel") or v.get("fuel_type"),
                "color": v.get("exterior_color") or v.get("color"),
                "fin": v.get("vin") or v.get("fin"),
                "photos": photos,
            } if v else None,
            "snapshot_id": snap_map.get(schluessel),
        })
    return out


@router.get("/driver/appointments/{appt_id}/pickup-order.pdf")
async def driver_pickup_order_pdf(appt_id: str, download: int = 0,
                                   driver=Depends(current_driver)):
    appt = await db.appointments.find_one(
        {"id": appt_id, "driver_id": driver["id"]}, {"_id": 0},
    )
    if not appt:
        raise HTTPException(404, "Termin nicht gefunden")
    await _zugriff_pruefen(appt, driver)
    vehicle: Dict[str, Any] = {}
    if appt.get("vehicle_id"):
        v_doc = await db.vehicles.find_one(
            {"id": appt["vehicle_id"], "dealer_id": appt.get("dealer_id")},
            {"_id": 0},
        ) or {}
        vehicle = dict(v_doc.get("data") or {})
        if v_doc.get("mobile_ad_id"):
            vehicle.setdefault("mobile_ad_id", v_doc["mobile_ad_id"])
    contract = {}
    if appt.get("contract_id"):
        doc = await db.generated_pdfs.find_one(
            {"id": appt["contract_id"], "dealer_id": appt.get("dealer_id")},
            {"_id": 0, "contract_data": 1},
        )
        if doc:
            contract = dict(doc.get("contract_data") or {})
    dealer = await db.dealers.find_one(
        {"id": appt.get("dealer_id")}, {"_id": 0},
    ) or {}
    from pickup_pdf_service import build_pickup_pdf
    import asyncio as _aio
    driver_info = {
        "id": driver["id"],
        "name": driver.get("display_name"),
        "email": driver.get("email"),
    }
    # Im Thread: PDF-Erzeugung ist CPU-Arbeit und wuerde sonst den ganzen
    # Worker-Prozess blockieren.
    pdf_bytes = await _aio.to_thread(
        build_pickup_pdf,
        appointment=appt, vehicle=vehicle, contract=contract,
        dealer=dealer, driver=driver_info,
    )
    disposition = "attachment" if download else "inline"
    return Response(
        content=pdf_bytes, media_type="application/pdf",
        headers={
            "Content-Disposition": f'{disposition}; filename="Abholauftrag.pdf"',
            "Cache-Control": "no-store",
        },
    )


@router.get("/driver/contracts/{contract_id}/pdf")
async def driver_contract_pdf(contract_id: str, driver=Depends(current_driver)):
    appt = await db.appointments.find_one(
        {"driver_id": driver["id"], "contract_id": contract_id},
        {"_id": 0, "id": 1, "dealer_id": 1},
    )
    if not appt:
        raise HTTPException(404, "Kein Zugriff auf diesen Vertrag")
    await _zugriff_pruefen(appt, driver)
    doc = await db.generated_pdfs.find_one(
        {"id": contract_id, "dealer_id": appt.get("dealer_id")},
        {"_id": 0, "pdf_b64": 1},
    )
    if not doc or not doc.get("pdf_b64"):
        raise HTTPException(404, "Vertrag nicht gefunden")
    pdf_bytes = base64.b64decode(doc["pdf_b64"])
    return Response(
        content=pdf_bytes, media_type="application/pdf",
        headers={"Content-Disposition": 'inline; filename="Kaufvertrag.pdf"',
                 "Cache-Control": "no-store"},
    )


@router.get("/driver/snapshots/{snap_id}/{kind}")
async def driver_snapshot(snap_id: str, kind: str,
                          driver=Depends(current_driver)):
    if kind not in ("pdf", "png"):
        raise HTTPException(400, "kind muss 'pdf' oder 'png' sein")
    snap = await db.listing_snapshots.find_one({"id": snap_id}, {"_id": 0})
    if not snap or snap.get("status") != "ready":
        raise HTTPException(404, "Snapshot nicht bereit")
    # Snapshots sind BEWUSST haendlerneutral (der erste Snapshot einer
    # Anzeige wird von allen uebernommen — 'nie doppelt fotografieren').
    # Eine Bindung an snap.dealer_id wuerde Fahrern von Haendler B den
    # wiederverwendeten Snapshot von Haendler A sperren. Der Zugriff ist
    # ueber den EIGENEN Termin des Fahrers zur selben Anzeige legitimiert;
    # Snapshots zeigen ausschliesslich oeffentliche Inseratsdaten.
    # Der legitimierende Termin muss bei einer Firma liegen, in deren
    # Fahrerliste der Fahrer AKTUELL steht (Pruefbericht 09/2026).
    dealer_ids_aktiv = await _verknuepfte_dealer_ids(driver["id"])
    allowed = None
    if dealer_ids_aktiv:
        allowed = await db.appointments.find_one(
            {"driver_id": driver["id"],
             "dealer_id": {"$in": dealer_ids_aktiv},
             "$or": [{"vehicle_id": snap.get("vehicle_id")},
                     {"mobile_ad_id": snap.get("mobile_ad_id")}]},
            {"_id": 0, "id": 1},
        )
    if not allowed:
        raise HTTPException(404, "Kein Zugriff auf diesen Snapshot")
    path = snap.get("pdf_path") if kind == "pdf" else snap.get("png_path")
    if not path:
        raise HTTPException(404, "Datei fehlt im Objectstore")
    try:
        from snapshot_service import get_object_async
        data, ctype = await get_object_async(path)
    except Exception as exc:
        log.exception("driver snapshot fetch failed")
        raise HTTPException(502, "Snapshot-Storage nicht erreichbar.")
    return Response(
        content=data, media_type=ctype or ("application/pdf" if kind == "pdf" else "image/jpeg"),
        headers={"Content-Disposition": "inline", "Cache-Control": "no-store"},
    )


@router.put("/driver/appointments/{appt_id}/zuteilung")
async def driver_zuteilung(appt_id: str, body: DriverZuteilungIn,
                           driver=Depends(current_driver)):
    """Fahrer nimmt die zugeteilte Fahrt an oder lehnt sie ab (Wunsch
    09/2026). Ablehnen gibt den Termin an den Haendler zurueck (Fahrer
    wird entfernt, Termin bleibt 'offen', Grund landet in den Notizen)."""
    appt = await db.appointments.find_one(
        {"id": appt_id, "driver_id": driver["id"]}, {"_id": 0})
    if not appt:
        raise HTTPException(404, "Termin nicht gefunden")
    await _zugriff_pruefen(appt, driver)
    _termin_offen_oder_409(appt)
    if (appt.get("zuteilung") or "angenommen") != "offen":
        return {"ok": True, "zuteilung": appt.get("zuteilung") or "angenommen",
                "unveraendert": True}
    if body.action == "annehmen":
        # Audit 09/2026: Compare-and-set auf "offen" — gleichzeitiges
        # Annehmen und Ablehnen darf nicht beides erfolgreich melden.
        r = await db.appointments.update_one(
            {"id": appt_id, "driver_id": driver["id"], "zuteilung": "offen"},
            {"$set": {"zuteilung": "angenommen",
                      "zuteilung_beantwortet_am": now_iso(),
                      "updated_at": now_iso()},
             "$unset": {"zuteilung_neu_wegen_aenderung": ""}})
        if r.modified_count == 0:
            jetzt = await db.appointments.find_one(
                {"id": appt_id}, {"_id": 0, "zuteilung": 1}) or {}
            return {"ok": True, "zuteilung": jetzt.get("zuteilung") or "abgelehnt",
                    "unveraendert": True}
        await log_activity(appt.get("dealer_id"), driver["id"],
                           "termin.fahrer.angenommen", ref=appt_id)
        return {"ok": True, "zuteilung": "angenommen"}
    grund = (body.grund or "").strip()[:500]
    notiz = f"[Fahrer] Fahrt abgelehnt" + (f": {grund}" if grund else "")
    r = await db.appointments.update_one(
        {"id": appt_id, "driver_id": driver["id"], "zuteilung": "offen"},
        {"$set": {"zuteilung": "abgelehnt",
                  "zuteilung_beantwortet_am": now_iso(),
                  "zuteilung_abgelehnt_von": driver.get("name") or driver["id"],
                  "zuteilung_abgelehnt_grund": grund,
                  "updated_at": now_iso(),
                  "notes": ((appt.get("notes") or "") + ("\n" if appt.get("notes") else "") + notiz)},
         "$unset": {"driver_id": "", "zuteilung_neu_wegen_aenderung": ""}})
    if r.modified_count == 0:
        jetzt = await db.appointments.find_one(
            {"id": appt_id}, {"_id": 0, "zuteilung": 1}) or {}
        return {"ok": True, "zuteilung": jetzt.get("zuteilung") or "angenommen",
                "unveraendert": True}
    await log_activity(appt.get("dealer_id"), driver["id"],
                       "termin.fahrer.abgelehnt", ref=appt_id, meta={"grund": grund})
    return {"ok": True, "zuteilung": "abgelehnt"}


@router.put("/driver/appointments/{appt_id}/status")
async def driver_set_status(appt_id: str, body: DriverStatusIn,
                            driver=Depends(current_driver)):
    """Fahrer markiert seinen Termin als abgeholt oder nicht abgeholt.
    Startet damit den Cleanup-Timer (7 bzw. 14 Tage) im Hintergrund."""
    if body.status not in ("abgeholt", "nicht abgeholt"):
        raise HTTPException(400, "Nur 'abgeholt' oder 'nicht abgeholt' erlaubt")
    appt = await db.appointments.find_one(
        {"id": appt_id, "driver_id": driver["id"]}, {"_id": 0},
    )
    if not appt:
        raise HTTPException(404, "Termin nicht gefunden")
    await _zugriff_pruefen(appt, driver)
    if (appt.get("status") or "") == body.status:
        # Idempotent (Pruefbericht Runde 4): Der Protokoll-Abschluss setzt
        # "abgeholt" bereits selbst; der anschliessende Aufruf aus dem
        # Abhol-Check-Dialog lief in 409 und liess den Fahrer neu versuchen.
        return {"ok": True, "status": body.status, "unveraendert": True,
                "auto_cleanup_days": 7 if body.status == "abgeholt" else 14}
    _termin_offen_oder_409(appt)
    if appt.get("zuteilung") == "offen":
        raise HTTPException(409, "Bitte zuerst die Fahrt annehmen (oder ablehnen).")
    # Vereinheitlichter Abschluss: "abgeholt" gibt es NUR mit unterschriebenem
    # Abholprotokoll (Beweiskette: Zustand + beide Unterschriften). Der alte
    # Schnellweg ohne Protokoll erzeugte "abgeholt"-Termine ohne jeden Beleg.
    if body.status == "abgeholt":
        final_proto = await db.pickup_protocols.find_one(
            {"appointment_id": appt_id, "status": "final",
             "superseded": {"$ne": True}}, {"_id": 0, "id": 1})
        if not final_proto:
            raise HTTPException(409, "Bitte zuerst das Abholprotokoll "
                                     "ausfüllen und unterschreiben — erst "
                                     "damit gilt das Fahrzeug als abgeholt.")
    update = {
        "status": body.status,
        "status_changed_at": now_iso(),
        "updated_at": now_iso(),
        "assets_cleaned_at": None,  # Timer startet neu
    }
    if body.notes:
        update["notes"] = (appt.get("notes") or "") + (
            "\n" if appt.get("notes") else ""
        ) + f"[Fahrer] {body.notes}"
    await db.appointments.update_one({"id": appt_id}, {"$set": update})
    # Fahrzeug-Lebenszyklus nachziehen.
    if appt.get("vehicle_id"):
        from lifecycle import try_set_lifecycle
        await try_set_lifecycle(
            appt["vehicle_id"], appt.get("dealer_id"),
            "abgeholt" if body.status == "abgeholt" else "nicht_abgeholt",
            user={"id": driver["id"]},
        )
    await log_activity(
        appt.get("dealer_id"), driver["id"],
        f"termin.fahrer.{body.status.replace(' ', '_')}", ref=appt_id,
    )
    return {"ok": True, "status": body.status,
            "auto_cleanup_days": 7 if body.status == "abgeholt" else 14}


# =========================================================
#            ABHOLBERICHT (digitale Abweichungen)
# =========================================================
# Der Bericht ist nach dem Einreichen UNVERÄNDERBAR. Korrekturen erzeugen
# eine neue Version (version+1, replaces_id) — die alte bleibt erhalten
# (superseded=True). Jede Einreichung wird im Audit-Log protokolliert.

@router.get("/pickup-fotos/{key:path}")
async def pickup_foto(key: str, user=Depends(current_firma)):
    """Abweichungsfoto aus einem Abholbericht — nur fuer die eigene Firma.
    (Der offene /api/files-Weg liefert pickup/-Dateien seit 08/2026 nicht
    mehr aus.)"""
    if not key.startswith("pickup/"):
        raise HTTPException(404, "Datei nicht gefunden")
    teile = key.split("/")
    if len(teile) < 3 or teile[1] != user.get("dealer_id"):
        raise HTTPException(404, "Datei nicht gefunden")
    from storage_service import guess_media_type, load_async, StorageError
    try:
        data = await load_async(key)
    except StorageError:
        raise HTTPException(404, "Datei nicht gefunden")
    return Response(content=data, media_type=guess_media_type(key),
                    headers={"Cache-Control": "private, max-age=3600"})


@router.get("/driver/pickup-fotos/{key:path}")
async def driver_pickup_foto(key: str, driver=Depends(current_driver)):
    """Wie /pickup-fotos, aber fuer Fahrer: nur Fotos von Firmen, denen
    der Fahrer zugeteilt ist."""
    if not key.startswith("pickup/"):
        raise HTTPException(404, "Datei nicht gefunden")
    # NUR Fotos aus den EIGENEN Abholberichten (PR-Review 09/2026):
    # vorher genuegte irgendein Termin bei der Firma, um bei bekanntem
    # Key beliebige Abholfotos dieser Firma zu laden.
    eigener_bericht = await db.pickup_reports.find_one(
        {"deviations.photo_key": key, "driver_account_id": driver["id"]},
        {"_id": 0, "dealer_id": 1})
    if not eigener_bericht:
        raise HTTPException(404, "Datei nicht gefunden")
    # ... und nur solange der Fahrer bei dieser Firma noch in der Liste steht.
    try:
        await _zugriff_pruefen(eigener_bericht, driver)
    except HTTPException:
        raise HTTPException(404, "Datei nicht gefunden")
    from storage_service import guess_media_type, load_async, StorageError
    try:
        data = await load_async(key)
    except StorageError:
        raise HTTPException(404, "Datei nicht gefunden")
    return Response(content=data, media_type=guess_media_type(key),
                    headers={"Cache-Control": "private, max-age=3600"})


@router.post("/driver/appointments/{appt_id}/report")
async def driver_submit_report(appt_id: str, body: PickupReportIn,
                               driver=Depends(current_driver)):
    appt = await db.appointments.find_one(
        {"id": appt_id, "driver_id": driver["id"]}, {"_id": 0},
    )
    if not appt:
        raise HTTPException(404, "Termin nicht gefunden")
    await _zugriff_pruefen(appt, driver)
    # Sperre nach Abschluss — mit EINER Ausnahme (Pruefbericht Runde 4): Der
    # Abweichungsbericht gehoert zur Abholung und wird direkt NACH dem
    # unterschriebenen Protokoll (Termin dann schon "abgeholt") eingereicht.
    # Erlaubt ist deshalb der ERSTE Bericht binnen 24 h nach "abgeholt";
    # Korrekturversionen danach nur ueber den Haendler (Termin wieder oeffnen).
    status = appt.get("status") or "offen"
    reserviert = False
    if status == "abgeholt":
        seit = appt.get("status_changed_at") or ""
        frisch = False
        try:
            from datetime import datetime, timedelta, timezone
            t = datetime.fromisoformat(seit)
            if t.tzinfo is None:
                t = t.replace(tzinfo=timezone.utc)
            frisch = datetime.now(timezone.utc) - t <= timedelta(hours=24)
        except (TypeError, ValueError):
            frisch = False
        # ATOMARE Reservierung des Erstberichts (Runde 5): zwei parallele
        # Erstanfragen bestanden vorher beide die Vorpruefung, die zweite
        # wurde als "Korrekturversion" gespeichert. Genau EINE gewinnt.
        res = await db.appointments.find_one_and_update(
            {"id": appt_id, "driver_id": driver["id"],
             "erstbericht_reserviert_at": {"$exists": False}},
            {"$set": {"erstbericht_reserviert_at": now_iso()}})
        vorhanden = await db.pickup_reports.count_documents(
            {"appointment_id": appt_id})
        if res is None or vorhanden or not frisch:
            if res is not None and not vorhanden and not frisch:
                await db.appointments.update_one(
                    {"id": appt_id}, {"$unset": {"erstbericht_reserviert_at": ""}})
            raise HTTPException(409, "Termin ist bereits 'abgeholt' — der "
                                     "Abholbericht kann nur einmal direkt nach "
                                     "der Abholung eingereicht werden; "
                                     "Korrekturen nur ueber den Haendler.")
        reserviert = True
    else:
        _termin_offen_oder_409(appt)

    # Fotos aus base64 in den Storage auslagern (nie in Mongo speichern).
    from storage_service import make_key, storage, StorageError
    deviations = []
    for d in body.deviations:
        entry = d.model_dump(exclude={"photo_b64"})
        entry["id"] = str(uuid.uuid4())
        if d.photo_b64:
            try:
                raw = base64.b64decode(d.photo_b64.split(",")[-1], validate=False)
                from storage_service import validate_image_bytes
                validate_image_bytes(raw, wo="Abweichungsfoto")
                key = make_key("pickup", appt.get("dealer_id", "x"), "foto.jpg")
                from storage_service import save_async
                await save_async(key, raw)
                entry["photo_key"] = key
            except (StorageError, ValueError) as exc:
                raise HTTPException(400, f"Foto konnte nicht gespeichert werden: {exc}")
        deviations.append(entry)

    # Versionierung: existiert schon ein Bericht, wird er ersetzt (nicht geändert).
    prev = await db.pickup_reports.find_one(
        {"appointment_id": appt_id, "superseded": {"$ne": True}},
        {"_id": 0, "id": 1, "version": 1},
        sort=[("version", -1)],
    )
    version = (prev or {}).get("version", 0) + 1
    report_id = str(uuid.uuid4())
    doc = {
        "id": report_id,
        "appointment_id": appt_id,
        "vehicle_id": appt.get("vehicle_id"),
        "dealer_id": appt.get("dealer_id"),
        "driver_account_id": driver["id"],
        "driver_name": driver.get("display_name", ""),
        "mileage_at_pickup": body.mileage_at_pickup,
        "keys_count": body.keys_count,
        "fuel_level": body.fuel_level,
        "deviations": deviations,
        "notes": body.notes,
        "version": version,
        "replaces_id": (prev or {}).get("id"),
        "superseded": False,
        "status": "bestaetigt",
        "created_at": now_iso(),
    }
    for versuch in range(3):
        try:
            await db.pickup_reports.insert_one(doc)
            break
        except Exception:
            # Unique-Index (appointment_id, version): ein paralleler
            # Bericht hat dieselbe Version belegt -> frisch lesen, neue
            # Versionsnummer nehmen und erneut versuchen.
            if versuch == 2:
                if reserviert:
                    await db.appointments.update_one(
                        {"id": appt_id}, {"$unset": {"erstbericht_reserviert_at": ""}})
                raise HTTPException(409, "Bericht wurde gerade parallel "
                                         "gespeichert — bitte neu laden.")
            doc.pop("_id", None)
            prev = await db.pickup_reports.find_one(
                {"appointment_id": appt_id, "superseded": {"$ne": True}},
                {"_id": 0, "id": 1, "version": 1}, sort=[("version", -1)])
            doc["version"] = (prev or {}).get("version", 0) + 1
            doc["replaces_id"] = (prev or {}).get("id")
    if prev:
        await db.pickup_reports.update_one(
            {"id": prev["id"]}, {"$set": {"superseded": True}})
    # Badge-Daten am Termin denormalisieren (schnelle Anzeige beim Händler).
    await db.appointments.update_one(
        {"id": appt_id},
        {"$set": {"deviations_count": len(deviations),
                  "has_pickup_report": True,
                  "updated_at": now_iso()}},
    )
    await log_activity(
        appt.get("dealer_id"), driver["id"],
        "abholung.bericht" if version == 1 else "abholung.bericht.korrektur",
        ref=appt_id,
        meta={"version": version, "abweichungen": len(deviations)},
    )
    return {"ok": True, "report_id": report_id, "version": version,
            "deviations_count": len(deviations)}


@router.get("/driver/appointments/{appt_id}/report")
async def driver_get_report(appt_id: str, driver=Depends(current_driver)):
    appt = await db.appointments.find_one(
        {"id": appt_id, "driver_id": driver["id"]},
        {"_id": 0, "id": 1, "dealer_id": 1},
    )
    if not appt:
        raise HTTPException(404, "Termin nicht gefunden")
    await _zugriff_pruefen(appt, driver)
    report = await db.pickup_reports.find_one(
        {"appointment_id": appt_id, "superseded": {"$ne": True}}, {"_id": 0},
    )
    return report or {}
