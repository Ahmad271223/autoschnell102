# -*- coding: utf-8 -*-
"""Runde 15 (08.09.2026): drei Pruefberichte "nur Sucher + Firmen-Chef",
bestaetigte Befunde — Races, Audit-Spuren, N+1, Eingabehaertung.

In-Prozess wie Runde 14: Routen-Funktionen direkt mit Fake-`user`-Dicts,
Modul-`db` zeigt auf einen Test-Client (nur Mongo noetig).
"""
import asyncio
import inspect
import os
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path

import pytest
from fastapi import HTTPException
from pydantic import ValidationError

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

MONGO_URL = os.environ.get("MONGO_URL") or "mongodb://127.0.0.1:27017"
DB_NAME = os.environ.get("DB_NAME") or "autoschnell"
WURZEL = Path(__file__).resolve().parents[2]


def _jetzt():
    return datetime.now(timezone.utc).isoformat()


def _lauf(coro):
    return asyncio.run(coro)


class _Welt:
    def __init__(self):
        s = uuid.uuid4().hex[:10]
        self.s = s
        self.dealer_id = f"d_r15_{s}"
        self.chef = {"id": f"chef_r15_{s}", "dealer_id": self.dealer_id, "role": "dealer"}
        self.sucher = {"id": f"su_r15_{s}", "dealer_id": self.dealer_id, "role": "sucher"}
        self.sucher_b = {"id": f"sb_r15_{s}", "dealer_id": self.dealer_id, "role": "sucher"}
        self.driver_id = f"f_r15_{s}"

    def appt(self, appt_id, **extra):
        doc = {"id": appt_id, "dealer_id": self.dealer_id, "title": f"Fahrt {appt_id}",
               "status": "offen", "pickup_date": "2099-09-15", "pickup_time": "10:00",
               "pickup_address": "Teststr. 1", "seller_name": "Verkaeufer",
               "created_by": self.chef["id"], "created_at": _jetzt()}
        doc.update(extra)
        return doc

    def fahrzeug(self, vid, **extra):
        doc = {"id": vid, "dealer_id": self.dealer_id, "lifecycle": "gekauft",
               "data": {"make_label": "BMW", "model_label": "320d"},
               "bestand": {"notes": "GEHEIME NOTIZ", "costs": [{"label": "x", "amount": 5}]},
               "purchase_price": 12345, "created_at": _jetzt()}
        doc.update(extra)
        return doc

    async def aufraeumen(self, db):
        for c in ("appointments", "vehicles", "dealer_drivers", "driver_accounts",
                  "activity_logs", "resale_listings", "subscriptions", "plan_requests",
                  "dealer_invites", "network_members", "generated_pdfs", "users"):
            await db[c].delete_many({"dealer_id": self.dealer_id})
        await db.driver_accounts.delete_many({"id": self.driver_id})
        await db.plan_requests.delete_many({"subject_user_id": {"$in": [self.chef["id"], self.sucher["id"]]}})


def _module(name):
    import importlib
    return importlib.import_module(name)


def _abfragen_zaehlen(monkeypatch, namen):
    """find/find_one je Sammlungsname zaehlen — auf Klassenebene, weil
    Motor bei jedem `db.<name>` ein neues Collection-Objekt liefert."""
    from motor.motor_asyncio import AsyncIOMotorCollection as K
    zaehler = {n: 0 for n in namen}
    f, fo = K.find, K.find_one

    def _f(self, *a, **k):
        if self.name in zaehler:
            zaehler[self.name] += 1
        return f(self, *a, **k)

    def _fo(self, *a, **k):
        if self.name in zaehler:
            zaehler[self.name] += 1
        return fo(self, *a, **k)
    monkeypatch.setattr(K, "find", _f)
    monkeypatch.setattr(K, "find_one", _fo)
    return zaehler


@pytest.fixture
def welt():
    """Modul-db auf einen frischen Motor-Client des laufenden Loops legen."""
    from motor.motor_asyncio import AsyncIOMotorClient
    w = _Welt()
    module_names = ["deps", "routes.appointments", "routes.drivers", "routes.bestand",
                    "routes.resale", "routes.dealer", "routes.marketplace", "routes.team",
                    "routes.contracts", "lifecycle"]
    mods = [_module(n) for n in module_names]
    alt = [(m, getattr(m, "db", None)) for m in mods]

    class _Ctx:
        def __init__(self):
            self.loop = asyncio.new_event_loop()
            asyncio.set_event_loop(self.loop)
            self.client = AsyncIOMotorClient(MONGO_URL, serverSelectionTimeoutMS=5000)
            self.db = self.client[DB_NAME]
            for m in mods:
                if hasattr(m, "db"):
                    m.db = self.db
            self.w = w

        def run(self, coro):
            return self.loop.run_until_complete(coro)

    ctx = _Ctx()
    yield ctx
    try:
        ctx.run(w.aufraeumen(ctx.db))
    finally:
        for m, d in alt:
            if d is not None:
                m.db = d
        ctx.client.close()
        ctx.loop.close()


# ================================================= Nr. 1: Fahrer entfernt waehrend Zuweisung
def test_01_fahrer_entfernt_zwischen_pruefung_und_write_wird_zurueckgenommen(welt, monkeypatch):
    A = _module("routes.appointments")
    w, db = welt.w, welt.db
    aid = f"a_{w.s}"

    async def lauf():
        await db.driver_accounts.insert_one({"id": w.driver_id, "display_name": "F", "active": True, "email": f"{w.driver_id}@e2etest-mail.de"})
        await db.dealer_drivers.insert_one({"id": str(uuid.uuid4()), "dealer_id": w.dealer_id,
                                            "driver_account_id": w.driver_id, "added_at": _jetzt()})
        await db.appointments.insert_one(w.appt(aid))
        # Race nachstellen: die Vorabpruefung ist durch, DANN entfernt der Chef
        # den Fahrer (drivers.py: erst Verknuepfung weg, dann Bereinigung)
        echt = A._fahrer_pruefen

        async def pruefen_dann_entfernen(dealer_id, driver_id):
            await echt(dealer_id, driver_id)
            await db.dealer_drivers.delete_many({"dealer_id": w.dealer_id})
            await db.appointments.update_many({"dealer_id": w.dealer_id, "driver_id": w.driver_id},
                                              {"$unset": {"driver_id": ""}})
        monkeypatch.setattr(A, "_fahrer_pruefen", pruefen_dann_entfernen)
        r = await A.update_appointment(aid, A.AppointmentIn(driver_id=w.driver_id), w.chef)
        nachher = await db.appointments.find_one({"id": aid}, {"_id": 0})
        return r, nachher

    r, nachher = welt.run(lauf())
    assert "driver_id" not in nachher, nachher
    assert nachher.get("zuteilung") is None
    assert r.get("hinweis") == A.FAHRER_ENTFERNT_HINWEIS


def test_01b_create_mit_soeben_entferntem_fahrer(welt, monkeypatch):
    A = _module("routes.appointments")
    w, db = welt.w, welt.db

    async def lauf():
        await db.driver_accounts.insert_one({"id": w.driver_id, "display_name": "F", "active": True, "email": f"{w.driver_id}@e2etest-mail.de"})
        await db.dealer_drivers.insert_one({"id": str(uuid.uuid4()), "dealer_id": w.dealer_id,
                                            "driver_account_id": w.driver_id, "added_at": _jetzt()})
        echt = A._fahrer_pruefen

        async def pruefen_dann_entfernen(dealer_id, driver_id):
            await echt(dealer_id, driver_id)
            await db.dealer_drivers.delete_many({"dealer_id": w.dealer_id})
        monkeypatch.setattr(A, "_fahrer_pruefen", pruefen_dann_entfernen)
        r = await A.create_appointment(
            A.AppointmentIn(title="T", pickup_date="2099-01-01", driver_id=w.driver_id), w.chef)
        return r, await db.appointments.find_one({"id": r["id"]}, {"_id": 0})

    r, gespeichert = welt.run(lauf())
    assert "driver_id" not in gespeichert and "driver_id" not in r
    assert r.get("hinweis") == A.FAHRER_ENTFERNT_HINWEIS


# ================================================= Nr. 2: Fahrer hinzufuegen atomar (409 statt 500)
def test_02_doppeltes_hinzufuegen_gibt_409_ohne_vorabpruefung(welt):
    D = _module("routes.drivers")
    w, db = welt.w, welt.db
    code = f"C{w.s[:6].upper()}"

    async def lauf():
        # derselbe Index wie in server.py — in der CI laeuft der Test gegen
        # eine frische Datenbank
        await db.dealer_drivers.create_index(
            [("dealer_id", 1), ("driver_account_id", 1)], unique=True)
        await db.driver_accounts.insert_one({"id": w.driver_id, "display_name": "F", "active": True,
                                             "email": f"{w.driver_id}@e2etest-mail.de", "driver_code": code})
        r1 = await D.add_driver_by_code(D.DriverLinkIn(driver_code=code), w.chef)
        with pytest.raises(HTTPException) as e:
            await D.add_driver_by_code(D.DriverLinkIn(driver_code=code), w.chef)
        return r1, e.value.status_code

    r1, status = welt.run(lauf())
    assert r1["id"] == w.driver_id and status == 409
    quelle = inspect.getsource(D.add_driver_by_code)
    assert "DuplicateKeyError" in quelle
    assert "existing = await db.dealer_drivers.find_one" not in quelle


# ================================================= Nr. 3: Terminliste laedt nur die noetigen Fahrzeuge
def test_03_terminliste_laedt_nur_fahrzeuge_der_termine_und_keine_bestandsdaten(welt, monkeypatch):
    A = _module("routes.appointments")
    from fastapi import Response
    from motor.motor_asyncio import AsyncIOMotorCollection as K
    w, db = welt.w, welt.db
    geladen = []
    echt = K.find

    def find_spion(self, *a, **k):
        if self.name == "vehicles":
            geladen.append((a, k))
        return echt(self, *a, **k)
    monkeypatch.setattr(K, "find", find_spion)

    async def lauf():
        await db.vehicles.insert_many([w.fahrzeug(f"v_a_{w.s}"), w.fahrzeug(f"v_b_{w.s}")])
        await db.appointments.insert_one(w.appt(f"a_{w.s}", vehicle_id=f"v_a_{w.s}"))
        return await A.list_appointments(Response(), w.chef)

    items = welt.run(lauf())
    assert len(items) == 1 and items[0]["vehicle"]["data"]["make_label"] == "BMW"
    assert "bestand" not in items[0]["vehicle"] and "purchase_price" not in items[0]["vehicle"]
    assert len(geladen) == 1
    filt, proj = geladen[0][0][0], geladen[0][0][1]
    assert filt["id"] == {"$in": [f"v_a_{w.s}"]}
    assert proj.get("data") == 1 and "bestand" not in proj


# ================================================= Nr. 4: Fahrerliste ohne N+1
def test_04_fahrerliste_zwei_abfragen_statt_1_plus_2n(welt, monkeypatch):
    D = _module("routes.drivers")
    w, db = welt.w, welt.db
    zaehler = _abfragen_zaehlen(monkeypatch, ("driver_accounts", "dealer_drivers"))

    async def lauf():
        ids = [f"{w.driver_id}_{i}" for i in range(5)]
        await db.driver_accounts.insert_many(
            [{"id": i, "display_name": f"Fahrer {n}", "active": True, "password_hash": "x", "email": f"{i}@e2etest-mail.de",
              "driver_code": f"K{w.s[:4]}{n}"} for n, i in enumerate(ids)])
        await db.dealer_drivers.insert_many(
            [{"id": str(uuid.uuid4()), "dealer_id": w.dealer_id, "driver_account_id": i,
              "added_at": _jetzt()} for i in ids])
        for k in zaehler:
            zaehler[k] = 0
        out = await D.list_drivers(w.chef)
        await db.driver_accounts.delete_many({"id": {"$in": ids}})
        return out, dict(zaehler)

    out, zaehler = welt.run(lauf())
    assert len(out) == 5 and all("password_hash" not in o for o in out)
    assert zaehler == {"driver_accounts": 1, "dealer_drivers": 1}, zaehler


# ================================================= Nr. 5: Bestand-Aenderung mit Audit
def test_05_bestand_aenderung_schreibt_audit_mit_kostensumme(welt):
    B = _module("routes.bestand")
    w, db = welt.w, welt.db
    vid = f"v_{w.s}"

    async def lauf():
        await db.vehicles.insert_one(w.fahrzeug(vid))
        await B.update_bestand(vid, B.BestandUpdateIn(costs=[{"label": "Aufbereitung", "amount": 5000}]),
                               w.chef)
        return await db.activity_logs.find_one({"dealer_id": w.dealer_id, "action": "bestand.geaendert"},
                                               {"_id": 0})

    log = welt.run(lauf())
    assert log and log["ref"] == vid and log["user_id"] == w.chef["id"]
    assert log["meta"]["felder"] == ["costs"]
    assert log["meta"]["kosten_summe_alt"] == 5 and log["meta"]["kosten_summe_neu"] == 5000


# ================================================= Nr. 4 (Runde C): Infinity/NaN in Kosten
@pytest.mark.parametrize("wert", [float("inf"), float("-inf"), float("nan"), "Infinity", 1e12])
def test_c4_bestandskosten_lehnen_inf_nan_ab(wert):
    B = _module("routes.bestand")
    with pytest.raises(HTTPException) as e:
        B._clean_costs([{"label": "x", "amount": wert}])
    assert e.value.status_code == 422


def test_c4_bestandskosten_normal_bleiben():
    B = _module("routes.bestand")
    assert B._clean_costs([{"label": "x", "amount": "12.345"}, "kaputt", {"label": "", "amount": 1}]) \
        == [{"label": "x", "amount": 12.35}]


# ================================================= Nr. 6: Resale-Aenderungen mit Audit
def test_06_inserat_aenderung_und_fotos_haben_audit():
    R = _module("routes.resale")
    for fn, aktion in ((R.update_listing, "inserat.geaendert"),
                       (R.upload_photos, "inserat.foto.hinzugefuegt"),
                       (R.remove_photo, "inserat.foto.entfernt")):
        q = inspect.getsource(fn)
        assert aktion in q and "log_activity" in q, fn.__name__
    assert inspect.getsource(R.remove_photo).count("inserat.foto.entfernt") == 2, "beide Zweige (key/url)"


def test_06b_preisaenderung_loggt_alt_und_neu(welt):
    R = _module("routes.resale")
    w, db = welt.w, welt.db
    lid = f"l_{w.s}"

    async def lauf():
        await db.resale_listings.insert_one(
            {"id": lid, "dealer_id": w.dealer_id, "vehicle_id": f"v_{w.s}", "status": "entwurf",
             "prices": {"public": 100.0}, "data": {"mileage": 1000}, "created_at": _jetzt()})
        await R.update_listing(lid, R.ListingUpdateIn(price_public=200, data={"mileage": 2000}), w.chef)
        return await db.activity_logs.find_one({"dealer_id": w.dealer_id, "action": "inserat.geaendert"},
                                               {"_id": 0})

    log = welt.run(lauf())
    assert log["ref"] == lid
    assert log["meta"]["preise_alt"] == {"public": 100.0} and log["meta"]["preise_neu"]["public"] == 200.0
    assert log["meta"]["fahrzeugdaten_geaendert"] == ["mileage"]
    assert "data" in log["meta"]["felder"] and "prices.public" in log["meta"]["felder"]


# ================================================= Nr. 7: Termin loeschen mit Audit
def test_07_termin_loeschen_schreibt_audit_mit_vorherigem_stand(welt):
    A = _module("routes.appointments")
    w, db = welt.w, welt.db
    aid = f"a_{w.s}"

    async def lauf():
        await db.appointments.insert_one(w.appt(aid, status="abgeholt", vehicle_id=f"v_{w.s}",
                                                contract_id=f"c_{w.s}", driver_id=w.driver_id))
        await A.delete_appointment(aid, w.chef)
        return await db.activity_logs.find_one({"dealer_id": w.dealer_id, "action": "termin.geloescht"},
                                               {"_id": 0})

    log = welt.run(lauf())
    assert log["ref"] == aid and log["user_id"] == w.chef["id"]
    m = log["meta"]
    assert m["status"] == "abgeholt" and m["vehicle_id"] == f"v_{w.s}"
    assert m["contract_id"] == f"c_{w.s}" and m["driver_id"] == w.driver_id
    assert m["pickup_date"] == "2099-09-15"


def test_07b_sucher_loescht_fremden_termin_weiter_nicht(welt):
    A = _module("routes.appointments")
    w, db = welt.w, welt.db
    aid = f"a_{w.s}"

    async def lauf():
        await db.appointments.insert_one(w.appt(aid))
        with pytest.raises(HTTPException) as e:
            await A.delete_appointment(aid, w.sucher)
        return e.value.status_code, await db.appointments.count_documents({"id": aid})

    status, n = welt.run(lauf())
    assert status == 403 and n == 1


# ================================================= Nr. 8: Abo-Kuendigung und Einladung loeschen mit Audit
def test_08_abo_kuendigung_schreibt_audit(welt, monkeypatch):
    Dl = _module("routes.dealer")
    w, db = welt.w, welt.db
    sid = f"s_{w.s}"

    async def lauf():
        await db.subscriptions.insert_one({"id": sid, "dealer_id": w.dealer_id, "plan": "monthly",
                                           "status": "active", "expires_at": "2099-01-01T00:00:00+00:00",
                                           "subject_user_id": w.chef["id"], "created_at": _jetzt()})

        async def massg(user):
            return await db.subscriptions.find_one({"id": sid}, {"_id": 0})
        monkeypatch.setattr(Dl, "massgebliches_abo", massg)
        r = await Dl.dealer_cancel_subscription(w.chef)
        return r, await db.activity_logs.find_one({"dealer_id": w.dealer_id, "action": "abo.gekuendigt"},
                                                  {"_id": 0})

    r, log = welt.run(lauf())
    assert r["ok"] and log and log["ref"] == sid and log["meta"]["plan"] == "monthly"


def test_08b_einladung_loeschen_schreibt_audit(welt):
    M = _module("routes.marketplace")
    w, db = welt.w, welt.db
    iid = f"i_{w.s}"

    async def lauf():
        await db.dealer_invites.insert_one({"id": iid, "dealer_id": w.dealer_id, "token": f"t_{w.s}",
                                            "expires_at": "2099-01-01T00:00:00+00:00", "max_uses": 1,
                                            "used_count": 0, "created_at": _jetzt()})
        await M.delete_invite(iid, w.chef)
        return await db.activity_logs.find_one({"dealer_id": w.dealer_id, "action": "einladung.geloescht"},
                                               {"_id": 0})

    log = welt.run(lauf())
    assert log and log["ref"] == iid


# ================================================= Runde B Nr. 2/3: Abo-Anfrage nur eigene Firma
def test_b2_abo_anzeige_ignoriert_offene_anfrage_anderer_firma():
    Dl = _module("routes.dealer")
    q = inspect.getsource(Dl.dealer_subscription_status) if hasattr(Dl, "dealer_subscription_status") \
        else inspect.getsource(Dl)
    i = q.index('"type": "sucher_abo", "subject_user_id": user["id"]')
    assert '"dealer_id": user["dealer_id"]' in q[i:i + 200]


def test_b3_abo_anfrage_selbst_aendert_keine_anfrage_anderer_firma(welt):
    T = _module("routes.team")
    w, db = welt.w, welt.db

    async def lauf():
        # Teil-Unique-Index wie server.py (_plan_requests_unique_indizes)
        await db.plan_requests.create_index(
            [("type", 1), ("subject_user_id", 1)], unique=True,
            name="uniq_offene_sucher_abo_anfrage",
            partialFilterExpression={"type": "sucher_abo", "status": "offen"})
        await db.dealers.insert_one({"id": w.dealer_id, "company_name": "Neu GmbH"})
        # Altanfrage unter einer ANDEREN Firma (nur per DB-Eingriff erreichbar)
        await db.plan_requests.insert_one(
            {"id": f"alt_{w.s}", "type": "sucher_abo", "subject_user_id": w.sucher["id"],
             "dealer_id": f"alte_firma_{w.s}", "status": "offen", "wanted_plan": "monthly",
             "price": 1, "created_at": _jetzt()})
        try:
            with pytest.raises(HTTPException) as e:
                await T.eigenes_abo_anfrage({"plan": "yearly"}, w.sucher)
            alt = await db.plan_requests.find_one({"id": f"alt_{w.s}"}, {"_id": 0})
            return e.value.status_code, alt
        finally:
            await db.dealers.delete_many({"id": w.dealer_id})

    status, alt = welt.run(lauf())
    assert status == 409
    assert alt["wanted_plan"] == "monthly" and alt["dealer_id"] == f"alte_firma_{w.s}", \
        "Altanfrage der anderen Firma darf nicht veraendert werden"


# ================================================= Runde B Nr. 4: Netzwerk-Mitglieder ohne N+1
def test_b4_netzwerkliste_eine_users_abfrage_und_limit(welt, monkeypatch):
    M = _module("routes.marketplace")
    w, db = welt.w, welt.db
    zaehler = _abfragen_zaehlen(monkeypatch, ("users", "network_members"))

    async def lauf():
        ids = [f"k_{w.s}_{i}" for i in range(4)]
        await db.users.insert_many([{"id": i, "dealer_id": w.dealer_id, "role": "b2b_buyer",
                                     "company_name": f"K{n}", "email": f"{i}@e2etest-mail.de", "active": True}
                                    for n, i in enumerate(ids)])
        await db.network_members.insert_many([{"dealer_id": w.dealer_id, "buyer_user_id": i,
                                               "created_at": _jetzt()} for i in ids[:3]]
                                             + [{"dealer_id": w.dealer_id, "buyer_user_id": "fehlt_" + w.s,
                                                 "created_at": _jetzt()}])
        zaehler["users"] = 0
        out = await M.list_network_members(w.chef)
        return out, dict(zaehler)

    out, n = welt.run(lauf())
    assert n == {"users": 1, "network_members": 1}, n
    assert len(out) == 4
    fehlend = [o for o in out if o["fehlt"]]
    assert len(fehlend) == 1 and fehlend[0]["active"] is False
    assert "to_list(2000)" in inspect.getsource(M.list_network_members)


# ================================================= Runde B Nr. 6: URL-Laenge
def test_b6_url_modelle_und_identity_begrenzt():
    L = _module("routes.listings")
    ident = _module("listing_identity")
    lang = "https://www.kleinanzeigen.de/s-anzeige/x/" + "a" * 3000 + "-1234567890"
    for modell in (L.CompareIn, L.ListingURLIn):
        with pytest.raises(ValidationError):
            modell(url=lang)
        modell(url="https://www.kleinanzeigen.de/s-anzeige/x/1234567890")
    with pytest.raises(ValidationError):
        L.IngestIn(url=lang, html="x" * 600)
    with pytest.raises(ident.ListingIdentityError) as e:
        ident.get_listing_identity(lang)
    assert "zu lang" in str(e.value) and "aaaa" not in str(e.value), "Meldung zitiert die URL nicht"
    assert ident.get_listing_identity("https://www.kleinanzeigen.de/s-anzeige/x/1234567890")["item_id"]


# ================================================= Runde B Nr. 7: Status-Poll hinter der Bezahlschranke
def test_b7_job_status_verlangt_aktives_abo():
    L = _module("routes.listings")
    sig = inspect.signature(L.listings_check_status)
    dep = sig.parameters["user"].default
    assert getattr(dep, "dependency", None) is L.require_active_sub


# ================================================= Runde C Nr. 5/6: Auto-Termin beim Vertrag
def test_c5_vertragserstellung_ueberlebt_terminfehler():
    C = _module("routes.contracts")
    q = inspect.getsource(C.create_contract)
    i = q.index("_abholtermin_fuer_vertrag")
    assert "try:" in q[i - 200:i] and "except Exception" in q[i:i + 400]
    assert "termin_hinweis" in q
    assert 'contract_id": pdf_id}, {"_id": 0}' not in q, "wirkungslose contract_id-Dublettenpruefung ist weg"


def test_c6_zweiter_vertrag_haengt_offenen_termin_um_statt_zweiten_anzulegen(welt):
    C = _module("routes.contracts")
    w, db = welt.w, welt.db
    vid = f"v_{w.s}"

    class Body:
        vehicle_id = vid
        seller_name = "Neu"; seller_phone = "1"; seller_email = "n@e2etest-mail.de"
        seller_address = "Weg 1"; seller_zip = "30159"; seller_city = "Hannover"
        pickup_date = "2099-10-10"; pickup_time = "11:00"

    async def lauf():
        await db.vehicles.insert_one(w.fahrzeug(vid))
        await db.generated_pdfs.insert_many([
            {"id": f"c1_{w.s}", "dealer_id": w.dealer_id, "user_id": w.chef["id"], "vehicle_id": vid,
             "appointment_id": None, "created_at": _jetzt()},
            {"id": f"c2_{w.s}", "dealer_id": w.dealer_id, "user_id": w.chef["id"], "vehicle_id": vid,
             "appointment_id": None, "created_at": _jetzt()}])
        a1, h1 = await C._abholtermin_fuer_vertrag(w.chef, Body(), {"make_label": "BMW"}, f"c1_{w.s}")
        a2, h2 = await C._abholtermin_fuer_vertrag(w.chef, Body(), {"make_label": "BMW"}, f"c2_{w.s}")
        termine = await db.appointments.find({"dealer_id": w.dealer_id}, {"_id": 0}).to_list(10)
        c1 = await db.generated_pdfs.find_one({"id": f"c1_{w.s}"}, {"_id": 0})
        c2 = await db.generated_pdfs.find_one({"id": f"c2_{w.s}"}, {"_id": 0})
        logs = [l["action"] async for l in db.activity_logs.find({"dealer_id": w.dealer_id})]
        return a1, a2, h1, h2, termine, c1, c2, logs

    a1, a2, h1, h2, termine, c1, c2, logs = welt.run(lauf())
    assert a1 == a2 and h1 is None and h2 is None
    assert len(termine) == 1 and termine[0]["contract_id"] == f"c2_{w.s}"
    assert c1["appointment_id"] is None and c2["appointment_id"] == a1
    assert "termin.auto-erstellt" in logs and "termin.auto-umgehaengt" in logs


def test_c6b_sucher_haengt_kollegen_termin_nicht_um(welt):
    C = _module("routes.contracts")
    w, db = welt.w, welt.db
    vid = f"v_{w.s}"

    class Body:
        vehicle_id = vid
        seller_name = "Neu"; seller_phone = "1"; seller_email = "n@e2etest-mail.de"
        seller_address = ""; seller_zip = ""; seller_city = ""
        pickup_date = "2099-10-10"; pickup_time = ""

    async def lauf():
        await db.vehicles.insert_one(w.fahrzeug(vid))
        await db.appointments.insert_one(w.appt(f"a_{w.s}", vehicle_id=vid, contract_id=f"ck_{w.s}",
                                                created_by=w.sucher["id"]))
        await db.generated_pdfs.insert_one({"id": f"ck_{w.s}", "dealer_id": w.dealer_id,
                                            "user_id": w.sucher["id"], "vehicle_id": vid,
                                            "appointment_id": f"a_{w.s}", "created_at": _jetzt()})
        a, hinweis = await C._abholtermin_fuer_vertrag(w.sucher_b, Body(), {}, f"cb_{w.s}")
        termine = await db.appointments.find({"dealer_id": w.dealer_id}, {"_id": 0}).to_list(10)
        return a, hinweis, termine

    a, hinweis, termine = welt.run(lauf())
    assert a is None and "Kollegen" in hinweis
    assert len(termine) == 1 and termine[0]["contract_id"] == f"ck_{w.s}"


def test_c6c_manueller_zweiter_offener_termin_je_fahrzeug_409(welt):
    A = _module("routes.appointments")
    w, db = welt.w, welt.db
    vid = f"v_{w.s}"

    async def lauf():
        await db.vehicles.insert_one(w.fahrzeug(vid))
        await db.appointments.insert_one(w.appt(f"a1_{w.s}", vehicle_id=vid))
        with pytest.raises(HTTPException) as e:
            await A.create_appointment(A.AppointmentIn(vehicle_id=vid, pickup_date="2099-01-02"), w.chef)
        # abgeschlossener Termin blockiert nicht
        await db.appointments.update_one({"id": f"a1_{w.s}"}, {"$set": {"status": "abgeholt"}})
        r = await A.create_appointment(A.AppointmentIn(vehicle_id=vid, pickup_date="2099-01-02"), w.chef)
        # Wieder-Oeffnen des alten waere ein zweiter offener -> 409
        with pytest.raises(HTTPException) as e2:
            await A.update_appointment(f"a1_{w.s}", A.AppointmentIn(status="offen"), w.chef)
        return e.value.status_code, r, e2.value.status_code

    status, r, status2 = welt.run(lauf())
    assert status == 409 and r["vehicle_id"] == vid and status2 == 409


def test_c6d_teil_unique_index_ist_definiert():
    q = (WURZEL / "backend" / "server.py").read_text(encoding="utf-8")
    assert "termin_offen_je_fahrzeug" in q and "partialFilterExpression" in q
    assert "await _termin_unique_index()" in q
    from pymongo import MongoClient
    info = MongoClient(MONGO_URL, serverSelectionTimeoutMS=5000)[DB_NAME].appointments.index_information()
    if "termin_offen_je_fahrzeug" in info:
        assert info["termin_offen_je_fahrzeug"]["unique"] is True
        assert info["termin_offen_je_fahrzeug"]["partialFilterExpression"]["status"]["$in"]
