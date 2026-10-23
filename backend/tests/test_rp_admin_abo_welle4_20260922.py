# -*- coding: utf-8 -*-
"""Rollenpruefung 22.09.2026, Welle 4 — Review-Befunde fuer Team admin_abo.

In-Prozess gegen eine Wegwerf-DB (autoschnell_rpaa4_<uuid>), kein Server.
  Review 1  PUT /admin/users/{id} role=dealer stufte den Super-Admin herab
            (Schutz stand erst NACH dem Rollen-Schreiben unter der Sperre)
  Review 3  Logowechsel loeschte die Datei, die Vertraege per
            contract_data.logo_key fuer spaetere Fassungen brauchen
(Review 2 — AuthContext nach Token-Verlaengerung — steht in
 frontend/src/context/AuthSitzung.rp_admin_abo.test.jsx.)
"""
import asyncio
import os
import sys
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import deps  # noqa: E402
import routes.admin as ADMIN  # noqa: E402
import routes.dealer as DEALER  # noqa: E402
import storage_service  # noqa: E402

MONGO_URL = os.environ.get("MONGO_URL") or "mongodb://127.0.0.1:27017"


def _iso(**delta):
    return (datetime.now(timezone.utc) + timedelta(**delta)).isoformat()


@pytest.fixture
def welt(monkeypatch):
    from motor.motor_asyncio import AsyncIOMotorClient
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    client = AsyncIOMotorClient(MONGO_URL, serverSelectionTimeoutMS=5000)
    name = f"autoschnell_rpaa4_{uuid.uuid4().hex[:10]}"
    db = client[name]
    for mod in (deps, ADMIN, DEALER):
        monkeypatch.setattr(mod, "db", db)

    async def _kein_rs():
        return False
    monkeypatch.setattr(deps, "ist_replica_set", _kein_rs)
    run = loop.run_until_complete
    s = uuid.uuid4().hex[:8]
    # Wie server.seed_super_admin: der Betreiber hat eine eigene Firma, deren
    # Zeiger (dealers.user_id) auf ihn zeigt, und eine dealer_id am Konto.
    w = SimpleNamespace(db=db, run=run, s=s, sa_firma=f"dsa_{s}", sa_id=f"sa_{s}",
                        dealer_id=f"d_{s}", chef_id=f"chef_{s}", sucher_id=f"su_{s}")
    w.sa = {"id": w.sa_id, "role": "admin", "is_super_admin": True,
            "username": f"sa-{s}", "dealer_id": w.sa_firma}
    try:
        run(db.job_locks.create_index("name", unique=True))
        run(db.dealers.insert_many([
            {"id": w.sa_firma, "user_id": w.sa_id, "company_name": "Betreiber",
             "kunden_nr": 10001, "created_at": _iso(days=-200)},
            {"id": w.dealer_id, "user_id": w.chef_id, "company_name": "RP Firma 4",
             "kunden_nr": 10997, "created_at": _iso(days=-100)},
        ]))
        run(db.users.insert_many([
            {**w.sa, "active": True, "current_session_id": "sid-sa",
             "created_at": _iso(days=-200)},
            {"id": w.chef_id, "role": "dealer", "dealer_id": w.dealer_id, "active": True,
             "kontonummer": "10997", "current_session_id": "sid-chef",
             "created_at": _iso(days=-100)},
            {"id": w.sucher_id, "role": "sucher", "dealer_id": w.dealer_id, "active": True,
             "kontonummer": "10997-1", "current_session_id": "sid-sucher",
             "created_at": _iso(days=-50)},
        ]))
        run(db.vehicles.insert_one({"id": f"v_{s}", "dealer_id": w.sa_firma,
                                    "owner_user_id": w.sa_id, "created_at": _iso()}))
        run(db.subscriptions.insert_one({"id": f"sub_{s}", "dealer_id": w.sa_firma,
                                         "subject_user_id": w.sa_id, "plan": "yearly",
                                         "status": "active", "expires_at": _iso(days=300),
                                         "created_at": _iso()}))
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


def _sa_unveraendert(w):
    sa = w.run(w.db.users.find_one({"id": w.sa_id}))
    assert sa["role"] == "admin" and sa["is_super_admin"] is True
    assert sa["current_session_id"] == "sid-sa", "Betreiber darf nicht abgemeldet werden"
    assert w.run(w.db.dealers.find_one({"id": w.sa_firma}))["user_id"] == w.sa_id


# ======================================================= Review 1 (Super-Admin)
@pytest.mark.parametrize("rolle", ["dealer", "sucher", "b2b_buyer"])
def test_super_admin_rolle_bleibt_bei_jedem_ziel(welt, rolle):
    w = welt
    for body in ({"role": rolle}, {"role": rolle, "chef_wechsel": True}):
        code, text = _fehler(w, ADMIN.admin_update_user(w.sa_id, body=body, admin=w.sa))
        assert code == 400 and "Super-Admin" in text, (body, code, text)
        _sa_unveraendert(w)
    # Keine Nebenwirkungen des Zwischenhaendler-Zweigs (vorher VOR der Pruefung)
    v = w.run(w.db.vehicles.find_one({"id": f"v_{w.s}"}))
    assert v["owner_user_id"] == w.sa_id
    sub = w.run(w.db.subscriptions.find_one({"id": f"sub_{w.s}"}))
    assert sub["status"] == "active"


def test_super_admin_zweite_linie_in_den_helfern(welt):
    w = welt
    code, _ = _fehler(w, ADMIN._chef_befoerdern(
        {"id": w.sa_id, "role": "admin", "dealer_id": w.sa_firma}, "admin", {}, True, w.sa))
    assert code == 400
    _sa_unveraendert(w)
    # Ein (kaputter) Stand mit role=dealer am Betreiber-Konto: die Herabstufung
    # als "Zweitkonto" schreibt trotzdem nichts.
    w.run(w.db.users.update_one({"id": w.sa_id}, {"$set": {"role": "dealer"}}))
    w.run(w.db.dealers.update_one({"id": w.sa_firma}, {"$set": {"user_id": "jemand"}}))
    code, _ = _fehler(w, ADMIN._zweitkonto_herabstufen(
        {"id": w.sa_id, "role": "dealer", "dealer_id": w.sa_firma}))
    assert code == 409
    assert w.run(w.db.users.find_one({"id": w.sa_id}))["role"] == "dealer"


def test_normaler_chefwechsel_und_sperre_des_betreibers_unveraendert(welt):
    w = welt
    erg = w.run(ADMIN.admin_update_user(
        w.sucher_id, body={"role": "dealer", "chef_wechsel": True}, admin=w.sa))
    assert erg["ok"]
    assert w.run(w.db.users.find_one({"id": w.sucher_id}))["role"] == "dealer"
    assert w.run(w.db.users.find_one({"id": w.chef_id}))["role"] == "sucher"
    assert w.run(w.db.dealers.find_one({"id": w.dealer_id}))["user_id"] == w.sucher_id
    # Unveraenderte Rolle am Betreiber (role=admin) bleibt die bekannte 400.
    code, _ = _fehler(w, ADMIN.admin_update_user(w.sa_id, body={"role": "admin"}, admin=w.sa))
    assert code == 400
    code, text = _fehler(w, ADMIN.admin_update_user(w.sa_id, body={"active": False},
                                                    admin=w.sa))
    assert code == 400 and "gesperrt" in text
    _sa_unveraendert(w)


# ======================================================= Review 3 (Logo im Vertrag)
@pytest.fixture
def loeschungen(monkeypatch):
    weg = []

    async def _loeschen(_db, *, key=None, grund="", dealer_id="", **_kw):
        weg.append((key, grund))
        return True
    monkeypatch.setattr(storage_service, "loeschen_oder_vormerken", _loeschen)
    return weg


def test_logo_bleibt_solange_ein_vertrag_es_nennt(welt, loeschungen):
    w = welt
    alt = f"/api/files/logo/{w.dealer_id}/alt.png"
    key = f"logo/{w.dealer_id}/alt.png"
    w.run(w.db.generated_pdfs.insert_one({
        "id": f"c_{w.s}", "dealer_id": w.dealer_id, "user_id": w.chef_id,
        "contract_data": {"logo_key": key, "seller_name": "X"}, "created_at": _iso()}))
    # Chef hat inzwischen ein neues Logo — die alte Datei nennt nur der Vertrag.
    w.run(w.db.dealers.update_one({"id": w.dealer_id},
                                  {"$set": {"logo_url": f"/api/files/logo/{w.dealer_id}/neu.png"}}))
    w.run(DEALER._altes_logo_wegraeumen(alt + "?v=3", w.dealer_id))
    assert loeschungen == []
    # Ein Vertrag einer ANDEREN Firma mit gleichem Schluessel zaehlt nicht
    # (der Schluessel liegt immer unter logo/<eigene Firma>/).
    w.run(w.db.generated_pdfs.update_one({"id": f"c_{w.s}"},
                                         {"$set": {"dealer_id": "d_fremd"}}))
    w.run(DEALER._altes_logo_wegraeumen(alt, w.dealer_id))
    assert loeschungen == [(key, "logo_ersetzt")]


def test_logo_ohne_vertrag_und_ohne_nutzer_wird_weiter_weggeraeumt(welt, loeschungen):
    w = welt
    w.run(w.db.generated_pdfs.insert_one({
        "id": f"c2_{w.s}", "dealer_id": w.dealer_id,
        "contract_data": {"logo_key": f"logo/{w.dealer_id}/anderes.png"}}))
    w.run(DEALER._altes_logo_wegraeumen(f"/api/files/logo/{w.dealer_id}/weg.png", w.dealer_id))
    assert loeschungen == [(f"logo/{w.dealer_id}/weg.png", "logo_ersetzt")]
