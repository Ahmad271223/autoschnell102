# -*- coding: utf-8 -*-
"""Eigene Kundennummer für Verträge (Entscheidung Ahmad 22.09.2026,
Rollenprüfung RP-428).

Vorher stand als "Kundennummer" im Kaufvertrag die ANMELDENUMMER des Chefs
(dealers.kunden_nr) — jeder Verkäufer kannte damit die Login-Nummer, und die
Sucher-Nummern (<nr>-1, -2 …) ließen sich ableiten. Jetzt bekommt jede Firma
eine zufällige sechsstellige Vertrags-Kundennummer (kontenanlage, Migration
14); Platzhalter, Versand-Dialog, Abholauftrag und Einstellungen nutzen nur
noch sie.
"""
import asyncio
import io
import os
import sys
import uuid
from pathlib import Path

import pytest

BACKEND = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND))
PROJEKT = BACKEND.parent
MONGO_URL = os.environ.get("MONGO_URL") or "mongodb://127.0.0.1:27017"

import vertrag_platzhalter as P  # noqa: E402
from vertrag_felder import KAEUFER_FELDER  # noqa: E402

VERTRAG = {"seller_name": "Max Muster", "pickup_date": "2026-09-25",
           "purchase_price": 12500, "contract_no": "KV-1"}


def _pdf_text(pdf: bytes, seite=None) -> str:
    from pypdf import PdfReader
    pages = PdfReader(io.BytesIO(pdf)).pages
    if seite is not None:
        pages = [pages[seite]]
    return "\n".join((p.extract_text() or "") for p in pages)


# ------------------------------------------------------------ Platzhalter
def test_01_platzhalter_nimmt_nie_die_anmeldenummer():
    firma = {"company_name": "Autohaus", "kunden_nr": 10023, "vertrags_kundennummer": "482913"}
    assert P.werte(VERTRAG, firma)["{kundennummer}"] == "482913"
    # beim Vertrag eingefroren gewinnt (spaetere Aenderung der Firma egal)
    mit_stand = {**VERTRAG, "contract_data": {"vertrags_kundennummer": "111222"}}
    assert P.werte(mit_stand, firma)["{kundennummer}"] == "111222"
    # ohne Vertrags-Kundennummer: Luecke, NIE 10023
    ohne = {"company_name": "Autohaus", "kunden_nr": 10023}
    assert P.werte(VERTRAG, ohne)["{kundennummer}"] == ""
    assert P.ersetzen("Nr. {kundennummer}", VERTRAG, ohne) == "Nr. ____"
    assert "10023" not in P.ersetzen("{kundennummer}", VERTRAG, firma)
    assert "nicht die Anmeldenummer" in P.PLATZHALTER_HILFE["{kundennummer}"]


def test_02_wird_beim_vertrag_eingefroren():
    from routes.contracts import kaeufer_einfrieren
    assert KAEUFER_FELDER["vertrags_kundennummer"] == "vertrags_kundennummer"
    c = kaeufer_einfrieren({}, {"company_name": "A", "vertrags_kundennummer": "482913",
                                "kunden_nr": 10023})
    assert c["vertrags_kundennummer"] == "482913"
    assert "kunden_nr" not in c and "10023" not in str(c)


def test_03_abholauftrag_nennt_die_vertrags_kundennummer():
    import pickup_pdf_service as Pk
    pdf = Pk.build_pickup_pdf(
        appointment={"id": "a-vk", "pickup_date": "2099-01-01", "pickup_time": "10:00",
                     "seller_name": "Verkäufer V", "pickup_address": "Abholweg 9"},
        vehicle={"make_label": "BMW", "model_label": "320d"}, contract={},
        dealer={"company_name": "Käufer GmbH", "address": "Weg 1", "zip_code": "10115",
                "city": "Berlin", "kunden_nr": 10023, "vertrags_kundennummer": "482913"},
        driver={"display_name": "Fahrer"})
    seite1 = _pdf_text(pdf, 0)
    assert "Kundennummer (Vertrag): 482913" in seite1, seite1
    assert "10023" not in _pdf_text(pdf)


def test_04_oberflaeche_und_sucher_sicht():
    send = (PROJEKT / "frontend" / "src" / "components" / "SendDialog.jsx").read_text(encoding="utf-8")
    assert "cd.vertrags_kundennummer || d?.vertrags_kundennummer" in send
    assert "d?.kunden_nr" not in send, "der Versand-Dialog nimmt noch die Anmeldenummer"
    einst = (PROJEKT / "frontend" / "src" / "pages" / "app" / "Einstellungen.jsx").read_text(encoding="utf-8")
    assert 'data-testid="vertrags-kundennummer"' in einst
    from routes.dealer import _SUCHER_SICHT_ZUSATZ
    assert "vertrags_kundennummer" in _SUCHER_SICHT_ZUSATZ, "Sucher braucht sie fuer den Versand-Dialog"
    import migrationen as M
    assert (14, "vertrags_kundennummern", M.m14_vertrags_kundennummern) in M.MIGRATIONEN
    assert M.ZIEL_VERSION == 14


# ------------------------------------------------------------ mit Wegwerf-DB
@pytest.fixture
def wegwerf_db():
    from motor.motor_asyncio import AsyncIOMotorClient
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    client = AsyncIOMotorClient(MONGO_URL, serverSelectionTimeoutMS=5000)
    name = f"autoschnell_vk0922_{uuid.uuid4().hex[:10]}"
    db = client[name]
    try:
        yield db, loop.run_until_complete
    finally:
        loop.run_until_complete(client.drop_database(name))
        client.close()
        loop.close()
        asyncio.set_event_loop(None)


def test_05_nummer_ist_sechsstellig_und_meidet_vergebene(wegwerf_db):
    from kontenanlage import vertrags_kundennummer_ziehen
    db, run = wegwerf_db
    run(db.dealers.insert_many([{"id": "f1", "kunden_nr": 123456},
                                {"id": "f2", "vertrags_kundennummer": "654321"}]))
    gezogen = {run(vertrags_kundennummer_ziehen(db)) for _ in range(40)}
    for nr in gezogen:
        assert len(nr) == 6 and nr.isdigit() and nr[0] != "0", nr
    assert "123456" not in gezogen and "654321" not in gezogen
    assert len(gezogen) > 1, "zufaellig, nicht immer dieselbe"


def test_06_neue_firma_bekommt_sie_beim_anlegen(wegwerf_db):
    from kontenanlage import firma_einfuegen
    db, run = wegwerf_db
    doc = {"id": "f_neu", "company_name": "Neu GmbH"}
    run(firma_einfuegen(db, doc))
    gespeichert = run(db.dealers.find_one({"id": "f_neu"}, {"_id": 0}))
    assert gespeichert["vertrags_kundennummer"].isdigit() and len(gespeichert["vertrags_kundennummer"]) == 6
    assert gespeichert["vertrags_kundennummer"] != str(gespeichert["kunden_nr"])


def test_07_migration_vergibt_altbestand_einmalig(wegwerf_db):
    import migrationen as M
    db, run = wegwerf_db
    run(db.dealers.insert_many([
        {"id": "alt1", "kunden_nr": 10001},
        {"id": "alt2", "kunden_nr": 10002, "vertrags_kundennummer": ""},
        {"id": "hat", "kunden_nr": 10003, "vertrags_kundennummer": "777888"},
    ]))
    erg = run(M.m14_vertrags_kundennummern(db))
    assert erg == {"vertrags_kundennummern_vergeben": 2}
    firmen = {d["id"]: d for d in run(db.dealers.find({}, {"_id": 0}).to_list(10))}
    assert firmen["hat"]["vertrags_kundennummer"] == "777888", "vorhandene bleibt"
    nummern = {d["vertrags_kundennummer"] for d in firmen.values()}
    assert len(nummern) == 3 and all(len(n) == 6 and n.isdigit() for n in nummern)
    # idempotent
    assert run(M.m14_vertrags_kundennummern(db)) == {"vertrags_kundennummern_vergeben": 0}
