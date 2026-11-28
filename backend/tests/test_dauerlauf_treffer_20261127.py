# -*- coding: utf-8 -*-
"""Dauerlauf (Wunsch Ahmad 27.11.2026): Trefferliste.

Das Programm schickt jedes selbst gelesene Auto; der Server prueft Schaden und Preislage und legt es in die Liste des
Kontos. Kein Vergleich, kein Portal-Abruf, kein Tageslimit.

Teil 1 ohne Server (Urteil, Preislage, Dokument, Verdrahtung), Teil 2 ueber HTTP (wie test_werkzeuge_20261003).
"""
import sys
from datetime import datetime, timezone
from pathlib import Path

import requests

import konten  # noqa: E402
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import dauerlauf_treffer as dt  # noqa: E402
import schaden_pruefung  # noqa: E402
import werkzeuge as wz  # noqa: E402
from test_werkzeuge_20261003 import _abo, _verbinden, welt  # noqa: E402,F401

API = konten.API
WID = wz.AUTOPOINTER
BACKEND = Path(__file__).resolve().parents[1]


# ------------------------------------------------------------ Teil 1: ohne Server
def _schaden(text, exakt=True):
    s = schaden_pruefung.pruefen(text, "Gebraucht", "Limousine", "")
    s["text_exakt"] = exakt
    return s


def test_01_urteil_gut_nur_bei_exakt_gelesenem_text():
    sauber = "Sehr gepflegt, Scheckheft, TÜV neu. Unfallfrei."
    assert dt.urteil(_schaden(sauber)) == "gut"
    # "es darf keine Lesefehler geben": nicht exakt gelesen -> nie "gut", ein Schadenswort koennte verlesen sein
    assert dt.urteil(_schaden(sauber, exakt=False)) == "pruefen"
    assert dt.urteil(_schaden("Kleiner Kratzer an der Stoßstange, sonst top.")) == "leicht"
    assert dt.urteil(_schaden("Kleiner Kratzer an der Stoßstange, sonst top.", exakt=False)) == "pruefen"
    # schwer zaehlt immer — auch aus einem unsicher gelesenen Text
    assert dt.urteil(_schaden("Motorschaden, nicht fahrbereit.")) == "schwer"
    assert dt.urteil(_schaden("Motorschaden, nicht fahrbereit.", exakt=False)) == "schwer"
    assert dt.urteil(_schaden("")) == "pruefen"                 # keine Beschreibung
    assert dt.urteil(None) == "pruefen" and dt.urteil({}) == "pruefen"
    assert [dt.rang({"urteil": u}) for u in ("gut", "leicht", "pruefen", "schwer", "?")] == [0, 1, 2, 3, 4]


def test_02_preislage_nur_mit_genug_vergleichsautos():
    karte = {"sample_size": 18, "median_sample_price": 9000, "p25_price": 7800, "min_price": 6500,
             "label": "VW Golf 2012–2015", "datenlage": "gut", "datenstand": "2026-11-27T05:00:00+00:00"}
    g = dt.preislage(7500, karte)
    assert g["lage"] == "guenstig" and g["mitte"] == 9000 and g["unter_mitte_eur"] == 1500 and g["anzahl"] == 18
    assert g["guenstigstes"] == 6500 and g["segment"] == "VW Golf 2012–2015"
    assert dt.preislage(8500, karte)["lage"] == "mittel"
    t = dt.preislage(11000, karte)
    assert t["lage"] == "teuer" and t["unter_mitte_eur"] == -2000
    # keine Aussage: kein Preis / 0 EUR / kein Segment / zu wenige Autos / keine Mitte
    assert dt.preislage(None, karte) is None and dt.preislage(0, karte) is None
    assert dt.preislage(7500, None) is None
    assert dt.preislage(7500, {**karte, "sample_size": dt.PREIS_MINDESTENS - 1}) is None
    assert dt.preislage(7500, {**karte, "median_sample_price": None}) is None
    # ohne unteres Viertel gibt es kein "guenstig"
    assert dt.preislage(5000, {**karte, "p25_price": None})["lage"] == "mittel"


def test_03_dokument_und_kennung():
    f = {"marke": "Volkswagen", "modell": "Tiguan", "titel": "VW Tiguan 1.4 Tsi TÜV neu", "ez_jahr": 2009, "ez_monat": 9,
         "kilometer": 204000, "preis": 3875, "quelle": "Kleinanzeigen", "inserat_id": "3583990986",
         "inserat_url": "https://www.kleinanzeigen.de/s-anzeige/3583990986", "kraftstoff": "", "hash_id": ""}
    jetzt = datetime(2026, 11, 27, 17, 0, tzinfo=timezone.utc)
    s = _schaden("Steuerkette wurde vor 2 Jahren erneuert. Rechte Seite ein paar Kratzer und kleine Dellen.")
    d = dt.dokument({"id": "u1", "dealer_id": "d1"}, {"id": "v1", "pc_name": "PC-A"}, f, {"zeit": "27.11.2026 17:24:22", "privat": True},
                    s, None, "Text " * 3000, "2026-11-27T17:00:00+00:00", jetzt)
    assert d["kennung"] == "kleinanzeigen:3583990986" and d["user_id"] == "u1" and d["dealer_id"] == "d1"
    assert d["urteil"] == "leicht" and d["erledigt"] is False and d["pc_name"] == "PC-A"
    assert (d["ablauf"] - jetzt).total_seconds() == dt.TREFFER_STUNDEN * 3600
    assert "kraftstoff" not in d["fahrzeug"] and d["fahrzeug"]["inserat_url"].endswith("3583990986")
    assert 5990 <= len(d["beschreibung"]) <= 6000                       # nie mehr gespeichert, als das Programm liest
    assert d["liste"]["zeit"] == "27.11.2026 17:24:22" and d["liste"]["privat"] is True
    assert d["liste"]["telefone"] == [] and d["liste"]["anbieter"] == "" and d["liste"]["email"] == ""
    # Telefon/E-Mail werden zu Knoepfen: nur was wie eine Nummer bzw. Adresse aussieht
    z = dt.listenzeile({"anbieter": "Frank  Patt", "telefone": ["+49 (0)163 2364007", "javascript:alert(1)", "0151/123-456", "x"],
                        "entfernung_km": 245, "ort": "D-45772 Marl", "email": "a b@x.de", "daten": "EZ: 11/2009, 169.500 Km"})
    assert z["telefone"] == ["+49 (0)163 2364007", "0151/123-456"] and z["anbieter"] == "Frank Patt"
    assert z["entfernung_km"] == 245 and z["ort"] == "D-45772 Marl" and z["email"] == "" and z["daten"].startswith("EZ:")
    assert dt.listenzeile({"email": "verkauf@beispiel.de", "entfernung_km": True})["email"] == "verkauf@beispiel.de"
    assert dt.listenzeile({"entfernung_km": True})["entfernung_km"] is None and dt.listenzeile(None)["telefone"] == []
    a = dt.antwort(d)
    assert "user_id" not in a and "dealer_id" not in a and "kennung" not in a and "ablauf" not in a
    assert a["urteil"] == "leicht" and a["fahrzeug"]["preis"] == 3875
    # ohne Nummer: dieselben Eckdaten -> dieselbe Kennung, anderes Auto -> andere
    ohne = {**f, "inserat_id": ""}
    assert dt.kennung(ohne) == dt.kennung(dict(ohne)) and dt.kennung(ohne).startswith("kleinanzeigen:~")
    assert dt.kennung(ohne) != dt.kennung({**ohne, "kilometer": 204001})
    assert dt.kennung({**f, "quelle": "Mobile.de"}) != dt.kennung(f)


def test_05_vertrag_aus_treffer_vorausgefuellt_und_fehlendes_genannt():
    f = {"marke": "Toyota", "modell": "Yaris", "titel": "Toyota Yaris 1.3", "ez_jahr": 2007, "ez_monat": 9, "kilometer": 185000,
         "ps": 87, "kw": 64, "preis": 2800, "quelle": "Kleinanzeigen", "inserat_id": "3584035902",
         "inserat_url": "https://www.kleinanzeigen.de/s-anzeige/3584035902"}
    liste = {"anbieter": "Semo", "telefone": ["+49 (0)151 5550177"], "ort": "D-40233 Flingern Süd", "privat": True}
    merkmale = [["Marke, Modell:", "Toyota Yaris"], ["Farbe", "Silber metallic"], ["HU", "bis 05/2028"], ["Sitze", "5"],
                ["Vorbesitzer", "2"], ["Hubraum", "1.298 cm³"], ["Türen", "2/3"], ["", "x"], ["leer", ""], "unsinn"]
    d = dt.dokument({"id": "u1", "dealer_id": "d1"}, {"id": "v1"}, f, liste, _schaden("TÜV neu. Unfallfrei."), None,
                    "TÜV neu. Unfallfrei.", "2026-11-27T17:00:00+00:00", None, merkmale, ["ABS", "Radio", "ABS", ""])
    assert [m["name"] for m in d["merkmale"]] == ["Marke, Modell", "Farbe", "HU", "Sitze", "Vorbesitzer", "Hubraum", "Türen"]
    assert d["ausstattung"] == ["ABS", "Radio"]
    # Kraftstoff und Getriebe standen nirgends: der Sucher erfaehrt es und traegt nach
    assert dt.fehlt(d) == ["Kraftstoff", "Getriebe"] and dt.antwort(d)["fehlt"] == ["Kraftstoff", "Getriebe"]
    v = dt.fahrzeug_fuer_vertrag(d)
    assert (v["make_label"], v["model_label"], v["first_registration"], v["mileage"]) == ("Toyota", "Yaris", "09/2007", 185000)
    assert (v["power_kw"], v["power_ps"], v["list_price"], v["mobile_ad_id"]) == (64, 87, 2800, "3584035902")
    assert v["features"] == ["ABS", "Radio"] and v["description"] == "TÜV neu. Unfallfrei."
    assert (v["color"], v["hu"], v["seats"], v["previous_owners"], v["doors"]) == ("Silber metallic", "05/2028", 5, 2, "2/3")
    assert (v["seller_name"], v["seller_phone"], v["seller_zip"], v["seller_city"]) == ("Semo", "+49 (0)151 5550177", "40233", "Flingern Süd")
    assert "fuel_label" not in v and "gearbox_label" not in v          # nichts erfunden
    # stehen sie in den Merkmalen, fehlt nichts und der Vertrag bekommt sie
    d2 = dt.dokument({"id": "u1", "dealer_id": "d1"}, {"id": "v1"}, f, liste, _schaden("ok"), None, "ok", "x", None,
                     [["Kraftstoff", "Benzin"], ["Getriebeart", "Schaltgetriebe"]], [])
    assert dt.fehlt(d2) == []
    v2 = dt.fahrzeug_fuer_vertrag(d2)
    assert (v2["fuel_label"], v2["gearbox_label"]) == ("Benzin", "Schaltgetriebe")
    # "Andere" / "Weitere VW" sind keine Marke bzw. kein Modell: Feld bleibt leer und wird genannt
    d3 = dt.dokument({"id": "u1", "dealer_id": "d1"}, {"id": "v1"}, {**f, "marke": "Andere", "modell": "", "kraftstoff": "Diesel", "getriebe": "Automatik"},
                     liste, _schaden("ok"), None, "ok", "x")
    assert dt.fehlt(d3) == ["Marke", "Modell"]
    v3 = dt.fahrzeug_fuer_vertrag(d3, {"make_label": "Andere", "model_label": "Weitere VW"})
    assert "make_label" not in v3 and "model_label" not in v3
    assert dt.fehlt({"fahrzeug": {"marke": "VW", "modell": "Weitere VW", "ez_jahr": 2010, "kraftstoff": "Benzin", "getriebe": "x"}}) == ["Modell"]


def test_06_html_reste_in_der_beschreibung():
    """Bild Ahmad 28.11.2026: AutoPointer zeigt bei AutoScout die Auszeichnung ohne spitze Klammern."""
    t = ("Verzurrösen Koffer-/Laderaum/li/ulbrbAusstattung:/bbrulliAbgedunkelte Scheiben/liliAmbiente-Beleuchtung"
         "/liliArmlehne/li/ulbrbTagfahrlicht (Art):/bbrulliAdaptives Kurvenlicht/li/ul")
    s = dt.html_reste(t)
    assert "/li" not in s and "ulli" not in s
    assert "• Abgedunkelte Scheiben\n• Ambiente-Beleuchtung\n• Armlehne" in s and "• Adaptives Kurvenlicht" in s
    assert s.startswith("Verzurrösen Koffer-/Laderaum\nAusstattung:")
    # normaler Text bleibt, wie er ist
    for normal in ("100 km/bar und/oder 1/2 Jahr", "Koffer-/Laderaum", "TÜV/AU neu, 8-fach bereift", "Preis VB / Tausch möglich", ""):
        assert dt.html_reste(normal) == normal, normal
    d = dt.dokument({"id": "u1", "dealer_id": "d1"}, {"id": "v1"}, {"marke": "Skoda", "modell": "Superb", "ez_jahr": 2022, "kilometer": 1},
                    None, _schaden("ok"), None, "Sitzheizung/liliNavi", "x")
    assert d["beschreibung"] == "Sitzheizung\n• Navi"
    assert dt.antwort({"beschreibung": "A/liliB"})["beschreibung"] == "A\n• B"          # auch Altbestand


def test_07_erkannte_schaeden_stehen_im_vertrag_unter_sonstige_schaeden():
    text = ("Verkaufe meinen Golf, TÜV neu. Rechte Seite ein paar Kratzer und kleine Dellen. Unfallfrei!\n"
            "Steuerkette wurde erneuert. Keine Kratzer am Dach.\n• Klimaanlage defekt\n• Sitzheizung")
    d = dt.dokument({"id": "u1", "dealer_id": "d1"}, {"id": "v1"}, {"marke": "VW", "modell": "Golf", "ez_jahr": 2012, "kilometer": 1},
                    {"art": "PKW, Gebraucht, Limousine"}, _schaden(text), None, text, "x")
    s = dt.schaeden_fuer_vertrag(d)
    assert "Rechte Seite ein paar Kratzer und kleine Dellen" in s and "Klimaanlage defekt" in s
    for nicht in ("Unfallfrei", "TÜV neu", "Steuerkette", "Keine Kratzer", "Sitzheizung", "…"):
        assert nicht not in s, (nicht, s)
    assert dt.fahrzeug_fuer_vertrag(d)["damage_note"] == s
    # nichts genannt -> Feld bleibt leer (nichts erfunden); Unfallwagen aus der Listenzeile steht vorne
    sauber = dt.dokument({"id": "u1", "dealer_id": "d1"}, {"id": "v1"}, {"marke": "VW", "modell": "Golf", "ez_jahr": 2012, "kilometer": 1},
                         None, _schaden("Top gepflegt, unfallfrei."), None, "Top gepflegt, unfallfrei.", "x")
    assert dt.schaeden_fuer_vertrag(sauber) == "" and "damage_note" not in dt.fahrzeug_fuer_vertrag(sauber)
    unfall = dt.dokument({"id": "u1", "dealer_id": "d1"}, {"id": "v1"}, {"marke": "VW", "modell": "Golf", "ez_jahr": 2012, "kilometer": 1},
                         {"art": "PKW, Unfallwagen, Limousine"}, _schaden("Motorschaden, nicht fahrbereit."), None, "Motorschaden, nicht fahrbereit.", "x")
    assert dt.schaeden_fuer_vertrag(unfall).startswith("Unfallwagen (laut Inserat); Motorschaden")
    assert len(dt.schaeden_fuer_vertrag({"beschreibung": ". ".join(f"Delle Nummer {i} an der Tür" for i in range(9))}).split("; ")) == 5


def test_04_verdrahtung():
    srv = (BACKEND / "server.py").read_text(encoding="utf-8")
    assert "include_router(dauerlauf_routes.router)" in srv
    idx = (BACKEND / "indizes.py").read_text(encoding="utf-8")
    assert '"dauerlauf_treffer_ablauf", "expireAfterSeconds": 0' in idx and '"dauerlauf_treffer_kennung", "unique": True' in idx
    adm = (BACKEND / "routes" / "admin.py").read_text(encoding="utf-8")
    assert '"dauerlauf_treffer",' in adm and 'db.dauerlauf_treffer.delete_many({"user_id": user_id})' in adm
    route = (BACKEND / "routes" / "dauerlauf.py").read_text(encoding="utf-8")
    # kein Vergleich, kein Abruf, kein Tageslimit in dieser Route
    for verboten in ("programm_tageslimit", "_vorab_abrufen", "vergleichs_links", "provider_fetch"):
        assert verboten not in route, verboten


# ------------------------------------------------------------ Teil 2: ueber HTTP
TIGUAN = {"marke": "VW", "modell": "Tiguan", "marke_modell_text": "VW Tiguan", "titel": "VW Tiguan 1.4 Tsi TÜV neu",
          "ez_monat": 9, "ez_jahr": 2009, "kilometer": 204000, "kw": 110, "ps": 150, "kraftstoff": "Benzin",
          "getriebe": "Schaltgetriebe", "preis": 3875, "zustand": "Gebraucht", "kategorie": "SUV/Geländewagen/Pickup",
          "quelle": "Kleinanzeigen", "inserat_id": "3583990986", "roh": True,
          "beschreibung_voll": "Befindet sich technisch in einen super Zustand\nSteuerkette wurde vor 2 Jahren erneuert\n"
                               "Rechte Seite sind ein paar Kratzer und kleine Dellen",
          "beschreibung_exakt": True}


def _melden(kopf, fahrzeug, liste=None):
    return requests.post(f"{API}/werkzeuge/{WID}/dauerlauf/treffer", headers=kopf,
                         json={"fahrzeug": fahrzeug, "liste": liste or {"zeit": "27.11.2026 17:24:22", "privat": True}}, timeout=30)


def _liste(kopf, **params):
    r = requests.get(f"{API}/dauerlauf/treffer", headers=kopf, params=params, timeout=30)
    assert r.status_code == 200, r.text
    return r.json()


def test_10_treffer_melden_und_in_der_liste_sehen(welt):  # noqa: F811
    db = welt["db"]
    db.dauerlauf_treffer.delete_many({"dealer_id": welt["firma"]["dealer_id"]})
    _abo(welt, True)
    _, prog, _ = _verbinden(welt, "PC-Dauerlauf")
    vorher = db.werkzeug_vergleiche.count_documents({"user_id": welt["sucher_id"]})
    r = _melden(prog, TIGUAN)
    assert r.status_code == 200, r.text
    j = r.json()
    assert j["neu"] is True and j["urteil"] == "leicht" and j["schaden"]["text_exakt"] is True
    assert j["inserat_url"] == "https://www.kleinanzeigen.de/s-anzeige/3583990986"
    # kein Vergleich protokolliert, kein Abruf angestossen
    assert db.werkzeug_vergleiche.count_documents({"user_id": welt["sucher_id"]}) == vorher
    # dasselbe Inserat noch einmal: derselbe Eintrag
    r2 = _melden(prog, {**TIGUAN, "preis": 3800})
    assert r2.status_code == 200 and r2.json()["neu"] is False and r2.json()["id"] == j["id"]
    assert db.dauerlauf_treffer.count_documents({"user_id": welt["sucher_id"]}) == 1
    # schwerer Schaden und nicht exakt gelesener Text
    assert _melden(prog, {**TIGUAN, "inserat_id": "3583990111", "beschreibung_voll": "Motorschaden, nicht fahrbereit"}).json()["urteil"] == "schwer"
    assert _melden(prog, {**TIGUAN, "inserat_id": "3583990222", "beschreibung_voll": "Top Zustand, unfallfrei",
                          "beschreibung_exakt": False}).json()["urteil"] == "pruefen"
    assert _melden(prog, {**TIGUAN, "inserat_id": "3583990333", "beschreibung_voll": "Top Zustand, unfallfrei"}).json()["urteil"] == "gut"

    for wer in ("sucher", "chef"):                               # der Chef sieht die Treffer seiner Firma
        liste = _liste(welt[wer])
        assert liste["gesamt"] == 4 and liste["stunden"] == dt.TREFFER_STUNDEN, wer
        urteile = {t["fahrzeug"]["inserat_id"]: t["urteil"] for t in liste["treffer"]}
        assert urteile == {"3583990986": "leicht", "3583990222": "pruefen", "3583990333": "gut"}, wer      # schwer fehlt
        eins = next(t for t in liste["treffer"] if t["fahrzeug"]["inserat_id"] == "3583990986")
        assert eins["fahrzeug"]["preis"] == 3800 and eins["liste"]["privat"] is True and "Steuerkette" in eins["beschreibung"]
        assert "user_id" not in eins and "kennung" not in eins
    assert len(_liste(welt["sucher"], alle=True)["treffer"]) == 4

    # erledigt: verschwindet aus der Liste, bleibt bei erneutem Lesen erledigt
    r = requests.post(f"{API}/dauerlauf/treffer/{j['id']}/erledigt", headers=welt["sucher"], timeout=30)
    assert r.status_code == 200 and r.json()["erledigt"] is True
    assert "3583990986" not in {t["fahrzeug"]["inserat_id"] for t in _liste(welt["sucher"])["treffer"]}
    assert _melden(prog, TIGUAN).json()["neu"] is False
    assert "3583990986" not in {t["fahrzeug"]["inserat_id"] for t in _liste(welt["sucher"])["treffer"]}
    r = requests.post(f"{API}/dauerlauf/treffer/{j['id']}/erledigt", headers=welt["sucher"], params={"zurueck": True}, timeout=30)
    assert r.status_code == 200 and r.json()["erledigt"] is False


def test_10b_kaufvertrag_aus_treffer_ohne_link_und_ohne_abruf(welt):  # noqa: F811
    """„Kaufvertrag“ in der Trefferliste: Fahrzeug liegt sofort im Pool, vorausgefuellt; Fehlendes wird genannt."""
    db = welt["db"]
    _abo(welt, True)
    _, prog, _ = _verbinden(welt, "PC-Dauerlauf")
    yaris = {**TIGUAN, "marke": "Toyota", "modell": "Yaris", "marke_modell_text": "Toyota Yaris", "titel": "Toyota Yaris 1.3",
             "ez_jahr": 2007, "kilometer": 185000, "kw": 64, "ps": 87, "preis": 2800, "kraftstoff": "", "getriebe": "",
             "kategorie": "Kleinwagen", "inserat_id": "3584035902", "beschreibung_voll": "TÜV neu. Unfallfrei."}
    r = requests.post(f"{API}/werkzeuge/{WID}/dauerlauf/treffer", headers=prog, timeout=30, json={
        "fahrzeug": yaris,
        "liste": {"zeit": "27.11.2026 19:00:00", "privat": True, "anbieter": "Semo", "telefone": ["+49 (0)151 5550177"],
                  "ort": "D-40233 Flingern Süd"},
        "merkmale": [["Farbe:", "Silber metallic"], ["HU:", "bis 05/2028"], ["Sitze:", "5"]],
        "ausstattung": ["ABS", "Radio"]})
    assert r.status_code == 200, r.text
    tid = r.json()["id"]
    eintrag = next(t for t in _liste(welt["sucher"])["treffer"] if t["id"] == tid)
    assert eintrag["fehlt"] == ["Kraftstoff", "Getriebe"] and eintrag["ausstattung"] == ["ABS", "Radio"]

    r = requests.post(f"{API}/dauerlauf/treffer/{tid}/vertrag", headers=welt["sucher"], timeout=30)
    assert r.status_code == 200, r.text
    j = r.json()
    assert j["fehlt"] == ["Kraftstoff", "Getriebe"] and j["vehicle_id"]
    v = j["vehicle"]
    assert (v["make_label"], v["model_label"], v["mileage"], v["list_price"]) == ("Toyota", "Yaris", 185000, 2800)
    assert v["features"] == ["ABS", "Radio"] and v["description"] == "TÜV neu. Unfallfrei." and v["color"] == "Silber metallic"
    assert (v["seller_name"], v["seller_phone"], v["seller_zip"]) == ("Semo", "+49 (0)151 5550177", "40233")
    assert not v.get("fuel_label") and not v.get("gearbox_label")          # nichts erfunden
    assert not v.get("damage_note")                                        # „TÜV neu. Unfallfrei.“ nennt keinen Schaden
    # das Fahrzeug liegt im Pool der Firma, der Treffer kennt es; ein zweiter Klick legt kein zweites an
    assert db.vehicles.count_documents({"id": j["vehicle_id"], "dealer_id": welt["firma"]["dealer_id"]}) == 1
    assert db.dauerlauf_treffer.find_one({"id": tid})["vehicle_id"] == j["vehicle_id"]
    r2 = requests.post(f"{API}/dauerlauf/treffer/{tid}/vertrag", headers=welt["sucher"], timeout=30)
    assert r2.status_code == 200 and r2.json()["vehicle_id"] == j["vehicle_id"]
    assert db.vehicles.count_documents({"id": j["vehicle_id"]}) == 1
    # fremde Firma und unbekannter Treffer
    assert requests.post(f"{API}/dauerlauf/treffer/{tid}/vertrag", headers=welt["andere"], timeout=30).status_code == 404
    assert requests.post(f"{API}/dauerlauf/treffer/gibts-nicht/vertrag", headers=welt["sucher"], timeout=30).status_code == 404
    db.vehicles.delete_many({"id": j["vehicle_id"]})


def test_11_fremde_und_unverbundene_kommen_nicht_heran(welt):  # noqa: F811
    assert _melden({wz.TOKEN_KOPF: "falsch"}, TIGUAN).status_code == 401
    assert _melden({}, TIGUAN).status_code == 401
    # andere Firma: keine Liste (Programm nicht freigeschaltet), fremder Treffer nicht aenderbar
    assert requests.get(f"{API}/dauerlauf/treffer", headers=welt["andere"], timeout=30).status_code == 404
    eigener = welt["db"].dauerlauf_treffer.find_one({"dealer_id": welt["firma"]["dealer_id"]}, {"_id": 0, "id": 1})
    assert eigener
    assert requests.post(f"{API}/dauerlauf/treffer/{eigener['id']}/erledigt", headers=welt["andere"], timeout=30).status_code == 404
    assert requests.post(f"{API}/dauerlauf/treffer/gibts-nicht/erledigt", headers=welt["sucher"], timeout=30).status_code == 404
    # die Erweiterung hat hier nichts zu suchen
    assert requests.post(f"{API}/werkzeuge/{wz.BROWSER_HELFER}/dauerlauf/treffer", headers={wz.TOKEN_KOPF: "x"},
                         json={"fahrzeug": TIGUAN}, timeout=30).status_code == 404
    # Pflichtfelder wie beim Vergleich
    assert _melden({wz.TOKEN_KOPF: "falsch"}, {"marke": "VW"}).status_code == 422
    welt["db"].dauerlauf_treffer.delete_many({"dealer_id": welt["firma"]["dealer_id"]})
