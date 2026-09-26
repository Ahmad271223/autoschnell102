# -*- coding: utf-8 -*-
"""Go-Live 14.09.2026, Runde 10 (15.09.2026) — Phase 3 des Befundplans
(Betrieb, Sperren, Backups):

  3.1 Job-Sperre mit Besitzer-Token und Heartbeat (A11 B14 B15)
  3.2 Storage-Nachholung mit Claim-Token (B16)
  3.3 Beweis-Erzeugung: Slot bleibt bis zum Ende des PDF-Threads belegt (B18 B19)
  3.4 Feste Deckel weg, verwaiste Archivzeilen, Grabsteine geloeschter Firmen
      (A2 A9 A10 A12 B24 B25, Liste 4 Nr. 15)
  3.5 Backups: Tagessperre nach Fehlschlag frei, eigene Offsite-Zugangsdaten,
      Restore setzt die Schema-Version, Option --exakt (F3 E3 E5 E6)
  3.6 Produktionspruefung: SMTP-Idempotenz fail-closed, eindeutige Indizes
      fail-closed, Proxy-Netze (A22 A17 B20 E8, Liste 4 Nr. 4-10)
  3.7 /health nimmt die Instanz aus der Rotation (E4)
"""
import asyncio
import importlib
import inspect
import os
import sys
import time
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest

BACKEND = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND))
sys.path.insert(0, str(BACKEND / "tests"))

import deps  # noqa: E402
import job_lock as JL  # noqa: E402
import cleanup_service as CS  # noqa: E402
import beweis_service as BS  # noqa: E402
import backup_service as BK  # noqa: E402
import email_service as ES  # noqa: E402
import indizes as IX  # noqa: E402
import routes.admin as ADMIN  # noqa: E402

MONGO_URL = os.environ.get("MONGO_URL") or "mongodb://127.0.0.1:27017"


def _jetzt(delta_s: int = 0) -> str:
    return (datetime.now(timezone.utc) + timedelta(seconds=delta_s)).isoformat()


@pytest.fixture
def wegwerf(monkeypatch):
    from motor.motor_asyncio import AsyncIOMotorClient
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    client = AsyncIOMotorClient(MONGO_URL, serverSelectionTimeoutMS=5000)
    name = f"autoschnell_r10_{uuid.uuid4().hex[:10]}"
    db = client[name]
    for mod in (deps, ADMIN):
        monkeypatch.setattr(mod, "db", db)
    try:
        yield SimpleNamespace(db=db, name=name, run=loop.run_until_complete)
    finally:
        try:
            loop.run_until_complete(client.drop_database(name))
        finally:
            client.close()
            loop.close()


# ============================================================ 3.1
def test_31_sperre_mit_token_und_heartbeat(wegwerf):
    db, run = wegwerf.db, wegwerf.run
    run(JL.ensure_lock_index(db))
    t1 = run(JL.acquire(db, "probe", 60))
    assert isinstance(t1, str) and len(t1) == 32
    assert run(JL.acquire(db, "probe", 60)) is None, "zweiter Griff scheitert"
    run(JL.release(db, "probe", token="falsches-token"))
    assert run(JL.gehalten(db, "probe")) is True, "fremdes Token gibt nicht frei"
    run(JL.release(db, "probe", token=t1))
    assert run(JL.gehalten(db, "probe")) is False
    t2 = run(JL.acquire(db, "probe", 60))
    assert t2 and t2 != t1
    assert run(JL.verlaengern(db, "probe", 60, token=t1)) is False, "alter Lauf verlaengert nicht"
    assert run(JL.verlaengern(db, "probe", 60, token=t2)) is True

    async def mit_puls():
        vorher = (await db.job_locks.find_one({"name": "probe"}))["expires_at"]
        async with JL.heartbeat(db, "probe", t2, 120, intervall=0.2):
            await asyncio.sleep(0.7)
        nachher = (await db.job_locks.find_one({"name": "probe"}))["expires_at"]
        return vorher, nachher

    vorher, nachher = run(mit_puls())
    assert nachher > vorher, "Heartbeat verlaengert die Sperre"
    # Aufrufer nutzen Token und Heartbeat
    assert 'release(db, "cleanup-cycle", token=token)' in inspect.getsource(ADMIN)
    q = inspect.getsource(CS.run_cleanup_forever)
    assert "heartbeat(" in q and "token = await acquire(" in q
    import migrationen as MIG
    assert "token=_TOKEN" in inspect.getsource(MIG._sperre_loesen)


# ============================================================ 3.2
def test_32_storage_nachholung_claim_token(wegwerf):
    db, run = wegwerf.db, wegwerf.run
    q = inspect.getsource(CS.storage_loeschungen_nachholen)
    assert '"claim_token": claim_token' in q and '{"id": e["id"], "claim_token": claim_token}' in q
    # Ein fremder, noch gueltiger Claim wird nicht angefasst
    run(db.storage_delete_retry.insert_one({
        "id": "r1", "art": "key", "key": "x/y.jpg", "claim_bis": _jetzt(600),
        "claim_token": "fremd", "versuche": 0}))
    assert run(CS.storage_loeschungen_nachholen(db)) == 0
    e = run(db.storage_delete_retry.find_one({"id": "r1"}))
    assert e["claim_token"] == "fremd" and e["versuche"] == 0


# ============================================================ 3.3
def test_33_beweis_slot_bleibt_bis_zum_thread_ende(wegwerf, monkeypatch):
    db, run = wegwerf.db, wegwerf.run
    q = inspect.getsource(BS._bearbeiten)
    assert "asyncio.shield(lauf)" in q and "NACHLAUF_MAX_SEKUNDEN" in q
    doc = {"id": "b1", "status": "in_arbeit", "bearbeiter": "w1", "versuche": 1,
           "cache_key": "k1"}
    run(db.inserat_beweise.insert_one(dict(doc)))
    monkeypatch.setattr(BS, "db", db, raising=False)

    async def _kein_puls(*a, **k):
        await asyncio.sleep(3600)
    monkeypatch.setattr(BS, "_herzschlag", _kein_puls)
    monkeypatch.setattr(BS, "ERZEUGUNG_MAX_SEKUNDEN", 0.2)

    async def langsam_aber_gut(dbx, d):
        await asyncio.sleep(0.6)
        await dbx.inserat_beweise.update_one({"id": d["id"]}, {"$set": {"status": "fertig"}})
        return True
    monkeypatch.setattr(BS, "beweis_erzeugen", langsam_aber_gut)
    t0 = time.monotonic()
    run(BS._bearbeiten(db, doc))
    assert time.monotonic() - t0 >= 0.55, "Slot erst frei, wenn der Lauf wirklich fertig ist"
    assert run(db.inserat_beweise.find_one({"id": "b1"}))["status"] == "fertig", \
        "verspaeteter Erfolg wird nicht als Fehlschlag verbucht"

    async def langsam_und_kaputt(dbx, d):
        await asyncio.sleep(0.5)
        raise RuntimeError("PDF kaputt")
    run(db.inserat_beweise.update_one({"id": "b1"}, {"$set": {"status": "in_arbeit"}}))
    monkeypatch.setattr(BS, "beweis_erzeugen", langsam_und_kaputt)
    run(BS._bearbeiten(db, doc))
    nach = run(db.inserat_beweise.find_one({"id": "b1"}))
    assert nach["status"] == "offen" and nach.get("naechster_versuch_ab"), \
        "Fehlschlag erst NACH dem Ende des Laufs verbucht"


# ============================================================ 3.4
def test_34_deckel_weg_archivzeilen_und_grabsteine(wegwerf):
    db, run = wegwerf.db, wegwerf.run
    src = inspect.getsource(CS)
    for alt in ('"deviations": 1}).limit(500)', '"dealer_id": 1}).limit(2000)',
                '"photos": 1}).to_list(None)', '"appointment_id": 1}).limit(2000)'):
        assert alt not in src, alt
    assert src.count(".batch_size(") >= 5
    # verwaiste Archivzeilen
    run(db.generated_pdfs.insert_one({"id": "c1", "dealer_id": "d1", "version": 3}))
    run(db.generated_pdf_versions.insert_many([
        {"contract_id": "c1", "version": 1}, {"contract_id": "c1", "version": 2},
        {"contract_id": "c1", "version": 3}, {"contract_id": "c-weg", "version": 1},
    ]))
    assert run(CS.archivzeilen_verwaist_bereinigen(db)) == 2
    assert run(_sammeln(db.generated_pdf_versions.find({}, {"_id": 0}))) == [
        {"contract_id": "c1", "version": 1}, {"contract_id": "c1", "version": 2}]
    # Grabsteine geloeschter Firmen
    run(db.firmen_geloescht.insert_one({"id": "d-x", "geloescht_am": _jetzt(-60)}))
    run(db.appointments.insert_many([{"id": "a1", "dealer_id": "d-x"}, {"id": "a2", "dealer_id": "d-ok"}]))
    run(db.users.insert_many([{"id": "u1", "dealer_id": "d-x"}, {"id": "u2", "dealer_id": "d-ok"}]))
    run(db.network_members.insert_one({"dealer_id": "d-x", "buyer_user_id": "k"}))
    n = run(CS.firmengrabsteine_bereinigen(db))
    assert n == 3
    assert run(db.appointments.count_documents({})) == 1
    assert run(db.users.find_one({"id": "u2"})) is not None
    assert run(db.network_members.count_documents({})) == 0
    assert "firmen_geloescht" in inspect.getsource(ADMIN.admin_delete_user)


async def _sammeln(cursor):
    return [d async for d in cursor]


# ============================================================ 3.5
def test_35_backup_tagessperre_offsite_zugang_und_restore(wegwerf):
    q = inspect.getsource(BK._run_backup)
    assert "-> bool" in q and "return ok" in q
    q2 = inspect.getsource(BK.run_backup_forever)
    assert "_lauf_mit_sperre" in q2 and "release(db, f\"backup-{tag}\", token=token)" in q2
    bm = (BACKEND / "scripts" / "backup_mongo.py").read_text(encoding="utf-8")
    assert 'BACKUP_S3_ACCESS_KEY' in bm and 'BACKUP_S3_SECRET_KEY' in bm
    op = (BACKEND / "scripts" / "offsite_pruefen.py").read_text(encoding="utf-8")
    assert 'BACKUP_S3_ACCESS_KEY' in op
    env = (BACKEND.parent / ".env.example").read_text(encoding="utf-8")
    assert "BACKUP_S3_ACCESS_KEY=" in env and "BACKUP_S3_SECRET_KEY=" in env
    # Restore: Schema-Version aus dem Backup
    sys.path.insert(0, str(BACKEND / "scripts"))
    import restore_mongo as RM
    import pymongo
    sync = pymongo.MongoClient(MONGO_URL)[wegwerf.name]
    sync.system_flags.insert_one({"_id": "schema", "version": 7})
    RM.schema_version_setzen(sync, ([{"_id": "schema", "version": 5}, {"_id": "wartungsmodus"}], None))
    assert sync.system_flags.find_one({"_id": "schema"})["version"] == 5
    RM.schema_version_setzen(sync, None)
    assert sync.system_flags.find_one({"_id": "schema"}) is None
    rq = inspect.getsource(RM.main)
    assert '"--exakt"' in rq
    assert "schema_version_setzen(ziel, flags_dump)" in inspect.getsource(RM.wiederherstellen)
    # Nachpruefung 20.09.2026 (Nr. 75): "exakt" war ein Schalter, den der
    # dokumentierte Befehl nie setzte — dadurch blieben live-eigene
    # Collections stehen (Mischstand). Jetzt ist es der Standard, und nur
    # --zusaetzliche-behalten schaltet es ab. Die Zusage bleibt dieselbe:
    # der Live-Stand entspricht danach dem Backup.
    assert 'exakt = not getattr(args, "zusaetzliche_behalten", False)' in \
        inspect.getsource(RM.wiederherstellen)
    assert '"--zusaetzliche-behalten"' in rq


# ============================================================ 3.6
def test_36_proxy_netze_und_produktionspruefung(monkeypatch):
    import rate_limiter
    monkeypatch.setenv("TRUST_PROXY", "true")
    monkeypatch.setenv("TRUSTED_PROXIES", "10.0.0.0/16,127.0.0.1")
    monkeypatch.delenv("TRUSTED_PROXIES_NUR_LISTE", raising=False)
    rl = importlib.reload(rate_limiter)
    try:
        # Load Balancer aus einem privaten Netz, das NICHT in der Liste steht
        assert rl._ist_eigener_proxy("10.7.0.9") is True
        assert rl._ist_eigener_proxy("203.0.113.7") is False

        class _Anfrage:
            def __init__(self, host, **kopf):
                self.client = SimpleNamespace(host=host)
                self.headers = {k.replace("_", "-"): v for k, v in kopf.items()}
        a = _Anfrage("10.7.0.9", x_forwarded_for="203.0.113.7, 10.7.0.9")
        assert rl.client_ip(a) == "203.0.113.7", "LB-Adresse ist nie der Besucher"
        monkeypatch.setenv("TRUSTED_PROXIES_NUR_LISTE", "true")
        rl = importlib.reload(rate_limiter)
        assert rl._ist_eigener_proxy("10.7.0.9") is False, "nur Liste: 10.7 nicht drin"
        assert rl._ist_eigener_proxy("10.0.4.4") is True
    finally:
        monkeypatch.delenv("TRUSTED_PROXIES_NUR_LISTE", raising=False)
        importlib.reload(rate_limiter)
    # Produktionspruefung: NUR_LISTE ohne 10.x-Netz ist ein Fehler
    import production_check as PC
    q = inspect.getsource(PC.pruefe_produktion)
    assert "TRUSTED_PROXIES_NUR_LISTE" in q and "10.0.0.0/8" in q
    assert "BACKUP_S3_ACCESS_KEY" in q
    # Compose-Standard enthaelt das Hetzner-Netz
    compose = (BACKEND.parent / "docker-compose.yml").read_text(encoding="utf-8")
    assert "10.0.0.0/8" in compose


def test_36_smtp_idempotenz_fail_closed(monkeypatch):
    class _Kaputt:
        def __getattr__(self, n):
            raise RuntimeError("db weg")
    monkeypatch.setattr(deps, "db", _Kaputt())
    assert asyncio.run(ES._smtp_idempotenz_beanspruchen("k1", "a@b.de")) == "db_fehler"
    q = inspect.getsource(ES.send_email_mit_beleg)
    assert 'stand == "db_fehler"' in q


def test_36_index_register_fail_closed():
    q = inspect.getsource(IX)
    assert 'await alarm(db, "unique_index_fehlt"' not in q
    assert q.count("_index_fehlt(") >= 9 and "FEHLENDE_UNIQUE" in q
    import server as S
    q2 = inspect.getsource(S.on_start)
    assert "FEHLENDE_UNIQUE" in q2 and "SystemExit(78)" in q2


# ============================================================ 3.7
def test_37_health_nimmt_instanz_aus_der_rotation(wegwerf, monkeypatch):
    db, run = wegwerf.db, wegwerf.run
    import server as S
    from fastapi import Response
    monkeypatch.setattr(S, "db", db)
    monkeypatch.setattr(IX, "FEHLENDE_UNIQUE", set())    # andere Tests hinterlassen Eintraege
    monkeypatch.setattr(S, "_KERN_CACHE", {"bis": 0.0, "fehler": []})
    r = Response()
    aus = run(S.health_check(r))
    assert r.status_code == 503 and aus["status"] == "unhealthy" and aus["db"] == "up"
    assert any(k.startswith("migration") for k in aus["kern"])
    # Migrationsstand + Kernindizes da -> gesund
    from migrationen import ZIEL_VERSION
    run(db.system_flags.update_one({"_id": "schema"}, {"$set": {"version": ZIEL_VERSION}}, upsert=True))
    run(db.vehicles.create_index([("dealer_id", 1), ("id", 1)], unique=True))
    run(db.kaufvorgaenge.create_index("contract_id", unique=True))
    monkeypatch.setattr(S, "_KERN_CACHE", {"bis": 0.0, "fehler": []})
    r = Response()
    assert run(S.health_check(r)) == {"status": "healthy", "db": "up"} and r.status_code == 200
    # Register meldet einen fehlenden Unique-Index -> 503
    IX.FEHLENDE_UNIQUE.add("probe.x")
    try:
        monkeypatch.setattr(S, "_KERN_CACHE", {"bis": 0.0, "fehler": []})
        r = Response()
        aus = run(S.health_check(r))
        assert r.status_code == 503 and any("probe.x" in k for k in aus["kern"])
    finally:
        IX.FEHLENDE_UNIQUE.discard("probe.x")
