# -*- coding: utf-8 -*-
"""Runde 16 (15.09.2026): Reviewer-Listen Versand/PDF/WhatsApp, Sucher-Funktionen,
Anbieter-Abrufe.

Wegwerf-Datenbank je Test (echtes Mongo), keine HTTP-Aufrufe.
- Versand-Schluessel: gescheiterter Versand hinterlaesst keinen "bereits gesendet"
- Schluessel an Inhalt gebunden (409), Different-Key-Race gesperrt
- WhatsApp-Link nach Versions-Race auf die aktuelle Fassung; alte Links laufen weiter
- Kundenfassung: Cache-CAS respektiert; Neuerzeugung setzt "versendet" zurueck
- gesperrte Firmen ueber dealers.user_id; Fahrzeugliste meldet Kuerzung
- Rueckfall-Rueckbuchung; Konto-Bremse fuer direkte Abrufe
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
from fastapi import HTTPException, Response

BACKEND = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND))
sys.path.insert(0, str(BACKEND / "tests"))

import deps  # noqa: E402
import routes.contracts as C  # noqa: E402
import routes.listings as L  # noqa: E402
import vertrag_mail as VM  # noqa: E402

MONGO_URL = os.environ.get("MONGO_URL") or "mongodb://127.0.0.1:27017"


def _jetzt(delta_s: int = 0) -> str:
    return (datetime.now(timezone.utc) + timedelta(seconds=delta_s)).isoformat()


@pytest.fixture
def wegwerf(monkeypatch):
    from motor.motor_asyncio import AsyncIOMotorClient
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    client = AsyncIOMotorClient(MONGO_URL, serverSelectionTimeoutMS=5000)
    name = f"autoschnell_r16_{uuid.uuid4().hex[:10]}"
    db = client[name]
    for mod in (deps, C, L):
        monkeypatch.setattr(mod, "db", db)
    try:
        yield SimpleNamespace(db=db, run=loop.run_until_complete)
    finally:
        try:
            loop.run_until_complete(client.drop_database(name))
        finally:
            client.close()
            loop.close()


CHEF = {"id": "chef", "dealer_id": "d1", "role": "dealer", "active": True}


def _vertrag(**extra):
    return {"id": "c1", "dealer_id": "d1", "user_id": "chef", "version": 1, "status": "erstellt",
            "contract_data": {"seller_name": "V"}, "send_status": [], "created_at": _jetzt(), **extra}


# ============================================================ Versand: Schluessel, Race, Nummer
def test_sendin_und_schluesselbindung():
    with pytest.raises(ValueError):
        C.SendIn(channel="email", recipient="a@b.de", message="x", methode="teilen")
    with pytest.raises(ValueError):
        C.SendIn(channel="fax", recipient="1", message="x")
    a = C.SendIn(channel="whatsapp", recipient="0170 1234567", message="Hallo")
    b = C.SendIn(channel="whatsapp", recipient="0170 7654321", message="Hallo")
    c = {"version": 1}
    assert C._versand_anfrage_hash(c, a) != C._versand_anfrage_hash(c, b)
    assert C._versand_anfrage_hash(c, a) != C._versand_anfrage_hash({"version": 2}, a)
    q = inspect.getsource(C.send_contract)
    assert '"anfrage_hash": anfrage_hash' in q and 'archiv["anfrage_hash"] != anfrage_hash' in q
    # Different-Key-Race: ein frischer laufender Versand desselben Kanals/Empfaengers sperrt
    assert '"send_status": {"$not": {"$elemMatch": {' in q and '"zustellung": "laeuft",' in q
    # Nummernpruefung und ehrlicher Status
    assert "WA_ZIFFERN_MIN <= len(digits) <= WA_ZIFFERN_MAX" in q
    assert 'out["zustellung"] = "link_bereit"' in q and '= "chat_geoeffnet"' not in q
    # Archiv erst nach Erfolg, Rollback entfernt den Eintrag
    assert q.index("await db.versand_schluessel.delete_one(") < q.index("await db.versand_schluessel.update_one(")
    # Versand-Limit und Fassung im Beleg
    assert "_versand_limiter.check(" in q and '"version": int(c.get("version") or 1), "anfrage_hash"' in q
    # E-Mail-Identitaet aus den eingefrorenen Kaeuferdaten
    assert "_apply_contract_overrides(\n                contract=dict(c.get(\"contract_data\") or {}), vehicle={}, dealer=dict(firma))" in q


def test_wa_nummer_regeln():
    assert C.wa_nummer("0170 1234567") == "491701234567"
    assert C.wa_nummer("+49 170 1234567") == "491701234567"
    assert len(C.wa_nummer("abc")) < C.WA_ZIFFERN_MIN and len(C.wa_nummer("12")) < C.WA_ZIFFERN_MIN


def test_freigabe_link_folgt_der_aktuellen_fassung_und_alte_links_laufen_weiter(wegwerf):
    db, run = wegwerf.db, wegwerf.run
    alt = {"token": "alt-token-xyz", "erstellt_am": _jetzt(-3600), "version": 1,
           "laeuft_ab": _jetzt(5 * 86400), "abrufe": 0}
    run(db.generated_pdfs.insert_one(_vertrag(version=2, freigabe=alt)))
    bereich = {"dealer_id": "d1"}
    link, bis = run(C._freigabe_link("c1", bereich, CHEF))
    doc = run(db.generated_pdfs.find_one({"id": "c1"}))
    assert doc["freigabe"]["version"] == 2 and doc["freigabe"]["token"] in link
    assert doc["freigabe"]["token"] != "alt-token-xyz"
    # der alte, noch laufende Link ist historisiert (bleibt abrufbar)
    assert [f["token"] for f in doc.get("freigabe_alt") or []] == ["alt-token-xyz"]
    # ein gueltiger Link derselben Fassung wird wiederverwendet
    link2, _ = run(C._freigabe_link("c1", bereich, CHEF))
    assert link2 == link
    q = inspect.getsource(C.public_vertrag_pdf)
    assert '{"freigabe_alt.token": token}' in q and 'c.get("freigabe_alt")' in q
    assert "for _ in range(4):" in inspect.getsource(C._freigabe_link)


def test_kundenfassung_cache_cas_und_neuerzeugung(wegwerf):
    db, run = wegwerf.db, wegwerf.run
    # Vertrag ohne digitale Fassung: Erzeugung + Cache
    run(db.dealers.insert_one({"id": "d1", "company_name": "Firma", "created_at": _jetzt()}))
    run(db.generated_pdfs.insert_one(_vertrag(vehicle_id="v1")))
    pdf = run(C._digitales_pdf_bytes(run(db.generated_pdfs.find_one({"id": "c1"}, {"_id": 0})), CHEF))
    assert pdf and pdf[:4] == b"%PDF"
    assert run(db.generated_pdfs.find_one({"id": "c1"}))["pdf_digital_b64"]
    # Version wechselt WAEHREND der Erzeugung: alte Fassung wird verworfen (None)
    alt_doc = run(db.generated_pdfs.find_one({"id": "c1"}, {"_id": 0}))
    alt_doc.pop("pdf_digital_b64", None)
    run(db.generated_pdfs.update_one({"id": "c1"}, {"$set": {"version": 2}, "$unset": {"pdf_digital_b64": ""}}))
    assert run(C._digitales_pdf_bytes(alt_doc, CHEF)) is None
    # Archivfassung: kein Cache-Schreiben
    assert run(C._digitales_pdf_bytes({**alt_doc, "version": 1}, CHEF, cache=False))[:4] == b"%PDF"
    assert "pdf_digital_b64" not in run(db.generated_pdfs.find_one({"id": "c1"}))
    # Neuerzeugung setzt "versendet" zurueck (Quelle) und der Beleg traegt die Fassung
    q = inspect.getsource(C.regenerate_contract_for_pickup)
    assert '"status": "neu erstellt"' in q
    # DuplicateKey-Race prueft den Hash
    assert inspect.getsource(C.create_contract).count('vorhanden["idempotency_hash"] != anfrage_hash') == 2
    # Versionsroute erzeugt die digitale Fassung ehrlich nach
    assert "cache=False" in inspect.getsource(C.get_contract_version_pdf)


def test_mail_zusammenfassung_liest_vertragsfelder():
    zeilen = dict(VM._zeilen({"contract_data": {"vehicle_first_registration": "05/2019",
                                                 "vehicle_mileage": "90000", "vehicle_vin": "WVW123"},
                              "pickup_date": "2026-09-20"}))
    assert zeilen["Erstzulassung"] == "05/2019" and "90" in zeilen["Kilometerstand"]
    assert zeilen["Fahrgestellnummer"] == "WVW123" and zeilen["Abholung"] == "20.09.2026"


# ============================================================ Sucher-Funktionen
def test_gesperrte_firmen_folgen_dem_hauptaccount(wegwerf):
    db, run = wegwerf.db, wegwerf.run
    run(db.dealers.insert_many([
        {"id": "dA", "user_id": "chefA2", "company_name": "A"},        # Chefwechsel: neuer Chef aktiv
        {"id": "dB", "user_id": "chefB", "company_name": "B"},         # Chef gesperrt
        {"id": "dC", "company_name": "C"},                             # ohne user_id: aeltestes dealer-Konto
    ]))
    run(db.users.insert_many([
        {"id": "chefA1", "dealer_id": "dA", "role": "dealer", "active": False, "created_at": "2026-01-01"},
        {"id": "chefA2", "dealer_id": "dA", "role": "dealer", "active": True, "created_at": "2026-02-01"},
        {"id": "chefB", "dealer_id": "dB", "role": "dealer", "active": False, "created_at": "2026-01-01"},
        {"id": "chefC", "dealer_id": "dC", "role": "dealer", "active": False, "created_at": "2026-01-01"},
    ]))
    assert run(deps.gesperrte_firmen_ids()) == {"dB", "dC"}, "A: eingetragener Chef aktiv -> nicht gesperrt"


def test_fahrzeugliste_meldet_kuerzung(wegwerf):
    db, run = wegwerf.db, wegwerf.run
    run(db.vehicles.insert_many([{"id": f"v{i}", "dealer_id": "d1", "owner_user_id": "chef",
                                  "updated_at": _jetzt(-i), "data": {}} for i in range(505)]))
    resp = Response()
    items = run(L.list_vehicles(CHEF, resp))
    assert len(items) == 500 and resp.headers.get("X-Truncated") == "1"
    assert inspect.getsource(L.live_counter).count("require_active_sub") == 1


def test_rueckfall_rueckbuchung_und_abruf_bremse(wegwerf, monkeypatch):
    db, run = wegwerf.db, wegwerf.run
    monkeypatch.setattr(L, "RUECKFALL_TAGESLIMIT", 2)
    user = {"id": "s1", "dealer_id": "d1"}
    assert run(L._rueckfall_erlaubt(True, user)) is True
    run(L._rueckfall_zurueck(user))
    tag = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    assert run(db.provider_budget.find_one({"_id": f"{tag}:rueckfall:s1"}))["n"] == 0
    run(L._rueckfall_zurueck(user))                     # nie unter null
    assert run(db.provider_budget.find_one({"_id": f"{tag}:rueckfall:s1"}))["n"] == 0
    # Konto-Bremse: hoechstens N gleichzeitig
    monkeypatch.setattr(L, "ABRUF_GLEICHZEITIG_JE_KONTO", 2)

    async def _lauf():
        s1, s2 = L._AbrufSlot(user), L._AbrufSlot(user)
        await s1.__aenter__()
        await s2.__aenter__()
        with pytest.raises(L.AbrufGebremst):
            await L._AbrufSlot(user).__aenter__()
        await s1.__aexit__(None, None, None)
        await s2.__aexit__(None, None, None)
        return (await db.abruf_slots.find_one({"_id": "s1"}))["n"]
    assert run(_lauf()) == 0
    # beide direkten Abrufpfade nutzen die Bremse, Rueckfall wird bei Fehlern zurueckgebucht
    q = inspect.getsource(L.compare)
    assert "async with _AbrufSlot(user):" in q and q.count("await _rueckfall_zurueck(user)") >= 4
    assert "async with _AbrufSlot(user):" in inspect.getsource(L.listings_resolve)
    assert '"inserat.aufgeloest"' in inspect.getsource(L.listings_resolve)
    assert "await _rueckfall_zurueck(user)" in inspect.getsource(L.listings_check)
