# -*- coding: utf-8 -*-
"""Bildschirm-Lesung absichern (Befund/Wunsch Ahmad 09.10.2026: "das darf alles nicht passieren"):
echte Inserat-Daten vor Bildschirm, aus Lesungen lernen, zweiter Leseversuch des Programms."""
import asyncio
import os
import sys
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import erkennung_lernen as el  # noqa: E402

VW = {"make": "VOLKSWAGEN", "make_label": "Volkswagen", "model": "T-Roc", "model_label": "T-Roc",
      "first_registration": "06/2020", "mileage": 48000, "fuel": "PETROL", "fuel_label": "Benzin",
      "gearbox": "MANUAL_GEAR", "power_kw": 110}


def test_01_passt_zum_bildschirm():
    bild = {"ez_jahr": 2020, "kilometer": 48100}
    assert el.passt_zum_bildschirm(VW, bild, "Volkswagen")
    assert el.passt_zum_bildschirm(VW, bild, None), "Marke unerkannt: EZ/km entscheiden"
    assert el.passt_zum_bildschirm(VW, {"ez_jahr": 2021, "kilometer": 50000}, "VW"), "VW/Volkswagen widerspricht nicht"
    assert not el.passt_zum_bildschirm(VW, {"ez_jahr": 2017, "kilometer": 48100}, None), "falsche Nummer gelesen?"
    assert not el.passt_zum_bildschirm(VW, {"ez_jahr": 2020, "kilometer": 90000}, None)
    assert not el.passt_zum_bildschirm(VW, bild, "Opel"), "andere Marke erkannt"
    assert not el.passt_zum_bildschirm(None, bild, None) and not el.passt_zum_bildschirm({}, bild, None)
    assert not el.passt_zum_bildschirm(VW, {"ez_jahr": None, "kilometer": 48000}, None)


def test_02_vehicle_aus_inserat_nimmt_die_echten_werte():
    bild = {"make_label": "VWT-", "model_label": "Ro c", "fuel": "EIektro", "mileage": 48100, "title": "VWT- Ro c"}
    v = el.vehicle_aus_inserat(VW, bild)
    assert (v["make_label"], v["model_label"], v["fuel"], v["mileage"]) == ("Volkswagen", "T-Roc", "PETROL", 48000)
    assert v["title"] == "VWT- Ro c", "was das Inserat nicht hat, bleibt"


def test_03_auswerten_braucht_zwei_belege_ohne_widerspruch():
    e = [{"marke": "Hyundai", "modell": "i30", "beleg": "mobile:1"}]
    assert el.auswerten(e) is None, "ein Beleg reicht nicht"
    e.append({"marke": "Hyundai", "modell": "i30", "beleg": "mobile:1"})
    assert el.auswerten(e) is None, "derselbe Beleg zaehlt einmal"
    e.append({"marke": "Hyundai", "modell": "i30", "beleg": "mobile:2"})
    assert el.auswerten(e) == ("Hyundai", "i30")
    e.append({"marke": "Hyundai", "modell": "i20", "beleg": "mobile:3"})
    assert el.auswerten(e) is None, "Widerspruch: nie raten"


def test_04_lernen_und_tabelle():
    from motor.motor_asyncio import AsyncIOMotorClient

    async def lauf():
        client = AsyncIOMotorClient(os.environ.get("MONGO_URL", "mongodb://localhost:27017"))
        db = client["el_lernen_" + uuid.uuid4().hex[:8]]
        try:
            await el.lernen(db, "Hyundai IBO", "Hyundai", "i30", "mobile:1")
            assert el.gelernt(await el.tabelle(db), "Hyundai IBO") is None
            await el.lernen(db, "HYUNDAI  IBO", "Hyundai", "i30", "mobile:2")       # gleicher Schluessel
            tab = await el.tabelle(db)
            assert el.gelernt(tab, "Hyundai IBO") == ("Hyundai", "i30")
            assert el.gelernt(tab, "Hyundai i30") is None
            doc = await db[el.SAMMLUNG].find_one({"_id": "hyundaiibo"})
            assert doc["ablauf"] > datetime.now(timezone.utc).replace(tzinfo=None) + timedelta(days=100)
            # Widerspruch nimmt die Zuordnung wieder weg
            await el.lernen(db, "Hyundai IBO", "Hyundai", "i20", "mobile:3")
            assert el.gelernt(await el.tabelle(db), "Hyundai IBO") is None
            # nie zu kurz/leer
            await el.lernen(db, "", "Hyundai", "i30", "mobile:4")
            await el.lernen(db, "x", "Hyundai", "", "mobile:4")
            assert await db[el.SAMMLUNG].count_documents({}) == 1
        finally:
            await client.drop_database(db.name)
            client.close()

    asyncio.run(lauf())


def test_05_erkennen_mit_gelerntem_und_zweiter_lesung():
    from routes.werkzeuge import _erkennen
    tab = {el.schluessel("Hyundai IBO"): ("Hyundai", "i30")}
    f = {"marke": "Hyundai", "marke_modell_text": "Hyundai IBX", "titel": "", "roh": True, "kraftstoff": "EIktr"}
    z = _erkennen(dict(f), tab, {})
    assert z["modell_gefunden"] is False and z["quelle"] == "bildschirm"
    # gelernt: der Bildschirmtext selbst
    g = dict(f, marke_modell_text="Hyundai IBO")
    z = _erkennen(g, {el.schluessel("Hyundai IBX"): ("Hyundai", "i30")}, {})
    assert z["quelle"] in ("bildschirm", "gelernt")
    g = dict(f)
    z = _erkennen(g, {el.schluessel("Hyundai IBX"): ("Hyundai", "i30")}, {})
    assert (z["quelle"], g["marke"], g["modell"]) == ("gelernt", "Hyundai", "i30")
    # zweite Lesung des Programms
    g = dict(f)
    z = _erkennen(g, {}, {"marke_modell_text": ["Hyundai i30"], "kraftstoff": ["Benzin"]})
    assert (z["quelle"], g["modell"], z["modell_gefunden"]) == ("zweite_lesung", "i30", True)
    assert g["kraftstoff"] == "Benzin", "erste Kraftstoff-Lesung ergab nichts -> zweite"
    # erste Lesung gut: die zweite wird nicht gebraucht
    g = dict(f, marke_modell_text="VW Golf", kraftstoff="Diesel")
    z = _erkennen(g, {}, {"marke_modell_text": ["VW Polo"], "kraftstoff": ["Benzin"]})
    assert (z["quelle"], g["modell"], g["kraftstoff"]) == ("bildschirm", "Golf", "Diesel")


# ------------------------------------------------------------ ueber HTTP (RUNDE14_HTTP=1, wie die anderen Werkzeug-Tests)
import pytest  # noqa: E402
import requests  # noqa: E402

pytestmark_http = pytest.mark.skipif(not os.environ.get("RUNDE14_HTTP"), reason="nur gegen ein laufendes Backend")


if os.environ.get("RUNDE14_HTTP"):
    from test_browser_helfer_20261004 import welt  # noqa: E402,F401 — dieselbe Testfirma (Kunde 10002) wie dort


@pytestmark_http
def test_10_vergleich_nimmt_echte_daten_lernt_und_nutzt_das_gelernte(welt):
    """Liegt das Inserat gelesen vor, kommen Marke/Modell/Kraftstoff daher (EZ/km passen); die abweichende
    Bildschirm-Lesung wird gelernt und gilt nach zwei Inseraten auch ohne Speicher-Treffer. Passt EZ nicht, bleibt es
    beim Bildschirm. Der zweite Leseversuch des Programms hilft, wenn die erste Lesung nichts ergab."""
    import test_browser_helfer_20261004 as tb
    import werkzeuge as wz
    db = welt["db"]
    pc = {**tb._verbinden(welt, "sucher", wid=wz.AUTOPOINTER, name="PC-Lernen"),
          "User-Agent": "AutoSchnell-Vergleich/1.5.12"}
    ids = ["487000101", "487000102", "487000103", "487000104"]
    jetzt = datetime.now(timezone.utc)

    def speicher(nr, **anders):
        db.listings_cache.update_one({"cache_key": f"mobile:{nr}"}, {"$set": {
            "cache_key": f"mobile:{nr}", "source": "mobile", "item_id": nr, "data": {**VW, **anders},
            "expires_at": jetzt + timedelta(days=1)}}, upsert=True)

    def vergleich(nr, text="VW XQ9", **zusatz):
        f = {"marke": "VW", "modell": "", "marke_modell_text": text, "titel": text, "ez_monat": 6, "ez_jahr": 2020,
             "kilometer": 48100, "kw": 110, "kraftstoff": "EIktr", "getriebe": "Schaltgetriebe", "preis": 21000,
             "quelle": "mobile.de", "inserat_id": nr, "roh": True, **zusatz}
        r = requests.post(f"{tb.API}/werkzeuge/{wz.AUTOPOINTER}/vergleich", headers=pc, json={"fahrzeug": f},
                          timeout=60)
        assert r.status_code == 200, r.text
        return r.json()

    try:
        db.erkennung_gelernt.delete_many({"_id": {"$in": ["vwxq9", "vwqqq"]}})
        speicher(ids[0])
        d = vergleich(ids[0])
        assert (d["fahrzeug"]["marke"], d["fahrzeug"]["modell"]) == ("Volkswagen", "T-Roc"), d["fahrzeug"]
        assert not any("kennt das Modell" in h for h in d["hinweise"]), d["hinweise"]
        mobile = next(l["url"] for l in d["links"] if l["portal"] == "mobile.de")
        assert "ft=PETROL" in mobile, "Kraftstoff aus dem Inserat, nicht vom Bildschirm"
        doc = db.werkzeug_vergleiche.find_one({"fahrzeug.inserat_id": ids[0], "user_id": welt["sucher_id"]})
        assert doc["fahrzeug"]["erkennung"] == "inserat"
        assert len(db.erkennung_gelernt.find_one({"_id": "vwxq9"})["eintraege"]) == 1
        speicher(ids[1])
        vergleich(ids[1])
        # ohne Speicher-Treffer: das Gelernte (zwei Inserate) gilt
        d = vergleich(ids[2])
        assert d["fahrzeug"]["modell"] == "T-Roc", d
        assert db.werkzeug_vergleiche.find_one({"fahrzeug.inserat_id": ids[2]})["fahrzeug"]["erkennung"] == "gelernt"
        # EZ passt nicht (falsche Nummer gelesen?): Bildschirm bleibt
        speicher(ids[3], first_registration="06/2012")
        d = vergleich(ids[3], text="VW QQQ")
        assert d["fahrzeug"]["modell"] != "T-Roc"
        assert db.erkennung_gelernt.count_documents({"_id": "vwqqq"}) == 0, "nichts gelernt"
        # zweiter Leseversuch des Programms
        d = vergleich("487000199", text="VW QQQ", alternativen={"marke_modell_text": ["VW Golf"],
                                                                "kraftstoff": ["Benzin"]})
        assert d["fahrzeug"]["modell"] == "Golf"
        assert "ft=PETROL" in next(l["url"] for l in d["links"] if l["portal"] == "mobile.de")
    finally:
        db.listings_cache.delete_many({"cache_key": {"$in": [f"mobile:{n}" for n in ids]}})
        db.erkennung_gelernt.delete_many({"_id": {"$in": ["vwxq9", "vwqqq"]}})
        db.werkzeug_vergleiche.delete_many({"user_id": welt["sucher_id"]})
        db.link_jobs.delete_many({"url": {"$regex": "48700"}})
