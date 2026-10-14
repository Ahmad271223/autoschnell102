# -*- coding: utf-8 -*-
"""Rollenprüfung 22.09.2026, Welle 2 — Uebergaben an Team markt_kaeufer
(routes/marketplace.py).

  RP-093(3)/192/343(c)  Fahrzeug-Nacharbeit nur, solange das Inserat auf dem
                        Ziel steht — sonst Merker weg statt nachziehen
  RP-093(4)/343(d)      Annahme ohne Preisangebot: Inseratspreis des Kaeufers
                        als vereinbarter Preis, ohne jeden Preis 400
  RP-093(1)/343(a)      Hand-Reservierung: nicht verhandelbar, "anderweitig"
  RP-098 Nr. 4/5/6/9/11 Zugangsanfrage gesperrt 409, abgelaufener Zugang 409,
                        Audit mit Firma, X-Truncated + Seite 2, privat = 404
  RP-509                Verlaengerungsanfrage ab 7 Tagen vor Ablauf
  RP-557 / Nr. 12       Kaeufer-Login: bekanntes Geraet, Sitzungsfelder
  RP-546                Fahrzeugliste reicht die Antwort an current_user durch
  RP-472                GET /dealer/interessen/anzahl

In-Prozess gegen eine Wegwerf-Datenbank (autoschnell_rpmk2_<uuid>), kein Server.
"""
import asyncio
import importlib
import os
import sys
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from fastapi import HTTPException, Response
from fastapi.security import HTTPAuthorizationCredentials

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

MONGO_URL = os.environ.get("MONGO_URL") or "mongodb://127.0.0.1:27017"
# routes.team: _offene_anfrage_upsert (Zugangsanfrage) schreibt ueber dessen db.
MODULE = ("deps", "routes.marketplace", "routes.resale", "routes.bestand",
          "routes.team", "kaufvorgang", "lifecycle")
PW = "Welle2-Passwort-9x"


def _jetzt(**delta):
    return (datetime.now(timezone.utc) + timedelta(**delta)).isoformat()


def _mod(name):
    return importlib.import_module(name)


class _Welt:
    def __init__(self, db, s):
        self.db, self.s = db, s
        self.did = f"d_rpmk2_{s}"
        self.chef = {"id": f"chef_rpmk2_{s}", "dealer_id": self.did, "role": "dealer"}
        self.k = {"id": f"k_rpmk2_{s}", "dealer_id": None, "role": "b2b_buyer",
                  "company_name": "Kaeufer A GmbH", "email": f"a_{s}@e2etest-mail.de",
                  "active": True}

    async def anlegen(self, public=True):
        await self.db.dealers.insert_one({
            "id": self.did, "user_id": self.chef["id"], "company_name": f"RP2 Autohaus {self.s}",
            "city": "Hannover", "phone": "0511 123",
            "marketplace": {"public": public, "slug": f"rp2-{self.s}"}, "created_at": _jetzt()})
        await self.db.users.insert_many([
            {**self.chef, "active": True, "created_at": _jetzt()},
            {**self.k, "created_at": _jetzt()}])

    async def inserat(self, status="veroeffentlicht", **extra):
        lid = f"l_{self.s}_{uuid.uuid4().hex[:8]}"
        doc = {"id": lid, "dealer_id": self.did, "title": "Haendlertitel Golf",
               "status": status, "visibility": "public",
               "prices": {"public": 9900}, "photos": {"mode": "neu", "uploaded_keys": []},
               "data": {"make_label": "VW", "model_label": "Golf", "mileage": 1000},
               "published_at": _jetzt(), "created_at": _jetzt(), "updated_at": _jetzt()}
        doc.update(extra)
        await self.db.resale_listings.insert_one(doc)
        return lid

    async def anfrage(self, lid, status="offen", offer=20000.0, **extra):
        iid = f"i_{self.s}_{uuid.uuid4().hex[:8]}"
        await self.db.listing_interest.insert_one({
            "id": iid, "listing_id": lid, "dealer_id": self.did, "listing_title": "alt",
            "buyer_user_id": self.k["id"], "buyer_name": self.k["company_name"],
            "offer": offer, "message": "", "status": status, "counter_offer": None,
            "history": [{"von": "kaeufer", "aktion": "interesse", "angebot": offer,
                         "zeit": _jetzt()}],
            "created_at": _jetzt(), "updated_at": _jetzt(), **extra})
        return iid


@pytest.fixture
def welt():
    from motor.motor_asyncio import AsyncIOMotorClient
    s = uuid.uuid4().hex[:10]
    mods = [_mod(n) for n in MODULE]
    alt = [(m, m.__dict__.get("db")) for m in mods]
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    client = AsyncIOMotorClient(MONGO_URL, serverSelectionTimeoutMS=5000)
    name = f"autoschnell_rpmk2_{s}"
    db = client[name]
    for m in mods:
        if "db" in m.__dict__:
            m.db = db
    w = _Welt(db, s)
    w.run = loop.run_until_complete
    w.run(w.anlegen())
    try:
        yield w
    finally:
        try:
            w.run(client.drop_database(name))
        finally:
            for m, d in alt:
                if d is not None:
                    m.db = d
            client.close()
            loop.close()


def _code(w, coro):
    async def lauf():
        try:
            return 200, await coro
        except HTTPException as e:
            return e.status_code, e.detail
    return w.run(lauf())


def _i(w, iid):
    return w.run(w.db.listing_interest.find_one({"id": iid}, {"_id": 0}))


def _l(w, lid):
    return w.run(w.db.resale_listings.find_one({"id": lid}, {"_id": 0}))


def _v(w, vid):
    return w.run(w.db.vehicles.find_one({"id": vid}, {"_id": 0}))


# ======================================================= RP-093(3)
def test_rp093_3_nacharbeit_nur_bei_passendem_inseratstatus(welt):
    M, CS = _mod("routes.marketplace"), _mod("cleanup_service")
    w = welt
    # a) Inserat inzwischen verkauft, Merker "veroeffentlicht" haengt noch:
    #    der Nachholer darf das verkaufte Auto NICHT zurueckdrehen.
    w.run(w.db.vehicles.insert_many([
        {"id": f"va_{w.s}", "dealer_id": w.did, "lifecycle": "verkauft"},
        {"id": f"vb_{w.s}", "dealer_id": w.did, "lifecycle": "veroeffentlicht"},
        {"id": f"vc_{w.s}", "dealer_id": w.did, "lifecycle": "veroeffentlicht"}]))
    la = w.run(w.inserat(status="verkauft", vehicle_id=f"va_{w.s}",
                         lifecycle_nacharbeit="veroeffentlicht", nacharbeit_versuche=4))
    # b) Merker "reserviert", das Inserat ist aber wieder veroeffentlicht
    lb = w.run(w.inserat(status="veroeffentlicht", vehicle_id=f"vb_{w.s}",
                         lifecycle_nacharbeit="reserviert"))
    # c) passt: Inserat reserviert, Fahrzeug haengt noch auf veroeffentlicht
    lc = w.run(w.inserat(status="reserviert", reserved_for=w.k["id"],
                         vehicle_id=f"vc_{w.s}", lifecycle_nacharbeit="reserviert"))
    w.run(CS.inserat_fahrzeug_nacharbeit_nachholen(w.db))
    for lid in (la, lb, lc):
        l = _l(w, lid)
        assert "lifecycle_nacharbeit" not in l and "nacharbeit_versuche" not in l, lid
    assert _v(w, f"va_{w.s}")["lifecycle"] == "verkauft"
    assert _v(w, f"vb_{w.s}")["lifecycle"] == "veroeffentlicht"
    assert _v(w, f"vc_{w.s}")["lifecycle"] == "reserviert"
    # Direkter Aufruf mit veraltetem Ziel: nichts anfassen, kein Merker
    assert w.run(M.inserat_fahrzeug_nachziehen(lb, "reserviert")) is True
    assert "lifecycle_nacharbeit" not in _l(w, lb)
    assert _v(w, f"vb_{w.s}")["lifecycle"] == "veroeffentlicht"


# ======================================================= RP-093(4)
def test_rp093_4_annahme_ohne_angebot_nimmt_den_inseratspreis(welt):
    M = _mod("routes.marketplace")
    w = welt
    # B2B-Kaeufer: der niedrigste fuer ihn zulaessige Preis (B2B 9.500 < 9.900)
    lid = w.run(w.inserat(prices={"public": 9900, "b2b": 9500, "network": 9000}))
    iid = w.run(w.anfrage(lid, offer=None))
    code, _ = _code(w, M.answer_interest(iid, M.InterestAnswerIn(action="akzeptieren"),
                                         user=w.chef))
    assert code == 200
    it = _i(w, iid)
    assert it["status"] == "akzeptiert"
    assert it["agreed_price"] == 9500 and it["agreed_price_quelle"] == "inserat_b2b"
    assert it["history"][-1]["angebot"] == 9500
    assert _l(w, lid)["status"] == "reserviert"
    # Netzwerkmitglied: Netzwerkpreis
    w.run(w.db.network_members.insert_one({"dealer_id": w.did, "buyer_user_id": w.k["id"]}))
    lid2 = w.run(w.inserat(prices={"public": 9900, "b2b": 9500, "network": 9000}))
    iid2 = w.run(w.anfrage(lid2, offer=None))
    assert _code(w, M.answer_interest(iid2, M.InterestAnswerIn(action="akzeptieren"),
                                      user=w.chef))[0] == 200
    assert _i(w, iid2)["agreed_price"] == 9000
    # Mit Angebot: wie bisher das Angebot, keine Quelle "inserat"
    lid3 = w.run(w.inserat())
    iid3 = w.run(w.anfrage(lid3, offer=8800.0))
    assert _code(w, M.answer_interest(iid3, M.InterestAnswerIn(action="akzeptieren"),
                                      user=w.chef))[0] == 200
    assert _i(w, iid3)["agreed_price"] == 8800 and "agreed_price_quelle" not in _i(w, iid3)
    # Weder Angebot noch Preis: nicht blind annehmen, nichts reserviert
    lid4 = w.run(w.inserat(prices={"public": None}))
    iid4 = w.run(w.anfrage(lid4, offer=None))
    code, text = _code(w, M.answer_interest(iid4, M.InterestAnswerIn(action="akzeptieren"),
                                            user=w.chef))
    assert code == 400 and "Gegenangebot" in text
    assert _l(w, lid4)["status"] == "veroeffentlicht"
    assert _i(w, iid4)["status"] == "offen"


# ======================================================= RP-093(1)
def test_rp093_1_hand_reservierung_nicht_verhandelbar(welt):
    M = _mod("routes.marketplace")
    w = welt
    assert M._inserat_verhandelbar({"status": "reserviert", "reserviert_manuell": True}, "k1") is False
    assert M._inserat_verhandelbar({"status": "reserviert", "reserved_for": None}, "k1") is False
    assert M._inserat_verhandelbar({"status": "reserviert", "reserved_for": "k1"}, "k1") is True
    lid = w.run(w.inserat(status="reserviert", reserviert_manuell=True))
    iid = w.run(w.anfrage(lid, status="gegenangebot", counter_offer=21000.0))
    code, _ = _code(w, M.buyer_answer_interest(
        iid, M.BuyerInterestAnswerIn(action="gegenangebot", counter_offer=19000), user=w.k))
    assert code == 409
    code, _ = _code(w, M.answer_interest(
        iid, M.InterestAnswerIn(action="gegenangebot", counter_offer=20500), user=w.chef))
    assert code == 409
    # Beide Listen sagen "anderweitig reserviert"
    liste = w.run(M.buyer_interests(Response(), user=w.k))
    assert liste[0]["anderweitig_reserviert"] is True
    # Beenden bleibt moeglich
    assert _code(w, M.buyer_answer_interest(
        iid, M.BuyerInterestAnswerIn(action="ablehnen"), user=w.k))[0] == 200


# ======================================================= RP-098 Nr. 5
def test_rp098_5_abgelaufener_zugang_haendler_kann_nicht_reservieren(welt, monkeypatch):
    M = _mod("routes.marketplace")
    w = welt
    monkeypatch.setattr(M, "MARKTPLATZ_KOSTENLOS", False)
    w.run(w.db.users.update_one({"id": w.k["id"]}, {"$set": {"marketplace_access": {
        "active": True, "plan": "monthly", "expires_at": _jetzt(days=-1)}}}))
    lid = w.run(w.inserat())
    iid = w.run(w.anfrage(lid))
    code, text = _code(w, M.answer_interest(iid, M.InterestAnswerIn(action="akzeptieren"),
                                            user=w.chef))
    assert code == 409 and "abgelaufen" in text
    assert _l(w, lid)["status"] == "veroeffentlicht"
    # Ablehnen geht weiter (schliesst nur ab)
    assert _code(w, M.answer_interest(iid, M.InterestAnswerIn(action="ablehnen"),
                                      user=w.chef))[0] == 200
    # Mit gueltigem Zugang wie bisher
    w.run(w.db.users.update_one({"id": w.k["id"]}, {"$set": {
        "marketplace_access.expires_at": _jetzt(days=10)}}))
    iid2 = w.run(w.anfrage(w.run(w.inserat())))
    assert _code(w, M.answer_interest(iid2, M.InterestAnswerIn(action="akzeptieren"),
                                      user=w.chef))[0] == 200


# ======================================================= RP-098 Nr. 4 / RP-509
def test_rp098_4_rp509_zugangsanfrage(welt, monkeypatch):
    M = _mod("routes.marketplace")
    w = welt
    gesperrt = {**w.k, "marketplace_access": {"gesperrt": True, "active": False}}
    code, text = _code(w, M.request_marketplace_access(user=gesperrt))
    assert code == 409 and "gesperrt" in text
    assert w.run(w.db.plan_requests.count_documents({"buyer_user_id": w.k["id"]})) == 0
    # Kostenlos-Modus: bereits aktiv, keine Anfrage
    erg = w.run(M.request_marketplace_access(user=w.k))
    assert "bereits aktiv" in erg["hinweis"] and "request_id" not in erg
    # Bezahlmodus, noch 30 Tage: "bereits aktiv (bis …)"
    monkeypatch.setattr(M, "MARKTPLATZ_KOSTENLOS", False)
    lang = {**w.k, "marketplace_access": {"active": True, "plan": "monthly",
                                          "expires_at": _jetzt(days=30)}}
    erg = w.run(M.request_marketplace_access(user=lang))
    assert "bereits aktiv (bis " in erg["hinweis"] and "request_id" not in erg
    # 3 Tage vor Ablauf: Verlaengerungsanfrage
    kurz = {**w.k, "marketplace_access": {"active": True, "plan": "monthly",
                                          "expires_at": _jetzt(days=3)}}
    erg = w.run(M.request_marketplace_access(user=kurz))
    assert erg["verlaengerung"] is True and erg["request_id"]
    doc = w.run(w.db.plan_requests.find_one({"id": erg["request_id"]}, {"_id": 0}))
    assert doc["verlaengerung"] is True and doc["wanted"].startswith("Verlängerung")
    assert doc["zugang_bis"] == kurz["marketplace_access"]["expires_at"]
    # Ohne Zugang: normale Anfrage (dieselbe offene Anfrage wird aufgefrischt)
    ohne = {**w.k, "marketplace_access": {}}
    erg2 = w.run(M.request_marketplace_access(user=ohne))
    assert erg2["request_id"] == erg["request_id"] and erg2["verlaengerung"] is False


# ======================================================= RP-098 Nr. 6
def test_rp098_6_kaeufer_antwort_im_firmenprotokoll(welt):
    M = _mod("routes.marketplace")
    w = welt
    lid = w.run(w.inserat())
    iid = w.run(w.anfrage(lid, status="gegenangebot", counter_offer=19500.0))
    assert _code(w, M.buyer_answer_interest(
        iid, M.BuyerInterestAnswerIn(action="annehmen", erwarteter_betrag=19500), user=w.k))[0] == 200
    eintrag = w.run(w.db.activity_logs.find_one(
        {"action": "interesse.kaeufer.annehmen", "ref": iid}, {"_id": 0}))
    assert eintrag and eintrag["dealer_id"] == w.did


# ======================================================= RP-098 Nr. 9
async def _listings(M, user, response=None, **k):
    args = dict(q=None, make=None, model=None, fuel=None, price_min=None, price_max=None,
                km_min=None, km_max=None, ps_min=None, ps_max=None, sort=None, dealer=None,
                nur_favoriten=0, page=1, limit=300)
    args.update(k)
    return await M.browse_listings(user=user, response=response, **args)


def test_rp098_9_weitere_seite_wird_gemeldet(welt):
    M = _mod("routes.marketplace")
    w = welt
    ids = [w.run(w.inserat(published_at=_jetzt(minutes=-i))) for i in range(3)]
    r1 = Response()
    seite1 = w.run(_listings(M, w.k, response=r1, limit=2, page=1))
    assert [v["id"] for v in seite1] == ids[:2]
    assert r1.headers["X-Truncated"] == "1"
    r2 = Response()
    seite2 = w.run(_listings(M, w.k, response=r2, limit=2, page=2))
    assert [v["id"] for v in seite2] == ids[2:]
    assert r2.headers["X-Truncated"] == "0"
    # Direkter Aufruf ohne Antwort-Objekt geht weiter
    assert len(w.run(_listings(M, None))) == 3


# ======================================================= RP-098 Nr. 11
def test_rp098_11_privates_haendlerprofil_wie_unbekannt(welt):
    M = _mod("routes.marketplace")
    w = welt
    w.run(w.db.dealers.update_one({"id": w.did}, {"$set": {"marketplace.public": False}}))
    privat = _code(w, M.dealer_page(f"rp2-{w.s}", None))
    unbekannt = _code(w, M.dealer_page(f"gibt-es-nicht-{w.s}", None))
    assert privat == unbekannt == (404, "Händler nicht gefunden")
    # Netzwerkmitglied sieht die Seite weiter
    w.run(w.db.network_members.insert_one({"dealer_id": w.did, "buyer_user_id": w.k["id"]}))
    assert _code(w, M.dealer_page(f"rp2-{w.s}", w.k))[0] == 200


# ======================================================= RP-557 / RP-098 Nr. 12
def _request(ip):
    from starlette.requests import Request
    return Request({"type": "http", "method": "POST", "path": "/api/buyer/login",
                    "headers": [(b"user-agent", b"Mozilla/5.0 (iPhone; CPU iPhone OS 17_0) Safari/604.1")],
                    "client": (ip, 40000), "query_string": b""})


def _ip():
    return f"203.0.113.{uuid.uuid4().int % 250 + 1}"


def test_rp557_kaeufer_login_merkt_geraet_und_sitzung(welt, monkeypatch):
    M, rl = _mod("routes.marketplace"), _mod("rate_limiter")
    from auth import hash_password
    w = welt
    nr = str(800000000 + uuid.uuid4().int % 100000000)
    w.run(w.db.users.update_one({"id": w.k["id"]}, {"$set": {
        "kontonummer": nr, "password_hash": hash_password(PW)}}))
    ip1 = _ip()
    code, erg = _code(w, M.buyer_login(M.BuyerLoginIn(kontonummer=nr, password=PW), _request(ip1)))
    assert code == 200, erg
    gid = erg.get("geraet_id")
    assert gid and rl.geraet_id_gueltig(gid) == gid
    u = w.run(w.db.users.find_one({"id": w.k["id"]}, {"_id": 0}))
    assert u["current_session_ip"] == ip1 and u["current_session_seit"]
    assert "iPhone" in u["current_session_geraet"]
    assert rl.geraet_merkwert(gid) in u["login_geraete_bekannt"]
    assert "login_geraete_bekannt" not in erg["user"]
    # Mitgeschickter Schluessel wird wiederverwendet (kein zweiter Eintrag)
    code, erg2 = _code(w, M.buyer_login(
        M.BuyerLoginIn(kontonummer=nr, password=PW, geraet_id=gid), _request(ip1)))
    assert code == 200 and erg2["geraet_id"] == gid
    u = w.run(w.db.users.find_one({"id": w.k["id"]}, {"_id": 0}))
    assert u["login_geraete_bekannt"].count(rl.geraet_merkwert(gid)) == 1

    # Konto-Sperre erreicht (Angreifer): neue IP ohne Geraet gesperrt, das
    # bekannte Geraet von einer NEUEN IP (Mobilnetz) kommt weiter rein.
    monkeypatch.setattr(rl, "_RATE_LIMIT_ENABLED", True)
    if rl._LOGIN_KONTO_LIMIT <= 0:
        pytest.skip("Konto-Limiter abgeschaltet")

    async def voll(_kennung):
        return rl._LOGIN_KONTO_LIMIT

    monkeypatch.setattr(rl.login_konto_limiter, "stand", voll)
    fremd = _code(w, M.buyer_login(M.BuyerLoginIn(kontonummer=nr, password=PW), _request(_ip())))
    assert fremd[0] == 429
    bekannt = _code(w, M.buyer_login(
        M.BuyerLoginIn(kontonummer=nr, password=PW, geraet_id=gid), _request(_ip())))
    assert bekannt[0] == 200, bekannt


# ======================================================= RP-546
def test_rp546_fahrzeugliste_liefert_frisches_token(welt):
    M = _mod("routes.marketplace")
    from auth import NEUES_TOKEN_KOPF, create_token, decode_token
    w = welt
    sid = f"sid_{w.s}"
    w.run(w.db.users.update_one({"id": w.k["id"]}, {"$set": {"current_session_id": sid}}))
    seit = int((datetime.now(timezone.utc) - timedelta(days=6)).timestamp())
    alt = create_token(w.k["id"], sid, seit=seit,
                       bis=datetime.now(timezone.utc) + timedelta(days=1))
    antwort = Response()
    nutzer = w.run(M.marktplatz_besucher(
        HTTPAuthorizationCredentials(scheme="Bearer", credentials=alt), antwort))
    assert nutzer["id"] == w.k["id"]
    neu = antwort.headers.get(NEUES_TOKEN_KOPF)
    assert neu and decode_token(neu)["sid"] == sid
    # Ohne Token bleibt es oeffentlich (keine Kopfzeile)
    leer = Response()
    assert w.run(M.marktplatz_besucher(None, leer)) is None
    assert NEUES_TOKEN_KOPF not in leer.headers


# ======================================================= RP-472
def test_rp472_anfragen_zaehler_fuer_den_chef(welt):
    M = _mod("routes.marketplace")
    w = welt
    lid = w.run(w.inserat())
    for status in ("offen", "offen", "gegenangebot_kaeufer", "gegenangebot",
                   "akzeptiert", "abgelehnt"):
        w.run(w.anfrage(lid, status=status))
    # fremde Firma zaehlt nicht
    w.run(w.db.listing_interest.insert_one({"id": f"fremd_{w.s}", "dealer_id": "andere",
                                            "status": "offen", "listing_id": lid}))
    erg = w.run(M.dealer_interessen_anzahl(user=w.chef))
    assert erg == {"anzahl": 3, "offen": 2, "gegenangebot_kaeufer": 1}
