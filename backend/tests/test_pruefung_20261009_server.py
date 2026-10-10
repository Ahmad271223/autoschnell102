# -*- coding: utf-8 -*-
"""Pruefung 09.10.2026 (Sicherheits-/Robustheits-Review des Servers, Wunsch Ahmad "baue das System besser und
sicherer"): unsichere Inserat-Nummern, Erkennungs-Pool mit Wartegrenze, Probelauf-Bremse, Lernen nur im Ernstfall
und je Firma begrenzt, Lesebild-Grenzen, "Vertrag" nur auf eigene Lesung — und der Mokka-e-Befund (mobile.de-Seite
ohne '"listing":{"attributes":' am Anfang)."""
import asyncio
import base64
import io
import os
import sys
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import browser_helfer as bh  # noqa: E402
import erkennung_lernen as el  # noqa: E402
import werkzeuge as wz  # noqa: E402
from test_browser_helfer_20261004 import (  # noqa: E402
    MOBILE_ID, MOBILE_URL, _flight_html, _identity, _mobile_inserat_html, _mobile_listing)

VW = {"make": "VOLKSWAGEN", "make_label": "Volkswagen", "model": "T-Roc", "model_label": "T-Roc",
      "first_registration": "06/2020", "mileage": 48000, "fuel": "PETROL", "fuel_label": "Benzin",
      "gearbox": "MANUAL_GEAR", "power_kw": 110}


# ------------------------------------------------------------ ohne Server
def test_01_mobile_listing_auch_wenn_attributes_nicht_vorne_steht():
    """Befund Ahmad 09.10.2026 (Opel Mokka-e): "Auf der Seite stehen keine Inseratsdaten" — das Inserat-Objekt begann
    nicht mit "attributes". Jetzt zaehlt jedes "listing"-Objekt mit Attributen; die passende Nummer gewinnt."""
    text = bh.next_flight_text(_flight_html(
        '2:{"listing":null}\n',
        '3:["$","div",null,{"listing":{"id":"555","adType":"NEW","attributes":[{"tag":"fuel","value":"Elektro"}]}}]\n'))
    assert bh.mobile_listing_objekt(text, "555")["adType"] == "NEW"
    # zwei Inserate auf der Seite (z. B. Haendler-Empfehlung zuerst): das mit der Nummer gewinnt
    text = bh.next_flight_text(_flight_html(
        '3:{"listing":{"id":"111","attributes":[{"tag":"fuel","value":"Diesel"}]}}\n',
        '4:{"listing":{"id":"555","attributes":[{"tag":"fuel","value":"Elektro"}]}}\n'))
    assert bh.mobile_listing_objekt(text, "555")["id"] == "555"
    assert bh.mobile_listing_objekt(text, "999")["id"] == "111", "unbekannte Nummer: das erste (-> 'anderes Inserat')"
    # ohne Attribute kein Inserat
    assert bh.mobile_listing_objekt(bh.next_flight_text(_flight_html('3:{"listing":{"id":"555"}}\n')), "555") is None
    assert bh.mobile_listing_objekt("", "555") is None
    # der ganze Weg wie live, nur mit "id" und "price" VOR "attributes"
    listing = _mobile_listing()
    umgestellt = {"id": listing["id"], "price": listing.get("price"), **listing}
    assert list(umgestellt)[0] == "id"
    fz, _ = bh.inserat_auslesen(_identity(MOBILE_URL), MOBILE_URL, _mobile_inserat_html(umgestellt))
    assert fz["make_label"] == "Volkswagen" and fz["mobile_ad_id"] == MOBILE_ID
    with pytest.raises(bh.SeiteUngueltig, match="anderen Inserat"):
        bh.inserat_auslesen(_identity(MOBILE_URL), MOBILE_URL,
                            _mobile_inserat_html({"id": 123456789, **{k: v for k, v in listing.items() if k != "id"}}))


class _FakeSammlung:
    def __init__(self, anzahl, bekannt):
        self.anzahl, self.bekannt, self.schreib = anzahl, bekannt, []

    async def count_documents(self, filt, limit=None):
        if "_id" in filt:
            return 1 if self.bekannt else 0
        return min(self.anzahl, limit or self.anzahl)

    async def update_one(self, *a, **k):
        self.schreib.append(a)


class _FakeDb:
    def __init__(self, s):
        self.s = s

    def __getitem__(self, name):
        return self.s


def test_02_lernen_hoechstens_je_firma():
    """Haertung (M3): ab JE_FIRMA_HOECHSTENS gelesenen Texten je Firma kommt kein NEUER dazu — bekannte Texte bekommen
    weiter Belege."""
    voll_neu = _FakeSammlung(el.JE_FIRMA_HOECHSTENS, bekannt=False)
    asyncio.run(el.lernen(_FakeDb(voll_neu), "VW Xy", "VW", "Golf", "mobile:1", "firma-a"))
    assert voll_neu.schreib == []
    voll_bekannt = _FakeSammlung(el.JE_FIRMA_HOECHSTENS, bekannt=True)
    asyncio.run(el.lernen(_FakeDb(voll_bekannt), "VW Xy", "VW", "Golf", "mobile:1", "firma-a"))
    assert len(voll_bekannt.schreib) == 1
    platz = _FakeSammlung(el.JE_FIRMA_HOECHSTENS - 1, bekannt=False)
    asyncio.run(el.lernen(_FakeDb(platz), "VW Xy", "VW", "Golf", "mobile:1", "firma-a"))
    assert len(platz.schreib) == 1
    # ohne Firma wird nie gelernt (Haertung 09.10.)
    ohne = _FakeSammlung(0, bekannt=False)
    asyncio.run(el.lernen(_FakeDb(ohne), "VW Xy", "VW", "Golf", "mobile:1", None))
    assert ohne.schreib == []


def test_03_lesebild_eingabe_begrenzt():
    """(N1) fahrzeug ist ein kleines Modell: fremde Schluessel fallen weg, zu lange Werte sind ein Fehler; 4 MP."""
    from pydantic import ValidationError
    from routes.werkzeuge import LESEBILD_PIXEL_MAX, LesebildIn
    assert LESEBILD_PIXEL_MAX == 4_000_000
    body = {"grund": "modell_unbekannt", "bild": "x" * 40,
            "fahrzeug": {"marke_modell_text": "Opel Mokka-e", "fremd": "egal", "tief": {"a": 1}}}
    assert LesebildIn(**body).fahrzeug.model_dump() == {"marke_modell_text": "Opel Mokka-e", "titel": None,
                                                        "quelle": None, "inserat_id": None}
    with pytest.raises(ValidationError):
        LesebildIn(**{**body, "fahrzeug": {"marke_modell_text": "x" * 161}})
    assert LesebildIn(grund="modell_unbekannt", bild="x" * 40, fahrzeug=None).fahrzeug is None


# ------------------------------------------------------------ ueber HTTP (RUNDE14_HTTP=1, wie die anderen Werkzeug-Tests)
import requests  # noqa: E402

pytestmark_http = pytest.mark.skipif(not os.environ.get("RUNDE14_HTTP"), reason="nur gegen ein laufendes Backend")

if os.environ.get("RUNDE14_HTTP"):
    from test_browser_helfer_20261004 import welt  # noqa: E402,F401 — dieselbe Testfirma (Kunde 10002) wie dort


def _pc(welt, name):
    import test_browser_helfer_20261004 as tb
    return {**tb._verbinden(welt, "sucher", wid=wz.AUTOPOINTER, name=name),
            "User-Agent": "AutoSchnell-Vergleich/1.5.13"}


def _png(breite=640, hoehe=240) -> bytes:
    from PIL import Image
    buf = io.BytesIO()
    Image.new("RGB", (breite, hoehe), "white").save(buf, "PNG")
    return buf.getvalue()


@pytestmark_http
def test_10_reparierte_nummer_ist_nie_sicher(welt):
    """(M1) "4870O02O1" wird zu 487000201 repariert. Gehoert das Inserat dazu zu einem ANDEREN Auto (EZ passt nicht):
    kein Link, Hinweis. Ohne Daten: Link bleibt, aber Hinweis. Passt es: wie bisher, kein Hinweis."""
    import test_browser_helfer_20261004 as tb
    db = welt["db"]
    pc = _pc(welt, "PC-Nummer")
    jetzt = datetime.now(timezone.utc)
    nummern = ["487000201", "487000202", "487000203"]

    def speicher(nr, **anders):
        db.listings_cache.update_one({"cache_key": f"mobile:{nr}"}, {"$set": {
            "cache_key": f"mobile:{nr}", "source": "mobile", "item_id": nr, "data": {**VW, **anders},
            "expires_at": jetzt + timedelta(days=1)}}, upsert=True)

    def vergleich(nr_gelesen):
        f = {"marke": "VW", "modell": "", "marke_modell_text": "VW T-Roc", "titel": "VW T-Roc", "ez_monat": 6,
             "ez_jahr": 2020, "kilometer": 48100, "kw": 110, "kraftstoff": "Benzin", "getriebe": "Schaltgetriebe",
             "preis": 21000, "quelle": "mobile.de", "inserat_id": nr_gelesen, "roh": True}
        r = requests.post(f"{tb.API}/werkzeuge/{wz.AUTOPOINTER}/vergleich", headers=pc, json={"fahrzeug": f},
                          timeout=60)
        assert r.status_code == 200, r.text
        return r.json()

    try:
        speicher(nummern[0], first_registration="06/2012")          # anderes Auto hinter der reparierten Nummer
        d = vergleich("4870O02O1")
        assert d["inserat_url"] is None
        assert any("unsicher gelesen" in h and "anderes Auto" in h for h in d["melden"]), d["melden"]
        doc = db.werkzeug_vergleiche.find_one({"user_id": welt["sucher_id"], "fahrzeug.inserat_id": "4870O02O1"})
        assert doc and doc["fahrzeug"]["inserat_nummer_unsicher"] is True and not doc["fahrzeug"]["inserat_url"]
        # keine Daten zur Nummer: Link zum Oeffnen/Lesen bleibt, aber der Hinweis
        d = vergleich("4870O02O2")
        assert d["inserat_url"] and d["inserat_url"].endswith("id=487000202")
        assert any("487000202 unsicher gelesen" in h and "4870O02O2" in h for h in d["melden"]), d["melden"]
        # die Daten passen: alles wie bisher
        speicher(nummern[2])
        d = vergleich("4870O02O3")
        assert d["inserat_url"].endswith("id=487000203") and d["fahrzeug"]["modell"] == "T-Roc"
        assert not any("unsicher" in h for h in d["melden"]), d["melden"]
        # eine sauber gelesene Nummer bekommt nie den Hinweis
        d = vergleich("487000204")
        assert not any("unsicher" in h for h in d["melden"])
    finally:
        db.listings_cache.delete_many({"cache_key": {"$in": [f"mobile:{n}" for n in nummern]}})
        db.werkzeug_vergleiche.delete_many({"user_id": welt["sucher_id"]})
        db.link_jobs.delete_many({"url": {"$regex": "4870002"}})


@pytestmark_http
def test_11_probelauf_lernt_nicht_und_ist_gebremst(welt):
    """(M2/M3) Der Probelauf zaehlt zu keinem Tageslimit — darum lernt er nichts und hat einen eigenen Minuten-Zaehler."""
    import test_browser_helfer_20261004 as tb
    db = welt["db"]
    pc = _pc(welt, "PC-Probe")
    jetzt = datetime.now(timezone.utc)
    nr = "487000301"
    text = "VW ZZ" + uuid.uuid4().hex[:5]
    k = el.doc_id(welt["firma"]["dealer_id"], text)

    def vergleich(probelauf=True):
        f = {"marke": "VW", "modell": "", "marke_modell_text": text, "titel": text, "ez_monat": 6, "ez_jahr": 2020,
             "kilometer": 48100, "kw": 110, "kraftstoff": "Benzin", "getriebe": "Schaltgetriebe", "preis": 21000,
             "quelle": "mobile.de", "inserat_id": nr, "roh": True}
        return requests.post(f"{tb.API}/werkzeuge/{wz.AUTOPOINTER}/vergleich", headers=pc,
                             json={"fahrzeug": f, "probelauf": probelauf}, timeout=60)

    try:
        db.listings_cache.update_one({"cache_key": f"mobile:{nr}"}, {"$set": {
            "cache_key": f"mobile:{nr}", "source": "mobile", "item_id": nr, "data": dict(VW),
            "expires_at": jetzt + timedelta(days=1)}}, upsert=True)
        r = vergleich(probelauf=True)
        assert r.status_code == 200, r.text
        assert r.json()["fahrzeug"]["modell"] == "T-Roc", "die echten Daten gelten auch im Probelauf"
        assert db.erkennung_gelernt.count_documents({"_id": k}) == 0, "aber gelernt wird im Probelauf nicht"
        # Bremse: hoechstens 20 Probelaeufe je Minute und Konto
        stati = [vergleich(probelauf=True).status_code for _ in range(22)]
        assert 429 in stati and stati[0] == 200, stati
        assert vergleich(probelauf=False).status_code == 200, "ein echter Vergleich geht weiter"
    finally:
        db.listings_cache.delete_many({"cache_key": f"mobile:{nr}"})
        db.erkennung_gelernt.delete_many({"_id": k})
        db.werkzeug_vergleiche.delete_many({"user_id": welt["sucher_id"]})
        db.rate_limits.delete_many({"_id": {"$regex": "^werkzeug_probelauf:"}})
        db.link_jobs.delete_many({"url": {"$regex": "487000301"}})


@pytestmark_http
def test_12_vertrag_zaehlt_nur_eigene_lesung_oder_speicher(welt):
    """(H1) /inserat-gelesen: eine Lesung einer FREMDEN Firma gilt nicht als "gelesen" — das Programm oeffnet dann das
    Inserat und die eigene Erweiterung liest es. Eigene Firma oder Server-Speicher gelten."""
    import test_browser_helfer_20261004 as tb
    db = welt["db"]
    pc = _pc(welt, "PC-Vertrag")
    nr = "487000401"
    url = f"https://suchen.mobile.de/fahrzeuge/details.html?id={nr}"
    jetzt = datetime.now(timezone.utc)

    def lesung(dealer_id, user_id):
        db.werkzeug_inserate.insert_one({
            "cache_key": f"mobile:{nr}", "user_id": user_id, "dealer_id": dealer_id, "source": "mobile",
            "item_id": nr, "url": url, "data": dict(VW), "pruefsumme": "x", "gelesen_am": jetzt,
            "ablauf": jetzt + timedelta(hours=1)})

    def gelesen():
        r = requests.get(f"{tb.API}/werkzeuge/{wz.AUTOPOINTER}/inserat-gelesen", headers=pc, params={"url": url},
                         timeout=30)
        assert r.status_code == 200, r.text
        return r.json()

    try:
        assert gelesen() == {"gelesen": False}
        lesung("fremde-firma-" + uuid.uuid4().hex[:6], "fremdes-konto")
        assert gelesen() == {"gelesen": False}, "fremde Lesung zaehlt nicht"
        lesung(welt["firma"]["dealer_id"], "kollege-" + uuid.uuid4().hex[:6])
        assert gelesen() == {"gelesen": True, "quelle": "browser"}, "Kollege derselben Firma zaehlt"
        db.werkzeug_inserate.delete_many({"cache_key": f"mobile:{nr}"})
        db.listings_cache.update_one({"cache_key": f"mobile:{nr}"}, {"$set": {
            "cache_key": f"mobile:{nr}", "source": "mobile", "item_id": nr, "data": dict(VW),
            "expires_at": jetzt + timedelta(days=1)}}, upsert=True)
        assert gelesen() == {"gelesen": True, "quelle": "speicher"}
    finally:
        db.werkzeug_inserate.delete_many({"cache_key": f"mobile:{nr}"})
        db.listings_cache.delete_many({"cache_key": f"mobile:{nr}"})


@pytestmark_http
def test_13_lesebild_grenzen(welt):
    """(N1) fahrzeug mit fremden Schluesseln wird angenommen (nur die vier Felder bleiben); mehr als 4 Megapixel im
    PNG-Kopf -> 422; null als fahrzeug geht."""
    import test_browser_helfer_20261004 as tb
    db = welt["db"]
    pc = _pc(welt, "PC-Bild")
    body = {"grund": "modell_unbekannt", "fehlt": [], "rohtext": "Marke, Modell: Opel Mokka-e", "vorgang_id": None,
            "fahrzeug": {"marke_modell_text": "Opel Mokka-e", "titel": "", "quelle": "mobile.de", "inserat_id": "",
                         "fremd": "egal", "tief": {"a": 1}},
            "bild": base64.b64encode(_png()).decode()}
    try:
        r = requests.post(f"{tb.API}/werkzeuge/{wz.AUTOPOINTER}/lesebild", headers=pc, json=body, timeout=60)
        assert r.status_code == 200, r.text
        doc = db.werkzeug_lesebilder.find_one({"id": r.json()["id"]})
        assert doc["fahrzeug"] == {"marke_modell_text": "Opel Mokka-e", "quelle": "mobile.de"}
        gross = bytearray(_png(64, 64))
        gross[16:20], gross[20:24] = (2100).to_bytes(4, "big"), (2000).to_bytes(4, "big")      # 4,2 MP
        r = requests.post(f"{tb.API}/werkzeuge/{wz.AUTOPOINTER}/lesebild", headers=pc, timeout=30,
                          json={**body, "bild": base64.b64encode(bytes(gross)).decode()})
        assert r.status_code == 422, r.text
        r = requests.post(f"{tb.API}/werkzeuge/{wz.AUTOPOINTER}/lesebild", headers=pc, timeout=30,
                          json={**body, "fahrzeug": {"marke_modell_text": "x" * 161}})
        assert r.status_code == 422
        r = requests.post(f"{tb.API}/werkzeuge/{wz.AUTOPOINTER}/lesebild", headers=pc, timeout=60,
                          json={**body, "fahrzeug": None})
        assert r.status_code == 200, r.text
    finally:
        db.werkzeug_lesebilder.delete_many({"dealer_id": welt["firma"]["dealer_id"]})
        db.rate_limits.delete_many({"_id": {"$regex": "^werkzeug_lesebild:"}})


@pytestmark_http
def test_14_nicht_lesbare_seite_fuer_den_betreiber(welt):
    """Befund Ahmad 09.10.2026 (Opel Mokka-e): eine Seite, die die Erweiterung nicht lesen konnte, bleibt 14 Tage fuer
    den Betreiber — Liste ohne die Seite, Download entpackt als Text (nie als HTML gerendert), Loeschen."""
    import konten
    import test_browser_helfer_20261004 as tb
    db = welt["db"]
    kopf = tb._verbinden(welt, "sucher", name="Edge · Leseseite")
    url = "https://suchen.mobile.de/fahrzeuge/details.html?id=487000501"
    try:
        r = requests.post(f"{tb.API}/werkzeuge/{tb.WID}/inserat", headers=kopf, timeout=60,
                          json={"url": url, "seite": tb._seite("<html><body>Zugriff verweigert</body></html>")})
        assert r.status_code == 422 and "keine Inseratsdaten" in r.text, r.text
        d = db.werkzeug_leseseiten.find_one({"url": url})
        assert d and d["dealer_id"] == welt["firma"]["dealer_id"] and d["grund"].startswith("Auf der Seite"), d
        ablauf = d["ablauf"] if d["ablauf"].tzinfo else d["ablauf"].replace(tzinfo=timezone.utc)   # pymongo: naiv
        assert d["seite"] and ablauf > datetime.now(timezone.utc) + timedelta(days=13)
        r = requests.get(f"{tb.API}/admin/werkzeug-leseseiten", headers=konten.super_kopf(), timeout=30)
        assert r.status_code == 200, r.text
        eintrag = next(x for x in r.json()["leseseiten"] if x["id"] == d["id"])
        assert "seite" not in eintrag and eintrag["portal"] == "mobile" and eintrag["item_id"] == "487000501"
        assert eintrag["firma"] and eintrag["konto"]
        r = requests.get(f"{tb.API}/admin/werkzeug-leseseiten/{d['id']}/seite", headers=konten.super_kopf(),
                         timeout=30)
        assert r.status_code == 200 and r.headers["content-type"].startswith("text/plain"), r.headers
        assert "attachment" in r.headers.get("content-disposition", "") and "Zugriff verweigert" in r.text
        assert r.headers.get("x-content-type-options") == "nosniff"
        # ohne Betreiber-Anmeldung nicht (der Erweiterungs-Schluessel ist keine Anmeldung)
        assert requests.get(f"{tb.API}/admin/werkzeug-leseseiten", headers=kopf, timeout=30).status_code in (401, 403)
        assert requests.delete(f"{tb.API}/admin/werkzeug-leseseiten/{d['id']}", headers=konten.super_kopf(),
                               timeout=30).status_code == 200
        assert db.werkzeug_leseseiten.count_documents({"id": d["id"]}) == 0
        assert requests.delete(f"{tb.API}/admin/werkzeug-leseseiten/{d['id']}", headers=konten.super_kopf(),
                               timeout=30).status_code == 404
    finally:
        db.werkzeug_leseseiten.delete_many({"url": url})
        db.rate_limits.delete_many({"_id": {"$regex": "^werkzeug_leseseite:"}})


# ------------------------------------------------------------ Vertragsweg (Befund Ahmad 09.10.2026: "Vertrag haengt")
@pytestmark_http
def test_15_app_start_mit_zustand_auch_ohne_anmeldung(welt):
    """Die App meldet den Start mit Zustand (offen / nachgefragt / anmeldung / abo) — auch ohne Anmeldung; die juengste
    Meldung gilt; eine angemeldete Meldung sieht nur das Programm derselben Firma."""
    import test_browser_helfer_20261004 as tb
    db = welt["db"]
    pc = _pc(welt, "PC-Start")
    frage = lambda s: requests.get(f"{tb.API}/werkzeuge/{wz.AUTOPOINTER}/app-start/{s}", headers=pc, timeout=30).json()  # noqa: E731
    start = uuid.uuid4().hex
    try:
        # Anmeldeseite: ohne Anmeldung, ohne Koerper -> "anmeldung"
        r = requests.post(f"{tb.API}/werkzeuge/app-start/{start}", timeout=30)
        assert r.status_code == 200 and r.json()["zustand"] == "anmeldung", r.text
        assert frage(start) == {"bestaetigt": True, "zustand": "anmeldung", "angemeldet": False}
        # nach der Anmeldung meldet die Vergleichsseite "offen" -> die juengste Meldung gilt
        r = requests.post(f"{tb.API}/werkzeuge/app-start/{start}", headers=welt["sucher"], json={"zustand": "offen"},
                          timeout=30)
        assert r.status_code == 200, r.text
        assert frage(start) == {"bestaetigt": True, "zustand": "offen", "angemeldet": True}
        # etwas ungespeichert: "nachgefragt" — das Programm sagt dem Sucher, dass in der App ein Dialog wartet
        requests.post(f"{tb.API}/werkzeuge/app-start/{start}", headers=welt["sucher"], json={"zustand": "nachgefragt"},
                      timeout=30)
        assert frage(start)["zustand"] == "nachgefragt"
        # unbekannter Zustand -> 422; abgelaufenes/fremdes Token zaehlt wie ohne Anmeldung
        assert requests.post(f"{tb.API}/werkzeuge/app-start/{start}", headers=welt["sucher"], json={"zustand": "x"},
                             timeout=30).status_code == 422
        s2 = uuid.uuid4().hex
        r = requests.post(f"{tb.API}/werkzeuge/app-start/{s2}", headers={"Authorization": "Bearer kaputt"},
                          json={"zustand": "offen"}, timeout=30)
        assert r.status_code == 200 and r.json()["zustand"] == "anmeldung"
        # angemeldete Meldung einer anderen Firma: fuer unser Programm nicht bestaetigt
        s3 = uuid.uuid4().hex
        requests.post(f"{tb.API}/werkzeuge/app-start/{s3}", headers=welt["andere"], timeout=30)
        assert frage(s3) == {"bestaetigt": False}
    finally:
        db.werkzeug_app_starts.delete_many({"start": {"$in": [start, s2, s3]}})
        db.rate_limits.delete_many({"_id": {"$regex": "^werkzeug_app_start:"}})


@pytestmark_http
def test_16_statusabfrage_nimmt_nachtraegliche_lesung(welt):
    """Kam die Lesung der Erweiterung erst NACH dem Einreihen, wartete die Seite bis 120 s auf den Anbieter-Abruf.
    Jetzt: Statusabfrage -> completed (browser_helfer), der Wartende steigt aus, ohne Wartende ist der Job weg."""
    import test_browser_helfer_20261004 as tb
    from datetime import datetime as _dt
    db = welt["db"]
    nr = "487000601"
    url = f"https://suchen.mobile.de/fahrzeuge/details.html?id={nr}"
    jetzt = datetime.now(timezone.utc)
    job_id = str(uuid.uuid4())
    try:
        db.link_jobs.insert_one({
            "id": job_id, "cache_key": f"mobile:{nr}", "source": "mobile", "item_id": nr, "url": url,
            "status": "queued", "active": True, "attempts": 0, "error": None,
            "requested_by_dealer": welt["firma"]["dealer_id"], "requested_by_user": welt["sucher_id"],
            "rueckfall_schluessel": "", "intern": False, "user_ids": [welt["sucher_id"]],
            "dealer_ids": [welt["firma"]["dealer_id"]], "created_at": _dt.now(timezone.utc) + timedelta(hours=1),
            "updated_at": jetzt, "fruehestens": jetzt + timedelta(hours=1)})      # der Worker fasst ihn so nicht an
        r = requests.get(f"{tb.API}/listings/check/{job_id}", headers=welt["sucher"], timeout=30)
        assert r.status_code == 200 and r.json()["status"] == "queued", r.text
        # jetzt liest die Erweiterung des Kontos das Inserat
        db.werkzeug_inserate.insert_one({
            "cache_key": f"mobile:{nr}", "user_id": welt["sucher_id"], "dealer_id": welt["firma"]["dealer_id"],
            "source": "mobile", "item_id": nr, "url": url, "data": dict(VW), "pruefsumme": "x", "gelesen_am": jetzt,
            "ablauf": jetzt + timedelta(hours=1)})
        r = requests.get(f"{tb.API}/listings/check/{job_id}", headers=welt["sucher"], timeout=30)
        assert r.status_code == 200, r.text
        assert r.json()["status"] == "completed" and r.json()["browser_helfer"] is True, r.json()
        assert db.link_jobs.count_documents({"id": job_id}) == 0, "niemand wartet mehr -> kein Abruf"
    finally:
        db.link_jobs.delete_many({"id": job_id})
        db.werkzeug_inserate.delete_many({"cache_key": f"mobile:{nr}"})
