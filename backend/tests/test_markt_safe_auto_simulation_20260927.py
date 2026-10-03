# -*- coding: utf-8 -*-
"""Pflicht-Simulation SAFE_AUTO (Pruefbefunde Phase F/G, 27.09.2026): 90 Tage, deterministisch, ohne echte Crawls.

Jeden simulierten Tag: Tagesplan nachgestellt (jobs._ruht mit der SAFE_AUTO-Wirkung am Segment, eine Budget-Rotation:
das Segment 'rotation' bekommt nur jeden zweiten Tag einen Platz), Tagesdokumente direkt geschrieben (Markt je
Segment als feste Regel, einzelne POOR-Ausfaelle), danach optimierung.berechnen mit dem Stichtag als Parameter (Uhr
ausdruecklich uebergeben, kein Mitternachts-Effekt). Modus SAFE_AUTO ab Tag 0; der Tagesplan-Merker meldet die ganze
Zeit eine Budgetgrenze (faellige Segmente warten).

Segmente: leer (EMPTY, ab Tag 70 wieder Treffer), duenn (THIN), gesund (HEALTHY, Auftrag 2x taeglich), heiss (HOT),
rotation (ruhig, Budget-Rotation), abgelehnt (ruhig mit Score-Drift 10-12; Tag 35 lehnt der Super-Admin die Reduktion
ab, Tag 80 hebt er die Ablehnung auf).

Geprueft: keine Wirkung wird ohne widersprechende Daten wieder aufgehoben (kein Pendeln, kein Protokoll-Churn);
Intervalle entsprechen exakt der Zuordnung; die abgelehnte Familie wird bis zur Aufhebung nie angewendet; EMPTY mit
neuen Treffern wird aufgehoben (nicht vorher, trotz Ausfaellen); Budgetgrenze -> 0 $ Ersparnis; nie mehr Abrufe.
"""
import sys
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from test_befunde_runde17_termine import _module, welt  # noqa: E402,F401
from test_markt_20260926 import JOBS, K  # noqa: E402
from test_markt_health_20260927 import H, OPT, _aufraeumen, _doc_daten, _mid, _modell_anlegen, _seg  # noqa: E402

START = "2031-01-01"
TAGE = 90
KM = ((0, 10000), (10001, 20000), (20001, 30000), (30001, 40000), (40001, 50000), (50001, 60000))
POOR = {"leer": {41, 48}, "duenn": {40}, "rotation": {50}, "gesund": {60}}
TREFFER_AB = 70
ABLEHNEN_TAG, AUFHEBEN_TAG = 35, 80


def _tag(d):
    return (datetime.strptime(START, "%Y-%m-%d") + timedelta(days=d)).strftime("%Y-%m-%d")


def _markt(name, d, luecke):
    """(Autos, Ereignisse) des Segments am Tag d; luecke = Tage seit dem letzten gueltigen Lauf (Ereignisse sammeln
    sich ueber die Luecke an)."""
    lk = luecke or 1
    if name == "leer":
        return (3 if d >= TREFFER_AB else 0), {}
    if name == "duenn":
        return [1, 2][d % 2], {}
    if name == "gesund":
        top = any(x % 3 == 0 for x in range(d - lk + 1, d + 1))
        return 5, {"neu": min(5, lk), "weg": min(5, lk), "top5": top}
    if name == "heiss":
        return 5, {"neu": min(5, 2 * lk), "weg": min(5, 2 * lk), "red": min(5, lk), "top3": True, "top5": True}
    if name == "rotation":
        return 5, {}
    if name == "abgelehnt":
        return (2 if (d // 7) % 2 == 0 else [2, 3, 2, 3, 2][d % 5]), {}
    raise AssertionError(name)


def test_safe_auto_90_tage_kein_pendeln(welt):
    db = welt.db
    alt = {i: welt.run(db[K.KONFIG].find_one({"_id": i})) for i in (K.OPTIMIERUNG_DOK, K.TAGESPLAN_DOK)}
    _aufraeumen(welt)
    welt.run(_module("indizes").markt_indizes(db))
    mid = _mid(welt)
    try:
        _modell_anlegen(welt, km=KM)
        segs = {"leer": _seg(welt, km=KM[0]), "gesund": _seg(welt, km=KM[1], cpd=2), "duenn": _seg(welt, km=KM[2]),
                "heiss": _seg(welt, km=KM[3]), "rotation": _seg(welt, km=KM[4]), "abgelehnt": _seg(welt, km=KM[5])}
        name_von = {s["id"]: n for n, s in segs.items()}
        welt.run(db[K.KONFIG].update_one({"_id": K.OPTIMIERUNG_DOK}, {"$set": {"modus": "SAFE_AUTO"}, "$unset": {"frequenz": ""}}, upsert=True))
        letzter_gueltig = {}
        geplant = defaultdict(list)            # name -> [(d, Abrufe)]
        zustand = defaultdict(list)            # name -> [(d, Wirkung)]
        for d in range(TAGE):
            tag = _tag(d)
            # ---- Tagesplan (nachgestellt): SAFE_AUTO-Ruhe, Budget-Rotation, Abrufe je Tag nur gesenkt
            for name, s in segs.items():
                seg = welt.run(db[K.SEGMENTE].find_one({"id": s["id"]}, {"_id": 0}))
                if JOBS._ruht(seg, tag) or (name == "rotation" and d % 2 == 1):
                    continue
                k = int(seg.get("crawls_per_day") or 1)
                if JOBS._wirkung(seg).get("crawls_per_day"):
                    k = max(1, min(k, int(JOBS._wirkung(seg)["crawls_per_day"])))
                welt.run(db[K.SEGMENTE].update_one({"id": s["id"]}, {"$set": {"last_planned_tag": tag}}))
                geplant[name].append((d, k))
                # ---- "Crawl": Tagesdokument direkt schreiben
                luecke = (d - letzter_gueltig[name]) if name in letzter_gueltig else None
                n, ev = _markt(name, d, luecke)
                poor = d in POOR.get(name, ())
                doc = _doc_daten(welt, s, tag, n, dq="POOR" if poor else "GOOD", praefix=(f"{name}{d}" if ev.get("neu") else name),
                                 vergleich=luecke is not None, kosten=0.01, **{x: ev[x] for x in ("neu", "weg", "red", "top3", "top5") if x in ev})
                doc["vergleich_vortag"] = _tag(letzter_gueltig[name]) if name in letzter_gueltig else None
                doc["valid_runs"] = k
                doc["crawl_cost_usd"] = round(0.01 * k, 4)
                welt.run(db[K.TAGESSTATS].update_one({"segment_id": s["id"], "date": tag}, {"$set": doc}, upsert=True))
                if not poor:
                    letzter_gueltig[name] = d
            # ---- Budgetgrenze: der Tagesplan meldet wartende Segmente
            welt.run(K.merker_setzen(db, K.TAGESPLAN_DOK, tag=tag, segmente_je_tag=50, segmente_gesamt=100, ruhend=0, wartend=50))
            # ---- Super-Admin
            if d == ABLEHNEN_TAG:
                v = welt.run(db[K.VORSCHLAEGE].find_one({"segment_id": segs["abgelehnt"]["id"], "typ": "REDUCE_FREQUENCY"}, {"_id": 0}))
                assert v and v["status"] == "APPLIED", v
                welt.run(OPT.vorschlag_entscheiden(db, v["id"], "ablehnen", wer="test-admin"))
            if d == AUFHEBEN_TAG:
                v = welt.run(db[K.VORSCHLAEGE].find_one({"segment_id": segs["abgelehnt"]["id"], "typ": "REDUCE_FREQUENCY"}, {"_id": 0}))
                assert v["status"] == "REJECTED"
                welt.run(OPT.ablehnung_aufheben(db, v["id"], wer="test-admin"))
            # ---- taeglicher Health-/SAFE_AUTO-Lauf nach dem Crawl-Fenster (Uhr als Parameter)
            erg = welt.run(OPT.berechnen(db, stichtag=tag, model_ids=[mid],
                                         jetzt=datetime.strptime(tag, "%Y-%m-%d").replace(hour=12, tzinfo=timezone.utc)))
            assert erg["fehler"] == 0 and erg["budget_grenze"] is True
            for name, s in segs.items():
                w = welt.run(db[K.SEGMENTE].find_one({"id": s["id"]}, {"_id": 0, "safe_auto": 1})).get("safe_auto") or {}
                zustand[name].append((d, (int(w.get("intervall_tage") or 1), w.get("crawls_per_day"), bool(w.get("pausiert")), bool(w.get("hot")))))

        protokoll = welt.run(db[K.AENDERUNGEN].find({"model_id": mid}, {"_id": 0}).sort("at", 1).to_list(200))
        je = defaultdict(list)
        for a in protokoll:
            je[name_von[a["segment_id"]]].append(a)

        def _tag_nr(a, feld="at"):
            return (datetime.strptime(str(a[feld])[:10], "%Y-%m-%d") - datetime.strptime(START, "%Y-%m-%d")).days

        # 1) kein Pendeln: jede beendete Wirkung endete aus einem erlaubten Grund (Daten widersprechen / Ablehnung)
        for a in protokoll:
            if a["status"] == "aktiv":
                continue
            name = name_von[a["segment_id"]]
            if name == "leer" and a["typ"] == "PAUSE_EMPTY":
                assert a["beendet_grund"].startswith("Daten widersprechen") and _tag_nr(a, "beendet_at") >= TREFFER_AB, a
            elif name == "abgelehnt":
                # (die Entscheidung des Super-Admins traegt die echte Uhrzeit, nicht die simulierte)
                assert a["status"] == "zurueckgenommen" and a["beendet_grund"] == "Vorschlag abgelehnt" and a["beendet_von"] == "test-admin"
            else:
                raise AssertionError(f"Wirkung ohne widersprechende Daten aufgehoben: {name} {a['typ']} {a.get('beendet_grund')}")
        # Wechsel der Wirkung je Segment (Tagesstand): hoechstens die erwarteten
        wechsel = {n: sum(1 for (_, x), (_, y) in zip(z, z[1:]) if x != y) for n, z in zustand.items()}
        assert wechsel["gesund"] == 1 and wechsel["duenn"] == 1 and wechsel["heiss"] == 1 and wechsel["rotation"] == 1, wechsel
        assert wechsel["abgelehnt"] == 3, wechsel            # angewendet, abgelehnt (weg), nach Aufhebung wieder an
        assert wechsel["leer"] in (2, 3), wechsel            # pausiert, aufgehoben (Treffer), evtl. danach Frequenz gesenkt
        assert [a["typ"] for a in je["gesund"]] == ["REDUCE_FREQUENCY"] and [a["typ"] for a in je["heiss"]] == ["PRIORITIZE_HOT"]
        assert [a["typ"] for a in je["duenn"]] == ["REDUCE_FREQUENCY"] and [a["typ"] for a in je["rotation"]] == ["REDUCE_FREQUENCY"]
        assert [a["typ"] for a in je["leer"]].count("PAUSE_EMPTY") == 1

        # 2) Intervalle exakt nach der Zuordnung (Score/Status bei der Anwendung), Wirkung am Segment = Protokoll
        cfg = H.FREQUENZ_STANDARD
        for a in protokoll:
            if a["typ"] == "REDUCE_FREQUENCY":
                exakt = H._zuordnung(a["health"], int(a["activity_score"]), cfg)
                assert a["neu"] == {"intervall_tage": exakt["intervall_tage"], "crawls_per_day": exakt["crawls_per_day"]}, a
            if a["typ"] == "PAUSE_EMPTY":
                assert a["neu"] == {"intervall_tage": 7, "crawls_per_day": 1, "pausiert": True}
        assert je["rotation"][0]["neu"] == {"intervall_tage": 2, "crawls_per_day": 1}
        assert je["gesund"][0]["neu"] == {"intervall_tage": 1, "crawls_per_day": 1}, "2x -> 1x taeglich"
        assert je["duenn"][0]["neu"]["intervall_tage"] >= H.THIN_MIN_INTERVALL_TAGE
        for name in ("gesund", "duenn", "heiss", "rotation"):
            assert je[name][0]["status"] == "aktiv"
            letzter = zustand[name][-1][1]
            neu = je[name][0]["neu"]
            if "intervall_tage" in neu:
                assert letzter[0] == neu["intervall_tage"], (name, letzter, neu)

        # 3) abgelehnte Familie nie angewendet — bis zur ausdruecklichen Aufhebung
        for d, (n, k, p, hot) in zustand["abgelehnt"]:
            if ABLEHNEN_TAG <= d < AUFHEBEN_TAG:
                assert n == 1 and k is None, (d, n, k)
        assert zustand["abgelehnt"][-1][1][0] > 1, "nach der Aufhebung wieder angewendet"
        reduce_abgelehnt = [a for a in je["abgelehnt"] if a["typ"] == "REDUCE_FREQUENCY"]
        assert len(reduce_abgelehnt) == 2 and _tag_nr(reduce_abgelehnt[1]) == AUFHEBEN_TAG

        # 4) EMPTY: Pause haelt trotz Ausfaellen (Tag 41/48 POOR) bis zu den neuen Treffern, dann aufgehoben
        pause = next(a for a in je["leer"] if a["typ"] == "PAUSE_EMPTY")
        start_pause, ende_pause = _tag_nr(pause), _tag_nr(pause, "beendet_at")
        assert start_pause <= 14 and TREFFER_AB <= ende_pause <= TREFFER_AB + 14, (start_pause, ende_pause)
        for d, (n, k, p, hot) in zustand["leer"]:
            if start_pause <= d < ende_pause:
                assert p and n == 7, (d, n, p)
        tage_leer = [d for d, _ in geplant["leer"] if start_pause < d <= ende_pause]
        assert all(b - a == 7 for a, b in zip(tage_leer, tage_leer[1:])), tage_leer
        # danach nicht mehr pausiert: wieder taeglich — oder, wenn die Daten noch duenn sind, die exakte Frequenz der
        # Zuordnung (Frequenz senken als eigene Familie, im selben Lauf; die Reduktionskette laeuft weiter)
        nachher = [a for a in je["leer"] if a["typ"] == "REDUCE_FREQUENCY"]
        n_nach = nachher[0]["neu"]["intervall_tage"] if nachher else 1
        assert not any(p for d, (n, k, p, hot) in zustand["leer"] if d >= ende_pause)
        tage_nach = [d for d, _ in geplant["leer"] if d > ende_pause]
        assert tage_nach and all(b - a == n_nach for a, b in zip([ende_pause] + tage_nach, tage_nach)), (n_nach, tage_nach)
        if nachher:
            assert nachher[0]["reduziert_seit"] == pause["reduziert_seit"] and nachher[0]["status"] == "aktiv"

        # 5) nie mehr Abrufe als der Auftrag vorsieht; HOT ohne Zusatzabrufe
        assert all(k <= 2 for _, k in geplant["gesund"]) and all(k == 1 for _, k in geplant["heiss"])
        start_g = _tag_nr(je["gesund"][0])
        assert all(k == 1 for d, k in geplant["gesund"] if d > start_g)

        # 6) Budgetgrenze: keine Dollar-Ersparnis, nur frei werdende Laeufe (Rotation: alle 2 Tage spart nichts)
        for a in protokoll:
            assert a["estimated_monthly_saving_usd"] in (0.0, None) and a["ersparnis_budget_grenze"] is True, a
        assert je["rotation"][0]["laeufe_frei_monat"] == 0.0 and je["duenn"][0]["laeufe_frei_monat"] > 0
        # Protokoll ohne Churn: hoechstens ein Eintrag je Wirkungswechsel
        assert len(protokoll) <= sum(wechsel.values())
        print("SIMULATION", {"wechsel": wechsel, "protokoll": [(name_von[a["segment_id"]], a["typ"], a["neu"], a["status"], _tag_nr(a),
                                                                a.get("beendet_grund")) for a in protokoll],
                             "pause": (start_pause, ende_pause), "abrufe": {n: len(g) for n, g in geplant.items()}})
    finally:
        for i, a in alt.items():
            if a:
                welt.run(db[K.KONFIG].replace_one({"_id": i}, a, upsert=True))
            else:
                welt.run(db[K.KONFIG].delete_one({"_id": i}))
        _aufraeumen(welt)
