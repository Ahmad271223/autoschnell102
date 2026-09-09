# -*- coding: utf-8 -*-
"""Runde 17 (08.09.2026): bestaetigte Audit-Befunde zu routes/contracts.py.

  338      DamageIn.x/y und purchase_price ohne inf/nan, Koordinaten gedeckelt
  346/347  pickup_date/pickup_time geprueft; PLZ/Ort/Abholadresse gedeckelt
  265      Vertrag bleibt gueltig, wenn Fahrzeugstatus/Kaufvorgang/Audit
           danach scheitern (Nacharbeit-Hinweis statt 500)
  270      409 fuer verkaufte/archivierte Fahrzeuge (kein zweiter Kaufvertrag)
  355/356  Umbau Kaufvorgaenge 09.09.2026: EIN offener Termin je VERTRAG -
           ein zweiter Vertrag zum selben Fahrzeug bekommt einen EIGENEN
           Termin; der Termin des anderen Vertrags bleibt samt Fahrer-Zusage
           und Vertragszeiger unberuehrt (kein Umhaengen mehr)
  348      Grabstein-Vertraege (loeschung.status == laeuft) sind unsichtbar,
           Loeschen bleibt idempotent
  349/374  list_contracts: q hoechstens 200 Zeichen, days 1..3650
  321      pickup_history auf die juengsten 100 Eintraege gedeckelt
  370      send: 409, wenn die Loeschung zwischen Lesen und Versand begann
  372      send: Vermerk nicht gespeichert -> status_vermerk + Audit-Eintrag
  373      regenerate: Compare-and-Swap auf die gelesene Version

In-Prozess wie Runde 14-16: Routen-Funktionen direkt mit Fake-`user`-Dicts,
Modul-`db` zeigt auf einen Test-Client (nur Mongo noetig). ReportLab wird
durch einen Stub ersetzt, auto_daten ebenfalls (anonyme Datensaetze haetten
keinen Aufraeum-Schluessel).
"""
import asyncio
import inspect
import os
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path

import pytest
from fastapi import HTTPException, Response
from pydantic import ValidationError

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

MONGO_URL = os.environ.get("MONGO_URL") or "mongodb://127.0.0.1:27017"
DB_NAME = os.environ.get("DB_NAME") or "autoschnell"
PDF_STUB = b"%PDF-1.4 test"


def _jetzt():
    return datetime.now(timezone.utc).isoformat()


def _module(name):
    import importlib
    return importlib.import_module(name)


class _Welt:
    def __init__(self):
        s = uuid.uuid4().hex[:10]
        self.s = s
        self.dealer_id = f"d_r17v_{s}"
        self.chef = {"id": f"chef_r17v_{s}", "dealer_id": self.dealer_id, "role": "dealer"}
        self.sucher = {"id": f"su_r17v_{s}", "dealer_id": self.dealer_id, "role": "sucher"}

    def konten(self):
        return [
            {"id": self.chef["id"], "dealer_id": self.dealer_id, "role": "dealer", "active": True,
             "email": f"{self.chef['id']}@e2etest-mail.de", "created_at": _jetzt()},
            {"id": self.sucher["id"], "dealer_id": self.dealer_id, "role": "sucher", "active": True,
             "first_name": "Sina", "last_name": "S",
             "email": f"{self.sucher['id']}@e2etest-mail.de", "created_at": _jetzt()},
        ]

    def fahrzeug(self, vid, lifecycle="verglichen", owner=None, **extra):
        doc = {"id": vid, "dealer_id": self.dealer_id, "lifecycle": lifecycle,
               "owner_user_id": owner or self.chef["id"], "mobile_ad_id": vid[2:],
               "data": {"make_label": "BMW", "model_label": "320d", "mileage": 100},
               "status": "verglichen", "created_at": _jetzt(), "updated_at": _jetzt()}
        doc.update(extra)
        return doc

    def vertrag(self, cid, **extra):
        doc = {"id": cid, "contract_no": f"KV-{cid[-6:]}", "dealer_id": self.dealer_id,
               "user_id": self.chef["id"], "vehicle_id": f"v_{self.s}", "make": "BMW",
               "model": "320d", "seller_name": "Verkaeufer R17", "pickup_date": "2099-09-10",
               "pickup_time": "09:00", "purchase_price": 5000, "version": 1,
               "contract_data": {"seller_name": "Verkaeufer R17", "purchase_price": 5000,
                                 "pickup_date": "2099-09-10", "pickup_time": "09:00"},
               "pdf_b64": "JVBERi0xLjQgYWx0", "filename": "Kaufvertrag.pdf",
               "send_status": [], "status": "erstellt", "appointment_id": None,
               "created_at": _jetzt(), "updated_at": _jetzt()}
        doc.update(extra)
        return doc

    def appt(self, aid, **extra):
        doc = {"id": aid, "dealer_id": self.dealer_id, "title": aid, "status": "offen",
               "vehicle_id": f"v_{self.s}", "pickup_date": "2099-09-15", "pickup_time": "10:00",
               "pickup_address": "Altweg 1 30159 Hannover", "seller_name": "Alt",
               "created_by": self.chef["id"], "created_at": _jetzt(), "updated_at": _jetzt()}
        doc.update(extra)
        return doc

    async def aufraeumen(self, db):
        for c in ("appointments", "vehicles", "activity_logs", "generated_pdfs",
                  "generated_pdf_versions", "users", "pickup_reports", "pickup_protocols",
                  "listing_snapshots", "vehicle_comparisons", "kaufvorgaenge"):
            await db[c].delete_many({"dealer_id": self.dealer_id})
        await db.dealers.delete_many({"id": self.dealer_id})


async def _indizes(db):
    """Dieselben Unique-Indizes wie server.py/indizes.py (CI-Datenbank ist
    frisch): ein Kaufvorgang je Vertrag, EIN offener Termin je VERTRAG.
    Der alte Index termin_offen_je_fahrzeug (ein Termin je Fahrzeug)
    widerspricht dem Umbau Kaufvorgaenge und wird wie im Backend entfernt."""
    from deps import TERMIN_OFFEN

    async def _alten_termin_index_entfernen():
        if "termin_offen_je_fahrzeug" in await db.appointments.index_information():
            await db.appointments.drop_index("termin_offen_je_fahrzeug")
    versuche = [
        _alten_termin_index_entfernen,
        lambda: db.kaufvorgaenge.create_index("contract_id", unique=True),
        lambda: db.appointments.create_index(
            [("dealer_id", 1), ("contract_id", 1)], unique=True, name="termin_offen_je_vertrag",
            partialFilterExpression={"contract_id": {"$type": "string", "$gt": ""},
                                     "status": {"$in": list(TERMIN_OFFEN)}}),
    ]
    for v in versuche:
        try:
            await v()
        except Exception:  # noqa: BLE001  (Index existiert mit anderer Definition / Altdubletten)
            pass


@pytest.fixture
def welt():
    from motor.motor_asyncio import AsyncIOMotorClient
    w = _Welt()
    # Umbau Kaufvorgaenge 09.09.2026: `kaufvorgang` haelt ein eigenes `db`
    # (from deps import db) - ohne Umbiegen haengt es am Loop des ersten Tests.
    names = ["deps", "routes.contracts", "routes.appointments", "routes.bestand",
             "lifecycle", "auto_daten", "cleanup_service", "kaufvorgang"]
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


# ------------------------------------------------------------------ Stubs/Haken
def _pdf_stub(monkeypatch, nebenwirkung=None):
    """ReportLab durch einen Stub ersetzen; merkt sich die Aufrufe."""
    C = _module("routes.contracts")
    aufrufe = []

    def _gen(*, dealer, vehicle, contract, digital=False):
        # Seit 09.09.2026 entstehen je Vertrag ZWEI Fassungen (Druck +
        # digital); gezaehlt wird weiter ein Aufruf je Vertrag (Druckfassung).
        if not digital:
            aufrufe.append(contract)
        if nebenwirkung:
            nebenwirkung()
        return PDF_STUB
    monkeypatch.setattr(C, "generate_contract_pdf", _gen)
    return aufrufe


def _auto_daten_stub(monkeypatch):
    AD = _module("auto_daten")

    async def _anlegen(db, contract_dict, vehicle, gekauft_am=None):
        return f"ad_stub_{uuid.uuid4().hex[:8]}"

    async def _nichts(*a, **k):
        return None
    monkeypatch.setattr(AD, "anlegen", _anlegen)
    monkeypatch.setattr(AD, "zurueckrollen", _nichts)
    monkeypatch.setattr(AD, "aktualisieren", _nichts)
    monkeypatch.setattr(AD, "nachtragen", _nichts)


def _sammlung_hooken(monkeypatch, methode, sammlung, ersatz):
    """Motor liefert bei jedem `db.<name>` ein neues Collection-Objekt —
    deshalb auf Klassenebene haken; `ersatz(orig, self, *a, **k)` laeuft
    nur fuer die genannte Sammlung."""
    from motor.motor_asyncio import AsyncIOMotorCollection as K
    orig = getattr(K, methode)

    async def _hook(self, *a, **k):
        if self.name == sammlung:
            return await ersatz(orig, self, *a, **k)
        return await orig(self, *a, **k)
    monkeypatch.setattr(K, methode, _hook)


def _body(vid, **extra):
    C = _module("routes.contracts")
    d = {"vehicle_id": vid, "seller_name": "Verkaeufer R17", "purchase_price": 5000,
         "seller_address": "Weg 1", "seller_zip": "30159", "seller_city": "Hannover"}
    d.update(extra)
    return C.ContractIn(**d)


async def _logs(db, dealer_id):
    return [l["action"] async for l in db.activity_logs.find({"dealer_id": dealer_id})]


# ================================================= 338: inf/nan und Koordinaten
def test_338_koordinaten_und_kaufpreis_ohne_inf_nan():
    C = _module("routes.contracts")
    basis = {"vehicle_id": "v", "seller_name": "s", "purchase_price": 1}
    for kaputt in (float("inf"), float("nan"), -float("inf")):
        with pytest.raises(ValidationError):
            C.ContractIn(**{**basis, "purchase_price": kaputt})
        with pytest.raises(ValidationError):
            C.DamageIn(x=kaputt)
        with pytest.raises(ValidationError):
            C.DamageIn(y=kaputt)
    for zu_gross in (10001, -10001, 1e12):
        with pytest.raises(ValidationError):
            C.DamageIn(x=zu_gross)
        with pytest.raises(ValidationError):
            C.ContractIn(**{**basis, "damages": [{"y": zu_gross}]})
    ok = C.ContractIn(**{**basis, "damages": [{"x": -10000, "y": 10000}, {"x": 12.5}]})
    assert ok.damages[0].x == -10000 and ok.damages[0].y == 10000
    assert ok.damages[1].x == 12.5 and ok.damages[1].y is None
    assert C.DamageIn().x is None


# ================================================= 346/347: Datum, Uhrzeit, PLZ/Ort
def test_346_347_pickup_datum_uhrzeit_plz_ort_geprueft():
    C = _module("routes.contracts")
    basis = {"vehicle_id": "v", "seller_name": "s", "purchase_price": 1}
    for feld, wert in (("pickup_date", "10.10.2099"), ("pickup_date", "2099-02-30"),
                       ("pickup_date", "2099-13-01"), ("pickup_date", "morgen"),
                       ("pickup_time", "25:00"), ("pickup_time", "9:00"),
                       ("pickup_time", "10:60"), ("pickup_time", "zehn"),
                       ("seller_zip", "x" * 21), ("seller_city", "x" * 101)):
        with pytest.raises(ValidationError, match=feld):
            C.ContractIn(**{**basis, feld: wert})
    ok = C.ContractIn(**{**basis, "pickup_date": " 2099-10-10 ", "pickup_time": "09:05",
                         "seller_zip": 30159, "seller_city": "x" * 100})
    assert ok.pickup_date == "2099-10-10" and ok.pickup_time == "09:05"
    assert ok.seller_zip == "30159", "Zahlen werden weiter zu Text (coerce_numbers_to_str)"
    # leer bleibt leer (kein Termin gewuenscht) — auch None
    leer = C.ContractIn(**{**basis, "pickup_date": "", "pickup_time": None})
    assert leer.pickup_date == "" and leer.pickup_time is None
    assert C.ContractIn(**basis).pickup_date == ""


# ================================================= 270: verkauft/archiviert -> 409
def test_270_kein_vertrag_fuer_verkauftes_fahrzeug(welt, monkeypatch):
    C = _module("routes.contracts")
    w, db = welt.w, welt.db
    aufrufe = _pdf_stub(monkeypatch)
    _auto_daten_stub(monkeypatch)

    async def lauf():
        await db.vehicles.insert_many([
            w.fahrzeug(f"v_verkauft{w.s}", lifecycle="verkauft"),
            w.fahrzeug(f"v_archiv{w.s}", lifecycle="archiviert"),
            w.fahrzeug(f"v_weg{w.s}", lifecycle="geloescht"),
        ])
        codes = {}
        for name in ("verkauft", "archiv", "weg"):
            with pytest.raises(HTTPException) as e:
                await C.create_contract(_body(f"v_{name}{w.s}"), w.chef)
            codes[name] = (e.value.status_code, e.value.detail)
        n = await db.generated_pdfs.count_documents({"dealer_id": w.dealer_id})
        return codes, n

    codes, n = welt.run(lauf())
    assert codes["verkauft"][0] == 409 and "verkauft" in codes["verkauft"][1]
    assert codes["archiv"][0] == 409
    assert codes["weg"][0] == 404, "geloescht faengt fahrzeug_bereich als 404"
    assert n == 0 and aufrufe == [], "kein PDF, kein Vertrag"


# ================================================= 265: Nacharbeit-Hinweis statt 500
def test_265_vertrag_bleibt_wenn_fahrzeugstatus_scheitert(welt, monkeypatch):
    """Umbau Kaufvorgaenge 09.09.2026: Fahrzeugstatus, Mitbearbeiter,
    kaufvorgang.anlegen und die Zusammenfassung stehen gemeinsam im
    try-Block nach dem Vertrags-Insert. Scheitert ein Schritt (a: der
    Fahrzeug-Write, b: das Anlegen des Vorgangs), bleibt der Vertrag samt
    kaufvorgang_id gespeichert, die Antwort traegt den Nacharbeit-Hinweis
    (kein 500, kein zweiter Vertrag durch Wiederholen)."""
    C = _module("routes.contracts")
    KV = _module("kaufvorgang")
    w, db = welt.w, welt.db
    _pdf_stub(monkeypatch)
    _auto_daten_stub(monkeypatch)
    va, vb = f"va_{w.s}", f"vb_{w.s}"
    kaputt = {"vehicles": True}

    async def _wackelig(orig, self, *a, **k):
        if kaputt["vehicles"]:
            raise RuntimeError("vehicles.update_one: simulierter DB-Aussetzer")
        return await orig(self, *a, **k)
    _sammlung_hooken(monkeypatch, "update_one", "vehicles", _wackelig)

    async def _anlegen_kaputt(**k):
        raise RuntimeError("kaufvorgaenge.insert_one: simulierter DB-Aussetzer")

    async def lauf():
        await db.vehicles.insert_many([w.fahrzeug(va), w.fahrzeug(vb)])
        # a) Fahrzeug-Write scheitert: kein Status, kein Vorgang - Vertrag + Hinweis
        out_a = await C.create_contract(_body(va), w.chef)
        c_a = await db.generated_pdfs.find_one({"id": out_a["id"]}, {"_id": 0})
        v_a = await db.vehicles.find_one({"id": va}, {"_id": 0})
        kv_a = await db.kaufvorgaenge.find_one({"contract_id": out_a["id"]}, {"_id": 0})
        # b) Fahrzeug-Write geht, kaufvorgang.anlegen scheitert: ebenfalls Hinweis
        kaputt["vehicles"] = False
        monkeypatch.setattr(KV, "anlegen", _anlegen_kaputt)
        out_b = await C.create_contract(_body(vb), w.chef)
        c_b = await db.generated_pdfs.find_one({"id": out_b["id"]}, {"_id": 0})
        v_b = await db.vehicles.find_one({"id": vb}, {"_id": 0})
        kv_b = await db.kaufvorgaenge.find_one({"contract_id": out_b["id"]}, {"_id": 0})
        return out_a, c_a, v_a, kv_a, out_b, c_b, v_b, kv_b, await _logs(db, w.dealer_id)

    out_a, c_a, v_a, kv_a, out_b, c_b, v_b, kv_b, logs = welt.run(lauf())
    for out, c in ((out_a, c_a), (out_b, c_b)):
        assert out["nacharbeit_hinweis"].startswith("Vertrag gespeichert"), out.get("nacharbeit_hinweis")
        assert out["pdf_b64"] and c and c["status"] == "erstellt", "Vertrag ist gespeichert"
        assert c["kaufvorgang_id"] and c["purchase_price"] == 5000, "Vorgangs-ID steht im Vertrag"
    assert v_a.get("status") == "verglichen" and v_a.get("lifecycle") == "verglichen"
    assert kv_a is None and kv_b is None, "Vorgang nicht angelegt (Nacharbeit)"
    assert (v_b["status"] == "Vertrag erstellt" and v_b["lifecycle"] == "verglichen"), (
        "Zusammenfassung nach dem Fehler nicht mehr erreicht")
    assert v_a.get("purchase_price") is None and v_b.get("purchase_price") is None, (
        "Kaufpreis gehoert zum Vorgang, nicht zum gemeinsamen Fahrzeug")
    assert logs.count("pdf.erstellt") == 2, "Audit ueber log_activity_sicher trotz Fehler"
    q = inspect.getsource(C.create_contract)
    i = q.index("db.vehicles.update_one")
    block = q[q.rindex("try:", 0, i):q.index("except Exception", i)]
    assert ("_kv.anlegen(" in block and "fahrzeug_status_aggregieren(" in block
            and "mitbearbeiter_ids" in block), "alle Nacharbeit-Schritte stehen im try-Block"
    assert "nacharbeit_hinweis = " in q[q.index("except Exception", i):][:400]
    assert 'log_activity_sicher(user["dealer_id"], user["id"], "pdf.erstellt"' in q


def test_265b_normaler_weg_ohne_hinweis_mit_termin(welt, monkeypatch):
    """Normaler Weg (Umbau Kaufvorgaenge 09.09.2026): kein Hinweis; der
    Vertrag traegt kaufvorgang_id, der Vorgang existiert mit Kaufpreis und
    Termin (abholung_geplant), der Termin zeigt auf den Vorgang. Das
    gemeinsame Fahrzeug bekommt KEINEN purchase_price (erst beim Abholen aus
    dem Vorgang), nur den zusammengefassten Lebenszyklus."""
    C = _module("routes.contracts")
    w, db = welt.w, welt.db
    aufrufe = _pdf_stub(monkeypatch)
    _auto_daten_stub(monkeypatch)
    vid = f"v_{w.s}"

    async def lauf():
        await db.vehicles.insert_one(w.fahrzeug(vid))
        out = await C.create_contract(
            _body(vid, pickup_date="2099-10-10", pickup_time="11:00"), w.chef)
        c = await db.generated_pdfs.find_one({"id": out["id"]}, {"_id": 0})
        v = await db.vehicles.find_one({"id": vid}, {"_id": 0})
        a = await db.appointments.find_one({"contract_id": out["id"]}, {"_id": 0})
        kv = await db.kaufvorgaenge.find_one({"contract_id": out["id"]}, {"_id": 0})
        n_kv = await db.kaufvorgaenge.count_documents({"dealer_id": w.dealer_id})
        return out, c, v, a, kv, n_kv, await _logs(db, w.dealer_id)

    out, c, v, a, kv, n_kv, logs = welt.run(lauf())
    assert "nacharbeit_hinweis" not in out and "termin_hinweis" not in out
    assert len(aufrufe) == 1 and aufrufe[0]["contract_no"] == out["contract_no"]
    assert c["status"] == "Termin erstellt" and c["appointment_id"] == a["id"]
    assert c["kaufvorgang_id"] and out["kaufvorgang_id"] == c["kaufvorgang_id"]
    # ein Vorgang je Vertrag: Sucher, Fahrzeug, Kaufpreis, Status, Termin
    assert n_kv == 1 and kv["id"] == c["kaufvorgang_id"]
    assert kv["user_id"] == w.chef["id"] and kv["vehicle_id"] == vid
    assert kv["purchase_price"] == 5000 and kv["status"] == "abholung_geplant"
    assert kv["appointment_id"] == a["id"] and a["kaufvorgang_id"] == kv["id"]
    assert v["status"] == "Vertrag erstellt" and v.get("purchase_price") is None, (
        "Kaufpreis gehoert zum Vorgang, nicht zum gemeinsamen Fahrzeug")
    assert v["lifecycle"] == "abholung_geplant", "Lebenszyklus = Zusammenfassung der Vorgaenge"
    assert a["pickup_address"] == "Weg 1 30159 Hannover" and a["pickup_date"] == "2099-10-10"
    assert a["created_by"] == w.chef["id"] and a["status"] == "offen"
    assert "pdf.erstellt" in logs and "termin.auto-erstellt" in logs


# ================================================= 355/356: kein Umhaengen - EIN Termin je Vertrag
def _eigener_termin_je_vertrag_pruefen(welt, monkeypatch, fallback_erzwingen: bool):
    """Umbau Kaufvorgaenge 09.09.2026: `_abholtermin_fuer_vertrag` haengt den
    offenen Termin eines ANDEREN Vertrags nie mehr um. Ein zweiter Vertrag
    desselben Suchers zum selben Fahrzeug bekommt einen EIGENEN Termin; der
    alte Termin bleibt exakt wie er war - samt angenommener Fahrer-Zusage
    (Nr. 355 damit gegenstandslos) und Vertragszeiger (Nr. 356: der alte
    Vertrag behaelt Termin und Status). Wiederholung fuer denselben Vertrag
    legt keinen zweiten Termin an (Teil-Unique-Index termin_offen_je_vertrag)."""
    C = _module("routes.contracts")
    A = _module("routes.appointments")
    w, db = welt.w, welt.db
    vid = f"v_{w.s}"
    a1, c1, c2, c3 = f"a_{w.s}", f"c1_{w.s}", f"c2_{w.s}", f"c3_{w.s}"
    kv1, kv2, kv3 = f"kv1_{w.s}", f"kv2_{w.s}", f"kv3_{w.s}"
    if fallback_erzwingen:
        monkeypatch.delattr(A, "zusage_zuruecksetzen_wenn_geaendert", raising=False)
    neu = _body(vid, pickup_date="2099-10-10", pickup_time="11:00",
                seller_address="A" * 500)              # 500 + PLZ + Ort > 500

    def vorgang(kid, cid, preis, status="vertrag_erstellt", appointment_id=None):
        return {"id": kid, "dealer_id": w.dealer_id, "user_id": w.chef["id"], "vehicle_id": vid,
                "contract_id": cid, "purchase_price": preis, "status": status,
                "appointment_id": appointment_id, "created_at": _jetzt(), "updated_at": _jetzt()}

    async def lauf():
        await db.vehicles.insert_one(w.fahrzeug(vid))
        alt = w.appt(a1, contract_id=c1, kaufvorgang_id=kv1, driver_id=f"f_{w.s}",
                     zuteilung="angenommen", zuteilung_am="2026-09-01T00:00:00+00:00",
                     zuteilung_beantwortet_am="2026-09-02T00:00:00+00:00")
        await db.appointments.insert_one(dict(alt))
        await db.generated_pdfs.insert_many([
            w.vertrag(c1, appointment_id=a1, status="Termin erstellt", kaufvorgang_id=kv1),
            w.vertrag(c2, purchase_price=4700, kaufvorgang_id=kv2),
        ])
        await db.kaufvorgaenge.insert_many([
            vorgang(kv1, c1, 5000, status="abholung_geplant", appointment_id=a1),
            vorgang(kv2, c2, 4700)])
        # zweiter Vertrag desselben Suchers zum selben Fahrzeug
        appt_id, hinweis = await C._abholtermin_fuer_vertrag(
            w.chef, neu, {"make_label": "BMW"}, c2, kaufvorgang_id=kv2)
        a_alt = await db.appointments.find_one({"id": a1}, {"_id": 0})
        a_neu = await db.appointments.find_one({"id": appt_id}, {"_id": 0})
        c1_doc = await db.generated_pdfs.find_one({"id": c1}, {"_id": 0})
        c2_doc = await db.generated_pdfs.find_one({"id": c2}, {"_id": 0})
        kv1_doc = await db.kaufvorgaenge.find_one({"id": kv1}, {"_id": 0})
        kv2_doc = await db.kaufvorgaenge.find_one({"id": kv2}, {"_id": 0})
        # Wiederholung fuer denselben Vertrag (Vorgang wird aus dem Vertrag gelesen)
        appt_id2, _ = await C._abholtermin_fuer_vertrag(w.chef, neu, {"make_label": "BMW"}, c2)
        # dritter Vertrag -> dritter Termin
        await db.generated_pdfs.insert_one(w.vertrag(c3, kaufvorgang_id=kv3))
        await db.kaufvorgaenge.insert_one(vorgang(kv3, c3, 5100))
        appt_id3, _ = await C._abholtermin_fuer_vertrag(w.chef, neu, {"make_label": "BMW"}, c3)
        a_alt2 = await db.appointments.find_one({"id": a1}, {"_id": 0})
        offen = await db.appointments.find(
            {"dealer_id": w.dealer_id, "vehicle_id": vid, "status": "offen"},
            {"_id": 0, "id": 1, "contract_id": 1}).to_list(10)
        v = await db.vehicles.find_one({"id": vid}, {"_id": 0})
        return (alt, appt_id, hinweis, a_alt, a_neu, c1_doc, c2_doc, kv1_doc, kv2_doc,
                appt_id2, appt_id3, a_alt2, offen, v, await _logs(db, w.dealer_id))

    (alt, appt_id, hinweis, a_alt, a_neu, c1_doc, c2_doc, kv1_doc, kv2_doc,
     appt_id2, appt_id3, a_alt2, offen, v, logs) = welt.run(lauf())
    assert appt_id and appt_id != a1 and hinweis is None, "eigener Termin statt Umhaengen"
    # Nr. 355: der Termin des anderen Vertrags bleibt samt Zusage exakt wie er war
    assert a_alt == alt and a_alt2 == alt
    # Nr. 356: alter Vertrag behaelt Termin und Status; der neue zeigt auf SEINEN Termin
    assert c1_doc["appointment_id"] == a1 and c1_doc["status"] == "Termin erstellt"
    assert c2_doc["appointment_id"] == appt_id and c2_doc["status"] == "Termin erstellt"
    # neuer Termin: Felder aus dem Vertragsformular; Nr. 347: Abholadresse gedeckelt
    assert a_neu["contract_id"] == c2 and a_neu["kaufvorgang_id"] == kv2
    assert a_neu["vehicle_id"] == vid and a_neu["created_by"] == w.chef["id"]
    assert a_neu["status"] == "offen" and "driver_id" not in a_neu and "zuteilung" not in a_neu
    assert a_neu["pickup_date"] == "2099-10-10" and a_neu["pickup_time"] == "11:00"
    assert len(a_neu["pickup_address"]) == 500 and a_neu["pickup_address"].startswith("A" * 500)
    # Kaufvorgaenge: nur der eigene Vorgang bewegt sich, Kaufpreise bleiben getrennt
    assert kv1_doc["status"] == "abholung_geplant" and kv1_doc["appointment_id"] == a1
    assert kv1_doc["purchase_price"] == 5000
    assert kv2_doc["status"] == "abholung_geplant" and kv2_doc["appointment_id"] == appt_id
    assert kv2_doc["purchase_price"] == 4700
    assert v.get("purchase_price") is None and v["lifecycle"] == "abholung_geplant"
    # Wiederholung: derselbe Termin, kein zweiter; dritter Vertrag: dritter Termin
    assert appt_id2 == appt_id and appt_id3 not in (a1, appt_id)
    assert sorted(o["contract_id"] for o in offen) == sorted([c1, c2, c3])
    assert logs.count("termin.auto-erstellt") == 2 and logs.count("termin.auto-wiederverwendet") == 1


def test_355_356_eigener_termin_je_vertrag_mit_lokalem_fallback(welt, monkeypatch):
    """Der Helfer aus routes.appointments fehlt (aelterer Stand): der fremde
    Termin wird trotzdem nie angefasst - die Zusage-Regel spielt beim
    Vertragsweg keine Rolle mehr (sie gilt weiter fuer PUT /appointments)."""
    _eigener_termin_je_vertrag_pruefen(welt, monkeypatch, fallback_erzwingen=True)


def test_355_356_eigener_termin_je_vertrag_mit_vorhandenem_helfer(welt, monkeypatch):
    """Mit dem Helfer aus routes.appointments muss dasselbe herauskommen -
    Terminplaner und Vertragsweg duerfen nicht auseinanderlaufen."""
    _eigener_termin_je_vertrag_pruefen(welt, monkeypatch, fallback_erzwingen=False)


def test_355_zusage_regel_lokal():
    C = _module("routes.contracts")
    f = C._zusage_zuruecksetzen_fallback
    alt = {"zuteilung": "angenommen", "pickup_date": "2099-09-15", "pickup_time": "10:00",
           "pickup_address": "Weg 1"}
    assert f(alt, {"pickup_date": "2099-09-15", "pickup_time": "10:00", "pickup_address": "Weg 1"}) == ({}, {})
    assert f({**alt, "zuteilung": "offen"}, {"pickup_date": "2099-12-24"}) == ({}, {})
    s, u = f(alt, {"pickup_date": "2099-12-24"})
    assert s["zuteilung"] == "offen" and s["zuteilung_neu_wegen_aenderung"] is True and s["zuteilung_am"]
    assert u == {"zuteilung_beantwortet_am": ""}
    assert f(alt, {"pickup_time": ""})[0].get("zuteilung") == "offen", "geleerte Uhrzeit ist eine Aenderung"
    assert f(alt, {"seller_name": "x"}) == ({}, {}), "nur Datum/Uhrzeit/Adresse zaehlen"


# ================================================= 348: Grabstein unsichtbar
def test_348_grabstein_vertrag_unsichtbar_loeschen_idempotent(welt, monkeypatch):
    C = _module("routes.contracts")
    A = _module("routes.appointments")
    B = _module("routes.bestand")
    w, db = welt.w, welt.db
    _pdf_stub(monkeypatch)
    _auto_daten_stub(monkeypatch)
    vid, grab, normal = f"v_{w.s}", f"c_grab{w.s}", f"c_ok{w.s}"
    assert C._vertrag_bereich(w.chef) == {"dealer_id": w.dealer_id,
                                          "loeschung.status": {"$ne": "laeuft"}}
    assert C._vertrag_bereich(w.sucher)["user_id"] == w.sucher["id"]
    assert "loeschung.status" in C._vertrag_bereich(w.sucher)

    async def lauf():
        await db.vehicles.insert_one(w.fahrzeug(vid, lifecycle="bestand"))
        await db.generated_pdfs.insert_many([
            w.vertrag(grab, loeschung={"status": "laeuft", "gestartet": _jetzt(),
                                       "grund": "frist", "scrub_pii": True}),
            w.vertrag(normal),
        ])
        await db.generated_pdf_versions.insert_one({
            "id": f"ver_{w.s}", "contract_id": grab, "dealer_id": w.dealer_id, "version": 1,
            "pdf_b64": "QUJD", "archived_at": _jetzt()})
        liste = await C.list_contracts(Response(), w.chef)
        codes = {}
        for name, coro in (
            ("get", C.get_contract(grab, w.chef)),
            ("pdf", C.get_contract_pdf(grab, w.chef)),
            ("versions", C.list_contract_versions(grab, w.chef)),
            ("version_pdf", C.get_contract_version_pdf(grab, 1, w.chef)),
            ("send", C.send_contract(grab, C.SendIn(channel="whatsapp", recipient="+4917",
                                                    message="x", idempotency_key="k1"), w.chef)),
            ("termin", A.create_appointment(A.AppointmentIn(contract_id=grab, vehicle_id=vid,
                                                            pickup_date="2099-01-01"), w.chef)),
        ):
            with pytest.raises(HTTPException) as e:
                await coro
            codes[name] = e.value.status_code
        regen = await C.regenerate_contract_for_pickup(
            contract_id=grab, dealer_id=w.dealer_id, user=w.chef, pickup_date="2099-12-01")
        regen_ohne_user = await C.regenerate_contract_for_pickup(
            contract_id=grab, dealer_id=w.dealer_id, user=None, pickup_date="2099-12-01")
        # Fahrzeugakte (routes/bestand.py) nutzt denselben Filter — direkt
        # gegen die Sammlung geprueft, unabhaengig vom Rest der Akte.
        akte = await db.generated_pdfs.find(
            {"vehicle_id": vid, **C._vertrag_bereich(w.chef)}, {"_id": 0, "id": 1}).to_list(10)
        ok = await C.get_contract(normal, w.chef)
        # Loeschen bleibt idempotent: der Grabstein-Vertrag wird zu Ende geloescht
        r = await C.delete_contract(grab, w.chef)
        rest = await db.generated_pdfs.count_documents({"id": grab})
        versionen = await db.generated_pdf_versions.count_documents({"contract_id": grab})
        return liste, codes, regen, regen_ohne_user, akte, ok, r, rest, versionen

    liste, codes, regen, regen_ohne_user, akte, ok, r, rest, versionen = welt.run(lauf())
    assert [c["id"] for c in liste] == [normal]
    assert codes == {"get": 404, "pdf": 404, "versions": 404, "version_pdf": 404,
                     "send": 404, "termin": 404}, codes
    assert regen is False and regen_ohne_user is False
    assert [c["id"] for c in akte] == [normal]
    assert ok["id"] == normal
    assert r == {"ok": True} and rest == 0 and versionen == 0
    assert "_vertrag_bereich" in inspect.getsource(B.vehicle_akte), "Akte nutzt den Filter"


def test_348b_delete_nutzt_nur_dealer_id():
    C = _module("routes.contracts")
    q = inspect.getsource(C.delete_contract)
    assert '{"id": contract_id, "dealer_id": user["dealer_id"]}' in q
    assert "_vertrag_bereich" not in q, "Loeschen muss Grabsteine weiter finden (idempotent)"


# ================================================= 349/374: list_contracts-Grenzen
def test_349_374_list_contracts_q_und_days_gedeckelt(welt):
    C = _module("routes.contracts")
    D = _module("deps")
    w, db = welt.w, welt.db
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    app = FastAPI()
    app.include_router(C.router, prefix="/api")
    app.dependency_overrides[D.current_firma] = lambda: w.chef
    # 422 faellt VOR dem Handler — keine DB noetig
    with TestClient(app) as tc:
        for params in ({"days": 0}, {"days": -1}, {"days": 3651}, {"days": 10 ** 12},
                       {"days": "x"}, {"q": "x" * 201}):
            r = tc.get("/api/contracts", params=params)
            assert r.status_code == 422, (params, r.status_code, r.text[:200])

    async def lauf():
        await db.generated_pdfs.insert_one(w.vertrag(f"c_{w.s}"))
        alle = await C.list_contracts(Response(), w.chef)
        gefiltert = await C.list_contracts(Response(), w.chef, q="x" * 200, days=3650)
        heute = await C.list_contracts(Response(), w.chef, q="BMW", days=1)
        return alle, gefiltert, heute

    alle, gefiltert, heute = welt.run(lauf())
    assert [c["id"] for c in alle] == [f"c_{w.s}"], "In-Prozess-Standardwerte bleiben None"
    assert gefiltert == [] and [c["id"] for c in heute] == [f"c_{w.s}"]
    sig = inspect.signature(C.list_contracts)
    assert sig.parameters["q"].default is None and sig.parameters["days"].default is None


# ================================================= 373/321: CAS und pickup_history
def test_373_321_regenerate_cas_und_history_gedeckelt(welt, monkeypatch):
    C = _module("routes.contracts")
    w, db = welt.w, welt.db
    aufrufe = _pdf_stub(monkeypatch)
    _auto_daten_stub(monkeypatch)
    vid, cid, alt_cid = f"v_{w.s}", f"c_{w.s}", f"c_alt{w.s}"
    historie = [{"von_datum": f"2098-01-{i % 28 + 1:02d}", "auf_datum": "x", "nr": i}
                for i in range(C.PICKUP_HISTORY_MAX)]
    konflikt = {"an": False}

    async def _parallel(orig, self, *a, **k):
        # Zwischen Lesen und Schreiben aendert jemand die Version (zweite
        # Verschiebung/anderer Prozess) — der CAS-Write darf nicht treffen.
        filt, upd = a[0], a[1]
        if konflikt["an"] and "pickup_history" in (upd.get("$push") or {}):
            await orig(self, {"id": filt["id"]}, {"$inc": {"version": 1}})
        return await orig(self, *a, **k)
    _sammlung_hooken(monkeypatch, "update_one", "generated_pdfs", _parallel)

    async def lauf():
        await db.vehicles.insert_one(w.fahrzeug(vid))
        await db.generated_pdfs.insert_many([
            w.vertrag(cid, pickup_history=historie),
            {k: v for k, v in w.vertrag(alt_cid).items() if k != "version"},   # Altvertrag
        ])
        ok = await C.regenerate_contract_for_pickup(
            contract_id=cid, dealer_id=w.dealer_id, user=w.chef, pickup_date="2099-09-11")
        c1 = await db.generated_pdfs.find_one({"id": cid}, {"_id": 0})
        v1 = await db.generated_pdf_versions.find({"contract_id": cid}, {"_id": 0}).to_list(10)
        konflikt["an"] = True
        verloren = await C.regenerate_contract_for_pickup(
            contract_id=cid, dealer_id=w.dealer_id, user=w.chef, pickup_date="2099-09-12")
        konflikt["an"] = False
        c2 = await db.generated_pdfs.find_one({"id": cid}, {"_id": 0})
        v2 = await db.generated_pdf_versions.find({"contract_id": cid}, {"_id": 0}).to_list(10)
        # Altvertrag ohne version-Feld: CAS auf "fehlt" trifft, danach Version 2
        alt_ok = await C.regenerate_contract_for_pickup(
            contract_id=alt_cid, dealer_id=w.dealer_id, user=w.chef, pickup_time="12:30")
        alt = await db.generated_pdfs.find_one({"id": alt_cid}, {"_id": 0})
        alt_v = await db.generated_pdf_versions.find({"contract_id": alt_cid}, {"_id": 0}).to_list(10)
        return ok, c1, v1, verloren, c2, v2, alt_ok, alt, alt_v, await _logs(db, w.dealer_id)

    ok, c1, v1, verloren, c2, v2, alt_ok, alt, alt_v, logs = welt.run(lauf())
    assert ok is True and c1["version"] == 2 and c1["pickup_date"] == "2099-09-11"
    assert c1["pdf_b64"] == "JVBERi0xLjQgdGVzdA==", "Stub-PDF gespeichert"
    # Nr. 321: 100 + 1 -> 100, aeltester faellt, juengster ist der neue
    assert len(c1["pickup_history"]) == C.PICKUP_HISTORY_MAX == 100
    assert c1["pickup_history"][0]["nr"] == 1 and c1["pickup_history"][-1]["auf_datum"] == "2099-09-11"
    assert [x["version"] for x in v1] == [1]
    # Nr. 373: Verlierer schreibt nichts und nimmt seine Archivfassung zurueck
    assert verloren is False
    assert c2["version"] == 3 and c2["pickup_date"] == "2099-09-11", "nur der $inc des Anderen"
    assert len(c2["pickup_history"]) == 100 and c2["pickup_history"][-1]["auf_datum"] == "2099-09-11"
    assert [x["version"] for x in v2] == [1], "keine zweite Archivfassung"
    assert len(aufrufe) == 3
    assert logs.count("vertrag.abholtermin.geaendert") == 2, "Verlierer schreibt kein Audit"
    assert alt_ok is True and alt["version"] == 2 and alt["pickup_time"] == "12:30"
    assert [x["version"] for x in alt_v] == [1]
    q = inspect.getsource(C.regenerate_contract_for_pickup)
    assert '"version": doc.get("version")' in q and 'delete_one({"id": archiv_id})' in q
    assert '"$slice": -PICKUP_HISTORY_MAX' in q


# ================================================= 370: 409 vor dem Versand
def test_370_send_409_wenn_loeschung_zwischen_lesen_und_versand_beginnt(welt, monkeypatch):
    C = _module("routes.contracts")
    w, db = welt.w, welt.db
    cid = f"c_{w.s}"

    async def _grabstein_vor_recheck(orig, self, *a, **k):
        filt = a[0] if a else k.get("filter")
        proj = a[1] if len(a) > 1 else k.get("projection")
        if proj == {"_id": 1} and "loeschung.status" in (filt or {}):
            # Loeschung beginnt genau jetzt (Frist- oder manuelle Loeschung)
            await self.update_one({"id": filt["id"]},
                                  {"$set": {"loeschung": {"status": "laeuft"}}})
        return await orig(self, *a, **k)
    _sammlung_hooken(monkeypatch, "find_one", "generated_pdfs", _grabstein_vor_recheck)

    async def lauf():
        await db.generated_pdfs.insert_one(w.vertrag(cid))
        with pytest.raises(HTTPException) as e:
            await C.send_contract(cid, C.SendIn(channel="whatsapp", recipient="+491701",
                                                message="Hallo", idempotency_key="k17"), w.chef)
        c = await db.generated_pdfs.find_one({"id": cid}, {"_id": 0})
        return e.value, c, await _logs(db, w.dealer_id)

    exc, c, logs = welt.run(lauf())
    assert exc.status_code == 409 and "gelöscht" in exc.detail
    assert c["status"] == "erstellt", "kein Versand-Status"
    assert not [x for x in logs if x.startswith("pdf.gesendet")]
    q = inspect.getsource(C.send_contract)
    i = q.index("Vertrag wird gerade gelöscht")
    assert "_reservierung_zurueck()" in q[i - 200:i]


# ================================================= 372: Vermerk nicht gespeichert
def test_372_send_meldet_fehlenden_status_vermerk(welt, monkeypatch):
    C = _module("routes.contracts")
    w, db = welt.w, welt.db
    cid = f"c_{w.s}"

    async def _grabstein_vor_vermerk(orig, self, *a, **k):
        filt, upd = a[0], a[1]
        if "send_status.$" in (upd.get("$set") or {}):
            # Loeschung beginnt NACH dem Versand, vor dem Vermerk
            await orig(self, {"id": filt["id"]}, {"$set": {"loeschung": {"status": "laeuft"}}})
        return await orig(self, *a, **k)
    _sammlung_hooken(monkeypatch, "update_one", "generated_pdfs", _grabstein_vor_vermerk)

    async def lauf():
        await db.generated_pdfs.insert_one(w.vertrag(cid))
        out = await C.send_contract(cid, C.SendIn(channel="whatsapp", recipient="+491701",
                                                  message="Hallo", idempotency_key="k17"), w.chef)
        c = await db.generated_pdfs.find_one({"id": cid}, {"_id": 0})
        return out, c, await _logs(db, w.dealer_id)

    out, c, logs = welt.run(lauf())
    assert out["status"] == "ok" and out["wa_url"].startswith("https://wa.me/491701")
    assert out["status_vermerk"] == "nicht_gespeichert"
    assert c["status"] == "erstellt" and c["send_status"][0]["zustellung"] == "laeuft"
    assert "pdf.gesendet.ohne_vermerk" in logs and "pdf.gesendet.whatsapp" in logs


def test_372b_normaler_versand_ohne_vermerk_feld(welt):
    C = _module("routes.contracts")
    w, db = welt.w, welt.db
    cid = f"c_{w.s}"

    async def lauf():
        await db.generated_pdfs.insert_one(w.vertrag(cid))
        out = await C.send_contract(cid, C.SendIn(channel="whatsapp", recipient="+491701",
                                                  message="Hallo", idempotency_key="k17"), w.chef)
        c = await db.generated_pdfs.find_one({"id": cid}, {"_id": 0})
        return out, c, await _logs(db, w.dealer_id)

    out, c, logs = welt.run(lauf())
    assert "status_vermerk" not in out
    assert c["status"] == "versand_vorbereitet" and c["send_status"][0]["zustellung"] == "chat_geoeffnet"
    assert "pdf.gesendet.ohne_vermerk" not in logs and "pdf.gesendet.whatsapp" in logs
