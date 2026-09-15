# -*- coding: utf-8 -*-
"""Go-Live 14.09.2026, Runde 11 (15.09.2026) — Phase 4 des Befundplans:

  4.1 Terminloeschung: Kernschritte atomar (Transaktion im Replica-Set,
      sonst nacheinander), Fahrzeug-Zusammenfassung mit Merker (A4 D9)
  4.3 Listen mit Deckel melden die Kuerzung (X-Truncated) und koennen
      seitenweise geladen werden (A15 A20 B29 D11 D12 G13, HTTP)
  4.4 mobile.de: kein Link bei unbekannter Marke oder unbekanntem Modell (A6 E9)
  4.5 Beweis-Randfaelle: Vormerkung mit Alarm, Cache-Rueckfall gekennzeichnet
      und eingefroren, Dokument ohne Fotos markiert (B21 B22 B23 A25)
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
import beweis_service as BS  # noqa: E402
import routes.appointments as A  # noqa: E402
import routes.manual_search as MS  # noqa: E402
import routes.admin as ADMIN  # noqa: E402

HTTP = os.environ.get("RUNDE14_HTTP") == "1"
http = pytest.mark.skipif(not HTTP, reason="RUNDE14_HTTP=1 nicht gesetzt")
MONGO_URL = os.environ.get("MONGO_URL") or "mongodb://127.0.0.1:27017"
API = K.API
SUF = uuid.uuid4().hex[:8]


def _jetzt(delta_s: int = 0) -> str:
    return (datetime.now(timezone.utc) + timedelta(seconds=delta_s)).isoformat()


@pytest.fixture
def wegwerf(monkeypatch):
    from motor.motor_asyncio import AsyncIOMotorClient
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    client = AsyncIOMotorClient(MONGO_URL, serverSelectionTimeoutMS=5000)
    name = f"autoschnell_r11_{uuid.uuid4().hex[:10]}"
    db = client[name]
    for mod in (deps, KV, LC, A, ADMIN):
        monkeypatch.setattr(mod, "db", db)
    try:
        yield SimpleNamespace(db=db, run=loop.run_until_complete)
    finally:
        try:
            loop.run_until_complete(client.drop_database(name))
        finally:
            client.close()
            loop.close()


# ============================================================ 4.1
def test_41_terminloeschung_kernschritte(wegwerf, monkeypatch):
    db, run = wegwerf.db, wegwerf.run
    monkeypatch.setattr(A, "_REPLICA_SET", {"bis": 0.0, "ist": False})
    run(db.vehicles.insert_one({"id": "v1", "dealer_id": "d1", "lifecycle": "abholung_geplant"}))
    run(db.generated_pdfs.insert_one({"id": "c1", "dealer_id": "d1", "vehicle_id": "v1",
                                      "user_id": "chef", "appointment_id": "t1"}))
    run(db.kaufvorgaenge.insert_one({"id": "k1", "dealer_id": "d1", "vehicle_id": "v1",
                                     "contract_id": "c1", "user_id": "chef",
                                     "status": "abholung_geplant", "appointment_id": "t1",
                                     "created_at": _jetzt(), "updated_at": _jetzt()}))
    run(db.appointments.insert_one({"id": "t1", "dealer_id": "d1", "vehicle_id": "v1",
                                    "contract_id": "c1", "status": "offen",
                                    "created_at": _jetzt(), "updated_at": _jetzt()}))
    user = {"id": "chef", "dealer_id": "d1", "role": "dealer"}
    assert run(A.delete_appointment("t1", user)) == {"ok": True}
    assert run(db.appointments.find_one({"id": "t1"})) is None
    k1 = run(db.kaufvorgaenge.find_one({"id": "k1"}))
    assert k1["status"] == "vertrag_erstellt" and k1["appointment_id"] is None
    assert run(db.generated_pdfs.find_one({"id": "c1"}))["appointment_id"] is None
    assert "nacharbeit_offen" not in k1
    # Replica-Set-Erkennung ist zwischengespeichert; ohne Replica-Set laeuft fn(None)
    assert run(A._ist_replica_set()) is False
    q = inspect.getsource(A._transaktion)
    assert "start_transaction" in q and "PyMongoError" in q
    # Loeschung eines fremden Termins bleibt 404 (nichts geschrieben)
    with pytest.raises(Exception) as e:
        run(A.delete_appointment("t-gibts-nicht", user))
    assert getattr(e.value, "status_code", None) == 404


# ============================================================ 4.4
def test_44_mobile_ohne_modell_kein_link():
    q = inspect.getsource(MS)
    assert "if mo_make_id and (not body.model or mo_model_id) else None" in q
    assert "kein mobile.de-Link" in q
    assert "zeigt die ganze Marke" not in q and "OHNE Markenfilter." not in q


# ============================================================ 4.5
def test_45_beweis_randfaelle(wegwerf, monkeypatch):
    db, run = wegwerf.db, wegwerf.run
    # B21: gescheiterte Vormerkung -> Alarm
    class _Kaputt:
        def __getattr__(self, n):
            raise RuntimeError("db weg")

    class _Db:
        name = "x"
        inserat_beweise = _Kaputt()

    alarme = []

    async def _alarm(dbx, typ, ref="", **details):
        alarme.append((typ, ref))
    import betrieb
    monkeypatch.setattr(betrieb, "alarm", _alarm)
    monkeypatch.setattr(BS, "beweis_indizes_sichern", lambda dbx: _true())
    assert run(BS.beweis_vormerken(_Db(), cache_key="ck1", quelle="mobile", item_id="1",
                                   url="https://x", anlass="test")) is None
    assert ("beweis_vormerkung_fehlgeschlagen", "ck1") in alarme
    # B22/B23/A25: Erzeugung aus dem Cache -> gekennzeichnet, eingefroren, ohne Fotos markiert
    run(db.listings_cache.insert_one({"cache_key": "ck2", "url": "https://m/1",
                                      "fetched_at": _jetzt(-60),
                                      "data": {"title": "Golf", "images": ["https://m/a.jpg"]}}))
    doc = {"id": "b2", "cache_key": "ck2", "quelle": "mobile", "item_id": "1",
           "status": "in_arbeit", "bearbeiter": "w", "versuche": BS.MAX_VERSUCHE}
    run(db.inserat_beweise.insert_one(dict(doc)))

    async def _keine_fotos(urls):
        return [None for _ in urls]
    monkeypatch.setattr(BS, "_fotos_laden", _keine_fotos)
    import beweis_pdf
    monkeypatch.setattr(beweis_pdf, "beweis_pdf", lambda **k: b"%PDF-1.4 probe")
    import storage_service

    async def _save(key, data):
        return key
    monkeypatch.setattr(storage_service, "save_async", _save)
    assert run(BS.beweis_erzeugen(db, doc)) is True
    nach = run(db.inserat_beweise.find_one({"id": "b2"}))
    assert nach["status"] == "fertig" and nach["daten_quelle"] == "cache"
    assert nach["ohne_fotos"] is True and nach["fotos_eingebettet"] == 0
    assert isinstance(nach.get("quelle_daten"), dict) and nach.get("quelle_abgerufen_am")
    aus = BS.oeffentlich(nach)
    assert aus["daten_quelle"] == "cache" and aus["ohne_fotos"] is True


async def _true():
    return True


# ============================================================ 4.3 (HTTP)
@http
def test_43_listen_melden_die_kuerzung():
    SA = K.super_kopf()
    dbx = K._db()
    ids = [f"pr_{SUF}_{i}" for i in range(3)]
    dbx.plan_requests.insert_many([
        {"id": i, "status": "offen", "type": "abo", "company_name": f"R11 {i}",
         "created_at": _jetzt(-n)} for n, i in enumerate(ids)])
    try:
        r = requests.get(f"{API}/admin/plan-requests?status=offen&limit=2", headers=SA, timeout=60)
        assert r.status_code == 200, r.text[:300]
        assert len(r.json()) == 2 and r.headers.get("X-Truncated") == "1"
        r2 = requests.get(f"{API}/admin/plan-requests?status=offen&limit=2&seite=2", headers=SA, timeout=60)
        assert r2.status_code == 200 and len(r2.json()) >= 1
        r3 = requests.get(f"{API}/admin/plan-requests?status=offen&limit=1000", headers=SA, timeout=60)
        assert r3.status_code == 200 and r3.headers.get("X-Truncated") is None
        r4 = requests.get(f"{API}/admin/drivers?limit=1", headers=SA, timeout=60)
        assert r4.status_code == 200 and len(r4.json()) <= 1
        r5 = requests.get(f"{API}/admin/buyers?limit=1", headers=SA, timeout=60)
        assert r5.status_code == 200 and len(r5.json()) <= 1
    finally:
        dbx.plan_requests.delete_many({"id": {"$in": ids}})
