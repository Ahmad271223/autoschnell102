# -*- coding: utf-8 -*-
"""Pruefbefunde zu Phase F/G (Segment-Health, Optimierungsvorschlaege, SAFE_AUTO) vom 27.09.2026 — F0 bis F18.

Je Befund ein Test, der ohne die Korrektur scheitert:
  F0/F5  Schwellen/Confidence unter einer Wirkung gegen die erwartbaren Laeufe (Puffer), Bestandsschutz
  F1/F6  Hysterese nur an der Grenze — Zielstufe = Zuordnung exakt, kein Nachrutschen am Folgetag
  F2     Activity Score haengt nicht vom Abrufabstand ab (Top-N je Vergleich, Zaehler je Kalendertag)
  F3/F7/F13  Schluessel = Familie ohne Intervall; abgelehnt/zurueckgenommen bleibt abgelehnt (auch alte Schluessel),
             bis die Ablehnung ausdruecklich aufgehoben wird
  F4     MERGE bleibt, wenn SAFE_AUTO die leeren Bereiche pausiert
  F8/F16 Ersparnis ehrlich (Budgetgrenze -> frei werdende Laeufe statt Dollar), je Segment nur die groesste Wirkung
  F9     Admin-Knopf setzt den Tagesmerker nicht
  F10/F12 Listen gefiltert und seitenweise mit Gesamtzahl, Sortierung nach Wirkung vor dem Kuerzen
  F14    Zaehler je Suchauftrag sofort nach jeder Entscheidung
  F18    Modellseite: Badge nur fuer die aktuelle Fassung eines aktiven Auftrags
Testdaten nur test-/t<hex>, Aufraeumen am Ende; Stichtag ausdruecklich (kein Mitternachts-Effekt).
"""
import sys
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))
from test_befunde_runde17_termine import _module, welt  # noqa: E402,F401
from test_markt_20260926 import K  # noqa: E402
from test_markt_health_20260927 import (  # noqa: E402
    H, OPT, STICHTAG, _aufraeumen, _doc, _doc_daten, _mid, _modell_anlegen, _rechnen, _reihe, _seg, _t,
)

KM2 = ((20000, 40000), (40001, 60000))
KM3 = ((20000, 40000), (40001, 60000), (60001, 130000))


def _start(welt, **kw):
    _aufraeumen(welt)
    welt.run(_module("indizes").markt_indizes(welt.db))
    return _modell_anlegen(welt, **kw)


class _Merker:
    """market_config-Dokumente (Modus, Tagesplan, Health) sichern und wiederherstellen."""

    def __init__(self, welt, *ids):
        self.welt, self.ids = welt, ids or (K.OPTIMIERUNG_DOK, K.TAGESPLAN_DOK, K.HEALTH_DOK)
        self.alt = {i: welt.run(welt.db[K.KONFIG].find_one({"_id": i})) for i in self.ids}

    def zurueck(self):
        for i, a in self.alt.items():
            if a:
                self.welt.run(self.welt.db[K.KONFIG].replace_one({"_id": i}, a, upsert=True))
            else:
                self.welt.run(self.welt.db[K.KONFIG].delete_one({"_id": i}))


def _modus(welt, modus):
    welt.run(welt.db[K.KONFIG].update_one({"_id": K.OPTIMIERUNG_DOK}, {"$set": {"modus": modus}}, upsert=True))


def _keine_rotation(welt, tag=STICHTAG):
    welt.run(K.merker_setzen(welt.db, K.TAGESPLAN_DOK, tag=tag, segmente_je_tag=100, segmente_gesamt=100, ruhend=0, wartend=0))


def _rotation(welt, tag=STICHTAG, je_tag=50, gesamt=100, wartend=50):
    welt.run(K.merker_setzen(welt.db, K.TAGESPLAN_DOK, tag=tag, segmente_je_tag=je_tag, segmente_gesamt=gesamt, ruhend=0, wartend=wartend))


def _aktive(welt, seg_id, typ=None):
    f = {"segment_id": seg_id, "status": "aktiv", **({"typ": typ} if typ else {})}
    return welt.run(welt.db[K.AENDERUNGEN].find(f, {"_id": 0}).to_list(20))


def _protokoll(welt, seg_id):
    return welt.run(welt.db[K.AENDERUNGEN].find({"segment_id": seg_id}, {"_id": 0}).sort("at", 1).to_list(50))


def _v(welt, seg_id, typ):
    return welt.run(welt.db[K.VORSCHLAEGE].find({"segment_id": seg_id, "typ": typ}, {"_id": 0}).to_list(20))


def _lange_pause(welt, seg, tage=(0, 7, 14, 21), n=0, poor=()):
    """Die Pause laeuft seit ueber 30 Tagen: nur noch Nachpruefungen alle 7 Tage im Fenster."""
    db = welt.db
    welt.run(db[K.AENDERUNGEN].update_many({"segment_id": seg["id"], "status": "aktiv"}, {"$set": {"reduziert_seit": _t(60)}}))
    welt.run(db[K.SEGMENTE].update_one({"id": seg["id"], "safe_auto": {"$exists": True}}, {"$set": {"safe_auto.reduziert_seit": _t(60)}}))
    welt.run(db[K.TAGESSTATS].delete_many({"segment_id": seg["id"]}))
    for i in tage:
        _doc(welt, seg, _t(i), n, dq="POOR" if i in poor else "GOOD", praefix=f"l{i}")


# ---------------------------------------------------------------- F0 / F5
def test_f0_f5_schwellen_unter_wirkung_mit_puffer(welt):
    """Pausiert (alle 7 Tage, seit ueber 30 Tagen): 4 Nachpruefungen im Fenster, eine davon POOR -> 3 gueltige Laeufe
    genuegen (4 erwartet, Puffer ein Ausfall) -> weiter EMPTY mit Confidence >= MEDIUM. Vorher: UNKNOWN, kein
    Vorschlag, Wirkung 'Empfehlung entfallen' aufgehoben. REDUCE alle 3 Tage mit einem ungueltigen Lauf (9 von 10):
    weiter ein Vorschlag mit Confidence >= MEDIUM (vorher: valid 9 < 10 -> kein Vorschlag)."""
    cfg = H.FREQUENZ_STANDARD
    pause = {"id": "s1", "model_id": "m", "version": 1, "definition_hash": "h1", "max_items": 5, "crawls_per_day": 1,
             "safe_auto": {"intervall_tage": 7, "pausiert": True, "reduziert_seit": _t(60)}}
    docs = [_doc_daten(welt, pause, _t(i), 0, dq="POOR" if i == 8 else "GOOD") for i in (22, 15, 8, 1)]
    h = H.segment_health(pause, docs, stichtag=STICHTAG, cfg=cfg)
    assert h["erwartete_laeufe"] == 4 and h["min_laeufe_empty"] == 3 and h["valid_runs"] == 3
    assert h["health"] == "EMPTY" and h["confidence"] in ("MEDIUM", "HIGH")
    v = OPT.vorschlaege_segment(pause, h)
    assert [x["typ"] for x in v] == ["PAUSE_EMPTY"] and v[0]["confidence"] in ("MEDIUM", "HIGH")
    # REDUCE alle 3 Tage: Score 18 (Ø 3,6 Autos) -> Zuordnung alle 3 Tage; 10 Laeufe erwartet, 9 gueltig
    red = {**pause, "id": "s2", "safe_auto": {"intervall_tage": 3, "crawls_per_day": 1, "reduziert_seit": _t(60)}}
    muster = [4, 4, 3, 4, 3]
    docs = [_doc_daten(welt, red, _t(i), muster[(i // 3) % 5], dq="POOR" if i == 9 else "GOOD", praefix=f"r{i}") for i in range(0, 30, 3)]
    h2 = H.segment_health(red, docs, stichtag=STICHTAG, cfg=cfg)
    assert h2["erwartete_laeufe"] == 10 and h2["valid_runs"] == 9 and h2["min_laeufe_empty"] == 7
    assert h2["health"] == "NORMAL" and h2["activity_score"] == 18 and h2["confidence"] in ("MEDIUM", "HIGH")
    v2 = OPT.vorschlaege_segment(red, h2)
    assert [(x["typ"], x["proposed_definition"]["intervall_tage"], x["confidence"] in ("MEDIUM", "HIGH")) for x in v2] == [
        ("REDUCE_FREQUENCY", 3, True)]
    # die Laeufe VOR der Reduktion zaehlen mit: Reduktion seit 5 Tagen -> 25 taegliche + 1 Lauf alle 3 Tage
    kurz = {**red["safe_auto"], "reduziert_seit": _t(5)}
    assert H.erwartete_laeufe(kurz, stichtag=STICHTAG, crawls_per_day=1) == 25 + 1
    assert H.schwelle(14, 26) == 14 and H.schwelle(14, 4) == 3 and H.schwelle(7, 10) == 7 and H.schwelle(14, None) == 14


def test_f0_f5_bestandsschutz_pause_bleibt_ohne_widerspruch(welt):
    """SAFE_AUTO pausiert ein EMPTY-Segment. Danach: ein Ausfall (POOR) -> Pause bleibt; Admin stellt die Nachpruefung
    auf 10 Tage -> Wirkung wird AKTUALISIERT (alt -> neu), nicht aufgehoben; nur noch UNKNOWN (fast keine Daten) ->
    Pause bleibt (Bestandsschutz). Vorher: 'aufgehoben: Empfehlung entfallen' und taeglich wieder abgerufen."""
    merker = _Merker(welt)
    _start(welt, km=KM2)
    db = welt.db
    try:
        _modus(welt, "SAFE_AUTO")
        _keine_rotation(welt)
        leer = _seg(welt, km=KM2[0])
        _reihe(welt, leer, 20, [0])
        _rechnen(welt)
        assert [a["neu"]["intervall_tage"] for a in _aktive(welt, leer["id"])] == [7]
        _lange_pause(welt, leer, poor=(14,))
        _rechnen(welt)
        h = welt.run(db[K.HEALTH].find_one({"segment_id": leer["id"]}, {"_id": 0}))
        assert h["valid_runs"] == 3 and h["health"] == "EMPTY", "ein Ausfall kippt kein Urteil"
        assert len(_aktive(welt, leer["id"])) == 1 and welt.run(db[K.SEGMENTE].find_one({"id": leer["id"]}))["safe_auto"]["pausiert"]
        # Admin: Nachpruefung alle 10 Tage -> aktualisiert (dieselbe Familie, Ziel geaendert)
        cfg = dict(H.FREQUENZ_STANDARD, empty_nachpruefung_tage=10)
        welt.run(OPT.frequenz_setzen(db, cfg, wer="test"))
        erg = _rechnen(welt)
        assert erg["safe_auto"]["aktualisiert"] == 1 and erg["safe_auto"]["aufgehoben"] == 0
        aktiv = _aktive(welt, leer["id"])
        assert [a["neu"]["intervall_tage"] for a in aktiv] == [10] and aktiv[0]["reduziert_seit"] == _t(60), "Kette bleibt"
        alt = [a for a in _protokoll(welt, leer["id"]) if a["status"] == "aufgehoben"]
        assert len(alt) == 1 and alt[0]["beendet_grund"].startswith("Empfehlung geändert")
        vs = _v(welt, leer["id"], "PAUSE_EMPTY")
        assert len(vs) == 1 and vs[0]["status"] == "APPLIED" and vs[0]["schluessel"] == f"PAUSE_EMPTY:{leer['id']}"
        # fast keine Daten mehr (UNKNOWN) -> Pause bleibt (fehlende Daten widersprechen nicht)
        welt.run(db[K.TAGESSTATS].delete_many({"segment_id": leer["id"], "date": {"$ne": _t(0)}}))
        _rechnen(welt)
        assert welt.run(db[K.HEALTH].find_one({"segment_id": leer["id"]}))["health"] in ("UNKNOWN", "STALE")
        assert len(_aktive(welt, leer["id"])) == 1, "Bestandsschutz"
        # Widerspruch: wieder Treffer -> aufgehoben mit Grund aus den Daten
        _lange_pause(welt, leer, n=3)
        _rechnen(welt)
        assert _aktive(welt, leer["id"], "PAUSE_EMPTY") == []
        ende = [a for a in _protokoll(welt, leer["id"]) if a["typ"] == "PAUSE_EMPTY"][-1]
        assert ende["beendet_grund"].startswith("Daten widersprechen")
        assert _v(welt, leer["id"], "PAUSE_EMPTY")[0]["status"] == "OBSOLETE"
    finally:
        merker.zurueck()
        _aufraeumen(welt)


def test_f0_thin_wird_empty_unter_reduktion(welt):
    """Befund F0 (d): ein THIN-Segment unter REDUCE (alle 5 Tage) ist 4 von 4 Mal leer -> EMPTY mit Confidence MEDIUM
    (gemessen an den erwartbaren Laeufen) -> PAUSE ersetzt REDUCE (Protokoll: 'ersetzt durch'), der REDUCE-Vorschlag
    ist ueberholt und wird nicht wieder angewendet; das Segment wird nie wieder taeglich abgerufen (vorher: PAUSE LOW
    nie angewendet, REDUCE aufgehoben -> taeglich)."""
    merker = _Merker(welt)
    _start(welt)
    db = welt.db
    try:
        _modus(welt, "SAFE_AUTO")
        _keine_rotation(welt)
        seg = _seg(welt)
        _reihe(welt, seg, 20, [1, 2])
        _rechnen(welt)
        red = _aktive(welt, seg["id"], "REDUCE_FREQUENCY")
        assert len(red) == 1 and red[0]["neu"]["intervall_tage"] == 5
        _lange_pause(welt, seg, tage=(0, 5, 10, 15, 20, 25))
        for tag in (STICHTAG, _t(-1), _t(-2)):
            erg = _rechnen(welt, stichtag=tag)
            assert erg["safe_auto"]["aufgehoben"] == 0 or tag == STICHTAG
        h = welt.run(db[K.HEALTH].find_one({"segment_id": seg["id"]}, {"_id": 0}))
        assert h["health"] == "EMPTY" and h["confidence"] in ("MEDIUM", "HIGH")
        aktiv = _aktive(welt, seg["id"])
        assert [a["typ"] for a in aktiv] == ["PAUSE_EMPTY"] and aktiv[0]["reduziert_seit"] == _t(60), "Kette laeuft weiter"
        ende = next(a for a in _protokoll(welt, seg["id"]) if a["typ"] == "REDUCE_FREQUENCY")
        assert ende["status"] == "aufgehoben" and ende["beendet_grund"].startswith("ersetzt durch")
        assert _v(welt, seg["id"], "REDUCE_FREQUENCY")[0]["status"] == "OBSOLETE"
        assert len(_protokoll(welt, seg["id"])) == 2, "kein Hin und Her"
        assert welt.run(db[K.SEGMENTE].find_one({"id": seg["id"]}))["safe_auto"]["pausiert"] is True
    finally:
        merker.zurueck()
        _aufraeumen(welt)


# ---------------------------------------------------------------- F1 / F6
def test_f1_f6_hysterese_verschiebt_nie_die_zielstufe(welt):
    """Score 22 (Zuordnung 20-44 -> alle 2 Tage): SAFE_AUTO wendet 'alle 2 Tage' an und bleibt am Folgetag dabei
    (vorher: Folgetag 'alle 3 Tage', Protokoll aufgehoben + neu). Reine Regel: exakt an der Zuordnung, Hysterese nur
    an der Grenze in beide Richtungen."""
    e = H.empfehlung
    zwei = {"intervall_tage": 2, "crawls_per_day": 1}
    assert e("NORMAL", 22, aktuell=zwei)["intervall_tage"] == 2 and e("NORMAL", 22)["intervall_tage"] == 2
    assert e("NORMAL", 10)["intervall_tage"] == 5 and e("NORMAL", 10, aktuell={"intervall_tage": 5, "crawls_per_day": 1})["intervall_tage"] == 5
    assert e("NORMAL", 18, aktuell=zwei)["intervall_tage"] == 2, "2 Punkte unter der Grenze 20: bleibt"
    assert e("NORMAL", 14, aktuell=zwei)["intervall_tage"] == 4, "6 Punkte unter der Grenze: Zielstufe exakt (14 -> 4 Tage)"
    assert e("NORMAL", 47, aktuell=zwei)["hysterese"] is True and e("NORMAL", 50, aktuell=zwei)["intervall_tage"] == 1
    fuenf = {"intervall_tage": 5, "crawls_per_day": 1}
    assert e("NORMAL", 12)["intervall_tage"] == 4 and e("NORMAL", 12, aktuell=fuenf)["intervall_tage"] == 5, "lineares Band: 1 Punkt ueber der Grenze haelt"
    assert e("NORMAL", 17, aktuell=fuenf)["intervall_tage"] == 3, "5+ Punkte ueber der Grenze: exakt die Zuordnung (17 -> 3 Tage)"
    zwei_x = {"intervall_tage": 1, "crawls_per_day": 1}
    assert e("HOT", 77, aktuell=zwei_x)["crawls_per_day"] == 1 and e("HOT", 80, aktuell=zwei_x)["crawls_per_day"] == 2
    merker = _Merker(welt)
    _start(welt)
    db = welt.db
    try:
        _modus(welt, "SAFE_AUTO")
        _keine_rotation(welt)
        seg = _seg(welt)
        _reihe(welt, seg, 20, [4, 5, 4, 5, 4])
        _rechnen(welt)
        h = welt.run(db[K.HEALTH].find_one({"segment_id": seg["id"]}, {"_id": 0}))
        assert h["activity_score"] == 22 and h["health"] == "NORMAL"
        assert [a["neu"] for a in _aktive(welt, seg["id"])] == [{"intervall_tage": 2, "crawls_per_day": 1}]
        for i in range(1, 4):
            _doc(welt, seg, _t(-i), [4, 5, 4][i - 1])
            erg = _rechnen(welt, stichtag=_t(-i))
            assert erg["safe_auto"]["aktualisiert"] == 0 and erg["safe_auto"]["aufgehoben"] == 0
        assert len(_protokoll(welt, seg["id"])) == 1, "kein Nachrutschen, kein Protokoll-Churn"
        assert welt.run(db[K.SEGMENTE].find_one({"id": seg["id"]}))["safe_auto"]["intervall_tage"] == 2
    finally:
        merker.zurueck()
        _aufraeumen(welt)


# ---------------------------------------------------------------- F2
def _markt_mit_abstand(welt, seg, n, tage=30):
    """Derselbe Markt (5/5 Zeilen, je Tag 1 neues + 1 verschwundenes Auto, 1 Preisaenderung, Top-3/5 wechseln
    taeglich), nur alle n Tage abgerufen: ueber n Tage sammeln sich n neue/verschwundene/Preisaenderungen an."""
    docs, vorher = [], None
    for i in range(tage - 1, -1, -n):
        d = _doc_daten(welt, seg, _t(i), 5, neu=min(5, n), weg=n, red=n, top3=True, top5=True, praefix=f"n{n}d{i}",
                       vergleich=vorher is not None)
        d["vergleich_vortag"] = vorher
        docs.append(d)
        vorher = _t(i)
    return docs


def test_f2_activity_score_unabhaengig_vom_abrufabstand(welt):
    """Identischer Markt, Abstand 1-4 Tage: derselbe Score (vorher 77/71/66/64 — die Top-N-Komponente fiel mit 1/n)."""
    seg = {"id": "s", "model_id": "m", "version": 1, "definition_hash": "h1", "max_items": 5, "crawls_per_day": 1}
    scores = {}
    for n in (1, 2, 3, 4):
        s = {**seg, "safe_auto": {"intervall_tage": n, "reduziert_seit": _t(60)}} if n > 1 else seg
        h = H.segment_health(s, _markt_mit_abstand(welt, seg, n), stichtag=STICHTAG, cfg=H.FREQUENZ_STANDARD)
        scores[n] = h["activity_score"]
        assert h["activity_komponenten"]["top"] == 1.0, n
    assert max(scores.values()) - min(scores.values()) <= 1, scores
    assert scores[1] >= 75


# ---------------------------------------------------------------- F3 / F7 / F13
def test_f3_f7_f13_familie_abgelehnt_bleibt_abgelehnt(welt):
    """Schluessel ohne Intervall; im OBSERVE abgelehnt, dann SAFE_AUTO an und der Score driftet (10 -> 12, Zuordnung
    5 -> 4 Tage): nie angewendet (vorher: neuer Schluessel ...:4t:1x wurde angewendet). Ablehnung aufheben (mit
    Protokoll) -> danach wieder anwendbar. Ruecknahme -> Familie abgelehnt, auch nach Ablauf der 30 Tage Ruhe."""
    merker = _Merker(welt)
    _start(welt, km=KM2)
    db = welt.db
    try:
        _modus(welt, "OBSERVE")
        _keine_rotation(welt)
        seg = _seg(welt)
        _reihe(welt, seg, 20, [2])
        _rechnen(welt)
        v = _v(welt, seg["id"], "REDUCE_FREQUENCY")
        assert len(v) == 1 and v[0]["schluessel"] == f"REDUCE_FREQUENCY:{seg['id']}" and v[0]["proposed_definition"]["intervall_tage"] == 5
        welt.run(OPT.vorschlag_entscheiden(db, v[0]["id"], "ablehnen", wer="test-admin"))
        _modus(welt, "SAFE_AUTO")
        _reihe(welt, seg, 20, [2, 3, 2, 3, 2])          # Score 12 -> alle 4 Tage
        _rechnen(welt)
        _rechnen(welt, stichtag=_t(-1))
        assert _aktive(welt, seg["id"]) == [], "abgelehnte Familie nie angewendet"
        v = _v(welt, seg["id"], "REDUCE_FREQUENCY")
        assert len(v) == 1 and v[0]["status"] == "REJECTED"
        # Ablehnung aufheben -> offen (Protokoll im Verlauf) -> SAFE_AUTO wendet an (Ziel: exakt alle 4 Tage)
        auf = welt.run(OPT.ablehnung_aufheben(db, v[0]["id"], wer="test-admin"))
        assert auf["status"] == "PROPOSED" and auf["ablehnung_aufgehoben_von"] == "test-admin"
        assert [x["aktion"] for x in auf["verlauf"]] == ["abgelehnt", "ablehnung_aufgehoben"]
        with pytest.raises(OPT.Konflikt):
            welt.run(OPT.ablehnung_aufheben(db, v[0]["id"], wer="test-admin"))
        _rechnen(welt, stichtag=_t(-1))
        aktiv = _aktive(welt, seg["id"])
        assert [a["neu"]["intervall_tage"] for a in aktiv] == [4]
        # Ruecknahme -> Familie abgelehnt + 30 Tage Ruhe; nach Ablauf der Ruhe weiter abgelehnt
        welt.run(OPT.aenderung_zuruecknehmen(db, aktiv[0]["id"], wer="test-admin"))
        welt.run(db[K.SEGMENTE].update_one({"id": seg["id"]}, {"$set": {"safe_auto_sperre_bis": "2000-01-01"}}))
        _reihe(welt, seg, 20, [2])                     # Score 10 -> alle 5 Tage (anderes Ziel)
        _rechnen(welt, stichtag=_t(-1))
        assert _aktive(welt, seg["id"]) == [] and _v(welt, seg["id"], "REDUCE_FREQUENCY")[0]["status"] == "REJECTED"
    finally:
        merker.zurueck()
        _aufraeumen(welt)


def test_f3_alte_schluessel_mit_intervall_wirken_weiter(welt):
    """Lese-Kompatibilitaet ohne Migration: eine Ablehnung unter dem ALTEN Schluessel (REDUCE_FREQUENCY:<seg>:5t:1x)
    sperrt die Familie — der neue Familien-Vorschlag beginnt abgelehnt, SAFE_AUTO wendet nichts an. Eine aktive
    Wirkung unter altem Schluessel wird ohne neuen Protokolleintrag mit dem Familien-Schluessel verknuepft."""
    merker = _Merker(welt)
    _start(welt, km=KM2)
    db = welt.db
    try:
        _modus(welt, "SAFE_AUTO")
        _keine_rotation(welt)
        seg = _seg(welt)
        _reihe(welt, seg, 20, [2])
        alt_id = uuid.uuid4().hex
        welt.run(db[K.VORSCHLAEGE].insert_one({"id": alt_id, "schluessel": f"REDUCE_FREQUENCY:{seg['id']}:5t:1x", "typ": "REDUCE_FREQUENCY",
                                               "segment_id": seg["id"], "model_id": seg["model_id"], "version": 1, "status": "REJECTED",
                                               "entschieden_von": "test-admin", "entschieden_at": "2031-03-01T00:00:00+00:00",
                                               "proposed_definition": {"intervall_tage": 5, "crawls_per_day": 1}}))
        _rechnen(welt)
        assert _aktive(welt, seg["id"]) == []
        fam = [x for x in _v(welt, seg["id"], "REDUCE_FREQUENCY") if x["id"] != alt_id]
        assert len(fam) == 1 and fam[0]["status"] == "REJECTED" and "Familie abgelehnt" in fam[0]["entscheidung_grund"]
        # Aufheben ueber den ALTEN Eintrag: alter -> OBSOLETE, Familie -> offen
        welt.run(OPT.ablehnung_aufheben(db, alt_id, wer="test-admin"))
        st = {x["id"]: x["status"] for x in _v(welt, seg["id"], "REDUCE_FREQUENCY")}
        assert st == {alt_id: "OBSOLETE", fam[0]["id"]: "PROPOSED"}
        # aktive Wirkung unter einem alten Schluessel: verknuepfen statt aufheben + neu anlegen
        seg2 = _seg(welt, km=KM2[1])
        _reihe(welt, seg2, 20, [2])
        v_alt = {"id": uuid.uuid4().hex, "schluessel": f"REDUCE_FREQUENCY:{seg2['id']}:5t:1x", "typ": "REDUCE_FREQUENCY",
                 "segment_id": seg2["id"], "model_id": seg2["model_id"], "version": 1, "status": "APPLIED", "angewendet_von": "safe_auto",
                 "proposed_definition": {"intervall_tage": 5, "crawls_per_day": 1}}
        welt.run(db[K.VORSCHLAEGE].insert_one(dict(v_alt)))
        a_id = uuid.uuid4().hex
        welt.run(db[K.AENDERUNGEN].insert_one({"id": a_id, "segment_id": seg2["id"], "model_id": seg2["model_id"], "typ": "REDUCE_FREQUENCY",
                                               "vorschlag_id": v_alt["id"], "schluessel": v_alt["schluessel"], "wer": "safe_auto",
                                               "neu": {"intervall_tage": 5, "crawls_per_day": 1}, "status": "aktiv",
                                               "at": "2031-03-20T10:00:00+00:00", "tag": _t(10)}))
        welt.run(OPT._wirkung_neu(db, seg2["id"]))
        _rechnen(welt)
        assert [a["id"] for a in _aktive(welt, seg2["id"])] == [a_id] and len(_protokoll(welt, seg2["id"])) == 1
        assert _aktive(welt, seg2["id"])[0]["schluessel"] == f"REDUCE_FREQUENCY:{seg2['id']}"
        st2 = {x["schluessel"]: x["status"] for x in _v(welt, seg2["id"], "REDUCE_FREQUENCY")}
        assert st2 == {v_alt["schluessel"]: "OBSOLETE", f"REDUCE_FREQUENCY:{seg2['id']}": "APPLIED"}
    finally:
        merker.zurueck()
        _aufraeumen(welt)


# ---------------------------------------------------------------- F4
def test_f4_merge_bleibt_bei_pausierten_bereichen(welt):
    """Zwei benachbarte EMPTY-Bereiche: SAFE_AUTO pausiert beide, der MERGE-Vorschlag bleibt offen — auch wenn nur noch
    Nachpruefungen (4 Laeufe, einer POOR) im Fenster liegen (vorher: < 14 Laeufe -> OBSOLETE, Uebernehmen 409)."""
    merker = _Merker(welt)
    _start(welt, km=KM3)
    db = welt.db
    try:
        _modus(welt, "SAFE_AUTO")
        _keine_rotation(welt)
        a, b, c = _seg(welt, km=KM3[0]), _seg(welt, km=KM3[1]), _seg(welt, km=KM3[2])
        _reihe(welt, a, 20, [0])
        _reihe(welt, b, 20, [0])
        _reihe(welt, c, 20, [5])
        _rechnen(welt)
        merge = welt.run(db[K.VORSCHLAEGE].find_one({"model_id": _mid(welt), "typ": "MERGE_KM_BUCKETS"}, {"_id": 0}))
        assert merge["status"] == "PROPOSED" and len(_aktive(welt, a["id"], "PAUSE_EMPTY")) == 1
        _lange_pause(welt, a)
        _lange_pause(welt, b, poor=(7, 14))              # b nur noch 2 gueltige Laeufe -> UNKNOWN, letzter Zustand EMPTY
        _rechnen(welt)
        assert welt.run(db[K.HEALTH].find_one({"segment_id": b["id"]}))["health"] != "EMPTY"
        m2 = welt.run(db[K.VORSCHLAEGE].find_one({"id": merge["id"]}, {"_id": 0}))
        assert m2["status"] == "PROPOSED", m2.get("obsolet_grund")
        assert m2["evidence"]["je_ez"][0]["health_b"] == "EMPTY"
        assert len(_aktive(welt, b["id"], "PAUSE_EMPTY")) == 1
    finally:
        merker.zurueck()
        _aufraeumen(welt)


# ---------------------------------------------------------------- F8 / F16
def test_f8_ersparnis_bei_budgetgrenze_nur_frei_werdende_laeufe(welt):
    """Rotation aktiv (Budget ist die Grenze, faellige Segmente warten): PAUSE_EMPTY spart 0 $ — die frei werdenden
    Laeufe gehen an andere Segmente (vorher: 0,26 $/Monat). Ohne Rotation: echte Dollar-Ersparnis."""
    merker = _Merker(welt)
    _start(welt)
    db = welt.db
    try:
        _modus(welt, "OBSERVE")
        seg = _seg(welt)
        _reihe(welt, seg, 20, [0])
        _rotation(welt)
        _rechnen(welt)
        v = _v(welt, seg["id"], "PAUSE_EMPTY")[0]
        assert v["estimated_monthly_saving_usd"] == 0.0 and v["ersparnis_budget_grenze"] is True
        assert v["laeufe_frei_monat"] == round((0.5 - 1 / 7) * 30.4, 1), "Rotationsfaktor 0,5: geplant heute 0,5 Laeufe/Tag"
        _keine_rotation(welt)
        _rechnen(welt)
        v = _v(welt, seg["id"], "PAUSE_EMPTY")[0]
        assert v["estimated_monthly_saving_usd"] == 0.26 and v["ersparnis_budget_grenze"] is False
        assert v["laeufe_frei_monat"] == round((1 - 1 / 7) * 30.4, 1)
        # Uebersicht (heutige Drosselung): Budgetgrenze -> 0 $, aber frei werdende Laeufe
        _rotation(welt, tag=K.heute_tag())
        u = welt.run(OPT.uebersicht(db))
        assert u["budget_grenze"] is True and u["ersparnis_offen_usd"] == 0.0 and u["laeufe_frei_offen_monat"] >= v["laeufe_frei_monat"]
        # reine Regel: alter Merker gilt nicht (unbekannt -> beobachtete Rate)
        assert OPT.rotation_aus_merker({"tag": "2031-03-20", "segmente_je_tag": 1, "segmente_gesamt": 2, "wartend": 1}, STICHTAG)["bekannt"] is False
    finally:
        merker.zurueck()
        _aufraeumen(welt)


def test_f16_merge_und_pause_derselben_segmente_nicht_doppelt(welt):
    """Zwei benachbarte EMPTY-Bereiche (je 0,304 $/Monat): PAUSE je 0,26 $, MERGE 0,30 $ — die Summe zaehlt je Segment
    nur die groesste Wirkung: 2 x 0,26 = 0,52 $ (vorher 0,82 $, mehr als beide Segmente zusammen kosten)."""
    merker = _Merker(welt)
    _start(welt, km=KM2)
    db = welt.db
    try:
        _modus(welt, "OBSERVE")
        _keine_rotation(welt)
        a, b = _seg(welt, km=KM2[0]), _seg(welt, km=KM2[1])
        _reihe(welt, a, 20, [0])
        _reihe(welt, b, 20, [0])
        _rechnen(welt)
        typen = sorted(v["typ"] for v in welt.run(db[K.VORSCHLAEGE].find({"model_id": _mid(welt), "status": "PROPOSED"}).to_list(10)))
        assert typen == ["MERGE_KM_BUCKETS", "PAUSE_EMPTY", "PAUSE_EMPTY"]
        mh = welt.run(db[K.MODELL_HEALTH].find_one({"model_id": _mid(welt)}, {"_id": 0}))
        assert mh["ersparnis_offen_usd"] == 0.52 and mh["vorschlaege_offen"] == 3
        summe = welt.run(OPT.wirkung_summe(db, K.VORSCHLAEGE, {"model_id": _mid(welt), "status": "PROPOSED"}, None))
        assert summe["usd"] == 0.52 and summe["laeufe"] == round(2 * (1 - 1 / 7) * 30.4, 1)
    finally:
        merker.zurueck()
        _aufraeumen(welt)


# ---------------------------------------------------------------- F9
def test_f9_admin_knopf_setzt_den_tagesmerker_nicht(welt, monkeypatch):
    """'Health jetzt berechnen' waehrend des Crawl-Fensters: der regulaere Lauf nach dem Fenster rechnet trotzdem
    (vorher: 'heute schon berechnet' bis zum Folgetag)."""
    merker = _Merker(welt, K.HEALTH_DOK)
    aufrufe = []

    async def _berechnen(db_, **kw):
        aufrufe.append(kw)
        return {"stichtag": kw.get("stichtag"), "fehler": 0, "modelle": 1, "health": {}}
    monkeypatch.setattr(OPT, "berechnen", _berechnen)
    db = welt.db
    try:
        welt.run(db[K.KONFIG].delete_one({"_id": K.HEALTH_DOK}))
        ab = OPT.taeglich_ab_stunde()
        frueh = datetime(2031, 4, 2, 7, 30, tzinfo=K.ZEITZONE)
        spaet = datetime(2031, 4, 2, ab, 5, tzinfo=K.ZEITZONE)
        welt.run(OPT.jetzt_berechnen(db, wer="test-admin", jetzt=frueh))
        stand = welt.run(K.merker_lesen(db, K.HEALTH_DOK))
        assert "tag" not in stand and stand["quelle"] == "admin" and stand["admin_tag"] == "2031-04-02"
        erg = welt.run(OPT.taeglich(db, jetzt=spaet))
        assert erg.get("modelle") == 1 and len(aufrufe) == 2, "der taegliche Lauf nach dem Fenster laeuft"
        assert welt.run(K.merker_lesen(db, K.HEALTH_DOK))["tag"] == "2031-04-02"
    finally:
        merker.zurueck()


# ---------------------------------------------------------------- F10 / F12
def test_f10_f12_listen_gefiltert_seitenweise_mit_gesamtzahl(welt):
    """19 offene Vorschlaege mit identischem updated_at: Seite 1 (5) zeigt die mit der groessten Wirkung (auch den
    Aufteilen-Vorschlag mit Mehrkosten) — nicht alphabetisch nach Schluessel; Gesamtzahl, weitere, je_typ; Blaettern
    liefert jeden genau einmal. Protokoll ebenso (gleiches 'at', stabil ueber offset)."""
    _aufraeumen(welt)
    db = welt.db
    mid = _mid(welt)
    jetzt = "2031-03-30T12:00:00+00:00"
    docs = []
    for i, (typ, wirkung) in enumerate([("MERGE_KM_BUCKETS", 5)] * 5 + [("PAUSE_EMPTY", 10)] * 5 + [("PRIORITIZE_HOT", 0)] * 5
                                       + [("REDUCE_FREQUENCY", 20)] * 3 + [("SPLIT_KM_BUCKET", 60)]):
        docs.append({"id": f"t{i}", "schluessel": f"{typ}:{mid}:{i}", "typ": typ, "model_id": mid, "segment_id": f"{mid}:s{i}",
                     "status": "PROPOSED", "updated_at": jetzt, "wirkung_betrag": float(wirkung) + i / 100,
                     "laeufe_frei_monat": -60.0 if typ == "SPLIT_KM_BUCKET" else float(wirkung)})
    try:
        welt.run(db[K.VORSCHLAEGE].insert_many([dict(d) for d in docs]))
        s1 = welt.run(OPT.vorschlaege_liste(db, status="offen", model_id=mid, limit=5))
        assert s1["gesamt"] == 19 and s1["anzahl"] == 5 and s1["weitere"] is True and s1["gekuerzt"] is True
        assert [v["typ"] for v in s1["vorschlaege"]][:4] == ["SPLIT_KM_BUCKET"] + ["REDUCE_FREQUENCY"] * 3
        assert s1["je_typ"] == {"MERGE_KM_BUCKETS": 5, "PAUSE_EMPTY": 5, "PRIORITIZE_HOT": 5, "REDUCE_FREQUENCY": 3, "SPLIT_KM_BUCKET": 1}
        ids = []
        for o in range(0, 19, 5):
            ids += [v["id"] for v in welt.run(OPT.vorschlaege_liste(db, status="offen", model_id=mid, limit=5, offset=o))["vorschlaege"]]
        assert sorted(ids) == sorted(d["id"] for d in docs) and len(ids) == 19
        nur = welt.run(OPT.vorschlaege_liste(db, status="offen", model_id=mid, typ="PAUSE_EMPTY", limit=2))
        assert nur["gesamt"] == 5 and nur["anzahl"] == 2
        with pytest.raises(OPT.Ungueltig):
            welt.run(OPT.vorschlaege_liste(db, sortierung="quatsch"))
        # Protokoll: 7 aktive Aenderungen mit identischem 'at'
        welt.run(db[K.AENDERUNGEN].insert_many([{"id": f"ta{i}", "segment_id": f"{mid}:s{i}", "model_id": mid, "typ": "PAUSE_EMPTY",
                                                 "status": "aktiv" if i < 7 else "aufgehoben", "at": jetzt, "label": "x"} for i in range(9)]))
        p = welt.run(OPT.aenderungen_liste(db, status="aktiv", model_id=mid, limit=3))
        assert p["gesamt"] == 7 and p["anzahl"] == 3 and p["weitere"] is True
        alle = []
        for o in (0, 3, 6):
            alle += [a["id"] for a in welt.run(OPT.aenderungen_liste(db, status="aktiv", model_id=mid, limit=3, offset=o))["aenderungen"]]
        assert sorted(alle) == [f"ta{i}" for i in range(7)]
        assert welt.run(OPT.aenderungen_liste(db, status="alle", model_id=mid, typ="PAUSE_EMPTY", limit=50))["gesamt"] == 9
    finally:
        _aufraeumen(welt)


# ---------------------------------------------------------------- F14
def test_f14_zaehler_je_auftrag_sofort_nachgerechnet(welt):
    """Ablehnen / Ablehnung aufheben / Moduswechsel: vorschlaege_offen und Ersparnis des Auftrags stimmen sofort
    (vorher erst nach dem naechsten Tageslauf)."""
    merker = _Merker(welt)
    _start(welt, km=KM2)
    db = welt.db
    try:
        _modus(welt, "OBSERVE")
        _keine_rotation(welt)
        a, b = _seg(welt, km=KM2[0]), _seg(welt, km=KM2[1])
        _reihe(welt, a, 20, [0])
        _reihe(welt, b, 20, [5])
        _rechnen(welt)
        mh = lambda: welt.run(db[K.MODELL_HEALTH].find_one({"model_id": _mid(welt)}, {"_id": 0}))  # noqa: E731
        vor = mh()["vorschlaege_offen"]
        pause = _v(welt, a["id"], "PAUSE_EMPTY")[0]
        welt.run(OPT.vorschlag_entscheiden(db, pause["id"], "ablehnen", wer="test"))
        assert mh()["vorschlaege_offen"] == vor - 1
        welt.run(OPT.ablehnung_aufheben(db, pause["id"], wer="test"))
        assert mh()["vorschlaege_offen"] == vor
        welt.run(OPT.modus_setzen(db, "SAFE_AUTO", wer="test"))
        assert mh()["safe_auto_aktiv"] >= 1
        welt.run(OPT.modus_setzen(db, "OBSERVE", wer="test"))
        assert mh()["safe_auto_aktiv"] == 0
    finally:
        merker.zurueck()
        _aufraeumen(welt)


# ---------------------------------------------------------------- F18
def test_f18_modellseite_nur_aktuelle_fassung(welt):
    """Pausierter Auftrag bzw. neue Fassung: modell_ansicht meldet 'nicht aktuell' (Grund, Stichtag) — die Modellseite
    zeigt dann kein altes Badge (vorher: altes HOT/THIN monatelang ohne Stichtag)."""
    _start(welt)
    db = welt.db
    try:
        seg = _seg(welt)
        _reihe(welt, seg, 20, [1, 2])
        _rechnen(welt)
        d = welt.run(OPT.modell_ansicht(db, _mid(welt)))
        assert d["aktuell"] is True and d["nicht_aktuell_grund"] is None and d["health_tag"] == STICHTAG
        welt.run(db[K.MODELLE].update_one({"id": _mid(welt)}, {"$set": {"status": "paused"}}))
        d = welt.run(OPT.modell_ansicht(db, _mid(welt)))
        assert d["aktuell"] is False and d["nicht_aktuell_grund"] == "pausiert" and d["health_tag"] == STICHTAG
        welt.run(db[K.MODELLE].update_one({"id": _mid(welt)}, {"$set": {"status": "active", "version": 2}}))
        d = welt.run(OPT.modell_ansicht(db, _mid(welt)))
        assert d["aktuell"] is False and d["nicht_aktuell_grund"] == "neue_fassung" and d["auftrag_version"] == 2 and d["health_version"] == 1
    finally:
        _aufraeumen(welt)


# ---------------------------------------------------------------- Routen
def test_routen_ablehnung_aufheben_und_listen(welt, monkeypatch):
    """Neue Aktion 'Ablehnung aufheben': nur Super-Admin, mit Protokoll (log_activity_sicher), 404/409; Listen mit
    offset/Sortierung; Lesen weiter current_admin."""
    from fastapi import HTTPException
    MA = _module("routes.markt_admin")
    monkeypatch.setattr(MA, "db", welt.db)
    merker = _Merker(welt)
    _start(welt)
    db = welt.db
    admin = {"id": "test-admin"}
    try:
        _modus(welt, "OBSERVE")
        seg = _seg(welt)
        _reihe(welt, seg, 20, [0])
        _rechnen(welt)
        v = _v(welt, seg["id"], "PAUSE_EMPTY")[0]
        with pytest.raises(HTTPException) as ex:
            welt.run(MA.admin_market_vorschlag_ablehnung_aufheben(v["id"], admin=admin))
        assert ex.value.status_code == 409
        welt.run(MA.admin_market_vorschlag_ablehnen(v["id"], admin=admin))
        r = welt.run(MA.admin_market_vorschlag_ablehnung_aufheben(v["id"], admin=admin))
        assert r["vorschlag"]["status"] == "PROPOSED"
        with pytest.raises(HTTPException) as ex:
            welt.run(MA.admin_market_vorschlag_ablehnung_aufheben("gibt-es-nicht", admin=admin))
        assert ex.value.status_code == 404
        liste = welt.run(MA.admin_market_vorschlaege(status="offen", typ=None, model_id=_mid(welt), limit=10, offset=0, sortierung="datum", _=admin))
        assert liste["gesamt"] == 1 and liste["sortierung"] == "datum"
        src = (Path(__file__).resolve().parent.parent / "routes" / "markt_admin.py").read_text(encoding="utf-8")
        kopf = src.split('@router.post("/admin/market/optimierung/vorschlaege/{vorschlag_id}/ablehnung-aufheben")')[1].split("\n\n")[0]
        assert "current_super_admin" in kopf and "log_activity_sicher" in kopf
    finally:
        merker.zurueck()
        _aufraeumen(welt)


def test_widerspruch_ist_das_gegenteil_der_anwendung():
    """Reine Regel (kein Pendeln): fuer jeden Status/Score gilt hoechstens eins — Vorschlag (anwenden) ODER Widerspruch
    (aufheben); UNKNOWN/STALE/UNSTABLE widersprechen nie."""
    seg = {"id": "s", "model_id": "m", "version": 1, "crawls_per_day": 1}
    basis = {"segment_id": "s", "version": 1, "valid_runs": 20, "valid_days": 20, "confidence": "MEDIUM", "kosten_je_lauf_usd": 0.01,
             "laeufe_je_tag": 1.0, "laeufe_je_tag_konfig": 1, "crawls_per_day": 1, "empty_rate": 0.0, "avg_valid_rows": 5}
    for st in ("HOT", "HEALTHY", "NORMAL", "THIN", "EMPTY"):
        for score in range(0, 101, 3):
            emp = H.empfehlung(st, score)
            h = {**basis, "health": st, "activity_score": score, "empfehlung": emp}
            typen = {v["typ"] for v in OPT.vorschlaege_segment(seg, h)}
            for typ in ("REDUCE_FREQUENCY", "PAUSE_EMPTY", "PRIORITIZE_HOT"):
                assert not (typ in typen and OPT.widerspruch(typ, h, seg)), (st, score, typ)
    for st in ("UNKNOWN", "STALE", "UNSTABLE"):
        for typ in ("REDUCE_FREQUENCY", "PAUSE_EMPTY", "PRIORITIZE_HOT"):
            assert OPT.widerspruch(typ, {**basis, "health": st, "activity_score": 0, "empfehlung": None}, seg) is None
    assert OPT.widerspruch("PAUSE_EMPTY", {**basis, "health": "THIN", "activity_score": 5}, seg).startswith("Daten widersprechen")
    assert OPT.widerspruch("PRIORITIZE_HOT", {**basis, "health": "HEALTHY", "activity_score": 72}, seg) is None, "72: nicht klar ausserhalb"
    assert OPT.widerspruch("PRIORITIZE_HOT", {**basis, "health": "HEALTHY", "activity_score": 69}, seg)
    _ = timedelta, timezone
