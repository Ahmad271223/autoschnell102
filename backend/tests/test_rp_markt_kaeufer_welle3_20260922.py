# -*- coding: utf-8 -*-
"""Rollenprüfung 22.09.2026, Welle 3 — Team markt_kaeufer (routes/marketplace.py).

  RP-519        laeuft_ab_am je Anfrage (Haendler- und Kaeuferliste) und im
                Marktplatz (Liste/Detail, Haendlerseite) — dasselbe Datum wie
                routes.resale._laufzeit_bis (nach einer Freigabe ab
                wieder_veroeffentlicht_am, RP-517), None ausserhalb "veroeffentlicht"
  RP-098 Nr. 2  "Zugang sperren" beendet die laufende Anfrage (400 beim
                Annehmen); die Kaeuferpruefung bleibt als zweite Linie (409)
                — In-Prozess-Gegenstueck zu test_befunde_runde14_marktplatz::
                test_h_1 (HTTP)

In-Prozess gegen eine Wegwerf-Datenbank (autoschnell_rpmk3_<uuid>), kein Server.
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

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

MONGO_URL = os.environ.get("MONGO_URL") or "mongodb://127.0.0.1:27017"
MODULE = ("deps", "routes.marketplace", "routes.resale", "routes.bestand",
          "routes.team", "routes.admin", "kaufvorgang", "lifecycle")
SA = {"id": "rpmk3_sa", "role": "admin", "is_super_admin": True,
      "username": "rpmk3-sa", "dealer_id": ""}


def _jetzt(**delta):
    return (datetime.now(timezone.utc) + timedelta(**delta)).isoformat()


def _mod(name):
    return importlib.import_module(name)


class _Welt:
    def __init__(self, db, s):
        self.db, self.s = db, s
        self.did = f"d_rpmk3_{s}"
        self.chef = {"id": f"chef_rpmk3_{s}", "dealer_id": self.did, "role": "dealer"}
        self.k = {"id": f"k_rpmk3_{s}", "dealer_id": None, "role": "b2b_buyer",
                  "company_name": "Kaeufer W3 GmbH", "email": f"w3_{s}@e2etest-mail.de",
                  "active": True}

    async def anlegen(self):
        await self.db.dealers.insert_one({
            "id": self.did, "user_id": self.chef["id"], "company_name": f"RP3 Autohaus {self.s}",
            "city": "Hannover", "phone": "0511 123",
            "marketplace": {"public": True, "slug": f"rp3-{self.s}"}, "created_at": _jetzt()})
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

    async def anfrage(self, lid, status="offen", offer=20000.0):
        iid = f"i_{self.s}_{uuid.uuid4().hex[:8]}"
        await self.db.listing_interest.insert_one({
            "id": iid, "listing_id": lid, "dealer_id": self.did, "listing_title": "alt",
            "buyer_user_id": self.k["id"], "buyer_name": self.k["company_name"],
            "offer": offer, "message": "", "status": status, "counter_offer": None,
            "history": [{"von": "kaeufer", "aktion": "interesse", "angebot": offer,
                         "zeit": _jetzt()}],
            "created_at": _jetzt(), "updated_at": _jetzt()})
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
    name = f"autoschnell_rpmk3_{s}"
    db = client[name]
    for m in mods:
        if "db" in m.__dict__:
            m.db = db
    w = _Welt(db, s)
    w.run = loop.run_until_complete
    try:
        w.run(w.anlegen())
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


async def _listings(M, user, **k):
    args = dict(q=None, make=None, model=None, fuel=None, price_min=None, price_max=None,
                km_min=None, km_max=None, ps_min=None, ps_max=None, sort=None, dealer=None,
                nur_favoriten=0, page=1, limit=300)
    args.update(k)
    return await M.browse_listings(user=user, response=Response(), **args)


# ======================================================= RP-519
def test_rp519_anfragen_liefern_laeuft_ab_am(welt):
    M, R = _mod("routes.marketplace"), _mod("routes.resale")
    w = welt
    pub = _jetzt(days=-5)
    lid = w.run(w.inserat(published_at=pub))
    lid_res = w.run(w.inserat(status="reserviert", published_at=pub, reserved_for="jemand"))
    lid_nie = w.run(w.inserat(published_at=None))
    iid = w.run(w.anfrage(lid))
    iid_res = w.run(w.anfrage(lid_res))
    iid_nie = w.run(w.anfrage(lid_nie))
    erwartet = R._laufzeit_bis({"published_at": pub})
    assert erwartet and erwartet > pub
    haendler = w.run(M.dealer_list_interests(Response(), user=w.chef))
    kaeufer = w.run(M.buyer_interests(Response(), user=w.k))
    for liste in (haendler, kaeufer):
        je = {i["id"]: i for i in liste}
        assert je[iid]["laeuft_ab_am"] == erwartet
        # reserviert (auch fuer jemand anderen) oder nie veroeffentlicht: kein Datum
        assert je[iid_res]["laeuft_ab_am"] is None
        assert je[iid_res]["anderweitig_reserviert"] is True
        assert je[iid_nie]["laeuft_ab_am"] is None
    # Gefilterte Haendlerliste (je Inserat) geht denselben Weg
    je_inserat = w.run(M.dealer_list_interests(Response(), listing_id=lid, user=w.chef))
    assert [i["laeuft_ab_am"] for i in je_inserat] == [erwartet]
    # Nach einer Freigabe durch den Betreiber zaehlt der spaetere Start
    # (RP-517, wie Aufraeumlauf und Inserat-Editor) — die Projektion traegt
    # wieder_veroeffentlicht_am mit.
    frei = _jetzt(days=-1)
    w.run(w.db.resale_listings.update_one({"id": lid},
                                          {"$set": {"wieder_veroeffentlicht_am": frei}}))
    neu = R._laufzeit_bis({"published_at": pub, "wieder_veroeffentlicht_am": frei})
    assert neu > erwartet
    je = {i["id"]: i for i in w.run(M.buyer_interests(Response(), user=w.k))}
    assert je[iid]["laeuft_ab_am"] == neu


def test_rp519_marktplatz_liefert_laeuft_ab_am(welt):
    M, R = _mod("routes.marketplace"), _mod("routes.resale")
    w = welt
    pub = _jetzt(days=-2)
    lid = w.run(w.inserat(published_at=pub))
    lid_zu = w.run(w.inserat(status="zurueckgezogen", published_at=pub))
    erwartet = R._laufzeit_bis({"published_at": pub})
    # Marktplatz-Liste (daraus oeffnet die Oberflaeche das Detail)
    liste = {v["id"]: v for v in w.run(_listings(M, w.k))}
    assert liste[lid]["laeuft_ab_am"] == erwartet
    assert lid_zu not in liste
    # Haendlerseite
    seite = w.run(M.dealer_page(f"rp3-{w.s}", w.k))
    assert [v["laeuft_ab_am"] for v in seite["listings"]] == [erwartet]
    # Oeffentliche Sicht direkt: nur "veroeffentlicht" traegt ein Datum
    assert M._public_listing_view(_l(w, lid_zu), is_member=False,
                                  is_trade=True)["laeuft_ab_am"] is None
    assert M._laeuft_ab_am({"status": "veroeffentlicht"}) is None
    assert M._laeuft_ab_am({"status": "veroeffentlicht", "published_at": "kaputt"}) is None
    assert M._laeuft_ab_am(None) is None


# ======================================================= RP-098 Nr. 2 (h_1 in-Prozess)
def test_rp098_2_zugang_sperren_beendet_anfrage_kaeuferpruefung_bleibt(welt):
    M, A = _mod("routes.marketplace"), _mod("routes.admin")
    w = welt
    lid = w.run(w.inserat())
    iid = w.run(w.anfrage(lid))
    erg = w.run(A.admin_set_buyer_access(w.k["id"], {"plan": None}, admin=SA))
    assert erg["gesperrt"] is True
    it = _i(w, iid)
    assert it["status"] == "abgelehnt" and it["beendet_grund"] == "kaeufer_gesperrt"
    assert it["history"][-1]["von"] == "system"
    assert it["history"][-1]["aktion"] == "kaeufer_gesperrt"
    # Die Anfrage ist zu — der Haendler bekommt 400, reserviert wird nichts.
    code, text = _code(w, M.answer_interest(iid, M.InterestAnswerIn(action="akzeptieren"),
                                            user=w.chef))
    assert code == 400 and "abgeschlossen" in text
    assert _l(w, lid)["status"] == "veroeffentlicht" and not _l(w, lid).get("reserved_for")
    # Zweite Linie: eine Anfrage, die das Beenden verpasst hat (Rennen mit der
    # Sperre), weist die Kaeuferpruefung ab — Annehmen/Gegenangebot 409.
    w.run(w.db.listing_interest.update_one({"id": iid}, {"$set": {"status": "offen"},
                                                         "$unset": {"beendet_grund": ""}}))
    code, text = _code(w, M.answer_interest(iid, M.InterestAnswerIn(action="akzeptieren"),
                                            user=w.chef))
    assert code == 409 and "nicht mehr aktiv" in text
    code, _ = _code(w, M.answer_interest(
        iid, M.InterestAnswerIn(action="gegenangebot", counter_offer=21000), user=w.chef))
    assert code == 409
    assert _l(w, lid)["status"] == "veroeffentlicht" and not _l(w, lid).get("reserved_for")
    # Ablehnen geht trotzdem (schliesst nur ab)
    assert _code(w, M.answer_interest(iid, M.InterestAnswerIn(action="ablehnen"),
                                      user=w.chef))[0] == 200
    assert _i(w, iid)["status"] == "abgelehnt"
