# -*- coding: utf-8 -*-
"""Runde inserat5 (28.09.2026): Pruefung Runde 4 der Inserat-Vorschlaege.

Regel ab jetzt: AUS DEM INSERAT WIRD NICHTS MEHR UNGEFRAGT IN DEN VERTRAG
GESCHRIEBEN — auch Bereifung und Schluesselanzahl sind im Dialog nur noch
Vorschlaege mit "Übernehmen". Die Regeln dafuer beachten jetzt Verneinung und
Frage:

1. Bereifung: "Keine Winterreifen dabei", "Winterreifen dabei? Nein",
   "Sommer- und Winterreifen gesucht" ergaben 8-fach, "4-fach Airbags" 4-fach.
2. Schluessel: "2 Schlüssel? Nein, nur einer" ergab 2.
3. HU-Angebot im Folgesatzteil mit Praeposition (", auf Wunsch", ", gegen
   Aufpreis", ", nach Absprache", ...) und "TÜV neu (letztes Jahr)/vor 2
   Jahren/bei Abholung/mit Mängelbericht" ergaben "HU: Ja".
4. Kappung je Teil (Beschreibung, Ausstattung, bekannte Maengel) — ein Mangel
   am Ende fiel weg.
5. Klar erkennbare Faelle der 83er-Probe: Symbol-Verneinungen, "Scheint",
   "Theoretisch", "naja", Antriebsmangel im Folgesatzteil.

Soll: "Ja"/"Nein"/"leer"/Wert exakt, "nicht_ja" = alles ausser Ja. Heute fest
28.09.2026. Ohne KI, ohne DB, ohne Netz.
"""
import sys
from datetime import date
from pathlib import Path

import pytest

BACKEND = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND))

from ai import inserat_regeln as R  # noqa: E402

HEUTE = date(2026, 9, 28)
U, F, H, T, K = "accident_free", "drivable", "hu_valid", "tires", "schluessel_anzahl"
HINWEIS_STICHWORT = {U: "Unfall", F: "Fahr", H: "HU", T: "Bereifung", K: "Schlüsselanzahl"}


@pytest.fixture(autouse=True)
def _uhr(monkeypatch):
    monkeypatch.setattr(R, "_heute_berlin", lambda: HEUTE)


def _werte(v):
    v = v if isinstance(v, dict) else {"description": v}
    erg = R.vorschlaege(v)
    return {k: e["value"] for k, e in erg["felder"].items()}, erg


def _pruefe(v, soll, hinweis=True):
    werte, erg = _werte(v)
    for feld, s in soll.items():
        ist = werte.get(feld, "leer")
        if s == "nicht_ja":
            assert ist not in ("Ja", "8-fach", "4-fach"), (v, feld, erg)
        else:
            assert ist == s, (v, feld, s, erg)
        if hinweis and s in ("leer", "nicht_ja") and ist == "leer":
            assert any(HINWEIS_STICHWORT[feld] in h for h in erg["hinweise"]), (v, feld, erg)


# ------------------------------------------------ 1. Bereifung
BEREIFUNG = [
    ("Keine Winterreifen dabei", {T: "leer"}),
    ("Winterreifen dabei? Nein", {T: "leer"}),
    ("Winterreifen dabei?", {T: "leer"}),
    ("Sommer- und Winterreifen gesucht", {T: "leer"}),
    ("Winterreifen dabei, leider nicht", {T: "leer"}),
    ("4-fach bereift, Winterreifen dabei", {T: "leer"}),          # widerspruechlich
]
BEREIFUNG_OHNE_HINWEIS = [
    ("4-fach Airbags", {T: "leer"}),
    ("8-fach elektrisch verstellbare Sitze", {T: "leer"}),
    ("Klima, 4-fach Fensterheber", {T: "leer"}),
    ("Winterreifen nicht dabei", {T: "leer"}),                    # kein Muster "Winterreifen dabei"
    ("Winterreifen gegen Aufpreis dabei", {T: "leer"}),
]
BEREIFUNG_JA = [
    ("Winterreifen dabei", {T: "8-fach"}),
    ("Sommer- und Winterreifen auf Alu", {T: "8-fach"}),
    ("8-fach bereift auf Alufelgen", {T: "8-fach"}),
    ("Bereifung: 8-fach", {T: "8-fach"}),
    ("2 Sätze Reifen", {T: "8-fach"}),
    ("4-fach bereift", {T: "4-fach"}),
    ("4-fach Allwetterreifen, neu", {T: "4-fach"}),
    ("Keine Mängel, Winterreifen dabei", {T: "8-fach"}),
]


@pytest.mark.parametrize("v, soll", BEREIFUNG, ids=[t for t, _ in BEREIFUNG])
def test_bereifung_verneint_oder_frage(v, soll):
    _pruefe(v, soll)


@pytest.mark.parametrize("v, soll", BEREIFUNG_OHNE_HINWEIS, ids=[t for t, _ in BEREIFUNG_OHNE_HINWEIS])
def test_bereifung_nur_mit_reifenbezug(v, soll):
    _pruefe(v, soll, hinweis=False)
    _, erg = _werte(v)
    assert not any("Bereifung" in h for h in erg["hinweise"]), erg


@pytest.mark.parametrize("v, soll", BEREIFUNG_JA, ids=[t for t, _ in BEREIFUNG_JA])
def test_bereifung_eindeutig(v, soll):
    _pruefe(v, soll)


# ------------------------------------------------ 2. Schluessel
SCHLUESSEL = [
    ("2 Schlüssel? Nein, nur einer", {K: "leer"}),
    ("Zweitschlüssel dabei? Nein", {K: "leer"}),
    ("Ein Schlüssel fehlt", {K: "leer"}),
    ("2 Schlüssel vorhanden, laut Vorbesitzer", {K: "leer"}),
    ("Zwei Schlüssel (nicht original)", {K: "leer"}),
]
SCHLUESSEL_JA = [
    ("2 Schlüssel", {K: "2"}),
    ("Nur ein Schlüssel vorhanden", {K: "1"}),
    ("Zweitschlüssel fehlt leider", {K: "1"}),
    ("kein Zweitschlüssel", {K: "1"}),
    ("inkl. Zweitschlüssel", {K: "2"}),
    ("drei Schlüssel dabei", {K: "3"}),
    ("Keine Mängel, 2 Schlüssel", {K: "2"}),
]


@pytest.mark.parametrize("v, soll", SCHLUESSEL, ids=[t for t, _ in SCHLUESSEL])
def test_schluessel_verneint_oder_frage(v, soll):
    _pruefe(v, soll)


@pytest.mark.parametrize("v, soll", SCHLUESSEL_JA, ids=[t for t, _ in SCHLUESSEL_JA])
def test_schluessel_eindeutig(v, soll):
    _pruefe(v, soll)


def test_schluessel_portalfeld_bleibt():
    werte, _ = _werte({"keys_count": "2", "description": "2 Schlüssel? Nein, nur einer"})
    assert werte[K] == "2"


# ------------------------------------------------ 3. HU-Angebot im Folgesatzteil
HU_UNKLAR = [
    "TÜV neu, auf Wunsch", "TÜV neu, gegen Aufpreis", "TÜV neu, nach Absprache", "TÜV neu, wenn gewünscht",
    "TÜV neu, dafür Aufpreis", "TÜV neu (letztes Jahr)", "TÜV neu vor 2 Jahren", "TÜV neu bei Abholung",
    "TÜV neu nur bei Übernahme der Kosten", "TÜV neu mit Mängelbericht", "TÜV neu wenn gewünscht",
    "HU neu, auf Wunsch des Käufers", "TÜV 10/2027, auf Wunsch neu", "TÜV neu, sofern Käufer zahlt",
    "TÜV neu – auf Wunsch", "TÜV neu. Auf Wunsch.",
]
HU_JA = [
    "TÜV neu, sonst alles top", "TÜV neu, Probefahrt nach Absprache", "TÜV neu, Abholung gegen Aufpreis möglich",
    "TÜV neu, letzte Inspektion bei 90.000 km", "TÜV neu, keine TÜV-relevanten Mängel", "TÜV neu 09/2028",
    "TÜV neu gemacht", "TÜV bis 05/2027, Besichtigung nach Absprache",
]


@pytest.mark.parametrize("text", HU_UNKLAR)
def test_hu_angebot_ist_kein_ja(text):
    _pruefe(text, {H: "leer"})


@pytest.mark.parametrize("text", HU_JA)
def test_hu_eindeutig_bleibt_ja(text):
    _pruefe(text, {H: "Ja"})


# ------------------------------------------------ 4. Kappung je Teil
def test_mangel_am_ende_der_beschreibung_faellt_nicht_weg():
    lang = "Unfallfrei. " + "Top Zustand, gepflegt. " * 1100 + "Unfallschaden vorne."   # ~25.000 Zeichen
    assert len(lang) > 25_000
    werte, erg = _werte(lang)
    assert werte.get(U) != "Ja", erg


def test_bekannte_maengel_hinter_langer_beschreibung_zaehlen():
    lang = "Unfallfrei. " + "Top Zustand, gepflegt. " * 1100
    werte, _ = _werte({"description": lang})
    assert werte.get(U) == "Ja"
    werte, erg = _werte({"description": lang, "known_defects": ["Unfallschaden vorne links"]})
    assert werte.get(U) != "Ja", erg
    # auch hinter einer langen Ausstattungsliste
    werte, erg = _werte({"description": "Unfallfrei", "features": [f"Ausstattung {i}" for i in range(3000)],
                         "known_defects": ["Unfallschaden vorne links"]})
    assert werte.get(U) != "Ja", erg


def test_kappen_behaelt_anfang_und_ende():
    s = "A" * 30_000 + "ENDE"
    k = R._kappen(s, 20_000)
    assert len(k) < 20_010 and k.startswith("A") and k.endswith("ENDE")
    assert R._kappen("kurz", 20_000) == "kurz"


# ------------------------------------------------ 5. klar erkennbare Faelle der 83er-Probe
PROBE_UNKLAR = [
    ("Unfallfrei 🚫", U), ("Unfallfrei 👎", U), ("Unfallfrei [ ]", U), ("Unfallfrei (?)", U), ("Unfallfrei ☐", U),
    ("Scheint unfallfrei", U), ("Unfallfrei … naja", U), ("Unfallfrei - aber wer weiß das schon", U),
    ("Fahrbereit, Zahnriemen gerissen", F), ("Fahrbereit, Getriebe hinüber", F), ("Fahrbereit (Notlauf)", F),
    ("Fahrbereit, Bremsen fest", F), ("Theoretisch fahrbereit", F), ("Fahrbereit 👎", F),
]
PROBE_JA = [
    ("Unfallfrei [x]", U), ("Unfallfrei, Nichtraucher", U), ("Fahrbereit, Bremsen neu", F),
    ("Zahnriemen neu, fahrbereit", F), ("Kein Notlauf, fahrbereit", F), ("Fahrbereit, Getriebe schaltet sauber", F),
]


@pytest.mark.parametrize("text, feld", PROBE_UNKLAR, ids=[t for t, _ in PROBE_UNKLAR])
def test_probe_unklar(text, feld):
    _pruefe(text, {feld: "nicht_ja"}, hinweis=False)


@pytest.mark.parametrize("text, feld", PROBE_JA, ids=[t for t, _ in PROBE_JA])
def test_probe_gegenrichtung_bleibt_ja(text, feld):
    _pruefe(text, {feld: "Ja"})
