# -*- coding: utf-8 -*-
"""Wunsch Ahmad 03.10.2026: Farbe und Layout des Kaufvertrags.

- Die Farbe (bisher fest Rot) waehlt der Chef — dieselben Farbnamen wie die Farbe der App.
- Zweites Layout "formular" nach Ahmads Vorlage (Ueberschrift mittig, Abschnittstitel mit Linie,
  Felder auf gepunkteter Linie, heller Preisstreifen, Ausstattung als Fliesstext). Inhalt und
  Regeln sind in beiden Layouts gleich, nur die Gestaltung unterscheidet sich.
- Farbe und Layout werden beim Anlegen im Vertrag festgehalten (wie das Logo): spaetere Fassungen
  sehen nie anders aus, nur weil die Firma heute etwas anderes gewaehlt hat; Altvertraege bleiben rot/modern.
- Vorschau mit Musterdaten in den Einstellungen (nur Chef), ohne zu speichern.
"""
import asyncio
import inspect
import io
import os
import sys
import uuid
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi import HTTPException
from pydantic import ValidationError

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import deps  # noqa: E402
import pdf_service as P  # noqa: E402
import routes.contracts as C  # noqa: E402
import routes.dealer as D  # noqa: E402

MONGO_URL = os.environ.get("MONGO_URL") or "mongodb://127.0.0.1:27017"

FIRMA = {"company_name": "KFZ Müller GmbH", "address": "Hauptstraße 1", "zip_code": "12345", "city": "Berlin",
         "phone": "030 123", "email": "info@kfz-mueller.de"}
FAHRZEUG = {"make_label": "Volkswagen", "model_label": "Golf", "first_registration": "03/2016", "mileage": 112700,
            "features": ["ABS", "Navigationssystem", "Sitzheizung"]}
VERTRAG = {"seller_name": "Erika Mustermann", "seller_address": "Musterweg 5", "seller_zip": "10115",
           "seller_city": "Berlin", "purchase_price": 22500, "contract_no": "KV-DESIGN-1",
           "payment_method": "Echtzeitüberweisung", "pickup_date": "2026-12-14",
           "additional_terms": "• Erster Punkt.\n• Zweiter Punkt.", "hu_valid": "ja", "hu_until": "2027-06",
           "accident_free": "ja", "vehicle_description": "Zweite Hand.",
           "digital_vertragstext": P.DIGITAL_VERTRAGSTEXT_STANDARD}


def _text(pdf: bytes) -> str:
    from pypdf import PdfReader
    return " ".join(" ".join((p.extract_text() or "").split()) for p in PdfReader(io.BytesIO(pdf)).pages)


def _inhaltsstroeme(pdf: bytes) -> bytes:
    from pypdf import PdfReader
    return b"".join(p.get_contents().get_data() for p in PdfReader(io.BytesIO(pdf)).pages)


def _rgb(hexwert: str) -> bytes:
    from reportlab.lib import colors
    from reportlab.lib.rl_accel import fp_str
    c = colors.HexColor(hexwert)
    return fp_str(c.red, c.green, c.blue).encode()


def test_01_farbe_und_layout_normalisieren():
    assert P.vertrag_farbe_hex(None) == "#FF3B30" == P.vertrag_farbe_hex("standard") == P.vertrag_farbe_hex("quatsch")
    assert P.vertrag_farbe_hex(" Petrol ") == "#0F766E"
    assert set(P.VERTRAG_FARBEN) >= {"standard", "rot", "lila", "gruen", "blau", "schwarz", "orange", "petrol",
                                     "pink", "gold", "indigo"}
    from konfig import AKZENTFARBEN
    assert set(AKZENTFARBEN) <= set(P.VERTRAG_FARBEN), "jede Farbe der App gibt es auch fuer den Vertrag"
    assert P.vertrag_layout(None) == "modern" == P.vertrag_layout("xyz")
    assert P.vertrag_layout("FORMULAR") == "formular"


def test_02_die_farbe_steht_im_pdf():
    rot = _inhaltsstroeme(P.generate_contract_pdf(dealer=FIRMA, vehicle=FAHRZEUG, contract=VERTRAG))
    petrol = _inhaltsstroeme(P.generate_contract_pdf(dealer=FIRMA, vehicle=FAHRZEUG,
                                                     contract={**VERTRAG, "vertrag_farbe": "petrol"}))
    assert _rgb("#FF3B30") in rot and _rgb("#0F766E") not in rot
    assert _rgb("#0F766E") in petrol and _rgb("#FF3B30") not in petrol, "kein Rot mehr, wenn Petrol gewaehlt ist"
    # nach dem Bauen ist die Gestaltung wieder zurueckgesetzt (kein Durchsickern in den naechsten Vertrag)
    assert P._GESTALTUNG.get() is None


@pytest.mark.parametrize("digital", [False, True], ids=["druck", "digital"])
def test_03_formular_hat_denselben_inhalt(digital):
    modern = _text(P.generate_contract_pdf(dealer=FIRMA, vehicle=FAHRZEUG, contract=VERTRAG, digital=digital))
    formular = _text(P.generate_contract_pdf(dealer=FIRMA, vehicle=FAHRZEUG, digital=digital,
                                             contract={**VERTRAG, "vertrag_layout": "formular"}))
    # 06.10.2026: Abschnitt heisst nur noch "Kaufpreis" (steht direkt vor dem Kasten)
    assert "KAUFPREIS KAUFPREIS (VEREINBART)" in formular and "Kaufpreis KAUFPREIS (VEREINBART)" in modern, "Layout greift"
    assert "Konditionen" not in formular and "Konditionen" not in modern
    for stueck in ("KFZ-KAUFVERTRAG", "für ein gebrauchtes Kraftfahrzeug", "KV-DESIGN-1", "Erika Mustermann",
                   "Musterweg 5", "KFZ Müller GmbH", "22.500,00 EUR", "Echtzeitüberweisung", "Volkswagen",
                   "112.700 km", "Ja, gültig bis 06/2027", "Zweite Hand.", "Erster Punkt.", "Zweiter Punkt.",
                   "auch ohne Unterschrift gültig", "Gewährleistung"):
        assert stueck in modern, stueck
        assert stueck in formular, stueck
    for titel in ("Verkäufer", "Käufer", "Fahrzeugdaten", "2 · Zustand", "Besondere Vereinbarungen",
                  "Allgemeine Vertragsbedingungen"):
        assert titel.upper() in formular, titel
    assert "ABS, Navigationssystem, Sitzheizung" in formular, "Ausstattung als Fliesstext"
    if digital:
        assert "UNTERSCHRIFTEN" not in formular and "Unterschriften" not in modern
    else:
        assert "UNTERSCHRIFTEN" in formular and "bestätigt Empfang von" in formular


def test_04_formular_mwst_und_portal():
    t = _text(P.generate_contract_pdf(dealer=FIRMA, vehicle=FAHRZEUG,
                                      contract={**VERTRAG, "vertrag_layout": "formular", "show_vat": True}))
    assert "NETTO" in t and "18.907,56 EUR" in t and "3.592,44 EUR" in t
    portal = _text(P.generate_contract_pdf(dealer=FIRMA, vehicle=FAHRZEUG, portal=True,
                                           contract={**VERTRAG, "vertrag_layout": "formular"}))
    assert "UNTERSCHRIFTEN" in portal and "bestätigt Empfang von" not in portal


def test_05_altvertrag_bleibt_wie_er_war_auch_wenn_die_firma_umstellt():
    """Die Firmeneinstellung wirkt nur beim ANLEGEN — generate liest den Vertrag, nie die Firma."""
    firma_neu = {**FIRMA, "vertrag_layout": "formular", "vertrag_farbe": "petrol"}
    alt = P.generate_contract_pdf(dealer=firma_neu, vehicle=FAHRZEUG, contract=VERTRAG)
    assert "Kaufpreis KAUFPREIS (VEREINBART)" in _text(alt) and _rgb("#FF3B30") in _inhaltsstroeme(alt)
    # Vorschau-Ueberschreibung greift trotzdem
    vorschau = P.generate_contract_pdf(dealer=FIRMA, vehicle=FAHRZEUG, contract=VERTRAG, layout="formular",
                                       farbe="gruen")
    assert "KAUFPREIS KAUFPREIS (VEREINBART)" in _text(vorschau) and _rgb("#15803D") in _inhaltsstroeme(vorschau)


def test_06_beim_anlegen_festhalten():
    cd = C.vertrag_design_festhalten({}, {"vertrag_farbe": "Lila", "vertrag_layout": "formular"})
    assert cd == {"vertrag_farbe": "lila", "vertrag_layout": "formular"}
    assert C.vertrag_design_festhalten({}, {}) == {"vertrag_farbe": "standard", "vertrag_layout": "modern"}
    assert C.vertrag_design_festhalten({}, {"vertrag_farbe": "<b>", "vertrag_layout": "x"}) == \
        {"vertrag_farbe": "standard", "vertrag_layout": "modern"}
    # Anlegen und Vorschau des Vertragsdialogs halten fest; die neue Fassung uebernimmt den alten Stand
    assert "vertrag_design_festhalten(contract_dict, dealer)" in inspect.getsource(C.create_contract)
    assert "vertrag_design_festhalten(contract_dict, dealer)" in inspect.getsource(C.preview_contract)
    q = inspect.getsource(C.vertrag_nachtraeglich_aendern)
    assert "contract_dict = dict(cd_alt)" in q and "vertrag_design_festhalten" not in q


def test_07_einstellungen_pruefen_farbe_und_layout():
    ok = D.DealerSettingsIn(vertrag_farbe=" Petrol ", vertrag_layout="FORMULAR")
    assert D._collect_settings_update(ok) == {"vertrag_farbe": "petrol", "vertrag_layout": "formular"}
    with pytest.raises(ValidationError):
        D.DealerSettingsIn(vertrag_farbe="#123456")
    with pytest.raises(ValidationError):
        D.DealerSettingsIn(vertrag_layout="bunt")
    # firmenweit: kein persoenlicher Wert fuer Sucher
    assert "vertrag_farbe" not in deps.SUCHER_SETTINGS_FIELDS
    assert "vertrag_layout" not in deps.SUCHER_SETTINGS_FIELDS


@pytest.fixture
def welt(monkeypatch):
    from motor.motor_asyncio import AsyncIOMotorClient
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    client = AsyncIOMotorClient(MONGO_URL, serverSelectionTimeoutMS=5000)
    name = f"autoschnell_vd_{uuid.uuid4().hex[:10]}"
    db = client[name]
    for mod in (deps, D):
        monkeypatch.setattr(mod, "db", db)
    s = uuid.uuid4().hex[:8]
    chef = {"id": f"chef-{s}", "role": "dealer", "dealer_id": f"d-{s}", "active": True}
    sucher = {"id": f"such-{s}", "role": "sucher", "dealer_id": f"d-{s}", "active": True}
    loop.run_until_complete(db.users.insert_many([dict(chef), dict(sucher)]))
    loop.run_until_complete(db.dealers.insert_one({"id": f"d-{s}", "user_id": chef["id"], **FIRMA,
                                                   "vertrag_farbe": "indigo", "vertrag_layout": "formular"}))
    try:
        yield SimpleNamespace(db=db, run=loop.run_until_complete, chef=chef, sucher=sucher)
    finally:
        try:
            loop.run_until_complete(client.drop_database(name))
        finally:
            client.close()
            loop.close()


def test_08_vorschau_mit_musterdaten(welt):
    w = welt
    r = w.run(D.vertrag_vorschau(D.VertragVorschauIn(), w.chef))
    assert r.media_type == "application/pdf" and r.body.startswith(b"%PDF")
    t = _text(r.body)
    assert "MUSTER-0001" in t and "Max Mustermann" in t and "KFZ Müller GmbH" in t
    assert "KAUFPREIS KAUFPREIS (VEREINBART)" in t, "ohne Auswahl: gespeichertes Layout der Firma (formular)"
    assert _rgb("#4F46E5") in _inhaltsstroeme(r.body), "ohne Auswahl: gespeicherte Farbe der Firma (indigo)"
    # Auswahl vor dem Speichern
    r2 = w.run(D.vertrag_vorschau(D.VertragVorschauIn(layout="modern", farbe="gruen", variante="digital"), w.chef))
    t2 = _text(r2.body)
    assert "Kaufpreis KAUFPREIS (VEREINBART)" in t2 and "Unterschriften" not in t2
    assert _rgb("#15803D") in _inhaltsstroeme(r2.body)
    # nichts gespeichert
    assert w.run(w.db.generated_pdfs.count_documents({})) == 0
    # nur der Chef
    with pytest.raises(HTTPException) as e:
        w.run(D.vertrag_vorschau(D.VertragVorschauIn(), w.sucher))
    assert e.value.status_code == 403
    with pytest.raises(ValidationError):
        D.VertragVorschauIn(farbe="neon")


def test_09_frontend_und_server_kennen_dieselben_farben():
    import re
    js = (Path(__file__).resolve().parents[2] / "frontend" / "src" / "lib" / "vertragDesign.js").read_text(encoding="utf-8")
    paare = dict(re.findall(r'key:\s*"(\w+)",\s*label:\s*"[^"]*",\s*hex:\s*"(#[0-9A-Fa-f]{6})"', js))
    assert len(paare) == 10
    for key, hexwert in paare.items():
        assert P.VERTRAG_FARBEN[key].upper() == hexwert.upper(), key
    assert re.findall(r'key:\s*"(\w+)",\s*label:\s*"(?:Modern|Formular)"', js) == list(P.VERTRAG_LAYOUTS)
