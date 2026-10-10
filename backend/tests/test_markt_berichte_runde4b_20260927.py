# -*- coding: utf-8 -*-
"""Pruefung Runde 4b der Berichte (Phase E, 27.09.2026) — Gegenpruefung von 8831027, weiter Schema 4 (nicht ausgeliefert).

Je Befund ein Test mit dem nachgerechneten Szenario der Pruefer (Zahlen vor der Korrektur im Docstring):
  N1  Neustart nach mehr als 14 Tagen ohne Plan / Wieder-Einschalten: laengst angelegte Segmente fehlen ('vorab')
  N2  Serienstart am letzten Periodentag: nie beobachtete Segmente gehoeren zum Korb (kein Teilkorb-Anker)
  N3  Intervall ueber 14 Tage: siehe test_33 (Runde 4) — abgelaufene Werte fehlen, Euro-Niveau leer, nicht HIGH
  N4  Start-/Endwert ueber die wirksamen Werte (SAFE_AUTO-Intervalle), auch in den 5-Tage-Bloecken
  N5  Budget danach fuer einen ganzen Tag ist 'nicht geplant wegen Budget'
  N6  Tagesplan-Protokoll fehlt, aber ein Tagesplan-Job belegt den Plan -> kein Planausfall
  B1  jobs.intervall: bei knappem Budget nie mehr als das Tagesbudget (Monatsverlauf, kein Ausfall am Monatsende)
Alle Tage werden ausdruecklich uebergeben; Testdaten nur test-/t<hex>, Protokolltage 2028-*.
"""
import math
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from test_befunde_runde17_termine import welt  # noqa: E402,F401
from test_markt_20260926 import BUD, JOBS, K  # noqa: E402
from test_markt_berichte_20260927 import B, _doc_daten, _job_db, _rechnen, _rotation_korb, _t  # noqa: E402
from test_markt_berichte_runde4_20260927 import _docs_aus_plan, _k, _korb_ez, _lauf, _modell_24, _protokoll_leeren, _tagesplan  # noqa: E402
from test_markt_hotdeals_20260927 import _aufraeumen  # noqa: E402

ALT = "2027-11-01T08:00:00+00:00"          # laengst angelegt (vor jedem geladenen Fenster)


def _alle_22750(ber):
    k = ber["kennzahlen"]
    assert (k["minimum"]["wert"], k["maximum"]["wert"], k["median_periode"], k["mittelwert_periode"]) == (22750, 22750, 22750, 22750), k
    assert (k["sample_market_change_eur"], k["sample_market_change_pct"]) == (0, 0)
    assert all(r["median_korb"] == 22750 for r in ber["tage"] if r["median"] is not None)


# ---------------------------------------------------------------- N1 Neustart / Wieder-Einschalten
def test_42_runde4b_neustart_nach_langer_pause_ist_kein_mix(welt):
    """N1 (mittel): Budget seit 12.01. erschoepft (kein Lauf), am 01.02. neues Monatsbudget, Rotation 18/24 (EZ-Korb, wahr
    22.750, Preise konstant), FIVE_DAY 01.-05. Am 01. fehlen die 6 nicht geplanten Segmente, ihr letzter Lauf liegt vor dem
    14-Tage-Vorlauf. 8831027 wertete sie als 'neu' (Mix): 01. rueckwaerts verkettet, aber Mix-Wert 21.916,67 als Minimum,
    Stichproben-Aenderung +3,802 %, HIGH ohne Gruende. Jetzt: laengst angelegt -> 'vorab' (fehlt), 01. kein Anker, 22.750.
    Dazu das Wieder-Einschalten eines pausierten Auftrags (Befund #4): alt angelegt, erster Job der 7 erst am 04. —
    vorher Minimum 19.882,35 am 03., +14,423 %, HIGH; jetzt 22.750 mit Hinweis 'Serienstart'."""
    segs, preis = _korb_ez(welt)
    segs = [{**s, "created_at": ALT} for s in segs]
    tage = [_k(k) for k in range(0, 5)]
    plan = _tagesplan(segs, tage, 18, letzter={s["id"]: "2028-01-11" for s in segs})
    assert sum(1 for s in segs if _t(1) not in plan[s["id"]]) == 6
    docs = _docs_aus_plan(welt, segs, preis, plan)
    status = {B._plus(_t(1), -i): B.PLAN_BUDGET for i in range(1, 15)}
    ber = _rechnen(welt, segs, docs, geplant=plan, planung={"tage": status})
    tag1, k = ber["tage"][0], ber["kennzahlen"]
    assert tag1["median"] == 20250, "roher Tageswert der 18 guenstigsten"
    assert (tag1["vorab_segmente"], tag1["anker"], tag1["teilabdeckung"]) == (6, False, False)
    _alle_22750(ber)
    assert k["vorab_segment_tage"] == 6 and any(h.startswith("Serienstart: 6 Segment-Tag(e)") for h in ber["hinweise"])
    assert (k["segment_luecken"], k["teilabgedeckte_tage"]) == (0, 0)
    # Wieder-Einschalten nach mehr als 15 Tagen Pause: 17 Segmente am 03., die uebrigen 7 am 04. (danach Rotation 18)
    plan = {s["id"]: {_t(3)} for s in segs[:17]}
    for sid, t in _tagesplan(segs, [_t(4), _t(5)], 18, letzter={s["id"]: _t(3) for s in segs[:17]}).items():
        plan.setdefault(sid, set()).update(t)
    docs = _docs_aus_plan(welt, segs, preis, plan)
    ber = _rechnen(welt, segs, docs, geplant=plan)
    tage = {r["date"]: r for r in ber["tage"]}
    assert round(tage[_t(3)]["median"], 2) == 19882.35 and (tage[_t(3)]["vorab_segmente"], tage[_t(3)]["anker"]) == (7, False)
    _alle_22750(ber)
    assert any(h.startswith("Serienstart: 7 Segment-Tag(e)") for h in ber["hinweise"]), ber["hinweise"]
    # echte neue Segmente (am 04. angelegt) bleiben Mix: am 03. keine vorab-Luecke
    neu = [{**s, "created_at": "2028-02-04T01:00:00+00:00"} if i >= 17 else s for i, s in enumerate(segs)]
    ber = _rechnen(welt, neu, docs, geplant=plan)
    assert (ber["tage"][2]["vorab_segmente"], round(ber["tage"][2]["median_korb"], 2)) == (0, 19882.35)


# ---------------------------------------------------------------- N2 Serienstart am letzten Periodentag
def test_43_runde4b_serienstart_am_letzten_periodentag(welt):
    """N2 (mittel): neuer Auftrag, created_at 05.02., 17 von 24 Segmenten am 05. (guenstigste EZ zuerst), der Rest am 06.
    FIVE_DAY 01.-05. final: 8831027 nahm die 7 nicht in den Korb (kein Basistag im Zeitraum) — der 05. deckte 100 % des
    Teilkorbs und war Anker: Median = Minimum = Maximum = 19.882,35 (wahr 22.750, -12,6 %), korb_wirksam_segmente 17.
    Jetzt gehoeren angelegte, laufende Segmente ohne Wert zum Korb ('vorab'): kein Anker, Euro-Niveau leer mit Hinweis.
    Ebenso Fassungswechsel am 05. und Wieder-Einschalten am 25. (FIVE_DAY 21.-25.)."""
    segs, preis = _korb_ez(welt)
    segs = [{**s, "created_at": "2028-02-05T01:00:00+00:00"} for s in segs]
    plan = {s["id"]: {_t(5)} for s in segs[:17]}
    plan.update({s["id"]: {_t(6)} for s in segs[17:]})
    docs = _docs_aus_plan(welt, segs, preis, plan)
    docs = [d for d in docs if d["date"] <= _t(5)]                     # final nach Periodenende: nur Tage bis 'bis' geladen

    def _leer(ber, vorab=7):
        k, tag5 = ber["kennzahlen"], {r["date"]: r for r in ber["tage"]}[ber["periode_bis"]]
        assert round(tag5["median"], 2) == 19882.35 and (tag5["vorab_segmente"], tag5["anker"], tag5["median_korb"]) == (vorab, False, None)
        assert (k["median_periode"], k["minimum"], k["maximum"], k["korb_wirksam_segmente"]) == (None, None, None, 24), k
        assert any(h.startswith(f"Serienstart: {vorab} Segment-Tag(e)") for h in ber["hinweise"]), ber["hinweise"]
        assert any("Kein Tag mit ausreichender Korbabdeckung" in h for h in ber["hinweise"])
    _leer(_rechnen(welt, segs, docs, geplant=plan, planung={"letzter": {s["id"]: _t(6) for s in segs[17:]}}))
    # grosse Stichproben (20 Inserate je Segment): die 7 ohne Preisbeobachtung zaehlen mit dem mittleren Gewicht — mit
    # 1,0 wie bei 8831027 laegen 17 x 20 gegen 7 x 1 bei 98 % Abdeckung, und der 05. waere doch Anker
    docs20 = [_doc_daten(welt, s, _t(5), preis[s["id"]], n=20) for s in segs[:17]]
    ber = _rechnen(welt, segs, docs20, geplant=plan)
    assert (ber["tage"][4]["korb_abdeckung_pct"], ber["tage"][4]["anker"], ber["kennzahlen"]["median_periode"]) == (70.8, False, None)
    # Fassungswechsel am 05.: alte Fassung lief 01.-04. taeglich
    alt, preis_alt = _korb_ez(welt, praefix="v1:", enabled=False)
    neu, preis_neu = _korb_ez(welt, praefix="v2:", version=2, dh="h2")
    neu = [{**s, "created_at": "2028-02-05T01:00:00+00:00"} for s in neu]
    plan = {s["id"]: {_k(k) for k in range(-14, 4)} for s in alt}
    plan.update({s["id"]: {_t(5)} for s in neu[:17]})
    docs = _docs_aus_plan(welt, alt, preis_alt, plan) + _docs_aus_plan(welt, neu, preis_neu, plan)
    _leer(_rechnen(welt, alt + neu, docs, geplant=plan))
    # Wieder-Einschalten am 25. (alt angelegt, im Fenster nie gelaufen): FIVE_DAY 21.-25.
    segs, preis = _korb_ez(welt, praefix="w:")
    segs = [{**s, "created_at": ALT} for s in segs]
    plan = {s["id"]: {_t(25)} for s in segs[:17]}
    docs = _docs_aus_plan(welt, segs, preis, plan)
    _leer(_rechnen(welt, segs, docs, "FIVE_DAY", _t(21), _t(25), geplant=plan))


# ---------------------------------------------------------------- N4 Start/Ende
def test_44_runde4b_start_und_ende_ueber_die_wirksamen_werte(welt):
    """N4 (mittel): 24 Segmente (12.000-35.000, wahr 23.500), 8 taeglich, der Rest mit SAFE_AUTO-Intervall 2/3/7/14
    (Segment i an Tagen k mit (k - i) % n == 0). Start-/Endwert kamen nur aus Segmenten mit >= 2 Basistagen im Zeitraum:
    FIVE_DAY 26.-29.02. Start = Ende aus 10-12 Segmenten (~17.900-20.500 EUR, -12 bis -24 %), Monatsbloecke ebenso,
    daneben median_periode 23.500. Jetzt je Korb-Segment der wirksame Wert (beobachtet oder getragen) am ersten und letzten
    Tag mit Wert — Start = Ende = 23.500 in jeder Periode und jedem Block."""
    segs, preis = _rotation_korb(welt)
    wahr = sum(preis.values()) / 24
    takt = [1] * 8 + [2] * 4 + [3] * 4 + [7] * 4 + [14] * 4
    plan, docs = {}, []
    for k in range(-14, 29):
        for i, s in enumerate(segs):
            if (k - i) % takt[i] == 0:
                plan.setdefault(s["id"], set()).add(_k(k))
                docs.append(_doc_daten(welt, s, _k(k), preis[s["id"]]))
    for typ, von, bis in B.perioden_im_monat(2028, 2):
        ber = _rechnen(welt, segs, docs, typ, von, bis, geplant=plan)
        k = ber["kennzahlen"]
        assert abs(k["median_periode"] - wahr) <= 1, (typ, von)
        assert abs(k["startwert"] - wahr) <= 1 and abs(k["endwert"] - wahr) <= 1 and abs(k["delta_eur"]) <= 1, (typ, von, k["startwert"])
        assert (k["korb_segmente"], k["start_ende_korb_pct"], k["richtung"]) == (24, 100.0, "STABLE"), (typ, von)
        for b in ber.get("bloecke") or []:
            assert abs(b["startwert"] - wahr) <= 1 and abs(b["endwert"] - wahr) <= 1, (typ, b["von"], b["startwert"])
    # Start/Ende ueber weniger als 95 % des Korbs (Serienstart): Euro leer, % und Richtung ueber denselben Teilkorb
    segs2, preis2 = _korb_ez(welt, praefix="s:")
    segs2 = [{**s, "created_at": ALT} for s in segs2]
    plan2 = {s["id"]: {_t(d) for d in range(1, 6)} for s in segs2[:20]}
    plan2.update({s["id"]: {_t(5)} for s in segs2[20:]})
    ber = _rechnen(welt, segs2, _docs_aus_plan(welt, segs2, preis2, plan2), geplant=plan2)
    k = ber["kennzahlen"]
    assert (k["startwert"], k["endwert"], k["delta_eur"], k["delta_pct"], k["richtung"]) == (None, None, None, 0, "STABLE"), k
    assert k["start_ende_korb_pct"] < 95 and any(h.startswith("Start-/Endwert nur über") for h in ber["hinweise"])


# ---------------------------------------------------------------- N5 Budget ganzer Tag
def test_45_runde4b_budget_storno_eines_ganzen_tages(welt):
    """N5 (niedrig): 24 Segmente taeglich; am 03. und 04. lief der Tagesplan (Protokoll ohne budget_grund), alle Jobs
    wurden mit budget_wait storniert. 8831027: tage_budget 0, budget_segment_tage 0, kein Budget-Hinweis (nur '2
    Kalendertag(e) ohne geplanten Abruf'). Jetzt wie 'Budget vor dem Plan': 2 Kalendertage, 48 Segment-Tage, Hinweis."""
    segs, preis = _korb_ez(welt)
    tage = [_k(k) for k in range(-14, 5) if _k(k) not in (_t(3), _t(4))]
    plan = {s["id"]: set(tage) for s in segs}
    docs = _docs_aus_plan(welt, segs, preis, plan)
    planung = {"tage": {}, "budget": {s["id"]: {_t(3), _t(4)} for s in segs}}
    ber = _rechnen(welt, segs, docs, geplant=plan, planung=planung)
    k, zeilen = ber["kennzahlen"], {r["date"]: r for r in ber["tage"]}
    assert (k["tage_budget"], k["budget_segment_tage"], k["tage_ohne_plan"], k["expected_days"]) == (2, 48, 2, 3), k
    assert (zeilen[_t(3)]["budget_segmente"], zeilen[_t(3)]["plan_status"]) == (24, B.PLAN_LIEF)
    assert any(h.startswith("Nicht geplant wegen Budget: 2 Kalendertag(e)") and "48 Segment-Tag(e)" in h for h in ber["hinweise"]), ber["hinweise"]
    # technischer Storno (ohne budget_wait) bleibt eine Luecke, kein Budget
    ber = _rechnen(welt, segs, docs, geplant={s["id"]: set(tage) | {_t(3), _t(4)} for s in segs}, planung={"tage": {}})
    assert (ber["kennzahlen"]["tage_budget"], ber["kennzahlen"]["expected_days"], ber["kennzahlen"]["coverage_days"]) == (0, 5, 3)


# ---------------------------------------------------------------- N6 Protokoll fehlt, Job belegt den Plan
def test_46_runde4b_protokoll_fehlt_aber_tagesplan_job_belegt_den_plan(welt):
    """N6 (niedrig): das Protokoll wird nach dem Tagesmerker geschrieben und darf scheitern (Failover, Prozessende,
    Rollback). Am 03. fehlt es; das Modell (24 Segmente) war am 03. nicht dran (Rotation), ein ANDERES Modell hatte einen
    Tagesplan-Job. 8831027: PLAN_AUSFALL — alle 24 erwartet und technisch fehlend (3/4... 4/5 Tage, Hinweis 'Tagesplan
    lief nicht', eingefroren). Jetzt belegt der Job den Plan: 03. 'lief', nicht geplant, keine Luecke. Ohne jeden Job
    bleibt der Tag ein Ausfall."""
    mid, segs = _modell_24(welt)
    db = welt.db
    try:
        _protokoll_leeren(welt)
        for tag in ("2028-01-31", _t(1), _t(2), _t(4), _t(5)):
            _lauf(welt, segs, tag)
            assert welt.run(K.tagesplan_protokollieren(db, tag, lief=True, segmente_geplant=24, budget_grund=None))
        ber = welt.run(B.bericht_berechnen(db, mid, "FIVE_DAY", _t(1), _t(5)))
        k = ber["kennzahlen"]
        assert (k["tage_tagesplan_ausfall"], k["expected_days"], k["coverage_days"]) == (1, 5, 4), "ohne Job: Ausfall"
        fremd = {"id": f"test-fremd-{welt.w.s}", "model_id": f"test-fremd-{welt.w.s}", "version": 1, "definition_hash": "h1"}
        _job_db(welt, fremd, _t(3), "completed")
        ber = welt.run(B.bericht_berechnen(db, mid, "FIVE_DAY", _t(1), _t(5)))
        k = ber["kennzahlen"]
        assert (k["tage_tagesplan_ausfall"], k["expected_days"], k["coverage_days"], k["tage_ohne_plan"]) == (0, 4, 4, 1), k
        assert ber["tage"][2]["plan_status"] == B.PLAN_LIEF and not any("Tagesplan lief nicht" in h for h in ber["hinweise"])
        assert k["segment_luecken"] == 0 and k["confidence"] == "HIGH", k["confidence_gruende"]
    finally:
        _protokoll_leeren(welt)
        _aufraeumen(welt)


# ---------------------------------------------------------------- B1 Taktung im Monatsverlauf
def test_47_runde4b_knappes_budget_reicht_bis_monatsende(welt, monkeypatch):
    """B1 (hoch): 180 Segmente, Monatsbudget passend fuer etwa Intervall 21, 30 Tage, das Tageskontingent jeden Tag neu
    aus jobs.intervall (Restbudget / Resttage; das Budget wird nur je Monat reserviert). 8831027 setzte bei
    'budget_reicht_nicht' Intervall 14 und ceil(180 / 14) = 13 Segmente je Tag: am 20. war das Budget weg, vom 21. bis
    30. lief kein einziger Abruf. Jetzt bleibt das Kontingent am Budget (8-9 je Tag, Intervall > 14 mit Warnung): jeden
    Tag laeuft etwas, das Budget reicht bis Monatsende."""
    db = welt.db
    s = welt.w.s
    _aufraeumen(welt)
    monat = f"test-{s}"
    rest = {"tage": 30}
    monkeypatch.setenv("MARKT_ENTFERNUNG_PRUEFEN", "false")
    monkeypatch.setenv("MARKT_CRAWL_INTERVALL_TAGE", "0")
    monkeypatch.setattr(K, "monat", lambda zeit=None: monat)
    monkeypatch.setattr(K, "rest_tage_im_monat", lambda zeit=None: rest["tage"])
    try:
        welt.run(db[K.MODELLE].insert_one({"id": f"test-sim-{s}", "model_id": "x", "status": "active"}))
        welt.run(db[K.SEGMENTE].insert_many([{"id": f"test-sim-{s}:{i}", "model_id": f"test-sim-{s}", "enabled": True, "max_items": 20,
                                              "crawls_per_day": 1} for i in range(180)]))
        # Kosten eines Tages mit ALLEN aktiven Segmenten genau wie jobs.intervall (andere Tests koennen Segmente haben)
        je_rows, n = {}, 0
        for x in welt.run(db[K.SEGMENTE].find({"enabled": True}, {"_id": 0, "max_items": 1, "crawls_per_day": 1}).to_list(None)):
            k = int(x.get("crawls_per_day") or 1)
            rows = int(x.get("max_items") or K.rows_je_segment())
            je_rows[rows] = je_rows.get(rows, 0) + k
            n += 1
        abruf = sum(K.zeilen_mit_puffer(r) * c for r, c in je_rows.items())
        alle = K.kosten_buendel_usd(K.actor(), JOBS.starts_je_gruppe(je_rows, max(1, K.buendel_groesse())), abruf)
        je_segment = alle / n
        budget = round(30 * alle / 21, 4)
        welt.run(BUD.budget_setzen(db, budget, monat))
        verbraucht, laeufe = 0.0, []
        for tag in range(30):
            rest["tage"] = 30 - tag
            welt.run(db[K.BUDGET].update_one({"_id": monat}, {"$set": {"used_usd": round(verbraucht, 4), "reserved_usd": 0}}))
            t = welt.run(JOBS.intervall(db))
            if tag == 0:
                assert t["budget_reicht_nicht"] and t["intervall_tage"] > K.MAX_INTERVALL_TAGE, t
            moeglich = int(max(0.0, budget - verbraucht) // je_segment + 1e-9)          # was das Monatsbudget noch zulaesst
            lief = min(int(t["segmente_je_tag"]), moeglich)
            laeufe.append(lief)
            verbraucht += lief * je_segment
        assert min(laeufe) >= 1, f"Tage ohne Abruf: {laeufe}"
        assert all(x <= math.ceil(n / 21) for x in laeufe), laeufe
        assert verbraucht <= budget + 1e-6
    finally:
        _aufraeumen(welt)
        welt.run(db[K.SEGMENTE].delete_many({"model_id": f"test-sim-{s}"}))
        welt.run(db[K.MODELLE].delete_many({"id": f"test-sim-{s}"}))
        welt.run(db[K.BUDGET].delete_many({"_id": monat}))
