# -*- coding: utf-8 -*-
"""Pruefbericht 20.09.2026, Block H (21.09.2026).

  AL-12/AL-15  Eine Datenbank-STOERUNG beendet den Heartbeat einer Job-Sperre
               nicht mehr (nur ein echter Besitzerwechsel); der Aufraeumlauf
               prueft die Sperre auch zwischen seinen Abschnitten
  R1-20        Korrektur-Version: Abloesen + Anlegen in EINER Transaktion
  R2-01        Kaufvorgang-Selbstheilung zieht den Vertragszeiger nach
  U-102        Terminfotos: Vorschaubilder ueber den eigenen Bild-Proxy
  DP-08        /health nimmt einen Server mit voller Platte aus der Rotation
"""
import asyncio
import inspect
import os
import sys
import uuid
from pathlib import Path
from types import SimpleNamespace

import pytest

BACKEND = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND))

MONGO_URL = os.environ.get("MONGO_URL") or "mongodb://127.0.0.1:27017"


def _module(name):
    import importlib
    return importlib.import_module(name)


_MODULE_MIT_DB = ("deps", "kaufvorgang", "cleanup_service", "routes.protocols",
                  "routes.appointments", "lifecycle")


@pytest.fixture
def frisch(monkeypatch):
    from motor.motor_asyncio import AsyncIOMotorClient
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    client = AsyncIOMotorClient(MONGO_URL, serverSelectionTimeoutMS=5000)
    name = f"autoschnell_h_{uuid.uuid4().hex[:10]}"
    db = client[name]
    for n in _MODULE_MIT_DB:
        m = _module(n)
        if hasattr(m, "db"):
            monkeypatch.setattr(m, "db", db)
    try:
        yield SimpleNamespace(db=db, run=loop.run_until_complete)
    finally:
        try:
            loop.run_until_complete(client.drop_database(name))
        finally:
            client.close()
            loop.close()


# ====================================================================== AL-12 / AL-15
def test_al12_stoerung_ist_kein_verlust():
    JL = _module("job_lock")

    class _KaputteSammlung:
        async def update_one(self, *a, **k):
            raise ConnectionError("Primary-Wechsel")

    kaputt = SimpleNamespace(job_locks=_KaputteSammlung())
    with pytest.raises(ConnectionError):
        asyncio.run(JL.verlaengern(kaputt, "probe", 60, token="t"))


def test_al12_heartbeat_ueberlebt_eine_stoerung(monkeypatch):
    JL = _module("job_lock")
    aufrufe = []

    async def verlaengern(db, name, ttl, token=None):
        aufrufe.append(1)
        if len(aufrufe) == 1:
            raise ConnectionError("Primary-Wechsel")
        return True
    monkeypatch.setattr(JL, "verlaengern", verlaengern)

    async def lauf():
        async with JL.heartbeat(None, "probe", "t", 60, intervall=0.01) as wache:
            await asyncio.sleep(0.1)
            return wache.verloren
    assert asyncio.run(lauf()) is False, "eine Stoerung beendet die Sperre nicht"
    assert len(aufrufe) >= 2


def test_al12_aufraeumlauf_prueft_zwischen_den_abschnitten(frisch):
    CS, JL = _module("cleanup_service"), _module("job_lock")
    wache = JL.Wache("cleanup-cycle")
    wache.verloren = True
    with pytest.raises(JL.SperreVerloren):
        frisch.run(CS._cleanup_once(frisch.db, wache=wache))
    q = inspect.getsource(CS.run_cleanup_forever)
    assert "_cleanup_once(d, wache=_w)" in q


# ====================================================================== R1-20
def test_r1_20_korrektur_in_einer_transaktion():
    q = inspect.getsource(_module("routes.protocols").start_correction)
    assert "await transaktion(_abloesen_und_anlegen)" in q
    kern = q[q.index("async def _abloesen_und_anlegen"):]
    assert kern.index("find_one_and_update") < kern.index("insert_one"), \
        "Abloesen und Anlegen im selben Schritt"
    assert "if session is None:" in kern, "ohne Replica-Set weiter mit Ruecknahme"


# ====================================================================== R2-01
def test_r2_01_selbstheilung_zieht_den_vertragszeiger_nach(frisch):
    KV = _module("kaufvorgang")
    db, run = frisch.db, frisch.run

    async def lauf():
        await db.kaufvorgaenge.insert_many([
            {"id": "k_richtig", "dealer_id": "d1", "vehicle_id": "v1", "contract_id": "c1",
             "status": "vertrag_erstellt"},
            {"id": "k_fremd", "dealer_id": "d1", "vehicle_id": "v9", "contract_id": "c9",
             "status": "vertrag_erstellt"}])
        await db.generated_pdfs.insert_many([
            {"id": "c1", "dealer_id": "d1", "vehicle_id": "v1", "user_id": "u1",
             "kaufvorgang_id": "k_fremd"},                       # falscher Zeiger
            {"id": "c2", "dealer_id": "d1", "vehicle_id": "v2", "user_id": "u1"}])  # ohne Vorgang
        a = await KV.fuer_vertrag(await db.generated_pdfs.find_one({"id": "c1"}, {"_id": 0}))
        b = await KV.fuer_vertrag(await db.generated_pdfs.find_one({"id": "c2"}, {"_id": 0}))
        c1 = await db.generated_pdfs.find_one({"id": "c1"}, {"_id": 0})
        c2 = await db.generated_pdfs.find_one({"id": "c2"}, {"_id": 0})
        return a, b, c1, c2

    a, b, c1, c2 = run(lauf())
    assert a["id"] == "k_richtig" and c1["kaufvorgang_id"] == "k_richtig"
    assert b and b["contract_id"] == "c2" and c2["kaufvorgang_id"] == b["id"]


# ====================================================================== U-102
def test_u102_terminfotos_ueber_den_bild_proxy():
    A = _module("routes.appointments")
    v = {"data": {"image_urls": ["https://img.classistatic.de/api/v1/mo-prod/images/aa/bild.jpg",
                                 "https://unbekannt.example/bild.jpg"]}}
    A._vorschaubilder(v)
    thumbs = v["data"]["images_thumbs"]
    assert len(thumbs) == 2
    assert thumbs[1] == "https://unbekannt.example/bild.jpg", "fremde Hosts bleiben unveraendert"
    import bild_proxy
    if bild_proxy.erlaubt(v["data"]["image_urls"][0]):
        assert thumbs[0].startswith("/api/bild?u=") and "&sig=" in thumbs[0]
    A._vorschaubilder({"data": None})                     # wirft nie


# ====================================================================== DP-08
def test_dp08_volle_platte_nimmt_den_server_aus_der_rotation(monkeypatch):
    S = _module("server")
    from collections import namedtuple
    Nutzung = namedtuple("Nutzung", "total used free")
    import shutil
    monkeypatch.setattr(shutil, "disk_usage", lambda p: Nutzung(10**9, 10**9, 10 * 1024 * 1024))
    fehler = S._platte_fehler()
    assert fehler and "MB frei" in fehler[0]
    monkeypatch.setattr(shutil, "disk_usage", lambda p: Nutzung(10**12, 0, 10**12))
    assert S._platte_fehler() == []
    assert "_platte_fehler" in inspect.getsource(S._kern_fehler)
