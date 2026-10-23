# -*- coding: utf-8 -*-
"""Master-Auftrag Ahmad 26.09.2026, Phase A — Fahrzeug-Masterliste (170 Zeilen).

Geprueft: Zeilen vollstaendig (119 neu / 51 alt = 70/30), keine EZ < 2012, Getriebe-Mapping
(nie 'alle'), jede Zeile aufgeloest oder needs_review mit Grund, km-Profile materialisiert,
Migration idempotent, Klassifikation UNCHANGED/CHANGED/NEW/DEPRECATED an Testdaten (Praefix test-),
Aktivieren ohne Testlauf -> 'erst Testlauf' auch fuer Seeds, filter_hash mit EZ/km.
Testdaten nur mit Praefix test-, Aufraeumen am Ende."""
import inspect
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))
from test_befunde_runde17_termine import _module, welt  # noqa: E402,F401

K = _module("markt.konfig")
ML = _module("markt.masterliste")
KAT = _module("markt.katalog")
A = _module("markt.auftraege")
SEG = _module("markt.segmente")
MIG = _module("migrationen")

FRONTEND = Path(__file__).resolve().parent.parent.parent / "frontend" / "src"


def _aufraeumen(welt):
    db = welt.db
    muster = "^(test|dbg)-"
    for coll in (K.MODELLE, K.SEGMENTE, K.JOBS, K.SEGMENTSTATS, K.TAGESSTATS):
        welt.run(db[coll].delete_many({"$or": [{"id": {"$regex": muster}}, {"model_id": {"$regex": muster}},
                                               {"segment_id": {"$regex": muster}}, {"_id": {"$regex": muster}}]}))


def test_01_zeilen_vollstaendig_und_verhaeltnis():
    zeilen = ML.MASTERLISTE
    assert len(zeilen) == 170 and [z["master_row"] for z in zeilen] == list(range(1, 171)), "keine Zeile fehlt, keine doppelt"
    neu = [z for z in zeilen if z["alter_gruppe"] == "neu"]
    alt = [z for z in zeilen if z["alter_gruppe"] == "alt"]
    assert len(neu) == 119 and len(alt) == 51 and round(len(neu) / 170, 2) == 0.7
    # keine EZ vor 2012, 'neu' ab 2018, 'alt' bis 2017; EZ-Jahre lueckenlos aufsteigend
    for z in zeilen:
        ez = z["ez_years"]
        assert ez == list(range(ez[0], ez[-1] + 1)) and 3 <= len(ez) <= 4, z["master_row"]
        assert min(ez) >= 2012, f"Zeile {z['master_row']}: EZ {min(ez)} < 2012"
        assert (min(ez) >= 2018) if z["alter_gruppe"] == "neu" else (max(ez) <= 2017), z["master_row"]
        assert z["fuel"] in ("PETROL", "DIESEL", "HYBRID", "HYBRID_DIESEL", "ELECTRICITY")
        assert z["km_profile"] in ML.KM_PROFILE
        assert (z["power_kw_min"] is None) == (z["power_kw_max"] is None)
        if z["power_kw_min"] is not None:
            assert 40 <= z["power_kw_min"] <= z["power_kw_max"] <= 400, z["master_row"]
    # Mercedes ohne kW (Bezeichnung = Motorisierung), ausser Vito (114/116 CDI waeren sonst ein Zwilling)
    for z in zeilen:
        if z["make"] == "Mercedes-Benz":
            assert (z["power_kw_min"] is None) == (z["model"] != "Vito"), z["master_row"]
    # Kraftstoff-Regeln: Diesel-Zeilen heissen TDI/CDI/dCi/EcoBlue/HDi/CRDi/Diesel/TDCi/CDTI/'d'; Elektro nur Enyaq/Model 3
    assert {z["model"] for z in zeilen if z["fuel"] == "ELECTRICITY"} == {"Enyaq", "Model 3"}
    assert all("hybrid" in z["variant"].lower() or z["variant"] == "330e" for z in zeilen if z["fuel"] == "HYBRID")


def test_02_km_profile_und_materialisierung():
    assert set(ML.KM_PROFILE) == {"K1", "K2", "D1", "D2", "D3", "E"}
    k2 = ML.km_buckets_fuer_profil("K2")
    assert k2 == [{"min_km": 20000, "max_km": 40000}, {"min_km": 40001, "max_km": 60000}, {"min_km": 60001, "max_km": 85000},
                  {"min_km": 85001, "max_km": 115000}, {"min_km": 115001, "max_km": 150000}, {"min_km": 150001, "max_km": 195000}]
    d3 = ML.km_buckets_fuer_profil("D3")
    assert d3[0]["min_km"] == 20000 and d3[-1]["max_km"] == 320000 and len(d3) == 6
    for profil in ML.KM_PROFILE:
        b = ML.km_buckets_fuer_profil(profil)
        assert all(x["min_km"] == y["max_km"] + 1 for x, y in zip(b[1:], b)) and A.km_bereiche_pruefen(b) == b
    # Profil je Antrieb: Elektro/Hybrid -> E, Benziner K1/K2, Diesel D1-D3
    for z in ML.MASTERLISTE:
        if z["fuel"] in ("ELECTRICITY", "HYBRID"):
            assert z["km_profile"] == "E", z["master_row"]
        elif z["fuel"] == "PETROL":
            # Ausnahme laut Ahmads Liste: Zeile 48 (Mercedes E 200 Benzin) mit Diesel-Profil D2 — bewusst uebernommen,
            # im Bericht als Rueckfrage (ALT -> VORSCHLAG -> GRUND), nicht still geaendert
            assert z["km_profile"] in ("K1", "K2") or (z["master_row"] == 48 and z["km_profile"] == "D2"), z["master_row"]
        else:
            assert z["km_profile"] in ("D1", "D2", "D3"), z["master_row"]


def test_03_getriebe_mapping_und_kennungen():
    ms = ML.master_modelle()
    assert all(m["gearbox"] in ("AUTOMATIC_GEAR", "MANUAL_GEAR") for m in ms), "nie 'alle'"
    schalter = [m["master_row"] for m in ms if m["gearbox"] == "MANUAL_GEAR"]
    assert len(schalter) == 34 and 2 in schalter and 1 not in schalter and 89 in schalter and 90 not in schalter
    # DSG/S tronic/DCT/EAT8/EDC/XTronic/S-CVT/9G-TRONIC sind Automatik — die Zeilen mit diesen Getrieben tragen AUTOMATIC_GEAR
    for nr in (5, 92, 96, 102, 108, 113, 90, 39):
        assert next(m for m in ms if m["master_row"] == nr)["gearbox"] == "AUTOMATIC_GEAR"
    ids = [m["id"] for m in ms]
    assert len(set(ids)) == 170 and all(len(i) <= 60 for i in ids)
    by = {m["master_row"]: m for m in ms}
    assert by[31]["id"] == "bmw-320d-auto-neu" and by[4]["id"] == "volkswagen-golf-2-0-tdi-schalt-neu" and by[123]["id"] == "volkswagen-golf-2-0-tdi-auto-alt"
    assert by[41]["id"] == "mercedes-benz-a-200-d-auto-neu" and by[6]["id"] != by[3]["id"]
    assert by[31]["label"] == "BMW 320d Automatik" and by[4]["label"] == "Volkswagen Golf 2.0 TDI Schaltung"
    assert all(m["rows"] == 5 and m["crawls_per_day"] == 1 and m["seed_version"] == 4 and m["status"] == "paused" and m["enabled"] is False for m in ms)
    assert all(m["km_buckets"] == ML.km_buckets_fuer_profil(m["km_profile"]) for m in ms)
    # start_modelle() liefert die Masterliste; der Altbestand v3 bleibt getrennt
    assert [m["id"] for m in KAT.start_modelle()] == ids and len(KAT.start_modelle_v3()) == 72 and KAT.SEED_VERSION == 4


def test_04_katalog_aufloesung_oder_needs_review():
    ms = ML.master_modelle()
    by = {m["master_row"]: m for m in ms}
    for m in ms:
        assert (m["make_id"] and m["model_id"]) or (m["needs_review"] and m["review_grund"]), m["master_row"]
        assert not m["needs_review"] or m["review_grund"], "needs_review immer mit Grund"
    assert all(m["model_id"] for m in ms), "alle 170 Zeilen im mobile.de-Katalog aufgeloest"
    # die in der GAP-Datei genannten Faelle
    assert by[96]["model_id"] == "26" and by[96]["model"] == "cee'd / Ceed"
    assert by[76]["model"] == "Grandland (X)" and by[89]["model"] == "Aygo (X)" and by[90]["model"] == "Aygo (X)" and by[88]["model"] == "RAV 4"
    assert by[41]["model"] == "A 200" and by[41]["fuel"] == "DIESEL" and by[148]["model"] == "E 220" and by[148]["fuel"] == "DIESEL"
    assert by[6]["model"] == "Golf" and by[6]["body"] == "EstateCar" and by[86]["model"] == "Corolla" and by[86]["body"] == "EstateCar"
    assert by[63]["model"] == "Enyaq" and by[29]["model"] == "218 Gran Coupé" and by[93]["model"] == "TUCSON"
    # needs_review-Zeilen: unsichere kW-Bereiche (breiter Bereich) mit Grund
    review = {m["master_row"] for m in ms if m["needs_review"]}
    assert {70, 81, 82, 115, 118, 130, 159} <= review and 31 not in review and 1 not in review
    assert all(by[nr]["power_kw_max"] - by[nr]["power_kw_min"] >= 25 for nr in (70, 82, 130, 159))
    # zwei Zeilen mit gleicher Definition und gleichen EZ-Jahren gibt es nicht (kein Zwilling im Sinne von auftraege)
    schluessel = [(A.definition_hash(m), tuple(m["ez_years"])) for m in ms]
    assert len(set(schluessel)) == 170


def _seed(w, nr, **extra):
    m = next(x for x in ML.master_modelle() if x["master_row"] == nr)
    return {**m, "id": f"test-ml-{w.s}-{nr}", "seed_version": 3, "master_row": None, **extra}


def test_05_klassifikation_unchanged_changed_new_deprecated(welt):
    w, db = welt.w, welt.db
    _aufraeumen(welt)
    s = w.s
    ms = ML.master_modelle()
    by = {m["master_row"]: m for m in ms}
    # reine Rechnung: gleiche Definition + EZ/km -> UNCHANGED; gleicher Schluessel, andere EZ -> CHANGED (neu vor alt);
    # ohne Gegenstueck -> DEPRECATED; uebrige Masterzeilen NEW
    unchanged = {**by[31], "id": "test-u"}
    unchanged.pop("master_row")
    changed = {**by[3], "id": "test-c", "ez_years": [2018, 2019, 2020, 2021, 2022], "km_buckets": list(K.KM_BUCKETS_STANDARD), "rows": 10}
    changed.pop("master_row")
    fremd = {"id": "test-d", "make": "BMW", "model": "530", "variant": "530d", "make_id": "3500", "model_id": "99", "fuel": "DIESEL",
             "gearbox": "AUTOMATIC_GEAR", "ez_years": [2019], "km_buckets": [{"min_km": 0, "max_km": 9}]}
    erg = ML.klassifizieren([unchanged, changed, fremd], ms)
    assert erg["zuordnung"] == {"test-u": {"status": "UNCHANGED", "master_row": 31}, "test-c": {"status": "CHANGED", "master_row": 3},
                                "test-d": {"status": "DEPRECATED", "master_row": None}}
    assert len(erg["neu"]) == 168 and 31 not in erg["neu"] and 3 not in erg["neu"] and 123 in erg["neu"], "Golf 2.0 TDI alt bleibt NEW"
    # in der Datenbank (Praefix test-): eigene Masterzeilen, Altbestand nur test-Dokumente
    master = [{**by[nr], "id": f"test-ml-{s}-m{nr}"} for nr in (3, 31, 123, 26)]
    u = _seed(w, 31, id=f"test-ml-{s}-u")
    u.pop("master_row")
    c = _seed(w, 3, id=f"test-ml-{s}-c", ez_years=[2018, 2019, 2020, 2021, 2022], km_buckets=list(K.KM_BUCKETS_STANDARD), rows=10,
              status="active", enabled=True, version=1, definition_hash="alt", hash_fassung=2)
    c.pop("master_row")
    d = {**fremd, "id": f"test-ml-{s}-d", "seed_version": 3, "status": "active", "enabled": True}
    # eigener Auftrag des Admins (ohne seed_version) mit derselben Definition wie Masterzeile 26 (BMW 118i) -> die neue
    # Masterzeile kommt als "zu pruefen" (kein stiller Zwilling), der eigene Auftrag bleibt unangetastet
    eigen = {k: v for k, v in by[26].items() if k not in ("seed_version", "master_row", "needs_review", "review_grund")}
    eigen.update({"id": f"test-ml-{s}-eigen", "label": "Mein 118i", "status": "paused", "enabled": False})
    welt.run(db[K.MODELLE].insert_many([dict(u), dict(c), dict(d), dict(eigen)]))
    welt.run(db[K.JOBS].insert_one({"id": f"test-ml-{s}-job", "segment_id": f"test-ml-{s}-c:2019:1-2", "model_id": c["id"], "tag": "2099-01-01",
                                    "status": "queued", "scheduled_at": "2099-01-01T00:00:00+00:00", "max_items": 10}))
    filt = {"id": {"$regex": f"^test-ml-{s}"}}
    try:
        z = welt.run(ML.importieren(db, synchronisieren=False, master=master, bestehende_filter=filt))
        assert z["unchanged"] == 1 and z["changed"] == 1 and z["deprecated"] == 1 and z["new"] == 2 and z["pausiert"] == 1 and z["jobs_storniert"] == 1
        du = welt.run(db[K.MODELLE].find_one({"id": u["id"]}, {"_id": 0}))
        assert du["master_status"] == "UNCHANGED" and du["master_row"] == 31 and du["seed_version"] == 4 and du["status"] == "paused"
        dc = welt.run(db[K.MODELLE].find_one({"id": c["id"]}, {"_id": 0}))
        assert dc["master_status"] == "CHANGED" and dc["master_row"] == 3 and dc["ez_years"] == [2021, 2022, 2023, 2024] and dc["rows"] == 5
        assert dc["km_profile"] == "D1" and dc["km_buckets"] == ML.km_buckets_fuer_profil("D1")
        assert dc["version"] == 2 and dc["status"] == "paused" and dc["enabled"] is False and "Testlauf" in dc["review_grund"], "neue Fassung, erst Testlauf"
        assert dc["definition_hash"] == A.definition_hash(dc) and dc["filter_hash"] == A.filter_hash(dc) and dc["hash_fassung"] == A.HASH_FASSUNG
        assert welt.run(db[K.JOBS].find_one({"id": f"test-ml-{s}-job"}, {"_id": 0}))["status"] == "cancelled"
        dd = welt.run(db[K.MODELLE].find_one({"id": d["id"]}, {"_id": 0}))
        assert dd["master_status"] == "DEPRECATED" and dd["status"] == "archived" and dd["enabled"] is False and dd["archived_at"]
        neu = welt.run(db[K.MODELLE].find({"id": {"$in": [f"test-ml-{s}-m123", f"test-ml-{s}-m26"]}}, {"_id": 0}).to_list(5))
        assert len(neu) == 2 and all(n["master_status"] == "NEW" and n["status"] == "paused" and n["enabled"] is False and n["version"] == 1 for n in neu)
        n26 = next(n for n in neu if n["id"] == f"test-ml-{s}-m26")
        assert n26["needs_review"] is True and "Mein 118i" in n26["review_grund"] and z["needs_review"] >= 1
        assert "master_status" not in welt.run(db[K.MODELLE].find_one({"id": eigen["id"]}, {"_id": 0})), "eigener Auftrag unangetastet"
        assert welt.run(db[K.MODELLE].count_documents({"id": {"$in": [f"test-ml-{s}-m3", f"test-ml-{s}-m31"]}})) == 0, "zugeordnete Zeilen nicht doppelt"
        # idempotent: zweiter Lauf aendert nichts
        z2 = welt.run(ML.importieren(db, synchronisieren=False, master=master, bestehende_filter=filt))
        assert (z2["unchanged"], z2["changed"], z2["deprecated"], z2["new"]) == (0, 0, 0, 0) and z2["schon"] == 4
        assert welt.run(db[K.MODELLE].find_one({"id": c["id"]}, {"_id": 0}))["version"] == 2
        # Aktivieren ohne Testlauf -> 'erst Testlauf' — auch fuer Seeds (Ausnahme entfernt)
        with pytest.raises(A.Ungueltig) as ex:
            welt.run(A.status_setzen(db, dc["id"], "active"))
        assert "erst Testlauf" in str(ex.value)
        with pytest.raises(A.Ungueltig):
            welt.run(A.status_setzen(db, f"test-ml-{s}-m26", "active"))
        # mit bestandenem Testlauf fuer genau diese Konfiguration (Filter + EZ + km) -> aktiv, 4 x 6 Segmente
        welt.run(db[K.MODELLE].update_one({"id": dc["id"]}, {"$set": {"testlauf_ok_at": K.jetzt_iso(), "testlauf_ok_hash": A.filter_hash(dc)}}))
        welt.run(A.status_setzen(db, dc["id"], "active"))
        assert welt.run(db[K.SEGMENTE].count_documents({"model_id": dc["id"], "enabled": True})) >= 24
        # EZ-/km-Aenderung am aktiven Auftrag verlangt einen neuen Testlauf (Fassung bleibt)
        with pytest.raises(A.Ungueltig) as ex:
            welt.run(A.aendern(db, dc["id"], {"ez_years": [2022, 2023]}))
        assert "erst Testlauf" in str(ex.value)
        assert welt.run(db[K.MODELLE].find_one({"id": dc["id"]}, {"_id": 0}))["version"] == 2
    finally:
        _aufraeumen(welt)


def test_06_filter_hash_mit_ez_km_und_fassung_unveraendert():
    e = {"make": "BMW", "model": "320", "variant": "320d", "fuel": "DIESEL", "gearbox": "AUTOMATIC_GEAR", "power_kw_min": 135, "power_kw_max": 150,
         "ez_years": [2021, 2022], "km_buckets": [{"min_km": 20000, "max_km": 55000}], "rows": 5}
    m = A.entwurf_pruefen(e)
    assert A.filter_hash(m) != A.filter_hash(A.entwurf_pruefen({**e, "ez_years": [2021]}))
    assert A.filter_hash(m) != A.filter_hash(A.entwurf_pruefen({**e, "km_buckets": [{"min_km": 20000, "max_km": 60000}]}))
    assert A.filter_hash(m) == A.filter_hash(A.entwurf_pruefen({**e, "rows": 9, "ez_years": [2022, 2021]})), "Zeilen und Reihenfolge egal"
    assert A.definition_hash(m) == A.definition_hash(A.entwurf_pruefen({**e, "ez_years": [2021]})), "Fassung bleibt bei EZ/km"
    assert A.HASH_FASSUNG == 3 and "ez_years" in A.TESTLAUF_FELDER and "ez_years" not in A.DEFINITION_FELDER
    # keine Seed-Ausnahme mehr im Quelltext, Testlauf muss alle Segmente decken
    q = inspect.getsource(A._testlauf_pruefen)
    assert "seed_version" not in q
    assert A.TESTLAUF_SEGMENTE_MAX == 40 and A.testlauf_bestanden({"gueltig_gesamt": 1, "verworfen_gesamt": 0, "sortierung_ungueltig": 0,
                                                                   "segmente_geprueft": 27, "segmente_gesamt": 28}) is False


def test_07_migration_route_und_oberflaeche():
    nummern = {n: name for n, name, _ in MIG.MIGRATIONEN}
    assert nummern[18] == "markt_masterliste_v4" and MIG.ZIEL_VERSION >= 18
    assert "masterliste.importieren" in inspect.getsource(MIG.m18_markt_masterliste_v4)
    r = (Path(__file__).resolve().parent.parent / "routes" / "markt_admin.py").read_text(encoding="utf-8")
    i = r.index('"/admin/market/masterliste/importieren"')
    assert "current_super_admin" in r[i:i + 300] and "masterliste.importieren" in r[i:i + 900]
    assert "needs_review" in r[r.index('"/admin/market/auftraege"'):r.index('"/admin/market/auftraege"') + 900]
    # Oberflaeche: keine Seed-Ausnahme beim Aktivieren, Filter needs_review, Import-Knopf, Masterstatus sichtbar
    jsx = (FRONTEND / "pages" / "admin_v2" / "MarktAuftraege.jsx").read_text(encoding="utf-8")
    zeile = next(l for l in jsx.splitlines() if l.startswith("const aktivierbar"))
    assert "seed_version" not in zeile
    for t in ("masterliste/importieren", "needs_review", "review_grund", "km_profile", "master_status", "auftraege-needs-review"):
        assert t in jsx, t
    assert "ez_years" in jsx.split("const MATERIELL")[1].split("\n")[0] and "km_buckets" in jsx.split("const MATERIELL")[1].split("\n")[0]


def test_08_sammel_testlauf_aktiviert_nur_bestandene(welt, monkeypatch):
    """Sammel-Testlauf: je pausiertem Masterlisten-Auftrag (ohne 'zu pruefen') EIN Testlauf ueber alle Segmente;
    bestandene werden aktiviert (Segmente entstehen), nicht bestandene bleiben pausiert mit Grund; ein zweiter
    Start waehrend eines Laufs -> LaeuftSchon; Budget wird reserviert/abgerechnet; Zeilen 'zu pruefen' bleiben aussen vor."""
    w, db = welt.w, welt.db
    _aufraeumen(welt)
    s = w.s
    APIFY = _module("markt.apify")
    BUD = _module("markt.budget")
    monkeypatch.setenv("MARKT_BUDGET_MONAT_USD", "100")
    monkeypatch.setenv("MARKT_APIFY_ACTOR_ERSATZ", "")
    monkeypatch.setenv("APIFY_TOKEN", "test-token")
    monkeypatch.setattr(K, "monat", lambda zeit=None: f"test-{s}")
    welt.run(db[K.BUDGET].delete_many({"_id": f"test-{s}"}))
    monkeypatch.setattr(SEG, "_nachplanen", lambda db: _leer())       # keine Jobs fuer fremde Demo-Segmente planen
    basis = {"make": "BMW", "model": "320", "variant": "320d", "make_id": "3500", "model_id": "10", "fuel": "DIESEL", "gearbox": "AUTOMATIC_GEAR",
             "ez_years": [2019], "km_buckets": [{"min_km": 20000, "max_km": 40000}], "rows": 5, "crawls_per_day": 1, "country": "DE",
             "status": "paused", "enabled": False, "seed_version": 4, "needs_review": False, "updated_at": "2026-09-26T20:00:00+00:00"}
    gut = {**basis, "id": f"test-ml-{s}-gut", "master_row": 9001, "label": "BMW 320d (gut)", "power_kw_min": 135, "power_kw_max": 150}
    schlecht = {**basis, "id": f"test-ml-{s}-schlecht", "master_row": 9002, "label": "BMW 320d (kW falsch)", "power_kw_min": 60, "power_kw_max": 70}
    review = {**basis, "id": f"test-ml-{s}-review", "master_row": 9003, "label": "BMW 320d (zu pruefen)", "needs_review": True, "power_kw_min": 135, "power_kw_max": 150}
    for m in (gut, schlecht, review):
        m.update({"definition_hash": A.definition_hash(m), "filter_hash": A.filter_hash(m), "hash_fassung": A.HASH_FASSUNG, "version": 1})
    welt.run(db[K.MODELLE].insert_many([dict(gut), dict(schlecht), dict(review)]))
    aufrufe = []

    async def _lauf(urls, max_items, zeitlimit_s=None, actor_name=None, max_items_per_query=None):
        aufrufe.append(list(urls))
        it = {"id": f"t{s}q{len(aufrufe)}", "url": "https://suchen.mobile.de/x", "make": "BMW", "model": "320", "title": "BMW 320d",
              "firstRegistration": "03/2019", "mileageKm": 25000, "power": "140 kW (190 PS)", "fuel": "Diesel", "gearbox": "Automatik",
              "priceGross": 21000, "seller": {"type": "DEALER"}, "country": "DE"}
        return {"items": [it], "usd": 0.006, "run_id": f"r-{len(aufrufe)}", "status": "SUCCEEDED", "dauer_ms": 3, "actor": K.actor(), "laeufe": 1}
    monkeypatch.setattr(APIFY, "lauf", _lauf)
    zusatz = {"id": {"$regex": f"^test-ml-{s}-"}}
    try:
        st = welt.run(ML.testlauf_alle_status(db, zusatz=zusatz))
        assert st["kandidaten"] == 2 and st["kandidaten_review"] == 1 and st["kosten_schaetzung_usd"] >= 0
        lauf_id = welt.run(ML.testlauf_alle_beanspruchen(db, aktivieren=True, mit_review=False, wer="admin-test"))
        with pytest.raises(ML.LaeuftSchon):
            welt.run(ML.testlauf_alle_beanspruchen(db, aktivieren=True, mit_review=False))
        doc = welt.run(ML.testlauf_alle_ausfuehren(db, lauf_id, aktivieren=True, mit_review=False, zusatz=zusatz))
        assert len(aufrufe) == 2, "je Auftrag genau ein Testlauf, 'zu pruefen' nicht"
        assert (doc["gesamt"], doc["fertig"], doc["bestanden"], doc["nicht_bestanden"], doc["aktiviert"]) == (2, 2, 1, 1, 1)
        assert doc["laeuft"] is False and doc["abbruch"] is None and doc["beendet_at"] and doc["kosten_usd"] > 0
        assert doc["fehler"][0]["id"] == schlecht["id"] and "verworfen" in doc["fehler"][0]["grund"]
        g = welt.run(db[K.MODELLE].find_one({"id": gut["id"]}, {"_id": 0}))
        assert g["status"] == "active" and g["enabled"] is True and g["testlauf_ok_hash"] == A.filter_hash(g) and g["testlauf_letzter"]["bestanden"] is True
        assert welt.run(db[K.SEGMENTE].count_documents({"model_id": gut["id"], "enabled": True})) == 1
        b = welt.run(db[K.MODELLE].find_one({"id": schlecht["id"]}, {"_id": 0}))
        assert b["status"] == "paused" and not b.get("testlauf_ok_hash") and b["testlauf_letzter"]["bestanden"] is False
        assert "kw 140 > 70" in b["testlauf_letzter"]["grund"]
        r = welt.run(db[K.MODELLE].find_one({"id": review["id"]}, {"_id": 0}))
        assert r["status"] == "paused" and "testlauf_letzter" not in r
        bud = welt.run(BUD.dokument(db, f"test-{s}"))
        assert float(bud.get("used_usd") or 0) > 0 and float(bud.get("reserved_usd") or 0) == 0, "Testlaeufe gegen das Marktbudget abgerechnet"
        # zweiter Lauf (nach Ende frei): nur noch der nicht bestandene ist Kandidat
        lauf2 = welt.run(ML.testlauf_alle_beanspruchen(db, aktivieren=True, mit_review=False))
        doc2 = welt.run(ML.testlauf_alle_ausfuehren(db, lauf2, aktivieren=True, mit_review=False, zusatz=zusatz))
        assert doc2["gesamt"] == 1 and len(aufrufe) == 3 and doc2["aktiviert"] == 0
        # Route: Super-Admin, 409 bei laufendem Sammellauf, Stand in der Auftragsliste
        q = (Path(__file__).resolve().parent.parent / "routes" / "markt_admin.py").read_text(encoding="utf-8")
        i = q.index('"/admin/market/masterliste/testlauf-alle"')
        assert "current_super_admin" in q[i:i + 300] and "HTTPException(409" in q[i:i + 1500] and '"testlauf_alle": await masterliste.testlauf_alle_status(db)' in q
    finally:
        welt.run(db[K.KONFIG].delete_one({"_id": ML.TESTLAUF_ALLE_DOK}))
        welt.run(db[K.BUDGET].delete_many({"_id": f"test-{s}"}))
        _aufraeumen(welt)


async def _leer():
    return 0
