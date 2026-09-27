# -*- coding: utf-8 -*-
"""Nachpruefung 28.09.2026 zur Inserat-Vorbelegung (Gruppe inserat2).

(1) Verneinung zu weit: Jede harte Verneinung (kein/keine/ohne/nie/nicht)
    bis 4 Woerter vor dem Stichwort machte das Stichwort zu "Nein" — auch
    wenn sie zu einem ANDEREN Wort gehoerte ("keine Maengel unfallfrei" ->
    "Unfallfrei: Nein"). Jetzt nur, wenn die Verneinung sich auf das
    Stichwort bezieht (unmittelbar davor oder nur durch Fuellwoerter
    getrennt, bzw. "<Stichwort>? nein").
(2) HU "abgelaufen" zu weit: "TUEV 10/2027 Bremsbelaege abgelaufen" ergab
    "HU/AU vorhanden: Nein". Zwischen HU-Wort und "abgelaufen" nur noch
    Datum, AU, "ist/seit/leider" und Satzzeichen ohne Komma.
(4) Entscheidung Auftraggeber: Portal-Zeilen im Vertrag nur noch als
    negative Offenlegung ("Unfallschaden (Inserat): Ja", "Fahrbereit
    (Inserat): Nein"), dann aber immer; positive Portalangaben nie.
Ohne KI, ohne DB, ohne Netz.
"""
import sys
from datetime import date
from pathlib import Path

import pytest

BACKEND = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from ai import inserat_regeln as R  # noqa: E402

HEUTE = date(2026, 9, 28)


def _werte(v, heute=HEUTE):
    erg = R.vorschlaege(v, heute=heute)
    return {k: e["value"] for k, e in erg["felder"].items()}, erg


# ------------------------------------------------ (1) die sechs Pruefer-Saetze
@pytest.mark.parametrize("text, erwartet", [
    ("keine Mängel unfallfrei", {"accident_free": "Ja"}),
    ("Nichtraucherfahrzeug keine Haustiere unfallfrei", {"accident_free": "Ja"}),
    ("Verkaufe ohne Garantie unfallfrei", {"accident_free": "Ja"}),
    ("Garagenwagen ohne Rost unfallfrei", {"accident_free": "Ja"}),
    ("TÜV neu keine Mängel unfallfrei fahrbereit", {"accident_free": "Ja", "drivable": "Ja"}),
    ("Wegen Neuanschaffung abzugeben kein Tausch unfallfrei fahrbereit",
     {"accident_free": "Ja", "drivable": "Ja"}),
])
def test_verneinung_eines_anderen_wortes_macht_kein_nein(text, erwartet):
    werte, erg = _werte({"description": text})
    for feld, wert in erwartet.items():
        assert werte.get(feld) == wert, (text, erg)
    assert "Nein" not in (werte.get("accident_free"), werte.get("drivable")), (text, erg)


def test_tuev_neu_keine_maengel_hu_bleibt_ja():
    werte, _ = _werte({"description": "TÜV neu keine Mängel unfallfrei fahrbereit"})
    assert werte.get("hu_valid") == "Ja"


# ------------------------------------------------ (1) Verneinung am Stichwort bleibt "Nein"
@pytest.mark.parametrize("text, feld", [
    ("Unfallfrei: nein", "accident_free"),
    ("Unfallfrei? nein", "accident_free"),
    ("nicht mehr ganz unfallfrei", "accident_free"),
    ("Leider nicht mehr unfallfrei.", "accident_free"),
    ("Das Auto war nie unfallfrei.", "accident_free"),
    ("nicht zu 100 % unfallfrei", "accident_free"),
    ("nicht 100% unfallfrei", "accident_free"),
    ("nicht wirklich fahrbereit", "drivable"),
    ("Fahrzeug ist leider nicht mehr fahrbereit", "drivable"),
    ("nicht komplett fahrbereit", "drivable"),
    ("Fahrbereit: nein", "drivable"),
])
def test_verneinung_am_stichwort_bleibt_nein(text, feld):
    werte, erg = _werte({"description": text})
    assert werte.get(feld) == "Nein", (text, erg)


@pytest.mark.parametrize("text", [
    "kein Unfall, unfallfrei",
    "keine Unfälle, scheckheftgepflegt",
    "Hatte nie einen Unfall",
    "kein größerer Unfallschaden",
    "ohne Unfall",
])
def test_verneinter_unfall_ist_unfallfrei(text):
    werte, erg = _werte({"description": text})
    assert werte.get("accident_free") == "Ja", (text, erg)


def test_keine_unfaelle_scheckheftgepflegt_ja_fuer_beide():
    # "scheckheftgepflegt" allein bleibt ein Hinweis (Vertrag sagt "Ja, lueckenlos");
    # das "keine" der Unfaelle darf das Scheckheft aber nicht verneinen.
    werte, erg = _werte({"description": "keine Unfälle, scheckheftgepflegt"})
    assert werte.get("accident_free") == "Ja"
    assert "service_book" not in werte
    assert any("heißt nicht zwingend lückenlos" in h for h in erg["hinweise"]), erg
    werte, erg = _werte({"description": "Keine Unfälle lückenlos scheckheftgepflegt"})
    assert werte == {"accident_free": "Ja", "service_book": "ja"}, erg


@pytest.mark.parametrize("text", [
    "keine Kratzer Unfallschaden hinten",       # Nomen dazwischen: der Unfall ist NICHT verneint
    "ohne Garantie Unfallwagen",
])
def test_verneinung_vor_anderem_nomen_verneint_den_unfall_nicht(text):
    werte, erg = _werte({"description": text})
    assert werte.get("accident_free") == "Nein", (text, erg)


@pytest.mark.parametrize("text, feld", [
    ("bedingt fahrbereit", "drivable"),
    ("Auto ist nur eingeschränkt fahrbereit", "drivable"),
    ("unfallfrei (laut Vorbesitzer)", "accident_free"),
    ("Kein Unfallschaden, aber Hagelschaden", "accident_free"),
    ("nicht lückenlos scheckheftgepflegt", "service_book"),
    ("unfallfrei ist er leider nicht", "accident_free"),
])
def test_unklare_faelle_bleiben_leer_mit_hinweis(text, feld):
    werte, erg = _werte({"description": text})
    assert feld not in werte, (text, erg)
    assert erg["hinweise"], (text, erg)


def test_eu_import_verneinung_nur_am_stichwort():
    werte, _ = _werte({"description": "kein EU-Import"})
    assert "eu_import" not in werte
    werte, _ = _werte({"description": "keine Mängel EU-Import"})
    assert werte.get("eu_import") == "Ja"


# ------------------------------------------------ (2) HU "abgelaufen" nur am HU-Wort
@pytest.mark.parametrize("text, bis", [
    ("TÜV 10/2027 Bremsbeläge abgelaufen", "10/2027"),
    ("TÜV 03/2027 Garantie abgelaufen", "03/2027"),
    ("HU 11/2027 Leasing abgelaufen", "11/2027"),
])
def test_hu_abgelaufen_gehoert_zu_anderem_wort(text, bis):
    werte, erg = _werte({"description": text})
    assert werte == {"hu_valid": "Ja", "hu_until": bis}, (text, erg)


@pytest.mark.parametrize("text", [
    "TÜV/AU 05/2026 abgelaufen", "HU abgelaufen", "TÜV seit 05.2026 abgelaufen",
    "HU ist leider abgelaufen", "TÜV: abgelaufen", "HU und AU abgelaufen", "HU/AU seit 05/2026 abgelaufen",
    "TÜV seit Mai abgelaufen", "HU ist seit 2025 abgelaufen", "keinen TÜV",
])
def test_hu_abgelaufen_bleibt_erkannt(text):
    werte, erg = _werte({"description": text})
    assert werte == {"hu_valid": "Nein"}, (text, erg)


def test_hu_ohne_datum_mit_fremdwort_vor_abgelaufen_belegt_nichts():
    # kein "Nein" aus einem fremden "abgelaufen", kein Datum -> gar nichts
    werte, erg = _werte({"description": "TÜV Garantie abgelaufen"})
    assert werte == {}, erg


def test_hu_abgelaufen_nicht_ueber_komma():
    werte, erg = _werte({"description": "TÜV 10/2027, Garantie abgelaufen"})
    assert werte == {"hu_valid": "Ja", "hu_until": "10/2027"}, erg


# ------------------------------------------------ (4) Portal nur als Offenlegung
def _pdf_text(vehicle, **vertrag):
    from pdf_service import generate_contract_pdf
    from test_dokumente_20260920 import _text, _vertrag
    roh = generate_contract_pdf(dealer={"company_name": "Autohaus Test"},
                                vehicle={"make_label": "BMW", **vehicle},
                                contract=_vertrag(**vertrag))
    return _text(roh)


@pytest.mark.parametrize("dialog", ["Ja", "Nein", "", None])
def test_portal_unfallschaden_ja_steht_immer_im_vertrag(dialog):
    text = _pdf_text({"accident_damaged": True}, accident_free=dialog)
    assert "Unfallschaden (Inserat)" in text


@pytest.mark.parametrize("dialog", ["Ja", "Nein", "", None])
def test_portal_nicht_fahrbereit_steht_immer_im_vertrag(dialog):
    text = _pdf_text({"roadworthy": False}, drivable=dialog)
    assert "Fahrbereit (Inserat)" in text


@pytest.mark.parametrize("dialog", ["Ja", "Nein", "", None])
def test_positive_portalangaben_nie_im_vertrag(dialog):
    text = _pdf_text({"accident_damaged": False, "roadworthy": True}, accident_free=dialog, drivable=dialog)
    assert "Unfallschaden (Inserat)" not in text
    assert "Fahrbereit (Inserat)" not in text


def test_offenlegung_zeigt_den_portalwert():
    import pdf_service as P
    assert P._inserat_zustand(True, offenlegung=True) == "Ja"
    assert P._inserat_zustand(False, offenlegung=False) == "Nein"
    assert P._inserat_zustand(False, offenlegung=True) == "—"
    assert P._inserat_zustand(True, offenlegung=False) == "—"
    assert P._inserat_zustand(None, offenlegung=True) == "—"
