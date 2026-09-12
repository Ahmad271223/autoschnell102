# -*- coding: utf-8 -*-
"""Runde 27 (12.09.2026, Pruefbefunde "30 Sucher"): Rennen und stille Grenzen.

  * Erstvergleich-Rennen: Zwei Sucher vergleichen denselben NEUEN Link. Wer
    verliert, darf die gemeinsamen Inseratsdaten des Gewinners NICHT
    ueberschreiben — auch nicht, wenn der Gewinner schon weiter ist.
  * Vertragsanlage (Beschluss Ahmad 12.09.2026): Das System sperrt nichts
    von sich aus. Gesperrt bleibt nur verkauft/geloescht/archiviert, und das
    Speichern wird nicht im letzten Moment verworfen.
  * Ebenso: Ein abgeschlossener Kauf beendet NICHT die Vorgaenge der
    Kollegen — Aufraeumen entscheiden Sucher und Chef selbst.
  * Stille Grenzen: Fahrzeugakte (10 Vertraege/Termine) und Bestandsliste
    (500 Fahrzeuge) melden jetzt die Gesamtzahl.

In-Prozess gegen eine Wegwerf-DB; kein Server.
"""
import asyncio
import importlib
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path

import pytest
from fastapi import HTTPException

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

MONGO_URL = "mongodb://127.0.0.1:27017"
FIRMA = {"company_name": "Firma R27 GmbH", "contact_person": "Chef",
         "address": "Weg 1", "zip_code": "30159", "city": "Hannover",
         "phone": "0511 1", "email": "firma@r27.test"}


def _jetzt():
    return datetime.now(timezone.utc).isoformat()


def _modul(name):
    return importlib.import_module(name)


@pytest.fixture
def welt():
    from motor.motor_asyncio import AsyncIOMotorClient
    s = uuid.uuid4().hex[:10]
    namen = ["deps", "routes.contracts", "routes.listings", "routes.bestand",
             "kaufvorgang", "lifecycle", "auto_daten", "routes.appointments",
             "fahrzeugpool", "beweis_service"]
    mods = [_modul(n) for n in namen]
    alt = [(m, getattr(m, "db", None)) for m in mods]

    class _W:
        pass

    w = _W()
    w.s = s
    w.dealer_id = f"d_r27_{s}"
    w.chef = {"id": f"chef_r27_{s}", "dealer_id": w.dealer_id, "role": "dealer"}
    w.a = {"id": f"sa_r27_{s}", "dealer_id": w.dealer_id, "role": "sucher",
           "settings_override": {"company_name": "Sucher A"}}
    w.b = {"id": f"sb_r27_{s}", "dealer_id": w.dealer_id, "role": "sucher",
           "settings_override": {"company_name": "Sucher B"}}
    w.loop = asyncio.new_event_loop()
    asyncio.set_event_loop(w.loop)
    w.client = AsyncIOMotorClient(MONGO_URL, serverSelectionTimeoutMS=5000)
    w.db_name = f"autoschnell_r27_{s}"
    w.db = w.client[w.db_name]
    for m in mods:
        if hasattr(m, "db"):
            m.db = w.db
    w.run = lambda coro: w.loop.run_until_complete(coro)
    w.run(w.db.users.insert_many([{**u, "active": True, "created_at": _jetzt()}
                                  for u in (w.chef, w.a, w.b)]))
    w.run(w.db.dealers.insert_one({"id": w.dealer_id, "user_id": w.chef["id"],
                                   **FIRMA, "created_at": _jetzt()}))
    yield w
    try:
        w.run(w.client.drop_database(w.db_name))
    finally:
        for m, d in alt:
            if d is not None:
                m.db = d
        w.client.close()
        w.loop.close()


def _fahrzeug(w, vid, besitzer, **extra):
    doc = {"id": vid, "dealer_id": w.dealer_id, "owner_user_id": besitzer["id"],
           "lifecycle": "verglichen", "status": "verglichen", "source": "plattform",
           "data": {"make_label": "BMW", "model_label": "320d", "mileage": 100000},
           "created_at": _jetzt(), "updated_at": _jetzt()}
    doc.update(extra)
    return doc


# ------------------------------------------------- Erstvergleich-Rennen
def test_01_verlierer_ueberschreibt_die_daten_des_gewinners_nicht(welt):
    """Das Fahrzeug entsteht zwischen Lesen und Schreiben (Kollege war
    schneller) und ist schon weiter. Der zweite Vergleich darf `data` nicht
    ueberschreiben — seine frischen Daten landen unter inserat_aktuell."""
    L = _modul("routes.listings")
    w = welt
    vid = f"v_r27_{w.s}"
    # Gewinner A hat das Auto schon angelegt UND einen Vertrag erstellt
    w.run(w.db.vehicles.insert_one(_fahrzeug(
        w, vid, w.a, lifecycle="vertrag_erstellt",
        data={"make_label": "BMW", "model_label": "320d", "mileage": 100000,
              "vom_gewinner": True})))

    # B liest "gibt es noch nicht" (Rennen), schreibt dann per Upsert
    echtes_find_one = w.db.vehicles.find_one
    zaehler = {"n": 0}

    async def _find_one(*a, **kw):
        zaehler["n"] += 1
        if zaehler["n"] == 1:
            return None                      # genau das Zeitfenster des Rennens
        return await echtes_find_one(*a, **kw)

    w.db.vehicles.find_one = _find_one
    try:
        kollege = w.run(L._fahrzeug_uebernehmen(
            w.b, vid, "320d-id", {"make_label": "BMW", "model_label": "320d",
                                  "mileage": 111111, "vom_verlierer": True}))
    finally:
        w.db.vehicles.find_one = echtes_find_one

    doc = w.run(echtes_find_one({"id": vid}))
    assert doc["data"].get("vom_gewinner") is True, "Daten des Gewinners bleiben"
    assert doc["data"]["mileage"] == 100000
    assert "vom_verlierer" not in doc["data"]
    assert (doc.get("inserat_aktuell") or {}).get("vom_verlierer") is True, \
        "die frischen Daten des zweiten Suchers stehen unter inserat_aktuell"
    assert doc["owner_user_id"] == w.a["id"], "Besitzer bleibt der Gewinner"
    assert kollege and kollege["user_id"] == w.a["id"]


def test_02_erstvergleich_legt_weiterhin_normal_an(welt):
    """Gegenprobe: Gibt es das Fahrzeug wirklich nicht, entsteht es mit den
    frischen Daten und dem Vergleichenden als Besitzer."""
    L = _modul("routes.listings")
    w = welt
    vid = f"v_neu_{w.s}"
    kollege = w.run(L._fahrzeug_uebernehmen(
        w.a, vid, "id-neu", {"make_label": "VW", "model_label": "Golf", "mileage": 5}))
    doc = w.run(w.db.vehicles.find_one({"id": vid}))
    assert kollege is None
    assert doc["data"]["model_label"] == "Golf" and doc["data"]["mileage"] == 5
    assert doc["owner_user_id"] == w.a["id"] and doc["lifecycle"] == "verglichen"


# ------------------------------------------------- Vertrag nach dem Kauf
def _vertrag_body(C, vid):
    return C.ContractIn(vehicle_id=vid, seller_name="Verkäufer V", purchase_price=1000,
                        dealer_company="Käufer GmbH", dealer_address="Weg 1",
                        dealer_zip="30159", dealer_city="Hannover")


def test_03_nur_verkauft_geloescht_archiviert_sperren_den_vertrag(welt):
    """Beschluss Ahmad (12.09.2026): Das System sperrt nichts von sich aus.
    Ein ABGEHOLTES Fahrzeug bleibt vertragsfaehig — wenn das Auto weg ist,
    loeschen Sucher und Chef es selbst."""
    C = _modul("routes.contracts")
    w = welt
    for lc in ("verkauft", "archiviert"):
        vid = f"v_{lc}_{w.s}"
        w.run(w.db.vehicles.insert_one(_fahrzeug(w, vid, w.a, lifecycle=lc)))
        with pytest.raises(HTTPException) as e:
            w.run(C.create_contract(_vertrag_body(C, vid), w.b))
        assert e.value.status_code == 409, (lc, e.value.detail)
    # Abgeholt: ausdruecklich ERLAUBT (auch mit abgeschlossenem Kaufvorgang)
    vid = f"v_abgeholt_{w.s}"
    w.run(w.db.vehicles.insert_one(_fahrzeug(
        w, vid, w.a, lifecycle="abgeholt", abgeholt_kaufvorgang_id="kv-1")))
    w.run(C.create_contract(_vertrag_body(C, vid), w.b))
    assert w.run(w.db.generated_pdfs.count_documents({"vehicle_id": vid})) == 1


# ------------------------------------------------------- Gewinnerlogik
def test_05_abgeschlossener_kauf_laesst_die_anderen_vorgaenge_in_ruhe(welt):
    """Beschluss Ahmad (12.09.2026): Holt ein Sucher ab, wird der Vorgang
    des Kollegen NICHT automatisch storniert und sein Termin bleibt stehen.
    Aufraeumen entscheiden die Beteiligten selbst."""
    import kaufvorgang as KV
    w = welt
    vid = f"v_win_{w.s}"
    w.run(w.db.vehicles.insert_one(_fahrzeug(w, vid, w.a, lifecycle="abholung_geplant")))
    t_b = f"t_b_{w.s}"
    w.run(w.db.appointments.insert_one({
        "id": t_b, "dealer_id": w.dealer_id, "vehicle_id": vid, "status": "offen",
        "pickup_date": "2099-01-01", "created_by": w.b["id"], "created_at": _jetzt()}))
    w.run(w.db.kaufvorgaenge.insert_many([
        {"id": f"kv_a_{w.s}", "dealer_id": w.dealer_id, "user_id": w.a["id"],
         "vehicle_id": vid, "contract_id": f"c_a_{w.s}", "status": "abgeholt",
         "purchase_price": 9000, "created_at": _jetzt(), "updated_at": _jetzt()},
        {"id": f"kv_b_{w.s}", "dealer_id": w.dealer_id, "user_id": w.b["id"],
         "vehicle_id": vid, "contract_id": f"c_b_{w.s}", "status": "abholung_geplant",
         "appointment_id": t_b, "purchase_price": 9500,
         "created_at": _jetzt(), "updated_at": _jetzt()}]))

    ziel = w.run(KV.fahrzeug_status_aggregieren(vid, w.dealer_id, user=w.chef))
    assert ziel == "abgeholt"
    kv_b = w.run(w.db.kaufvorgaenge.find_one({"id": f"kv_b_{w.s}"}))
    assert kv_b["status"] == "abholung_geplant", "der Vorgang des Kollegen bleibt offen"
    termin = w.run(w.db.appointments.find_one({"id": t_b}))
    assert termin["status"] == "offen", "der Termin des Kollegen bleibt stehen"
    # Der realisierte Einkaufspreis am Fahrzeug kommt vom abgeholten Vorgang
    fzg = w.run(w.db.vehicles.find_one({"id": vid}))
    assert fzg["abgeholt_kaufvorgang_id"] == f"kv_a_{w.s}"
    assert fzg["purchase_price"] == 9000


# ------------------------------------------------------- Stille Grenzen
def test_06_akte_meldet_wie_viele_vertraege_es_wirklich_gibt(welt):
    B = _modul("routes.bestand")
    w = welt
    vid = f"v_akte_{w.s}"
    w.run(w.db.vehicles.insert_one(_fahrzeug(w, vid, w.a)))
    w.run(w.db.generated_pdfs.insert_many([
        {"id": f"c{i}_{w.s}", "dealer_id": w.dealer_id, "user_id": w.a["id"],
         "vehicle_id": vid, "contract_no": f"KV-{i}", "created_at": f"2026-09-{i+1:02d}",
         "contract_data": {}} for i in range(13)]))
    akte = w.run(B.vehicle_akte(vid, w.chef))
    assert len(akte["contracts"]) == 10
    assert akte["contracts_gesamt"] == 13, "der Chef sieht, dass mehr da sind"
    assert akte["appointments_gesamt"] == 0


def test_07_bestandsliste_sagt_wenn_sie_gekuerzt_ist(welt, monkeypatch):
    """Gegenpruefung 12.09.2026: Grenze wirklich herabsetzen statt den
    Quelltext zu durchsuchen."""
    B = _modul("routes.bestand")
    w = welt
    w.run(w.db.vehicles.insert_many([
        _fahrzeug(w, f"v_liste_{i}_{w.s}", w.a, lifecycle="bestand") for i in range(6)]))
    daten = w.run(B.list_bestand(w.chef))
    assert daten["gesamt"] == 6 and daten["gekuerzt"] is False
    assert len(daten["items"]) == 6

    monkeypatch.setattr(B, "BESTAND_MAX", 4)
    daten = w.run(B.list_bestand(w.chef))
    assert len(daten["items"]) == 4, "Liste wirklich gekuerzt"
    assert daten["gesamt"] == 6 and daten["gekuerzt"] is True
    assert daten["counts"].get("bestand") == 6, "die Zaehler bleiben vollstaendig"


# ---------------------------------------------------- LIVE-Zaehler
def _vergleich(w, cache_key, *, user, dealer_id=None, minuten=1, wiederholung=False):
    from datetime import timedelta
    return {
        "id": f"vc_{uuid.uuid4().hex[:8]}", "cache_key": cache_key,
        "mobile_ad_id": cache_key.split(":")[-1],
        "source": cache_key.split(":")[0],
        "dealer_id": dealer_id or w.dealer_id, "user_id": user["id"],
        "wiederholung": wiederholung,
        "created_at": (datetime.now(timezone.utc)
                       - timedelta(minutes=minuten)).isoformat()}


def test_08_live_zaehler_ist_fuer_alle_gleich(welt):
    """Beschluss Ahmad: Der LIVE-Zaehler ist ein anonymes Nachfrage-Signal.
    Frueher rechnete er die EIGENE Firma heraus (oben 0, unten 8) — jetzt
    sehen alle dieselbe Zahl, egal aus welcher Firma."""
    L = _modul("routes.listings")
    w = welt
    fremd = {"id": f"fremd_{w.s}", "dealer_id": f"d_fremd_{w.s}", "role": "sucher"}
    w.run(w.db.vehicle_comparisons.insert_many([
        _vergleich(w, "kleinanzeigen:111", user=w.a),
        _vergleich(w, "kleinanzeigen:111", user=w.b),
        _vergleich(w, "kleinanzeigen:111", user=fremd, dealer_id=fremd["dealer_id"]),
    ]))
    eigene = w.run(L.live_counter("111", quelle="kleinanzeigen", user=w.a))
    fremde = w.run(L.live_counter("111", quelle="kleinanzeigen", user=fremd))
    assert eigene["active_now"] == 3 and fremde["active_now"] == 3
    assert eigene["today"] == 3 and fremde["today"] == 3
    assert eigene["fenster_minuten"] == L.LIVE_FENSTER_MIN


def test_09_zaehler_trennt_die_portale(welt):
    """mobile.de 12345 und Kleinanzeigen 12345 sind zwei verschiedene Autos."""
    L = _modul("routes.listings")
    w = welt
    w.run(w.db.vehicle_comparisons.insert_many([
        _vergleich(w, "mobile:12345", user=w.a),
        _vergleich(w, "mobile:12345", user=w.b),
        _vergleich(w, "kleinanzeigen:12345", user=w.a),
    ]))
    assert w.run(L.live_counter("12345", quelle="mobile", user=w.a))["active_now"] == 2
    assert w.run(L.live_counter("12345", quelle="kleinanzeigen", user=w.a))["active_now"] == 1


def test_10_wiederholungen_blaehen_den_zaehler_nicht_auf(welt):
    """Profilwechsel/Doppelklick starten denselben Vergleich erneut — das
    ist keine neue Nachfrage."""
    L = _modul("routes.listings")
    w = welt
    w.run(w.db.vehicle_comparisons.insert_many([
        _vergleich(w, "kleinanzeigen:222", user=w.a),
        _vergleich(w, "kleinanzeigen:222", user=w.a, wiederholung=True),
        _vergleich(w, "kleinanzeigen:222", user=w.a, wiederholung=True),
    ]))
    zahl = w.run(L.live_counter("222", quelle="kleinanzeigen", user=w.a))
    assert zahl["active_now"] == 1 and zahl["today"] == 1


def test_11_alte_eintraege_zaehlen_nur_beim_tageswert(welt):
    """Das 10-Minuten-Fenster ist enger als der Tageswert. Der Tageswert
    wird aus den Zeitstempeln selbst erwartet — sonst haengt der Test an
    der Uhrzeit des Laufs (kurz nach Mitternacht)."""
    L = _modul("routes.listings")
    w = welt
    eintraege = [_vergleich(w, "kleinanzeigen:333", user=w.a, minuten=1),
                 _vergleich(w, "kleinanzeigen:333", user=w.b, minuten=120)]
    w.run(w.db.vehicle_comparisons.insert_many([dict(e) for e in eintraege]))
    heute = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    erwartet_heute = sum(1 for e in eintraege if e["created_at"].startswith(heute))
    zahl = w.run(L.live_counter("333", quelle="kleinanzeigen", user=w.a))
    assert zahl["active_now"] == 1, "nur das 10-Minuten-Fenster"
    assert zahl["today"] == erwartet_heute
    assert erwartet_heute >= 1
