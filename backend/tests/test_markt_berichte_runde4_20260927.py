# -*- coding: utf-8 -*-
"""Pruefung Runde 4 der Berichte (Phase E, 27.09.2026) — Schema 4.

Je Befund ein Test mit den nachgerechneten Szenarien der Pruefer (Zahlen vor der Korrektur im Docstring und, wo die
Rechnung sie noch zeigt, als roher Tageswert im Test):
  #1  ganztaegiger Ausfall des Tagesplans ist eine Luecke (Tagesplan-Protokoll), Crawler bewusst aus / Budget nicht
  #2  Intervall hoechstens 14 Tage; abgelaufene Werte fallen aus dem wirksamen Korb (Rotation im Intervall 14 und 21)
  #3  Serienende auf Ebene der Fassung (Pause unter Rotation), Anker nur mit Mindestabdeckung gegen den vollen Korb
  #4  Serienstart: schon angelegte, noch nicht beobachtete Segmente fehlen (kein Teilkorb-Anker)
  #5  vorlaeufiger Bericht am ersten Periodentag, ausstehende Laeufe, Korb ueber die Bloecke stabil
  #6  Confidence mit Mindesttagen
Dazu: Laufzeit eines Monatsberichts mit 180 Segmenten (Job-/Protokoll-Abfrage und Rechnung getrennt gemessen).
Alle Tage werden ausdruecklich uebergeben; Testdaten nur test-/t<hex>, Protokolltage 2028-*/2099-*.
"""
import asyncio
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from test_befunde_runde17_termine import _module, welt  # noqa: E402,F401
from test_markt_20260926 import BUD, JOBS, K  # noqa: E402
from test_markt_berichte_20260927 import (B, _doc, _doc_daten, _job_db, _rechnen, _rotation_korb, _seg, _seg_daten,  # noqa: E402
                                          _start, _t)
from test_markt_hotdeals_20260927 import _aufraeumen  # noqa: E402


def _korb_ez(welt, praefix="", **kw):
    """8 EZ x 3 km = 24 Segmente wie im Befund; IDs in EZ-Reihenfolge (aelteste = guenstigste zuerst — so sortiert der
    Tagesplan bei gleichem last_planned_tag), Preis 14.000 + 2.500 x EZ-Index, je 5 Inserate: wahres Niveau 22.750 EUR."""
    segs, preis = [], {}
    for e in range(8):
        for k in range(3):
            i = e * 3 + k
            s = _seg_daten(welt, f"{100000 + i * 1000}-{100000 + i * 1000 + 999}", praefix=praefix, **kw)
            segs.append(s)
            preis[s["id"]] = 14000.0 + 2500 * e
    return segs, preis


def _tagesplan(segs, tage, je_tag, letzter=None):
    """Wie jobs.tagesplan: faellig = last_planned_tag < Tag, sortiert nach (last_planned_tag, id), die ersten je_tag.
    Liefert Segment -> geplante Tage."""
    stand = {s["id"]: "" for s in segs}
    stand.update(letzter or {})
    plan = {}
    for t in tage:
        faellig = sorted((s["id"] for s in segs if stand[s["id"]] < t), key=lambda sid: (stand[sid], sid))
        for sid in faellig[:je_tag]:
            plan.setdefault(sid, set()).add(t)
            stand[sid] = t
    return plan


def _docs_aus_plan(welt, segs, preis, plan):
    return [_doc_daten(welt, s, t, preis[s["id"]]) for s in segs for t in sorted(plan.get(s["id"], ()))]


def _k(k):
    return B._plus(_t(1), k)


def _protokoll_leeren(welt, bis="2028-03-01"):
    """Tagesplan-Protokolle vor 'bis' entfernen (nur Test-Datenbank) — die Einfuehrung des Protokolls ist der erste
    Protokolltag; Reste anderer Tests (z. B. von heute) wuerden sie sonst vorverlegen."""
    welt.run(welt.db[K.TAGESPLAN_LOG].delete_many({"tag": {"$lt": bis}}))


# ---------------------------------------------------------------- #3 Serienende
def test_30_runde4_serienende_pause_unter_rotation(welt):
    """Runde 4 #3 (mittel): 24 Segmente (8 EZ x 3 km), Budget-Rotation wie jobs.tagesplan (je Tag 18 von 24 nach
    last_planned_tag, dann id), Auftrag nach dem letzten Lauf am 05. pausiert, FIVE_DAY 01.-05. Am 05. fehlen die
    teuersten 6 (EZ 6/7) im Plan. Vorher fiel ein abgeschaltetes Segment ab seinem EIGENEN letzten Plan aus dem Korb:
    der 05. war Anker aus 18 Segmenten — Minimum 20.250 EUR am 05. (wahr 22.750, -11 %), Mittel 22.250 EUR,
    Stichproben-Aenderung -10,989 %, eingefroren mit HIGH; mit abwechselnden Haelften 17.500 EUR (-25,5 %).
    Jetzt: aktiv bis zum letzten Plan IRGENDEINES Segments der Fassung bzw. bis zum Abschalten — alle Werte 22.750."""
    segs, preis = _korb_ez(welt)
    tage = [_k(k) for k in range(-14, 5)]
    plan = _tagesplan(segs, tage, 18)
    docs = _docs_aus_plan(welt, segs, preis, plan)
    assert [s["id"] for s in segs if _t(5) not in plan[s["id"]]] == [s["id"] for s in segs[18:]], "am 05. fehlen EZ 6 und 7"
    for abschalt in ("2028-02-05T20:00:00+00:00", "2028-02-06T07:00:00+00:00", None):     # nach dem Lauf, nach Periodenende, unbekannt
        pausiert = [{**s, "enabled": False, **({"updated_at": abschalt} if abschalt else {})} for s in segs]
        ber = _rechnen(welt, pausiert, docs, geplant=plan)
        k, tag5 = ber["kennzahlen"], ber["tage"][4]
        assert tag5["median"] == 20250, "roher Tageswert = die 18 gelaufenen Segmente (vorher der Anker)"
        assert (tag5["median_korb"], tag5["nicht_geplante_segmente"], tag5["korb_abdeckung_pct"], tag5["anker"]) == (22750, 6, 100.0, True), abschalt
        assert (k["minimum"], k["maximum"]) == ({"date": _t(1), "wert": 22750}, {"date": _t(1), "wert": 22750}), abschalt
        assert (k["mittelwert_periode"], k["median_periode"], k["sample_market_change_eur"], k["sample_market_change_pct"]) == (22750, 22750, 0, 0)
        assert (k["anker_tage"], k["teilabgedeckte_tage"], k["segment_luecken"]) == (5, 0, 0)
    # abwechselnde Haelften (24 Segmente 12.000-35.000, wahr 23.500), am 05. die guenstige Haelfte, danach pausiert
    segs2, preis2 = _rotation_korb(welt)
    plan2, docs2 = {}, []
    for k in range(-14, 5):
        for i, s in enumerate(segs2):
            if (i < 12) == (k % 2 == 0):
                plan2.setdefault(s["id"], set()).add(_k(k))
                docs2.append(_doc_daten(welt, s, _k(k), preis2[s["id"]]))
    pausiert = [{**s, "enabled": False, "updated_at": "2028-02-05T21:00:00+00:00"} for s in segs2]
    ber = _rechnen(welt, pausiert, docs2, geplant=plan2)
    k = ber["kennzahlen"]
    assert ber["tage"][4]["median"] == 17500 and abs(ber["tage"][4]["median_korb"] - 23500) <= 1
    assert abs(k["minimum"]["wert"] - 23500) <= 1 and abs(k["sample_market_change_eur"]) <= 1


def test_31_runde4_anker_braucht_mindestabdeckung_gegen_den_vollen_korb(welt):
    """Runde 4 #3: auch ein Tag ohne technisch fehlendes Segment ist nur Anker, wenn er ANKER_MIN_ABDECKUNG des VOLLEN
    Korbs deckt. 24 Segmente; 7 davon werden erst am 02. angelegt (created_at 02., erster Job 02.) — am 01. sind sie
    'neu' (Mix, keine Luecke), der 01. deckt aber nur 17/24 = 70,8 % des Korbs: kein Anker (vorher: vollstaendig, Anker).
    Sein Wert bleibt die Marktinformation (neue Segmente = Mix) ueber die Kette vom 02."""
    segs, preis = _korb_ez(welt)
    spaet = {s["id"] for s in segs[17:]}
    segs = [{**s, "created_at": "2028-02-02T01:00:00+00:00"} if s["id"] in spaet else s for s in segs]
    plan = {s["id"]: {_k(k) for k in range(-14 if s["id"] not in spaet else 1, 5)} for s in segs}
    docs = _docs_aus_plan(welt, segs, preis, plan)
    ber = _rechnen(welt, segs, docs, geplant=plan)
    tag1, k = ber["tage"][0], ber["kennzahlen"]
    assert (tag1["anker"], tag1["korb_abdeckung_pct"], tag1["teilabdeckung"], tag1["vorab_segmente"]) == (False, 70.8, False, 0)
    assert abs(tag1["median_korb"] - tag1["median"]) <= 1, "Mix bleibt Marktinformation (neue Serie), nichts aufgefuellt"
    assert (k["anker_tage"], k["maximum"]["wert"], k["vorab_segment_tage"]) == (4, 22750, 0)


# ---------------------------------------------------------------- #4 Serienstart
def test_32_runde4_serienstart_schon_angelegte_segmente_fehlen(welt):
    """Runde 4 #4 (mittel): Fassungswechsel am 03. (neue Segment-IDs, created_at 03.), unter Rotation bekommt der
    Auftrag nur das restliche Kontingent: 17 der 24 neuen Segmente am 03. (ID-Reihenfolge = guenstigste EZ zuerst),
    der Rest am 04., danach Rotation (18 je Tag). Vorher war der 03. Anker aus 17 Segmenten: Minimum 19.882 EUR am 03.
    (wahr 22.750, -12,6 %), Mittel 21.794 EUR, Stichproben-Aenderung +14,4 %, HIGH. Jetzt fehlen die 7 schon
    angelegten, noch nicht beobachteten am 03. ('vorab'), der 03. ist kein Anker und rueckwaerts vom 04. verkettet.
    Sind die 7 erst am 04. angelegt (neu), bleibt der 03. Mix — Marktinformation, aber auch kein Anker (Abdeckung)."""
    alt, preis_alt = _korb_ez(welt, enabled=False)
    neu, preis = _korb_ez(welt, praefix="v2:", version=2, dh="h2")
    neu = [{**s, "created_at": "2028-02-03T01:00:00+00:00"} for s in neu]      # 02:00 deutscher Zeit am 03.
    plan = {s["id"]: {_k(k) for k in range(-14, 2)} for s in alt}
    plan.update({s["id"]: {_t(3)} for s in neu[:17]})
    plan_rest = _tagesplan(neu, [_t(4), _t(5)], 18, letzter={s["id"]: _t(3) for s in neu[:17]})
    for sid, tage in plan_rest.items():
        plan.setdefault(sid, set()).update(tage)
    docs = _docs_aus_plan(welt, alt, preis_alt, plan) + _docs_aus_plan(welt, neu, preis, plan)
    ber = _rechnen(welt, alt + neu, docs, geplant=plan)
    tage, k = {r["date"]: r for r in ber["tage"]}, ber["kennzahlen"]
    assert round(tage[_t(3)]["median"], 2) == 19882.35, "roher Tageswert der 17 (vorher der Anker)"
    assert (tage[_t(3)]["vorab_segmente"], tage[_t(3)]["anker"], tage[_t(3)]["teilabdeckung"]) == (7, False, False)
    assert tage[_t(3)]["median_korb"] == 22750 and tage[_t(4)]["anker"] and tage[_t(5)]["anker"]
    assert (k["minimum"]["wert"], k["maximum"]["wert"], k["mittelwert_periode"], k["sample_market_change_eur"]) == (22750, 22750, 22750, 0)
    assert k["vorab_segment_tage"] == 7 and any(h.startswith("Serienstart: 7 Segment-Tag(e)") for h in ber["hinweise"]), ber["hinweise"]
    assert (k["segment_luecken"], k["teilabgedeckte_tage"]) == (0, 0), "vorab ist kein technischer Ausfall"
    # die 7 erst am 04. angelegt (und erst dann geplant): am 03. neu (Mix), keine vorab-Luecke
    neu2 = [{**s, "created_at": "2028-02-04T01:00:00+00:00"} if i >= 17 else s for i, s in enumerate(neu)]
    ber = _rechnen(welt, alt + neu2, docs, geplant=plan)
    tage, k = {r["date"]: r for r in ber["tage"]}, ber["kennzahlen"]
    assert (tage[_t(3)]["vorab_segmente"], tage[_t(3)]["anker"], k["vorab_segment_tage"]) == (0, False, 0)
    assert round(tage[_t(3)]["median_korb"], 2) == 19882.35, "echte neue Segmente: Mix bleibt Marktinformation"


# ---------------------------------------------------------------- #2 Intervall
def test_33_runde4_rotation_im_intervall_14_und_21(welt):
    """Runde 4 #2 (mittel): jedes Segment wird alle n Tage geplant, versetzt (Segment i an Tagen k mit (k - i) % n == 0).
    n = 14 (Obergrenze konfig.MAX_INTERVALL_TAGE): jeder nicht geplante Wert ist hoechstens 13 Tage alt und wird
    getragen — jeder Tag vollstaendig, Anker, 23.500 EUR. n = 21/30 (Budget reicht nicht, SAFE_AUTO-Ruhe): an jedem Tag
    fehlt ~1/3 der Werte (aelter als 14 Tage bzw. im Fenster noch nie beobachtet). Runde 4b: 8831027 nahm die
    abgelaufenen aus dem Nenner und wertete die noch nie beobachteten als neu (Mix) — Ersatz-Anker 07. mit 24.166,67
    (18 von 24), Maximum 26.475,40, Stichproben-Aenderung -8,72 %, HIGH ohne Gruende (n = 30: Median 18.842,98). Jetzt
    bleibt ihr Wert fehlend: kein Anker, alle Euro-Niveauwerte None mit Hinweis, keine technische Luecke, nicht HIGH."""
    assert K.MAX_INTERVALL_TAGE == 14 and B.TRAGEN_MAX_TAGE == B.VORLAUF_TAGE >= K.MAX_INTERVALL_TAGE
    segs, preis = _rotation_korb(welt)
    wahr = sum(preis.values()) / 24
    for n in (14, 21, 30):
        plan, docs = {}, []
        for k in range(-14, 29):
            for i, s in enumerate(segs):
                if (k - i) % n == 0:
                    plan.setdefault(s["id"], set()).add(_k(k))
                    docs.append(_doc_daten(welt, s, _k(k), preis[s["id"]], n=12))
        ber = _rechnen(welt, segs, docs, "MONTHLY", _t(1), _t(29), geplant=plan)
        k = ber["kennzahlen"]
        assert (k["teilabgedeckte_tage"], k["segment_luecken"]) == (0, 0), n
        if n == 14:
            assert (k["anker_tage"], k["abgelaufene_segment_tage"], k["niveau_tage"]) == (29, 0, 29)
            assert all(abs(r["median_korb"] - wahr) <= 1 for r in ber["tage"])
            assert k["confidence"] == "HIGH" and abs(k["startwert"] - wahr) <= 1 and abs(k["endwert"] - wahr) <= 1
            assert not any("Kein Tag mit ausreichender Korbabdeckung" in h for h in ber["hinweise"])
        else:
            assert k["abgelaufene_segment_tage"] > 0 and k["anker_tage"] == 0, (n, k)
            assert (k["median_periode"], k["mittelwert_periode"], k["minimum"], k["maximum"], k["sample_market_change_pct"]) == (
                None, None, None, None, None), n
            assert all(r["median_korb"] is None for r in ber["tage"]), n
            assert any("ohne tragbaren Wert" in h and "Intervall über 14 Tage" in h for h in ber["hinweise"]), ber["hinweise"]
            assert any("Kein Tag mit ausreichender Korbabdeckung" in h for h in ber["hinweise"]), ber["hinweise"]
            assert k["confidence"] != "HIGH" and any("ohne tragbaren Wert" in g for g in k["confidence_gruende"]), k["confidence_gruende"]
            assert any(r["abgelaufene_segmente"] for r in ber["tage"])


def test_34_runde4_intervall_hoechstens_14_tage(welt, monkeypatch):
    """Runde 4 #2: MARKT_CRAWL_INTERVALL_TAGE hoechstens 14 (vorher 30); jobs.intervall setzt 'budget_reicht_nicht', wenn
    das Budget nicht fuer jedes Segment alle 14 Tage reicht — Runde 4b: das automatische Intervall bleibt dann am
    Budget (ehrlich ueber 14, kein hoeheres Kontingent, siehe test_48); ist das Restbudget aufgebraucht,
    'budget_erschoepft' (1 Segment je Tag wie bisher)."""
    db = welt.db
    s = welt.w.s
    _aufraeumen(welt)
    monkeypatch.setenv("MARKT_CRAWL_INTERVALL_TAGE", "21")
    assert K.crawl_intervall_tage() == 14
    monkeypatch.setenv("MARKT_ENTFERNUNG_PRUEFEN", "false")
    monkeypatch.setattr(K, "monat", lambda zeit=None: f"test-{s}")
    monkeypatch.setattr(K, "rest_tage_im_monat", lambda zeit=None: 10)
    try:
        welt.run(db[K.MODELLE].insert_one({"id": f"test-int-{s}", "model_id": "x", "status": "active"}))
        welt.run(db[K.SEGMENTE].insert_many([{"id": f"test-int-{s}:{i}", "model_id": f"test-int-{s}", "enabled": True, "max_items": 20,
                                              "crawls_per_day": 1} for i in range(30)]))
        gesamt = welt.run(db[K.SEGMENTE].count_documents({"enabled": True}))     # >= 30 (andere Tests koennen Segmente haben)
        welt.run(BUD.budget_setzen(db, 100, f"test-{s}"))
        t = welt.run(JOBS.intervall(db))
        assert (t["intervall_tage"], t["segmente_je_tag"], t["budget_reicht_nicht"], t["automatisch"]) == (14, -(-gesamt // 14), False, False)
        monkeypatch.setenv("MARKT_CRAWL_INTERVALL_TAGE", "0")
        if gesamt == 30:
            t = welt.run(JOBS.intervall(db))
            assert (t["intervall_tage"], t["budget_reicht_nicht"], t["budget_erschoepft"]) == (1, False, False)
        welt.run(BUD.budget_setzen(db, 0.3, f"test-{s}"))                  # 0,03 $ je Tag: 1 Segment -> >= 30 Tage
        t = welt.run(JOBS.intervall(db))
        assert (t["budget_reicht_nicht"], t["budget_erschoepft"]) == (True, False) and t["segmente_je_tag"] < -(-gesamt // 14), t
        assert t["intervall_tage"] == -(-gesamt // t["segmente_je_tag"]) > 14, t
        welt.run(db[K.BUDGET].update_one({"_id": f"test-{s}"}, {"$set": {"used_usd": 0.3}}))
        t = welt.run(JOBS.intervall(db))
        assert (t["segmente_je_tag"], t["budget_erschoepft"], t["budget_reicht_nicht"]) == (1, True, True), t
    finally:
        _aufraeumen(welt)
        welt.run(db[K.SEGMENTE].delete_many({"model_id": f"test-int-{s}"}))
        welt.run(db[K.MODELLE].delete_many({"id": f"test-int-{s}"}))


# ---------------------------------------------------------------- #1 Tagesplan-Protokoll
def _modell_24(welt):
    mid = _start(welt)
    segs = [_seg(welt, f"{100000 + i * 1000}-{100000 + i * 1000 + 999}") for i in range(24)]
    return mid, segs


def _lauf(welt, segs, tag, preis=20000, **job):
    for s in segs:
        _doc(welt, s, tag, preis, n=12)
        _job_db(welt, s, tag, job.get("status", "completed"), job.get("error"))


def test_35_runde4_ganztaegiger_planausfall_ist_eine_luecke(welt):
    """Runde 4 #1 (mittel, beide Pruefer): 24 Segmente taeglich, am 03. und 04. lief der Tagesplan nicht (Token fehlt,
    Wartung, Exception, beide Server aus) — kein einziger Job. Vorher: expected_days 3, 3/3, 72/72 Segment-Tage, HIGH und
    'keine Datenluecke' (eingefroren). Jetzt mit Tagesplan-Protokoll: ohne Protokoll ab dessen Einfuehrung technischer
    Ausfall — 3/5 Tage, 72/120 Segment-Tage, MEDIUM, Hinweis 'Tagesplan lief nicht'. Crawler bewusst aus bzw. Budget
    (Protokoll) = nicht geplant (3/3, HIGH, eigener Hinweis). Altdaten vor dem Protokoll: Tag gilt als gelaufen, wenn es
    fuer IRGENDEIN Segment einen Tagesplan-Job gibt, sonst Planung unbekannt (erwartet)."""
    mid, segs = _modell_24(welt)
    db = welt.db
    try:
        _protokoll_leeren(welt)
        for tag in ("2028-01-31", _t(1), _t(2), _t(5)):
            _lauf(welt, segs, tag)
            assert welt.run(K.tagesplan_protokollieren(db, tag, lief=True, segmente_geplant=24, budget_grund=None))
        ber = welt.run(B.bericht_berechnen(db, mid, "FIVE_DAY", _t(1), _t(5)))
        k, tage = ber["kennzahlen"], {r["date"]: r for r in ber["tage"]}
        assert (k["expected_days"], k["coverage_days"], k["segment_tage_gueltig"], k["segment_tage_erwartet"]) == (5, 3, 72, 120)
        assert (k["confidence"], k["tage_tagesplan_ausfall"], k["tage_ohne_plan"]) == ("MEDIUM", 2, 0) and "Abdeckung 3/5 Tage" in k["confidence_gruende"]
        assert (tage[_t(3)]["plan_status"], tage[_t(2)]["plan_status"]) == (B.PLAN_AUSFALL, B.PLAN_LIEF)
        assert any(h.startswith("Tagesplan lief nicht: 2 Kalendertag(e)") for h in ber["hinweise"]), ber["hinweise"]
        assert any("3 / 5 gültige Tage" in h for h in ber["hinweise"]) and not any("keine Datenlücke" in h for h in ber["hinweise"])
        # Crawler bewusst aus (Worker-Protokoll): nicht geplant
        for tag in (_t(3), _t(4)):
            assert welt.run(K.tagesplan_protokollieren(db, tag, lief=False))
        ber = welt.run(B.bericht_berechnen(db, mid, "FIVE_DAY", _t(1), _t(5)))
        k = ber["kennzahlen"]
        assert (k["expected_days"], k["coverage_days"], k["segment_tage_erwartet"], k["tage_crawler_aus"], k["tage_ohne_plan"]) == (3, 3, 72, 2, 2)
        assert (k["confidence"], k["kalendertage"], k["tage_mit_wert"], k["getragene_tage"]) == ("HIGH", 5, 5, 2)
        assert any(h.startswith("Crawler bewusst ausgeschaltet: 2 Kalendertag(e)") for h in ber["hinweise"])
        assert ber["tage"][2]["plan_status"] == B.PLAN_AUS
        # Budget 0 bzw. aufgebraucht (Tagesplan lief, budget_grund): nicht geplant wegen Budget
        welt.run(db[K.TAGESPLAN_LOG].delete_many({"tag": {"$in": [_t(3), _t(4)]}}))
        welt.run(K.tagesplan_protokollieren(db, _t(3), lief=True, segmente_geplant=0, budget_grund=K.BUDGET_GRUND_OHNE))
        welt.run(K.tagesplan_protokollieren(db, _t(4), lief=True, segmente_geplant=1, budget_grund=K.BUDGET_GRUND_ERSCHOEPFT))
        ber = welt.run(B.bericht_berechnen(db, mid, "FIVE_DAY", _t(1), _t(5)))
        k = ber["kennzahlen"]
        assert (k["expected_days"], k["coverage_days"], k["tage_budget"], k["tage_tagesplan_ausfall"]) == (3, 3, 2, 0)
        assert any(h.startswith("Nicht geplant wegen Budget: 2 Kalendertag(e)") for h in ber["hinweise"]), ber["hinweise"]
        # Altdaten (vor jedem Protokoll): ohne jeden Tagesplan-Job an dem Tag -> Planung unbekannt (erwartet)
        _protokoll_leeren(welt)
        ber = welt.run(B.bericht_berechnen(db, mid, "FIVE_DAY", _t(1), _t(5)))
        k = ber["kennzahlen"]
        assert (k["expected_days"], k["coverage_days"], k["tage_planung_unbekannt"], k["confidence"]) == (5, 3, 2, "MEDIUM")
        assert any(h.startswith("Planung unbekannt: 2 Kalendertag(e)") for h in ber["hinweise"])
        # ... mit einem Tagesplan-Job eines ANDEREN Modells an beiden Tagen: der Plan lief -> nicht geplant
        fremd = {"id": f"test-fremd-{welt.w.s}", "model_id": f"test-fremd-{welt.w.s}", "version": 1, "definition_hash": "h1"}
        for tag in (_t(3), _t(4)):
            _job_db(welt, fremd, tag, "completed")
        ber = welt.run(B.bericht_berechnen(db, mid, "FIVE_DAY", _t(1), _t(5)))
        k = ber["kennzahlen"]
        assert (k["expected_days"], k["coverage_days"], k["tage_planung_unbekannt"], k["tage_ohne_plan"]) == (3, 3, 0, 2)
    finally:
        _protokoll_leeren(welt)
        _aufraeumen(welt)


def test_36_runde4_budget_storno_ist_nicht_geplant_und_jobs_bis_heute(welt):
    """Runde 4 #1: Budget danach erschoepft — der Job des 03. wartete am Budget (budget_wait) und wurde am Folgetag
    'Tagesplan veraltet' storniert: nicht geplant wegen Budget (getragen, keine Luecke), einheitlich mit 'Budget vor dem
    Plan erschoepft'. Ohne budget_wait bleibt 'Tagesplan veraltet' ein technischer Ausfall (Kapazitaet). Runde 4 #3: die
    Job-Abfrage reicht bis heute (letzter Plan nach 'bis')."""
    mid = _start(welt)
    a, b, c = _seg(welt, "20000-40000"), _seg(welt, "40001-60000"), _seg(welt, "60001-80000")
    db = welt.db
    try:
        _protokoll_leeren(welt)
        for d in ("2028-01-31", _t(1), _t(2), _t(3)):
            welt.run(K.tagesplan_protokollieren(db, d, lief=True, segmente_geplant=3, budget_grund=None))
        for s, preis in ((a, 12000), (b, 35000), (c, 23500)):
            for d in ("2028-01-31", _t(1), _t(2)):
                _doc(welt, s, d, preis)
                _job_db(welt, s, d, "completed")
        _doc(welt, a, _t(3), 12000)
        _job_db(welt, a, _t(3), "completed")
        welt.run(db[K.JOBS].insert_one({"id": f"test-job-b3-{welt.w.s}", "segment_id": b["id"], "model_id": mid, "tag": _t(3),
                                        "status": "cancelled", "error": JOBS.STORNO_ALT, "budget_wait": True}))
        _job_db(welt, c, _t(3), "cancelled", JOBS.STORNO_ALT)
        _job_db(welt, a, "2028-02-07", "queued")                              # nach 'bis' (heute noch geplant)
        jobs = welt.run(B._jobs_laden(db, sorted([a["id"], b["id"], c["id"]]), _t(1), _t(3), "2028-02-09"))
        assert jobs["budget"] == {b["id"]: {_t(3)}} and _t(3) not in jobs["geplant"][b["id"]] and _t(3) in jobs["geplant"][c["id"]]
        assert jobs["letzter"][a["id"]] == "2028-02-07" and jobs["erster"][a["id"]] == "2028-01-31" and _t(3) in jobs["plan"]
        geplant, planung = welt.run(B._planung_laden(db, sorted([a["id"], b["id"], c["id"]]), _t(1), _t(3)))
        segs = {s["id"]: s for s in (a, b, c)}
        docs = welt.run(db[K.TAGESSTATS].find({"segment_id": {"$in": list(segs)}}, {"_id": 0}).to_list(None))
        ber = B.bericht_rechnen({"id": mid}, segs, docs, "FIVE_DAY", _t(1), _t(5), geplant=geplant, planung=planung)
        tag3, k = ber["tage"][2], ber["kennzahlen"]
        assert (tag3["fehlende_segmente"], tag3["nicht_geplante_segmente"], tag3["budget_segmente"]) == (1, 1, 1), "c technisch, b Budget"
        assert k["budget_segment_tage"] == 1 and any("Nicht geplant wegen Budget: 0 Kalendertag(e)" in h and "1 Segment-Tag(e)" in h
                                                     for h in ber["hinweise"]), ber["hinweise"]
    finally:
        _protokoll_leeren(welt)
        _aufraeumen(welt)


def test_37_runde4_tagesplan_und_worker_schreiben_das_protokoll(welt, monkeypatch):
    """Runde 4 #1: jobs.tagesplan protokolliert jeden Lauf (auch ohne Budget: budget_grund), atomar je Tag (zwei Server
    gleichzeitig: ein Dokument); der Worker protokolliert 'Crawler bewusst aus' — nicht bei fehlendem Token und nicht bei
    Wartung (technischer Ausfall, kein Protokoll)."""
    db = welt.db
    s = welt.w.s
    _aufraeumen(welt)
    welt.run(_module("indizes").markt_indizes(db))
    tag = f"2099-11-{int(s[:1], 16) % 20 + 1:02d}"
    tag_aus = "2099-12-30"
    schalter = welt.run(db[K.KONFIG].find_one({"_id": K.SCHALTER_DOK}))
    try:
        welt.run(db[K.TAGESPLAN_LOG].delete_many({"tag": {"$in": [tag, tag_aus]}}))
        monkeypatch.setenv("MARKT_BUDGET_MONAT_USD", "100")
        monkeypatch.setenv("MARKT_CRAWL_INTERVALL_TAGE", "1")
        monkeypatch.setattr(K, "monat", lambda zeit=None: f"test-{s}")
        welt.run(JOBS.tagesplan(db, tag))
        doc = welt.run(db[K.TAGESPLAN_LOG].find_one({"tag": tag}, {"_id": 0}))
        assert doc["lief_at"] and doc["crawler_aktiv"] is True and doc["budget_grund"] is None and doc["laeufe"] == 1
        welt.run(BUD.budget_setzen(db, 0, f"test-{s}"))
        assert welt.run(JOBS.tagesplan(db, tag))["status"] == "ohne Budget pausiert"
        doc = welt.run(db[K.TAGESPLAN_LOG].find_one({"tag": tag}, {"_id": 0}))
        assert (doc["budget_grund"], doc["laeufe"], doc["segmente_geplant"]) == (K.BUDGET_GRUND_OHNE, 2, 0)
        # zwei Server gleichzeitig auf einen neuen Tag: ein Dokument, beide Laeufe gezaehlt
        welt.run(db[K.TAGESPLAN_LOG].delete_many({"tag": tag}))

        async def _zwei():
            return await asyncio.gather(*[K.tagesplan_protokollieren(db, tag, lief=True, segmente_geplant=1) for _ in range(2)])
        assert welt.run(_zwei()) == [True, True]
        assert welt.run(db[K.TAGESPLAN_LOG].count_documents({"tag": tag})) == 1
        assert welt.run(db[K.TAGESPLAN_LOG].find_one({"tag": tag}))["laeufe"] == 2
        # Worker: Crawler bewusst aus -> Protokoll; Token fehlt / Wartung -> keins
        monkeypatch.setattr(K, "heute_tag", lambda zeit=None: tag_aus)

        class _Stopp(BaseException):
            pass

        def _worker(aktiv, token, wartung):
            K._AUS_PROTOKOLLIERT["tag"] = ""
            welt.run(K.crawler_schalten(db, aktiv))
            monkeypatch.setenv("APIFY_TOKEN", token)
            aufrufe = []

            async def _schlaf(sek):
                aufrufe.append(sek)
                if len(aufrufe) >= 2:
                    raise _Stopp()

            async def _wartung(db_):
                return wartung
            with monkeypatch.context() as m:
                m.setattr(JOBS.asyncio, "sleep", _schlaf)
                m.setattr(JOBS, "_wartung_aktiv", _wartung)
                try:
                    welt.run(JOBS.worker_forever(db))
                except _Stopp:
                    pass
            return aufrufe
        assert _worker(True, "", False) == [15, 60]
        assert welt.run(db[K.TAGESPLAN_LOG].count_documents({"tag": tag_aus})) == 0, "Token fehlt = technischer Ausfall"
        assert _worker(True, "tok", True) == [15, 60]
        assert welt.run(db[K.TAGESPLAN_LOG].count_documents({"tag": tag_aus})) == 0, "Wartung = technischer Ausfall"
        assert _worker(False, "tok", False) == [15, 60]
        doc = welt.run(db[K.TAGESPLAN_LOG].find_one({"tag": tag_aus}, {"_id": 0}))
        assert doc["crawler_aus_at"] and doc["crawler_aktiv"] is False and not doc.get("lief_at")
    finally:
        welt.run(db[K.KONFIG].delete_one({"_id": K.SCHALTER_DOK}))
        if schalter:
            welt.run(db[K.KONFIG].insert_one(schalter))
        welt.run(db[K.TAGESPLAN_LOG].delete_many({"tag": {"$in": [tag, tag_aus]}}))
        K._AUS_PROTOKOLLIERT["tag"] = ""
        _aufraeumen(welt)


# ---------------------------------------------------------------- #5 vorlaeufig, Bloecke
def test_38_runde4_vorlaeufig_erster_periodentag_und_stabiler_korb(welt):
    """Runde 4 #5 (niedrig): FIVE_DAY 06.-10., Stand 06. abends, abwechselnde Haelften (am 05. die teure, am 06. die
    guenstige), alle geplanten Laeufe erledigt. Vorher gehoerte die teure Haelfte nicht zum Korb (kein Basistag im
    Zeitraum), der 06. war 'vollstaendig': Median/Min/Max 17.500 EUR (wahr 23.500, -25,5 %), HIGH. Jetzt gehoeren
    Segmente mit tragbarem Wert aus dem Vorlauf zum Korb: 23.500. Stehen am 06. noch Laeufe aus, steht 'Laeufe von
    heute stehen noch aus' statt 'Kein Tag mit ausreichender Korbabdeckung'. Intervall 7: die Korbzusammensetzung der
    6 FIVE_DAY-Perioden bleibt 24 Segmente, jeder Tag 23.500 (vorher fiel ein im Block nicht beobachtetes Segment
    heraus)."""
    segs, preis = _rotation_korb(welt)
    wahr = sum(preis.values()) / 24
    plan, docs = {}, []
    for k in range(-9, 6):
        for i, s in enumerate(segs):
            if (i < 12) == (k % 2 == 1):
                plan.setdefault(s["id"], set()).add(_k(k))
                docs.append(_doc_daten(welt, s, _k(k), preis[s["id"]]))
    ber = _rechnen(welt, segs, docs, "FIVE_DAY", _t(6), _t(10), heute=_t(6), geplant=plan)
    k, tag6 = ber["kennzahlen"], ber["tage"][0]
    assert tag6["median"] == 17500 and abs(tag6["median_korb"] - wahr) <= 1 and tag6["anker"]
    assert (k["korb_wirksam_segmente"], tag6["nicht_geplante_segmente"]) == (24, 12)
    assert abs(k["median_periode"] - wahr) <= 1 and abs(k["minimum"]["wert"] - wahr) <= 1
    # 06. abends: 6 der 12 geplanten Laeufe stehen noch aus
    offen = {s["id"] for s in segs[6:12]}
    docs_frueh = [d for d in docs if not (d["date"] == _t(6) and d["segment_id"] in offen)]
    ber = _rechnen(welt, segs, docs_frueh, "FIVE_DAY", _t(6), _t(10), heute=_t(6), geplant=plan)
    k = ber["kennzahlen"]
    assert (k["median_periode"], k["anker_tage"], ber["tage"][0]["ausstehende_segmente"]) == (None, 0, 6)
    assert any(h.startswith("Läufe von heute stehen noch aus") for h in ber["hinweise"]), ber["hinweise"]
    assert not any("Kein Tag mit ausreichender Korbabdeckung" in h for h in ber["hinweise"])
    # Intervall 7 (SAFE_AUTO-Stufe): Segment i an Tagen k mit (k - i) % 7 == 0 — stabiler Korb ueber alle Bloecke
    plan, docs = {}, []
    for k in range(-14, 29):
        for i, s in enumerate(segs):
            if (k - i) % 7 == 0:
                plan.setdefault(s["id"], set()).add(_k(k))
                docs.append(_doc_daten(welt, s, _k(k), preis[s["id"]]))
    for typ, von, bis in B.perioden_im_monat(2028, 2):
        ber = _rechnen(welt, segs, docs, typ, von, bis, geplant=plan)
        k = ber["kennzahlen"]
        assert k["korb_wirksam_segmente"] == 24 and all(abs(r["median_korb"] - wahr) <= 1 for r in ber["tage"]), (typ, von)
        assert k["anker_tage"] == len(ber["tage"]), (typ, von)
    mon = _rechnen(welt, segs, docs, "MONTHLY", _t(1), _t(29), geplant=plan)
    assert len(mon["bloecke"]) == 6 and all(b["expected_days"] == b["coverage_days"] for b in mon["bloecke"])


# ---------------------------------------------------------------- #6 Confidence
def test_39_runde4_confidence_mit_mindesttagen(welt):
    """Runde 4 #6 (niedrig): die Segmente eines Modells werden unter Rotation als Block geplant — Modell am 02. und
    wieder am 06. geplant (davor am 28.01.). FIVE_DAY 01.-05.: vorher expected_days 1, 1/1, HIGH ohne Gruende neben
    Richtung UNKNOWN. Jetzt LOW ('nur 1 gueltige(r) Tag(e)', '4 Tage ohne Plan'); zwei gueltige Tage MEDIUM, drei HIGH
    (bei >= 50 % der Kalendertage mit gueltigem oder getragenem Wert)."""
    assert (B.CONF_HOCH_MIN_TAGE, B.CONF_MITTEL_MIN_TAGE, B.CONF_HOCH_KALENDER_ANTEIL) == (3, 2, 0.5)
    segs = [_seg_daten(welt, f"{100000 + i * 1000}-{100000 + i * 1000 + 999}") for i in range(6)]

    def bericht(tage_k):
        plan = {s["id"]: {_k(k) for k in tage_k} for s in segs}
        docs = [_doc_daten(welt, s, _k(k), 20000 + 1000 * i, n=12) for i, s in enumerate(segs) for k in tage_k]
        return _rechnen(welt, segs, docs, geplant=plan)["kennzahlen"]
    k = bericht((-4, 1, 5))
    assert (k["expected_days"], k["coverage_days"], k["richtung"], k["tage_ohne_plan"], k["tage_mit_wert"]) == (1, 1, "UNKNOWN", 4, 5)
    assert (k["confidence"], k["confidence_gruende"]) == ("LOW", ["nur 1 gültige(r) Tag(e) (hoch ab 3)", "4 Tage ohne Plan"])
    k = bericht((-4, 1, 3))
    assert (k["coverage_days"], k["confidence"]) == (2, "MEDIUM") and "nur 2 gültige(r) Tag(e) (hoch ab 3)" in k["confidence_gruende"]
    k = bericht((-4, 0, 2, 4))
    assert (k["coverage_days"], k["confidence"], k["confidence_gruende"]) == (3, "HIGH", [])
    assert B.confidence(3, 3, {"GOOD": 3}, {"FULL": 3}, 20, kalendertage=10, tage_mit_wert=4, tage_ohne_plan=7) == (
        "MEDIUM", ["4/10 Kalendertage mit gültigem Wert (hoch ab 50 %)", "7 Tage ohne Plan"])


def test_41_runde4_planausfall_vor_anlage_des_modells_ist_keine_luecke(welt):
    """Runde 4 #1: ein Tag ohne Tagesplan-Protokoll ist nur dann eine Luecke dieses Modells, wenn an dem Tag ein Segment
    des Modells lief bzw. schon angelegt war. Suchauftrag am 03. angelegt (created_at), Plan fiel am 01. und 02. aus:
    keine erwarteten Tage, kein Hinweis 'Tagesplan lief nicht'. Faellt der Plan am 04. aus, ist das eine Luecke."""
    segs = [{**_seg_daten(welt, f"{100000 + i * 1000}-{100000 + i * 1000 + 999}"), "created_at": "2028-02-03T01:00:00+00:00"} for i in range(4)]
    plan = {s["id"]: {_t(3), _t(5)} for s in segs}
    docs = [_doc_daten(welt, s, t, 20000, n=12) for s in segs for t in (_t(3), _t(5))]
    tage = {_t(1): B.PLAN_AUSFALL, _t(2): B.PLAN_AUSFALL, _t(3): B.PLAN_LIEF, _t(4): B.PLAN_LIEF, _t(5): B.PLAN_LIEF}
    ber = _rechnen(welt, segs, docs, geplant=plan, planung={"tage": tage})
    k = ber["kennzahlen"]
    assert (k["expected_days"], k["coverage_days"], k["tage_tagesplan_ausfall"], k["tage_ohne_plan"]) == (2, 2, 0, 3)
    assert not any("Tagesplan lief nicht" in h for h in ber["hinweise"])
    ber = _rechnen(welt, segs, docs, geplant=plan, planung={"tage": {**tage, _t(4): B.PLAN_AUSFALL}})
    k = ber["kennzahlen"]
    assert (k["expected_days"], k["coverage_days"], k["tage_tagesplan_ausfall"], k["segment_tage_erwartet"]) == (3, 2, 1, 12)
    assert any(h.startswith("Tagesplan lief nicht: 1 Kalendertag(e)") for h in ber["hinweise"])


# ---------------------------------------------------------------- Laufzeit
def test_40_runde4_laufzeit_monatsbericht_180_segmente(welt):
    """Laufzeit eines Monatsberichts mit 180 Segmenten (15 EZ x 12 km) unter Budget-Rotation (jeder 4. Segment-Tag nicht
    geplant), getrennt gemessen: Job-/Protokoll-Abfrage (_planung_laden: eine Aggregation ueber den Unique-Index
    (segment_id, tag) mit ~5.800 Jobs, zwei Index-Abfragen auf das Protokoll) und reine Rechnung (bericht_rechnen, im
    Betrieb im Hilfsthread). Die Zeiten stehen in der Ausgabe (-s)."""
    mid = _start(welt)
    db = welt.db
    s = welt.w.s
    segs = [_seg_daten(welt, f"{100000 + i * 1000}-{100000 + i * 1000 + 999}") for i in range(180)]
    von, bis = _t(1), _t(29)
    tage = [B._plus(von, d) for d in range(-B.VORLAUF_TAGE, 29)]
    try:
        _protokoll_leeren(welt)
        welt.run(db[K.SEGMENTE].insert_many([dict(x) for x in segs]))
        jobs = [{"id": f"test-lz-{s}-{i}-{t}", "segment_id": x["id"], "model_id": mid, "tag": t, "status": "completed", "job_type": "daily"}
                for i, x in enumerate(segs) for d, t in enumerate(tage) if (i + d) % 4]
        welt.run(db[K.JOBS].insert_many(jobs))
        for t in tage:
            welt.run(K.tagesplan_protokollieren(db, t, lief=True, segmente_geplant=135))
        docs = [_doc_daten(welt, x, t, 20000 + (d % 7) * 50) for i, x in enumerate(segs) for d, t in enumerate(tage) if (i + d) % 4]
        t0 = time.perf_counter()
        geplant, planung = welt.run(B._planung_laden(db, sorted(x["id"] for x in segs), von, bis))
        abfrage = time.perf_counter() - t0
        t0 = time.perf_counter()
        ber = B.bericht_rechnen({"id": mid}, {x["id"]: x for x in segs}, docs, "MONTHLY", von, bis, geplant=geplant, planung=planung)
        rechnung = time.perf_counter() - t0
        print(f"\nMonatsbericht 180 Segmente: Job-/Protokoll-Abfrage {abfrage * 1000:.0f} ms ({len(jobs)} Jobs), "
              f"Rechnung {rechnung * 1000:.0f} ms (Hilfsthread)")
        k = ber["kennzahlen"]
        assert k["planung"] == "jobs" and k["segmente_gesamt"] == 180 and k["niveau_tage"] == 29 and k["tage_tagesplan_ausfall"] == 0
        assert k["getragene_segment_tage"] == 29 * 45 and k["teilabgedeckte_tage"] == 0 and k["anker_tage"] == 29
        assert all(st == B.PLAN_LIEF for st in planung["tage"].values())
        assert abfrage < 2.0 and rechnung < 10.0, (abfrage, rechnung)
    finally:
        _protokoll_leeren(welt)
        welt.run(db[K.JOBS].delete_many({"id": {"$regex": f"^test-lz-{s}-"}}))
        _aufraeumen(welt)
