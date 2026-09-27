# -*- coding: utf-8 -*-
"""Go-Live-Pruefung 27.09.2026 (K1, K2, K6, K7 + Zusatzfunde): Die
Vorbelegung der Zusicherungsfelder aus dem Inserat darf dem Verkaeufer nie
eine Zusicherung unterschieben, die das Inserat nicht klar hergibt.

K1  Verneinungen im Umkreis ("nicht mehr unfallfrei", "nicht lueckenlos
    scheckheftgepflegt", "bedingt fahrbereit") ergaben "Ja".
K6  Portalfeld accident_damaged=False ("Unbeschaedigtes Fahrzeug") ergab
    "Unfallfrei: Ja" mit erfundener Fundstelle "unfallfrei laut Inserat".
K7  roadworthy=True ueberstimmte "nicht fahrbereit" im Text.
K2  Abgelaufene HU ergab "HU: Ja"; "HU abgelaufen" im Text wurde vom
    Portalfeld ueberstimmt; "TUEV/AU 05/2026 abgelaufen" nicht erkannt.
PDF "Unfallschaden (Inserat)" / "Fahrbereit (Inserat)" widersprachen dem
    Dialogwert im selben Abschnitt.
Ohne KI, ohne DB, ohne Netz.
"""
import json
import sys
from datetime import date
from pathlib import Path

import pytest

BACKEND = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from ai import inserat_regeln as R  # noqa: E402

HEUTE = date(2026, 9, 27)


def _werte(v, heute=HEUTE):
    erg = R.vorschlaege(v, heute=heute)
    return {k: e["value"] for k, e in erg["felder"].items()}, erg


# ------------------------------------------------ K1: Verneinung im Umkreis
@pytest.mark.parametrize("text, feld", [
    ("Leider nicht mehr unfallfrei.", "accident_free"),
    ("Das Auto war nie unfallfrei.", "accident_free"),
    ("nicht ganz unfallfrei", "accident_free"),
    ("Unfallfrei? Leider nein", "accident_free"),
    ("Fahrzeug ist nicht mehr fahrbereit.", "drivable"),
])
def test_k1_verneinung_wird_nein(text, feld):
    werte, erg = _werte({"description": text})
    assert werte.get(feld) == "Nein", (text, erg)


@pytest.mark.parametrize("text, feld", [
    ("bedingt fahrbereit", "drivable"),
    ("Auto ist nur eingeschränkt fahrbereit", "drivable"),
    ("nicht lückenlos scheckheftgepflegt", "service_book"),
    ("nicht lueckenlos scheckheftgepflegt", "service_book"),
    ("nicht durchgehend scheckheftgepflegt", "service_book"),
    # strittig: ein Hagelschaden ist rechtlich meist kein Unfall — trotzdem kein "Ja"
    ("Kein Unfallschaden, aber Hagelschaden", "accident_free"),
    ("unfallfrei (laut Vorbesitzer)", "accident_free"),
])
def test_k1_unklar_wird_nichts_und_hinweis(text, feld):
    werte, erg = _werte({"description": text})
    assert feld not in werte, (text, erg)
    assert erg["hinweise"], "der Sucher muss sehen, warum nichts vorbelegt ist"


def test_k1_nie_ja_bei_widerspruch_im_freitext():
    for text in ("unfallfrei, hatte aber einen Unfall", "Unfallfrei: ja. Unfallschaden hinten links",
                 "fahrbereit, Motorschaden", "fahrbereit. Fahrbereit? Nein"):
        werte, erg = _werte({"description": text})
        assert "Ja" not in (werte.get("accident_free"), werte.get("drivable")), (text, erg)


@pytest.mark.parametrize("v, erwartet", [
    ({"description": "unfallfrei"}, {"accident_free": "Ja"}),
    ({"description": "Unfallfreies Fahrzeug aus erster Hand"}, {"accident_free": "Ja"}),
    ({"description": "kein Unfallschaden"}, {"accident_free": "Ja"}),
    ({"description": "lückenlos scheckheftgepflegt"}, {"service_book": "ja"}),
    ({"description": "Klima geht nicht, aber fahrbereit"}, {"drivable": "Ja"}),
    ({"description": "Unfallschaden vorne links, fahrbereit"}, {"accident_free": "Nein", "drivable": "Ja"}),
    ({"description": "HU neu 09/2027"}, {"hu_valid": "Ja", "hu_until": "09/2027"}),
    ({"description": "TÜV neu"}, {"hu_valid": "Ja"}),
])
def test_positivfaelle_bleiben_ja(v, erwartet):
    werte, erg = _werte(v)
    assert werte == erwartet, erg


def test_scheckheftgepflegt_allein_bleibt_hinweis():
    werte, erg = _werte({"description": "scheckheftgepflegt"})
    assert "service_book" not in werte and "lückenlos" in erg["hinweise"][0]


# ------------------------------------------------ K6: Portalfeld Unfall
def test_k6_portal_unbeschaedigt_ist_kein_unfallfrei():
    werte, erg = _werte({"accident_damaged": False,
                         "zustand_portal": "Kleinanzeigen: Unbeschädigtes Fahrzeug"})
    assert "accident_free" not in werte
    hinweis = " ".join(erg["hinweise"])
    assert "Kleinanzeigen: Unbeschädigtes Fahrzeug" in hinweis, "echter Portalwert als Fundstelle"
    assert "unfallfrei laut Inserat" not in json.dumps(erg, ensure_ascii=False)


def test_k6_portal_false_ohne_rohwert_zeigt_das_feld_nicht_einen_erfundenen_satz():
    werte, erg = _werte({"accident_damaged": False})
    assert "accident_free" not in werte
    assert "Portalfeld „Unfallschaden“: nein" in " ".join(erg["hinweise"])


def test_k6_portal_false_und_unfalltext_nichts_vorbelegen():
    werte, erg = _werte({"accident_damaged": False,
                         "description": "Unfallwagen, Frontschaden repariert. Leider nicht unfallfrei."})
    assert "accident_free" not in werte, erg
    assert any("widersprüchlich" in h for h in erg["hinweise"])


def test_k6_portal_false_und_text_unfallfrei_ja_aus_dem_text():
    werte, erg = _werte({"accident_damaged": False, "description": "Unfallfrei, Nichtraucher"})
    assert werte["accident_free"] == "Ja"
    assert erg["felder"]["accident_free"]["source"] == "listing_description"


def test_k6_portal_unfallschaden_bleibt_nein_mit_echtem_wert():
    werte, erg = _werte({"accident_damaged": True,
                         "zustand_portal": "Kleinanzeigen: Beschädigtes Fahrzeug"})
    assert werte["accident_free"] == "Nein"
    assert erg["felder"]["accident_free"]["source_text"] == "Kleinanzeigen: Beschädigtes Fahrzeug"
    werte, _ = _werte({"accident_damaged": True, "description": "unfallfrei"})
    assert "accident_free" not in werte, "Portal Unfall + Text unfallfrei = widerspruechlich"


def test_k6_mobile_repariert():
    import mobile_service as M
    assert M.zustand_unfall(False, "used vehicle, repaired") is None
    assert M.zustand_unfall(False, "gebrauchtfahrzeug, repariert") is None
    assert M.zustand_unfall(False, "damaged, not repaired") is True
    assert M.zustand_unfall(False, "") is False
    werte, erg = _werte({"accident_damaged": None, "zustand_portal": "mobile.de: Used vehicle, Repaired"})
    assert "accident_free" not in werte
    assert "mobile.de: Used vehicle, Repaired" in " ".join(erg["hinweise"])
    werte, _ = _werte({"accident_damaged": None, "zustand_portal": "mobile.de: Used vehicle, Repaired",
                       "description": "unfallfrei"})
    assert "accident_free" not in werte


def test_k6_mobile_datensatz_isdamagecase_false():
    import mobile_service as M
    item = json.loads((BACKEND / "tests" / "fixtures" / "apify_mobile_item.json").read_text(encoding="utf-8"))[0]
    v = M._parse_apify_item(item, "42196329136896")
    assert v["zustand_portal"] == "mobile.de: Used vehicle, Accident-free"
    werte, erg = _werte(v)
    assert "accident_free" not in werte and "drivable" not in werte, erg
    assert any("mobile.de: Used vehicle, Accident-free" in h for h in erg["hinweise"])
    item2 = json.loads(json.dumps(item))
    for a in item2["attributes"]:
        if a.get("tag") == "damageCondition":
            a["value"] = "Used vehicle, Repaired"
    v2 = M._parse_apify_item(item2, "42196329136896")
    assert v2["accident_damaged"] is None
    assert "accident_free" not in _werte(v2)[0]


def test_k6_kleinanzeigen_api_traegt_echten_portalwert():
    from test_kleinanzeigen_api import URL, _ad
    import kleinanzeigen_api as A
    v = A.fahrzeug_aus_api(_ad(), URL, "3458821471")
    assert v["accident_damaged"] is False
    assert v["zustand_portal"] == "Kleinanzeigen: Unbeschädigtes Fahrzeug"
    werte, erg = _werte(v)
    assert "accident_free" not in werte
    assert any("Kleinanzeigen: Unbeschädigtes Fahrzeug" in h for h in erg["hinweise"])


# ------------------------------------------------ K7: Portalfeld fahrbereit
def test_k7_portal_fahrbereit_ueberstimmt_text_nicht():
    werte, erg = _werte({"roadworthy": True, "description": "Motor defekt, Auto nicht fahrbereit"})
    assert "drivable" not in werte, erg
    assert any("widersprüchlich" in h for h in erg["hinweise"])


def test_k7_portal_fahrbereit_allein_ist_nur_hinweis():
    werte, erg = _werte({"roadworthy": True})
    assert "drivable" not in werte
    assert "fahrbereit laut Inserat" not in json.dumps(erg, ensure_ascii=False)
    assert any("Portalfeld „fahrbereit“: ja" in h for h in erg["hinweise"])
    werte, _ = _werte({"roadworthy": True, "description": "fahrbereit"})
    assert werte["drivable"] == "Ja"
    werte, _ = _werte({"roadworthy": False})
    assert werte["drivable"] == "Nein"


# ------------------------------------------------ K2: HU
def test_k2_hu_08_2026_ist_heute_abgelaufen():
    # bewusst mit dem ECHTEN heutigen Datum (Pruefung am 27.09.2026)
    erg = R.vorschlaege({"description": "HU 08/2026"})
    assert "hu_valid" not in erg["felder"] and "hu_until" not in erg["felder"]
    assert any("HU abgelaufen (08/2026)" in h for h in erg["hinweise"])


def test_k2_hu_im_laufenden_monat_gilt_noch():
    werte, _ = _werte({"description": "HU 09/2026"})
    assert werte == {"hu_valid": "Ja", "hu_until": "09/2026"}
    werte, erg = _werte({"description": "HU 09/2026"}, heute=date(2026, 10, 1))
    assert werte == {} and "HU abgelaufen (09/2026)" in erg["hinweise"][0]


def test_k2_portalfeld_abgelaufen():
    werte, erg = _werte({"hu": "2026-08"})
    assert werte == {}
    assert "HU abgelaufen (08/2026)" in erg["hinweise"][0]


@pytest.mark.parametrize("hu_feld", ["08/2026", "08/2028", "Neu"])
def test_k2_text_abgelaufen_hat_vorrang_vor_portalfeld(hu_feld):
    werte, erg = _werte({"hu": hu_feld, "description": "HU abgelaufen, wird neu gemacht"})
    assert werte.get("hu_valid") != "Ja" and "hu_until" not in werte, erg
    assert werte.get("hu_valid") == "Nein"


@pytest.mark.parametrize("text", ["TÜV/AU 05/2026 abgelaufen", "TÜV seit 05.2026 abgelaufen",
                                  "Keine HU", "ohne TÜV", "abgelaufener TÜV"])
def test_k2_abgelaufen_muster(text):
    werte, erg = _werte({"description": text})
    assert werte == {"hu_valid": "Nein"}, (text, erg)


# ------------------------------------------------ PDF: keine Widersprueche im Abschnitt
@pytest.mark.parametrize("inserat, dialog, sichtbar", [
    (False, "Ja", True),        # passt: kein Unfallschaden <-> unfallfrei
    (True, "Nein", True),       # passt: Unfallschaden <-> nicht unfallfrei
    (False, "Nein", False),     # Widerspruch -> Zeile weg
    (True, "Ja", False),
    (False, "", False),         # im Dialog leer -> Zeile weg
    (None, "Ja", False),        # keine Portalangabe
])
def test_pdf_unfallschaden_inserat_nur_passend(inserat, dialog, sichtbar):
    from pdf_service import generate_contract_pdf
    from test_dokumente_20260920 import _text, _vertrag
    roh = generate_contract_pdf(dealer={"company_name": "Autohaus Test"},
                                vehicle={"make_label": "BMW", "accident_damaged": inserat},
                                contract=_vertrag(accident_free=dialog))
    assert ("Unfallschaden (Inserat)" in _text(roh)) is sichtbar


@pytest.mark.parametrize("inserat, dialog, sichtbar", [
    (True, "Ja", True), (False, "Nein", True),
    (True, "Nein", False), (False, "Ja", False), (True, "", False), (True, None, False),
])
def test_pdf_fahrbereit_inserat_nur_passend(inserat, dialog, sichtbar):
    from pdf_service import generate_contract_pdf
    from test_dokumente_20260920 import _text, _vertrag
    roh = generate_contract_pdf(dealer={"company_name": "Autohaus Test"},
                                vehicle={"make_label": "BMW", "roadworthy": inserat},
                                contract=_vertrag(drivable=dialog))
    text = _text(roh)
    assert ("Fahrbereit (Inserat)" in text) is sichtbar
    if dialog:
        assert "Fahrtauglich" in text
