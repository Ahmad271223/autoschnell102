# -*- coding: utf-8 -*-
"""Reparaturwelle KI-Bewertung nach dem Review vom 26.09.2026 (Nr. 4-38) —
ohne echten KI-Aufruf (Attrappen wie in den drei KI-Testdateien).

K1  Zwischenspeicher verlangt Prompt-Fassung, Modell und Verfall (KI_CACHE_TAGE)
K2  genau eine Position je Eingabe (doppelte/fremde raus, fehlende -> fehler)
K3  Lernfall nur fuer Fahrzeug und Konto der Bewertung
K4  Bewertung lesen nur im eigenen Bereich (Sucher B derselben Firma: 404)
K5  Marktdaten-Karte nur im eigenen Bereich
K6  eigene Referenzen in Stufen, >= 5 Werte aus >= 2 Quellen, 120 Tage
K7  hoechstens 12 Recherche-Positionen, teuerste zuerst
K8  Budget: eine Wahrheit (Zaehler), verwaiste Reservierung wird abgeglichen
K9  atomarer Claim bei der Abholung: zwei parallele Starts -> ein Aufruf
K10 "Neu berechnen" im Hintergrund, Antwort sofort "laeuft"
K11 Marktvergleich: Dedupe, ganzes Modell, Getriebe, Leistung, Alter,
    Vorrang der Marktbeobachtung
K12 Kalibrierung je Art, Firma vor global, Faktor 0,6-1,2
"""
import asyncio
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))
from test_befunde_runde17_termine import _jetzt, _module, welt  # noqa: E402,F401
from test_ki_abholbewertung_20260925 import (  # noqa: E402
    _attrappe as _attrappe_abholung, _aufraeumen, _welt_aufbauen, hintergrund_abwarten)
from test_ki_vertrag_20260926 import (  # noqa: E402
    ANTWORT as ANTWORT_V, SCHAEDEN, _attrappe as _attrappe_vertrag, _fahrzeug, _ki_aufraeumen)

BACKEND = Path(__file__).resolve().parents[1]


def _vor_tagen(n):
    return (datetime.now(timezone.utc) - timedelta(days=n)).isoformat()


def _in_sekunden(n):
    return (datetime.now(timezone.utc) + timedelta(seconds=n)).isoformat()


# ------------------------------------------------ K1 Zwischenspeicher
def test_01_cache_vertrag_verlangt_modell_prompt_und_frische(welt, monkeypatch):
    aufrufe = []
    D = _attrappe_vertrag(monkeypatch, zaehler=aufrufe)
    PR = _module("ai.provider")
    w, db = welt.w, welt.db
    monkeypatch.setenv("KI_CACHE_TAGE", "7")
    assert PR.ki_cache_tage() == 7
    fz = _fahrzeug(welt, f"v_kr1_{w.s}")
    erg = welt.run(D.bewerten(user=w.sucher, vehicle_doc=fz, damages=SCHAEDEN))
    assert erg["status"] == "ok" and len(aufrufe) == 1
    h = erg["input_hash"]
    filt = {"dealer_id": w.dealer_id, "input_hash": h, "status": "ok"}
    # gleicher Stand -> Treffer
    assert welt.run(D.bewerten(user=w.sucher, vehicle_doc=fz, damages=SCHAEDEN))["id"] == erg["id"] and len(aufrufe) == 1
    # anderes Modell -> neu rechnen
    welt.run(db.ki_bewertungen.update_many(filt, {"$set": {"modell": "claude-altmodell"}}))
    erg2 = welt.run(D.bewerten(user=w.sucher, vehicle_doc=fz, damages=SCHAEDEN))
    assert erg2["status"] == "ok" and erg2["id"] != erg["id"] and len(aufrufe) == 2
    # alte Prompt-Fassung -> neu rechnen
    welt.run(db.ki_bewertungen.update_many(filt, {"$set": {"prompt_version": "vertrag_v0"}}))
    assert welt.run(D.bewerten(user=w.sucher, vehicle_doc=fz, damages=SCHAEDEN))["status"] == "ok" and len(aufrufe) == 3
    # zu alt -> neu rechnen
    welt.run(db.ki_bewertungen.update_many(filt, {"$set": {"created_at": _vor_tagen(8)}}))
    assert welt.run(D.bewerten(user=w.sucher, vehicle_doc=fz, damages=SCHAEDEN))["status"] == "ok" and len(aufrufe) == 4
    # laengere Frist -> das 8 Tage alte Ergebnis zaehlt wieder (juengste gueltige Ablage)
    welt.run(db.ki_bewertungen.update_many(filt, {"$set": {"created_at": _vor_tagen(8)}}))
    monkeypatch.setenv("KI_CACHE_TAGE", "30")
    assert welt.run(D.bewerten(user=w.sucher, vehicle_doc=fz, damages=SCHAEDEN))["status"] == "ok" and len(aufrufe) == 4
    monkeypatch.setenv("KI_CACHE_TAGE", "7")
    # laufender Lauf mit gueltigem Lease bleibt ein Treffer (kein neuer Aufruf)
    welt.run(db.ki_bewertungen.insert_one({"id": f"lauf_{w.s}", "art": "vertrag", "dealer_id": w.dealer_id,
                                          "user_id": w.sucher["id"], "input_hash": h, "status": "laeuft",
                                          "modell": "egal", "prompt_version": "egal", "created_at": _jetzt(),
                                          "lease_until": _in_sekunden(120)}))
    erg5 = welt.run(D.bewerten(user=w.sucher, vehicle_doc=fz, damages=SCHAEDEN))
    assert erg5["status"] == "laeuft" and erg5["id"] == f"lauf_{w.s}" and len(aufrufe) == 4
    assert PR.ergebnis_gueltig({"prompt_version": "x", "modell": PR.ki_modell(), "created_at": _jetzt()}, "x")
    assert not PR.ergebnis_gueltig({"prompt_version": "x", "modell": PR.ki_modell(), "created_at": "kaputt"}, "x")
    _ki_aufraeumen(welt)


def test_02_cache_abholung_verlangt_modell_prompt_und_frische(welt, monkeypatch):
    aufrufe = []
    K = _attrappe_abholung(monkeypatch, zaehler=aufrufe)
    w, db = welt.w, welt.db
    monkeypatch.setenv("KI_CACHE_TAGE", "7")
    _cid, _tid, _vid, pid = _welt_aufbauen(welt, "r2")
    monkeypatch.setattr(K, "bewertung_anstossen", lambda *a, **k: None)
    erg = welt.run(K.bewertung_ausfuehren(pid, w.dealer_id))
    assert erg["status"] == "ok" and len(aufrufe) == 1
    assert welt.run(K.bewertung_ausfuehren(pid, w.dealer_id))["status"] == "ok" and len(aufrufe) == 1
    filt = {"protocol_id": pid, "input_hash": erg["input_hash"]}
    # anderes Modell: Lesen meldet "veraltet" (altes Ergebnis bleibt sichtbar), Rechnen rechnet neu
    welt.run(db.ki_bewertungen.update_one(filt, {"$set": {"modell": "claude-altmodell"}}))
    gelesen = welt.run(K.bewertung_lesen(pid, w.dealer_id))
    assert gelesen["status"] == "veraltet" and gelesen["ergebnis"] is not None
    assert welt.run(K.bewertung_ausfuehren(pid, w.dealer_id))["status"] == "ok" and len(aufrufe) == 2
    assert welt.run(K.bewertung_lesen(pid, w.dealer_id))["status"] == "ok"
    welt.run(db.ki_bewertungen.update_one(filt, {"$set": {"prompt_version": "abholung_v1"}}))
    assert welt.run(K.bewertung_ausfuehren(pid, w.dealer_id))["status"] == "ok" and len(aufrufe) == 3
    welt.run(db.ki_bewertungen.update_one(filt, {"$set": {"created_at": _vor_tagen(8)}}))
    assert welt.run(K.bewertung_lesen(pid, w.dealer_id))["status"] == "veraltet"
    assert welt.run(K.bewertung_ausfuehren(pid, w.dealer_id))["status"] == "ok" and len(aufrufe) == 4
    # laufender Lauf mit gueltigem Lease: weder Lesen noch erzwingen starten neu
    welt.run(db.ki_bewertungen.update_one(filt, {"$set": {"status": "laeuft", "lease_until": _in_sekunden(120)}}))
    assert welt.run(K.bewertung_lesen(pid, w.dealer_id))["status"] == "laeuft"
    assert welt.run(K.bewertung_ausfuehren(pid, w.dealer_id, erzwingen=True))["status"] == "laeuft"
    assert len(aufrufe) == 4
    _aufraeumen(welt)


# ------------------------------------------------ K2 genau eine Position je Eingabe
def test_03_positionen_abgleich_doppelt_fremd_fehlend(welt, monkeypatch):
    S = _module("ai.schemas")
    erg, fehlende, doppelte, fremde = S.positionen_abgleichen(
        {"items": [{"source_id": "a", "fair_discount_eur": 1}, {"source_id": "a", "fair_discount_eur": 2},
                   {"source_id": "zz"}, {"source_id": "b"}], "combined": {}}, ["a", "b", "c"])
    assert [i["source_id"] for i in erg["items"]] == ["a", "b"] and erg["items"][0]["fair_discount_eur"] == 1
    assert fehlende == ["c"] and doppelte == ["a"] and fremde == ["zz"]
    assert S.positionen_abgleichen({"items": []}, [])[1:] == ([], [], [])
    # Vertrag: doppelte d1 (nur die erste zaehlt) und fremde Position -> combined aus den Verbleibenden
    w, db = welt.w, welt.db
    doppelt = {**ANTWORT_V, "items": [ANTWORT_V["items"][0],
                                      {**ANTWORT_V["items"][0], "fair_discount_eur": 900, "minimum_justified_eur": 900,
                                       "best_realistic_eur": 900, "negotiation_start_eur": 900},
                                      {**ANTWORT_V["items"][1], "source_id": "fremd"},
                                      ANTWORT_V["items"][1]],
               "combined": {**ANTWORT_V["combined"], "sum_fair_eur": 1450, "fair_discount_eur": 1450,
                            "best_realistic_eur": 1500, "negotiation_start_eur": 1600}}
    D = _attrappe_vertrag(monkeypatch, antwort=doppelt)
    fz = _fahrzeug(welt, f"v_kr3_{w.s}")
    erg = welt.run(D.bewerten(user=w.sucher, vehicle_doc=fz, damages=SCHAEDEN))
    assert erg["status"] == "ok"
    ids = [i["source_id"] for i in erg["ergebnis"]["items"]]
    assert sorted(ids) == ["d1", "d2"], ids
    assert erg["ergebnis"]["combined"]["sum_fair_eur"] == 400.0 and erg["ergebnis"]["combined"]["fair_discount_eur"] == 400.0
    doc = welt.run(db.ki_bewertungen.find_one({"id": erg["id"]}, {"_id": 0, "abgleich": 1}))
    assert doc["abgleich"] == {"doppelte": ["d1"], "fremde": ["fremd"]}
    # fehlende Position -> Lauf endet mit fehler und verstaendlichem Grund, nichts als Ergebnis
    D = _attrappe_vertrag(monkeypatch, antwort={**ANTWORT_V, "items": [ANTWORT_V["items"][0]]})
    erg = welt.run(D.bewerten(user=w.sucher, vehicle_doc=fz, damages=[dict(SCHAEDEN[0], zone="Tür vorne links"),
                                                                       SCHAEDEN[1]]))
    assert erg["status"] == "fehler" and erg["ergebnis"] is None
    assert "Kratzer Stoßfänger hinten" in erg["grund"] and "erneut starten" in erg["grund"]
    # Abholung: dieselbe Pruefung (Attrappe ohne Ergaenzung liefert nur drei von vielen Positionen)
    K = _attrappe_abholung(monkeypatch, ergaenzen=False)
    _cid, _tid, _vid, pid = _welt_aufbauen(welt, "r3")
    erg = welt.run(K.bewertung_ausfuehren(pid, w.dealer_id))
    assert erg["status"] == "fehler" and "KI hat Position" in erg["grund"] and "erneut starten" in erg["grund"]
    assert welt.run(db.ki_bewertungen.find_one({"protocol_id": pid}))["status"] == "fehler"
    _aufraeumen(welt)
    _ki_aufraeumen(welt)


# ------------------------------------------------ K4 / K5 Bereich des Kontos
def _sucher_b(welt):
    w, db = welt.w, welt.db
    b = {"id": f"su2_r17_{w.s}", "dealer_id": w.dealer_id, "role": "sucher", "active": True,
         "email": f"su2_{w.s}@e2etest-mail.de", "created_at": _jetzt(), "ki_aktiv": True}
    welt.run(db.users.insert_one(dict(b)))
    return {"id": b["id"], "dealer_id": w.dealer_id, "role": "sucher"}


def test_04_bewertung_lesen_nur_eigener_bereich(welt, monkeypatch):
    from fastapi import HTTPException
    D = _attrappe_vertrag(monkeypatch)
    C = _module("routes.contracts")
    w, db = welt.w, welt.db
    vid = f"v_kr4_{w.s}"
    welt.run(db.users.update_many({"id": {"$in": [w.chef["id"], w.sucher["id"]]}}, {"$set": {"ki_aktiv": True}}))
    welt.run(db.vehicles.insert_one(w.fahrzeug(vid, owner=w.sucher["id"], data={
        "make_label": "BMW", "model_label": "530 Gran Turismo", "mileage": 206000, "first_registration": "01/2010",
        "power_kw": 180, "fuel_label": "Diesel", "price": 8900})))
    fz = welt.run(db.vehicles.find_one({"id": vid}, {"_id": 0}))
    sucher_b = _sucher_b(welt)
    erg = welt.run(D.bewerten(user=w.sucher, vehicle_doc=fz, damages=SCHAEDEN))
    assert erg["status"] == "ok"
    assert welt.run(D.lesen(erg["id"], w.dealer_id, w.sucher))["status"] == "ok", "eigene Bewertung"
    assert welt.run(D.lesen(erg["id"], w.dealer_id, w.chef))["status"] == "ok", "Chef: Fahrzeug der Firma"
    assert welt.run(D.lesen(erg["id"], w.dealer_id, sucher_b)) is None, "Sucher B: nicht sein Fahrzeug"
    with pytest.raises(HTTPException) as ex:
        welt.run(C.vertrag_ki_schadennachlass_stand(erg["id"], user=sucher_b))
    assert ex.value.status_code == 404
    with pytest.raises(HTTPException) as ex:
        welt.run(C.vertrag_ki_vorschau(C.KiSchadenIn(vehicle_id=vid, damages=SCHAEDEN), user=sucher_b))
    assert ex.value.status_code == 404
    assert welt.run(C.vertrag_ki_schadennachlass_stand(erg["id"], user=w.chef))["status"] == "ok"
    # Mitbearbeiter darf lesen
    welt.run(db.vehicles.update_one({"id": vid}, {"$set": {"mitbearbeiter_ids": [sucher_b["id"]]}}))
    assert welt.run(C.vertrag_ki_schadennachlass_stand(erg["id"], user=sucher_b))["status"] == "ok"
    # ohne Fahrzeug an der Bewertung: nur das eigene Konto
    welt.run(db.ki_bewertungen.update_one({"id": erg["id"]}, {"$unset": {"vehicle_id": ""}}))
    assert welt.run(D.lesen(erg["id"], w.dealer_id, w.sucher))["status"] == "ok"
    assert welt.run(D.lesen(erg["id"], w.dealer_id, w.chef)) is None
    _ki_aufraeumen(welt)


def test_05_marktdaten_karte_nur_eigener_bereich(welt, monkeypatch):
    from fastapi import HTTPException
    M = _module("routes.markt")
    w, db = welt.w, welt.db
    vid = f"v_kr5_{w.s}"
    welt.run(db.vehicles.insert_one(w.fahrzeug(vid, owner=w.sucher["id"], data={"make_label": "BMW", "model_label": "320"})))
    sucher_b = _sucher_b(welt)

    async def _karte(db_, daten, listing_id=None):
        return {"sample_size": 3, "median_top20_price": 18400}
    monkeypatch.setattr(M.abfrage, "karte", _karte)
    assert welt.run(M.markt_karte(vid, user=w.chef))["sample_size"] == 3
    assert welt.run(M.markt_karte(vid, user=w.sucher))["sample_size"] == 3
    with pytest.raises(HTTPException) as ex:
        welt.run(M.markt_karte(vid, user=sucher_b))
    assert ex.value.status_code == 404 and "Fahrzeug nicht gefunden" in str(ex.value.detail)
    with pytest.raises(HTTPException) as ex:
        welt.run(M.markt_karte(vid, user={**w.chef, "dealer_id": "d_fremd"}))
    assert ex.value.status_code == 404
    quelle = (BACKEND / "routes" / "markt.py").read_text(encoding="utf-8")
    assert "**fahrzeug_bereich(user)" in quelle and '"dealer_id": user.get("dealer_id")' not in quelle


# ------------------------------------------------ K6 eigene Referenzen in Stufen
def _preis(key, *, marke="testmarke", modell="Alpha 2.0", alter="3-7", quelle="ADAC", stand=None, typisch=150):
    return {"key": key, "typ": "delle", "marke": marke, "modell": modell, "alter_klasse": alter, "min_eur": 80,
            "max_eur": 250, "typisch_eur": typisch, "quelle": quelle, "url": "", "art": "vertrag",
            "stand": stand or _jetzt()}


def _paket_vertrag(key="delle_klein"):
    return {"vehicle": {"make": "Testmarke", "model": "Alpha 2.0", "age_years": 5},
            "damages": [{"id": "d1", "type": "delle", "label": "Delle", "zone": "Tür",
                         "repair_reference": {"key": key, "low": 80, "median": 150, "high": 250}}]}


def test_06_eigene_referenzen_stufen_quellen_und_frist(welt):
    MD = _module("ai.marktdaten")
    db = welt.db
    assert MD.EIGENE_MIN == 5 and MD.EIGENE_TAGE == 120 and MD.EIGENE_QUELLEN_MIN == 2
    key = f"delle_kr6_{welt.w.s}"
    paket = _paket_vertrag(key)

    def _setzen(docs):
        welt.run(db.ki_reparaturpreise.delete_many({"key": key}))
        if docs:
            welt.run(db.ki_reparaturpreise.insert_many([dict(d) for d in docs]))
        return welt.run(MD.eigene_referenzen(paket, "vertrag"))

    # (1) Marke + Modell + Altersklasse, 5 Werte, 2 Quellen -> reicht
    e = _setzen([_preis(key, quelle="ADAC" if i % 2 else "FairGarage") for i in range(5)])
    assert e[key]["stufe"] == "marke_modell_alter" and e[key]["reicht"] is True and e[key]["n"] == 5
    assert e[key]["quellen_n"] == 2 and "testmarke" in e[key]["source"]
    assert MD.recherche_noetig(paket, "vertrag", e) == []
    # nur 4 Werte -> Anhalt aus der engsten Stufe, aber es wird weiter gesucht
    e = _setzen([_preis(key, quelle="ADAC" if i % 2 else "FairGarage") for i in range(4)])
    assert e[key]["stufe"] == "marke_modell_alter" and e[key]["reicht"] is False
    assert [p["id"] for p in MD.recherche_noetig(paket, "vertrag", e)] == ["d1"]
    # 5 Werte, aber nur EINE Quelle (auch mit Zusatz) -> reicht nicht
    e = _setzen([_preis(key, quelle="ADAC, netto 2024" if i % 2 else "ADAC") for i in range(5)])
    assert e[key]["reicht"] is False and e[key]["quellen_n"] == 1
    # (2) anderes Modell, gleiche Marke und Altersklasse
    e = _setzen([_preis(key, modell="Beta 1.6", quelle="ADAC" if i % 2 else "FairGarage") for i in range(5)])
    assert e[key]["stufe"] == "marke_alter" and e[key]["reicht"] is True and e[key]["nur_marke"] is True
    # (3) andere Altersklasse, gleiche Marke
    e = _setzen([_preis(key, alter="12+", quelle="ADAC" if i % 2 else "FairGarage") for i in range(5)])
    assert e[key]["stufe"] == "marke" and e[key]["reicht"] is True
    # (4) andere Marke -> alle
    e = _setzen([_preis(key, marke="andere", quelle="ADAC" if i % 2 else "FairGarage") for i in range(5)])
    assert e[key]["stufe"] == "alle" and e[key]["reicht"] is True and e[key]["nur_marke"] is False
    # Stufe 1 gewinnt vor Stufe 2, auch wenn Stufe 2 mehr Werte hat
    e = _setzen([_preis(key, quelle="ADAC" if i % 2 else "FairGarage", typisch=100) for i in range(5)]
                + [_preis(key, modell="Beta 1.6", quelle="ADAC" if i % 2 else "FairGarage", typisch=500) for i in range(9)])
    assert e[key]["stufe"] == "marke_modell_alter" and e[key]["median"] == 100
    # aelter als 120 Tage zaehlt nicht
    e = _setzen([_preis(key, quelle="ADAC" if i % 2 else "FairGarage", stand=_vor_tagen(121)) for i in range(5)])
    assert key not in e
    e = _setzen([_preis(key, quelle="ADAC" if i % 2 else "FairGarage", stand=_vor_tagen(119)) for i in range(5)])
    assert e[key]["reicht"] is True
    welt.run(db.ki_reparaturpreise.delete_many({"key": key}))


# ------------------------------------------------ K7 Recherche: 12 Positionen, teuerste zuerst
def test_07_recherche_hoechstens_zwoelf_teuerste_zuerst():
    MD = _module("ai.marktdaten")
    assert MD.RECHERCHE_POSITIONEN_MAX == 12
    positionen = [{"id": f"d{i}", "type": "delle", "label": "Delle", "zone": f"Zone {i}", "severity_data": {},
                   "repair_reference": {"key": "delle_klein", "low": 10, "median": i * 10, "high": 20}}
                  for i in range(1, 15)]                      # Mediane 10 ... 140
    auswahl = MD.recherche_auswahl(positionen)
    assert [p["id"] for p in auswahl] == [f"d{i}" for i in range(14, 2, -1)], "teuerste zuerst, d1/d2 fallen weg"
    frage = MD._fall_frage("vertrag", {"vehicle": {"make": "BMW", "model": "320d"}, "damages": positionen}, positionen)
    zeilen = [z for z in frage.splitlines() if z.startswith("- id ")]
    assert len(zeilen) == 12 and zeilen[0].startswith("- id d14:") and zeilen[-1].startswith("- id d3:")
    assert "- id d1:" not in frage and "- id d2:" not in frage
    # ohne Referenz zaehlt 0 -> ans Ende, nie ein Absturz
    ohne = [{"id": "x", "type": "kratzer", "label": "Kratzer"}]
    assert [p["id"] for p in MD.recherche_auswahl(positionen[:3] + ohne)] == ["d3", "d2", "d1", "x"]


# ------------------------------------------------ K8 Budget: Zaehler und Abgleich
def test_08_budget_zaehler_und_abgleich(welt, monkeypatch):
    B = _module("ai.budget")
    w, db = welt.w, welt.db
    monkeypatch.setenv("KI_BUDGET_MONAT_EUR", "15")
    monkeypatch.setenv("KI_KOSTEN_MAX_CT", "15")
    key = B._schluessel(None, w.dealer_id, "abholung")
    welt.run(db.ki_budget.delete_many({"_id": {"$regex": f":{w.dealer_id}:"}}))
    welt.run(db.ki_bewertungen.delete_many({"dealer_id": w.dealer_id}))
    # verwaiste Reservierung (Lauf abgestuerzt, nie abgerechnet) -> Abgleich holt sie zurueck
    res = welt.run(B.reservieren(user_id=None, dealer_id=w.dealer_id, art="abholung"))
    assert res and res["schluessel"] == key
    assert welt.run(B.zaehler_ct(user_id=None, dealer_id=w.dealer_id, art="abholung")) == 15.0
    assert welt.run(B.pruefen(user_id=None, dealer_id=w.dealer_id, art="abholung"))["verbraucht_ct"] == 15.0
    erg = welt.run(B.abgleichen(db))
    assert erg.get("fehler") is None and key in [k for k in (welt.run(db.ki_budget.distinct("_id")))]
    assert welt.run(B.zaehler_ct(user_id=None, dealer_id=w.dealer_id, art="abholung")) == 0.0
    # laufender Lauf mit gueltigem Lease zaehlt mit est_ct, abgelaufener nicht
    lauf = {"id": f"lauf_kr8_{w.s}", "art": "abholung", "dealer_id": w.dealer_id, "protocol_id": f"p_kr8_{w.s}",
            "input_hash": "h8", "status": "laeuft", "created_at": _jetzt(), "lease_until": _in_sekunden(100)}
    welt.run(db.ki_bewertungen.insert_one(dict(lauf)))
    welt.run(B.est_ct_vermerken(lauf["id"], res))
    welt.run(B.abgleichen(db))
    assert welt.run(B.zaehler_ct(user_id=None, dealer_id=w.dealer_id, art="abholung")) == 15.0
    welt.run(db.ki_bewertungen.update_one({"id": lauf["id"]}, {"$set": {"lease_until": _in_sekunden(-5)}}))
    welt.run(B.abgleichen(db))
    assert welt.run(B.zaehler_ct(user_id=None, dealer_id=w.dealer_id, art="abholung")) == 0.0
    # abgeschlossene Laeufe: Summe kosten_ct (ok und fehler), je Art/Konto getrennt
    welt.run(db.ki_bewertungen.insert_many([
        {"id": f"ok_kr8_{w.s}", "art": "abholung", "dealer_id": w.dealer_id, "status": "ok", "created_at": _jetzt(),
         "kosten_ct": 3.5},
        {"id": f"f_kr8_{w.s}", "art": "abholung", "dealer_id": w.dealer_id, "status": "fehler", "created_at": _jetzt(),
         "kosten_ct": 1.25},
        {"id": f"v_kr8_{w.s}", "art": "vertrag", "dealer_id": w.dealer_id, "user_id": w.sucher["id"], "status": "ok",
         "created_at": _jetzt(), "kosten_ct": 2.0}]))
    welt.run(B.abgleichen(db))
    assert welt.run(B.zaehler_ct(user_id=None, dealer_id=w.dealer_id, art="abholung")) == 4.75
    assert welt.run(B.zaehler_ct(user_id=w.sucher["id"], dealer_id=w.dealer_id, art="vertrag")) == 2.0
    bud = welt.run(B.pruefen(user_id=None, dealer_id=w.dealer_id, art="abholung"))
    assert bud["verbraucht_ct"] == 4.75 and bud["letzter_lauf_ct"] > 0 and bud["erlaubt"] is True
    # Aufraeumlauf-Schritt vorhanden
    quelle = (BACKEND / "cleanup_service.py").read_text(encoding="utf-8")
    assert 'await s("ki_budget"' in quelle and "abgleichen" in quelle
    welt.run(db.ki_budget.delete_many({"_id": {"$regex": f":({w.dealer_id}|{w.sucher['id']}):"}}))
    welt.run(db.ki_bewertungen.delete_many({"dealer_id": w.dealer_id}))


# ------------------------------------------------ K9 atomarer Claim (Abholung)
def test_09_zwei_parallele_starts_ein_aufruf(welt, monkeypatch):
    aufrufe = []
    K = _attrappe_abholung(monkeypatch, zaehler=aufrufe, pause_s=0.6)
    IX = _module("indizes")
    welt.run(IX.ki_indizes(welt.db))
    w, db = welt.w, welt.db
    _cid, _tid, _vid, pid = _welt_aufbauen(welt, "r9")

    async def _parallel():
        return await asyncio.gather(K.bewertung_ausfuehren(pid, w.dealer_id),
                                    K.bewertung_ausfuehren(pid, w.dealer_id, erzwingen=True))
    a, b = welt.run(_parallel())
    assert sorted([a["status"], b["status"]]) == ["laeuft", "ok"], (a["status"], b["status"])
    assert len(aufrufe) == 1, "genau EIN Modellaufruf"
    assert welt.run(db.ki_bewertungen.count_documents({"protocol_id": pid})) == 1
    gespeichert = welt.run(db.ki_bewertungen.find_one({"protocol_id": pid}, {"_id": 0}))
    assert gespeichert["status"] == "ok" and "lease_until" not in gespeichert
    # laufender Lauf mit gueltigem Lease: auch erzwingen startet nicht doppelt
    welt.run(db.ki_bewertungen.update_one({"protocol_id": pid}, {"$set": {"status": "laeuft", "lease_until": _in_sekunden(90)}}))
    erg = welt.run(K.bewertung_ausfuehren(pid, w.dealer_id, erzwingen=True))
    assert erg["status"] == "laeuft" and len(aufrufe) == 1
    # abgelaufener Lease wird atomar uebernommen
    welt.run(db.ki_bewertungen.update_one({"protocol_id": pid}, {"$set": {"lease_until": _in_sekunden(-1)}}))
    erg = welt.run(K.bewertung_ausfuehren(pid, w.dealer_id))
    assert erg["status"] == "ok" and len(aufrufe) == 2
    _aufraeumen(welt)


# ------------------------------------------------ K10 "Neu berechnen" im Hintergrund
def test_10_neu_berechnen_antwortet_sofort_und_rechnet_im_hintergrund(welt, monkeypatch):
    from fastapi import HTTPException
    aufrufe = []
    K = _attrappe_abholung(monkeypatch, zaehler=aufrufe, pause_s=0.4)
    P = _module("routes.protocols")
    w = welt.w
    _cid, _tid, _vid, pid = _welt_aufbauen(welt, "r10")
    t0 = time.perf_counter()
    erg = welt.run(P.protokoll_ki_bewertung_neu(pid, user=w.chef))
    dauer = time.perf_counter() - t0
    assert erg["status"] == "laeuft" and erg["protocol_id"] == pid and erg["ergebnis"] is None
    assert dauer < 0.35, f"Route wartet nicht auf die KI ({dauer:.2f} s)"
    assert aufrufe == [], "der Lauf beginnt erst nach der Antwort"
    hintergrund_abwarten(welt, K)
    assert len(aufrufe) == 1
    erg = welt.run(P.protokoll_ki_bewertung(pid, user=w.chef))
    assert erg["status"] == "ok" and erg["ergebnis"]["combined"]["fair_discount_eur"] == 530.0
    with pytest.raises(HTTPException) as ex:
        welt.run(P.protokoll_ki_bewertung_neu(f"gibtsnicht_{w.s}", user=w.chef))
    assert ex.value.status_code == 404
    # KI aus -> sofort "aus", kein Lauf
    monkeypatch.setattr(K, "ki_aktiv", lambda: False)
    assert welt.run(P.protokoll_ki_bewertung_neu(pid, user=w.chef))["status"] == "aus"
    hintergrund_abwarten(welt, K)
    assert len(aufrufe) == 1
    _aufraeumen(welt)


# ------------------------------------------------ K11 Marktvergleich
def _fz(w, vid, **data):
    d = {"make_label": "Mercedes-Benz", "model_label": "C 220 d", "price": 20000, "first_registration": "05/2020",
         "mileage": 60000, "fuel_label": "Diesel", "gearbox_label": "Automatik", "power_kw": 143}
    d.update(data)
    return w.fahrzeug(vid, data=d)


def test_11_marktvergleich_dedupe_modell_getriebe_leistung_alter(welt):
    KX = _module("ai.kontext")
    w, db = welt.w, welt.db
    s = w.s
    docs = [_fz(w, f"v_kr11a{i}_{s}") for i in range(3)]                          # 3x C 220 d, 20.000, Automatik
    docs += [_fz(w, f"v_kr11b{i}_{s}", model_label="C 180", price=15000) for i in range(3)]
    docs += [_fz(w, f"v_kr11c{i}_{s}", gearbox_label="Schaltgetriebe", price=30000) for i in range(3)]
    docs += [_fz(w, f"v_kr11d_{s}", model_label="C 2200", price=40000)]
    docs += [_fz(w, f"v_kr11e_{s}", model_label="C 220 d 4MATIC", price=21000)]
    docs += [_fz(w, f"v_kr11f_{s}", power_kw=250, price=50000)]
    alt = _fz(w, f"v_kr11g_{s}", price=60000)
    alt["created_at"] = alt["updated_at"] = _vor_tagen(200)
    docs.append(alt)
    welt.run(db.vehicles.insert_many(docs))
    eigene = {"make_label": "Mercedes-Benz", "model_label": "C 220 d", "first_registration": "05/2020", "mileage": 60000,
              "fuel_label": "Diesel", "gearbox_label": "Automatik", "power_kw": 140}
    KX._markt_cache.clear()
    m = welt.run(KX.marktvergleich(eigene, db=db))
    # 3x 20.000 (nicht dedupliziert!) + 21.000 (4MATIC, Wort-Praefix); C 180, C 2200, Schalter, 250 kW, 200 Tage alt: nein
    assert m["source"] == "grob" and m["comparable_count"] == 4 and m["median_price_eur"] == 20000, m
    # Halbautomatik zaehlt wie Automatik; Schalter findet die drei Schalter
    KX._markt_cache.clear()
    m2 = welt.run(KX.marktvergleich({**eigene, "gearbox_label": "Halbautomatik"}, db=db))
    assert m2["comparable_count"] == 4
    KX._markt_cache.clear()
    m3 = welt.run(KX.marktvergleich({**eigene, "gearbox_label": "Schaltgetriebe"}, db=db))
    assert m3["comparable_count"] == 3 and m3["median_price_eur"] == 30000
    # ohne Getriebe/Leistung: kein Filter darauf (3 + 3 Schalter + 4MATIC + 250 kW = 8)
    KX._markt_cache.clear()
    m4 = welt.run(KX.marktvergleich({k: v for k, v in eigene.items() if k not in ("gearbox_label", "power_kw")}, db=db))
    assert m4["comparable_count"] == 8
    # C 180 trifft nur C 180
    KX._markt_cache.clear()
    m5 = welt.run(KX.marktvergleich({**eigene, "model_label": "C 180"}, db=db))
    assert m5["comparable_count"] == 3 and m5["median_price_eur"] == 15000
    # Modell-Matching als Wort-Praefix
    assert KX.modell_passt({"model_label": "C 220 d 4MATIC"}, "c 220 d")
    assert KX.modell_passt({"model": "C 220"}, "c 220 d")
    assert not KX.modell_passt({"model_label": "C 180"}, "c 220 d") and not KX.modell_passt({"model_label": "C 2200"}, "c 220")
    # Cache-Schluessel: Jahr exakt, km je 10.000, Getriebe, kW je 10
    k1 = KX._markt_schluessel(eigene)
    assert k1 != KX._markt_schluessel({**eigene, "first_registration": "05/2021"})
    assert k1 != KX._markt_schluessel({**eigene, "gearbox_label": "Schaltgetriebe"})
    assert k1 != KX._markt_schluessel({**eigene, "power_kw": 165})
    assert k1 != KX._markt_schluessel({**eigene, "mileage": 71000})
    assert k1 == KX._markt_schluessel({**eigene, "mileage": 69000, "power_kw": 149, "gearbox_label": "Halbautomatik"})
    assert "c 220 d" in k1
    KX._markt_cache.clear()


def test_12_marktbeobachtung_hat_vorrang(welt):
    KX = _module("ai.kontext")
    MK = _module("markt.konfig")
    w, db = welt.w, welt.db
    s = w.s
    mid = f"test-kiv-{s}"
    seg_id = f"{mid}:2020:50001-100000"
    jetzt = _jetzt()

    def _weg():
        for coll in (MK.MODELLE, MK.SEGMENTE, MK.SEGMENTSTATS):
            welt.run(db[coll].delete_many({"$or": [{"id": {"$regex": "^test-kiv"}}, {"_id": {"$regex": "^test-kiv"}},
                                                   {"model_id": {"$regex": "^test-kiv"}}]}))
    _weg()
    welt.run(db[MK.MODELLE].insert_one({"id": mid, "make": "BMW", "model": "320", "variant": "320d", "label": "BMW 320d (KI-Test)",
                                        "fuel": "DIESEL", "power_kw_min": 120, "power_kw_max": 145, "priority": 1,
                                        "enabled": True, "make_id": "3500", "model_id": "10",
                                        "km_buckets": [{"min_km": 50001, "max_km": 100000}], "ez_years": [2020]}))
    welt.run(db[MK.SEGMENTE].insert_one({"id": seg_id, "model_id": mid, "label": "BMW 320d (KI-Test)", "min_km": 50001,
                                         "max_km": 100000, "km_label": "50–100k km", "year_from": 2020, "year_to": 2020,
                                         "ez_label": "EZ 2020", "max_items": 20, "sort": "price_asc", "enabled": True,
                                         "priority": 1}))
    welt.run(db[MK.SEGMENTSTATS].insert_one({"_id": seg_id, "segment_id": seg_id, "sample_size": 3, "min_price": 17500,
                                             "median_price": 18400, "avg_price": 19200, "max_price": 21700,
                                             "p25_price": 17950, "p75_price": 20050, "updated_at": jetzt,
                                             "date": jetzt[:10], "datenlage": "niedrig"}))
    # grobe Vergleichsdaten, die OHNE Marktbeobachtung greifen wuerden
    welt.run(db.vehicles.insert_many([_fz(w, f"v_kr12_{i}_{s}", make_label="BMW", model_label="320", price=25000,
                                          fuel_label="Diesel", power_kw=140) for i in range(3)]))
    fz = {"make_label": "BMW", "model_label": "320", "first_registration": "05/2020", "mileage": 60000, "fuel_label": "Diesel",
          "power_kw": 140, "list_price": 19000}
    try:
        KX._markt_cache.clear()
        m = welt.run(KX.marktvergleich(fz, db=db))
        assert m["source"] == "marktbeobachtung" and m["comparable_count"] == 3 and m["median_price_eur"] == 18400
        assert m["min_price_eur"] == 17500 and m["p25_price_eur"] == 17950 and m["p75_price_eur"] == 20050
        assert m["datenstand"] == jetzt and m["segment"] == "BMW 320d (KI-Test)"
        pos = KX.marktposition(m, listing=19000, agreed=18000)
        assert pos["source"] == "marktbeobachtung" and pos["listing_vs_median_percent"] == 3.3
        # Segment ohne Daten -> grober Vergleich als Rueckfall
        welt.run(db[MK.SEGMENTSTATS].update_one({"_id": seg_id}, {"$set": {"sample_size": 0}}))
        KX._markt_cache.clear()
        m2 = welt.run(KX.marktvergleich(fz, db=db))
        assert m2["source"] == "grob" and m2["median_price_eur"] == 25000
        # Kontext insgesamt: eine Marktzahl mit Quelle
        welt.run(db[MK.SEGMENTSTATS].update_one({"_id": seg_id}, {"$set": {"sample_size": 3}}))
        KX._markt_cache.clear()
        ktx = welt.run(KX.sammeln(fz, "vertrag", db=db))
        assert ktx["markt"]["source"] == "marktbeobachtung"
    finally:
        _weg()
        KX._markt_cache.clear()


# ------------------------------------------------ K12 Kalibrierung je Art
def test_13_kalibrierung_firma_vor_global_und_band(welt, monkeypatch):
    K = _module("ai.kalibrierung")
    w, db = welt.w, welt.db
    docs = [{"art": "abholung", "dealer_id": w.dealer_id, "protocol_id": f"p_kal{i}_{w.s}", "created_at": _jetzt(),
             "ki_nachlass": 100.0, "tatsaechlicher_nachlass": 250.0,
             "items": [{"category": "keys", "fair_discount_eur": 100.0}]} for i in range(5)]
    docs += [{"art": "vertrag", "dealer_id": w.dealer_id, "contract_id": f"c_kal{i}_{w.s}", "created_at": _jetzt(),
              "ki_nachlass": 100.0, "tatsaechlicher_nachlass": 20.0,
              "items": [{"category": "damage", "fair_discount_eur": 100.0}]} for i in range(5)]
    welt.run(db.ki_lernfaelle.insert_many(docs))
    try:
        monkeypatch.setattr(K, "MIN_FIRMA", 5)
        K.zuruecksetzen()
        firma = welt.run(K.erfahrungswerte(frisch=True, dealer_id=w.dealer_id))
        assert firma["je_art"]["abholung"]["faktor_median"] == 2.5 and firma["je_art"]["vertrag"]["faktor_median"] == 0.2
        ab = welt.run(K.prompt_zusatz(w.dealer_id, "abholung"))
        ve = welt.run(K.prompt_zusatz(w.dealer_id, "vertrag"))
        assert "Faellen dieser Firma (Abholung)" in ab and "etwa 120 %" in ab and "250 %" not in ab
        assert "Faellen dieser Firma (Vertrag)" in ve and "etwa 60 %" in ve and "20 %" not in ve
        assert "keys etwa 120 %" in ab and "damage etwa 60 %" in ve
        for t in (ab, ve):
            assert "Orientierung" in t and "kein Ersatz fuer die Reparaturkosten" in t
            assert "Faellen dieser Firma" in t and t.count("Erfahrungswerte aus") == 1, "EIN Wert, nicht Firma + global"
        # ohne Firma: global je Art (nur mit genug Faellen), nie der Firmenwert
        global_text = welt.run(K.prompt_zusatz(None, "abholung"))
        assert "dieser Firma" not in global_text
        assert K.als_text({"je_art": {"abholung": {"n": 3, "faktor_median": 0.9}}}, art="abholung", minimum=4) == ""
        assert "etwa 90 %" in K.als_text({"je_art": {"abholung": {"n": 3, "faktor_median": 0.9}}}, art="abholung", minimum=3)
    finally:
        welt.run(db.ki_lernfaelle.delete_many({"dealer_id": w.dealer_id}))
        K.zuruecksetzen()


# ------------------------------------------------ Umgebung
def test_14_cache_tage_erreichen_den_container():
    text = (BACKEND.parent / "docker-compose.yml").read_text(encoding="utf-8")
    assert "- KI_CACHE_TAGE=${KI_CACHE_TAGE:-7}" in text
    PR = _module("ai.provider")
    assert 1 <= PR.ki_cache_tage() <= 365
