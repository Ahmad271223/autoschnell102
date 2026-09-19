# -*- coding: utf-8 -*-
"""Runde 18 (Pruefbefunde 09.09.2026, Sucher + Chef + Kaufvorgaenge).

Geprueft und behoben:
  * m6 schliesst den Besitzer-Alarm nicht mehr, solange Fahrzeuge OHNE
    Besitzer existieren (Ablauf m4 -> m5 -> m6)
  * m5 ist nach Teilabbruch vollstaendig wiederaufnehmbar
  * POST /appointments: "abgeholt" mit Fahrer nur ueber das Protokoll
  * Termin verbindet nie Fahrzeug A mit dem Vertrag fuer Fahrzeug B
  * Vertragswechsel trifft den NEUEN Kaufvorgang (auch ohne Statuswechsel)
  * Einkaufspreis eines verkauften Fahrzeugs bleibt unangetastet
  * Foto-Bereinigung schreibt nur Fotofelder und wartet auf andere Vorgaenge
  * Fahrer-Status verliert gegen zwischenzeitliche Stornierung/Umteilung
  * Firmenloeschung raeumt kaufvorgaenge mit
  * Termin ohne Vorgang (Fehlerfall bei der Anlage) wird nachgeholt
  * gescheiterte Fahrzeug-Zusammenfassung wird beim Wiederholen repariert
"""
import asyncio
import os
import sys
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from fastapi import HTTPException

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

MONGO_URL = os.environ.get("MONGO_URL") or "mongodb://127.0.0.1:27017"
DB_NAME = os.environ.get("DB_NAME") or "autoschnell"


def _jetzt():
    return datetime.now(timezone.utc).isoformat()


def _module(name):
    import importlib
    return importlib.import_module(name)


@pytest.fixture
def welt():
    from motor.motor_asyncio import AsyncIOMotorClient
    s = uuid.uuid4().hex[:10]
    names = ["deps", "kaufvorgang", "lifecycle", "cleanup_service", "migrationen",
             "routes.contracts", "routes.appointments", "routes.drivers", "routes.admin"]
    mods = [_module(n) for n in names]
    alt = [(m, getattr(m, "db", None)) for m in mods]

    class _Ctx:
        def __init__(self):
            self.s = s
            self.dealer_id = f"d_r18_{s}"
            self.chef = {"id": f"chef_r18_{s}", "dealer_id": self.dealer_id, "role": "dealer"}
            self.a = {"id": f"sa_r18_{s}", "dealer_id": self.dealer_id, "role": "sucher"}
            self.b = {"id": f"sb_r18_{s}", "dealer_id": self.dealer_id, "role": "sucher"}
            self.loop = asyncio.new_event_loop()
            asyncio.set_event_loop(self.loop)
            self.client = AsyncIOMotorClient(MONGO_URL, serverSelectionTimeoutMS=5000)
            self.db = self.client[DB_NAME]
            for m in mods:
                if hasattr(m, "db"):
                    m.db = self.db

        def run(self, coro):
            return self.loop.run_until_complete(coro)

        def fahrzeug(self, vid, **extra):
            d = {"id": vid, "dealer_id": self.dealer_id, "lifecycle": "verglichen",
                 "mobile_ad_id": vid[2:], "owner_user_id": self.a["id"],
                 "mitbearbeiter_ids": [self.b["id"]],
                 "data": {"make_label": "BMW", "model_label": "320d"},
                 "created_at": _jetzt(), "updated_at": _jetzt()}
            d.update(extra)
            return d

    ctx = _Ctx()
    ctx.run(ctx.db.users.insert_many([
        {"id": ctx.chef["id"], "dealer_id": ctx.dealer_id, "role": "dealer", "active": True,
         "email": f"{ctx.chef['id']}@e2etest-mail.de", "created_at": _jetzt()},
        {"id": ctx.a["id"], "dealer_id": ctx.dealer_id, "role": "sucher", "active": True,
         "first_name": "Anna", "last_name": "A",
         "email": f"{ctx.a['id']}@e2etest-mail.de", "created_at": _jetzt()},
        {"id": ctx.b["id"], "dealer_id": ctx.dealer_id, "role": "sucher", "active": True,
         "first_name": "Ben", "last_name": "B",
         "email": f"{ctx.b['id']}@e2etest-mail.de", "created_at": _jetzt()}]))
    ctx.run(ctx.db.dealers.insert_one({"id": ctx.dealer_id, "user_id": ctx.chef["id"],
                                       "company_name": "R18 GmbH", "created_at": _jetzt()}))
    yield ctx
    try:
        for c in ("vehicles", "appointments", "generated_pdfs", "generated_pdf_versions",
                  "kaufvorgaenge", "activity_logs", "pickup_protocols", "pickup_reports",
                  "users", "dealers", "subscriptions"):
            ctx.run(ctx.db[c].delete_many({"dealer_id": ctx.dealer_id}))
        ctx.run(ctx.db.dealers.delete_many({"id": ctx.dealer_id}))
    finally:
        for m, d in alt:
            if d is not None:
                m.db = d
        ctx.client.close()
        ctx.loop.close()


class _Body:
    """Minimal-Eingabe fuer die Termin-Endpunkte (wie AppointmentIn)."""

    def __init__(self, **kw):
        self.title = kw.get("title")
        self.vehicle_id = kw.get("vehicle_id")
        self.contract_id = kw.get("contract_id")
        self.seller_name = kw.get("seller_name", "")
        self.seller_phone = kw.get("seller_phone", "")
        self.seller_email = kw.get("seller_email", "")
        self.pickup_address = kw.get("pickup_address", "")
        self.pickup_date = kw.get("pickup_date", "2099-10-10")
        self.pickup_time = kw.get("pickup_time", "10:00")
        self.driver_id = kw.get("driver_id")
        self.status = kw.get("status", "offen")
        self.notes = kw.get("notes", "")
        self.final_price = kw.get("final_price")
        self.extra_costs = kw.get("extra_costs")
        self.contract_loesen = kw.get("contract_loesen", False)
        self.fahrzeug_loesen = kw.get("fahrzeug_loesen", False)
        self._gesetzt = set(kw)

    def model_dump(self, exclude_none=False, exclude=None, exclude_unset=False):
        felder = {"title", "vehicle_id", "contract_id", "seller_name", "seller_phone",
                  "seller_email", "pickup_address", "pickup_date", "pickup_time",
                  "driver_id", "status", "notes", "final_price", "extra_costs",
                  "contract_loesen", "fahrzeug_loesen"}
        if exclude_unset:
            felder &= self._gesetzt
        d = {k: getattr(self, k) for k in felder}
        if exclude_none:
            d = {k: v for k, v in d.items() if v is not None}
        for k in (exclude or ()):
            d.pop(k, None)
        return d


async def _vertrag(w, user, vid, preis, cid, status="erstellt"):
    KV = _module("kaufvorgang")
    kv_id = str(uuid.uuid4())
    await w.db.generated_pdfs.insert_one({
        "id": cid, "dealer_id": w.dealer_id, "user_id": user["id"], "vehicle_id": vid,
        "purchase_price": preis, "status": status, "appointment_id": None,
        "kaufvorgang_id": kv_id, "created_at": _jetzt(),
        "contract_data": {"seller_name": "Verkaeufer"}})
    await KV.anlegen(dealer_id=w.dealer_id, user_id=user["id"], vehicle_id=vid,
                     contract_id=cid, purchase_price=preis, kaufvorgang_id=kv_id)
    await KV.fahrzeug_status_aggregieren(vid, w.dealer_id, user=user)
    return kv_id


# ============================================================ Migrationen
def test_01_m6_schliesst_besitzer_alarm_nicht_bei_fahrzeugen_ohne_besitzer(welt):
    """Befund: m4 oeffnet den Alarm, m6 sah nur Fahrzeuge MIT Besitzer und
    schloss ihn wieder — obwohl weiterhin besitzerlose Fahrzeuge da waren.
    Geprueft wird der echte Ablauf m4 -> m5 -> m6.

    Ein Fahrzeug bleibt nur dann ohne Besitzer, wenn die Firma auch keinen
    aktiven Chef mehr hat (sonst faengt ihn die Heuristik auf) — deshalb eine
    eigene, kontenlose Firma."""
    M = _module("migrationen")
    w = welt
    leer = f"{w.dealer_id}_leer"
    ohne = f"v_ohne_{w.s}"
    kaputt = f"v_kaputt_{w.s}"

    async def lauf():
        # Fahrzeug einer Firma ohne aktive Konten -> bleibt besitzerlos
        await w.db.vehicles.insert_one({"id": ohne, "dealer_id": leer,
                                        "lifecycle": "verglichen", "data": {},
                                        "created_at": _jetzt()})
        # Fahrzeug mit ungueltigem Besitzer, aber Vertrag von A -> m6 repariert es
        await w.db.vehicles.insert_one({"id": kaputt, "dealer_id": w.dealer_id,
                                        "lifecycle": "verglichen", "data": {},
                                        "owner_user_id": f"weg_{w.s}", "created_at": _jetzt()})
        await w.db.generated_pdfs.insert_one({
            "id": f"c_r18_{w.s}", "dealer_id": w.dealer_id, "user_id": w.a["id"],
            "vehicle_id": kaputt, "status": "erstellt", "created_at": _jetzt()})
        s4 = await M.m4_fahrzeug_besitzer(w.db)
        offen_m4 = await w.db.betriebsalarme.count_documents(
            {"typ": "fahrzeuge_ohne_besitzer", "offen": True})
        s5 = await M.m5_kaufvorgaenge(w.db)
        s6 = await M.m6_besitzer_nachbessern(w.db)
        offen_m6 = await w.db.betriebsalarme.count_documents(
            {"typ": "fahrzeuge_ohne_besitzer", "offen": True})
        v_ohne = await w.db.vehicles.find_one({"id": ohne}, {"_id": 0, "owner_user_id": 1})
        v_kaputt = await w.db.vehicles.find_one({"id": kaputt}, {"_id": 0, "owner_user_id": 1})
        # Zuweisen -> der Alarm haengt nur noch an evtl. FREMDEN Restdaten
        await w.db.vehicles.update_one({"id": ohne}, {"$set": {"owner_user_id": w.a["id"]}})
        await M._offene_besitzer_melden(w.db, 0)
        rest = await w.db.vehicles.count_documents(M.OHNE_BESITZER)
        offen_danach = await w.db.betriebsalarme.count_documents(
            {"typ": "fahrzeuge_ohne_besitzer", "offen": True})
        await w.db.vehicles.delete_many({"dealer_id": leer})
        return s4, s5, s6, offen_m4, offen_m6, v_ohne, v_kaputt, rest, offen_danach

    s4, s5, s6, offen_m4, offen_m6, v_ohne, v_kaputt, rest, offen_danach = w.run(lauf())
    assert s4["offen"] >= 1, "Fahrzeug ohne Konto bleibt offen"
    assert offen_m4 == 1, "m4 meldet den Alarm"
    assert v_kaputt["owner_user_id"] == w.a["id"], "m6 repariert den ungueltigen Besitzer"
    assert not v_ohne.get("owner_user_id"), "das besitzerlose Fahrzeug bleibt offen"
    assert offen_m6 == 1, ("m6 darf den Alarm NICHT schliessen, solange Fahrzeuge "
                           "ohne Besitzer existieren")
    assert (offen_danach == 0) == (rest == 0),         "der Alarm folgt genau der Zahl besitzerloser Fahrzeuge"


def test_02_m5_ist_nach_teilabbruch_wiederaufnehmbar(welt):
    """Befund: m5 setzte zuerst contract.kaufvorgang_id; brach ein spaeterer
    Schritt ab, uebersprang die Wiederholung den Vertrag — Terminverweis und
    Mitbearbeiter fehlten dauerhaft."""
    M = _module("migrationen")
    w = welt
    vid = f"v_m5_{w.s}"
    cid = f"c_m5_{w.s}"
    aid = f"a_m5_{w.s}"

    async def lauf():
        await w.db.vehicles.insert_one(w.fahrzeug(vid, mitbearbeiter_ids=[]))
        await w.db.generated_pdfs.insert_one({
            "id": cid, "dealer_id": w.dealer_id, "user_id": w.b["id"], "vehicle_id": vid,
            "purchase_price": 5000, "status": "versendet", "created_at": _jetzt()})
        await w.db.appointments.insert_one({
            "id": aid, "dealer_id": w.dealer_id, "contract_id": cid, "vehicle_id": vid,
            "status": "offen", "created_by": w.b["id"], "created_at": _jetzt()})
        # Teilabbruch simulieren: Vorgang da, Vertrag markiert, Rest fehlt
        kv_id = str(uuid.uuid4())
        await w.db.kaufvorgaenge.insert_one({
            "id": kv_id, "dealer_id": w.dealer_id, "user_id": w.b["id"], "vehicle_id": vid,
            "contract_id": cid, "purchase_price": 5000, "status": "abholung_geplant",
            "appointment_id": aid, "created_at": _jetzt(), "updated_at": _jetzt()})
        await w.db.generated_pdfs.update_one({"id": cid}, {"$set": {"kaufvorgang_id": kv_id}})
        quelle = __import__("inspect").getsource(M.m5_kaufvorgaenge)
        return kv_id, quelle

    kv_id, quelle = w.run(lauf())
    # Reihenfolge im Code: Termin/Mitbearbeiter VOR dem Merker am Vertrag
    i_termin = quelle.index('db.appointments.update_one({"id": appt["id"]}')
    i_mit = quelle.index("mitbearbeiter_ids")
    i_merker = quelle.index('db.generated_pdfs.update_one({"id": c["id"]}')
    assert i_termin < i_merker and i_mit < i_merker, \
        "kaufvorgang_id am Vertrag muss ZULETZT gesetzt werden (Fertig-Kennzeichen)"


# ============================================================ Termine
def test_03_anlegen_mit_fahrer_und_abgeholt_ist_gesperrt(welt):
    """Befund: POST /appointments akzeptierte status='abgeholt' mit Fahrer —
    ohne Abholprotokoll. Beim Aendern galt die Regel laengst."""
    A_ = _module("routes.appointments")
    w = welt
    vid = f"v_ab_{w.s}"

    async def lauf():
        await w.db.vehicles.insert_one(w.fahrzeug(vid))
        await w.db.dealer_drivers.insert_one({
            "id": f"dd_{w.s}", "dealer_id": w.dealer_id, "driver_id": f"drv_{w.s}",
            "active": True, "created_at": _jetzt()})
        fehler = None
        try:
            await A_.create_appointment(
                _Body(vehicle_id=vid, status="abgeholt", driver_id=f"drv_{w.s}"), w.chef)
        except HTTPException as e:
            fehler = e
        # ohne Fahrer bleibt der Buero-Abschluss erlaubt
        ok = await A_.create_appointment(_Body(vehicle_id=vid, status="abgeholt"), w.chef)
        anzahl = await w.db.appointments.count_documents({"dealer_id": w.dealer_id})
        await w.db.dealer_drivers.delete_many({"dealer_id": w.dealer_id})
        return fehler, ok, anzahl

    fehler, ok, anzahl = w.run(lauf())
    assert fehler is not None and fehler.status_code == 409
    assert "Abholprotokoll" in fehler.detail
    assert ok["status"] == "abgeholt" and anzahl == 1, "nur der Termin ohne Fahrer entstand"


def test_04_termin_verbindet_nicht_fahrzeug_a_mit_vertrag_fuer_b(welt):
    """Befund: Fahrzeug und Vertrag wurden nur einzeln auf Zugriff geprueft.
    Ein Termin konnte Fahrzeug A mit dem Vertrag fuer Fahrzeug B verbinden."""
    A_ = _module("routes.appointments")
    w = welt
    va, vb = f"v_a_{w.s}", f"v_b_{w.s}"
    cb = f"c_b_{w.s}"

    async def lauf():
        await w.db.vehicles.insert_one(w.fahrzeug(va))
        await w.db.vehicles.insert_one(w.fahrzeug(vb))
        await _vertrag(w, w.a, vb, 7000, cb)
        fehler = None
        try:
            await A_.create_appointment(_Body(vehicle_id=va, contract_id=cb), w.a)
        except HTTPException as e:
            fehler = e
        # Passend geht es
        ok = await A_.create_appointment(_Body(vehicle_id=vb, contract_id=cb), w.a)
        # Auch beim Aendern: Fahrzeug auf A umbiegen ist gesperrt
        fehler2 = None
        try:
            await A_.update_appointment(ok["id"], _Body(vehicle_id=va), w.a)
        except HTTPException as e:
            fehler2 = e
        appt = await w.db.appointments.find_one({"id": ok["id"]}, {"_id": 0, "vehicle_id": 1})
        return fehler, ok, fehler2, appt

    fehler, ok, fehler2, appt = w.run(lauf())
    assert fehler is not None and fehler.status_code == 409 and "anderen Fahrzeug" in fehler.detail
    assert ok["vehicle_id"] == vb
    assert fehler2 is not None and fehler2.status_code == 409
    assert appt["vehicle_id"] == vb, "das Fahrzeug am Termin blieb unveraendert"


def test_05_vertragswechsel_trifft_den_neuen_kaufvorgang(welt):
    """Befund: Bei Vertragswechsel + Statusaenderung in EINEM PUT gewann die
    alte kaufvorgang_id — der ALTE Kauf wurde als abgeholt markiert."""
    A_ = _module("routes.appointments")
    w = welt
    vid = f"v_sw_{w.s}"
    c1, c2 = f"c1_{w.s}", f"c2_{w.s}"

    async def lauf():
        await w.db.vehicles.insert_one(w.fahrzeug(vid))
        kv1 = await _vertrag(w, w.a, vid, 10000, c1)
        kv2 = await _vertrag(w, w.a, vid, 12000, c2)
        appt = await A_.create_appointment(_Body(vehicle_id=vid, contract_id=c1), w.a)
        # Wechsel auf Vertrag 2 UND Abschluss in einem Aufruf
        await A_.update_appointment(appt["id"],
                                    _Body(contract_id=c2, status="nicht abgeholt"), w.a)
        kvs = {k["contract_id"]: k for k in await w.db.kaufvorgaenge.find(
            {"vehicle_id": vid}, {"_id": 0}).to_list(10)}
        return kv1, kv2, appt, kvs

    kv1, kv2, appt, kvs = w.run(lauf())
    assert kvs[c2]["status"] == "nicht_abgeholt", "der NEUE Vorgang bekommt den Status"
    assert kvs[c1]["status"] == "vertrag_erstellt", "der alte Vorgang bleibt unberuehrt"
    assert kvs[c1].get("appointment_id") is None
    assert kvs[c2].get("appointment_id") == appt["id"]


def test_06_vertragswechsel_ohne_statusaenderung_zieht_vorgang_nach(welt):
    """Befund (zweite Haelfte): ohne Statusaenderung blieb der neue Vorgang
    auf 'vertrag_erstellt' stehen, obwohl ein Abholtermin an ihm haengt."""
    A_ = _module("routes.appointments")
    w = welt
    vid = f"v_sw2_{w.s}"
    c1, c2 = f"c1b_{w.s}", f"c2b_{w.s}"

    async def lauf():
        await w.db.vehicles.insert_one(w.fahrzeug(vid))
        await _vertrag(w, w.a, vid, 10000, c1)
        await _vertrag(w, w.a, vid, 12000, c2)
        appt = await A_.create_appointment(_Body(vehicle_id=vid, contract_id=c1), w.a)
        await A_.update_appointment(appt["id"], _Body(contract_id=c2), w.a)
        kvs = {k["contract_id"]: k for k in await w.db.kaufvorgaenge.find(
            {"vehicle_id": vid}, {"_id": 0}).to_list(10)}
        return appt, kvs

    appt, kvs = w.run(lauf())
    assert kvs[c2]["status"] == "abholung_geplant" and kvs[c2]["appointment_id"] == appt["id"]
    assert kvs[c1]["status"] == "vertrag_erstellt" and kvs[c1].get("appointment_id") is None


# ============================================================ Kaufvorgang
def test_07_einkaufspreis_eines_verkauften_fahrzeugs_bleibt(welt):
    """Befund: Die Zusammenfassung schrieb purchase_price, bevor sie den
    Lebenszyklus prueft — ein spaeter abgeschlossener zweiter Vorgang
    veraenderte die historische Marge eines verkauften Fahrzeugs."""
    KV = _module("kaufvorgang")
    w = welt
    vid = f"v_preis_{w.s}"
    c1, c2 = f"cp1_{w.s}", f"cp2_{w.s}"

    async def lauf():
        await w.db.vehicles.insert_one(w.fahrzeug(vid))
        kv1 = await _vertrag(w, w.a, vid, 10000, c1)
        kv2 = await _vertrag(w, w.b, vid, 15000, c2)
        await KV.status_setzen(kv1, "abgeholt", user=w.a)
        v_nach_kauf = await w.db.vehicles.find_one(
            {"id": vid}, {"_id": 0, "purchase_price": 1, "lifecycle": 1,
                          "abgeholt_kaufvorgang_id": 1})
        # Fahrzeug wird verkauft ...
        await w.db.vehicles.update_one({"id": vid}, {"$set": {"lifecycle": "verkauft"}})
        # ... danach schliesst der zweite Sucher seinen Vorgang ebenfalls ab
        await KV.status_setzen(kv2, "abgeholt", user=w.b)
        v_nach_verkauf = await w.db.vehicles.find_one(
            {"id": vid}, {"_id": 0, "purchase_price": 1, "lifecycle": 1,
                          "abgeholt_kaufvorgang_id": 1})
        return kv1, v_nach_kauf, v_nach_verkauf

    kv1, v_nach_kauf, v_nach_verkauf = w.run(lauf())
    assert v_nach_kauf["purchase_price"] == 10000
    assert v_nach_kauf["abgeholt_kaufvorgang_id"] == kv1
    assert v_nach_verkauf["purchase_price"] == 10000, "Einkaufspreis darf sich nicht aendern"
    assert v_nach_verkauf["lifecycle"] == "verkauft"
    assert v_nach_verkauf["abgeholt_kaufvorgang_id"] == kv1


def test_08_gescheiterte_zusammenfassung_wird_beim_wiederholen_repariert(welt):
    """Befund: Vorgangsstatus gesetzt, Fahrzeugaktualisierung gescheitert
    (abgefangen). Beim Wiederholen sah termin_status_uebernehmen denselben
    Status und uebersprang die Zusammenfassung — das Fahrzeug blieb falsch."""
    KV = _module("kaufvorgang")
    w = welt
    vid = f"v_rep_{w.s}"
    cid = f"c_rep_{w.s}"

    async def lauf():
        await w.db.vehicles.insert_one(w.fahrzeug(vid))
        kv_id = await _vertrag(w, w.a, vid, 9000, cid)
        appt_id = f"a_rep_{w.s}"
        await w.db.appointments.insert_one({
            "id": appt_id, "dealer_id": w.dealer_id, "contract_id": cid, "vehicle_id": vid,
            "kaufvorgang_id": kv_id, "status": "abgeholt", "created_by": w.a["id"],
            "created_at": _jetzt()})
        # Vorgang steht bereits auf abgeholt, das Fahrzeug haengt zurueck
        await w.db.kaufvorgaenge.update_one({"id": kv_id}, {"$set": {"status": "abgeholt"}})
        await w.db.vehicles.update_one({"id": vid}, {"$set": {"lifecycle": "gekauft"}})
        appt = await w.db.appointments.find_one({"id": appt_id}, {"_id": 0})
        erfolg = await KV.termin_status_uebernehmen(appt, "abgeholt", user=w.a)
        v = await w.db.vehicles.find_one({"id": vid}, {"_id": 0, "lifecycle": 1,
                                                       "purchase_price": 1})
        return erfolg, v

    erfolg, v = w.run(lauf())
    assert erfolg is True
    assert v["lifecycle"] == "abgeholt", "Wiederholung gleicht das Fahrzeug nach"
    assert v["purchase_price"] == 9000


def test_09_termin_ohne_vorgang_wird_ueber_den_vertrag_nachgeholt(welt):
    """Befund: Scheiterte die Vorgangsanlage nach dem Vertrags-Insert, trug
    der Auto-Termin eine kaufvorgang_id ins Leere; der Terminpfad reparierte
    das nie und fiel auf die direkte Fahrzeugstatus-Aenderung zurueck."""
    KV = _module("kaufvorgang")
    w = welt
    vid = f"v_lose_{w.s}"
    cid = f"c_lose_{w.s}"
    appt_id = f"a_lose_{w.s}"
    tot = str(uuid.uuid4())

    async def lauf():
        await w.db.vehicles.insert_one(w.fahrzeug(vid))
        await w.db.generated_pdfs.insert_one({
            "id": cid, "dealer_id": w.dealer_id, "user_id": w.a["id"], "vehicle_id": vid,
            "purchase_price": 8000, "status": "erstellt", "kaufvorgang_id": tot,
            "created_at": _jetzt(), "contract_data": {}})
        await w.db.appointments.insert_one({
            "id": appt_id, "dealer_id": w.dealer_id, "contract_id": cid, "vehicle_id": vid,
            "kaufvorgang_id": tot, "status": "offen", "created_by": w.a["id"],
            "created_at": _jetzt()})
        appt = await w.db.appointments.find_one({"id": appt_id}, {"_id": 0})
        erfolg = await KV.termin_status_uebernehmen(appt, "abgeholt", user=w.a)
        kvs = await w.db.kaufvorgaenge.find({"contract_id": cid}, {"_id": 0}).to_list(5)
        appt_nach = await w.db.appointments.find_one({"id": appt_id},
                                                     {"_id": 0, "kaufvorgang_id": 1})
        v = await w.db.vehicles.find_one({"id": vid}, {"_id": 0, "lifecycle": 1,
                                                       "purchase_price": 1})
        return erfolg, kvs, appt_nach, v

    erfolg, kvs, appt_nach, v = w.run(lauf())
    assert erfolg is True, "der Vorgang wird ueber den Vertrag nachgelegt"
    assert len(kvs) == 1 and kvs[0]["status"] == "abgeholt"
    assert kvs[0]["id"] == tot, "die im Vertrag vermerkte Vorgangs-ID wird uebernommen"
    assert appt_nach["kaufvorgang_id"] == kvs[0]["id"]
    assert v["lifecycle"] == "abgeholt" and v["purchase_price"] == 8000


# ============================================================ Aufraeumen
def test_10_fotobereinigung_erhaelt_andere_fahrzeugdaten_und_offene_vorgaenge(welt):
    """Zwei Befunde: (a) der Job schrieb das ganze data-Objekt zurueck und
    ueberschrieb zwischenzeitliche Korrekturen; (b) er raeumte die Fotos des
    gemeinsamen Fahrzeugs, obwohl ein anderer Sucher noch einen offenen
    Vorgang hatte."""
    CS = _module("cleanup_service")
    w = welt
    vid = f"v_foto_{w.s}"
    alt_iso = (datetime.now(timezone.utc) - timedelta(days=90)).isoformat()

    async def lauf():
        await w.db.vehicles.insert_one(w.fahrzeug(vid, data={
            "make_label": "BMW", "mileage": 100000, "image_urls": ["a.jpg", "b.jpg"]}))
        # Vorgang A abgeschlossen, Vorgang B des zweiten Suchers noch offen
        await _vertrag(w, w.a, vid, 10000, f"cf1_{w.s}")
        await _vertrag(w, w.b, vid, 11000, f"cf2_{w.s}")
        await w.db.appointments.insert_one({
            "id": f"af_{w.s}", "dealer_id": w.dealer_id, "vehicle_id": vid,
            "contract_id": f"cf1_{w.s}", "status": "abgeholt", "created_by": w.a["id"],
            "abgeschlossen_seit": alt_iso, "status_changed_at": alt_iso,
            "created_at": alt_iso})
        await w.db.vehicles.update_one({"id": vid}, {"$set": {"lifecycle": "abgeholt"}})
        await CS._cleanup_once(w.db)
        v1 = await w.db.vehicles.find_one({"id": vid}, {"_id": 0, "data": 1})
        a1 = await w.db.appointments.find_one({"id": f"af_{w.s}"},
                                              {"_id": 0, "cleanup_skipped": 1})
        # Vorgang B abschliessen -> beim naechsten Lauf duerfen die Fotos weg
        await w.db.kaufvorgaenge.update_many({"vehicle_id": vid},
                                             {"$set": {"status": "abgeholt"}})
        await w.db.appointments.update_one({"id": f"af_{w.s}"},
                                           {"$unset": {"assets_cleaned_at": "",
                                                       "cleanup_skipped": ""}})
        # zwischenzeitliche Korrektur am Fahrzeug (darf nicht verloren gehen)
        await w.db.vehicles.update_one({"id": vid}, {"$set": {"data.mileage": 123456,
                                                              "lifecycle": "abgeholt"}})
        await CS._cleanup_once(w.db)
        v2 = await w.db.vehicles.find_one({"id": vid}, {"_id": 0, "data": 1})
        return v1, a1, v2

    v1, a1, v2 = w.run(lauf())
    assert v1["data"]["image_urls"] == ["a.jpg", "b.jpg"], \
        "Fotos bleiben, solange ein anderer Vorgang offen ist"
    assert a1.get("cleanup_skipped") == "anderer_vorgang_offen"
    assert v2["data"]["image_urls"] == [], "danach werden die Fotos geraeumt"
    assert v2["data"]["mileage"] == 123456, "Korrekturen duerfen nicht ueberschrieben werden"
    assert v2["data"]["make_label"] == "BMW"


# ============================================================ Fahrer
def test_11_fahrer_status_verliert_gegen_zwischenzeitliche_aenderung(welt):
    """Befund: Der Schreibfilter enthielt nur die Termin-ID. Wurde der Termin
    zwischen Lesen und Schreiben storniert oder umgeteilt, setzte der alte
    Fahreraufruf den Status trotzdem."""
    from motor.motor_asyncio import AsyncIOMotorCollection
    D = _module("routes.drivers")
    w = welt
    vid = f"v_drv_{w.s}"
    appt_id = f"a_drv_{w.s}"
    drv = {"id": f"drv_{w.s}", "display_name": "Fahrer"}

    class _Status:
        status = "nicht abgeholt"
        notes = ""

    async def lauf():
        await w.db.vehicles.insert_one(w.fahrzeug(vid))
        await w.db.appointments.insert_one({
            "id": appt_id, "dealer_id": w.dealer_id, "vehicle_id": vid, "status": "offen",
            "driver_id": drv["id"], "zuteilung": "angenommen", "created_by": w.a["id"],
            "created_at": _jetzt()})
        echte_pruefung = D._zugriff_pruefen

        async def _frei(appt, driver):
            return None
        D._zugriff_pruefen = _frei
        # Motor liefert bei jedem Zugriff ein NEUES Collection-Objekt — der
        # Haken muss auf der KLASSE sitzen.
        original = AsyncIOMotorCollection.find_one
        zustand = {"n": 0}

        async def find_one_mit_rennen(self, *a, **kw):
            doc = await original(self, *a, **kw)
            if (self.name == "appointments" and zustand["n"] == 0
                    and isinstance(doc, dict) and doc.get("id") == appt_id):
                zustand["n"] = 1
                # Genau jetzt storniert der Chef den Termin.
                await w.db.appointments.update_one(
                    {"id": appt_id}, {"$set": {"status": "storniert"}})
            return doc
        AsyncIOMotorCollection.find_one = find_one_mit_rennen
        fehler = None
        try:
            await D.driver_set_status(appt_id, _Status(), drv)
        except HTTPException as e:
            fehler = e
        finally:
            AsyncIOMotorCollection.find_one = original
            D._zugriff_pruefen = echte_pruefung
        appt = await w.db.appointments.find_one({"id": appt_id}, {"_id": 0, "status": 1})
        return fehler, appt, zustand["n"]

    fehler, appt, gerannt = w.run(lauf())
    assert gerannt == 1, "das Rennen wurde ausgeloest"
    assert fehler is not None and fehler.status_code == 409, "der ueberholte Aufruf muss scheitern"
    assert appt["status"] == "storniert", "die Stornierung bleibt bestehen"


# ============================================================ Firmenloeschung
def test_12_firmenloeschung_raeumt_kaufvorgaenge(welt):
    """Befund: kaufvorgaenge fehlte in _COMPANY_COLLECTIONS — Vorgaenge mit
    Firmen-, Nutzer-, Vertrags- und Fahrzeugbezug blieben liegen."""
    ADM = _module("routes.admin")
    w = welt
    assert "kaufvorgaenge" in ADM._COMPANY_COLLECTIONS
    vid = f"v_del_{w.s}"

    async def lauf():
        await w.db.vehicles.insert_one(w.fahrzeug(vid))
        await _vertrag(w, w.a, vid, 6000, f"cd_{w.s}")
        vorschau = await ADM.company_delete_preview(w.dealer_id, {"id": "admin", "role": "admin"}) \
            if hasattr(ADM, "company_delete_preview") else None
        vorher = await w.db.kaufvorgaenge.count_documents({"dealer_id": w.dealer_id})
        for coll in ADM._COMPANY_COLLECTIONS:
            await w.db[coll].delete_many({"dealer_id": w.dealer_id})
        nachher = await w.db.kaufvorgaenge.count_documents({"dealer_id": w.dealer_id})
        return vorher, nachher, vorschau

    vorher, nachher, vorschau = w.run(lauf())
    assert vorher == 1 and nachher == 0
    if vorschau:
        assert "kaufvorgaenge" in vorschau.get("wuerde_loeschen", {})


def test_13_nachlauf_raeumt_vorgaenge_frueher_geloeschter_firmen(welt):
    """Befund (zweite Haelfte): Auch der Nachlauf fuer Firmenreste kannte die
    Collection nicht — Vorgaenge von Firmen, die VOR dem Fix geloescht wurden,
    blieben liegen."""
    CS = _module("cleanup_service")
    w = welt
    tot = f"{w.dealer_id}_tot"

    async def lauf():
        await w.db.kaufvorgaenge.insert_one({
            "id": f"kv_tot_{w.s}", "dealer_id": tot, "user_id": f"u_{w.s}",
            "vehicle_id": f"v_{w.s}", "contract_id": f"c_tot_{w.s}",
            "purchase_price": 4000, "status": "abgeholt",
            "created_at": _jetzt(), "updated_at": _jetzt()})
        # eigener, LEBENDER Vorgang zum Vergleich
        await w.db.kaufvorgaenge.insert_one({
            "id": f"kv_lebt_{w.s}", "dealer_id": w.dealer_id, "user_id": w.a["id"],
            "vehicle_id": f"v2_{w.s}", "contract_id": f"c_lebt_{w.s}",
            "purchase_price": 4000, "status": "abgeholt",
            "created_at": _jetzt(), "updated_at": _jetzt()})
        await CS.firmenreste_bereinigen(w.db)
        tot_da = await w.db.kaufvorgaenge.count_documents({"dealer_id": tot})
        lebt_da = await w.db.kaufvorgaenge.count_documents({"dealer_id": w.dealer_id})
        return tot_da, lebt_da

    tot_da, lebt_da = w.run(lauf())
    assert tot_da == 0, "Vorgaenge geloeschter Firmen werden nachtraeglich entfernt"
    assert lebt_da == 1, "Vorgaenge bestehender Firmen bleiben"
