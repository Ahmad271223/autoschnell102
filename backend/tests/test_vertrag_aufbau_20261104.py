# -*- coding: utf-8 -*-
"""Wunsch Ahmad 04.11.2026 (Kaufvertrag): Fahrzeugdaten, Zustand und Schaeden in EINEM Punkt "1 · Fahrzeugdaten"
(Zustand und "Schäden / Beschädigungen" keine eigenen Abschnitte mehr, "Sonstige Schäden" mit darin); "Hersteller / Typ"
fasst Marke und Modell zusammen, die Modellbezeichnung entfaellt; beim Kaeufer "Name / Firma"; "Kaufpreis" ohne
"(vereinbart)"; die Kundennummer im Kopf; neuer Punkt "2 · Zusätzliche Vereinbarung" aus dem Feld "Sonstige
Vereinbarung"; Beschriftungen schwarz und fett statt hellgrau (Ausdruck)."""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import pdf_service as P  # noqa: E402
from test_technik_zustand_20261009 import _KOPF, _text  # noqa: E402

DEALER = {"company_name": "Autohaus Muster GmbH", "address": "Hauptstr. 1", "zip_code": "10115", "city": "Berlin",
          "phone": "030 123456", "vertrags_kundennummer": "K-10002"}
FZ = {"make_label": "BMW", "model_label": "530", "model_description": "530d Touring M Sport", "first_registration": "12/2010",
      "mileage": 381000, "fuel_label": "Diesel", "color": "Schwarz metallic"}
VERTRAG = {**_KOPF, "tires": "8-fach", "accident_free": "Nein", "nachlackierungsfrei": "Ja", "technik_zustand": "in Ordnung",
           "damages_text": "Delle – Tür vorne links\nKratzer – Stoßstange hinten", "vehicle_damage_note": "Steinschlag Frontscheibe",
           "sonstige_vereinbarung": "8-fach bereift, Winterräder auf Felgen."}


@pytest.mark.parametrize("layout", ["modern", "formular"])
def test_01_ein_punkt_fahrzeugdaten_mit_zustand_und_schaeden(layout):
    f = _text(P.generate_contract_pdf(dealer=DEALER, vehicle=FZ, contract={**VERTRAG, "vertrag_layout": layout}))
    oben = f.upper()
    assert "1 · FAHRZEUGDATEN" in oben
    assert "2 · ZUSTAND" not in oben and "SCHÄDEN / BESCHÄDIGUNGEN" not in oben, "keine eigenen Punkte mehr"
    # alles im Punkt 1: Fahrzeug, Zustand, Schaeden, sonstige Schaeden — vor der Zusaetzlichen Vereinbarung
    i1, i2 = oben.index("1 · FAHRZEUGDATEN"), oben.index("2 · ZUSÄTZLICHE VEREINBARUNG")
    for teil in ("Hersteller / Typ BMW 530", "Bereifung 8-fach", "Unfallfrei Nein", "Nachlackierungsfrei Ja",
                 "Motor/Getriebe/Kupplung alles in Ordnung",
                 "Schäden Delle – Tür vorne links; Kratzer – Stoßstange hinten",
                 "Sonstige Schäden Steinschlag Frontscheibe"):
        assert teil in f, teil
        assert i1 < f.index(teil) < i2, teil
    assert "8-fach bereift, Winterräder auf Felgen." in f[f.index("Zusätzliche Vereinbarung".upper()) if layout == "formular"
                                                         else f.index("Zusätzliche Vereinbarung"):]


@pytest.mark.parametrize("layout", ["modern", "formular"])
def test_02_hersteller_typ_kaeufer_kaufpreis_kundennummer(layout):
    f = _text(P.generate_contract_pdf(dealer=DEALER, vehicle=FZ, contract={**VERTRAG, "vertrag_layout": layout}))
    assert "Hersteller / Typ BMW 530" in f
    assert "Modellbezeichnung" not in f and "530d Touring M Sport" not in f
    assert "Marke BMW" not in f and "Modell 530" not in f
    # Kaeufer wie Verkaeufer "Name / Firma" (der Firmenname steht nur im Kaeufer-Kasten mit dieser Beschriftung)
    assert "Name / Firma Autohaus Muster GmbH" in f
    assert "KAUFPREIS" in f and "(VEREINBART)" not in f.upper()
    assert "KUNDEN-NR." in f and "K-10002" in f
    # ohne Kundennummer kein leerer Eintrag
    ohne = _text(P.generate_contract_pdf(dealer={"company_name": "Firma"}, vehicle=FZ, contract=_KOPF))
    assert "KUNDEN-NR." not in ohne


def test_03_ohne_angaben_keine_leeren_zeilen():
    f = _text(P.generate_contract_pdf(dealer={"company_name": "Firma"}, vehicle={"make_label": "VW", "model_label": "Golf"},
                                      contract=_KOPF))
    assert "Hersteller / Typ VW Golf" in f
    assert "Zusätzliche Vereinbarung".upper() not in f.upper(), "ohne Text kein Punkt 2"
    assert "Sonstige Schäden" not in f and "Schäden " not in f.replace("Schäden /", "")


def test_04_hilfsfunktionen():
    assert P.hersteller_typ({"make_label": "BMW", "model_label": "530"}) == "BMW 530"
    assert P.hersteller_typ({"make_label": "BMW", "model_label": "BMW 530d"}) == "BMW 530d"
    assert P.hersteller_typ({"make": "VW"}) == "VW"
    assert P.hersteller_typ({}) == ""
    assert P.schaden_zeilen({"damages_text": "Delle – Tür\n\nKratzer – Heck"}) == ["Delle – Tür", "Kratzer – Heck"]
    assert P.schaden_zeilen({"damages": [{"type_label": "Delle", "zone": "Tür"}, "Rost am Schweller", None, {}]}) == \
        ["Delle: Tür", "Rost am Schweller", "Schaden"]
    assert P.schaden_zeilen({}) == []


def test_05_schrift_schwarz_und_fett():
    st = P._styles()
    for name in ("label", "f_label", "small", "meta_label", "sig_label", "subtitle", "value", "body"):
        assert st[name].textColor == P.SCHRIFT, name
    import inspect
    assert 'Paragraph(f"<b>{label}</b>"' in inspect.getsource(P._kv_compact)


def test_06_feld_sonstige_vereinbarung_im_eingabemodell():
    from routes.contracts import ContractIn
    c = ContractIn(**_KOPF, sonstige_vereinbarung="8-fach bereift. " * 60)
    assert c.sonstige_vereinbarung.startswith("8-fach bereift.")
    assert ContractIn(**_KOPF).sonstige_vereinbarung == ""
    with pytest.raises(Exception):
        ContractIn(**_KOPF, sonstige_vereinbarung="x" * 3001)
