# -*- coding: utf-8 -*-
"""Serverpruefung 24.11.2026 (Wunsch Ahmad: "schau alle nicht erkannten Inserate an und pruefe, was wir verbessern
koennen"). Aus den echten Faellen der letzten 30 Tage:

- Kleinanzeigen ohne Modell in der Tabelle ("Seat Arona", "Hyundai i20 ...", "Tiguan 2,0 dti Allrad"): das Modell kommt
  jetzt aus dem Titel — nur ein Katalogtreffer derselben Marke (mobile_service._enhance_generic_model)
- "VW Golf7 1.5TSI ACT Join" unter "Weitere VW": dieselbe Erkennung wie im Programm -> Golf
- Programm: Feld "Andere", Titel "Kia ceed Automatik" -> cee'd / Ceed (werkzeug_erkennung: zuletzt die ersten Woerter);
  ein gekuerzter Treffer ist der schwaechste (anderes Portal, Gelerntes und die zweite Lesung gehen vor)
- Hinweis ohne „None“/„“
- mobile.de-Sicherheitspruefung (Akamai): eigener Text, die Seite wird nicht als Leseseite aufgehoben
- scripts/leseseite_analysieren.py: Foto-Teil (Stopp-Wort vor dem ersten Foto)
"""
import sys
from pathlib import Path

import pytest

BACKEND = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND))
sys.path.insert(0, str(BACKEND / "scripts"))

import browser_helfer as bh  # noqa: E402
import kleinanzeigen_service as KS  # noqa: E402
import mobile_service as ms  # noqa: E402
import werkzeug_erkennung as we  # noqa: E402
import werkzeuge as wz  # noqa: E402

_KA_URL = "https://www.kleinanzeigen.de/s-anzeige/auto/3012345678-216-1234"


def _ka_seite(titel: str, tabelle: str, text: str = "Gepflegtes Auto, Tausche gegen VW Polo.") -> str:
    return f"""
<html><head><title>x</title></head><body>
<h1 id="viewad-title">{titel}</h1>
<h2 id="viewad-price">8.900 €</h2>
<span id="viewad-locality" itemprop="addressLocality">30179 Niedersachsen - Hannover</span>
<div id="viewad-details">
{tabelle}
<div>Kilometerstand</div><div>133.200 km</div>
</div>
<div>Beschreibung</div>
<div>{text}</div>
</body></html>
"""


# ------------------------------------------------------------ Kleinanzeigen / Browser-Helfer
@pytest.mark.parametrize("marke,titel,modell", [
    ("Seat", "Seat Arona", "Arona"),
    ("Hyundai", "Hyundai i20 Benzin, 2013, 133.200 km", "i20"),
    ("Volkswagen", "Tiguan 2,0 dti Allrad", "Tiguan"),
    ("Kia", "Kia Venga 1.6", "Venga"),
    ("Skoda", "Skoda Fabia 1.0 MPI Cool Plus, 60 PS, EZ 6/2020", "Fabia"),
    ("Skoda", "Škoda Superb, 1.4 TSI Scheckheftgepflegt, TÜV NEU 1.Hand", "Superb"),
])
def test_01_leeres_modell_kommt_aus_dem_titel(marke, titel, modell):
    v = KS.parse_kleinanzeigen_html(_KA_URL, _ka_seite(titel, f"<div>Marke</div><div>{marke}</div>"))
    assert v["model_label"] == modell, v["model_label"]
    assert ms.modell_aufgeloest(v) == (True, True)


def test_02_leeres_modell_ohne_katalogtreffer_bleibt_leer():
    # kein Freitext-Rueckfall ("Avila") und kein Modell aus der Beschreibung ("Tausche gegen VW Polo")
    for marke, titel in (("Opel", "Opel Avila"), ("Skoda", "Skoda"), ("Volkswagen", "Gebrauchtwagen")):
        v = KS.parse_kleinanzeigen_html(_KA_URL, _ka_seite(titel, f"<div>Marke</div><div>{marke}</div>"))
        assert not v["model_label"], (titel, v["model_label"])
        assert not v.get("model_vorschlag") and not v.get("_modell_aus_beschreibung")
    # ein echtes Modell in der Tabelle bleibt unangetastet
    v = KS.parse_kleinanzeigen_html(_KA_URL, _ka_seite(
        "BMW 320d Touring", "<div>Marke</div><div>BMW</div><div>Modell</div><div>3er</div>"))
    assert v["model_label"] == "3er"


def test_03_platzhalter_mit_programm_erkennung():
    v = {"make_label": "Volkswagen", "model_label": "Weitere VW", "model_description": "VW Golf7 1.5TSI ACT Join"}
    ms._enhance_generic_model(v)
    assert v["model_label"] == "Golf"
    v = {"make_label": "Kia", "model_label": "Weitere Kia", "model_description": "Kia ceed Automatik"}
    ms._enhance_generic_model(v)
    assert v["model_label"] == "cee'd / Ceed" and ms.modell_aufgeloest(v) == (True, True)
    # andere Marke im Titel (Ateca ist Seat/Cupra) -> kein VW-Modell daraus
    assert ms._modell_wie_im_programm({"raw_name": "Volkswagen"}, "ateca 1.5 dsg unfall") is None
    assert ms._modell_wie_im_programm({"raw_name": "Volkswagen"}, "Seat Ateca 1.5") is None
    assert ms._modell_wie_im_programm({}, "VW Golf") is None
    # nur zusammenhaengend und an einer Wortgrenze: "3200" ist nicht 320, "T5 Bulli multivan" (verstreut) nicht
    assert ms._modell_wie_im_programm({"raw_name": "BMW"}, "BMW 3200") is None
    assert ms._steht_als_wort_im_titel("Golf", "VW Golf7 1.5TSI") and ms._steht_als_wort_im_titel("XC60", "Volvo XC 60")
    assert ms._steht_als_wort_im_titel("cee'd / Ceed", "Kia ceed Automatik")
    assert not ms._steht_als_wort_im_titel("320", "BMW 3200") and not ms._steht_als_wort_im_titel("Arona", "Aronas")
    assert not ms._steht_als_wort_im_titel("T5 Multivan", "VW T5 Bulli multivan")


# ------------------------------------------------------------ Programm (werkzeug_erkennung)
def test_04_gekuerztes_modell():
    assert we.kuerzer("ceed Automatik") == ["ceed"]
    assert we.kuerzer("Range Rover Sport HSE") == ["Range Rover Sport", "Range Rover", "Range"]
    assert we.kuerzer("Golf") == [] and we.kuerzer("") == [] and we.kuerzer("Weitere VW Export") == []
    for feld, titel in (("Andere", "Kia ceed Automatik"), ("Kia ceed Automatik", "")):
        r = we.zuordnen(feld, titel)
        assert (r["marke"], r["modell"], r["modell_gefunden"], r["modell_gekuerzt"]) == \
               ("Kia", "cee'd / Ceed", True, True), r
        # an die Link-Bauer geht der Katalogname, nicht "ceed Automatik"
        assert r["modell_text"] == "cee'd / Ceed"
    # ganz gefunden -> nicht gekuerzt, Text bleibt wie gelesen
    r = we.zuordnen("VW Golf Variant Highline", "")
    assert r["modell_gefunden"] and not r["modell_gekuerzt"] and r["modell_text"] == "Golf Variant Highline"
    # nichts passt -> bleibt offen
    r = we.zuordnen("VW Gebrauchtwagen Export", "")
    assert not r["modell_gefunden"] and not r["modell_gekuerzt"]


def test_05_gekuerzt_ist_der_schwaechste_treffer():
    # Soll-Wert aus 1.3.5: AutoScout kennt "Aero Max" (aus dem Titel) — mobile.de haette aus "Aero" das Aero 8 gemacht
    r = we.zuordnen("Morgan Aero Mox", "Morgan Aero Max")
    assert r["modell"] == "Aero Max" and r["modell_text"] == "Aero Max" and not r["modell_gekuerzt"]


def test_06_zweite_lesung_geht_vor_gekuerzt():
    from routes.werkzeuge import _erkennen
    f = {"roh": True, "marke_modell_text": "Kia ceed Automatik", "titel": ""}
    r = _erkennen(f, None, {"marke_modell_text": ["Kia XCeed"]})
    assert r["quelle"] == "zweite_lesung" and r["modell"] == "XCeed" and f["modell"] == "XCeed"
    f = {"roh": True, "marke_modell_text": "Kia ceed Automatik", "titel": ""}
    r = _erkennen(f, None, {"marke_modell_text": ["Kia ceed Automatlk"]})     # auch gekuerzt -> bleibt die erste
    assert r["quelle"] == "bildschirm" and r["modell"] == "cee'd / Ceed" and r["modell_gefunden"]
    # Gelerntes geht ebenfalls vor
    import erkennung_lernen
    tab = {erkennung_lernen.schluessel("Kia ceed Automatik"): ("Kia", "ProCeed")}
    f = {"roh": True, "marke_modell_text": "Kia ceed Automatik", "titel": ""}
    r = _erkennen(f, tab, {})
    assert r["quelle"] == "gelernt" and r["modell"] == "ProCeed"


def test_06b_marke_aus_eindeutigem_modell():
    k = we.katalog()
    assert k.marke_aus_modell("Tiguan 2,0 dti Allrad") == ("Volkswagen", "Tiguan 2,0 dti Allrad")
    assert k.marke_aus_modell("Octavia Combi")[0] == "Skoda" and k.marke_aus_modell("Astra")[0] == "Opel"
    # mehrdeutig (Fiat/Mercedes "500", Opel/Chevrolet "Corsa"), zu kurz ("Up"), Platzhalter, nur Zahlen: nie
    for text in ("500 Lounge", "Corsa D", "Up 1.0", "Andere", "Weitere Modelle", "1234 Export", "", None):
        assert k.marke_aus_modell(text) is None, text
    r = we.zuordnen("Tiguan 2,0 dti Allrad", "Tiguan 2,0 dti Allrad")
    assert (r["marke"], r["modell"], r["modell_text"], r["erkannt"], r["marke_aus_modell"]) == \
           ("Volkswagen", "Tiguan", "Tiguan", True, True)
    # eine Marke im Text geht immer vor — dann nie "aus dem Modell"
    r = we.zuordnen("VW Tiguan", "")
    assert r["marke_aus_modell"] is False and r["modell"] == "Tiguan"
    # Gelerntes und eine zweite Lesung mit Marke gehen vor
    from routes.werkzeuge import _erkennen
    f = {"roh": True, "marke_modell_text": "Tiguan 2,0 dti Allrad", "titel": "Tiguan 2,0 dti Allrad"}
    r = _erkennen(f, None, {"marke_modell_text": ["VW Tiguan Allspace"]})
    assert r["quelle"] == "zweite_lesung" and r["marke_aus_modell"] is False and r["modell"] == "Tiguan Allspace"
    f = {"roh": True, "marke_modell_text": "Tiguan 2,0 dti Allrad", "titel": "Tiguan 2,0 dti Allrad"}
    r = _erkennen(f, None, {})
    assert r["quelle"] == "bildschirm" and r["marke_aus_modell"] is True and r["marke"] == "Volkswagen"


# ------------------------------------------------------------ Hinweise
def test_07_hinweis_ohne_none():
    links, h = wz.vergleichs_links({"make_label": "Opel", "model_label": None}, {})
    assert links == [] and len(h) >= 2
    assert not any("None" in x or "„“" in x for x in h), h
    assert "Im Inserat steht kein Modell (nur „Opel“) – kein mobile.de-Vergleich." in h
    assert "Im Inserat steht kein Modell (nur „Opel“) – kein AutoScout24-Vergleich." in h
    links, h = wz.vergleichs_links({"make_label": None, "model_label": None}, {})
    assert links == [] and "Im Inserat steht keine Marke – kein mobile.de-Vergleich." in h
    assert not any("None" in x or "„“" in x for x in h), h
    # Portal abgewaehlt -> sein Hinweis faellt weiter weg
    assert wz.hinweise_nach_portalen(h, {"autoscout": False}) == [x for x in h if "AutoScout" not in x]
    # bekannte Marke, unbekanntes Modell: alter Satz
    _l, h = wz.vergleichs_links({"make_label": "Hyundai", "model_label": "BO"}, {})
    assert any(x.startswith("mobile.de kennt das Modell „BO“ nicht") for x in h), h


# ------------------------------------------------------------ mobile.de-Sicherheitspruefung
_AKAMAI = ("<html><head></head><body><div id=\"sec-container\"><iframe src=\"/_sec/cp_challenge/x\"></iframe>"
           "<p>Powered and protected by Akamai</p></div></body></html>")


def test_08_sicherheitspruefung_eigener_text_und_nicht_aufgehoben():
    from routes.werkzeuge import _LESESEITE_GRUENDE
    assert bh.mobile_pruefseite(_AKAMAI)
    assert not bh.mobile_pruefseite("<html>" + "x" * 30_000 + " akamai</html>")     # echte Seiten sind gross
    assert not bh.mobile_pruefseite("<html>kleine Seite ohne Hinweis</html>")
    with pytest.raises(bh.SeiteUngueltig) as e:
        bh.mobile_inserat(_AKAMAI, "123456789", "https://suchen.mobile.de/fahrzeuge/details.html?id=123456789")
    assert str(e.value) == bh.PRUEFSEITE_TEXT
    assert not any(g in bh.PRUEFSEITE_TEXT for g in _LESESEITE_GRUENDE), "Pruefseite nie aufheben"
    # eine leere Seite ohne Akamai-Hinweis bleibt beim alten Text (und wird weiter aufgehoben)
    with pytest.raises(bh.SeiteUngueltig) as e:
        bh.mobile_inserat("<html><body>leer</body></html>", "123456789", "https://suchen.mobile.de/x")
    assert "keine Inseratsdaten" in str(e.value)


# ------------------------------------------------------------ Pruefskript
def test_09_foto_teil_im_pruefskript(capsys):
    import leseseite_analysieren as L
    html = ('<html><body><nav>Weitere Anzeigen</nav>'
            '<img src="https://img.kleinanzeigen.de/api/v1/prod-ads/images/ab/abcdef12-3456?rule=$_59.AUTO">'
            '<script>x={"u":"https:\\/\\/img.kleinanzeigen.de\\/api\\/v1\\/prod-ads\\/images\\/cd\\/12-34"}</script>'
            '</body></html>')
    L.fotos_untersuchen(html, "kleinanzeigen")
    aus = capsys.readouterr().out
    assert "prod-ads-Adressen in der Seite: 2" in aus and "unser Leser findet heute: 0" in aus
    assert "'Weitere Anzeigen' an Stelle 17   <-- VOR dem ersten Foto" in aus
    assert "img.kleinanzeigen.de/api/v1/prod-ads/images/ab" in aus
    # ohne Stopp-Wort davor findet der Leser die Fotos
    L.fotos_untersuchen(html.replace("<nav>Weitere Anzeigen</nav>", "") + "Weitere Anzeigen", "kleinanzeigen")
    aus = capsys.readouterr().out
    assert "unser Leser findet heute: 2" in aus and "VOR dem ersten Foto" not in aus
    sh = (BACKEND.parent / "deploy" / "serverpruefung.sh").read_text(encoding="utf-8")
    assert 'leseseite_analysieren.py --grund "ohne Fotos"' in sh
