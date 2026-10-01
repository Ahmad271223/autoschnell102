# -*- coding: utf-8 -*-
"""Wunsch Ahmad 01.10.2026 (zweite Runde, Vertrags-PDF):

  * "Halter" und "Händler" verschwinden ueberall aus dem Kaufvertrag — Beschriftungen der Parteien,
    Unterschriftskaesten, Untertitel ("Ankauf durch Händler") und die Abschnitte im Vertragsdialog.
    Die Rechtsklausel "Der Käufer ist Händler im Sinne des § 14 BGB" bleibt (Inhalt, keine Beschriftung).
  * "Dieser Vertrag ist (rechtskräftig, verbindlich und auch) ohne Unterschrift gültig." steht in KEINEM
    Vertragstext mehr: nicht im Standard (pdf_service), nicht im Bestand (Migration 22 raeumt
    Firmen- und Sucher-Texte, nummeriert lueckenlos neu). Gespeicherte Vertraege bleiben (Archiv).
"""
import io
import re
from pathlib import Path

import migrationen as MI
import pdf_service as P

from test_kundenportal_20260929 import welt  # noqa: F401

FRONTEND = Path(__file__).resolve().parent.parent.parent / "frontend" / "src"
SATZ_LANG = "Dieser Vertrag ist rechtskräftig, verbindlich und auch ohne Unterschrift gültig."
SATZ_KURZ = "Dieser Vertrag ist ohne Unterschrift gültig."


def _text(pdf: bytes) -> str:
    from pypdf import PdfReader
    return " ".join(" ".join((p.extract_text() or "").split()) for p in PdfReader(io.BytesIO(pdf)).pages)


def test_01_standardtexte_ohne_den_satz_und_lueckenlos():
    for t in (P.DIGITAL_VERTRAGSTEXT_STANDARD, P.AGB_PUNKTE_START, P.VERTRAGSTEXT_START):
        assert "ohne Unterschrift" not in t
    absaetze = [a for a in P.VERTRAGSTEXT_START.split("\n\n") if a.strip()]
    nummern = [int(a.split(".")[0]) for a in absaetze if a[:1].isdigit()]
    assert nummern == list(range(1, 9)), nummern
    assert "§ 14 BGB" in P.VERTRAGSTEXT_START and "Gerichtsstand" in P.VERTRAGSTEXT_START


def test_02_bereinigung_von_texten():
    f = P.vertragstext_ohne_unterschriftssatz
    assert f("") == "" and f(None) == ""
    unveraendert = "1. Erste Regel.\n\n2. Zweite Regel."
    assert f(unveraendert) == unveraendert
    alt = ("Folgende Vertragsbedingungen werden beidseitig eingewilligt.\n\n1. A.\n\n2. B.\n\n3. C.\n\n"
           f"4. {SATZ_LANG}\n\n5. D.\n\n6. E.")
    assert f(alt) == "Folgende Vertragsbedingungen werden beidseitig eingewilligt.\n\n1. A.\n\n2. B.\n\n3. C.\n\n4. D.\n\n5. E."
    # kurze Form, andere Nummernart, Zeilenumbruch im Satz, Gross-/Kleinschreibung, mitten im Absatz
    assert f("1) X.\n\n2) Dieser Vertrag ist ohne\nUnterschrift gültig.\n\n3) Y.") == "1) X.\n\n2) Y."
    assert f("Regel. DIESER VERTRAG IST OHNE UNTERSCHRIFT GÜLTIG. Noch eine Regel.") == "Regel. Noch eine Regel."
    assert f(f"Abholung Montag. {SATZ_KURZ}") == "Abholung Montag."
    # der alte Standardtext (so steht er bei bestehenden Firmen) wird genau zum neuen Starttext
    alte_agb = re.sub(r"^(\d+)\.", lambda m: f"{int(m.group(1)) + 1}.", P.AGB_PUNKTE_START, flags=re.M)
    alt_standard = P.DIGITAL_VERTRAGSTEXT_STANDARD + f"\n\n4. {SATZ_LANG}\n\n" + alte_agb
    assert f(alt_standard) == P.VERTRAGSTEXT_START


def test_03_migration_bereinigt_firmen_und_sucher(welt):  # noqa: F811
    w = welt
    w.run(w.db.dealers.update_one({"id": w.dealer_id}, {"$set": {
        "digital_vertragstext": f"1. A.\n\n4. {SATZ_LANG}\n\n5. B.",
        "default_terms": "AGB ohne den Satz",
        "default_special_agreements": f"{SATZ_KURZ} Abholung Montag."}}))
    w.run(w.db.users.update_one({"id": w.sucher["id"]}, {"$set": {"settings_override": {
        "digital_vertragstext": f"1. X.\n\n2. {SATZ_KURZ}", "company_name": "Eigen"}}}))
    assert w.run(MI.m22_vertragstext_ohne_unterschriftssatz(w.db)) == {"dealers": 1, "users": 1}
    d = w.run(w.db.dealers.find_one({"id": w.dealer_id}, {"_id": 0}))
    assert d["digital_vertragstext"] == "1. A.\n\n2. B."
    assert d["default_terms"] == "AGB ohne den Satz" and d["default_special_agreements"] == "Abholung Montag."
    u = w.run(w.db.users.find_one({"id": w.sucher["id"]}, {"_id": 0, "settings_override": 1}))
    assert u["settings_override"] == {"digital_vertragstext": "1. X.", "company_name": "Eigen"}
    # idempotent, und im Register eingetragen
    assert w.run(MI.m22_vertragstext_ohne_unterschriftssatz(w.db)) == {"dealers": 0, "users": 0}
    assert any(nr == 22 and fn is MI.m22_vertragstext_ohne_unterschriftssatz for nr, _, fn in MI.MIGRATIONEN)


def test_04_pdf_und_dialog_ohne_halter_haendler_und_ohne_den_satz():
    cd = {"seller_name": "Erika Mustermann", "seller_address": "Musterweg 2", "seller_zip": "10115",
          "seller_city": "Berlin", "purchase_price": 12500.0, "vehicle_make": "VW", "vehicle_model": "Golf VII",
          "vehicle_mileage": "85000", "vehicle_first_registration": "05/2019", "vehicle_fuel": "Diesel",
          "contract_no": "KV-TEST", "digital_vertragstext": P.VERTRAGSTEXT_START, "previous_owners": "2"}
    dealer = {"company_name": "KFZ Müller GmbH", "address": "Hauptstraße 1", "zip_code": "12345", "city": "Berlin"}
    fahrzeug = {"make_label": "VW", "model_label": "Golf VII"}
    for digital in (False, True):
        t = _text(P.generate_contract_pdf(dealer=dealer, vehicle=fahrzeug, contract=cd, digital=digital))
        assert "KFZ-KAUFVERTRAG" in t and "Ankauf durch Händler" not in t
        for verboten in ("/ Halter", "(Halter)", "/ Händler", "(Händler)", "ohne Unterschrift",
                         "eigenhändige Unterschrift"):
            assert verboten not in t, (digital, verboten)
        assert "Verkäufer" in t and "Käufer" in t
        assert "§ 14 BGB" in t                                   # Rechtsklausel bleibt
    quelle = (FRONTEND / "components" / "ContractDialog.jsx").read_text(encoding="utf-8")
    assert 'title="Verkäufer"' in quelle and 'title="Käufer (du)"' in quelle
    assert "Verkäufer / Halter" not in quelle and "Händler — du" not in quelle
