# -*- coding: utf-8 -*-
"""Rollenprüfung 22.09.2026, Welle B2 (Entscheidungen Ahmad 22.09.2026).

  RP-474   Ende-zu-Ende: Termin abgeholt + unterschriebenes Protokoll (km,
           neue Schäden) -> (a) Akte zeigt den Befund auch ohne Abhol-Check,
           (b) apply-deviations ohne Bericht übernimmt km/Schäden ins
           Fahrzeug, (c) der Inseratsentwurf übernimmt sie
  RP-454   "Fahrzeug löschen" bei offenen Abholterminen: ohne Flag 409 mit
           der Terminliste (id, pickup_date, driver_name); mit
           termine_stornieren=true werden die Termine über den normalen
           Storno-Weg (update_appointment: Verlauf termin.aktualisiert,
           status_von/nach) storniert und das Fahrzeug danach gelöscht.
           Nicht löschbarer Status: 409, Termine bleiben unverändert.
  RP-477/491  Historie mit vereinbartem Betrag und 409 bei Stand-Abweichung
           sind in test_rp_markt_kaeufer_20260922.py abgedeckt (verifiziert).

In-Prozess gegen eine Wegwerf-DB (autoschnell_rpb2_<uuid>), kein Server.
"""
import asyncio
import os
import re
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path

import pytest
from fastapi import HTTPException

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

MONGO_URL = os.environ.get("MONGO_URL") or "mongodb://127.0.0.1:27017"
WURZEL = Path(__file__).resolve().parents[2]


def _jetzt():
    return datetime.now(timezone.utc).isoformat()


def _module(name):
    import importlib
    return importlib.import_module(name)


@pytest.fixture
def welt(monkeypatch):
    from motor.motor_asyncio import AsyncIOMotorClient
    monkeypatch.setenv("MARKTPLATZ_AKTIV", "true")
    s = uuid.uuid4().hex[:10]
    names = ["deps", "lifecycle", "kaufvorgang", "routes.bestand", "routes.resale",
             "routes.contracts", "routes.appointments", "routes.marketplace",
             "routes.protocols", "routes.drivers"]
    mods = [_module(n) for n in names]
    alt = [(m, getattr(m, "db", None)) for m in mods]

    class _W:
        pass

    w = _W()
    w.s = s
    w.dealer_id = f"d_rpb2_{s}"
    w.chef = {"id": f"chef_rpb2_{s}", "dealer_id": w.dealer_id, "role": "dealer"}
    w.loop = asyncio.new_event_loop()
    asyncio.set_event_loop(w.loop)
    w.client = AsyncIOMotorClient(MONGO_URL, serverSelectionTimeoutMS=5000)
    w.db_name = f"autoschnell_rpb2_{s}"
    w.db = w.client[w.db_name]
    for m in mods:
        if hasattr(m, "db"):
            m.db = w.db
    w.run = lambda coro: w.loop.run_until_complete(coro)
    w.run(w.db.users.insert_one(
        {**w.chef, "active": True, "first_name": "Chef", "last_name": "C",
         "created_at": "2026-01-02T00:00:00+00:00"}))
    w.run(w.db.dealers.insert_one({"id": w.dealer_id, "user_id": w.chef["id"],
                                   "company_name": "RP GmbH", "created_at": _jetzt()}))
    yield w
    try:
        w.run(w.client.drop_database(w.db_name))
    finally:
        for m, d in alt:
            if d is not None:
                m.db = d
        w.client.close()
        w.loop.close()


def _fahrzeug(w, vid, lifecycle="abgeholt", **extra):
    doc = {"id": vid, "dealer_id": w.dealer_id, "lifecycle": lifecycle,
           "lifecycle_changed_at": _jetzt(), "mobile_ad_id": vid[2:],
           "owner_user_id": w.chef["id"],
           "data": {"make_label": "BMW", "model_label": "320d", "mileage": 80000,
                    "image_urls": ["https://x.invalid/1.jpg"]},
           "status": "verglichen", "created_at": _jetzt(), "updated_at": _jetzt()}
    doc.update(extra)
    return doc


def _termin(w, tid, vid, status, **extra):
    doc = {"id": tid, "dealer_id": w.dealer_id, "vehicle_id": vid, "status": status,
           "created_by": w.chef["id"], "pickup_date": "2026-09-30", "pickup_time": "14:00",
           "created_at": _jetzt(), "updated_at": _jetzt()}
    doc.update(extra)
    return doc


def _protokoll(w, pid, vid, tid, km=85120, schaeden=None):
    return {"id": pid, "dealer_id": w.dealer_id, "vehicle_id": vid, "appointment_id": tid,
            "status": "final", "superseded": False, "version": 1,
            "finalized_at": _jetzt(), "created_at": _jetzt(),
            "condition": {"mileage": km},
            "new_damages": schaeden if schaeden is not None else [
                {"type_label": "Kratzer", "zone": "Tür links"},
                {"type_label": "Delle", "zone": "Heckklappe"}]}


def _ohne_bericht(monkeypatch):
    """Kein freiwilliger Abhol-Check (pickup_reports) — nur das Protokoll."""
    import abholbericht

    async def _bericht(db, vehicle_id, dealer_id, nur_termine=None):
        return None
    monkeypatch.setattr(abholbericht, "massgeblicher_bericht", _bericht)


# ============================================================ RP-474 Ende-zu-Ende
def test_rp474_protokoll_akte_fahrzeug_inserat(welt, monkeypatch):
    B, R = _module("routes.bestand"), _module("routes.resale")
    w = welt
    _ohne_bericht(monkeypatch)
    vid, tid = f"v_e2e_{w.s}", f"t_e2e_{w.s}"
    w.run(w.db.vehicles.insert_one(_fahrzeug(w, vid, known_defects=["Steinschlag"])))
    w.run(w.db.appointments.insert_one(_termin(w, tid, vid, "abgeholt")))
    w.run(w.db.pickup_protocols.insert_one(_protokoll(w, f"p_e2e_{w.s}", vid, tid)))
    erwartet = ["Kratzer: Tür links", "Delle: Heckklappe"]

    # (a) Akte: Befund sichtbar, obwohl es keinen Abhol-Check gibt
    akte = w.run(B.vehicle_akte(vid, dict(w.chef)))
    assert akte["pickup_report"] is None
    assert akte["protokoll_befund"] == {"km": 85120, "schaeden": erwartet}
    assert akte["vehicle"]["data"]["mileage"] == 80000, "noch nichts uebernommen"

    # (c) Inseratsentwurf uebernimmt km und Schaeden direkt aus dem Protokoll
    entwurf = w.run(R.create_draft(vid, dict(w.chef)))
    assert entwurf["data"]["mileage"] == 85120
    assert entwurf["known_defects"] == ["Steinschlag", *erwartet]
    assert any("Abholprotokoll" in n for n in entwurf["auto_notes"])

    # (b) Uebernahme ins Fahrzeug ohne Bericht (Kennung der Akte)
    out = w.run(B.apply_deviations(vid, B.ApplyDeviationsIn(deviation_ids=["abholprotokoll"]),
                                   dict(w.chef)))
    assert {"feld": "Kilometerstand", "neu": 85120, "quelle": "abholprotokoll"} in out["applied"]
    assert [a["mangel"] for a in out["applied"] if a.get("mangel")] == erwartet
    v = w.run(w.db.vehicles.find_one({"id": vid}))
    assert v["data"]["mileage"] == 85120
    assert v["known_defects"] == ["Steinschlag", *erwartet]
    assert v["data"]["image_urls"], "Dotted-Path: Fotos unangetastet"
    # danach hat die Akte nichts mehr anzubieten (Oberflaeche blendet den Hinweis aus)
    akte2 = w.run(B.vehicle_akte(vid, dict(w.chef)))
    assert akte2["protokoll_befund"] == {"km": 85120, "schaeden": erwartet}
    assert akte2["vehicle"]["data"]["mileage"] == 85120
    assert all(s in akte2["vehicle"]["known_defects"] for s in erwartet)


def test_rp474_akte_hinweis_und_knopf_in_der_oberflaeche():
    """Quelltext-Wache: die Akte zeigt den Hinweis mit km/Schaeden und der
    Knopf ruft apply-deviations mit der Kennung PROTOKOLL_BEFUND_ID."""
    akte = (WURZEL / "frontend" / "src" / "pages" / "app" / "FahrzeugAkte.jsx").read_text("utf-8")
    assert "protokollBefundHinweis(" in akte
    assert 'deviation_ids: ["abholprotokoll"]' in akte
    assert 'data-testid="akte-protokoll-uebernehmen"' in akte
    B = _module("routes.bestand")
    assert B.PROTOKOLL_BEFUND_ID == "abholprotokoll"
    helfer = (WURZEL / "frontend" / "src" / "lib" / "akteHinweise.js").read_text("utf-8")
    assert "ins Fahrzeug übernehmen" in helfer


# ============================================================ RP-454
def test_rp454_ohne_flag_409_mit_terminliste(welt):
    B = _module("routes.bestand")
    w = welt
    vid = f"v_del_{w.s}"
    w.run(w.db.driver_accounts.insert_one(
        {"id": f"fa_{w.s}", "display_name": "Max Mustermann", "active": True}))
    w.run(w.db.vehicles.insert_one(_fahrzeug(w, vid)))
    w.run(w.db.appointments.insert_many([
        _termin(w, f"t_alt_{w.s}", vid, "abgeholt", pickup_date="2026-09-01"),
        _termin(w, f"t_a_{w.s}", vid, "offen", driver_id=f"fa_{w.s}"),
        _termin(w, f"t_b_{w.s}", vid, "bestätigt", pickup_date="2026-10-02", pickup_time=None)]))
    with pytest.raises(HTTPException) as e:
        w.run(B.vehicle_decision(vid, B.DecisionIn(decision="loeschen"), dict(w.chef)))
    assert e.value.status_code == 409
    d = e.value.detail
    assert d["code"] == B.TERMINE_OFFEN_CODE and "stornieren" in d["msg"]
    assert d["termine"] == [
        {"id": f"t_a_{w.s}", "pickup_date": "2026-09-30", "pickup_time": "14:00",
         "driver_name": "Max Mustermann"},
        {"id": f"t_b_{w.s}", "pickup_date": "2026-10-02", "pickup_time": None,
         "driver_name": None}]
    # nichts passiert
    assert w.run(w.db.vehicles.find_one({"id": vid}))["lifecycle"] == "abgeholt"
    assert w.run(w.db.appointments.find_one({"id": f"t_a_{w.s}"}))["status"] == "offen"
    assert not w.run(w.db.activity_logs.find_one({"ref": f"t_a_{w.s}"}))


def test_rp454_mit_flag_storniert_termine_und_loescht(welt):
    B = _module("routes.bestand")
    w = welt
    vid = f"v_del2_{w.s}"
    w.run(w.db.driver_accounts.insert_one(
        {"id": f"fa2_{w.s}", "display_name": "Erika E.", "active": True}))
    w.run(w.db.dealer_drivers.insert_one(
        {"dealer_id": w.dealer_id, "driver_account_id": f"fa2_{w.s}", "active": True}))
    w.run(w.db.vehicles.insert_one(_fahrzeug(w, vid)))
    w.run(w.db.appointments.insert_many([
        _termin(w, f"t_ab_{w.s}", vid, "abgeholt", pickup_date="2026-09-01"),
        _termin(w, f"t_o1_{w.s}", vid, "offen", driver_id=f"fa2_{w.s}", zuteilung="angenommen"),
        _termin(w, f"t_o2_{w.s}", vid, "", pickup_date="2026-10-05"),
        # anderes Fahrzeug bleibt unberuehrt
        _termin(w, f"t_x_{w.s}", f"v_x_{w.s}", "offen")]))
    r = w.run(B.vehicle_decision(
        vid, B.DecisionIn(decision="loeschen", von_lifecycle="abgeholt", termine_stornieren=True),
        dict(w.chef)))
    assert r["lifecycle"] == "geloescht"
    assert sorted(r["termine_storniert"]) == sorted([f"t_o1_{w.s}", f"t_o2_{w.s}"])
    v = w.run(w.db.vehicles.find_one({"id": vid}))
    assert v["lifecycle"] == "geloescht" and v["data"]["image_urls"] == []
    for tid in (f"t_o1_{w.s}", f"t_o2_{w.s}"):
        t = w.run(w.db.appointments.find_one({"id": tid}))
        assert t["status"] == "storniert", tid
        assert t.get("status_changed_at") and t.get("abgeschlossen_seit")
        # derselbe Verlaufseintrag wie beim Storno unter "Termine"
        log = w.run(w.db.activity_logs.find_one({"action": "termin.aktualisiert", "ref": tid}))
        assert log and log["meta"]["status_nach"] == "storniert"
        assert log["meta"]["status_von"] == ("offen" if tid.startswith("t_o1") else "")
        assert log["user_id"] == w.chef["id"]
    # Fahrer-Sichtfrist laeuft (Termin verschwindet aus der Fahrer-App)
    t1 = w.run(w.db.appointments.find_one({"id": f"t_o1_{w.s}"}))
    assert t1.get("driver_id") == f"fa2_{w.s}", "Fahrer bleibt als Beweisdatum am Termin"
    assert w.run(w.db.appointments.find_one({"id": f"t_ab_{w.s}"}))["status"] == "abgeholt"
    assert w.run(w.db.appointments.find_one({"id": f"t_x_{w.s}"}))["status"] == "offen"
    ent = w.run(w.db.activity_logs.find_one({"action": "fahrzeug.entscheidung.geloescht",
                                             "ref": vid}))
    assert sorted(ent["meta"]["termine_storniert"]) == sorted([f"t_o1_{w.s}", f"t_o2_{w.s}"])


def test_rp454_flag_ohne_offene_termine_ist_harmlos(welt):
    B = _module("routes.bestand")
    w = welt
    vid = f"v_del3_{w.s}"
    w.run(w.db.vehicles.insert_one(_fahrzeug(w, vid, lifecycle="bestand")))
    w.run(w.db.appointments.insert_one(_termin(w, f"t_s_{w.s}", vid, "storniert")))
    r = w.run(B.vehicle_decision(
        vid, B.DecisionIn(decision="loeschen", termine_stornieren=True), dict(w.chef)))
    assert r["lifecycle"] == "geloescht" and r["termine_storniert"] == []


def test_rp454_nicht_loeschbarer_status_laesst_termine_stehen(welt):
    B = _module("routes.bestand")
    w = welt
    vid = f"v_del4_{w.s}"
    w.run(w.db.vehicles.insert_one(_fahrzeug(w, vid, lifecycle="abholung_geplant")))
    w.run(w.db.appointments.insert_one(_termin(w, f"t_g_{w.s}", vid, "offen")))
    with pytest.raises(HTTPException) as e:
        w.run(B.vehicle_decision(
            vid, B.DecisionIn(decision="loeschen", termine_stornieren=True), dict(w.chef)))
    assert e.value.status_code == 409 and "abholung_geplant" in e.value.detail
    assert w.run(w.db.appointments.find_one({"id": f"t_g_{w.s}"}))["status"] == "offen"
    assert w.run(w.db.vehicles.find_one({"id": vid}))["lifecycle"] == "abholung_geplant"


def test_rp454_veralteter_stand_storniert_nicht_still(welt, monkeypatch):
    """Hat sich der Termin seit dem Lesen geaendert (Fahrer schliesst gerade
    ab), bricht der Storno mit 409 ab und das Fahrzeug bleibt."""
    B = _module("routes.bestand")
    w = welt
    vid = f"v_del5_{w.s}"
    w.run(w.db.vehicles.insert_one(_fahrzeug(w, vid)))
    w.run(w.db.appointments.insert_one(_termin(w, f"t_v_{w.s}", vid, "offen")))

    echt = B._offene_termine

    async def _alt(vehicle_id, dealer_id):
        termine = await echt(vehicle_id, dealer_id)
        for t in termine:
            t["updated_at"] = "2020-01-01T00:00:00+00:00"   # veralteter Lesestand
        return termine
    monkeypatch.setattr(B, "_offene_termine", _alt)
    with pytest.raises(HTTPException) as e:
        w.run(B.vehicle_decision(
            vid, B.DecisionIn(decision="loeschen", termine_stornieren=True), dict(w.chef)))
    assert e.value.status_code == 409
    assert "nicht gelöscht" in e.value.detail and "2026-09-30" in e.value.detail
    assert w.run(w.db.appointments.find_one({"id": f"t_v_{w.s}"}))["status"] == "offen"
    assert w.run(w.db.vehicles.find_one({"id": vid}))["lifecycle"] == "abgeholt"


def test_rp454_nur_hauptaccount_und_oberflaeche_fragt_nach():
    """Quelltext-Wache: die Entscheidung bleibt Chefsache (current_haendler),
    der Storno laeuft ueber update_appointment (kein zweiter Storno-Weg), und
    beide Oberflaechen fragen mit der Terminliste nach, bevor das Flag geht."""
    quelle = (WURZEL / "backend" / "routes" / "bestand.py").read_text("utf-8")
    kopf = quelle[quelle.index("async def vehicle_decision("):]
    kopf = kopf[:kopf.index(":", kopf.index("user=Depends(")) + 1]
    assert "current_haendler" in kopf
    helfer = quelle[quelle.index("async def _termine_stornieren("):
                    quelle.index("async def vehicle_decision(")]
    assert "update_appointment(" in helfer and "update_one" not in helfer
    for datei in ("Bestand.jsx", "FahrzeugAkte.jsx"):
        src = (WURZEL / "frontend" / "src" / "pages" / "app" / datei).read_text("utf-8")
        assert "termineStornoFrage(" in src and "window.confirm(" in src
        assert re.search(r"termine_stornieren:\s*true", src), datei
        # nie ohne Rueckfrage: das Flag steht nur nach dem confirm
        i_confirm = src.index("window.confirm(termineStornoFrage(")
        i_flag = src.index("termine_stornieren: true")
        assert i_confirm < i_flag, datei
