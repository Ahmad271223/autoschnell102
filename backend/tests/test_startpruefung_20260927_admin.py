# -*- coding: utf-8 -*-
"""Startpruefung 27.09.2026 (Go-Live-Pruefbericht, gegengeprueft) — Admin-Teil:

G3 + Zusatzfund "Markt-Budget als Dauer-Vorgabe": PUT /admin/market/config uebernahm jedes budget_usd >= 0
ungeprueft und merkte es auch als Vorgabe fuer alle Folgemonate. Die Oberflaeche machte aus "1.000" 1 $.
Jetzt: 0 bleibt erlaubt (bewusst pausieren), 0 < Wert < 5 $ -> 422 "unplausibel klein — meinten Sie ...?",
ausser bestaetigt=true; die Pruefung laeuft VOR jeder Aenderung (km-/EZ-Bereiche bleiben unberuehrt).

H9-Kern: die Sucherliste der Firma sagt je Konto, ob PATCH .../abo-gueltig-bis ein neues Datum annimmt
(ablauf_korrigierbar) — auch bei einem nur per Datum abgelaufenen Abo; bei einem aufgehobenen nicht.
"""
import asyncio
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from fastapi import HTTPException, Response

sys.path.insert(0, str(Path(__file__).resolve().parent))
from test_befunde_runde17_termine import _module, welt  # noqa: E402,F401

# Beim Laden importieren (nicht im Test) — siehe test_markt_sichtbarkeit_20260927
R = _module("routes.markt_admin")
A = _module("routes.admin")

ADMIN = {"id": "sa_startpruefung", "email": "sa-startpruefung@e2etest-mail.de", "role": "admin",
         "is_super_admin": True, "dealer_id": ""}


def _iso(tage: int) -> str:
    return (datetime.now(timezone.utc) + timedelta(days=tage)).isoformat()


# ================================================= G3: Budget-Plausibilitaet
def test_01_budget_unplausibel_regeln():
    f = R.budget_unplausibel
    assert "meinten Sie 1.000 $" in f(1, False), "1.000 als 1 gelesen -> Rueckfrage nennt 1.000 $"
    assert "meinten Sie 700 $" in f(0.7, False)
    assert "meinten Sie 1.500 $" in f(1.5, False) and "Monatsbudget 1,5 $" in f(1.5, False)
    assert f(4.99, False)
    assert f(0, False) is None, "0 = bewusst pausieren bleibt erlaubt"
    assert f(5, False) is None and f(700, False) is None and f(1000.5, False) is None
    assert f(1, True) is None, "bestaetigt -> erlaubt"
    assert f(None, False) is None, "Budget nicht gesendet -> keine Pruefung"


def _rekorder(monkeypatch):
    aufrufe = []

    def mach(name, ergebnis=None):
        async def fn(*a, **k):
            aufrufe.append((name, a, k))
            return ergebnis
        return fn
    monkeypatch.setattr(R.segmente, "km_buckets_setzen", mach("km"))
    monkeypatch.setattr(R.segmente, "ez_buckets_setzen", mach("ez"))
    monkeypatch.setattr(R.segmente, "einstellungen_setzen", mach("rows"))
    monkeypatch.setattr(R.segmente, "synchronisieren", mach("sync", {"segmente": 16}))
    monkeypatch.setattr(R.budget, "budget_setzen", mach("budget"))
    monkeypatch.setattr(R.jobs, "intervall", mach("takt", {"intervall_tage": 3}))
    monkeypatch.setattr(R, "log_activity_sicher", mach("log"))
    return aufrufe


def test_02_config_lehnt_kleines_budget_vor_jeder_aenderung_ab(monkeypatch):
    aufrufe = _rekorder(monkeypatch)
    body = R.KonfigIn(km_buckets=[{"min_km": 10, "max_km": 30}], rows_je_segment=20, budget_usd=1)
    with pytest.raises(HTTPException) as ex:
        asyncio.run(R.admin_market_config(body, admin=ADMIN))
    assert ex.value.status_code == 422
    assert "unplausibel klein" in ex.value.detail and "meinten Sie 1.000 $" in ex.value.detail
    assert aufrufe == [], "nichts gespeichert — auch nicht die km-Bereiche aus derselben Anfrage"

    # bestaetigt -> gespeichert (als Dauer-Vorgabe, wie bisher ueber budget_setzen)
    erg = asyncio.run(R.admin_market_config(R.KonfigIn(budget_usd=1, bestaetigt=True), admin=ADMIN))
    assert erg["ok"] is True
    assert [a[1][1] for a in aufrufe if a[0] == "budget"] == [1.0]

    # 0 = bewusst pausieren -> ohne Bestaetigung erlaubt; normale Betraege ebenso
    aufrufe.clear()
    asyncio.run(R.admin_market_config(R.KonfigIn(budget_usd=0), admin=ADMIN))
    asyncio.run(R.admin_market_config(R.KonfigIn(budget_usd=700), admin=ADMIN))
    assert [a[1][1] for a in aufrufe if a[0] == "budget"] == [0.0, 700.0]
    # ohne Budget im Body (nur Bereiche) -> keine Rueckfrage
    aufrufe.clear()
    asyncio.run(R.admin_market_config(R.KonfigIn(rows_je_segment=10), admin=ADMIN))
    assert [a[0] for a in aufrufe if a[0] == "budget"] == []


def test_03_negativ_und_unsinn_scheitern_weiter_am_modell():
    from pydantic import ValidationError
    for falsch in (-1, "abc", float("nan")):
        with pytest.raises(ValidationError):
            R.KonfigIn(budget_usd=falsch)


# ================================================= H9: Ablaufdatum nach Ablauf korrigierbar
def test_04_sucherliste_ablauf_korrigierbar_wie_der_patch(welt, monkeypatch):
    w, db = welt.w, welt.db
    monkeypatch.setattr(A, "db", db)
    su2, su3 = f"su2_sp_{w.s}", f"su3_sp_{w.s}"
    ids = [w.sucher["id"], su2, su3, w.chef["id"]]

    async def vorbereiten():
        await db.users.insert_many([
            {"id": su2, "dealer_id": w.dealer_id, "role": "sucher", "active": True, "first_name": "Anna",
             "last_name": "Aufgehoben", "created_at": _iso(0)},
            {"id": su3, "dealer_id": w.dealer_id, "role": "sucher", "active": True, "first_name": "Lars",
             "last_name": "Laeuft", "created_at": _iso(0)}])
        await db.subscriptions.insert_many([
            # Jahr vertippt: status weiter 'active', Ablauf in der Vergangenheit
            {"id": f"abo1_{w.s}", "subject_user_id": w.sucher["id"], "dealer_id": w.dealer_id, "plan": "yearly",
             "status": "active", "expires_at": _iso(-365), "created_at": _iso(-1)},
            # 'Abo aufheben' -> cancelled mit Ablauf jetzt
            {"id": f"abo2_{w.s}", "subject_user_id": su2, "dealer_id": w.dealer_id, "plan": "monthly",
             "status": "cancelled", "expires_at": _iso(-1), "created_at": _iso(-2)},
            {"id": f"abo3_{w.s}", "subject_user_id": su3, "dealer_id": w.dealer_id, "plan": "monthly",
             "status": "active", "expires_at": _iso(20), "created_at": _iso(-2)}])

    async def aufraeumen():
        await db.subscriptions.delete_many({"subject_user_id": {"$in": ids}})
        await db.zugangs_aenderungen.delete_many({"subject_user_id": {"$in": ids}})
        await db.activity_logs.delete_many({"ref": {"$in": ids}})

    welt.run(vorbereiten())
    try:
        zeilen = {z["id"]: z for z in welt.run(A.admin_list_dealer_sucher(w.dealer_id, Response(), limit=200, seite=1, _=ADMIN))}
        s1, s2, s3, chef = zeilen[w.sucher["id"]], zeilen[su2], zeilen[su3], zeilen[w.chef["id"]]
        assert s1["subscription"]["active"] is False and s1["subscription"]["status"] == "expired"
        assert s1["ablauf_korrigierbar"] is True, "per Datum abgelaufen -> Datum korrigierbar (Knopf 'Speichern')"
        assert s2["subscription"]["status"] == "expired", "aufgehoben sieht in der Liste auch 'expired' aus ..."
        assert s2["ablauf_korrigierbar"] is False, "... der PATCH nimmt es aber nicht an (404)"
        assert s3["ablauf_korrigierbar"] is True
        assert chef["ablauf_korrigierbar"] is False

        # Gegenprobe am Server: genau dort, wo die Liste 'korrigierbar' sagt, nimmt der PATCH das Datum an
        neu = (datetime.now(timezone.utc) + timedelta(days=30)).date().isoformat()
        r = welt.run(A.admin_set_abo_gueltig_bis(w.sucher["id"], {"gueltig_bis": neu, "grund": "Jahr vertippt"},
                                                 admin=ADMIN))
        assert r["ok"] is True and r["expires_at"].startswith(neu)
        with pytest.raises(HTTPException) as ex:
            welt.run(A.admin_set_abo_gueltig_bis(su2, {"gueltig_bis": neu, "grund": "Test"}, admin=ADMIN))
        assert ex.value.status_code == 404
        zeilen = {z["id"]: z for z in welt.run(A.admin_list_dealer_sucher(w.dealer_id, Response(), limit=200, seite=1, _=ADMIN))}
        assert zeilen[w.sucher["id"]]["subscription"]["active"] is True, "Zugang ohne neue Zahlung zurueck"
        assert welt.run(db.manual_payments.count_documents({"subject_user_id": w.sucher["id"]})) == 0
    finally:
        welt.run(aufraeumen())


# ================================================= H9-Rest (28.09.2026): auch beim Hauptchef / ohne Datum
def _liste(welt):
    return {z["id"]: z for z in welt.run(
        A.admin_list_dealer_sucher(welt.w.dealer_id, Response(), limit=200, seite=1, _=ADMIN))}


def _patch_ok(welt, konto_id, neu):
    r = welt.run(A.admin_set_abo_gueltig_bis(konto_id, {"gueltig_bis": neu, "grund": "Datum korrigiert"},
                                             admin=ADMIN))
    return r["ok"] is True and r["expires_at"].startswith(neu)


def test_05_chef_mit_vertipptem_datum_ohne_firmen_abo(welt, monkeypatch):
    """Die Freischaltung schreibt beim Chef ein PERSOENLICHES Abo. Ist es nur per Datum abgelaufen und gibt
    es kein Firmen-Abo, zeigt subscription_for den Firmen-Rueckfall (status 'none'). Die Liste sagte deshalb
    'nicht korrigierbar', obwohl der PATCH das Datum annimmt."""
    w, db = welt.w, welt.db
    monkeypatch.setattr(A, "db", db)
    chef = w.chef["id"]
    welt.run(db.subscriptions.insert_one(
        {"id": f"abo_chef_{w.s}", "subject_user_id": chef, "dealer_id": w.dealer_id, "plan": "yearly",
         "status": "active", "expires_at": _iso(-365), "created_at": _iso(-1)}))
    try:
        z = _liste(welt)[chef]
        assert z["ist_chef"] is True and z["subscription"]["active"] is False
        assert z["subscription"]["status"] == "none", "Anzeige: Firmen-Rueckfall ohne Firmen-Abo"
        assert z["ablauf_korrigierbar"] is True, "Chef: vertipptes Datum muss per 'Speichern' korrigierbar sein"
        assert z["ablauf_abo_bis"][:10] == _iso(-365)[:10], "Liste nennt den Ablauf des eigenen Abos"
        # Gegenprobe am Server: der PATCH nimmt genau dieses Datum an
        neu = (datetime.now(timezone.utc) + timedelta(days=30)).date().isoformat()
        assert _patch_ok(welt, chef, neu)
        assert _liste(welt)[chef]["subscription"]["active"] is True
    finally:
        welt.run(db.subscriptions.delete_many({"subject_user_id": chef}))
        welt.run(db.zugangs_aenderungen.delete_many({"subject_user_id": chef}))
        welt.run(db.activity_logs.delete_many({"ref": chef}))


def test_06_fehlendes_datum_ist_korrigierbar_chef_und_sucher(welt, monkeypatch):
    """Ablaufdatum fehlt (status 'ungueltig' bzw. beim Chef 'none'): der PATCH setzt ein Datum — die Liste
    muss dasselbe sagen. Aufgehobene Abos bleiben weiter aussen vor (siehe test_04)."""
    w, db = welt.w, welt.db
    monkeypatch.setattr(A, "db", db)
    chef, su = w.chef["id"], w.sucher["id"]
    welt.run(db.subscriptions.insert_many([
        {"id": f"abo_chef_nd_{w.s}", "subject_user_id": chef, "dealer_id": w.dealer_id, "plan": "monthly",
         "status": "active", "created_at": _iso(-1)},
        {"id": f"abo_su_nd_{w.s}", "subject_user_id": su, "dealer_id": w.dealer_id, "plan": "monthly",
         "status": "active", "expires_at": "irgendwann", "created_at": _iso(-1)}]))
    try:
        zeilen = _liste(welt)
        assert zeilen[su]["subscription"]["status"] == "ungueltig"
        assert zeilen[su]["ablauf_korrigierbar"] is True, "Sucher mit unlesbarem Datum: korrigierbar"
        assert zeilen[chef]["subscription"]["active"] is False
        assert zeilen[chef]["ablauf_korrigierbar"] is True, "Chef ohne Datum: korrigierbar"
        assert zeilen[chef]["ablauf_abo_bis"] is None
        neu = (datetime.now(timezone.utc) + timedelta(days=10)).date().isoformat()
        assert _patch_ok(welt, chef, neu) and _patch_ok(welt, su, neu)
        zeilen = _liste(welt)
        assert zeilen[chef]["subscription"]["active"] is True and zeilen[su]["subscription"]["active"] is True
    finally:
        welt.run(db.subscriptions.delete_many({"subject_user_id": {"$in": [chef, su]}}))
        welt.run(db.zugangs_aenderungen.delete_many({"subject_user_id": {"$in": [chef, su]}}))
        welt.run(db.activity_logs.delete_many({"ref": {"$in": [chef, su]}}))
