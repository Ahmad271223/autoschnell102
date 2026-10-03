# -*- coding: utf-8 -*-
"""Reviewer-Befunde 16.09.2026, Block D (Abos / Mail / Aufraeumen / Pool /
Konten): 61, 62, 92, 93, 100, 106, 110, 111, 112 — in-process gegen eine
Wegwerf-Datenbank (echtes Mongo).

- Abo-Anzeige mit derselben Firmenbindung wie die Zugriffspruefung; Team-
  uebersicht ohne ersetzte Abos; naive Ablaufzeit als UTC; plan muss String sein
- Nacharbeit rotiert (nacharbeit_versuch_am)
- Resend 5xx = unklar: kein SMTP-Rueckfall
- Abholberichte offener Termine bleiben; Frist ab Abschluss
- Konten ohne Firma auch mit nicht laufender Loeschung gesperrt
- Pool-Trimmen: Schutzstempel gehoert in den CAS
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
from fastapi import HTTPException

BACKEND = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND))

import cleanup_service as CS  # noqa: E402
import deps  # noqa: E402
import email_service as ES  # noqa: E402
import fahrzeugpool as FP  # noqa: E402
import routes.dealer as D  # noqa: E402
import routes.team as T  # noqa: E402

MONGO_URL = os.environ.get("MONGO_URL") or "mongodb://127.0.0.1:27017"


def _iso(delta_s: int = 0) -> str:
    return (datetime.now(timezone.utc) + timedelta(seconds=delta_s)).isoformat()


@pytest.fixture
def wegwerf(monkeypatch):
    from motor.motor_asyncio import AsyncIOMotorClient
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    client = AsyncIOMotorClient(MONGO_URL, serverSelectionTimeoutMS=5000)
    name = f"autoschnell_bd_{uuid.uuid4().hex[:10]}"
    db = client[name]
    for mod in (deps, D, T):
        monkeypatch.setattr(mod, "db", db)
    try:
        yield SimpleNamespace(db=db, run=loop.run_until_complete)
    finally:
        try:
            loop.run_until_complete(client.drop_database(name))
        finally:
            client.close()
            loop.close()


class _Sammlung:
    """Sammlung, deren find() vor dem Lesen einen Eingriff erlaubt (Rennen
    Kandidatenwahl -> Schreiben nachstellen)."""

    def __init__(self, echt, dazwischen):
        self._echt, self._dazwischen = echt, dazwischen

    def __getattr__(self, name):
        return getattr(self._echt, name)

    def find(self, *a, **k):
        cur = self._echt.find(*a, **k)
        dazwischen = self._dazwischen

        async def gen():
            await dazwischen(None)          # Eingriff VOR dem ersten Dokument (auch bei leerer Sammlung)
            async for d in cur:
                yield d
        return gen()


class _Db:
    def __init__(self, echt, **ersatz):
        self._echt, self._ersatz = echt, ersatz

    def __getattr__(self, name):
        if name in self._ersatz:
            return self._ersatz[name]
        return getattr(self._echt, name)

    def __getitem__(self, name):
        return getattr(self, name)


# ------------------------------------------------------------- Quelltext
def test_quelltext_block_d():
    assert '"status": {"$ne": "ersetzt"}}},' in inspect.getsource(T.list_sucher)          # 62
    assert "not isinstance(plan, str)" in inspect.getsource(T.eigenes_abo_anfrage)      # 92
    assert "_ablauf_parsen(expires_at)" in inspect.getsource(D.dealer_subscription)     # 93
    assert '{"dealer_id": {"$exists": False}}' in inspect.getsource(D.massgebliches_abo)  # 61
    for fn in (CS.vertrags_nacharbeit_nachholen, CS.termin_nacharbeit_nachholen,
               CS.kaufvorgang_nacharbeit_nachholen):                                    # 100
        q = inspect.getsource(fn)
        assert '.sort("nacharbeit_versuch_am", 1).limit(200)' in q, fn.__name__
        assert '"nacharbeit_versuch_am": now_iso()' in q and '"nacharbeit_versuch_am": ""' in q
    assert '"loeschung.status": {"$ne": "laeuft"}' in inspect.getsource(CS.konten_ohne_firma_sperren)  # 111
    b = inspect.getsource(CS.berichte_nach_frist_loeschen)                              # 110
    assert "_TERMIN_GESCHLOSSEN" in b and '(termin.get("updated_at") or "") > cutoff' in b
    assert "except ResendUnklar" in inspect.getsource(ES.send_email_mit_beleg)          # 106
    assert "raise ResendUnklar(" in inspect.getsource(ES._send_resend)
    assert '"geschuetzt_bis": {"$exists": False}' in inspect.getsource(FP.fahrzeugpool_trimmen).split("stand = {")[1]  # 112


# ------------------------------------------------------------- 61 / 93
def test_abo_anzeige_mit_firmenbindung_und_naiver_ablaufzeit(wegwerf):
    db, run = wegwerf.db, wegwerf.run
    sucher = {"id": "s1", "dealer_id": "d_neu", "role": "sucher"}
    spaeter = (datetime.now(timezone.utc) + timedelta(days=100)).isoformat()

    async def lauf():
        await db.dealers.insert_one({"id": "d_neu", "company_name": "Neu", "user_id": "chef"})
        await db.subscriptions.insert_one({
            "id": "alt", "subject_user_id": "s1", "dealer_id": "d_alt", "plan": "yearly",
            "status": "active", "expires_at": spaeter, "created_at": _iso(60)})
        nur_fremd = await D.massgebliches_abo(sucher)
        await db.subscriptions.insert_one({
            "id": "legacy", "subject_user_id": "s1", "plan": "monthly", "status": "active",
            "expires_at": "2099-01-01T00:00:00", "created_at": _iso(0)})     # naiv, ohne dealer_id
        legacy = await D.massgebliches_abo(sucher)
        anzeige = await D.dealer_subscription(sucher)
        await db.subscriptions.insert_one({
            "id": "neu", "subject_user_id": "s1", "dealer_id": "d_neu", "plan": "yearly",
            "status": "active", "expires_at": spaeter, "created_at": _iso(120)})
        eigen = await D.massgebliches_abo(sucher)
        return nur_fremd, legacy, anzeige, eigen

    nur_fremd, legacy, anzeige, eigen = run(lauf())
    assert nur_fremd is None, "Abo der frueheren Firma wird nicht angezeigt"
    assert legacy["id"] == "legacy" and eigen["id"] == "neu"
    assert anzeige["active"] is True and anzeige["days_remaining"] is not None and anzeige["days_remaining"] > 1000


# ------------------------------------------------------------- 92
def test_abo_anfrage_mit_falschem_plan_typ_ist_400(wegwerf):
    run = wegwerf.run
    user = {"id": "s1", "dealer_id": "d1", "role": "sucher"}
    for plan in ([], {}, 5):
        with pytest.raises(HTTPException) as e:
            run(T.eigenes_abo_anfrage({"plan": plan}, user))
        assert e.value.status_code == 400, plan


# ------------------------------------------------------------- 106
def test_resend_unklar_kein_smtp_rueckfall(monkeypatch):
    gesendet = []
    monkeypatch.setattr(ES, "email_configured", lambda: True)
    monkeypatch.setattr(ES, "resend_aktiv", lambda: True)
    monkeypatch.setattr(ES, "smtp_aktiv", lambda: True)
    monkeypatch.setattr(ES, "_send_sync", lambda **k: gesendet.append(k["to"]))

    async def unklar(**k):
        raise ES.ResendUnklar("Resend HTTP 503 nach Wiederholungen")
    monkeypatch.setattr(ES, "_send_resend", unklar)
    ok, beleg = asyncio.run(ES.send_email_mit_beleg("v@example.test", "Betreff", "Text",
                                                    idempotency_key="k-106"))
    assert ok is False and beleg == "" and gesendet == [], "unklar: NICHT ueber SMTP nachsenden"

    async def abgelehnt(**k):
        return ""
    monkeypatch.setattr(ES, "_send_resend", abgelehnt)
    ok, beleg = asyncio.run(ES.send_email_mit_beleg("v@example.test", "Betreff", "Text"))
    assert ok is True and beleg == "smtp" and gesendet == ["v@example.test"], "sicher abgelehnt: SMTP darf"


# ------------------------------------------------------------- 110
def test_berichte_offener_termine_bleiben_frist_ab_abschluss(wegwerf):
    db, run = wegwerf.db, wegwerf.run
    now = datetime.now(timezone.utc)
    alt = (now - timedelta(days=CS.BERICHT_AUFBEWAHRUNG_TAGE + 5)).isoformat()

    def bericht(n, aid):
        return {"id": f"r_{n}", "appointment_id": aid, "dealer_id": "d1", "deviations": [],
                "created_at": alt}

    async def lauf():
        await db.appointments.insert_many([
            {"id": "t_offen", "dealer_id": "d1", "status": "offen", "updated_at": alt},
            {"id": "t_wieder", "dealer_id": "d1", "status": "verschoben", "updated_at": _iso()},
            {"id": "t_frisch_zu", "dealer_id": "d1", "status": "abgeholt", "updated_at": _iso()},
            {"id": "t_alt_zu", "dealer_id": "d1", "status": "abgeholt", "updated_at": alt}])
        await db.pickup_reports.insert_many([
            bericht("offen", "t_offen"), bericht("wieder", "t_wieder"),
            bericht("frisch_zu", "t_frisch_zu"), bericht("alt_zu", "t_alt_zu"),
            bericht("verwaist", "t_weg")])
        n = await CS.berichte_nach_frist_loeschen(db, now, {})
        rest = sorted([r["id"] async for r in db.pickup_reports.find({}, {"_id": 0, "id": 1})])
        return n, rest

    n, rest = run(lauf())
    assert rest == ["r_frisch_zu", "r_offen", "r_wieder"], rest
    assert n == 2


# ------------------------------------------------------------- 111
def test_konten_ohne_firma_auch_mit_alter_loeschung_gesperrt(wegwerf):
    db, run = wegwerf.db, wegwerf.run

    async def lauf():
        await db.users.insert_many([
            {"id": "u_ohne", "dealer_id": "d_fehlt", "role": "sucher", "active": True},
            {"id": "u_alt", "dealer_id": "d_fehlt", "role": "sucher", "active": True,
             "loeschung": {"status": "abgebrochen"}},
            {"id": "u_laeuft", "dealer_id": "d_fehlt", "role": "sucher", "active": True,
             "loeschung": {"status": "laeuft"}}])
        n = await CS.konten_ohne_firma_sperren(db)
        return n, {u["id"]: u.get("active") async for u in db.users.find({}, {"_id": 0, "id": 1, "active": 1})}

    n, aktiv = run(lauf())
    assert n == 2 and aktiv == {"u_ohne": False, "u_alt": False, "u_laeuft": True}, aktiv


# ------------------------------------------------------------- 100
def test_nacharbeit_rotiert(wegwerf, monkeypatch):
    db, run = wegwerf.db, wegwerf.run
    import kaufvorgang as KV

    async def bleibt_offen(*a, **k):
        return None
    monkeypatch.setattr(KV, "fahrzeug_status_aggregieren", bleibt_offen)

    async def lauf():
        await db.kaufvorgaenge.insert_many([
            {"id": "k_nie", "dealer_id": "d1", "vehicle_id": "v1", "nacharbeit_offen": True},
            {"id": "k_alt", "dealer_id": "d1", "vehicle_id": "v1", "nacharbeit_offen": True,
             "nacharbeit_versuch_am": "2026-01-01T00:00:00+00:00"}])
        await CS.kaufvorgang_nacharbeit_nachholen(db)
        docs = {d["id"]: d async for d in db.kaufvorgaenge.find({}, {"_id": 0})}
        return docs

    docs = run(lauf())
    assert docs["k_nie"]["nacharbeit_versuch_am"] > "2026-09"
    assert docs["k_alt"]["nacharbeit_versuch_am"] > "2026-09", "auch der alte Versuch wird neu gestempelt"
    assert docs["k_nie"]["nacharbeit_versuche"] == 1 and docs["k_alt"]["nacharbeit_versuche"] == 1


# ------------------------------------------------------------- 112
def test_pool_trimmen_respektiert_frischen_schutzstempel(wegwerf):
    db, run = wegwerf.db, wegwerf.run

    async def schuetzen(_d):
        # zwischen Kandidatenwahl und Loeschen beginnt eine Vertragsanlage
        await FP.kurz_schuetzen(db, "d1", "v_alt")

    async def lauf():
        await db.vehicles.insert_many([
            {"id": "v_alt", "dealer_id": "d1", "lifecycle": "verglichen", "owner_user_id": "uA",
             "updated_at": _iso(-600), "created_at": _iso(-600)},
            {"id": "v_neu", "dealer_id": "d1", "lifecycle": "verglichen", "owner_user_id": "uA",
             "updated_at": _iso(-60), "created_at": _iso(-60)}])
        proxy = _Db(db, generated_pdfs=_Sammlung(db.generated_pdfs, schuetzen))
        n = await FP.fahrzeugpool_trimmen(proxy, "d1", limit=1, owner_user_id="uA")
        uebrig = await db.vehicles.count_documents({"id": "v_alt"})
        # Stempel abgelaufen: beim naechsten Lauf wird getrimmt
        await db.vehicles.update_one({"id": "v_alt"}, {"$set": {"geschuetzt_bis": _iso(-5)}})
        n2 = await FP.fahrzeugpool_trimmen(db, "d1", limit=1, owner_user_id="uA")
        return n, uebrig, n2, await db.vehicles.count_documents({"id": "v_alt"})

    n, uebrig, n2, danach = run(lauf())
    assert n == 0 and uebrig == 1, "frisch geschuetztes Fahrzeug bleibt"
    assert n2 == 1 and danach == 0
