# -*- coding: utf-8 -*-
"""Runde inserat4 (28.09.2026): Pruefung Runde 3 der Inserat-Vorschlaege.

Entscheidung Auftraggeber: Zusicherungen (HU, Scheckheft, unfallfrei,
fahrbereit, EU-Import) sind im Vertragsdialog nur noch VORSCHLAEGE mit
Fundstelle und "Übernehmen"-Knopf. Die Vorschlaege sollen trotzdem so gut wie
moeglich sein — hier die Saetze des Pruefers aus Runde 3 (149 Saetze, 156
Feldpruefungen, vorher 80 Abweichungen) plus die Punkte aus dem Auftrag:

1. Umgangssprache/Formular ("Unfallfrei: nö", "net fahrbereit", "k.A.", "❌").
2. Vorbehalt im Folgesatzteil ("Unfallfrei, laut Vorbesitzer").
3. Hoerensagen und Zeitraeume ("sagt der Vorbesitzer", "Seit 2019 unfallfrei").
4. Unfall-Komposita ("Auffahrunfall") und "keine größeren Unfälle".
5. fahrbereit in Vergangenheit/Bedingung, "kaputt".
6. HU: "TÜV neu" als Angebot/Plan, "TÜV neu gemacht 09/2024", "fällig" mit
   Zeitangabe, Datum nach "abgelaufen", "TÜV-relevante Mängel".
7. Scheckheft mit Einschraenkung (Klammer/Folgesatzteil), EU-Import.
8. Ausfall einer Regel wird protokolliert, Laufzeit linear, Route im Thread.

Soll: "Ja"/"Nein"/"leer" exakt, "nicht_ja" = Nein oder leer, "nicht_nein" =
Ja oder leer. Heute fest 28.09.2026. Ohne KI, ohne Netz.
"""
import asyncio
import importlib
import logging
import sys
import time
from datetime import date
from pathlib import Path

import pytest

BACKEND = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND))

from ai import inserat_regeln as R  # noqa: E402

HEUTE = date(2026, 9, 28)
U, F, S, H, E = "accident_free", "drivable", "service_book", "hu_valid", "eu_import"
HINWEIS_STICHWORT = {U: "Unfall", F: "Fahr", H: "HU", S: "checkheft"}


@pytest.fixture(autouse=True)
def _uhr(monkeypatch):
    monkeypatch.setattr(R, "_heute_berlin", lambda: HEUTE)


def _werte(v):
    erg = R.vorschlaege(v)
    werte = {k: e["value"] for k, e in erg["felder"].items()}
    werte = {k: ({"ja": "Ja", "nein": "Nein"}.get(w, w) if k == S else w) for k, w in werte.items()}
    return werte, erg


def _pruefe(v, soll):
    v = v if isinstance(v, dict) else {"description": v}
    werte, erg = _werte(v)
    for feld, s in soll.items():
        ist = werte.get(feld, "leer")
        if s == "nicht_ja":
            assert ist != "Ja", (v, feld, erg)
        elif s == "nicht_nein":
            assert ist != "Nein", (v, feld, erg)
        else:
            assert ist == s, (v, feld, s, erg)
        if s == "leer" and feld in HINWEIS_STICHWORT:
            # leer heisst: der Sucher sieht, warum (Hinweis mit Fundstelle)
            assert any(HINWEIS_STICHWORT[feld] in h for h in erg["hinweise"]), (v, feld, erg)


# ------------------------------------------------ Pruefer-Saetze Runde 3 (1:1)
PRUEFER = [
    # Unfall: nachgestellte Vorbehalte nach Komma
    ("Unfallfrei, laut Vorbesitzer", {U: "leer"}),
    ("Unfallfrei, soweit bekannt", {U: "leer"}),
    ("unfallfrei, soweit ich weiß", {U: "leer"}),
    ("Unfallfrei, vermutlich", {U: "leer"}),
    ("Unfallfrei, so der Vorbesitzer", {U: "leer"}),
    ("Unfallfrei, leider nicht", {U: "nicht_ja"}),
    ("Unfallfrei. Leider nicht.", {U: "nicht_ja"}),
    ("Unfallfrei (sagt der Vorbesitzer)", {U: "leer"}),
    ("Unfallfrei sagt der Vorbesitzer", {U: "leer"}),
    ("Unfallfrei gemäß Vorbesitzer", {U: "leer"}),
    ("Unfallfrei gem. Vorbesitzer", {U: "leer"}),
    ("Unfallfrei (Vorbesitzer-Angabe)", {U: "leer"}),
    ("Vorbesitzer meinte unfallfrei", {U: "leer"}),
    # Unfall: Zeitraum / Besitz
    ("Seit 2019 unfallfrei", {U: "leer"}),
    ("Unfallfrei seit Kauf", {U: "leer"}),
    ("Bei uns unfallfrei", {U: "leer"}),
    ("In meiner Hand unfallfrei", {U: "leer"}),
    ("Bis letztes Jahr unfallfrei", {U: "leer"}),
    ("Seit Erstzulassung unfallfrei", {U: "Ja"}),
    # Unfall: Umgangssprache / Tippfehler
    ("nich unfallfrei", {U: "nicht_ja"}),
    ("Auto is net unfallfrei", {U: "nicht_ja"}),
    ("ned unfallfrei, Heck war dran", {U: "nicht_ja"}),
    ("garnicht unfallfrei", {U: "nicht_ja"}),
    ("gar nicht unfallfrei", {U: "nicht_ja"}),
    ("überhaupt nicht unfallfrei", {U: "nicht_ja"}),
    ("Unfallfrei: nö", {U: "nicht_ja"}),
    ("Unfallfrei: nee", {U: "nicht_ja"}),
    ("unfallfrei ne", {U: "nicht_ja"}),
    ("Unfallfrei: k.A.", {U: "nicht_ja"}),
    ("Unfallfrei: ?", {U: "nicht_ja"}),
    ("Unfallfrei: -", {U: "nicht_ja"}),
    ("Unfallfrei jein", {U: "nicht_ja"}),
    ("Unfallfrei ❌", {U: "nicht_ja"}),
    ("UNFALLFREI NEIN", {U: "nicht_ja"}),
    ("unfalfrei", {U: "nicht_nein"}),
    ("Unfall frei ja", {U: "nicht_nein"}),
    # Unfall: Unfall-Komposita
    ("Unfallfrei. Kleiner Auffahrunfall 2021 hinten, repariert.", {U: "nicht_ja"}),
    ("Unfallfrei, Wildunfall 2022 fachgerecht repariert", {U: "nicht_ja"}),
    ("Unfallfrei, Unfallreparatur 2020 beim Händler", {U: "nicht_ja"}),
    ("Unfallfrei (Stoßstange neu lackiert nach Parkrempler)", {U: "nicht_ja"}),
    # Unfall: Nomen-Negativ in Umgangssprache
    ("Unfallschaden? Nö", {U: "nicht_nein"}),
    ("Unfallschaden? Fehlanzeige!", {U: "nicht_nein"}),
    ("Unfallschaden: nee", {U: "nicht_nein"}),
    ("Unfallwagen? Nö!", {U: "nicht_nein"}),
    ("Unfallschäden: keine", {U: "nicht_nein"}),
    ("Unfälle: keine", {U: "nicht_nein"}),
    ("Kein Unfallwagen", {U: "Ja"}),
    ("ist KEIN Unfallwagen!!", {U: "Ja"}),
    ("Unfallschaden vorne links, repariert", {U: "Nein"}),
    # Unfall: Nomen-Ausnahme gegen Missbrauch
    ("Keine Ahnung ob unfallfrei", {U: "leer"}),
    ("Keine Garantie auf unfallfrei", {U: "leer"}),
    ("Kein Nachweis unfallfrei", {U: "nicht_nein"}),
    ("Keine Haustiere Nicht Unfallfrei", {U: "nicht_ja"}),
    ("Nichtraucher nicht unfallfrei", {U: "Nein"}),
    ("keine Mängel, nicht unfallfrei", {U: "Nein"}),
    ("Kein Rost kein Tausch unfallfrei", {U: "Ja"}),
    ("unfallfrei nicht fahrbereit", {U: "nicht_nein", F: "Nein"}),
    ("nicht unfallfrei, aber fahrbereit", {U: "Nein", F: "Ja"}),
    ("Keine Unfälle keine Mängel", {U: "nicht_nein"}),
    # Unfall: Stichpunktliste
    ("- Unfallfrei\n- Nichtraucher\n- keine Tiere\n- TÜV neu", {U: "Ja", H: "Ja"}),
    ("• unfallfrei • fahrbereit • 2 Schlüssel", {U: "Ja", F: "Ja"}),
    ("Unfallfrei / Fahrbereit / TÜV 10/2027", {U: "Ja", F: "Ja", H: "Ja"}),
    # fahrbereit
    ("Stand 2 Jahre, war beim Abstellen fahrbereit", {F: "leer"}),
    ("Bis vor kurzem fahrbereit", {F: "leer"}),
    ("War bis letzte Woche fahrbereit", {F: "leer"}),
    ("Nach Batteriewechsel fahrbereit", {F: "leer"}),
    ("Wäre fahrbereit wenn die Batterie getauscht wird", {F: "leer"}),
    ("fahrbereit, wenn man die Batterie lädt", {F: "leer"}),
    ("Fahrbereit: nö", {F: "nicht_ja"}),
    ("Fahrbereit: nee", {F: "nicht_ja"}),
    ("net fahrbereit", {F: "nicht_ja"}),
    ("ned fahrbereit", {F: "nicht_ja"}),
    ("nich fahrbereit", {F: "nicht_ja"}),
    ("Fahrbereit ❌", {F: "nicht_ja"}),
    ("Motor kaputt, fahrbereit", {F: "nicht_ja"}),
    ("Fahrbereit, Kupplung kaputt", {F: "nicht_ja"}),
    ("Motor läuft nicht fahrbereit", {F: "nicht_ja"}),
    ("Klima geht nicht, fahrbereit", {F: "Ja"}),
    ("Auto ist fahrbereit, TÜV abgelaufen", {F: "Ja", H: "Nein"}),
    ("fahrbereit (nur Kurzstrecke)", {F: "leer"}),
    ("fahrbereit, Motor defekt", {F: "leer"}),
    ("Auto Ist Nicht Mehr Fahrbereit", {F: "Nein"}),
    ("Fahrbereit? Nein", {F: "Nein"}),
    # HU
    ("Keine TÜV-relevanten Mängel", {H: "nicht_nein"}),
    ("keine HU-relevanten Mängel, sonst top", {H: "nicht_nein"}),
    ("TÜV neu, keine TÜV-relevanten Mängel", {H: "nicht_nein"}),
    ("Ohne TÜV-Mängel", {H: "nicht_nein"}),
    ("Kein HU-Bericht vorhanden, TÜV 10/2027", {H: "nicht_nein"}),
    ("TÜV im Oktober fällig", {H: "nicht_nein"}),
    ("TÜV nächsten Monat fällig", {H: "nicht_nein"}),
    ("HU Ende Oktober fällig", {H: "nicht_nein"}),
    ("TÜV fällig", {H: "nicht_ja"}),
    ("HU fällig 10/2026", {H: "nicht_nein"}),
    ("Auf Wunsch TÜV neu", {H: "leer"}),
    ("Neuer TÜV auf Wunsch", {H: "leer"}),
    ("TÜV neu gegen Aufpreis", {H: "leer"}),
    ("TÜV neu möglich", {H: "leer"}),
    ("TÜV neu nach Absprache", {H: "leer"}),
    ("TÜV neu kommt vor Übergabe", {H: "leer"}),
    ("TÜV neu geplant", {H: "leer"}),
    ("TÜV neu beantragt", {H: "leer"}),
    ("TÜV neu wird bei Kauf gemacht", {H: "leer"}),
    ("TÜV neu gemacht 09/2024", {H: "nicht_ja"}),
    ("TÜV neu seit 08/2024", {H: "nicht_ja"}),
    ("Letzter TÜV 10/2024", {H: "nicht_ja"}),
    ("nächster TÜV 10/2027", {H: "Ja"}),
    ("TÜV 10/27", {H: "Ja"}),
    ("TÜV bis 05/2025", {H: "nicht_ja"}),
    ("tüv abgel.", {H: "Nein"}),
    ("TÜV 2 Monate überzogen", {H: "Nein"}),
    ("TÜV nicht abgelaufen", {H: "nicht_nein"}),
    ("Reifen abgelaufen TÜV neu", {H: "Ja"}),
    ("TÜV neu Reifen abgelaufen", {H: "Ja"}),
    ("TÜV abgelaufen reifen neu", {H: "Nein"}),
    ("Kein TÜV mehr", {H: "Nein"}),
    ("Kein neuer TÜV", {H: "nicht_ja"}),
    ("TÜV 10/2027, laut Vorbesitzer", {H: "nicht_nein"}),
    ("Tüv neu, Bremsen neu, Öl neu", {H: "Ja"}),
    ("TÜV: 12/26", {H: "Ja"}),
    ("HU 09/2026", {H: "Ja"}),
    ("HU 08/2026", {H: "nicht_ja"}),
    # Scheckheft
    ("lückenlos scheckheftgepflegt (bis 2019)", {S: "leer"}),
    ("Lückenlos scheckheftgepflegt, ab 2019 freie Werkstatt", {S: "leer"}),
    ("Scheckheft lückenlos, letzte Inspektion fehlt", {S: "leer"}),
    ("Lückenlos scheckheftgepflegt, Scheckheft leider verloren", {S: "leer"}),
    ("Lückenlos scheckheftgepflegt die letzten 3 Jahre", {S: "leer"}),
    ("Scheckheft lückenlos bis 120.000 km", {S: "leer"}),
    ("Kein lückenloses Scheckheft", {S: "nicht_ja"}),
    ("Scheckheft nicht lückenlos", {S: "nicht_ja"}),
    ("Scheckheft fehlt nicht", {S: "nicht_nein"}),
    ("lückenloses Scheckheft bei VW", {S: "Ja"}),
    ("Scheckheft vollständig? Nein", {S: "nicht_ja"}),
    ("ohne Scheckheft", {S: "Nein"}),
    # EU-Import
    ("EU-Import ausgeschlossen", {E: "leer"}),
    ("Reimport: nö", {E: "leer"}),
    ("Kein EU-Import, deutsches Fahrzeug", {E: "leer"}),
    ("EU-Import aus Italien", {E: "Ja"}),
    # Portal-Kombinationen
    ({"description": "Unfallfrei, laut Vorbesitzer", "accident_damaged": False}, {U: "leer"}),
    ({"description": "Unfallfrei: nö", "accident_damaged": True}, {U: "nicht_ja"}),
    ({"description": "Keine TÜV-relevanten Mängel", "hu": "10/2027"}, {H: "nicht_nein"}),
    ({"description": "TÜV im Oktober fällig", "hu": "10/2026"}, {H: "nicht_nein"}),
    ({"description": "Auf Wunsch TÜV neu", "hu": "05/2026"}, {H: "nicht_ja"}),
    ({"description": "TÜV neu gemacht 09/2024"}, {H: "nicht_ja"}),
    ({"description": "", "hu": "10/2027"}, {H: "Ja"}),
    ({"description": "Fahrbereit: nö", "roadworthy": True}, {F: "nicht_ja"}),
    ({"description": "net fahrbereit", "roadworthy": False}, {F: "Nein"}),
    ({"description": "Stand 2 Jahre, war beim Abstellen fahrbereit", "roadworthy": True}, {F: "leer"}),
    ({"description": "unfallfrei", "zustand_portal": "Unfallfahrzeug, repariert"}, {U: "nicht_ja"}),
    ({"description": "Unfallschaden? Nö", "accident_damaged": False}, {U: "nicht_nein"}),
]


def test_pruefer_satzanzahl():
    assert len(PRUEFER) == 149
    assert sum(len(s) for _, s in PRUEFER) == 156


@pytest.mark.parametrize("v, soll", PRUEFER, ids=[f"p{i:03d}" for i in range(len(PRUEFER))])
def test_pruefer_saetze_runde3(v, soll):
    _pruefe(v, soll)


# ------------------------------------------------ weitere Saetze aus dem Auftrag
AUFTRAG = [
    ("Unfallfrei, Vorbesitz unbekannt", {U: "leer"}),
    ("Unfallfrei (seit Kauf)", {U: "leer"}),
    ("Unfallfrei, soweit bekannt. TÜV neu", {U: "leer", H: "Ja"}),
    ("Unfallfrei k.A.", {U: "leer"}),
    ("Unfallfrei ✗", {U: "nicht_ja"}),
    ("Unfallschaden? Fehlanzeige", {U: "nicht_nein"}),
    ("Unfallschaden: -", {U: "nicht_nein"}),
    ("Unfallschaden?", {U: "nicht_nein"}),
    ("Unfallschaden: ja, hinten links", {U: "Nein"}),
    ("Unfallfrei: ja", {U: "Ja"}),
    ("Unfallfrei: ✓", {U: "Ja"}),
    ("Unfallfrei: x", {U: "Ja"}),
    ("Kein Wildunfall, unfallfrei", {U: "Ja"}),
    ("Keine größeren Unfälle", {U: "leer"}),
    ("HU fällig im November 2026", {H: "leer"}),
    ("TÜV fällig ab 11/2026", {H: "leer"}),
    ("TÜV abgelaufen 10/2027", {H: "leer"}),
    ("TÜV seit 05.2026 abgelaufen", {H: "Nein"}),
    ("TÜV fällig seit 2 Monaten", {H: "Nein"}),
    ("keine HU/AU-Mängel", {H: "nicht_nein"}),
    ("lückenlos scheckheftgepflegt (bei VW bis 2019)", {S: "leer"}),
    ("Scheckheft lückenlos, letzte Inspektion bei 90.000 km", {S: "Ja"}),
    ("EU-Import: nee", {E: "leer"}),
    ("Fahrbereit, Turbo kaputt", {F: "leer"}),
    # Gegenrichtung: gewoehnliche Inserate bleiben eindeutig
    ("Verkaufe meinen Golf. Unfallfrei, fahrbereit, TÜV neu, 2 Schlüssel. Nichtraucherfahrzeug, keine Tiere.",
     {U: "Ja", F: "Ja", H: "Ja"}),
    ("Unfallfrei, Nichtraucher, keine Haustiere, Scheckheft lückenlos bei VW", {U: "Ja", S: "Ja"}),
    ("unfallfrei fahrbereit tüv bis 05/2027 nichtraucher", {U: "Ja", F: "Ja", H: "Ja"}),
    ("Fahrzeug ist unfallfrei und fahrbereit. TÜV neu, sonst alles top.", {U: "Ja", F: "Ja", H: "Ja"}),
    ("Unfallfrei, keine Mängel, fahrbereit, Klima geht nicht", {U: "Ja", F: "Ja"}),
    ("Unfallfrei TÜV bis 05/2027 Nichtraucher", {U: "Ja", H: "Ja"}),
    ("Seit Erstzulassung unfallfrei", {U: "Ja"}),
    ("Fahrbereit, Probefahrt nach Absprache", {F: "Ja"}),
    ("TÜV neu gemacht", {H: "Ja"}),
    ("TÜV neu 09/2028", {H: "Ja"}),
    ("HU abgelaufen, muss neu gemacht werden", {H: "Nein"}),
]


@pytest.mark.parametrize("v, soll", AUFTRAG, ids=[f"a{i:02d}" for i in range(len(AUFTRAG))])
def test_auftrag_saetze(v, soll):
    _pruefe(v, soll)


def test_kleinere_unfaelle_hinweis():
    werte, erg = _werte({"description": "Keine nennenswerten Unfallschäden"})
    assert U not in werte
    assert any("deutet auf kleinere Unfälle" in h for h in erg["hinweise"]), erg


# ------------------------------------------------ Ausfall einer Regel wird protokolliert
def test_regel_ausfall_wird_protokolliert(monkeypatch, caplog):
    def kaputt(*a, **k):
        raise RuntimeError("Regel kaputt")
    monkeypatch.setattr(R, "hu", kaputt)
    with caplog.at_level(logging.ERROR, logger="autohandel"):
        werte, _ = _werte({"description": "unfallfrei, TÜV neu"})
    assert werte == {U: "Ja"}
    treffer = [r for r in caplog.records if "Inserat-Vorschlag" in r.getMessage()]
    assert treffer and treffer[0].exc_info is not None, "Ausfall muss mit Stacktrace im Log stehen"


# ------------------------------------------------ Laufzeit
def _messen(txt):
    for name in ("_analyse", "_ist_stoer"):         # Zwischenspeicher leeren: echte Erstlaufzeit
        f = getattr(R, name, None)
        if hasattr(f, "cache_clear"):
            f.cache_clear()
    t0 = time.perf_counter()
    R.vorschlaege({"description": txt})
    return time.perf_counter() - t0


@pytest.mark.parametrize("stueck", [
    "unfallfrei ",
    "Top Zustand unfallfrei fahrbereit TÜV neu Scheckheft gepflegt keine Mängel ",
    "nicht unfallfrei laut Vorbesitzer fahrbereit keine Unfälle TÜV abgelaufen Reifen ",
])
def test_laufzeit_linear_ohne_satzzeichen(stueck):
    # vorher (f6d2b0e): 10.000 Zeichen 2,3–4,2 s, 33.000 Zeichen 23–48 s
    txt = (stueck * 2000)[:10_000]
    dauer = min(_messen(txt) for _ in range(2))
    assert dauer < 0.4, f"{dauer:.3f} s fuer 10.000 Zeichen"
    lang = (stueck * 5000)[:33_000]
    assert min(_messen(lang) for _ in range(2)) < 0.8


def test_text_wird_begrenzt():
    # inserat5: je Teil gekappt, Anfang UND Ende bleiben (Maengel stehen oft am
    # Schluss) — nur die Mitte eines ueberlangen Texts faellt weg.
    mitte = "Top Zustand " * 1400 + "unfallfrei " + "Top Zustand " * 700   # ~25.000 Zeichen
    werte, _ = _werte({"description": mitte})
    assert U not in werte, "Mitte eines Texts ueber 20.000 Zeichen zaehlt nicht"
    fuell = "Top Zustand " * 2000                    # 24.000 Zeichen
    werte, _ = _werte({"description": "unfallfrei " + fuell})
    assert werte.get(U) == "Ja"
    werte, _ = _werte({"description": fuell + "unfallfrei"})
    assert werte.get(U) == "Ja", "das Ende eines langen Texts zaehlt (inserat5)"


def test_route_rechnet_im_thread(monkeypatch):
    """GET /contracts/vorschlaege blockiert den Event-Loop nicht mehr."""
    C = importlib.import_module("routes.contracts")

    async def _fahrzeug(user, vehicle_id):
        return {"data": {"description": "unfallfrei, TÜV 10/2027"}}
    monkeypatch.setattr(C, "_fahrzeug_fuer_vertrag", _fahrzeug)
    aufrufe = []
    original = asyncio.to_thread

    async def _to_thread(fn, *a, **k):
        aufrufe.append(fn)
        return await original(fn, *a, **k)
    monkeypatch.setattr(C.asyncio, "to_thread", _to_thread)
    erg = asyncio.run(C.vertrag_vorschlaege("V1", user={"id": "U1"}))
    assert aufrufe == [R.vorschlaege] or [getattr(f, "__name__", "") for f in aufrufe] == ["vorschlaege"]
    assert erg["felder"][U]["value"] == "Ja" and erg["felder"]["hu_until"]["value"] == "10/2027"
