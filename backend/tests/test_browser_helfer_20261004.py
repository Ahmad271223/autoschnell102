# -*- coding: utf-8 -*-
"""Browser-Helfer (04.10.2026, Wunsch Ahmad): eigene Erweiterung fuer Chrome/Edge.

Teil 1 ohne Server: Seite entpacken, Next.js-Daten lesen (mobile.de), __NEXT_DATA__ (AutoScout24),
Ergebnislisten, Marktlage + Ampel, Hinweise, Werkzeug-Eintrag (ZIP statt EXE).
Die Seiten werden hier nachgebaut — im Aufbau, wie er am 04.10.2026 live auf mobile.de und AutoScout24
stand (Inserat = derselbe Datensatz, den der Apify-Actor liefert: tests/fixtures/apify_mobile_item.json).

Teil 2 ueber HTTP (laufendes Backend wie die anderen Werkzeug-Tests): verbinden, Inserat schicken,
Kaufvertrag-Weg ohne Abruf (seit 04.10. abends fuer alle Konten), Vergleichsseite -> Ampel, Fehlerfaelle.
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
    # Wunsch Ahmad 06.10.2026: OHNE Preis wird das Inserat trotzdem gelesen (Kaufvertrag muss gehen) — test_43/44
    fz, _ = bh.inserat_auslesen(_identity(MOBILE_URL), MOBILE_URL, _mobile_inserat_html(_mobile_listing(price={})))
    assert fz["make_label"] == "Volkswagen" and not fz.get("list_price")


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


def test_12_navi_wie_eingestellt():
    """Wunsch Ahmad 04.10.2026 (abends): Programm und Helfer immer nach den AutoSchnell-Einstellungen."""
    v = {"make_label": "Volkswagen", "model_label": "Golf", "first_registration": "05/2018", "mileage": 90000,
         "power_kw": 110, "features": ["Navigationssystem"]}
    links, _ = wz.vergleichs_links(v, {"navi": {"mode": "wenn_vorhanden"}, "sort": "price_asc"})
    assert "NAVIGATION_SYSTEM" in links[0]["url"]
    links, _ = wz.vergleichs_links(v, {"navi": {"mode": "ignore"}, "sort": "price_asc"})
    assert "NAVIGATION_SYSTEM" not in links[0]["url"]


def test_16_gleiche_suche_erkennt_die_vergleichsseite_des_programms():
    """AutoScout24 schreibt die Adresse nach dem Laden um (04.10.2026 im Browser gesehen), mobile.de nicht."""
    link = ("https://www.autoscout24.de/lst/volkswagen?atype=C&cy=D&cat=ma74mo2090&fregfrom=2004&kmto=158000"
            "&powerfrom=51&powerto=59&powertype=kw&fuel=B&gear=M&damaged_listing=exclude&ocs_listing=include"
            "&sort=price&desc=0&ustate=N,U")
    umgeschrieben = ("https://www.autoscout24.de/lst/volkswagen/polo/ft_benzin/tr_schaltgetriebe?fregfrom=2004&cy=D"
                     "&kmto=158000&powerfrom=51&powerto=59&powertype=kw&damaged_listing=exclude&ocs_listing=include"
                     "&sort=price&desc=0&ustate=N%2CU&atype=C")
    assert bh.gleiche_suche(link, link) and bh.gleiche_suche(link, umgeschrieben)
    assert not bh.gleiche_suche(link, umgeschrieben.replace("kmto=158000", "kmto=150000"))   # Filter geaendert
    assert not bh.gleiche_suche(link, umgeschrieben + "&page=2")                             # andere Seite
    assert not bh.gleiche_suche(link, umgeschrieben.replace("/volkswagen/", "/audi/"))      # andere Marke
    assert not bh.gleiche_suche(link, umgeschrieben.replace("autoscout24.de", "autoscout24.at"))
    mobile = ("https://suchen.mobile.de/fahrzeuge/search.html?isSearchRequest=true&ref=quickSearch&s=Car&vc=Car"
              "&pageNumber=1&ms=25200%3B27%3B%3B%3B&fr=2004%3A&ml=%3A158000&pw=51%3A59&ft=PETROL&dam=0&cn=DE&sb=p&od=up")
    assert bh.gleiche_suche(mobile, mobile.replace("ms=25200%3B27%3B%3B%3B", "ms=25200;27;;;"))
    assert bh.gleiche_suche(mobile, mobile.replace("ref=quickSearch", "ref=srp"))
    assert not bh.gleiche_suche(mobile, mobile.replace("pageNumber=1", "pageNumber=2"))
    assert not bh.gleiche_suche(mobile, mobile.replace("od=up", "od=down"))
    assert not bh.gleiche_suche(mobile, mobile + "&fe=NAVIGATION_SYSTEM")
    assert not bh.gleiche_suche(mobile, link) and not bh.gleiche_suche("https://example.com/x", mobile)
    # 06.10.2026, vier weitere echte Suchen im Browser geladen: "Erstzulassung genau 2012" wandert als "re_2012"
    # in den Pfad (vorher: keine Ampel), Modelltyp als "mt_c-220", bei Tesla fallen Kraftstoff/Getriebe ganz weg
    echte = [
        ("https://www.autoscout24.de/lst/bmw?atype=C&cy=D&cat=ma13mo1641&fregfrom=2016&kmto=175000&powerfrom=136"
         "&powerto=143&powertype=kw&fuel=D&gear=A&damaged_listing=exclude&ocs_listing=include&sort=price&desc=0"
         "&ustate=N,U",
         "https://www.autoscout24.de/lst/bmw/320/ft_diesel/tr_automatik?fregfrom=2016&cy=D&kmto=175000&powerfrom=136"
         "&powerto=143&powertype=kw&damaged_listing=exclude&ocs_listing=include&sort=price&desc=0&ustate=N%2CU&atype=C"),
        ("https://www.autoscout24.de/lst/mercedes-benz?atype=C&cat=ma47mo2147&powerfrom=118&powerto=132&powertype=kw"
         "&fuel=D&gear=A&ocs_listing=include&sort=price&desc=0&ustate=N,U",
         "https://www.autoscout24.de/lst/mercedes-benz/c-klasse/mt_c-220/ft_diesel/tr_automatik?powerfrom=118"
         "&powerto=132&powertype=kw&ocs_listing=include&sort=price&desc=0&ustate=N%2CU&atype=C"),
        ("https://www.autoscout24.de/lst/opel?atype=C&custtype=D&cat=ma54mo1918&fregfrom=2012&fregto=2012&kmfrom=78000"
         "&kmto=118000&powerfrom=51&powerto=51&powertype=kw&fuel=B&gear=M&ocs_listing=include&sort=price&desc=0"
         "&ustate=N,U",
         "https://www.autoscout24.de/lst/opel/corsa/re_2012/ft_benzin/tr_schaltgetriebe?custtype=D&kmfrom=78000"
         "&kmto=118000&powerfrom=51&powerto=51&powertype=kw&ocs_listing=include&sort=price&desc=0&ustate=N%2CU&atype=C"),
        ("https://www.autoscout24.de/lst/tesla?atype=C&cy=D&cat=ma51520mo74665&fregfrom=2020&kmto=90000&powerfrom=321"
         "&powerto=328&powertype=kw&fuel=E&gear=A&damaged_listing=exclude&ocs_listing=include&sort=price&desc=0"
         "&ustate=N,U",
         "https://www.autoscout24.de/lst/tesla/model-3?fregfrom=2020&cy=D&kmto=90000&powerfrom=321&powerto=328"
         "&powertype=kw&damaged_listing=exclude&ocs_listing=include&sort=price&desc=0&ustate=N%2CU&atype=C"),
    ]
    for i, (l, s) in enumerate(echte):
        assert bh.gleiche_suche(l, s), s
        assert not any(bh.gleiche_suche(l, s2) for j, (_, s2) in enumerate(echte) if j != i), l
    opel_link, opel_seite = echte[2]
    assert not bh.gleiche_suche(opel_link, opel_seite.replace("re_2012", "re_2013"))        # anderes Jahr
    assert not bh.gleiche_suche(opel_link, opel_seite.replace("custtype=D&", ""))           # Filter entfernt


def test_17_auswertung_laeuft_nicht_quadratisch():
    """Pruefung 05./06.10.2026 (Paket 1): vier Stellen liefen auf einer praeparierten Seite quadratisch (128 KB
    "<script " = 2,5 s, jede Verdopplung x4; die Regex-Maschine gibt dabei den Prozess nicht frei -> alle anderen
    Anfragen dieses Prozesses standen). Jetzt linear: 400 KB muessen in Sekundenbruchteilen durch sein
    (quadratisch waeren es je Fall 10 s bis Minuten)."""
    import time
    import mobile_service as ms
    n = 400_000
    faelle = {
        "next_data": lambda: bh.next_data("<script " * (n // 8)),
        "beschreibung <": lambda: ms._apify_html_zu_text("<" * n),
        "beschreibung <li": lambda: ms._apify_html_zu_text("<li" * (n // 3)),
        "flight kaputte Stuecke": lambda: bh.next_flight_text("self.__next_f.push([1," * (n // 22)),
        "karten lange Adresse": lambda: bh.mobile_karten(
            '<html><body><a href="details.html?' + "details.html?&" * (n // 14) + '">x</a></body></html>'),
    }
    for name, fn in faelle.items():
        start = time.perf_counter()
        fn()
        assert time.perf_counter() - start < 2.0, name
    # ... und die echten Faelle gehen weiter
    assert bh.next_data('<script id="__NEXT_DATA__" type="application/json">{"a":1}</script>') == {"a": 1}
    assert ms._apify_html_zu_text("<ul><li class='x'>Eins</li><li>Zwei</li></ul><b>fett</b>") == "- Eins\n- Zwei\n\nfett"
    assert bh.mobile_karten('<html><body><a href="/fahrzeuge/details.html?x=1&id=123456">Golf</a></body></html>') \
        == {"123456": {"titel": "Golf", "zustand": []}}


def test_18_werte_aus_der_seite_werden_vor_dem_speichern_begrenzt():
    """Pruefung 05.10.2026 (Paket 1): die Lesung gilt 24 h fuer ALLE Konten — nie ungeprueft speichern."""
    ident = _identity(MOBILE_URL)
    # Preis NaN: bestand "Preis vorhanden", wurde gespeichert, danach int(nan) -> 500. NaN ist kein JSON.
    kaputt = _mobile_listing()
    kaputt["price"] = {"grs": {"amount": float("nan"), "currency": "EUR"}, "type": "FIXED"}
    assert "NaN" in _mobile_inserat_html(kaputt)
    with pytest.raises(bh.SeiteUngueltig):
        bh.inserat_auslesen(ident, MOBILE_URL, _mobile_inserat_html(kaputt))
    # normale Seite: unveraendert
    normal, _ = bh.inserat_auslesen(ident, MOBILE_URL, _mobile_inserat_html(_mobile_listing()))
    assert normal["make_label"] == "Volkswagen" and normal["list_price"] == 23850.0 and len(normal["image_urls"]) == 46
    assert normal["image_count"] == 46 and isinstance(normal["features"], list) and normal["features"]
    # die Regeln selbst
    roh = {"make_label": "Volkswagen", "model_label": {"x": 1}, "model_description": ["a"], "title": 320,
           "list_price": float("inf"), "mileage": 10 ** 20, "power_kw": -5, "power_ps": True, "seats": 5,
           "description": "x" * 50_000, "color": "y" * 5000, "features": ["Klima", {"boese": 1}, ["x"], 7, "z" * 900],
           "image_urls": ["https://img.classistatic.de/a.jpg", "https://fremd.example/zaehler.gif", 5,
                          "http://img.classistatic.de/unverschluesselt.jpg"],
           "images": [{"u": 1}], "image_count": 99, "previous_owners": 2, "accident_damaged": False,
           "irgendwas": {"objekt": 1}, "_resolved_make_id": 25200}
    s = bh.fahrzeug_bereinigen(roh)
    assert s["model_label"] is None and s["model_description"] is None and s["title"] == "320"
    assert s["list_price"] is None and s["mileage"] is None and s["power_kw"] is None and s["power_ps"] is None
    assert s["seats"] == 5 and len(s["description"]) == bh.MAX_BESCHREIBUNG and len(s["color"]) == bh.MAX_TEXT
    assert s["features"] == ["Klima", 7, "z" * bh.MAX_TEXT]
    assert s["image_urls"] == ["https://img.classistatic.de/a.jpg"] and s["images"] == [] and s["image_count"] == 1
    assert s["previous_owners"] == 2 and s["accident_damaged"] is False and s["irgendwas"] is None
    assert s["_resolved_make_id"] == 25200
    # falscher Typ in der Seite (Untertitel als Objekt): frueher KeyError -> 500; jetzt sauber gelesen oder abgelehnt
    try:
        f, _ = bh.inserat_auslesen(ident, MOBILE_URL, _mobile_inserat_html(_mobile_listing(subTitle={"x": 1})))
        assert f["model_description"] is None or isinstance(f["model_description"], str)
        assert bh.fahrzeug_kurz(f, ident, MOBILE_URL)["titel"] is not None
    except (bh.SeiteUngueltig, KeyError, TypeError, AttributeError, ValueError):
        pass        # der Leser lehnt ab — die Route macht daraus 422 (test_42)


def test_19_absurde_vergleichsangebote_bringen_die_umrechnung_nicht_zum_ueberlauf():
    """Kilometer 10^20 oder Erstzulassung 01/9999 in einer Ergebnisseite: frueher OverflowError -> 500."""
    assert bh._treffer_bereinigen({"id": 7, "titel": "x" * 900, "preis": 10 ** 20, "km": 10 ** 20, "ez": "01/9999" * 9,
                                   "zustand": ["Unfallfrei", {"x": 1}], "kw": "viel", "verkaeufer": "wer"}) \
        == {"id": "", "titel": "x" * 200, "zustand": ["Unfallfrei"], "neu": False, "preis": None, "km": None,
            "ez": ("01/9999" * 9)[:10], "kw": None, "ps": None, "kraftstoff": None, "getriebe": None,
            "verkaeufer": None, "bewertung": None}
    assert bh._jahr("01/9999") is None and bh._jahr("9999") is None and bh._jahr("06/2015") == 2015 + 5 / 12
    wild = [bh._treffer_bereinigen(dict(_t(i, 9000 + i * 500), km=(10 ** 20 if i % 2 else 100000),
                                        ez=("01/9999" if i % 3 else "06/2015"))) for i in range(12)]
    lage = bh.marktlage(11000, "", {"treffer": wild, "gesamt": 12, "sortierung": "preis_auf"},
                        eigen={"kilometer": 0, "ez_jahr": 1950, "ez_monat": 1})
    assert lage["ampel"] in ("gruen", "gelb", "rot", "grau")
    # ... und selbst ungefilterte Werte (eigenes Auto "EZ 9999") lassen die Umrechnung nur aus
    roh = [dict(_t(i, 9000 + i * 500)) for i in range(8)]
    assert bh.marktlage(11000, "", {"treffer": roh, "gesamt": 8, "sortierung": "preis_auf"},
                        eigen={"kilometer": 10 ** 9, "ez_jahr": 9999, "ez_monat": 1})["ampel"]


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
               "andere": konten._kopf(andere["token"]), "andere_firma": andere, "firma": firma, "sucher_id": sucher_id}
    finally:
        db.users.delete_many({"id": {"$in": [firma["user_id"], sucher_id, andere["user_id"]]}})
        db.dealers.delete_many({"id": {"$in": [firma["dealer_id"], andere["dealer_id"]]}})
        for sammlung in ("subscriptions", "werkzeug_codes", "werkzeug_verbindungen", "werkzeug_vergleiche",
                         "werkzeug_app_starts", "werkzeug_inserate", "vehicles", "vehicle_comparisons"):
            db[sammlung].delete_many({"dealer_id": {"$in": [firma["dealer_id"], andere["dealer_id"]]}})
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


def test_32_kaufvertrag_nimmt_die_browserdaten_ohne_abruf(welt):
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
    assert db.listings_cache.count_documents({"cache_key": f"mobile:{MOBILE_ID}"}) == 0, "nicht im Server-Speicher"
    vc = db.vehicle_comparisons.find_one({"user_id": welt["sucher_id"], "cache_key": f"mobile:{MOBILE_ID}"})
    assert vc and "browser_helfer_von" not in vc, "eigene Lesung: kein Lieferer vermerkt"
    # Entscheidung Ahmad 04.10.2026 abends ("alle sofort"): auch der Chef nimmt die Lesung des Suchers — kein Abruf
    chef = requests.post(f"{API}/mobile/compare", json={"url": MOBILE_URL}, headers=welt["chef"], timeout=60)
    assert chef.status_code == 200, chef.text[:300]
    assert chef.json()["cached"] is True and chef.json()["vehicle"]["make_label"] == "Volkswagen"
    assert not chef.json()["vehicle"].get("_mock")
    # 08.10.2026: dieselbe Firma -> Kontaktdaten des Verkaeufers bleiben (Lesung des eigenen Suchers)
    assert chef.json()["vehicle"]["seller_phone"] == "+49 (0)1515 1747777"
    assert chef.json()["vehicle"]["seller_address"] == "Versbacher Str. 6"
    assert db.link_jobs.count_documents({"url": {"$regex": MOBILE_ID}}) == jobs_vorher, "kein Abruf fuer den Chef"
    vc = db.vehicle_comparisons.find_one({"user_id": welt["firma"]["user_id"], "cache_key": f"mobile:{MOBILE_ID}"})
    assert vc["browser_helfer_von"] == {"user_id": welt["sucher_id"], "dealer_id": welt["firma"]["dealer_id"]}
    db.listings_cache.delete_many({"cache_key": f"mobile:{MOBILE_ID}"})


def test_32b_fremde_firma_nimmt_die_lesung_ohne_apify(welt):
    """Entscheidung Ahmad 04.10.2026 abends: wer den mobile.de-Link direkt in AutoSchnell einfuegt (ohne Helfer und
    Programm), bekommt die Lesung des Helfers irgendeines Kontos aus den letzten 24 h — kein Apify-Abruf. Wer
    geliefert hat, steht nur am Vergleich (nie in der Antwort)."""
    from datetime import datetime, timezone
    db = welt["db"]
    andere = welt["andere_firma"]
    ck = f"mobile:{MOBILE_ID}"
    db.listings_cache.delete_many({"cache_key": ck})
    db.subscriptions.insert_one({
        "id": f"bh-test-andere-{andere['user_id']}", "subject_user_id": andere["user_id"],
        "dealer_id": andere["dealer_id"], "plan": "monthly", "status": "active",
        "expires_at": "2099-01-01T00:00:00+00:00", "created_at": datetime.now(timezone.utc).isoformat()})
    jobs_vorher = db.link_jobs.count_documents({"url": {"$regex": MOBILE_ID}})
    chk = requests.post(f"{API}/listings/check", json={"url": MOBILE_URL}, headers=welt["andere"], timeout=30)
    assert chk.status_code == 200 and chk.json()["status"] == "completed" and chk.json().get("browser_helfer"), chk.text
    r = requests.post(f"{API}/mobile/compare", json={"url": MOBILE_URL}, headers=welt["andere"], timeout=60)
    assert r.status_code == 200, r.text[:300]
    d = r.json()
    assert d["cached"] is True and d["vehicle"]["make_label"] == "Volkswagen" and d["vehicle"]["mobile_ad_id"] == MOBILE_ID
    assert d["beweis_moeglich"] is True, "Beweis auf Knopfdruck per Server-Abruf (RP-446)"
    # Wunsch Ahmad 08.10.2026 (Datenschutz): Fahrzeugdaten ja, Kontaktdaten des Verkaeufers NICHT aus der Lesung
    # einer anderen Firma — Name, PLZ und Ort (wie im Inserat oeffentlich) bleiben
    for feld in bh.KONTAKT_FELDER:
        assert not d["vehicle"].get(feld), (feld, d["vehicle"].get(feld))
    assert d["vehicle"]["seller_name"] == "AUTO-MAGER.DE" and d["vehicle"]["seller_zip"] == "97078"
    fz = db.vehicles.find_one({"id": d["vehicle_id"], "dealer_id": andere["dealer_id"]}, {"_id": 0, "data": 1})
    assert fz and not (fz["data"].get("seller_phone") or fz["data"].get("seller_address")), "auch nicht gespeichert"
    assert "1515 1747777" not in r.text
    assert db.link_jobs.count_documents({"url": {"$regex": MOBILE_ID}}) == jobs_vorher, "kein Apify-Abruf"
    assert welt["sucher_id"] not in r.text, "der Lieferer steht nie in der Antwort"
    vc = db.vehicle_comparisons.find_one({"dealer_id": andere["dealer_id"], "cache_key": ck})
    assert vc["browser_helfer_von"] == {"user_id": welt["sucher_id"], "dealer_id": welt["firma"]["dealer_id"]}
    db.listings_cache.delete_many({"cache_key": ck})


def test_33_vergleichsseite_ergibt_die_ampel(welt):
    d, prog = welt["vergleich"], welt["prog"]
    preis = d["fahrzeug"]["preis"]
    preise = [preis - 2000, preis - 1000, preis + 500, preis + 900, preis + 1500, preis + 3000, preis + 4000, preis + 5000]
    suche = next(l["url"] for l in d["links"] if l["portal"] == "mobile.de")
    # Pruefung 05.10.2026 (Paket 1): eine ANDERE Suche (z.B. der Tab laedt noch die Suche des vorigen Autos)
    # bekommt keine Ampel — 409, nichts gespeichert; die richtige Seite danach schon
    andere = requests.post(f"{API}/werkzeuge/{WID}/marktlage", headers=prog, timeout=60,
                           json={"vergleich_id": d["vergleich_id"], "url": MOBILE_SUCHE,
                                 "seite": _seite(_mobile_suche_html(preise))})
    assert andere.status_code == 409, andere.text
    assert not welt["db"].werkzeug_vergleiche.find_one({"id": d["vergleich_id"]})["marktlage"]
    r = requests.post(f"{API}/werkzeuge/{WID}/marktlage", headers=prog, timeout=60,
                      json={"vergleich_id": d["vergleich_id"], "url": suche, "seite": _seite(_mobile_suche_html(preise))})
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
                          json={"vergleich_id": d["vergleich_id"], "url": suche,
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


def test_34b_beschaedigte_wie_eingestellt(welt):
    """Wunsch Ahmad 04.10.2026 (abends): "immer an die AutoSchnell-Regeln halten" — die Firmenregel entscheidet
    ueber Beschaedigte (vormittags hatte der Helfer sie noch immer ausgeschlossen)."""
    db = welt["db"]
    vorher = db.dealers.find_one({"id": welt["firma"]["dealer_id"]}, {"_id": 0, "comparison_rules": 1}) or {}
    try:
        for modus, mit_filter in (("ignore", False), ("no_accident", True)):
            db.dealers.update_one({"id": welt["firma"]["dealer_id"]},
                                  {"$set": {"comparison_rules": {"damage": {"mode": modus}, "sort": "price_asc"}}})
            r = _inserat(welt["prog"])
            assert r.status_code == 200, r.text
            links = {l["portal"]: l["url"] for l in r.json()["links"]}
            assert ("dam=0" in links["mobile.de"]) is mit_filter, (modus, links)
            if "AutoScout24" in links:
                assert ("damaged_listing=exclude" in links["AutoScout24"]) is mit_filter, (modus, links)
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
        # Wunsch Ahmad 06.10.2026: der Betreiber sieht, wer wann welches Programm heruntergeladen hat
        a = requests.get(f"{API}/admin/werkzeug-downloads", headers=konten.super_kopf(), timeout=30)
        assert a.status_code == 200, a.text
        meiner = next(d for d in a.json()["downloads"] if d["user_id"] == welt["sucher_id"] and d["werkzeug"] == WID)
        assert meiner["version"] == "2.0.0-test" and meiner["programm"] == wz.WERKZEUGE[WID]["name"]
        assert meiner["kunden_nr"] == 10002 and meiner["konto"] and meiner["am"]
        assert requests.get(f"{API}/admin/werkzeug-downloads", headers=welt["chef"], timeout=30).status_code == 403
    finally:
        if vorher:
            db.werkzeuge.replace_one({"id": WID}, vorher, upsert=True)
        else:
            db.werkzeuge.delete_one({"id": WID})


def test_38_programm_und_helfer_arbeiten_zusammen(welt):
    """Wunsch Ahmad 04.10.2026 (abends): Hat das Windows-Programm das Auto gerade verglichen, oeffnet der Helfer
    nichts von selbst (nur auf Knopfdruck) — und die Vergleichsseiten des Programms bekommen die Ampel."""
    from datetime import datetime, timedelta, timezone
    db, helfer = welt["db"], welt["prog"]
    assert _inserat(helfer).json()["programm_verglichen"] is None
    pc = _verbinden(welt, "sucher", wid=wz.AUTOPOINTER, name="PC-Buero")
    f = {"marke": "VW", "modell": "Golf", "marke_modell_text": "VW Golf", "titel": "VW Golf VII 2.0 GTI TCR",
         "ez_monat": 5, "ez_jahr": 2019, "kilometer": 60000, "kw": 213, "ps": 290, "kraftstoff": "Benzin",
         "getriebe": "Automatik", "preis": 23850, "quelle": "mobile.de", "inserat_id": MOBILE_ID, "roh": True}
    r = requests.post(f"{API}/werkzeuge/{wz.AUTOPOINTER}/vergleich", headers=pc, json={"fahrzeug": f}, timeout=60)
    assert r.status_code == 200, r.text
    links = {l["portal"]: l["url"] for l in r.json()["links"]}
    eintrag = db.werkzeug_vergleiche.find_one({"werkzeug": wz.AUTOPOINTER, "user_id": welt["sucher_id"]},
                                              sort=[("erstellt_am", -1)])
    try:
        # 1. Helfer oeffnet dasselbe Inserat -> er weiss, dass das Programm es gerade verglichen hat
        h = _inserat(helfer)
        assert h.status_code == 200 and h.json()["programm_verglichen"] == eintrag["erstellt_am"], h.text

        def zuordnen(kopf, url):
            return requests.post(f"{API}/werkzeuge/{WID}/programm-suche", headers=kopf, json={"url": url}, timeout=30)

        # 2. Vergleichsseite des Programms -> Zuordnung, dann die Ampel am Vergleich des Programms
        z = zuordnen(helfer, links["mobile.de"])
        assert z.status_code == 200, z.text
        assert z.json()["vergleich_id"] == eintrag["id"] and z.json()["portal"] == "mobile.de"
        assert z.json()["fahrzeug"]["preis"] == 23850 and z.json()["fahrzeug"]["inserat_url"] == MOBILE_URL
        preise = [21850, 22850, 24350, 24750, 25350, 26850, 27850, 28850]
        m = requests.post(f"{API}/werkzeuge/{WID}/marktlage", headers=helfer, timeout=60,
                          json={"vergleich_id": eintrag["id"], "url": links["mobile.de"],
                                "seite": _seite(_mobile_suche_html(preise))})
        assert m.status_code == 200, m.text
        assert m.json()["platz"] == 3 and m.json()["ampel"] == "gruen", m.json()
        assert db.werkzeug_vergleiche.find_one({"id": eintrag["id"]})["marktlage"]["mobile"]["platz"] == 3
        # 3. andere Suche, fremdes Konto, keine Vergleichsseite -> nichts
        assert zuordnen(helfer, links["mobile.de"].replace("od=up", "od=down")).status_code == 404
        assert zuordnen(_verbinden(welt, "chef", name="Chrome · Windows"), links["mobile.de"]).status_code == 404
        assert zuordnen(helfer, MOBILE_URL).status_code == 400
        # 4. aelter als 30 Minuten -> wieder frei
        alt = (datetime.now(timezone.utc) - timedelta(minutes=31)).isoformat()
        db.werkzeug_vergleiche.update_one({"id": eintrag["id"]}, {"$set": {"erstellt_am": alt}})
        assert _inserat(helfer).json()["programm_verglichen"] is None
        assert zuordnen(helfer, links["mobile.de"]).status_code == 404
    finally:
        db.werkzeug_vergleiche.delete_many({"werkzeug": wz.AUTOPOINTER, "user_id": welt["sucher_id"]})
        db.link_jobs.delete_many({"url": MOBILE_URL})


def test_39_status_und_texte_fuer_den_browser(welt):
    """Pruefung 05.10.2026 (Nr. 15/24): /status sagt dem Helfer, ob das Konto das Windows-Programm hat (sonst fragt
    jede Ergebnisseite umsonst /programm-suche); die Meldungen sprechen vom Browser, nicht vom PC/Programm."""
    s = requests.get(f"{API}/werkzeuge/{WID}/status", headers=welt["prog"], timeout=30)
    assert s.status_code == 200 and s.json()["programm_verbunden"] is True, s.text      # test_38 hat es verbunden
    chef = _verbinden(welt, "chef", name="Edge · Chef")
    assert requests.get(f"{API}/werkzeuge/{WID}/status", headers=chef, timeout=30).json()["programm_verbunden"] is False
    falsch = requests.get(f"{API}/werkzeuge/{WID}/status", timeout=30,
                          headers={wz.TOKEN_KOPF: "gibt-es-nicht", "X-Werkzeug-Version": "2.6.0"})
    assert falsch.status_code == 401
    assert "Browser" in falsch.json()["detail"] and "PC" not in falsch.json()["detail"], falsch.text
    ohne = requests.get(f"{API}/werkzeuge/{WID}/status", timeout=30, headers={"X-Werkzeug-Version": "2.6.0"})
    assert ohne.status_code == 401 and "Programm" not in ohne.json()["detail"]
    # das Windows-Programm behaelt seine Texte
    prog = requests.get(f"{API}/werkzeuge/{wz.AUTOPOINTER}/status", timeout=30, headers={wz.TOKEN_KOPF: "gibt-es-nicht"})
    assert "nicht (mehr) verbunden" in prog.json()["detail"] and "PC" in prog.json()["detail"]


def test_40_trefferzahl_gedeckelt():
    """Pruefung 05.10.2026 (Nr. 11): mehr als MAX_TREFFER Treffer wertet der Server nie aus (praeparierte Seite)."""
    preise = [10000 + i * 10 for i in range(bh.MAX_TREFFER + 150)]
    liste = bh.treffer_auslesen(MOBILE_SUCHE, _mobile_suche_html(preise))
    assert len(liste["treffer"]) == bh.MAX_TREFFER
    lage = bh.marktlage(20000, "", liste)
    assert lage["gesamt"] == bh.MAX_TREFFER + 150 and lage["ampel"], lage


def test_41_index_auf_vergleichs_id():
    """Pruefung 05.10.2026 (Nr. 1): /marktlage liest und schreibt werkzeug_vergleiche ueber "id" — ohne Index ein
    Vollscan je Vergleichsseite."""
    from indizes import WERKZEUG_INDIZES
    assert any(s == "werkzeug_vergleiche" and k == [("id", 1)] and o.get("unique") for s, k, o in WERKZEUG_INDIZES)


def test_42_kaputte_seitenwerte_enden_als_422_und_werden_nie_gespeichert(welt):
    """Pruefung 05.10.2026 (Paket 1): vorher wurde erst gespeichert (fuer alle Konten, 24 h) und danach scheiterte
    die Anfrage mit 500 — der kaputte Wert blieb liegen."""
    db, prog = welt["db"], welt["prog"]
    andere_id = "42196329136897"
    url = f"https://suchen.mobile.de/fahrzeuge/details.html?id={andere_id}"
    ck = f"mobile:{andere_id}"
    db.werkzeug_inserate.delete_many({"cache_key": ck})
    kaputt = _mobile_listing(id=int(andere_id))
    kaputt["price"] = {"grs": {"amount": float("nan"), "currency": "EUR"}, "type": "FIXED"}
    r = _inserat(prog, url=url, html=_mobile_inserat_html(kaputt))
    assert r.status_code == 422, r.text
    r = _inserat(prog, url=url, html=_mobile_inserat_html(_mobile_listing(id=int(andere_id), subTitle={"x": 1})))
    assert r.status_code in (200, 422), r.text
    if r.status_code == 422:
        assert db.werkzeug_inserate.count_documents({"cache_key": ck}) == 0, "abgelehnt = nie gespeichert"
    # riesige Beschreibung + fremde Bildadresse: gespeichert wird nur die begrenzte Fassung
    gross = _mobile_listing(id=int(andere_id))
    r = _inserat(prog, url=url, html=_mobile_inserat_html(gross, beschreibung="Sehr lang. " * 40_000))
    assert r.status_code == 200, r.text
    gemerkt = db.werkzeug_inserate.find_one({"cache_key": ck, "user_id": welt["sucher_id"]})
    assert gemerkt and len(gemerkt["data"]["description"]) <= bh.MAX_BESCHREIBUNG
    assert all(u.startswith("https://img.classistatic.de/") for u in gemerkt["data"]["image_urls"])
    db.werkzeug_inserate.delete_many({"cache_key": ck})
    db.werkzeug_vergleiche.delete_many({"fahrzeug.inserat_id": andere_id})


def test_43_fehlerantwort_spiegelt_keine_ganze_seite(welt):
    """Pruefung 05.10.2026 (Paket 1): eine zu grosse "seite" kam in der 422-Antwort vollstaendig zurueck (bis
    25 MB, auch ohne Anmeldung). Jetzt gekuerzt."""
    gross = "A" * (5 * 1024 * 1024)
    r = requests.post(f"{API}/werkzeuge/{WID}/inserat", timeout=60, json={"url": MOBILE_URL, "seite": gross})
    assert r.status_code == 422, r.status_code
    assert len(r.content) < 5000, len(r.content)
    assert "Zeichen" in r.text and "seite" in r.text
    # ... auch wenn das Echo der ganze Anfragekoerper ist (Pflichtfeld fehlt)
    r = requests.post(f"{API}/werkzeuge/{WID}/marktlage", timeout=60, json={"url": MOBILE_URL, "seite": gross})
    assert r.status_code == 422 and len(r.content) < 5000, (r.status_code, len(r.content))


# ------------------------------------------------------------ Befund 06.10.2026: Kleinanzeigen im Astro-Format
_KA_ASTRO_URL = "https://www.kleinanzeigen.de/s-anzeige/toyota-yaris-1-5-hybrid/3532756704-216-8369"


def _ka_astro_seite(inseln: int = 12, props_kb: int = 450, verschieden: bool = False) -> str:
    """Wie die echte Seite: die sichtbaren Teile (viewad-*, Tabelle) und viele <astro-island>, die jeweils die
    kompletten Inseratsdaten samt Bildadressen in props tragen (Kleinanzeigen, Oktober 2026)."""
    bilder = "".join(f"&quot;https://img.kleinanzeigen.de/api/v1/prod-ads/images/ab/{i:04d}?rule=$_59.AUTO&quot;,"
                     for i in range(200))
    daten = ("{&quot;adData&quot;:[0,{&quot;images&quot;:[" + bilder + "]}]}") * max(1, props_kb * 1024 // len(bilder))
    teile = [f'<astro-island uid="u{i}" component-url="/_astro/K{i}.js" props="{daten + (str(i) if verschieden else "")}">'
             f"<div>Teil {i}</div></astro-island>" for i in range(inseln)]
    return ("<html><head><title>Toyota Yaris</title>"
            '<link rel="canonical" href="https://www.kleinanzeigen.de/s-anzeige/toyota-yaris-1-5-hybrid/3532756704-216-8369">'
            "</head><body>"
            '<h1 id="viewad-title">Toyota Yaris 1.5 Hybrid Style</h1>'
            '<h2 id="viewad-price">15.220 €</h2>'
            '<span id="viewad-locality">74259 Baden-Württemberg - Widdern</span>'
            '<div id="viewad-details"><div>Marke</div><div>Toyota</div><div>Modell</div><div>Yaris</div>'
            "<div>Kilometerstand</div><div>83.895 km</div><div>Erstzulassung</div><div>Januar 2020</div>"
            "<div>Kraftstoffart</div><div>Hybrid</div><div>Leistung</div><div>101 PS</div>"
            "<div>Getriebe</div><div>Automatik</div></div>"
            + "".join(teile) + "</body></html>")


def test_40_kleinanzeigen_astro_seite_ueber_4_mb_wird_gekuerzt_und_gelesen():
    """Befund 06.10.2026: "Die Seite ist zu groß." bei einem Kleinanzeigen-Auto (es war zufaellig reserviert —
    damit hatte es nichts zu tun). Das neue Seitenformat wiederholt die Inseratsdaten ~12-mal (6,2 MB); seit
    Paket 2 (MAX_HTML 4 MB) wurde das abgelehnt. Jetzt: bis MAX_ROH entpacken, Wiederholungen leeren, lesen."""
    import time
    html = _ka_astro_seite()
    assert len(html) > bh.MAX_HTML, len(html)
    start = time.perf_counter()
    entpackt = bh.seite_entpacken(_seite(html))
    dauer = time.perf_counter() - start
    assert len(entpackt) < bh.MAX_HTML and entpackt.count('props=""') == 11
    assert dauer < 2.0, f"Kuerzen dauerte {dauer:.2f}s — muss linear bleiben"
    fahrzeug, _ = bh.kleinanzeigen_inserat(entpackt, "3532756704", _KA_ASTRO_URL)
    assert fahrzeug["make_label"] == "Toyota" and fahrzeug["mileage"] == 83895
    assert fahrzeug["first_registration"] == "01/2020"
    assert fahrzeug["images"], "die Bildadressen aus der ersten Kopie bleiben"


def test_41_alle_verschieden_dann_nur_die_erste_kopie_und_grenzen_bleiben():
    html = _ka_astro_seite(inseln=12, verschieden=True)
    assert len(html) > bh.MAX_HTML
    entpackt = bh.seite_entpacken(_seite(html))
    assert len(entpackt) < bh.MAX_HTML and entpackt.count('props=""') == 11
    # ohne props (keine Astro-Seite) bleibt es bei der Grenze nach dem Kuerzen
    with pytest.raises(bh.SeiteUngueltig):
        bh.seite_entpacken(_seite("<p>" + "x" * (bh.MAX_HTML + 10) + "</p>"))
    # Zip-Bombe ueber MAX_ROH: abgelehnt, ohne alles zu entpacken
    bombe = base64.b64encode(gzip.compress(b"a" * (bh.MAX_ROH + 10))).decode()
    with pytest.raises(bh.SeiteUngueltig):
        bh.seite_entpacken(bombe)
    assert bh.MAX_ROH == 16 * 1024 * 1024 and bh.MAX_HTML == 4 * 1024 * 1024


def test_42_kuerzen_bleibt_linear_bei_vielen_kurzen_props():
    """Paket 1: keine quadratischen Muster — viele kurze props (unter der 10-KB-Schwelle) und ein offenes
    props=" ohne Ende laufen in Sekundenbruchteilen."""
    import time
    viele = '<astro-island props="kurz">' * 200_000 + ' props="' + "y" * 500_000
    start = time.perf_counter()
    assert bh._astro_props_kuerzen(viele) == viele
    assert time.perf_counter() - start < 2.0


# ------------------------------------------------------------ Wunsch Ahmad 06.10.2026: Inserat ohne Preis
_KA_OHNE_PREIS_URL = "https://www.kleinanzeigen.de/s-anzeige/opel-corsa-1-2/3532756705-216-8369"


def _ka_ohne_preis(preis_text: str) -> str:
    return ("<html><head><title>Opel Corsa</title>"
            f'<link rel="canonical" href="{_KA_OHNE_PREIS_URL}"></head><body>'
            '<h1 id="viewad-title">Opel Corsa 1.2</h1>'
            f'<h2 id="viewad-price">{preis_text}</h2>'
            '<span id="viewad-locality">30159 Niedersachsen - Hannover</span>'
            '<div id="viewad-details"><div>Marke</div><div>Opel</div><div>Modell</div><div>Corsa</div>'
            "<div>Kilometerstand</div><div>120.000 km</div><div>Erstzulassung</div><div>März 2012</div></div>"
            '<p id="viewad-description-text">Gepflegter Kleinwagen aus zweiter Hand, Scheckheft, neue Reifen, '
            "TÜV bis 2027. Preis auf Anfrage – bitte nur ernst gemeinte Anrufe.</p>"
            "</body></html>")


def test_43_inserat_ohne_preis_wird_gelesen_ampel_grau():
    """Vorher: "Auf der Seite steht kein Preis." — man kam aus dem Helfer nicht zum Kaufvertrag. Jetzt wird das
    Inserat gelesen (Marke reicht), die Ampel sagt ehrlich "Kein Preis im Inserat"; den Kaufpreis traegt der
    Sucher im Vertrag selbst ein. Ohne Marke bleibt es eine ungueltige Seite."""
    ident = _identity(_KA_OHNE_PREIS_URL)
    for preis_text in ("VB", "Zu verschenken", ""):
        fz, _ = bh.inserat_auslesen(ident, _KA_OHNE_PREIS_URL, _ka_ohne_preis(preis_text))
        assert fz["make_label"] == "Opel" and not fz.get("list_price"), (preis_text, fz.get("list_price"))
        kurz = bh.fahrzeug_kurz(fz, ident, _KA_OHNE_PREIS_URL)
        assert kurz["preis"] is None
        from mobile_service import DEFAULT_RULES
        links, _ = wz.vergleichs_links(fz, DEFAULT_RULES)       # Links gehen auch ohne Preis
        assert links
        bh.verhandlung_hinweise(fz)
    lage = bh.marktlage(None, "x", _liste([9000 + i * 100 for i in range(8)], gesamt=8))
    assert lage["ampel"] == "grau" and lage["text"].startswith("Kein Preis im Inserat")
    ohne_marke = _ka_ohne_preis("VB").replace("<div>Marke</div><div>Opel</div>", "")
    with pytest.raises(bh.SeiteUngueltig, match="Fahrzeugmarke"):
        bh.inserat_auslesen(ident, _KA_OHNE_PREIS_URL, ohne_marke.replace("Opel Corsa", "Auto"))


def test_44_ohne_preis_bis_zum_kaufvertrag_und_kontakt_pflicht(welt):
    """Der ganze Weg ueber HTTP: Helfer liest ein mobile.de-Inserat OHNE Preis -> die App vergleicht es (ohne
    Abruf) -> Kaufvertrag. Wunsch Ahmad 06.10.2026: beim Anlegen ist Telefon ODER E-Mail des Verkaeufers Pflicht —
    eins reicht."""
    db = welt["db"]
    nr = "42196329136897"
    url = f"https://suchen.mobile.de/fahrzeuge/details.html?id={nr}"
    ck = f"mobile:{nr}"
    db.listings_cache.delete_many({"cache_key": ck})
    prog = welt.get("prog") or _verbinden(welt)
    r = _inserat(prog, url=url, html=_mobile_inserat_html(_mobile_listing(id=int(nr), price={})))
    assert r.status_code == 200, r.text[:300]
    d = r.json()
    assert d["fahrzeug"]["preis"] is None and d["app_pfad"].startswith("/app/vergleich?url=")
    cmp_ = requests.post(f"{API}/mobile/compare", json={"url": url}, headers=welt["sucher"], timeout=60)
    assert cmp_.status_code == 200, cmp_.text[:300]
    v = cmp_.json()
    assert v["vehicle"]["make_label"] == "Volkswagen" and not v["vehicle"].get("list_price")
    vid = v["vehicle_id"]
    basis = {"vehicle_id": vid, "seller_name": "Vera Verkauf", "seller_address": "Weg 1", "seller_zip": "30159",
             "seller_city": "Hannover", "purchase_price": 7400}
    try:
        ohne = requests.post(f"{API}/contracts", headers=welt["sucher"], json=basis, timeout=90)
        assert ohne.status_code == 422 and "Telefonnummer oder E-Mail" in ohne.text, ohne.text[:300]
        leer = requests.post(f"{API}/contracts", headers=welt["sucher"], timeout=90,
                             json={**basis, "seller_phone": "   ", "seller_email": ""})
        assert leer.status_code == 422, "nur Leerzeichen zaehlen nicht"
        assert db.generated_pdfs.count_documents({"vehicle_id": vid}) == 0, "ohne Kontakt entsteht nichts"
        mail = requests.post(f"{API}/contracts", headers=welt["sucher"], timeout=120,
                             json={**basis, "seller_email": "vera@e2etest-mail.de"})
        assert mail.status_code == 200, mail.text[:300]
        tel = requests.post(f"{API}/contracts", headers=welt["sucher"], timeout=120,
                            json={**basis, "seller_phone": "0170 1234567", "zweiter_vertrag_bestaetigt": True})
        assert tel.status_code == 200, tel.text[:300]
        gespeichert = db.generated_pdfs.find_one({"id": tel.json()["id"]}, {"_id": 0, "purchase_price": 1,
                                                                           "contract_data": 1})
        preis = gespeichert.get("purchase_price") or (gespeichert.get("contract_data") or {}).get("purchase_price")
        assert preis == 7400, "den Kaufpreis traegt der Sucher selbst ein"
    finally:
        for sammlung in ("generated_pdfs", "generated_pdf_versions", "appointments", "admin_vehicle_data"):
            db[sammlung].delete_many({"dealer_id": welt["firma"]["dealer_id"]})
        db.listings_cache.delete_many({"cache_key": ck})
        db.werkzeug_inserate.delete_many({"cache_key": ck})


def test_45_inserat_lesen_kontaktdaten_nur_eigene_firma():
    """Wunsch Ahmad 08.10.2026: inserat_lesen nimmt zuerst das eigene Konto, dann die eigene Firma, sonst irgendeine
    Lesung — aus einer fremden Firma (oder ohne Firmenangabe) ohne Telefon, E-Mail und Strasse."""
    import asyncio
    from datetime import datetime, timedelta, timezone
    from motor.motor_asyncio import AsyncIOMotorClient
    import os

    async def lauf():
        client = AsyncIOMotorClient(os.environ.get("MONGO_URL", "mongodb://localhost:27017"))
        db = client["bh_kontakt_" + uuid.uuid4().hex[:8]]
        try:
            jetzt = datetime.now(timezone.utc)
            daten = {"make_label": "VW", "seller_name": "Vera", "seller_phone": "0170 1", "seller_email": "v@x.de",
                     "seller_address": "Weg 1", "seller_zip": "30159"}
            for uid, did, alter in (("u_a", "firma_a", 5), ("u_b", "firma_b", 1)):
                await db[bh.SAMMLUNG_INSERATE].insert_one({
                    "cache_key": "mobile:1", "user_id": uid, "dealer_id": did, "data": dict(daten, seller_name=uid),
                    "gelesen_am": jetzt - timedelta(minutes=alter), "ablauf": jetzt + timedelta(hours=1)})
            eigen = await bh.inserat_lesen(db, "mobile:1", "u_a", "firma_a")
            assert eigen[0]["seller_phone"] == "0170 1" and eigen[2]["user_id"] == "u_a"
            kollege = await bh.inserat_lesen(db, "mobile:1", "u_a2", "firma_a")
            assert kollege[0]["seller_phone"] == "0170 1" and kollege[2]["user_id"] == "u_a", \
                "eigene Firma vor der juengeren fremden Lesung"
            fremd = await bh.inserat_lesen(db, "mobile:1", "u_c", "firma_c")
            assert fremd[2]["user_id"] == "u_b" and fremd[0]["seller_name"] == "u_b"
            assert not any(k in fremd[0] for k in bh.KONTAKT_FELDER) and fremd[0]["seller_zip"] == "30159"
            ohne_firma = await bh.inserat_lesen(db, "mobile:1", "", None)
            assert not any(k in ohne_firma[0] for k in bh.KONTAKT_FELDER)
            # gespeichert bleibt alles (die liefernde Firma braucht es weiter)
            roh = await db[bh.SAMMLUNG_INSERATE].find_one({"user_id": "u_b"})
            assert roh["data"]["seller_phone"] == "0170 1"
        finally:
            await client.drop_database(db.name)
            client.close()

    asyncio.run(lauf())


def test_46_helfer_nimmt_die_portalwahl_aus_autoschnell(welt):
    """Wunsch Ahmad 08.10.2026: die Portalwahl gibt es nur noch in AutoSchnell — der Server liefert dem Helfer nur
    die Links der gewaehlten Portale, /status nennt die Wahl (Erweiterung ab 2.7.2 zeigt sie nur noch an)."""
    prog = welt.get("prog") or _verbinden(welt)
    db = welt["db"]
    try:
        db.users.update_one({"id": welt["sucher_id"]}, {"$set": {"vergleich_portale": {"mobile": False,
                                                                                        "autoscout": True}}})
        st = requests.get(f"{API}/werkzeuge/{WID}/status", headers=prog, timeout=30)
        assert st.status_code == 200 and st.json()["portale"] == {"mobile": False, "autoscout": True}
        r = _inserat(prog)
        assert r.status_code == 200, r.text
        assert all(l["portal"] != "mobile.de" for l in r.json()["links"]), r.json()["links"]
        assert not any("kein mobile.de-Vergleich" in h for h in r.json()["hinweise"])
    finally:
        db.users.update_one({"id": welt["sucher_id"]}, {"$unset": {"vergleich_portale": ""}})
    r = _inserat(prog)
    assert any(l["portal"] == "mobile.de" for l in r.json()["links"])


def test_47_vorgangsnummer_programm_erweiterung_app(welt):
    """Wunsch Ahmad 08.10.2026 (Vorgangsnummer): hat das Konto die Erweiterung, oeffnet das Programm nur
    /app/vorgang/<id>; die Erweiterung uebernimmt den Vorgang genau einmal und oeffnet selbst, das Programm fragt
    nach (sonst oeffnet es selbst), die App-Seite zeigt ihn als Rueckfall. Das Inserat dazu wird ueber die Nummer
    zugeordnet — ohne 30-Minuten-Suche."""
    import re
    from datetime import datetime, timedelta, timezone
    db, helfer = welt["db"], welt.get("prog") or _verbinden(welt)
    # die Erweiterung muss den Vorgang kennen (ab 2.7.2) — ihre Version kommt mit jeder Anfrage (hier /status)
    helfer = {**helfer, "X-Werkzeug-Version": "2.7.2"}
    assert requests.get(f"{API}/werkzeuge/{WID}/status", headers=helfer, timeout=30).status_code == 200
    pc = _verbinden(welt, "sucher", wid=wz.AUTOPOINTER, name="PC-Vorgang")
    f = {"marke": "VW", "modell": "Golf", "marke_modell_text": "VW Golf", "titel": "VW Golf VII 2.0 GTI TCR",
         "ez_monat": 5, "ez_jahr": 2019, "kilometer": 60000, "kw": 213, "ps": 290, "kraftstoff": "Benzin",
         "getriebe": "Automatik", "preis": 23850, "quelle": "mobile.de", "inserat_id": MOBILE_ID, "roh": True}

    def vergleich(**zusatz):
        return requests.post(f"{API}/werkzeuge/{wz.AUTOPOINTER}/vergleich", timeout=60,
                             headers={**pc, "User-Agent": "AutoSchnell-Vergleich/1.5.8"}, json={"fahrzeug": f, **zusatz})

    def uebernehmen(vid):
        return requests.post(f"{API}/werkzeuge/{WID}/vorgang/{vid}/uebernehmen", headers=helfer, timeout=30)

    def stand(vid):
        return requests.get(f"{API}/werkzeuge/{wz.AUTOPOINTER}/vorgang/{vid}", headers=pc, timeout=30)

    r = vergleich()
    assert r.status_code == 200, r.text
    d = r.json()
    vid = d["vorgang_id"]
    try:
        assert re.fullmatch(r"[0-9a-f-]{36}", vid) and d["ueber_helfer"] is True and d["links"]
        assert stand(vid).json() == {"uebernommen": False}
        app = requests.get(f"{API}/werkzeuge/vorgang/{vid}", headers=welt["sucher"], timeout=30)
        assert app.status_code == 200 and app.json()["links"] == d["links"] and app.json()["uebernommen"] is False
        assert requests.get(f"{API}/werkzeuge/vorgang/{vid}", headers=welt["chef"], timeout=30).status_code == 404, \
            "nur das eigene Konto"
        u = uebernehmen(vid)
        assert u.status_code == 200 and u.json()["schon_uebernommen"] is False, u.text
        assert u.json()["links"] == d["links"] and u.json()["inserat_url"] == MOBILE_URL
        assert u.json()["kennung"] == f"mobile:{MOBILE_ID}", "dieselbe Kennung wie inseratKennung der Erweiterung"
        assert u.json()["fahrzeug"]["preis"] == 23850 and u.json()["fahrzeug"]["marke"]
        assert uebernehmen(vid).json()["schon_uebernommen"] is True, "Neuladen oeffnet nichts doppelt"
        assert stand(vid).json() == {"uebernommen": True}
        # das Inserat zu diesem Vorgang: genau dieser Programm-Vergleich (keine 30-Minuten-Suche)
        h = requests.post(f"{API}/werkzeuge/{WID}/inserat", headers=helfer, timeout=60,
                          json={"url": MOBILE_URL, "seite": _seite(_mobile_inserat_html(_mobile_listing())),
                                "vorgang_id": vid})
        assert h.status_code == 200, h.text
        assert h.json()["programm_verglichen"] == db.werkzeug_vergleiche.find_one({"id": vid})["erstellt_am"]
        assert db.werkzeug_vergleiche.find_one({"id": h.json()["vergleich_id"]})["vorgang_id"] == vid
        # Ampel ueber den Vorgang wie bei einem eigenen Vergleich
        preise = [21850, 22850, 24350, 24750, 25350, 26850, 27850, 28850]
        mobile = next(l["url"] for l in d["links"] if l["portal"] == "mobile.de")
        m = requests.post(f"{API}/werkzeuge/{WID}/marktlage", headers=helfer, timeout=60,
                          json={"vergleich_id": vid, "url": mobile, "seite": _seite(_mobile_suche_html(preise))})
        assert m.status_code == 200 and m.json()["platz"] == 3, m.text
        # falsche, fremde und fremd-geformte Nummern; der Helfer fragt nicht nach (das tut nur das Programm)
        assert uebernehmen(str(uuid.uuid4())).status_code == 404
        assert uebernehmen("kaputt").status_code == 404
        assert requests.get(f"{API}/werkzeuge/{WID}/vorgang/{vid}", headers=helfer, timeout=30).status_code == 404
        # nach 10 Minuten nicht mehr uebernehmbar (die Seite in der App zeigt ihn weiter)
        alt = (datetime.now(timezone.utc) - timedelta(minutes=11)).isoformat()
        db.werkzeug_vergleiche.update_one({"id": vid}, {"$set": {"erstellt_am": alt}, "$unset": {"helfer_am": ""}})
        assert uebernehmen(vid).status_code == 404 and stand(vid).status_code == 404
        assert requests.get(f"{API}/werkzeuge/vorgang/{vid}", headers=welt["sucher"], timeout=30).status_code == 200
        # Probelauf: nie ueber die Erweiterung
        p = vergleich(probelauf=True)
        assert p.json()["ueber_helfer"] is False and uebernehmen(p.json()["vorgang_id"]).status_code == 404
    finally:
        db.werkzeug_vergleiche.delete_many({"werkzeug": wz.AUTOPOINTER, "user_id": welt["sucher_id"]})
        db.link_jobs.delete_many({"url": MOBILE_URL})


def test_48_mindestversionen():
    """Durchsicht vor dem Rollout 08.10.2026: neue Wege nur mit Programm UND Erweiterung, die sie kennen."""
    assert wz.version_mindestens("1.5.8", "1.5.8") and wz.version_mindestens("1.5.10", "1.5.8")
    assert wz.version_mindestens("2.8.0", "2.7.2") and not wz.version_mindestens("2.7.1", "2.7.2")
    assert not wz.version_mindestens(None, "1.5.7") and not wz.version_mindestens("", "1.5.7")
    assert not wz.version_mindestens("kaputt", "1.5.7") and not wz.version_mindestens("1.5.x", "1.5.7")

