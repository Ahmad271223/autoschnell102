# -*- coding: utf-8 -*-
"""Befund Ahmad 26.09.2026: Ausstattung kam vom Scraper englisch und stand so
im Kaufvertrag. Geprueft: Uebersetzungstabelle (alle 82 Bezeichnungen eines
echten memo23-Abrufs), Auslesen (Apify + mobile.de-API), PDF-Liste,
Migration 15 fuer den Bestand."""
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from test_befunde_runde17_termine import _module, welt  # noqa: E402,F401

# Ausstattung eines echten memo23-Abrufs (Mercedes E 53, 26.09.2026)
MEMO23 = ["ABS", "Adaptive cornering lights", "Adaptive Cruise Control", "Alarm system", "Alloy wheels",
          "All season tyres", "Ambient lighting", "Android Auto", "Apple CarPlay", "Arm rest",
          "Autom. dimming interior mirror", "Blind spot assist", "Bluetooth", "CD Multichanger", "CD player",
          "Central locking", "DAB radio", "Digital cockpit", "Distance warning system", "Dynamic chassis control",
          "Electric seat adjustment", "Electric side mirror", "Electric tailgate", "Electric windows",
          "Eletric seat adjustment with memory function", "Emergency brake assist", "Emergency call system",
          "Emergency tyre repair kit", "ESP", "Fatigue warning system", "Fog lamps", "Fold flat passenger seat",
          "Folding exterior mirrors", "Four-wheel drive", "Full Service History", "Glare-free high beam headlights",
          "Hands-free kit", "Headlight washer system", "Heated seats", "Heated windshield", "High beam assist",
          "Hill-start assist", "Immobilizer", "Induction charging for smartphones", "Isofix",
          "Keyless central locking", "Lane change assist", "Leather steering wheel", "LED headlights",
          "LED running lights", "Light sensor", "Lumbar support", "Multifunction steering wheel",
          "Navigation system", "Non-smoker vehicle", "On-board computer", "Paddle shifters", "Panoramic roof",
          "Particulate filter", "Power Assisted Steering", "Rain sensor", "Rear traffic alert", "Seat ventilation",
          "Ski bag", "Sound system", "Speed limit control system", "Sport seats", "Sports package",
          "Sports suspension", "Start-stop system", "Sunroof", "Tinted windows", "Touchscreen", "Traction control",
          "Traffic sign recognition", "Tuner/radio", "Tyre pressure monitoring", "USB port", "Voice control",
          "Warranty", "Winter package", "WLAN / Wi-Fi hotspot"]
ENGLISCH = re.compile(r"\b(wheels|locking|heated|seat|seats|windows|mirror|control|assist|system|lights|"
                      r"headlights|sensor|package|suspension|roof|steering|tyres|tires|drive|vehicle|"
                      r"history|warning|support|charging|player|display|cockpit|computer)\b", re.I)


def test_01_alle_memo23_bezeichnungen_werden_deutsch():
    A = _module("ausstattung_de")
    uebrig = [f for f in MEMO23 if ENGLISCH.search(A.uebersetzen(f))]
    assert not uebrig, f"noch englisch: {uebrig}"
    assert A.uebersetzen("Alloy wheels") == "Leichtmetallfelgen"
    assert A.uebersetzen("Central locking") == "Zentralverriegelung"
    assert A.uebersetzen("Eletric seat adjustment with memory function") == "Elektr. Sitzeinstellung mit Memory-Funktion"
    assert A.uebersetzen("Full Service History") == "Scheckheftgepflegt"
    assert A.uebersetzen("Automatic climatisation, 2 zones") == "2-Zonen-Klimaautomatik"
    assert A.uebersetzen("Trailer coupling (detachable)") == "Anhängerkupplung abnehmbar"
    assert A.uebersetzen("ALLOY_WHEELS") == "Leichtmetallfelgen"          # API-Schluessel
    # Deutsches und Unbekanntes bleibt, Idempotenz
    assert A.uebersetzen("Klimaanlage") == "Klimaanlage"
    assert A.uebersetzen("Sonderlackierung Nardo") == "Sonderlackierung Nardo"
    assert A.uebersetzen(A.uebersetzen("Heated seats")) == "Sitzheizung"
    assert A.uebersetzen("  ") == "" and A.uebersetzen(None) == ""
    # Dubletten nach Uebersetzung fallen weg, Reihenfolge bleibt
    assert A.liste_uebersetzen(["Heated seats", "Sitzheizung", "", "ABS", "abs"]) == ["Sitzheizung", "ABS"]
    assert A.ist_englisch("Alloy wheels") and not A.ist_englisch("Leichtmetallfelgen") and A.ist_englisch("Automatic climatisation, 3 zones")


def test_02_auslesen_apify_und_api_liefern_deutsch():
    M = _module("mobile_service")
    item = {"id": "1", "url": "https://suchen.mobile.de/x/1.html", "title": "BMW 320d", "make": "BMW", "model": "320",
            "features": ["Alloy wheels", "Heated seats", "Klimaanlage", "Heated seats"], "attributes": [],
            "htmlDescription": "<p>ok</p>", "price": {"gross": "8.900 €"}}
    v = M._parse_apify_item(item, "1")
    assert v["features"] == ["Leichtmetallfelgen", "Sitzheizung", "Klimaanlage"]
    # mobile.de-API: Schluessel ohne deutsche Beschreibung, englische Beschreibung, kurze Abkuerzung
    ad = {"ad:features": {"ad:feature": [
        {"@key": "ALLOY_WHEELS"},
        {"@key": "CENTRAL_LOCKING", "resource:local-description": [{"@xml-lang": "en", "#text": "Central locking"}]},
        {"@key": "ABS"},
        {"@key": "HEATED_SEATS", "resource:local-description": [{"@xml-lang": "de", "#text": "Sitzheizung"}]},
    ]}}
    assert M._features_list(ad) == ["Leichtmetallfelgen", "Zentralverriegelung", "ABS", "Sitzheizung"]


def test_03_pdf_liste_uebersetzt_altbestand():
    P = _module("pdf_service")
    assert P._ausstattung_liste(["Alloy wheels", "Klimaanlage", "Sunroof"]) == ["Leichtmetallfelgen", "Klimaanlage", "Schiebedach"]
    assert P._ausstattung_liste("Heated seats, Navigation system") == ["Sitzheizung", "Navigationssystem"]
    assert P._ausstattung_liste(None) == []


def test_04_migration_15_uebersetzt_bestand(welt):
    MI = _module("migrationen")
    w, db = welt.w, welt.db
    assert MI.ZIEL_VERSION >= 15 and any(n == "ausstattung_deutsch" for _, n, _ in MI.MIGRATIONEN)
    vid_en, vid_de = f"v_aus_en_{w.s}", f"v_aus_de_{w.s}"

    async def lauf():
        await db.vehicles.insert_many([
            w.fahrzeug(vid_en, data={"features": ["Alloy wheels", "Heated seats"]}),
            w.fahrzeug(vid_de, data={"features": ["Klimaanlage"]}),
        ])
        await db.listings_cache.insert_one({"cache_key": f"aus_{w.s}", "data": {"features": ["Central locking"]},
                                            "fetched_at": "2026-09-26T00:00:00"})
        erg = await MI.m15_ausstattung_deutsch(db)
        a = await db.vehicles.find_one({"id": vid_en}, {"_id": 0, "data.features": 1})
        b = await db.vehicles.find_one({"id": vid_de}, {"_id": 0, "data.features": 1, "updated_at": 1})
        c = await db.listings_cache.find_one({"cache_key": f"aus_{w.s}"}, {"_id": 0, "data.features": 1})
        erg2 = await MI.m15_ausstattung_deutsch(db)
        await db.listings_cache.delete_one({"cache_key": f"aus_{w.s}"})
        return erg, a, b, c, erg2
    erg, a, b, c, erg2 = welt.run(lauf())
    assert erg["vehicles"] >= 1 and erg["listings_cache"] >= 1
    assert a["data"]["features"] == ["Leichtmetallfelgen", "Sitzheizung"]
    assert b["data"]["features"] == ["Klimaanlage"]
    assert c["data"]["features"] == ["Zentralverriegelung"]
    # zweiter Lauf aendert an diesen Dokumenten nichts mehr
    a2 = welt.run(db.vehicles.find_one({"id": vid_en}, {"_id": 0, "data.features": 1}))
    assert a2["data"]["features"] == ["Leichtmetallfelgen", "Sitzheizung"]
    assert isinstance(erg2, dict)
