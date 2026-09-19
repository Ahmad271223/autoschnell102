# -*- coding: utf-8 -*-
"""Runde 28 (12.09.2026, Pruefbefunde zur Konto-Trennung):

  1. Termin auf das Auto eines Kollegen: Beim Anlegen wurde nur geprueft, ob
     das Fahrzeug der FIRMA gehoert. Fahrzeug-IDs sind ratbar (v_<Anzeigen-
     nummer>), also konnte Sucher B einen Termin auf das Auto von Sucher A
     legen — und ohne eigenen Vertrag ueber try_set_lifecycle sogar dessen
     Fahrzeugstatus veraendern.
  2. Link-Warteschlange: Ein einzelnes Konto konnte beliebig viele Links
     einreihen; der Worker arbeitete rein nach Alter (FIFO) und liess damit
     alle anderen Sucher warten.

In-Prozess gegen eine Wegwerf-DB; kein Server.
"""
import asyncio
import importlib
import sys
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from fastapi import HTTPException

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

MONGO_URL = "mongodb://127.0.0.1:27017"
FIRMA = {"company_name": "Firma R28 GmbH", "contact_person": "Chef",
         "address": "Weg 1", "zip_code": "30159", "city": "Hannover",
         "phone": "0511 1", "email": "firma@r28.test"}


def _jetzt():
    return datetime.now(timezone.utc).isoformat()


def _modul(name):
    return importlib.import_module(name)


@pytest.fixture
def welt():
    from motor.motor_asyncio import AsyncIOMotorClient
    s = uuid.uuid4().hex[:10]
    namen = ["deps", "routes.appointments", "routes.contracts", "routes.listings",
             "kaufvorgang", "lifecycle", "auto_daten", "link_jobs"]
    mods = [_modul(n) for n in namen]
    alt = [(m, getattr(m, "db", None)) for m in mods]

    class _W:
        pass

    w = _W()
    w.s = s
    w.dealer_id = f"d_r28_{s}"
    w.chef = {"id": f"chef_r28_{s}", "dealer_id": w.dealer_id, "role": "dealer"}
    w.a = {"id": f"sa_r28_{s}", "dealer_id": w.dealer_id, "role": "sucher"}
    w.b = {"id": f"sb_r28_{s}", "dealer_id": w.dealer_id, "role": "sucher"}
    w.loop = asyncio.new_event_loop()
    asyncio.set_event_loop(w.loop)
    w.client = AsyncIOMotorClient(MONGO_URL, serverSelectionTimeoutMS=5000)
    w.db_name = f"autoschnell_r28_{s}"
    w.db = w.client[w.db_name]
    for m in mods:
        if hasattr(m, "db"):
            m.db = w.db
    w.run = lambda coro: w.loop.run_until_complete(coro)
    w.run(w.db.users.insert_many([{**u, "active": True, "created_at": _jetzt()}
                                  for u in (w.chef, w.a, w.b)]))
    w.run(w.db.dealers.insert_one({"id": w.dealer_id, "user_id": w.chef["id"],
                                   **FIRMA, "created_at": _jetzt()}))
    yield w
    try:
        w.run(w.client.drop_database(w.db_name))
    finally:
        for m, d in alt:
            if d is not None:
                m.db = d
        w.client.close()
        w.loop.close()


def _fahrzeug(w, vid, besitzer, **extra):
    doc = {"id": vid, "dealer_id": w.dealer_id, "owner_user_id": besitzer["id"],
           "lifecycle": "verglichen", "status": "verglichen", "source": "plattform",
           "data": {"make_label": "BMW", "model_label": "320d"},
           "created_at": _jetzt(), "updated_at": _jetzt()}
    doc.update(extra)
    return doc


def _termin_body(A, **extra):
    daten = {"title": "Fahrzeug abholen", "pickup_date": "2099-01-01",
             "pickup_time": "10:00", "seller_name": "Verkäufer V"}
    daten.update(extra)
    return A.AppointmentIn(**daten)


# ------------------------------------------- 1. Termin nur am eigenen Auto
def test_01_sucher_kann_keinen_termin_auf_das_auto_des_kollegen_legen(welt):
    A = _modul("routes.appointments")
    w = welt
    vid = f"v_a_{w.s}"
    w.run(w.db.vehicles.insert_one(_fahrzeug(w, vid, w.a)))
    with pytest.raises(HTTPException) as e:
        w.run(A.create_appointment(_termin_body(A, vehicle_id=vid), w.b))
    assert e.value.status_code == 404, e.value.detail
    assert w.run(w.db.appointments.count_documents({})) == 0
    # Der Lebenszyklus des Kollegen-Fahrzeugs bleibt unangetastet
    fz = w.run(w.db.vehicles.find_one({"id": vid}))
    assert fz["lifecycle"] == "verglichen"


def test_02_eigenes_fahrzeug_und_mitbearbeiter_gehen_weiter(welt):
    A = _modul("routes.appointments")
    w = welt
    eigen = f"v_eigen_{w.s}"
    mit = f"v_mit_{w.s}"
    w.run(w.db.vehicles.insert_many([
        _fahrzeug(w, eigen, w.b),
        _fahrzeug(w, mit, w.a, mitbearbeiter_ids=[w.b["id"]]),
    ]))
    for vid in (eigen, mit):
        antwort = w.run(A.create_appointment(_termin_body(A, vehicle_id=vid), w.b))
        assert antwort["vehicle_id"] == vid, antwort
    assert w.run(w.db.appointments.count_documents({})) == 2


def test_03_eigener_vertrag_genuegt_auch_ohne_fahrzeugbereich(welt):
    """Der Vertrag ist der zweite erlaubte Weg — sonst koennte ein Sucher zu
    seinem eigenen Kaufvertrag keinen Abholtermin anlegen."""
    A = _modul("routes.appointments")
    w = welt
    vid = f"v_vertrag_{w.s}"
    w.run(w.db.vehicles.insert_one(_fahrzeug(w, vid, w.a)))
    w.run(w.db.generated_pdfs.insert_one({
        "id": f"c_{w.s}", "dealer_id": w.dealer_id, "user_id": w.b["id"],
        "vehicle_id": vid, "contract_no": "KV-1", "created_at": _jetzt(),
        "contract_data": {}}))
    antwort = w.run(A.create_appointment(_termin_body(A, vehicle_id=vid), w.b))
    assert antwort["vehicle_id"] == vid


def test_04_chef_darf_weiterhin_alles(welt):
    A = _modul("routes.appointments")
    w = welt
    vid = f"v_chef_{w.s}"
    w.run(w.db.vehicles.insert_one(_fahrzeug(w, vid, w.a)))
    antwort = w.run(A.create_appointment(_termin_body(A, vehicle_id=vid), w.chef))
    assert antwort["vehicle_id"] == vid


def test_05_termin_kann_nicht_nachtraeglich_umgebogen_werden(welt):
    """Auch beim Aendern: ein eigener Termin darf nicht auf das Auto eines
    Kollegen zeigen."""
    A = _modul("routes.appointments")
    w = welt
    fremd = f"v_fremd_{w.s}"
    w.run(w.db.vehicles.insert_one(_fahrzeug(w, fremd, w.a)))
    eigener = w.run(A.create_appointment(_termin_body(A), w.b))
    with pytest.raises(HTTPException) as e:
        w.run(A.update_appointment(eigener["id"],
                                   A.AppointmentIn(vehicle_id=fremd), w.b))
    assert e.value.status_code == 404, e.value.detail
    danach = w.run(w.db.appointments.find_one({"id": eigener["id"]}))
    assert not danach.get("vehicle_id")


# --------------------------------------- 2. Faire Link-Warteschlange
def _job(w, cache_key, user, minuten=0, status="queued"):
    return {"id": f"job_{uuid.uuid4().hex[:8]}", "cache_key": cache_key,
            "source": "kleinanzeigen", "item_id": cache_key.split(":")[-1],
            "url": f"https://www.kleinanzeigen.de/s-anzeige/x/{cache_key.split(':')[-1]}-216-1",
            "status": status, "active": status in ("queued", "processing"),
            "attempts": 0, "error": None,
            "requested_by_dealer": w.dealer_id, "requested_by_user": user["id"],
            "dealer_ids": [w.dealer_id], "user_ids": [user["id"]],
            "created_at": datetime.now(timezone.utc) - timedelta(minutes=minuten),
            "updated_at": datetime.now(timezone.utc)}


def test_06_ein_konto_kann_die_warteschlange_nicht_fluten(welt, monkeypatch):
    import link_jobs as LJ
    w = welt
    monkeypatch.setattr(LJ, "MAX_OFFEN_JE_KONTO", 2)
    monkeypatch.setattr(LJ, "MAX_OFFEN_JE_FIRMA", 99)
    w.run(w.db.link_jobs.insert_many([
        _job(w, f"kleinanzeigen:{i}", w.a, minuten=5 - i) for i in range(2)]))
    with pytest.raises(LJ.WarteschlangeVoll) as e:
        w.run(LJ.enqueue_job(w.db, f"https://www.kleinanzeigen.de/s-anzeige/x/97{uuid.uuid4().int % 10**8:08d}-216-1",
                             dealer_id=w.dealer_id, user_id=w.a["id"]))
    assert e.value.grenze == 2 and e.value.offen == 2
    assert "Warteschlange" in e.value.text
    # Der Kollege ist davon NICHT betroffen
    job = w.run(LJ.enqueue_job(w.db, f"https://www.kleinanzeigen.de/s-anzeige/x/96{uuid.uuid4().int % 10**8:08d}-216-1",
                               dealer_id=w.dealer_id, user_id=w.b["id"]))
    assert job["status"] == "queued" and job["user_ids"] == [w.b["id"]]


def test_07_firmengrenze_greift_zusaetzlich(welt, monkeypatch):
    import link_jobs as LJ
    w = welt
    monkeypatch.setattr(LJ, "MAX_OFFEN_JE_KONTO", 99)
    monkeypatch.setattr(LJ, "MAX_OFFEN_JE_FIRMA", 3)
    w.run(w.db.link_jobs.insert_many([
        _job(w, f"kleinanzeigen:1{i}", w.a if i % 2 else w.b, minuten=i) for i in range(3)]))
    with pytest.raises(LJ.WarteschlangeVoll):
        w.run(LJ.enqueue_job(w.db, f"https://www.kleinanzeigen.de/s-anzeige/x/95{uuid.uuid4().int % 10**8:08d}-216-1",
                             dealer_id=w.dealer_id, user_id=w.a["id"]))


def test_08_worker_bedient_die_konten_reihum(welt):
    """Sucher A wirft drei Links ein, B danach einen. B muss trotzdem als
    Zweiter drankommen — vorher haette er hinter allen drei gewartet."""
    import link_jobs as LJ
    w = welt
    w.run(w.db.link_jobs.insert_many([
        _job(w, f"kleinanzeigen:a{i}", w.a, minuten=10 - i) for i in range(3)]
        + [_job(w, "kleinanzeigen:b1", w.b, minuten=1)]))
    erster = w.run(LJ._claim_one(w.db))
    zweiter = w.run(LJ._claim_one(w.db))
    assert erster["requested_by_user"] == w.a["id"], "aeltester zuerst"
    assert zweiter["requested_by_user"] == w.b["id"], "danach der andere Sucher"
    dritter = w.run(LJ._claim_one(w.db))
    assert dritter["requested_by_user"] == w.a["id"]
    assert w.run(w.db.link_jobs.count_documents({"status": "processing"})) == 3


def test_09_gemeinsamer_link_wird_nicht_doppelt_geholt(welt):
    """Zwei Sucher, derselbe Link: EIN technischer Abruf, aber beide Konten
    haengen am Job (jeder sieht seinen Status)."""
    import link_jobs as LJ
    w = welt
    # wie im Betrieb: der Unique-Index sorgt fuer genau EINEN aktiven Job
    w.run(LJ.ensure_job_indexes(w.db))
    url = f"https://www.kleinanzeigen.de/s-anzeige/x/94{uuid.uuid4().int % 10**8:08d}-216-1"
    eins = w.run(LJ.enqueue_job(w.db, url, dealer_id=w.dealer_id, user_id=w.a["id"]))
    zwei = w.run(LJ.enqueue_job(w.db, url, dealer_id=w.dealer_id, user_id=w.b["id"]))
    assert eins["id"] == zwei["id"], "derselbe Job"
    doc = w.run(w.db.link_jobs.find_one({"id": eins["id"]}))
    assert set(doc["user_ids"]) == {w.a["id"], w.b["id"]}
    assert w.run(w.db.link_jobs.count_documents({"active": True})) == 1
