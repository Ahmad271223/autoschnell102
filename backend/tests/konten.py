# -*- coding: utf-8 -*-
"""Kontonummer (13.09.2026), Schritt 3 — zentrale Konto-Helfer der Backend-Tests.

Anmeldung laeuft ueber die Kontonummer; Konten legt der Super-Admin an. Damit
nicht jede Testdatei eigene Register-/Login-Kopien pflegt, stehen die Wege
hier EINMAL:

  super_admin_token()        Wegwerf-Super-Admin (Benutzername), Token gecacht
  anmelden(kennung, pw, weg) POST /auth|buyer|driver/login mit der Kennung
  kennung_fuer_mail(mail)    Bruecke fuer Tests, die ihr Konto per Mail kennen
  login_per_mail(mail, pw)   Ersatz fuer requests.post(.../login, {"email": …})
  token_direkt(konto_id)     Sitzung per DB setzen und Token erzeugen
  registrieren(json)         Ersatz fuer POST /auth/register (Firma + Chef)
  sucher_als_chef_anlegen()  Ersatz fuer POST /dealer/sucher
  fahrer_registrieren(json)  Ersatz fuer POST /driver/register
  kaeufer_registrieren(json) Ersatz fuer POST /buyer/register (+ Einladung)

Bewusst KEIN conftest.py (Import-Nebenwirkungen in allen Testdateien und in
In-Prozess-Schleifen). Nur requests, pymongo, bcrypt und — erst beim Aufruf —
`auth` fuer Tokens. Importiert nie server.py und keine routes.*.

Single-Session: jede Anmeldung ersetzt die Sitzung des Kontos. Die Helfer
melden jedes neu angelegte Konto genau EINMAL an (wie frueher die
Registrierung, die ebenfalls ein Token ausstellte).

Testkonten behalten ihre Kontakt-E-Mail — die Aufraeumroutinen der Tests
(per E-Mail-Muster bzw. ID) laufen unveraendert; hier kommt kein neues
breites Loeschmuster dazu (Vorfall geloeschte Alt-Vertraege).
"""
import atexit
import hashlib
import json as _json
import os
import re
import secrets
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path

import requests

BACKEND = Path(__file__).resolve().parents[1]

BASE = (os.environ.get("TEST_BASE_URL") or "http://localhost:8001").rstrip("/")
API = f"{BASE}/api"
MONGO_URL = os.environ.get("MONGO_URL") or "mongodb://127.0.0.1:27017"
DB_NAME = os.environ.get("DB_NAME") or "autoschnell"

WEGE = {"auth": "/auth/login", "buyer": "/buyer/login", "driver": "/driver/login"}
NUMMERN_ROLLEN = ("dealer", "sucher", "b2b_buyer")

_client = None


def _db():
    global _client
    if _client is None:
        from pymongo import MongoClient
        _client = MongoClient(MONGO_URL, serverSelectionTimeoutMS=5000)
    return _client[DB_NAME]


def _jetzt():
    return datetime.now(timezone.utc).isoformat()


def _kopf(token):
    return {"Authorization": f"Bearer {token}"}


class Antwort:
    """Response-aehnliches Objekt fuer zusammengesetzte Helfer-Antworten
    (status_code, json(), text, ok) — Tests pruefen es wie requests.Response."""

    def __init__(self, status_code, daten):
        self.status_code = status_code
        self._daten = daten
        self.text = _json.dumps(daten, ensure_ascii=False, default=str)
        self.content = self.text.encode("utf-8")
        self.ok = 200 <= status_code < 400
        self.headers = {"content-type": "application/json"}

    def json(self):
        return self._daten

    def raise_for_status(self):
        if not self.ok:
            raise requests.HTTPError(f"{self.status_code}: {self.text[:200]}")


# ------------------------------------------------------------ Anmeldung
def anmelden(kennung, pw, weg="auth", headers=None, timeout=30, **kw):
    """POST /auth/login bzw. /buyer/login, /driver/login mit der Kennung
    (Kontonummer, Benutzername des Super-Admins — oder bis Schritt 4 eine
    E-Mail-Adresse, die dann unter dem alten Feldnamen geht)."""
    kennung = "" if kennung is None else str(kennung)
    feld = "email" if "@" in kennung else "kontonummer"
    return requests.post(f"{API}{WEGE[weg]}", json={feld: kennung, "password": pw},
                         headers=headers, timeout=timeout, **kw)


def _naechste_nummer(dbx) -> int:
    """Wie kontenanlage.naechste_nummer (synchron): EINE Reihe
    counters._id='kunden_nr', nie auf oder unter die hoechste vergebene Nummer."""
    from pymongo import ReturnDocument
    from pymongo.errors import DuplicateKeyError
    for _ in range(8):
        try:
            doc = dbx.counters.find_one_and_update(
                {"_id": "kunden_nr"}, {"$inc": {"seq": 1}, "$setOnInsert": {"start": 1000}},
                upsert=True, return_document=ReturnDocument.AFTER)
        except DuplicateKeyError:
            continue
        nr = 1000 + int(doc["seq"])
        hoechste = 0
        for coll, feld in ((dbx.dealers, "kunden_nr"), (dbx.users, "kontonummer_basis"),
                           (dbx.driver_accounts, "kontonummer_basis")):
            top = coll.find_one({feld: {"$type": "number"}}, {"_id": 0, feld: 1},
                                sort=[(feld, -1)])
            if top and int(top[feld]) > hoechste:
                hoechste = int(top[feld])
        if nr > hoechste:
            return nr
        dbx.counters.update_one({"_id": "kunden_nr"}, {"$max": {"seq": hoechste - 1000}})
    raise AssertionError("konten: keine freie Kontonummer gefunden")


def _nummer_vergeben(sammlung: str, konto_id: str) -> str:
    """Direkt eingefuegtes Konto ohne Nummer (Altbestand im Test): Nummer aus
    der gemeinsamen Reihe samt kontonummer_basis — nur, falls noch keine da ist."""
    from pymongo.errors import DuplicateKeyError
    dbx = _db()
    coll = dbx[sammlung]
    for _ in range(5):
        nr = _naechste_nummer(dbx)
        try:
            coll.update_one({"id": konto_id, "kontonummer": {"$exists": False}},
                            {"$set": {"kontonummer": str(nr), "kontonummer_basis": nr}})
        except DuplicateKeyError:
            continue
        doc = coll.find_one({"id": konto_id}, {"_id": 0, "kontonummer": 1}) or {}
        if doc.get("kontonummer"):
            return doc["kontonummer"]
    raise AssertionError(f"konten: Nummer fuer {sammlung}/{konto_id} nicht vergeben")


def kennung_fuer_mail(mail, sammlung="users"):
    """Bruecke fuer Tests, die ihr Konto ueber die Kontaktadresse kennen:
      - Konto mit Kontonummer -> die Nummer
      - Super-Admin (is_super_admin True) -> Benutzername (fehlt er, wird
        't-'+sha1(mail)[:12] gesetzt — passt nie zum Nummernmuster)
      - normaler Admin -> die Adresse selbst (bis Schritt 4 greift der
        '@'-Altzweig; admin_konten_ohne_super_admin zeigt weiter die Adresse)
      - direkt eingefuegtes Chef/Sucher/Kaeufer- bzw. Fahrerkonto ohne
        Nummer -> Nummer aus der gemeinsamen Reihe vergeben
      - unbekannte Adresse -> die Adresse (Negativtests bleiben 401)
    Mehr als ein Treffer ist ein Testfehler (AssertionError)."""
    if not mail or "@" not in str(mail):
        return mail
    coll = _db()[sammlung]
    treffer = list(coll.find(
        {"email": {"$regex": f"^{re.escape(str(mail).strip())}$", "$options": "i"}},
        {"_id": 0, "id": 1, "role": 1, "is_super_admin": 1, "username": 1,
         "kontonummer": 1}).limit(3))
    assert len(treffer) <= 1, f"konten: {len(treffer)} Konten mit der Adresse {mail} in {sammlung}"
    if not treffer:
        return mail
    konto = treffer[0]
    if konto.get("kontonummer"):
        return konto["kontonummer"]
    if sammlung == "users":
        rolle = konto.get("role")
        if rolle == "admin":
            if konto.get("is_super_admin") is not True:
                return mail
            if konto.get("username"):
                return konto["username"]
            name = "t-" + hashlib.sha1(str(mail).strip().lower().encode()).hexdigest()[:12]
            coll.update_one({"id": konto["id"], "username": {"$exists": False}},
                            {"$set": {"username": name}})
            return (coll.find_one({"id": konto["id"]}, {"username": 1}) or {}).get("username") or mail
        if rolle not in NUMMERN_ROLLEN:
            return mail
    return _nummer_vergeben(sammlung, konto["id"])


def login_per_mail(mail, pw, weg="auth", headers=None, timeout=30, **kw):
    """Ersatz fuer requests.post(f"{API}/<weg>/login", json={"email": mail, …}):
    Kennung ueber kennung_fuer_mail bestimmen, dann anmelden. Liefert die
    Response unveraendert (auch 401/403/429)."""
    sammlung = "driver_accounts" if weg == "driver" else "users"
    return anmelden(kennung_fuer_mail(mail, sammlung), pw, weg, headers=headers,
                    timeout=timeout, **kw)


def token_direkt(konto_id, sammlung="users"):
    """Neue Sitzung per DB setzen und das passende Token erzeugen (ohne
    Login-Route) — z.B. fuer Konten, die sich nicht anmelden koennen."""
    if str(BACKEND) not in sys.path:
        sys.path.insert(0, str(BACKEND))
    sid = str(uuid.uuid4())
    _db()[sammlung].update_one({"id": konto_id}, {"$set": {"current_session_id": sid}})
    if sammlung == "driver_accounts":
        # wie routes.drivers.create_driver_token (ohne routes-Import)
        from datetime import timedelta
        import jwt
        from auth import JWT_ALG, JWT_SECRET
        payload = {"sub": konto_id, "sid": sid, "role": "driver_account",
                   "exp": datetime.now(timezone.utc) + timedelta(days=7)}
        return jwt.encode(payload, JWT_SECRET, algorithm=JWT_ALG)
    from auth import create_token
    return create_token(konto_id, sid)


# ------------------------------------------------------------ Super-Admin
_SA = {"id": None, "name": None, "pw": None, "token": None}


def _sa_loeschen():
    if _SA["id"]:
        try:
            _db().users.delete_one({"id": _SA["id"], "role": "admin"})
        except Exception:
            pass


def _sa_anlegen():
    import bcrypt
    s = uuid.uuid4().hex[:10]
    pw = f"Sa-{secrets.token_hex(8)}!7"
    _db().users.insert_one({
        "id": f"t_sa_{s}", "username": f"t-sa-{s}", "role": "admin",
        "is_super_admin": True, "active": True, "dealer_id": None,
        "password_hash": bcrypt.hashpw(pw.encode(), bcrypt.gensalt()).decode(),
        "current_session_id": None, "created_at": _jetzt()})
    if _SA["id"] is None:
        atexit.register(_sa_loeschen)
    _SA.update(id=f"t_sa_{s}", name=f"t-sa-{s}", pw=pw, token=None)


def super_admin_token(fresh=False):
    """Token eines Wegwerf-Super-Admins (einmal je Prozess angelegt, per
    Benutzername angemeldet, gecacht; atexit loescht das Konto). Absichtlich
    NICHT der geseedete Super-Admin — Backend-Suite und E2E nehmen sich so nie
    gegenseitig die Sitzung."""
    if _SA["token"] and not fresh:
        return _SA["token"]
    if not _SA["id"] or not _db().users.find_one({"id": _SA["id"]}, {"_id": 1}):
        _sa_anlegen()
    r = anmelden(_SA["name"], _SA["pw"], "auth")
    assert r.status_code == 200 and r.json().get("token"), \
        f"konten: Super-Admin-Login {r.status_code} {r.text[:200]}"
    _SA["token"] = r.json()["token"]
    return _SA["token"]


def super_kopf(fresh=False):
    return _kopf(super_admin_token(fresh))


def _super(methode, pfad, body=None, timeout=60):
    """Aufruf als Super-Admin; bei 401 (Sitzung anderweitig ersetzt) einmal
    mit frischem Token wiederholen."""
    r = requests.request(methode, f"{API}{pfad}", json=body, headers=super_kopf(),
                         timeout=timeout)
    if r.status_code == 401:
        r = requests.request(methode, f"{API}{pfad}", json=body,
                             headers=super_kopf(fresh=True), timeout=timeout)
    return r


# ------------------------------------------------------------ Kontenanlage
def _ohne_none(d):
    return {k: v for k, v in d.items() if v is not None}


def registrieren(json=None, timeout=30, **_):
    """Ersatz fuer POST /auth/register: Firma + Chef ueber POST /admin/users
    (plan_type none, wie die Selbstregistrierung ohne Abo), danach EIN Login
    per Kontonummer. Antwort wie frueher {token, user{…, kontonummer}};
    Fehler der Anlage (409/422) werden unveraendert durchgereicht."""
    body = dict(json or {})
    pw = body.get("password")
    r = _super("POST", "/admin/users", _ohne_none({
        "email": body.get("email"), "password": pw,
        "company_name": body.get("company_name"),
        "contact_person": body.get("contact_person") or "",
        "phone": body.get("phone") or "", "plan_type": "none"}), timeout)
    if r.status_code != 200:
        return r
    return anmelden(r.json()["kontonummer"], pw, "auth", timeout=timeout)


def sucher_als_chef_anlegen(chef=None, json=None, timeout=30, headers=None, **_):
    """Ersatz fuer POST /dealer/sucher mit dem Token des Chefs: /auth/me ->
    dealer_id -> POST /admin/dealers/{id}/sucher. Antwort {ok, sucher_id,
    kontonummer, …}. Kein Login des Suchers.
    Nicht-Chef (Sucher, Kaeufer, …), fehlende Pflichtfelder der alten Route
    oder ungueltiges Token: der Aufruf geht an die alte Route, damit deren
    Ablehnung (401/403/422) erhalten bleibt."""
    kopf = headers or (chef if isinstance(chef, dict) else _kopf(chef))
    body = dict(json or {})
    me = requests.get(f"{API}/auth/me", headers=kopf, timeout=timeout)
    alt = (me.status_code != 200
           or (me.json().get("user") or {}).get("role") != "dealer"
           or not body.get("email") or not body.get("first_name")
           or not body.get("last_name"))
    if alt:
        return requests.post(f"{API}/dealer/sucher", headers=kopf, json=body,  # ALTWEG – Schritt 5
                             timeout=timeout)
    chef_user = me.json()["user"]
    r = _super("POST", f"/admin/dealers/{chef_user['dealer_id']}/sucher", _ohne_none({
        "email": body.get("email"), "password": body.get("password"),
        "first_name": body.get("first_name"), "last_name": body.get("last_name"),
        "phone": body.get("phone") or ""}), timeout)
    if r.status_code == 200:
        # Felder der alten Chef-Route nachziehen (Personalnummer, angelegt von)
        extra = {"created_by": chef_user["id"]}
        if body.get("employee_id"):
            extra["employee_id"] = body["employee_id"]
        _db().users.update_one({"id": r.json()["sucher_id"]}, {"$set": extra})
    return r


def fahrer_registrieren(json=None, timeout=30, **_):
    """Ersatz fuer POST /driver/register: POST /admin/drivers, danach EIN
    Login per Kontonummer. Antwort wie frueher {token, driver{id, email,
    kontonummer, display_name, driver_code}}."""
    body = dict(json or {})
    pw = body.get("password")
    r = _super("POST", "/admin/drivers", _ohne_none({
        "display_name": body.get("display_name"), "password": pw,
        "email": body.get("email"), "phone": body.get("phone")}), timeout)
    if r.status_code != 200:
        return r
    return anmelden(r.json()["kontonummer"], pw, "driver", timeout=timeout)


def kaeufer_registrieren(json=None, timeout=30, **_):
    """Ersatz fuer POST /buyer/register: POST /admin/buyers (b2b_nachweis =
    gewerblich_bestaetigt), EIN Login per Kontonummer, bei invite_token das
    Einloesen ueber POST /invites/{token}/redeem. Antwort wie frueher
    {ok, token, user{id, email, role, kontonummer, company_name}, network_joined}."""
    body = dict(json or {})
    if "gewerblich_bestaetigt" not in body:
        return Antwort(422, {"detail": [{"loc": ["body", "gewerblich_bestaetigt"],
                                         "msg": "Field required", "type": "missing"}]})
    pw = body.get("password")
    r = _super("POST", "/admin/buyers", _ohne_none({
        "company_name": body.get("company_name"), "contact_name": body.get("contact_name"),
        "email": body.get("email"), "phone": body.get("phone") or "",
        "ust_id": body.get("ust_id") or "", "password": pw,
        "b2b_nachweis": bool(body.get("gewerblich_bestaetigt"))}), timeout)
    if r.status_code != 200:
        return r
    login = anmelden(r.json()["kontonummer"], pw, "buyer", timeout=timeout)
    if login.status_code != 200:
        return login
    token, user = login.json()["token"], login.json()["user"]
    joined = False
    if body.get("invite_token"):
        e = requests.post(f"{API}/invites/{body['invite_token']}/redeem",
                          headers=_kopf(token), timeout=timeout)
        joined = e.status_code == 200
    return Antwort(200, {"ok": True, "token": token,
                         "user": {"id": user["id"], "email": user.get("email"),
                                  "role": "b2b_buyer", "kontonummer": user.get("kontonummer"),
                                  "company_name": user.get("company_name")},
                         "network_joined": joined})
