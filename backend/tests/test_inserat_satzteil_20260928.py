# -*- coding: utf-8 -*-
"""Runde inserat3 (28.09.2026): Inserat-Vorbelegung konservativ je Satzteil.

Die Einzelregeln der Runden vom 27./28.09. erzeugten jeweils neue Fehler in
die falsche Richtung ("keinesfalls unfallfrei" -> Ja, "keinerlei Unfaelle"
-> Nein, "TUEV ist seit 2 Monaten abgelaufen" + Portalfeld -> Ja). Neues
Prinzip (Vorgabe Auftraggeber): Pro Stichwort-Vorkommen ein Urteil
{ja, nein, unklar} im Satzteil; "ja" nur eindeutig, "nein" nur bei
ausdruecklichen Negativmustern, alles andere mit Verneinungs-/
Vorbehaltswort = unklar -> Feld leer mit Hinweis.

1. Satzkatalog des Auftraggebers (fixtures/inserat_korpus_20260928.json)
   komplett: "Ja"/"Nein"/"leer" exakt, "nicht_ja" = Nein oder leer,
   "nicht_nein" = Ja oder leer.
2. Eigene Saetze im Kleinanzeigen-/mobile.de-Stil (mit und ohne
   Satzzeichen, Stichpunktlisten, ganze Inserate).
3. Portalfeld + Text.
Heute fest 28.09.2026 (Uhr per monkeypatch). Ohne KI, ohne DB, ohne Netz.
"""
import json
import sys
from datetime import date
from pathlib import Path

import pytest

BACKEND = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND))

from ai import inserat_regeln as R  # noqa: E402

HEUTE = date(2026, 9, 28)
KORPUS = json.loads((BACKEND / "tests" / "fixtures" / "inserat_korpus_20260928.json").read_text(encoding="utf-8"))
KORPUS_FELD = {"unfallfrei": "accident_free", "fahrbereit": "drivable", "scheckheft": "service_book",
               "hu": "hu_valid"}
# Hinweis muss zum Feld passen (leer = der Sucher muss sehen, warum)
HINWEIS_STICHWORT = {"accident_free": "Unfall", "drivable": "Fahr", "hu_valid": "HU",
                     "service_book": "checkheft"}


@pytest.fixture(autouse=True)
def _uhr(monkeypatch):
    # fester Tag fuer JEDE HU-Pruefung (auch ohne heute-Parameter)
    monkeypatch.setattr(R, "_heute_berlin", lambda: HEUTE)


def _werte(v):
    erg = R.vorschlaege(v)
    werte = {k: e["value"] for k, e in erg["felder"].items()}
    # service_book speichert klein ("ja"/"nein")
    werte = {k: ({"ja": "Ja", "nein": "Nein"}.get(w, w) if k == "service_book" else w) for k, w in werte.items()}
    return werte, erg


def _pruefe(werte, erg, feld, soll, text):
    ist = werte.get(feld, "leer")
    if soll == "nicht_ja":
        assert ist != "Ja", (text, feld, erg)
    elif soll == "nicht_nein":
        assert ist != "Nein", (text, feld, erg)
    else:
        assert ist == soll, (text, feld, soll, erg)
    if ist == "leer" and soll in ("leer", "nicht_ja", "nicht_nein"):
        assert any(HINWEIS_STICHWORT[feld] in h for h in erg["hinweise"]), (text, feld, erg)


# ------------------------------------------------ 1. Satzkatalog komplett
def _korpus_faelle():
    for gruppe, feld in KORPUS_FELD.items():
        for satz, soll in KORPUS[gruppe]:
            v = {"description": satz}
            if "(Portalfeld hu=" in satz:
                text, portal = satz.split(" (Portalfeld hu=")
                v = {"description": text, "hu": portal.rstrip(")")}
            yield pytest.param(v, feld, soll, id=f"{gruppe}: {satz}")


def test_korpus_vollstaendig_geladen():
    assert sum(len(KORPUS[g]) for g in KORPUS_FELD) == 71
    assert len(list(_korpus_faelle())) == 71


@pytest.mark.parametrize("v, feld, soll", list(_korpus_faelle()))
def test_satzkatalog(v, feld, soll):
    werte, erg = _werte(v)
    _pruefe(werte, erg, feld, soll, v["description"])


# ------------------------------------------------ 2. eigene Saetze
EIGENE = [
    # --- Unfall: eindeutig
    ("Unfallfrei, Nichtraucher, Garagenwagen", {"accident_free": "Ja"}),
    ("Fahrzeug ist unfallfrei!", {"accident_free": "Ja"}),
    ("Unfallfrei: Ja", {"accident_free": "Ja"}),
    ("Unfallfrei: Nein", {"accident_free": "Nein"}),
    ("Unfall frei", {"accident_free": "Ja"}),
    ("kein Unfallwagen", {"accident_free": "Ja"}),
    ("Keine Unfälle, keine Mängel", {"accident_free": "Ja"}),
    ("Kein Rost kein Unfall", {"accident_free": "Ja"}),
    ("unfallfrei, kein Hagelschaden", {"accident_free": "Ja"}),
    ("Unfallfrei – Nichtraucher – Garage", {"accident_free": "Ja"}),
    ("Hatte einen leichten Unfallschaden hinten, fachgerecht repariert", {"accident_free": "Nein"}),
    ("reparierter Unfallschaden vorne rechts", {"accident_free": "Nein"}),
    ("Unfallschaden: ja", {"accident_free": "Nein"}),
    ("Nicht unfallfrei, Heckschaden repariert", {"accident_free": "Nein"}),
    ("NICHT UNFALLFREI", {"accident_free": "Nein"}),
    # --- Unfall: Vorbehalt / Hoerensagen / offen -> leer
    ("Unfallfrei?", {"accident_free": "leer"}),
    ("Unfallfrei bis auf einen kleinen Parkrempler", {"accident_free": "leer"}),
    ("Unfallfrei soweit bekannt", {"accident_free": "leer"}),
    ("Unfallfrei (soweit mir bekannt)", {"accident_free": "leer"}),
    ("In meinem Besitz unfallfrei", {"accident_free": "leer"}),
    ("Bei mir unfallfrei, Vorbesitz unbekannt", {"accident_free": "leer"}),
    ("Vorbesitzer hatte einen Unfall", {"accident_free": "leer"}),
    ("Fahrzeug war nie in einen Unfall verwickelt", {"accident_free": "leer"}),
    ("Das Auto ist optisch unfallfrei", {"accident_free": "leer"}),
    ("Wurde als unfallfrei gekauft", {"accident_free": "leer"}),
    ("Kratzer, sonst unfallfrei", {"accident_free": "leer"}),
    ("Unfallfrei kann ich nicht garantieren", {"accident_free": "leer"}),
    ("unfallfrei? keine Ahnung", {"accident_free": "leer"}),
    ("keine Garantie ob unfallfrei", {"accident_free": "leer"}),
    ("unfallfrei, Hagelschaden am Dach", {"accident_free": "leer"}),
    ("keine Unfallschäden bekannt", {"accident_free": "leer"}),
    ("kein schwerer Unfall", {"accident_free": "leer"}),
    ("Unfallschaden? Nein", {"accident_free": "leer"}),
    ("Klima geht nicht – unfallfrei", {"accident_free": "leer"}),
    # ohne Satzzeichen: "keine" NACH dem Stichwort -> unklar (Ausnahme gilt nur davor)
    ("Keine Unfälle keine Mängel", {"accident_free": "leer"}),
    # Titelschreibung / Grossbuchstaben: Nomen nicht erkennbar -> keine Ausnahme
    ("Nicht Wirklich Unfallfrei", {"accident_free": "leer"}),
    ("KEINE MÄNGEL UNFALLFREI", {"accident_free": "leer"}),
    # --- fahrbereit
    ("Fahrzeug fahrtauglich? Leider nein", {"drivable": "Nein"}),
    ("nicht fahrbereit, Motorschaden", {"drivable": "Nein"}),
    ("fahruntüchtig", {"drivable": "Nein"}),
    ("nicht angemeldet fahrbereit", {"drivable": "Ja"}),
    ("Kein Motorschaden fahrbereit", {"drivable": "Ja"}),
    ("Auto springt nicht an, fahrbereit", {"drivable": "leer"}),
    ("fahrbereit nur mit Starthilfe", {"drivable": "leer"}),
    ("Klima geht nicht fahrbereit", {"drivable": "leer"}),
    ("Fahrbereit, wird aber nur als Export verkauft", {"drivable": "leer"}),
    ("fahrbereit laut Vorbesitzer", {"drivable": "leer"}),
    ("fahrbereit ohne Zulassung", {"drivable": "leer"}),
    ("Nicht–fahrbereit", {"drivable": "leer"}),
    ("unfallfrei und fahrbereit, aber Klima geht nicht", {"accident_free": "Ja", "drivable": "Ja"}),
    ("unfallfrei und fahrbereit aber Klima geht nicht", {"accident_free": "leer", "drivable": "leer"}),
    # --- HU
    ("TÜV bis 05/2027", {"hu_valid": "Ja", "hu_until": "05/2027"}),
    ("TÜV noch bis 05/2027", {"hu_valid": "Ja", "hu_until": "05/2027"}),
    ("TÜV 12.05.2027", {"hu_valid": "Ja", "hu_until": "05/2027"}),
    ("TÜV: 06/27", {"hu_valid": "Ja", "hu_until": "06/2027"}),
    ("HU/AU neu", {"hu_valid": "Ja"}),
    ("TÜV 02/2027 Reifen abgelaufen", {"hu_valid": "Ja", "hu_until": "02/2027"}),
    ("TÜV fällig", {"hu_valid": "Nein"}),
    ("HU überzogen", {"hu_valid": "Nein"}),
    ("TÜV bald fällig", {"hu_valid": "leer"}),
    ("HU 11/2025", {"hu_valid": "leer"}),
    ("TÜV ist noch nicht abgelaufen", {"hu_valid": "leer"}),
    ("TÜV neu machen lassen", {"hu_valid": "leer"}),
    ("HU 09/2026 neu gemacht", {"hu_valid": "leer"}),       # Pruefdatum, nicht "gueltig bis"
    ("HU 10/2026 fällig", {"hu_valid": "leer"}),            # gueltiges Datum + "faellig"
    ("TÜV 03/2028, HU abgelaufen", {"hu_valid": "leer"}),
    ("TÜV neu 03/2025", {"hu_valid": "leer"}),
    # --- Scheckheft ("Ja, lueckenlos" nur bei lueckenlos/vollstaendig)
    ("Scheckheft lückenlos", {"service_book": "Ja"}),
    ("lückenlose Servicehistorie", {"service_book": "Ja"}),
    ("Kein Scheckheft", {"service_book": "Nein"}),
    ("Scheckheft komplett", {"service_book": "leer"}),
    ("Scheckheft leider nicht lückenlos", {"service_book": "leer"}),
    ("lückenlos scheckheftgepflegt bis 2019", {"service_book": "leer"}),
    ("lückenlos scheckheftgepflegt (laut Vorbesitzer)", {"service_book": "leer"}),
    ("Scheckheft unvollständig", {"service_book": "leer"}),
    ("Scheckheft vollständig? Nein", {"service_book": "leer"}),
    # --- ganze Inserate / Stichpunktlisten
    ("- unfallfrei\n- scheckheftgepflegt\n- TÜV neu",
     {"accident_free": "Ja", "hu_valid": "Ja", "service_book": "leer"}),
    ("Verkaufe meinen Golf 7, 1. Hand, unfallfrei, TÜV 08/2027, 2 Schlüssel, Winterreifen dabei.",
     {"accident_free": "Ja", "hu_valid": "Ja", "hu_until": "08/2027", "schluessel_anzahl": "2", "tires": "8-fach"}),
    ("BMW 320d Touring\nTÜV neu\nunfallfrei\nkeine Mängel\nscheckheftgepflegt\nPreis VB",
     {"accident_free": "Ja", "hu_valid": "Ja", "service_book": "leer"}),
    ("Opel Corsa zu verkaufen. Leider hatte er einen Unfall hinten links. Fahrbereit. TÜV bis 03/2027",
     {"accident_free": "leer", "drivable": "Ja", "hu_valid": "Ja", "hu_until": "03/2027"}),
    ("Unfallfrei, TÜV ist abgelaufen, fahrbereit, kein Scheckheft",
     {"accident_free": "Ja", "hu_valid": "Nein", "drivable": "Ja", "service_book": "Nein"}),
    ("Auto ist technisch einwandfrei, unfallfrei, fahrbereit, HU 10/2028",
     {"accident_free": "Ja", "drivable": "Ja", "hu_valid": "Ja", "hu_until": "10/2028"}),
]


def test_eigene_saetze_mindestens_40():
    assert len(EIGENE) >= 40


@pytest.mark.parametrize("text, soll", EIGENE, ids=[t for t, _ in EIGENE])
def test_eigene_saetze(text, soll):
    werte, erg = _werte({"description": text})
    for feld, wert in soll.items():
        if feld in HINWEIS_STICHWORT:
            _pruefe(werte, erg, feld, wert, text)
        else:
            assert werte.get(feld) == wert, (text, feld, erg)


def test_stichpunkte_in_ausstattung_und_maengeln():
    werte, erg = _werte({"features": ["Unfallfrei", "Nichtraucher", "Scheckheft lückenlos"]})
    assert werte == {"accident_free": "Ja", "service_book": "Ja"}, erg
    werte, erg = _werte({"description": "fahrbereit", "known_defects": ["Unfallschaden hinten links"]})
    assert werte == {"accident_free": "Nein", "drivable": "Ja"}, erg
    # jede Zeile ist ein eigener Satzteil
    werte, erg = _werte({"description": "Unfallfrei.\nNicht fahrbereit."})
    assert werte == {"accident_free": "Ja", "drivable": "Nein"}, erg


# ------------------------------------------------ 3. Portalfeld + Text
@pytest.mark.parametrize("v, feld", [
    ({"accident_damaged": False, "description": "Das Fahrzeug ist keinesfalls unfallfrei"}, "accident_free"),
    ({"accident_damaged": False, "description": "Der Wagen ist keineswegs unfallfrei"}, "accident_free"),
    ({"roadworthy": True, "description": "Auto ist leider keinesfalls fahrbereit"}, "drivable"),
    ({"roadworthy": True, "description": "Fahrzeug keinesfalls fahrbereit"}, "drivable"),
    ({"hu": "10/2027", "description": "TÜV ist seit kurzem abgelaufen"}, "hu_valid"),
    ({"hu": "10/2027", "description": "HU ist im März abgelaufen"}, "hu_valid"),
    ({"hu": "10/2027", "description": "TÜV 05/2027"}, "hu_valid"),
    ({"hu": "03/2026", "description": "TÜV neu"}, "hu_valid"),
    ({"hu": "Neu", "description": "HU vor kurzem abgelaufen"}, "hu_valid"),
])
def test_portal_und_text_widersprechen_sich_leer_mit_hinweis(v, feld):
    werte, erg = _werte(v)
    assert feld not in werte and "hu_until" not in werte, erg
    assert any("widersprüchlich" in h for h in erg["hinweise"]), erg


def test_portal_und_text_passen():
    werte, _ = _werte({"hu": "10/2027", "description": "TÜV neu"})
    assert werte == {"hu_valid": "Ja", "hu_until": "10/2027"}
    werte, _ = _werte({"hu": "10/2027", "description": "TÜV 10/2027"})
    assert werte == {"hu_valid": "Ja", "hu_until": "10/2027"}
    werte, _ = _werte({"accident_damaged": False, "description": "keinerlei Unfälle"})
    assert werte == {"accident_free": "Ja"}
    werte, _ = _werte({"roadworthy": False, "description": "nicht-fahrbereit"})
    assert werte == {"drivable": "Nein"}


def test_uhr_wird_benutzt(monkeypatch):
    # dieselbe HU gilt am 28.09. noch, am 01.10. nicht mehr
    werte, _ = _werte({"description": "HU 09/2026"})
    assert werte == {"hu_valid": "Ja", "hu_until": "09/2026"}
    monkeypatch.setattr(R, "_heute_berlin", lambda: date(2026, 10, 1))
    werte, erg = _werte({"description": "HU 09/2026"})
    assert werte == {} and any("HU abgelaufen (09/2026)" in h for h in erg["hinweise"])


def test_vorschlaege_werfen_nie_und_regeln_fallen_einzeln_aus(monkeypatch):
    def kaputt(*a, **k):
        raise RuntimeError("Test")
    monkeypatch.setattr(R, "hu", kaputt)
    werte, _ = _werte({"description": "unfallfrei, TÜV neu"})
    assert werte == {"accident_free": "Ja"}, "eine kaputte HU-Regel darf die anderen nicht mitnehmen"
