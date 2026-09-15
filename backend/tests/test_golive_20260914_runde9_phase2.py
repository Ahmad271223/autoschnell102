# -*- coding: utf-8 -*-
"""Go-Live 14.09.2026, Runde 9 (15.09.2026) — Phase 2 des Befundplans
(Geld und Datenkette):

  2.3  Kaufvorgang-Verweise pruefen statt blind folgen (G1 G2 G3 G15)
  2.4  Nacharbeitsmerker: Fahrzeug-Zusammenfassung meldet Scheitern, Merker am
       Vorgang, Cleanup raeumt nur bei Erfolg; Versand "gesendet"; Verweise
       auf geloeschte Termine; Termin-Update ohne 500 (E7 G4-G6 G10 D4-D6
       D8-D10, Liste 4 Nr. 2/3, Liste 3 Nr. 9)
  2.5  "erledigt" mit Protokollpflicht wie "abgeholt" (A7 D7, HTTP)
  2.6  Wieder-Oeffnen mit finalem Protokoll setzt den Vorgang nicht zurueck (D15)
  2.7  Reservierung/Kaeufersperre ziehen den Fahrzeugstatus mit, Merker statt
       Alarm (A8 B9 B10)
  2.8  Termin-Update mit Stand-Pruefung (D3, HTTP)
  2.9  Protokoll-Entwurf mit Revision (B26, HTTP)
  2.10 Fahrer-Doppelbuchung: Warnung, keine Sperre (D1 D2, HTTP)
"""
import asyncio
import inspect
import os
import sys
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest
import requests

BACKEND = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND))
sys.path.insert(0, str(BACKEND / "tests"))

import deps  # noqa: E402
import konten as K  # noqa: E402
import kaufvorgang as KV  # noqa: E402
import lifecycle as LC  # noqa: E402
import cleanup_service as CS  # noqa: E402
import routes.appointments as A  # noqa: E402
import routes.contracts as C  # noqa: E402
import routes.marketplace as M  # noqa: E402
import routes.protocols as P  # noqa: E402
from betrieb import offene_alarme  # noqa: E402

HTTP = os.environ.get("RUNDE14_HTTP") == "1"
http = pytest.mark.skipif(not HTTP, reason="RUNDE14_HTTP=1 nicht gesetzt")
MONGO_URL = os.environ.get("MONGO_URL") or "mongodb://127.0.0.1:27017"
API = K.API
SUF = uuid.uuid4().hex[:8]
PW = f"Rz9{SUF}Kq4Lm9Xw2"


def _jetzt(delta_s: int = 0) -> str:
    return (datetime.now(timezone.utc) + timedelta(seconds=delta_s)).isoformat()


@pytest.fixture
def wegwerf(monkeypatch):
    from motor.motor_asyncio import AsyncIOMotorClient
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    client = AsyncIOMotorClient(MONGO_URL, serverSelectionTimeoutMS=5000)
    name = f"autoschnell_r9_{uuid.uuid4().hex[:10]}"
    db = client[name]
    for mod in (deps, KV, LC, A, C, M, P):
        monkeypatch.setattr(mod, "db", db)
    try:
        yield SimpleNamespace(db=db, run=loop.run_until_complete)
    finally:
        try:
            loop.run_until_complete(client.drop_database(name))
        finally:
            client.close()
            loop.close()


def _kv(id_, dealer, vehicle, contract, status="vertrag_erstellt", **extra):
    return {"id": id_, "dealer_id": dealer, "vehicle_id": vehicle, "contract_id": contract,
            "user_id": "u1", "status": status, "purchase_price": 1000,
            "created_at": _jetzt(-600), "updated_at": _jetzt(-600), **extra}


# ============================================================ 2.3
def test_23_verweise_werden_geprueft(wegwerf):
    db, run = wegwerf.db, wegwerf.run
    run(db.kaufvorgaenge.insert_many([
        _kv("kvX", "d2", "v2", "cX"),                       # fremder Vorgang
        _kv("kvA", "d1", "v1", "c1"),                       # der richtige zu c1
        _kv("kvB", "d9", "v9", "c2"),                       # Vorgang zu c2 gehoert Firma d9
    ]))
    # G1: Vertrag zeigt auf fremden Vorgang -> nicht folgen, ueber contract_id finden
    c1 = {"id": "c1", "dealer_id": "d1", "vehicle_id": "v1", "user_id": "u1", "kaufvorgang_id": "kvX"}
    assert run(KV.fuer_vertrag(c1))["id"] == "kvA"
    # G15: Vorgang zum Vertrag passt nicht zur Firma -> nicht uebernehmen, Alarm
    assert run(KV.fuer_vertrag({"id": "c2", "dealer_id": "d1", "vehicle_id": "v1", "user_id": "u1"})) is None
    typen = {a["typ"] for a in run(offene_alarme(db))}
    assert "kaufvorgang_verweis_falsch" in typen
    # G2: Termin mit falschem Zeiger (nur id mitgegeben, Rest wird nachgeladen)
    run(db.appointments.insert_one({"id": "t1", "dealer_id": "d1", "vehicle_id": "v1",
                                    "contract_id": "c1", "kaufvorgang_id": "kvX",
                                    "created_at": _jetzt(), "updated_at": _jetzt()}))
    kv = run(KV.fuer_termin({"id": "t1", "contract_id": "c1", "kaufvorgang_id": "kvX"}))
    assert kv["id"] == "kvA"
    assert run(db.appointments.find_one({"id": "t1"}))["kaufvorgang_id"] == "kvA"
    # G3: Selbstheilung laedt den Vertrag nur innerhalb der Firma
    run(db.generated_pdfs.insert_many([
        {"id": "c3", "dealer_id": "d2", "vehicle_id": "v3", "user_id": "u2"},
        {"id": "c4", "dealer_id": "d1", "vehicle_id": "v4", "user_id": "u1", "purchase_price": 5},
    ]))
    run(db.appointments.insert_many([
        {"id": "t3", "dealer_id": "d1", "contract_id": "c3", "created_at": _jetzt(), "updated_at": _jetzt()},
        {"id": "t4", "dealer_id": "d1", "contract_id": "c4", "created_at": _jetzt(), "updated_at": _jetzt()},
    ]))
    assert run(KV.fuer_termin({"id": "t3"})) is None, "fremder Vertrag wird nicht geheilt"
    geheilt = run(KV.fuer_termin({"id": "t4"}))
    assert geheilt and geheilt["contract_id"] == "c4" and geheilt["dealer_id"] == "d1"
    assert run(db.appointments.find_one({"id": "t4"}))["kaufvorgang_id"] == geheilt["id"]


# ============================================================ 2.4 Vorgang/Fahrzeug
def test_24_zusammenfassung_meldet_scheitern_und_merker(wegwerf, monkeypatch):
    db, run = wegwerf.db, wegwerf.run
    run(db.vehicles.insert_one({"id": "v1", "dealer_id": "d1", "lifecycle": "abholung_geplant"}))
    run(db.kaufvorgaenge.insert_one(_kv("k1", "d1", "v1", "c1", status="abholung_geplant")))
    assert run(KV.fahrzeug_status_aggregieren("v-ohne", "d1")) == "", "kein Vorgang = nichts zu tun"

    async def kaputt(*a, **k):
        return False
    monkeypatch.setattr(KV, "try_set_lifecycle", kaputt)
    doc = run(KV.status_setzen("k1", "abgeholt"))
    assert doc["status"] == "abgeholt" and doc["fahrzeug_status"] is None
    assert run(db.kaufvorgaenge.find_one({"id": "k1"}))["nacharbeit_offen"] is True
    assert run(db.vehicles.find_one({"id": "v1"}))["lifecycle"] == "abholung_geplant"
    # Cleanup: bleibt offen, solange es scheitert (Versuche zaehlen)
    assert run(CS.kaufvorgang_nacharbeit_nachholen(db)) == 0
    assert run(db.kaufvorgaenge.find_one({"id": "k1"}))["nacharbeit_versuche"] == 1
    # ... und raeumt auf, sobald es gelingt
    monkeypatch.setattr(KV, "try_set_lifecycle", LC.try_set_lifecycle)
    assert run(CS.kaufvorgang_nacharbeit_nachholen(db)) == 1
    k1 = run(db.kaufvorgaenge.find_one({"id": "k1"}))
    assert "nacharbeit_offen" not in k1 and "nacharbeit_versuche" not in k1
    assert run(db.vehicles.find_one({"id": "v1"}))["lifecycle"] == "abgeholt"
    assert run(db.vehicles.find_one({"id": "v1"}))["abgeholt_kaufvorgang_id"] == "k1"
    # try_set_lifecycle liefert jetzt True/False
    assert run(LC.try_set_lifecycle("v1", "d1", "bestand")) is True
    assert run(LC.try_set_lifecycle("v1", "d1", "gefunden")) is False


def test_24_vertrags_nacharbeit_setzt_gesendet_nach(wegwerf):
    db, run = wegwerf.db, wegwerf.run
    run(db.vehicles.insert_one({"id": "v5", "dealer_id": "d1", "lifecycle": "vertrag_erstellt"}))
    run(db.generated_pdfs.insert_one({"id": "c5", "dealer_id": "d1", "vehicle_id": "v5", "user_id": "u1",
                                      "nacharbeit_offen": True, "nacharbeit_status": "gesendet"}))
    run(db.kaufvorgaenge.insert_one(_kv("k5", "d1", "v5", "c5")))
    assert run(CS.vertrags_nacharbeit_nachholen(db)) == 1
    assert run(db.kaufvorgaenge.find_one({"id": "k5"}))["status"] == "gesendet"
    c5 = run(db.generated_pdfs.find_one({"id": "c5"}))
    assert "nacharbeit_offen" not in c5 and "nacharbeit_status" not in c5
    assert run(db.vehicles.find_one({"id": "v5"}))["lifecycle"] == "gekauft"
    src = inspect.getsource(C)
    assert '"nacharbeit_status": "gesendet"' in src


def test_24_verweise_auf_geloeschte_termine(wegwerf):
    db, run = wegwerf.db, wegwerf.run
    jetzt = datetime.now(timezone.utc)
    alt = (jetzt - timedelta(hours=1)).isoformat()
    run(db.vehicles.insert_one({"id": "v6", "dealer_id": "d1", "lifecycle": "abholung_geplant"}))
    run(db.appointments.insert_one({"id": "t-da", "dealer_id": "d1", "created_at": alt, "updated_at": alt}))
    run(db.kaufvorgaenge.insert_many([
        _kv("k6", "d1", "v6", "c6", status="abholung_geplant", appointment_id="t-weg", updated_at=alt),
        _kv("k7", "d1", "v6", "c7", status="abholung_geplant", appointment_id="t-da", updated_at=alt),
        _kv("k8", "d1", "v6", "c8", status="abholung_geplant", appointment_id="t-frisch",
            updated_at=jetzt.isoformat()),
    ]))
    run(db.generated_pdfs.insert_many([
        {"id": "c6", "dealer_id": "d1", "appointment_id": "t-weg", "updated_at": alt},
        {"id": "c7", "dealer_id": "d1", "appointment_id": "t-da", "updated_at": alt},
    ]))
    n = run(CS.termin_verweise_bereinigen(db, jetzt))
    assert n == 2
    k6 = run(db.kaufvorgaenge.find_one({"id": "k6"}))
    assert k6["status"] == "vertrag_erstellt" and k6["appointment_id"] is None
    assert run(db.kaufvorgaenge.find_one({"id": "k7"}))["appointment_id"] == "t-da"
    assert run(db.kaufvorgaenge.find_one({"id": "k8"}))["appointment_id"] == "t-frisch", "zu frisch"
    assert run(db.generated_pdfs.find_one({"id": "c6"}))["appointment_id"] is None
    assert run(db.generated_pdfs.find_one({"id": "c7"}))["appointment_id"] == "t-da"


def test_24_termin_update_ohne_500_quelle():
    src = inspect.getsource(A.update_appointment)
    i = src.index("Phase 2 (2.4, D4-D6)")
    assert "nacharbeit_fehler = True" in src[i:] and 'merker_gesetzt = True' in src[i:]
    assert src.index("except HTTPException:", i) < src.index('meta = {"pickup_changed"', i)
    cs = inspect.getsource(CS.termin_nacharbeit_nachholen)
    assert "_vertragszeiger_abgleichen(" in cs and '.get("nacharbeit_offen")' in cs
    assert "kaufvorgang_nacharbeit_nachgeholt" in inspect.getsource(CS._cleanup_once)


# ============================================================ 2.6
def test_26_wiederoeffnen_mit_finalem_protokoll(wegwerf):
    db, run = wegwerf.db, wegwerf.run
    run(db.vehicles.insert_one({"id": "v2", "dealer_id": "d1", "lifecycle": "abgeholt",
                                "abgeholt_kaufvorgang_id": "k2"}))
    run(db.kaufvorgaenge.insert_many([
        _kv("k2", "d1", "v2", "c2", status="abgeholt", appointment_id="t2"),
        _kv("k2b", "d1", "v2b", "c2b", status="abgeholt", appointment_id="t2b"),
    ]))
    run(db.vehicles.insert_one({"id": "v2b", "dealer_id": "d1", "lifecycle": "abgeholt",
                                "abgeholt_kaufvorgang_id": "k2b"}))
    run(db.appointments.insert_many([
        {"id": "t2", "dealer_id": "d1", "vehicle_id": "v2", "contract_id": "c2", "status": "offen",
         "created_at": _jetzt(), "updated_at": _jetzt()},
        {"id": "t2b", "dealer_id": "d1", "vehicle_id": "v2b", "contract_id": "c2b", "status": "offen",
         "created_at": _jetzt(), "updated_at": _jetzt()},
    ]))
    run(db.pickup_protocols.insert_one({"id": "p2", "appointment_id": "t2", "version": 1,
                                        "status": "final", "superseded": False}))
    assert run(KV.termin_status_uebernehmen({"id": "t2"}, "offen")) is True
    assert run(db.kaufvorgaenge.find_one({"id": "k2"}))["status"] == "abgeholt",         "finales Protokoll: Vorgang bleibt abgeholt"
    assert run(db.vehicles.find_one({"id": "v2"}))["lifecycle"] == "abgeholt"
    assert run(KV.termin_status_uebernehmen({"id": "t2b"}, "offen")) is True
    assert run(db.kaufvorgaenge.find_one({"id": "k2b"}))["status"] == "abholung_geplant",         "ohne Protokoll wie bisher"


# ============================================================ 2.7
def test_27_reservierung_und_sperre_ziehen_fahrzeug_mit(wegwerf):
    db, run = wegwerf.db, wegwerf.run
    run(db.dealers.insert_one({"id": "d1", "company_name": "X", "marketplace": {"public": True}}))
    run(db.vehicles.insert_many([
        {"id": "v1", "dealer_id": "d1", "lifecycle": "reserviert"},
        {"id": "v2", "dealer_id": "d1", "lifecycle": "verkauft"},
    ]))
    run(db.resale_listings.insert_many([
        {"id": "l1", "dealer_id": "d1", "vehicle_id": "v1", "status": "reserviert",
         "reserved_for": "k1", "visibility": "public"},
        {"id": "l2", "dealer_id": "d1", "vehicle_id": "v2", "status": "veroeffentlicht",
         "visibility": "public"},
    ]))
    # B9: Reservierung zurueck -> Fahrzeug geht mit
    run(M.reservierung_zurueckgeben("l1", "k1"))
    assert run(db.resale_listings.find_one({"id": "l1"}))["status"] == "veroeffentlicht"
    assert run(db.vehicles.find_one({"id": "v1"}))["lifecycle"] == "veroeffentlicht"
    # A8: Uebergang nicht moeglich -> Merker statt nur Alarm, Cleanup holt nach
    assert run(M.inserat_fahrzeug_nachziehen("l2", "reserviert")) is False
    assert run(db.resale_listings.find_one({"id": "l2"}))["lifecycle_nacharbeit"] == "reserviert"
    assert run(CS.inserat_fahrzeug_nacharbeit_nachholen(db)) == 0
    assert run(db.resale_listings.find_one({"id": "l2"}))["nacharbeit_versuche"] == 1
    run(db.vehicles.update_one({"id": "v2"}, {"$set": {"lifecycle": "veroeffentlicht"}}))
    assert run(CS.inserat_fahrzeug_nacharbeit_nachholen(db)) == 1
    l2 = run(db.resale_listings.find_one({"id": "l2"}))
    assert "lifecycle_nacharbeit" not in l2 and "nacharbeit_versuche" not in l2
    assert run(db.vehicles.find_one({"id": "v2"}))["lifecycle"] == "reserviert"
    # B10: Kaeufersperre gibt seine Reservierung frei
    run(db.resale_listings.update_one({"id": "l1"}, {"$set": {"status": "reserviert", "reserved_for": "k1"}}))
    run(db.vehicles.update_one({"id": "v1"}, {"$set": {"lifecycle": "reserviert"}}))
    run(db.network_members.insert_one({"dealer_id": "d1", "buyer_user_id": "k1"}))
    erg = run(M.remove_network_member("k1", user={"id": "chef", "dealer_id": "d1", "role": "dealer"}))
    assert erg["reservierungen_freigegeben"] == ["l1"]
    assert run(db.resale_listings.find_one({"id": "l1"}))["status"] == "veroeffentlicht"
    assert run(db.vehicles.find_one({"id": "v1"}))["lifecycle"] == "veroeffentlicht"
    assert run(db.network_members.count_documents({"buyer_user_id": "k1"})) == 0


# ============================================================ HTTP: 2.5 / 2.8 / 2.9 / 2.10
def _welt():
    firma = K.registrieren(json={"email": f"r9chef{SUF}@example.com", "password": PW,
                                 "company_name": f"R9 Chef {SUF}", "contact_person": "Chef"})
    assert firma.status_code == 200, firma.text[:300]
    fahrer = K.fahrer_registrieren(json={"display_name": f"Fahrer R9 {SUF}", "password": PW,
                                         "email": f"r9fahrer{SUF}@example.com"})
    assert fahrer.status_code == 200, fahrer.text[:300]
    C_ = K._kopf(firma.json()["token"])
    r = requests.post(f"{API}/drivers/add", json={"driver_code": fahrer.json()["driver"]["driver_code"]},
                      headers=C_, timeout=60)
    assert r.status_code in (200, 409), r.text[:300]
    return SimpleNamespace(C=C_, F=K._kopf(fahrer.json()["token"]),
                           fid=fahrer.json()["driver"]["id"],
                           dealer_id=firma.json()["user"]["dealer_id"])


@http
def test_25_erledigt_nur_mit_protokoll():
    w = _welt()
    body = {"title": "R9 erledigt", "pickup_date": "2099-03-01", "pickup_time": "09:00",
            "driver_id": w.fid, "status": "erledigt"}
    r = requests.post(f"{API}/appointments", json=body, headers=w.C, timeout=60)
    assert r.status_code == 409 and "Erledigt" in r.text, r.text[:300]
    r = requests.post(f"{API}/appointments", json={**body, "status": "offen"}, headers=w.C, timeout=60)
    assert r.status_code == 200, r.text[:300]
    aid = r.json()["id"]
    r = requests.put(f"{API}/appointments/{aid}", json={"status": "erledigt"}, headers=w.C, timeout=60)
    assert r.status_code == 409 and "Erledigt" in r.text, r.text[:300]
    # Runde 12 (Nr. 15): "Fahrer entfernen + erledigt" im selben Aufruf umgeht die
    # Protokollpflicht nicht mehr -> 409; in zwei Schritten (erst Fahrer weg,
    # dann erledigt) bleibt der manuelle Abschluss ohne Fahrer moeglich.
    r = requests.put(f"{API}/appointments/{aid}", json={"driver_id": None, "status": "erledigt"},
                     headers=w.C, timeout=60)
    assert r.status_code == 409, r.text[:300]
    r = requests.put(f"{API}/appointments/{aid}", json={"driver_id": None}, headers=w.C, timeout=60)
    assert r.status_code == 200, r.text[:300]
    r = requests.put(f"{API}/appointments/{aid}", json={"status": "erledigt"}, headers=w.C, timeout=60)
    assert r.status_code == 200, r.text[:300]
    K._db().appointments.delete_one({"id": aid})


@http
def test_28_termin_stand_pruefung():
    w = _welt()
    r = requests.post(f"{API}/appointments", json={"title": "R9 Stand", "pickup_date": "2099-03-02"},
                      headers=w.C, timeout=60)
    assert r.status_code == 200, r.text[:300]
    aid = r.json()["id"]
    stand = requests.get(f"{API}/appointments/{aid}", headers=w.C, timeout=60).json()["updated_at"]
    r = requests.put(f"{API}/appointments/{aid}", json={"notes": "eins", "stand": stand},
                     headers=w.C, timeout=60)
    assert r.status_code == 200, r.text[:300]
    r = requests.put(f"{API}/appointments/{aid}", json={"notes": "zwei", "stand": stand},
                     headers=w.C, timeout=60)
    assert r.status_code == 409 and "neu laden" in r.text, r.text[:300]
    assert requests.get(f"{API}/appointments/{aid}", headers=w.C, timeout=60).json()["notes"] == "eins"
    r = requests.put(f"{API}/appointments/{aid}", json={"notes": "drei"}, headers=w.C, timeout=60)
    assert r.status_code == 200, "ohne Stand wie bisher"
    K._db().appointments.delete_one({"id": aid})


@http
def test_29_protokoll_entwurf_revision():
    w = _welt()
    r = requests.post(f"{API}/appointments", json={"title": "R9 Revision", "pickup_date": "2099-03-03",
                                                    "pickup_time": "08:00", "driver_id": w.fid},
                      headers=w.C, timeout=60)
    assert r.status_code == 200, r.text[:300]
    aid = r.json()["id"]
    # Fahrer nimmt die Fahrt an — erst dann ist das Protokoll zugaenglich
    r = requests.put(f"{API}/driver/appointments/{aid}/zuteilung", json={"action": "annehmen"},
                     headers=w.F, timeout=60)
    assert r.status_code == 200, r.text[:300]
    pfad = f"{API}/driver/appointments/{aid}/protocol"
    r = requests.put(pfad, json={"notes": "a"}, headers=w.F, timeout=60)
    assert r.status_code == 200, r.text[:300]
    assert r.json()["revision"] == 1
    r = requests.put(pfad, json={"notes": "b", "revision": 1}, headers=w.F, timeout=60)
    assert r.status_code == 200 and r.json()["revision"] == 2, r.text[:300]
    r = requests.put(pfad, json={"notes": "c", "revision": 1}, headers=w.F, timeout=60)
    assert r.status_code == 409 and "anderen Tab" in r.text, r.text[:300]
    dbx = K._db()
    assert dbx.pickup_protocols.find_one({"appointment_id": aid})["notes"] == "b"
    # Runde 13 (Liste 3 Nr. 9): ohne Revision wird ein Entwurf mit Revision nicht mehr ueberschrieben
    r = requests.put(pfad, json={"notes": "d"}, headers=w.F, timeout=60)
    assert r.status_code == 409 and "Revision" in r.text, r.text[:300]
    r = requests.put(pfad, json={"notes": "d", "revision": 2}, headers=w.F, timeout=60)
    assert r.status_code == 200 and r.json()["revision"] == 3
    dbx.pickup_protocols.delete_many({"appointment_id": aid})
    dbx.appointments.delete_one({"id": aid})


@http
def test_210_doppelbuchung_warnt_nur():
    w = _welt()
    body = {"title": "R9 Fahrt 1", "pickup_date": "2099-03-04", "pickup_time": "11:00", "driver_id": w.fid}
    r1 = requests.post(f"{API}/appointments", json=body, headers=w.C, timeout=60)
    assert r1.status_code == 200 and not r1.json().get("doppelbuchung"), r1.text[:300]
    r2 = requests.post(f"{API}/appointments", json={**body, "title": "R9 Fahrt 2"}, headers=w.C, timeout=60)
    assert r2.status_code == 200, "Doppelbuchung wird nicht gesperrt"
    assert r2.json().get("doppelbuchung") is True and "bereits eine andere Fahrt" in r2.json()["hinweis"]
    r = requests.put(f"{API}/appointments/{r1.json()['id']}", json={"notes": "x"}, headers=w.C, timeout=60)
    assert r.status_code == 200 and r.json().get("doppelbuchung") is True
    r = requests.put(f"{API}/appointments/{r1.json()['id']}", json={"pickup_time": "12:00"},
                     headers=w.C, timeout=60)
    assert r.status_code == 200 and not r.json().get("doppelbuchung")
    K._db().appointments.delete_many({"id": {"$in": [r1.json()["id"], r2.json()["id"]]}})
