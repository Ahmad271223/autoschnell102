# -*- coding: utf-8 -*-
"""Pruefung 14.09.2026 — Bereich "loeschung": Vertragsfrist und Protokoll-
Personendaten.

  D1   Die 90-Tage-Frist lief ab dem ANLEGEN des Vertrags: ein Vertrag mit
       noch offenem Abholtermin oder mit einem frisch unterschriebenen
       Protokoll wurde mitsamt Unterschriften und Protokoll-PDF geloescht.
       Jetzt: zurueckstellen, bis Termin geschlossen und Protokoll aelter
       als die Frist sind.
  C21  Protokolle wurden nur ueber die Termine des Vertrags gefunden. Wurde
       der Termin nach dem Abschluss umgehaengt oder vom Vertrag geloest,
       blieb das unterschriebene Protokoll (Name, Ort, PDF) stehen. Jetzt
       zusaetzlich ueber pickup_protocols.contract_id.

Wie test_loeschung.py: lokale Mongo, `now` im Jahr 2001, Testdaten im Jahr 2000.
"""
import asyncio
import os
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

MONGO_URL = os.environ.get("MONGO_URL") or "mongodb://127.0.0.1:27017"
DB_NAME = os.environ.get("DB_NAME") or "autoschnell"
SUF = uuid.uuid4().hex[:10]
DEALER = f"d_gl14l_{SUF}"
ALT = datetime(2000, 3, 1, tzinfo=timezone.utc).isoformat()
JUNG = datetime(2000, 12, 20, tzinfo=timezone.utc).isoformat()   # < 90 Tage vor NOW
NOW = datetime(2001, 1, 1, tzinfo=timezone.utc)


def _run(coro_factory):
    async def _inner():
        from motor.motor_asyncio import AsyncIOMotorClient
        cl = AsyncIOMotorClient(MONGO_URL, serverSelectionTimeoutMS=5000)
        try:
            return await coro_factory(cl[DB_NAME])
        finally:
            cl.close()
    return asyncio.run(_inner())


async def _aufraeumen(db):
    for coll in ("generated_pdfs", "generated_pdf_versions", "appointments",
                 "pickup_protocols", "admin_vehicle_data", "activity_logs",
                 "betriebsalarme", "storage_delete_retry", "kaufvorgaenge"):
        await db[coll].delete_many({"$or": [{"id": {"$regex": SUF}}, {"dealer_id": DEALER},
                                            {"ref": {"$regex": SUF}}]})


@pytest.fixture(autouse=True)
def _sauber():
    yield
    _run(_aufraeumen)


async def _vertrag(db, name, *, termin_status=None, finalized_at=None):
    cid, avd = f"c_{name}_{SUF}", f"avd_{name}_{SUF}"
    await db.admin_vehicle_data.insert_one({"id": avd, "brand": "T", "damages": [],
                                            "schema_version": 2})
    await db.generated_pdfs.insert_one({"id": cid, "dealer_id": DEALER, "contract_no": name,
                                        "created_at": ALT, "admin_vehicle_data_id": avd})
    if termin_status is not None:
        aid = f"a_{name}_{SUF}"
        await db.appointments.insert_one({"id": aid, "dealer_id": DEALER, "contract_id": cid,
                                          "status": termin_status, "seller_name": "Max"})
        if finalized_at:
            await db.pickup_protocols.insert_one(
                {"id": f"p_{name}_{SUF}", "appointment_id": aid, "dealer_id": DEALER,
                 "status": "final", "finalized_at": finalized_at, "seller_name": "Max",
                 "place": "Ort", "pdf_path": f"protocol/{DEALER}/{name}.pdf"})
    return cid


def test_d1_offener_termin_oder_junges_protokoll_stellt_den_vertrag_zurueck(monkeypatch):
    monkeypatch.setenv("VERTRAG_LOESCHUNG_AKTIV", "true")

    async def lauf(db):
        import cleanup_service as cs
        ohne = await _vertrag(db, "ohne")                                   # kein Termin
        offen = await _vertrag(db, "offen", termin_status="offen")
        verschoben = await _vertrag(db, "versch", termin_status="verschoben")
        jung = await _vertrag(db, "jung", termin_status="abgeholt", finalized_at=JUNG)
        alt = await _vertrag(db, "alt", termin_status="abgeholt", finalized_at=ALT)
        storno = await _vertrag(db, "storno", termin_status="storniert")
        stats = {}
        n = await cs.vertraege_nach_frist_loeschen(db, NOW, stats=stats)
        assert n == 3 and stats["contracts_zurueckgestellt"] == 3, stats
        for cid in (ohne, alt, storno):
            assert await db.generated_pdfs.find_one({"id": cid}) is None, cid
        for cid in (offen, verschoben, jung):
            assert await db.generated_pdfs.find_one({"id": cid}) is not None, cid
        # Protokoll des jungen Abschlusses: unangetastet
        p = await db.pickup_protocols.find_one({"id": f"p_jung_{SUF}"}, {"_id": 0})
        assert p["seller_name"] == "Max" and p.get("pdf_path")
        # Kein Alarm fuer das Zurueckstellen (normaler Zustand)
        assert await db.betriebsalarme.count_documents({"ref": {"$in": [offen, jung]}}) == 0
        # Termin geschlossen + Protokoll alt -> beim naechsten Lauf weg
        await db.appointments.update_one({"contract_id": offen}, {"$set": {"status": "erledigt"}})
        await db.pickup_protocols.update_one({"id": f"p_jung_{SUF}"},
                                             {"$set": {"finalized_at": ALT}})
        assert await cs.vertraege_nach_frist_loeschen(db, NOW) == 2
        assert await db.generated_pdfs.find_one({"id": verschoben}) is not None

    _run(lauf)


def test_c21_protokoll_am_umgehaengten_termin_wird_ueber_contract_id_bereinigt():
    async def lauf(db):
        import cleanup_service as cs
        cid, anderer = f"c_um_{SUF}", f"c_anderer_{SUF}"
        aid = f"a_um_{SUF}"
        await db.generated_pdfs.insert_one({"id": cid, "dealer_id": DEALER, "contract_no": "U",
                                            "created_at": ALT})
        # Termin haengt inzwischen an einem ANDEREN Vertrag ...
        await db.appointments.insert_one({"id": aid, "dealer_id": DEALER, "contract_id": anderer,
                                          "status": "abgeholt"})
        # ... das finale Protokoll traegt aber den Vertrag des Abschlusses.
        await db.pickup_protocols.insert_one(
            {"id": f"p_um_{SUF}", "appointment_id": aid, "dealer_id": DEALER, "contract_id": cid,
             "status": "final", "finalized_at": ALT, "seller_name": "Max", "place": "Ort",
             "pdf_path": f"protocol/{DEALER}/um.pdf"})
        assert await cs.vertrag_endgueltig_loeschen(db, cid, scrub_pii=True, grund="manuell")
        p = await db.pickup_protocols.find_one({"id": f"p_um_{SUF}"}, {"_id": 0})
        assert p["seller_name"] == "" and p["place"] == "" and p.get("pii_geloescht_at")
        assert "pdf_path" not in p or p.get("pdf_path_loeschung_offen")
        # Der fremde Vertragsverweis am Termin bleibt
        assert (await db.appointments.find_one({"id": aid}))["contract_id"] == anderer

    _run(lauf)
