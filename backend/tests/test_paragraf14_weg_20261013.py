# -*- coding: utf-8 -*-
"""Wunsch Ahmad 13.10.2026: "Der Käufer ist Händler im Sinne des § 14 BGB" gehoerte nie in den Kaufvertrag.
Raus aus dem Gewaehrleistungs-Absatz, aus dem Startertext (Punkt 6, Nummern nachgezogen), aus den gespeicherten
Texten (Migration 23) und beim Erzeugen jedes PDFs aus dem festgehaltenen Vertragstext (Altvertraege)."""
import io
import sys
from pathlib import Path

import pytest
from pypdf import PdfReader

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import migrationen as MI  # noqa: E402
import pdf_service as P  # noqa: E402

ALT_PUNKT_6 = ("6. Der Käufer ist Händler im Sinne des § 14 BGB. Der Erwerb erfolgt zum "
               "Zwecke des gewerblichen Wiederverkaufs.")
ALTER_STARTERTEXT = P.DIGITAL_VERTRAGSTEXT_STANDARD + "\n\n" + (
    "5. Das Fahrzeug wird unter Ausschluss jeglicher Sachmängelhaftung verkauft, "
    "soweit gesetzlich zulässig (§ 444 BGB bleibt unberührt).\n\n"
    + ALT_PUNKT_6 + "\n\n"
    "7. Eigentumsübergang erfolgt erst nach vollständigem Zahlungseingang.\n\n"
    "8. Mündliche Nebenabreden bestehen nicht. Änderungen oder Ergänzungen "
    "bedürfen der Schriftform.\n\n"
    "9. Erfüllungsort und Gerichtsstand ist der Sitz des Käufers, soweit "
    "gesetzlich zulässig."
)


def _text(pdf: bytes) -> str:
    return " ".join(" ".join((p.extract_text() or "").split()) for p in PdfReader(io.BytesIO(pdf)).pages)


def test_01_startertext_ohne_paragraf_14_und_nummern_lueckenlos():
    assert "§ 14" not in P.AGB_PUNKTE_START and "§ 14" not in P.VERTRAGSTEXT_START
    assert "Wiederverkauf" not in P.VERTRAGSTEXT_START
    absaetze = [a for a in P.VERTRAGSTEXT_START.split("\n\n") if a.strip()]
    assert [int(a.split(".")[0]) for a in absaetze if a[:1].isdigit()] == list(range(1, 9))
    assert "6. Eigentumsübergang" in P.VERTRAGSTEXT_START and "8. Erfüllungsort" in P.VERTRAGSTEXT_START


def test_02_filter_entfernt_nur_den_satz():
    # der alte Startertext wird genau zum neuen
    assert P.vertragstext_ohne_paragraf_14(ALTER_STARTERTEXT) == P.VERTRAGSTEXT_START
    # Texte ohne den Satz bleiben unveraendert (auch Leerzeichen/Zeilen)
    for t in ("", "Hallo\n\n  2. Eigene Regel.\n\n", P.VERTRAGSTEXT_START, None):
        assert P.vertragstext_ohne_paragraf_14(t) == (t or "")
    # eigene Nummern (nicht lueckenlos) bleiben, nur der Absatz faellt weg
    eigen = "1. Erste Regel.\n\n6. Der Käufer ist Händler im Sinne des § 14 BGB.\n\n9. Letzte Regel."
    assert P.vertragstext_ohne_paragraf_14(eigen) == "1. Erste Regel.\n\n9. Letzte Regel."
    # der Satz mitten in einem laengeren Absatz: nur der Satz geht, der Rest bleibt
    mitten = "Gewährleistung ausgeschlossen. Der Käufer ist Händler im Sinne des §14 BGB. Zahlung bar."
    assert P.vertragstext_ohne_paragraf_14(mitten) == "Gewährleistung ausgeschlossen. Zahlung bar."
    # idempotent
    assert P.vertragstext_ohne_paragraf_14(P.vertragstext_ohne_paragraf_14(ALTER_STARTERTEXT)) == P.VERTRAGSTEXT_START


def test_03_firmentext_wird_beim_lesen_gefiltert():
    assert P.digitaler_vertragstext({"digital_vertragstext": ALTER_STARTERTEXT}) == P.VERTRAGSTEXT_START
    assert P.digitaler_vertragstext({}) == P.DIGITAL_VERTRAGSTEXT_STANDARD


@pytest.mark.parametrize("digital", [False, True])
def test_04_pdf_nie_mit_paragraf_14_auch_altvertrag(digital):
    cd = {"vehicle_id": "v1", "seller_name": "Erika Mustermann", "seller_phone": "0170 1", "purchase_price": 12500.0,
          "contract_no": "KV-P14", "digital_vertragstext": ALTER_STARTERTEXT,
          "agb_text": "Eigene AGB.\n\nDer Käufer ist Händler im Sinne des § 14 BGB."}
    dealer = {"company_name": "KFZ Müller GmbH", "address": "Hauptstraße 1", "zip_code": "12345", "city": "Berlin"}
    t = _text(P.generate_contract_pdf(dealer=dealer, vehicle={"make_label": "VW", "model_label": "Golf"},
                                      contract=cd, digital=digital))
    assert "§ 14" not in t and "§14" not in t and "Wiederverkauf" not in t
    assert "Gewährleistung: Das Fahrzeug wird unter Ausschluss jeglicher Gewährleistung verkauft" in t
    assert "Eigenschaftszusicherungen siehe oben." in t
    assert "6. Eigentumsübergang" in t and "8. Erfüllungsort" in t and "9. " not in t
    assert "Eigene AGB." in t


def test_05_migration_registriert():
    assert (23, "vertragstext_ohne_14bgb", MI.m23_vertragstext_ohne_14bgb) in MI.MIGRATIONEN
    assert "vertragstext_ohne_paragraf_14" in MI.m23_vertragstext_ohne_14bgb.__code__.co_names \
        or "ohne" in MI.m23_vertragstext_ohne_14bgb.__code__.co_varnames
