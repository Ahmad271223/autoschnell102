# -*- coding: utf-8 -*-
"""Rollenpruefung 22.09.2026 — Team admin_abo (Betreiber, Abo, Chefwechsel,
Anmeldung).

In-Prozess gegen eine Wegwerf-DB (autoschnell_rpaa_<uuid>), kein Server.
Jeder Test nennt die Befund-Nummern (RP-xxx), deren Verhalten er festhaelt.
"""
import asyncio
import os
import sys
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi import HTTPException, Response
from fastapi.security import HTTPAuthorizationCredentials

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import auth as AUTHMOD  # noqa: E402
import deps  # noqa: E402
import lifecycle  # noqa: E402
import rate_limiter as RL  # noqa: E402
import routes.admin as ADMIN  # noqa: E402
import routes.auth as AUTH  # noqa: E402
import routes.bestand as BESTAND  # noqa: E402
import routes.dealer as DEALER  # noqa: E402
import routes.marketplace as MARKT  # noqa: E402
import routes.team as TEAM  # noqa: E402

MONGO_URL = os.environ.get("MONGO_URL") or "mongodb://127.0.0.1:27017"
SA = {"id": "rpaa_sa", "role": "admin", "is_super_admin": True,
      "username": "rpaa-sa", "dealer_id": ""}


def _iso(**delta):
    return (datetime.now(timezone.utc) + timedelta(**delta)).isoformat()


@pytest.fixture
def welt(monkeypatch):
    from motor.motor_asyncio import AsyncIOMotorClient
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    client = AsyncIOMotorClient(MONGO_URL, serverSelectionTimeoutMS=5000)
    name = f"autoschnell_rpaa_{uuid.uuid4().hex[:10]}"
    db = client[name]
    for mod in (deps, ADMIN, TEAM, DEALER, MARKT, lifecycle, BESTAND, AUTH):
        monkeypatch.setattr(mod, "db", db)

    async def _kein_rs():
        return False
    # Einzelserver-Verhalten (Schritte einzeln) — der echte Client von deps
    # gehoert zu einer anderen Schleife.
    monkeypatch.setattr(deps, "ist_replica_set", _kein_rs)
    run = loop.run_until_complete
    run(db.manual_payments.create_index("vorgang_id", unique=True, sparse=True))
    run(db.job_locks.create_index("name", unique=True))
    s = uuid.uuid4().hex[:8]
    w = SimpleNamespace(db=db, run=run, s=s, dealer_id=f"d_{s}",
                        chef_id=f"chef_{s}", sucher_id=f"su_{s}")
    run(db.dealers.insert_one({"id": w.dealer_id, "user_id": w.chef_id,
                               "company_name": "RP Firma", "kunden_nr": 10999,
                               "created_at": _iso(days=-100)}))
    run(db.users.insert_many([
        {"id": w.chef_id, "role": "dealer", "dealer_id": w.dealer_id, "active": True,
         "kontonummer": "10999", "current_session_id": "sid-chef",
         "created_at": _iso(days=-100)},
        {"id": w.sucher_id, "role": "sucher", "dealer_id": w.dealer_id, "active": True,
         "kontonummer": "10999-1", "current_session_id": "sid-sucher",
         "created_at": _iso(days=-50)},
    ]))
    try:
        yield w
    finally:
        try:
            run(client.drop_database(name))
        finally:
            client.close()
            loop.close()


def _fehler(w, coro):
    try:
        w.run(coro)
    except HTTPException as e:
        return e.status_code, str(e.detail)
    return None, None


def _abo(w, **felder):
    doc = {"id": f"sub_{uuid.uuid4().hex[:8]}", "dealer_id": w.dealer_id,
           "plan": "monthly", "status": "active", "expires_at": _iso(days=10),
           "created_at": _iso(days=-20)}
    doc.update(felder)
    w.run(w.db.subscriptions.insert_one(dict(doc)))
    return doc


def _tage(iso):
    return (datetime.fromisoformat(iso) - datetime.now(timezone.utc)).total_seconds() / 86400


# ======================================================= RP-224 / RP-375
def test_rp224_chef_kann_fuer_sucher_keine_probe_anfragen(welt):
    w = welt
    chef = {"id": w.chef_id, "dealer_id": w.dealer_id, "role": "dealer"}
    for plan in ("probe3", "probe5", [], {}, None, "quatsch"):
        code, _ = _fehler(w, TEAM.sucher_abo_request(w.sucher_id, {"plan": plan}, chef))
        assert code == 400, plan
    assert w.run(w.db.plan_requests.count_documents({})) == 0
    erg = w.run(TEAM.sucher_abo_request(w.sucher_id, {"plan": "monthly"}, chef))
    assert erg["ok"]


# ======================================================= RP-050 / RP-149 / RP-230
def test_rp050_gekuendigtes_abo_zaehlt_als_restlaufzeit(welt):
    w = welt
    _abo(w, subject_user_id=w.sucher_id, status="cancelled", expires_at=_iso(days=10))
    erg = w.run(ADMIN.admin_set_sucher_abo(
        w.sucher_id, ADMIN.AboFreischaltenIn(plan="monthly"), admin=SA))
    assert 39.9 < _tage(erg["expires_at"]) < 40.1, "Resttage des gekuendigten Abos gehen nicht verloren"


def test_rp050_probe_ersetzt_kein_gekuendigtes_bezahltes_abo(welt):
    w = welt
    _abo(w, subject_user_id=w.sucher_id, status="cancelled", plan="yearly",
         expires_at=_iso(days=200))
    code, text = _fehler(w, ADMIN.admin_set_sucher_abo(
        w.sucher_id, ADMIN.AboFreischaltenIn(plan="probe3"), admin=SA))
    assert code == 400 and "bezahltes Abo" in text


def test_rp230_probe_vergleich_als_datum_nicht_als_text(welt):
    w = welt
    # Vor einer Stunde abgelaufen, aber mit +02:00 geschrieben: der alte
    # Stringvergleich hielt das fuer "laeuft noch" und sperrte die Probe.
    berlin = timezone(timedelta(hours=2))
    abgelaufen = (datetime.now(timezone.utc) - timedelta(hours=1)).astimezone(berlin).isoformat()
    _abo(w, subject_user_id=w.sucher_id, expires_at=abgelaufen)
    erg = w.run(ADMIN.admin_set_sucher_abo(
        w.sucher_id, ADMIN.AboFreischaltenIn(plan="probe3"), admin=SA))
    assert erg["plan"] == "probe3"


def test_rp230_datetime_altwert_gibt_kein_500(welt):
    w = welt
    _abo(w, subject_user_id=w.sucher_id, plan="yearly",
         expires_at=datetime.now(timezone.utc) + timedelta(days=100))
    code, text = _fehler(w, ADMIN.admin_set_sucher_abo(
        w.sucher_id, ADMIN.AboFreischaltenIn(plan="probe5"), admin=SA))
    assert code == 400 and "bezahltes Abo" in text


def test_rp228_probe_pruefung_steht_unter_der_sperre():
    import inspect
    q = inspect.getsource(ADMIN._abo_freischalten)
    meldung = "Dieses Konto hat bereits ein bezahltes Abo"
    assert meldung in q and "_ablauf_parsen" in q
    assert meldung not in inspect.getsource(ADMIN.admin_set_sucher_abo)


def test_rp050_gueltig_bis_auch_fuer_gekuendigtes_abo(welt):
    w = welt
    sub = _abo(w, subject_user_id=w.sucher_id, status="cancelled", expires_at=_iso(days=5))
    erg = w.run(ADMIN.admin_set_abo_gueltig_bis(
        w.sucher_id, {"gueltig_bis": "2099-06-30", "grund": "Kulanz"}, admin=SA))
    assert erg["expires_at"].startswith("2099-06-30")
    neu = w.run(w.db.subscriptions.find_one({"id": sub["id"]}))
    assert neu["expires_at"].startswith("2099-06-30") and neu["status"] == "cancelled"


def test_rp050_gueltig_bis_nicht_fuer_aufgehobenes_abo(welt):
    w = welt
    _abo(w, subject_user_id=w.sucher_id, status="cancelled", expires_at=_iso(minutes=-1))
    code, _ = _fehler(w, ADMIN.admin_set_abo_gueltig_bis(
        w.sucher_id, {"gueltig_bis": "2099-06-30", "grund": "x"}, admin=SA))
    assert code == 404, "ein aufgehobenes Abo wird ueber das Datum nicht wiederbelebt"


# Rollenpruefung 22.09.2026 (Welle 3, test_betreiber::test_14): die Umstellung
# auf sub_status_from_doc (RP-050) hatte 'Gueltig bis' fuer ein nur per Datum
# abgelaufenes Abo mit 404 verbaut. Der Betreiber steuert die Laufzeit frei:
# Datum in der Vergangenheit sperrt, ein neues Datum gibt den Zugang zurueck.
def test_welle3_gueltig_bis_verlaengert_ein_per_datum_abgelaufenes_abo(welt):
    w = welt
    sub = _abo(w, subject_user_id=w.sucher_id, expires_at=_iso(days=-1))
    sucher = w.run(w.db.users.find_one({"id": w.sucher_id}))
    assert w.run(deps.subscription_for(sucher))["active"] is False
    zahlungen = w.run(w.db.manual_payments.count_documents({}))
    erg = w.run(ADMIN.admin_set_abo_gueltig_bis(
        w.sucher_id, {"gueltig_bis": "2099-06-30", "grund": "Laufzeit verlaengert"}, admin=SA))
    assert erg["expires_at"].startswith("2099-06-30")
    neu = w.run(w.db.subscriptions.find_one({"id": sub["id"]}))
    assert neu["expires_at"].startswith("2099-06-30") and neu["status"] == "active"
    assert w.run(deps.subscription_for(sucher))["active"] is True
    assert w.run(w.db.manual_payments.count_documents({})) == zahlungen, "keine neue Zahlung"
    verlauf = w.run(w.db.zugangs_aenderungen.find_one({"abo_id": sub["id"]}))
    assert verlauf["art"] == "laufzeit_geaendert" and verlauf["alt"] == sub["expires_at"]


def test_welle3_gueltig_bis_weckt_kein_aelteres_abo_hinter_einem_aufgehobenen(welt):
    w = welt
    alt = _abo(w, subject_user_id=w.sucher_id, expires_at=_iso(days=-30),
               created_at=_iso(days=-60))
    _abo(w, subject_user_id=w.sucher_id, status="cancelled", expires_at=_iso(minutes=-1),
         created_at=_iso(days=-5))
    code, _ = _fehler(w, ADMIN.admin_set_abo_gueltig_bis(
        w.sucher_id, {"gueltig_bis": "2099-06-30", "grund": "x"}, admin=SA))
    assert code == 404, "massgeblich ist das juengste Abo — und das ist aufgehoben"
    assert w.run(w.db.subscriptions.find_one({"id": alt["id"]}))["expires_at"] == alt["expires_at"]


def test_welle3_gueltig_bis_beim_chef_auch_fuer_abgelaufenes_firmen_abo(welt):
    w = welt
    firma = _abo(w, plan="yearly", expires_at=_iso(days=-2))       # ohne subject_user_id
    w.run(ADMIN.admin_set_abo_gueltig_bis(
        w.chef_id, {"gueltig_bis": "2099-01-31", "grund": "Umstellung"}, admin=SA))
    assert w.run(w.db.subscriptions.find_one({"id": firma["id"]}))["expires_at"].startswith("2099-01-31")
    chef = w.run(w.db.users.find_one({"id": w.chef_id}))
    assert w.run(deps.subscription_for(chef))["active"] is True


# ======================================================= RP-051 / RP-150 / RP-229 / RP-380
def test_rp051_chef_verlaengerung_nimmt_das_firmen_abo_mit(welt):
    w = welt
    firma = _abo(w, plan="yearly", expires_at=_iso(days=100))      # ohne subject_user_id
    erg = w.run(ADMIN.admin_set_sucher_abo(
        w.chef_id, ADMIN.AboFreischaltenIn(plan="monthly"), admin=SA))
    assert 129.9 < _tage(erg["expires_at"]) < 130.1, "Resttage des Firmen-Abos bleiben"
    alt = w.run(w.db.subscriptions.find_one({"id": firma["id"]}))
    assert alt["status"] == "ersetzt" and alt["ersetzt_durch"] == erg["vorgang_id"]
    chef = w.run(w.db.users.find_one({"id": w.chef_id}))
    sub = w.run(deps.subscription_for(chef))
    assert sub["active"] and sub["plan"] == "monthly"


def test_rp051_gueltig_bis_beim_chef_trifft_das_firmen_abo(welt):
    w = welt
    firma = _abo(w, plan="yearly", expires_at=_iso(days=100))
    w.run(ADMIN.admin_set_abo_gueltig_bis(
        w.chef_id, {"gueltig_bis": "2099-01-31", "grund": "Umstellung"}, admin=SA))
    assert w.run(w.db.subscriptions.find_one({"id": firma["id"]}))["expires_at"].startswith("2099-01-31")


def test_rp229_aufheben_beim_chef_beendet_auch_das_firmen_abo(welt):
    w = welt
    firma = _abo(w, plan="yearly", expires_at=_iso(days=100))
    chef = w.run(w.db.users.find_one({"id": w.chef_id}))
    assert w.run(deps.subscription_for(chef))["active"] is True
    erg = w.run(ADMIN.admin_set_sucher_abo(w.chef_id, ADMIN.AboFreischaltenIn(plan=None), admin=SA))
    assert erg["active"] is False, "die Antwort nennt den tatsaechlichen Stand"
    assert w.run(w.db.subscriptions.find_one({"id": firma["id"]}))["status"] == "cancelled"
    assert w.run(deps.subscription_for(chef))["active"] is False


def test_rp229_aufheben_beim_sucher_laesst_das_firmen_abo_in_ruhe(welt):
    w = welt
    firma = _abo(w, plan="yearly", expires_at=_iso(days=100))
    _abo(w, subject_user_id=w.sucher_id)
    w.run(ADMIN.admin_set_sucher_abo(w.sucher_id, ADMIN.AboFreischaltenIn(plan=None), admin=SA))
    assert w.run(w.db.subscriptions.find_one({"id": firma["id"]}))["status"] == "active"


# ======================================================= RP-225 / RP-376
def test_rp225_gleicher_schluessel_bucht_nur_einmal(welt):
    w = welt
    koerper = dict(plan="monthly", idempotenz_schluessel="klick-abc-12345678")
    erste = w.run(ADMIN.admin_set_sucher_abo(
        w.sucher_id, ADMIN.AboFreischaltenIn(**koerper), admin=SA))
    zweite = w.run(ADMIN.admin_set_sucher_abo(
        w.sucher_id, ADMIN.AboFreischaltenIn(**koerper), admin=SA))
    assert zweite["bereits_freigeschaltet"] is True
    assert zweite["vorgang_id"] == erste["vorgang_id"]
    assert zweite["expires_at"] == erste["expires_at"], "nicht noch einmal verlaengert"
    assert w.run(w.db.manual_payments.count_documents({"subject_user_id": w.sucher_id})) == 1
    # ein NEUER Schluessel ist eine neue, gewollte Buchung
    w.run(ADMIN.admin_set_sucher_abo(w.sucher_id, ADMIN.AboFreischaltenIn(
        plan="monthly", idempotenz_schluessel="klick-neu-87654321"), admin=SA))
    assert w.run(w.db.manual_payments.count_documents({"subject_user_id": w.sucher_id})) == 2


def _anfrage(w, **felder):
    doc = {"id": f"req_{uuid.uuid4().hex[:8]}", "type": "sucher_abo",
           "subject_user_id": w.sucher_id, "dealer_id": w.dealer_id,
           "status": "offen", "wanted_plan": "monthly", "created_at": _iso()}
    doc.update(felder)
    w.run(w.db.plan_requests.insert_one(dict(doc)))
    return doc


def test_rp225_anfrage_wird_nur_einmal_freigeschaltet(welt):
    w = welt
    req = _anfrage(w)
    erste = w.run(ADMIN.admin_set_sucher_abo(
        w.sucher_id, ADMIN.AboFreischaltenIn(plan="monthly", anfrage_id=req["id"]), admin=SA))
    assert w.run(w.db.plan_requests.find_one({"id": req["id"]}))["status"] == "erledigt"
    zweite = w.run(ADMIN.admin_set_sucher_abo(
        w.sucher_id, ADMIN.AboFreischaltenIn(plan="monthly", anfrage_id=req["id"]), admin=SA))
    assert zweite["bereits_freigeschaltet"] and zweite["vorgang_id"] == erste["vorgang_id"]
    assert w.run(w.db.manual_payments.count_documents({"subject_user_id": w.sucher_id})) == 1


def test_rp225_anfrage_pruefungen(welt):
    w = welt
    fremd = _anfrage(w, subject_user_id=w.chef_id)
    code, _ = _fehler(w, ADMIN.admin_set_sucher_abo(
        w.sucher_id, ADMIN.AboFreischaltenIn(plan="monthly", anfrage_id=fremd["id"]), admin=SA))
    assert code == 400
    abgelehnt = _anfrage(w, status="abgelehnt")
    code, _ = _fehler(w, ADMIN.admin_set_sucher_abo(
        w.sucher_id, ADMIN.AboFreischaltenIn(plan="monthly", anfrage_id=abgelehnt["id"]), admin=SA))
    assert code == 409
    probe = _anfrage(w, wanted_plan="probe3", status="offen", subject_user_id=w.sucher_id,
                     dealer_id="andere")
    code, text = _fehler(w, ADMIN.admin_set_sucher_abo(
        w.sucher_id, ADMIN.AboFreischaltenIn(plan="probe3", anfrage_id=probe["id"]), admin=SA))
    assert code == 400 and "Probe" in text
    assert w.run(w.db.manual_payments.count_documents({})) == 0


# ======================================================= RP-227 / RP-378
def _vorgang(w, vid, minuten, status="laeuft", plan="monthly", tage=30):
    doc = {"id": vid, "typ": "freischaltung", "subject_user_id": w.sucher_id,
           "dealer_id": w.dealer_id, "plan": plan, "expires_at": _iso(days=tage),
           "betrag": 150.0, "waehrung": "EUR", "zahlungsart": "rechnung_bezahlt",
           "grund": "", "gezahlt_am": _iso()[:10], "notiz": "", "admin_id": SA["id"],
           "admin_email": "rpaa-sa", "status": status, "schritte": {},
           "created_at": _iso(minutes=minuten), "updated_at": _iso(minutes=minuten)}
    w.run(w.db.abo_vorgaenge.insert_one(dict(doc)))
    return doc


def test_rp227_nachholen_ueberschreibt_keine_neuere_freischaltung(welt):
    w = welt
    alt = _vorgang(w, f"alt_{w.s}", -30)
    neu = w.run(ADMIN.admin_set_sucher_abo(
        w.sucher_id, ADMIN.AboFreischaltenIn(plan="yearly"), admin=SA))
    n = w.run(ADMIN.abo_vorgaenge_nachholen())
    assert n == 1
    v_alt = w.run(w.db.abo_vorgaenge.find_one({"id": alt["id"]}))
    assert v_alt["status"] == "ueberholt" and v_alt["ueberholt_durch"] == neu["vorgang_id"]
    aktiv = w.run(w.db.subscriptions.find_one({"id": neu["vorgang_id"]}))
    assert aktiv["status"] == "active", "das neue Abo bleibt aktiv"
    assert w.run(w.db.subscriptions.count_documents({"id": alt["id"]})) == 0
    assert w.run(w.db.manual_payments.count_documents({"vorgang_id": alt["id"]})) == 0
    assert w.run(w.db.betriebsalarme.count_documents({"typ": "abo_vorgang_ueberholt"})) == 1


def test_rp227_nachholen_ohne_neueren_vorgang_fuehrt_zu_ende(welt):
    w = welt
    alt = _vorgang(w, f"alt2_{w.s}", -30)
    assert w.run(ADMIN.abo_vorgaenge_nachholen()) == 1
    assert w.run(w.db.abo_vorgaenge.find_one({"id": alt["id"]}))["status"] == "fertig"
    assert w.run(w.db.subscriptions.find_one({"id": alt["id"]}))["status"] == "active"
    assert w.run(w.db.manual_payments.count_documents({"vorgang_id": alt["id"]})) == 1


def test_rp227_schritt1_ersetzt_nur_aeltere_abos(welt):
    w = welt
    jung = _abo(w, subject_user_id=w.sucher_id, created_at=_iso(minutes=-1))
    alt = _vorgang(w, f"alt3_{w.s}", -30)
    w.run(ADMIN._abo_vorgang_ausfuehren(alt))
    assert w.run(w.db.subscriptions.find_one({"id": jung["id"]}))["status"] == "active"


def test_rp227_nachholen_wartet_auf_belegte_sperre(welt):
    w = welt
    _vorgang(w, f"alt4_{w.s}", -30)
    w.run(w.db.sperren.insert_one({"_id": f"abo:{w.sucher_id}", "owner": "anderer",
                                   "seit": _iso(), "bis": _iso(minutes=5)}))
    assert w.run(ADMIN.abo_vorgaenge_nachholen()) == 0
    assert w.run(w.db.abo_vorgaenge.find_one({"id": f"alt4_{w.s}"}))["status"] == "laeuft"


# ======================================================= RP-231 / RP-382
def test_rp231_keine_bezahlte_freischaltung_bei_gesperrter_oder_geloeschter_firma(welt):
    w = welt
    w.run(w.db.users.update_one({"id": w.chef_id}, {"$set": {"active": False}}))
    code, text = _fehler(w, ADMIN.admin_set_sucher_abo(
        w.sucher_id, ADMIN.AboFreischaltenIn(plan="monthly"), admin=SA))
    assert code == 409 and "gesperrt" in text
    w.run(w.db.users.update_one({"id": w.chef_id}, {"$set": {"active": True}}))
    w.run(w.db.dealers.update_one({"id": w.dealer_id},
                                  {"$set": {"loeschung": {"status": "laeuft"}}}))
    code, _ = _fehler(w, ADMIN.admin_set_sucher_abo(
        w.sucher_id, ADMIN.AboFreischaltenIn(plan="monthly"), admin=SA))
    assert code == 409
    w.run(w.db.dealers.update_one({"id": w.dealer_id}, {"$unset": {"loeschung": ""}}))
    w.run(w.db.users.update_one({"id": w.sucher_id},
                                {"$set": {"loeschung": {"status": "laeuft"}}}))
    code, _ = _fehler(w, ADMIN.admin_set_sucher_abo(
        w.sucher_id, ADMIN.AboFreischaltenIn(plan="monthly"), admin=SA))
    assert code == 409
    assert w.run(w.db.manual_payments.count_documents({})) == 0


# ======================================================= RP-053 / RP-152
def test_rp053_plan_type_ersetzt_das_alte_firmen_abo(welt):
    w = welt
    alt = _abo(w, plan="yearly", expires_at=_iso(days=300))
    w.run(ADMIN.admin_update_user(w.chef_id, body={"plan_type": "trial"}, admin=SA))
    offen = w.run(w.db.subscriptions.count_documents(
        {"dealer_id": w.dealer_id, "subject_user_id": None, "status": {"$ne": "ersetzt"}}))
    assert offen == 1
    alt_neu = w.run(w.db.subscriptions.find_one({"id": alt["id"]}))
    assert alt_neu["status"] == "ersetzt" and alt_neu["status_vorher"] == "active"


def test_rp053_zweites_dealer_konto_bekommt_ein_persoenliches_abo(welt):
    w = welt
    zweit = f"zweit_{w.s}"
    w.run(w.db.users.insert_one({"id": zweit, "role": "dealer", "dealer_id": w.dealer_id,
                                 "active": True, "created_at": _iso()}))
    w.run(ADMIN.admin_update_user(zweit, body={"plan_type": "monthly"}, admin=SA))
    sub = w.run(w.db.subscriptions.find_one({"dealer_id": w.dealer_id}))
    assert sub["subject_user_id"] == zweit, "kein Firmen-Abo fuer den Hauptchef"


# ======================================================= RP-057
def test_rp057_zweites_dealer_konto_erbt_kein_firmen_abo(welt):
    w = welt
    _abo(w, plan="yearly", expires_at=_iso(days=100))
    zweit = {"id": f"zweit_{w.s}", "role": "dealer", "dealer_id": w.dealer_id, "active": True}
    w.run(w.db.users.insert_one(dict(zweit, created_at=_iso())))
    assert w.run(deps.subscription_for(zweit))["active"] is False
    chef = w.run(w.db.users.find_one({"id": w.chef_id}))
    assert w.run(deps.subscription_for(chef))["active"] is True


# ======================================================= RP-028 / RP-029 / RP-031 (Chefwechsel, Loeschen)
def test_rp028_chefwechsel_schreibt_die_rolle_unter_der_sperre(welt):
    w = welt
    erg = w.run(ADMIN.admin_update_user(
        w.sucher_id, body={"role": "dealer", "chef_wechsel": True}, admin=SA))
    assert erg["ok"]
    assert w.run(w.db.users.find_one({"id": w.sucher_id}))["role"] == "dealer"
    assert w.run(w.db.users.find_one({"id": w.chef_id}))["role"] == "sucher"
    assert w.run(w.db.dealers.find_one({"id": w.dealer_id}))["user_id"] == w.sucher_id
    import inspect
    q = inspect.getsource(ADMIN.admin_update_user)
    assert 'fields.pop("role", None)' in q, "Rolle nie mehr ausserhalb der Sperre schreiben"


def test_rp028_veralteter_stand_wird_abgelehnt(welt):
    w = welt
    # Ein paralleler Chefwechsel hat das Konto schon befoerdert — der zweite
    # Aufruf mit dem alten Stand ("sucher") darf nichts mehr schreiben.
    w.run(w.db.users.update_one({"id": w.sucher_id}, {"$set": {"role": "dealer"}}))
    alt = {"id": w.sucher_id, "role": "sucher", "dealer_id": w.dealer_id}
    code, _ = _fehler(w, ADMIN._chef_befoerdern(alt, "sucher", {}, True, SA))
    assert code == 409
    assert w.run(w.db.dealers.find_one({"id": w.dealer_id}))["user_id"] == w.chef_id


def test_rp128_kein_chefwechsel_in_laufender_firmenloeschung(welt):
    w = welt
    w.run(w.db.dealers.update_one({"id": w.dealer_id},
                                  {"$set": {"loeschung": {"status": "laeuft"}}}))
    code, text = _fehler(w, ADMIN.admin_update_user(
        w.sucher_id, body={"role": "dealer", "chef_wechsel": True}, admin=SA))
    assert code == 409 and "gelöscht" in text
    assert w.run(w.db.users.find_one({"id": w.sucher_id}))["role"] == "sucher"


def test_rp029_loeschen_und_chefwechsel_teilen_die_sperre(welt):
    w = welt
    w.run(w.db.job_locks.insert_one({"name": f"chefwechsel-{w.dealer_id}", "owner": "x",
                                     "token": "t", "expires_at": datetime.now(timezone.utc)
                                     + timedelta(minutes=1)}))
    code, _ = _fehler(w, ADMIN.admin_delete_user(w.sucher_id, admin=SA))
    assert code == 409
    assert w.run(w.db.users.find_one({"id": w.sucher_id})).get("loeschung") is None


def test_rp029_sucher_der_inzwischen_chef_ist_wird_nicht_geloescht(welt):
    w = welt
    # Zeiger zeigt schon auf den "Sucher" (Chefwechsel lief dazwischen),
    # die Rolle ist aber noch nicht nachgezogen.
    w.run(w.db.dealers.update_one({"id": w.dealer_id}, {"$set": {"user_id": w.sucher_id}}))
    code, _ = _fehler(w, ADMIN.admin_delete_user(w.sucher_id, admin=SA))
    assert code == 409
    assert w.run(w.db.users.count_documents({"id": w.sucher_id})) == 1


def test_rp029_sucher_loeschen_in_laufender_firmenloeschung_409(welt):
    w = welt
    w.run(w.db.dealers.update_one({"id": w.dealer_id},
                                  {"$set": {"loeschung": {"status": "laeuft"}}}))
    code, _ = _fehler(w, ADMIN.admin_delete_user(w.sucher_id, admin=SA))
    assert code == 409


def test_rp031_zweites_dealer_konto_wird_wie_ein_sucher_geloescht(welt):
    w = welt
    zweit = f"zweit_{w.s}"
    w.run(w.db.users.insert_one({"id": zweit, "role": "dealer", "dealer_id": w.dealer_id,
                                 "active": True, "created_at": _iso(days=-200)}))
    w.run(w.db.vehicles.insert_one({"id": f"v_{w.s}", "dealer_id": w.dealer_id,
                                    "owner_user_id": zweit, "created_at": _iso()}))
    erg = w.run(ADMIN.admin_delete_user(zweit, admin=SA))      # ohne firma_loeschen
    assert erg["geloescht"] == "nur_nutzer"
    assert w.run(w.db.dealers.count_documents({"id": w.dealer_id})) == 1, "Firma bleibt"
    v = w.run(w.db.vehicles.find_one({"id": f"v_{w.s}"}))
    assert v["owner_user_id"] == w.chef_id, "an den ZEIGER, nicht ans aelteste dealer-Konto"


def test_rp031_hauptchef_loeschen_verlangt_weiter_firma_loeschen(welt):
    w = welt
    code, text = _fehler(w, ADMIN.admin_delete_user(w.chef_id, admin=SA))
    assert code == 409 and "firma_loeschen" in text


def test_rp031_zweitkonto_darf_zum_sucher_werden_hauptchef_nicht(welt):
    w = welt
    zweit = f"zweit_{w.s}"
    w.run(w.db.users.insert_one({"id": zweit, "role": "dealer", "dealer_id": w.dealer_id,
                                 "active": True, "current_session_id": "s",
                                 "created_at": _iso()}))
    w.run(ADMIN.admin_update_user(zweit, body={"role": "sucher"}, admin=SA))
    z = w.run(w.db.users.find_one({"id": zweit}))
    assert z["role"] == "sucher" and z["current_session_id"] is None
    code, _ = _fehler(w, ADMIN.admin_update_user(w.chef_id, body={"role": "sucher"}, admin=SA))
    assert code == 400


def test_rp028_abgebrochener_chefwechsel_wird_durch_wiederholen_fertig(welt):
    w = welt
    # Einzelserver-Abbruch: neue Rolle steht, Zeiger noch beim alten Chef.
    w.run(w.db.users.update_one({"id": w.sucher_id}, {"$set": {"role": "dealer"}}))
    w.run(ADMIN.admin_update_user(w.sucher_id, body={"role": "dealer", "chef_wechsel": True},
                                  admin=SA))
    assert w.run(w.db.dealers.find_one({"id": w.dealer_id}))["user_id"] == w.sucher_id
    assert w.run(w.db.users.find_one({"id": w.chef_id}))["role"] == "sucher"


# ======================================================= RP-046 / RP-145 (Wechsel zum Zwischenhaendler)
def test_rp046_zwischenhaendler_wechsel_uebergibt_den_vorgang_an_den_zeiger(welt):
    w = welt
    # aelteres, liegengebliebenes dealer-Konto: frueher bekam ES die Fahrzeuge
    w.run(w.db.users.insert_one({"id": f"alt_{w.s}", "role": "dealer", "dealer_id": w.dealer_id,
                                 "active": True, "created_at": _iso(days=-300)}))
    w.run(w.db.generated_pdfs.insert_one({"id": f"c_{w.s}", "dealer_id": w.dealer_id,
                                          "user_id": w.sucher_id}))
    w.run(w.db.vehicles.insert_one({"id": f"v_{w.s}", "dealer_id": w.dealer_id,
                                    "owner_user_id": w.sucher_id}))
    w.run(ADMIN.admin_update_user(w.sucher_id, body={"role": "b2b_buyer"}, admin=SA))
    assert w.run(w.db.generated_pdfs.find_one({"id": f"c_{w.s}"}))["user_id"] == w.chef_id
    assert w.run(w.db.vehicles.find_one({"id": f"v_{w.s}"}))["owner_user_id"] == w.chef_id
    u = w.run(w.db.users.find_one({"id": w.sucher_id}))
    assert u["role"] == "b2b_buyer" and u["dealer_id"] is None


def test_rp046_zwischenhaendler_wechsel_ohne_chef_409(welt):
    w = welt
    w.run(w.db.users.delete_one({"id": w.chef_id}))
    w.run(w.db.dealers.update_one({"id": w.dealer_id}, {"$unset": {"user_id": ""}}))
    code, _ = _fehler(w, ADMIN.admin_update_user(w.sucher_id, body={"role": "b2b_buyer"}, admin=SA))
    assert code == 409
    assert w.run(w.db.users.find_one({"id": w.sucher_id}))["role"] == "sucher"


# ======================================================= RP-151 / RP-131 (Sperren)
def test_rp151_zweites_dealer_konto_sperren_meldet_keine_sucher_ab(welt):
    w = welt
    zweit = f"zweit_{w.s}"
    w.run(w.db.users.insert_one({"id": zweit, "role": "dealer", "dealer_id": w.dealer_id,
                                 "active": True, "current_session_id": "s2",
                                 "created_at": _iso()}))
    erg = w.run(ADMIN.admin_user_set_active(zweit, ADMIN.AdminActiveIn(active=False), admin=SA))
    assert erg["sucher_abgemeldet"] == 0
    assert w.run(w.db.users.find_one({"id": w.sucher_id}))["current_session_id"] == "sid-sucher"


def test_rp131_chef_sperren_meldet_alle_anderen_konten_ab(welt):
    w = welt
    zweit = f"zweit_{w.s}"
    w.run(w.db.users.insert_one({"id": zweit, "role": "dealer", "dealer_id": w.dealer_id,
                                 "active": True, "current_session_id": "s2",
                                 "created_at": _iso()}))
    erg = w.run(ADMIN.admin_user_set_active(w.chef_id, ADMIN.AdminActiveIn(active=False), admin=SA))
    assert erg["sucher_abgemeldet"] == 2
    for uid in (w.sucher_id, zweit):
        assert w.run(w.db.users.find_one({"id": uid}))["current_session_id"] is None


def test_rp131_verwaister_zeiger_gleich_bewertet(welt):
    w = welt
    # Zeiger zeigt ins Leere, das aelteste dealer-Konto ist gesperrt
    w.run(w.db.dealers.update_one({"id": w.dealer_id}, {"$set": {"user_id": "gibt-es-nicht"}}))
    w.run(w.db.users.update_one({"id": w.chef_id}, {"$set": {"active": False}}))
    assert w.run(deps.firma_gesperrt(w.dealer_id)) is True
    assert w.dealer_id in w.run(deps.gesperrte_firmen_ids())


# ======================================================= RP-033 / RP-132 / RP-283 (Anzeigen)
def test_rp033_listen_kennen_den_chef_am_zeiger(welt):
    w = welt
    zweit = f"zweit_{w.s}"
    w.run(w.db.users.insert_one({"id": zweit, "role": "dealer", "dealer_id": w.dealer_id,
                                 "active": True, "created_at": _iso(days=-500)}))
    _abo(w, plan="yearly", expires_at=_iso(days=100))
    w.run(w.db.manual_payments.insert_one({"id": "p1", "subject_user_id": w.sucher_id,
                                           "zahlungsart": "probe", "created_at": _iso(days=-3)}))
    zeilen = w.run(ADMIN.admin_list_dealer_sucher(w.dealer_id, Response(), _=SA))
    je = {z["id"]: z for z in zeilen}
    assert zeilen[0]["id"] == w.chef_id, "der ZEIGER-Chef steht oben"
    assert je[w.chef_id]["ist_chef"] is True and je[w.chef_id]["subscription"]["active"]
    assert je[zweit]["ist_chef"] is False and je[zweit]["weiteres_dealer_konto"] is True
    assert je[zweit]["subscription"]["active"] is False, "kein Firmen-Abo-Rueckfall"
    assert je[w.sucher_id]["probe_vergeben_am"]
    alle = {u["id"]: u for u in w.run(ADMIN.admin_list_users(Response(), _=SA))}
    assert alle[w.chef_id]["ist_chef"] is True and alle[zweit]["ist_chef"] is False
    assert alle[zweit]["subscription"]["active"] is False
    vertraege = w.run(ADMIN.admin_user_contracts(zweit, Response(), _=SA))
    assert vertraege["umfang"] == "nutzer" and vertraege["user"]["ist_chef"] is False
    chef_v = w.run(ADMIN.admin_user_contracts(w.chef_id, Response(), _=SA))
    assert chef_v["umfang"] == "firma" and chef_v["user"]["ist_chef"] is True


# ======================================================= RP-232 / RP-383
def test_rp232_restlaufzeit_rundet_auf(welt):
    w = welt
    _abo(w, subject_user_id=w.sucher_id, plan="probe3", expires_at=_iso(days=2, hours=23))
    sucher = w.run(w.db.users.find_one({"id": w.sucher_id}, {"_id": 0}))
    d = w.run(DEALER.dealer_subscription(sucher))
    assert d["days_remaining"] == 3


def test_rp232_teamliste_zaehlt_abo_ohne_firmenfeld(welt):
    w = welt
    w.run(w.db.subscriptions.insert_one({"id": "alt", "subject_user_id": w.sucher_id,
                                         "plan": "monthly", "status": "active",
                                         "expires_at": _iso(days=5), "created_at": _iso()}))
    chef = w.run(w.db.users.find_one({"id": w.chef_id}, {"_id": 0}))
    zeilen = w.run(TEAM.list_sucher(Response(), user=chef))
    assert zeilen[0]["subscription"]["active"] is True


# ======================================================= RP-098 / RP-348 / RP-517 / RP-509
def _reserviert(w, lid, vid):
    w.run(w.db.vehicles.insert_one({"id": vid, "dealer_id": w.dealer_id,
                                    "lifecycle": "reserviert"}))
    w.run(w.db.resale_listings.insert_one({"id": lid, "dealer_id": w.dealer_id,
                                           "vehicle_id": vid, "status": "reserviert",
                                           "reserved_for": "kaeufer1",
                                           "published_at": _iso(days=-40)}))


def test_rp517_freigabe_zieht_das_fahrzeug_mit(welt):
    w = welt
    _reserviert(w, f"l_{w.s}", f"v_{w.s}")
    n = w.run(ADMIN.kaeufer_reservierungen_freigeben("kaeufer1", "kaeufer_gesperrt"))
    assert n == 1
    l = w.run(w.db.resale_listings.find_one({"id": f"l_{w.s}"}))
    assert l["status"] == "veroeffentlicht" and "reserved_for" not in l
    assert l.get("wieder_veroeffentlicht_am")
    assert w.run(w.db.vehicles.find_one({"id": f"v_{w.s}"}))["lifecycle"] == "veroeffentlicht"


def test_rp098_zugang_sperren_beendet_verhandlungen(welt):
    w = welt
    w.run(w.db.users.insert_one({"id": "kaeufer1", "role": "b2b_buyer", "active": True,
                                 "marketplace_access": {"active": True}}))
    _reserviert(w, f"l_{w.s}", f"v_{w.s}")
    w.run(w.db.listing_interest.insert_many([
        {"id": "i1", "buyer_user_id": "kaeufer1", "status": "offen"},
        {"id": "i2", "buyer_user_id": "kaeufer1", "status": "gegenangebot"}]))
    erg = w.run(ADMIN.admin_set_buyer_access("kaeufer1", {"plan": None}, admin=SA))
    assert erg["gesperrt"] and erg["reservierungen_freigegeben"] == 1
    for i in ("i1", "i2"):
        assert w.run(w.db.listing_interest.find_one({"id": i}))["status"] == "abgelehnt"
    assert w.run(w.db.vehicles.find_one({"id": f"v_{w.s}"}))["lifecycle"] == "veroeffentlicht"


def test_rp509_entsperren_behaelt_die_restlaufzeit(welt):
    w = welt
    w.run(w.db.users.insert_one({"id": "kaeufer2", "role": "b2b_buyer", "active": True,
                                 "marketplace_access": {"active": False, "gesperrt": True,
                                                        "expires_at": _iso(days=10)}}))
    erg = w.run(ADMIN.admin_set_buyer_access("kaeufer2", {"plan": "monthly"}, admin=SA))
    assert 39.9 < _tage(erg["expires_at"]) < 40.1


# ======================================================= RP-508
def test_rp508_abgelaufenes_paket_ohne_months_gilt_30_tage(welt, monkeypatch):
    w = welt
    monkeypatch.setattr(TEAM, "VERKAUF_KOSTENLOS", False)
    w.run(w.db.dealers.update_one({"id": w.dealer_id}, {"$set": {"sale_plan": {
        "tier": "s5", "valid_until": _iso(days=-3), "period_start": _iso(days=-40)}}}))
    erg = w.run(ADMIN.admin_set_sale_plan(w.dealer_id, {"tier": "s10"}, admin=SA))
    assert 29.9 < _tage(erg["sale_plan"]["valid_until"]) < 30.1
    z = w.run(w.db.manual_payments.find_one({"dealer_id": w.dealer_id}))
    assert z and z["amount"] == 19.99 and z["plan"] == "verkauf_s10"


# ======================================================= RP-554 / RP-556
def test_rp554_falsches_eigenes_passwort_ist_400(welt):
    w = welt
    w.run(w.db.users.insert_one({**SA, "password_hash": AUTHMOD.hash_password("Richtig-12345"),
                                 "active": True}))
    code, _ = _fehler(w, ADMIN.admin_self_password(
        ADMIN.AdminSelfPasswordIn(current_password="falsch", new_password="Neues-Passwort-9"),
        admin=SA))
    assert code == 400, "401 wuerde die Oberflaeche abmelden"


def test_rp556_abschalten_laesst_eine_gnadenfrist(welt, monkeypatch):
    import mfa as MFA
    w = welt
    monkeypatch.setenv("APP_ENV", "production")
    secret = MFA.secret_erzeugen()
    w.run(w.db.users.insert_one({**SA, "active": True, "mfa": {
        "aktiv": True, "secret": MFA.verschluesseln(secret), "letzter_zaehler": -1}}))
    erg = w.run(ADMIN.admin_mfa_deaktivieren(ADMIN.MfaCodeIn(code=MFA.totp(secret)), admin=SA))
    assert erg["aktiv"] is False and erg["pflicht"] is True and erg["neu_einrichten_bis"]
    m = w.run(w.db.users.find_one({"id": SA["id"]}))["mfa"]
    assert m["aktiv"] is False and m["pflicht_ausgesetzt_bis"] > deps.now_iso()
    st = w.run(ADMIN.admin_mfa_status(admin=SA))
    assert st["pflicht"] is True and st["neu_einrichten_bis"]


# ======================================================= RP-546 (gleitende Sitzung)
def test_rp546_token_wird_kurz_vor_ablauf_erneuert():
    jetzt = datetime.now(timezone.utc).timestamp()
    frisch = AUTHMOD.decode_token(AUTHMOD.create_token("u1", "sid1"))
    assert AUTHMOD.token_erneuern(frisch, "sucher") is None, "frisches Token bleibt"
    bald = {"sub": "u1", "sid": "sid1", "exp": jetzt + 3600, "seit": jetzt - 6 * 86400}
    neu = AUTHMOD.token_erneuern(bald, "sucher")
    p = AUTHMOD.decode_token(neu)
    assert p["sub"] == "u1" and p["sid"] == "sid1", "dieselbe Einzel-Sitzung"
    assert p["exp"] > jetzt + 6 * 86400 and p["seit"] == int(bald["seit"])
    zu_alt = dict(bald, seit=jetzt - (AUTHMOD.SITZUNG_MAX_TAGE + 1) * 86400)
    assert AUTHMOD.token_erneuern(zu_alt, "sucher") is None, "hoechstens SITZUNG_MAX_TAGE"
    assert AUTHMOD.token_erneuern(bald, "admin") is None, "nie fuer den Betreiber"
    assert AUTHMOD.token_erneuern(dict(bald, typ="mfa"), "sucher") is None
    # das Ende wird auf SITZUNG_MAX_TAGE gedeckelt
    knapp = dict(bald, seit=jetzt - (AUTHMOD.SITZUNG_MAX_TAGE - 3) * 86400)
    p2 = AUTHMOD.decode_token(AUTHMOD.token_erneuern(knapp, "sucher"))
    assert p2["exp"] <= knapp["seit"] + AUTHMOD.SITZUNG_MAX_TAGE * 86400 + 1


def test_rp546_current_user_liefert_das_neue_token_mit(welt):
    w = welt
    jetzt = datetime.now(timezone.utc)
    import jwt
    tok = jwt.encode({"sub": w.sucher_id, "sid": "sid-sucher",
                      "exp": jetzt + timedelta(hours=5),
                      "seit": int((jetzt - timedelta(days=6)).timestamp())},
                     AUTHMOD.JWT_SECRET, algorithm=AUTHMOD.JWT_ALG)
    resp = Response()
    user = w.run(deps.current_user(
        HTTPAuthorizationCredentials(scheme="Bearer", credentials=tok), resp))
    assert user["id"] == w.sucher_id
    neu = resp.headers.get(AUTHMOD.NEUES_TOKEN_KOPF)
    assert neu and AUTHMOD.decode_token(neu)["sid"] == "sid-sucher"
    # ohne Response (direkter Aufruf) laeuft alles wie bisher
    assert w.run(deps.current_user(
        HTTPAuthorizationCredentials(scheme="Bearer", credentials=tok)))["id"] == w.sucher_id


# ======================================================= RP-557 (bekanntes Geraet)
def test_rp557_bekanntes_geraet_entschaerft_die_kontosperre(welt, monkeypatch):
    w = welt
    monkeypatch.setattr(RL, "_RATE_LIMIT_ENABLED", True)
    monkeypatch.setattr(RL, "_LOGIN_KONTO_LIMIT", 30)

    async def _stand(_k):
        return 31
    monkeypatch.setattr(RL.login_konto_limiter, "stand", _stand)
    gid = w.run(RL.bekanntes_geraet_merken(w.db, "users", w.sucher_id, None))
    assert RL.geraet_id_gueltig(gid) == gid
    konto = w.run(w.db.users.find_one({"id": w.sucher_id}))
    assert gid not in str(konto["login_geraete_bekannt"]), "nur der HMAC liegt am Konto"
    ip = "198.51.100.7"
    assert w.run(RL.konto_gesperrt("10999-1", ip, konto)) is True, "fremdes Geraet: gesperrt"
    assert w.run(RL.konto_gesperrt("10999-1", ip, konto, geraet_id=gid)) is False
    assert w.run(RL.konto_gesperrt("10999-1", ip, konto, geraet_id="x" * 20)) is True
    # derselbe Schluessel wird nicht doppelt gemerkt, hoechstens 5 Geraete
    for _ in range(3):
        w.run(RL.bekanntes_geraet_merken(w.db, "users", w.sucher_id, gid))
    for _ in range(6):
        w.run(RL.bekanntes_geraet_merken(w.db, "users", w.sucher_id, None))
    konto = w.run(w.db.users.find_one({"id": w.sucher_id}))
    assert len(konto["login_geraete_bekannt"]) == 5


def test_rp557_anmeldung_gibt_den_geraete_schluessel_zurueck(welt):
    w = welt
    user = {"id": w.sucher_id, "role": "sucher", "dealer_id": w.dealer_id, "active": True,
            "password_hash": "h", "kontonummer": "10999-1"}
    w.run(w.db.users.update_one({"id": w.sucher_id}, {"$set": {"password_hash": "h"}}))
    erg = w.run(AUTH._sitzung_ausstellen(user, "198.51.100.8", "Chrome auf Android"))
    assert RL.geraet_id_gueltig(erg["geraet_id"])
    assert "login_geraete_bekannt" not in erg["user"]
    gid = "Mein-Geraet_1234567890"
    erg2 = w.run(AUTH._sitzung_ausstellen(user, "198.51.100.8", "", gid))
    assert erg2["geraet_id"] == gid
