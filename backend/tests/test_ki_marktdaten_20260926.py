# -*- coding: utf-8 -*-
"""KI Stufe 5 — Marktanalyse und eigene Preisdatenbank (Wunsch Ahmad
26.09.2026: "Websuche erst mal fuer ein Jahr, dabei eigene Datenbank
aufbauen"). Ohne echte Websuche: recherche und json_bewerten sind Attrappen.
Geprueft: Markttabelle (drei Gruppen, Umwandlung, Ablage, Frische,
Aufraeumschritt), Prompt-Zusatz, Recherche je Fall mit Datenblock ->
ki_reparaturpreise, eigene Referenzen ersetzen die Suche, Sparmodus,
Kosten der Websuche, Betriebsseite."""
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
    {"typ": "keys", "auspraegung": "ein Schluessel fehlt (Funkschluessel)", "min_eur": 420, "max_eur": 180,
     "typisch_eur": 900, "quelle": "Fachanbieter", "hinweis": ""},
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


def _preise_weg(welt):
    welt.run(welt.db.ki_reparaturpreise.delete_many({"marke": {"$in": ["bmw", "testmarke"]}}))


def test_01_markttabelle_recherche_umwandlung_ablage(welt, monkeypatch):
    aufrufe = []
    MD = _markt_attrappen(monkeypatch, zaehler=aufrufe)
    db = welt.db
    _tabelle_weg(welt)
    erg = welt.run(MD.aktualisieren(db))
    # 25.09.2026 abends: vierte Gruppe "Technik" (Schadenkatalog)
    assert erg["status"] == "ok" and erg["aktualisiert"] is True and erg["suchen"] == 12
    assert len(aufrufe) == 4, "vier Gruppen je Lauf"
    assert "technical" in aufrufe[3]["frage"]
    assert "ADAC" in aufrufe[0]["frage"] and "delle" in aufrufe[0]["frage"] and "SOFORT" in aufrufe[0]["frage"]
    assert "keys" in aufrufe[2]["frage"]
    doc = welt.run(db.ki_marktdaten.find_one({"_id": "aktuell"}))
    typen = {p["typ"]: p for p in doc["positionen"]}
    assert set(typen) == {"delle", "keys"}
    assert (typen["keys"]["min_eur"], typen["keys"]["max_eur"], typen["keys"]["typisch_eur"]) == (180.0, 420.0, 420.0)
    assert doc["quellen"] == QUELLEN and doc["usage"]["web_search_requests"] == 12
    text = MD.als_text(doc)
    assert "Aktuelle Marktpreise" in text and "delle / klein" in text and "Quelle ADAC" in text
    assert MD.frisch(doc) and MD.alter_tage(doc) < 1
    erg2 = welt.run(MD.aktualisieren(db))
    assert erg2["aktualisiert"] is False and len(aufrufe) == 4
    schritt = welt.run(MD.pruefen_und_aktualisieren(db))
    assert schritt["status"] == "frisch" and len(aufrufe) == 4
    welt.run(db.ki_marktdaten.update_one({"_id": "aktuell"}, {"$set": {"stand": "2020-01-01T00:00:00+00:00"}}))
    schritt = welt.run(MD.pruefen_und_aktualisieren(db))
    assert schritt["status"] == "ok" and schritt["aktualisiert"] is True and len(aufrufe) == 8
    # Markttabelle legt sich ueber die Startwert-Referenz (typ + aehnliche Auspraegung)
    KX = _module("ai.kontext")
    ref = KX.reparaturreferenz({"type_key": "delle", "zone": "Tür", "severity_data": {"groesse": "bis 2 cm", "lack": "nein"}}, doc)
    assert ref["median"] == 150 and ref["source"].startswith("Marktdaten") and "ADAC" in ref["source"]
    _tabelle_weg(welt)


def test_01b_umwandlung_je_gruppe_nicht_abgeschnitten(welt, monkeypatch):
    """Betrieb 26.09.2026: Alarm ki_marktdaten_fehlgeschlagen "Antwort abgeschnitten
    (max_tokens)" — die Umwandlung aller vier Gruppen in EINEM Aufruf sprengte 2500
    Tokens. Jetzt: je Gruppe ein Aufruf mit nur deren Positionen und 6000 Tokens;
    scheitert eine Gruppe, bleibt die Tabelle der anderen; scheitern alle -> Fehler."""
    MD = _markt_attrappen(monkeypatch)
    db = welt.db
    _tabelle_weg(welt)
    aufrufe = []

    async def _json(**kw):
        aufrufe.append(kw)
        if len(aufrufe) == 2:
            return {"status": "fehler", "grund": "Antwort abgeschnitten (max_tokens)", "daten": None, "dauer_ms": 1,
                    "modell": "a", "usage": {"output_tokens": 6000}}
        return {"status": "ok", "grund": "", "daten": {"positionen": [TABELLE["positionen"][0]] if len(aufrufe) < 4 else TABELLE["positionen"],
                                                        "zusammenfassung": f"Gruppe {len(aufrufe)}."},
                "dauer_ms": 3, "modell": "a", "usage": {"input_tokens": 100, "output_tokens": 50}}
    monkeypatch.setattr(MD, "json_bewerten", _json)
    erg = welt.run(MD.aktualisieren(db, erzwingen=True))
    assert erg["status"] == "ok" and erg["aktualisiert"] is True
    assert len(aufrufe) == 4, "je Recherche-Gruppe ein Umwandlungs-Aufruf"
    assert all(kw["max_tokens"] == MD.UMWANDLUNG_MAX_TOKENS >= 6000 for kw in aufrufe)
    gruppen = [g for g, _ in MD._gruppen()]
    for kw, (titel, positionen) in zip(aufrufe, MD._gruppen()):
        assert kw["nutzer"]["bericht"].startswith(f"## {titel}") and BERICHT.strip() in kw["nutzer"]["bericht"]
        assert kw["nutzer"]["positionen"] == positionen, "nur die Positionen der eigenen Gruppe"
    doc = welt.run(db.ki_marktdaten.find_one({"_id": "aktuell"}))
    assert {p["typ"] for p in doc["positionen"]} == {"delle", "keys"}, "dedupliziert ueber Gruppen"
    assert doc["gruppen_fehler"] == [gruppen[1]]
    assert doc["zusammenfassung"] == "Gruppe 1. Gruppe 3. Gruppe 4."
    assert doc["usage"]["output_tokens"] == 6000 + 3 * 50
    assert not welt.run(db.betriebsalarme.find_one({"typ": "ki_marktdaten_fehlgeschlagen", "ref": "aktuell", "offen": True}))

    async def _kaputt(**kw):
        return {"status": "fehler", "grund": "Antwort abgeschnitten (max_tokens)", "daten": None, "dauer_ms": 1, "modell": "a", "usage": {}}
    monkeypatch.setattr(MD, "json_bewerten", _kaputt)
    erg = welt.run(MD.aktualisieren(db, erzwingen=True))
    assert erg["status"] == "fehler" and "abgeschnitten" in erg["grund"]
    assert welt.run(db.ki_marktdaten.find_one({"_id": "aktuell"}))["positionen"], "alte Tabelle bleibt"
    _tabelle_weg(welt)


def test_02_recherche_scheitert_ohne_absturz_und_wartet(welt, monkeypatch):
    MD = _markt_attrappen(monkeypatch)
    db = welt.db
    _tabelle_weg(welt)

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
    assert welt.run(MD.pruefen_und_aktualisieren(db))["status"] == "wartet"
    monkeypatch.setenv("KI_MARKTANALYSE_AKTIV", "false")
    assert welt.run(MD.aktualisieren(db, erzwingen=True))["status"] == "aus"
    _tabelle_weg(welt)


def test_03_abholung_recherche_je_fall_lernt_und_quellen(welt, monkeypatch):
    aufrufe = []
    K = _attrappe_abholung(monkeypatch, zaehler=aufrufe)
    MD = _markt_attrappen(monkeypatch)
    _preise_weg(welt)
    recherchen = []

    async def _recherche(**kw):
        recherchen.append(kw)
        text = ("Delle Smart-Repair 120-200 EUR (ADAC), Schluessel BMW 250-400 EUR\n"
                + MD.DATEN_MARKER + "\nd1|120|200|160|ADAC|https://www.adac.de/x\n"
                "dev:keys|250|400|300|Autobutler|https://www.autobutler.de/bmw-schluessel\n"
                # Review 25.09.2026 abends: unbekannte Quelle und unplausibler Wert werden NICHT gelernt
                "d1|1|2|1|irgendwer|https://foren.example.org/x\n"
                "dev:keys|9000|20000|12000|ADAC|https://www.adac.de/y\n")
        return {"status": "ok", "grund": "", "text": text, "quellen": QUELLEN, "suchen": 2, "dauer_ms": 9,
                "modell": "attrappe", "usage": {"web_search_requests": 2, "input_tokens": 300}}
    monkeypatch.setattr(MD, "recherche", _recherche)
    monkeypatch.setenv("KI_MARKTANALYSE_ABHOLUNG", "true")
    gesehen = {}
    P = _module("ai.pickup_assessment")
    alt = P.json_bewerten

    async def _bewerten(**kw):
        gesehen.update(kw)
        return await alt(**kw)
    monkeypatch.setattr(P, "json_bewerten", _bewerten)
    welt.run(MD.aktualisieren(welt.db, erzwingen=True))
    recherchen.clear()          # die Tabellen-Recherche zaehlt hier nicht
    w = welt.w
    _cid, _tid, _vid, pid = _welt_aufbauen(welt, "m3")
    erg = welt.run(K.bewertung_ausfuehren(pid, w.dealer_id))
    assert erg["status"] == "ok"
    assert len(recherchen) == 1 and "id d1" in recherchen[0]["frage"] and "BMW" in recherchen[0]["frage"]
    assert "id dev:keys" in recherchen[0]["frage"] and MD.DATEN_MARKER in recherchen[0]["frage"]
    z = gesehen["zusatz"]
    assert "Aktuelle Marktpreise" in z and "Marktrecherche zu diesem Fall" in z and MD.DATEN_MARKER not in z
    assert erg["ergebnis"]["quellen"] == QUELLEN
    doc = welt.run(welt.db.ki_bewertungen.find_one({"protocol_id": pid}, {"_id": 0}))
    assert doc["recherche"]["suchen"] == 2 and doc["recherche"]["gelernt"] == 2 and doc["usage"]["web_search_requests"] == 2
    # eigene Preisdatenbank hat gelernt
    preise = welt.run(welt.db.ki_reparaturpreise.find({"marke": "bmw"}, {"_id": 0}).to_list(50))
    keys = {p["key"]: p for p in preise}
    assert keys["delle_klein"]["typisch_eur"] == 160 and keys["delle_klein"]["quelle"] == "ADAC"
    assert keys["keys_fehlt"]["min_eur"] == 250 and keys["keys_fehlt"]["alter_klasse"] == "12+"
    # die Referenz im Paket traegt danach die eigenen Werte
    refs = erg["ergebnis"]["referenzen"]
    assert refs["d1"]["median"] == 160 and refs["d1"]["source"].startswith("eigene Datenbank")
    # genug eigene Werte (EIGENE_MIN) -> keine Suche mehr fuer diese Positionen
    for _ in range(MD.EIGENE_MIN):
        welt.run(welt.db.ki_reparaturpreise.insert_many([
            {"key": "delle_klein", "typ": "delle", "marke": "bmw", "alter_klasse": "12+", "min_eur": 100, "max_eur": 200,
             "typisch_eur": 150, "quelle": "ADAC", "stand": _jetzt(), "art": "abholung"},
            {"key": "keys_fehlt", "typ": "keys", "marke": "bmw", "alter_klasse": "12+", "min_eur": 200, "max_eur": 400,
             "typisch_eur": 300, "quelle": "x", "stand": _jetzt(), "art": "abholung"}]))
    grund = welt.run(K._grundlagen(pid, w.dealer_id))
    paket = K.paket_bauen(*grund)
    eigene = welt.run(MD.eigene_referenzen(paket, "abholung"))
    assert eigene["delle_klein"]["n"] >= MD.EIGENE_MIN and eigene["delle_klein"]["nur_marke"] is True
    offen = MD.recherche_noetig(paket, "abholung", eigene)
    assert all(p.get("id") not in ("d1", "dev:keys") for p in offen), "eigene Daten reichen"

    # Sparmodus: keine Recherche
    async def _keine(**kw):
        raise AssertionError("im Sparmodus darf nicht gesucht werden")
    monkeypatch.setattr(MD, "recherche", _keine)
    assert welt.run(MD.fall_recherche("abholung", paket, sparmodus=True, eigene={})) is None
    monkeypatch.setenv("KI_MARKTANALYSE_ABHOLUNG", "false")
    assert welt.run(MD.fall_recherche("abholung", paket, eigene={})) is None
    _tabelle_weg(welt)
    _preise_weg(welt)
    _ki_aufraeumen(welt)


def test_04_vertrag_recherche_standard_an_und_datenblock(welt, monkeypatch):
    D = _attrappe_vertrag(monkeypatch)
    MD = _markt_attrappen(monkeypatch)
    _preise_weg(welt)
    recherchen = []

    async def _recherche(**kw):
        recherchen.append(kw)
        return {"status": "ok", "text": "x\n" + MD.DATEN_MARKER + "\nd1|100|200|150|ADAC|u", "quellen": QUELLEN,
                "suchen": 1, "dauer_ms": 1, "usage": {"web_search_requests": 1}}
    monkeypatch.setattr(MD, "recherche", _recherche)
    monkeypatch.delenv("KI_MARKTANALYSE_VERTRAG", raising=False)
    w = welt.w
    fz = _fahrzeug(welt, f"v_km4_{w.s}")
    erg = welt.run(D.bewerten(user=w.sucher, vehicle_doc=fz, damages=SCHAEDEN))
    assert erg["status"] == "ok" and len(recherchen) == 1 and erg["ergebnis"]["quellen"] == QUELLEN
    assert welt.run(welt.db.ki_reparaturpreise.count_documents({"key": "delle_klein", "marke": "bmw"})) == 1
    monkeypatch.setenv("KI_MARKTANALYSE_VERTRAG", "false")
    erg = welt.run(D.bewerten(user=w.sucher, vehicle_doc=fz, damages=[dict(SCHAEDEN[0], zone="Tür vorne links")]))
    assert erg["status"] == "ok" and len(recherchen) == 1 and erg["ergebnis"]["quellen"] == []
    # Datenblock-Parser
    zeilen = MD._daten_parsen("Text\n###DATEN\nd1|300|100|150|ADAC|https://a\nkaputt\n| dev:keys | 80 | 700 | 200 | ADAC |")
    assert zeilen[0] == {"id": "d1", "min_eur": 100.0, "max_eur": 300.0, "typisch_eur": 150.0, "quelle": "ADAC", "url": "https://a"}
    assert zeilen[1]["id"] == "dev:keys" and zeilen[1]["typisch_eur"] == 200.0
    assert MD._daten_parsen("ohne Block") == []
    _preise_weg(welt)
    _ki_aufraeumen(welt)


def test_05_kosten_betriebsseite_und_aufraeumschritt(welt, monkeypatch):
    K = _module("ai.kalibrierung")
    assert K._kosten_usd("claude-sonnet-5", {"web_search_requests": 3}) == pytest.approx(0.03)
    _markt_attrappen(monkeypatch)
    A = _module("routes.admin")
    erg = welt.run(A.admin_ki(admin={"id": "x"}))
    assert "marktdaten" in erg and "je_fall_abholung" in erg["marktdaten"] and "eigene_preise" in erg
    erg = welt.run(A.admin_ki_marktdaten(admin={"id": "x"}))
    assert erg["status"] == "ok" and erg["aktualisiert"] is True
    _tabelle_weg(welt)
    quelle = Path(__file__).resolve().parents[1].joinpath("cleanup_service.py").read_text(encoding="utf-8")
    assert 'await s("ki_marktdaten"' in quelle
    P = _module("ai.provider")
    assert P.WEBSUCHE_TOOL.startswith("web_search_") and callable(P.recherche)
    assert "ebay.de" in P.GESPERRTE_DOMAINS and "kleinanzeigen.de" in P.GESPERRTE_DOMAINS
    assert P.KI_MAX_TOKENS <= 3000 and P.ki_denken_aus() is True, "kurze Antwort ohne Denk-Tokens = Kostenbremse"
    assert "haiku" in P.ki_recherche_modell()
