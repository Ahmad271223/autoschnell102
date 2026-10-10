# -*- coding: utf-8 -*-
"""Wunsch Ahmad 01.10.2026 (zweite Runde, Vertrags-PDF):

  * "Halter" und "Händler" verschwinden ueberall aus dem Kaufvertrag — Beschriftungen der Parteien,
    Unterschriftskaesten, Untertitel ("Ankauf durch Händler") und die Abschnitte im Vertragsdialog.
    Die Rechtsklausel "Der Käufer ist Händler im Sinne des § 14 BGB" bleibt (Inhalt, keine Beschriftung).
  * Klarstellung Ahmad (abends): Punkt 4 des Standardtexts ("… auch ohne Unterschrift gültig.") BLEIBT,
    die gespeicherten Vertragsbedingungen der Firmen werden nicht angefasst. Weg ist nur die zusaetzliche
    Zeile "Dieser Vertrag ist ohne Unterschrift gültig." ganz unten in der Online-Fassung.
"""
import io
from pathlib import Path

import migrationen as MI
import pdf_service as P

FRONTEND = Path(__file__).resolve().parent.parent.parent / "frontend" / "src"
PUNKT_4 = "4. Dieser Vertrag ist rechtskräftig, verbindlich und auch ohne Unterschrift gültig."
ZEILE_UNTEN = "Dieser Vertrag ist ohne Unterschrift gültig."


def _text(pdf: bytes) -> str:
    from pypdf import PdfReader
    return " ".join(" ".join((p.extract_text() or "").split()) for p in PdfReader(io.BytesIO(pdf)).pages)


def test_01_standardtext_behaelt_punkt_4_und_keine_migration_dazu():
    assert PUNKT_4 in P.DIGITAL_VERTRAGSTEXT_STANDARD and PUNKT_4 in P.VERTRAGSTEXT_START
    absaetze = [a for a in P.VERTRAGSTEXT_START.split("\n\n") if a.strip()]
    nummern = [int(a.split(".")[0]) for a in absaetze if a[:1].isdigit()]
    assert nummern == list(range(1, 10)), nummern
    # keine Migration, die gespeicherte Vertragstexte umschreibt
    assert all("vertragstext" not in name for _, name, _ in MI.MIGRATIONEN if name != "vorlagen_texte")
    assert not hasattr(P, "vertragstext_ohne_unterschriftssatz")


def test_02_pdf_und_dialog_ohne_halter_haendler_nur_die_zeile_unten_weg():
    cd = {"seller_name": "Erika Mustermann", "seller_address": "Musterweg 2", "seller_zip": "10115",
          "seller_city": "Berlin", "purchase_price": 12500.0, "vehicle_make": "VW", "vehicle_model": "Golf VII",
          "vehicle_mileage": "85000", "vehicle_first_registration": "05/2019", "vehicle_fuel": "Diesel",
          "contract_no": "KV-TEST", "digital_vertragstext": P.VERTRAGSTEXT_START, "previous_owners": "2"}
    dealer = {"company_name": "KFZ Müller GmbH", "address": "Hauptstraße 1", "zip_code": "12345", "city": "Berlin"}
    fahrzeug = {"make_label": "VW", "model_label": "Golf VII"}
    for digital in (False, True):
        t = _text(P.generate_contract_pdf(dealer=dealer, vehicle=fahrzeug, contract=cd, digital=digital))
        assert "KFZ-KAUFVERTRAG" in t and "Ankauf durch Händler" not in t
        for verboten in ("/ Halter", "(Halter)", "/ Händler", "(Händler)", "eigenhändige Unterschrift", ZEILE_UNTEN):
            assert verboten not in t, (digital, verboten)
        assert "Verkäufer" in t and "Käufer" in t
        assert PUNKT_4 in t and "§ 14 BGB" in t                 # Vertragsbedingungen unveraendert
    quelle = (FRONTEND / "components" / "ContractDialog.jsx").read_text(encoding="utf-8")
    assert 'title="Verkäufer"' in quelle and 'title="Käufer (du)"' in quelle
    assert "Verkäufer / Halter" not in quelle and "Händler — du" not in quelle
