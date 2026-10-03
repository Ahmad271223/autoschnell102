# -*- coding: utf-8 -*-
"""Vorher/Nachher im Abholprotokoll (Wunsch Ahmad, 12.09.2026).

Der Chef sah im Freigabe-Kasten "Erstzulassung: weicht ab -> 01/2020" — aber
nicht, was im Vertrag stand. Die Analyse fand dahinter drei Fehler: "laut
Vertrag" war der Inseratswert, Ja/Nein-Zeilen wurden ohne Vertrag gewertet,
und ein zurueckgenommener Korrekturwert blieb stehen.

Reine Funktionen, keine Datenbank.
"""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import protokoll_vergleich as PV  # noqa: E402


def _felder():
    from routes.protocols import VEHICLE_CHECK_FIELDS
    return VEHICLE_CHECK_FIELDS


def _zeile(zeilen, schluessel):
    return next(z for z in zeilen if z["schluessel"] == schluessel)


# ------------------------------------------------------------ Lesen
@pytest.mark.parametrize("eingabe,art,erwartet", [
    ("01/2020", "ez", "01/2020"),
    ("1.2020", "ez", "01/2020"),
    ("2020-01", "ez", "01/2020"),
    ("01.03.2020", "ez", "03/2020"),
    ("032020", "ez", "03/2020"),
    ("06/26", "hu", "06/2026"),
    ("01/95", "ez", "01/1995"),
    ("2018", "ez", "2018"),       # nur ein Jahr bleibt unveraendert
    ("Neu", "hu", "Neu"),
    ("13/2020", "ez", "13/2020"),
    (None, "ez", ""),
])
def test_01_monat_jahr_text(eingabe, art, erwartet):
    assert PV.monat_jahr_text(eingabe, art) == erwartet


def test_02_zahlen_und_km():
    assert PV.zahl("86.000 km") == 86000 and PV.zahl(86000.0) == 86000 and PV.zahl("") is None
    # Gegenpruefung 12.09.2026: nicht alle Ziffern aneinanderhaengen
    assert PV.zahl("ca. 72.000, Tacho 2019") is None and PV.zahl("86400 km, Reserve 50") is None
    assert PV.zahl("2 (Brief: 3)") is None
    assert PV.zahl(float("inf")) is None and PV.zahl(float("nan")) is None
    assert PV.zahl("86000.5") == 86000 and PV.zahl("110 kW") == 110 and PV.zahl("75.200 km") == 75200
    assert PV.km_text(86000) == "86.000 km" and PV.km_text(None) == ""
    assert PV.ja_nein("ja") == "Ja" and PV.ja_nein("Nein") == "Nein" and PV.ja_nein("unbekannt") is None


# ------------------------------------------------------------ Vertragswerte
def test_03_vertrag_schlaegt_inserat():
    """Befund: Korrigierte der Haendler im Vertrag die EZ, sah der Fahrer
    weiter den Inseratswert."""
    fahrzeug = {"make_label": "Chevrolet", "model_label": "Camaro", "first_registration": "01/2021",
                "mileage": 75000, "power_kw": 110, "vin": "wdb 123", "exterior_color": "Rot"}
    vertrag = {"vehicle_first_registration": "03/2021", "vehicle_mileage": "86000",
               "previous_owners": "2", "hu_valid": "Ja", "hu_until": "06/26",
               "accident_free": "Ja", "commercial_since_ez": "Nein"}
    w = PV.vertragswerte(fahrzeug, vertrag)
    assert w["first_registration"] == {"wert": "03/2021", "text": "03/2021", "quelle": "vertrag"}
    assert w["mileage_contract"]["text"] == "86.000 km" and w["mileage_contract"]["quelle"] == "vertrag"
    assert w["make"]["text"] == "Chevrolet" and w["make"]["quelle"] == "inserat"
    assert w["power"]["text"] == "110 kW / 150 PS"
    assert w["vin"]["text"] == "WDB123"
    assert w["previous_owners"] == {"wert": 2, "text": "2", "quelle": "vertrag"}
    assert w["hu"]["text"] == "06/2026"
    assert w["accident_free"]["wert"] == "Ja" and w["commercial"]["wert"] == "Nein"


def test_04_hu_varianten_und_fehlender_vertrag():
    assert PV.vertragswerte({}, {"hu_valid": "Nein", "hu_until": "06/26"})["hu"]["text"] == "keine HU"
    ohne = PV.vertragswerte({"hu": "Neu", "previous_owners": 3}, {})
    assert ohne["hu"] == {"wert": "Neu", "text": "Neu", "quelle": "inserat"}
    assert ohne["previous_owners"]["quelle"] == "inserat"
    assert ohne["accident_free"] == {"wert": None, "text": "", "quelle": None}


# ------------------------------------------------------------ Vergleich
def _lauf(vehicle_check, condition=None, fahrzeug=None, vertrag=None):
    werte = PV.vertragswerte(fahrzeug or {"first_registration": "03/2019", "mileage": 86000},
                             vertrag if vertrag is not None else
                             {"previous_owners": "2", "accident_free": "Ja", "commercial_since_ez": "Nein"})
    return PV.vergleich(_felder(), vehicle_check, condition or {}, werte)


def test_05_vorher_nachher_bei_abweichung():
    zeilen = _lauf({"first_registration": {"status": "weicht ab", "value": "1.2020"},
                    "previous_owners": {"status": "weicht ab", "value": "3"}})
    ez = _zeile(zeilen, "first_registration")
    assert (ez["vertrag_text"], ez["vor_ort_text"], ez["abweichend"]) == ("03/2019", "01/2020", True)
    halter = _zeile(zeilen, "previous_owners")
    assert (halter["vertrag_text"], halter["vor_ort_text"]) == ("2", "3")


def test_06_stimmt_mit_altem_korrekturwert_ist_keine_abweichung():
    """Befund: Zurueck auf 'stimmt' — der alte Wert landete trotzdem im PDF."""
    zeilen = _lauf({"first_registration": {"status": "stimmt", "value": "01/2020"}})
    ez = _zeile(zeilen, "first_registration")
    assert ez["abweichend"] is False and ez["vor_ort_text"] == "03/2019"
    assert PV.abweichungen(zeilen) == []


def test_07_ja_nein_wird_mit_dem_vertrag_verglichen():
    """Befund: 'Gewerbliche Nutzung: Nein' galt als Abweichung."""
    zeilen = _lauf({"commercial": {"status": "Nein"}, "accident_free": {"status": "Ja"}})
    assert not _zeile(zeilen, "commercial")["abweichend"]
    assert not _zeile(zeilen, "accident_free")["abweichend"]
    assert PV.abweichungen(zeilen) == []

    zeilen = _lauf({"commercial": {"status": "Ja"}, "accident_free": {"status": "Nein"}})
    assert _zeile(zeilen, "commercial")["abweichend"] is True
    unfall = _zeile(zeilen, "accident_free")
    assert (unfall["vertrag_text"], unfall["vor_ort_text"], unfall["abweichend"]) == ("Ja", "Nein", True)


def test_08_unfallfrei_nein_immer_und_unbekannt_als_hinweis():
    zeilen = _lauf({"accident_free": {"status": "Nein"}, "commercial": {"status": "unbekannt"}},
                   vertrag={})
    assert _zeile(zeilen, "accident_free")["abweichend"] is True, "ohne Vertragsangabe trotzdem rot"
    gew = _zeile(zeilen, "commercial")
    assert gew["abweichend"] is False and gew["hinweis"] == "Fahrer: unbekannt"
    assert {a["schluessel"] for a in PV.abweichungen(zeilen)} == {"accident_free", "commercial"}


def test_09_kilometer_bei_abholung():
    ok = _zeile(_lauf({"mileage_contract": {"status": "stimmt"}}, condition={"mileage": "86.400"}),
                "mileage_contract")
    assert ok["vor_ort_text"] == "86.400 km" and ok["abweichend"] is False
    assert ok["hinweis"] == "+400 km bei Abholung"

    weit = _zeile(_lauf({"mileage_contract": {"status": "stimmt"}}, condition={"mileage": 88000}),
                  "mileage_contract")
    assert weit["abweichend"] is True and weit["hinweis"] == "+2.000 km bei Abholung"

    korrigiert = _zeile(_lauf({"mileage_contract": {"status": "weicht ab", "value": "72000"}}),
                        "mileage_contract")
    assert (korrigiert["vertrag_text"], korrigiert["vor_ort_text"]) == ("86.000 km", "72.000 km")
    assert korrigiert["hinweis"] == "−14.000 km"


def test_10_weicht_ab_ohne_wert_und_ohne_vertrag():
    zeilen = _lauf({"color": {"status": "weicht ab", "value": ""},
                    "fuel": {"status": "weicht ab", "value": "Diesel"}}, fahrzeug={})
    assert _zeile(zeilen, "color")["hinweis"] == "kein Wert eingetragen"
    fuel = _zeile(zeilen, "fuel")
    assert fuel["vor_ort_text"] == "Diesel" and fuel["hinweis"] == "nicht im Vertrag"


def test_11_abweichungen_behalten_die_runde_30_schluessel():
    """tests/test_befunde_runde30_freigabe.py liest feld/status/wert."""
    zeilen = _lauf({"mileage_contract": {"status": "weicht ab", "value": "75.200 km"}})
    a = PV.abweichungen(zeilen)
    assert [x["feld"] for x in a] == ["KM-Stand laut Vertrag"]
    assert a[0]["wert"] == "75.200 km" and a[0]["status"] == "weicht ab"
    assert a[0]["vertrag_text"] == "86.000 km" and a[0]["vor_ort_text"] == "75.200 km"


def test_12_alle_zwoelf_zeilen_kommen_zurueck():
    zeilen = _lauf({})
    assert [z["schluessel"] for z in zeilen] == [k for k, _l, _o in _felder()]
    assert all(z["abweichend"] is False for z in zeilen)


# ------------------------------------------------------------ Gegenpruefung 12.09.2026
def test_13_hu_nur_aus_dem_vertrag():
    """Befund: Das HU-Datum aus dem Inserat hiess 'laut Vertrag', der
    Kaufvertrag druckt es aber nicht."""
    assert PV.vertragswerte({"hu": "06/2024"}, {"purchase_price": 45000})["hu"]["text"] == ""
    assert PV.vertragswerte({"hu": "03/2024"}, {"hu_valid": "Ja"})["hu"]["text"] == "gültig"
    assert PV.vertragswerte({"hu": "06/2024"}, {})["hu"]["text"] == "06/2024", "ohne Vertrag: Inserat"


def test_14_unfall_im_vertrag_bekannt_ist_keine_abweichung():
    zeilen = _lauf({"accident_free": {"status": "Nein"}}, vertrag={"accident_free": "Nein"})
    unfall = _zeile(zeilen, "accident_free")
    assert unfall["abweichend"] is False and unfall["hinweis"] == "Unfall laut Vertrag bekannt"
    assert PV.abweichungen(zeilen) == []


def test_15_halb_getipptes_datum_wird_nicht_umgedeutet():
    zeilen = _lauf({"hu": {"status": "weicht ab", "value": "06/20"},
                    "first_registration": {"status": "weicht ab", "value": "03/201"}})
    hu = _zeile(zeilen, "hu")
    assert (hu["vor_ort_text"], hu["hinweis"]) == ("06/20", "unvollständig")
    ez = _zeile(zeilen, "first_registration")
    assert (ez["vor_ort_text"], ez["hinweis"]) == ("03/201", "unvollständig")
    keine = _zeile(_lauf({"hu": {"status": "weicht ab", "value": "keine HU"}}), "hu")
    assert keine["vor_ort_text"] == "keine HU" and keine["hinweis"] != "unvollständig"
    # Altwerte aus Vertrag/Inserat duerfen weiter kurz sein
    assert PV.monat_jahr_text("06/26", "hu") == "06/2026"


def test_16_unlesbare_kilometer_bleiben_text():
    z = _zeile(_lauf({"mileage_contract": {"status": "weicht ab", "value": "ca. 72.000, Tacho 2019"}}),
               "mileage_contract")
    assert z["vor_ort_text"] == "ca. 72.000, Tacho 2019" and z["hinweis"] == "keine lesbare Zahl"
    abholung = _zeile(_lauf({"mileage_contract": {"status": "stimmt"}},
                            condition={"mileage": "86400 km, Reserve 50"}), "mileage_contract")
    assert abholung["abweichend"] is False and abholung["vor_ort_text"] == "86.000 km"


def test_17_json_sicher():
    assert PV.json_sicher({"a": float("inf"), "b": [1.5, float("nan")], "c": "x"}) \
        == {"a": None, "b": [1.5, None], "c": "x"}


def test_18_protokoll_pdf_ohne_datenbank():
    """Befund: build_pickup_pdf zog routes.protocols -> deps nach und brauchte
    MONGO_URL — auch fuer das leere Papier-PDF."""
    import os
    import subprocess
    env = {k: v for k, v in os.environ.items() if k not in ("MONGO_URL", "DB_NAME")}
    code = ("import sys, pickup_pdf_service as P; "
            "pdf = P.build_pickup_pdf(appointment={'id': 't'}, vehicle={}, contract={}, "
            "filled={'vehicle_check': {}}); "
            "assert pdf[:4] == b'%PDF'; "
            "assert 'deps' not in sys.modules, sorted(m for m in sys.modules if m.startswith('routes'))")
    r = subprocess.run([sys.executable, "-c", code], cwd=str(Path(__file__).resolve().parents[1]),
                       env=env, capture_output=True, text=True, timeout=180)
    assert r.returncode == 0, (r.stdout + r.stderr)[-2500:]

