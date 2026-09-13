# -*- coding: utf-8 -*-
"""Runde 17 (08.09.2026): Termine, Abholprotokoll, Fahrer — bestaetigte
Befunde aus dem Pruefprotokoll (Nr. 1-13 des Umsetzungsauftrags).

In-Prozess wie Runde 14/15/16: Routen-Funktionen direkt mit Fake-`user`-/
`driver`-Dicts, Modul-`db` zeigt auf einen Test-Client (nur Mongo noetig).
Die Unique-Indizes werden wie in server.py angelegt (CI-Datenbank ist
frisch). Alle Testdaten tragen ein uuid-Suffix und werden aufgeraeumt.
"""
import asyncio
import base64
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

# 1x1-PNG (nur der Magic-Header wird geprueft) fuer Unterschriften.
_PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 64
_PNG_B64 = base64.b64encode(_PNG).decode()


def _jetzt():
    return datetime.now(timezone.utc).isoformat()


class _Welt:
    def __init__(self):
        s = uuid.uuid4().hex[:10]
        self.s = s
        self.dealer_id = f"d_r17_{s}"
        self.chef = {"id": f"chef_r17_{s}", "dealer_id": self.dealer_id, "role": "dealer"}
        self.sucher = {"id": f"su_r17_{s}", "dealer_id": self.dealer_id, "role": "sucher"}
        self.driver_id = f"f_r17_{s}"
        self.driver = {"id": self.driver_id, "display_name": f"Fahrer {s}",
                       "email": f"fahrer-{s}@e2etest-mail.de",
                       "driver_code": "R17" + s.upper()[:7], "active": True}

    def konten(self):
        return [
            {"id": self.chef["id"], "dealer_id": self.dealer_id, "role": "dealer", "active": True,
             "email": f"{self.chef['id']}@e2etest-mail.de", "created_at": _jetzt()},
            {"id": self.sucher["id"], "dealer_id": self.dealer_id, "role": "sucher", "active": True,
             "first_name": "Susi", "last_name": "S", "email": f"{self.sucher['id']}@e2etest-mail.de",
             "created_at": _jetzt()},
        ]

    def fahrzeug(self, vid, owner=None, lifecycle="abholung_geplant", **extra):
        doc = {"id": vid, "dealer_id": self.dealer_id, "lifecycle": lifecycle,
               "mobile_ad_id": vid[2:], "data": {"make_label": "BMW", "model_label": "320d"},
               "status": lifecycle, "created_at": _jetzt(), "updated_at": _jetzt()}
        if owner is not None:
            doc["owner_user_id"] = owner
        doc.update(extra)
        return doc

    def appt(self, aid, **extra):
        doc = {"id": aid, "dealer_id": self.dealer_id, "title": f"Fahrt {aid}", "status": "offen",
               "pickup_date": "2099-09-10", "pickup_time": "10:00", "pickup_address": "Teststr. 1",
               "seller_name": "Verkaeufer", "created_by": self.chef["id"], "created_at": _jetzt()}
        doc.update(extra)
        return doc

    def vertrag(self, cid, user_id=None, **extra):
        doc = {"id": cid, "dealer_id": self.dealer_id, "user_id": user_id or self.chef["id"],
               "pickup_date": "2099-09-10", "pickup_time": "10:00", "version": 1,
               "status": "erstellt", "seller_name": "Vera Verkauf", "seller_phone": "0170 1111111",
               "seller_email": "vera@e2etest-mail.de",
               "contract_data": {"seller_name": "Vera Verkauf", "seller_phone": "0170 1111111",
                                 "seller_email": "vera@e2etest-mail.de"},
               "created_at": _jetzt()}
        doc.update(extra)
        return doc

    def link(self):
        return {"id": str(uuid.uuid4()), "dealer_id": self.dealer_id,
                "driver_account_id": self.driver_id,
                "display_name": self.driver["display_name"], "added_at": _jetzt()}

    async def fahrer_anlegen(self, db, verknuepfen=True):
        await db.driver_accounts.insert_one({**self.driver, "password_hash": "x",
                                             "created_at": _jetzt()})
        if verknuepfen:
            await db.dealer_drivers.insert_one(self.link())

    def entwurf(self, P, appt_id, proto_id, **extra):
        doc = {"id": proto_id, "appointment_id": appt_id, "dealer_id": self.dealer_id,
               "driver_account_id": self.driver_id, "driver_name": self.driver["display_name"],
               # Runde 30 (12.09.2026): Unterschrieben wird erst NACH der Freigabe
               # des Chefs. Diese Tests pruefen den Abschluss selbst, deshalb
               # startet das Protokoll direkt als freigegeben.
               "version": 1, "status": P.FREIGEGEBEN, "superseded": False,
               "vehicle_check": {k: {"status": "stimmt"} for k, _l, _o in P.VEHICLE_CHECK_FIELDS},
               "condition": {"mileage": "123456"}, "keys_count": "2",
               "damages_confirmed": True, "place": "Hannover", "created_at": _jetzt()}
        doc.update(extra)
        return doc

    async def aufraeumen(self, db):
        for c in ("appointments", "vehicles", "activity_logs", "generated_pdfs",
                  "generated_pdf_versions", "users", "pickup_reports", "pickup_protocols",
                  "dealer_drivers", "storage_delete_retry", "kaufvorgaenge"):
            await db[c].delete_many({"dealer_id": self.dealer_id})
        await db.dealers.delete_many({"id": self.dealer_id})
        await db.driver_accounts.delete_many({"id": self.driver_id})
        await db.betriebsalarme.delete_many({"ref": {"$regex": self.s}})
        try:
            import storage_service
            storage_service.storage.delete_prefix(f"protocol/{self.dealer_id}/")
        except Exception:  # noqa: BLE001
            pass


def _module(name):
    import importlib
    return importlib.import_module(name)


async def _indizes(db):
    """Dieselben Unique-Indizes wie server.py — die CI-Datenbank ist frisch."""
    from deps import TERMIN_OFFEN
    # Umbau Kaufvorgaenge 09.09.2026 (indizes.py): EIN offener Termin je
    # VERTRAG statt je Fahrzeug, ein Kaufvorgang je Vertrag. Der alte Index
    # termin_offen_je_fahrzeug widerspricht der neuen Regel und wird wie im
    # Backend entfernt.
    async def _alten_termin_index_entfernen():
        if "termin_offen_je_fahrzeug" in await db.appointments.index_information():
            await db.appointments.drop_index("termin_offen_je_fahrzeug")
    versuche = [
        _alten_termin_index_entfernen,
        lambda: db.appointments.create_index(
            [("dealer_id", 1), ("contract_id", 1)], unique=True, name="termin_offen_je_vertrag",
            partialFilterExpression={"contract_id": {"$type": "string", "$gt": ""},
                                     "status": {"$in": list(TERMIN_OFFEN)}}),
        lambda: db.kaufvorgaenge.create_index("contract_id", unique=True),
        lambda: db.pickup_protocols.create_index(
            "appointment_id", unique=True, partialFilterExpression={"superseded": False},
            name="ein_aktuelles_protokoll_je_termin"),
        lambda: db.pickup_protocols.create_index(
            [("appointment_id", 1), ("version", 1)], unique=True, name="protokollversion_eindeutig"),
        lambda: db.dealer_drivers.create_index(
            [("dealer_id", 1), ("driver_account_id", 1)], unique=True),
        lambda: db.generated_pdfs.create_index([("dealer_id", 1), ("appointment_id", 1)]),
    ]
    for v in versuche:
        try:
            await v()
        except Exception:  # noqa: BLE001  (Index existiert mit anderer Definition)
            pass


@pytest.fixture
def welt():
    from motor.motor_asyncio import AsyncIOMotorClient
    w = _Welt()
    # Umbau Kaufvorgaenge 09.09.2026: `kaufvorgang` haelt ein eigenes `db`
    # (from deps import db) - ohne Umbiegen haengt es am Loop des ersten Tests.
    names = ["deps", "routes.appointments", "routes.contracts", "routes.protocols",
             "routes.drivers", "routes.bestand", "lifecycle", "kaufvorgang"]
    mods = [_module(n) for n in names]
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
    ctx.run(_indizes(ctx.db))
    ctx.run(ctx.db.users.insert_many(w.konten()))
    # user_id gesetzt: dealers.user_id ist unique (nicht sparse) - zwei parallel
    # laufende Testdateien ohne user_id kollidierten auf {user_id: null}.
    ctx.run(ctx.db.dealers.insert_one({"id": w.dealer_id, "user_id": w.chef["id"],
                                       "company_name": "R17 GmbH", "created_at": _jetzt()}))
    yield ctx
    try:
        ctx.run(w.aufraeumen(ctx.db))
    finally:
        for m, d in alt:
            if d is not None:
                m.db = d
        ctx.client.close()
        ctx.loop.close()


def _put(A, appt_id, user, **felder):
    return A.update_appointment(appt_id, A.AppointmentIn(**felder), user)


async def _erwarte(status, coro):
    with pytest.raises(HTTPException) as e:
        await coro
    assert e.value.status_code == status, (e.value.status_code, e.value.detail)
    return e.value


# ================================================= Nr. 1: Betraege
@pytest.mark.parametrize("kw", [{"final_price": -1}, {"extra_costs": -0.01},
                                {"final_price": float("inf")}, {"extra_costs": float("nan")},
                                {"final_price": float("-inf")}])
def test_01_betraege_nicht_negativ_kein_inf_nan(kw):
    A = _module("routes.appointments")
    with pytest.raises(ValidationError):
        A.AppointmentIn(**kw)


def test_01b_betraege_normal_bleiben():
    A = _module("routes.appointments")
    a = A.AppointmentIn(final_price=0, extra_costs=12.5)
    assert a.final_price == 0 and a.extra_costs == 12.5
    assert A.AppointmentIn().final_price is None


# ================================================= Nr. 2: leere IDs / bewusstes Loesen
def test_02_leere_id_strings_werden_none():
    A = _module("routes.appointments")
    a = A.AppointmentIn(vehicle_id="", contract_id="   ", driver_id="\t")
    assert a.vehicle_id is None and a.contract_id is None and a.driver_id is None
    assert "vehicle_id" in a.model_fields_set
    b = A.AppointmentIn(vehicle_id="v_1", contract_id="c_1", driver_id="f_1")
    assert (b.vehicle_id, b.contract_id, b.driver_id) == ("v_1", "c_1", "f_1")
    assert a.contract_loesen is False and a.fahrzeug_loesen is False
    assert "contract_loesen" in inspect.getdoc(A.AppointmentIn)


def test_02b_steuerfelder_landen_nicht_in_der_datenbank(welt):
    A = _module("routes.appointments")
    w, db = welt.w, welt.db

    async def lauf():
        r = await A.create_appointment(A.AppointmentIn(title="x", pickup_date="2099-01-01",
                                                       vehicle_id="", contract_id=""), w.chef)
        return await db.appointments.find_one({"id": r["id"]}, {"_id": 0})

    doc = welt.run(lauf())
    assert "contract_loesen" not in doc and "fahrzeug_loesen" not in doc
    assert "vehicle_id" not in doc and "contract_id" not in doc, "leere IDs sind kein Verweis"


def test_02c_put_loest_vertrag_und_fahrzeug_bewusst(welt):
    A = _module("routes.appointments")
    w, db = welt.w, welt.db
    aid, ca, vid = f"a_{w.s}", f"ca_{w.s}", f"v_{w.s}"

    async def lauf():
        await db.vehicles.insert_one(w.fahrzeug(vid))
        await db.generated_pdfs.insert_one(w.vertrag(ca, appointment_id=aid, status="Termin erstellt"))
        await db.appointments.insert_one(w.appt(aid, contract_id=ca, vehicle_id=vid))
        # PUT mit contract_id "" allein aendert NICHTS (leer = nicht gesendet)
        await _put(A, aid, w.chef, contract_id="")
        unveraendert = await db.appointments.find_one({"id": aid}, {"_id": 0})
        # widerspruechlich: loesen UND neue ID
        await _erwarte(400, _put(A, aid, w.chef, contract_loesen=True, contract_id=f"cx_{w.s}"))
        await _erwarte(400, _put(A, aid, w.chef, fahrzeug_loesen=True, vehicle_id=vid))
        r1 = await _put(A, aid, w.chef, contract_loesen=True)
        nach_vertrag = await db.appointments.find_one({"id": aid}, {"_id": 0})
        alt_vertrag = await db.generated_pdfs.find_one({"id": ca}, {"_id": 0})
        r2 = await _put(A, aid, w.chef, fahrzeug_loesen=True)
        nach_fahrzeug = await db.appointments.find_one({"id": aid}, {"_id": 0})
        fahrzeug = await db.vehicles.find_one({"id": vid}, {"_id": 0, "lifecycle": 1})
        # nochmal loesen: idempotent, kein Fehler
        r3 = await _put(A, aid, w.chef, contract_loesen=True, fahrzeug_loesen=True)
        logs = [l["meta"] async for l in db.activity_logs.find(
            {"dealer_id": w.dealer_id, "action": "termin.aktualisiert"}, {"_id": 0})]
        return unveraendert, r1, nach_vertrag, alt_vertrag, r2, nach_fahrzeug, fahrzeug, r3, logs

    unv, r1, nv, alt, r2, nf, fz, r3, logs = welt.run(lauf())
    assert unv["contract_id"] == ca and unv["vehicle_id"] == vid
    assert r1["ok"] and "contract_id" not in nv and nv["vehicle_id"] == vid
    assert alt["appointment_id"] is None and alt["status"] == "erstellt", "alter Vertrag freigegeben"
    assert r2["ok"] and "vehicle_id" not in nf and "contract_id" not in nf
    assert fz["lifecycle"] == "abholung_geplant", "Loesen aendert den Lebenszyklus nicht"
    assert r3["ok"] and "hinweis" not in r3
    assert any(m.get("contract_geloest") == ca for m in logs)
    assert any(m.get("fahrzeug_geloest") == vid for m in logs)


def test_02d_sucher_loest_auf_abgeschlossenem_termin_nicht(welt):
    A = _module("routes.appointments")
    w, db = welt.w, welt.db
    aid, ca = f"a_{w.s}", f"ca_{w.s}"

    async def lauf():
        await db.generated_pdfs.insert_one(w.vertrag(ca, user_id=w.sucher["id"], appointment_id=aid))
        await db.appointments.insert_one(w.appt(aid, contract_id=ca, status="storniert",
                                                created_by=w.sucher["id"]))
        await _erwarte(403, _put(A, aid, w.sucher, contract_loesen=True))
        r = await _put(A, aid, w.chef, contract_loesen=True)
        return r, await db.appointments.find_one({"id": aid}, {"_id": 0})

    r, a = welt.run(lauf())
    assert r["ok"] and "contract_id" not in a


# ================================================= Nr. 3: Vertragszeiger idempotent
def test_03_vertragszeiger_werden_bei_jedem_put_abgeglichen(welt):
    A = _module("routes.appointments")
    w, db = welt.w, welt.db
    aid, ca, cb, cc = f"a_{w.s}", f"ca_{w.s}", f"cb_{w.s}", f"cc_{w.s}"

    async def lauf():
        await db.generated_pdfs.insert_many([
            w.vertrag(ca, appointment_id=aid, status="Termin erstellt"),   # haengt noch am Termin
            w.vertrag(cc, appointment_id=aid, status="gesendet"),          # haengt noch, aber gesendet
            w.vertrag(cb, appointment_id=None),                            # der wirkliche Vertrag
        ])
        await db.appointments.insert_one(w.appt(aid, contract_id=cb))
        await _put(A, aid, w.chef, notes="nur Notiz")          # KEIN Vertragswechsel
        nach1 = {c["id"]: c async for c in db.generated_pdfs.find({"dealer_id": w.dealer_id}, {"_id": 0})}
        await _put(A, aid, w.chef, notes="noch eine Notiz")    # idempotent
        nach2 = {c["id"]: c async for c in db.generated_pdfs.find({"dealer_id": w.dealer_id}, {"_id": 0})}
        # Termin ohne Vertrag: haengende Verweise werden trotzdem geloest
        await db.generated_pdfs.update_one({"id": ca}, {"$set": {"appointment_id": aid,
                                                                  "status": "Termin erstellt"}})
        await _put(A, aid, w.chef, contract_loesen=True)
        nach3 = {c["id"]: c async for c in db.generated_pdfs.find({"dealer_id": w.dealer_id}, {"_id": 0})}
        return nach1, nach2, nach3

    n1, n2, n3 = welt.run(lauf())
    assert n1[ca]["appointment_id"] is None and n1[ca]["status"] == "erstellt"
    assert n1[cc]["appointment_id"] is None and n1[cc]["status"] == "gesendet", "gesendet bleibt"
    assert n1[cb]["appointment_id"] == aid and n1[cb]["status"] == "Termin erstellt"
    assert n2 == n1, "zweiter PUT aendert nichts mehr"
    assert n3[ca]["appointment_id"] is None and n3[ca]["status"] == "erstellt"
    assert n3[cb]["appointment_id"] is None and n3[cb]["status"] == "erstellt"


def test_03b_vertragswechsel_wie_runde_14(welt):
    A = _module("routes.appointments")
    w, db = welt.w, welt.db
    aid, ca, cb = f"a_{w.s}", f"ca_{w.s}", f"cb_{w.s}"

    async def lauf():
        await db.generated_pdfs.insert_many([w.vertrag(ca, appointment_id=aid, status="Termin erstellt"),
                                             w.vertrag(cb)])
        await db.appointments.insert_one(w.appt(aid, contract_id=ca))
        await _put(A, aid, w.chef, contract_id=cb)
        return ({c["id"]: c async for c in db.generated_pdfs.find({"dealer_id": w.dealer_id}, {"_id": 0})},
                await db.appointments.find_one({"id": aid}, {"_id": 0, "contract_id": 1}))

    c, a = welt.run(lauf())
    assert a["contract_id"] == cb
    assert c[ca]["appointment_id"] is None and c[ca]["status"] == "erstellt"
    assert c[cb]["appointment_id"] == aid and c[cb]["status"] == "Termin erstellt"


# ================================================= Nr. 4: Vertrag veraltet
class _Rekorder:
    def __init__(self, ergebnis=True):
        self.aufrufe = []
        self.ergebnis = ergebnis

    async def __call__(self, **kw):
        self.aufrufe.append(kw)
        return self.ergebnis


def test_04_vertrag_veraltet_wird_gemerkt_und_nachgeholt(welt, monkeypatch):
    A, C = _module("routes.appointments"), _module("routes.contracts")
    w, db = welt.w, welt.db
    aid, ca = f"a_{w.s}", f"ca_{w.s}"
    rek = _Rekorder(ergebnis=False)
    monkeypatch.setattr(C, "regenerate_contract_for_pickup", rek)

    async def lauf():
        await db.generated_pdfs.insert_one(w.vertrag(ca, appointment_id=aid, status="Termin erstellt"))
        await db.appointments.insert_one(w.appt(aid, contract_id=ca))
        # 1) Datum verschoben, Neuerzeugung scheitert -> Merker + Hinweis
        r1 = await _put(A, aid, w.chef, pickup_date="2099-09-20")
        a1 = await db.appointments.find_one({"id": aid}, {"_id": 0})
        # 2) naechster PUT ohne Datumsaenderung holt die Neuerzeugung nach
        rek.ergebnis = True
        r2 = await _put(A, aid, w.chef, notes="nur Notiz")
        a2 = await db.appointments.find_one({"id": aid}, {"_id": 0})
        # 3) unveraendertes Datum ohne Merker: kein Aufruf, kein Hinweis
        n_vor = len(rek.aufrufe)
        r3 = await _put(A, aid, w.chef, pickup_date="2099-09-20")
        n = (n_vor, len(rek.aufrufe))
        # 4) False, aber der Vertrag zeigt den Termin bereits: KEIN Merker
        rek.ergebnis = False
        await db.generated_pdfs.update_one({"id": ca}, {"$set": {"pickup_date": "2099-09-25",
                                                                  "pickup_time": "10:00"}})
        r4 = await _put(A, aid, w.chef, pickup_date="2099-09-25")
        a4 = await db.appointments.find_one({"id": aid}, {"_id": 0})
        logs = [l["meta"] async for l in db.activity_logs.find(
            {"dealer_id": w.dealer_id, "action": "termin.aktualisiert"}, {"_id": 0})]
        return r1, a1, r2, a2, n, r3, r4, a4, logs

    r1, a1, r2, a2, (n_vor, n_nach), r3, r4, a4, logs = welt.run(lauf())
    assert r1["pickup_date_changed"] is True and r1["contract_updated"] is False
    assert r1["hinweis"] == A.VERTRAG_VERALTET_HINWEIS
    assert a1["vertrag_veraltet"] is True and a1["pickup_date"] == "2099-09-20"
    assert len(rek.aufrufe) >= 2
    assert rek.aufrufe[1]["contract_id"] == ca
    assert rek.aufrufe[1]["pickup_date"] == "2099-09-20" and rek.aufrufe[1]["pickup_time"] == "10:00"
    assert r2["contract_updated"] is True and "hinweis" not in r2
    assert "vertrag_veraltet" not in a2, "Merker nach erfolgreicher Neuerzeugung weg"
    assert n_nach == n_vor and "hinweis" not in r3
    assert r4["contract_updated"] is False and "hinweis" not in r4
    assert "vertrag_veraltet" not in a4, "Vertrag zeigt den Termin bereits — nicht veraltet"
    assert any(m.get("vertrag_veraltet") is True for m in logs)


# ================================================= Nr. 5: Zusage zuruecksetzen (Helfer)
def test_05_helfer_zusage_zuruecksetzen():
    A = _module("routes.appointments")
    sig = inspect.signature(A.zusage_zuruecksetzen_wenn_geaendert)
    assert list(sig.parameters) == ["existing", "neu"]
    alt = {"zuteilung": "angenommen", "pickup_date": "2099-09-10", "pickup_time": "10:00",
           "pickup_address": "Teststr. 1"}
    s, u = A.zusage_zuruecksetzen_wenn_geaendert(alt, {"pickup_time": "17:45"})
    assert s["zuteilung"] == "offen" and s["zuteilung_neu_wegen_aenderung"] is True and s["zuteilung_am"]
    assert u == {"zuteilung_beantwortet_am": ""}
    assert A.zusage_zuruecksetzen_wenn_geaendert(alt, {"pickup_time": "10:00", "notes": "x"}) == ({}, {})
    assert A.zusage_zuruecksetzen_wenn_geaendert(alt, {"notes": "x"}) == ({}, {})
    assert A.zusage_zuruecksetzen_wenn_geaendert({**alt, "zuteilung": "offen"},
                                                 {"pickup_date": "2099-12-24"}) == ({}, {})
    # leer und fehlend gelten als gleich
    assert A.zusage_zuruecksetzen_wenn_geaendert({**alt, "pickup_address": None},
                                                 {"pickup_address": ""}) == ({}, {})
    s2, _ = A.zusage_zuruecksetzen_wenn_geaendert(alt, {"pickup_address": "Neue Str. 9"})
    assert s2["zuteilung"] == "offen"


def test_05b_put_nutzt_den_helfer_auch_mit_unveraendertem_fahrer(welt):
    A = _module("routes.appointments")
    w, db = welt.w, welt.db
    aid = f"a_{w.s}"

    async def lauf():
        await w.fahrer_anlegen(db)
        await db.appointments.insert_one(w.appt(aid, driver_id=w.driver_id, zuteilung="angenommen",
                                                zuteilung_beantwortet_am="2026-09-01T00:00:00+00:00"))
        # Oberflaeche sendet das ganze Objekt inkl. (gleichem) Fahrer
        await _put(A, aid, w.chef, driver_id=w.driver_id, pickup_time="17:45")
        a1 = await db.appointments.find_one({"id": aid}, {"_id": 0})
        await db.appointments.update_one({"id": aid}, {"$set": {"zuteilung": "angenommen"}})
        await _put(A, aid, w.chef, notes="unwesentlich")
        a2 = await db.appointments.find_one({"id": aid}, {"_id": 0})
        return a1, a2

    a1, a2 = welt.run(lauf())
    assert a1["pickup_time"] == "17:45" and a1["zuteilung"] == "offen"
    assert a1["zuteilung_neu_wegen_aenderung"] is True
    assert "zuteilung_beantwortet_am" not in a1, "alte Antwort ist geloescht"
    assert a2["zuteilung"] == "angenommen", "Notiz hebt keine Zusage auf"
    q = inspect.getsource(A.update_appointment)
    assert "zusage_zuruecksetzen_wenn_geaendert(existing, update)" in q


# ================================================= Nr. 6: Fahrer hinzufuegen/entfernen
def test_06_fahrer_add_und_delete_haben_audit(welt):
    D = _module("routes.drivers")
    w, db = welt.w, welt.db
    a_offen, a_fertig = f"ao_{w.s}", f"af_{w.s}"

    async def lauf():
        await w.fahrer_anlegen(db, verknuepfen=False)
        r_add = await D.add_driver_by_code(D.DriverLinkIn(driver_code=w.driver["driver_code"]), w.chef)
        log_add = await db.activity_logs.find_one({"dealer_id": w.dealer_id, "action": "fahrer.hinzugefuegt"},
                                                  {"_id": 0})
        await db.appointments.insert_many([
            w.appt(a_offen, driver_id=w.driver_id, zuteilung="offen"),
            w.appt(a_fertig, driver_id=w.driver_id, status="abgeholt")])
        r_del = await D.delete_driver(w.driver_id, w.chef)
        log_del = await db.activity_logs.find_one({"dealer_id": w.dealer_id, "action": "fahrer.entfernt"},
                                                  {"_id": 0})
        offen = await db.appointments.find_one({"id": a_offen}, {"_id": 0})
        fertig = await db.appointments.find_one({"id": a_fertig}, {"_id": 0})
        return r_add, log_add, r_del, log_del, offen, fertig

    r_add, log_add, r_del, log_del, offen, fertig = welt.run(lauf())
    assert r_add.get("id") == w.driver_id
    assert log_add["ref"] == w.driver_id and log_add["user_id"] == w.chef["id"]
    assert log_add["meta"] == {}, "keine Namen/Codes im Audit"
    assert r_del == {"ok": True, "offene_termine_getrennt": 1, "abgeschlossene_termine_archiviert": 1}
    assert log_del["ref"] == w.driver_id
    assert log_del["meta"] == {"offene_termine_getrennt": 1, "abgeschlossene_termine_archiviert": 1}
    assert "driver_id" not in offen and offen["zuteilung"] is None
    assert "driver_id" not in fertig and fertig["driver_id_hist"] == w.driver_id


def test_06b_delete_ueberlebt_fehlgeschlagene_bereinigung_mit_alarm(welt, monkeypatch):
    D = _module("routes.drivers")
    from motor.motor_asyncio import AsyncIOMotorCollection as K
    w, db = welt.w, welt.db
    alt = K.update_many

    async def kaputt(self, *a, **k):
        if self.name == "appointments":
            raise RuntimeError("Mongo weg")
        return await alt(self, *a, **k)
    monkeypatch.setattr(K, "update_many", kaputt)

    async def lauf():
        await w.fahrer_anlegen(db)
        await db.appointments.insert_one(w.appt(f"a_{w.s}", driver_id=w.driver_id))
        r = await D.delete_driver(w.driver_id, w.chef)
        link = await db.dealer_drivers.find_one({"dealer_id": w.dealer_id, "driver_account_id": w.driver_id})
        alarm = await db.betriebsalarme.find_one({"typ": "fahrer_bereinigung_fehlgeschlagen",
                                                  "ref": w.driver_id}, {"_id": 0})
        log = await db.activity_logs.find_one({"dealer_id": w.dealer_id, "action": "fahrer.entfernt"},
                                              {"_id": 0})
        return r, link, alarm, log

    r, link, alarm, log = welt.run(lauf())
    assert r["ok"] is True and "hinweis" in r
    assert r["offene_termine_getrennt"] == 0 and r["abgeschlossene_termine_archiviert"] == 0
    assert link is None, "Verknuepfung ist trotzdem weg (Reihenfolge: Link zuerst)"
    assert alarm and alarm["offen"] is True and alarm["details"]["dealer_id"] == w.dealer_id
    assert "Mongo weg" in alarm["details"]["fehler"]
    assert log and "Mongo weg" in log["meta"]["bereinigung_fehler"]


# ================================================= Nr. 7 / 8: Protokoll-Abschluss als CAS
def _pdf_fake(merker):
    def build(**kw):
        merker.append(kw)
        return b"%PDF-1.4\n%r17-test\n"
    return build


def test_07_finalize_setzt_termin_nur_wenn_noch_offen(welt, monkeypatch):
    P = _module("routes.protocols")
    import pickup_pdf_service
    import storage_service
    w, db = welt.w, welt.db
    a1, v1, p1 = f"a1_{w.s}", f"v1_{w.s}", f"p1_{w.s}"
    a2, v2, p2 = f"a2_{w.s}", f"v2_{w.s}", f"p2_{w.s}"
    gebaut = []
    monkeypatch.setattr(pickup_pdf_service, "build_pickup_pdf", _pdf_fake(gebaut))
    alt_save = storage_service.save_async
    fin = P.FinalizeIn(signature_driver_b64=_PNG_B64, signature_seller_b64=_PNG_B64)

    async def lauf():
        await w.fahrer_anlegen(db)
        await db.vehicles.insert_many([w.fahrzeug(v1), w.fahrzeug(v2)])
        await db.appointments.insert_many([w.appt(a1, driver_id=w.driver_id, vehicle_id=v1),
                                           w.appt(a2, driver_id=w.driver_id, vehicle_id=v2)])
        await db.pickup_protocols.insert_many([w.entwurf(P, a1, p1), w.entwurf(P, a2, p2)])

        # a) Rennen: der Haendler storniert den Termin, waehrend das PDF geschrieben wird
        async def save_und_stornieren(key, data):
            await db.appointments.update_one({"id": a1}, {"$set": {"status": "storniert"}})
            return await alt_save(key, data)
        monkeypatch.setattr(storage_service, "save_async", save_und_stornieren)
        r1 = await P.finalize_protocol(a1, fin, w.driver)
        t1 = await db.appointments.find_one({"id": a1}, {"_id": 0})
        pr1 = await db.pickup_protocols.find_one({"id": p1}, {"_id": 0})
        fz1 = await db.vehicles.find_one({"id": v1}, {"_id": 0, "lifecycle": 1})
        alarm = await db.betriebsalarme.find_one({"typ": "protokoll_final_termin_geschlossen", "ref": a1},
                                                 {"_id": 0})
        # b) Normalfall
        monkeypatch.setattr(storage_service, "save_async", alt_save)
        r2 = await P.finalize_protocol(a2, fin, w.driver)
        t2 = await db.appointments.find_one({"id": a2}, {"_id": 0})
        fz2 = await db.vehicles.find_one({"id": v2}, {"_id": 0, "lifecycle": 1})
        return r1, t1, pr1, fz1, alarm, r2, t2, fz2

    r1, t1, pr1, fz1, alarm, r2, t2, fz2 = welt.run(lauf())
    assert r1["ok"] is True and r1["protocol_id"] == p1
    assert r1["hinweis"] == P.TERMIN_GESCHLOSSEN_HINWEIS
    assert pr1["status"] == "final" and pr1["pdf_path"], "Protokoll bleibt als Beweis gespeichert"
    assert t1["status"] == "storniert" and "protocol_id" not in t1, "geschlossener Termin unangetastet"
    assert fz1["lifecycle"] == "abholung_geplant", "kein Lebenszyklus-Sprung"
    assert alarm and alarm["offen"] is True and alarm["details"]["driver_id"] == w.driver_id
    assert "hinweis" not in r2 and r2["protocol_id"] == p2
    assert t2["status"] == "abgeholt" and t2["protocol_id"] == p2 and t2["abgeschlossen_seit"]
    assert fz2["lifecycle"] == "abgeholt"
    # Nr. 8: die Rohbytes aus _save_sig wandern ins PDF (keine zweite Dekodierung)
    assert gebaut and gebaut[-1]["filled"]["signature_driver"] == _PNG
    assert gebaut[-1]["filled"]["signature_seller"] == _PNG


def test_07b_finalize_prueft_verknuepfung_vor_dem_finalen_write(welt, monkeypatch):
    P = _module("routes.protocols")
    import pickup_pdf_service
    import storage_service
    w, db = welt.w, welt.db
    aid, pid = f"a_{w.s}", f"p_{w.s}"
    monkeypatch.setattr(pickup_pdf_service, "build_pickup_pdf", _pdf_fake([]))
    alt_save, alt_make_key = storage_service.save_async, storage_service.make_key
    keys = []

    def make_key_merken(*a, **k):
        key = alt_make_key(*a, **k)
        keys.append(key)
        return key
    monkeypatch.setattr(storage_service, "make_key", make_key_merken)

    async def save_und_fahrer_entfernen(key, data):
        await db.dealer_drivers.delete_many({"dealer_id": w.dealer_id, "driver_account_id": w.driver_id})
        return await alt_save(key, data)
    monkeypatch.setattr(storage_service, "save_async", save_und_fahrer_entfernen)
    fin = P.FinalizeIn(signature_driver_b64=_PNG_B64, signature_seller_b64=_PNG_B64)

    async def lauf():
        await w.fahrer_anlegen(db)
        await db.appointments.insert_one(w.appt(aid, driver_id=w.driver_id))
        await db.pickup_protocols.insert_one(w.entwurf(P, aid, pid))
        await _erwarte(404, P.finalize_protocol(aid, fin, w.driver))
        return (await db.pickup_protocols.find_one({"id": pid}, {"_id": 0}),
                await db.appointments.find_one({"id": aid}, {"_id": 0}))

    proto, appt = welt.run(lauf())
    # Runde 30: Ein gescheiterter Abschluss faellt auf FREIGEGEBEN zurueck —
    # sonst waere die Freigabe des Chefs weg und der Fahrer muesste vor Ort
    # erneut auf ihn warten.
    assert proto["status"] == P.FREIGEGEBEN and "claim_bis" not in proto and "pdf_path" not in proto
    assert appt["status"] == "offen" and "protocol_id" not in appt
    assert len(keys) == 3, keys
    assert all(not storage_service.storage.exists(k) for k in keys), "Rollback raeumt Dateien auf"


def test_07c_selbstheilung_ebenfalls_als_cas(welt, monkeypatch):
    P = _module("routes.protocols")
    w, db = welt.w, welt.db
    aid, vid, pid = f"a_{w.s}", f"v_{w.s}", f"p_{w.s}"
    alt_current = P._current

    async def current_und_schliessen(appt_id):
        doc = await alt_current(appt_id)
        await db.appointments.update_one({"id": appt_id}, {"$set": {"status": "nicht abgeholt"}})
        return doc
    monkeypatch.setattr(P, "_current", current_und_schliessen)
    fin = P.FinalizeIn(signature_driver_b64=_PNG_B64, signature_seller_b64=_PNG_B64)

    async def lauf():
        await w.fahrer_anlegen(db)
        await db.vehicles.insert_one(w.fahrzeug(vid))
        await db.appointments.insert_one(w.appt(aid, driver_id=w.driver_id, vehicle_id=vid))
        await db.pickup_protocols.insert_one(w.entwurf(P, aid, pid, status="final",
                                                       pdf_path=f"protocol/{w.dealer_id}/x.pdf"))
        r = await P.finalize_protocol(aid, fin, w.driver)
        return (r, await db.appointments.find_one({"id": aid}, {"_id": 0}),
                await db.vehicles.find_one({"id": vid}, {"_id": 0, "lifecycle": 1}),
                await db.betriebsalarme.find_one({"typ": "protokoll_final_termin_geschlossen", "ref": aid}))

    r, appt, fz, alarm = welt.run(lauf())
    assert r["ok"] and r["nachgezogen"] is True and r["hinweis"] == P.TERMIN_GESCHLOSSEN_HINWEIS
    assert appt["status"] == "nicht abgeholt" and "protocol_id" not in appt
    assert fz["lifecycle"] == "abholung_geplant" and alarm is not None


# ================================================= Nr. 8: Unterschriften-Obergrenze
def test_08_signatur_b64_gedeckelt_und_einmal_dekodiert():
    P = _module("routes.protocols")
    with pytest.raises(ValidationError):
        P.FinalizeIn(signature_driver_b64="a" * 3_000_001, signature_seller_b64=_PNG_B64)
    with pytest.raises(ValidationError):
        P.FinalizeIn(signature_driver_b64=_PNG_B64, signature_seller_b64="b" * 3_000_001)
    assert P.FinalizeIn(signature_driver_b64=_PNG_B64, signature_seller_b64=_PNG_B64)
    q = inspect.getsource(P.finalize_protocol)
    assert q.count("b64decode(") == 1, "nur EINE Dekodierung (in _save_sig)"
    assert "return key, raw" in q


# ================================================= Nr. 9: documents/features gedeckelt
@pytest.mark.parametrize("feld", ["documents", "features", "vehicle_check", "condition"])
def test_09_dict_felder_gedeckelt(feld):
    P = _module("routes.protocols")
    with pytest.raises(ValidationError):
        P.ProtocolIn(**{feld: {f"k{i}": True for i in range(61)}})
    with pytest.raises(ValidationError):
        P.ProtocolIn(**{feld: {"x" * 81: True}})
    with pytest.raises(ValidationError):
        P.ProtocolIn(**{feld: "kein dict"})
    ok = P.ProtocolIn(**{feld: {"Fahrzeugschein": True}})
    assert getattr(ok, feld) == {"Fahrzeugschein": True}


# ================================================= Nr. 10: new_damages typisiert
def test_10_new_damages_typisiert(welt):
    P = _module("routes.protocols")
    from routes.contracts import DamageIn
    w, db = welt.w, welt.db
    with pytest.raises(ValidationError):
        P.ProtocolIn(new_damages=["Kratzer"])
    with pytest.raises(ValidationError):
        P.ProtocolIn(new_damages=[{"zone": "x"}] * 41)
    with pytest.raises(ValidationError):
        P.ProtocolIn(new_damages=[{"zone": "z" * 501}])
    p = P.ProtocolIn(new_damages=[{"view": "front", "zone": "tuer_vl", "x": 10, "y": 20,
                                   "type_label": "Kratzer", "unbekannt": "weg"}])
    assert isinstance(p.new_damages[0], DamageIn)
    aid = f"a_{w.s}"

    async def lauf():
        await w.fahrer_anlegen(db)
        await db.appointments.insert_one(w.appt(aid, driver_id=w.driver_id))
        await P.save_protocol(aid, p, w.driver)
        return await db.pickup_protocols.find_one({"appointment_id": aid}, {"_id": 0, "new_damages": 1})

    doc = welt.run(lauf())
    assert doc["new_damages"] == [{"view": "front", "zone": "tuer_vl", "x": 10.0, "y": 20.0,
                                   "type_label": "Kratzer", "type_key": ""}]


# ================================================= Nr. 11: Korrektur verwerfen
def test_11_termin_schliessen_verwirft_korrektur_entwurf(welt):
    A, P = _module("routes.appointments"), _module("routes.protocols")
    w, db = welt.w, welt.db
    aid, p1, p2 = f"a_{w.s}", f"p1_{w.s}", f"p2_{w.s}"

    async def lauf():
        await w.fahrer_anlegen(db)
        await db.appointments.insert_one(w.appt(aid, driver_id=w.driver_id))
        await db.pickup_protocols.insert_many([
            w.entwurf(P, aid, p1, status="final", superseded=True, superseded_at=_jetzt(),
                      pdf_path=f"protocol/{w.dealer_id}/v1.pdf"),
            # Runde 30: bewusst ein ENTWURF — genau den verwirft das Schliessen.
            w.entwurf(P, aid, p2, version=2, corrects_version=1, status="entwurf"),
        ])
        assert await P.korrektur_verwerfen(f"ohne_{w.s}") is False
        r = await _put(A, aid, w.chef, status="storniert")
        v1 = await db.pickup_protocols.find_one({"id": p1}, {"_id": 0})
        v2 = await db.pickup_protocols.find_one({"id": p2}, {"_id": 0})
        nochmal = await P.korrektur_verwerfen(aid)
        aktuell = await P._current(aid)
        return r, v1, v2, nochmal, aktuell

    r, v1, v2, nochmal, aktuell = welt.run(lauf())
    assert r["ok"]
    assert v2["superseded"] is True and v2["verworfen_am"]
    assert v1["superseded"] is False and "superseded_at" not in v1, "korrigierte Version wieder aktuell"
    assert nochmal is False and aktuell["id"] == p1
    q = inspect.getsource(A.update_appointment)
    assert "korrektur_verwerfen(appt_id)" in q


# ================================================= Nr. 12: Verkaeuferdaten aus dem Vertrag
def test_12_post_fuellt_leere_verkaeuferfelder_aus_dem_vertrag(welt):
    """Umbau Kaufvorgaenge 09.09.2026: der Termin traegt zusaetzlich den
    Kaufvorgang seines Vertrags (kaufvorgang_id); der Vorgang zeigt auf den
    Termin und steht auf 'abholung_geplant', der Fahrzeug-Lebenszyklus ist
    nur die Zusammenfassung. Altvertraege ohne Vorgang bekommen weiterhin
    ihren Termin (ohne kaufvorgang_id)."""
    A = _module("routes.appointments")
    w, db = welt.w, welt.db
    ca, cb, vid, kv_id = f"ca_{w.s}", f"cb_{w.s}", f"v_{w.s}", f"kv_{w.s}"

    async def lauf():
        await db.vehicles.insert_one(w.fahrzeug(vid, lifecycle="gekauft"))
        await db.generated_pdfs.insert_many([
            w.vertrag(ca, vehicle_id=vid, kaufvorgang_id=kv_id),
            # Altvertrag: nur contract_data traegt die Verkaeuferdaten, kein Vorgang
            w.vertrag(cb, seller_name=None, seller_phone=None, seller_email=None,
                      contract_data={"seller_name": "Alt Anbieter", "seller_email": "alt@e2etest-mail.de"}),
        ])
        await db.kaufvorgaenge.insert_one({
            "id": kv_id, "dealer_id": w.dealer_id, "user_id": w.chef["id"], "vehicle_id": vid,
            "contract_id": ca, "purchase_price": 4500, "status": "vertrag_erstellt",
            "appointment_id": None, "created_at": _jetzt(), "updated_at": _jetzt()})
        r1 = await A.create_appointment(A.AppointmentIn(contract_id=ca, pickup_date="2099-01-01",
                                                        seller_phone="0151 anders"), w.chef)
        a1 = await db.appointments.find_one({"id": r1["id"]}, {"_id": 0})
        c1 = await db.generated_pdfs.find_one({"id": ca}, {"_id": 0})
        kv = await db.kaufvorgaenge.find_one({"id": kv_id}, {"_id": 0})
        v = await db.vehicles.find_one({"id": vid}, {"_id": 0})
        r2 = await A.create_appointment(A.AppointmentIn(contract_id=cb, pickup_date="2099-01-02"), w.chef)
        a2 = await db.appointments.find_one({"id": r2["id"]}, {"_id": 0})
        r3 = await A.create_appointment(A.AppointmentIn(pickup_date="2099-01-03"), w.chef)
        return r1, a1, c1, kv, v, a2, r3

    r1, a1, c1, kv, v, a2, r3 = welt.run(lauf())
    assert a1["seller_name"] == "Vera Verkauf" and a1["seller_email"] == "vera@e2etest-mail.de"
    assert a1["seller_phone"] == "0151 anders", "abweichender Wert bleibt (kein 4xx)"
    assert r1["seller_name"] == "Vera Verkauf"
    assert c1["appointment_id"] == r1["id"] and c1["status"] == "Termin erstellt"
    # Umbau Kaufvorgaenge: Termin <-> Vorgang verknuepft, Fahrzeugstatus zusammengefasst
    assert a1["kaufvorgang_id"] == kv_id and r1["kaufvorgang_id"] == kv_id
    assert kv["appointment_id"] == r1["id"] and kv["status"] == "abholung_geplant"
    assert kv["purchase_price"] == 4500 and v.get("purchase_price") is None, "Kaufpreis bleibt am Vorgang"
    assert v["lifecycle"] == "abholung_geplant"
    assert a2["seller_name"] == "Alt Anbieter" and a2["seller_email"] == "alt@e2etest-mail.de"
    assert a2.get("seller_phone", "") == "" and "kaufvorgang_id" not in a2, "Altvertrag ohne Vorgang"
    assert r3.get("seller_name", "") == "", "ohne Vertrag bleibt alles leer"


# ================================================= Nr. 13: zentrale Datum-/Zeitpruefung
@pytest.mark.parametrize("kw", [{"pickup_date": "01.09.2026"}, {"pickup_date": "2026-02-30"},
                                {"pickup_time": "25:00"}, {"pickup_time": "9:00"}])
def test_13_datum_und_zeit_ueber_deps(kw):
    A = _module("routes.appointments")
    with pytest.raises(ValidationError):
        A.AppointmentIn(**kw)
    a = A.AppointmentIn(pickup_date="  ", pickup_time="", notes="x")
    assert a.pickup_date == "" and a.pickup_time == ""
    b = A.AppointmentIn(pickup_date=" 2026-09-10 ", pickup_time="23:59")
    assert b.pickup_date == "2026-09-10" and b.pickup_time == "23:59"
    assert A.AppointmentIn(pickup_date=None).pickup_date is None
    q = inspect.getsource(A.AppointmentIn)
    assert "datum_iso_pruefen(v)" in q and "uhrzeit_hhmm_pruefen(v)" in q
    assert not hasattr(A, "_ISO_DATUM") and not hasattr(A, "_UHRZEIT"), "keine eigene Kopie mehr"
