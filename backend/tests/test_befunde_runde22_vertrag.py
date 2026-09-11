# -*- coding: utf-8 -*-
"""Runde 22 (11.09.2026, Vorlage Ahmad): Kaufvertrag — Empfangsbestaetigung,
Zulassungsstatus, Zahlungsart.

  - Abschnitt "Unterschriften": je Partei ein Kasten "bestätigt Empfang von:"
    mit echten Ankreuz-Kaestchen (Kaeufer: Zulassungsbescheinigung Teil I & II,
    KFZ mit n Schlüssel(n); Verkaeufer: Kaufpreis) und "Datum und Ort"
  - Zusicherungen: Zeile "Zulassung" (Angemeldet | Abgemeldet | —)
  - Zahlungsart Echtzeitüberweisung erscheint mit Umlaut im Kaufpreis-Kasten
  - ContractIn prueft die neuen Felder

Ohne Server: generate_contract_pdf direkt (Text per pypdf), ContractIn direkt
(routes.contracts laesst sich in-Prozess importieren, wie in Runde 14/17).
"""
import io
import sys
from pathlib import Path

import pytest
from pydantic import ValidationError

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from pypdf import PdfReader  # noqa: E402
from pypdf.generic import ContentStream  # noqa: E402

import pdf_service  # noqa: E402
from pdf_service import (  # noqa: E402
    DIGITAL_NACHTRAEGLICH, DIGITAL_VERTRAGSTEXT_STANDARD, generate_contract_pdf)

_DEALER = {"company_name": "Runde22 Autohaus GmbH"}
_VEHICLE = {"make_label": "VW", "model_label": "Golf"}
_BASIS = {"seller_name": "Max Muster", "purchase_price": 1000,
          "contract_no": "KV-R22", "digital_vertragstext": DIGITAL_VERTRAGSTEXT_STANDARD}
_EMPFANG = {
    "zulassung": "abgemeldet",
    "payment_method": "Echtzeitüberweisung",
    "empfang_zulassungsbescheinigung": True,
    "empfang_schluessel": True,
    "schluessel_anzahl": "2",
    "empfang_kaufpreis": True,
    "empfang_datum": "2026-08-18",
    "empfang_ort_kaeufer": "Rensenheim",
    "empfang_ort_verkaeufer": "Grethem",
}
_KAESTCHEN = ("empfang_zulassungsbescheinigung", "empfang_schluessel", "empfang_kaufpreis")


def _pdf(contract, digital=False):
    return generate_contract_pdf(dealer=_DEALER, vehicle=_VEHICLE,
                                 contract=contract, digital=digital)


def _text(pdf_bytes: bytes) -> str:
    return "\n".join((p.extract_text() or "")
                     for p in PdfReader(io.BytesIO(pdf_bytes)).pages)


def _flach(s: str) -> str:
    """pypdf bricht Zeilen — fuer Textvergleiche Whitespace normalisieren."""
    return " ".join((s or "").split())


def _linien_ops(pdf_bytes: bytes) -> int:
    """Anzahl der Linien-Operatoren ('l') in allen Seiteninhalten — ein Haken
    im Kaestchen besteht aus genau zwei Strichen, ein leeres Kaestchen ist ein
    Rechteck ('re') ohne Striche."""
    reader = PdfReader(io.BytesIO(pdf_bytes))
    n = 0
    for page in reader.pages:
        inhalt = page.get_contents()
        ops = (inhalt.operations if hasattr(inhalt, "operations")
               else ContentStream(inhalt, reader).operations)
        n += sum(1 for _, op in ops if op == b"l")
    return n


def _rechtecke(pdf_bytes: bytes) -> int:
    reader = PdfReader(io.BytesIO(pdf_bytes))
    n = 0
    for page in reader.pages:
        inhalt = page.get_contents()
        ops = (inhalt.operations if hasattr(inhalt, "operations")
               else ContentStream(inhalt, reader).operations)
        n += sum(1 for _, op in ops if op == b"re")
    return n


def _kaestchen_rechtecke(pdf_bytes: bytes) -> int:
    """Nur die Ankreuz-Kaestchen: Rechtecke '0 0 8 8 re' (die Flowable
    zeichnet im eigenen Koordinatensystem ab 0,0 mit Kantenlaenge 8)."""
    reader = PdfReader(io.BytesIO(pdf_bytes))
    n = 0
    for page in reader.pages:
        inhalt = page.get_contents()
        ops = (inhalt.operations if hasattr(inhalt, "operations")
               else ContentStream(inhalt, reader).operations)
        n += sum(1 for args, op in ops
                 if op == b"re" and [float(a) for a in args] == [0.0, 0.0, 8.0, 8.0])
    return n


# ---------------------------------------------------------------------------
# PDF: beide Fassungen
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("digital", [False, True])
def test_01_empfangsbestaetigung_in_beiden_fassungen(digital):
    f = _flach(_text(_pdf(dict(_BASIS, **_EMPFANG), digital=digital)))
    assert "bestätigt Empfang von" in f
    assert "Zulassungsbescheinigung Teil I & II" in f
    assert "KFZ mit 2 Schlüssel(n)" in f
    assert "Kaufpreis" in f
    assert "Datum und Ort: 18.08.2026, Rensenheim" in f
    assert "Datum und Ort: 18.08.2026, Grethem" in f
    assert "Zulassung" in f and "Abgemeldet" in f
    assert "Echtzeitüberweisung" in f            # Umlaut korrekt
    assert "Verkäufer / Halter" in f and "Käufer / Händler" in f


def test_02_druckfassung_behaelt_linien_digital_ohne():
    druck = _flach(_text(_pdf(dict(_BASIS, **_EMPFANG))))
    digital = _flach(_text(_pdf(dict(_BASIS, **_EMPFANG), digital=True)))
    # Beschluss 11.09.2026: je Partei nur noch "Datum und Ort" (Vorlage
    # Ahmad) — die fruehere zweite Linie "Ort, Datum" ist weg. Marker der
    # Druckfassung ist "Mit ihrer Unterschrift".
    assert "Ort, Datum" not in druck and "Unterschrift" in druck
    assert druck.count("Datum und Ort:") == 2
    assert "Mit ihrer Unterschrift" in druck
    assert "Ort, Datum" not in digital and "Mit ihrer Unterschrift" not in digital
    assert "Dieser Vertrag ist ohne Unterschrift gültig." in digital
    # digital: Empfang steht VOR dem Gueltigkeitssatz
    assert digital.index("bestätigt Empfang von") < digital.index(
        "Dieser Vertrag ist ohne Unterschrift gültig.")


def test_03_zulassung_angemeldet_und_leer():
    f = _flach(_text(_pdf(dict(_BASIS, zulassung="angemeldet"))))
    assert "Angemeldet" in f and "Abgemeldet" not in f
    f = _flach(_text(_pdf(dict(_BASIS))))
    assert "Zulassung" in f and "Angemeldet" not in f and "Abgemeldet" not in f
    # Unbekannter Wert (z. B. Altdaten) wird nicht gedruckt
    f = _flach(_text(_pdf(dict(_BASIS, zulassung="vielleicht"))))
    assert "vielleicht" not in f


@pytest.mark.parametrize("digital", [False, True])
def test_04_altvertrag_ohne_felder(digital):
    """Altvertrag: keine Felder -> PDF entsteht, leere Kaestchen, Linie."""
    alt = {"seller_name": "Alt V", "purchase_price": 500, "contract_no": "KV-ALT"}
    if digital:
        alt["digital_vertragstext"] = DIGITAL_NACHTRAEGLICH
    pdf = _pdf(alt, digital=digital)
    assert pdf[:4] == b"%PDF"
    f = _flach(_text(pdf))
    assert "KFZ mit ____ Schlüssel(n)" in f
    assert "bestätigt Empfang von" in f
    assert "Datum und Ort: ____" in f            # Linie zum Ausfuellen
    assert "18.08.2026" not in f                 # nichts erfunden
    assert "Bar / Überweisung" in f              # Standard-Zahlungsart alter Vertraege


def test_05_nur_datum_oder_nur_ort():
    """Runde 22 (Gegenpruefung): fehlt nur ein Teil, steht dort eine Linie
    zum Ausfuellen von Hand — nicht einfach nichts."""
    linie_datum, linie_ort, linie_voll = "_" * 10, "_" * 14, "_" * 26
    # nur Datum (Formular fuellt es immer vor) -> Linie fuer den Ort
    f = _flach(_text(_pdf(dict(_BASIS, empfang_datum="2026-08-18"))))
    assert f.count(f"Datum und Ort: 18.08.2026, {linie_ort}") == 2
    # nur Ort -> Linie fuer das Datum; die andere Seite: durchgehende Linie
    f = _flach(_text(_pdf(dict(_BASIS, empfang_ort_kaeufer="Rensenheim"))))
    assert f"Datum und Ort: {linie_datum}, Rensenheim" in f
    assert f"Datum und Ort: {linie_voll}" in f
    # typischer Fall: Datum + Kaeufer-Ort, Inserat ohne Verkaeufer-Ort
    f = _flach(_text(_pdf(dict(_BASIS, empfang_datum="2026-08-18",
                               empfang_ort_kaeufer="Rensenheim"))))
    assert "Datum und Ort: 18.08.2026, Rensenheim" in f
    assert f"Datum und Ort: 18.08.2026, {linie_ort}" in f
    assert f.count("Datum und Ort: 18.08.2026") == 2
    # beides da -> keine Linie im Empfangsblock
    f = _flach(_text(_pdf(dict(_BASIS, **_EMPFANG))))
    assert "Datum und Ort: 18.08.2026, Grethem" in f
    assert "Datum und Ort: _" not in f and f"18.08.2026, {linie_ort}" not in f


@pytest.mark.parametrize("digital", [False, True])
def test_06_angekreuzt_unterscheidet_sich_im_seiteninhalt(digital, monkeypatch):
    """Echte Kaestchen: drei Rechtecke je Fassung; jeder Haken sind genau zwei
    zusaetzliche Striche — sonst ist der Seiteninhalt identisch."""
    leer = dict(_BASIS, **{**_EMPFANG, **{k: False for k in _KAESTCHEN}})
    basis_l = _linien_ops(_pdf(leer, digital=digital))
    basis_re = _rechtecke(_pdf(leer, digital=digital))
    for feld in _KAESTCHEN:
        pdf = _pdf(dict(leer, **{feld: True}), digital=digital)
        assert _linien_ops(pdf) - basis_l == 2, feld
        assert _rechtecke(pdf) == basis_re, feld
        assert _kaestchen_rechtecke(pdf) == 3, feld
    alle = _pdf(dict(_BASIS, **_EMPFANG), digital=digital)
    assert _linien_ops(alle) - basis_l == 6
    assert _kaestchen_rechtecke(alle) == 3
    # Runde 22 (Gegenpruefung): die drei Kaestchen werden auch dann
    # gezeichnet, wenn nichts angekreuzt ist — gezaehlt, nicht nur verglichen.
    ohne = dict(_BASIS)
    for vertrag in (leer, ohne):
        pdf = _pdf(vertrag, digital=digital)
        assert _kaestchen_rechtecke(pdf) == 3
        assert _rechtecke(pdf) == basis_re
        assert _linien_ops(pdf) == basis_l
    # Gegenprobe: zeichnet die Flowable nichts, fehlen genau diese drei
    # Rechtecke (die Zaehlung oben misst also wirklich die Kaestchen).
    monkeypatch.setattr(pdf_service._Kaestchen, "draw", lambda self: None)
    ohne_draw = _pdf(leer, digital=digital)
    assert basis_re - _rechtecke(ohne_draw) == 3
    assert _kaestchen_rechtecke(ohne_draw) == 0


def test_07_texte_alter_clients_fuer_kaestchen():
    """contract_data aus alten Clients: "true"/"false" als Text."""
    leer = dict(_BASIS, **{**_EMPFANG, **{k: False for k in _KAESTCHEN}})
    basis_l = _linien_ops(_pdf(leer))
    assert _linien_ops(_pdf(dict(leer, empfang_kaufpreis="true"))) - basis_l == 2
    assert _linien_ops(_pdf(dict(leer, empfang_kaufpreis="false"))) == basis_l


@pytest.mark.parametrize("digital", [False, True])
def test_08_xml_im_ort_bricht_pdf_nicht(digital):
    boese = "<font color=red>x</font>"
    c = dict(_BASIS, **{**_EMPFANG, "empfang_ort_kaeufer": boese,
                        "empfang_ort_verkaeufer": "A & B <b>",
                        "schluessel_anzahl": "3"})
    f = _flach(_text(_pdf(c, digital=digital)))
    assert f"Datum und Ort: 18.08.2026, {boese}" in f
    assert "Datum und Ort: 18.08.2026, A & B <b>" in f
    assert "KFZ mit 3 Schlüssel(n)" in f


def test_09_alles_auf_einer_seite_zusammen():
    """KeepTogether: Kasten-Inhalt steht mit den Linien auf derselben Seite."""
    pdf = _pdf(dict(_BASIS, **_EMPFANG))
    seiten = [_flach(p.extract_text() or "") for p in PdfReader(io.BytesIO(pdf)).pages]
    seite = next(s for s in seiten if "bestätigt Empfang von" in s)
    for teil in ("Unterschrift", "Datum und Ort: 18.08.2026, Grethem",
                 "Datum und Ort: 18.08.2026, Rensenheim", "Mit ihrer Unterschrift"):
        assert teil in seite, teil


# ---------------------------------------------------------------------------
# ContractIn
# ---------------------------------------------------------------------------
def _contract_in():
    import importlib
    return importlib.import_module("routes.contracts").ContractIn


_KOPF = {"vehicle_id": "v", "seller_name": "s", "purchase_price": 1}


def test_20_contractin_standardwerte_fuer_alte_clients():
    ContractIn = _contract_in()
    d = ContractIn(**_KOPF).model_dump()
    assert d["zulassung"] == "" and d["schluessel_anzahl"] == "" and d["empfang_datum"] == ""
    assert d["empfang_zulassungsbescheinigung"] is False
    assert d["empfang_schluessel"] is False and d["empfang_kaufpreis"] is False
    assert d["empfang_ort_kaeufer"] == "" and d["empfang_ort_verkaeufer"] == ""
    assert d["payment_method"] == "Bar / Überweisung"


def test_21_contractin_gueltige_werte_landen_in_model_dump():
    ContractIn = _contract_in()
    m = ContractIn(**_KOPF, **{**_EMPFANG, "zulassung": " Abgemeldet ",
                               "schluessel_anzahl": 2, "empfang_datum": " 2026-08-18 "})
    d = m.model_dump()
    assert d["zulassung"] == "abgemeldet"
    assert d["schluessel_anzahl"] == "2"          # coerce_numbers_to_str
    assert d["empfang_datum"] == "2026-08-18"
    for k in _KAESTCHEN:
        assert d[k] is True
    assert d["empfang_ort_kaeufer"] == "Rensenheim"
    assert d["empfang_ort_verkaeufer"] == "Grethem"
    assert d["payment_method"] == "Echtzeitüberweisung"
    assert ContractIn(**_KOPF, zulassung="ANGEMELDET").zulassung == "angemeldet"
    assert ContractIn(**_KOPF, zulassung="").zulassung == ""
    assert ContractIn(**_KOPF, schluessel_anzahl="12").schluessel_anzahl == "12"
    assert ContractIn(**_KOPF, schluessel_anzahl=" ").schluessel_anzahl == ""
    # Freitext alter Clients bei der Zahlungsart bleibt gueltig (kein Enum)
    assert ContractIn(**_KOPF, payment_method="Bar / Überweisung").payment_method == "Bar / Überweisung"
    assert ContractIn(**_KOPF, payment_method="Scheck").payment_method == "Scheck"
    assert ContractIn(**_KOPF, empfang_ort_kaeufer="x" * 100).empfang_ort_kaeufer == "x" * 100


@pytest.mark.parametrize("feld,wert", [
    ("zulassung", "vielleicht"),
    ("schluessel_anzahl", "abc"),
    ("schluessel_anzahl", "123"),
    ("schluessel_anzahl", "-1"),
    ("schluessel_anzahl", 2.5),
    ("empfang_datum", "18.08.2026"),
    ("empfang_datum", "2026-02-30"),
    ("empfang_ort_kaeufer", "x" * 101),
    ("empfang_ort_verkaeufer", "x" * 101),
    ("payment_method", "x" * 501),
])
def test_22_contractin_lehnt_ungueltiges_ab(feld, wert):
    ContractIn = _contract_in()
    with pytest.raises(ValidationError, match=feld):
        ContractIn(**_KOPF, **{feld: wert})


def test_23_contractin_bis_ins_pdf():
    """Validierte Eingabe -> model_dump -> PDF (so wie preview/create)."""
    ContractIn = _contract_in()
    d = ContractIn(**_KOPF, **_EMPFANG).model_dump()
    d["digital_vertragstext"] = DIGITAL_VERTRAGSTEXT_STANDARD
    for digital in (False, True):
        f = _flach(_text(_pdf(d, digital=digital)))
        assert "Datum und Ort: 18.08.2026, Rensenheim" in f
        assert "KFZ mit 2 Schlüssel(n)" in f and "Abgemeldet" in f
