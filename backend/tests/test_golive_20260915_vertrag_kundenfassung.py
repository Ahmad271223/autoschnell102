# -*- coding: utf-8 -*-
"""15.09.2026 (Wunsch Ahmad):
- Kundenfassung des Kaufvertrags (digital, E-Mail/WhatsApp) ohne Abschnitt
  "Unterschriften" und ohne Empfangsbestaetigung (Schluessel erhalten,
  Kaufpreis bestaetigt) — beides nur in der Druckfassung fuer Fahrer,
  Sucher und Chef
- Scheckheftgepflegt: ja (lueckenlos) / nein / teilweise bis MM/JJJJ
- Vergleichsregel Leistung "min_ps": -X PS und aufwaerts, nach oben offen
"""
import os
import sys
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import pytest

BACKEND = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND))
sys.path.insert(0, str(BACKEND / "tests"))

import autoscout_service as AS  # noqa: E402
import mobile_service as MS  # noqa: E402
import regeln as R  # noqa: E402
from pdf_service import generate_contract_pdf  # noqa: E402
from routes.contracts import ContractIn  # noqa: E402

FIRMA = {"company_name": "Test Autohaus", "contact_person": "Chef", "address": "Weg 1",
         "zip_code": "30159", "city": "Hannover", "phone": "0511", "email": "x@y.de"}
FZ = {"make": "VW", "make_label": "Volkswagen", "model": "Passat", "model_label": "Passat",
      "first_registration": "05/2019", "mileage": 90000, "power_kw": 110, "power_ps": 150,
      "fuel": "DIESEL", "gearbox": "AUTOMATIC_GEAR", "doors": "FOUR_OR_FIVE", "displacement": 1968}
BASIS = {"seller_name": "Max Muster", "seller_address": "ul. Osiecka 1", "seller_zip": "00-000",
         "seller_city": "Warschau", "purchase_price": 12000, "contract_no": "KV-KF",
         "pickup_date": "2026-09-20", "payment_method": "Bar",
         "empfang_schluessel": True, "schluessel_anzahl": "2", "empfang_kaufpreis": True}


def _text(pdf: bytes) -> str:
    from pypdf import PdfReader
    import io
    return " ".join(" ".join((s.extract_text() or "").split()) for s in PdfReader(io.BytesIO(pdf)).pages)


def _q(url):
    return parse_qs(urlparse(url).query)


def test_kundenfassung_ohne_unterschrift_und_empfang():
    druck = _text(generate_contract_pdf(dealer=dict(FIRMA), vehicle={"make_label": "VW"}, contract=dict(BASIS)))
    kunde = _text(generate_contract_pdf(dealer=dict(FIRMA), vehicle={"make_label": "VW"}, contract=dict(BASIS),
                                        digital=True))
    # Druckfassung (Fahrer, Sucher, Chef): Unterschriften + Empfangsbestaetigung
    assert "Unterschriften" in druck and "bestätigt Empfang von" in druck
    assert "Schlüssel" in druck and "Kaufpreis" in druck and "Datum und Ort:" in druck
    # Kundenfassung: nichts davon, nur der Gueltigkeitssatz
    assert "bestätigt Empfang von" not in kunde and "Datum und Ort:" not in kunde
    assert "Unterschriften" not in kunde and "Schlüssel(n)" not in kunde
    assert "Dieser Vertrag ist ohne Unterschrift gültig." in kunde


def test_scheckheft_im_vertrag_und_modell():
    f = _text(generate_contract_pdf(dealer=dict(FIRMA), vehicle={"make_label": "VW"},
                                    contract=dict(BASIS, service_book="teilweise", service_book_until="05/2024")))
    assert "Scheckheftgepflegt" in f and "Teilweise, bis 05/2024" in f
    f = _text(generate_contract_pdf(dealer=dict(FIRMA), vehicle={"make_label": "VW"},
                                    contract=dict(BASIS, service_book="ja")))
    assert "Ja, lückenlos" in f
    f = _text(generate_contract_pdf(dealer=dict(FIRMA), vehicle={"make_label": "VW"},
                                    contract=dict(BASIS, service_book="nein")))
    assert "Scheckheftgepflegt Nein" in f or "Scheckheftgepflegt" in f and "Nein" in f
    # Modell: Auswahl normalisiert, Unsinn abgelehnt
    ok = ContractIn(vehicle_id="v1", seller_name="x", purchase_price=1, service_book="Teilweise ",
                    service_book_until="05/2024")
    assert ok.service_book == "teilweise" and ok.service_book_until == "05/2024"
    with pytest.raises(ValueError):
        ContractIn(vehicle_id="v1", seller_name="x", purchase_price=1, service_book="vielleicht")


def test_ps_regel_nur_nach_unten():
    regeln = R.regeln_validieren({"power": {"mode": "min_ps", "value": 2}})
    assert regeln["power"] == {"mode": "min_ps", "value": 2}
    with pytest.raises(R.RegelFehler):
        R.regeln_validieren({"power": {"mode": "max_ps", "value": 2}})
    # mobile.de: pw=MIN: (nach oben offen), MIN = 150 - 2 PS in kW
    q = _q(MS.build_search_url(dict(FZ), {"power": {"mode": "min_ps", "value": 2}}))
    assert q["pw"] == [f"{MS.ps_to_kw(148)}:"], q.get("pw")
    q5 = _q(MS.build_search_url(dict(FZ), {"power": {"mode": "min_ps", "value": 5}}))
    assert q5["pw"] == [f"{MS.ps_to_kw(145)}:"]
    # AutoScout24: nur powerfrom, kein powerto
    qa = _q(AS.build_search_url(dict(FZ), {"power": {"mode": "min_ps", "value": 2}}))
    assert qa["powerfrom"] == [str(AS.ps_to_kw(148))] and "powerto" not in qa
    assert qa["powertype"] == ["kw"]
    # bisherige Toleranz unveraendert (beide Grenzen)
    qt = _q(MS.build_search_url(dict(FZ), {"power": {"mode": "tolerance_ps", "value": 5}}))
    assert qt["pw"] == [f"{MS.ps_to_kw(145)}:{MS.ps_to_kw(155)}"]


def test_leere_felder_erscheinen_nicht_im_vertrag():
    """Wunsch Ahmad (16.09.2026): nicht ausgefuellte Punkte (z. B. Ansprechpartner,
    E-Mail, Bereifung) stehen gar nicht im Vertrag — statt einer Zeile mit Strich."""
    mit = _text(generate_contract_pdf(
        dealer={**FIRMA, "contact_person": "Anna Chef", "email": "chef@example.com"},
        vehicle={"make_label": "VW", "model_label": "Golf"},
        contract={**BASIS, "seller_email": "v@example.com", "tires": "Sommer"}))
    assert "Ansprechpartner" in mit and "Anna Chef" in mit and "Bereifung" in mit
    ohne = _text(generate_contract_pdf(
        dealer={**FIRMA, "contact_person": "", "email": ""},
        vehicle={"make_label": "VW", "model_label": "Golf"},
        contract={**BASIS, "seller_email": "", "tires": ""}))
    assert "Ansprechpartner" not in ohne and "Bereifung" not in ohne
    assert "chef@example.com" not in ohne and "v@example.com" not in ohne
    assert "Marke" in ohne and "VW" in ohne          # gefuellte Felder bleiben

