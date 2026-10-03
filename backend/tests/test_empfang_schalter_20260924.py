# -*- coding: utf-8 -*-
"""Wunsch Ahmad 24.09.2026: Der Block "Übergabe & Empfangsbestätigung" wird
beim Erstellen des Kaufvertrags nicht mehr abgefragt; ob er im gedruckten
Vertrag steht, schaltet der Chef in den Einstellungen (empfang_drucken)."""
import io
import sys
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND))
PROJEKT = BACKEND.parent

import pdf_service as P  # noqa: E402

DEALER = {"company_name": "Käufer GmbH", "address": "Weg 1", "zip_code": "10115",
          "city": "Berlin", "vertrags_kundennummer": "482913"}
VEHICLE = {"make_label": "BMW", "model_label": "320d"}
CONTRACT = {"contract_no": "KV-E-1", "seller_name": "Vera Verkauf", "purchase_price": 12500,
            "pickup_date": "2026-09-25", "empfang_datum": "2026-09-25",
            "empfang_ort_kaeufer": "Berlin", "empfang_ort_verkaeufer": "Dresden"}


def _text(pdf: bytes) -> str:
    from pypdf import PdfReader
    return " ".join("\n".join((p.extract_text() or "")
                              for p in PdfReader(io.BytesIO(pdf)).pages).split())


def _pdf(contract, dealer=DEALER):
    return P.generate_contract_pdf(dealer=dealer, vehicle=VEHICLE, contract=contract)


def test_01_standard_an_wie_bisher():
    text = _text(_pdf(CONTRACT))
    assert "bestätigt Empfang von" in text
    assert "Zulassungsbescheinigung Teil I & II" in text and "Kaufpreis" in text
    assert "25.09.2026, Berlin" in text and "25.09.2026, Dresden" in text


def test_02_firmeneinstellung_aus_laesst_nur_datum_und_ort():
    text = _text(_pdf(CONTRACT, dict(DEALER, empfang_drucken=False)))
    assert "bestätigt Empfang von" not in text
    assert "Zulassungsbescheinigung Teil I & II" not in text
    assert "Unterschriften" in text and "Unterschrift" in text
    assert "Datum und Ort: 25.09.2026, Berlin" in text
    assert "Datum und Ort: 25.09.2026, Dresden" in text


def test_03_stand_im_vertrag_gewinnt_ueber_die_firma():
    # Beim Erstellen eingefroren (KAEUFER_FELDER): spaetere Fassungen zeigen
    # dieselbe Wahl, auch wenn der Chef den Schalter inzwischen umlegt.
    from vertrag_felder import KAEUFER_FELDER
    assert KAEUFER_FELDER["empfang_drucken"] == "empfang_drucken"
    assert P.empfang_drucken({"empfang_drucken": False}, {"empfang_drucken": True}) is False
    assert P.empfang_drucken({"empfang_drucken": True}, {"empfang_drucken": False}) is True
    assert P.empfang_drucken({}, {"empfang_drucken": False}) is False
    assert P.empfang_drucken({}, {}) is True and P.empfang_drucken(None, None) is True
    text = _text(_pdf(dict(CONTRACT, empfang_drucken=False), DEALER))
    assert "bestätigt Empfang von" not in text


def test_04_einstellung_wird_gespeichert():
    from routes.dealer import DealerSettingsIn, _collect_settings_update
    assert _collect_settings_update(DealerSettingsIn(empfang_drucken=False))["empfang_drucken"] is False
    assert "empfang_drucken" not in _collect_settings_update(DealerSettingsIn())


def test_05_oberflaeche():
    dialog = (PROJEKT / "frontend" / "src" / "components" / "ContractDialog.jsx").read_text(encoding="utf-8")
    assert 'title="Übergabe & Empfangsbestätigung"' not in dialog
    assert "contract-empfang-" not in dialog, "der Block darf beim Erstellen nicht mehr erscheinen"
    einst = (PROJEKT / "frontend" / "src" / "pages" / "app" / "Einstellungen.jsx").read_text(encoding="utf-8")
    assert 'data-testid="set-empfang-drucken"' in einst
    assert "empfang_drucken: dealer.empfang_drucken !== false" in einst
