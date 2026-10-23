# -*- coding: utf-8 -*-
"""Zwei Wuensche Ahmads vom 20.09.2026.

  A) PROBE-ABO: 3 oder 5 Tage, kostenlos. Laeuft von selbst ab — danach
     sperrt die Abo-Pruefung die Sucher-Funktion automatisch, wie bei
     jedem abgelaufenen Abo.
  B) VERTRAGSLISTE: nicht mehr bis zu 2000 Stueck auf einmal, sondern 20
     und darunter ein Knopf fuer die naechsten 20.
"""
import asyncio
import inspect
import sys
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest

BACKEND = Path(__file__).resolve().parents[1]
WURZEL = BACKEND.parent
sys.path.insert(0, str(BACKEND))

import deps  # noqa: E402
import routes.admin as ADMIN  # noqa: E402
import routes.team as TEAM  # noqa: E402

MONGO_URL = __import__("os").environ.get("MONGO_URL") or "mongodb://127.0.0.1:27017"
SA = {"id": "t_pa_sa", "role": "admin", "is_super_admin": True,
      "username": "t-pa-sa", "dealer_id": ""}


def _jetzt():
    return datetime.now(timezone.utc).isoformat()


@pytest.fixture
def welt(monkeypatch):
    from motor.motor_asyncio import AsyncIOMotorClient
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    client = AsyncIOMotorClient(MONGO_URL, serverSelectionTimeoutMS=5000)
    name = f"autoschnell_pa_{uuid.uuid4().hex[:10]}"
    db = client[name]
    monkeypatch.setattr(deps, "db", db)
    monkeypatch.setattr(ADMIN, "db", db)
    loop.run_until_complete(
        db.manual_payments.create_index("vorgang_id", unique=True, sparse=True))
    try:
        yield SimpleNamespace(db=db, run=loop.run_until_complete)
    finally:
        try:
            loop.run_until_complete(client.drop_database(name))
        finally:
            client.close()
            loop.close()


async def _sucher(db, aktiv=True):
    uid = f"sucher-{uuid.uuid4().hex[:8]}"
    await db.users.insert_one({"id": uid, "role": "sucher", "active": aktiv,
                               "dealer_id": "firma-1", "username": uid,
                               "created_at": _jetzt()})
    return uid


# ==================================================== A) Probe-Abo
def test_01_es_gibt_probe_plaene():
    assert TEAM.SUCHER_PLANS["probe3"]["days"] == 3
    assert TEAM.SUCHER_PLANS["probe5"]["days"] == 5
    for plan in ("probe3", "probe5"):
        assert TEAM.SUCHER_PLANS[plan]["price"] == 0.0
        assert TEAM.ist_probe(plan) is True
    for plan in ("monthly", "yearly", "", None, "quatsch"):
        assert TEAM.ist_probe(plan) is False


def test_02_ein_probe_abo_gibt_wirklich_zugang():
    """Der Stolperstein: ohne Eintrag in ABO_PLAENE_ERLAUBT waere das Abo
    zwar angelegt, gaebe aber KEINEN Zugang (Plan gilt als ungueltig)."""
    assert "probe3" in deps.ABO_PLAENE_ERLAUBT
    assert "probe5" in deps.ABO_PLAENE_ERLAUBT


def test_03_der_sucher_kann_die_probe_nicht_selbst_anfragen():
    """Sonst holte sich jeder alle drei Tage neue drei Tage."""
    assert "probe3" not in TEAM.ANFRAGBARE_PLANS
    assert "probe5" not in TEAM.ANFRAGBARE_PLANS
    assert set(TEAM.ANFRAGBARE_PLANS) == {"monthly", "yearly"}
    q = inspect.getsource(TEAM)
    assert "plan not in ANFRAGBARE_PLANS" in q
    assert "return {\"plans\": ANFRAGBARE_PLANS}" in q, \
        "auch die Planliste der Firma zeigt sie nicht"


def test_04_probe_freischalten_laeuft_nach_drei_tagen_ab(welt):
    db = welt.db

    async def lauf():
        sid = await _sucher(db)
        erg = await ADMIN.admin_set_sucher_abo(
            sid, ADMIN.AboFreischaltenIn(plan="probe3"), admin=SA)
        abo = await db.subscriptions.find_one({"subject_user_id": sid,
                                               "status": "active"}, {"_id": 0})
        zahlung = await db.manual_payments.find_one({"subject_user_id": sid},
                                                    {"_id": 0})
        return erg, abo, zahlung

    erg, abo, zahlung = welt.run(lauf())
    assert erg["active"] is True and erg["plan"] == "probe3"
    ablauf = datetime.fromisoformat(abo["expires_at"])
    tage = (ablauf - datetime.now(timezone.utc)).total_seconds() / 86400
    assert 2.9 < tage < 3.1, f"3 Tage erwartet, sind {tage:.2f}"
    assert zahlung["amount"] == 0.0 and zahlung["kostenlos"] is True
    assert zahlung["zahlungsart"] == "probe", "nicht als Kulanz verbuchen"


def test_05_fuenf_tage_sind_fuenf_tage(welt):
    db = welt.db

    async def lauf():
        sid = await _sucher(db)
        await ADMIN.admin_set_sucher_abo(
            sid, ADMIN.AboFreischaltenIn(plan="probe5"), admin=SA)
        return await db.subscriptions.find_one({"subject_user_id": sid}, {"_id": 0})

    abo = welt.run(lauf())
    tage = (datetime.fromisoformat(abo["expires_at"])
            - datetime.now(timezone.utc)).total_seconds() / 86400
    assert 4.9 < tage < 5.1


def test_06_eine_probe_ersetzt_kein_bezahltes_abo(welt):
    """Die gefaehrlichste Falle: _abo_vorgang_ausfuehren ersetzt ALLE
    bisherigen Abos. Eine Probe auf ein laufendes Jahr haette ein bezahltes
    Jahr durch drei Tage ersetzt."""
    db = welt.db

    async def lauf():
        from fastapi import HTTPException
        sid = await _sucher(db)
        await ADMIN.admin_set_sucher_abo(
            sid, ADMIN.AboFreischaltenIn(plan="yearly"), admin=SA)
        z = {}
        try:
            await ADMIN.admin_set_sucher_abo(
                sid, ADMIN.AboFreischaltenIn(plan="probe3"), admin=SA)
        except HTTPException as e:
            z["code"], z["text"] = e.status_code, e.detail
        z["abo"] = await db.subscriptions.find_one(
            {"subject_user_id": sid, "status": "active"}, {"_id": 0})
        return z

    z = welt.run(lauf())
    assert z["code"] == 400 and "bezahltes Abo" in z["text"]
    assert z["abo"]["plan"] == "yearly", "das bezahlte Jahr steht unveraendert"
    tage = (datetime.fromisoformat(z["abo"]["expires_at"])
            - datetime.now(timezone.utc)).days
    assert tage > 300, "und es ist noch ein Jahr lang gueltig"


def test_07_zwei_proben_ergeben_nicht_sechs_tage(welt):
    """Die Probe beginnt JETZT, nicht am Ende der Restlaufzeit."""
    db = welt.db

    async def lauf():
        sid = await _sucher(db)
        await ADMIN.admin_set_sucher_abo(
            sid, ADMIN.AboFreischaltenIn(plan="probe3"), admin=SA)
        await ADMIN.admin_set_sucher_abo(
            sid, ADMIN.AboFreischaltenIn(plan="probe3"), admin=SA)
        return await db.subscriptions.find_one(
            {"subject_user_id": sid, "status": "active"}, {"_id": 0})

    abo = welt.run(lauf())
    tage = (datetime.fromisoformat(abo["expires_at"])
            - datetime.now(timezone.utc)).total_seconds() / 86400
    assert 2.9 < tage < 3.1, f"wieder 3 Tage erwartet, sind {tage:.2f}"


def test_08_kein_betrag_und_kein_eigenes_datum_bei_der_probe(welt):
    db = welt.db

    async def lauf():
        from decimal import Decimal

        from fastapi import HTTPException
        sid = await _sucher(db)
        z = {}
        for feld, koerper in (
                ("betrag", ADMIN.AboFreischaltenIn(plan="probe3",
                                                   betrag=Decimal("50"))),
                ("datum", ADMIN.AboFreischaltenIn(plan="probe3",
                                                  gueltig_bis="2027-01-01"))):
            try:
                await ADMIN.admin_set_sucher_abo(sid, koerper, admin=SA)
                z[feld] = "durchgelassen"
            except HTTPException as e:
                z[feld] = e.detail
        z["abos"] = await db.subscriptions.count_documents({})
        return z

    z = welt.run(lauf())
    assert "kostenlos" in z["betrag"]
    assert "3 bzw. 5 Tage" in z["datum"]
    assert z["abos"] == 0, "nichts davon darf ein Abo anlegen"


def test_09_zahlungsart_probe_nur_beim_probe_abo(welt):
    db = welt.db

    async def lauf():
        from fastapi import HTTPException
        sid = await _sucher(db)
        try:
            await ADMIN.admin_set_sucher_abo(
                sid, ADMIN.AboFreischaltenIn(plan="yearly", zahlungsart="probe"),
                admin=SA)
            return "durchgelassen"
        except HTTPException as e:
            return e.detail

    assert "nur beim Probe-Abo" in welt.run(lauf())


def test_10_die_oberflaeche_kennt_die_probe():
    quelle = (WURZEL / "frontend" / "src" / "pages" / "admin_v2"
              / "UserDetail.jsx").read_text(encoding="utf-8")
    assert 'grantAbo(s, "probe3")' in quelle and 'grantAbo(s, "probe5")' in quelle
    assert "sperrt danach automatisch" in quelle, "der Chef muss wissen, was passiert"
    # Ein Datum darf beim Probe-Abo gar nicht erst mitgeschickt werden.
    assert 'const datum = probe ? "" :' in quelle
    # Plan-Bezeichnungen stehen an EINER Stelle statt dreimal im Text.
    assert "const PLAENE = {" in quelle
    assert 'plan === "yearly" ? "jährlich' not in quelle


# ============================================== B) Vertragsliste
def test_11_die_liste_blaettert_in_20er_schritten():
    assert ADMIN.ADMIN_VERTRAEGE_SEITE == 20
    assert ADMIN.ADMIN_VERTRAEGE_MAX == 2000, "Ahmads Obergrenze"
    q = inspect.getsource(ADMIN.admin_user_contracts)
    assert "seite: int = 1" in q and "limit: int = ADMIN_VERTRAEGE_SEITE" in q
    assert ".skip(ueberspringen)" in q


def test_12_echte_seiten_mit_daten(welt):
    db = welt.db

    async def lauf():
        from fastapi import Response
        uid = "chef-1"
        await db.users.insert_one({"id": uid, "role": "dealer",
                                   "dealer_id": "firma-1", "active": True})
        await db.dealers.insert_one({"id": "firma-1", "company_name": "Norden",
                                     "kunden_nr": 10002})
        # 45 Vertraege, neueste zuerst
        await db.generated_pdfs.insert_many([
            {"id": f"c{i:03d}", "dealer_id": "firma-1", "user_id": uid,
             "pdf_b64": "x" * 100,
             "created_at": (datetime.now(timezone.utc)
                            - timedelta(minutes=i)).isoformat()}
            for i in range(45)])
        s1 = await ADMIN.admin_user_contracts(uid, Response(), _=SA, seite=1)
        s2 = await ADMIN.admin_user_contracts(uid, Response(), _=SA, seite=2)
        s3 = await ADMIN.admin_user_contracts(uid, Response(), _=SA, seite=3)
        return s1, s2, s3

    s1, s2, s3 = welt.run(lauf())
    assert len(s1["contracts"]) == 20 and s1["weitere"] is True
    assert len(s2["contracts"]) == 20 and s2["weitere"] is True
    assert len(s3["contracts"]) == 5 and s3["weitere"] is False
    assert s1["gesamt"] == s2["gesamt"] == 45, "die Gesamtzahl steht im Kopf"
    # Keine Dubletten, richtige Reihenfolge (neueste zuerst)
    ids = [c["id"] for c in s1["contracts"] + s2["contracts"] + s3["contracts"]]
    assert len(set(ids)) == 45
    assert ids == sorted(ids), "c000 (neueste) bis c044"
    # Und ohne PDF-Bytes, sonst waere die Liste schwer.
    assert "pdf_b64" not in s1["contracts"][0]


def test_13_kaputte_seitenzahlen_stuerzen_nicht_ab(welt):
    db = welt.db

    async def lauf():
        from fastapi import Response
        await db.users.insert_one({"id": "u1", "role": "sucher",
                                   "dealer_id": "f1", "active": True})
        aus = {}
        for name, kwargs in (("null", {"seite": 0}),
                             ("negativ", {"seite": -5}),
                             ("riesig", {"seite": 10 ** 12}),
                             ("limit_null", {"limit": 0}),
                             ("limit_riesig", {"limit": 99999})):
            aus[name] = await ADMIN.admin_user_contracts("u1", Response(),
                                                         _=SA, **kwargs)
        return aus

    aus = welt.run(lauf())
    assert aus["null"]["seite"] == 1 and aus["negativ"]["seite"] == 1
    assert aus["riesig"]["seite"] <= 10 ** 6, "sonst OverflowError (500)"
    # limit=0 heisst "nicht angegeben" -> Standard. Genauso haelt es
    # /admin/contracts seit Audit 13.09.2026 (#52); eine Seite mit EINEM
    # Eintrag waere auch niemandem gedient.
    assert aus["limit_null"]["limit"] == ADMIN.ADMIN_VERTRAEGE_SEITE
    assert aus["limit_riesig"]["limit"] == ADMIN.ADMIN_VERTRAEGE_MAX


def test_14_die_obergrenze_bleibt_und_wird_gesagt(welt):
    """Ueber 2000 zeigt die Admin-Ansicht nicht — das steht in der Antwort,
    statt still gezogen zu werden."""
    q = inspect.getsource(ADMIN.admin_user_contracts)
    assert "abgeschnitten" in q and "ADMIN_VERTRAEGE_MAX" in q
    assert "weitere = False" in q, \
        "am Ende der 2000 verschwindet der Knopf, statt ins Leere zu laufen"


def test_15_die_oberflaeche_laedt_nach():
    quelle = (WURZEL / "frontend" / "src" / "pages" / "admin_v2"
              / "UserDetail.jsx").read_text(encoding="utf-8")
    assert "Weitere 20 anzeigen" in quelle
    assert "weitereLaden" in quelle and "params: { seite: naechste }" in quelle
    # Die schon geladenen bleiben stehen, die naechsten kommen dazu:
    assert "setMehr((m) => [...m, ...(r.data?.contracts || [])])" in quelle
    assert "const contracts = [...(data.contracts || []), ...mehr];" in quelle
    # Der Knopf verschwindet am Ende:
    assert "{data.weitere && (" in quelle
    # Und der Zaehler oben zeigt die Gesamtzahl, nicht die geladene:
    assert "<Badge>{fmtNum(gesamt)}</Badge>" in quelle
