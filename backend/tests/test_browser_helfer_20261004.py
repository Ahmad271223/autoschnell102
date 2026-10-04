# -*- coding: utf-8 -*-
"""Browser-Helfer (04.10.2026, Wunsch Ahmad): eigene Erweiterung fuer Chrome/Edge.

Teil 1 ohne Server: Seite entpacken, Next.js-Daten lesen (mobile.de), __NEXT_DATA__ (AutoScout24),
Ergebnislisten, Marktlage + Ampel, Hinweise, Werkzeug-Eintrag (ZIP statt EXE).
Die Seiten werden hier nachgebaut — im Aufbau, wie er am 04.10.2026 live auf mobile.de und AutoScout24
stand (Inserat = derselbe Datensatz, den der Apify-Actor liefert: tests/fixtures/apify_mobile_item.json).

Teil 2 ueber HTTP (laufendes Backend wie die anderen Werkzeug-Tests): verbinden, Inserat schicken,
Kaufvertrag-Weg ohne Abruf (nur fuer DIESES Konto), Vergleichsseite -> Ampel, Fehlerfaelle.
"""
import base64
import copy
import gzip
import io
import json
import secrets
import sys
import uuid
import zipfile
from datetime import date
from pathlib import Path

import pytest
import requests

import konten  # noqa: E402
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import browser_helfer as bh  # noqa: E402
import werkzeuge as wz  # noqa: E402

API = konten.API
WID = wz.BROWSER_HELFER
FIXTURES = Path(__file__).resolve().parent / "fixtures"


# ------------------------------------------------------------ nachgebaute Portalseiten
def _seite(text: str) -> str:
    return base64.b64encode(gzip.compress(text.encode("utf-8"))).decode("ascii")


def _flight_html(*stuecke: str) -> str:
    """HTML mit Next.js-Datenstrom: jedes Stueck als self.__next_f.push([1,"…"])."""
    skripte = "".join(f"<script>self.__next_f.push([1,{json.dumps(s)}])</script>" for s in stuecke)
    return f"<!doctype html><html><head><title>t</title></head><body><main>x</main>{skripte}</body></html>"


def _mobile_listing(**aenderungen) -> dict:
    item = copy.deepcopy(json.loads((FIXTURES / "apify_mobile_item.json").read_text(encoding="utf-8"))[0])
    item.update(aenderungen)
    return item


def _mobile_inserat_html(listing: dict, beschreibung="<b>Scheckheft</b><br>Motorschaden? Nein. Neuer Zahnriemen – 1.500 €") -> str:
    listing = dict(listing)
    listing["htmlDescription"] = "$4f"
    laenge = len(beschreibung.encode("utf-8"))
    # Das Inserat steht mitten im Strom, die Beschreibung als eigener Textblock — wie live
    return _flight_html('0:["$","html",null,{}]\n2:{"listing":null}\n',
                        '3:["$","div",null,{"listing":' + json.dumps(listing, ensure_ascii=False) + '}]\n',
                        f"4f:T{laenge:x},{beschreibung}\n5:[\"x\"]\n")


def _mobile_suche_html(preise, typen=None, gesamt=None, eigene=None) -> str:
    listings = []
    for i, p in enumerate(preise):
        listings.append({
            "id": (eigene if (eigene and i == 0) else 400000000 + i),
            "type": (typen[i] if typen else "regular"),
            "attr": {"fr": "05/2018", "pw": "110 kW (150 PS)", "ft": "Diesel", "ml": "96.008 km", "tr": "Automatik"},
            "contact": {"enumType": "DEALER" if i % 2 else "FSBO"},
            "price": {"grs": {"amount": p}}, "priceRating": {"rating": "GOOD_PRICE"},
            "make": {"localized": "Volkswagen"}, "model": {"localized": "Golf"},
        })
    sr = {"numResultsTotal": gesamt if gesamt is not None else len(preise), "listings": listings,
          "searchId": "s", "pageNumber": 1}
    return _flight_html('1:{"searchResults":[{"id":"1"}],"surface":"SRP"}\n',
                        '7:{"searchResults":' + json.dumps(sr) + '}\n')


AS_ID = "886b362a-615f-4dbe-90a5-8e321d396afb"
AS_URL = f"https://www.autoscout24.de/angebote/volkswagen-golf-1-4-trendline-variant-benzin-blau-{AS_ID}"


def _autoscout_inserat_html(**v_aenderungen) -> str:
    v = {"make": "Volkswagen", "model": "Golf", "modelGroup": "Golf", "modelVersionInput": "1.4 Trendline Variant",
         "mileageInKmRaw": 221000, "firstRegistrationDate": "06/2000", "bodyType": "Kombi", "numberOfDoors": 4,
         "numberOfSeats": 5, "bodyColor": "Blau", "rawPowerInKw": 55, "rawPowerInHp": 75,
         "transmissionType": "Schaltgetriebe", "rawDisplacementInCCM": 1390,
         "fuelCategory": {"raw": "B", "formatted": "Benzin"}, "hadAccident": False, "noOfPreviousOwners": 5,
         "nextVehicleSafetyInspection": None,
         "equipment": {"comfortAndConvenience": [{"id": "Klimaanlage"}, {"id": "Elektrische Fensterheber"}]}}
    v.update(v_aenderungen)
    details = {"id": AS_ID, "description": "Bastlerfahrzeug<br />TÜV fällig", "isNew": False,
               "images": ["https://prod.pictures.autoscout24.net/listing-images/a.jpg/1280x960.webp"],
               "prices": {"public": {"priceRaw": 2950, "negotiable": True, "taxDeductible": False, "median": 3500}},
               "vehicle": v,
               "seller": {"isDealer": True, "type": "Dealer", "companyName": "R & S Ihr Autohaus",
                          "contactName": "Thomas Ritter",
                          "phones": [{"phoneType": "Office", "formattedNumber": "+49 4240 93110"}]},
               "location": {"zip": "28857", "city": "Syke", "street": "Hannoversche Straße 57"}}
    nd = {"props": {"pageProps": {"listingDetails": details}}, "page": "/angebote/[...]"}
    return ('<html><body><script id="__NEXT_DATA__" type="application/json">'
            + json.dumps(nd, ensure_ascii=False) + "</script></body></html>")


def _autoscout_suche_html(preise, sponsored=0, gesamt=None) -> str:
    listings = [{"id": str(uuid.uuid4()), "searchResultType": "Sponsored" if i < sponsored else "Organic",
                 "price": {"priceRaw": p}, "tracking": {"firstRegistration": "09-2015", "mileage": "133000"},
                 "vehicleDetails": [{"data": "55 kW (75 PS)", "iconName": "speedometer"}],
                 "vehicle": {"fuel": "Benzin", "transmission": "Schaltgetriebe"}, "seller": {"type": "Private"}}
                for i, p in enumerate(preise)]
    nd = {"props": {"pageProps": {"listings": listings, "numberOfResults": gesamt or len(preise)}}}
    return '<html><script id="__NEXT_DATA__" type="application/json">' + json.dumps(nd) + "</script></html>"


MOBILE_ID = "42196329136896"
MOBILE_URL = f"https://suchen.mobile.de/fahrzeuge/details.html?id={MOBILE_ID}"
MOBILE_SUCHE = "https://suchen.mobile.de/fahrzeuge/search.html?isSearchRequest=true&ms=25200%3B14%3B%3B&sb=p&od=up"


def _identity(url):
    from listing_identity import get_listing_identity
    return get_listing_identity(url)


# ------------------------------------------------------------ Teil 1: ohne Server
def test_01_seite_entpacken_mit_grenzen():
    assert bh.seite_entpacken(_seite("<html>ä€</html>")) == "<html>ä€</html>"
    for kaputt in ("", "kein base64!!", base64.b64encode(b"kein gzip").decode()):
        with pytest.raises(bh.SeiteUngueltig):
            bh.seite_entpacken(kaputt)
    # Zip-Bombe: klein gepackt, entpackt ueber der Grenze -> abgelehnt, ohne alles zu entpacken
    bombe = base64.b64encode(gzip.compress(b"a" * (bh.MAX_HTML + 10))).decode()
    with pytest.raises(bh.SeiteUngueltig):
        bh.seite_entpacken(bombe)


def test_02_flight_text_und_textblock_mit_umlauten():
    html = _flight_html('a:{"x":1}\n', 'b:{"listing":{"attributes":[],"id":5}}\n', "c:T7,äöü€\nd:1")
    text = bh.next_flight_text(html)
    assert bh.json_objekt_nach(text, '"listing":{"attributes":') == {"attributes": [], "id": 5}
    assert bh.json_objekt_nach(text, '"gibts":{') is None
    # 7 Bytes: ä ö ü (je 2) + 1 Byte von "€" reicht nicht -> nur die vollstaendigen Zeichen
    assert bh.flight_textblock(text, "$c") == "äöü"
    assert bh.flight_textblock(text, "kein Verweis") == "kein Verweis"
    assert bh.flight_textblock(text, "$zz") is None and bh.flight_textblock(text, None) is None


def test_03_mobile_inserat_wie_live():
    html = _mobile_inserat_html(_mobile_listing())
    fz, bewertung = bh.inserat_auslesen(_identity(MOBILE_URL), MOBILE_URL, html)
    assert fz["make_label"] == "Volkswagen" and fz["model_label"] == "Golf"
    assert fz["mobile_ad_id"] == MOBILE_ID and fz["list_price"] > 0
    assert fz["fuel"] in ("PETROL", "DIESEL") and fz["first_registration"]
    assert "Scheckheft" in fz["description"] and "1.500 €" in fz["description"]
    assert fz["image_count"] > 0 and fz["images"][0].startswith("https://")
    assert bewertung is None or bewertung["portal"] == "mobile.de"
    kurz = bh.fahrzeug_kurz(fz, _identity(MOBILE_URL), MOBILE_URL)
    assert kurz["marke"] == "Volkswagen" and kurz["quelle"] == "mobile" and kurz["inserat_id"] == MOBILE_ID
    assert isinstance(kurz["ez_jahr"], int) and kurz["preis"] == int(fz["list_price"])


def test_04_mobile_fremdes_inserat_oder_pruefseite_wird_abgelehnt():
    with pytest.raises(bh.SeiteUngueltig, match="anderen Inserat"):
        bh.inserat_auslesen(_identity(MOBILE_URL), MOBILE_URL, _mobile_inserat_html(_mobile_listing(id=123456789)))
    with pytest.raises(bh.SeiteUngueltig, match="keine Inseratsdaten"):
        bh.inserat_auslesen(_identity(MOBILE_URL), MOBILE_URL, "<html><body>Zugriff verweigert</body></html>")
    with pytest.raises(bh.SeiteUngueltig, match="Preis"):
        bh.inserat_auslesen(_identity(MOBILE_URL), MOBILE_URL, _mobile_inserat_html(_mobile_listing(price={})))


def test_05_mobile_privatanbieter_und_preisbewertung():
    contact = {"type": "Privatanbieter", "enumType": "FSBO", "address2": "DE-91101 Musterstadt"}
    rating = {"rating": "GOOD_PRICE", "ratingLabel": "Guter Preis",
              "thresholdLabels": ["24.900 €", "32.300 €", "34.800 €", "38.700 €", "41.500 €", "46.200 €"]}
    fz, b = bh.inserat_auslesen(_identity(MOBILE_URL), MOBILE_URL,
                                _mobile_inserat_html(_mobile_listing(contact=contact, priceRating=rating)))
    assert fz["seller_type"] == "privat" and fz["seller_zip"] == "91101"   # FSBO = privat (04.10.2026)
    assert b["stufe"] == "Guter Preis" and (b["fair_von"], b["fair_bis"]) == (34800, 38700)
    assert [s["text"] for s in b["stufen"]][1] == "guter Preis" and b["stufen"][1]["von"] == 32300
    kaputt = dict(rating, thresholdLabels=["1 €", "2 €"])
    assert bh.mobile_bewertung({"priceRating": kaputt})["fair_von"] is None
    assert bh.mobile_bewertung({}) is None


def test_06_autoscout_inserat():
    fz, b = bh.inserat_auslesen(_identity(AS_URL), AS_URL, _autoscout_inserat_html())
    assert (fz["make_label"], fz["model_label"], fz["first_registration"], fz["mileage"]) == \
        ("Volkswagen", "Golf", "06/2000", 221000)
    assert (fz["power_kw"], fz["power_ps"], fz["fuel"], fz["gearbox"]) == (55, 75, "PETROL", "MANUAL_GEAR")
    assert fz["seller_name"] == "R & S Ihr Autohaus" and fz["seller_ansprechpartner"] == "Thomas Ritter"
    assert fz["seller_type"] == "haendler" and fz["seller_city"] == "Syke" and fz["seller_phone"]
    assert fz["price_negotiable"] is True and fz["list_price"] == 2950.0
    assert fz["accident_damaged"] is False and fz["previous_owners"] == "5"
    assert "Klimaanlage" in fz["features"] and "TÜV fällig" in fz["description"]
    assert b == {"portal": "AutoScout24", "stufe": None, "code": "", "mitte": 3500}
    falsch = AS_URL.replace(AS_ID, "11111111-2222-3333-4444-555555555555")
    with pytest.raises(bh.SeiteUngueltig):
        bh.inserat_auslesen(_identity(falsch), falsch, _autoscout_inserat_html())


def test_07_ergebnislisten_ohne_werbeplaetze():
    html = _mobile_suche_html([9999, 15000, 15500, 16000], typen=["topOfPage", "regular", "eyecatcher", "regular"],
                              gesamt=57)
    liste = bh.treffer_auslesen(MOBILE_SUCHE, html)
    assert liste["portal"] == "mobile.de" and liste["gesamt"] == 57 and liste["sortierung"] == "preis_auf"
    assert [t["preis"] for t in liste["treffer"]] == [15000, 15500, 16000]   # Werbung oben zaehlt nicht
    assert liste["treffer"][0]["km"] == 96008 and liste["treffer"][0]["kw"] == 110
    a = bh.treffer_auslesen("https://www.autoscout24.de/lst/volkswagen/golf?sort=price&desc=0",
                            _autoscout_suche_html([100, 2000, 2300], sponsored=1, gesamt=40))
    assert a["portal"] == "AutoScout24" and [t["preis"] for t in a["treffer"]] == [2000, 2300]
    assert a["treffer"][0]["ez"] == "09/2015" and a["treffer"][0]["ps"] == 75 and a["gesamt"] == 40
    with pytest.raises(bh.SeiteUngueltig):
        bh.treffer_auslesen(MOBILE_URL, html)                  # Inserat ist keine Vergleichsseite
    assert bh.ist_vergleichsseite("http://suchen.mobile.de/fahrzeuge/search.html") is None


def _liste(preise, gesamt=None, sortierung="preis_auf", ids=None):
    return {"portal": "mobile.de", "gesamt": gesamt if gesamt is not None else len(preise), "sortierung": sortierung,
            "treffer": [{"id": (ids[i] if ids else str(i)), "preis": p} for i, p in enumerate(preise)]}


def test_08_ampel_nach_platz_unter_allen_treffern():
    preise = [10000 + 100 * i for i in range(20)]               # Seite 1 = die 20 guenstigsten
    gruen = bh.marktlage(10050, "x", _liste(preise, gesamt=20))
    assert gruen["ampel"] == "gruen" and gruen["platz"] == 2 and gruen["exakt"] is True
    assert gruen["guenstigstes"] == 10000 and "50 € teurer" in gruen["text_guenstigstes"]
    assert gruen["mitte"] == 10950                               # alle Treffer gelesen -> echte Mitte
    assert bh.marktlage(10850, "x", _liste(preise, gesamt=20))["ampel"] == "gelb"
    assert bh.marktlage(11850, "x", _liste(preise, gesamt=20))["ampel"] == "rot"
    # mehr Treffer als gelesen: Platz bleibt exakt, solange der Preis in Seite 1 liegt
    viele = bh.marktlage(10250, "x", _liste(preise, gesamt=400))       # 3 guenstigere -> Platz 4
    assert viele["platz"] == 4 and viele["ampel"] == "gruen" and viele["mitte"] is None
    # teurer als alle gelesenen: nur Untergrenze
    assert bh.marktlage(99999, "x", _liste(preise, gesamt=30))["ampel"] == "rot"       # >= 20/30
    assert bh.marktlage(99999, "x", _liste(preise, gesamt=60))["ampel"] == "gelb"      # >= 20/60
    unbekannt = bh.marktlage(99999, "x", _liste(preise, gesamt=400))
    assert unbekannt["ampel"] == "grau" and unbekannt["platz"] is None and "genauer Platz unbekannt" in unbekannt["text"]


def test_09_eigenes_inserat_zaehlt_nicht_mit_und_zu_wenige():
    lage = bh.marktlage(9000, MOBILE_ID, _liste([9000, 9500, 9900, 12000], ids=[MOBILE_ID, "a", "b", "c"]))
    assert lage["gesamt"] == 3 and lage["guenstiger"] == 0 and lage["ampel"] == "gruen"
    assert bh.marktlage(9000, "x", _liste([9500, 9900]))["ampel"] == "grau"             # nur 2 Vergleiche
    assert bh.marktlage(None, "x", _liste([1, 2, 3]))["ampel"] == "grau"
    stich = bh.marktlage(15000, "x", _liste([16000, 14000, 20000, 9000], gesamt=500, sortierung="relevanz"))
    assert stich["stichprobe"] is True and "Stichprobe" in stich["text"] and stich["platz"] == 3


def test_10_verhandlung_hinweise_kurz_und_ehrlich():
    fz = {"accident_damaged": True, "roadworthy": False, "hu": "11/2026", "previous_owners": "4",
          "description": "Motorschaden, Bastler, ohne TÜV. Rostfrei!", "price_negotiable": True}
    h = bh.verhandlung_hinweise(fz, max_anzahl=10, heute=date(2026, 10, 4))
    assert h[:4] == ["Unfallschaden laut Inserat", "Nicht fahrbereit laut Inserat", "HU bald fällig (11/2026)",
                     "4 Vorbesitzer"]
    # Schadenswoerter mit Fundstelle — der Sucher sieht, ob es "wir kaufen auch mit Motorschaden" heisst
    assert any(x.startswith("Motorschaden in der Beschreibung: „Motorschaden, Bastler, ohne TÜV.") for x in h)
    assert any(x.startswith("Bastler in der Beschreibung: „") for x in h)
    assert any(x.startswith("HU/TÜV in der Beschreibung: „") for x in h) and "Preis verhandelbar (VB)" in h
    assert not any(x.startswith("Rost") for x in h)              # "Rostfrei" ist kein Rost
    lang = bh.verhandlung_hinweise({"description": "Wir kaufen alles an. " * 5 + "(auch Unfall + Motorschaden). "
                                                   + "Der Ankauf erfolgt sofort. " * 5})
    assert len(lang) == 1 and lang[0].startswith("Motorschaden in der Beschreibung: „…") and lang[0].endswith("…“")
    assert "(auch Unfall + Motorschaden). Der Ankauf" in lang[0] and len(lang[0]) < 120
    assert bh.verhandlung_hinweise({"hu": "01/2020"}, heute=date(2026, 10, 4)) == ["HU abgelaufen (01/2020)"]
    assert bh.verhandlung_hinweise({"hu": "Neu", "previous_owners": "1"}) == []


def test_11_werkzeug_eintrag_zip_statt_exe(monkeypatch):
    monkeypatch.delenv("BROWSER_HELFER_KUNDEN", raising=False)
    monkeypatch.delenv("AUTOPOINTER_VERGLEICH_KUNDEN", raising=False)
    assert wz.freigegebene_werkzeuge(10002) == [wz.AUTOPOINTER, WID]
    assert wz.art(WID) == "browser" and wz.art(wz.AUTOPOINTER) == "windows"
    puffer = io.BytesIO()
    with zipfile.ZipFile(puffer, "w") as z:
        z.writestr("manifest.json", "{}")
        z.writestr("background.js", "x" * 3000)
    gut = puffer.getvalue()
    wz.datei_pruefen(WID, gut)
    with pytest.raises(ValueError):
        wz.datei_pruefen(WID, b"MZ" + b"\0" * 5000)              # EXE ist keine Erweiterung
    with pytest.raises(ValueError):
        wz.datei_pruefen(wz.AUTOPOINTER, gut)                    # ZIP ist kein Programm
    ohne = io.BytesIO()
    with zipfile.ZipFile(ohne, "w") as z:
        z.writestr("ordner/manifest.json", "{}" + " " * 3000)
    with pytest.raises(ValueError, match="manifest.json"):
        wz.datei_pruefen(WID, ohne.getvalue())
    oe = wz.oeffentlich(None, WID)
    assert oe["art"] == "browser" and oe["geraet"] == "Browser" and oe["dateiname"].endswith(".zip")


def test_12_navi_nur_im_programm_ignoriert():
    v = {"make_label": "Volkswagen", "model_label": "Golf", "first_registration": "05/2018", "mileage": 90000,
         "power_kw": 110, "features": ["Navigationssystem"]}
    regeln = {"navi": {"mode": "wenn_vorhanden"}, "sort": "price_asc"}
    links_programm, _ = wz.vergleichs_links(v, regeln)
    links_browser, _ = wz.vergleichs_links(v, regeln, navi_ignorieren=False)
    assert "NAVIGATION_SYSTEM" not in links_programm[0]["url"]
    assert "NAVIGATION_SYSTEM" in links_browser[0]["url"]


def _t(i, preis, titel="Volkswagen Golf", zustand=(), neu=False, km=120000, ez="06/2015"):
    return {"id": f"t{i}", "preis": preis, "titel": titel, "zustand": list(zustand), "neu": neu, "km": km, "ez": ez}


def test_13_aussortieren_unfall_export_neuwagen_lockangebot():
    """Wunsch Ahmad 04.10.2026: Unfallwagen, Export, Neuwagen (und Lockangebote) zaehlen nicht mit."""
    treffer = [
        _t(1, 870, zustand=["Beschädigt", "Unfallfahrzeug"]),
        _t(2, 1900, titel="Golf VII 1.2 TSI UNFALLFAHRZEUG"),
        _t(3, 2500, zustand=["Unfallfrei", "Nicht fahrtauglich"]),
        _t(4, 3000, titel="Golf 2.0 TDI Motorschaden"),
        _t(5, 4000, titel="Golf Highline – nur Export / Gewerbe"),
        _t(6, 26000, neu=True),                                   # Tageszulassung
        _t(7, 9100, km=12, ez="09/2026"),                         # Neuwagen ohne Kennzeichnung
        _t(8, 2000, titel="Golf Comfortline"),                    # Lockangebot (< 40 % der Mitte)
        _t(9, 9000, titel="Golf Comfortline Export möglich", zustand=["Unfallfrei"]),   # bleibt
        _t(10, 9500), _t(11, 9800), _t(12, 10200), _t(13, 11000, titel="Golf unbeschädigt, Unfallfrei"),
    ]
    lage = bh.marktlage(9700, "x", {"portal": "mobile.de", "gesamt": 13, "sortierung": "preis_auf",
                                    "treffer": treffer}, eigen={"kilometer": 120000, "ez_jahr": 2015, "ez_monat": 6})
    gruende = {a["preis"]: a["grund"] for a in lage["aussortiert"]}
    assert gruende == {870: "unfall", 1900: "unfall", 2500: "defekt", 3000: "defekt", 4000: "export",
                       26000: "neu", 9100: "neu", 2000: "preis"}
    assert lage["aussortiert_anzahl"] == 8 and lage["gesamt"] == 5 and lage["gelesen"] == 5
    assert lage["guenstigstes"] == 9000 and lage["platz"] == 3 and lage["ampel"] == "gelb", lage
    assert lage["text_aussortiert"].startswith("8 aussortiert: 2× Unfall/beschädigt")
    assert "Export/Händlerpreis" in lage["text_aussortiert"] and "Neuwagen/Tageszulassung" in lage["text_aussortiert"]
    # nur Aussortiertes auf Seite 1 -> ehrlich grau
    nur_schrott = bh.marktlage(9700, "x", {"portal": "mobile.de", "gesamt": 300, "sortierung": "preis_auf",
                                           "treffer": treffer[:5]})
    assert nur_schrott["ampel"] == "grau" and "nur aussortierte" in nur_schrott["text"]


def test_14_umrechnung_auf_km_und_baujahr():
    import math
    # Markt nach Formel: -2 % je 10.000 km, +10 % je Jahr juenger
    def preis(km, jahr):
        return int(round(20000 * 0.98 ** ((km - 100000) / 10000) * 1.10 ** (jahr - 2015)))
    saubere = [_t(i, preis(km, j), km=km, ez=f"01/{j}") for i, (km, j) in enumerate(
        [(60000, 2017), (80000, 2016), (100000, 2015), (120000, 2015), (140000, 2014), (160000, 2014),
         (90000, 2017), (150000, 2016)])]
    je_km, je_jahr, quelle = bh.umrechnungs_faktoren(saubere)
    assert math.isclose(je_km, -0.02, abs_tol=0.002) and math.isclose(je_jahr, 0.10, abs_tol=0.01)
    assert quelle == "aus 8 Vergleichsangeboten berechnet"
    u = bh.umrechnung(saubere, {"kilometer": 100000, "ez_jahr": 2015, "ez_monat": 1}, 21000)
    assert abs(u["preis"] - 20000) <= 100, u                       # auf das eigene Auto umgerechnet
    assert u["text"].startswith("Günstigstes sauberes Angebot umgerechnet auf euer Auto: ca. ")
    assert "Inserat ca." in u["text_inserat"] and "darüber" in u["text_inserat"]
    assert "je 10.000 km mehr −2,0 %" in u["text_faktoren"] and "je Jahr jünger +10,0 %" in u["text_faktoren"]
    # zu wenige Angebote -> Faustwert (und so benannt)
    wenig = bh.umrechnung(saubere[:3], {"kilometer": 100000, "ez_jahr": 2015}, None)
    assert wenig["quelle"] == "Faustwert" and "Faustwert" in wenig["text_faktoren"] and "text_inserat" not in wenig
    assert bh.umrechnung(saubere, {"kilometer": None, "ez_jahr": 2015}, 1) is None       # eigenes Auto unbekannt
    # unplausibles Ergebnis (mehr km = teurer) -> Faustwert statt Unsinn
    verkehrt = [_t(i, 10000 + i * 1000, km=50000 + i * 20000, ez="01/2015" if i % 2 else "01/2016") for i in range(8)]
    assert bh.umrechnungs_faktoren(verkehrt)[2] == "Faustwert"


def test_15_mobile_karten_titel_und_zustand():
    html = ('<html><body>'
            '<a href="/fahrzeuge/details.html?id=111111111&amp;vc=Car" data-testid="base-result-listing-1-link">'
            '<div>Gesponsert</div><div>Volkswagen Golf</div><div>VII 1.2 TSI</div><div>2.500 €</div>'
            '<div>Beschädigt</div><div>•</div><div>Unfallfahrzeug</div><div>• EZ 04/2015 • 126.406 km</div></a>'
            '<a href="/fahrzeuge/details.html?id=222222222"><div>NEU</div><div>Volkswagen Golf</div>'
            '<div>Comfortline</div><div>9.900 €</div><div>Unfallfrei</div></a>'
            '</body></html>')
    karten = bh.mobile_karten(html)
    assert karten["111111111"] == {"titel": "Volkswagen Golf VII 1.2 TSI", "zustand": ["Beschädigt", "Unfallfahrzeug"]}
    assert karten["222222222"] == {"titel": "Volkswagen Golf Comfortline", "zustand": ["Unfallfrei"]}


# ------------------------------------------------------------ Teil 2: ueber HTTP
def _neue_firma():
    s = uuid.uuid4().hex[:8]
    pw = "Bh-Test-" + secrets.token_hex(6) + "!"
    r = konten.registrieren({"email": f"bh_chef_{s}@bhtest-mail.de", "password": pw,
                             "company_name": f"BH Testfirma {s}", "contact_person": "Test"})
    assert r.status_code == 200, r.text
    token = r.json()["token"]
    me = requests.get(f"{API}/auth/me", headers=konten._kopf(token), timeout=30).json()["user"]
    return {"token": token, "dealer_id": me["dealer_id"], "user_id": me["id"]}


@pytest.fixture(scope="module")
def welt():
    from datetime import datetime, timezone
    db = konten._db()
    firma, andere = _neue_firma(), _neue_firma()
    getauscht = None
    halter = db.dealers.find_one({"kunden_nr": {"$in": [10002, "10002"]}}, {"_id": 0, "id": 1, "kunden_nr": 1})
    if halter and halter["id"] != firma["dealer_id"]:
        ersatz = 9_000_000 + secrets.randbelow(900_000)
        db.dealers.update_one({"id": halter["id"]}, {"$set": {"kunden_nr": ersatz}})
        getauscht = (halter["id"], halter["kunden_nr"])
    db.dealers.update_one({"id": firma["dealer_id"]}, {"$set": {"kunden_nr": 10002}})
    s = konten.sucher_als_chef_anlegen(firma["token"], json={"password": "Bh-Sucher-" + secrets.token_hex(6) + "!"})
    assert s.status_code == 200, s.text
    sucher_id = s.json()["sucher_id"]
    for uid in (sucher_id, firma["user_id"]):
        db.subscriptions.insert_one({
            "id": f"bh-test-{uid}", "subject_user_id": uid, "dealer_id": firma["dealer_id"], "plan": "monthly",
            "status": "active", "expires_at": "2099-01-01T00:00:00+00:00",
            "created_at": datetime.now(timezone.utc).isoformat()})
    try:
        yield {"db": db, "chef": konten._kopf(firma["token"]), "sucher": konten._kopf(konten.token_direkt(sucher_id)),
               "andere": konten._kopf(andere["token"]), "firma": firma, "sucher_id": sucher_id}
    finally:
        db.users.delete_many({"id": {"$in": [firma["user_id"], sucher_id]}})
        db.dealers.delete_one({"id": firma["dealer_id"]})
        for sammlung in ("subscriptions", "werkzeug_codes", "werkzeug_verbindungen", "werkzeug_vergleiche",
                         "werkzeug_app_starts", "werkzeug_inserate", "vehicles", "vehicle_comparisons"):
            db[sammlung].delete_many({"dealer_id": firma["dealer_id"]})
        db.subscriptions.delete_many({"id": {"$regex": "^bh-test-"}})
        if getauscht:
            db.dealers.update_one({"id": getauscht[0]}, {"$set": {"kunden_nr": getauscht[1]}})


def _verbinden(welt, wer="sucher", wid=WID, name="Edge · Windows"):
    r = requests.post(f"{API}/werkzeuge/{wid}/code", headers=welt[wer], timeout=30)
    assert r.status_code == 200, r.text
    r = requests.post(f"{API}/werkzeuge/{wid}/verbinden",
                      json={"code": r.json()["code"], "pc_name": name, "pc_kennung": name + "-" + wer}, timeout=30)
    assert r.status_code == 200, r.text
    return {wz.TOKEN_KOPF: r.json()["schluessel"], "X-Werkzeug-Version": "2.0.0"}


def _inserat(kopf, url=MOBILE_URL, html=None, wid=WID):
    return requests.post(f"{API}/werkzeuge/{wid}/inserat", headers=kopf, timeout=60,
                         json={"url": url, "seite": _seite(html if html is not None else _mobile_inserat_html(_mobile_listing()))})


def test_30_liste_zeigt_browser_helfer(welt):
    liste = requests.get(f"{API}/werkzeuge", headers=welt["sucher"], timeout=30).json()["werkzeuge"]
    b = next((w for w in liste if w["id"] == WID), None)
    assert b and b["art"] == "browser" and b["geraet"] == "Browser" and b["dateiname"] == "AutoSchnell-Helfer.zip"
    assert requests.get(f"{API}/werkzeuge", headers=welt["andere"], timeout=30).json()["werkzeuge"] == []
    # andere Firma: ohne Abo 402 (Abo-Pruefung zuerst), mit Abo 404 — einen Code bekommt sie nie
    assert requests.post(f"{API}/werkzeuge/{WID}/code", headers=welt["andere"], timeout=30).status_code in (402, 404)


def test_31_inserat_liefert_links_und_merkt_den_vertrag(welt):
    prog = _verbinden(welt)
    s = requests.get(f"{API}/werkzeuge/{WID}/status", headers=prog, timeout=30)
    assert s.status_code == 200 and s.json()["pc_name"] == "Edge · Windows", s.text
    r = _inserat(prog)
    assert r.status_code == 200, r.text
    d = r.json()
    assert d["fahrzeug"]["marke"] == "Volkswagen" and d["fahrzeug"]["inserat_id"] == MOBILE_ID
    assert d["inserat_url"] == MOBILE_URL and d["app_pfad"].startswith("/app/vergleich?url=https%3A%2F%2F")
    assert any(l["portal"] == "mobile.de" for l in d["links"]), d
    db = welt["db"]
    gemerkt = db.werkzeug_inserate.find_one({"user_id": welt["sucher_id"], "cache_key": f"mobile:{MOBILE_ID}"})
    assert gemerkt and gemerkt["data"]["make_label"] == "Volkswagen" and gemerkt["dealer_id"] == welt["firma"]["dealer_id"]
    eintrag = db.werkzeug_vergleiche.find_one({"id": d["vergleich_id"]})
    assert eintrag["werkzeug"] == WID and eintrag["vorab"] == "fertig" and eintrag["pc_name"] == "Edge · Windows"
    meine = requests.get(f"{API}/werkzeuge/{WID}/meine", headers=welt["sucher"], timeout=30).json()["vergleiche"]
    assert meine[0]["fahrzeug"]["inserat_url"] == MOBILE_URL
    welt["vergleich"] = d
    welt["prog"] = prog


def test_32_kaufvertrag_nimmt_die_browserdaten_nur_fuer_dieses_konto(welt):
    db = welt["db"]
    db.listings_cache.delete_many({"cache_key": f"mobile:{MOBILE_ID}"})
    jobs_vorher = db.link_jobs.count_documents({"url": {"$regex": MOBILE_ID}})
    # Die App fragt zuerst /listings/check — mit Browserdaten fertig, KEIN Hintergrund-Abruf (Apify)
    chk = requests.post(f"{API}/listings/check", json={"url": MOBILE_URL}, headers=welt["sucher"], timeout=30)
    assert chk.status_code == 200 and chk.json()["status"] == "completed" and chk.json().get("browser_helfer"), chk.text
    r = requests.post(f"{API}/mobile/compare", json={"url": MOBILE_URL}, headers=welt["sucher"], timeout=60)
    assert r.status_code == 200, r.text[:300]
    d = r.json()
    assert d["cached"] is True and d["vehicle"]["make_label"] == "Volkswagen"
    assert d["vehicle"]["mobile_ad_id"] == MOBILE_ID and not d["vehicle"].get("_mock")
    # 04.10.2026: Beweis auch hier — auf Knopfdruck holt der Server das Inserat selbst (test_37)
    assert d["beweis_moeglich"] is True and d["abgerufen_am"]
    welt["vehicle_id"] = d["vehicle_id"]
    assert db.link_jobs.count_documents({"url": {"$regex": MOBILE_ID}}) == jobs_vorher, "kein Abruf"
    assert db.listings_cache.count_documents({"cache_key": f"mobile:{MOBILE_ID}"}) == 0, "nichts geteilt"
    # Der Chef derselben Firma bekommt die Browserdaten des Suchers NICHT (A-01/A-02)
    chef = requests.post(f"{API}/mobile/compare", json={"url": MOBILE_URL}, headers=welt["chef"], timeout=60)
    assert chef.status_code != 200 or chef.json()["vehicle"].get("_mock") or \
        chef.json()["vehicle"].get("seller_name") != d["vehicle"].get("seller_name") or \
        db.listings_cache.count_documents({"cache_key": f"mobile:{MOBILE_ID}"}) == 1
    db.listings_cache.delete_many({"cache_key": f"mobile:{MOBILE_ID}"})


def test_33_vergleichsseite_ergibt_die_ampel(welt):
    d, prog = welt["vergleich"], welt["prog"]
    preis = d["fahrzeug"]["preis"]
    preise = [preis - 2000, preis - 1000, preis + 500, preis + 900, preis + 1500, preis + 3000, preis + 4000, preis + 5000]
    r = requests.post(f"{API}/werkzeuge/{WID}/marktlage", headers=prog, timeout=60,
                      json={"vergleich_id": d["vergleich_id"], "url": MOBILE_SUCHE, "seite": _seite(_mobile_suche_html(preise))})
    assert r.status_code == 200, r.text
    lage = r.json()
    # 2 von 8 Vergleichsangeboten sind guenstiger = 25 % -> gerade noch gruen
    assert lage["platz"] == 3 and lage["gesamt"] == 8 and lage["anteil"] == 0.25 and lage["ampel"] == "gruen", lage
    assert lage["aussortiert_anzahl"] == 0 and lage["umgerechnet"]["text"].startswith("Günstigstes sauberes Angebot")
    gespeichert = welt["db"].werkzeug_vergleiche.find_one({"id": d["vergleich_id"]})
    assert gespeichert["marktlage"]["mobile"]["platz"] == 3
    # falscher Vergleich, keine Vergleichsseite, fremdes Konto
    falsch = requests.post(f"{API}/werkzeuge/{WID}/marktlage", headers=prog, timeout=30,
                           json={"vergleich_id": "gibts-nicht", "url": MOBILE_SUCHE, "seite": _seite("<html></html>")})
    assert falsch.status_code == 404
    kein = requests.post(f"{API}/werkzeuge/{WID}/marktlage", headers=prog, timeout=30,
                         json={"vergleich_id": d["vergleich_id"], "url": MOBILE_URL, "seite": _seite("<html></html>")})
    assert kein.status_code == 400
    chef_prog = _verbinden(welt, "chef", name="Chrome · Windows")
    fremd = requests.post(f"{API}/werkzeuge/{WID}/marktlage", headers=chef_prog, timeout=30,
                          json={"vergleich_id": d["vergleich_id"], "url": MOBILE_SUCHE,
                                "seite": _seite(_mobile_suche_html(preise))})
    assert fremd.status_code == 404, "nur der eigene Vergleich"


def test_34_fehlerfaelle(welt):
    prog = welt["prog"]
    assert _inserat(prog, html="<html>Prüfseite</html>").status_code == 422
    assert _inserat(prog, url="https://example.com/auto/1").status_code == 400
    assert _inserat({"X-Werkzeug-Version": "2.0.0"}).status_code == 401
    fremd = _mobile_inserat_html(_mobile_listing(id=111111111))
    assert _inserat(prog, html=fremd).status_code == 422
    r = requests.post(f"{API}/werkzeuge/{WID}/inserat", headers=prog, timeout=30,
                      json={"url": MOBILE_URL, "seite": "kein base64"})
    assert r.status_code == 422
    # Das Windows-Programm hat keinen Inserat-Weg (nur Werkzeuge mit art "browser")
    r = requests.post(f"{API}/werkzeuge/{wz.AUTOPOINTER}/inserat", headers=prog, timeout=30,
                      json={"url": MOBILE_URL, "seite": _seite("x" * 30)})
    assert r.status_code == 404


def test_34b_beschaedigte_filtert_schon_das_portal(welt):
    """Wunsch Ahmad 04.10.2026: Unfall-/beschaedigte Autos gar nicht erst anzeigen — auch wenn die Firmenregel
    (z.B. Export-Profil) sie zulaesst."""
    db = welt["db"]
    vorher = db.dealers.find_one({"id": welt["firma"]["dealer_id"]}, {"_id": 0, "comparison_rules": 1}) or {}
    db.dealers.update_one({"id": welt["firma"]["dealer_id"]},
                          {"$set": {"comparison_rules": {"damage": {"mode": "ignore"}, "sort": "price_asc"}}})
    try:
        r = _inserat(welt["prog"])
        assert r.status_code == 200, r.text
        links = {l["portal"]: l["url"] for l in r.json()["links"]}
        assert "dam=0" in links["mobile.de"], links
        if "AutoScout24" in links:
            assert "damaged_listing=exclude" in links["AutoScout24"]
    finally:
        if vorher.get("comparison_rules"):
            db.dealers.update_one({"id": welt["firma"]["dealer_id"]}, {"$set": {"comparison_rules": vorher["comparison_rules"]}})
        else:
            db.dealers.update_one({"id": welt["firma"]["dealer_id"]}, {"$unset": {"comparison_rules": ""}})


def test_35_autoscout_inserat_ueber_http(welt):
    r = _inserat(welt["prog"], url=AS_URL, html=_autoscout_inserat_html())
    assert r.status_code == 200, r.text
    d = r.json()
    assert d["fahrzeug"]["quelle"] == "autoscout24" and d["portal_bewertung"]["mitte"] == 3500
    assert "Preis verhandelbar (VB)" in d["verhandlung"]
    assert d["inserat_url"] == f"https://www.autoscout24.de/angebote/{AS_ID}"


def test_37_beweis_fuer_browserdaten_holt_der_server_selbst(welt):
    """Wunsch Ahmad 04.10.2026: Fotos ins Beweisdokument, wenn man eins erstellt. Browserdaten zaehlen als Beweis
    nicht (RP-446) — der Server holt das Inserat auf Knopfdruck einmal selbst (Link-Job), der Beweis wartet darauf."""
    db = welt["db"]
    ck = f"mobile:{MOBILE_ID}"
    db.listings_cache.delete_many({"cache_key": ck})
    db.inserat_beweise.delete_many({"cache_key": ck})
    try:
        r = requests.post(f"{API}/beweise/anfordern", json={"vehicle_id": welt["vehicle_id"]},
                          headers=welt["sucher"], timeout=30)
        assert r.status_code == 200, r.text
        assert "holt unser Server das Inserat jetzt einmal selbst" in r.json()["hinweis"]
        assert db.link_jobs.count_documents({"url": MOBILE_URL}) >= 1, "Server-Abruf eingereiht"
        doc = db.inserat_beweise.find_one({"cache_key": ck})
        assert doc and doc.get("serverabruf_fuer_browserdaten") is True and not doc.get("quelle_daten")
        # zweimal klicken: dasselbe Dokument, kein zweiter Auftrag
        r2 = requests.post(f"{API}/beweise/anfordern", json={"vehicle_id": welt["vehicle_id"]},
                           headers=welt["sucher"], timeout=30)
        assert r2.status_code == 200 and r2.json()["beweis"]["id"] == r.json()["beweis"]["id"]
    finally:
        db.inserat_beweise.delete_many({"cache_key": ck})
        db.link_jobs.delete_many({"url": MOBILE_URL})
        db.listings_cache.delete_many({"cache_key": ck})


def test_36_zip_laden_fuer_die_firma(welt):
    from storage_service import storage
    puffer = io.BytesIO()
    with zipfile.ZipFile(puffer, "w") as z:
        z.writestr("manifest.json", json.dumps({"manifest_version": 3, "name": "AutoSchnell Helfer", "version": "2.0.0"}))
        z.writestr("background.js", "// " + "x" * 4000)
    daten = puffer.getvalue()
    db = welt["db"]
    vorher = db.werkzeuge.find_one({"id": WID}, {"_id": 0})
    try:
        wz.hochladen(db, WID, daten, "2.0.0-test", storage=storage)
        r = requests.get(f"{API}/werkzeuge/{WID}/download", headers=welt["sucher"], timeout=60)
        assert r.status_code == 200 and r.content == daten
        assert r.headers["content-type"].startswith("application/zip")
        assert 'filename="AutoSchnell-Helfer.zip"' in r.headers["content-disposition"]
    finally:
        if vorher:
            db.werkzeuge.replace_one({"id": WID}, vorher, upsert=True)
        else:
            db.werkzeuge.delete_one({"id": WID})
