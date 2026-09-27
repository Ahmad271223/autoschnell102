# -*- coding: utf-8 -*-
"""Schlussrunde Marktanalyse (27.09.2026) — Pruefbefunde F/G und Berichte, Vorgaben des Auftraggebers.

Je Punkt ein Test, der ohne die Korrektur scheitert (Gegenprobe: Korrektur einzeln zurueckgenommen):
  S1  Ersparnis je Abruf: 2x -> 1x taeglich spart echte Laeufe und wird nie gedeckelt (Befund: 60 Segmente, 40 von
      2x auf 1x — 1216 Laeufe bzw. 12,16 $/Monat, vorher auf 0 gekappt); nur Intervall/Pause unter Budget-Rotation
      auf die frei gebliebenen Segment-Plaetze (gewichtet mit den Abrufen je Tag)
  S2  PAUSE_EMPTY pendelt nicht an der 80-%-Grenze (Befund Tag 42/49: 4/5 leer -> Pause, eine Woche spaeter ohne Lauf
      3/4 leer -> vorher aufgehoben); Aufhebung nur nach einem NEUEN Lauf mit Treffern; Wechselzahl gegen das Orakel
  S3  Mindestverweildauer 7 Tage (Produktentscheidung) mit Protokoll 'haelt bis'; Ausnahme Pause nach neuem Treffer
  S4  Obergrenzen der Zuordnung: Intervall hoechstens 7, Nachpruefung hoechstens 14 Tage; Altwerte beim Lesen begrenzt
  S5  modell_berechnen rechnet im Hilfsthread (Event-Loop frei)
  S6  EMPTY-pausierte Segmente gehoeren nicht in den Preiskorb und sind nie 'vorab' (Befund: 24 Segmente, 3 EMPTY mit
      Nachpruefung 30 bzw. 21 Tage — wieder 23.571,43, Anker, kein Serienstart-Hinweis)
  S7  Beschriftungen: 'Serienstart' nur bei echtem Serienbeginn, sonst 'seit mehr als 14 Tagen nicht abgerufen';
      Neustart nach Budget-Stillstand ohne '(Intervall ueber 14 Tage)'
Testdaten nur test-/t<hex>, Aufraeumen am Ende; Stichtage ausdruecklich (kein Mitternachts-Effekt).
"""
import asyncio
import sys
import time
import uuid
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))
import markt_pause_sim_hilfe as SIM  # noqa: E402
from test_befunde_runde17_termine import welt  # noqa: E402,F401
from test_markt_20260926 import JOBS, K  # noqa: E402
from test_markt_berichte_20260927 import B, _rotation_korb  # noqa: E402
from test_markt_berichte_20260927 import _doc_daten as _bdoc  # noqa: E402
from test_markt_berichte_20260927 import _rechnen as _brechnen  # noqa: E402
from test_markt_berichte_20260927 import _t as _bt  # noqa: E402
from test_markt_berichte_runde4_20260927 import _korb_ez, _tagesplan  # noqa: E402
from test_markt_fg_pruefbefunde_20260927 import _aktive, _keine_rotation, _Merker, _modus, _protokoll, _start  # noqa: E402
from test_markt_health_20260927 import H, OPT, STICHTAG, _aufraeumen, _doc, _mid, _rechnen, _seg, _t  # noqa: E402

ALT = "2027-11-01T08:00:00+00:00"          # laengst angelegt (vor jedem geladenen Fenster)


# ---------------------------------------------------------------- S1 Ersparnis je Abruf
def _summe(teile):
    f = OPT._wirkung_felder(teile)
    return {"laeufe": f["laeufe_frei_monat"], "laeufe_takt": round(sum(x["laeufe_takt"] for x in f["wirkung_je_segment"]), 1),
            "usd": f["estimated_monthly_saving_usd"], "usd_voll": f["estimated_monthly_saving_usd"], "budget_grenze": False}


def test_s1_ersparnis_2x_auf_1x_wird_nie_gedeckelt(welt):
    """Befund: Tagesplan-Merker {segmente_je_tag 60, segmente_gesamt 60, ruhend 0, wartend 0, segmente 60}, 40 Segmente
    SAFE_AUTO-REDUCE 2x -> 1x taeglich, 0,01 $/Lauf: je Segment 30,4 Laeufe bzw. 0,304 $ — Summe 1216 Laeufe bzw.
    12,16 $/Monat. aktive_wirkung_deckeln lieferte 0 Laeufe / 0 $ ('gedeckelt'), weil Kontingent und 'geplant'
    Segment-Plaetze sind. Jetzt: Takt-Senkungen nie gedeckelt; Intervalle/Pausen nur unter Budget-Rotation auf die frei
    gebliebenen Plaetze x Abrufe je Tag der ruhenden Segmente."""
    tag = STICHTAG
    ohne = OPT.rotation_aus_merker({"tag": tag, "segmente_je_tag": 60, "segmente_gesamt": 60, "ruhend": 0, "wartend": 0, "segmente": 60}, tag)
    h = {"laeufe_je_tag_konfig": 2, "laeufe_je_tag": 2.0, "kosten_je_lauf_usd": 0.01}
    w = OPT.wirkung_schaetzen(h, 1.0, ohne, ziel={"intervall_tage": 1, "crawls_per_day": 1})
    assert round(w["laeufe"], 6) == 30.4 and round(w["laeufe_takt"], 6) == 30.4 and round(w["usd"], 6) == 0.304
    summe = _summe([(f"s{i}", w) for i in range(40)])
    assert (summe["laeufe"], summe["laeufe_takt"], summe["usd"]) == (1216.0, 1216.0, 12.16)
    d = OPT.aktive_wirkung_deckeln(summe, ohne)
    assert (d["laeufe"], d["usd"], d["gedeckelt"]) == (1216.0, 12.16, False), d
    # Aufruf wie im Befund (Summe ohne Aufteilung): ohne Budget-Rotation ist jeder gesparte Lauf echt
    assert OPT.aktive_wirkung_deckeln({k: v for k, v in summe.items() if k != "laeufe_takt"}, ohne)["laeufe"] == 1216.0
    # unter Budget-Rotation spart 2x -> 1x auch dann echte Laeufe (vorher 0: 'geplant' 0,5 gegen 1x = 0,5)
    rot = OPT.rotation_aus_merker({"tag": tag, "segmente_je_tag": 50, "segmente_gesamt": 100, "ruhend": 0, "wartend": 50, "segmente": 50}, tag)
    wr = OPT.wirkung_schaetzen(h, 1.0, rot, ziel={"intervall_tage": 1, "crawls_per_day": 1})
    assert round(wr["laeufe"], 6) == round(0.5 * 30.4, 6) == round(wr["laeufe_takt"], 6) and wr["usd"] == 0.0
    # Budget-Rotation (60 von 100 je Tag), heute 20 ruhend (Auftrag 2x: 40 Abrufe), 55 geplant: 5 Plaetze x 2 Abrufe frei.
    # 40 Takt-Senkungen (1216) bleiben ganz, 800 Intervall-Laeufe werden auf 5 x 2 x 30,4 = 304 gedeckelt
    rot = OPT.rotation_aus_merker({"tag": tag, "segmente_je_tag": 60, "segmente_gesamt": 100, "ruhend": 20, "ruhend_abrufe": 40,
                                   "wartend": 0, "segmente": 55}, tag)
    gemischt = {"laeufe": 2016.0, "laeufe_takt": 1216.0, "usd": 20.16, "usd_voll": 20.16, "budget_grenze": False}
    d = OPT.aktive_wirkung_deckeln(gemischt, rot)
    assert (d["laeufe"], d["usd"], d["gedeckelt"]) == (1520.0, 15.2, True), d
    # ohne Gewicht im Merker (Altbestand): 1 Abruf je Platz
    rot1 = OPT.rotation_aus_merker({"tag": tag, "segmente_je_tag": 60, "segmente_gesamt": 100, "ruhend": 20, "wartend": 0, "segmente": 55}, tag)
    assert OPT.aktive_wirkung_deckeln(gemischt, rot1)["laeufe"] == 1368.0
    # Datenbank: Uebersicht 'Wirkung von SAFE_AUTO' mit 40 aktiven Aenderungen 2x -> 1x
    merker = _Merker(welt)
    _start(welt)
    db = welt.db
    mid = _mid(welt)
    try:
        heute = K.heute_tag()
        welt.run(K.merker_setzen(db, K.TAGESPLAN_DOK, tag=heute, segmente_je_tag=60, segmente_gesamt=60, ruhend=0, wartend=0, segmente=60))

        def _aenderungen(mit_takt):
            return [{"id": uuid.uuid4().hex, "segment_id": f"{mid}:x{i}", "model_id": mid, "typ": "REDUCE_FREQUENCY", "status": "aktiv",
                     "wer": "safe_auto", "alt": {"intervall_tage": 1, "crawls_per_day": 2, "pausiert": False},
                     "neu": {"intervall_tage": 1, "crawls_per_day": 1}, "at": f"{heute}T01:00:00+00:00", "tag": heute,
                     "estimated_monthly_saving_usd": 0.3, "laeufe_frei_monat": 30.4,
                     "wirkung_je_segment": [{"segment_id": f"{mid}:x{i}", "usd_voll": 0.304, "laeufe": 30.4,
                                             **({"laeufe_takt": 30.4} if mit_takt else {})}]} for i in range(40)]
        welt.run(db[K.AENDERUNGEN].insert_many(_aenderungen(True)))
        u = welt.run(OPT.uebersicht(db))
        assert (u["ersparnis_safe_auto_usd"], u["laeufe_frei_safe_auto_monat"], u["ersparnis_safe_auto_gedeckelt"]) == (12.16, 1216.0, False), u
        assert u["laeufe_frei_safe_auto_takt_monat"] == 1216.0
        # Altbestand ohne laeufe_takt, unter Budget-Rotation mit 'geplant' = Kontingent (keine freien Plaetze): eine reine
        # Frequenzsenkung (REDUCE, Intervall 1) zaehlt ganz als Takt — nie auf 0 gekappt
        welt.run(db[K.AENDERUNGEN].delete_many({"model_id": mid}))
        welt.run(db[K.AENDERUNGEN].insert_many(_aenderungen(False)))
        welt.run(K.merker_setzen(db, K.TAGESPLAN_DOK, tag=heute, segmente_je_tag=60, segmente_gesamt=100, ruhend=0, wartend=0, segmente=60))
        u = welt.run(OPT.uebersicht(db))
        assert (u["ersparnis_safe_auto_usd"], u["laeufe_frei_safe_auto_monat"]) == (12.16, 1216.0), u
    finally:
        merker.zurueck()
        _aufraeumen(welt)


def test_s1_tagesplan_merkt_die_abrufe_der_ruhenden_segmente(welt, monkeypatch):
    """Der Tagesplan schreibt ruhend_abrufe (Abrufe je Tag des Auftrags der ruhenden Segmente) in den Merker."""
    src = Path(JOBS.__file__).read_text(encoding="utf-8")
    assert "ruhend_abrufe=int(ruhend_abrufe)" in src and "ruhend_abrufe = sum(" in src
    r = OPT.rotation_aus_merker({"tag": STICHTAG, "segmente_je_tag": 5, "segmente_gesamt": 9, "ruhend": 2, "ruhend_abrufe": 4,
                                 "wartend": 0, "segmente": 5}, STICHTAG)
    assert (r["ruhend"], r["ruhend_abrufe"]) == (2, 4)


# ---------------------------------------------------------------- S2 PAUSE_EMPTY mit Hysterese
def test_s2_pause_pendelt_nicht_tag_42_49(welt):
    """Befund (Tag 42/49): ein Segment unter REDUCE alle 6 Tage hat am Tag 42 5 gueltige Laeufe im Fenster, 4 davon leer
    (0,8) -> EMPTY -> PAUSE 7 Tage. Tag 49 OHNE neuen Lauf: der aelteste (leere) Lauf faellt aus dem Fenster, 3/4 = 0,75 ->
    vorher THIN -> 'Daten widersprechen', Pause aufgehoben, REDUCE, kurz danach wieder PAUSE. Jetzt haelt EMPTY unter der
    Pause bis unter 60 % bzw. bis ein NEUER Lauf Treffer hat; ein neuer Treffer-Lauf hebt sie auf — auch innerhalb der
    Mindestverweildauer (Schutz gegen Blindheit)."""
    merker = _Merker(welt)
    _start(welt)
    db = welt.db
    try:
        _modus(welt, "SAFE_AUTO")
        _keine_rotation(welt)
        seg = _seg(welt)
        d42, d49 = _t(7), _t(0)
        for i, n in ((7, 0), (13, 0), (19, 1), (25, 0), (31, 0)):
            _doc(welt, seg, _t(i), n, praefix=f"r{i}")
        welt.run(db[K.AENDERUNGEN].insert_one({
            "id": uuid.uuid4().hex, "segment_id": seg["id"], "model_id": seg["model_id"], "version": 1, "typ": "REDUCE_FREQUENCY",
            "wer": "safe_auto", "alt": {"intervall_tage": 1, "crawls_per_day": 1, "pausiert": False}, "neu": {"intervall_tage": 6, "crawls_per_day": 1},
            "status": "aktiv", "at": f"{_t(60)}T01:00:00+00:00", "tag": _t(60), "reduziert_seit": _t(60), "vorschlag_id": None,
            "schluessel": f"REDUCE_FREQUENCY:{seg['id']}"}))
        welt.run(OPT._wirkung_neu(db, seg["id"]))
        _rechnen(welt, stichtag=d42)
        h = welt.run(db[K.HEALTH].find_one({"segment_id": seg["id"]}, {"_id": 0}))
        assert (h["valid_runs"], h["empty_rate"], h["health"]) == (5, 0.8, "EMPTY"), h
        pause = _aktive(welt, seg["id"], "PAUSE_EMPTY")
        assert len(pause) == 1 and pause[0]["tag"] == d42 and pause[0]["geprueft_bis"] == d42 and pause[0]["haelt_bis"] == OPT._tag_plus(d42, 7)
        # Tag 49 ohne Lauf: 3/4 leer — die Pause haelt (Hysterese, kein neuer Treffer-Lauf)
        erg = _rechnen(welt, stichtag=d49)
        h = welt.run(db[K.HEALTH].find_one({"segment_id": seg["id"]}, {"_id": 0}))
        assert (h["valid_runs"], h["empty_rate"], h["health"], h["health_grund"]) == (4, 0.75, "EMPTY", "leer_pause_haelt"), h
        assert erg["safe_auto"]["aufgehoben"] == 0 and len(_aktive(welt, seg["id"], "PAUSE_EMPTY")) == 1
        assert not any(str(a.get("beendet_grund") or "").startswith("Daten widersprechen") for a in _protokoll(welt, seg["id"]) if a["typ"] == "PAUSE_EMPTY")
        # reine Regel: THIN ohne neuen Treffer-Lauf widerspricht der Pause nicht (mit der Aenderung); ohne sie wie bisher
        thin = {**h, "health": "THIN", "segment_id": seg["id"]}
        a = _aktive(welt, seg["id"], "PAUSE_EMPTY")[0]
        assert OPT.widerspruch("PAUSE_EMPTY", thin, seg, a) is None and OPT.widerspruch("PAUSE_EMPTY", thin, seg)
        # ein NEUER Lauf mit Treffern 3 Tage nach der Pause (innerhalb der Mindestverweildauer): aufgehoben
        _doc(welt, seg, _t(4), 3, praefix="neu")
        erg = _rechnen(welt, stichtag=_t(4))
        ende = [x for x in _protokoll(welt, seg["id"]) if x["typ"] == "PAUSE_EMPTY"][-1]
        assert ende["status"] == "aufgehoben" and "neuer Lauf mit Treffern" in ende["beendet_grund"], ende
    finally:
        merker.zurueck()
        _aufraeumen(welt)


def test_s2_wechsel_gegen_das_orakel():
    """Stochastischer Markt wie im Befund (lam 0,082, Abgang 11,6 %/Tag, 10 Zeilen), 16 Segmente x 200 Tage, SAFE_AUTO-
    Schleife ohne Datenbank mit den echten Regeln (tests/markt_pause_sim_hilfe.py). Wechsel mit PAUSE-Bezug: vorher
    (nachgestellt, ohne Hysterese/Verweildauer) deutlich mehr als das Orakel mit taeglicher Messung; jetzt hoechstens so
    viele wie das Orakel, und weniger Protokolleintraege je Segment."""
    alt, neu, orakel = (SIM.lauf(16, 200, art) for art in ("alt", "neu", "orakel"))
    print("SIM-ORAKEL", {"alt": alt, "neu": neu, "orakel": orakel})
    assert alt["wechsel"] > 1.5 * orakel["wechsel"], (alt, orakel)
    assert neu["wechsel"] <= orakel["wechsel"], (neu, orakel)
    assert neu["max_eintraege"] < alt["max_eintraege"] and neu["eintraege"] < alt["eintraege"]


# ---------------------------------------------------------------- S3 Mindestverweildauer
def test_s3_mindestverweildauer_sieben_tage(welt):
    """Produktentscheidung: eine angewendete Wirkung wird fruehestens nach 7 Tagen geaendert. REDUCE 'alle 2 Tage' am
    Tag X; ab X+1 verlangen die Daten wieder die Auftragsfrequenz (HOT): die Aufhebung wird bis X+7 zurueckgehalten
    (Protokoll: haelt_bis, zurueckgehalten mit Grund), am Tag X+7 aufgehoben. Vorher: sofort am Folgetag."""
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
        assert [a["neu"] for a in red] == [{"intervall_tage": 2, "crawls_per_day": 1}] and red[0]["haelt_bis"] == _t(3), red
        for i in range(0, 30):                                       # sehr bewegter Markt: HOT (Auftrag 1x taeglich)
            _doc(welt, seg, _t(i), 5, neu=2, weg=2, red=1, top3=True, top5=True, praefix=f"h{i}")
        for tag in (_t(9), _t(4)):
            erg = _rechnen(welt, stichtag=tag)
            red = _aktive(welt, seg["id"], "REDUCE_FREQUENCY")
            assert len(red) == 1 and erg["safe_auto"]["zurueckgehalten"] >= 1, (tag, erg["safe_auto"])
            assert red[0]["zurueckgehalten"]["grund"].startswith("Daten widersprechen") and red[0]["zurueckgehalten"]["bis"] == _t(3)
        assert welt.run(db[K.HEALTH].find_one({"segment_id": seg["id"]}))["health"] == "HOT"
        erg = _rechnen(welt, stichtag=_t(3))
        assert _aktive(welt, seg["id"], "REDUCE_FREQUENCY") == [] and erg["safe_auto"]["aufgehoben"] >= 1
        ende = next(a for a in _protokoll(welt, seg["id"]) if a["typ"] == "REDUCE_FREQUENCY")
        assert ende["status"] == "aufgehoben" and ende["beendet_grund"].startswith("Daten widersprechen") and ende["beendet_at"]
        # reine Regeln
        a = {"tag": "2031-03-01"}
        assert OPT.verweil_bis(a) == "2031-03-08" and OPT.verweil_haelt(a, "2031-03-07") and not OPT.verweil_haelt(a, "2031-03-08")
        assert OPT.schwellen()["min_verweil_tage"] == OPT.MIN_VERWEIL_TAGE == 7
    finally:
        merker.zurueck()
        _aufraeumen(welt)


# ---------------------------------------------------------------- S4 Obergrenzen
def test_s4_obergrenzen_der_zuordnung(welt):
    """Frequenzstufen hoechstens LUECKE_MAX_TAGE (7) Tage, EMPTY-Nachpruefung hoechstens MAX_INTERVALL_TAGE (14) — mit
    verstaendlicher Meldung; gespeicherte Altwerte werden beim Lesen begrenzt (kein Migrationszwang)."""
    assert H.FREQUENZ_MAX_INTERVALL_TAGE == H.LUECKE_MAX_TAGE == 7 and H.NACHPRUEFUNG_MAX_TAGE == K.MAX_INTERVALL_TAGE == 14
    ok = dict(H.FREQUENZ_STANDARD, empty_nachpruefung_tage=14)
    assert H.frequenz_pruefen(ok)["empty_nachpruefung_tage"] == 14
    for falsch, text in ((dict(ok, stufen=[{"ab": 0, "intervall_tage": 8}]), "höchstens 7 Tage"),
                         (dict(ok, stufen=[{"ab": 0, "intervall_tage": 3, "intervall_tage_bis": 10}]), "höchstens 7 Tage"),
                         (dict(ok, empty_nachpruefung_tage=15), "höchstens 14 Tage")):
        with pytest.raises(H.Ungueltig) as ex:
            H.frequenz_pruefen(falsch)
        assert text in str(ex.value), str(ex.value)
    alt = {"stufen": [{"ab": 50, "intervall_tage": 1}, {"ab": 0, "intervall_tage": 14, "intervall_tage_bis": 30}], "empty_nachpruefung_tage": 30}
    b = H.frequenz_begrenzen(alt)
    assert b["stufen"][1]["intervall_tage"] == 7 and b["stufen"][1]["intervall_tage_bis"] is None and b["empty_nachpruefung_tage"] == 14
    # auch eine ungepruefte Zuordnung wird in der Rechnung begrenzt
    assert H._zuordnung("NORMAL", 0, alt)["intervall_tage"] == 7 and H._zuordnung("EMPTY", 0, alt)["intervall_tage"] == 14
    db = welt.db
    vorher = welt.run(db[K.KONFIG].find_one({"_id": K.OPTIMIERUNG_DOK}))
    try:
        welt.run(db[K.KONFIG].update_one({"_id": K.OPTIMIERUNG_DOK}, {"$set": {"frequenz": alt}}, upsert=True))
        einst = welt.run(OPT.einstellungen(db))
        assert einst["frequenz"] == b and einst["frequenz_begrenzt"] is True
        assert einst["frequenz_grenzen"] == {"intervall_max_tage": 7, "nachpruefung_max_tage": 14}
        with pytest.raises(H.Ungueltig):
            welt.run(OPT.frequenz_setzen(db, alt, wer="test"))
        welt.run(OPT.frequenz_setzen(db, b, wer="test"))
        assert welt.run(OPT.einstellungen(db))["frequenz_begrenzt"] is False
    finally:
        if vorher:
            welt.run(db[K.KONFIG].replace_one({"_id": K.OPTIMIERUNG_DOK}, vorher, upsert=True))
        else:
            welt.run(db[K.KONFIG].delete_one({"_id": K.OPTIMIERUNG_DOK}))
    # Tagesplan: eine Wirkung von vor der Begrenzung (alle 30 Tage) ruht hoechstens 14 Tage
    s = {"safe_auto": {"intervall_tage": 30, "pausiert": True}, "last_planned_tag": "2031-03-01"}
    assert JOBS._ruht(s, "2031-03-14") is True and JOBS._ruht(s, "2031-03-15") is False


# ---------------------------------------------------------------- S5 Event-Loop
def test_s5_modell_berechnen_rechnet_im_hilfsthread(welt, monkeypatch):
    """Die Rechnung aller Segmente eines Auftrags laeuft per asyncio.to_thread: auch wenn jedes Segment 20 ms braucht
    (20 Segmente = 400 ms), bleibt der Event-Loop frei (vorher: 400 ms am Stueck blockiert)."""
    _start(welt)
    db = welt.db
    try:
        segs = [_seg(welt, km=(i * 1000, i * 1000 + 999)) for i in range(20)]
        for s in segs:
            for i in range(8):
                _doc(welt, s, _t(i), 3, praefix=f"e{i}")
        echt = H.segment_health

        def langsam(*a, **k):
            time.sleep(0.02)
            return echt(*a, **k)
        monkeypatch.setattr(H, "segment_health", langsam)

        async def messen():
            lag, stop = 0.0, False

            async def ticker():
                nonlocal lag
                while not stop:
                    t0 = time.perf_counter()
                    await asyncio.sleep(0)
                    lag = max(lag, time.perf_counter() - t0)
            tk = asyncio.create_task(ticker())
            modell = await db[K.MODELLE].find_one({"id": _mid(welt)}, {"_id": 0})
            t0 = time.perf_counter()
            erg = await H.modell_berechnen(db, modell, stichtag=STICHTAG, cfg=H.FREQUENZ_STANDARD)
            dauer = time.perf_counter() - t0
            stop = True
            await tk
            return lag, dauer, erg
        lag, dauer, erg = welt.run(messen())
        assert len(erg["segmente"]) == 20 and dauer >= 0.4
        assert lag < 0.15, f"Event-Loop {lag * 1000:.0f} ms blockiert"
    finally:
        _aufraeumen(welt)


# ---------------------------------------------------------------- S6 EMPTY nicht im Preiskorb
def _leer_korb(welt):
    """Befund: 24 Segmente (12.000-35.000), die drei mittleren (22.000/23.000/24.000) sind EMPTY — wahres Niveau der
    uebrigen 21: 23.571,43. Segment 10 nur mit SAFE_AUTO-Pause, 11 nur mit last_empty_at am Segmentdokument (letzter
    gueltiger Lauf ohne Treffer), 12 mit beidem."""
    segs, preis = _rotation_korb(welt)
    segs = [{**s, "created_at": ALT} for s in segs]
    segs[10] = {**segs[10], "safe_auto": {"intervall_tage": 30, "pausiert": True, "reduziert_seit": "2027-11-15"}}
    segs[11] = {**segs[11], "last_empty_at": "2027-12-22T06:00:00+00:00", "last_success_at": "2027-10-01T06:00:00+00:00"}
    segs[12] = {**segs[12], "safe_auto": {"intervall_tage": 30, "pausiert": True}, "last_empty_at": "2028-01-10T06:00:00+00:00",
                "last_success_at": "2027-10-01T06:00:00+00:00"}
    wahr = sum(preis[s["id"]] for i, s in enumerate(segs) if i not in (10, 11, 12)) / 21
    assert round(wahr, 2) == 23571.43
    return segs, preis


def test_s6_empty_pausierte_segmente_nicht_im_preiskorb(welt):
    """Befund (FIVE_DAY 06.-10.02.2028, Nachpruefung 30 Tage): zwei der drei EMPTY-Segmente haben keine Beobachtung im
    geladenen Fenster (letzte am 30.12. bzw. 22.12.). fc1eceb nahm sie mit dem mittleren Gewicht in den Korb und fuehrte
    sie jeden Tag als 'vorab': korb_wirksam 23, Abdeckung 91,3 %, 0 Anker, Euro-Niveau leer, Hinweis 'Serienstart: 10
    Segment-Tag(e)' und Confidence HIGH. Jetzt: 23.571,43 an jedem Tag, 5 Anker, kein Serienstart-Hinweis, kein 'vorab'."""
    segs, preis = _leer_korb(welt)
    plan, docs = {}, []
    for k in range(-14, 5):
        t = B._plus(_bt(6), k)
        for i, s in enumerate(segs):
            if i in (10, 11):
                continue                                       # Nachpruefung 30.12. bzw. 22.12. — vor dem Fenster
            if i == 12:
                if t == _bt(2):
                    plan.setdefault(s["id"], set()).add(t)
                    docs.append(_bdoc(welt, s, t, 0, n=0))       # gueltig leer beobachtet (im Fenster)
                continue
            plan.setdefault(s["id"], set()).add(t)
            docs.append(_bdoc(welt, s, t, preis[s["id"]]))
    ber = _brechnen(welt, segs, docs, "FIVE_DAY", _bt(6), _bt(10), geplant=plan)
    k = ber["kennzahlen"]
    assert round(k["median_periode"], 2) == 23571.43 and k["anker_tage"] == 5 and k["korb_wirksam_segmente"] == 21, k
    assert k["vorab_segment_tage"] == 0 and all(r["vorab_segmente"] == 0 and round(r["median_korb"], 2) == 23571.43 for r in ber["tage"])
    assert not any("Serienstart" in h for h in ber["hinweise"]), ber["hinweise"]
    assert round(k["startwert"], 2) == round(k["endwert"], 2) == 23571.43
    # reine Regel: als leer bekannt nur mit Pause bzw. letztem Lauf ohne Treffer — ein neuer Lauf mit Zeilen hebt das auf
    daten = B._Daten({s["id"]: s for s in segs}, docs, _bt(6), _bt(10), geplant=plan)
    assert daten.leer_bekannt(segs[10]["id"]) and daten.leer_bekannt(segs[11]["id"]) and not daten.leer_bekannt(segs[0]["id"])
    wieder = B._Daten({segs[11]["id"]: {**segs[11], "last_success_at": "2028-01-02T06:00:00+00:00"}}, [], _bt(6), _bt(10), geplant={})
    assert not wieder.leer_bekannt(segs[11]["id"])


def test_s6_nachpruefung_21_tage_jede_periode_mit_ankern(welt):
    """Befund: Nachpruefung alle 21 Tage — in drei von fuenf FIVE_DAY-Perioden fiel die Zahl der Anker von 5 auf 1
    (Ersatz-Anker bei 95,5 %), ab zwei solchen Segmenten ging das Niveau verloren. Jetzt in jeder Februar-Periode
    23.571,43 an jedem Tag mit Anker."""
    segs, preis = _leer_korb(welt)
    versatz = {10: 0, 11: 7, 12: 14}
    for typ, von, bis in B.perioden_im_monat(2028, 2):
        if typ != "FIVE_DAY":
            continue
        plan, docs = {}, []
        tage = B.tage_zwischen(B._plus(von, -B.VORLAUF_TAGE), bis)
        for t in tage:
            nr = (B._datum(t) - B._datum("2027-12-01")).days
            for i, s in enumerate(segs):
                if i in versatz:
                    if (nr - versatz[i]) % 21 == 0:
                        plan.setdefault(s["id"], set()).add(t)
                        docs.append(_bdoc(welt, s, t, 0, n=0))
                    continue
                plan.setdefault(s["id"], set()).add(t)
                docs.append(_bdoc(welt, s, t, preis[s["id"]]))
        ber = _brechnen(welt, segs, docs, "FIVE_DAY", von, bis, geplant=plan)
        k = ber["kennzahlen"]
        n_tage = len(B.tage_zwischen(von, bis))
        assert k["anker_tage"] == n_tage and round(k["median_periode"], 2) == 23571.43 and k["vorab_segment_tage"] == 0, (von, k)


# ---------------------------------------------------------------- S7 Beschriftungen
def test_s7_intervall_21_ist_kein_serienstart(welt):
    """Befund: Intervall 21 (Budget reicht nicht), 24 laengst laufende Segmente, MONTHLY Feb. 2028: 'Serienstart: 24
    Segment-Tag(e) ... rueckwaerts verkettet' und 'Segm. noch nicht beobachtet', obwohl nichts startet und nichts
    verkettet wird (alle Euro-Werte leer). Jetzt: 'seit mehr als 14 Tagen nicht abgerufen', kein Serienstart."""
    segs, preis = _rotation_korb(welt)
    plan, docs = {}, []
    for k in range(-14, 29):
        for i, s in enumerate(segs):
            if (k - i) % 21 == 0:
                t = B._plus(_bt(1), k)
                plan.setdefault(s["id"], set()).add(t)
                docs.append(_bdoc(welt, s, t, preis[s["id"]], n=12))
    ber = _brechnen(welt, segs, docs, "MONTHLY", _bt(1), _bt(29), geplant=plan)
    k = ber["kennzahlen"]
    assert k["vorab_segment_tage"] > 0 and k["vorab_lange_segment_tage"] == k["vorab_segment_tage"] and k["vorab_neu_segment_tage"] == 0, k
    assert not any(h.startswith("Serienstart") for h in ber["hinweise"]), ber["hinweise"]
    lang = [h for h in ber["hinweise"] if "seit mehr als 14 Tagen nicht abgerufen" in h]
    assert lang and "rückwärts verkettet" not in lang[0] and "ohne Ankertag leer" in lang[0], lang
    assert all(r["vorab_lange_segmente"] == r["vorab_segmente"] for r in ber["tage"])
    assert "kein Ankertag (Euro-Niveau leer)" in k["confidence_gruende"] and k["confidence"] != "HIGH"


def test_s7_neustart_nach_budget_stillstand_nennt_keinen_intervall_grund(welt):
    """Budget-Stillstand 25.01.-07.02. (Tagesplan-Protokoll 'budget'), vorher liefen alle 24 Segmente taeglich; Neustart
    am 08.02. mit Rotation 18/Tag. Am 08. haben 6 Segmente keinen tragbaren Wert (letzter Abruf 24.01.) — vorher hiess
    der Confidence-Grund '(Intervall ueber 14 Tage)', obwohl das Intervall normal war. Jetzt: Stillstand."""
    segs, preis = _korb_ez(welt)
    plan = {s["id"]: {"2028-01-23", "2028-01-24"} for s in segs}
    for sid, tage in _tagesplan(segs, [_bt(8), _bt(9), _bt(10)], 18, letzter={s["id"]: "2028-01-24" for s in segs}).items():
        plan[sid] |= tage
    docs = [_bdoc(welt, s, t, preis[s["id"]]) for s in segs for t in sorted(plan[s["id"]])]
    status = {B._plus("2028-01-24", i): B.PLAN_BUDGET for i in range(1, 15)}
    ber = _brechnen(welt, segs, docs, "FIVE_DAY", _bt(6), _bt(10), geplant=plan, planung={"tage": status})
    k = ber["kennzahlen"]
    assert k["abgelaufene_segment_tage"] == 6 == k["abgelaufene_stillstand_segment_tage"], k
    assert not any("Intervall über" in g for g in k["confidence_gruende"]), k["confidence_gruende"]
    assert any("Stillstand" in g for g in k["confidence_gruende"]), k["confidence_gruende"]
    assert not any("Intervall über 14 Tage — z. B." in h for h in ber["hinweise"]) and any("nach einem Stillstand" in h for h in ber["hinweise"])
    assert {r["date"]: r["abgelaufene_stillstand_segmente"] for r in ber["tage"]}[_bt(8)] == 6
    # Intervall 21 bleibt 'Intervall ueber 14 Tage' (test_33) — derselbe Grund nur ohne Stillstand
    assert not any("Serienstart" in h for h in ber["hinweise"])
