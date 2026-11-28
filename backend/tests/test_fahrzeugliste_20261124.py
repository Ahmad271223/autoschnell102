# -*- coding: utf-8 -*-
"""Wunsch Ahmad 24.11.2026 — Fahrzeugliste (Bestand):

  * Filter "Gekauft im Zeitraum" (Heute, Diese/Letzte Woche, Dieser/Letzter
    Monat, eigener Zeitraum) in Europe/Berlin, Woche ab Montag, serverseitig;
  * Anzahl und "Einkauf gesamt" ueber ALLE Treffer (nicht nur die Seite),
    nur fuer Rollen, die Kaufpreise sehen (Chef; Sucher nur eigene);
  * Sammeländerung des Status: jedes Fahrzeug einzeln geprueft (Firma,
    Recht, erlaubter Uebergang), Obergrenze, geaendert/uebersprungen mit
    Grund, Verlauf je Fahrzeug wie bei der Einzelaenderung.

In-Prozess gegen eine Wegwerf-DB (DB_NAME bzw. autoschnell_fl_<zufall>); kein Server.
"""
import asyncio
import importlib
import sys
import uuid
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import pytest
from fastapi import HTTPException
from pydantic import ValidationError

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

MONGO_URL = "mongodb://127.0.0.1:27017"


def _iso(d: datetime) -> str:
    return d.astimezone(timezone.utc).isoformat()


def _jetzt():
    return datetime.now(timezone.utc).isoformat()


def _modul(name):
    return importlib.import_module(name)


@pytest.fixture
def welt():
    from motor.motor_asyncio import AsyncIOMotorClient
    s = uuid.uuid4().hex[:10]
    namen = ["deps", "routes.bestand", "routes.contracts", "kaufvorgang", "lifecycle",
             "cleanup_service"]
    mods = [_modul(n) for n in namen]
    alt = [(m, getattr(m, "db", None)) for m in mods]

    class _W:
        pass

    w = _W()
    w.s = s
    w.dealer_id = f"d_fl_{s}"
    w.fremd_id = f"d_flx_{s}"
    w.chef = {"id": f"chef_fl_{s}", "dealer_id": w.dealer_id, "role": "dealer"}
    w.a = {"id": f"sa_fl_{s}", "dealer_id": w.dealer_id, "role": "sucher"}
    w.b = {"id": f"sb_fl_{s}", "dealer_id": w.dealer_id, "role": "sucher"}
    w.loop = asyncio.new_event_loop()
    asyncio.set_event_loop(w.loop)
    w.client = AsyncIOMotorClient(MONGO_URL, serverSelectionTimeoutMS=5000)
    w.db_name = f"autoschnell_fl_{s}"
    w.db = w.client[w.db_name]
    for m in mods:
        if hasattr(m, "db"):
            m.db = w.db
    w.run = lambda coro: w.loop.run_until_complete(coro)
    w.run(w.db.users.insert_many([
        {**w.chef, "active": True, "first_name": "Chef", "last_name": "C", "created_at": _jetzt()},
        {**w.a, "active": True, "first_name": "Anna", "last_name": "A", "created_at": _jetzt()},
        {**w.b, "active": True, "first_name": "Ben", "last_name": "B", "created_at": _jetzt()}]))
    w.run(w.db.dealers.insert_many([
        {"id": w.dealer_id, "user_id": w.chef["id"], "company_name": "Firma FL",
         "created_at": _jetzt()},
        {"id": w.fremd_id, "user_id": "chef_fremd", "company_name": "Fremd",
         "created_at": _jetzt()}]))
    yield w
    try:
        w.run(w.client.drop_database(w.db_name))
    finally:
        for m, d in alt:
            if d is not None:
                m.db = d
        w.client.close()
        w.loop.close()


def _fahrzeug(w, name, besitzer=None, *, dealer_id=None, lifecycle="abgeholt", **extra):
    doc = {"id": f"v_{name}_{w.s}", "dealer_id": dealer_id or w.dealer_id,
           "owner_user_id": (besitzer or w.chef)["id"],
           "lifecycle": lifecycle, "status": "verglichen", "source": "plattform",
           "data": {"make_label": "VW", "model_label": name, "images": ["https://x/1.jpg"]},
           "created_at": _jetzt(), "updated_at": _jetzt(), "lifecycle_changed_at": _jetzt()}
    doc.update(extra)
    return doc


def _vorgang(w, fahrzeug, von, *, preis=10000, status="abgeholt", am=None):
    cid = f"c_{fahrzeug['id']}_{von['id']}_{uuid.uuid4().hex[:4]}"
    am = am or _jetzt()
    vertrag = {"id": cid, "dealer_id": fahrzeug["dealer_id"], "user_id": von["id"],
               "vehicle_id": fahrzeug["id"], "contract_no": "FL-1", "send_status": [],
               "status": "erstellt", "created_at": am}
    vorgang = {"id": f"k_{cid}", "dealer_id": fahrzeug["dealer_id"], "user_id": von["id"],
               "vehicle_id": fahrzeug["id"], "contract_id": cid, "status": status,
               "purchase_price": preis, "created_at": am, "updated_at": am}
    return vertrag, vorgang


async def _anlegen(w, fahrzeuge=(), paare=()):
    if fahrzeuge:
        await w.db.vehicles.insert_many([dict(f) for f in fahrzeuge])
    for vertrag, vorgang in paare:
        await w.db.generated_pdfs.insert_one(dict(vertrag))
        await w.db.kaufvorgaenge.insert_one(dict(vorgang))


# =====================================================================
#                 Zeitraum-Grenzen (Europe/Berlin)
# =====================================================================
def _grenzen(name, jetzt, **k):
    B = _modul("routes.bestand")
    return B.zeitraum_grenzen(name, jetzt=jetzt, **k)


def _utc(*a):
    return datetime(*a, tzinfo=timezone.utc)


def test_01_heute_nach_mitternacht_in_berlin():
    # 23:30 UTC am 23.11. = 00:30 Berlin am 24.11. -> "heute" ist der 24.
    a, e = _grenzen("heute", _utc(2026, 11, 23, 23, 30))
    assert a == _utc(2026, 11, 23, 23, 0) and e == _utc(2026, 11, 24, 23, 0)
    # Sommerzeit: Mitternacht Berlin = 22:00 UTC
    a, e = _grenzen("heute", _utc(2026, 7, 1, 12))
    assert a == _utc(2026, 6, 30, 22) and e == _utc(2026, 7, 1, 22)


def test_02_woche_beginnt_montag():
    # Do 26.11.2026 -> Woche Mo 23.11. bis Mo 30.11. (Berlin-Mitternacht)
    a, e = _grenzen("diese_woche", _utc(2026, 11, 26, 10))
    assert a == _utc(2026, 11, 22, 23) and e == _utc(2026, 11, 29, 23)
    a, e = _grenzen("letzte_woche", _utc(2026, 11, 26, 10))
    assert a == _utc(2026, 11, 15, 23) and e == _utc(2026, 11, 22, 23)
    # Sonntag 23:30 Berlin gehoert noch zur alten Woche, Montag 00:10 zur neuen
    a, _ = _grenzen("diese_woche", _utc(2026, 11, 29, 22, 30))
    assert a == _utc(2026, 11, 22, 23)
    a, _ = _grenzen("diese_woche", _utc(2026, 11, 29, 23, 10))
    assert a == _utc(2026, 11, 29, 23)
    # Woche ueber die Zeitumstellung (25.10.2026): Anfang Sommer-, Ende Winterzeit
    a, e = _grenzen("diese_woche", _utc(2026, 10, 22, 12))
    assert a == _utc(2026, 10, 18, 22) and e == _utc(2026, 10, 25, 23)


def test_03_monatswechsel_und_jahreswechsel():
    # 01.12. 00:30 Berlin (= 30.11. 23:30 UTC): schon Dezember
    a, e = _grenzen("dieser_monat", _utc(2026, 11, 30, 23, 30))
    assert a == _utc(2026, 11, 30, 23) and e == _utc(2026, 12, 31, 23)
    a, e = _grenzen("letzter_monat", _utc(2026, 11, 30, 23, 30))
    assert a == _utc(2026, 10, 31, 23) and e == _utc(2026, 11, 30, 23)
    # letzter Monat Oktober: Anfang Sommerzeit (22 UTC), Ende Winterzeit (23 UTC)
    a, e = _grenzen("letzter_monat", _utc(2026, 11, 10, 12))
    assert a == _utc(2026, 9, 30, 22) and e == _utc(2026, 10, 31, 23)
    # Januar -> letzter Monat ist Dezember des Vorjahres
    a, e = _grenzen("letzter_monat", _utc(2027, 1, 15, 12))
    assert a == _utc(2026, 11, 30, 23) and e == _utc(2026, 12, 31, 23)
    # Februar im Schaltjahr
    a, e = _grenzen("dieser_monat", _utc(2028, 2, 10, 12))
    assert e - a == timedelta(days=29)


def test_04_eigener_zeitraum_und_fehler():
    a, e = _grenzen("eigener", _utc(2026, 11, 24), von="2026-11-01", bis="2026-11-30")
    assert a == _utc(2026, 10, 31, 23) and e == _utc(2026, 11, 30, 23), "bis einschliesslich"
    a, e = _grenzen("eigener", _utc(2026, 11, 24), von="2026-11-01")
    assert e is None
    a, e = _grenzen("eigener", _utc(2026, 11, 24), bis="2026-11-01")
    assert a is None and e == _utc(2026, 11, 1, 23)
    for k in ({}, {"von": "2026-11-30", "bis": "2026-11-01"}, {"von": "kaputt"},
              {"von": "2000-01-01", "bis": "2026-01-01"}):
        with pytest.raises(HTTPException) as exc:
            _grenzen("eigener", _utc(2026, 11, 24), **k)
        assert exc.value.status_code == 422
    with pytest.raises(HTTPException) as exc:
        _grenzen("gestern", _utc(2026, 11, 24))
    assert exc.value.status_code == 422
    assert _grenzen(None, _utc(2026, 11, 24)) is None


# =====================================================================
#              Liste: Zeitraum-Filter, Summe, Rechte
# =====================================================================
def test_05_zeitraum_filter_grenze_mitternacht_berlin(welt):
    """Kauf am 01.11. 00:30 Berlin (= 31.10. 23:30 UTC) zaehlt zum November,
    Kauf am 31.10. 23:30 Berlin (= 31.10. 22:30 UTC) zum Oktober."""
    w = welt
    B = _modul("routes.bestand")
    nov = _fahrzeug(w, "nov")
    okt = _fahrzeug(w, "okt")
    man = _fahrzeug(w, "man", lifecycle="bestand", source="manuell",
                    created_at=_iso(_utc(2026, 11, 15, 9)), purchase_price=5000)

    async def lauf():
        await _anlegen(w, [nov, okt, man], [
            _vorgang(w, nov, w.chef, preis=1000, am=_iso(_utc(2026, 10, 31, 23, 30))),
            _vorgang(w, okt, w.chef, preis=2000, am=_iso(_utc(2026, 10, 31, 22, 30)))])
        november = await B.list_bestand(w.chef, zeitraum="eigener",
                                        von="2026-11-01", bis="2026-11-30")
        oktober = await B.list_bestand(w.chef, zeitraum="eigener",
                                       von="2026-10-01", bis="2026-10-31")
        alle = await B.list_bestand(w.chef)
        return november, oktober, alle

    november, oktober, alle = w.run(lauf())
    assert {i["id"] for i in november["items"]} == {nov["id"], man["id"]}
    assert {i["id"] for i in oktober["items"]} == {okt["id"]}
    assert november["zusammenfassung"] == {"anzahl": 2, "einkauf_gesamt": 6000.0,
                                           "ohne_preis": 0}
    assert november["counts"] == {"abgeholt": 1, "bestand": 1}, "Zaehler folgen dem Zeitraum"
    assert alle["zusammenfassung"]["anzahl"] == 3 and alle["counts"]["abgeholt"] == 2
    kauf = {i["id"]: i["gekauft_am"] for i in alle["items"]}
    assert kauf[nov["id"]].startswith("2026-10-31T23:30"), "Kaufdatum = Vorgang"
    assert kauf[man["id"]].startswith("2026-11-15T09"), "manuell: Anlagedatum"


def test_06_summe_ueber_alle_treffer_nicht_nur_die_seite(welt):
    w = welt
    B = _modul("routes.bestand")
    fahrzeuge = [_fahrzeug(w, f"s{i}") for i in range(7)]
    paare = [_vorgang(w, f, w.chef, preis=1000 * (i + 1)) for i, f in enumerate(fahrzeuge)]
    # realisierter Preis am Fahrzeug geht vor dem Vertragspreis
    fahrzeuge[0]["purchase_price"] = 1500
    storno = _fahrzeug(w, "storno", lifecycle="storniert")
    ohne = _fahrzeug(w, "ohne")

    async def lauf():
        await _anlegen(w, [*fahrzeuge, storno, ohne],
                       [*paare, _vorgang(w, storno, w.chef, preis=99999, status="nicht_abgeholt"),
                        _vorgang(w, ohne, w.chef, preis=None)])
        s1 = await B.list_bestand(w.chef, seite=1, je_seite=2)
        s5 = await B.list_bestand(w.chef, seite=5, je_seite=2)
        return s1, s5

    s1, s5 = w.run(lauf())
    erwartet = 1500 + sum(1000 * (i + 1) for i in range(1, 7))
    for s in (s1, s5):
        assert s["gesamt"] == 9 and s["zusammenfassung"]["anzahl"] == 9
        assert s["zusammenfassung"]["einkauf_gesamt"] == erwartet, "storniert zaehlt nicht"
        assert s["zusammenfassung"]["ohne_preis"] == 1
        assert s["gekuerzt"] is False and s["je_seite"] == 2
    assert len(s1["items"]) == 2 and len(s5["items"]) == 1 and s5["seite"] == 5


def test_07_sucher_summe_nur_eigene_preise(welt):
    w = welt
    B = _modul("routes.bestand")
    eigen = _fahrzeug(w, "eigen", w.a)
    geteilt = _fahrzeug(w, "geteilt", w.a, mitbearbeiter_ids=[w.b["id"]], purchase_price=777)
    kollege = _fahrzeug(w, "kollege", w.b)

    async def lauf():
        kv_a = _vorgang(w, geteilt, w.a, preis=3000, status="vertrag_erstellt")
        kv_b = _vorgang(w, geteilt, w.b, preis=4000)
        await _anlegen(w, [eigen, geteilt, kollege], [
            _vorgang(w, eigen, w.a, preis=2000), kv_a, kv_b,
            _vorgang(w, kollege, w.b, preis=8000)])
        # der realisierte Preis 777 stammt aus dem Vorgang des Kollegen
        await w.db.vehicles.update_one({"id": geteilt["id"]},
                                       {"$set": {"abgeholt_kaufvorgang_id": kv_b[1]["id"]}})
        return await B.list_bestand(w.a), await B.list_bestand(w.chef)

    a, chef = w.run(lauf())
    assert {i["id"] for i in a["items"]} == {eigen["id"], geteilt["id"]}
    assert a["zusammenfassung"]["einkauf_gesamt"] == 5000.0, "nur eigene Vertragspreise"
    assert all(i.get("purchase_price") != 777 for i in a["items"]), "kein Kollegenpreis"
    assert chef["zusammenfassung"]["einkauf_gesamt"] == 2000 + 777 + 8000


def test_08_betrag_fehlt_fuer_rollen_ohne_recht(welt):
    w = welt
    B = _modul("routes.bestand")
    deps = _modul("deps")
    f = _fahrzeug(w, "x", purchase_price=12345)
    fremde_rolle = {"id": "fahrer_x", "dealer_id": w.dealer_id, "role": "driver"}

    async def lauf():
        await _anlegen(w, [f], [_vorgang(w, f, w.chef, preis=12345)])
        return await B.list_bestand(fremde_rolle)

    r = w.run(lauf())
    assert r["zusammenfassung"] == {"anzahl": 1}, "kein einkauf_gesamt, kein ohne_preis"
    assert "purchase_price" not in r["items"][0]
    assert B.einkaufspreis_sichtbar(w.chef) and B.einkaufspreis_sichtbar(w.a)
    # ...und ueber HTTP kommt eine solche Rolle gar nicht erst in die Liste
    with pytest.raises(HTTPException) as exc:
        w.run(deps.current_firma(fremde_rolle))
    assert exc.value.status_code == 403


# =====================================================================
#                          Sammeländerung
# =====================================================================
def _audit(w, vid, aktion):
    return w.run(w.db.activity_logs.count_documents(
        {"dealer_id": w.dealer_id, "ref": vid, "action": aktion}))


def test_09_sammel_prueft_jedes_fahrzeug_einzeln(welt):
    w = welt
    B = _modul("routes.bestand")
    abgeholt = _fahrzeug(w, "abg")
    im_bestand = _fahrzeug(w, "best", lifecycle="bestand")
    verkauft = _fahrzeug(w, "verk", lifecycle="verkauft")
    fremd = _fahrzeug(w, "fremd", dealer_id=w.fremd_id)
    w.run(_anlegen(w, [abgeholt, im_bestand, verkauft, fremd]))

    r = w.run(B.bestand_status_sammel(B.SammelStatusIn(
        ids=[abgeholt["id"], abgeholt["id"], fremd["id"], verkauft["id"], "gibt_es_nicht"],
        ziel="bestand"), w.chef))
    assert [g["id"] for g in r["geaendert"]] == [abgeholt["id"]], "doppelte ID nur einmal"
    gruende = {u["id"]: u["grund"] for u in r["uebersprungen"]}
    assert "nicht gefunden" in gruende[fremd["id"]]
    assert "nicht gefunden" in gruende["gibt_es_nicht"]
    assert "nicht erlaubt" in gruende[verkauft["id"]]
    assert "„Verkauft“" in gruende[verkauft["id"]], "Grund nennt den Zustand auf Deutsch"
    v = w.run(w.db.vehicles.find_one({"id": abgeholt["id"]}))
    assert v["lifecycle"] == "bestand" and v["bestand"]["expires_at"]
    fr = w.run(w.db.vehicles.find_one({"id": fremd["id"]}))
    assert fr["lifecycle"] == "abgeholt", "fremde Firma unberuehrt"
    # gleicher Verlauf wie die Einzelentscheidung "Nur speichern"
    assert _audit(w, abgeholt["id"], "fahrzeug.status.bestand") == 1
    assert _audit(w, abgeholt["id"], "fahrzeug.entscheidung.bestand") == 1
    assert _audit(w, verkauft["id"], "fahrzeug.status.bestand") == 0
    sammel = w.run(w.db.activity_logs.find_one({"action": "fahrzeug.sammelaenderung"}))
    assert sammel["ref"] == r["sammel_id"] and sammel["meta"]["geaendert"] == [abgeholt["id"]]

    # "bestand" auf ein Fahrzeug im Bestand = Frist erneuern (wie Einzelweg)
    r = w.run(B.bestand_status_sammel(B.SammelStatusIn(ids=[im_bestand["id"]], ziel="bestand"),
                                      w.chef))
    assert r["geaendert"][0]["verlaengert"] is True
    assert _audit(w, im_bestand["id"], "fahrzeug.bestand.verlaengert") == 1


def test_10_archivieren_und_storno_ohne_abkuerzung(welt):
    w = welt
    B = _modul("routes.bestand")
    best = _fahrzeug(w, "arch", lifecycle="bestand")
    nicht = _fahrzeug(w, "nicht", lifecycle="nicht_abgeholt")
    offen = _fahrzeug(w, "offen", lifecycle="gekauft")
    termin = _fahrzeug(w, "termin", lifecycle="nicht_abgeholt")
    live = _fahrzeug(w, "live", lifecycle="verkaufsbereit")

    async def lauf():
        await _anlegen(w, [best, nicht, offen, termin, live],
                       [_vorgang(w, offen, w.chef, status="vertrag_erstellt")])
        await w.db.appointments.insert_one({"id": f"t_{w.s}", "dealer_id": w.dealer_id,
                                            "vehicle_id": termin["id"], "status": "offen"})
        await w.db.resale_listings.insert_one({"id": f"l_{w.s}", "dealer_id": w.dealer_id,
                                               "vehicle_id": live["id"],
                                               "status": "verkaufsbereit"})
        arch = await B.bestand_status_sammel(B.SammelStatusIn(
            ids=[best["id"], nicht["id"]], ziel="archiviert"), w.chef)
        storno = await B.bestand_status_sammel(B.SammelStatusIn(
            ids=[nicht["id"], offen["id"], termin["id"]], ziel="storniert"), w.chef)
        zurueck = await B.bestand_status_sammel(B.SammelStatusIn(
            ids=[live["id"]], ziel="bestand"), w.chef)
        return arch, storno, zurueck

    arch, storno, zurueck = w.run(lauf())
    assert [g["id"] for g in arch["geaendert"]] == [best["id"]]
    assert "nicht erlaubt" in arch["uebersprungen"][0]["grund"], "nicht_abgeholt -> archiviert"
    a = w.run(w.db.vehicles.find_one({"id": best["id"]}))
    assert a["lifecycle"] == "archiviert" and a["data"]["images"] == [] and a["archived_at"]
    assert "archiv_aufraeumen_offen" not in a, "Nebenaufraeumen lief sofort"
    assert _audit(w, best["id"], "fahrzeug.status.archiviert") == 1
    assert _audit(w, best["id"], "fahrzeug.archiviert.hand") == 1

    assert [g["id"] for g in storno["geaendert"]] == [nicht["id"]]
    gruende = {u["id"]: u["grund"] for u in storno["uebersprungen"]}
    assert "Kaufvertrag läuft" in gruende[offen["id"]], "kein Storno an offenem Vertrag vorbei"
    assert "Abholtermin offen" in gruende[termin["id"]]
    assert w.run(w.db.vehicles.find_one({"id": offen["id"]}))["lifecycle"] == "gekauft"
    assert _audit(w, nicht["id"], "fahrzeug.status.storniert") == 1

    assert zurueck["geaendert"] == [] and "Inserat" in zurueck["uebersprungen"][0]["grund"]


def test_11_obergrenze_und_nur_chef(welt):
    w = welt
    B = _modul("routes.bestand")
    deps = _modul("deps")
    assert B.SAMMEL_MAX == 500
    B.SammelStatusIn(ids=[f"v{i}" for i in range(500)], ziel="archiviert")
    with pytest.raises(ValidationError):
        B.SammelStatusIn(ids=[f"v{i}" for i in range(501)], ziel="archiviert")
    with pytest.raises(ValidationError):
        B.SammelStatusIn(ids=[], ziel="archiviert")
    for ziel in ("abgeholt", "verkauft", "gekauft", "geloescht"):
        with pytest.raises(ValidationError):
            B.SammelStatusIn(ids=["v1"], ziel=ziel)
    # Route haengt an current_haendler -> current_chef: Sucher 403
    with pytest.raises(HTTPException) as exc:
        w.run(deps.current_chef(w.a))
    assert exc.value.status_code == 403
    import inspect
    for fn in (B.bestand_status_sammel, B.bestand_ids):
        assert "current_haendler" in str(inspect.signature(fn).parameters["user"].default)


def test_12_alle_treffer_ids_mit_filter_und_deckel(welt, monkeypatch):
    w = welt
    B = _modul("routes.bestand")
    fz = [_fahrzeug(w, f"i{i}") for i in range(5)]
    alt = _fahrzeug(w, "alt")

    async def lauf():
        await _anlegen(w, [*fz, alt], [*[_vorgang(w, f, w.chef) for f in fz],
                                       _vorgang(w, alt, w.chef, am=_iso(_utc(2020, 1, 5)))])
        heute = date.today().isoformat()
        r = await B.bestand_ids(w.chef, zeitraum="eigener", von="2025-01-01", bis=heute)
        monkeypatch.setattr(B, "SAMMEL_MAX", 3)
        gedeckelt = await B.bestand_ids(w.chef)
        return r, gedeckelt

    r, gedeckelt = w.run(lauf())
    assert set(r["ids"]) == {f["id"] for f in fz} and r["gesamt"] == 5 and not r["gekuerzt"]
    assert len(gedeckelt["ids"]) == 3 and gedeckelt["gesamt"] == 6 and gedeckelt["gekuerzt"]
