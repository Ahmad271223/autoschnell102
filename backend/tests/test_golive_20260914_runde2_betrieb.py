# -*- coding: utf-8 -*-
"""Pruefung 14.09.2026, zweite Liste — Betreiber, Marktplatz, Zahlung, Indizes.

  M1   Geloeschter/gesperrter Kaeufer gibt reservierte Fahrzeuge frei.
  M2   Inserate zu geloeschten Fahrzeugen werden vom Aufraeum-Job nachgeschlossen.
  M4-6 Fehlende Schutz-Indizes brechen den Produktionsstart ab.
  M7   Zugangs-Anfrage mit bereits angelegtem Konto wird nie ein zweites Mal
       verwendet.
  M8   Konten ohne Firma werden gesperrt und gemeldet.
  M11  Transaktion VOR der Stripe-Session; Webhook findet sie ueber die tx_id.
  M12  Chefwechsel: neuer Chef zuerst, Firma nie ohne Chef.
  M13  Abo-Wechsel: scheitert der Insert, bleibt das alte Abo aktiv.
  M14/M15 plan_type nur monthly/yearly/trial (kein lifetime, kein Freitext).
  M17  Sucher -> Zwischenhaendler loest die Firmenbindung.
  F6/F7 Beweisdokument-Verfall: Claim + erneute Pruefung, keine Loeschung einer
       wiederbelebten Zeile.

In-Prozess ueber deps.db (DB_NAME); routes.admin am Modulanfang importiert.
"""
import asyncio
import os
import sys
import uuid
from datetime import timedelta
from pathlib import Path

import pytest
from fastapi import HTTPException

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))
os.environ.setdefault("MONGO_URL", "mongodb://127.0.0.1:27017")
os.environ.setdefault("DB_NAME", "autoschnell_r2_betrieb")
import routes.admin as ADMIN  # noqa: E402
import routes.payments as PAY  # noqa: E402
from test_beweis_service import _alt_fertig, _jetzt, welt as beweis_welt  # noqa: E402,F401
from test_payments import (API_KEY, ORIGIN, env, fake, stripe_fake,  # noqa: E402,F401
                           welt as pay_welt)

MONGO_URL = os.environ["MONGO_URL"]
DB_NAME = os.environ["DB_NAME"]
SUF = uuid.uuid4().hex[:8]
SA = {"id": f"r2sa_{SUF}", "role": "admin", "is_super_admin": True,
      "active": True, "dealer_id": None, "username": f"r2sa_{SUF}"}


def _db():
    from pymongo import MongoClient
    return MongoClient(MONGO_URL, serverSelectionTimeoutMS=5000)[DB_NAME]


def _run(coro):
    return asyncio.run(coro)


def _fehler(coro):
    with pytest.raises(HTTPException) as e:
        _run(coro)
    return e.value.status_code, e.value.detail


@pytest.fixture(scope="module")
def aufraeumen():
    yield
    dbx = _db()
    muster = {"$regex": SUF}
    for coll in ("users", "dealers", "resale_listings", "listing_interest", "vehicles",
                 "subscriptions", "plan_requests", "betriebsalarme", "activity_logs",
                 "payment_transactions"):
        dbx[coll].delete_many({"$or": [{"id": muster}, {"dealer_id": muster}, {"ref": muster},
                                       {"buyer_user_id": muster}, {"reserved_for": muster},
                                       {"subject_user_id": muster}, {"user_id": muster}]})


def _firma(dbx, name):
    dealer_id, chef_id, sucher_id = f"d_{name}_{SUF}", f"c_{name}_{SUF}", f"s_{name}_{SUF}"
    dbx.dealers.insert_one({"id": dealer_id, "user_id": chef_id, "company_name": name,
                            "kunden_nr": 90000 + abs(hash(name)) % 1000, "created_at": "2026-09-01"})
    dbx.users.insert_many([
        {"id": chef_id, "dealer_id": dealer_id, "role": "dealer", "active": True,
         "kontonummer": f"r2{SUF}{name}c", "created_at": "2026-09-01T00:00:00+00:00"},
        {"id": sucher_id, "dealer_id": dealer_id, "role": "sucher", "active": True,
         "kontonummer": f"r2{SUF}{name}s", "created_at": "2026-09-02T00:00:00+00:00"}])
    return dealer_id, chef_id, sucher_id


# ============================================================ M1
def test_m1_geloeschter_oder_gesperrter_kaeufer_gibt_reservierung_frei(aufraeumen):
    dbx = _db()
    kaeufer = f"k_m1_{SUF}"
    dbx.users.insert_one({"id": kaeufer, "role": "b2b_buyer", "active": True,
                          "kontonummer": f"r2{SUF}k1", "created_at": "2026-09-01"})
    dbx.resale_listings.insert_one({"id": f"l_m1_{SUF}", "dealer_id": f"d_m1_{SUF}",
                                    "status": "reserviert", "reserved_for": kaeufer})
    dbx.listing_interest.insert_one({"id": f"i_m1_{SUF}", "listing_id": f"l_m1_{SUF}",
                                     "buyer_user_id": kaeufer, "status": "akzeptiert"})
    _run(ADMIN.admin_user_set_active(kaeufer, ADMIN.AdminActiveIn(active=False), admin=SA))
    l = dbx.resale_listings.find_one({"id": f"l_m1_{SUF}"})
    assert l["status"] == "veroeffentlicht" and "reserved_for" not in l
    assert dbx.listing_interest.find_one({"id": f"i_m1_{SUF}"})["status"] == "abgelehnt"
    # nochmal reservieren, dann loeschen
    dbx.resale_listings.update_one({"id": f"l_m1_{SUF}"},
                                   {"$set": {"status": "reserviert", "reserved_for": kaeufer}})
    _run(ADMIN.admin_delete_user(kaeufer, admin=SA))
    l = dbx.resale_listings.find_one({"id": f"l_m1_{SUF}"})
    assert l["status"] == "veroeffentlicht" and "reserved_for" not in l


# ============================================================ M2 / M8
def test_m2_m8_aufraeumjob_schliesst_inserate_und_sperrt_konten_ohne_firma(aufraeumen):
    import cleanup_service as CS
    from motor.motor_asyncio import AsyncIOMotorClient
    dbx = _db()
    dealer_id = f"d_m2_{SUF}"
    dbx.vehicles.insert_one({"id": f"v_m2_{SUF}", "dealer_id": dealer_id, "lifecycle": "geloescht"})
    dbx.resale_listings.insert_one({"id": f"l_m2_{SUF}", "dealer_id": dealer_id,
                                    "vehicle_id": f"v_m2_{SUF}", "status": "veroeffentlicht"})
    dbx.users.insert_one({"id": f"u_m8_{SUF}", "dealer_id": f"d_fehlt_{SUF}", "role": "sucher",
                          "active": True, "kontonummer": f"r2{SUF}m8", "created_at": "2026-09-01"})

    async def lauf():
        cl = AsyncIOMotorClient(MONGO_URL, serverSelectionTimeoutMS=5000)
        try:
            db = cl[DB_NAME]
            return (await CS.inserate_geloeschter_fahrzeuge_schliessen(db),
                    await CS.konten_ohne_firma_sperren(db))
        finally:
            cl.close()
    n_inserate, n_konten = _run(lauf())
    assert n_inserate >= 1 and n_konten >= 1
    assert dbx.resale_listings.find_one({"id": f"l_m2_{SUF}"})["status"] == "geloescht"
    u = dbx.users.find_one({"id": f"u_m8_{SUF}"})
    assert u["active"] is False and u["firma_fehlt"] is True
    assert dbx.betriebsalarme.find_one({"typ": "konto_ohne_firma", "ref": f"d_fehlt_{SUF}", "offen": True})


# ============================================================ M4-6
def test_m4_m6_fehlender_schutzindex_bricht_produktionsstart_ab(monkeypatch):
    import indizes
    monkeypatch.setenv("APP_ENV", "production")
    with pytest.raises(SystemExit) as e:
        indizes._in_produktion_abbrechen("Test")
    assert e.value.code == 78
    monkeypatch.setenv("APP_ENV", "development")
    indizes._in_produktion_abbrechen("Test")          # nur Alarm, kein Abbruch


# ============================================================ M7
def test_m7_anfrage_mit_bestehendem_konto_wird_nicht_erneut_verwendet(aufraeumen):
    dbx = _db()
    anfrage_id = f"a_m7_{SUF}"
    dbx.plan_requests.insert_one({"id": anfrage_id, "type": "zugang", "art": "fahrer",
                                  "status": "offen", "created_at": "2026-09-01"})
    dbx.driver_accounts.insert_one({"id": f"f_m7_{SUF}", "zugangsanfrage_id": anfrage_id,
                                    "kontonummer": f"r2{SUF}m7", "active": True})
    try:
        code, text = _fehler(ADMIN._anfrage_reservieren(anfrage_id, "fahrer"))
        assert code == 409 and f"r2{SUF}m7" in text
        a = dbx.plan_requests.find_one({"id": anfrage_id})
        assert a["status"] == "erledigt" and a["angelegt_konto_id"] == f"f_m7_{SUF}"
    finally:
        dbx.driver_accounts.delete_one({"id": f"f_m7_{SUF}"})


# ============================================================ M12 / M13 / M14 / M17
def test_m12_chefwechsel_neuer_chef_zuerst(aufraeumen):
    dbx = _db()
    dealer_id, chef_id, sucher_id = _firma(dbx, "m12")
    r = _run(ADMIN.admin_update_user(sucher_id, body={"role": "dealer", "chef_wechsel": True}, admin=SA))
    assert r["ok"]
    assert dbx.users.find_one({"id": sucher_id})["role"] == "dealer"
    assert dbx.users.find_one({"id": chef_id})["role"] == "sucher"
    assert dbx.dealers.find_one({"id": dealer_id})["user_id"] == sucher_id


def test_m13_m14_abo_wechsel_und_plan_validierung(aufraeumen, monkeypatch):
    dbx = _db()
    dealer_id, chef_id, sucher_id = _firma(dbx, "m13")
    alt = {"id": f"sub_alt_{SUF}", "dealer_id": dealer_id, "subject_user_id": sucher_id,
           "plan": "monthly", "status": "active", "expires_at": "2099-01-01T00:00:00+00:00",
           "created_at": "2026-09-01"}
    dbx.subscriptions.insert_one(alt)
    # M14/M15: lifetime und Freitext gehen nicht
    for plan in ("lifetime", "gratis"):
        code, _ = _fehler(ADMIN.admin_update_user(sucher_id, body={"plan_type": plan}, admin=SA))
        assert code == 400, plan
    assert dbx.subscriptions.find_one({"id": alt["id"]})["status"] == "active"
    # M13: Insert scheitert -> altes Abo bleibt aktiv
    echt = ADMIN.db

    class _Subs:
        def __getattr__(self, n):
            return getattr(echt.subscriptions, n)

        async def insert_one(self, *a, **k):
            raise RuntimeError("Mongo weg")

    class _DB:
        def __getattr__(self, n):
            return _Subs() if n == "subscriptions" else getattr(echt, n)

        def __getitem__(self, n):
            return self.__getattr__(n)
    monkeypatch.setattr(ADMIN, "db", _DB())
    code, text = _fehler(ADMIN.admin_update_user(sucher_id, body={"plan_type": "yearly"}, admin=SA))
    monkeypatch.setattr(ADMIN, "db", echt)
    assert code == 500 and "bisherige" in text
    a = dbx.subscriptions.find_one({"id": alt["id"]})
    assert a["status"] == "active" and "ersetzt_durch" not in a
    # und mit funktionierender DB: neues Abo aktiv, altes ersetzt
    r = _run(ADMIN.admin_update_user(sucher_id, body={"plan_type": "yearly"}, admin=SA))
    assert r["ok"]
    assert dbx.subscriptions.find_one({"id": alt["id"]})["status"] == "ersetzt"
    neu = dbx.subscriptions.find_one({"subject_user_id": sucher_id, "status": "active"})
    assert neu["plan"] == "yearly" and neu["expires_at"]


def test_m17_sucher_wird_zwischenhaendler_ohne_firmenbindung(aufraeumen):
    dbx = _db()
    dealer_id, chef_id, sucher_id = _firma(dbx, "m17")
    dbx.vehicles.insert_one({"id": f"v_m17_{SUF}", "dealer_id": dealer_id,
                             "owner_user_id": sucher_id, "mitbearbeiter_ids": [sucher_id],
                             "lifecycle": "bestand"})
    r = _run(ADMIN.admin_update_user(sucher_id, body={"role": "b2b_buyer"}, admin=SA))
    assert r["ok"]
    u = dbx.users.find_one({"id": sucher_id})
    assert u["role"] == "b2b_buyer" and u["dealer_id"] is None and u["ehemalige_dealer_id"] == dealer_id
    v = dbx.vehicles.find_one({"id": f"v_m17_{SUF}"})
    assert v["owner_user_id"] == chef_id and sucher_id not in v["mitbearbeiter_ids"]


# ============================================================ M11
def test_m11_transaktion_vor_stripe_session_und_webhook_fallback(env, stripe_fake, pay_welt,
                                                                  monkeypatch):
    import stripe
    dbx = _db()
    k = pay_welt["K1"]
    # Stripe faellt aus -> Transaktion bleibt als "failed" nachvollziehbar
    def _kaputt(**params):
        raise RuntimeError("Stripe weg")
    monkeypatch.setattr(stripe.checkout.Session, "create", staticmethod(_kaputt))
    code, _ = _fehler(PAY.create_checkout(PAY.CheckoutIn(plan="marktplatz", origin_url=ORIGIN), user=k))
    assert code == 502
    tx = dbx.payment_transactions.find_one({"user_id": k["id"], "status": "failed"})
    assert tx and tx["session_id"] is None and tx["fehler"] == "stripe_session"
    # Stripe geht: Transaktion existiert schon VOR der Session, session_id nachgetragen
    monkeypatch.setattr(stripe.checkout.Session, "create", staticmethod(stripe_fake.create))
    r = _run(PAY.create_checkout(PAY.CheckoutIn(plan="marktplatz", origin_url=ORIGIN), user=k))
    tx = dbx.payment_transactions.find_one({"session_id": r["session_id"]})
    assert tx and tx["status"] == "initiated"
    # Webhook-Fallback: session_id ging verloren, Stripe nennt unsere tx_id
    dbx.payment_transactions.update_one({"id": tx["id"]}, {"$set": {"session_id": None}})
    _run(PAY._zahlung_bestaetigt(r["session_id"], {"id": r["session_id"], "payment_status": "paid",
                                                   "client_reference_id": tx["id"],
                                                   "metadata": {"tx_id": tx["id"]}}))
    tx2 = dbx.payment_transactions.find_one({"id": tx["id"]})
    assert tx2["session_id"] == r["session_id"] and tx2["status"] in ("paid", "activating", "active")
    dbx.payment_transactions.delete_many({"user_id": k["id"]})


# ============================================================ F6 / F7
def test_f6_f7_verfall_prueft_nach_dem_claim_erneut_und_schont_wiederbelebte(beweis_welt, monkeypatch):
    import beweis_service as BS
    w = beweis_welt
    _alt_fertig(w, "race")
    _alt_fertig(w, "lebt")
    # "lebt" wurde gerade wiederbelebt (offen) -> darf nicht geloescht werden
    w.run(w.db.inserat_beweise.update_one({"cache_key": w.ck("lebt")}, {"$set": {"status": "offen"}}))
    echt = BS._gehalten
    aufrufe = {"n": 0}

    async def _erst_frei_dann_gehalten(db, ck):
        if ck == w.ck("race"):
            aufrufe["n"] += 1
            return aufrufe["n"] >= 2        # 1. Pruefung frei, 2. (nach dem Claim) gehalten
        return await echt(db, ck)
    monkeypatch.setattr(BS, "_gehalten", _erst_frei_dann_gehalten)
    w.run(BS.beweise_verfallen(w.db, altbestand_filter={"dealer_id": w.dealer_id}))
    race = w.run(w.db.inserat_beweise.find_one({"cache_key": w.ck("race")}))
    assert race["status"] == "fertig" and race["verfall_pruefen_ab"], race
    assert w.run(w.db.inserat_beweise.find_one({"cache_key": w.ck("lebt")}))["status"] == "offen"
