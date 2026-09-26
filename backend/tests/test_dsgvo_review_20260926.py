# -*- coding: utf-8 -*-
"""Reparaturwelle Review 26.09.2026, Nr. 136-147 — Freigabe-Schnappschuss,
DSGVO-Loeschung der KI-Daten, KI-Indizes einzeln. In-Prozess gegen DB_NAME
(Fixture `welt` aus test_golive_20260913_abschluss: Storage/PDF im Speicher).

136/137 kanonischer Freigabe-Stand (Reifen, Scheckheft, Ausstattung, Maengel,
        Schluessel, Bedingungen); Abschluss meldet "Vertrag geaendert";
        Altformat wird nach dem alten Verfahren geprueft
138/139 PII-Bereinigung leert Rueckfrage-Frage/Antworten/Verlauf und die
        Freitext-Notizen der Schaeden; die Nachsuche findet sie
140/147 ki_bewertungen: Rohdaten nach KI_BEWERTUNG_ROHDATEN_TAGE, Loeschung
        nach KI_BEWERTUNG_TAGE (ausser mit Lernfall); Lernfall ohne Klartext
141-145 Firmenloeschung/Loeschvorschau mit KI-Sammlungen, ki_budget-Rotation
143     Sucher-Loeschung pseudonymisiert die KI-Spuren
146     ki_indizes: je Index eigener Fehlerfang
"""
import asyncio  # noqa: F401
import inspect
import re
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from fastapi import HTTPException

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))
from test_golive_20260913_abschluss import (  # noqa: E402,F401  (welt = Fixture)
    _abholung, _doc, _fin, _jetzt, _m, welt)

BACKEND = Path(__file__).resolve().parents[1]


def _vor_tagen(n):
    return (datetime.now(timezone.utc) - timedelta(days=n)).isoformat()


def _fehler(w, coro):
    with pytest.raises(HTTPException) as e:
        w.run(coro)
    return e.value.status_code, e.value.detail


# ============================================================ 136/137
def test_136_schnappschuss_sieht_alle_freigaberelevanten_felder():
    P = _m("routes.protocols")
    vehicle = {"make_label": "BMW", "model_label": "320d", "vin": "WBA12345678901234", "mileage": 120000,
               "first_registration": "03/2018", "features": ["Klima", "AHK"],
               "known_defects": ["Rollo lose"], "description": "gepflegt, COC vorhanden"}
    contract = {"purchase_price": 9000.0, "tires": "4-fach", "service_book": "ja", "hu_valid": "Ja",
                "schluessel_anzahl": "2", "additional_terms": "", "pickup_date": "2026-10-01",
                "pickup_time": "10:00", "pickup_address": "Teststr. 1",
                "damages": [{"id": "k1", "type_key": "kratzer", "zone": "Tür vorne links"}]}
    basis = P.vertragswerte_stand(vehicle, contract)
    assert basis.startswith("v2:") and len(basis) == 3 + 64
    assert basis == P.vertragswerte_stand(dict(vehicle), dict(contract)), "deterministisch"
    schnapp = P.freigabe_schnappschuss(contract, vehicle)
    assert schnapp["v"] == 2 and schnapp["unterlagen"]["Zweitsatz Reifen"] is False
    assert schnapp["unterlagen"]["Servicebuch / Scheckheft"] is True
    assert schnapp["unterlagen"]["COC-Papiere (EG-Übereinstimmung)"] is True
    assert schnapp["schluessel_anzahl"] == 2 and schnapp["known_defects"] == ["Rollo lose"]
    # jede dieser Aenderungen macht die Freigabe hinfaellig
    for feld, wert in (("tires", "8-fach"), ("service_book", "nein"), ("hu_valid", "Nein"),
                       ("schluessel_anzahl", "3"), ("additional_terms", "Winterreifen werden nachgeliefert"),
                       ("pickup_date", "2026-10-02"), ("purchase_price", 8500.0),
                       ("preis_vor_abholung", 9500.0),
                       ("damages", [{"id": "k1", "type_key": "delle", "zone": "Tür vorne links"}])):
        assert P.vertragswerte_stand(vehicle, {**contract, feld: wert}) != basis, feld
    for feld, wert in (("features", ["Klima"]), ("known_defects", []), ("vin", "WBA00000000000000"),
                       ("mileage", 130000), ("description", "gepflegt, COC nicht vorhanden")):
        assert P.vertragswerte_stand({**vehicle, feld: wert}, contract) != basis, feld
    # Reihenfolge der Ausstattung und eine Textkorrektur ohne geaenderte Zusage: gleich
    assert P.vertragswerte_stand({**vehicle, "features": ["AHK", "Klima"]}, contract) == basis
    assert P.vertragswerte_stand({**vehicle, "description": "sehr gepflegt, COC vorhanden"}, contract) == basis
    # Altformat (erste Fassung, ohne Praefix): nach dem alten Verfahren geprueft
    alt = P._vertragswerte_stand_v1(vehicle, contract)
    assert ":" not in alt and P.vertragswerte_stand_gleich(alt, vehicle, contract)
    assert not P.vertragswerte_stand_gleich(alt, vehicle, {**contract, "purchase_price": 1.0})
    assert P.vertragswerte_stand_gleich(basis, vehicle, contract)
    assert not P.vertragswerte_stand_gleich(basis, vehicle, {**contract, "tires": "8-fach"})
    for kaputt in ("", None, "v9:abc", 42):
        assert not P.vertragswerte_stand_gleich(kaputt, vehicle, contract)
    # leere/fehlende Eingaben stuerzen nicht ab
    assert P.vertragswerte_stand({}, {}).startswith("v2:")


def test_137_abschluss_meldet_vertrag_geaendert_nach_reifen_scheckheft_ausstattung(welt):
    w = welt
    P = _m("routes.protocols")
    faelle = [("a", lambda t: w.db.generated_pdfs.update_one({"id": t.ca}, {"$set": {"contract_data.tires": "8-fach"}})),
              ("b", lambda t: w.db.generated_pdfs.update_one({"id": t.ca}, {"$set": {"contract_data.service_book": "nein"}})),
              ("c", lambda t: w.db.vehicles.update_one({"id": t.vid}, {"$set": {"data.known_defects": ["Klima defekt"]}})),
              ("d", lambda t: w.db.generated_pdfs.update_one(
                  {"id": t.ca}, {"$set": {"contract_data.additional_terms": "Winterreifen werden nachgeliefert"}}))]
    for name, aenderung in faelle:
        t = _abholung(w, name=name, proto_status="entwurf")
        w.run(P.submit_protocol(t.aid, w.driver))
        p = _doc(w, "pickup_protocols", t.pid)
        assert p["vertragswerte_stand"].startswith("v2:")
        fr = w.run(P.protokoll_freigeben(t.pid, P.FreigabeIn(neuer_preis=9000, stand=p["freigabe_stand"]), w.chef))
        w.run(aenderung(t))
        code, text = _fehler(w, P.finalize_protocol(t.aid, _fin(P, 9000.0, fr["stand"]), w.driver))
        assert code == 409 and "Vertrag" in text, (name, text)
        p = _doc(w, "pickup_protocols", t.pid)
        assert p["status"] == "entwurf" and "vertragswerte_stand" not in p and p["rueckfrage"], name
    # Ausstattung im Inserat ergaenzt -> ebenfalls hinfaellig
    t = _abholung(w, name="e", proto_status="entwurf")
    w.run(w.db.vehicles.update_one({"id": t.vid}, {"$set": {"data.features": ["Klima"]}}))
    w.run(w.db.pickup_protocols.update_one({"id": t.pid}, {"$set": {"features": {"Klima": True}}}))
    w.run(P.submit_protocol(t.aid, w.driver))
    p = _doc(w, "pickup_protocols", t.pid)
    fr = w.run(P.protokoll_freigeben(t.pid, P.FreigabeIn(neuer_preis=9000, stand=p["freigabe_stand"]), w.chef))
    w.run(w.db.vehicles.update_one({"id": t.vid}, {"$set": {"data.features": ["Klima", "AHK"]}}))
    code, text = _fehler(w, P.finalize_protocol(t.aid, _fin(P, 9000.0, fr["stand"]), w.driver))
    assert code == 409 and "Vertrag" in text


def test_137b_altes_standformat_scheitert_nicht_an_der_umstellung(welt):
    """Protokoll, das VOR der Umstellung freigegeben wurde (Stand ohne Praefix):
    unveraendert -> Abschluss laeuft durch; Preis geaendert -> weiter 409."""
    w = welt
    P = _m("routes.protocols")
    t = _abholung(w, name="alt", proto_status="entwurf")
    w.run(P.submit_protocol(t.aid, w.driver))
    appt = _doc(w, "appointments", t.aid)
    vehicle, contract = w.run(P._fahrzeug_und_vertrag(appt))
    alt = P._vertragswerte_stand_v1(vehicle, contract)
    w.run(w.db.pickup_protocols.update_one({"id": t.pid}, {"$set": {"vertragswerte_stand": alt}}))
    p = _doc(w, "pickup_protocols", t.pid)
    fr = w.run(P.protokoll_freigeben(t.pid, P.FreigabeIn(neuer_preis=9000, stand=p["freigabe_stand"]), w.chef))
    assert w.run(P.finalize_protocol(t.aid, _fin(P, 9000.0, fr["stand"]), w.driver))["ok"]
    t2 = _abholung(w, name="alt2", proto_status="entwurf")
    w.run(P.submit_protocol(t2.aid, w.driver))
    appt2 = _doc(w, "appointments", t2.aid)
    vehicle2, contract2 = w.run(P._fahrzeug_und_vertrag(appt2))
    w.run(w.db.pickup_protocols.update_one(
        {"id": t2.pid}, {"$set": {"vertragswerte_stand": P._vertragswerte_stand_v1(vehicle2, contract2)}}))
    p2 = _doc(w, "pickup_protocols", t2.pid)
    fr2 = w.run(P.protokoll_freigeben(t2.pid, P.FreigabeIn(neuer_preis=9000, stand=p2["freigabe_stand"]), w.chef))
    w.run(w.db.generated_pdfs.update_one({"id": t2.ca}, {"$set": {"contract_data.purchase_price": 12000.0}}))
    code, text = _fehler(w, P.finalize_protocol(t2.aid, _fin(P, 9000.0, fr2["stand"]), w.driver))
    assert code == 409 and "Vertrag" in text


# ============================================================ 138/139
def test_138_pii_entfernen_leert_rueckfragen_schadennotizen_und_ki_rohdaten(welt):
    w = welt
    CS = _m("cleanup_service")
    aid, pid, bid = f"a138_{w.s}", f"p138_{w.s}", f"b138_{w.s}"

    async def lauf():
        await w.db.pickup_protocols.insert_one(
            {"id": pid, "appointment_id": aid, "dealer_id": w.dealer_id, "version": 1, "status": "final",
             "seller_name": "Vera", "place": "Hannover", "notes": "Tel 0170 123",
             "rueckfrage_frage": {"question": "Bitte Frau Vera (0170 123) fragen", "source_id": "n1"},
             "rueckfrage_antworten": [{"question": "x", "answer": "Vera sagt ja, 0170 123"}],
             "rueckfrage_verlauf": [{"frage": {"question": "Vera?"}, "antworten": [{"answer": "0170 123"}]}],
             "new_damages": [{"id": "n1", "type_key": "delle", "zone": "Tür", "note": "laut Vera vom Nachbarn",
                              "severity_data": {"groesse": "bis 2 cm"}}]})
        await w.db.ki_bewertungen.insert_one(
            {"id": bid, "art": "abholung", "dealer_id": w.dealer_id, "protocol_id": pid, "input_hash": "h",
             "status": "ok", "created_at": _jetzt(), "kosten_ct": 3.5,
             "eingabe": {"driver_notes": "Tel 0170 123", "deviations": [{"field": "vin", "actual": "WBA1"}]},
             "roh": {"items": []}, "abgleich": {"doppelte": []},
             "recherche": {"status": "ok", "suchen": 2, "text": "Vera ...", "quellen": [{"url": "u"}], "gelernt": 1},
             "ergebnis": {"combined": {"fair_discount_eur": 300}, "items": []}})
        await CS._protokolle_pii_entfernen(w.db, [aid], w.dealer_id, _jetzt())
        return (await w.db.pickup_protocols.find_one({"id": pid}, {"_id": 0}),
                await w.db.ki_bewertungen.find_one({"id": bid}, {"_id": 0}))
    try:
        p, b = w.run(lauf())
        assert p["pii_geloescht_at"] and p["seller_name"] == "" and p["notes"] == ""
        assert p["rueckfrage_frage"] is None and p["rueckfrage_antworten"] == [] and p["rueckfrage_verlauf"] == []
        assert p["new_damages"] == [{"id": "n1", "type_key": "delle", "zone": "Tür",
                                     "severity_data": {"groesse": "bis 2 cm"}}], "Notiz weg, Zustand bleibt"
        assert "Vera" not in str(p) and "0170" not in str(p)
        # KI-Bewertung: Rohdaten weg, Ergebnis/Kosten/Hash bleiben
        assert b["eingabe"] is None and b["roh"] is None and b["abgleich"] is None
        assert b["recherche"]["text"] is None and b["recherche"]["quellen"] is None
        assert b["recherche"]["status"] == "ok" and b["recherche"]["suchen"] == 2
        assert b["ergebnis"]["combined"]["fair_discount_eur"] == 300 and b["kosten_ct"] == 3.5
        assert b["input_hash"] == "h" and b["rohdaten_entfernt_am"]
        assert "Vera" not in str(b) and "0170" not in str(b) and "WBA1" not in str(b)
    finally:
        w.run(w.db.ki_bewertungen.delete_many({"id": bid}))
    # Quelltext: dieselben Felder auch bei der Vertragsloeschung (laeuft ueber
    # _protokolle_pii_entfernen) — kein zweiter Pfad mit eigener Feldliste
    src = (BACKEND / "cleanup_service.py").read_text(encoding="utf-8")
    assert src.count('"rueckfrage_verlauf": []') == 1
    assert "await _protokolle_pii_entfernen(db, termin_ids, dealer_id, jetzt," in src


def test_139_nachsuche_findet_protokoll_nur_mit_rueckfrage_verlauf(welt, monkeypatch):
    w = welt
    CS = _m("cleanup_service")
    monkeypatch.setenv("VERTRAG_LOESCHUNG_AKTIV", "true")
    aid, pid = f"a139_{w.s}", f"p139_{w.s}"
    alt = _vor_tagen(400)

    async def lauf():
        # Termin laengst geschlossen, ohne Vertrag, eigene Felder schon leer —
        # nur das Protokoll traegt noch den Rueckfrage-Verlauf
        await w.db.appointments.insert_one(
            {"id": aid, "dealer_id": w.dealer_id, "status": "abgeholt", "abgeschlossen_seit": alt,
             "created_at": alt, "seller_name": "", "seller_phone": "", "seller_email": "", "pickup_address": ""})
        await w.db.pickup_protocols.insert_one(
            {"id": pid, "appointment_id": aid, "dealer_id": w.dealer_id, "version": 1, "status": "final",
             "seller_name": "", "place": "",
             "rueckfrage_verlauf": [{"frage": {"question": "Vera?"}, "antworten": [{"answer": "0170 123"}]}]})
        await CS.termine_ohne_vertrag_bereinigen(w.db, datetime.now(timezone.utc))
        return await w.db.pickup_protocols.find_one({"id": pid}, {"_id": 0})
    p = w.run(lauf())
    assert p["pii_geloescht_at"] and p["rueckfrage_verlauf"] == [] and "0170" not in str(p)
    # Quelltext: alle drei Rueckfrage-Felder, Notizen und Sondervereinbarung in der Nachsuche
    src = inspect.getsource(CS.termine_ohne_vertrag_bereinigen)
    for feld in ("rueckfrage_frage.question", "rueckfrage_antworten.0", "rueckfrage_verlauf.0",
                 '"notes": {"$nin"', '"sondervereinbarung": {"$nin"'):
        assert feld in src, feld


# ============================================================ 140/147
def test_140_retention_rohdaten_nach_90_tagen_loeschung_nach_730(welt, monkeypatch):
    w = welt
    R = _m("ai.retention")
    monkeypatch.setenv("KI_BEWERTUNG_ROHDATEN_TAGE", "90")
    monkeypatch.setenv("KI_BEWERTUNG_TAGE", "730")
    assert R.rohdaten_tage() == 90 and R.aufbewahrung_tage() == 730
    s = w.s
    ids = {k: f"b140{k}_{s}" for k in ("frisch", "alt", "uralt", "lern", "lernv", "lauf")}

    def _bew(k, tage, **extra):
        return {"id": ids[k], "art": "abholung", "dealer_id": w.dealer_id, "protocol_id": f"p140{k}_{s}",
                "input_hash": f"h{k}", "status": "ok", "created_at": _vor_tagen(tage), "kosten_ct": 1.0,
                "eingabe": {"driver_notes": "geheim"}, "roh": {"x": 1},
                "recherche": {"status": "ok", "suchen": 1, "text": "T", "quellen": [{"url": "u"}]},
                "ergebnis": {"combined": {"fair_discount_eur": 100}}, **extra}

    async def lauf():
        await w.db.ki_bewertungen.insert_many([
            _bew("frisch", 10), _bew("alt", 100), _bew("uralt", 800), _bew("lern", 800),
            _bew("lernv", 800, art="vertrag", protocol_id=None),
            # Recherche fehlt (Sparmodus / abgebrochen): darf die Bereinigung nicht kippen
            {**_bew("lauf", 100), "recherche": None}])
        await w.db.ki_lernfaelle.insert_many([
            {"art": "abholung", "dealer_id": w.dealer_id, "protocol_id": f"p140lern_{s}", "input_hash": "hlern"},
            {"art": "vertrag", "dealer_id": w.dealer_id, "contract_id": "c", "ki_bewertung_id": ids["lernv"]}])
        erg = await R.ki_bewertungen_retention(w.db, datetime.now(timezone.utc))
        docs = {d["id"]: d async for d in w.db.ki_bewertungen.find({"dealer_id": w.dealer_id}, {"_id": 0})}
        return erg, docs
    try:
        erg, docs = w.run(lauf())
        # Rohdaten: alt, uralt (vor der Loeschung), lern, lernv, lauf = 5
        assert erg["geloescht"] == 1 and erg["behalten_lernfall"] == 2 and erg["rohdaten_entfernt"] == 5
        assert ids["uralt"] not in docs, "aelter als KI_BEWERTUNG_TAGE ohne Lernfall: geloescht"
        assert ids["frisch"] in docs and docs[ids["frisch"]]["eingabe"] == {"driver_notes": "geheim"}
        for k in ("alt", "lern", "lernv", "lauf"):
            d = docs[ids[k]]
            assert d["eingabe"] is None and d["roh"] is None and d["rohdaten_entfernt_am"], k
            assert d["ergebnis"]["combined"]["fair_discount_eur"] == 100 and d["kosten_ct"] == 1.0, k
            assert d["input_hash"] == f"h{k}" and d["status"] == "ok", k
        assert docs[ids["alt"]]["recherche"] == {"status": "ok", "suchen": 1, "text": None, "quellen": None,
                                                  "verworfen": None}
        assert docs[ids["lauf"]]["recherche"] is None
        # zweiter Lauf: nichts mehr zu tun (idempotent)
        erg2 = w.run(R.ki_bewertungen_retention(w.db, datetime.now(timezone.utc)))
        assert erg2["rohdaten_entfernt"] == 0 and erg2["geloescht"] == 0
    finally:
        w.run(w.db.ki_bewertungen.delete_many({"dealer_id": w.dealer_id}))
        w.run(w.db.ki_lernfaelle.delete_many({"dealer_id": w.dealer_id}))
    # der Schritt haengt im stuendlichen Aufraeumlauf, die Variablen im Container
    CS = _m("cleanup_service")
    src = inspect.getsource(CS._cleanup_once)
    assert 'await s("ki_bewertungen_retention"' in src and 'await s("ki_budget_rotieren"' in src
    compose = (BACKEND.parent / "docker-compose.yml").read_text(encoding="utf-8")
    assert "KI_BEWERTUNG_ROHDATEN_TAGE=${KI_BEWERTUNG_ROHDATEN_TAGE:-90}" in compose
    assert "KI_BEWERTUNG_TAGE=${KI_BEWERTUNG_TAGE:-730}" in compose


def test_147_lernfall_ohne_klartext():
    R = _m("ai.retention")
    roh = [{"id": "n1", "type": "delle", "zone": "Tür", "note": "laut Vera 0170", "severity_data": {"g": "2 cm"}},
           {"id": "dev:vin", "field": "vin", "label": "FIN", "expected": "WBA123", "actual": "WBA124"},
           {"id": "dev:tires", "field": "tire_profile", "expected": "ok", "actual": "abgefahren",
            "manual_hint": True},
           "kaputt", None]
    erg = R.lernfall_ohne_klartext(roh)
    assert erg == [{"id": "n1", "type": "delle", "zone": "Tür", "severity_data": {"g": "2 cm"}},
                   {"id": "dev:vin", "field": "vin", "label": "FIN"},
                   {"id": "dev:tires", "field": "tire_profile", "expected": "ok", "actual": "abgefahren"}]
    assert R.lernfall_ohne_klartext(None) == []
    # beide Lernfall-Schreiber nutzen den Filter
    for datei in ("ai/pickup_assessment.py", "ai/damage_pricing.py"):
        assert "retention.lernfall_ohne_klartext(" in (BACKEND / datei).read_text(encoding="utf-8"), datei
    # ki_reparaturpreise: systemweit, ohne Firmen-/Nutzerkennung, ohne Freitext (Nr. 145)
    src = inspect.getsource(_m("ai.marktdaten").lernen_aus_recherche)
    block = src[src.index("docs.append({"):src.index("insert_many")]
    assert "dealer_id" not in block and "user_id" not in block and "note" not in block


# ============================================================ 141-145
def test_141_firmenloeschung_und_vorschau_kennen_die_ki_sammlungen(welt, monkeypatch):
    w = welt
    ADM = _m("routes.admin")
    B = _m("ai.budget")
    monkeypatch.setattr(ADM, "db", w.db)
    assert "ki_bewertungen" in ADM._COMPANY_COLLECTIONS and "ki_lernfaelle" in ADM._COMPANY_COLLECTIONS
    assert "ki_reparaturpreise" not in ADM._COMPANY_COLLECTIONS, "systemweit, nicht je Firma"
    assert "ki_budget" not in ADM._COMPANY_COLLECTIONS, "kein dealer_id-Feld — eigener Weg"
    s = w.s
    fremd = f"d_fremd_{s}"

    async def lauf():
        await w.db.ki_bewertungen.insert_one({"id": f"b141_{s}", "dealer_id": w.dealer_id, "created_at": _jetzt()})
        await w.db.ki_lernfaelle.insert_one({"art": "abholung", "dealer_id": w.dealer_id})
        await w.db.ki_budget.insert_many([
            {"_id": f"abholung:{w.dealer_id}:2026-09", "ct": 5.0},
            {"_id": f"vertrag:{w.chef['id']}:2026-09", "ct": 2.0},
            {"_id": f"abholung:{fremd}:2026-09", "ct": 1.0},
            # Praefix-Falle: "d_glab_<s>x" ist eine ANDERE Firma
            {"_id": f"abholung:{w.dealer_id}x:2026-09", "ct": 1.0}])
        vorschau = await ADM.admin_delete_preview(w.dealer_id, admin={"id": "sa", "is_super_admin": True})
        n_zaehlen = await B.zaehler_zaehlen(w.dealer_id, [w.chef["id"]], db=w.db)
        n = await B.zaehler_loeschen(w.dealer_id, [w.chef["id"]], db=w.db)
        rest = sorted([d["_id"] async for d in w.db.ki_budget.find({"_id": {"$regex": s}})])
        return vorschau, n_zaehlen, n, rest
    try:
        vorschau, n_zaehlen, n, rest = w.run(lauf())
        z = vorschau["wuerde_loeschen"]
        assert z["ki_bewertungen"] == 1 and z["ki_lernfaelle"] == 1 and z["ki_budget"] == 2
        assert n_zaehlen == 2 and n == 2
        assert rest == [f"abholung:{fremd}:2026-09", f"abholung:{w.dealer_id}x:2026-09"]
        assert B.schluessel_filter(None, []) is None and B.schluessel_filter("", ["", None]) is None
        assert w.run(B.zaehler_loeschen(None, [], db=w.db)) == 0
    finally:
        w.run(w.db.ki_bewertungen.delete_many({"dealer_id": w.dealer_id}))
        w.run(w.db.ki_lernfaelle.delete_many({"dealer_id": w.dealer_id}))
        w.run(w.db.ki_budget.delete_many({"_id": {"$regex": s}}))
    # Quelltext: die Firmenloeschung und der Grabstein-Nachlauf loeschen die Zaehler
    src = inspect.getsource(ADM.admin_delete_user)
    assert "ki_budget.zaehler_loeschen(dealer_id, konten, db=db)" in src
    assert src.index("for coll in _COMPANY_COLLECTIONS") < src.index("ki_budget.zaehler_loeschen(dealer_id")
    assert "ki_budget.zaehler_loeschen(grab[\"id\"], db=db)" in inspect.getsource(
        _m("cleanup_service").firmengrabsteine_bereinigen)


def test_144_ki_budget_rotation_nach_drei_monaten(welt):
    w = welt
    B = _m("ai.budget")
    s = w.s
    d = f"d144_{s}"
    now = datetime(2026, 9, 26, 12, 0, tzinfo=timezone.utc)

    async def lauf():
        await w.db.ki_budget.insert_many([
            {"_id": f"abholung:{d}:2026-05", "ct": 1.0}, {"_id": f"abholung:{d}:2026-06", "ct": 1.0},
            {"_id": f"vertrag:{d}:2026-07", "ct": 1.0}, {"_id": f"abholung:{d}:2026-09", "ct": 1.0},
            {"_id": f"abholung:{d}:2025-12", "ct": 1.0}, {"_id": f"abholung:{d}:kaputt", "ct": 1.0}])
        n = await B.alte_zaehler_loeschen(w.db, now)
        rest = sorted([z["_id"] async for z in w.db.ki_budget.find({"_id": {"$regex": d}})])
        return n, rest
    try:
        n, rest = w.run(lauf())
        # Ende September: Monate vor Juni (Mai, Dezember) fliegen raus, Juni bleibt
        assert n >= 2
        assert rest == [f"abholung:{d}:2026-06", f"abholung:{d}:2026-09", f"abholung:{d}:kaputt", f"vertrag:{d}:2026-07"]
        # Jahreswechsel: Anfang Januar bleiben Oktober bis Januar
        n2 = w.run(B.alte_zaehler_loeschen(w.db, datetime(2027, 1, 5, tzinfo=timezone.utc)))
        rest2 = sorted(w.run(w.db.ki_budget.distinct("_id", {"_id": {"$regex": d}})))
        # (>=: die gemeinsame Test-DB traegt Zaehler anderer Tests aus dem laufenden Monat)
        assert n2 >= 3 and rest2 == [f"abholung:{d}:kaputt"]
    finally:
        w.run(w.db.ki_budget.delete_many({"_id": {"$regex": d}}))


# ============================================================ 143
def test_143_sucher_loeschung_pseudonymisiert_ki_spuren(welt):
    w = welt
    R = _m("ai.retention")
    ADM = _m("routes.admin")
    s = w.s
    uid, andere = f"su143_{s}", f"su143b_{s}"
    pseudonym = ADM._nutzer_pseudonym(uid)

    async def lauf():
        await w.db.ki_bewertungen.insert_many([
            {"id": f"b143a_{s}", "art": "vertrag", "dealer_id": w.dealer_id, "user_id": uid, "created_at": _jetzt()},
            {"id": f"b143b_{s}", "art": "vertrag", "dealer_id": w.dealer_id, "user_id": andere, "created_at": _jetzt()}])
        await w.db.ki_lernfaelle.insert_one({"art": "vertrag", "dealer_id": w.dealer_id, "user_id": uid,
                                             "contract_id": f"c143_{s}"})
        n = await R.nutzer_pseudonymisieren(w.db, uid, pseudonym)
        return (n, await w.db.ki_bewertungen.count_documents({"user_id": uid}),
                await w.db.ki_bewertungen.count_documents({"user_id": pseudonym}),
                await w.db.ki_bewertungen.count_documents({"user_id": andere}),
                await w.db.ki_lernfaelle.count_documents({"user_id": pseudonym}))
    try:
        n, offen, pseudo, fremd, lern = w.run(lauf())
        assert n == 2 and offen == 0 and pseudo == 1 and fremd == 1 and lern == 1
        assert pseudonym.startswith("geloescht:") and uid not in pseudonym
        assert w.run(R.nutzer_pseudonymisieren(w.db, "", "x")) == 0
    finally:
        w.run(w.db.ki_bewertungen.delete_many({"dealer_id": w.dealer_id}))
        w.run(w.db.ki_lernfaelle.delete_many({"dealer_id": w.dealer_id}))
    # Quelltext: im Sucher-Loeschpfad neben zugang_grants, mit demselben Pseudonym
    src = inspect.getsource(ADM.admin_delete_user)
    i_grant = src.index("await db.zugang_grants.update_many(")
    i_ki = src.index("ki_retention.nutzer_pseudonymisieren(db, user_id, pseudonym)")
    assert i_grant < i_ki < src.index("await db.users.delete_one({\"id\": user_id})")
    assert "ki_budget.zaehler_loeschen(None, [user_id], db=db)" in src


# ============================================================ 146
def test_146_ki_indizes_einzeln_mit_dublette(welt):
    w = welt
    IX = _m("indizes")
    src = inspect.getsource(IX.ki_indizes)
    # Quelltext: kein gemeinsamer try-Block um alle Anlagen, Unique ueber unique_anlegen
    assert src.count("try:") >= 3 and "unique_anlegen(db.ki_bewertungen" in src
    assert not re.search(r"try:\s*\n\s*await db\.ki_bewertungen\.create_index\([^)]*unique=True", src)
    s = w.s
    name = "ki_bewertung_je_protokoll_stand"

    async def lauf():
        try:
            await w.db.ki_bewertungen.drop_index(name)
        except Exception:  # noqa: BLE001 — Index gab es (noch) nicht
            pass
        await w.db.ki_bewertungen.insert_many([
            {"id": f"b146a_{s}", "dealer_id": w.dealer_id, "protocol_id": f"p146_{s}", "input_hash": "h", "created_at": _jetzt()},
            {"id": f"b146b_{s}", "dealer_id": w.dealer_id, "protocol_id": f"p146_{s}", "input_hash": "h", "created_at": _jetzt()}])
        erg = await IX.ki_indizes(w.db)
        namen = set((await w.db.ki_bewertungen.index_information()).keys())
        namen_lern = set((await w.db.ki_lernfaelle.index_information()).keys())
        return erg, namen, namen_lern
    try:
        erg, namen, namen_lern = w.run(lauf())
        assert erg["ok"] is False and erg["fehler"] == [f"ki_bewertungen.{name}"]
        assert name not in namen, "Dublette: dieser eine Index fehlt ..."
        for n in ("ki_vertrag_laeuft_je_stand", "ki_bewertung_firma_zeit", "ki_bewertung_nutzer_zeit",
                  "ki_bewertung_id", "ki_bewertung_zeit"):
            assert n in namen, f"... die uebrigen stehen trotzdem: {n}"
        assert {"ki_lernfall_art_marke", "ki_lernfall_protokoll_stand", "ki_lernfall_bewertung"} <= namen_lern
        assert f"ki_bewertungen.{name}" in IX.FEHLENDE_UNIQUE
    finally:
        w.run(w.db.ki_bewertungen.delete_many({"dealer_id": w.dealer_id}))
        # Dublette weg -> Index entsteht, Register wieder sauber
        erg2 = w.run(IX.ki_indizes(w.db))
        assert erg2["ok"] is True and erg2["fehler"] == []
        assert name in w.run(w.db.ki_bewertungen.index_information())
        assert f"ki_bewertungen.{name}" not in IX.FEHLENDE_UNIQUE
        w.run(w.db.betriebsalarme.delete_many({"ref": {"$regex": "^ki_"}}))
