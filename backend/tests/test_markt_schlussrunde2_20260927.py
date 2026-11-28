# -*- coding: utf-8 -*-
"""Schlussrunde Marktanalyse 2 (27.09.2026) — Gegenpruefung von 12c08fd.

Je Befund ein Test mit dem nachgerechneten Szenario der Pruefer (Gegenprobe: Korrektur zurueckgenommen -> rot):
  R1  Beschriftungen unter Budget-Rotation mit mehreren Auftraegen: das Kontingent gilt fuer alle Auftraege, der
      Tagesplan sortiert nach (last_planned_tag, priority, id) — eine Fassung laeuft in Bloecken und ruht dazwischen
      laenger als eine Woche. Vorher 'Serienstart' bzw. 'Stillstand ... das Intervall selbst war normal' bei einem seit
      Monaten laufenden Intervall 24 bzw. 20. Jetzt entscheidet das Tagesplan-Protokoll (intervall_tage /
      budget_reicht_nicht): 'Intervall ueber 14 Tage'
  R2  'vorab' nach einem Budget-Stillstand heisst nie 'Intervall ueber 14 Tage' (Neustart am 29. bzw. 30.01. gleich
      beschriftet, mit und ohne Intervall im Protokoll)
  R3  _protokoll_laden liefert das Intervall je Tag (nur Tage, an denen der Plan lief)
  R4  optimierung: ein zurueckgehaltener Wunsch verschwindet, sobald sein Grund wegfaellt
Testdaten nur test-/t<hex>, Protokolltage 2028-*, Aufraeumen am Ende.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from test_befunde_runde17_termine import welt  # noqa: E402,F401
from test_markt_20260926 import K  # noqa: E402
from test_markt_berichte_20260927 import B, _rotation_korb, _seg_daten  # noqa: E402
from test_markt_berichte_20260927 import _doc_daten as _bdoc  # noqa: E402
from test_markt_berichte_20260927 import _rechnen as _brechnen  # noqa: E402
from test_markt_berichte_20260927 import _t as _bt  # noqa: E402
from test_markt_berichte_runde4_20260927 import _protokoll_leeren, _tagesplan  # noqa: E402
from test_markt_fg_pruefbefunde_20260927 import _aktive, _Merker, _modus, _start  # noqa: E402
from test_markt_health_20260927 import OPT, _aufraeumen, _doc, _rechnen, _seg, _t  # noqa: E402

ALT = "2027-09-01T08:00:00+00:00"          # laengst angelegt (vor jeder Simulation)


# ---------------------------------------------------------------- R1 mehrere Auftraege
def _mehrere_auftraege(welt, auftraege, je_tag):
    """Auftrag 0 = das berichtete Modell (_rotation_korb, 24 Segmente 12.000-35.000), weitere Auftraege mit je 24
    Segmenten (andere IDs, im Bericht nicht geladen). Tagesplan wie jobs.tagesplan ueber ALLE Auftraege (faellig =
    last_planned_tag < Tag, sortiert nach (last_planned_tag, id) bei gleicher Prioritaet), eingeschwungen seit 01.10.2027."""
    segs, preis = _rotation_korb(welt)
    segs = [{**s, "created_at": ALT} for s in segs]
    andere = [_seg_daten(welt, f"{i * 10000}-{i * 10000 + 9999}", praefix=f"z{a}:") for a in range(1, auftraege) for i in range(24)]
    tage = B.tage_zwischen("2027-10-01", "2028-03-31")
    plan = _tagesplan(segs + andere, tage, je_tag)
    eigen = {s["id"]: plan.get(s["id"], set()) for s in segs}
    return segs, preis, eigen


def _bericht(welt, segs, preis, plan, typ, von, bis, intervall=None, status=None):
    """Bericht wie aus der Datenbank: nur Jobs/Tagesdokumente des geladenen Fensters (Vorlauf + Periode), Protokoll
    'lief' an jedem Tag (bzw. status), Intervall laut Protokoll (intervall: Tag -> Tage oder None = ohne Angabe)."""
    start = B._plus(von, -B.VORLAUF_TAGE)
    fenster = B.tage_zwischen(start, bis)
    geplant = {sid: {t for t in tage if start <= t <= bis} for sid, tage in plan.items()}
    docs = [_bdoc(welt, s, t, preis[s["id"]]) for s in segs for t in sorted(geplant[s["id"]])]
    planung = {"tage": {t: (status or {}).get(t, B.PLAN_LIEF) for t in fenster}}
    if intervall is not None:
        planung["intervall"] = {t: intervall(t) for t in fenster if intervall(t) is not None}
    return _brechnen(welt, segs, docs, typ, von, bis, geplant=geplant, planung=planung)


def _nur_intervall(ber):
    k, h = ber["kennzahlen"], ber["hinweise"]
    assert k["vorab_neu_segment_tage"] == 0 and k["abgelaufene_stillstand_segment_tage"] == 0, k
    assert not any(x.startswith("Serienstart") for x in h), h
    assert not any("nach einem Stillstand" in x for x in h), h
    assert not any("Stillstand" in g for g in k["confidence_gruende"]), k["confidence_gruende"]
    if k["abgelaufene_segment_tage"]:
        assert any("Intervall über 14 Tage — z. B." in x for x in h), h
    if k["vorab_segment_tage"]:
        assert any("seit mehr als 14 Tagen nicht abgerufen" in x for x in h), h
    assert k["abgelaufene_segment_tage"] + k["vorab_segment_tage"] > 0, k


def test_r1_mehrere_auftraege_intervall_ueber_14_ist_kein_serienstart(welt):
    """Pruefer Fall 1: 2 Auftraege x 24 Segmente, 2 je Tag (Intervall 24, Protokoll budget_reicht_nicht). 12c08fd:
    FIVE_DAY 01.-05.02. 'Serienstart: 60 Segment-Tag(e)', 21.-25.02. '18 Segment-Tag(e) ohne tragbaren Wert nach einem
    Stillstand ... das Intervall selbst war normal', MONTHLY Februar 114 Stillstand- und 72 Serienstart-Segment-Tage,
    kein Hinweis 'Intervall ueber 14 Tage'. Fall 2: 5 Auftraege x 24, 6 je Tag (Intervall 20): FIVE_DAY 16.-20.02.
    'Serienstart: 36', MONTHLY Maerz '72 ... nach einem Stillstand'. Jetzt ueberall 'Intervall ueber 14 Tage'."""
    segs, preis, plan = _mehrere_auftraege(welt, 2, 2)
    for typ, von, bis in (("FIVE_DAY", _bt(1), _bt(5)), ("FIVE_DAY", _bt(21), _bt(25)), ("MONTHLY", _bt(1), _bt(29))):
        ber = _bericht(welt, segs, preis, plan, typ, von, bis, intervall=lambda t: 24)
        print("R1-FALL1", typ, von, {f: ber["kennzahlen"][f] for f in ("vorab_segment_tage", "vorab_lange_segment_tage",
                                                                        "abgelaufene_segment_tage", "abgelaufene_stillstand_segment_tage")})
        _nur_intervall(ber)
    segs, preis, plan = _mehrere_auftraege(welt, 5, 6)
    for typ, von, bis in (("FIVE_DAY", _bt(16), _bt(20)), ("MONTHLY", _bt(1, 3), _bt(31, 3))):
        ber = _bericht(welt, segs, preis, plan, typ, von, bis, intervall=lambda t: 20)
        print("R1-FALL2", typ, von, {f: ber["kennzahlen"][f] for f in ("vorab_segment_tage", "vorab_lange_segment_tage",
                                                                        "abgelaufene_segment_tage", "abgelaufene_stillstand_segment_tage")})
        _nur_intervall(ber)
    # die Tageszeilen tragen dieselbe Aufteilung
    ber = _bericht(welt, segs, preis, plan, "FIVE_DAY", _bt(16), _bt(20), intervall=lambda t: 20)
    assert all(r["vorab_lange_segmente"] == r["vorab_segmente"] and r["abgelaufene_stillstand_segmente"] == 0 for r in ber["tage"])


def test_r1_intervall_laut_protokoll_normal_ist_pause_kein_intervall(welt):
    """Gegenstueck: dieselbe Fassung ruht 20 Tage, das Protokoll meldet aber Intervall 12 (normal) — dann lief sie
    nicht (Auftrag pausiert und wieder eingeschaltet): 'Serienstart' bzw. Stillstand/Pause, nie 'Intervall ueber 14'."""
    segs, preis = _rotation_korb(welt)
    segs = [{**s, "created_at": ALT} for s in segs]
    tage = [t for t in B.tage_zwischen("2027-12-01", "2028-02-29") if not ("2028-01-10" <= t <= "2028-01-29")]
    plan = _tagesplan(segs, tage, 2)
    ber = _bericht(welt, segs, preis, plan, "FIVE_DAY", _bt(1), _bt(5), intervall=lambda t: 12)
    k = ber["kennzahlen"]
    assert k["vorab_segment_tage"] > 0 and k["vorab_lange_segment_tage"] == 0, k
    assert k["abgelaufene_segment_tage"] == k["abgelaufene_stillstand_segment_tage"], k
    assert not any("seit mehr als 14 Tagen" in h or "Intervall über 14 Tage — z. B." in h for h in ber["hinweise"]), ber["hinweise"]
    # reine Regel
    daten = B._Daten({s["id"]: s for s in segs}, [], _bt(1), _bt(5), geplant={}, planung={"intervall": {_bt(1): 15, "2028-01-20": 12}})
    assert daten.intervall_lang(_bt(1)) is True and daten.intervall_lang(_bt(2)) is True
    assert daten.intervall_lang("2028-01-20") is False and daten.intervall_lang("2028-01-19") is None
    # an einem Budget-Tag steht das Notintervall (1 Segment je Tag) — zaehlt nicht als Intervall
    daten = B._Daten({}, [], _bt(1), _bt(5), geplant={}, planung={"tage": {_bt(1): B.PLAN_BUDGET}, "intervall": {_bt(1): 24}})
    assert daten.intervall_lang(_bt(1)) is None


# ---------------------------------------------------------------- R2 vorab nach Budget-Stillstand
def test_r2_vorab_nach_budget_stillstand_nie_intervall_ueber_14(welt):
    """Pruefer: 24 Segmente (created_at 2027-11), bis 15.01.2028 taeglich abgerufen, Protokoll 'budget' vom 16.01. bis
    zum Neustart, danach Rotation 2 je Tag (Intervall 12). Geladen ab Fensterbeginn 23.01., FIVE_DAY 06.-10.02.
    12c08fd: Neustart 29.01. -> 'vorab 12, lange 12' mit Hinweis '... seit mehr als 14 Tagen nicht abgerufen (Intervall
    ueber 14 Tage)'; Neustart 30.01. -> 20 Segment-Tage 'Serienstart'. Jetzt beide Male Serienstart — mit und ohne
    Intervall im Protokoll."""
    segs, preis = _rotation_korb(welt)
    segs = [{**s, "created_at": "2027-11-01T08:00:00+00:00"} for s in segs]
    for neustart, vorab in (("2028-01-29", 12), ("2028-01-30", 20)):
        vorher = {s["id"]: set(B.tage_zwischen("2027-12-01", "2028-01-15")) for s in segs}
        nachher = _tagesplan(segs, B.tage_zwischen(neustart, "2028-02-29"), 2, letzter={s["id"]: "2028-01-15" for s in segs})
        plan = {sid: vorher[sid] | nachher.get(sid, set()) for sid in vorher}
        status = {t: B.PLAN_BUDGET for t in B.tage_zwischen("2028-01-16", B._plus(neustart, -1))}
        for intervall in (lambda t: 12 if t >= neustart else None, None):
            ber = _bericht(welt, segs, preis, plan, "FIVE_DAY", _bt(6), _bt(10), intervall=intervall, status=status)
            k = ber["kennzahlen"]
            assert (k["vorab_segment_tage"], k["vorab_lange_segment_tage"], k["vorab_neu_segment_tage"]) == (vorab, 0, vorab), (neustart, k)
            assert any(h.startswith(f"Serienstart: {vorab} Segment-Tag(e)") for h in ber["hinweise"]), ber["hinweise"]
            assert not any("seit mehr als 14 Tagen nicht abgerufen" in h for h in ber["hinweise"]), ber["hinweise"]
            assert all(r["vorab_lange_segmente"] == 0 for r in ber["tage"])


# ---------------------------------------------------------------- R3 Protokoll liefert das Intervall
def test_r3_protokoll_laden_liefert_das_intervall(welt):
    db = welt.db
    try:
        _protokoll_leeren(welt)
        welt.run(K.tagesplan_protokollieren(db, _bt(1), lief=True, segmente_geplant=2, budget_grund=K.BUDGET_GRUND_REICHT_NICHT,
                                            intervall_tage=24, segmente_je_tag=2))
        welt.run(K.tagesplan_protokollieren(db, _bt(2), lief=True, segmente_geplant=2, budget_grund=K.BUDGET_GRUND_REICHT_NICHT,
                                            segmente_je_tag=2))                 # ohne intervall_tage: Grund genuegt
        welt.run(K.tagesplan_protokollieren(db, _bt(3), lief=True, segmente_geplant=8, budget_grund=None, intervall_tage=12))
        welt.run(K.tagesplan_protokollieren(db, _bt(4), lief=True, segmente_geplant=1, budget_grund=K.BUDGET_GRUND_ERSCHOEPFT,
                                            intervall_tage=24))
        intervalle = {}
        status = welt.run(B._protokoll_laden(db, _bt(1), _bt(5), None, set(), intervalle))
        assert status[_bt(4)] == B.PLAN_BUDGET and status[_bt(3)] == B.PLAN_LIEF
        assert intervalle[_bt(1)] == 24 and intervalle[_bt(2)] == K.MAX_INTERVALL_TAGE + 1 and intervalle[_bt(3)] == 12
        assert _bt(5) not in intervalle
        daten = B._Daten({}, [], _bt(1), _bt(5), geplant={}, planung={"tage": status, "intervall": intervalle})
        assert daten.intervall_lang(_bt(3)) is True and daten.intervall_lang(_bt(1)) is True
        # ohne Rueckgabe-Dict bleibt die Signatur wie bisher
        assert welt.run(B._protokoll_laden(db, _bt(1), _bt(5), None, set())) == status
    finally:
        _protokoll_leeren(welt)


# ---------------------------------------------------------------- R4 zurueckgehalten verschwindet mit seinem Grund
def test_r4_zurueckgehalten_verschwindet_wenn_der_grund_wegfaellt(welt):
    """Pruefer: REDUCE 'alle 2 Tage' am Tag X angewendet; X+1 HOT -> zurueckgehalten {Daten widersprechen ..., bis X+7};
    X+2 und X+4 wieder NORMAL, Empfehlung wieder 'alle 2 Tage', kein Widerspruch — 12c08fd liess den Eintrag bis X+7
    stehen (Seite: 'haelt bis ... — zurueckgehalten: Daten widersprechen ... HOT'). Jetzt ist er am X+2 weg; die
    Aenderung bleibt aktiv."""
    merker = _Merker(welt)
    _start(welt)
    db = welt.db
    try:
        _modus(welt, "SAFE_AUTO")
        seg = _seg(welt)
        for i in range(10, 30):
            _doc(welt, seg, _t(i), [4, 5, 4, 5, 4][(i - 10) % 5])
        _rechnen(welt, stichtag=_t(10))
        red = _aktive(welt, seg["id"], "REDUCE_FREQUENCY")
        assert [a["neu"] for a in red] == [{"intervall_tage": 2, "crawls_per_day": 1}], red
        normal = welt.run(db[K.HEALTH].find_one({"segment_id": seg["id"]}, {"_id": 0}))
        assert normal["health"] != "HOT"
        hot = {"health": "HOT", "activity_score": 100, "empfehlung": {"intervall_tage": 1, "crawls_per_day": 1}}
        welt.run(db[K.HEALTH].update_one({"segment_id": seg["id"]}, {"$set": {**hot, "tag": _t(9)}}))
        erg = welt.run(OPT.safe_auto_anwenden(db, tag=_t(9), model_ids=[seg["model_id"]]))
        a = _aktive(welt, seg["id"], "REDUCE_FREQUENCY")
        assert erg["zurueckgehalten"] == 1 and len(a) == 1 and a[0]["zurueckgehalten"]["grund"].startswith("Daten widersprechen"), (erg, a)
        # Daten wieder wie bei der Anwendung (gleiche Empfehlung, kein Widerspruch)
        welt.run(db[K.HEALTH].replace_one({"segment_id": seg["id"]}, {**normal, "tag": _t(8)}))
        for tag in (_t(8), _t(6)):
            erg = welt.run(OPT.safe_auto_anwenden(db, tag=tag, model_ids=[seg["model_id"]]))
            a = _aktive(welt, seg["id"], "REDUCE_FREQUENCY")
            assert erg["zurueckgehalten"] == 0 and len(a) == 1 and "zurueckgehalten" not in a[0], (tag, erg, a)
            assert a[0]["id"] == red[0]["id"] and a[0]["neu"] == {"intervall_tage": 2, "crawls_per_day": 1}
        assert erg["zurueckgehalten_erledigt"] == 0                                   # idempotent: nichts mehr zu tun
        # wird wieder etwas zurueckgehalten, steht es wieder da (mit neuem Tag)
        welt.run(db[K.HEALTH].update_one({"segment_id": seg["id"]}, {"$set": {**hot, "tag": _t(5)}}))
        erg = welt.run(OPT.safe_auto_anwenden(db, tag=_t(5), model_ids=[seg["model_id"]]))
        a = _aktive(welt, seg["id"], "REDUCE_FREQUENCY")
        assert erg["zurueckgehalten"] == 1 and a[0]["zurueckgehalten"]["tag"] == _t(5), a
        # ein anderer Server hat den Eintrag inzwischen neu geschrieben: der veraltete Stand loescht ihn nicht
        welt.run(db[K.AENDERUNGEN].update_one({"id": a[0]["id"]}, {"$set": {"zurueckgehalten.grund": "neu von Server 2"}}))
        welt.run(db[K.HEALTH].replace_one({"segment_id": seg["id"]}, {**normal, "tag": _t(4)}))
        stale = dict(a[0])
        r = welt.run(db[K.AENDERUNGEN].update_one({"id": stale["id"], "status": "aktiv", "zurueckgehalten": stale["zurueckgehalten"]},
                                                  {"$unset": {"zurueckgehalten": ""}}))
        assert r.modified_count == 0
    finally:
        merker.zurueck()
        _aufraeumen(welt)
