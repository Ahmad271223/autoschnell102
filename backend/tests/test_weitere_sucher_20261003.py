# -*- coding: utf-8 -*-
"""Wunsch Ahmad 03.10.2026: "Weitere Sucher anfragen" auf der Team-Seite des Chefs.

Vorher war der Knopf ein mailto-Link an eine Adresse, die niemand las — die Anfrage kam nie an.
Jetzt: echte Anfrage (plan_requests, type "weitere_sucher"), sichtbar im Admin-Bereich
(Freischaltungen, Zahl "Offene Anfragen" auf der Uebersicht) und per Betriebsmeldung an
BETRIEB_MELDUNG_AN. Eine offene Anfrage je Firma; eine Aenderung wird erneut gemeldet.
In-Prozess gegen eine Wegwerf-Datenbank.
"""
import asyncio
import os
import sys
import uuid
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi import HTTPException
from pydantic import ValidationError

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import betriebsmeldung as BM  # noqa: E402
import deps  # noqa: E402
import routes.admin as A  # noqa: E402
import routes.team as T  # noqa: E402

MONGO_URL = os.environ.get("MONGO_URL") or "mongodb://127.0.0.1:27017"


@pytest.fixture
def welt(monkeypatch):
    from motor.motor_asyncio import AsyncIOMotorClient
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    client = AsyncIOMotorClient(MONGO_URL, serverSelectionTimeoutMS=5000)
    name = f"autoschnell_ws_{uuid.uuid4().hex[:10]}"
    db = client[name]
    for mod in (deps, T, A):
        monkeypatch.setattr(mod, "db", db)
    monkeypatch.setenv("BETRIEB_MELDUNG_AN", "ahmadfkh006@gmail.com")
    gesendet = []

    async def _fake_send(to, betreff, text, *a, **k):
        gesendet.append({"to": to, "betreff": betreff, "text": text, "key": k.get("idempotency_key")})
        return True

    import email_service
    monkeypatch.setattr(email_service, "send_email", _fake_send)
    s = uuid.uuid4().hex[:8]
    chef = {"id": f"chef-{s}", "role": "dealer", "dealer_id": f"d-{s}", "active": True,
            "kontonummer": "10002", "email": "chef@kfz-mueller.de"}
    sucher = {"id": f"such-{s}", "role": "sucher", "dealer_id": f"d-{s}", "active": True}
    loop.run_until_complete(db.users.insert_many([dict(chef), dict(sucher)]))
    loop.run_until_complete(db.dealers.insert_one({"id": f"d-{s}", "user_id": chef["id"],
                                                   "company_name": "KFZ Müller GmbH", "kunden_nr": 10002,
                                                   "phone": "030 123456", "email": "info@kfz-mueller.de"}))
    loop.run_until_complete(T.__dict__.get("db").plan_requests.create_index(
        [("type", 1), ("dealer_id", 1)], unique=True, name="uniq_offene_weitere_sucher_anfrage",
        partialFilterExpression={"type": "weitere_sucher", "status": "offen"}))
    try:
        yield SimpleNamespace(db=db, run=loop.run_until_complete, chef=chef, sucher=sucher, gesendet=gesendet)
    finally:
        try:
            loop.run_until_complete(client.drop_database(name))
        finally:
            client.close()
            loop.close()


def _anfrage(w):
    return w.run(w.db.plan_requests.find_one({"type": "weitere_sucher"}, {"_id": 0}))


def test_01_anfrage_landet_beim_betreiber(welt):
    w = welt
    assert w.run(T.weitere_sucher_anfrage_lesen(w.chef)) == {"anfrage": None}
    r = w.run(T.weitere_sucher_anfragen(T.WeitereSucherIn(anzahl=3, plan="yearly",
                                                          message="Anna und Ben"), w.chef))
    assert r["ok"] and not r.get("bereits_offen")
    a = _anfrage(w)
    assert a["status"] == "offen" and a["dealer_id"] == w.chef["dealer_id"]
    assert a["sucher_anzahl"] == 3 and a["wanted_plan"] == "yearly" and a["message"] == "Anna und Ben"
    assert a["wanted"] == "3 weitere Sucher-Zugänge · Jährlich (1.500 € je Sucher)"
    assert a["company_name"] == "KFZ Müller GmbH" and a["kunden_nr"] == 10002
    assert a["contact_email"] == "chef@kfz-mueller.de" and a["contact_phone"] == "030 123456"
    assert a["requester_user_id"] == w.chef["id"] and "gemeldet_am" not in a
    assert "wanted_tier" not in a, "sonst zeigte die Admin-Seite 'Paket aktivieren'"
    lesen = w.run(T.weitere_sucher_anfrage_lesen(w.chef))["anfrage"]
    assert lesen["id"] == a["id"] and lesen["sucher_anzahl"] == 3


def test_02_aenderung_aktualisiert_und_meldet_neu(welt):
    w = welt
    w.run(T.weitere_sucher_anfragen(T.WeitereSucherIn(anzahl=2), w.chef))
    erste = _anfrage(w)
    w.run(w.db.plan_requests.update_one({"id": erste["id"]}, {"$set": {"gemeldet_am": "2026-10-03T10:00:00"}}))
    # gleiche Angaben: nichts Neues zu melden
    r = w.run(T.weitere_sucher_anfragen(T.WeitereSucherIn(anzahl=2), w.chef))
    assert r["bereits_offen"] and "bereits" in r["hinweis"] and "gemeldet_am" in _anfrage(w)
    # mehr Sucher: dieselbe Anfrage, erneut zu melden
    r = w.run(T.weitere_sucher_anfragen(T.WeitereSucherIn(anzahl=5, message="doch fünf"), w.chef))
    a = _anfrage(w)
    assert r["bereits_offen"] and "aktualisiert" in r["hinweis"]
    assert a["id"] == erste["id"] and a["sucher_anzahl"] == 5 and "gemeldet_am" not in a
    assert w.run(w.db.plan_requests.count_documents({"type": "weitere_sucher"})) == 1


def test_03_nur_der_chef_und_gueltige_angaben(welt):
    w = welt
    with pytest.raises(HTTPException) as e:
        w.run(T.current_chef(w.sucher))
    assert e.value.status_code == 403
    with pytest.raises(HTTPException) as e:
        w.run(T.weitere_sucher_anfragen(T.WeitereSucherIn(anzahl=1, plan="probe3"), w.chef))
    assert e.value.status_code == 400, "Probe-Abos vergibt nur der Betreiber"
    for falsch in (0, 51):
        with pytest.raises(ValidationError):
            T.WeitereSucherIn(anzahl=falsch)
    # die Routen haengen am Chef
    for route in T.router.routes:
        if getattr(route, "path", "") == "/dealer/sucher-zugaenge-anfrage":
            assert "current_chef" in {d.call.__name__ for d in route.dependant.dependencies}


def test_04_mail_an_die_betriebsadresse(welt):
    w = welt
    w.run(T.weitere_sucher_anfragen(T.WeitereSucherIn(anzahl=3, plan="monthly", message="Anna"), w.chef))
    assert w.run(BM.neue_anfragen_melden(w.db)) == 1
    mail = w.gesendet[-1]
    assert mail["to"] == "ahmadfkh006@gmail.com"
    assert "KFZ Müller GmbH" in mail["betreff"]
    for stueck in ("Weitere Sucher-Zugaenge angefragt", "Sucher gewuenscht: 3", "Monatlich", "Anna",
                   "030 123456", "chef@kfz-mueller.de"):
        assert stueck in mail["text"], stueck
    assert "gemeldet_am" in _anfrage(w)
    assert w.run(BM.neue_anfragen_melden(w.db)) == 0, "nicht doppelt"
    # Aenderung -> neue Mail mit NEUER Kennung (sonst verwirft der Mailanbieter sie als Doppel)
    w.run(T.weitere_sucher_anfragen(T.WeitereSucherIn(anzahl=4, plan="monthly", message="Anna"), w.chef))
    assert w.run(BM.neue_anfragen_melden(w.db)) == 1
    assert "Sucher gewuenscht: 4" in w.gesendet[-1]["text"]
    assert w.gesendet[-1]["key"] != w.gesendet[-2]["key"]


def test_05_admin_zahl_und_index(welt):
    w = welt
    w.run(T.weitere_sucher_anfragen(T.WeitereSucherIn(anzahl=1), w.chef))
    stats = w.run(A.admin_stats(None))
    assert stats["anfragen_offen"] == 1
    import indizes as IX
    w.run(IX.plan_requests_unique_indizes(w.db))
    idx = w.run(w.db.plan_requests.index_information())
    assert idx["uniq_offene_weitere_sucher_anfrage"]["partialFilterExpression"] == \
        {"type": "weitere_sucher", "status": "offen"}
    assert BM.anfrage_ueberschrift({"type": "weitere_sucher"}) == "Weitere Sucher-Zugaenge angefragt"
