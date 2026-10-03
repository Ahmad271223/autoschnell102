# -*- coding: utf-8 -*-
"""Startpruefung 27.09.2026 (K3, K4): Empfangsbestaetigung im gedruckten
Kaufvertrag.

K3: Der Schalter "Empfangsbestaetigung drucken" (empfang_drucken) wurde beim
    Anlegen als TEXT eingefroren (aus False wurde "False"), und der Druck hielt
    jeden Text fuer "an" — der Block stand in jedem Vertrag, auch bei
    Einstellung "aus". Die Vorschau fror nichts ein und zeigte ihn NICHT:
    Vorschau und gespeicherter Vertrag wichen voneinander ab.
K4: Eine Schluesselanzahl (eingetippt oder aus dem Inserat) hakte "KFZ mit
    n Schluessel(n)" automatisch an, obwohl der Block im Dialog seit 24.09.
    nicht mehr abgefragt wird. Serverseitig sind die Kaestchen beim Anlegen
    jetzt immer leer.

Die Tests gehen den ganzen Anlegeweg (ContractIn -> _apply_contract_overrides
-> kaeufer_einfrieren -> generate_contract_pdf), ohne Datenbank und zusaetzlich
in-Prozess gegen eine Wegwerf-DB (Vorschau und Anlage im Vergleich).
"""
import asyncio
import io
import os
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path

import pytest

BACKEND = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND))

import pdf_service as P  # noqa: E402
from vertrag_felder import (EMPFANG_KAESTCHEN, _apply_contract_overrides,  # noqa: E402
                            als_wahrheitswert)

MONGO_URL = os.environ.get("MONGO_URL") or "mongodb://127.0.0.1:27017"
BLOCK = "bestätigt Empfang von"

FIRMA = {"company_name": "Empfang Test GmbH", "contact_person": "Chef E",
         "address": "Weg 1", "zip_code": "10115", "city": "Berlin",
         "phone": "030 1", "email": "chef@empfang.test"}
VEHICLE = {"make_label": "BMW", "model_label": "320d"}
# So schickt der Dialog den Vertrag (buildPayload: ...form) — mit den
# Empfangs-Kaestchen, wie sie ein alter Dialogstand automatisch setzte.
DIALOG = {"vehicle_id": "v_empfang", "seller_name": "Vera Verkauf",
          "seller_city": "Dresden", "purchase_price": 12500,
          "pickup_date": "2026-09-30", "empfang_datum": "2026-09-30",
          "empfang_ort_kaeufer": "Berlin", "empfang_ort_verkaeufer": "Dresden",
          "schluessel_anzahl": "2", "empfang_schluessel": True,
          "empfang_zulassungsbescheinigung": True, "empfang_kaufpreis": True}


def _text(pdf: bytes) -> str:
    from pypdf import PdfReader
    return " ".join("\n".join((p.extract_text() or "")
                              for p in PdfReader(io.BytesIO(pdf)).pages).split())


def _anlegeweg(firma: dict, payload: dict = DIALOG):
    """Wie create_contract ohne Datenbank: Modell -> Kaestchen leeren ->
    Overrides -> Einfrieren -> Druck-PDF."""
    from routes.contracts import ContractIn, empfang_kaestchen_leeren, kaeufer_einfrieren
    body = ContractIn.model_validate(payload)
    contract = body.model_dump(exclude={"zweiter_vertrag_bestaetigt", "ki_bewertung_id"})
    empfang_kaestchen_leeren(contract)
    vehicle, dealer = _apply_contract_overrides(contract=contract, vehicle=dict(VEHICLE),
                                                dealer=dict(firma))
    kaeufer_einfrieren(contract, dealer)
    pdf = P.generate_contract_pdf(dealer=dealer, vehicle=vehicle, contract=contract)
    return contract, pdf


# ---------------------------------------------------------------- ohne DB
@pytest.mark.parametrize("wert,erwartet", [
    (False, False), (True, True), (None, None), ("", None), ("  ", None),
    ("False", False), ("false", False), (" FALSE ", False), ("0", False),
    ("nein", False), ("Nein", False), ("aus", False), ("off", False),
    ("True", True), ("true", True), ("1", True), ("ja", True), ("an", True),
    ("vielleicht", None), (0, False), (1, True),
])
def test_01_als_wahrheitswert(wert, erwartet):
    assert als_wahrheitswert(wert) is erwartet


@pytest.mark.parametrize("text", ["False", "false", "0", "nein", "aus", " False "])
def test_02_gespeicherter_text_aus_gilt_als_aus(text):
    # Vertraege seit 24.09. tragen "False" als Text — die Firma hat den
    # Schalter inzwischen vielleicht wieder an; der Vertrag gewinnt.
    assert P.empfang_drucken({"empfang_drucken": text}, {"empfang_drucken": True}) is False
    assert P.empfang_drucken({"empfang_drucken": text}, {}) is False


@pytest.mark.parametrize("text", ["True", "true", "1", "ja", "an"])
def test_03_gespeicherter_text_an_gilt_als_an(text):
    assert P.empfang_drucken({"empfang_drucken": text}, {"empfang_drucken": False}) is True


def test_04_unbekannter_text_faellt_auf_die_firma_zurueck():
    assert P.empfang_drucken({"empfang_drucken": ""}, {"empfang_drucken": False}) is False
    assert P.empfang_drucken({"empfang_drucken": "???"}, {}) is True
    assert P.empfang_drucken({}, {}) is True


def test_05_anlegeweg_firma_aus_kein_block():
    """K3: Firmeneinstellung aus -> eingefroren als bool False, kein Block.
    Vor der Korrektur: eingefroren "False", Block im Druck-PDF."""
    contract, pdf = _anlegeweg(dict(FIRMA, empfang_drucken=False))
    assert contract["empfang_drucken"] is False, repr(contract["empfang_drucken"])
    text = _text(pdf)
    assert BLOCK not in text
    assert "Zulassungsbescheinigung Teil I & II" not in text
    assert "Datum und Ort: 30.09.2026, Berlin" in text
    assert "Datum und Ort: 30.09.2026, Dresden" in text


def test_06_anlegeweg_firma_an_und_ohne_einstellung_block():
    for firma in (dict(FIRMA, empfang_drucken=True), dict(FIRMA)):
        contract, pdf = _anlegeweg(firma)
        text = _text(pdf)
        assert BLOCK in text and "Zulassungsbescheinigung Teil I & II" in text
        assert "KFZ mit 2 Schlüssel(n)" in text, "die Anzahl steht weiter im Text"
    assert _anlegeweg(dict(FIRMA, empfang_drucken=True))[0]["empfang_drucken"] is True
    # Keine Einstellung: nichts eingefroren (None = an, wie bisher).
    assert "empfang_drucken" not in _anlegeweg(dict(FIRMA))[0]


def test_07_altvertrag_mit_text_false():
    """Vertrag vom 24.-27.09.: contract_data.empfang_drucken == "False".
    (a) Archiv-/Direktdruck aus contract_data, (b) neue Fassung ueber
    _apply_contract_overrides + kaeufer_einfrieren — die Firma hat den
    Schalter inzwischen wieder an; der eingefrorene Stand gewinnt."""
    from routes.contracts import kaeufer_einfrieren
    alt = dict(DIALOG, contract_no="KV-ALT", empfang_drucken="False",
               empfang_schluessel=False, empfang_zulassungsbescheinigung=False,
               empfang_kaufpreis=False)
    assert BLOCK not in _text(P.generate_contract_pdf(dealer=dict(FIRMA), vehicle=VEHICLE,
                                                      contract=dict(alt)))
    contract = dict(alt)
    vehicle, dealer = _apply_contract_overrides(
        contract=contract, vehicle=dict(VEHICLE), dealer=dict(FIRMA, empfang_drucken=True))
    assert dealer["empfang_drucken"] is False, "Wahrheitsfeld bleibt bool"
    kaeufer_einfrieren(contract, dealer)
    assert contract["empfang_drucken"] is False, "neue Fassung heilt den Text"
    assert BLOCK not in _text(P.generate_contract_pdf(dealer=dealer, vehicle=vehicle,
                                                      contract=contract))
    # Gegenstueck "True" als Text: Block bleibt.
    an = dict(alt, empfang_drucken="True")
    assert BLOCK in _text(P.generate_contract_pdf(dealer=dict(FIRMA, empfang_drucken=False),
                                                  vehicle=VEHICLE, contract=an))


def test_08_einfrieren_bool_bleibt_bool():
    from routes.contracts import kaeufer_einfrieren
    for wert in (False, True):
        c = kaeufer_einfrieren({}, dict(FIRMA, empfang_drucken=wert))
        assert c["empfang_drucken"] is wert
        assert c["dealer_company"] == FIRMA["company_name"], "Textfelder wie bisher"
    assert "empfang_drucken" not in kaeufer_einfrieren({}, dict(FIRMA, empfang_drucken=None))


def test_09_anlegeweg_kaestchen_leer(monkeypatch):
    """K4: Der Dialog schickte empfang_schluessel=True mit (Schluesselanzahl
    eingetippt oder aus dem Inserat) — der Druck kreuzte "KFZ mit 2
    Schluessel(n)" an. Jetzt sind alle Kaestchen beim Anlegen leer."""
    gezeichnet = []
    echt = P._angekreuzt

    def spion(wert):
        erg = echt(wert)
        gezeichnet.append(erg)
        return erg

    monkeypatch.setattr(P, "_angekreuzt", spion)
    contract, pdf = _anlegeweg(dict(FIRMA, empfang_drucken=True))
    for feld in EMPFANG_KAESTCHEN:
        assert contract[feld] is False, feld
    assert len(gezeichnet) == 3, gezeichnet      # Kaeufer 2 Kaestchen, Verkaeufer 1
    assert not any(gezeichnet), "kein Kaestchen angekreuzt"
    assert "KFZ mit 2 Schlüssel(n)" in _text(pdf)
    # Gegenprobe: ohne das Leeren waere genau das angekreuzt worden.
    gezeichnet.clear()
    from routes.contracts import ContractIn
    roh = ContractIn.model_validate(DIALOG).model_dump()
    P.generate_contract_pdf(dealer=dict(FIRMA), vehicle=VEHICLE, contract=roh)
    assert any(gezeichnet)


def test_10_migration_m7_speichert_bool():
    """m7 lief live laengst; auf einer frischen/wiederhergestellten DB darf
    sie aus False nicht "False" machen und ein eingefrorenes False nicht als
    fehlend ueberschreiben (fehlend-Pruefung ueber kaeufer_wert_fehlt)."""
    from vertrag_felder import kaeufer_wert, kaeufer_wert_fehlt
    assert kaeufer_wert_fehlt("empfang_drucken", None)
    assert kaeufer_wert_fehlt("empfang_drucken", "")
    assert not kaeufer_wert_fehlt("empfang_drucken", False)
    assert not kaeufer_wert_fehlt("empfang_drucken", "False")
    assert kaeufer_wert("empfang_drucken", "False") is False
    assert kaeufer_wert("dealer_city", "  Berlin ") == "Berlin"
    assert kaeufer_wert_fehlt("dealer_city", "   ")


# ---------------------------------------------------------------- Wegwerf-DB
def _jetzt():
    return datetime.now(timezone.utc).isoformat()


def _module(name):
    import importlib
    return importlib.import_module(name)


@pytest.fixture
def welt():
    from motor.motor_asyncio import AsyncIOMotorClient
    s = uuid.uuid4().hex[:10]
    names = ["deps", "routes.contracts", "migrationen", "kaufvorgang",
             "lifecycle", "auto_daten", "routes.appointments"]
    mods = [_module(n) for n in names]
    alt = [(m, getattr(m, "db", None)) for m in mods]

    class _W:
        pass

    w = _W()
    w.s = s
    w.dealer_id = f"d_emp_{s}"
    w.chef = {"id": f"chef_emp_{s}", "dealer_id": w.dealer_id, "role": "dealer"}
    w.loop = asyncio.new_event_loop()
    asyncio.set_event_loop(w.loop)
    w.client = AsyncIOMotorClient(MONGO_URL, serverSelectionTimeoutMS=5000)
    basis = os.environ.get("DB_NAME") or "autoschnell_spfix_empfang"
    w.db_name = f"{basis}_{s}"
    w.db = w.client[w.db_name]
    for m in mods:
        if hasattr(m, "db"):
            m.db = w.db
    w.run = lambda coro: w.loop.run_until_complete(coro)
    w.run(w.db.users.insert_one({**w.chef, "active": True, "created_at": _jetzt()}))
    w.run(w.db.dealers.insert_one({"id": w.dealer_id, "user_id": w.chef["id"],
                                   **FIRMA, "created_at": _jetzt()}))
    yield w
    try:
        w.run(w.client.drop_database(w.db_name))
    finally:
        for m, d in alt:
            if d is not None:
                m.db = d
        w.client.close()
        w.loop.close()


def _fahrzeug(w, vid):
    w.run(w.db.vehicles.insert_one({"id": vid, "dealer_id": w.dealer_id,
                                    "owner_user_id": w.chef["id"],
                                    "lifecycle": "verglichen", "status": "verglichen",
                                    "data": dict(VEHICLE), "created_at": _jetzt()}))


@pytest.mark.parametrize("einstellung", [False, True])
def test_11_vorschau_und_vertrag_zeigen_denselben_block(welt, einstellung):
    """Firmeneinstellung aus/an: Vorschau (/contracts/preview) und
    gespeicherter Vertrag (Druckfassung) zeigen denselben Block; die
    Empfangs-Kaestchen sind im gespeicherten Vertrag leer."""
    import base64
    C = _module("routes.contracts")
    w = welt
    w.run(w.db.dealers.update_one({"id": w.dealer_id},
                                  {"$set": {"empfang_drucken": einstellung}}))
    vid = f"v_emp_{einstellung}_{w.s}"
    _fahrzeug(w, vid)
    body = C.ContractIn.model_validate(dict(DIALOG, vehicle_id=vid))
    vorschau = w.run(C.preview_contract(body, w.chef, variante="druck"))
    vorschau_text = _text(vorschau.body)
    w.run(C.create_contract(body, w.chef))
    doc = w.run(w.db.generated_pdfs.find_one({"vehicle_id": vid}))
    daten = doc["contract_data"]
    assert daten["empfang_drucken"] is einstellung, repr(daten["empfang_drucken"])
    for feld in EMPFANG_KAESTCHEN:
        assert daten[feld] is False, feld
    vertrag_text = _text(base64.b64decode(doc["pdf_b64"]))
    assert (BLOCK in vorschau_text) is einstellung
    assert (BLOCK in vertrag_text) is einstellung, "Vertrag wie Vorschau"
    assert "Datum und Ort: 30.09.2026, Berlin" in vertrag_text


def test_12_migration_m7_friert_bool_ein(welt):
    """m7 auf einer frischen/wiederhergestellten DB: Firma "aus" -> False als
    bool (vorher "False"); ein schon eingefrorenes False bleibt, auch wenn
    die Firma den Schalter inzwischen an hat."""
    import migrationen
    w = welt
    w.run(w.db.dealers.update_one({"id": w.dealer_id}, {"$set": {"empfang_drucken": False}}))
    neu_id, alt_id = f"m7a_{w.s}", f"m7b_{w.s}"
    for cid, daten in ((neu_id, {}), (alt_id, {"empfang_drucken": False})):
        w.run(w.db.generated_pdfs.insert_one({
            "id": cid, "dealer_id": w.dealer_id, "user_id": w.chef["id"],
            "contract_data": {"seller_name": "V", "purchase_price": 1, **daten}}))
    w.run(migrationen.m7_kaeuferdaten_einfrieren(w.db))
    a = w.run(w.db.generated_pdfs.find_one({"id": neu_id}))["contract_data"]
    assert a["empfang_drucken"] is False, repr(a["empfang_drucken"])
    w.run(w.db.dealers.update_one({"id": w.dealer_id}, {"$set": {"empfang_drucken": True}}))
    w.run(w.db.generated_pdfs.update_one({"id": alt_id},
                                         {"$unset": {"contract_data.dealer_city": ""}}))
    w.run(migrationen.m7_kaeuferdaten_einfrieren(w.db))
    b = w.run(w.db.generated_pdfs.find_one({"id": alt_id}))["contract_data"]
    assert b["empfang_drucken"] is False, "eingefrorenes False ist nicht fehlend"
    assert b["dealer_city"] == FIRMA["city"]
