# -*- coding: utf-8 -*-
"""Runde 19 (16.09.2026): Reviewer-Listen Sucher/Termine, Abrufe/Link-Jobs,
Auto-Daten — in-process gegen eine Wegwerf-Datenbank (echtes Mongo), keine
HTTP-Aufrufe.

- Termin-Antworten fuer Sucher ohne Konto-Kennungen; Termin-Detail mit Projektion
- Entfernen = Uebergabe des ganzen Vorgangs; Besitzerwechsel mit Merker + Nachholen
- Altbestand ohne Besitzer: Verlierer wird Mitbearbeiter
- Pool-Trimmen: Nachfolger wird nachgetrimmt, geschuetzte Fahrzeuge bleiben
- Abruf-Lease mit Claim-Token; Link-Jobs schliessen nur den eigenen Claim ab
- Anbieter-Budget bei technischem Fehler zurueckgebucht
- Browser-Ingest first-wins atomar
- Auto-Daten: Datensatz nur ueber die Fahrzeug-ID, Entfernen markiert zuerst,
  Nachfuehrung per Merker, Vertragssperre
- Frischabgleich paginiert; Vertrags-Loeschung durch Sucher bei finalem Protokoll
"""
import asyncio
import inspect
import os
import sys
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest

BACKEND = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND))

import auto_daten  # noqa: E402
import cleanup_service as CS  # noqa: E402
import deps  # noqa: E402
import fahrzeugpool as FP  # noqa: E402
import link_jobs as LJ  # noqa: E402
import listing_identity as LI  # noqa: E402
import provider_fetch as PF  # noqa: E402
import routes.appointments as A  # noqa: E402
import routes.bestand as B  # noqa: E402
import routes.contracts as C  # noqa: E402
import routes.listings as L  # noqa: E402
import routes.manual_search as MS  # noqa: E402

MONGO_URL = os.environ.get("MONGO_URL") or "mongodb://127.0.0.1:27017"


def _jetzt(delta_s: int = 0) -> str:
    return (datetime.now(timezone.utc) + timedelta(seconds=delta_s)).isoformat()


@pytest.fixture
def wegwerf(monkeypatch):
    from motor.motor_asyncio import AsyncIOMotorClient
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    client = AsyncIOMotorClient(MONGO_URL, serverSelectionTimeoutMS=5000)
    name = f"autoschnell_r19_{uuid.uuid4().hex[:10]}"
    db = client[name]
    for mod in (deps, A, B, C, L):
        monkeypatch.setattr(mod, "db", db)
    try:
        yield SimpleNamespace(db=db, run=loop.run_until_complete)
    finally:
        try:
            loop.run_until_complete(client.drop_database(name))
        finally:
            client.close()
            loop.close()


SUCHER = {"id": "s1", "dealer_id": "d1", "role": "sucher", "active": True}
SUCHER2 = {"id": "s2", "dealer_id": "d1", "role": "sucher", "active": True}
CHEF = {"id": "chef", "dealer_id": "d1", "role": "dealer", "active": True}


# ------------------------------------------------------------------ Termine
def test_termin_antworten_fuer_sucher_ohne_kennungen():
    a = {"id": "t1", "created_by": "s1", "uebergeben_von": "s0", "uebergeben_am": "x",
         "kaufvorgang_id": "kv", "driver_id_hist": "f0", "driver_id": "f1", "status": "offen"}
    assert A.termin_fuer_sucher(dict(CHEF), dict(a)) == a
    s = A.termin_fuer_sucher(dict(SUCHER), dict(a))
    assert s == {"id": "t1", "driver_id": "f1", "status": "offen"}
    q = inspect.getsource(A.get_appointment)
    assert "konten_maskieren(user, v)" in q and '"bestand"' not in q and '{"_id": 0, "id": 1, "data": 1' in q
    assert "return termin_fuer_sucher(user, a)" in q
    q = inspect.getsource(A.list_appointments)
    assert "to_list(grenze + 1)" in q and "abgeschnitten = len(items) > grenze" in q
    assert '.sort("created_at", -1).to_list(grenze + 1)' in q
    q = inspect.getsource(A.delete_appointment)
    assert '"updated_at": appt.get("updated_at")}' in q


# ------------------------------------------------------------ Fahrzeuge
def test_entfernen_uebergibt_ganzen_vorgang_an_chef(wegwerf):
    db, run = wegwerf.db, wegwerf.run
    run(db.dealers.insert_one({"id": "d1", "user_id": "chef", "company_name": "F", "created_at": _jetzt()}))
    run(db.vehicles.insert_one({"id": "v1", "dealer_id": "d1", "owner_user_id": "s1",
                                "mitbearbeiter_ids": [], "lifecycle": "abgeholt", "data": {}}))
    run(db.generated_pdfs.insert_one({"id": "c1", "dealer_id": "d1", "vehicle_id": "v1", "user_id": "s1",
                                      "created_at": _jetzt()}))
    run(db.appointments.insert_one({"id": "t1", "dealer_id": "d1", "vehicle_id": "v1", "created_by": "s1",
                                    "status": "erledigt", "created_at": _jetzt()}))
    run(db.kaufvorgaenge.insert_one({"id": "kv1", "dealer_id": "d1", "vehicle_id": "v1", "user_id": "s1"}))
    out = run(B.vehicle_fuer_sucher_entfernen("v1", dict(SUCHER)))
    assert out["an_chef"] is True and out["uebergabe"] == {"kaufvorgaenge": 1, "vertraege": 1, "termine": 1}
    assert run(db.generated_pdfs.find_one({"id": "c1"}))["user_id"] == "chef"
    t = run(db.appointments.find_one({"id": "t1"}))
    assert t["created_by"] == "chef" and t["uebergeben_von"] == "s1"
    assert run(db.kaufvorgaenge.find_one({"id": "kv1"}))["user_id"] == "chef"
    assert run(db.vehicles.find_one({"id": "v1"}))["owner_user_id"] == "chef"


def test_besitzerwechsel_merker_nachholen_und_protokollsperre_je_firma(wegwerf):
    db, run = wegwerf.db, wegwerf.run
    run(db.users.insert_many([{"id": "s1", "dealer_id": "d1", "role": "sucher", "active": True},
                              {"id": "s2", "dealer_id": "d1", "role": "sucher", "active": True}]))
    run(db.vehicles.insert_one({"id": "v1", "dealer_id": "d1", "owner_user_id": "s1", "mitbearbeiter_ids": []}))
    run(db.generated_pdfs.insert_one({"id": "c1", "dealer_id": "d1", "vehicle_id": "v1", "user_id": "s1"}))
    # Protokoll einer ANDEREN Firma (gleiche Fahrzeug-ID) sperrt nicht mehr
    run(db.pickup_protocols.insert_one({"id": "p9", "dealer_id": "d2", "vehicle_id": "v1",
                                        "status": "zur_freigabe", "appointment_id": "tx"}))
    out = run(B.set_vehicle_owner("v1", B.BesitzerIn(owner_user_id="s2"), dict(CHEF)))
    assert out["owner_user_id"] == "s2" and out["uebergabe"]["vertraege"] == 1
    v = run(db.vehicles.find_one({"id": "v1"}))
    assert v["owner_user_id"] == "s2" and "uebergabe_offen" not in v
    # Abgebrochene Uebergabe (Merker steht, Vertrag noch beim alten Konto): die
    # Wiederholung mit demselben Ziel holt nach statt "unveraendert" zu melden
    run(db.generated_pdfs.update_one({"id": "c1"}, {"$set": {"user_id": "s2"}}))
    run(db.vehicles.update_one({"id": "v1"}, {"$set": {"owner_user_id": "s1",
                                                      "uebergabe_offen": {"von": "s2", "an": "s1", "seit": _jetzt(-300)}}}))
    out = run(B.set_vehicle_owner("v1", B.BesitzerIn(owner_user_id="s1"), dict(CHEF)))
    assert out.get("unveraendert") is True and out["uebergabe"]["vertraege"] == 1
    assert run(db.generated_pdfs.find_one({"id": "c1"}))["user_id"] == "s1"
    assert "uebergabe_offen" not in run(db.vehicles.find_one({"id": "v1"}))
    # Aufraeumjob holt liegengebliebene Merker nach
    run(db.vehicles.update_one({"id": "v1"}, {"$set": {"uebergabe_offen": {"von": "s1", "an": "s2", "seit": _jetzt(-300)}}}))
    assert run(CS.uebergaben_nachholen(db, datetime.now(timezone.utc))) == 1
    assert run(db.generated_pdfs.find_one({"id": "c1"}))["user_id"] == "s2"
    # eigene Firma mit laufendem Protokoll: Sperre greift
    run(db.pickup_protocols.insert_one({"id": "p1", "dealer_id": "d1", "vehicle_id": "v1",
                                        "status": "zur_freigabe", "appointment_id": "t1"}))
    from fastapi import HTTPException
    with pytest.raises(HTTPException) as e:
        run(B.set_vehicle_owner("v1", B.BesitzerIn(owner_user_id="s2"), dict(CHEF)))
    assert e.value.status_code == 409


def test_altbestand_ohne_besitzer_verlierer_wird_mitbearbeiter(wegwerf):
    db, run = wegwerf.db, wegwerf.run
    run(db.vehicles.insert_one({"id": "v1", "dealer_id": "d1", "mitbearbeiter_ids": [], "lifecycle": "verglichen",
                                "updated_at": _jetzt()}))
    filt = {"id": "v1", "dealer_id": "d1"}
    assert run(L._altbestand_uebernehmen(dict(SUCHER), filt)) is None          # Gewinner
    kollege = run(L._altbestand_uebernehmen(dict(SUCHER2), filt))               # Verlierer
    assert kollege and kollege.get("user_id") == "s1"
    v = run(db.vehicles.find_one({"id": "v1"}))
    assert v["owner_user_id"] == "s1" and "s2" in v["mitbearbeiter_ids"]
    q = inspect.getsource(L._fahrzeug_uebernehmen)
    assert q.count("_altbestand_uebernehmen(user, filt)") == 2


def test_pool_trimmen_nachfolger_und_schutzstempel(wegwerf):
    db, run = wegwerf.db, wegwerf.run
    run(db.users.insert_many([{"id": "a", "dealer_id": "d1", "role": "sucher", "active": True},
                              {"id": "b", "dealer_id": "d1", "role": "sucher", "active": True}]))
    alt, neu = _jetzt(-3600), _jetzt()
    run(db.vehicles.insert_many([
        {"id": "va_alt", "dealer_id": "d1", "lifecycle": "verglichen", "owner_user_id": "a",
         "mitbearbeiter_ids": ["b"], "updated_at": alt},
        {"id": "va_neu", "dealer_id": "d1", "lifecycle": "verglichen", "owner_user_id": "a",
         "mitbearbeiter_ids": [], "updated_at": neu},
        {"id": "vb", "dealer_id": "d1", "lifecycle": "verglichen", "owner_user_id": "b",
         "mitbearbeiter_ids": [], "updated_at": neu},
        # geschuetzt (Vertrag entsteht gerade): darf nicht getrimmt werden
        {"id": "va_schutz", "dealer_id": "d1", "lifecycle": "verglichen", "owner_user_id": "a",
         "mitbearbeiter_ids": [], "updated_at": _jetzt(-7200), "geschuetzt_bis": _jetzt(300)},
    ]))
    geloescht = run(FP.fahrzeugpool_trimmen(db, "d1", limit=1, owner_user_id="a"))
    # a: va_neu bleibt, va_alt geht an b; b hat dann 2 -> Nachtrimmen loescht das aeltere
    assert run(db.vehicles.find_one({"id": "va_schutz"})) is not None
    assert run(db.vehicles.find_one({"id": "va_neu"}))["owner_user_id"] == "a"
    assert run(db.vehicles.count_documents({"owner_user_id": "b", "lifecycle": "verglichen"})) == 1
    assert run(db.vehicles.find_one({"id": "va_alt"})) is None and geloescht == 1
    run(FP.kurz_schuetzen(db, "d1", "vb", sekunden=60))
    assert run(db.vehicles.find_one({"id": "vb"}))["geschuetzt_bis"] > _jetzt()


# ------------------------------------------------------------ Abrufe
def test_lease_mit_claim_token(wegwerf):
    db, run = wegwerf.db, wegwerf.run
    key = "kleinanzeigen:1"
    run(db.listings_cache.insert_one({"cache_key": key, "source": "kleinanzeigen", "item_id": "1",
                                      "fetching_until": datetime.now(timezone.utc) + timedelta(seconds=60),
                                      "fetching_claim": "B"}))
    # ein ueberholter Prozess (Claim A) gibt die Lease von B NICHT frei
    run(LI._lease_freigeben(db, key, "A"))
    doc = run(db.listings_cache.find_one({"cache_key": key}))
    assert doc["fetching_until"] is not None and doc["fetching_claim"] == "B"
    run(LI._lease_freigeben(db, key, "B"))
    doc = run(db.listings_cache.find_one({"cache_key": key}))
    assert doc["fetching_until"] is None and "fetching_claim" not in doc
    q = inspect.getsource(LI.get_or_fetch_listing)
    assert q.count("_lease_freigeben(db, cache_key, claim)") == 5
    assert '{"cache_key": cache_key, "fetching_claim": claim}' in q     # Herzschlag + Ergebnis
    # das Ergebnis wird ohne Upsert unter der eigenen Lease geschrieben
    ende = q.split('"$unset": {"fetching_claim": ""},')[1]
    assert "upsert" not in ende.split("if res.matched_count == 0:")[0]
    assert "if res.matched_count == 0:" in ende


def test_link_jobs_claim_und_race(wegwerf):
    q = inspect.getsource(LJ._process)
    # Endzustaende nur unter dem eigenen Claim
    assert '{"id": job["id"]}' in q.split("eigener_claim = ")[0]         # nur der Import-Fehler davor
    assert '{"id": job["id"]}' not in q.split("eigener_claim = ")[1]
    assert "from routes.listings import _AbrufSlot" in q
    q = inspect.getsource(LJ.enqueue_job)
    assert "peek_cached_listing" in q and "raise JobRace(" in q
    assert issubclass(LJ.JobRace, LJ.WarteschlangeVoll)
    assert "except JobRace as race:" in inspect.getsource(L.listings_check) if hasattr(L, "listings_check") \
        else "JobRace" in inspect.getsource(L)


def test_budget_wird_bei_technischem_fehler_zurueckgebucht(wegwerf, monkeypatch):
    db, run = wegwerf.db, wegwerf.run
    monkeypatch.setattr(PF, "MOCK_PROVIDER_FETCH", False)

    async def kaputt(db_, source, item_id, url):
        raise RuntimeError("Netz weg")
    monkeypatch.setattr(PF, "_abrufen", kaputt)
    with pytest.raises(RuntimeError):
        run(PF.fetch_listing(db, "mobile", "1", "https://suchen.mobile.de/x", dealer_id="d1"))
    tag = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    for schluessel in (f"{tag}:firma:d1", f"{tag}:gesamt"):
        doc = run(db.provider_budget.find_one({"_id": schluessel}))
        assert doc is not None and doc["n"] == 0, (schluessel, doc)


def test_browser_ingest_first_wins_atomar(wegwerf):
    db, run = wegwerf.db, wegwerf.run
    url = "https://www.kleinanzeigen.de/s-anzeige/test/123456789-216-1"
    run(LI.store_client_listing(db, url, {"make": "VW", "price": 1}, "d1"))
    run(LI.store_client_listing(db, url, {"make": "VW", "price": 999}, "d1"))
    doc = run(db.listings_cache_client.find_one({"dealer_id": "d1"}))
    assert doc["data"]["price"] == 1 and doc.get("zuletzt_gesehen")


def test_vergleich_deckel_und_manuelle_suche_reihenfolge():
    assert L.VERGLEICH_JE_KONTO_MINUTE == 120
    q = inspect.getsource(L.compare)
    assert q.index("_vergleich_limiter.check(") < q.index("get_or_fetch_listing(")
    assert q.count("except Exception as exc:") == 0 or "compare fetch failed" in q
    assert q.count("except Exception:") >= 1 and 'log.exception("compare fetch failed for %s", raw_url)' in q
    q = inspect.getsource(MS.manual_search)
    assert q.index("Modell unbekannt") < q.index("suche_limiter.check(")


# ------------------------------------------------------------ Auto-Daten
def _datensatz(did, cents=500000):
    return {"id": did, "brand": "VW", "model": "Golf", "purchase_price_cents": cents,
            "currency": "EUR", "damages": [], "schema_version": 2, "purchase_date": "2026-09-01"}


def _vertrag(cid, did, **extra):
    return {"id": cid, "dealer_id": "d1", "user_id": "chef", "version": 1, "status": "erstellt",
            "contract_no": cid, "vehicle_id": "v1", "mobile_ad_id": "m1",
            "admin_vehicle_data_id": did,
            "contract_data": {"seller_name": "V", "purchase_price": 5000.0,
                              "vehicle_make": "VW", "vehicle_model": "Golf"},
            "send_status": [], "created_at": _jetzt(), **extra}


def test_auto_daten_entfernen_markiert_zuerst_und_nachfuehren(wegwerf):
    db, run = wegwerf.db, wegwerf.run
    q = inspect.getsource(auto_daten.entfernen)
    assert q.index("update_many(") < q.index("delete_one(")
    run(db.admin_vehicle_data.insert_one(_datensatz("avd1")))
    run(db.generated_pdfs.insert_one(_vertrag("c1", "avd1")))
    # Nachfuehren aktualisiert den vorhandenen Datensatz und loescht den Merker
    run(db.generated_pdfs.update_one({"id": "c1"}, {"$set": {"auto_daten_nachfuehrung_offen": True,
                                                            "contract_data.purchase_price": 4321.0}}))
    c = run(db.generated_pdfs.find_one({"id": "c1"}, {"_id": 0}))
    assert run(auto_daten.nachfuehren(db, c)) is True
    assert run(db.admin_vehicle_data.find_one({"id": "avd1"}))["purchase_price_cents"] == 432100
    assert "auto_daten_nachfuehrung_offen" not in run(db.generated_pdfs.find_one({"id": "c1"}))
    # Datensatz fehlt ohne Vermerk (Verweis ins Leere): neuer Datensatz statt Alarm
    run(db.admin_vehicle_data.delete_one({"id": "avd1"}))
    c = run(db.generated_pdfs.find_one({"id": "c1"}, {"_id": 0}))
    assert run(auto_daten.nachfuehren(db, c)) is True
    c = run(db.generated_pdfs.find_one({"id": "c1"}))
    assert c["admin_vehicle_data_id"] != "avd1"
    assert run(db.admin_vehicle_data.find_one({"id": c["admin_vehicle_data_id"]}))["purchase_price_cents"] == 432100
    # Reparaturjob holt Merker nach
    run(db.generated_pdfs.update_one({"id": "c1"}, {"$set": {"auto_daten_nachfuehrung_offen": True,
                                                            "contract_data.purchase_price": 100.0}}))
    assert run(CS.auto_daten_reparieren(db)) >= 1
    assert run(db.admin_vehicle_data.find_one({"id": c["admin_vehicle_data_id"]}))["purchase_price_cents"] == 10000
    # aktualisieren meldet, ob der Datensatz noch da war
    assert run(auto_daten.aktualisieren(db, "gibt-es-nicht", {"purchase_price": 1}, {})) is False


def test_vertragssperre_je_firma_und_fahrzeug(wegwerf):
    db, run = wegwerf.db, wegwerf.run
    k = run(auto_daten.vertrag_sperre(db, "d1", "v1", sekunden=30))
    assert k == "auto_daten:d1:v1"
    # zweiter Aufruf wartet (~6 s) und geht dann ohne Sperre weiter (None) — hier kurz:
    import time
    t0 = time.time()
    assert run(auto_daten.vertrag_sperre(db, "d1", "v1", sekunden=30)) is None
    assert time.time() - t0 >= 3
    run(auto_daten.sperre_freigeben(db, k))
    assert run(auto_daten.vertrag_sperre(db, "d1", "v1", sekunden=30)) == k
    assert run(auto_daten.vertrag_sperre(db, "d1", None)) is None
    q = inspect.getsource(C.create_contract)
    assert q.index("vertrag_sperre(") < q.index("insert_one(doc)") < q.index("aktualisieren(db, auto_daten_id")


def test_fristloeschung_repariert_verweis_ins_leere(wegwerf):
    db, run = wegwerf.db, wegwerf.run
    alt = (datetime.now(timezone.utc) - timedelta(days=91)).isoformat()
    run(db.generated_pdfs.insert_one(_vertrag("c_leer", "avd_weg", created_at=alt)))
    n = run(CS.vertraege_nach_frist_loeschen(db, datetime.now(timezone.utc), aktiv=True))
    assert n == 1 and run(db.generated_pdfs.find_one({"id": "c_leer"})) is None
    assert run(db.betriebsalarme.count_documents({"ref": "c_leer"})) == 0
    assert run(db.admin_vehicle_data.count_documents({})) == 1        # Datensatz bleibt (anonym)


def test_frischabgleich_paginiert(wegwerf, monkeypatch):
    db, run = wegwerf.db, wegwerf.run
    gesehen = []

    async def _zeiger(dealer_id, appt_id, contract_id):
        gesehen.append(appt_id)

    async def _status(appt, status):
        return True
    import kaufvorgang as KV
    monkeypatch.setattr(A, "_vertragszeiger_abgleichen", _zeiger)
    monkeypatch.setattr(KV, "termin_status_uebernehmen", _status)
    run(db.appointments.insert_many([
        {"id": f"t{i}", "dealer_id": "d1", "status": "offen", "updated_at": _jetzt(-600 + i)} for i in range(7)]))
    assert run(CS.termine_frisch_abgleichen(db, datetime.now(timezone.utc), stunden=2, limit=2)) == 7
    assert sorted(gesehen) == [f"t{i}" for i in range(7)]


def test_sucher_loescht_keinen_vertrag_mit_finalem_protokoll():
    q = inspect.getsource(C.delete_contract)
    assert '"status": "final"}, limit=1)' in q and 'user.get("role") == "sucher" and termin_ids' in q
    q = inspect.getsource(B.vehicle_akte)
    assert "comparisons_gesamt" in q and "protocols_gesamt" in q and "pickup_reports_gesamt" in q
    assert '.sort([("finalized_at", -1), ("version", -1)])' in q
    assert 'v["bestand"].pop(feld, None)' in q and '"created_at": h.get("created_at")} for h in history]' in q
