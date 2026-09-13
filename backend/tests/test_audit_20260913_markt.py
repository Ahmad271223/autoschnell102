# -*- coding: utf-8 -*-
"""Audit 13.09.2026 — Bereich markt (Marktplatz, Weiterverkauf, Kaeufer,
Einladungen, Netzwerk).

  16  GET /resale: Abschnitt per X-Truncated melden, before-Cursor
  17  PUT /resale data.*: Typ/Endlichkeit/Laenge je Feld, Altbestand defensiv
  18  Merken: Doppelklick ergibt EINEN Eintrag (Teil-Unique-Index + Bereinigung)
  19  hoechstens EINE laufende Anfrage je Kaeufer und Inserat (409 + Index)
  20  Haendler-Anfragen: laufende werden nicht von erledigten verdraengt
  21  Kaeufer-Anfragen: dito
  22  Haendlerseite zaehlt alle sichtbaren Inserate (Filter vor dem Limit)
  23  Einladung: paralleler Aufruf desselben Kaeufers verbraucht nur einmal und
      nimmt die Mitgliedschaft nicht zurueck
  24  hoechstens 500 offene Einladungslinks je Firma, alle bleiben gelistet
  25  Kaeufer-Login zaehlt je Konto (nicht je IP)
  26  erfolgreicher Kaeufer-Login leert den Zaehler
  27  hoechstens EINE offene Zugangsanfrage je Kaeufer
  56  Audit-Fehler kippt die Kaeufer-Registrierung nicht
  57  Audit-Fehler kippt Einladung erstellen/loeschen nicht
  58  Netzwerk-Widerruf ist nach einem Teilfehler wiederholbar

In-Prozess: Routen-Funktionen direkt mit Nutzer-Dicts, Modul-`db` zeigt auf
einen eigenen Motor-Client des Test-Loops (nur Mongo noetig, kein Server).
"""
import asyncio
import importlib
import inspect
import json
import os
import re
import sys
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from fastapi import HTTPException, Response

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

WURZEL = Path(__file__).resolve().parents[2]
MONGO_URL = os.environ.get("MONGO_URL") or "mongodb://127.0.0.1:27017"
MAIL = "e2etest-mail.de"
PW = "AuditMarkt13!xY"
MODULE = ("deps", "indizes", "routes.bestand", "routes.marketplace",
          "routes.resale", "routes.team", "kaufvorgang")


def _jetzt(**delta):
    return (datetime.now(timezone.utc) + timedelta(**delta)).isoformat()


def _mod(name):
    return importlib.import_module(name)


def _ip():
    return f"203.0.113.{uuid.uuid4().int % 250 + 1}"


def _request(ip="127.0.0.1", path="/api/buyer/login"):
    from starlette.requests import Request
    return Request({"type": "http", "method": "POST", "path": path, "headers": [],
                    "client": (ip, 40000), "query_string": b""})


class _SammlungHaken:
    def __init__(self, echt, methoden):
        self._echt, self._methoden = echt, methoden

    def __getattr__(self, name):
        if name in self._methoden:
            return self._methoden[name](getattr(self._echt, name))
        return getattr(self._echt, name)


class _Haken:
    """Motor-db mit eingehaengtem Verhalten fuer einzelne (Sammlung, Methode)."""

    def __init__(self, echt, haken):
        self._echt, self._haken = echt, haken

    def __getattr__(self, name):
        coll = getattr(self._echt, name)
        methoden = {m: f for (c, m), f in self._haken.items() if c == name}
        return _SammlungHaken(coll, methoden) if methoden else coll

    def __getitem__(self, name):
        return getattr(self, name)


class _Welt:
    def __init__(self, db):
        s = uuid.uuid4().hex[:10]
        self.s, self.db = s, db
        self.did = f"d_a13_{s}"
        self.slug = f"a13-{s}"
        self.chef = {"id": f"chef_a13_{s}", "dealer_id": self.did, "role": "dealer",
                     "email": f"chef_a13_{s}@{MAIL}"}
        self.k = {"id": f"k_a13_{s}", "dealer_id": None, "role": "b2b_buyer",
                  "company_name": "K GmbH", "email": f"k_a13_{s}@{MAIL}"}
        self.user_ids = [self.chef["id"], self.k["id"]]
        self.mails = []
        self.ips = []

    async def anlegen(self):
        await self.db.dealers.insert_one({
            "id": self.did, "user_id": self.chef["id"], "company_name": f"A13 Autohaus {self.s}",
            "marketplace": {"public": True, "slug": self.slug}, "created_at": _jetzt()})
        await self.db.users.insert_many([
            {**self.chef, "active": True, "password_hash": "x", "created_at": _jetzt()},
            {**self.k, "active": True, "password_hash": "x", "created_at": _jetzt()}])

    def inserat_doc(self, sichtbarkeit="public", **extra):
        lid = f"l_{self.s}_{uuid.uuid4().hex[:10]}"
        doc = {"id": lid, "dealer_id": self.did, "vehicle_id": f"v_{lid}",
               "title": f"A13 Golf {self.s}", "status": "veroeffentlicht",
               "visibility": sichtbarkeit, "prices": {"public": 9900},
               "data": {"make_label": "VW", "model_label": "Golf", "mileage": 1000},
               "photos": {}, "purchase_price": 5000, "published_at": _jetzt(),
               "created_at": _jetzt(), "updated_at": _jetzt()}
        doc.update(extra)
        return doc

    async def inserat(self, sichtbarkeit="public", **extra):
        doc = self.inserat_doc(sichtbarkeit, **extra)
        await self.db.resale_listings.insert_one(doc)
        return doc["id"]

    async def einladung(self, max_uses=1, used_count=0, used_by=None, stunden=24, **extra):
        doc = {"id": str(uuid.uuid4()), "dealer_id": self.did,
               "token": f"a13tok_{uuid.uuid4().hex[:16]}", "expires_at": _jetzt(hours=stunden),
               "max_uses": max_uses, "used_count": used_count, "used_by": used_by or [],
               "created_at": _jetzt()}
        doc.update(extra)
        await self.db.dealer_invites.insert_one(doc)
        return doc

    async def login_konten(self, n):
        from auth import hash_password_async
        h = await hash_password_async(PW)
        mails = []
        for i in range(n):
            uid = f"lk{i}_a13_{self.s}"
            mail = f"{uid}@{MAIL}"
            await self.db.users.insert_one({"id": uid, "email": mail, "role": "b2b_buyer",
                                            "active": True, "dealer_id": None,
                                            "password_hash": h, "created_at": _jetzt()})
            self.user_ids.append(uid)
            mails.append(mail)
        return mails

    async def aufraeumen(self):
        db = self.db
        for c in ("resale_listings", "network_members", "buyer_favorites", "activity_logs",
                  "listing_interest", "dealer_invites", "plan_requests"):
            await db[c].delete_many({"dealer_id": self.did})
        await db.dealers.delete_many({"id": self.did})
        ids = self.user_ids
        for c in ("buyer_favorites", "listing_interest", "network_members", "plan_requests"):
            await db[c].delete_many({"buyer_user_id": {"$in": ids}})
        await db.activity_logs.delete_many({"user_id": {"$in": ids}})
        await db.users.delete_many({"id": {"$in": ids}})
        for mail in self.mails:
            u = await db.users.find_one({"email": mail}, {"_id": 0, "id": 1}) or {}
            await db.activity_logs.delete_many({"user_id": u.get("id", "-")})
            await db.users.delete_many({"email": mail})
        for ip in self.ips:
            await db.rate_limits.delete_many({"_id": {"$regex": "^login(-ip)?:" + re.escape(ip)}})


@pytest.fixture
def welt():
    """Modul-db aller beteiligten Module auf einen frischen Motor-Client des
    Test-Loops legen (Muster test_befunde_runde17_markt)."""
    from motor.motor_asyncio import AsyncIOMotorClient
    mods = [_mod(n) for n in MODULE]
    alt = [(m, m.__dict__.get("db")) for m in mods]
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    client = AsyncIOMotorClient(MONGO_URL, serverSelectionTimeoutMS=5000)
    db = client[os.environ["DB_NAME"]]
    for m in mods:
        if "db" in m.__dict__:
            m.db = db
    w = _Welt(db)
    w.run = loop.run_until_complete
    w.run(w.anlegen())
    try:
        yield w
    finally:
        try:
            w.run(w.aufraeumen())
        finally:
            for m, d in alt:
                if d is not None:
                    m.db = d
            client.close()
            loop.close()


async def _vor_dem_index(coll, name):
    """Zustand vor dem Rollout: den neuen Index (aus einem frueheren Lauf auf
    derselben Test-DB) entfernen, damit sich Altdubletten anlegen lassen."""
    if name in await coll.index_information():
        await coll.drop_index(name)


def _code(coro_fn):
    """Statuscode einer Route (200 bei Erfolg)."""
    async def lauf():
        try:
            await coro_fn()
            return 200
        except HTTPException as e:
            return e.status_code
    return lauf()


# ============================================================ Nr. 16
def test_16_resale_liste_meldet_abschnitt_und_blaettert(welt):
    R = _mod("routes.resale")
    basis = datetime.now(timezone.utc).replace(microsecond=123456) - timedelta(days=2)
    docs = [welt.inserat_doc(status="verkauft",
                             updated_at=(basis + timedelta(seconds=i)).isoformat())
            for i in range(305)]

    async def lauf():
        await welt.db.resale_listings.insert_many([dict(d) for d in docs])
        r1 = Response()
        teil1 = await R.list_listings(r1, user=welt.chef, status=None, before=None)
        r2 = Response()
        teil2 = await R.list_listings(r2, user=welt.chef, status=None,
                                      before=teil1[-1]["updated_at"])
        return teil1, r1.headers.get("X-Truncated"), teil2, r2.headers.get("X-Truncated")

    teil1, kopf1, teil2, kopf2 = welt.run(lauf())
    assert len(teil1) == 300 and kopf1 == "1"
    assert len(teil2) == 5 and kopf2 == "0"
    assert {d["id"] for d in teil1} | {d["id"] for d in teil2} == {d["id"] for d in docs}


# ============================================================ Nr. 17
def test_17_fahrzeugdaten_werden_je_feld_bereinigt(welt):
    R = _mod("routes.resale")
    db = welt.db

    async def lauf():
        lid = await welt.inserat(status="entwurf", data={
            "mileage": 1000, "first_registration": "03/2019", "features": ["ABS"]})
        await R.update_listing(lid, R.ListingUpdateIn(data={
            "mileage": float("inf"), "first_registration": {"a": 1},
            "features": ["x" * 5000] * 500, "accident_free": "Nein", "power_ps": "150",
            "power_kw": "1e400", "color": "y" * 500, "unbekannt": "egal"}), welt.chef)
        doc = await db.resale_listings.find_one({"id": lid}, {"_id": 0})
        audit = await db.activity_logs.find_one({"ref": lid, "action": "inserat.geaendert"})
        return doc, audit

    doc, audit = welt.run(lauf())
    d = doc["data"]
    json.dumps(d, allow_nan=False)            # vorher ValueError (inf)
    assert d["mileage"] == 1000, "nicht endlicher km-Stand verworfen, alter Wert bleibt"
    assert d["first_registration"] == "03/2019", "Objekt statt Text verworfen"
    assert len(d["features"]) <= 150 and all(len(f) <= 200 for f in d["features"])
    assert d["accident_free"] == "Nein" and d["power_ps"] == "150"
    assert "power_kw" not in d, "'1e400' ist nicht endlich"
    assert len(d["color"]) == 80 and "unbekannt" not in d
    geaendert = audit["meta"]["fahrzeugdaten_geaendert"]
    assert "mileage" not in geaendert and "first_registration" not in geaendert
    assert "power_ps" in geaendert and "features" in geaendert


def test_17_reine_regel_und_altbestand_in_der_oeffentlichen_sicht():
    R = _mod("routes.resale")
    M = _mod("routes.marketplace")
    f = R._fahrzeugwert_bereinigen
    assert f("accident_free", "") == (True, "")
    assert f("accident_free", "Ja") == (True, "Ja")
    assert f("accident_free", True) == (True, True)
    assert f("mileage", "") == (True, "")
    assert f("mileage", 2000) == (True, 2000)
    assert f("mileage", "1e400")[0] is False
    assert f("mileage", float("nan"))[0] is False
    assert f("previous_owners", {"a": 1})[0] is False
    assert f("model_label", ["x"])[0] is False
    assert f("mileage", 10 ** 30)[0] is False, "passt nicht in BSON int64"
    # Altbestand mit inf darf die oeffentliche Liste nicht mehr kippen
    sicht = M._public_listing_view({"id": "x", "dealer_id": "d", "data": {
        "mileage": float("inf"), "features": ["ABS", float("nan")]}},
        is_member=False, is_trade=False)
    json.dumps(sicht, allow_nan=False)
    assert sicht["data"]["mileage"] is None and sicht["data"]["features"] == ["ABS"]


# ============================================================ Nr. 18
def test_18_favorit_doppelklick_ergibt_einen_eintrag(welt, monkeypatch):
    M, I = _mod("routes.marketplace"), _mod("indizes")
    db = welt.db
    k = welt.k

    async def langsam(user, listing):
        await asyncio.sleep(0.05)
        return True
    monkeypatch.setattr(M, "_inserat_sichtbar_fuer", langsam)

    async def lauf():
        lid = await welt.inserat()
        alt = await welt.inserat()
        # Altdublette aus der Zeit vor dem Index: wird automatisch bereinigt
        await _vor_dem_index(db.buyer_favorites, "favorit_je_kaeufer_inserat")
        await db.buyer_favorites.insert_many([
            {"id": str(uuid.uuid4()), "buyer_user_id": k["id"], "listing_id": alt,
             "dealer_id": welt.did, "created_at": _jetzt(minutes=-i)} for i in range(3)])
        steht = await I._favoriten_unique_index()
        nach_bereinigung = await db.buyer_favorites.count_documents(
            {"buyer_user_id": k["id"], "listing_id": alt})
        erg = await asyncio.gather(M.toggle_favorit(lid, user=k), M.toggle_favorit(lid, user=k))
        n1 = await db.buyer_favorites.count_documents({"buyer_user_id": k["id"], "listing_id": lid})
        weg = await M.toggle_favorit(lid, user=k)
        n0 = await db.buyer_favorites.count_documents({"buyer_user_id": k["id"], "listing_id": lid})
        return steht, nach_bereinigung, erg, n1, weg, n0

    steht, nach_bereinigung, erg, n1, weg, n0 = welt.run(lauf())
    assert steht is True and nach_bereinigung == 1
    assert erg == [{"favorit": True}, {"favorit": True}] and n1 == 1
    assert weg == {"favorit": False} and n0 == 0
    assert "_zugang_erzwingen(user)" in inspect.getsource(M.toggle_favorit)
    assert "_favoriten_unique_index()" in (WURZEL / "backend" / "server.py").read_text(encoding="utf-8")


# ============================================================ Nr. 19
def test_19_hoechstens_eine_laufende_anfrage_je_kaeufer_und_inserat(welt, monkeypatch):
    M, I = _mod("routes.marketplace"), _mod("indizes")
    db = welt.db
    k = welt.k

    async def lauf():
        alt = await welt.inserat()
        # Altdubletten: zwei laufende Anfragen desselben Kaeufers
        await _vor_dem_index(db.listing_interest, "interesse_offen_je_kaeufer")
        await db.listing_interest.insert_many([
            {"id": f"alt{i}_{welt.s}", "listing_id": alt, "dealer_id": welt.did,
             "buyer_user_id": k["id"], "status": st, "history": [],
             "created_at": _jetzt(hours=-5 + i), "updated_at": _jetzt(hours=-5 + i)}
            for i, st in enumerate(("offen", "gegenangebot"))])
        steht = await I._interesse_unique_index()
        alt_stati = {d["id"]: d["status"] async for d in db.listing_interest.find({"listing_id": alt})}

        lid = await welt.inserat()
        erste = await M.send_interest(lid, M.InterestIn(offer=1000), user=k)
        zweite = await _code(lambda: M.send_interest(lid, M.InterestIn(offer=900), user=k))
        n = await db.listing_interest.count_documents(
            {"listing_id": lid, "buyer_user_id": k["id"], "status": "offen"})
        # nach Ablehnung ist eine neue Anfrage wieder moeglich
        await M.answer_interest(erste["interest_id"], M.InterestAnswerIn(action="ablehnen"),
                                user=welt.chef)
        dritte = await _code(lambda: M.send_interest(lid, M.InterestIn(offer=950), user=k))
        return steht, alt_stati, zweite, n, dritte

    steht, alt_stati, zweite, n, dritte = welt.run(lauf())
    assert steht is True
    assert alt_stati == {f"alt0_{welt.s}": "abgelehnt", f"alt1_{welt.s}": "gegenangebot"}, \
        "die zuletzt bewegte Verhandlung bleibt, die Dublette wird geschlossen"
    assert zweite == 409 and n == 1
    assert dritte == 200

    # Rennen: beide bestehen die Vorabpruefung, der Index faengt die zweite
    async def langsam(user, listing):
        await asyncio.sleep(0.05)
        return True
    monkeypatch.setattr(M, "_inserat_sichtbar_fuer", langsam)

    async def rennen():
        lid = await welt.inserat()
        codes = await asyncio.gather(
            _code(lambda: M.send_interest(lid, M.InterestIn(offer=1), user=k)),
            _code(lambda: M.send_interest(lid, M.InterestIn(offer=2), user=k)))
        n = await db.listing_interest.count_documents({"listing_id": lid, "buyer_user_id": k["id"]})
        return sorted(codes), n

    codes, n = welt.run(rennen())
    assert codes == [200, 409] and n == 1
    assert "_interesse_unique_index()" in (WURZEL / "backend" / "server.py").read_text(encoding="utf-8")


# ============================================================ Nr. 20/21
def _anfragen(welt, feld_wert, alt_status):
    offen_id = f"lauf_{welt.s}"
    docs = [{"id": offen_id, "listing_id": "l", "dealer_id": welt.did, "buyer_user_id": welt.k["id"],
             "status": alt_status, "history": [], "created_at": _jetzt(days=-10),
             "updated_at": _jetzt(days=-10)}]
    docs += [{"id": f"zu{i}_{welt.s}", "listing_id": f"l{i}", "dealer_id": welt.did,
              "buyer_user_id": welt.k["id"], "status": "abgelehnt", "history": [],
              "created_at": _jetzt(minutes=-i), "updated_at": _jetzt(minutes=-i)}
             for i in range(205)]
    return offen_id, docs


def test_20_haendler_anfragen_laufende_nicht_verdraengt(welt):
    M = _mod("routes.marketplace")
    offen_id, docs = _anfragen(welt, None, "offen")

    async def lauf():
        await welt.db.listing_interest.insert_many(docs)
        r = Response()
        alle = await M.dealer_list_interests(r, status=None, listing_id=None, user=welt.chef)
        r2 = Response()
        abgelehnt = await M.dealer_list_interests(r2, status="abgelehnt", listing_id=None,
                                                  user=welt.chef)
        return alle, r.headers.get("X-Truncated"), abgelehnt, r2.headers.get("X-Truncated")

    alle, kopf, abgelehnt, kopf2 = welt.run(lauf())
    assert offen_id in {i["id"] for i in alle}, "laufende Anfrage fehlt in 'Alle'"
    assert len(alle) == 201 and kopf == "1", "erledigte ueber 200 werden als Abschnitt gemeldet"
    assert [i["created_at"] for i in alle] == sorted((i["created_at"] for i in alle), reverse=True)
    assert len(abgelehnt) == 205 and kopf2 == "0"


def test_21_kaeufer_anfragen_gegenangebot_bleibt_sichtbar(welt):
    M = _mod("routes.marketplace")
    offen_id, docs = _anfragen(welt, None, "gegenangebot")

    async def lauf():
        await welt.db.listing_interest.insert_many(docs)
        r = Response()
        return await M.buyer_interests(r, user=welt.k), r.headers.get("X-Truncated")

    alle, kopf = welt.run(lauf())
    assert offen_id in {i["id"] for i in alle}
    assert kopf == "1"


# ============================================================ Nr. 22
def test_22_haendlerseite_zaehlt_alle_sichtbaren_inserate(welt):
    M = _mod("routes.marketplace")
    db = welt.db

    async def lauf():
        await db.resale_listings.insert_many([
            welt.inserat_doc(published_at=_jetzt(minutes=-i)) for i in range(205)])
        viele = await M.dealer_page(welt.slug, None)
        await db.resale_listings.delete_many({"dealer_id": welt.did})
        await db.resale_listings.insert_many(
            [welt.inserat_doc("private", published_at=_jetzt(minutes=-i)) for i in range(150)]
            + [welt.inserat_doc(published_at=_jetzt(days=-1, minutes=-i)) for i in range(60)])
        gemischt = await M.dealer_page(welt.slug, None)
        return viele, gemischt

    viele, gemischt = welt.run(lauf())
    assert viele["profile"]["vehicle_count"] == 205
    assert len(viele["listings"]) == 200 and viele["listings_abgeschnitten"] is True
    assert gemischt["profile"]["vehicle_count"] == 60 and len(gemischt["listings"]) == 60
    assert gemischt["listings_abgeschnitten"] is False
    assert "is_trade=True" not in inspect.getsource(M.dealer_page)


# ============================================================ Nr. 23
def test_23_fall_a_paralleler_aufruf_verbraucht_nur_einmal(welt):
    M = _mod("routes.marketplace")
    db = welt.db
    k = welt.k["id"]

    async def lauf():
        await db.network_members.create_index([("dealer_id", 1), ("buyer_user_id", 1)], unique=True)
        inv = await welt.einladung(max_uses=5)
        alle = asyncio.Event()
        n = {"up": 0}

        def upsert(echt):
            async def update_one(*a, **kw):
                r = await echt(*a, **kw)
                n["up"] += 1
                if n["up"] >= 3:
                    alle.set()
                await alle.wait()
                return r
            return update_one
        M.db = _Haken(db, {("network_members", "update_one"): upsert})
        try:
            erg = await asyncio.wait_for(asyncio.gather(
                *[M._redeem_invite(inv["token"], k) for _ in range(3)]), 20)
        finally:
            M.db = db
        d = await db.dealer_invites.find_one({"id": inv["id"]})
        mitglieder = await db.network_members.count_documents({"dealer_id": welt.did, "buyer_user_id": k})
        return erg, d, mitglieder

    erg, d, mitglieder = welt.run(lauf())
    assert erg == [welt.did] * 3
    assert d["used_count"] == 1 and d["used_by"] == [k], "Nutzungen fuer andere Eingeladene bleiben"
    assert mitglieder == 1


def test_23_fall_b_rueckbau_loescht_fremd_verbuchte_mitgliedschaft_nicht(welt):
    M = _mod("routes.marketplace")
    db = welt.db
    k = welt.k["id"]

    async def lauf():
        inv = await welt.einladung(max_uses=1)
        e1, e2 = asyncio.Event(), asyncio.Event()
        n = {"up": 0, "fau": 0}

        def upsert(echt):
            async def update_one(*a, **kw):
                n["up"] += 1
                meine = n["up"]
                r = await echt(*a, **kw)
                if meine == 1:
                    e1.set()
                return r
            return update_one

        def verbrauch(echt):
            async def find_one_and_update(*a, **kw):
                n["fau"] += 1
                if n["fau"] == 1:
                    await e2.wait()       # A verbraucht erst NACH B
                    return await echt(*a, **kw)
                try:
                    return await echt(*a, **kw)
                finally:
                    e2.set()
            return find_one_and_update

        async def zweiter():
            await e1.wait()               # B startet nach dem Upsert von A
            return await M._redeem_invite(inv["token"], k)

        M.db = _Haken(db, {("network_members", "update_one"): upsert,
                           ("dealer_invites", "find_one_and_update"): verbrauch})
        try:
            a, b = await asyncio.wait_for(asyncio.gather(
                M._redeem_invite(inv["token"], k), zweiter()), 20)
        finally:
            M.db = db
        mitglieder = await db.network_members.count_documents({"dealer_id": welt.did, "buyer_user_id": k})
        dritter = await M._redeem_invite(inv["token"], k)
        d = await db.dealer_invites.find_one({"id": inv["id"]})
        return a, b, mitglieder, dritter, d

    a, b, mitglieder, dritter, d = welt.run(lauf())
    assert b == welt.did
    assert a == welt.did, "A meldete den Beitritt als gescheitert"
    assert mitglieder == 1, "Rueckbau von A loeschte die von B verbuchte Mitgliedschaft"
    assert dritter == welt.did and d["used_count"] == 1


# ============================================================ Nr. 24
def test_24_offene_einladungen_gedeckelt_und_alle_gelistet(welt):
    M = _mod("routes.marketplace")
    db = welt.db

    async def lauf():
        docs = [{"id": str(uuid.uuid4()), "dealer_id": welt.did,
                 "token": f"a13m_{uuid.uuid4().hex}", "expires_at": _jetzt(hours=100),
                 "max_uses": 5, "used_count": 0, "used_by": [],
                 "created_at": _jetzt(seconds=-i)} for i in range(M.OFFENE_EINLADUNGEN_MAX)]
        await db.dealer_invites.insert_many([dict(d) for d in docs])
        voll = await _code(lambda: M.create_invite(M.InviteIn(), user=welt.chef))
        r = Response()
        liste = await M.list_invites(user=welt.chef, response=r)
        await M.delete_invite(docs[0]["id"], user=welt.chef)
        wieder = await _code(lambda: M.create_invite(M.InviteIn(), user=welt.chef))
        return docs, voll, liste, r.headers.get("X-Truncated"), wieder

    docs, voll, liste, kopf, wieder = welt.run(lauf())
    assert M.OFFENE_EINLADUNGEN_MAX == 500
    assert voll == 409
    assert {d["id"] for d in docs} <= {e["id"] for e in liste}
    assert kopf == "0"
    assert wieder == 200


# ============================================================ Nr. 25/26
def test_25_kaeufer_login_zaehlt_je_konto_nicht_je_ip(welt, monkeypatch):
    M, rl = _mod("routes.marketplace"), _mod("rate_limiter")
    monkeypatch.setattr(rl, "_RATE_LIMIT_ENABLED", True)
    ip = _ip()
    welt.ips.append(ip)
    a, b = welt.run(welt.login_konten(2))

    async def lauf():
        fehl = [await _code(lambda: M.buyer_login(M.BuyerLoginIn(email=a, password="falsch-falsch"),
                                                  _request(ip))) for _ in range(10)]
        kollege = await _code(lambda: M.buyer_login(M.BuyerLoginIn(email=b, password=PW), _request(ip)))
        return fehl, kollege

    fehl, kollege = welt.run(lauf())
    assert fehl == [401] * 10
    assert kollege == 200, "Fehlversuche eines Kontos sperren den Kollegen am selben Anschluss"


def test_26_erfolgreicher_kaeufer_login_leert_den_zaehler(welt, monkeypatch):
    M, rl = _mod("routes.marketplace"), _mod("rate_limiter")
    monkeypatch.setattr(rl, "_RATE_LIMIT_ENABLED", True)
    ip = _ip()
    welt.ips.append(ip)
    (a,) = welt.run(welt.login_konten(1))

    def anmelden(pw):
        return _code(lambda: M.buyer_login(M.BuyerLoginIn(email=a, password=pw), _request(ip)))

    async def lauf():
        codes = [await anmelden("falsch-falsch") for _ in range(9)]
        codes.append(await anmelden(PW))
        rest = await welt.db.rate_limits.find_one(
            {"_id": {"$regex": "^" + re.escape(f"login:{ip}|{a}:")}})
        danach = [await anmelden(PW) for _ in range(10)]
        return codes, rest, danach

    codes, rest, danach = welt.run(lauf())
    assert codes == [401] * 9 + [200]
    assert rest is None, "Zaehler des Kontos nach Erfolg nicht geleert"
    assert danach == [200] * 10


def test_25_26_quelltext_wie_auth_login():
    M = _mod("routes.marketplace")
    q = inspect.getsource(M.buyer_login)
    assert "login_schluessel(" in q
    assert "login_limiter.check(schluessel)" in q
    assert "login_ip_limiter.check(ip)" in q
    assert "login_limiter.reset(schluessel)" in q
    assert "login_limiter.check(ip)" not in q


# ============================================================ Nr. 27
def test_27_hoechstens_eine_offene_zugangsanfrage(welt):
    M, I = _mod("routes.marketplace"), _mod("indizes")
    db = welt.db
    k = {**welt.k, "marketplace_access": {"gesperrt": True, "active": False}}
    k2, k3 = f"k2_a13_{welt.s}", f"k3_a13_{welt.s}"
    welt.user_ids += [k2, k3]

    async def lauf():
        await _vor_dem_index(db.plan_requests, "uniq_offene_buyer_access_anfrage")
        await db.plan_requests.insert_many([
            {"id": f"pr{i}_{welt.s}", "type": "buyer_access", "buyer_user_id": k2,
             "status": "offen", "created_at": _jetzt(minutes=-10 + i)} for i in range(2)])
        steht = await I._buyer_access_unique_index()
        k2_stati = {d["id"]: d["status"] async for d in db.plan_requests.find({"buyer_user_id": k2})}
        r1 = await M.request_marketplace_access(user=k)
        r2 = await M.request_marketplace_access(user=k)
        offen = await db.plan_requests.count_documents(
            {"type": "buyer_access", "buyer_user_id": k["id"], "status": "offen"})
        logs = await db.activity_logs.count_documents(
            {"user_id": k["id"], "action": "marktplatz.zugang.anfrage"})
        k3u = {"id": k3, "role": "b2b_buyer", "email": "x@y.de",
               "marketplace_access": {"gesperrt": True}}
        await asyncio.gather(*(M.request_marketplace_access(user=k3u) for _ in range(3)))
        offen3 = await db.plan_requests.count_documents({"type": "buyer_access", "buyer_user_id": k3})
        return steht, k2_stati, r1, r2, offen, logs, offen3

    steht, k2_stati, r1, r2, offen, logs, offen3 = welt.run(lauf())
    assert steht is True
    assert k2_stati == {f"pr0_{welt.s}": "offen", f"pr1_{welt.s}": "erledigt"}, "aelteste bleibt offen"
    assert r1["request_id"] == r2["request_id"]
    assert offen == 1 and logs == 1
    assert offen3 == 1
    assert "_buyer_access_unique_index()" in (WURZEL / "backend" / "server.py").read_text(encoding="utf-8")


# ============================================================ Nr. 56
def test_56_audit_fehler_kippt_registrierung_nicht(welt, monkeypatch):
    M, deps = _mod("routes.marketplace"), _mod("deps")
    from auth import decode_token

    async def frei(_ip):
        return True

    async def kaputt(*a, **kw):
        raise RuntimeError("Mongo weg")
    monkeypatch.setattr(M.register_limiter, "check", frei)
    monkeypatch.setattr(M, "log_activity", kaputt)
    monkeypatch.setattr(deps, "log_activity", kaputt)
    mail = f"reg_a13_{welt.s}@{MAIL}"
    welt.mails.append(mail)
    body = M.BuyerRegisterIn(company_name="A13 Kaeufer", contact_name="K M", email=mail,
                             password=PW, phone="0511", gewerblich_bestaetigt=True)

    async def lauf():
        r = await M.buyer_register(body, _request(path="/api/buyer/register"))
        u = await welt.db.users.find_one({"email": mail})
        return r, u

    r, u = welt.run(lauf())
    assert r["ok"] is True and u and u["role"] == "b2b_buyer"
    assert decode_token(r["token"])["sid"] == u["current_session_id"]


# ============================================================ Nr. 57
def test_57_audit_fehler_kippt_einladung_nicht(welt, monkeypatch):
    M, deps = _mod("routes.marketplace"), _mod("deps")

    async def kaputt(*a, **kw):
        raise RuntimeError("Mongo weg")
    monkeypatch.setattr(M, "log_activity", kaputt)
    monkeypatch.setattr(deps, "log_activity", kaputt)

    async def lauf():
        r = await M.create_invite(M.InviteIn(), user=welt.chef)
        inv = await welt.db.dealer_invites.find_one({"token": r["token"]})
        weg = await M.delete_invite(inv["id"], user=welt.chef)
        rest = await welt.db.dealer_invites.find_one({"id": inv["id"]})
        return r, inv, weg, rest

    r, inv, weg, rest = welt.run(lauf())
    assert r["ok"] is True and inv is not None
    assert weg == {"ok": True} and rest is None


# ============================================================ Nr. 58
def test_58_widerruf_nach_teilfehler_wiederholbar(welt, monkeypatch):
    M, deps = _mod("routes.marketplace"), _mod("deps")
    db = welt.db
    k = welt.k["id"]

    def kaputt_many(_echt):
        async def delete_many(*a, **kw):
            raise RuntimeError("Mongo weg")
        return delete_many

    async def lauf():
        lid = await welt.inserat("private")
        await db.network_members.insert_one({"dealer_id": welt.did, "buyer_user_id": k,
                                             "created_at": _jetzt()})
        await db.buyer_favorites.insert_one({"id": str(uuid.uuid4()), "buyer_user_id": k,
                                             "listing_id": lid, "dealer_id": welt.did,
                                             "created_at": _jetzt()})
        M.db = _Haken(db, {("buyer_favorites", "delete_many"): kaputt_many})
        try:
            with pytest.raises(RuntimeError):
                await M.remove_network_member(k, user=welt.chef)
        finally:
            M.db = db
        nochmal = await _code(lambda: M.remove_network_member(k, user=welt.chef))
        mitglieder = await db.network_members.count_documents({"dealer_id": welt.did, "buyer_user_id": k})
        favs = await db.buyer_favorites.count_documents({"buyer_user_id": k})
        audits = await db.activity_logs.count_documents(
            {"ref": k, "action": "netzwerk.mitglied.entfernt"})
        nie = await _code(lambda: M.remove_network_member(f"nie_{welt.s}", user=welt.chef))
        return nochmal, mitglieder, favs, audits, nie

    nochmal, mitglieder, favs, audits, nie = welt.run(lauf())
    assert nochmal == 200, "Wiederholung nach Teilfehler endete mit 404"
    assert mitglieder == 0 and favs == 0 and audits == 1
    assert nie == 404

    # Audit-Fehler nach dem Loeschen kippt den Widerruf nicht
    async def kaputt(*a, **kw):
        raise RuntimeError("Mongo weg")
    monkeypatch.setattr(M, "log_activity", kaputt)
    monkeypatch.setattr(deps, "log_activity", kaputt)

    async def lauf2():
        await db.network_members.insert_one({"dealer_id": welt.did, "buyer_user_id": k,
                                             "created_at": _jetzt()})
        r = await M.remove_network_member(k, user=welt.chef)
        n = await db.network_members.count_documents({"dealer_id": welt.did, "buyer_user_id": k})
        return r, n

    r, n = welt.run(lauf2())
    assert r == {"ok": True} and n == 0
