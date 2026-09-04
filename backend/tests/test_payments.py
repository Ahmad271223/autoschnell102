# -*- coding: utf-8 -*-
"""Stripe-Zahlungen mit dem offiziellen SDK (Pruefbericht 09/2026) —
In-Process-Tests: FastAPI-App nur aus dem Payments-Router, Motor gegen die
lokale Mongo, Stripe-SDK-Aufrufe (Session.create/retrieve) gemockt, die
Webhook-Signatur dagegen ECHT (HMAC wie Stripe sie bildet, verifiziert durch
stripe.Webhook.construct_event).

Abgedeckt:
- /payments/config spiegelt stripe_aktiv (Key + Secret gesetzt, kein Mock)
- Checkout: Session ueber das SDK (mode=payment, 2000 Cent, Idempotency-Key,
  Erfolgs-/Abbruch-URL nur aus origin_url), Transaktion "initiated";
  403 fuer Firmen, 400 unbekannter Plan, 503 wenn Stripe nicht aktiv,
  422 fremde origin_url
- Webhook: falsche/fehlende/abgelaufene Signatur 400, ohne Secret 503
- checkout.session.completed schaltet 30 Tage frei, EIN Beleg in
  manual_payments, Status active; dasselbe Ereignis zweimal -> ein Beleg,
  Ablauf unveraendert
- Status-Endpunkt: Eigentuemer-Pruefung, Aktivierung ueber Session.retrieve
- Freischaltung scheitert -> activation_failed + Betriebsalarm
  zahlung_ohne_zugang; zahlungen_abgleichen holt nach (auch haengendes
  "activating")
- async_payment_failed -> failed, expired -> expired, completed ohne
  Zahlungseingang bleibt initiated, fremde Ereignistypen 200

Braucht nur Mongo (MONGO_URL / DB_NAME) — kein laufendes Backend, kein
Stripe-Konto. Reihenfolgeabhaengig: immer die ganze Datei laufen lassen.
"""
import asyncio
import hashlib
import hmac
import json
import os
import sys
import time
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

MONGO_URL = os.environ.get("MONGO_URL") or "mongodb://127.0.0.1:27017"
DB_NAME = os.environ.get("DB_NAME") or "autoschnell"
os.environ.setdefault("MONGO_URL", MONGO_URL)
os.environ.setdefault("DB_NAME", DB_NAME)

import httpx  # noqa: E402
import stripe  # noqa: E402
from fastapi import APIRouter, FastAPI  # noqa: E402
from motor.motor_asyncio import AsyncIOMotorClient  # noqa: E402

import deps  # noqa: E402
import routes.payments as p  # noqa: E402

SUF = uuid.uuid4().hex[:8]
SECRET = f"whsec_test_{SUF}"
API_KEY = "sk_test_dummy"
ORIGIN = "http://localhost:3000"
CHECKOUT = {"plan": "marktplatz", "origin_url": ORIGIN}


def _db():
    from pymongo import MongoClient
    return MongoClient(MONGO_URL, serverSelectionTimeoutMS=5000)[DB_NAME]


def _buyer(nr: int) -> dict:
    return {"id": f"pay_k{nr}_{SUF}", "email": f"pay_k{nr}_{SUF}@e2etest-mail.de",
            "role": "b2b_buyer", "active": True, "dealer_id": None,
            "company_name": f"Pay Kaeufer {nr} {SUF}",
            "current_session_id": f"sid_{SUF}",
            "created_at": "2026-01-01T00:00:00+00:00"}


@pytest.fixture(scope="module")
def welt():
    z = {"K1": _buyer(1), "K2": _buyer(2),
         "F": {"id": f"pay_f_{SUF}", "email": f"pay_f_{SUF}@e2etest-mail.de",
               "role": "dealer", "dealer_id": f"pay_d_{SUF}", "active": True,
               "current_session_id": f"sid_{SUF}"}}
    dbx = _db()
    dbx.users.insert_many([dict(z["K1"]), dict(z["K2"]), dict(z["F"])])
    yield z
    dbx.users.delete_many({"id": {"$regex": f"_{SUF}$"}})
    for coll, feld in (("payment_transactions", "session_id"),
                       ("manual_payments", "zahlung_ref"),
                       ("betriebsalarme", "ref"),
                       ("subscriptions", "session_id")):
        dbx[coll].delete_many({feld: {"$regex": f"^cs_test_{SUF}"}})


@pytest.fixture
def env(monkeypatch):
    monkeypatch.setenv("STRIPE_API_KEY", API_KEY)
    monkeypatch.setenv("STRIPE_WEBHOOK_SECRET", SECRET)
    monkeypatch.setenv("CORS_ORIGINS", ORIGIN)
    monkeypatch.delenv("MOCK_PROVIDER_FETCH", raising=False)
    return monkeypatch


class _StripeFake:
    """Ersatz fuer stripe.checkout.Session.create/retrieve (kein Netz).
    Liefert echte Session-Objekte des SDK (construct_from), damit der
    Produktionscode denselben Objekttyp sieht wie mit Stripe."""

    def __init__(self):
        self.create_calls = []
        self.retrieve_status = {}   # session_id -> (payment_status, status)
        self.n = 0

    def create(self, **params):
        self.create_calls.append(params)
        self.n += 1
        sid = f"cs_test_{SUF}_{self.n}"
        return stripe.checkout.Session.construct_from({
            "id": sid, "object": "checkout.session",
            "url": f"https://checkout.stripe.com/c/pay/{sid}",
            "payment_status": "unpaid", "status": "open",
            "metadata": params.get("metadata") or {}}, "sk_test")

    def retrieve(self, sid, **params):
        ps, st = self.retrieve_status.get(sid, ("unpaid", "open"))
        return stripe.checkout.Session.construct_from({
            "id": sid, "object": "checkout.session", "payment_status": ps,
            "status": st, "payment_intent": f"pi_{sid}"}, "sk_test")


@pytest.fixture(scope="module")
def fake():
    return _StripeFake()


@pytest.fixture
def stripe_fake(monkeypatch, fake):
    monkeypatch.setattr(stripe.checkout.Session, "create", staticmethod(fake.create))
    monkeypatch.setattr(stripe.checkout.Session, "retrieve", staticmethod(fake.retrieve))
    return fake


def _app(user=None) -> FastAPI:
    """Minimal-App wie in server.py: Router unter /api, Webhook direkt an app."""
    app = FastAPI()
    api = APIRouter(prefix="/api")
    api.include_router(p.router)
    app.include_router(api)
    app.post("/api/webhook/stripe")(p.stripe_webhook)
    if user is not None:
        app.dependency_overrides[deps.current_user] = lambda: user
    return app


def _client(app):
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app),
                             base_url="http://test")


def _lauf(szenario):
    """Szenario in eigenem Loop mit eigener Motor-Verbindung ausfuehren;
    routes.payments.db zeigt solange darauf (Modul-Global — patchen und
    awaiten muessen im SELBEN async-Kontext passieren, wie in
    test_betreiber)."""
    async def _inner():
        cl = AsyncIOMotorClient(MONGO_URL, serverSelectionTimeoutMS=5000)
        mdb = cl[DB_NAME]
        alt = p.db
        p.db = mdb
        try:
            return await szenario(mdb)
        finally:
            p.db = alt
            cl.close()
    return asyncio.run(_inner())


def _signatur(payload: bytes, secret: str = SECRET, ts=None) -> str:
    """Stripe-Signature-Header genau so, wie Stripe ihn bildet:
    t=<ts>,v1=HMAC_SHA256(secret, "<ts>.<payload>")."""
    ts = ts or int(time.time())
    mac = hmac.new(secret.encode(), f"{ts}.{payload.decode()}".encode(),
                   hashlib.sha256).hexdigest()
    return f"t={ts},v1={mac}"


def _event(session_id, typ="checkout.session.completed",
           payment_status="paid", status="complete") -> bytes:
    return json.dumps({
        "id": f"evt_{uuid.uuid4().hex[:12]}", "object": "event", "type": typ,
        "created": int(time.time()),
        "data": {"object": {
            "id": session_id, "object": "checkout.session",
            "payment_status": payment_status, "status": status,
            "payment_intent": f"pi_{session_id}", "metadata": {}}},
    }).encode()


async def _webhook(client, payload: bytes, header):
    headers = {"Content-Type": "application/json"}
    if header is not None:
        headers["Stripe-Signature"] = header
    return await client.post("/api/webhook/stripe", content=payload,
                             headers=headers)


# ---------- Config-Flag ----------
def test_01_config_spiegelt_flag(env, welt):
    async def sz(mdb):
        async with _client(_app()) as c:
            r = await c.get("/api/payments/config")
            assert r.status_code == 200, r.text
            daten = r.json()
            # Feldweise pruefen statt die ganze Antwort zu vergleichen: ein
            # zusaetzliches Feld ist kein Fehler, ein falscher Wert schon.
            # (Die Gleichheitspruefung brach beim Ergaenzen von
            # marktplatz_kostenlos, obwohl nichts kaputt war.)
            for feld, wert in (("stripe_aktiv", True), ("preis", 20.0),
                               ("waehrung", "eur"), ("tage", 30)):
                assert daten[feld] == wert, (feld, daten)
            # Die Startseite braucht die Angabe, ob der Zugang Geld kostet.
            assert isinstance(daten["marktplatz_kostenlos"], bool), daten
            # Nichts Geheimes in einer oeffentlichen Antwort.
            for verboten in ("sk_", "secret", "key", "password"):
                assert verboten not in r.text.lower(), (verboten, r.text)
            env.delenv("STRIPE_WEBHOOK_SECRET")
            assert (await c.get("/api/payments/config")).json()["stripe_aktiv"] is False
            env.setenv("STRIPE_WEBHOOK_SECRET", SECRET)
            env.setenv("MOCK_PROVIDER_FETCH", "true")
            assert (await c.get("/api/payments/config")).json()["stripe_aktiv"] is False
            assert p.stripe_aktiv() is False
            env.delenv("MOCK_PROVIDER_FETCH")
            env.delenv("STRIPE_API_KEY")
            assert p.stripe_aktiv() is False
    _lauf(sz)


# ---------- Checkout ----------
def test_02_checkout_legt_session_und_transaktion_an(env, stripe_fake, welt):
    async def sz(mdb):
        async with _client(_app(welt["K1"])) as c:
            r = await c.post("/api/payments/checkout",
                             json={"plan": "marktplatz", "origin_url": ORIGIN + "/"})
            assert r.status_code == 200, r.text
            body = r.json()
            sid = body["session_id"]
            assert sid.startswith(f"cs_test_{SUF}") and body["url"].endswith(sid)
            welt["sid1"] = sid
            tx = await mdb.payment_transactions.find_one({"session_id": sid}, {"_id": 0})
            assert tx and tx["status"] == "initiated" and tx["payment_status"] == "unpaid"
            assert tx["user_id"] == welt["K1"]["id"]
            assert tx["amount"] == 20.0 and tx["currency"] == "eur"
            assert tx["plan"] == "marktplatz" and tx["created_at"] and tx["updated_at"]
            # Aufruf des offiziellen SDK
            params = stripe_fake.create_calls[-1]
            assert params["api_key"] == API_KEY
            assert params["idempotency_key"] == f"checkout-{tx['id']}"
            assert params["client_reference_id"] == tx["id"]
            assert params["mode"] == "payment"
            li = params["line_items"][0]
            assert li["quantity"] == 1
            assert li["price_data"]["unit_amount"] == 2000
            assert li["price_data"]["currency"] == "eur"
            assert li["price_data"]["product_data"]["name"] == "AutoSchnell Marktplatz-Zugang 30 Tage"
            assert params["success_url"] == (
                f"{ORIGIN}/markt/zahlung-erfolg?session_id={{CHECKOUT_SESSION_ID}}")
            assert params["cancel_url"] == f"{ORIGIN}/markt"
            assert params["metadata"]["user_id"] == welt["K1"]["id"]
            assert params["metadata"]["tx_id"] == tx["id"]
            # Fremde origin_url -> 422 (Validator gegen CORS_ORIGINS)
            r = await c.post("/api/payments/checkout",
                             json={"plan": "marktplatz", "origin_url": "https://boese.example"})
            assert r.status_code == 422, r.text
            # Unbekannter Plan -> 400
            r = await c.post("/api/payments/checkout",
                             json={"plan": "monthly", "origin_url": ORIGIN})
            assert r.status_code == 400, r.text
            # Stripe nicht aktiv -> 503 mit Hinweis auf die Rechnung
            env.delenv("STRIPE_WEBHOOK_SECRET")
            r = await c.post("/api/payments/checkout", json=CHECKOUT)
            assert r.status_code == 503, r.text
            assert "Rechnung" in r.json()["detail"]
            env.setenv("STRIPE_WEBHOOK_SECRET", SECRET)
        # Firma -> 403 (Rechnung statt Stripe)
        async with _client(_app(welt["F"])) as c:
            r = await c.post("/api/payments/checkout", json=CHECKOUT)
            assert r.status_code == 403 and "Rechnung" in r.json()["detail"]
        # Nur der eine erfolgreiche Aufruf hat eine Session angelegt
        assert len(stripe_fake.create_calls) == 1
    _lauf(sz)


# ---------- Webhook: Signatur ----------
def test_03_webhook_lehnt_unsignierte_ereignisse_ab(env, welt):
    async def sz(mdb):
        payload = _event(welt["sid1"])
        async with _client(_app()) as c:
            # falsches Secret
            r = await _webhook(c, payload, _signatur(payload, secret="whsec_falsch"))
            assert r.status_code == 400, r.text
            # gar kein Header
            assert (await _webhook(c, payload, None)).status_code == 400
            # Replay: Zeitstempel aelter als die Stripe-Toleranz (5 min)
            r = await _webhook(c, payload, _signatur(payload, ts=int(time.time()) - 3600))
            assert r.status_code == 400
            # manipulierter Body zu gueltiger Signatur
            r = await _webhook(c, payload.replace(b'"paid"', b'"paid "'), _signatur(payload))
            assert r.status_code == 400
            # ohne konfiguriertes Secret: 503 — selbst bei "gueltiger" Signatur
            env.delenv("STRIPE_WEBHOOK_SECRET")
            r = await _webhook(c, payload, _signatur(payload))
            assert r.status_code == 503, r.text
        tx = await mdb.payment_transactions.find_one({"session_id": welt["sid1"]})
        assert tx["status"] == "initiated"
        u = await mdb.users.find_one({"id": welt["K1"]["id"]})
        assert not (u.get("marketplace_access") or {}).get("active")
        assert await mdb.manual_payments.count_documents({"zahlung_ref": welt["sid1"]}) == 0
    _lauf(sz)


# ---------- Webhook: completed ----------
def test_04_completed_schaltet_30_tage_frei_und_bucht_einmal(env, welt):
    async def sz(mdb):
        sid = welt["sid1"]
        payload = _event(sid)
        welt["payload1"], welt["header1"] = payload, _signatur(payload)
        vorher = datetime.now(timezone.utc)
        async with _client(_app()) as c:
            r = await _webhook(c, payload, welt["header1"])
            assert r.status_code == 200, r.text
            assert r.json()["ok"] is True
        tx = await mdb.payment_transactions.find_one({"session_id": sid}, {"_id": 0})
        assert tx["status"] == "active" and tx["payment_status"] == "paid"
        assert tx["paid_at"] and tx["activated_at"]
        assert tx["stripe_payment_intent"] == f"pi_{sid}"
        u = await mdb.users.find_one({"id": welt["K1"]["id"]})
        acc = u["marketplace_access"]
        assert acc["active"] is True and acc["activated_by"] == "stripe"
        assert acc["session_id"] == sid and acc["price"] == 20.0
        ablauf = datetime.fromisoformat(acc["expires_at"])
        assert abs((ablauf - (vorher + timedelta(days=30))).total_seconds()) < 120
        assert tx["period_until"] == acc["expires_at"]
        belege = await mdb.manual_payments.find({"zahlung_ref": sid}, {"_id": 0}).to_list(10)
        assert len(belege) == 1, belege
        b = belege[0]
        assert b["subject_user_id"] == welt["K1"]["id"] and b["plan"] == "marktplatz"
        assert b["amount"] == 20.0 and b["currency"] == "eur"
        assert b["quelle"] == "stripe" and b["recorded_by"] == "stripe"
        assert b["period_until"] == acc["expires_at"]
        assert b["paid_at"] == tx["paid_at"][:10]
        assert b["id"] and b["created_at"]
        welt["ablauf1"] = acc["expires_at"]
    _lauf(sz)


def test_05_gleiches_ereignis_zweimal_aktiviert_nicht_doppelt(env, welt):
    async def sz(mdb):
        sid = welt["sid1"]
        async with _client(_app()) as c:
            # exakt dieselbe Zustellung (gleicher Body, gleicher Header)
            r = await _webhook(c, welt["payload1"], welt["header1"])
            assert r.status_code == 200, r.text
            # ... und noch einmal als async_payment_succeeded
            pl = _event(sid, typ="checkout.session.async_payment_succeeded")
            assert (await _webhook(c, pl, _signatur(pl))).status_code == 200
        u = await mdb.users.find_one({"id": welt["K1"]["id"]})
        assert u["marketplace_access"]["expires_at"] == welt["ablauf1"]
        assert await mdb.manual_payments.count_documents({"zahlung_ref": sid}) == 1
        tx = await mdb.payment_transactions.find_one({"session_id": sid}, {"_id": 0})
        assert tx["status"] == "active"
        # Direkter Aufruf der Aktivierung: ebenfalls nein (schon active)
        assert await p._activate_paid_transaction(tx, sid) is False
        assert await mdb.manual_payments.count_documents({"zahlung_ref": sid}) == 1
    _lauf(sz)


# ---------- Status-Endpunkt ----------
def test_06_status_endpunkt(env, stripe_fake, welt):
    async def sz(mdb):
        sid = welt["sid1"]
        async with _client(_app(welt["K1"])) as c:
            r = await c.get(f"/api/payments/status/{sid}")
            assert r.status_code == 200, r.text
            assert r.json()["payment_status"] == "paid" and r.json()["status"] == "active"
            r = await c.get(f"/api/payments/status/cs_test_{SUF}_gibtsnicht")
            assert r.status_code == 404
        async with _client(_app(welt["K2"])) as c:
            # fremde Zahlung -> 403
            assert (await c.get(f"/api/payments/status/{sid}")).status_code == 403
            # Neuer Checkout fuer K2, Webhook kommt nicht — der Poll fragt
            # ueber Session.retrieve bei Stripe nach.
            r = await c.post("/api/payments/checkout", json=CHECKOUT)
            assert r.status_code == 200, r.text
            sid2 = r.json()["session_id"]
            welt["sid2"] = sid2
            r = await c.get(f"/api/payments/status/{sid2}")
            assert r.json()["status"] == "initiated" and r.json()["payment_status"] == "unpaid"
            stripe_fake.retrieve_status[sid2] = ("paid", "complete")
            r = await c.get(f"/api/payments/status/{sid2}")
            assert r.status_code == 200, r.text
            assert r.json()["status"] == "active" and r.json()["payment_status"] == "paid"
            # nochmal pollen: stabil, kein zweiter Beleg
            r = await c.get(f"/api/payments/status/{sid2}")
            assert r.json()["status"] == "active"
        u = await mdb.users.find_one({"id": welt["K2"]["id"]})
        assert u["marketplace_access"]["active"] is True
        assert await mdb.manual_payments.count_documents({"zahlung_ref": sid2}) == 1
        welt["ablauf2"] = u["marketplace_access"]["expires_at"]
    _lauf(sz)


# ---------- Freischaltung scheitert -> Alarm -> Abgleich ----------
class _KaputteSammlung:
    def __init__(self, echt):
        self._echt = echt

    def __getattr__(self, n):
        return getattr(self._echt, n)

    async def update_one(self, *a, **k):
        raise RuntimeError("users.update_one kaputt (Testsimulation)")


class _KaputteDb:
    """Wie die echte DB, nur users.update_one wirft — simuliert einen
    DB-Fehler mitten in der Freischaltung (Zahlung ist da, Zugang nicht)."""

    def __init__(self, echt):
        self._echt = echt

    def __getattr__(self, n):
        c = getattr(self._echt, n)
        return _KaputteSammlung(c) if n == "users" else c


def test_07_freischaltung_scheitert_alarm_und_abgleich(env, stripe_fake, welt):
    async def sz(mdb):
        async with _client(_app(welt["K2"])) as c:
            r = await c.post("/api/payments/checkout", json=CHECKOUT)
            sid3 = r.json()["session_id"]
            welt["sid3"] = sid3
        payload = _event(sid3)
        p.db = _KaputteDb(mdb)
        try:
            async with _client(_app()) as c:
                r = await _webhook(c, payload, _signatur(payload))
                assert r.status_code == 200, r.text
        finally:
            p.db = mdb
        tx = await mdb.payment_transactions.find_one({"session_id": sid3}, {"_id": 0})
        assert tx["status"] == "activation_failed" and tx["payment_status"] == "paid"
        assert "kaputt" in tx["activation_error"]
        al = await mdb.betriebsalarme.find_one(
            {"typ": "zahlung_ohne_zugang", "ref": sid3}, {"_id": 0})
        assert al and al["offen"] is True, al
        assert al["details"]["user_id"] == welt["K2"]["id"]
        assert "kaputt" in al["details"]["fehler"]
        assert await mdb.manual_payments.count_documents({"zahlung_ref": sid3}) == 0
        u = await mdb.users.find_one({"id": welt["K2"]["id"]})
        assert u["marketplace_access"]["expires_at"] == welt["ablauf2"]   # unveraendert

        # Abgleich: zu frisch (< 2 min) -> nicht angefasst
        await p.zahlungen_abgleichen(mdb)
        tx = await mdb.payment_transactions.find_one({"session_id": sid3})
        assert tx["status"] == "activation_failed"

        # aelter machen -> Abgleich holt nach
        alt = (datetime.now(timezone.utc) - timedelta(minutes=3)).isoformat()
        await mdb.payment_transactions.update_one({"session_id": sid3},
                                                  {"$set": {"updated_at": alt}})
        st = await p.zahlungen_abgleichen(mdb)
        assert st["aktiviert"] >= 1, st
        tx = await mdb.payment_transactions.find_one({"session_id": sid3}, {"_id": 0})
        assert tx["status"] == "active" and tx["activated_at"]
        assert tx["activation_error"] is None
        assert await mdb.manual_payments.count_documents({"zahlung_ref": sid3}) == 1
        u = await mdb.users.find_one({"id": welt["K2"]["id"]})
        # zweite Zahlung von K2: +30 Tage ab bisherigem Ablauf
        erw = datetime.fromisoformat(welt["ablauf2"]) + timedelta(days=30)
        ist = datetime.fromisoformat(u["marketplace_access"]["expires_at"])
        assert abs((ist - erw).total_seconds()) < 120, (ist, erw)
        # Alarm bleibt zum Quittieren offen — aber es gibt nur einen
        assert await mdb.betriebsalarme.count_documents(
            {"typ": "zahlung_ohne_zugang", "ref": sid3}) == 1
        # zweiter Abgleich: nichts mehr zu tun fuer diese Session
        await p.zahlungen_abgleichen(mdb)
        assert await mdb.manual_payments.count_documents({"zahlung_ref": sid3}) == 1
    _lauf(sz)


def test_08_haengendes_activating_wird_nachgeholt(env, welt):
    async def sz(mdb):
        haengt = f"cs_test_{SUF}_haengt"
        frisch = f"cs_test_{SUF}_frisch"
        jetzt = datetime.now(timezone.utc)
        basis = {"user_id": welt["K1"]["id"], "dealer_id": None,
                 "plan": "marktplatz", "amount": 20.0, "currency": "eur",
                 "payment_status": "paid", "status": "activating"}
        await mdb.payment_transactions.insert_many([
            {**basis, "id": str(uuid.uuid4()), "session_id": haengt,
             "created_at": (jetzt - timedelta(minutes=30)).isoformat(),
             "updated_at": (jetzt - timedelta(minutes=11)).isoformat()},
            {**basis, "id": str(uuid.uuid4()), "session_id": frisch,
             "created_at": jetzt.isoformat(), "updated_at": jetzt.isoformat()},
        ])
        st = await p.zahlungen_abgleichen(mdb)
        assert st["aktiviert"] >= 1 and st["uebersprungen"] >= 1, st
        h = await mdb.payment_transactions.find_one({"session_id": haengt})
        assert h["status"] == "active"
        f = await mdb.payment_transactions.find_one({"session_id": frisch})
        assert f["status"] == "activating"      # laeuft (angeblich) noch
        assert await mdb.manual_payments.count_documents({"zahlung_ref": haengt}) == 1
        assert await mdb.manual_payments.count_documents({"zahlung_ref": frisch}) == 0
        u = await mdb.users.find_one({"id": welt["K1"]["id"]})
        erw = datetime.fromisoformat(welt["ablauf1"]) + timedelta(days=30)
        ist = datetime.fromisoformat(u["marketplace_access"]["expires_at"])
        assert abs((ist - erw).total_seconds()) < 120, (ist, erw)
    _lauf(sz)


# ---------- failed / expired / ignorierte Ereignisse ----------
def test_09_fehlgeschlagen_abgelaufen_und_fremde_ereignisse(env, stripe_fake, welt):
    async def sz(mdb):
        sids = []
        async with _client(_app(welt["K2"])) as c:
            for _ in range(3):
                r = await c.post("/api/payments/checkout", json=CHECKOUT)
                assert r.status_code == 200, r.text
                sids.append(r.json()["session_id"])
        s_fail, s_exp, s_unpaid = sids
        async with _client(_app()) as c:
            pl = _event(s_fail, typ="checkout.session.async_payment_failed",
                        payment_status="unpaid")
            assert (await _webhook(c, pl, _signatur(pl))).status_code == 200
            pl = _event(s_exp, typ="checkout.session.expired",
                        payment_status="unpaid", status="expired")
            assert (await _webhook(c, pl, _signatur(pl))).status_code == 200
            # completed OHNE Zahlungseingang (verzoegerte Zahlart): nicht freischalten
            pl = _event(s_unpaid, payment_status="unpaid", status="complete")
            assert (await _webhook(c, pl, _signatur(pl))).status_code == 200
            # fremder Ereignistyp -> 200, ignoriert
            pl = _event(s_unpaid, typ="payment_intent.created")
            r = await _webhook(c, pl, _signatur(pl))
            assert r.status_code == 200 and r.json()["ok"] is True
            # bezahlte Session, die wir nicht kennen -> 200 + Alarm (Geld ohne Vorgang)
            pl = _event(f"cs_test_{SUF}_unbekannt")
            assert (await _webhook(c, pl, _signatur(pl))).status_code == 200
            # spaetes expired auf eine bereits bezahlte Session setzt nichts zurueck
            pl = _event(welt["sid1"], typ="checkout.session.expired", status="expired")
            assert (await _webhook(c, pl, _signatur(pl))).status_code == 200
        f = await mdb.payment_transactions.find_one({"session_id": s_fail})
        assert f["status"] == "failed" and f["payment_status"] == "failed"
        e = await mdb.payment_transactions.find_one({"session_id": s_exp})
        assert e["status"] == "expired" and e["payment_status"] == "expired"
        u_ = await mdb.payment_transactions.find_one({"session_id": s_unpaid})
        assert u_["status"] == "initiated" and u_["payment_status"] == "unpaid"
        assert (await mdb.payment_transactions.find_one(
            {"session_id": welt["sid1"]}))["status"] == "active"
        assert await mdb.manual_payments.count_documents({"zahlung_ref": {"$in": sids}}) == 0
        al = await mdb.betriebsalarme.find_one(
            {"typ": "zahlung_ohne_zugang", "ref": f"cs_test_{SUF}_unbekannt"})
        assert al and al["offen"] is True
        # Poll auf abgebrochene Zahlung: Endzustand ohne Stripe-Rueckfrage
        async with _client(_app(welt["K2"])) as c:
            r = await c.get(f"/api/payments/status/{s_exp}")
            assert r.status_code == 200 and r.json()["status"] == "expired"
            r = await c.get(f"/api/payments/status/{s_fail}")
            assert r.json()["status"] == "failed"
    _lauf(sz)
