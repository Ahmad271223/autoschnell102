# -*- coding: utf-8 -*-
"""Runde 33 (12.09.2026, Wunsch Ahmad): Freigaben, wenn mehrere Fahrer
gleichzeitig beim Verkaeufer auf den Chef warten — und Vorher/Nachher.

"was wenn mehrere Fahrer gerade Autos abholen und vom Chef die
Nachverhandlung brauchen — kann es crashen?" Die Analyse fand keinen Absturz,
aber echte Schwaechen, die hier festgehalten sind:
  * neueste zuerst, Grenze 50 vor dem Sucher-Filter, abgeschlossene Termine
    blieben ewig stehen
  * Chef und Sucher geben gleichzeitig frei: der Letzte gewann still
  * ein verhandelter Preis liess sich nicht mehr entfernen
  * im Abschluss pruefte der Claim den Preis nicht mit
  * "laut Vertrag" war der Inseratswert; ein alter Korrekturwert landete im PDF

In-Prozess gegen eine Wegwerf-DB; kein Server.
"""
import asyncio
import base64
import importlib
import json
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path

import pytest
from fastapi import HTTPException

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

MONGO_URL = "mongodb://127.0.0.1:27017"
_PNG_B64 = base64.b64encode(b"\x89PNG\r\n\x1a\n" + b"\x00" * 64).decode()


def _jetzt():
    return datetime.now(timezone.utc).isoformat()


def _modul(name):
    return importlib.import_module(name)


@pytest.fixture
def welt(monkeypatch):
    from motor.motor_asyncio import AsyncIOMotorClient
    s = uuid.uuid4().hex[:10]
    namen = ["deps", "routes.protocols", "routes.bestand", "routes.drivers",
             "routes.appointments", "kaufvorgang", "lifecycle", "auftraggeber"]
    mods = [_modul(n) for n in namen]
    alt = [(m, getattr(m, "db", None)) for m in mods]

    class _W:
        pass

    w = _W()
    w.s = s
    w.dealer_id = f"d_r33_{s}"
    w.chef = {"id": f"chef_r33_{s}", "dealer_id": w.dealer_id, "role": "dealer"}
    w.sucher = {"id": f"su_r33_{s}", "dealer_id": w.dealer_id, "role": "sucher"}
    w.driver = {"id": f"f_r33_{s}", "display_name": "Fahrer R33"}
    w.loop = asyncio.new_event_loop()
    asyncio.set_event_loop(w.loop)
    w.client = AsyncIOMotorClient(MONGO_URL, serverSelectionTimeoutMS=5000)
    w.db_name = f"autoschnell_r33_{s}"
    w.db = w.client[w.db_name]
    for m in mods:
        if hasattr(m, "db"):
            m.db = w.db
    w.run = lambda coro: w.loop.run_until_complete(coro)

    SS = _modul("storage_service")
    monkeypatch.setattr(SS, "make_key", lambda *a, **k: f"test/{uuid.uuid4().hex}.bin")

    async def _save(key, daten):
        return key
    monkeypatch.setattr(SS, "save_async", _save, raising=False)
    PDF = _modul("pickup_pdf_service")
    monkeypatch.setattr(PDF, "build_pickup_pdf", lambda **k: b"%PDF-1.4 test")

    w.run(w.db.users.insert_many([
        {**w.chef, "active": True, "first_name": "Serkan", "last_name": "Chef",
         "email": f"chef{s}@t.invalid", "created_at": _jetzt()},
        {**w.sucher, "active": True, "first_name": "Sam", "last_name": "Sucher",
         "email": f"su{s}@t.invalid", "created_at": _jetzt()}]))
    w.run(w.db.dealers.insert_one({"id": w.dealer_id, "user_id": w.chef["id"],
                                   "company_name": "Firma R33", "created_at": _jetzt()}))
    w.run(w.db.driver_accounts.insert_one({**w.driver, "active": True, "created_at": _jetzt()}))
    w.run(w.db.dealer_drivers.insert_one(
        {"id": str(uuid.uuid4()), "dealer_id": w.dealer_id, "driver_account_id": w.driver["id"],
         "display_name": w.driver["display_name"], "added_at": _jetzt()}))
    yield w
    try:
        w.run(w.client.drop_database(w.db_name))
    finally:
        for m, d in alt:
            if d is not None:
                m.db = d
        w.client.close()
        w.loop.close()


def _abholung(w, name, *, von=None, status="offen", vertrag_extra=None, fahrzeug_extra=None):
    """Termin + Fahrzeug + Vertrag + Kaufvorgang wie nach einem echten Vertrag."""
    von = von or w.chef
    aid, vid, cid, kid = (f"{x}_{name}_{w.s}" for x in ("t", "v", "c", "k"))
    fahrzeug = {"id": vid, "dealer_id": w.dealer_id, "lifecycle": "abholung_geplant",
                "owner_user_id": von["id"], "created_at": _jetzt(),
                "data": {"make_label": "Chevrolet", "model_label": name,
                         "first_registration": "01/2019", "mileage": 85000, **(fahrzeug_extra or {})}}
    vertrag = {"id": cid, "dealer_id": w.dealer_id, "user_id": von["id"], "vehicle_id": vid,
               "contract_no": f"KV-{name}", "created_at": _jetzt(),
               "contract_data": {"seller_name": "MTRADEX", "purchase_price": 45000,
                                 **(vertrag_extra or {})}}
    vorgang = {"id": kid, "dealer_id": w.dealer_id, "user_id": von["id"], "vehicle_id": vid,
               "contract_id": cid, "status": "abholung_geplant", "purchase_price": 45000,
               "appointment_id": aid, "created_at": _jetzt(), "updated_at": _jetzt()}
    termin = {"id": aid, "dealer_id": w.dealer_id, "driver_id": w.driver["id"], "vehicle_id": vid,
              "contract_id": cid, "kaufvorgang_id": kid, "status": status, "created_by": von["id"],
              "pickup_date": "2099-01-01", "pickup_time": "10:00", "seller_name": "MTRADEX",
              "pickup_address": "ul. Osiecka 15", "created_at": _jetzt()}

    async def anlegen():
        await w.db.vehicles.insert_one(fahrzeug)
        await w.db.generated_pdfs.insert_one(vertrag)
        await w.db.kaufvorgaenge.insert_one(vorgang)
        await w.db.appointments.insert_one(termin)
    w.run(anlegen())
    return aid


def _protokoll(w, aid, P, **extra):
    doc = {"id": f"p_{aid}", "appointment_id": aid, "dealer_id": w.dealer_id,
           "vehicle_id": aid.replace("t_", "v_", 1), "driver_account_id": w.driver["id"],
           "driver_name": w.driver["display_name"], "version": 1, "status": "entwurf",
           "superseded": False,
           "vehicle_check": {k: {"status": "stimmt"} for k, _l, _o in P.VEHICLE_CHECK_FIELDS},
           "condition": {"mileage": "85000"}, "keys_count": "2", "damages_confirmed": True,
           "place": "Warschau", "created_at": _jetzt(), "updated_at": _jetzt()}
    doc.update(extra)
    w.run(w.db.pickup_protocols.insert_one(doc))
    return doc["id"]


def _fin(P, preis=None):
    return P.FinalizeIn(signature_driver_b64=_PNG_B64, signature_seller_b64=_PNG_B64,
                        seller_name="MTRADEX", place="Warschau", neuer_preis_gesehen=preis)


def _status(exc):
    return getattr(exc, "status_code", None), getattr(exc, "detail", "")


# ------------------------------------------------------------ Liste & Zaehler
def test_01_aelteste_zuerst_ohne_geschlossene_und_geloeschte_termine(welt):
    w = welt
    P = _modul("routes.protocols")
    alt = _abholung(w, "alt")
    neu = _abholung(w, "neu")
    zu = _abholung(w, "storniert", status="storniert")
    _protokoll(w, alt, P, status=P.ZUR_FREIGABE, erstmals_abgeschickt_am="2026-09-12T08:00:00+00:00",
               abgeschickt_am="2026-09-12T11:00:00+00:00")
    _protokoll(w, neu, P, status=P.ZUR_FREIGABE, erstmals_abgeschickt_am="2026-09-12T09:00:00+00:00",
               abgeschickt_am="2026-09-12T09:00:00+00:00")
    _protokoll(w, zu, P, status=P.FREIGEGEBEN, erstmals_abgeschickt_am="2026-09-12T07:00:00+00:00")
    _protokoll(w, f"t_weg_{w.s}", P, status=P.ZUR_FREIGABE)   # Termin geloescht

    liste = w.run(P.protokolle_zur_freigabe(w.chef))
    assert [e["appointment_id"] for e in liste] == [alt, neu], \
        "am laengsten wartend zuerst — auch wenn es zuletzt erneut abgeschickt wurde"
    anzahl = w.run(P.protokolle_zur_freigabe_anzahl(w.chef))
    assert (anzahl["wartet"], anzahl["freigegeben"]) == (2, 0)
    assert anzahl["ids"] == [f"p_{alt}", f"p_{neu}"]


def test_02_sucher_sieht_nur_eigene_auch_ueber_fuenfzig(welt):
    """Vorher: Grenze 50 VOR dem Sucher-Filter — ein Sucher verlor seine eigenen."""
    w = welt
    P = _modul("routes.protocols")
    for i in range(55):
        _protokoll(w, _abholung(w, f"chef{i}"), P, status=P.ZUR_FREIGABE,
                   erstmals_abgeschickt_am=f"2026-09-12T08:{i:02d}:00+00:00")
    eigene = _abholung(w, "eigen", von=w.sucher)
    _protokoll(w, eigene, P, status=P.ZUR_FREIGABE, erstmals_abgeschickt_am="2026-09-12T10:00:00+00:00")

    sucher = w.run(P.protokolle_zur_freigabe(w.sucher))
    assert [e["appointment_id"] for e in sucher] == [eigene]
    anz = w.run(P.protokolle_zur_freigabe_anzahl(w.sucher))
    assert (anz["wartet"], anz["freigegeben"], anz["ids"]) == (1, 0, [f"p_{eigene}"])
    assert len(w.run(P.protokolle_zur_freigabe(w.chef))) == 56


def test_03_vergleich_vertrag_vor_ort(welt):
    """Wunsch: 'besseres vorher nachher' — und der Vorher-Wert kommt aus dem
    Vertrag, nicht aus dem Inserat."""
    w = welt
    P = _modul("routes.protocols")
    aid = _abholung(w, "vergleich", vertrag_extra={"vehicle_first_registration": "03/2019",
                                                   "previous_owners": "2", "accident_free": "Ja",
                                                   "commercial_since_ez": "Nein"})
    vc = {k: {"status": "stimmt"} for k, _l, _o in P.VEHICLE_CHECK_FIELDS}
    vc.update({"first_registration": {"status": "weicht ab", "value": "1.2020"},
               "previous_owners": {"status": "weicht ab", "value": "3"},
               "commercial": {"status": "Nein"}, "accident_free": {"status": "Nein"}})
    _protokoll(w, aid, P, status=P.ZUR_FREIGABE, vehicle_check=vc, condition={"mileage": "86000"},
               erstmals_abgeschickt_am=_jetzt())

    e = w.run(P.protokolle_zur_freigabe(w.chef))[0]
    zeilen = {z["schluessel"]: z for z in e["vergleich"]}
    assert (zeilen["first_registration"]["vertrag_text"], zeilen["first_registration"]["vor_ort_text"]) \
        == ("03/2019", "01/2020"), "Vertragswert statt Inseratswert (01/2019)"
    assert (zeilen["previous_owners"]["vertrag_text"], zeilen["previous_owners"]["vor_ort_text"]) == ("2", "3")
    assert zeilen["accident_free"]["abweichend"] is True
    assert zeilen["commercial"]["abweichend"] is False, "Gewerbliche Nutzung: Nein ist keine Abweichung"
    assert {a["schluessel"] for a in e["abweichungen"]} == {"first_registration", "previous_owners", "accident_free"}
    assert e["kilometerstand_text"] == "86.000 km"
    assert e["stand"] and e["erstmals_abgeschickt_am"]


def test_04_get_protocol_liefert_vertragswerte_und_eingabearten(welt):
    w = welt
    P = _modul("routes.protocols")
    aid = _abholung(w, "vorlage", vertrag_extra={"vehicle_first_registration": "03/2019",
                                                 "hu_valid": "Ja", "hu_until": "06/26"})
    daten = w.run(P.get_protocol(aid, w.driver))
    werte = daten["template"]["vehicle_check_values"]
    assert werte["first_registration"] == "03/2019" and werte["hu"] == "06/2026"
    assert daten["template"]["vehicle_check_art"]["first_registration"] == "monat_jahr"
    assert daten["template"]["vehicle_check_art"]["hu"] == "hu"


# ------------------------------------------------------------ Gleichzeitig
def test_05_zweite_freigabe_mit_altem_stand_wird_abgelehnt(welt):
    """Chef und Sucher geben gleichzeitig mit verschiedenen Preisen frei: der
    Zweite bekommt 409 mit Name und Preis — statt still zu ueberschreiben."""
    w = welt
    P = _modul("routes.protocols")
    aid = _abholung(w, "gleichzeitig", von=w.sucher)
    _protokoll(w, aid, P, status=P.ZUR_FREIGABE, erstmals_abgeschickt_am=_jetzt())
    stand = w.run(P.protokolle_zur_freigabe(w.chef))[0]["stand"]
    pid = f"p_{aid}"

    w.run(P.protokoll_freigeben(pid, P.FreigabeIn(neuer_preis=42000, stand=stand), w.chef))
    with pytest.raises(HTTPException) as fehler:
        w.run(P.protokoll_freigeben(pid, P.FreigabeIn(neuer_preis=40000, stand=stand), w.sucher))
    code, text = _status(fehler.value)
    assert code == 409 and "Serkan Chef" in text and "42.000,00 €" in text, text
    doc = w.run(w.db.pickup_protocols.find_one({"id": pid}))
    assert doc["neuer_preis"] == 42000


def test_06_ohne_stand_bleibt_der_bisherige_weg(welt):
    """Aeltere Oberflaeche im Rollout schickt keinen Stand — das muss gehen."""
    w = welt
    P = _modul("routes.protocols")
    aid = _abholung(w, "ohnestand")
    _protokoll(w, aid, P, status=P.ZUR_FREIGABE)
    r = w.run(P.protokoll_freigeben(f"p_{aid}", P.FreigabeIn(neuer_preis=41000), w.chef))
    assert r["status"] == P.FREIGEGEBEN and r["neuer_preis"] == 41000


def test_07_preis_zuruecksetzen_und_abschluss_zum_vertragspreis(welt):
    w = welt
    P = _modul("routes.protocols")
    aid = _abholung(w, "zuruecksetzen")
    _protokoll(w, aid, P, status=P.ZUR_FREIGABE)
    pid = f"p_{aid}"
    w.run(P.protokoll_freigeben(pid, P.FreigabeIn(neuer_preis=41000, notiz="Kratzer"), w.chef))
    stand = w.run(P.protokolle_zur_freigabe(w.chef))[0]["stand"]
    r = w.run(P.protokoll_freigeben(pid, P.FreigabeIn(preis_zuruecksetzen=True, stand=stand), w.chef))
    assert r["neuer_preis"] is None
    doc = w.run(w.db.pickup_protocols.find_one({"id": pid}, {"_id": 0}))
    assert "neuer_preis" not in doc and "preis_notiz" not in doc and doc["status"] == P.FREIGEGEBEN

    fertig = w.run(P.finalize_protocol(aid, _fin(P, None), w.driver))
    assert fertig["ok"] is True
    kv = w.run(w.db.kaufvorgaenge.find_one({"appointment_id": aid}, {"_id": 0}))
    assert kv["purchase_price"] == 45000


def test_08_freigabe_waehrend_der_unterschrift(welt):
    w = welt
    P = _modul("routes.protocols")
    aid = _abholung(w, "unterschrift")
    _protokoll(w, aid, P, status="wird_abgeschlossen")
    with pytest.raises(HTTPException) as fehler:
        w.run(P.protokoll_freigeben(f"p_{aid}", P.FreigabeIn(neuer_preis=1), w.chef))
    code, text = _status(fehler.value)
    assert code == 409 and "unterschrieben" in text, text


def test_09_geschlossener_termin_nimmt_keine_freigabe(welt):
    w = welt
    P = _modul("routes.protocols")
    aid = _abholung(w, "zu", status="storniert")
    _protokoll(w, aid, P, status=P.ZUR_FREIGABE)
    with pytest.raises(HTTPException) as fehler:
        w.run(P.protokoll_freigeben(f"p_{aid}", P.FreigabeIn(), w.chef))
    assert _status(fehler.value)[0] == 409


def test_10_abschluss_claim_prueft_den_preis(welt, monkeypatch):
    """Aendert der Chef den Preis genau zwischen Preispruefung und Claim, darf
    nicht mit dem alten Preis unterschrieben werden."""
    from pymongo import MongoClient
    w = welt
    P = _modul("routes.protocols")
    aid = _abholung(w, "claim")
    _protokoll(w, aid, P, status=P.FREIGEGEBEN, neuer_preis=42000.0)
    pid = f"p_{aid}"
    echt = P._pflichtfelder_pruefen
    sync = MongoClient(MONGO_URL, serverSelectionTimeoutMS=5000)

    def dazwischen(*a, **k):
        echt(*a, **k)
        sync[w.db_name].pickup_protocols.update_one({"id": pid}, {"$set": {"neuer_preis": 39000.0}})

    monkeypatch.setattr(P, "_pflichtfelder_pruefen", dazwischen)
    try:
        with pytest.raises(HTTPException) as fehler:
            w.run(P.finalize_protocol(aid, _fin(P, 42000), w.driver))
    finally:
        sync.close()
    code, text = _status(fehler.value)
    assert code == 409 and "Preis" in text, text
    doc = w.run(w.db.pickup_protocols.find_one({"id": pid}, {"_id": 0}))
    assert doc["status"] == P.FREIGEGEBEN and doc["neuer_preis"] == 39000.0


def test_11_erneutes_abschicken_behaelt_den_platz_in_der_liste(welt):
    w = welt
    P = _modul("routes.protocols")
    aid = _abholung(w, "erneut")
    _protokoll(w, aid, P)
    w.run(P.submit_protocol(aid, w.driver))
    erstes = w.run(w.db.pickup_protocols.find_one({"id": f"p_{aid}"}))["erstmals_abgeschickt_am"]
    w.run(P.protokoll_freigeben(f"p_{aid}", P.FreigabeIn(zurueck=True, notiz="Foto fehlt"), w.chef))
    w.run(P.submit_protocol(aid, w.driver))
    doc = w.run(w.db.pickup_protocols.find_one({"id": f"p_{aid}"}))
    assert doc["erstmals_abgeschickt_am"] == erstes and doc["status"] == P.ZUR_FREIGABE


def test_12_zurueckgeschickt_nennt_wer_es_war(welt):
    w = welt
    P = _modul("routes.protocols")
    aid = _abholung(w, "rueck")
    _protokoll(w, aid, P, status=P.ZUR_FREIGABE)
    stand = w.run(P.protokolle_zur_freigabe(w.chef))[0]["stand"]
    w.run(P.protokoll_freigeben(f"p_{aid}", P.FreigabeIn(zurueck=True, stand=stand), w.chef))
    with pytest.raises(HTTPException) as fehler:
        w.run(P.protokoll_freigeben(f"p_{aid}", P.FreigabeIn(neuer_preis=1, stand=stand), w.chef))
    code, text = _status(fehler.value)
    assert code == 409, text   # nicht mehr zur Freigabe -> "noch nicht abgeschickt"


# ------------------------------------------------------------ PDF
def test_13_pdf_druckt_bei_stimmt_keinen_alten_korrekturwert():
    pypdf = pytest.importorskip("pypdf")
    import io
    PDF = _modul("pickup_pdf_service")
    P = _modul("routes.protocols")
    vc = {k: {"status": "stimmt"} for k, _l, _o in P.VEHICLE_CHECK_FIELDS}
    vc["first_registration"] = {"status": "stimmt", "value": "07/2011"}     # alter Wert
    vc["color"] = {"status": "weicht ab", "value": "Anthrazit"}
    pdf = PDF.build_pickup_pdf(
        appointment={"id": "t1", "pickup_date": "2099-01-01"},
        vehicle={"make_label": "Chevrolet", "model_label": "Camaro", "first_registration": "01/2019",
                 "exterior_color": "Rot"},
        contract={"vehicle_first_registration": "03/2019", "purchase_price": 45000},
        filled={"vehicle_check": vc, "condition": {"mileage": "85000"}})
    text = "".join(seite.extract_text() or "" for seite in pypdf.PdfReader(io.BytesIO(pdf)).pages)
    assert "03/2019" in text, "Vertragswert der Erstzulassung"
    assert "07/2011" not in text, "zurueckgenommener Korrekturwert gehoert nicht ins PDF"
    assert "Anthrazit" in text


# ============================================================ Gegenpruefung 12.09.2026
def _fin_stand(P, preis, stand):
    return P.FinalizeIn(signature_driver_b64=_PNG_B64, signature_seller_b64=_PNG_B64,
                        seller_name="MTRADEX", place="Warschau", neuer_preis_gesehen=preis,
                        freigabe_stand_gesehen=stand)


def test_14_vermerk_geaendert_nach_freigabe_stoppt_den_abschluss(welt):
    """Befund: Nur der Preis war abgesichert — ein spaeter geaenderter Vermerk
    stand ueber Unterschriften, die ihn nie gesehen hatten."""
    w = welt
    P = _modul("routes.protocols")
    aid = _abholung(w, "vermerk")
    _protokoll(w, aid, P, status=P.ZUR_FREIGABE)
    pid = f"p_{aid}"
    w.run(P.protokoll_freigeben(pid, P.FreigabeIn(neuer_preis=42000, notiz="Rost am Schweller"), w.chef))
    gesehen = w.run(P.get_protocol(aid, w.driver))["protocol"]["freigabe_stand"]
    stand = w.run(P.protokolle_zur_freigabe(w.chef))[0]["stand"]
    assert stand == gesehen

    # Der Chef aendert NUR den Vermerk, der Preis bleibt.
    w.run(P.protokoll_freigeben(pid, P.FreigabeIn(notiz="Verzicht auf Gewährleistung", stand=stand), w.chef))
    with pytest.raises(HTTPException) as fehler:
        w.run(P.finalize_protocol(aid, _fin_stand(P, 42000, gesehen), w.driver))
    code, text = _status(fehler.value)
    assert code == 409 and "Vermerk" in text, text
    assert w.run(w.db.pickup_protocols.find_one({"id": pid}))["status"] == P.FREIGEGEBEN

    neu = w.run(P.get_protocol(aid, w.driver))["protocol"]["freigabe_stand"]
    assert w.run(P.finalize_protocol(aid, _fin_stand(P, 42000, neu), w.driver))["ok"] is True


def test_15_claim_prueft_auch_den_vermerk(welt, monkeypatch):
    """Aendert der Chef den Vermerk genau zwischen Pruefung und Claim, darf
    auch eine aeltere App (ohne Stand) nicht abschliessen."""
    from pymongo import MongoClient
    w = welt
    P = _modul("routes.protocols")
    aid = _abholung(w, "claimvermerk")
    _protokoll(w, aid, P, status=P.FREIGEGEBEN, neuer_preis=42000.0, preis_notiz="A",
               freigabe_stand="2026-09-12T10:00:00+00:00")
    pid = f"p_{aid}"
    echt = P._pflichtfelder_pruefen
    sync = MongoClient(MONGO_URL, serverSelectionTimeoutMS=5000)

    def dazwischen(*a, **k):
        echt(*a, **k)
        sync[w.db_name].pickup_protocols.update_one(
            {"id": pid}, {"$set": {"preis_notiz": "B", "freigabe_stand": "2026-09-12T10:05:00+00:00"}})

    monkeypatch.setattr(P, "_pflichtfelder_pruefen", dazwischen)
    try:
        with pytest.raises(HTTPException) as fehler:
            w.run(P.finalize_protocol(aid, _fin(P, 42000), w.driver))
    finally:
        sync.close()
    code, text = _status(fehler.value)
    assert code == 409 and "Vermerk" in text, text
    doc = w.run(w.db.pickup_protocols.find_one({"id": pid}, {"_id": 0}))
    assert doc["status"] == P.FREIGEGEBEN and doc["preis_notiz"] == "B"


def test_16_unendliche_zahlen_abgelehnt_und_liste_bleibt_heil(welt):
    """Befund: condition.mileage = Infinity liess GET /protocols/zur-freigabe
    mit 500 scheitern — keine Freigabe der Firma war mehr moeglich."""
    from pydantic import ValidationError
    w = welt
    P = _modul("routes.protocols")
    with pytest.raises(ValidationError):
        P.ProtocolIn(condition={"mileage": float("inf")})
    with pytest.raises(ValidationError):
        P.ProtocolIn(vehicle_check={"power": {"status": "weicht ab", "value": float("nan")}})

    kaputt = _abholung(w, "kaputt")
    gut = _abholung(w, "gut")
    _protokoll(w, kaputt, P, status=P.ZUR_FREIGABE, condition={"mileage": float("inf")},
               erstmals_abgeschickt_am="2026-09-12T08:00:00+00:00")      # Altdaten direkt in der DB
    _protokoll(w, gut, P, status=P.ZUR_FREIGABE, erstmals_abgeschickt_am="2026-09-12T09:00:00+00:00")
    liste = w.run(P.protokolle_zur_freigabe(w.chef))
    assert [e["appointment_id"] for e in liste] == [kaputt, gut]
    json.dumps(liste, allow_nan=False)
    json.dumps(w.run(P.get_protocol(kaputt, w.driver))["protocol"], allow_nan=False)


def test_17_ein_fehler_kostet_nur_die_eigene_karte(welt, monkeypatch):
    w = welt
    P = _modul("routes.protocols")
    a = _abholung(w, "fehler")
    b = _abholung(w, "heil")
    _protokoll(w, a, P, status=P.ZUR_FREIGABE, condition={"mileage": "1"},
               erstmals_abgeschickt_am="2026-09-12T08:00:00+00:00")
    _protokoll(w, b, P, status=P.ZUR_FREIGABE, erstmals_abgeschickt_am="2026-09-12T09:00:00+00:00")
    echt = P.PV.vergleich

    def vergleich(felder, vc, zustand, werte):
        if (zustand or {}).get("mileage") == "1":
            raise RuntimeError("kaputt")
        return echt(felder, vc, zustand, werte)

    monkeypatch.setattr(P.PV, "vergleich", vergleich)
    liste = w.run(P.protokolle_zur_freigabe(w.chef))
    assert [e["appointment_id"] for e in liste] == [a, b]
    assert liste[0]["ladefehler"] and liste[0]["stand"]
    assert "ladefehler" not in liste[1] and len(liste[1]["vergleich"]) == 12


def test_18_abgelaufener_claim_kommt_zurueck(welt):
    """Befund: Starb ein Abschluss mittendrin, verschwand das Protokoll fuer
    immer aus Liste und Zaehler; Chef und Fahrer kamen nicht weiter."""
    w = welt
    P = _modul("routes.protocols")
    aid = _abholung(w, "absturz")
    _protokoll(w, aid, P, status="wird_abgeschlossen", neuer_preis=42000.0,
               claim_bis="2000-01-01T00:00:00+00:00", erstmals_abgeschickt_am=_jetzt())
    liste = w.run(P.protokolle_zur_freigabe(w.chef))
    assert [e["status"] for e in liste] == [P.FREIGEGEBEN]
    r = w.run(P.protokoll_freigeben(f"p_{aid}", P.FreigabeIn(neuer_preis=41000, stand=liste[0]["stand"]),
                                    w.chef))
    assert r["neuer_preis"] == 41000

    aid2 = _abholung(w, "absturz2")
    _protokoll(w, aid2, P, status="wird_abgeschlossen", claim_bis="2000-01-01T00:00:00+00:00")
    assert w.run(P.finalize_protocol(aid2, _fin(P, None), w.driver))["ok"] is True


def test_19_korrektur_wartet_neu(welt):
    """Befund: Die Korrektur erbte erstmals_abgeschickt_am und stand in der
    Liste als 'wartet seit 50 Std.' ganz oben."""
    w = welt
    P = _modul("routes.protocols")
    aid = _abholung(w, "korrektur")
    _protokoll(w, aid, P, status="final", erstmals_abgeschickt_am="2026-09-10T08:00:00+00:00",
               abgeschickt_am="2026-09-10T08:00:00+00:00", freigegeben_von=w.chef["id"],
               freigabe_stand="2026-09-10T08:30:00+00:00", neuer_preis=42000.0)
    neu = w.run(P.start_correction(aid, w.driver))
    for feld in ("erstmals_abgeschickt_am", "abgeschickt_am", "freigegeben_von", "freigabe_stand"):
        assert feld not in neu, feld
    assert neu["neuer_preis"] == 42000.0, "der verhandelte Preis bleibt"


def test_20_gescheiterter_abschluss_stoert_die_standpruefung_nicht(welt):
    """Befund: Die Stand-Pruefung lief gegen updated_at — Claim und Rollback
    eines gescheiterten Abschlusses liessen den naechsten Klick des Chefs mit
    einer Meldung ueber eine 'fremde' Freigabe scheitern."""
    w = welt
    P = _modul("routes.protocols")
    aid = _abholung(w, "rollback")
    _protokoll(w, aid, P, status=P.ZUR_FREIGABE)
    pid = f"p_{aid}"
    stand = w.run(P.protokolle_zur_freigabe(w.chef))[0]["stand"]
    r = w.run(P.protokoll_freigeben(pid, P.FreigabeIn(neuer_preis=42000, stand=stand), w.chef))

    kaputt = P.FinalizeIn(signature_driver_b64="kein-bild-" * 3, signature_seller_b64=_PNG_B64,
                          seller_name="MTRADEX", place="Warschau", neuer_preis_gesehen=42000)
    with pytest.raises(Exception):
        w.run(P.finalize_protocol(aid, kaputt, w.driver))
    assert w.run(w.db.pickup_protocols.find_one({"id": pid}))["status"] == P.FREIGEGEBEN

    r2 = w.run(P.protokoll_freigeben(pid, P.FreigabeIn(neuer_preis=41000, stand=r["stand"]), w.chef))
    assert r2["neuer_preis"] == 41000
    with pytest.raises(HTTPException) as fehler:
        w.run(P.protokoll_freigeben(pid, P.FreigabeIn(neuer_preis=40000, stand=r["stand"]), w.chef))
    code, text = _status(fehler.value)
    assert code == 409 and "Du hast" in text and "jemand anderes" not in text, text


def test_21_zuruecksetzen_nennt_wer_zurueckgesetzt_hat(welt):
    w = welt
    P = _modul("routes.protocols")
    aid = _abholung(w, "wer", von=w.sucher)
    _protokoll(w, aid, P, status=P.ZUR_FREIGABE)
    pid = f"p_{aid}"
    w.run(P.protokoll_freigeben(pid, P.FreigabeIn(neuer_preis=20000), w.sucher))
    w.run(P.protokoll_freigeben(pid, P.FreigabeIn(preis_zuruecksetzen=True), w.chef))
    e = w.run(P.protokolle_zur_freigabe(w.chef))[0]
    assert e["freigegeben_von_name"] == "Serkan Chef" and e["neuer_preis"] is None


def test_22_altlasten_verdraengen_keine_wartenden(welt, monkeypatch):
    """Befund: Grenze ohne Sortierung vor dem Termin-Filter — liegen
    gebliebene Protokolle verdraengten die Fahrer, die jetzt warten."""
    w = welt
    P = _modul("routes.protocols")
    monkeypatch.setattr(P, "_FREIGABE_MAX", 2)
    for i in range(3):
        zu = _abholung(w, f"leiche{i}", status="storniert")
        _protokoll(w, zu, P, status=P.ZUR_FREIGABE, abgeschickt_am=f"2026-09-01T08:0{i}:00+00:00")
    jetzt_wartend = _abholung(w, "wartet")
    _protokoll(w, jetzt_wartend, P, status=P.ZUR_FREIGABE, abgeschickt_am="2026-09-12T10:00:00+00:00")
    assert [e["appointment_id"] for e in w.run(P.protokolle_zur_freigabe(w.chef))] == [jetzt_wartend]
    assert w.run(P.protokolle_zur_freigabe_anzahl(w.chef))["wartet"] == 1


def test_23_pdf_kein_kilometerpfeil_bei_stimmt():
    """Befund: 'KM-Stand laut Vertrag: [X] stimmt' und daneben ein Pfeil zum
    Stand bei Abholung — das unterschriebene Dokument widersprach sich."""
    pypdf = pytest.importorskip("pypdf")
    import io
    PDF = _modul("pickup_pdf_service")
    P = _modul("routes.protocols")

    def text(vc):
        pdf = PDF.build_pickup_pdf(
            appointment={"id": "t1", "pickup_date": "2099-01-01"},
            vehicle={"make_label": "Chevrolet", "model_label": "Camaro", "mileage": 86000},
            contract={"vehicle_mileage": "86000", "purchase_price": 45000},
            filled={"vehicle_check": vc, "condition": {"mileage": "88000"}})
        return "".join(seite.extract_text() or "" for seite in pypdf.PdfReader(io.BytesIO(pdf)).pages)

    vc = {k: {"status": "stimmt"} for k, _l, _o in P.VEHICLE_CHECK_FIELDS}
    bestaetigt = text(vc)
    assert "86.000 km" in bestaetigt
    assert "88.000 km" not in bestaetigt, "bei 'stimmt' keine Korrektur neben dem Vertragswert"

    vc["mileage_contract"] = {"status": "weicht ab", "value": "72000"}
    assert "72.000 km" in text(vc), "bei 'weicht ab' bleibt der Pfeil"

