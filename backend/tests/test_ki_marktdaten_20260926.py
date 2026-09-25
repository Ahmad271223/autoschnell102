# -*- coding: utf-8 -*-
"""KI Stufe 5 — Marktanalyse (Wunsch Ahmad 26.09.2026: "so genau wie es geht,
auf ADAC und Smart-Repair achten"). Ohne echte Websuche: recherche und
json_bewerten sind Attrappen. Geprueft: Markttabelle (Recherche + Umwandlung,
Ablage, Frische, Aufraeumschritt), Prompt-Zusatz, Recherche je Fall bei der
Abholung (an) und beim Vertrag (aus), Quellen im Ergebnis, Kosten der Websuche,
Betriebsseite und Fahrzeugfelder (EZ, PS, Alter)."""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))
from test_befunde_runde17_termine import _jetzt, _module, welt  # noqa: E402,F401
from test_ki_abholbewertung_20260925 import _attrappe as _attrappe_abholung, _welt_aufbauen  # noqa: E402
from test_ki_vertrag_20260926 import SCHAEDEN, _attrappe as _attrappe_vertrag, _fahrzeug, _ki_aufraeumen  # noqa: E402

BERICHT = ("- delle / klein, Lack intakt: 90-220 EUR, typisch 150, Quelle ADAC\n"
           "- keys / ein Schluessel fehlt: 180-420 EUR, typisch 280, Quelle Schluesselnotdienst\n")
TABELLE = {"positionen": [
    {"typ": "delle", "auspraegung": "klein, Lack intakt (bis 5 cm)", "min_eur": 90, "max_eur": 220,
     "typisch_eur": 150, "quelle": "ADAC", "hinweis": "Smart-Repair"},
    {"typ": "keys", "auspraegung": "ein Schluessel fehlt", "min_eur": 420, "max_eur": 180,
     "typisch_eur": 900, "quelle": "Fachanbieter", "hinweis": ""},
    {"typ": "unbekannt", "auspraegung": "x", "min_eur": 1, "max_eur": 2, "typisch_eur": 1, "quelle": "", "hinweis": ""},
], "zusammenfassung": "Smart-Repair bleibt deutlich guenstiger als Lackierung."}
QUELLEN = [{"url": "https://www.adac.de/x", "titel": "ADAC Smart Repair"}]


def _markt_attrappen(monkeypatch, *, recherche_status="ok", zaehler=None):
    MD = _module("ai.marktdaten")

    async def _recherche(**kw):
        if zaehler is not None:
            zaehler.append(kw)
        return {"status": recherche_status, "grund": "" if recherche_status == "ok" else "Attrappe",
                "text": BERICHT if recherche_status == "ok" else "", "quellen": QUELLEN, "suchen": 3,
                "dauer_ms": 7, "modell": "attrappe", "usage": {"input_tokens": 500, "web_search_requests": 3}}

    async def _json(**kw):
        return {"status": "ok", "grund": "", "daten": TABELLE, "dauer_ms": 3, "modell": "attrappe",
                "usage": {"input_tokens": 100, "output_tokens": 50}}
    monkeypatch.setattr(MD, "recherche", _recherche)
    monkeypatch.setattr(MD, "json_bewerten", _json)
    monkeypatch.setattr(MD, "ki_aktiv", lambda: True)
    monkeypatch.setenv("KI_MARKTANALYSE_AKTIV", "true")
    return MD


def _tabelle_weg(welt):
    welt.run(welt.db.ki_marktdaten.delete_many({"_id": "aktuell"}))


def test_01_markttabelle_recherche_umwandlung_ablage(welt, monkeypatch):
    aufrufe = []
    MD = _markt_attrappen(monkeypatch, zaehler=aufrufe)
    db = welt.db
    _tabelle_weg(welt)
    erg = welt.run(MD.aktualisieren(db))
    assert erg["status"] == "ok" and erg["aktualisiert"] is True and erg["suchen"] == 9
    assert len(aufrufe) == 3, "drei Gruppen je Lauf"
    assert "ADAC" in aufrufe[0]["frage"] and "delle" in aufrufe[0]["frage"] and "SOFORT" in aufrufe[0]["frage"]
    assert "keys" in aufrufe[2]["frage"]
    doc = welt.run(db.ki_marktdaten.find_one({"_id": "aktuell"}))
    typen = {p["typ"]: p for p in doc["positionen"]}
    assert set(typen) == {"delle", "keys"}, "unbekannte Position faellt weg"
    assert (typen["keys"]["min_eur"], typen["keys"]["max_eur"], typen["keys"]["typisch_eur"]) == (180.0, 420.0, 420.0)
    assert doc["quellen"] == QUELLEN and doc["usage"]["web_search_requests"] == 9
    text = MD.als_text(doc)
    assert "Aktuelle Marktpreise" in text and "delle / klein" in text and "Quelle ADAC" in text
    assert MD.frisch(doc) and MD.alter_tage(doc) < 1
    # frisch: kein zweiter Lauf ohne Zwang
    erg2 = welt.run(MD.aktualisieren(db))
    assert erg2["aktualisiert"] is False and len(aufrufe) == 3
    schritt = welt.run(MD.pruefen_und_aktualisieren(db))
    assert schritt["status"] == "frisch" and len(aufrufe) == 3
    # veraltet: der Aufraeumschritt erneuert
    welt.run(db.ki_marktdaten.update_one({"_id": "aktuell"}, {"$set": {"stand": "2020-01-01T00:00:00+00:00"}}))
    schritt = welt.run(MD.pruefen_und_aktualisieren(db))
    assert schritt["status"] == "ok" and schritt["aktualisiert"] is True and len(aufrufe) == 6
    _tabelle_weg(welt)


def test_02_recherche_scheitert_ohne_absturz_und_wartet(welt, monkeypatch):
    MD = _markt_attrappen(monkeypatch)
    db = welt.db
    _tabelle_weg(welt)
    # Bericht ohne Preise (KI hat aufgegeben): kein Erfolg, sondern Fehlversuch

    async def _leer(**kw):
        return {"status": "ok", "grund": "", "daten": {"positionen": [], "zusammenfassung": "nichts"},
                "dauer_ms": 1, "modell": "a", "usage": {}}
    monkeypatch.setattr(MD, "json_bewerten", _leer)
    erg = welt.run(MD.aktualisieren(db, erzwingen=True))
    assert erg["status"] == "fehler" and "keine Preisangaben" in erg["grund"]
    MD = _markt_attrappen(monkeypatch, recherche_status="zeitlimit")
    erg = welt.run(MD.aktualisieren(db, erzwingen=True))
    assert erg["status"] == "zeitlimit" and erg["aktualisiert"] is False
    assert MD.als_text(welt.run(db.ki_marktdaten.find_one({"_id": "aktuell"}))) == ""
    # Fehlversuch: der naechste Aufraeumlauf wartet (6 h), statt stuendlich zu suchen
    assert welt.run(MD.pruefen_und_aktualisieren(db))["status"] == "wartet"
    monkeypatch.setenv("KI_MARKTANALYSE_AKTIV", "false")
    assert welt.run(MD.aktualisieren(db, erzwingen=True))["status"] == "aus"
    _tabelle_weg(welt)


def test_03_abholung_mit_recherche_je_fall_und_quellen(welt, monkeypatch):
    aufrufe = []
    K = _attrappe_abholung(monkeypatch, zaehler=aufrufe)
    MD = _markt_attrappen(monkeypatch)
    recherchen = []

    async def _recherche(**kw):
        recherchen.append(kw)
        return {"status": "ok", "grund": "", "text": "Delle Smart-Repair 120-200 EUR (ADAC)", "quellen": QUELLEN,
                "suchen": 2, "dauer_ms": 9, "modell": "attrappe", "usage": {"web_search_requests": 2, "input_tokens": 300}}
    monkeypatch.setattr(MD, "recherche", _recherche)
    monkeypatch.setenv("KI_MARKTANALYSE_ABHOLUNG", "true")
    gesehen = {}
    P = _module("ai.pickup_assessment")
    alt = P.json_bewerten

    async def _bewerten(**kw):
        gesehen.update(kw)
        return await alt(**kw)
    monkeypatch.setattr(P, "json_bewerten", _bewerten)
    # Markttabelle vorhanden -> Zusatz enthaelt sie
    welt.run(MD.aktualisieren(welt.db, erzwingen=True))
    recherchen.clear()          # die Tabellen-Recherche zaehlt hier nicht
    w = welt.w
    _cid, _tid, _vid, pid = _welt_aufbauen(welt, "m3")
    erg = welt.run(K.bewertung_ausfuehren(pid, w.dealer_id))
    assert erg["status"] == "ok"
    assert len(recherchen) == 1 and "Delle" in recherchen[0]["frage"] and "BMW" in recherchen[0]["frage"]
    assert "Schlüssel" in recherchen[0]["frage"], "Abweichung Schluessel wird mit recherchiert"
    z = gesehen["zusatz"]
    assert "Aktuelle Marktpreise" in z and "Marktrecherche zu diesem Fall" in z and "adac.de" in z
    assert "Ausgangswerte AutoSchnell" in gesehen["system"]
    assert erg["ergebnis"]["quellen"] == QUELLEN
    doc = welt.run(welt.db.ki_bewertungen.find_one({"protocol_id": pid}, {"_id": 0}))
    assert doc["recherche"]["suchen"] == 2 and doc["usage"]["web_search_requests"] == 2
    # Fahrzeugfelder fuer die KI (Wunsch Ahmad: EZ, PS, Marke, Modell, Alter)
    fz = doc["eingabe"]["vehicle"]
    assert fz["make"] == "BMW" and fz["first_registration"] and fz["power_ps"] == 245 and fz["age_years"] >= 16
    # aus -> keine Recherche, kein Quellenfeld-Inhalt
    monkeypatch.setenv("KI_MARKTANALYSE_ABHOLUNG", "false")
    erg2 = welt.run(K.bewertung_ausfuehren(pid, w.dealer_id, erzwingen=True))
    assert erg2["status"] == "ok" and len(recherchen) == 1 and erg2["ergebnis"]["quellen"] == []
    _tabelle_weg(welt)
    _ki_aufraeumen(welt)


def test_04_vertrag_ohne_recherche_je_fall_standard(welt, monkeypatch):
    D = _attrappe_vertrag(monkeypatch)
    MD = _markt_attrappen(monkeypatch)
    recherchen = []

    async def _recherche(**kw):
        recherchen.append(kw)
        return {"status": "ok", "text": "x", "quellen": QUELLEN, "suchen": 1, "dauer_ms": 1, "usage": {}}
    monkeypatch.setattr(MD, "recherche", _recherche)
    monkeypatch.delenv("KI_MARKTANALYSE_VERTRAG", raising=False)
    w = welt.w
    fz = _fahrzeug(welt, f"v_km4_{w.s}")
    erg = welt.run(D.bewerten(user=w.sucher, vehicle_doc=fz, damages=SCHAEDEN))
    assert erg["status"] == "ok" and not recherchen and erg["ergebnis"]["quellen"] == []
    paket = D.paket_bauen(fz["data"], SCHAEDEN, kaufpreis=None)
    assert paket["vehicle"]["power_ps"] == 245 and paket["vehicle"]["age_years"] >= 16
    # ausdruecklich an: Recherche laeuft auch beim Vertrag
    monkeypatch.setenv("KI_MARKTANALYSE_VERTRAG", "true")
    erg = welt.run(D.bewerten(user=w.sucher, vehicle_doc=fz, damages=[dict(SCHAEDEN[0], zone="Tür vorne links")]))
    assert erg["status"] == "ok" and len(recherchen) == 1 and erg["ergebnis"]["quellen"] == QUELLEN
    _ki_aufraeumen(welt)


def test_05_kosten_betriebsseite_und_aufraeumschritt(welt, monkeypatch):
    K = _module("ai.kalibrierung")
    assert K._kosten_usd("claude-sonnet-5", {"web_search_requests": 3}) == pytest.approx(0.03)
    _markt_attrappen(monkeypatch)
    A = _module("routes.admin")
    erg = welt.run(A.admin_ki(admin={"id": "x"}))
    assert "marktdaten" in erg and "je_fall_abholung" in erg["marktdaten"]
    erg = welt.run(A.admin_ki_marktdaten(admin={"id": "x"}))
    assert erg["status"] == "ok" and erg["aktualisiert"] is True
    _tabelle_weg(welt)
    quelle = Path(__file__).resolve().parents[1].joinpath("cleanup_service.py").read_text(encoding="utf-8")
    assert 'await s("ki_marktdaten"' in quelle, "Markttabelle haengt am stuendlichen Aufraeumlauf"
    P = _module("ai.provider")
    assert P.WEBSUCHE_TOOL.startswith("web_search_") and callable(P.recherche)
    # Probelauf 26.09.2026: Handels-/Forenseiten liefern keine brauchbaren Preise
    assert "ebay.de" in P.GESPERRTE_DOMAINS and "kleinanzeigen.de" in P.GESPERRTE_DOMAINS
