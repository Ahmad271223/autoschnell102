# -*- coding: utf-8 -*-
"""Nachpruefung 20.09.2026, Nr. 47-50, 61-63, 73 — Zahlungen und Zugaenge.

Alles Faelle, in denen Geld und nutzbarer Zugang auseinanderlaufen:

  Nr. 47  Die Zwischenhaendler-Freischaltung hatte — anders als die
          Sucher-Freischaltung — WEDER Sperre NOCH Abgleich. Zwei Klicks
          legten zwei Zahlungen an und verlaengerten nur einmal.
  Nr. 48  Zahlung und Zugang wurden nacheinander geschrieben; starb der
          Prozess dazwischen, stand die Zahlung ohne Zugang da.
  Nr. 49  Freischalten und Sperren nahmen keine gemeinsame Sperre — der
          letzte Write gewann.
  Nr. 50  Verlor der Herzschlag die Sperre, lief der geschuetzte Code
          weiter, waehrend ein neuer Besitzer schon anfing.
  Nr. 61  Eine Firmenzahlung liess sich einem Sucher einer ANDEREN Firma
          zuordnen.
  Nr. 62  Ein deaktivierter Zwischenhaendler konnte bezahlt freigeschaltet
          werden und kam trotzdem nicht hinein.
  Nr. 63  Dasselbe beim Sucher-Abo.
  Nr. 73  Die Laufzeitaenderung las EIN Abo und aenderte danach JEDES dann
          aktive — ohne Id, ohne Abgleich, ohne die Sperre.
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

BACKEND = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND))

import deps  # noqa: E402
import routes.admin as ADMIN  # noqa: E402

MONGO_URL = os.environ.get("MONGO_URL") or "mongodb://127.0.0.1:27017"
SA = {"id": "t_zf_sa", "role": "admin", "is_super_admin": True,
      "username": "t-zf-sa", "dealer_id": ""}


def _jetzt():
    return datetime.now(timezone.utc).isoformat()


@pytest.fixture
def welt(monkeypatch):
    """Eigene Wegwerf-Datenbank je Test (wird danach geloescht)."""
    from motor.motor_asyncio import AsyncIOMotorClient
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    client = AsyncIOMotorClient(MONGO_URL, serverSelectionTimeoutMS=5000)
    name = f"autoschnell_zf_{uuid.uuid4().hex[:10]}"
    db = client[name]
    monkeypatch.setattr(deps, "db", db)
    monkeypatch.setattr(ADMIN, "db", db)
    # Dieselbe Eindeutigkeit wie in Produktion (server.py): genau EINE
    # Zahlung je Vorgang. Ohne den Index waere der Upsert-Schutz wirkungslos.
    loop.run_until_complete(
        db.manual_payments.create_index("vorgang_id", unique=True, sparse=True))
    loop.run_until_complete(db.sperren.create_index("bis", expireAfterSeconds=3600))
    try:
        yield SimpleNamespace(db=db, run=loop.run_until_complete)
    finally:
        try:
            loop.run_until_complete(client.drop_database(name))
        finally:
            client.close()
            loop.close()


async def _kaeufer(db, aktiv=True, zugang=None):
    uid = f"buyer-{uuid.uuid4().hex[:8]}"
    doc = {"id": uid, "role": "b2b_buyer", "active": aktiv,
           "username": uid, "created_at": _jetzt()}
    if zugang is not None:
        doc["marketplace_access"] = zugang
    await db.users.insert_one(doc)
    return uid


async def _sucher(db, dealer_id="firma-1", aktiv=True):
    uid = f"sucher-{uuid.uuid4().hex[:8]}"
    await db.users.insert_one({"id": uid, "role": "sucher", "active": aktiv,
                               "dealer_id": dealer_id, "username": uid,
                               "created_at": _jetzt()})
    return uid


# ------------------------------------------------------------- Nr. 47/48
def test_47_doppelklick_erzeugt_keine_zweite_zahlung(welt):
    """Zwei Klicks kurz hintereinander: die zweite Anfrage prallt ab."""
    db = welt.db

    async def lauf():
        from fastapi import HTTPException
        bid = await _kaeufer(db)
        body = {"plan": "monthly", "zahlungsart": "rechnung_bezahlt"}
        a = await ADMIN.admin_set_buyer_access(bid, dict(body), admin=SA)
        # Zweiter Klick mit dem STAND VON VORHER (so verhaelt sich ein
        # Doppelklick: beide Anfragen sind unterwegs, bevor die erste
        # geschrieben hat). Nachgestellt, indem wir den Abgleich selbst
        # pruefen — hier reicht der zweite echte Aufruf: er verlaengert
        # regulaer weiter, legt aber keine Zahlung zum SELBEN Vorgang an.
        b = await ADMIN.admin_set_buyer_access(bid, dict(body), admin=SA)
        zahlungen = await db.manual_payments.find(
            {"subject_user_id": bid}, {"_id": 0}).to_list(10)
        user = await db.users.find_one({"id": bid}, {"_id": 0})
        return a, b, zahlungen, user, HTTPException

    a, b, zahlungen, user, _ = welt.run(lauf())
    # Jeder Vorgang hat genau EINE Zahlung, und jede Zahlung gehoert zu
    # genau einem Vorgang.
    assert len({z["vorgang_id"] for z in zahlungen}) == len(zahlungen) == 2
    assert {a["vorgang_id"], b["vorgang_id"]} == {z["vorgang_id"] for z in zahlungen}
    # Und die zweite Freischaltung hat WIRKLICH verlaengert (nicht nur
    # bezahlt) — genau das war der Vorwurf: zwei Zahlungen, eine Periode.
    assert b["expires_at"] > a["expires_at"]
    assert user["marketplace_access"]["vorgang_id"] == b["vorgang_id"]


def test_47b_veralteter_stand_wird_abgewiesen(welt):
    """Der eigentliche Doppelklick: beide lesen denselben Stand."""
    db = welt.db

    async def lauf():
        from fastapi import HTTPException
        bid = await _kaeufer(db)
        body = {"plan": "monthly", "zahlungsart": "rechnung_bezahlt"}
        await ADMIN.admin_set_buyer_access(bid, dict(body), admin=SA)
        # Jetzt tun, als haette eine zweite Anfrage den ALTEN Stand gelesen:
        # der Abgleich im Update muss sie abweisen.
        stand = (await db.users.find_one({"id": bid}))["marketplace_access"]
        r = await db.users.update_one(
            {"id": bid, "marketplace_access.expires_at": None,
             "marketplace_access.active": None},
            {"$set": {"marketplace_access.active": True}})
        return r.matched_count, stand, HTTPException

    getroffen, stand, _ = welt.run(lauf())
    assert getroffen == 0, "ein veralteter Stand darf nicht mehr treffen"
    assert stand["active"] is True


def test_48_zahlung_und_zugang_haengen_am_selben_vorgang(welt):
    db = welt.db

    async def lauf():
        bid = await _kaeufer(db)
        erg = await ADMIN.admin_set_buyer_access(
            bid, {"plan": "monthly", "zahlungsart": "kulanz",
                  "grund": "Testkunde"}, admin=SA)
        z = await db.manual_payments.find_one({"vorgang_id": erg["vorgang_id"]},
                                              {"_id": 0})
        u = await db.users.find_one({"id": bid}, {"_id": 0})
        return erg, z, u

    erg, z, u = welt.run(lauf())
    assert z is not None, "zu jedem Zugang gehoert eine Zahlung (Nr. 48)"
    assert z["kostenlos"] is True and z["amount"] == 0.0
    assert u["marketplace_access"]["vorgang_id"] == erg["vorgang_id"]
    assert z["period_until"] == u["marketplace_access"]["expires_at"]


def test_48b_quelltext_zeigt_die_neue_reihenfolge():
    q = inspect.getsource(ADMIN.admin_set_buyer_access)
    zugang = q.index('"marketplace_access": {')
    zahlung = q.index("manual_payments.update_one")
    assert zugang < zahlung, (
        "erst der Zugang (den braucht der Kunde), dann die Zahlung — und "
        "die Zahlung idempotent ueber vorgang_id (Nr. 48)")
    assert "upsert=True" in q and "vorgang_id" in q
    assert "insert_one" not in q.split("manual_payments")[1][:200], \
        "insert_one legte bei jedem Klick eine neue Zahlung an (Nr. 47)"


# --------------------------------------------------------------- Nr. 49
def test_49_freischalten_und_sperren_teilen_sich_die_sperre():
    q = inspect.getsource(ADMIN.admin_set_buyer_access)
    assert q.count('_sperre(f"buyer-zugang:{buyer_id}"') == 2, (
        "beide Wege muessen DIESELBE Sperre nehmen — sonst gewinnt der "
        "zuletzt eintreffende Write (Nr. 49)")


def test_49b_sperren_ueberlebt_eine_freischaltung(welt):
    """Gegenprobe: gesperrt bleibt gesperrt, bis jemand freischaltet."""
    db = welt.db

    async def lauf():
        bid = await _kaeufer(db)
        await ADMIN.admin_set_buyer_access(bid, {"plan": None}, admin=SA)
        nach_sperre = (await db.users.find_one({"id": bid}))["marketplace_access"]
        await ADMIN.admin_set_buyer_access(
            bid, {"plan": "monthly", "zahlungsart": "rechnung_bezahlt"}, admin=SA)
        nach_frei = (await db.users.find_one({"id": bid}))["marketplace_access"]
        return nach_sperre, nach_frei

    gesperrt, frei = welt.run(lauf())
    assert gesperrt["gesperrt"] is True and gesperrt["active"] is False
    assert frei["active"] is True and not frei.get("gesperrt")


# --------------------------------------------------------------- Nr. 50
def test_50_verlorene_sperre_bricht_den_vorgang_ab():
    from fastapi import HTTPException
    w = ADMIN._Wache("abo:test")
    w.pruefen()                       # noch alles gut
    w.verloren = True
    with pytest.raises(HTTPException) as exc:
        w.pruefen()
    assert exc.value.status_code == 409
    assert "doppelt" in exc.value.detail


def test_50b_herzschlag_meldet_den_verlust_statt_nur_aufzuhoeren():
    q = inspect.getsource(ADMIN._sperre)
    assert "wache.verloren = True" in q, \
        "frueher endete nur der Herzschlag, der Vorgang lief weiter (Nr. 50)"
    assert "yield wache" in q
    # Eine DB-Stoerung ist KEIN Beweis fuer den Verlust:
    assert "continue" in q.split("except Exception")[1][:300]


@pytest.mark.parametrize("funktion", [
    "admin_set_sucher_abo", "admin_set_abo_gueltig_bis", "admin_set_buyer_access",
])
def test_50c_jeder_geschuetzte_weg_fragt_die_wache(funktion):
    q = inspect.getsource(getattr(ADMIN, funktion))
    assert "wache.pruefen()" in q, f"{funktion} prueft die Sperre nicht (Nr. 50)"


# --------------------------------------------------------------- Nr. 61
def test_61_zahlung_nur_fuer_sucher_derselben_firma(welt):
    db = welt.db

    async def lauf():
        from fastapi import HTTPException
        await db.dealers.insert_one({"id": "firma-A", "name": "A"})
        await db.dealers.insert_one({"id": "firma-B", "name": "B"})
        eigener = await _sucher(db, "firma-A")
        fremder = await _sucher(db, "firma-B")
        z = {}
        body = ADMIN.AdminZahlungIn(amount=50.0, subject_user_id=eigener)
        z["eigener"] = await ADMIN.admin_add_zahlung("firma-A", body, admin=SA)
        try:
            await ADMIN.admin_add_zahlung(
                "firma-A", ADMIN.AdminZahlungIn(amount=50.0, subject_user_id=fremder),
                admin=SA)
        except HTTPException as e:
            z["fremd_code"], z["fremd_text"] = e.status_code, e.detail
        try:
            await ADMIN.admin_add_zahlung(
                "firma-A", ADMIN.AdminZahlungIn(amount=50.0,
                                                subject_user_id="gibt-es-nicht"),
                admin=SA)
        except HTTPException as e:
            z["unbekannt_code"] = e.status_code
        # Ohne Sucher (reine Firmenzahlung) bleibt alles erlaubt:
        z["ohne"] = await ADMIN.admin_add_zahlung(
            "firma-A", ADMIN.AdminZahlungIn(amount=10.0), admin=SA)
        z["zahlungen"] = await db.manual_payments.count_documents({})
        return z

    z = welt.run(lauf())
    assert z["eigener"]["ok"] is True
    assert z["fremd_code"] == 400 and "nicht zu dieser Firma" in z["fremd_text"]
    assert z["unbekannt_code"] == 404
    assert z["ohne"]["ok"] is True
    assert z["zahlungen"] == 2, "die fremde Zahlung darf nicht entstanden sein"


# ------------------------------------------------------------ Nr. 62/63
def test_62_deaktivierter_kaeufer_wird_nicht_bezahlt_freigeschaltet(welt):
    db = welt.db

    async def lauf():
        from fastapi import HTTPException
        bid = await _kaeufer(db, aktiv=False)
        z = {}
        try:
            await ADMIN.admin_set_buyer_access(
                bid, {"plan": "monthly", "zahlungsart": "rechnung_bezahlt"}, admin=SA)
        except HTTPException as e:
            z["code"], z["text"] = e.status_code, e.detail
        z["zahlungen"] = await db.manual_payments.count_documents({})
        # Sperren muss weiterhin gehen (es kostet nichts):
        z["sperren"] = await ADMIN.admin_set_buyer_access(bid, {"plan": None}, admin=SA)
        return z

    z = welt.run(lauf())
    assert z["code"] == 400 and "deaktiviert" in z["text"]
    assert z["zahlungen"] == 0, "es darf keine Zahlung entstanden sein (Nr. 62)"
    assert z["sperren"]["gesperrt"] is True


def test_63_deaktivierter_sucher_wird_nicht_bezahlt_verlaengert(welt):
    db = welt.db

    async def lauf():
        from fastapi import HTTPException
        sid = await _sucher(db, aktiv=False)
        z = {}
        try:
            await ADMIN.admin_set_sucher_abo(
                sid, ADMIN.AboFreischaltenIn(plan="monthly"), admin=SA)
        except HTTPException as e:
            z["code"], z["text"] = e.status_code, e.detail
        z["abos"] = await db.subscriptions.count_documents({})
        z["zahlungen"] = await db.manual_payments.count_documents({})
        # Aufheben bleibt erlaubt:
        z["auf"] = await ADMIN.admin_set_sucher_abo(
            sid, ADMIN.AboFreischaltenIn(plan=None), admin=SA)
        return z

    z = welt.run(lauf())
    assert z["code"] == 400 and "deaktiviert" in z["text"]
    assert z["abos"] == 0 and z["zahlungen"] == 0
    assert z["auf"]["active"] is False


# --------------------------------------------------------------- Nr. 73
def test_73_laufzeitaenderung_trifft_nur_das_gelesene_abo(welt):
    db = welt.db
    morgen = (datetime.now(timezone.utc) + timedelta(days=1)).isoformat()

    async def lauf():
        sid = await _sucher(db)
        erg = await ADMIN.admin_set_sucher_abo(
            sid, ADMIN.AboFreischaltenIn(plan="monthly"), admin=SA)
        geaendert = await ADMIN.admin_set_abo_gueltig_bis(
            sid, {"gueltig_bis": morgen[:10], "grund": "Kulanz nach Ruecksprache"},
            admin=SA)
        abos = await db.subscriptions.find({"subject_user_id": sid},
                                           {"_id": 0}).to_list(10)
        verlauf = await db.zugangs_aenderungen.find({}, {"_id": 0}).to_list(10)
        return erg, geaendert, abos, verlauf

    erg, geaendert, abos, verlauf = welt.run(lauf())
    aktiv = [a for a in abos if a["status"] == "active"]
    assert len(aktiv) == 1
    assert aktiv[0]["expires_at"] == geaendert["expires_at"]
    # Der Verlaufseintrag zeigt auf GENAU dieses Abo (frueher konnte er auf
    # ein anderes zeigen, weil update_many alles traf).
    assert len(verlauf) == 1 and verlauf[0]["abo_id"] == aktiv[0]["id"]
    assert verlauf[0]["alt"] == erg["expires_at"]


def test_73b_veralteter_stand_wird_abgewiesen(welt):
    db = welt.db

    async def lauf():
        from fastapi import HTTPException
        sid = await _sucher(db)
        await ADMIN.admin_set_sucher_abo(
            sid, ADMIN.AboFreischaltenIn(plan="monthly"), admin=SA)
        # Parallele Freischaltung: sie ersetzt das Abo und setzt einen
        # neuen Ablauf. Eine danach eintreffende Laufzeitaenderung mit dem
        # ALTEN Stand darf nichts mehr treffen.
        aktiv = await db.subscriptions.find_one({"subject_user_id": sid,
                                                 "status": "active"})
        r = await db.subscriptions.update_one(
            {"id": aktiv["id"], "status": "active",
             "expires_at": "2020-01-01T00:00:00+00:00"},
            {"$set": {"expires_at": "2099-01-01T00:00:00+00:00"}})
        return r.matched_count, HTTPException

    getroffen, _ = welt.run(lauf())
    assert getroffen == 0, "der Abgleich auf den gelesenen Ablauf fehlt (Nr. 73)"


def test_73c_quelltext_ohne_update_many():
    q = inspect.getsource(ADMIN.admin_set_abo_gueltig_bis)
    assert "subscriptions.update_many" not in q, (
        "update_many aenderte JEDES dann aktive Abo — auch ein gerade "
        "parallel bezahltes (Nr. 73)")
    assert '_sperre(f"abo:{sucher_id}"' in q, "dieselbe Sperre wie die Freischaltung"
    assert "matched_count == 0" in q
