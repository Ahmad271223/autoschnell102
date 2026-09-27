# -*- coding: utf-8 -*-
"""Master-Auftrag Marktanalyse (Ahmad 26.09.2026), Phase F — Optimierungsvorschlaege im Modus OBSERVE
(Abschnitte 30-34, 38, 43).

Je Regel ein Test: MERGE_KM_BUCKETS (benachbarte duenne km-Bereiche in allen EZ-Jahren) mit korrekter Evidenz und
Ersparnis aus den echten Crawl-Kosten; SPLIT_KM_BUCKET (staendig voll + grosse Preisstreuung, Mehrkosten);
REDUCE_FREQUENCY / PAUSE_EMPTY / PRIORITIZE_HOT; OBSERVE aendert nichts am Tagesplan; Vorschlag idempotent (ein
Dokument je Schluessel, ueberholt statt geloescht, wieder offen); Annehmen/Ablehnen (abgelehnt bleibt abgelehnt);
Uebernehmen erzeugt eine neue Fassung und pausiert den Auftrag (Testlauf-Pflicht), ohne Abruf.
Phase G (SAFE_AUTO): plant seltene/pausierte Segmente seltener und HOT zuerst, aendert nie km/EZ/Zeilen/Fassung und nie
mehr Abrufe; Nachpruefung pausierter EMPTY-Segmente; Ruecknahme; zurueck auf OBSERVE hebt alles auf; FULL_AUTO gesperrt;
zwei Server; Routen. Testdaten test-/t<hex>.
"""
import asyncio
import inspect
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))
from test_befunde_runde17_termine import _module, welt  # noqa: E402,F401
from test_markt_20260926 import APIFY, ENT, JOBS, K, SEG  # noqa: E402
from test_markt_health_20260927 import (  # noqa: E402
    H, OPT, _aufraeumen, _doc, _mid, _modell_anlegen, _rechnen, _reihe, _seg, _t,
)

A = _module("markt.auftraege")
KM3 = ((20000, 40000), (40001, 60000), (60001, 130000))


def _vorschlaege(welt, mid=None, **filt):
    return welt.run(welt.db[K.VORSCHLAEGE].find({"model_id": mid or _mid(welt), **filt}, {"_id": 0}).sort("schluessel", 1).to_list(100))


def _typ(welt, typ, **filt):
    return [v for v in _vorschlaege(welt, **filt) if v["typ"] == typ]


def _daten_drei_bereiche(welt, segs, tage=20):
    """km 1 und 2 duenn (Ø 1,0 Fahrzeuge, 25 % leer), km 3 staendig voll mit grosser Preisstreuung (P25-P75 = 20 %)."""
    for (ez, km), seg in segs.items():
        if km == KM3[0]:
            _reihe(welt, seg, tage, [0, 1, 2, 1])
        elif km == KM3[1]:
            _reihe(welt, seg, tage, [1, 1, 2, 0])
        else:
            _reihe(welt, seg, tage, [5], schritt=2000.0)


def _drei_bereiche(welt, ez=(2019, 2020)):
    _aufraeumen(welt)
    welt.run(_module("indizes").markt_indizes(welt.db))
    m = _modell_anlegen(welt, km=KM3, ez=ez)
    segs = {(j, km): _seg(welt, km=km, ez=j) for j in ez for km in KM3}
    _daten_drei_bereiche(welt, segs)
    return m, segs


def test_01_merge_vorschlag_evidenz_und_ersparnis(welt):
    """Abschnitt 32: 20.000-40.000 und 40.001-60.000 sind in BEIDEN EZ-Jahren duenn (THIN, je >= 14 gueltige Laeufe,
    zusammen <= 5 Zeilen) -> ein Vorschlag 20.000-60.000; Evidenz (Tage, Ø Fahrzeuge, Leeranteil, Inserate je EZ),
    Ersparnis = je EZ ein Segment weniger = Mittel der echten Monatskosten beider Segmente (0,01 $ je Lauf, taeglich)."""
    _drei_bereiche(welt)
    try:
        _rechnen(welt)
        merge = _typ(welt, "MERGE_KM_BUCKETS")
        assert len(merge) == 1
        v = merge[0]
        assert v["schluessel"] == f"MERGE_KM_BUCKETS:{_mid(welt)}:v1:20000-40000+40001-60000" and v["status"] == "PROPOSED"
        assert v["proposed_definition"]["km_buckets"] == [{"min_km": 20000, "max_km": 60000}, {"min_km": 60001, "max_km": 130000}]
        assert v["proposed_definition"]["ez_years"] == [2019, 2020] and v["proposed_definition"]["rows"] == 5
        ev = v["evidence"]
        assert ev["days"] == 20 and ev["avg_rows"] == {"a": 1.0, "b": 1.0} and ev["empty_rate"] == {"a": 0.25, "b": 0.25}
        assert ev["unique_listings"] == {"a": 4, "b": 4} and len(ev["je_ez"]) == 2 and ev["je_ez"][0]["health_a"] == "THIN"
        assert v["confidence"] == "MEDIUM" and v["estimated_monthly_saving_usd"] == 0.61      # 2 EZ x (0,304 + 0,304) / 2
        assert len(v["source_segment_ids"]) == 4 and "Neue Fassung" in v["reason"] and "Historie bleibt" in v["reason"]
        # ein EZ-Jahr nicht duenn -> kein Vorschlag (km-Bereiche gelten fuer alle EZ-Jahre gemeinsam)
        welt.run(welt.db[K.VORSCHLAEGE].delete_many({"model_id": _mid(welt)}))
        seg_voll = welt.run(welt.db[K.SEGMENTE].find_one({"id": f"{_mid(welt)}:2020:40001-60000"}, {"_id": 0}))
        _reihe(welt, seg_voll, 20, [5])
        _rechnen(welt)
        assert _typ(welt, "MERGE_KM_BUCKETS") == []
        # reine Regel: zusammen mehr Autos als Zeilen -> kein Vorschlag; Luecke zwischen den Bereichen -> kein Vorschlag
        segs = [{"id": f"s{i}", "year_from": 2020, "min_km": a, "max_km": b, "version": 1, "enabled": True, "max_items": 5}
                for i, (a, b) in enumerate(((0, 10000), (10001, 20000), (25000, 30000)))]
        h = {"health": "THIN", "valid_runs": 20, "avg_valid_rows": 1.9, "confidence": "MEDIUM", "valid_days": 20}
        voll = OPT.vorschlaege_modell({"id": "m", "version": 1, "rows": 3}, segs, {s["id"]: dict(h) for s in segs})
        assert voll == [], "1,9 + 1,9 > 3 Zeilen"
        luecke = OPT.vorschlaege_modell({"id": "m", "version": 1, "rows": 5}, segs, {s["id"]: dict(h) for s in segs})
        assert [x["schluessel"] for x in luecke] == ["MERGE_KM_BUCKETS:m:v1:0-10000+10001-20000"], "20.001-24.999 ist eine Luecke"
    finally:
        _aufraeumen(welt)


def test_02_split_vorschlag_evidenz_und_mehrkosten(welt):
    """Abschnitt 33: 60.001-130.000 ist in allen EZ-Jahren staendig voll (100 % der Laeufe) mit grosser Preisstreuung
    (P25-P75 = 20 % des Low-Market-Medians) -> aufteilen bei 95.000 (auf 5.000 km gerundet); Ersparnis negativ
    (ein Segment je EZ mehr)."""
    _drei_bereiche(welt)
    try:
        _rechnen(welt)
        split = _typ(welt, "SPLIT_KM_BUCKET")
        assert len(split) == 1
        v = split[0]
        assert v["schluessel"] == f"SPLIT_KM_BUCKET:{_mid(welt)}:v1:60001-130000"
        assert v["proposed_definition"]["neu"] == [{"min_km": 60001, "max_km": 95000}, {"min_km": 95001, "max_km": 130000}]
        assert v["proposed_definition"]["km_buckets"][-2:] == v["proposed_definition"]["neu"] and len(v["proposed_definition"]["km_buckets"]) == 4
        assert v["evidence"]["streuung_median_pct"] == 20.0 and all(x["voll_anteil"] == 1.0 for x in v["evidence"]["je_ez"])
        assert v["evidence"]["avg_rows"] == 5.0 and v["estimated_monthly_saving_usd"] == -0.61 and "Mehrkosten" in v["reason"]
        # geringe Streuung -> kein Vorschlag; zu schmaler Bereich -> kein Vorschlag
        h = {"health": "NORMAL", "valid_runs": 20, "voll_anteil": 1.0, "preis_streuung_pct": 8.0, "confidence": "MEDIUM", "valid_days": 20}
        segs = [{"id": "s1", "year_from": 2020, "min_km": 60001, "max_km": 130000, "version": 1, "enabled": True}]
        assert OPT.vorschlaege_modell({"id": "m", "version": 1, "rows": 5}, segs, {"s1": h}) == []
        segs2 = [{"id": "s2", "year_from": 2020, "min_km": 60001, "max_km": 80000, "version": 1, "enabled": True}]
        assert OPT.vorschlaege_modell({"id": "m", "version": 1, "rows": 5}, segs2, {"s2": {**h, "preis_streuung_pct": 30.0}}) == []
        assert OPT.SPLIT_STREUUNG_PCT == 15.0 and OPT.SPLIT_VOLL_ANTEIL == 0.9
    finally:
        _aufraeumen(welt)


def test_03_frequenz_pause_hot_und_observe_aendert_den_tagesplan_nicht(welt, monkeypatch):
    """REDUCE_FREQUENCY (ruhig -> alle 2 Tage), PAUSE_EMPTY (EMPTY -> Nachpruefung alle 7 Tage), PRIORITIZE_HOT (HOT,
    keine Zusatzabrufe) mit Ersparnis aus den echten Kosten. Im Modus OBSERVE bleibt der Tagesplan unveraendert: jedes
    Segment wird wie vom Auftrag vorgesehen geplant, kein Segment traegt eine Wirkung."""
    _aufraeumen(welt)
    welt.run(_module("indizes").markt_indizes(welt.db))
    db, s = welt.db, welt.w.s
    alt = welt.run(db[K.KONFIG].find_one({"_id": K.OPTIMIERUNG_DOK}))
    try:
        welt.run(db[K.KONFIG].update_one({"_id": K.OPTIMIERUNG_DOK}, {"$set": {"modus": "OBSERVE"}}, upsert=True))
        _modell_anlegen(welt, km=KM3, ez=(2020,))
        leer = _seg(welt, km=KM3[0])
        heiss = _seg(welt, km=KM3[1])
        ruhig = _seg(welt, km=KM3[2])
        _reihe(welt, leer, 20, [0])
        for i in range(20):
            _doc(welt, heiss, _t(i), 5, neu=2, weg=2, red=1, top3=True, top5=True, praefix=f"h{i}")
        _reihe(welt, ruhig, 20, [5])
        _rechnen(welt)
        pause = _typ(welt, "PAUSE_EMPTY")[0]
        assert pause["segment_id"] == leer["id"] and pause["proposed_definition"]["nachpruefung_tage"] == 7
        assert pause["estimated_monthly_saving_usd"] == 0.26 and pause["evidence"]["empty_rate"] == 1.0   # 0,01 x (1 - 1/7) x 30,4
        red = _typ(welt, "REDUCE_FREQUENCY")
        assert [(x["segment_id"], x["proposed_definition"]["intervall_tage"]) for x in red] == [(ruhig["id"], 2)]
        assert red[0]["estimated_monthly_saving_usd"] == 0.15 and "alle 2 Tage statt täglich" in red[0]["reason"]
        hot = _typ(welt, "PRIORITIZE_HOT")
        assert [x["segment_id"] for x in hot] == [heiss["id"]] and hot[0]["estimated_monthly_saving_usd"] == 0.0
        assert all(v["status"] == "PROPOSED" for v in _vorschlaege(welt))
        # OBSERVE: keine Wirkung am Segment, der Tagesplan plant alle drei wie gehabt
        assert welt.run(db[K.SEGMENTE].count_documents({"model_id": _mid(welt), "safe_auto": {"$exists": True}})) == 0
        monkeypatch.setenv("MARKT_BUDGET_MONAT_USD", "1000")
        monkeypatch.setenv("MARKT_CRAWL_INTERVALL_TAGE", "1")
        monkeypatch.setenv("MARKT_APIFY_ACTOR_ERSATZ", "")
        monkeypatch.setattr(K, "monat", lambda zeit=None: f"test-{s}")
        tag = f"2098-03-{int(s[:1], 16) % 28 + 1:02d}"
        welt.run(JOBS.tagesplan(db, tag))
        ids = [leer["id"], heiss["id"], ruhig["id"]]
        assert welt.run(db[K.JOBS].count_documents({"segment_id": {"$in": ids}, "tag": tag})) == 3
        naechster = (datetime.strptime(tag, "%Y-%m-%d") + timedelta(days=1)).strftime("%Y-%m-%d")
        welt.run(JOBS.tagesplan(db, naechster))
        assert welt.run(db[K.JOBS].count_documents({"segment_id": {"$in": ids}, "tag": naechster})) == 3, "OBSERVE: jeden Tag alle"
    finally:
        welt.run(db[K.BUDGET].delete_many({"_id": f"test-{s}"}))
        welt.run(db[K.KONFIG].delete_many({"_id": K.TAGESPLAN_DOK, "tag": {"$regex": "^2098"}}))
        if alt:
            welt.run(db[K.KONFIG].replace_one({"_id": K.OPTIMIERUNG_DOK}, alt, upsert=True))
        else:
            welt.run(db[K.KONFIG].delete_one({"_id": K.OPTIMIERUNG_DOK}))
        _aufraeumen(welt)


def test_04_vorschlag_idempotent_ueberholt_und_wieder_offen(welt):
    """Derselbe Vorschlag entsteht nie taeglich neu (eindeutiger Schluessel): ein Dokument, erstes Auftauchen bleibt,
    Evidenz wird fortgeschrieben. Entfaellt die Bedingung, wird er OBSOLETE (nicht geloescht); kommt sie wieder,
    ist er wieder offen. Ein pausierter Auftrag (keine aktiven Segmente) -> offene Vorschlaege ueberholt."""
    m, segs = _drei_bereiche(welt, ez=(2020,))
    db = welt.db
    try:
        _rechnen(welt)
        erst = {v["schluessel"]: v for v in _vorschlaege(welt)}
        assert set(v["typ"] for v in erst.values()) >= {"MERGE_KM_BUCKETS", "SPLIT_KM_BUCKET", "REDUCE_FREQUENCY"}
        _rechnen(welt)
        _rechnen(welt, stichtag=_t(-1))
        zweit = {v["schluessel"]: v for v in _vorschlaege(welt)}
        assert set(zweit) == set(erst), "keine neuen Dokumente"
        for k, v in zweit.items():
            assert v["id"] == erst[k]["id"] and v["created_at"] == erst[k]["created_at"] and v["first_seen_tag"] == erst[k]["first_seen_tag"]
            assert v["last_seen_tag"] == _t(-1) and v["status"] == "PROPOSED"
        merge_key = f"MERGE_KM_BUCKETS:{_mid(welt)}:v1:20000-40000+40001-60000"
        # Bedingung entfaellt: der zweite Bereich wird voll -> MERGE ueberholt
        seg_b = segs[(2020, KM3[1])]
        _reihe(welt, seg_b, 20, [5])
        _rechnen(welt, stichtag=_t(-1))
        v = welt.run(db[K.VORSCHLAEGE].find_one({"schluessel": merge_key}, {"_id": 0}))
        assert v["status"] == "OBSOLETE" and v["obsolet_grund"] == "Bedingung nicht mehr erfüllt" and v["obsolet_at"]
        # Bedingung wieder da -> derselbe Vorschlag wieder offen (kein zweites Dokument)
        _reihe(welt, seg_b, 20, [1, 1, 2, 0])
        _rechnen(welt, stichtag=_t(-1))
        v2 = welt.run(db[K.VORSCHLAEGE].find_one({"schluessel": merge_key}, {"_id": 0}))
        assert v2["status"] == "PROPOSED" and v2["id"] == v["id"] and v2["wieder_offen_at"] and "obsolet_grund" not in v2
        assert welt.run(db[K.VORSCHLAEGE].count_documents({"schluessel": merge_key})) == 1
        # Auftrag pausiert (Segmente inaktiv) -> ein voller Lauf markiert seine offenen Vorschlaege als ueberholt
        welt.run(db[K.SEGMENTE].update_many({"model_id": _mid(welt)}, {"$set": {"enabled": False}}))
        welt.run(OPT.berechnen(db, stichtag=_t(-1), jetzt=datetime(2031, 3, 31, 12, 0, tzinfo=timezone.utc)))
        offen = [x for x in _vorschlaege(welt) if x["status"] in ("PROPOSED", "ACCEPTED")]
        assert offen == [] and welt.run(db[K.VORSCHLAEGE].find_one({"schluessel": merge_key}))["obsolet_grund"].startswith("Suchauftrag ohne aktive Segmente")
        assert welt.run(db[K.VORSCHLAEGE].count_documents({"model_id": _mid(welt)})) == len(_vorschlaege(welt)) > 0, "nichts geloescht"
    finally:
        _aufraeumen(welt)


def test_05_annehmen_ablehnen_abgelehnt_bleibt_abgelehnt(welt):
    """Annehmen (PROPOSED -> ACCEPTED) und Ablehnen (-> REJECTED). Ein abgelehnter Vorschlag bleibt abgelehnt, auch wenn
    die Bedingung weiter besteht (Evidenz wird nicht mehr angefasst); ein angenommener wird fortgeschrieben und bei
    entfallener Bedingung ueberholt."""
    m, segs = _drei_bereiche(welt, ez=(2020,))
    db = welt.db
    try:
        _rechnen(welt)
        merge = _typ(welt, "MERGE_KM_BUCKETS")[0]
        split = _typ(welt, "SPLIT_KM_BUCKET")[0]
        assert welt.run(OPT.vorschlag_entscheiden(db, merge["id"], "ablehnen", wer="test-admin"))["status"] == "REJECTED"
        a = welt.run(OPT.vorschlag_entscheiden(db, split["id"], "annehmen", wer="test-admin"))
        assert a["status"] == "ACCEPTED" and a["entschieden_von"] == "test-admin"
        with pytest.raises(OPT.Konflikt):
            welt.run(OPT.vorschlag_entscheiden(db, merge["id"], "annehmen", wer="test-admin"))
        with pytest.raises(OPT.Ungueltig):
            welt.run(OPT.vorschlag_entscheiden(db, merge["id"], "loeschen", wer="test-admin"))
        with pytest.raises(OPT.NichtGefunden):
            welt.run(OPT.vorschlag_entscheiden(db, "gibt-es-nicht", "annehmen", wer="test-admin"))
        _rechnen(welt, stichtag=_t(-1))
        r = welt.run(db[K.VORSCHLAEGE].find_one({"id": merge["id"]}, {"_id": 0}))
        assert r["status"] == "REJECTED" and not r["updated_at"].startswith("2031"), "Evidenz eines abgelehnten nicht fortgeschrieben"
        assert r["last_seen_tag"] == _t(-1), "gesehen ja, aber nicht wieder geoeffnet"
        acc = welt.run(db[K.VORSCHLAEGE].find_one({"id": split["id"]}, {"_id": 0}))
        assert acc["status"] == "ACCEPTED" and acc["updated_at"].startswith("2031-03-30"), "angenommen: Evidenz fortgeschrieben"
        # Bedingung entfaellt -> auch der angenommene ist ueberholt
        _reihe(welt, segs[(2020, KM3[2])], 20, [5])
        _rechnen(welt, stichtag=_t(-1))
        assert welt.run(db[K.VORSCHLAEGE].find_one({"id": split["id"]}, {"_id": 0}))["status"] == "OBSOLETE"
    finally:
        _aufraeumen(welt)


def _echter_auftrag(welt, ez=(2019, 2020)):
    """Suchauftrag wie ueber das Formular angelegt (Katalog-IDs, Hashes, bestandener Testlauf) + Segmente der Fassung 1."""
    _aufraeumen(welt)
    welt.run(_module("indizes").markt_indizes(welt.db))
    s = welt.w.s
    e = {"make": "BMW", "model": "320", "variant": f"Test {s} 320d", "fuel": "DIESEL", "gearbox": "AUTOMATIC_GEAR", "power_kw_min": 120,
         "power_kw_max": 145, "ez_years": list(ez), "km_buckets": [{"min_km": a, "max_km": b} for a, b in KM3], "rows": 5,
         "crawls_per_day": 1, "status": "active"}
    m = A.entwurf_pruefen(e)
    jetzt = K.jetzt_iso()
    doc = {**m, "id": _mid(welt), "label": "BMW 320d (Test)", "version": 1, "definition_hash": A.definition_hash(m), "filter_hash": A.filter_hash(m),
           "hash_fassung": A.HASH_FASSUNG, "testlauf_ok_at": jetzt, "testlauf_ok_hash": A.filter_hash(m), "created_at": jetzt, "updated_at": jetzt}
    welt.run(welt.db[K.MODELLE].insert_one(dict(doc)))
    segs = {(j, km): _seg(welt, km=km, ez=j, dh=doc["definition_hash"]) for j in ez for km in KM3}
    _daten_drei_bereiche(welt, segs)
    return doc, segs


def test_06_uebernehmen_neue_fassung_pausiert_ohne_abruf(welt, monkeypatch):
    """MERGE uebernehmen (nur Super-Admin, nie automatisch): auftraege.aendern -> neue Fassung (v2, neue Segment-IDs),
    km-Bereiche zusammengelegt, Auftrag PAUSIERT; alte Segmente inaktiv, ihre Tagesdaten bleiben unveraendert;
    Aktivieren erst nach neuem Testlauf. Kein Abruf. Ein zweites Uebernehmen und ein Vorschlag der alten Fassung
    werden abgewiesen (409, ueberholt)."""
    async def _verboten(*a, **k):
        raise AssertionError("Crawl-Funktion aufgerufen")
    for mod, name in ((APIFY, "lauf"), (APIFY, "lauf_mit_ersatz"), (JOBS, "job_sofort"), (JOBS, "tagesplan"), (JOBS, "einmal"),
                      (JOBS, "verarbeiten_buendel"), (ENT, "taeglich")):
        monkeypatch.setattr(mod, name, _verboten)
    monkeypatch.setattr(SEG, "_nachplanen", lambda db: _leer())     # Nachplanen fremder Segmente im Test nie
    doc, segs = _echter_auftrag(welt)
    db = welt.db
    try:
        _rechnen(welt)
        merge = _typ(welt, "MERGE_KM_BUCKETS")[0]
        split = _typ(welt, "SPLIT_KM_BUCKET")[0]
        tage_vorher = welt.run(db[K.TAGESSTATS].count_documents({"model_id": _mid(welt)}))
        erg = welt.run(OPT.vorschlag_uebernehmen(db, merge["id"], wer="test-admin"))
        m = erg["modell"]
        assert m["version"] == 2 and m["status"] == "paused" and m["enabled"] is False
        assert m["km_buckets"] == [{"min_km": 20000, "max_km": 60000}, {"min_km": 60001, "max_km": 130000}]
        assert m["ez_years"] == [2019, 2020] and m["rows"] == 5 and m["definition_hash"] == doc["definition_hash"], "Filter unveraendert"
        v = erg["vorschlag"]
        assert v["status"] == "APPLIED" and v["angewendet_von"] == "test-admin" and v["alte_version"] == 1 and v["neue_version"] == 2
        assert "Testlauf" in erg["hinweis"]
        alte = welt.run(db[K.SEGMENTE].find({"model_id": _mid(welt)}, {"_id": 0, "id": 1, "enabled": 1}).to_list(50))
        assert len(alte) == 6 and not any(x["enabled"] for x in alte), "alte Fassung nur deaktiviert, nichts geloescht"
        assert welt.run(db[K.TAGESSTATS].count_documents({"model_id": _mid(welt)})) == tage_vorher, "Historie bleibt unveraendert"
        # Aktivieren erst nach neuem Testlauf (die km-Bereiche wurden nie geprueft)
        with pytest.raises(A.Ungueltig) as ex:
            welt.run(A.status_setzen(db, _mid(welt), "active"))
        assert "erst Testlauf" in str(ex.value)
        # neue Fassung: Segment-IDs tragen v2 (entstehen beim Aktivieren); die alte Zeitreihe mischt sich nie hinein
        assert SEG.segment_id(_mid(welt), {"min_km": 20000, "max_km": 60000}, {"year_from": 2019, "year_to": 2019}, 2) == f"{_mid(welt)}:v2:2019:20000-60000"
        with pytest.raises(OPT.Konflikt):
            welt.run(OPT.vorschlag_uebernehmen(db, merge["id"], wer="test-admin"))
        with pytest.raises(OPT.Konflikt) as ex2:
            welt.run(OPT.vorschlag_uebernehmen(db, split["id"], wer="test-admin"))
        assert "geändert" in str(ex2.value)
        assert welt.run(db[K.VORSCHLAEGE].find_one({"id": split["id"]}, {"_id": 0}))["status"] == "OBSOLETE"
    finally:
        _aufraeumen(welt)


def test_07_uebernehmen_nur_bei_unveraendertem_auftrag(welt, monkeypatch):
    """Wurde der Auftrag seit dem Vorschlag geaendert (andere km-Bereiche), ist der Vorschlag ueberholt (409) — und ein
    gescheitertes aendern (z. B. gleichzeitige Bearbeitung) laesst den Vorschlag offen."""
    monkeypatch.setattr(SEG, "_nachplanen", lambda db: _leer())
    doc, segs = _echter_auftrag(welt, ez=(2020,))
    db = welt.db
    try:
        _rechnen(welt)
        merge = _typ(welt, "MERGE_KM_BUCKETS")[0]
        echt = A.aendern

        async def _konflikt(*a, **k):
            raise A.Konflikt("gleichzeitig geändert")
        monkeypatch.setattr(A, "aendern", _konflikt)
        with pytest.raises(A.Konflikt):
            welt.run(OPT.vorschlag_uebernehmen(db, merge["id"], wer="test-admin"))
        v = welt.run(db[K.VORSCHLAEGE].find_one({"id": merge["id"]}, {"_id": 0}))
        assert v["status"] == "PROPOSED" and "angewendet_von" not in v, "zurueckgesetzt"
        monkeypatch.setattr(A, "aendern", echt)
        welt.run(db[K.MODELLE].update_one({"id": _mid(welt)}, {"$set": {"km_buckets": [{"min_km": 20000, "max_km": 130000}]}}))
        with pytest.raises(OPT.Konflikt):
            welt.run(OPT.vorschlag_uebernehmen(db, merge["id"], wer="test-admin"))
        assert welt.run(db[K.VORSCHLAEGE].find_one({"id": merge["id"]}, {"_id": 0}))["status"] == "OBSOLETE"
        assert welt.run(db[K.MODELLE].find_one({"id": _mid(welt)}, {"_id": 0}))["version"] == 1, "nichts geaendert"
    finally:
        _aufraeumen(welt)


async def _leer():
    return {"uebersprungen": "test"}


def test_08_health_reihenfolge_bleibt_fuer_vorschlaege_massgeblich():
    """Reine Regeln: keine Vorschlaege aus UNKNOWN/STALE/UNSTABLE; REDUCE nur, wenn die Empfehlung seltener ist als der
    Auftrag (2x taeglich beim Auftrag mit 1x taeglich ist KEINE Reduktion); PAUSE nur fuer EMPTY."""
    seg = {"id": "s", "model_id": "m", "version": 1, "crawls_per_day": 1, "ez_label": "EZ 2020", "km_label": "20–40k km"}
    basis = {"valid_runs": 20, "valid_days": 20, "confidence": "MEDIUM", "kosten_je_lauf_usd": 0.01, "laeufe_je_tag": 1.0,
             "laeufe_je_tag_konfig": 1, "crawls_per_day": 1, "activity_score": 30}
    for st in ("UNKNOWN", "STALE", "UNSTABLE"):
        assert OPT.vorschlaege_segment(seg, {**basis, "health": st, "empfehlung": None}) == []
    hot = OPT.vorschlaege_segment(seg, {**basis, "health": "HOT", "activity_score": 90, "empfehlung": H.empfehlung("HOT", 90)})
    assert [v["typ"] for v in hot] == ["PRIORITIZE_HOT"], "2x taeglich empfohlen, Auftrag 1x -> keine Reduktion, nie mehr Abrufe"
    zwei = OPT.vorschlaege_segment({**seg, "crawls_per_day": 2}, {**basis, "crawls_per_day": 2, "laeufe_je_tag_konfig": 2, "laeufe_je_tag": 2.0,
                                                                 "health": "HEALTHY", "activity_score": 60, "empfehlung": H.empfehlung("HEALTHY", 60)})
    assert [(v["typ"], v["proposed_definition"]["crawls_per_day"]) for v in zwei] == [("REDUCE_FREQUENCY", 1)]
    assert zwei[0]["estimated_monthly_saving_usd"] == 0.3      # 0,01 x (2 - 1) x 30,4
    wenig = OPT.vorschlaege_segment(seg, {**basis, "valid_runs": 10, "health": "NORMAL", "empfehlung": H.empfehlung("NORMAL", 30)})
    assert wenig == [], "Frequenz erst ab 14 gueltigen Laeufen senken"
    ohne_kosten = OPT.vorschlaege_segment(seg, {**basis, "kosten_je_lauf_usd": None, "health": "NORMAL", "empfehlung": H.empfehlung("NORMAL", 30)})
    assert ohne_kosten[0]["estimated_monthly_saving_usd"] is None, "keine erfundene Ersparnis ohne Kostendaten"
    assert OPT.frequenz_text({"intervall_tage": 1, "crawls_per_day": 2}) == "2× täglich" and OPT.frequenz_text({"intervall_tage": 3}) == "alle 3 Tage"


# ---------------------------------------------------------------- Phase G: SAFE_AUTO
def _modus_merken(welt):
    return welt.run(welt.db[K.KONFIG].find_one({"_id": K.OPTIMIERUNG_DOK}))


def _modus_zurueck(welt, alt):
    if alt:
        welt.run(welt.db[K.KONFIG].replace_one({"_id": K.OPTIMIERUNG_DOK}, alt, upsert=True))
    else:
        welt.run(welt.db[K.KONFIG].delete_one({"_id": K.OPTIMIERUNG_DOK}))


def _plan_umgebung(welt, monkeypatch, buendel="1"):
    s = welt.w.s
    monkeypatch.setenv("MARKT_BUDGET_MONAT_USD", "1000")
    monkeypatch.setenv("MARKT_CRAWL_INTERVALL_TAGE", "1")
    monkeypatch.setenv("MARKT_APIFY_ACTOR_ERSATZ", "")
    monkeypatch.setenv("MARKT_BUENDEL_GROESSE", buendel)
    monkeypatch.setattr(K, "monat", lambda zeit=None: f"test-{s}")
    return f"2099-0{int(s[:1], 16) % 5 + 1}-01"


def _plus(tag, n):
    return (datetime.strptime(tag, "%Y-%m-%d") + timedelta(days=n)).strftime("%Y-%m-%d")


def _jobs(welt, ids, tag):
    return welt.run(welt.db[K.JOBS].find({"segment_id": {"$in": ids}, "tag": {"$regex": f"^{tag}"}}, {"_id": 0}).sort("scheduled_at", 1).to_list(50))


def _drei_safe(welt):
    """EMPTY (-> pausieren), HOT (-> vorziehen), ruhig (-> alle 2 Tage) in einem Auftrag, EZ 2020."""
    _aufraeumen(welt)
    welt.run(_module("indizes").markt_indizes(welt.db))
    _modell_anlegen(welt, km=KM3, ez=(2020,))
    leer, heiss, ruhig = _seg(welt, km=KM3[0]), _seg(welt, km=KM3[1]), _seg(welt, km=KM3[2])
    _reihe(welt, leer, 20, [0])
    for i in range(20):
        _doc(welt, heiss, _t(i), 5, neu=2, weg=2, red=1, top3=True, top5=True, praefix=f"h{i}")
    _reihe(welt, ruhig, 20, [5])
    return leer, heiss, ruhig


def test_09_safe_auto_plant_seltener_hot_zuerst_nie_km_ez_zeilen(welt, monkeypatch):
    """SAFE_AUTO darf NUR: Frequenz je Segment senken, EMPTY pausieren (mit Nachpruefung), HOT vorziehen. Der Tagesplan
    plant pausierte/seltene Segmente nicht jeden Tag und HOT zuerst — mit nie mehr Abrufen als der Auftrag vorsieht.
    Suchauftrag, km-Bereiche, EZ, Zeilen, Fassung und Filter bleiben unveraendert. Jede Aenderung steht im Protokoll."""
    alt = _modus_merken(welt)
    leer, heiss, ruhig = _drei_safe(welt)
    db = welt.db
    ids = [leer["id"], heiss["id"], ruhig["id"]]
    modell_vorher = welt.run(db[K.MODELLE].find_one({"id": _mid(welt)}, {"_id": 0}))
    segs_vorher = {s["id"]: s for s in welt.run(db[K.SEGMENTE].find({"id": {"$in": ids}}, {"_id": 0}).to_list(10))}
    try:
        welt.run(OPT.modus_setzen(db, "SAFE_AUTO", wer="test-admin"))
        erg = _rechnen(welt)
        assert erg["safe_auto"]["modus"] == "SAFE_AUTO" and erg["safe_auto"]["angewendet"] == 3
        w = {s["id"]: s.get("safe_auto") for s in welt.run(db[K.SEGMENTE].find({"id": {"$in": ids}}, {"_id": 0}).to_list(10))}
        assert w[leer["id"]]["intervall_tage"] == 7 and w[leer["id"]]["pausiert"] is True
        assert w[ruhig["id"]]["intervall_tage"] == 2 and w[ruhig["id"]]["pausiert"] is False and w[ruhig["id"]]["crawls_per_day"] == 1
        assert w[heiss["id"]]["hot"] is True and w[heiss["id"]]["intervall_tage"] == 1
        # nie km/EZ/Zeilen/Fassung/Filter
        assert welt.run(db[K.MODELLE].find_one({"id": _mid(welt)}, {"_id": 0})) == modell_vorher
        for s in welt.run(db[K.SEGMENTE].find({"id": {"$in": ids}}, {"_id": 0}).to_list(10)):
            v = segs_vorher[s["id"]]
            for k in ("min_km", "max_km", "year_from", "year_to", "max_items", "version", "definition_hash", "crawls_per_day", "enabled"):
                assert s.get(k) == v.get(k), (s["id"], k)
        # Protokoll: wer = safe_auto, alt -> neu, Grund; Vorschlaege APPLIED
        log = welt.run(db[K.AENDERUNGEN].find({"model_id": _mid(welt)}, {"_id": 0}).to_list(10))
        assert len(log) == 3 and all(a["wer"] == "safe_auto" and a["status"] == "aktiv" and a["grund"] for a in log)
        pause = next(a for a in log if a["typ"] == "PAUSE_EMPTY")
        assert pause["alt"] == {"intervall_tage": 1, "crawls_per_day": 1, "prioritaet": "normal", "pausiert": False}
        assert pause["neu"] == {"intervall_tage": 7, "crawls_per_day": 1, "pausiert": True} and pause["estimated_monthly_saving_usd"] == 0.26
        assert all(v["status"] == "APPLIED" and v["angewendet_von"] == "safe_auto" for v in _vorschlaege(welt) if v["typ"] in OPT.SAFE_TYPEN)
        assert [v["status"] for v in _vorschlaege(welt) if v["typ"] in OPT.STRUKTUR_TYPEN] in ([], ["PROPOSED"]), "MERGE/SPLIT nie automatisch"
        # zweiter Lauf: nichts doppelt
        assert _rechnen(welt)["safe_auto"]["angewendet"] == 0
        assert welt.run(db[K.AENDERUNGEN].count_documents({"model_id": _mid(welt), "status": "aktiv"})) == 3
        # Tagesplan: Tag 1 alle (HOT zuerst), Tag 2 nur HOT, Tag 3 HOT + ruhig, Tag 8 wieder alle (Nachpruefung EMPTY)
        tag = _plan_umgebung(welt, monkeypatch)
        welt.run(db[K.SEGMENTE].update_many({"id": {"$in": ids}}, {"$unset": {"last_planned_tag": ""}}))
        p1 = welt.run(JOBS.tagesplan(db, tag))
        j1 = _jobs(welt, ids, tag)
        assert len(j1) == 3 and j1[0]["segment_id"] == heiss["id"], "HOT zuerst (fruehester Slot)"
        assert p1["safe_auto"] is True and p1["ruhend"] == 0
        assert len({j["scheduled_at"] for j in j1}) == 3
        erwartet = {1: [heiss["id"]], 2: [heiss["id"], ruhig["id"]], 3: [heiss["id"]], 7: [heiss["id"], leer["id"]]}
        for n in range(1, 8):
            t = _plus(tag, n)
            welt.run(JOBS.tagesplan(db, t))
            geplant = sorted(j["segment_id"] for j in _jobs(welt, ids, t))
            soll = sorted(erwartet.get(n, [heiss["id"]] + ([ruhig["id"]] if n % 2 == 0 else [])))
            assert geplant == soll, (n, geplant)
        # nie mehr Abrufe als vorgesehen: je Segment und Tag hoechstens ein Job (crawls_per_day 1, HOT bleibt 1x)
        alle = welt.run(db[K.JOBS].find({"segment_id": {"$in": ids}}, {"_id": 0, "segment_id": 1, "tag": 1}).to_list(200))
        assert all("#" not in j["tag"] for j in alle) and len(alle) == len({(j["segment_id"], j["tag"]) for j in alle})
    finally:
        welt.run(db[K.BUDGET].delete_many({"_id": f"test-{welt.w.s}"}))
        welt.run(db[K.KONFIG].delete_many({"_id": K.TAGESPLAN_DOK, "tag": {"$regex": "^2099"}}))
        _modus_zurueck(welt, alt)
        _aufraeumen(welt)


def test_10_safe_auto_senkt_nur_und_nachpruefung_bestaetigt_oder_hebt_auf(welt, monkeypatch):
    """HOT mit 2x-Empfehlung erhoeht nie die Abrufe (Auftrag 1x taeglich bleibt 1x). Pausierte EMPTY-Segmente werden
    in festen Abstaenden nachgeprueft: bleiben die Nachpruefungen leer, bleibt EMPTY (auch mit wenigen Laeufen im
    Fenster) und die Pause steht; findet eine Nachpruefung Autos, entfaellt die Pause (Protokoll: aufgehoben)."""
    alt = _modus_merken(welt)
    leer, heiss, ruhig = _drei_safe(welt)
    db = welt.db
    try:
        welt.run(OPT.modus_setzen(db, "SAFE_AUTO", wer="test-admin"))
        _rechnen(welt)
        assert welt.run(db[K.SEGMENTE].find_one({"id": heiss["id"]}))["safe_auto"].get("crawls_per_day") is None, "nie mehr Abrufe"
        # nur noch Nachpruefungen alle 7 Tage im Fenster (4 leere Laeufe) -> EMPTY bleibt bestaetigt
        welt.run(db[K.TAGESSTATS].delete_many({"segment_id": leer["id"]}))
        for i in (0, 7, 14, 21):
            _doc(welt, leer, _t(i), 0)
        _rechnen(welt)
        h = welt.run(db[K.HEALTH].find_one({"segment_id": leer["id"]}, {"_id": 0}))
        assert h["health"] == "EMPTY" and h["valid_runs"] == 4 and h["erwarteter_abstand_tage"] == 7 and h["health"] != "STALE"
        assert H.min_laeufe(H.MIN_LAEUFE_EMPTY, {"intervall_tage": 7}) == 4 and H.min_laeufe(H.MIN_LAEUFE_EMPTY, None) == 14
        assert welt.run(db[K.AENDERUNGEN].count_documents({"segment_id": leer["id"], "status": "aktiv"})) == 1
        # Nachpruefung findet Autos -> nicht mehr EMPTY -> Pause aufgehoben (wer safe_auto, Grund)
        _doc(welt, leer, _t(0), 3)
        _doc(welt, leer, _t(7), 2)
        _rechnen(welt)
        a = welt.run(db[K.AENDERUNGEN].find_one({"segment_id": leer["id"], "typ": "PAUSE_EMPTY"}, {"_id": 0}))
        assert a["status"] == "aufgehoben" and a["beendet_von"] == "safe_auto" and a["beendet_grund"] == "Empfehlung entfallen"
        seg = welt.run(db[K.SEGMENTE].find_one({"id": leer["id"]}, {"_id": 0}))
        assert not (seg.get("safe_auto") or {}).get("pausiert")
    finally:
        _modus_zurueck(welt, alt)
        _aufraeumen(welt)


def test_11_ruecknahme_einzeln(welt):
    """Jede SAFE_AUTO-Aenderung ist einzeln ruecknehmbar: Wirkung weg, Vorschlag abgelehnt (wird nie wieder
    angewendet), Segment 30 Tage ohne neue SAFE_AUTO-Wirkung; das Protokoll behaelt den Eintrag (zurueckgenommen)."""
    alt = _modus_merken(welt)
    leer, heiss, ruhig = _drei_safe(welt)
    db = welt.db
    try:
        welt.run(OPT.modus_setzen(db, "SAFE_AUTO", wer="test-admin"))
        _rechnen(welt)
        a = welt.run(db[K.AENDERUNGEN].find_one({"segment_id": ruhig["id"], "status": "aktiv"}, {"_id": 0}))
        erg = welt.run(OPT.aenderung_zuruecknehmen(db, a["id"], wer="test-admin"))
        assert erg["status"] == "zurueckgenommen" and erg["beendet_von"] == "test-admin" and erg["sperre_bis"]
        seg = welt.run(db[K.SEGMENTE].find_one({"id": ruhig["id"]}, {"_id": 0}))
        assert "safe_auto" not in seg and seg["safe_auto_sperre_bis"] == erg["sperre_bis"]
        assert welt.run(db[K.VORSCHLAEGE].find_one({"id": a["vorschlag_id"]}, {"_id": 0}))["status"] == "REJECTED"
        _rechnen(welt)
        assert welt.run(db[K.AENDERUNGEN].count_documents({"segment_id": ruhig["id"], "status": "aktiv"})) == 0, "nicht wieder angewendet"
        assert welt.run(db[K.AENDERUNGEN].count_documents({"segment_id": ruhig["id"]})) == 1, "Protokoll bleibt"
        with pytest.raises(OPT.Konflikt):
            welt.run(OPT.aenderung_zuruecknehmen(db, a["id"], wer="test-admin"))
        with pytest.raises(OPT.NichtGefunden):
            welt.run(OPT.aenderung_zuruecknehmen(db, "gibt-es-nicht", wer="test-admin"))
        # Ablehnen eines angewendeten Vorschlags nimmt die Wirkung ebenfalls zurueck
        v_hot = next(v for v in _vorschlaege(welt) if v["typ"] == "PRIORITIZE_HOT")
        welt.run(OPT.vorschlag_entscheiden(db, v_hot["id"], "ablehnen", wer="test-admin"))
        assert "safe_auto" not in welt.run(db[K.SEGMENTE].find_one({"id": heiss["id"]}, {"_id": 0}))
        assert welt.run(db[K.AENDERUNGEN].find_one({"segment_id": heiss["id"]}, {"_id": 0}))["status"] == "zurueckgenommen"
    finally:
        _modus_zurueck(welt, alt)
        _aufraeumen(welt)


def test_12_zurueck_auf_observe_hebt_alle_wirkungen_auf(welt, monkeypatch):
    """Zurueck auf OBSERVE hebt alle SAFE_AUTO-Wirkungen auf (Protokoll: aufgehoben, Vorschlaege wieder offen) — und
    der Tagesplan beachtet eine Wirkung am Segment ohnehin nur im Modus SAFE_AUTO (naechster Tagesplan wie vorher)."""
    alt = _modus_merken(welt)
    leer, heiss, ruhig = _drei_safe(welt)
    db = welt.db
    ids = [leer["id"], heiss["id"], ruhig["id"]]
    try:
        welt.run(OPT.modus_setzen(db, "SAFE_AUTO", wer="test-admin"))
        _rechnen(welt)
        tag = _plan_umgebung(welt, monkeypatch, buendel="10")
        welt.run(JOBS.tagesplan(db, tag))
        assert len(_jobs(welt, ids, _plus(tag, 0))) == 3
        welt.run(JOBS.tagesplan(db, _plus(tag, 1)))
        assert [j["segment_id"] for j in _jobs(welt, ids, _plus(tag, 1))] == [heiss["id"]]
        erg = welt.run(OPT.modus_setzen(db, "OBSERVE", wer="test-admin"))
        assert erg["modus"] == "OBSERVE" and erg["vorher"] == "SAFE_AUTO" and erg["aufgehoben"] >= 3
        assert welt.run(db[K.SEGMENTE].count_documents({"id": {"$in": ids}, "safe_auto": {"$exists": True}})) == 0
        assert welt.run(db[K.AENDERUNGEN].count_documents({"model_id": _mid(welt), "status": "aktiv"})) == 0
        assert all(a["beendet_grund"] == "Modus OBSERVE" for a in welt.run(db[K.AENDERUNGEN].find({"model_id": _mid(welt)}).to_list(10)))
        assert all(v["status"] == "PROPOSED" for v in _vorschlaege(welt) if v["typ"] in OPT.SAFE_TYPEN)
        welt.run(JOBS.tagesplan(db, _plus(tag, 2)))
        assert len(_jobs(welt, ids, _plus(tag, 2))) == 3, "naechster Tagesplan wie ohne SAFE_AUTO"
        # Gating: eine (z. B. nach einer Wiederherstellung) stehengebliebene Wirkung zaehlt in OBSERVE nicht
        welt.run(db[K.SEGMENTE].update_one({"id": leer["id"]}, {"$set": {"safe_auto": {"intervall_tage": 30, "pausiert": True}}}))
        welt.run(JOBS.tagesplan(db, _plus(tag, 3)))
        assert leer["id"] in [j["segment_id"] for j in _jobs(welt, ids, _plus(tag, 3))]
        # OBSERVE rechnet weiter Empfehlungen, wendet aber nichts an
        erg = _rechnen(welt)
        assert erg["safe_auto"]["angewendet"] == 0 and erg["safe_auto"]["modus"] == "OBSERVE"
    finally:
        welt.run(db[K.BUDGET].delete_many({"_id": f"test-{welt.w.s}"}))
        welt.run(db[K.KONFIG].delete_many({"_id": K.TAGESPLAN_DOK, "tag": {"$regex": "^2099"}}))
        _modus_zurueck(welt, alt)
        _aufraeumen(welt)


def test_13_full_auto_gesperrt_zwei_server_und_routen(welt, monkeypatch):
    """FULL_AUTO ist gesperrt; neue Wirkungen nur ab Confidence MEDIUM; zwei gleichzeitige Anwendungen legen jede
    Wirkung nur einmal an (Teil-Unique-Index); Routen: Modus/Ruecknahme nur Super-Admin mit Protokoll, Lesen Admin."""
    from fastapi import HTTPException
    MA = _module("routes.markt_admin")
    monkeypatch.setattr(MA, "db", welt.db)
    alt = _modus_merken(welt)
    leer, heiss, ruhig = _drei_safe(welt)
    db = welt.db
    admin = {"id": "test-admin"}
    try:
        with pytest.raises(OPT.Ungueltig):
            welt.run(OPT.modus_setzen(db, "FULL_AUTO", wer="test-admin"))
        with pytest.raises(HTTPException) as ex:
            welt.run(MA.admin_market_optimierung_modus(MA.ModusIn(modus="FULL_AUTO"), admin=admin))
        assert ex.value.status_code == 400
        info = welt.run(db[K.AENDERUNGEN].index_information())
        assert info["markt_aenderung_aktiv"].get("unique") and info["markt_aenderung_aktiv"]["partialFilterExpression"] == {"status": "aktiv"}
        _rechnen(welt)                                     # OBSERVE: nur Vorschlaege
        welt.run(db[K.KONFIG].update_one({"_id": K.OPTIMIERUNG_DOK}, {"$set": {"modus": "SAFE_AUTO"}}, upsert=True))

        async def _zwei():
            return await asyncio.gather(*[OPT.safe_auto_anwenden(db, tag=_t(0), model_ids=[_mid(welt)]) for _ in range(2)])
        welt.run(_zwei())
        assert welt.run(db[K.AENDERUNGEN].count_documents({"model_id": _mid(welt), "status": "aktiv"})) == 3
        # Confidence LOW -> keine neue Wirkung
        welt.run(OPT.alle_aufheben(db, grund="Test", wer="test"))
        welt.run(db[K.VORSCHLAEGE].update_many({"model_id": _mid(welt)}, {"$set": {"confidence": "LOW", "status": "PROPOSED"}}))
        assert welt.run(OPT.safe_auto_anwenden(db, tag=_t(0), model_ids=[_mid(welt)]))["angewendet"] == 0
        # Routen
        r = welt.run(MA.admin_market_optimierung_modus(MA.ModusIn(modus="SAFE_AUTO"), admin=admin))
        assert r["modus"] == "SAFE_AUTO"
        welt.run(db[K.VORSCHLAEGE].update_many({"model_id": _mid(welt)}, {"$set": {"confidence": "MEDIUM"}}))
        welt.run(OPT.safe_auto_anwenden(db, tag=_t(0), model_ids=[_mid(welt)]))
        liste = welt.run(MA.admin_market_optimierung_aenderungen(status="aktiv", model_id=_mid(welt), limit=50, _=admin))
        assert liste["anzahl"] == 3
        zur = welt.run(MA.admin_market_aenderung_zuruecknehmen(liste["aenderungen"][0]["id"], admin=admin))
        assert zur["aenderung"]["status"] == "zurueckgenommen"
        with pytest.raises(HTTPException) as ex:
            welt.run(MA.admin_market_aenderung_zuruecknehmen(liste["aenderungen"][0]["id"], admin=admin))
        assert ex.value.status_code == 409
        with pytest.raises(HTTPException) as ex:
            welt.run(MA.admin_market_aenderung_zuruecknehmen("gibt-es-nicht", admin=admin))
        assert ex.value.status_code == 404
        u = welt.run(MA.admin_market_optimierung(_=admin))
        assert u["modus"] == "SAFE_AUTO" and u["modi"][1]["gesperrt"] is False and u["modi"][2]["gesperrt"] is True
        aktiv = welt.run(db[K.AENDERUNGEN].find({"status": "aktiv"}, {"_id": 0}).to_list(100))
        assert u["safe_auto_aktiv"] == len(aktiv) == 2
        assert u["ersparnis_safe_auto_usd"] == round(sum(float(a.get("estimated_monthly_saving_usd") or 0) for a in aktiv
                                                         if float(a.get("estimated_monthly_saving_usd") or 0) > 0), 2)
        src = (Path(__file__).resolve().parent.parent / "routes" / "markt_admin.py").read_text(encoding="utf-8")
        for pfad in ('@router.put("/admin/market/optimierung/modus")', '@router.post("/admin/market/optimierung/aenderungen/{aenderung_id}/zuruecknehmen")'):
            kopf = src.split(pfad)[1].split("\n\n")[0]
            assert "current_super_admin" in kopf and "log_activity_sicher" in kopf, pfad
        kopf = src.split('@router.get("/admin/market/optimierung/aenderungen")')[1].split("\n\n")[0]
        assert "Depends(current_admin)" in kopf
        # der Tagesplan liest den Modus (ohne lesbaren Modus: OBSERVE)
        q = inspect.getsource(JOBS.tagesplan)
        assert "_safe_auto_an(db)" in q and "MODUS_SAFE_AUTO" in inspect.getsource(JOBS._safe_auto_an)
    finally:
        _modus_zurueck(welt, alt)
        _aufraeumen(welt)
