# -*- coding: utf-8 -*-
"""Rollenprüfung 22.09.2026 — Team markt_kaeufer (routes/marketplace.py).

  RP-089/188/339  Netzwerk-Widerruf beendet die Anfragen des Kaeufers
  RP-541          Mitglied ueber anderen Link verbraucht keine Nutzung
  RP-477          vereinbarter Preis in der Historie
  RP-491          Haendler-Antwort nur fuer den gesehenen Stand (409)
  RP-495          Kaeufer-Annahme nur fuer das gesehene Gegenangebot (409)
  RP-502          Kaeufer zieht zurueck / aendert sein Angebot
  RP-510          Beenden ohne aktiven Zugang, annehmen weiter mit 402
  RP-478          anderweitig reserviert: Hinweisfelder in beiden Listen
  RP-503          Kaeuferliste mit Haendler, Kontakt (bei Annahme) und Inserat
  RP-520          Zaehler "am Zug" / "neu"
  RP-521          niedrigster zulaessiger Preis (Anzeige = Filter = Sortierung)
  RP-504/505/507  oeffentliche Sicht: keine Portal-Unfall-Zusicherung, kein
                  Privatverkaeufer-Titel, leeres km nicht als 0
  RP-507/524      Markenfilter mit Wortgrenzen und Akzenten
  RP-528/529      Modellfilter tolerant und mit Wortgrenzen
  RP-506          Kraftstoff-Filter ueber Codes (Hybrid, Gas)
  RP-512          km/PS-Filter deutsch gelesen
  RP-530          ungueltiges Token -> 401 statt still anonym

In-Prozess gegen eine Wegwerf-Datenbank (autoschnell_rpmk_<uuid>), kein Server.
"""
import asyncio
import importlib
import os
import re
import sys
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from fastapi import HTTPException, Response
from fastapi.security import HTTPAuthorizationCredentials

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

MONGO_URL = os.environ.get("MONGO_URL") or "mongodb://127.0.0.1:27017"
MODULE = ("deps", "routes.marketplace", "routes.resale", "routes.bestand",
          "kaufvorgang", "lifecycle")


def _jetzt(**delta):
    return (datetime.now(timezone.utc) + timedelta(**delta)).isoformat()


def _mod(name):
    return importlib.import_module(name)


class _Welt:
    def __init__(self, db, s):
        self.db, self.s = db, s
        self.did = f"d_rpmk_{s}"
        self.chef = {"id": f"chef_rpmk_{s}", "dealer_id": self.did, "role": "dealer"}
        self.k = {"id": f"k_rpmk_{s}", "dealer_id": None, "role": "b2b_buyer",
                  "company_name": "Kaeufer A GmbH", "email": f"a_{s}@e2etest-mail.de",
                  "active": True}
        self.k2 = {"id": f"k2_rpmk_{s}", "dealer_id": None, "role": "b2b_buyer",
                   "company_name": "Kaeufer B GmbH", "email": f"b_{s}@e2etest-mail.de",
                   "active": True}

    async def anlegen(self, public=True):
        await self.db.dealers.insert_one({
            "id": self.did, "user_id": self.chef["id"], "company_name": f"RP Autohaus {self.s}",
            "city": "Hannover", "phone": "0511 123", "email": "haendler@rp.test",
            "contact_person": "Herr Chef",
            "marketplace": {"public": public, "slug": f"rp-{self.s}"}, "created_at": _jetzt()})
        await self.db.users.insert_many([
            {**self.chef, "active": True, "created_at": _jetzt()},
            {**self.k, "created_at": _jetzt()},
            {**self.k2, "created_at": _jetzt()}])

    async def inserat(self, sichtbarkeit="public", status="veroeffentlicht", **extra):
        lid = f"l_{self.s}_{uuid.uuid4().hex[:8]}"
        doc = {"id": lid, "dealer_id": self.did, "title": "Haendlertitel Golf",
               "status": status, "visibility": sichtbarkeit,
               "prices": {"public": 9900}, "photos": {"mode": "neu", "uploaded_keys": []},
               "data": {"make_label": "VW", "model_label": "Golf", "mileage": 1000},
               "published_at": _jetzt(), "created_at": _jetzt(), "updated_at": _jetzt()}
        doc.update(extra)
        await self.db.resale_listings.insert_one(doc)
        return lid

    async def anfrage(self, lid, kaeufer=None, status="offen", **extra):
        k = kaeufer or self.k
        iid = f"i_{self.s}_{uuid.uuid4().hex[:8]}"
        await self.db.listing_interest.insert_one({
            "id": iid, "listing_id": lid, "dealer_id": self.did, "listing_title": "alt",
            "buyer_user_id": k["id"], "buyer_name": k["company_name"],
            "offer": 20000.0, "message": "", "status": status, "counter_offer": None,
            "history": [{"von": "kaeufer", "aktion": "interesse", "angebot": 20000.0,
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
    name = f"autoschnell_rpmk_{s}"
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


# ======================================================= RP-089/188/339
def test_rp089_netzwerk_widerruf_beendet_anfragen(welt):
    M = _mod("routes.marketplace")
    w = welt
    w.run(w.db.network_members.insert_one({"dealer_id": w.did, "buyer_user_id": w.k["id"],
                                           "created_at": _jetzt()}))
    reserviert = w.run(w.inserat(status="reserviert", reserved_for=w.k["id"]))
    privat = w.run(w.inserat("private"))
    oeffentlich = w.run(w.inserat())
    verkauft = w.run(w.inserat(status="verkauft", sold_to_user_id=w.k["id"]))
    i_res = w.run(w.anfrage(reserviert, status="akzeptiert", agreed_price=19000.0))
    i_priv = w.run(w.anfrage(privat, status="gegenangebot", counter_offer=21000.0))
    i_pub = w.run(w.anfrage(oeffentlich))
    i_verk = w.run(w.anfrage(verkauft, status="akzeptiert"))
    erg = w.run(M.remove_network_member(w.k["id"], user=w.chef))
    assert erg["reservierungen_freigegeben"] == [reserviert]
    assert _l(w, reserviert)["status"] == "veroeffentlicht"
    for iid in (i_res, i_priv):
        it = _i(w, iid)
        assert it["status"] == "abgelehnt" and it["beendet_grund"] == "netzwerk_entfernt", it
        assert it["history"][-1]["von"] == "system"
        assert it["history"][-1]["aktion"] == "netzwerk_entfernt"
    assert _i(w, i_pub)["status"] == "offen", "oeffentliches Inserat sieht er weiter"
    assert _i(w, i_verk)["status"] == "akzeptiert", "an ihn verkauft bleibt der Abschluss"
    # Ein zweiter Kaeufer kann jetzt angenommen werden — und es gibt nur EIN akzeptiert
    i_b = w.run(w.anfrage(reserviert, kaeufer=w.k2))
    code, _ = _code(w, M.answer_interest(i_b, M.InterestAnswerIn(action="akzeptieren"),
                                         user=w.chef))
    assert code == 200
    akzeptiert = w.run(w.db.listing_interest.count_documents(
        {"listing_id": reserviert, "status": "akzeptiert"}))
    assert akzeptiert == 1
    log = w.run(w.db.activity_logs.find_one({"action": "netzwerk.mitglied.entfernt",
                                             "ref": w.k["id"]}))
    assert log["meta"]["anfragen_beendet"] == 2


def test_rp089_nicht_oeffentliche_firma_beendet_alle_laufenden(welt):
    M = _mod("routes.marketplace")
    w = welt
    w.run(w.db.dealers.update_one({"id": w.did}, {"$set": {"marketplace.public": False}}))
    w.run(w.db.network_members.insert_one({"dealer_id": w.did, "buyer_user_id": w.k["id"],
                                           "created_at": _jetzt()}))
    lid = w.run(w.inserat())
    iid = w.run(w.anfrage(lid, status="gegenangebot_kaeufer", buyer_counter_offer=18000.0))
    w.run(M.remove_network_member(w.k["id"], user=w.chef))
    assert _i(w, iid)["status"] == "abgelehnt"


# ======================================================= RP-541
def test_rp541_mitglied_ueber_anderen_link_verbraucht_nichts(welt):
    M = _mod("routes.marketplace")
    w = welt
    w.run(w.db.network_members.insert_one({"dealer_id": w.did, "buyer_user_id": w.k["id"],
                                           "via_invite_id": "alt", "created_at": _jetzt()}))
    inv = {"id": str(uuid.uuid4()), "dealer_id": w.did, "token": f"tok_{w.s}",
           "expires_at": _jetzt(hours=24), "max_uses": 5, "used_count": 0, "used_by": [],
           "created_at": _jetzt()}
    w.run(w.db.dealer_invites.insert_one(dict(inv)))
    assert w.run(M._redeem_invite(inv["token"], w.k["id"])) == w.did
    d = w.run(w.db.dealer_invites.find_one({"id": inv["id"]}))
    assert d["used_count"] == 0 and d["used_by"] == []
    # Altbestand ohne via_invite_id: ebenso
    w.run(w.db.network_members.update_one({"buyer_user_id": w.k["id"]},
                                          {"$unset": {"via_invite_id": ""}}))
    assert w.run(M._redeem_invite(inv["token"], w.k["id"])) == w.did
    assert w.run(w.db.dealer_invites.find_one({"id": inv["id"]}))["used_count"] == 0
    # Neuer Kaeufer ohne Mitgliedschaft verbraucht weiterhin regulaer
    assert w.run(M._redeem_invite(inv["token"], w.k2["id"])) == w.did
    assert w.run(w.db.dealer_invites.find_one({"id": inv["id"]}))["used_count"] == 1


# ======================================================= RP-477 / RP-491
def test_rp477_vereinbarter_preis_in_der_historie(welt):
    M = _mod("routes.marketplace")
    w = welt
    lid = w.run(w.inserat())
    iid = w.run(w.anfrage(lid, status="gegenangebot_kaeufer", buyer_counter_offer=18500.0))
    code, _ = _code(w, M.answer_interest(iid, M.InterestAnswerIn(action="akzeptieren"),
                                         user=w.chef))
    assert code == 200
    it = _i(w, iid)
    assert it["agreed_price"] == 18500.0
    assert it["history"][-1]["aktion"] == "akzeptieren"
    assert it["history"][-1]["angebot"] == 18500.0


def test_rp491_veralteter_stand_reserviert_nicht(welt):
    M = _mod("routes.marketplace")
    w = welt
    lid = w.run(w.inserat())
    # Haendler sah "offen, 20.000 €" — der Kaeufer bot inzwischen 18.000 €
    iid = w.run(w.anfrage(lid, status="gegenangebot_kaeufer", buyer_counter_offer=18000.0))
    body = M.InterestAnswerIn(action="akzeptieren", erwarteter_status="offen",
                              erwarteter_betrag=20000.0)
    code, text = _code(w, M.answer_interest(iid, body, user=w.chef))
    assert code == 409 and "18.000 €" in text, text
    assert _l(w, lid)["status"] == "veroeffentlicht", "nichts reserviert"
    assert _i(w, iid)["status"] == "gegenangebot_kaeufer"
    # gleicher Status, anderer Betrag -> ebenfalls 409
    body = M.InterestAnswerIn(action="akzeptieren", erwarteter_status="gegenangebot_kaeufer",
                              erwarteter_betrag=17000.0)
    assert _code(w, M.answer_interest(iid, body, user=w.chef))[0] == 409
    # passender Stand -> reserviert zu genau diesem Preis
    body = M.InterestAnswerIn(action="akzeptieren", erwarteter_status="gegenangebot_kaeufer",
                              erwarteter_betrag=18000.0)
    assert _code(w, M.answer_interest(iid, body, user=w.chef))[0] == 200
    assert _i(w, iid)["agreed_price"] == 18000.0
    assert _l(w, lid)["status"] == "reserviert"


def test_rp491_ohne_preisangebot_und_ohne_angaben(welt):
    M = _mod("routes.marketplace")
    w = welt
    lid = w.run(w.inserat())
    iid = w.run(w.anfrage(lid, offer=None))
    body = M.InterestAnswerIn(action="akzeptieren", erwarteter_status="offen",
                              erwarteter_betrag=None)
    assert "erwarteter_betrag" in body.model_fields_set
    assert _code(w, M.answer_interest(iid, body, user=w.chef))[0] == 200
    # alte Oberflaeche ohne Stand: wie bisher
    lid2 = w.run(w.inserat())
    iid2 = w.run(w.anfrage(lid2))
    assert _code(w, M.answer_interest(iid2, M.InterestAnswerIn(action="ablehnen"),
                                      user=w.chef))[0] == 200


def test_rp491_betrag_im_schreibfilter_festgenagelt(welt):
    """Aendert der Kaeufer sein Angebot zwischen Lesen und Schreiben des
    Haendlers, gewinnt die Annahme nicht still den neuen Betrag."""
    M = _mod("routes.marketplace")
    w = welt
    lid = w.run(w.inserat())
    iid = w.run(w.anfrage(lid, status="gegenangebot_kaeufer", buyer_counter_offer=18000.0))
    echt = M._kaeufer_darf_noch

    async def dazwischen(it):
        await echt(it)
        await w.db.listing_interest.update_one({"id": iid},
                                               {"$set": {"buyer_counter_offer": 15000.0}})
    M._kaeufer_darf_noch = dazwischen
    try:
        code, _ = _code(w, M.answer_interest(iid, M.InterestAnswerIn(action="akzeptieren"),
                                             user=w.chef))
    finally:
        M._kaeufer_darf_noch = echt
    assert code == 409
    assert _l(w, lid)["status"] == "veroeffentlicht", "Reservierung zurueckgenommen"
    assert _i(w, iid)["status"] == "gegenangebot_kaeufer"


# ======================================================= RP-495 / RP-502 / RP-510
def test_rp495_annahme_nur_fuer_gesehenes_gegenangebot(welt):
    M = _mod("routes.marketplace")
    w = welt
    lid = w.run(w.inserat())
    iid = w.run(w.anfrage(lid, status="gegenangebot", counter_offer=21000.0))
    body = M.BuyerInterestAnswerIn(action="annehmen", erwarteter_betrag=20500.0)
    code, text = _code(w, M.buyer_answer_interest(iid, body, user=w.k))
    assert code == 409 and "21.000 €" in text, text
    assert _l(w, lid)["status"] == "veroeffentlicht"
    body = M.BuyerInterestAnswerIn(action="annehmen", erwarteter_betrag=21000.0)
    assert _code(w, M.buyer_answer_interest(iid, body, user=w.k))[0] == 200
    assert _i(w, iid)["agreed_price"] == 21000.0
    assert _l(w, lid)["reserved_for"] == w.k["id"]


def test_rp502_zurueckziehen_und_angebot_aendern(welt):
    M = _mod("routes.marketplace")
    w = welt
    lid = w.run(w.inserat())
    iid = w.run(w.anfrage(lid))
    # Angebot aendern aus "offen" -> gegenangebot_kaeufer, dann erneut aendern
    for betrag in (19000.0, 18800.0):
        body = M.BuyerInterestAnswerIn(action="gegenangebot", counter_offer=betrag)
        assert _code(w, M.buyer_answer_interest(iid, body, user=w.k))[0] == 200
        it = _i(w, iid)
        assert it["status"] == "gegenangebot_kaeufer" and it["buyer_counter_offer"] == betrag
    # zurueckziehen
    body = M.BuyerInterestAnswerIn(action="zurueckziehen", message="Doch nicht")
    code, erg = _code(w, M.buyer_answer_interest(iid, body, user=w.k))
    assert code == 200 and erg["status"] == "abgelehnt"
    it = _i(w, iid)
    assert it["status"] == "abgelehnt" and it["beendet_grund"] == "kaeufer_zurueckgezogen"
    assert it["history"][-1]["von"] == "kaeufer" and it["history"][-1]["aktion"] == "zurueckgezogen"
    # abgeschlossene Anfrage: nicht noch einmal
    assert _code(w, M.buyer_answer_interest(iid, body, user=w.k))[0] == 400
    # ein neues Senden ist danach wieder moeglich (keine laufende mehr)
    code, _ = _code(w, M.send_interest(lid, M.InterestIn(offer=17000), user=w.k))
    assert code == 200


def test_rp502_akzeptierte_nicht_zurueckziehbar(welt):
    M = _mod("routes.marketplace")
    w = welt
    lid = w.run(w.inserat(status="reserviert", reserved_for=w.k["id"]))
    iid = w.run(w.anfrage(lid, status="akzeptiert"))
    body = M.BuyerInterestAnswerIn(action="zurueckziehen")
    assert _code(w, M.buyer_answer_interest(iid, body, user=w.k))[0] == 400
    assert _i(w, iid)["status"] == "akzeptiert"


def test_rp510_beenden_ohne_zugang_annehmen_nicht(welt, monkeypatch):
    M = _mod("routes.marketplace")
    w = welt
    monkeypatch.setattr(M, "MARKTPLATZ_KOSTENLOS", False)
    abgelaufen = {**w.k, "marketplace_access": {"active": True, "plan": "monat",
                                                "expires_at": _jetzt(days=-1)}}
    lid = w.run(w.inserat())
    i1 = w.run(w.anfrage(lid, status="gegenangebot", counter_offer=21000.0))
    code, _ = _code(w, M.buyer_answer_interest(
        i1, M.BuyerInterestAnswerIn(action="annehmen"), user=abgelaufen))
    assert code == 402
    code, _ = _code(w, M.buyer_answer_interest(
        i1, M.BuyerInterestAnswerIn(action="gegenangebot", counter_offer=1), user=abgelaufen))
    assert code == 402
    code, _ = _code(w, M.buyer_answer_interest(
        i1, M.BuyerInterestAnswerIn(action="ablehnen"), user=abgelaufen))
    assert code == 200 and _i(w, i1)["status"] == "abgelehnt"
    lid2 = w.run(w.inserat())
    i2 = w.run(w.anfrage(lid2))
    code, _ = _code(w, M.buyer_answer_interest(
        i2, M.BuyerInterestAnswerIn(action="zurueckziehen"), user=abgelaufen))
    assert code == 200
    # Betreiber-Sperre bleibt an der Route (Abhaengigkeit)
    import inspect
    sig = inspect.signature(M.buyer_answer_interest)
    assert sig.parameters["user"].default.dependency is M.buyer_nicht_gesperrt


def test_rp510_beenden_auch_ohne_sicht_auf_das_inserat(welt):
    M = _mod("routes.marketplace")
    w = welt
    lid = w.run(w.inserat("private"))          # kein Netzwerk -> nicht sichtbar
    iid = w.run(w.anfrage(lid, status="gegenangebot", counter_offer=21000.0))
    assert _code(w, M.buyer_answer_interest(
        iid, M.BuyerInterestAnswerIn(action="annehmen"), user=w.k))[0] == 403
    assert _code(w, M.buyer_answer_interest(
        iid, M.BuyerInterestAnswerIn(action="ablehnen"), user=w.k))[0] == 200


# ======================================================= RP-478 / RP-503 / RP-520
def test_rp478_503_listen_zeigen_reservierung_haendler_und_inserat(welt):
    M = _mod("routes.marketplace")
    w = welt
    lid = w.run(w.inserat(photos={"mode": "neu", "uploaded_keys": ["resale/x/bild1.jpg"]}))
    i_b = w.run(w.anfrage(lid, kaeufer=w.k2))
    i_a = w.run(w.anfrage(lid))
    assert _code(w, M.answer_interest(i_a, M.InterestAnswerIn(action="akzeptieren"),
                                      user=w.chef))[0] == 200
    haendler = w.run(M.dealer_list_interests(Response(), status=None, listing_id=None,
                                             user=w.chef))
    zeile = {i["id"]: i for i in haendler}
    assert zeile[i_b]["anderweitig_reserviert"] is True
    assert zeile[i_b]["inserat_status"] == "reserviert"
    assert zeile[i_a]["anderweitig_reserviert"] is False
    # Kaeufer B sieht dasselbe, ohne Kontakt (nicht angenommen), mit Firma
    b = w.run(M.buyer_interests(Response(), user=w.k2))
    assert b[0]["anderweitig_reserviert"] is True
    assert b[0]["haendler"]["company_name"].startswith("RP Autohaus")
    assert "kontakt" not in b[0]["haendler"]
    assert "inserat" not in b[0], "reserviert fuer A: fuer B nicht mehr sichtbar"
    # Kaeufer A: Kontakt und Inseratsstand (reserviert fuer ihn)
    a = w.run(M.buyer_interests(Response(), user=w.k))
    assert a[0]["haendler"]["kontakt"]["phone"] == "0511 123"
    assert a[0]["haendler"]["kontakt"]["email"] == "haendler@rp.test"
    assert a[0]["inserat"]["title"] == "Haendlertitel Golf"
    assert a[0]["inserat"]["foto"].startswith("/api/files/resale/x/bild1.jpg?exp=")
    # Reservierung aufgehoben -> B ist wieder frei (abgeleitet, kein Status)
    w.run(M.reservierung_zurueckgeben(lid, w.k["id"]))
    b = w.run(M.buyer_interests(Response(), user=w.k2))
    assert b[0]["anderweitig_reserviert"] is False and b[0]["status"] == "offen"
    assert b[0]["inserat"]["make_label"] == "VW"


def test_rp503_gesperrte_firma_liefert_nichts(welt):
    M = _mod("routes.marketplace")
    w = welt
    lid = w.run(w.inserat())
    w.run(w.anfrage(lid, status="akzeptiert"))
    w.run(w.db.users.update_one({"id": w.chef["id"]}, {"$set": {"active": False}}))
    a = w.run(M.buyer_interests(Response(), user=w.k))
    assert "haendler" not in a[0] and "inserat" not in a[0]


def test_rp520_zaehler_am_zug_und_neu(welt):
    M = _mod("routes.marketplace")
    w = welt
    lid = w.run(w.inserat())
    vorher = _jetzt(minutes=-5)
    w.run(w.anfrage(lid, status="gegenangebot", counter_offer=21000.0,
                    history=[{"von": "haendler", "aktion": "gegenangebot"}]))
    w.run(w.anfrage(w.run(w.inserat()), status="akzeptiert",
                    history=[{"von": "haendler", "aktion": "akzeptieren"}]))
    w.run(w.anfrage(w.run(w.inserat()), status="gegenangebot_kaeufer",
                    history=[{"von": "kaeufer", "aktion": "gegenangebot"}]))
    z = w.run(M.buyer_interessen_zaehler(seit=vorher, user=w.k))
    # neu: nur die Annahme — das Gegenangebot zaehlt schon in am_zug, das
    # eigene Kaeufer-Gegenangebot gar nicht
    assert z == {"am_zug": 1, "neu": 1}, z
    z = w.run(M.buyer_interessen_zaehler(seit=_jetzt(minutes=5), user=w.k))
    assert z == {"am_zug": 1, "neu": 0}
    # Browser-Format (toISOString) und Unsinn
    assert w.run(M.buyer_interessen_zaehler(
        seit=datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.000Z"), user=w.k))["am_zug"] == 1
    assert w.run(M.buyer_interessen_zaehler(seit="kaputt", user=w.k)) == {"am_zug": 1, "neu": 0}


# ======================================================= RP-521
def test_rp521_niedrigster_zulaessiger_preis():
    M = _mod("routes.marketplace")
    l = {"prices": {"public": 8000, "b2b": 9000, "network": 8500}}
    assert M._preisstufe(l, is_member=True, is_trade=True) == (8000, "oeffentlich")
    assert M._preisstufe(l, is_member=False, is_trade=True) == (8000, "oeffentlich")
    l = {"prices": {"public": 9900, "b2b": 9000, "network": 8000}}
    assert M._preisstufe(l, is_member=True, is_trade=True) == (8000, "netzwerk")
    assert M._preisstufe(l, is_member=False, is_trade=True) == (9000, "b2b")
    assert M._preisstufe(l, is_member=False, is_trade=False) == (9900, "oeffentlich")
    # Gleichstand: bevorzugte Stufe
    assert M._preisstufe({"prices": {"public": 9000, "b2b": 9000}},
                         is_member=False, is_trade=True) == (9000, "b2b")
    # ohne Preis wie bisher "auf Anfrage"
    assert M._preisstufe({"prices": {"public": None, "b2b": 0}},
                         is_member=True, is_trade=True) == (None, "oeffentlich")


def test_rp521_filter_und_sortierung_rechnen_mit_demselben_preis(welt):
    M = _mod("routes.marketplace")
    w = welt
    guenstig = w.run(w.inserat(prices={"public": 8000, "b2b": 9500}))
    mittel = w.run(w.inserat(prices={"public": 9900, "b2b": 8800}))
    liste = w.run(_listings(M, w.k, sort="preis_auf"))
    assert [(v["id"], v["price"]) for v in liste] == [(guenstig, 8000), (mittel, 8800)]
    assert [v["id"] for v in w.run(_listings(M, w.k, price_max=8500))] == [guenstig]


# ======================================================= RP-504 / RP-505 / RP-507(2)
def test_rp504_505_507_oeffentliche_sicht():
    M = _mod("routes.marketplace")
    basis = {"id": "x", "dealer_id": "d", "photos": {"mode": "neu", "uploaded_keys": ["resale/a.jpg"]}}
    v = M._public_listing_view({**basis, "data": {
        "model_description": "Golf, Tel. 0151/98765432", "accident_damaged": False,
        "mileage": "", "power_ps": ""}}, is_member=False, is_trade=False)
    assert "model_description" not in v["data"] and "accident_damaged" not in v["data"]
    assert v["data"]["accident_free"] is None, "keine Zusicherung aus dem Portal"
    assert "unfallschaden_laut_einkauf" not in v["data"]
    assert v["data"]["mileage"] is None and v["data"]["power_ps"] is None
    # Portal meldet Unfallschaden, Haendler hat nichts angegeben -> Hinweis
    v = M._public_listing_view({**basis, "data": {"accident_damaged": True}},
                               is_member=False, is_trade=False)
    assert v["data"]["unfallschaden_laut_einkauf"] is True
    # Haendler hat ausdruecklich angegeben -> seine Angabe gilt allein
    v = M._public_listing_view({**basis, "data": {"accident_damaged": True,
                                                  "accident_free": "Ja"}},
                               is_member=False, is_trade=False)
    assert v["data"]["accident_free"] == "Ja" and "unfallschaden_laut_einkauf" not in v["data"]
    # RP-099: Haendlerfotos 3 Tage gueltig, beide Listen identisch
    exp = int(re.search(r"exp=(\d+)", v["photos"][0]).group(1))
    assert exp - datetime.now(timezone.utc).timestamp() > 2 * 24 * 3600
    assert v["photos"] == v["dealer_photos"]


# ======================================================= RP-507(1) / RP-524
@pytest.mark.parametrize("filt,label,erwartet", [
    ("Mercedes-Benz", "Lamborghini", False),
    ("Mercedes-Benz", "Mercedes-Benz", True),
    ("Mercedes-Benz", "Mercedes-AMG", True),
    ("Volkswagen", "VW", True),
    ("VW", "Volkswagen", True),
    ("DS", "DS Automobiles", True),
    ("DS", "Mercedes", False),
    ("Citroen", "Citroën", True),
    ("Citroën", "Citroen", True),
    ("Citroën", "CITROËN", True),
    ("Skoda", "Škoda", True),
    ("Land Rover", "Range Rover", True),
])
def test_rp507_524_markenmuster(filt, label, erwartet):
    M = _mod("routes.marketplace")
    muster = M._make_regex_variants(filt)
    assert bool(re.search(muster, label, re.IGNORECASE)) is erwartet, (filt, label, muster)
    assert M._norm_make("Citroën") == M._norm_make("Citroen") == "citroen"


@pytest.mark.parametrize("filt,label,erwartet", [
    ("C 200", "GLC 200", False),
    ("C 200", "C 200 d", True),
    ("C 200", "C200d", True),
    ("C 200", "C 2000", False),
    ("X1", "iX1", False),
    ("X1", "X1 sDrive18i", True),
    ("TT", "A4 Avant quattro", False),
    ("TT", "TT RS", True),
    ("DS 3", "DS3", True),
    ("Ceed", "cee'd", True),
    ("RS Q3", "RSQ3", True),
    ("Q3", "RS Q3", True),
    ("A3", "A35", False),
    ("Ka", "Kadjar", False),
    ("Megane", "Mégane", True),
])
def test_rp528_529_modellmuster(filt, label, erwartet):
    M = _mod("routes.marketplace")
    muster = M._modell_regex(filt)
    assert bool(re.search(muster, label, re.IGNORECASE)) is erwartet, (filt, label, muster)


async def _listings(M, user, **k):
    args = dict(q=None, make=None, model=None, fuel=None, price_min=None, price_max=None,
                km_min=None, km_max=None, ps_min=None, ps_max=None, sort=None, dealer=None,
                nur_favoriten=0, page=1, limit=300)
    args.update(k)
    return await M.browse_listings(user=user, **args)


def test_rp524_528_filter_in_mongo(welt):
    """Dieselben Muster in der echten Datenbank (PCRE statt Python-re)."""
    M = _mod("routes.marketplace")
    w = welt
    citroen = w.run(w.inserat(data={"make_label": "Citroën", "model_label": "C3"}))
    lambo = w.run(w.inserat(data={"make_label": "Lamborghini", "model_label": "Urus"}))
    benz = w.run(w.inserat(data={"make_label": "Mercedes-Benz", "model_label": "C 200 d"}))
    glc = w.run(w.inserat(data={"make_label": "Mercedes-Benz", "model_label": "GLC 200"}))
    ids = lambda **k: {v["id"] for v in w.run(_listings(M, w.k, **k))}  # noqa: E731
    assert ids(make="Citroen") == {citroen}
    assert ids(make="CITROËN") == {citroen}
    assert ids(make="Mercedes-Benz") == {benz, glc}
    assert lambo not in ids(make="Mercedes-Benz")
    assert ids(make="Mercedes-Benz", model="C 200") == {benz}


# ======================================================= RP-506
def test_rp506_kraftstoff_gruppen(welt):
    M = _mod("routes.marketplace")
    w = welt

    def auto(fuel, label):
        return w.run(w.inserat(data={"make_label": "X", "fuel": fuel, "fuel_label": label}))
    benzin = auto("PETROL", "Benzin")
    hybrid_code = auto("HYBRID", "Hybrid (Benzin/Elektro)")
    hybrid_alt = auto("ELEKTRO/BENZIN", "Elektro/Benzin")      # AutoScout-Altbestand
    elektro = auto("ELECTRICITY", "Elektro")
    cng = auto("CNG", "Erdgas (CNG)")
    lpg_alt = auto("", "Autogas (LPG)")
    diesel_alt = auto("", "Diesel")
    ids = lambda f: {v["id"] for v in w.run(_listings(M, w.k, fuel=f))}  # noqa: E731
    assert ids("Hybrid") == {hybrid_code, hybrid_alt}
    assert ids("Benzin") == {benzin}
    assert ids("Elektro") == {elektro}
    assert ids("Gas") == {cng, lpg_alt}
    assert ids("LPG") == {cng, lpg_alt}, "alte Oberflaeche sendet LPG"
    assert ids("Diesel") == {diesel_alt}
    assert ids("Erdgas") == {cng, lpg_alt}, "Erdgas gehoert zur Gas-Gruppe"
    # Nicht erkannter Wert: Teilstring der Beschriftung wie bisher
    assert ids("Erdg") == {cng}


# ======================================================= RP-512
def test_rp512_km_ps_deutsch(welt):
    M = _mod("routes.marketplace")
    w = welt
    assert M._ganzzahl_filter("150.000", "x") == 150000
    assert M._ganzzahl_filter(" 150 000 km", "x") == 150000
    assert M._ganzzahl_filter("", "x") is None and M._ganzzahl_filter(None, "x") is None
    assert M._ganzzahl_filter("150", "x") == 150
    for kaputt in ("95.5", "95,5", "viel", "1.5.0"):
        with pytest.raises(HTTPException) as e:
            M._ganzzahl_filter(kaputt, "Kilometer bis")
        assert e.value.status_code == 400 and "ganze Zahl" in e.value.detail
    wenig = w.run(w.inserat(data={"make_label": "VW", "mileage": 90000}))
    viel = w.run(w.inserat(data={"make_label": "VW", "mileage": 160000}))
    treffer = {v["id"] for v in w.run(_listings(M, w.k, km_max="150.000"))}
    assert wenig in treffer and viel not in treffer
    code, _ = _code(w, _listings(M, w.k, km_max="95.5"))
    assert code == 400


# ======================================================= RP-530
def test_rp530_ungueltiges_token_401_ohne_token_oeffentlich(welt):
    M = _mod("routes.marketplace")
    w = welt
    kaputt = HTTPAuthorizationCredentials(scheme="Bearer", credentials="unsinn")
    code, _ = _code(w, M.marktplatz_besucher(kaputt))
    assert code == 401
    assert w.run(M.marktplatz_besucher(None)) is None
