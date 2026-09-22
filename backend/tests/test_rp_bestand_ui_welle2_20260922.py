# -*- coding: utf-8 -*-
"""Rollenprüfung 22.09.2026, Welle 2 — Übergaben an Team Bestand/Oberfläche.

  RP-454   "Löschen" mit offenem Abholtermin -> 409 (abgeschlossene/stornierte
           Termine blockieren nicht)
  RP-132   Akte: "hauptaccount" nach dem Zeiger dealers.user_id, nicht nach
           der rohen Rolle (zweites dealer-Konto = kein Hauptaccount)
  RP-474   apply-deviations fällt ohne Abhol-Check auf das unterschriebene
           Abholprotokoll zurück (km -> data.mileage, Schäden -> known_defects);
           die Akte bietet den Befund dem Chef an, nie dem Sucher
  RP-045   Oberfläche: Weiterverkaufen-Knöpfe hängen am Marktplatz-Schalter
           (Quelltext-Wache; das Verhalten prüft vitest)

In-Prozess gegen eine Wegwerf-DB (autoschnell_rpbu2_<uuid>), kein Server.
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
FRONTEND_SRC = WURZEL / "frontend" / "src"


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
             "routes.contracts", "routes.appointments", "routes.marketplace"]
    mods = [_module(n) for n in names]
    alt = [(m, getattr(m, "db", None)) for m in mods]

    class _W:
        pass

    w = _W()
    w.s = s
    w.dealer_id = f"d_rpbu2_{s}"
    w.chef = {"id": f"chef_rpbu2_{s}", "dealer_id": w.dealer_id, "role": "dealer"}
    w.sucher = {"id": f"su_rpbu2_{s}", "dealer_id": w.dealer_id, "role": "sucher"}
    w.loop = asyncio.new_event_loop()
    asyncio.set_event_loop(w.loop)
    w.client = AsyncIOMotorClient(MONGO_URL, serverSelectionTimeoutMS=5000)
    w.db_name = f"autoschnell_rpbu2_{s}"
    w.db = w.client[w.db_name]
    for m in mods:
        if hasattr(m, "db"):
            m.db = w.db
    w.run = lambda coro: w.loop.run_until_complete(coro)
    w.run(w.db.users.insert_many([
        {**w.chef, "active": True, "first_name": "Chef", "last_name": "C",
         "created_at": "2026-01-02T00:00:00+00:00"},
        {**w.sucher, "active": True, "first_name": "Sam", "last_name": "S",
         "created_at": "2026-01-03T00:00:00+00:00"}]))
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
           "data": {"make_label": "BMW", "model_label": "320d", "mileage": 100000,
                    "image_urls": ["https://x.invalid/1.jpg"]},
           "status": "verglichen", "created_at": _jetzt(), "updated_at": _jetzt()}
    doc.update(extra)
    return doc


def _termin(w, tid, vid, status, **extra):
    doc = {"id": tid, "dealer_id": w.dealer_id, "vehicle_id": vid, "status": status,
           "created_by": w.chef["id"], "pickup_date": "2026-09-30",
           "created_at": _jetzt(), "updated_at": _jetzt()}
    doc.update(extra)
    return doc


def _protokoll(w, pid, vid, tid, km=123456, schaeden=None, **extra):
    doc = {"id": pid, "dealer_id": w.dealer_id, "vehicle_id": vid, "appointment_id": tid,
           "status": "final", "superseded": False, "version": 1,
           "finalized_at": _jetzt(), "created_at": _jetzt(),
           "condition": {"mileage": km},
           "new_damages": schaeden if schaeden is not None else [
               {"type_label": "Kratzer", "zone": "Tür links"}]}
    doc.update(extra)
    return doc


def _ohne_bericht(monkeypatch, bericht=None):
    """abholbericht.massgeblicher_bericht fest vorgeben (kein Abhol-Check)."""
    import abholbericht

    async def _bericht(db, vehicle_id, dealer_id, nur_termine=None):
        return bericht
    monkeypatch.setattr(abholbericht, "massgeblicher_bericht", _bericht)


# ============================================================ RP-454
@pytest.mark.parametrize("status", ["offen", "bestätigt", "verschoben", "in Bearbeitung", ""])
def test_01_loeschen_mit_offenem_termin_409(welt, status):
    B = _module("routes.bestand")
    w = welt
    vid = f"v_del_{w.s}"
    w.run(w.db.vehicles.insert_one(_fahrzeug(w, vid)))
    w.run(w.db.appointments.insert_many([
        _termin(w, f"t_alt_{w.s}", vid, "abgeholt"),
        _termin(w, f"t_zwei_{w.s}", vid, status)]))
    with pytest.raises(HTTPException) as e:
        w.run(B.vehicle_decision(vid, B.DecisionIn(decision="loeschen"), w.chef))
    assert e.value.status_code == 409
    # Welle B2 (RP-454): detail = {msg, code, termine} — die Oberflaeche baut
    # daraus die Rueckfrage "Offene Termine stornieren und Fahrzeug löschen".
    detail = e.value.detail
    assert "offenen Abholtermin" in detail["msg"] and "stornieren" in detail["msg"]
    assert detail["code"] == "termine_offen"
    assert [t["id"] for t in detail["termine"]] == [f"t_zwei_{w.s}"]
    v = w.run(w.db.vehicles.find_one({"id": vid}))
    assert v["lifecycle"] == "abgeholt" and v["data"]["image_urls"], "nichts geaendert"
    assert w.run(w.db.appointments.find_one({"id": f"t_zwei_{w.s}"}))["status"] == status


def test_02_loeschen_ohne_offenen_termin_geht(welt):
    B = _module("routes.bestand")
    w = welt
    vid = f"v_del2_{w.s}"
    w.run(w.db.vehicles.insert_one(_fahrzeug(w, vid)))
    w.run(w.db.appointments.insert_many([
        _termin(w, f"t_a_{w.s}", vid, "abgeholt"),
        _termin(w, f"t_s_{w.s}", vid, "storniert"),
        # anderes Fahrzeug / andere Firma zaehlt nicht
        _termin(w, f"t_x_{w.s}", f"v_anders_{w.s}", "offen"),
        {**_termin(w, f"t_y_{w.s}", vid, "offen"), "dealer_id": "fremde_firma"}]))
    r = w.run(B.vehicle_decision(vid, B.DecisionIn(decision="loeschen"), w.chef))
    assert r["lifecycle"] == "geloescht"
    v = w.run(w.db.vehicles.find_one({"id": vid}))
    assert v["lifecycle"] == "geloescht" and v["data"]["image_urls"] == []


# ============================================================ RP-132
def test_03_hauptaccount_nach_zeiger_nicht_nach_rolle(welt, monkeypatch):
    B = _module("routes.bestand")
    w = welt
    _ohne_bericht(monkeypatch)
    zweit = {"id": f"zweit_rpbu2_{w.s}", "dealer_id": w.dealer_id, "role": "dealer",
             "active": True, "first_name": "Zweit", "last_name": "Konto",
             # aelter als der Chef: die alte Rollen-Regel UND ein Altbestand-
             # Rueckfall haetten ihn zum Hauptaccount gemacht
             "created_at": "2026-01-01T00:00:00+00:00"}
    w.run(w.db.users.insert_one(zweit))
    w.run(w.db.vehicles.insert_many([
        _fahrzeug(w, f"v_z_{w.s}", owner_user_id=zweit["id"]),
        _fahrzeug(w, f"v_c_{w.s}", owner_user_id=w.chef["id"])]))
    akte_z = w.run(B.vehicle_akte(f"v_z_{w.s}", dict(w.chef)))
    akte_c = w.run(B.vehicle_akte(f"v_c_{w.s}", dict(w.chef)))
    assert akte_z["owner"]["id"] == zweit["id"] and akte_z["owner"]["hauptaccount"] is False
    assert akte_c["owner"]["hauptaccount"] is True


def test_04_hauptaccount_quelltext_ohne_rohe_rolle():
    quelle = (WURZEL / "backend" / "routes" / "bestand.py").read_text(encoding="utf-8")
    block = quelle[quelle.index('owner = {"id": v["owner_user_id"]'):]
    block = block[:block.index("}") + 1]
    assert "haupt_chef_id" in block and '"role"' not in block


# ============================================================ RP-474
def test_05_uebernahme_ohne_abholcheck_aus_dem_protokoll(welt, monkeypatch):
    B = _module("routes.bestand")
    w = welt
    _ohne_bericht(monkeypatch)
    vid = f"v_pr_{w.s}"
    tid = f"t_pr_{w.s}"
    w.run(w.db.vehicles.insert_one(_fahrzeug(w, vid, known_defects=["Delle Heck"])))
    w.run(w.db.appointments.insert_one(_termin(w, tid, vid, "abgeholt")))
    w.run(w.db.pickup_protocols.insert_one(_protokoll(w, f"p_{w.s}", vid, tid)))
    out = w.run(B.apply_deviations(vid, B.ApplyDeviationsIn(deviation_ids=["abholprotokoll"]),
                                   w.chef))
    assert {"feld": "Kilometerstand", "neu": 123456, "quelle": "abholprotokoll"} in out["applied"]
    assert out["known_defects"] == ["Delle Heck", "Kratzer: Tür links"]
    v = w.run(w.db.vehicles.find_one({"id": vid}))
    assert v["data"]["mileage"] == 123456
    assert v["data"]["image_urls"], "nur geaenderte data-Felder (Dotted-Path)"
    assert v["known_defects"] == ["Delle Heck", "Kratzer: Tür links"]
    log = w.run(w.db.activity_logs.find_one({"action": "fahrzeug.abweichungen.uebernommen"}))
    assert log and log["meta"]["abholprotokoll"] is True
    # zweimal uebernehmen: keine Dubletten
    out2 = w.run(B.apply_deviations(vid, B.ApplyDeviationsIn(deviation_ids=["abholprotokoll"]),
                                    w.chef))
    assert out2["known_defects"] == ["Delle Heck", "Kratzer: Tür links"]
    assert not [a for a in out2["applied"] if a.get("mangel")]


def test_06_ohne_bericht_und_ohne_protokoll_404(welt, monkeypatch):
    B = _module("routes.bestand")
    w = welt
    _ohne_bericht(monkeypatch)
    vid = f"v_leer_{w.s}"
    tid = f"t_leer_{w.s}"
    w.run(w.db.vehicles.insert_one(_fahrzeug(w, vid)))
    # Protokoll eines STORNIERTEN Termins zaehlt nicht (V-12)
    w.run(w.db.appointments.insert_one(_termin(w, tid, vid, "storniert")))
    w.run(w.db.pickup_protocols.insert_one(_protokoll(w, f"p_st_{w.s}", vid, tid)))
    with pytest.raises(HTTPException) as e:
        w.run(B.apply_deviations(vid, B.ApplyDeviationsIn(deviation_ids=["d1"]), w.chef))
    assert e.value.status_code == 404
    assert w.run(w.db.vehicles.find_one({"id": vid}))["data"]["mileage"] == 100000


def test_07_mit_bericht_geht_das_protokoll_vor(welt, monkeypatch):
    B = _module("routes.bestand")
    w = welt
    vid = f"v_beide_{w.s}"
    tid = f"t_beide_{w.s}"
    _ohne_bericht(monkeypatch, {
        "id": "r1", "appointment_id": tid, "mileage_at_pickup": 110000,
        "deviations": [{"id": "d1", "field": "mileage", "label": "km"},
                       {"id": "d2", "field": "damage", "label": "Kratzer",
                        "actual": "Stoßstange"}]})
    w.run(w.db.vehicles.insert_one(_fahrzeug(w, vid)))
    w.run(w.db.appointments.insert_one(_termin(w, tid, vid, "erledigt")))
    w.run(w.db.pickup_protocols.insert_one(_protokoll(w, f"p_b_{w.s}", vid, tid, km=111111)))
    # Nur Bericht-Abweichungen gewaehlt: Protokoll bleibt aussen vor (wie bisher)
    out = w.run(B.apply_deviations(vid, B.ApplyDeviationsIn(deviation_ids=["d1", "d2"]), w.chef))
    assert w.run(w.db.vehicles.find_one({"id": vid}))["data"]["mileage"] == 110000
    assert "Kratzer: Tür links" not in out["known_defects"]
    # Mit Protokoll: dessen km gilt, ein einziger Kilometerstand-Eintrag
    out = w.run(B.apply_deviations(
        vid, B.ApplyDeviationsIn(deviation_ids=["d1", "abholprotokoll"]), w.chef))
    km = [a for a in out["applied"] if a.get("feld") == "Kilometerstand"]
    assert km == [{"feld": "Kilometerstand", "neu": 111111, "quelle": "abholprotokoll"}]
    assert w.run(w.db.vehicles.find_one({"id": vid}))["data"]["mileage"] == 111111
    assert "Kratzer: Tür links" in out["known_defects"]


def test_08_akte_zeigt_befund_nur_dem_chef(welt, monkeypatch):
    B = _module("routes.bestand")
    w = welt
    _ohne_bericht(monkeypatch)
    vid = f"v_ak_{w.s}"
    tid = f"t_ak_{w.s}"
    w.run(w.db.vehicles.insert_one(_fahrzeug(w, vid, mitbearbeiter_ids=[w.sucher["id"]])))
    w.run(w.db.appointments.insert_one(_termin(w, tid, vid, "abgeholt",
                                               created_by=w.sucher["id"])))
    w.run(w.db.pickup_protocols.insert_one(_protokoll(w, f"p_ak_{w.s}", vid, tid)))
    akte = w.run(B.vehicle_akte(vid, dict(w.chef)))
    assert akte["protokoll_befund"] == {"km": 123456, "schaeden": ["Kratzer: Tür links"]}
    akte_s = w.run(B.vehicle_akte(vid, dict(w.sucher)))
    assert akte_s["protokoll_befund"] is None
    # ohne verwertbares Protokoll: None statt leerer Huelle
    vid2 = f"v_ak2_{w.s}"
    w.run(w.db.vehicles.insert_one(_fahrzeug(w, vid2)))
    assert w.run(B.vehicle_akte(vid2, dict(w.chef)))["protokoll_befund"] is None


# ============================================================ RP-045 (Quelltext)
def test_09_weiterverkaufen_nur_mit_marktplatz_schalter():
    for datei in ("Bestand.jsx", "FahrzeugAkte.jsx"):
        quelle = (FRONTEND_SRC / "pages" / "app" / datei).read_text(encoding="utf-8")
        for m in re.finditer(r"Speichern & weiterverkaufen|> Weiterverkaufen|Jetzt inserieren", quelle):
            davor = quelle[max(0, m.start() - 900):m.start()]
            assert "features.marktplatz" in davor, f"{datei}: {m.group(0)} ohne Marktplatz-Schalter"


def test_10_features_fehler_wird_nicht_zwischengespeichert():
    quelle = (FRONTEND_SRC / "lib" / "features.js").read_text(encoding="utf-8")
    fang = quelle[quelle.index(".catch("):]
    fang = fang[:fang.index(")") + 30]
    assert "cache =" not in fang
