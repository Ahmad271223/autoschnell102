# -*- coding: utf-8 -*-
"""Zwei Server, eine Sicherung (06.09.2026).

Die Sicherung laeuft auf EINEM Server; fragt der Load Balancer den
anderen nach /api/ready, fand der lokal keine Sicherung und warnte.
Jetzt steht der Stand in der Datenbank, die beide sehen, und die
juengere Auskunft gewinnt."""
import asyncio
import os
import sys
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import backup_service as bs  # noqa: E402

MONGO_URL = os.environ.get("MONGO_URL") or "mongodb://127.0.0.1:27017"
DB_NAME = os.environ.get("DB_NAME") or "autoschnell"


def _mit_db(coro_factory):
    from motor.motor_asyncio import AsyncIOMotorClient

    async def _run():
        cl = AsyncIOMotorClient(MONGO_URL, serverSelectionTimeoutMS=5000)
        try:
            return await coro_factory(cl[DB_NAME])
        finally:
            cl.close()
    return asyncio.run(_run())


def test_datenbank_eintrag_gewinnt_wenn_lokal_nichts_liegt(monkeypatch, tmp_path):
    """Der Server ohne eigene Sicherung (prod2) sieht die von prod1."""
    monkeypatch.setattr(bs, "BACKUP_DIR", tmp_path)       # leer: keine lokale Sicherung
    vor_2h = (datetime.now(timezone.utc) - timedelta(hours=2)).isoformat()

    async def _lauf(db):
        alt = await db.system_flags.find_one({"_id": bs._STAND_ID})
        await db.system_flags.update_one({"_id": bs._STAND_ID}, {"$set": {
            "erstellt": vor_2h, "vollstaendig": True, "offsite": True,
            "pfad": "/backups/autoschnell-test", "server": "prod1-test"}}, upsert=True)
        try:
            return await bs.letztes_backup_info_global(db)
        finally:
            if alt is None:
                await db.system_flags.delete_one({"_id": bs._STAND_ID})
            else:
                await db.system_flags.replace_one({"_id": bs._STAND_ID}, alt)
    info = _mit_db(_lauf)
    assert info["vollstaendig"] is True and info["offsite"] is True
    assert 1.9 < info["alter_stunden"] < 2.2, info["alter_stunden"]
    assert "prod1-test" in info["quelle"]


def test_ohne_datenbank_eintrag_bleibt_die_lokale_auskunft(monkeypatch, tmp_path):
    monkeypatch.setattr(bs, "BACKUP_DIR", tmp_path)

    async def _lauf(db):
        alt = await db.system_flags.find_one({"_id": bs._STAND_ID})
        await db.system_flags.delete_one({"_id": bs._STAND_ID})
        try:
            return await bs.letztes_backup_info_global(db)
        finally:
            if alt is not None:
                await db.system_flags.replace_one({"_id": bs._STAND_ID}, alt, upsert=True)
    info = _mit_db(_lauf)
    assert info["alter_stunden"] is None and "quelle" not in info


def test_lauf_schreibt_den_stand(monkeypatch, tmp_path):
    monkeypatch.setattr(bs, "BACKUP_DIR", tmp_path)

    async def _lauf(db):
        alt = await db.system_flags.find_one({"_id": bs._STAND_ID})
        try:
            await bs.stand_speichern(db)
            doc = await db.system_flags.find_one({"_id": bs._STAND_ID})
            return doc
        finally:
            if alt is None:
                await db.system_flags.delete_one({"_id": bs._STAND_ID})
            else:
                await db.system_flags.replace_one({"_id": bs._STAND_ID}, alt)
    doc = _mit_db(_lauf)
    assert doc and doc.get("server") and doc.get("gespeichert")


def test_ready_fragt_serveruebergreifend():
    quelle = (Path(__file__).resolve().parents[1] / "server.py").read_text(encoding="utf-8")
    assert "letztes_backup_info_global" in quelle
