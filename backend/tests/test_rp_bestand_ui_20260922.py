# -*- coding: utf-8 -*-
"""Rollenprüfung 22.09.2026 — Team Bestand/Oberfläche (bestand_ui).

  RP-085/RP-184/RP-496  Entscheidung "bestand"/"verkaufsentwurf" bei live
                        Inserat -> 409; angezeigter Zustand (von_lifecycle)
  RP-450                "bestand" auf "bestand" = Frist verlängern
  RP-449                Betrag ohne Bezeichnung verschwindet nicht mehr
  RP-461                Bestandsdaten mit Stand (kein stilles Überschreiben)
  RP-276a               Entfernen sieht Termine, die nur über kaufvorgang_id
                        am Sucher hängen
  RP-483                Akte-Historie mit Vertrags-, Protokoll-, Inserats-Refs
  RP-475                Schlüssel-Abweichung landet bei den Mängeln
  RP-566                keine Bilder von fremden Servern im Frontend
  RP-024/RP-274         HSL-Tripel-Variablen nie direkt als Farbe

In-Prozess gegen eine Wegwerf-DB (autoschnell_rpbu_<uuid>), kein Server.
"""
import asyncio
import os
import re
import sys
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from fastapi import HTTPException

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

MONGO_URL = os.environ.get("MONGO_URL") or "mongodb://127.0.0.1:27017"
WURZEL = Path(__file__).resolve().parents[2]
FRONTEND_SRC = WURZEL / "frontend" / "src"


def _jetzt():
    return datetime.now(timezone.utc).isoformat()


def _module(name):
    import importlib
    return importlib.import_module(name)


@pytest.fixture
def welt(monkeypatch):
    from motor.motor_asyncio import AsyncIOMotorClient
    monkeypatch.setenv("MARKTPLATZ_AKTIV", "true")
    s = uuid.uuid4().hex[:10]
    names = ["deps", "lifecycle", "kaufvorgang", "routes.bestand", "routes.resale",
             "routes.contracts", "routes.appointments", "routes.marketplace"]
    mods = [_module(n) for n in names]
    alt = [(m, getattr(m, "db", None)) for m in mods]

    class _W:
        pass

    w = _W()
    w.s = s
    w.dealer_id = f"d_rpbu_{s}"
    w.chef = {"id": f"chef_rpbu_{s}", "dealer_id": w.dealer_id, "role": "dealer"}
    w.sucher = {"id": f"su_rpbu_{s}", "dealer_id": w.dealer_id, "role": "sucher"}
    w.loop = asyncio.new_event_loop()
    asyncio.set_event_loop(w.loop)
    w.client = AsyncIOMotorClient(MONGO_URL, serverSelectionTimeoutMS=5000)
    w.db_name = f"autoschnell_rpbu_{s}"
    w.db = w.client[w.db_name]
    for m in mods:
        if hasattr(m, "db"):
            m.db = w.db
    w.run = lambda coro: w.loop.run_until_complete(coro)
    w.run(w.db.users.insert_many([
        {**w.chef, "active": True, "first_name": "Chef", "created_at": _jetzt()},
        {**w.sucher, "active": True, "first_name": "Sam", "created_at": _jetzt()}]))
    w.run(w.db.dealers.insert_one({"id": w.dealer_id, "user_id": w.chef["id"],
                                   "company_name": "RP GmbH", "created_at": _jetzt()}))
    yield w
    try:
        w.run(w.client.drop_database(w.db_name))
    finally:
        for m, d in alt:
            if d is not None:
                m.db = d
        w.client.close()
        w.loop.close()


def _fahrzeug(w, vid, lifecycle="abgeholt", **extra):
    doc = {"id": vid, "dealer_id": w.dealer_id, "lifecycle": lifecycle,
           "lifecycle_changed_at": _jetzt(), "mobile_ad_id": vid[2:],
           "owner_user_id": w.chef["id"],
           "data": {"make_label": "BMW", "model_label": "320d"},
           "status": "verglichen", "created_at": _jetzt(), "updated_at": _jetzt()}
    doc.update(extra)
    return doc


def _inserat(w, lid, vid, status):
    return {"id": lid, "dealer_id": w.dealer_id, "vehicle_id": vid, "status": status,
            "title": "BMW", "data": {}, "prices": {"public": 9900.0},
            "photos": {"mode": "neu", "einkauf_urls": [], "uploaded_keys": []},
            "costs": [], "created_by": w.chef["id"], "created_at": _jetzt(),
            "updated_at": _jetzt()}


def _status(exc_info):
    return exc_info.value.status_code


# =============================================== RP-085/RP-184/RP-496
@pytest.mark.parametrize("inserat_status", ["verkaufsbereit", "veroeffentlicht", "reserviert"])
def test_01_bestand_bei_live_inserat_409(welt, inserat_status):
    B = _module("routes.bestand")
    w = welt
    vid = f"v_live_{w.s}"
    # veroeffentlicht/verkaufsbereit -> bestand erlaubt die Statusmaschine,
    # genau deshalb braucht es die Inserats-Pruefung.
    lc = "verkaufsbereit" if inserat_status == "verkaufsbereit" else "veroeffentlicht"

    async def lauf():
        await w.db.vehicles.insert_one(_fahrzeug(w, vid, lifecycle=lc))
        await w.db.resale_listings.insert_one(_inserat(w, f"l_{w.s}", vid, inserat_status))
        with pytest.raises(HTTPException) as e:
            await B.vehicle_decision(vid, B.DecisionIn(decision="bestand"), w.chef)
        v = await w.db.vehicles.find_one({"id": vid}, {"_id": 0})
        return _status(e), e.value.detail, v

    status, text, v = w.run(lauf())
    assert status == 409 and "zurückziehen" in text
    assert v["lifecycle"] == lc, "Fahrzeug unverändert"


def test_02_verkaufsentwurf_bei_live_inserat_409_entwurf_blockiert_nicht(welt):
    B = _module("routes.bestand")
    w = welt
    live, frei = f"v_a_{w.s}", f"v_b_{w.s}"

    async def lauf():
        await w.db.vehicles.insert_many([_fahrzeug(w, live, lifecycle="verkaufsbereit"),
                                         _fahrzeug(w, frei, lifecycle="verkaufsentwurf")])
        await w.db.resale_listings.insert_many([
            _inserat(w, f"l_a_{w.s}", live, "verkaufsbereit"),
            _inserat(w, f"l_b_{w.s}", frei, "entwurf")])
        with pytest.raises(HTTPException) as e:
            await B.vehicle_decision(live, B.DecisionIn(decision="verkaufsentwurf"), w.chef)
        # Ein (unsichtbarer) Entwurf blockiert "Nur speichern" nicht.
        r = await B.vehicle_decision(frei, B.DecisionIn(decision="bestand"), w.chef)
        return _status(e), r

    status, r = w.run(lauf())
    assert status == 409
    assert r["lifecycle"] == "bestand" and r["verlaengert"] is False


def test_03_veralteter_tab_von_lifecycle_409(welt):
    B = _module("routes.bestand")
    w = welt
    vid = f"v_tab_{w.s}"

    async def lauf():
        await w.db.vehicles.insert_one(_fahrzeug(w, vid, lifecycle="verkaufsentwurf"))
        with pytest.raises(HTTPException) as e:
            await B.vehicle_decision(
                vid, B.DecisionIn(decision="bestand", von_lifecycle="abgeholt"), w.chef)
        v1 = await w.db.vehicles.find_one({"id": vid}, {"_id": 0})
        r = await B.vehicle_decision(
            vid, B.DecisionIn(decision="bestand", von_lifecycle="verkaufsentwurf"), w.chef)
        return _status(e), v1, r

    status, v1, r = w.run(lauf())
    assert status == 409 and v1["lifecycle"] == "verkaufsentwurf"
    assert r["lifecycle"] == "bestand"
    # Ohne Angabe (alte Oberflaeche) bleibt alles wie bisher
    assert _module("routes.bestand").DecisionIn(decision="bestand").von_lifecycle is None


# =============================================== RP-450
def test_04_frist_verlaengern(welt):
    B = _module("routes.bestand")
    w = welt
    vid = f"v_frist_{w.s}"
    gespeichert = (datetime.now(timezone.utc) - timedelta(days=45)).isoformat()
    alte_frist = (datetime.now(timezone.utc) + timedelta(days=5)).isoformat()

    async def lauf():
        await w.db.vehicles.insert_one(_fahrzeug(
            w, vid, lifecycle="bestand",
            bestand={"saved_at": gespeichert, "expires_at": alte_frist, "notes": "bleibt"}))
        r = await B.vehicle_decision(
            vid, B.DecisionIn(decision="bestand", von_lifecycle="bestand"), w.chef)
        v = await w.db.vehicles.find_one({"id": vid}, {"_id": 0})
        logs = [l["action"] async for l in w.db.activity_logs.find({"ref": vid})]
        return r, v, logs

    r, v, logs = w.run(lauf())
    assert r["verlaengert"] is True and v["lifecycle"] == "bestand"
    neu = datetime.fromisoformat(v["bestand"]["expires_at"])
    assert timedelta(days=49) < neu - datetime.now(timezone.utc) <= timedelta(days=50)
    assert v["bestand"]["saved_at"] == gespeichert, "Aufnahmedatum bleibt"
    assert v["bestand"]["notes"] == "bleibt" and v["bestand"]["verlaengert_am"]
    assert "fahrzeug.bestand.verlaengert" in logs


# =============================================== RP-449
def test_05_betrag_ohne_bezeichnung_heisst_kosten():
    B = _module("routes.bestand")
    assert B._clean_costs([{"label": "", "amount": 250}, {"label": " ", "amount": 0},
                           {"label": "Reifen", "amount": "12.5"}]) == [
        {"label": "Kosten", "amount": 250.0}, {"label": "Reifen", "amount": 12.5}]


# =============================================== RP-461
def test_06_bestand_mit_stand_kein_stilles_ueberschreiben(welt):
    B = _module("routes.bestand")
    w = welt
    vid = f"v_stand_{w.s}"

    async def lauf():
        await w.db.vehicles.insert_one(_fahrzeug(w, vid, lifecycle="bestand",
                                                 bestand={"location": "Hof"}))
        # Geraet A (kennt noch keinen Stand) speichert
        a = await B.update_bestand(vid, B.BestandUpdateIn(location="Halle 1", stand=""), w.chef)
        # Geraet B hat die Akte vorher geladen (auch ohne Stand) -> 409
        with pytest.raises(HTTPException) as e:
            await B.update_bestand(vid, B.BestandUpdateIn(notes="von B", stand=""), w.chef)
        # Mit dem aktuellen Stand klappt es
        b = await B.update_bestand(
            vid, B.BestandUpdateIn(notes="von B", stand=a["bestand"]["stand"]), w.chef)
        # Alte Oberflaeche ohne Stand: wie bisher
        c = await B.update_bestand(vid, B.BestandUpdateIn(location="Halle 2"), w.chef)
        v = await w.db.vehicles.find_one({"id": vid}, {"_id": 0})
        return a, _status(e), e.value.detail, b, c, v

    a, status, text, b, c, v = w.run(lauf())
    assert a["bestand"]["stand"] and status == 409 and "neu laden" in text
    assert "abgeschlossen" not in text, "eigene Meldung fuer das Rennen"
    assert b["bestand"]["stand"] != a["bestand"]["stand"]
    assert v["bestand"]["location"] == "Halle 2" and v["bestand"]["notes"] == "von B"
    assert v["bestand"]["stand"] == c["bestand"]["stand"]


# =============================================== RP-276a
def test_07_entfernen_sieht_termin_ueber_kaufvorgang(welt):
    B = _module("routes.bestand")
    w = welt
    vid = f"v_kv_{w.s}"

    async def lauf():
        await w.db.vehicles.insert_one(_fahrzeug(w, vid, lifecycle="abholung_geplant",
                                                 owner_user_id=w.sucher["id"]))
        await w.db.kaufvorgaenge.insert_one({"id": f"k_{w.s}", "dealer_id": w.dealer_id,
                                             "vehicle_id": vid, "user_id": w.sucher["id"],
                                             "status": "offen", "created_at": _jetzt()})
        # Termin vom Chef angelegt, Vertrag geloescht: haengt nur am Vorgang
        await w.db.appointments.insert_one({"id": f"t_{w.s}", "dealer_id": w.dealer_id,
                                            "vehicle_id": vid, "status": "offen",
                                            "created_by": w.chef["id"], "contract_id": None,
                                            "kaufvorgang_id": f"k_{w.s}", "created_at": _jetzt()})
        with pytest.raises(HTTPException) as e:
            await B.vehicle_fuer_sucher_entfernen(vid, w.sucher)
        v = await w.db.vehicles.find_one({"id": vid}, {"_id": 0})
        return _status(e), v

    status, v = w.run(lauf())
    assert status == 409 and v["owner_user_id"] == w.sucher["id"]


# =============================================== RP-483
def test_08_akte_historie_mit_vertrag_protokoll_inserat(welt):
    B = _module("routes.bestand")
    w = welt
    vid = f"v_hist_{w.s}"
    cid, pid, lid, tid = f"c_{w.s}", f"p_{w.s}", f"l_{w.s}", f"t_{w.s}"

    async def lauf():
        await w.db.vehicles.insert_one(_fahrzeug(w, vid, lifecycle="verkaufsentwurf",
                                                 owner_user_id=w.sucher["id"]))
        await w.db.generated_pdfs.insert_one({"id": cid, "dealer_id": w.dealer_id,
                                              "vehicle_id": vid, "user_id": w.sucher["id"],
                                              "contract_no": "KV-1", "created_at": _jetzt()})
        await w.db.appointments.insert_one({"id": tid, "dealer_id": w.dealer_id,
                                            "vehicle_id": vid, "status": "abgeholt",
                                            "created_by": w.sucher["id"], "contract_id": cid,
                                            "created_at": _jetzt()})
        await w.db.pickup_protocols.insert_one({"id": pid, "dealer_id": w.dealer_id,
                                                "vehicle_id": vid, "appointment_id": tid,
                                                "status": "zur_freigabe", "version": 1})
        await w.db.resale_listings.insert_one(_inserat(w, lid, vid, "entwurf"))
        eintraege = [("pdf.gesendet.email", cid, w.sucher["id"]),
                     ("protokoll.zur_freigabe", pid, "fahrer"),
                     ("inserat.entwurf", lid, w.chef["id"]),
                     ("fahrzeug.status.abgeholt", vid, w.chef["id"])]
        await w.db.activity_logs.insert_many([
            {"id": f"a{i}_{w.s}", "dealer_id": w.dealer_id, "user_id": u, "action": a,
             "ref": ref, "meta": {"x": 1}, "created_at": _jetzt()}
            for i, (a, ref, u) in enumerate(eintraege)])
        chef = await B.vehicle_akte(vid, w.chef)
        sucher = await B.vehicle_akte(vid, w.sucher)
        return chef, sucher

    chef, sucher = w.run(lauf())
    chef_aktionen = {h["action"] for h in chef["history"]}
    assert {"pdf.gesendet.email", "protokoll.zur_freigabe", "inserat.entwurf"} <= chef_aktionen
    sucher_aktionen = {h["action"] for h in sucher["history"]}
    assert {"pdf.gesendet.email", "protokoll.zur_freigabe"} <= sucher_aktionen
    assert "inserat.entwurf" not in sucher_aktionen, "Inserate sind Chefsache"
    assert "fahrzeug.status.abgeholt" not in sucher_aktionen, "fremde Aktionen am Fahrzeug nicht"
    assert all("meta" not in h and "user_id" not in h for h in sucher["history"])


# =============================================== RP-475
def test_09_schluessel_abweichung_wird_mangel(welt):
    B = _module("routes.bestand")
    w = welt
    vid = f"v_keys_{w.s}"

    async def lauf():
        await w.db.vehicles.insert_one(_fahrzeug(w, vid, lifecycle="bestand"))
        await w.db.appointments.insert_one({"id": f"t_{w.s}", "dealer_id": w.dealer_id,
                                            "vehicle_id": vid, "status": "abgeholt",
                                            "pickup_date": "2026-09-20", "created_at": _jetzt()})
        await w.db.pickup_reports.insert_one({
            "id": f"r_{w.s}", "appointment_id": f"t_{w.s}", "vehicle_id": vid,
            "dealer_id": w.dealer_id, "mileage_at_pickup": 88000, "keys_count": 1,
            "deviations": [{"id": "d1", "field": "keys", "label": "Schlüssel fehlt",
                            "expected": "2", "actual": "1"}],
            "version": 1, "superseded": False, "created_at": _jetzt()})
        r = await B.apply_deviations(vid, B.ApplyDeviationsIn(deviation_ids=["d1"]), w.chef)
        v = await w.db.vehicles.find_one({"id": vid}, {"_id": 0})
        return r, v

    r, v = w.run(lauf())
    assert v["data"]["keys_count"] == "1"
    assert "Schlüssel: 1 (erwartet 2)" in v["known_defects"]
    assert len(r["applied"]) == 1 and r["applied"][0]["mangel"] == "Schlüssel: 1 (erwartet 2)"


# =============================================== Quelltext-Wächter
def test_10_keine_bilder_von_fremden_servern():
    """RP-566: Startseite und Anmeldung luden Bilder von emergentagent.com und
    pexels.com — beide nicht in der Datenschutzerklärung."""
    treffer = []
    for p in FRONTEND_SRC.rglob("*.js*"):
        text = p.read_text(encoding="utf-8", errors="ignore")
        for host in ("://static.prod-images.emergentagent.com", "://images.pexels.com"):
            if host in text:
                treffer.append(f"{p.relative_to(FRONTEND_SRC).as_posix()}: {host}")
    assert not treffer, treffer


def test_11_hsl_tripel_nie_direkt_als_farbe():
    """RP-024/RP-274: --card, --border, --accent … sind HSL-Tripel
    ("0 0% 8%") für shadcn. var(--card) direkt als Farbe ist ungültiges CSS
    (durchsichtig). Nur hsl(var(--…)) ist richtig."""
    css = "\n".join(p.read_text(encoding="utf-8", errors="ignore")
                    for p in FRONTEND_SRC.rglob("*.css"))
    tripel = set(re.findall(
        r"(--[a-zA-Z0-9-]+)\s*:\s*\d+(?:\.\d+)?\s+\d+(?:\.\d+)?%\s+\d+(?:\.\d+)?%\s*;", css))
    assert {"--card", "--border", "--accent"} <= tripel, "Liste leer — Pfad falsch?"
    fehlt = []
    for p in FRONTEND_SRC.rglob("*.jsx"):
        if ".test." in p.name:
            continue
        for nr, zeile in enumerate(p.read_text(encoding="utf-8", errors="ignore").splitlines(), 1):
            for m in re.finditer(r"var\((--[a-zA-Z0-9-]+)", zeile):
                if m.group(1) in tripel and not zeile[:m.start()].endswith("hsl("):
                    fehlt.append(f"{p.relative_to(FRONTEND_SRC).as_posix()}:{nr} {m.group(1)}")
    assert not fehlt, fehlt


def test_12_oberflaeche_nutzt_die_neuen_wege():
    """Quelltext: ein Aufruf fürs Weiterverkaufen, Stand und deutscher Parser
    in der Akte, Abmeldung ohne Umleitung, kein active_profile beim Speichern."""
    akte = (FRONTEND_SRC / "pages" / "app" / "FahrzeugAkte.jsx").read_text(encoding="utf-8")
    bestand = (FRONTEND_SRC / "pages" / "app" / "Bestand.jsx").read_text(encoding="utf-8")
    for seite in (akte, bestand):
        teil = seite[seite.index("const decide = async"):]
        teil = teil[:teil.index("/decision`")]
        assert "/resale/draft/" in teil, "verkaufsentwurf ruft direkt create_draft"
        assert "von_lifecycle" in seite
    assert '<input type="number"' not in akte and "parseFloat(" not in akte
    assert "kostenLesen(" in akte and "stand: bestandForm.stand" in akte
    assert "parseFloat(" not in bestand and "parseInt(" not in bestand
    assert 'type="number"' not in bestand.split("function ManualVehicleDialog")[1]
    api = (FRONTEND_SRC / "lib" / "api.js").read_text(encoding="utf-8")
    assert "if (loestAbmeldungAus(url))" in api
    einst = (FRONTEND_SRC / "pages" / "app" / "Einstellungen.jsx").read_text(encoding="utf-8")
    assert "speicherPayload(form, ausgangRef.current, { istChef })" in einst
    assert '"/dealer/settings/zuruecksetzen", { felder: eigene }' in einst
