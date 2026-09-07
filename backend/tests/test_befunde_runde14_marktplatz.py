# -*- coding: utf-8 -*-
"""Nachpruefung Runde 14 (09/2026), Gruppe "marktplatz".

  1/2/3  answer_interest: Kaeufer neu laden (aktiv, b2b_buyer, nicht
         gesperrt, nicht geloescht) und Sichtbarkeit des Inserats pruefen
  48     akzeptieren im Status 'gegenangebot' -> 400 (eigenes Angebot
         liegt beim Kaeufer); Frontend zeigt den Knopf dort nicht
  62     buyer_register: DuplicateKeyError -> 409 statt 500
  63     buyer_register: Fehler beim Einloesen der Einladung kippt die
         Registrierung nicht mehr
  64/115 _redeem_invite: Mitgliedschaft zuerst, Verbrauch atomar danach,
         Rueckbau bei Misserfolg (DELETE der Einladung im Fenster)
  66     verwaiste Netzwerkmitglieder: active=False, fehlt=True
  87     list_invites: alle gueltigen, dazu die neuesten 50 ungueltigen
  119    Marktplatz-Profil: gezielte $set je Feld (kein Lost Update)

Einheitentests laufen ohne Server direkt gegen die Routenfunktionen
(motor ueber deps.db, Mongo 127.0.0.1:27017). HTTP-Tests laufen nur mit
RUNDE14_HTTP=1 gegen TEST_BASE_URL (Backend nach dem Neustart).
"""
import asyncio
import os
import re
import sys
import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
import requests

BACKEND = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND))
os.environ.setdefault("MONGO_URL", "mongodb://127.0.0.1:27017")
os.environ.setdefault("DB_NAME", "autoschnell")

HTTP = os.environ.get("RUNDE14_HTTP") == "1"
BASE = (os.environ.get("TEST_BASE_URL") or "http://localhost:8001").rstrip("/")
API = f"{BASE}/api"
MONGO_URL = os.environ["MONGO_URL"]
DB_NAME = os.environ["DB_NAME"]
SUF = uuid.uuid4().hex[:8]
PW = "RundeVierzehn14!"
MAIL = "e2etest-mail.de"
JETZT = datetime.now(timezone.utc)


def _db():
    from pymongo import MongoClient
    return MongoClient(MONGO_URL, serverSelectionTimeoutMS=5000)[DB_NAME]


def _jetzt(delta_h=0):
    return (JETZT + timedelta(hours=delta_h)).isoformat()


def _kopf(token):
    return {"Authorization": f"Bearer {token}"}


# =====================================================================
#                 Einheitentests: gemeinsame Welt (nur Mongo)
# =====================================================================
@pytest.fixture()
def welt_unit():
    """Firma (oeffentlich, aktiver Chef), Kaeufer, veroeffentlichtes
    Inserat — direkt in Mongo, ohne Server."""
    dbx = _db()
    did = f"r14mp_d_{SUF}_{uuid.uuid4().hex[:6]}"
    chef_id = f"r14mp_chef_{did}"
    k_id = f"r14mp_k_{did}"
    dbx.dealers.insert_one({"id": did, "user_id": chef_id, "company_name": f"R14 Autohaus {SUF}",
                            "marketplace": {"public": True, "slug": f"r14-{SUF}"},
                            "created_at": _jetzt()})
    dbx.users.insert_many([
        {"id": chef_id, "email": f"{chef_id}@{MAIL}", "role": "dealer", "active": True,
         "dealer_id": did, "password_hash": "x", "created_at": _jetzt()},
        {"id": k_id, "email": f"{k_id}@{MAIL}", "role": "b2b_buyer", "active": True,
         "dealer_id": None, "company_name": "K GmbH", "contact_name": "K M",
         "password_hash": "x", "created_at": _jetzt()},
    ])
    chef = {"id": chef_id, "role": "dealer", "dealer_id": did}
    z = {"did": did, "chef": chef, "chef_id": chef_id, "k_id": k_id, "dbx": dbx}

    def inserat(sichtbarkeit="public"):
        lid = str(uuid.uuid4())
        dbx.resale_listings.insert_one({
            "id": lid, "dealer_id": did, "vehicle_id": str(uuid.uuid4()),
            "title": f"R14 Golf {SUF}", "status": "veroeffentlicht",
            "visibility": sichtbarkeit, "prices": {"public": 9900, "b2b": 9000},
            "created_at": _jetzt(), "updated_at": _jetzt()})
        return lid

    def anfrage(lid, status="offen", **extra):
        iid = str(uuid.uuid4())
        dbx.listing_interest.insert_one({
            "id": iid, "listing_id": lid, "dealer_id": did, "buyer_user_id": k_id,
            "buyer_name": "K GmbH", "offer": 20000.0, "status": status,
            "counter_offer": None, "history": [], "created_at": _jetzt(),
            "updated_at": _jetzt(), **extra})
        return iid

    z["inserat"] = inserat
    z["anfrage"] = anfrage
    yield z
    dbx.users.delete_many({"id": {"$in": [chef_id, k_id]}})
    dbx.dealers.delete_many({"id": did})
    for coll in ("resale_listings", "listing_interest", "network_members",
                 "dealer_invites", "activity_logs"):
        dbx[coll].delete_many({"dealer_id": did})
    dbx.network_members.delete_many({"buyer_user_id": k_id})


def _antwort(m, iid, chef, **body):
    return asyncio.run(m.answer_interest(iid, m.InterestAnswerIn(**body), user=chef))


def _status(m, iid, chef, **body):
    from fastapi import HTTPException
    try:
        r = _antwort(m, iid, chef, **body)
        return 200, r
    except HTTPException as e:
        return e.status_code, e.detail


# --------------------------------------------------------------- Nr. 1/2/3
def test_u_1_akzeptieren_prueft_kaeufer_neu(welt_unit):
    """Gesperrter, deaktivierter, geloeschter oder rollenfremder Kaeufer:
    Haendler kann nicht mehr annehmen oder gegenbieten; Ablehnen geht."""
    import routes.marketplace as m
    w = welt_unit
    dbx = w["dbx"]
    lid = w["inserat"]("public")
    iid = w["anfrage"](lid)

    def inserat_unberuehrt():
        l = dbx.resale_listings.find_one({"id": lid})
        assert l["status"] == "veroeffentlicht" and not l.get("reserved_for"), l
        assert dbx.listing_interest.find_one({"id": iid})["status"] == "offen"

    # deaktiviert (Admin-Sperre users.active)
    dbx.users.update_one({"id": w["k_id"]}, {"$set": {"active": False}})
    assert _status(m, iid, w["chef"], action="akzeptieren")[0] == 409
    assert _status(m, iid, w["chef"], action="gegenangebot", counter_offer=21000)[0] == 409
    inserat_unberuehrt()
    # Betreiber-Sperre des Marktplatz-Zugangs (auch im Kostenlos-Modus)
    dbx.users.update_one({"id": w["k_id"]}, {"$set": {
        "active": True, "marketplace_access": {"gesperrt": True, "active": False}}})
    assert _status(m, iid, w["chef"], action="akzeptieren")[0] == 409
    inserat_unberuehrt()
    # falsche Rolle
    dbx.users.update_one({"id": w["k_id"]}, {"$set": {"role": "sucher"},
                                             "$unset": {"marketplace_access": ""}})
    assert _status(m, iid, w["chef"], action="akzeptieren")[0] == 409
    inserat_unberuehrt()
    # Ablehnen bleibt moeglich (schliesst nur ab)
    dbx.users.update_one({"id": w["k_id"]}, {"$set": {"active": False, "role": "b2b_buyer"}})
    code, r = _status(m, iid, w["chef"], action="ablehnen")
    assert code == 200 and r["status"] == "abgelehnt", (code, r)
    # geloeschtes Konto: neue Anfrage, Kaeufer weg
    iid2 = w["anfrage"](lid)
    dbx.users.delete_one({"id": w["k_id"]})
    assert _status(m, iid2, w["chef"], action="akzeptieren")[0] == 409
    l = dbx.resale_listings.find_one({"id": lid})
    assert l["status"] == "veroeffentlicht" and not l.get("reserved_for")


def test_u_2_3_akzeptieren_prueft_sichtbarkeit(welt_unit):
    """Privates Inserat ohne Netzwerkmitgliedschaft (Widerruf oder
    nachtraeglich privat): 409; im Netzwerk: Annahme reserviert."""
    import routes.marketplace as m
    w = welt_unit
    dbx = w["dbx"]
    lid = w["inserat"]("private")
    iid = w["anfrage"](lid)
    # Kaeufer nicht (mehr) im Netzwerk
    assert _status(m, iid, w["chef"], action="akzeptieren")[0] == 409
    assert _status(m, iid, w["chef"], action="gegenangebot", counter_offer=21000)[0] == 409
    assert dbx.resale_listings.find_one({"id": lid})["status"] == "veroeffentlicht"
    # nicht oeffentlicher Haendler + oeffentliches Inserat: ebenfalls kein Zugang
    lid2 = w["inserat"]("public")
    iid2 = w["anfrage"](lid2)
    dbx.dealers.update_one({"id": w["did"]}, {"$set": {"marketplace.public": False}})
    assert _status(m, iid2, w["chef"], action="akzeptieren")[0] == 409
    dbx.dealers.update_one({"id": w["did"]}, {"$set": {"marketplace.public": True}})
    # im Netzwerk: privates Inserat annehmbar
    dbx.network_members.insert_one({"dealer_id": w["did"], "buyer_user_id": w["k_id"],
                                    "created_at": _jetzt()})
    code, r = _status(m, iid, w["chef"], action="akzeptieren")
    assert code == 200 and r["status"] == "akzeptiert", (code, r)
    l = dbx.resale_listings.find_one({"id": lid})
    assert l["status"] == "reserviert" and l["reserved_for"] == w["k_id"]
    assert dbx.listing_interest.find_one({"id": iid})["agreed_price"] == 20000.0


# ------------------------------------------------------------------- Nr. 48
def test_u_48_eigenes_gegenangebot_nicht_annehmbar(welt_unit):
    import routes.marketplace as m
    w = welt_unit
    dbx = w["dbx"]
    lid = w["inserat"]("public")
    iid = w["anfrage"](lid)
    code, r = _status(m, iid, w["chef"], action="gegenangebot", counter_offer=22000)
    assert code == 200 and r["status"] == "gegenangebot", (code, r)
    code, detail = _status(m, iid, w["chef"], action="akzeptieren")
    assert code == 400 and "Gegenangebot" in str(detail), (code, detail)
    it = dbx.listing_interest.find_one({"id": iid})
    assert it["status"] == "gegenangebot" and "agreed_price" not in it
    assert dbx.resale_listings.find_one({"id": lid})["status"] == "veroeffentlicht"
    # neues Gegenangebot und Ablehnen bleiben moeglich
    assert _status(m, iid, w["chef"], action="gegenangebot", counter_offer=21500)[0] == 200
    # Kaeufer-Gegenangebot -> Haendler nimmt GENAU diesen Betrag an
    dbx.listing_interest.update_one({"id": iid}, {"$set": {
        "status": "gegenangebot_kaeufer", "buyer_counter_offer": 21000.0}})
    code, r = _status(m, iid, w["chef"], action="akzeptieren")
    assert code == 200 and r["status"] == "akzeptiert", (code, r)
    it = dbx.listing_interest.find_one({"id": iid})
    assert it["agreed_price"] == 21000.0
    assert dbx.resale_listings.find_one({"id": lid})["status"] == "reserviert"


def test_u_48_frontend_knopf_nur_offen_und_kaeufer_gegenangebot():
    quelle = (BACKEND.parent / "frontend" / "src" / "pages" / "app" / "Anfragen.jsx").read_text(
        encoding="utf-8")
    block = quelle[quelle.index("anfrage-akzeptieren-") - 600: quelle.index("anfrage-akzeptieren-")]
    assert '["offen", "gegenangebot_kaeufer"].includes(it.status)' in block, \
        "Akzeptieren-Knopf muss auf offen/gegenangebot_kaeufer beschraenkt sein"


# ---------------------------------------------------------------- Nr. 62/63
class _SammlungHaken:
    """Motor-Sammlung mit eingehaengtem Verhalten fuer EINE Methode."""

    def __init__(self, echt, methode, ersatz):
        self._echt, self._methode, self._ersatz = echt, methode, ersatz

    def __getattr__(self, name):
        if name == self._methode:
            return self._ersatz(getattr(self._echt, name))
        return getattr(self._echt, name)


class _DbHaken:
    def __init__(self, echt, sammlung, methode, ersatz):
        self._echt, self._sammlung, self._methode, self._ersatz = echt, sammlung, methode, ersatz

    def __getattr__(self, name):
        coll = getattr(self._echt, name)
        if name == self._sammlung:
            return _SammlungHaken(coll, self._methode, self._ersatz)
        return coll


def _request():
    from starlette.requests import Request
    return Request({"type": "http", "method": "POST", "path": "/api/buyer/register",
                    "headers": [], "client": ("127.0.0.1", 40000), "query_string": b""})


def _register_body(mail, invite=None):
    import routes.marketplace as m
    return m.BuyerRegisterIn(company_name=f"R14 Kaeufer {SUF}", contact_name="K M",
                             email=mail, password=PW, phone="0511 2",
                             invite_token=invite, gewerblich_bestaetigt=True)


@pytest.fixture()
def ohne_limiter(monkeypatch):
    import routes.marketplace as m

    async def frei(_ip):
        return True
    monkeypatch.setattr(m.register_limiter, "check", frei)


def test_u_62_dublette_beim_insert_gibt_409(monkeypatch, ohne_limiter):
    import routes.marketplace as m
    from fastapi import HTTPException
    from pymongo.errors import DuplicateKeyError

    def ersatz(_echt):
        async def insert_one(doc, *a, **kw):
            raise DuplicateKeyError("E11000 duplicate key error index: email_1")
        return insert_one
    monkeypatch.setattr(m, "db", _DbHaken(m.db, "users", "insert_one", ersatz))
    mail = f"r14dup_{SUF}@{MAIL}"
    with pytest.raises(HTTPException) as e:
        asyncio.run(m.buyer_register(_register_body(mail), _request()))
    assert e.value.status_code == 409
    assert _db().users.count_documents({"email": mail}) == 0


def test_u_63_einladungsfehler_kippt_registrierung_nicht(monkeypatch, ohne_limiter):
    import routes.marketplace as m
    from auth import decode_token

    async def kaputt(token, uid):
        raise RuntimeError("Mongo weg")
    monkeypatch.setattr(m, "_redeem_invite", kaputt)
    mail = f"r14inv_{SUF}@{MAIL}"
    dbx = _db()
    try:
        r = asyncio.run(m.buyer_register(_register_body(mail, invite="egal"), _request()))
        assert r["ok"] is True and r["network_joined"] is False, r
        u = dbx.users.find_one({"email": mail})
        assert u and u["role"] == "b2b_buyer" and u["active"] is True
        # Token passt zur gespeicherten Sitzung -> Login sofort moeglich
        assert decode_token(r["token"])["sid"] == u["current_session_id"]
    finally:
        u = dbx.users.find_one({"email": mail}) or {}
        dbx.activity_logs.delete_many({"user_id": u.get("id", "-")})
        dbx.users.delete_many({"email": mail})


# --------------------------------------------------------------- Nr. 64/115
@pytest.fixture()
def einladung_unit(welt_unit):
    w = welt_unit
    dbx = w["dbx"]

    def anlegen(max_uses=1, used_count=0, used_by=None, stunden=24):
        doc = {"id": str(uuid.uuid4()), "dealer_id": w["did"],
               "token": f"r14tok_{uuid.uuid4().hex[:12]}", "expires_at": _jetzt(stunden),
               "max_uses": max_uses, "used_count": used_count, "used_by": used_by or [],
               "created_at": _jetzt()}
        dbx.dealer_invites.insert_one(doc)
        return doc
    w["einladung"] = anlegen
    return w


def _mitglieder(w):
    return w["dbx"].network_members.count_documents(
        {"dealer_id": w["did"], "buyer_user_id": w["k_id"]})


def test_u_64_redeem_normalfall_und_verbrauchter_link(einladung_unit):
    import routes.marketplace as m
    w = einladung_unit
    dbx = w["dbx"]
    inv = w["einladung"](max_uses=1)
    assert asyncio.run(m._redeem_invite(inv["token"], w["k_id"])) == w["did"]
    assert _mitglieder(w) == 1
    d = dbx.dealer_invites.find_one({"id": inv["id"]})
    assert d["used_count"] == 1 and d["used_by"] == [w["k_id"]]
    mitglied = dbx.network_members.find_one({"dealer_id": w["did"], "buyer_user_id": w["k_id"]})
    assert mitglied["via_invite_id"] == inv["id"]
    # erneut: bereits eingeloest, Mitglied -> dealer_id, nichts doppelt
    assert asyncio.run(m._redeem_invite(inv["token"], w["k_id"])) == w["did"]
    assert _mitglieder(w) == 1
    # verbrauchter Link eines ANDEREN Kaeufers: keine Mitgliedschaft, kein Rest
    dbx.network_members.delete_many({"buyer_user_id": w["k_id"]})
    inv2 = w["einladung"](max_uses=1, used_count=1, used_by=["jemand-anders"])
    assert asyncio.run(m._redeem_invite(inv2["token"], w["k_id"])) is None
    assert _mitglieder(w) == 0
    assert dbx.dealer_invites.find_one({"id": inv2["id"]})["used_count"] == 1
    # abgelaufen
    inv3 = w["einladung"](max_uses=5, stunden=-1)
    assert asyncio.run(m._redeem_invite(inv3["token"], w["k_id"])) is None
    assert _mitglieder(w) == 0


def test_u_64_abbruch_vor_verbrauch_laesst_link_gueltig(einladung_unit, monkeypatch):
    """Bricht es beim Anlegen der Mitgliedschaft ab, ist NICHTS verbraucht —
    der naechste Versuch loest den Link regulaer ein (vorher war der
    Einmal-Link nach einem Abbruch zwischen den Schritten verloren)."""
    import routes.marketplace as m
    w = einladung_unit
    dbx = w["dbx"]
    inv = w["einladung"](max_uses=1)

    def ersatz(_echt):
        async def update_one(*a, **kw):
            raise RuntimeError("Mongo weg")
        return update_one
    monkeypatch.setattr(m, "db", _DbHaken(m.db, "network_members", "update_one", ersatz))
    with pytest.raises(RuntimeError):
        asyncio.run(m._redeem_invite(inv["token"], w["k_id"]))
    d = dbx.dealer_invites.find_one({"id": inv["id"]})
    assert d["used_count"] == 0 and d["used_by"] == [] and _mitglieder(w) == 0
    monkeypatch.undo()
    import routes.marketplace as m2
    assert asyncio.run(m2._redeem_invite(inv["token"], w["k_id"])) == w["did"]
    assert _mitglieder(w) == 1


def test_u_115_loeschen_im_fenster_hinterlaesst_kein_mitglied(einladung_unit, monkeypatch):
    """DELETE /dealer/invites zwischen Mitgliedschafts-Upsert und Verbrauch:
    keine Mitgliedschaft bleibt zurueck, Rueckgabe None (Route: 400)."""
    import routes.marketplace as m
    w = einladung_unit
    dbx = w["dbx"]
    inv = w["einladung"](max_uses=1)
    echt_db = m.db

    def ersatz(echt_update):
        async def update_one(*a, **kw):
            r = await echt_update(*a, **kw)
            # der Chef loescht die Einladung genau jetzt
            await echt_db.dealer_invites.delete_one({"id": inv["id"]})
            return r
        return update_one
    monkeypatch.setattr(m, "db", _DbHaken(m.db, "network_members", "update_one", ersatz))
    assert asyncio.run(m._redeem_invite(inv["token"], w["k_id"])) is None
    assert _mitglieder(w) == 0
    assert dbx.dealer_invites.find_one({"id": inv["id"]}) is None


def test_u_115_bestehende_mitgliedschaft_bleibt(einladung_unit, monkeypatch):
    """Eine aeltere Mitgliedschaft (andere Einladung) wird beim Rueckbau
    NICHT mitgeloescht: nur die eben angelegte (upserted_id) faellt."""
    import routes.marketplace as m
    w = einladung_unit
    dbx = w["dbx"]
    dbx.network_members.insert_one({"dealer_id": w["did"], "buyer_user_id": w["k_id"],
                                    "via_invite_id": "alt", "created_at": _jetzt()})
    inv = w["einladung"](max_uses=1)
    echt_db = m.db

    def ersatz(echt_update):
        async def update_one(*a, **kw):
            r = await echt_update(*a, **kw)
            await echt_db.dealer_invites.delete_one({"id": inv["id"]})
            return r
        return update_one
    monkeypatch.setattr(m, "db", _DbHaken(m.db, "network_members", "update_one", ersatz))
    assert asyncio.run(m._redeem_invite(inv["token"], w["k_id"])) is None
    assert _mitglieder(w) == 1
    assert dbx.network_members.find_one({"buyer_user_id": w["k_id"]})["via_invite_id"] == "alt"


# ------------------------------------------------------------------- Nr. 66
def test_u_66_verwaistes_mitglied_ehrlich_gemeldet(welt_unit):
    import routes.marketplace as m
    w = welt_unit
    dbx = w["dbx"]
    weg = f"r14weg_{uuid.uuid4().hex[:8]}"
    dbx.network_members.insert_many([
        {"dealer_id": w["did"], "buyer_user_id": weg, "created_at": _jetzt(-2)},
        {"dealer_id": w["did"], "buyer_user_id": w["k_id"], "created_at": _jetzt(-1)},
    ])
    out = asyncio.run(m.list_network_members(user=w["chef"]))
    je = {e["buyer_user_id"]: e for e in out}
    assert je[weg]["active"] is False and je[weg]["fehlt"] is True
    assert je[weg]["email"] == "" and je[weg]["company_name"] == ""
    assert je[w["k_id"]]["active"] is True and je[w["k_id"]]["fehlt"] is False
    assert je[w["k_id"]]["company_name"] == "K GmbH"


# ------------------------------------------------------------------- Nr. 87
def test_u_87_alle_gueltigen_einladungen_sichtbar(einladung_unit):
    import routes.marketplace as m
    w = einladung_unit
    dbx = w["dbx"]
    gueltig = [w["einladung"](max_uses=5, stunden=100 + i)["id"] for i in range(60)]
    out = asyncio.run(m.list_invites(user=w["chef"]))
    assert len(out) == 60 and all(e["valid"] for e in out)
    # 60 abgelaufene + 60 verbrauchte dazu: alle gueltigen bleiben, ungueltige gedeckelt auf 50
    for i in range(60):
        w["einladung"](max_uses=5, stunden=-1 - i)
    for i in range(60):
        w["einladung"](max_uses=1, used_count=1, used_by=["x"], stunden=50)
    out = asyncio.run(m.list_invites(user=w["chef"]))
    ids = {e["id"] for e in out}
    assert set(gueltig) <= ids, "eine gueltige Einladung fehlt in der Liste"
    assert sum(1 for e in out if e["valid"]) == 60
    assert sum(1 for e in out if not e["valid"]) == 50
    assert len(out) == 110
    assert dbx.dealer_invites.count_documents({"dealer_id": w["did"]}) == 180


# ------------------------------------------------------------------ Nr. 119
def test_u_119_profil_felder_getrennt_und_parallel(welt_unit):
    import routes.marketplace as m
    w = welt_unit
    dbx = w["dbx"]
    # Ausgangslage: noch kein marketplace-Objekt
    dbx.dealers.update_one({"id": w["did"]}, {"$unset": {"marketplace": ""}})
    r = asyncio.run(m.update_marketplace_profile(m.ProfileIn(public=False, description="alt"),
                                                 user=w["chef"]))
    mp = dbx.dealers.find_one({"id": w["did"]})["marketplace"]
    assert r["slug"] == mp["slug"] and mp["slug"].endswith(w["did"][:6])
    assert mp["public"] is False and mp["description"] == "alt" and mp.get("member_since")
    seit, slug = mp["member_since"], mp["slug"]

    async def beide():
        await asyncio.gather(
            m.update_marketplace_profile(m.ProfileIn(public=True), user=w["chef"]),
            m.update_marketplace_profile(m.ProfileIn(description="neu"), user=w["chef"]))
    for _ in range(10):
        dbx.dealers.update_one({"id": w["did"]}, {"$set": {
            "marketplace.public": False, "marketplace.description": "alt"}})
        asyncio.run(beide())
        mp = dbx.dealers.find_one({"id": w["did"]})["marketplace"]
        assert mp["public"] is True and mp["description"] == "neu", mp
    # slug und member_since bleiben beim zweiten Aufruf unveraendert
    assert mp["slug"] == slug and mp["member_since"] == seit
    # leerer PUT schreibt nichts kaputt
    r = asyncio.run(m.update_marketplace_profile(m.ProfileIn(), user=w["chef"]))
    assert r["public"] is True and r["slug"] == slug


# =====================================================================
#                              HTTP-Tests
# =====================================================================
def _login(mail):
    r = requests.post(f"{API}/auth/login", json={"email": mail, "password": PW}, timeout=30)
    assert r.status_code == 200, r.text[:200]
    return _kopf(r.json()["token"])


def _haendler():
    mail = f"r14mp_chef_{SUF}@{MAIL}"
    r = requests.post(f"{API}/auth/register", json={
        "email": mail, "password": PW, "company_name": f"R14 Autohaus {SUF}",
        "contact_person": "Chef", "phone": "0511 1"}, timeout=30)
    assert r.status_code == 200, f"Backend braucht SELF_SIGNUP=true: {r.text[:200]}"
    kopf = _kopf(r.json()["token"])
    r2 = requests.put(f"{API}/dealer/marketplace-profile", headers=kopf,
                      json={"public": True, "description": "R14"}, timeout=30)
    assert r2.status_code == 200, r2.text[:200]
    return {"kopf": kopf, "dealer_id": r.json()["user"]["dealer_id"],
            "user_id": r.json()["user"]["id"], "mail": mail}


def _inserat(h, name, sichtbarkeit):
    dbx = _db()
    vid = str(uuid.uuid4())
    dbx.vehicles.insert_one({
        "id": vid, "dealer_id": h["dealer_id"], "lifecycle": "bestand", "status": "Bestand",
        "purchase_price": 5000,
        "data": {"make_label": "VW", "model_label": name, "mileage": 90000,
                 "first_registration": "01/2020", "fuel_label": "Benzin", "power_ps": 110,
                 "images": []},
        "created_at": _jetzt(), "updated_at": _jetzt()})
    r = requests.post(f"{API}/resale/draft/{vid}", headers=h["kopf"], timeout=30)
    assert r.status_code == 200, r.text[:300]
    lid = r.json()["id"]
    assert requests.put(f"{API}/resale/{lid}", headers=h["kopf"],
                        json={"price_public": 9900, "price_b2b": 9000}, timeout=30).status_code == 200
    assert requests.post(f"{API}/resale/{lid}/status", headers=h["kopf"],
                         json={"status": "verkaufsbereit"}, timeout=30).status_code == 200
    r = requests.post(f"{API}/resale/{lid}/publish", headers=h["kopf"],
                      json={"visibility": sichtbarkeit}, timeout=30)
    assert r.status_code == 200, r.text[:300]
    return lid


def _kaeufer_neu_anmelden(k):
    """Befund 18: Sperren beendet die Sitzung, Entsperren stellt sie NICHT
    wieder her — der Test holt sich wie ein echter Kaeufer ein neues Token."""
    from auth import create_token, new_session_id
    sid = new_session_id()
    _db().users.update_one({"id": k["id"]}, {"$set": {"current_session_id": sid}})
    k["kopf"] = _kopf(create_token(k["id"], sid))
    return k


def _kaeufer_direkt(nr):
    """Kaeufer direkt in Mongo + Token (schont den Registrierungs-Limiter)."""
    import bcrypt
    from auth import create_token, new_session_id
    uid = f"r14mp_k{nr}_{SUF}"
    sid = new_session_id()
    _db().users.insert_one({
        "id": uid, "email": f"{uid}@{MAIL}", "role": "b2b_buyer", "active": True,
        "dealer_id": None, "company_name": f"R14 Kaeufer {nr}", "contact_name": "K M",
        "password_hash": bcrypt.hashpw(PW.encode(), bcrypt.gensalt()).decode(),
        "current_session_id": sid, "created_at": _jetzt()})
    return {"kopf": _kopf(create_token(uid, sid)), "id": uid, "mail": f"{uid}@{MAIL}"}


@pytest.fixture(scope="module")
def welt():
    if not HTTP:
        pytest.skip("HTTP nach Neustart")
    import bcrypt
    dbx = _db()
    z = {}
    admin_mail = f"r14mp_admin_{SUF}@{MAIL}"
    dbx.users.insert_one({
        "id": f"r14mpadm_{SUF}", "email": admin_mail, "role": "admin", "active": True,
        "dealer_id": None, "is_super_admin": True,
        "password_hash": bcrypt.hashpw(PW.encode(), bcrypt.gensalt()).decode(),
        "created_at": "2026-01-01T00:00:00+00:00"})
    z["A"] = _login(admin_mail)
    z["h"] = _haendler()
    z["oeffentlich"] = _inserat(z["h"], f"Golf offen {SUF}", "public")
    z["privat"] = _inserat(z["h"], f"Golf privat {SUF}", "private")
    z["k1"] = _kaeufer_direkt(1)
    z["k2"] = _kaeufer_direkt(2)
    z["k3"] = _kaeufer_direkt(3)
    yield z
    dbx.users.delete_many({"email": {"$regex": f"_{SUF}@"}})
    d = z["h"]["dealer_id"]
    for coll in ("resale_listings", "vehicles", "network_members", "dealer_invites",
                 "listing_interest", "activity_logs", "subscriptions"):
        dbx[coll].delete_many({"dealer_id": d})
    dbx.buyer_favorites.delete_many({"buyer_user_id": {"$regex": f"_{SUF}$"}})
    dbx.dealers.delete_many({"id": d})


def _interesse(welt, k, lid, offer=20000):
    r = requests.post(f"{API}/marktplatz/listings/{lid}/interesse", headers=k["kopf"],
                      json={"offer": offer, "message": "Interesse"}, timeout=30)
    assert r.status_code == 200, r.text[:300]
    return r.json()["interest_id"]


def _antwort_http(welt, iid, **body):
    return requests.post(f"{API}/interessen/{iid}/antwort", headers=welt["h"]["kopf"],
                         json={"message": "", **body}, timeout=30)


def _inserat_frei(lid):
    l = _db().resale_listings.find_one({"id": lid})
    assert l["status"] == "veroeffentlicht" and not l.get("reserved_for"), l


def _inserat_freigeben(lid):
    _db().resale_listings.update_one({"id": lid}, {"$set": {"status": "veroeffentlicht"},
                                                   "$unset": {"reserved_for": ""}})


def test_h_1_gesperrter_und_geloeschter_kaeufer(welt):
    if not HTTP:
        pytest.skip("HTTP nach Neustart")
    k1, k3, lid = welt["k1"], welt["k3"], welt["oeffentlich"]
    iid = _interesse(welt, k1, lid)
    # Admin deaktiviert das Konto
    r = requests.post(f"{API}/admin/users/{k1['id']}/active", headers=welt["A"],
                      json={"active": False}, timeout=30)
    assert r.status_code == 200, r.text[:200]
    r = _antwort_http(welt, iid, action="akzeptieren")
    # Die Sperre schliesst die Anfrage sofort (400 "bereits abgeschlossen");
    # ohne dieses Schliessen greift die Kaeuferpruefung mit 409.
    assert r.status_code in (400, 409), r.text[:300]
    _inserat_frei(lid)
    assert _antwort_http(welt, iid, action="gegenangebot", counter_offer=21000).status_code in (400, 409)
    # Betreiber-Sperre des Marktplatz-Zugangs (neue Anfrage, die alte ist geschlossen)
    r = requests.post(f"{API}/admin/users/{k1['id']}/active", headers=welt["A"],
                      json={"active": True}, timeout=30)
    assert r.status_code == 200, r.text[:200]
    _kaeufer_neu_anmelden(k1)                    # alte Sitzung ist nach der Sperre weg (Befund 18)
    iid2 = _interesse(welt, k1, lid)
    r = requests.post(f"{API}/admin/buyers/{k1['id']}/access", headers=welt["A"],
                      json={"plan": None}, timeout=30)
    assert r.status_code == 200, r.text[:200]
    assert _antwort_http(welt, iid2, action="akzeptieren").status_code == 409
    _inserat_frei(lid)
    # Ablehnen geht trotzdem
    r = _antwort_http(welt, iid2, action="ablehnen")
    assert r.status_code == 200, r.text[:300]
    # geloeschtes Konto
    iid3 = _interesse(welt, k3, lid)
    _db().users.delete_one({"id": k3["id"]})
    assert _antwort_http(welt, iid3, action="akzeptieren").status_code == 409
    _inserat_frei(lid)


def test_h_2_netzwerk_widerruf_beendet_haendlerseite(welt):
    if not HTTP:
        pytest.skip("HTTP nach Neustart")
    h, k2, lid = welt["h"], welt["k2"], welt["privat"]
    r = requests.post(f"{API}/dealer/invites", headers=h["kopf"], json={"max_uses": 5}, timeout=30)
    assert r.status_code == 200, r.text[:300]
    r = requests.post(f"{API}/invites/{r.json()['token']}/redeem", headers=k2["kopf"], timeout=30)
    assert r.status_code == 200, r.text[:300]
    iid = _interesse(welt, k2, lid, 8500)
    assert _antwort_http(welt, iid, action="gegenangebot", counter_offer=8900).status_code == 200
    r = requests.delete(f"{API}/dealer/network/members/{k2['id']}", headers=h["kopf"], timeout=30)
    assert r.status_code == 200, r.text[:300]
    # Kaeuferseite (A10) und jetzt auch Haendlerseite dicht
    assert requests.post(f"{API}/interessen/{iid}/kaeufer-antwort", headers=k2["kopf"],
                         json={"action": "annehmen"}, timeout=30).status_code == 403
    _db().listing_interest.update_one({"id": iid}, {"$set": {"status": "gegenangebot_kaeufer",
                                                             "buyer_counter_offer": 8700.0}})
    r = _antwort_http(welt, iid, action="akzeptieren")
    assert r.status_code == 409, r.text[:300]
    _inserat_frei(lid)
    assert _antwort_http(welt, iid, action="gegenangebot", counter_offer=8800).status_code == 409
    assert _antwort_http(welt, iid, action="ablehnen").status_code == 200


def test_h_48_eigenes_gegenangebot_nicht_annehmbar(welt):
    if not HTTP:
        pytest.skip("HTTP nach Neustart")
    k1, lid = welt["k1"], welt["oeffentlich"]
    # k1 wieder freischalten (Test 1 hat gesperrt)
    r = requests.post(f"{API}/admin/buyers/{k1['id']}/access", headers=welt["A"],
                      json={"plan": "monthly", "zahlungsart": "kulanz", "grund": "Test R14"}, timeout=30)
    assert r.status_code == 200, r.text[:200]
    _kaeufer_neu_anmelden(k1)
    iid = _interesse(welt, k1, lid, 20000)
    assert _antwort_http(welt, iid, action="gegenangebot", counter_offer=22000).status_code == 200
    r = _antwort_http(welt, iid, action="akzeptieren")
    assert r.status_code == 400, r.text[:300]
    _inserat_frei(lid)
    assert _db().listing_interest.find_one({"id": iid})["status"] == "gegenangebot"
    r = requests.post(f"{API}/interessen/{iid}/kaeufer-antwort", headers=k1["kopf"],
                      json={"action": "gegenangebot", "counter_offer": 21000}, timeout=30)
    assert r.status_code == 200, r.text[:300]
    r = _antwort_http(welt, iid, action="akzeptieren")
    assert r.status_code == 200, r.text[:300]
    it = _db().listing_interest.find_one({"id": iid})
    assert it["status"] == "akzeptiert" and it["agreed_price"] == 21000.0
    assert _db().resale_listings.find_one({"id": lid})["status"] == "reserviert"
    _inserat_freigeben(lid)


def test_h_62_parallele_registrierung_gleiche_mail(welt):
    if not HTTP:
        pytest.skip("HTTP nach Neustart")
    mail = f"r14mp_dup_{SUF}@{MAIL}"
    start = _jetzt()
    body = {"gewerblich_bestaetigt": True, "company_name": f"R14 Dup {SUF}",
            "contact_name": "K M", "email": mail, "password": PW, "phone": "0511 2"}

    def schuss(_):
        return requests.post(f"{API}/buyer/register", json=body, timeout=30).status_code
    with ThreadPoolExecutor(max_workers=2) as ex:
        codes = sorted(ex.map(schuss, range(2)))
    assert codes == [200, 409], codes
    assert _db().users.count_documents({"email": mail}) == 1
    assert _db().error_logs.count_documents({"error_type": "DuplicateKeyError",
                                             "created_at": {"$gte": start}}) == 0


def test_h_66_verwaistes_mitglied(welt):
    if not HTTP:
        pytest.skip("HTTP nach Neustart")
    h = welt["h"]
    weg = f"r14mp_weg_{SUF}"
    _db().network_members.insert_one({"dealer_id": h["dealer_id"], "buyer_user_id": weg,
                                      "created_at": _jetzt()})
    r = requests.get(f"{API}/dealer/network/members", headers=h["kopf"], timeout=30)
    assert r.status_code == 200, r.text[:200]
    e = next(x for x in r.json() if x["buyer_user_id"] == weg)
    assert e["active"] is False and e["fehlt"] is True
    assert requests.delete(f"{API}/dealer/network/members/{weg}", headers=h["kopf"],
                           timeout=30).status_code == 200


def test_h_87_alle_gueltigen_einladungen_loeschbar(welt):
    if not HTTP:
        pytest.skip("HTTP nach Neustart")
    h = welt["h"]
    dbx = _db()
    docs = []
    for i in range(60):
        docs.append({"id": str(uuid.uuid4()), "dealer_id": h["dealer_id"],
                     "token": f"r14g_{uuid.uuid4().hex[:12]}", "expires_at": _jetzt(100 + i),
                     "max_uses": 5, "used_count": 0, "used_by": [],
                     "created_at": (JETZT - timedelta(days=20) + timedelta(minutes=i)).isoformat()})
    for i in range(60):
        docs.append({"id": str(uuid.uuid4()), "dealer_id": h["dealer_id"],
                     "token": f"r14a_{uuid.uuid4().hex[:12]}", "expires_at": _jetzt(-1 - i),
                     "max_uses": 1, "used_count": 0, "used_by": [],
                     "created_at": (JETZT - timedelta(days=1) + timedelta(minutes=i)).isoformat()})
    dbx.dealer_invites.insert_many(docs)
    r = requests.get(f"{API}/dealer/invites", headers=h["kopf"], timeout=30)
    assert r.status_code == 200, r.text[:200]
    out = r.json()
    gueltig = [e for e in out if e["valid"]]
    assert {d["id"] for d in docs[:60]} <= {e["id"] for e in gueltig}
    assert len([e for e in out if not e["valid"]]) == 50
    aelteste = min(docs[:60], key=lambda d: d["created_at"])
    assert requests.delete(f"{API}/dealer/invites/{aelteste['id']}", headers=h["kopf"],
                           timeout=30).status_code == 200


def test_h_119_parallele_profil_updates(welt):
    if not HTTP:
        pytest.skip("HTTP nach Neustart")
    h = welt["h"]
    dbx = _db()
    seit = dbx.dealers.find_one({"id": h["dealer_id"]})["marketplace"].get("member_since")

    def put(body):
        return requests.put(f"{API}/dealer/marketplace-profile", headers=h["kopf"],
                            json=body, timeout=30).status_code
    for _ in range(10):
        dbx.dealers.update_one({"id": h["dealer_id"]}, {"$set": {
            "marketplace.public": False, "marketplace.description": "alt"}})
        with ThreadPoolExecutor(max_workers=2) as ex:
            codes = list(ex.map(put, [{"public": True}, {"description": "neu"}]))
        assert codes == [200, 200], codes
        mp = dbx.dealers.find_one({"id": h["dealer_id"]})["marketplace"]
        assert mp["public"] is True and mp["description"] == "neu", mp
    assert mp.get("member_since") == seit
