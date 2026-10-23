# -*- coding: utf-8 -*-
"""Pruefbefunde Phase F/G, Runde 2 (27.09.2026) — Gegenpruefung von a9005a9.

Je Befund ein Test, der ohne die Korrektur scheitert:
  F2 / Pendeln   Activity Score als TAGESRATE: an stochastischen Maerkten (Poisson-Zugaenge, geometrische Abgaenge,
                 guenstigste 5 als Stichprobe) verschiebt ein Abrufabstand von 2-4 Tagen den Score nicht mehr (vorher
                 +3 bis +9 Punkte bei duennen Maerkten = so viel wie die Hysterese -> SAFE_AUTO pendelte 2t <-> 4t)
  lange Nachpruefung  Pause mit Nachpruefung ab 15 Tagen war unaufhebbar (Untergrenze 3 Laeufe > Laeufe im Fenster)
  Ablehnung aufheben  nie wieder mit veraltetem Ziel offen (sofort eingeschaltetes SAFE_AUTO wendete es an)
  Rennen Ablehnen <-> SAFE_AUTO   Vorschlag wird nach dem Eintragen beansprucht, sonst Aenderung sofort beendet
  Fassungswechsel  Ablehnung und 30-Tage-Ruhe gelten fuer denselben Bereich auch in der neuen Fassung
  aenderung_id     unter d2d66db angewendete Vorschlaege bekommen die aenderung_id nachgetragen
  Ersparnis        Summe der aktiven Wirkungen zur Lesezeit auf (Kontingent - geplant) gedeckelt
Testdaten nur test-/t<hex>, Aufraeumen am Ende; Stichtag ausdruecklich (kein Mitternachts-Effekt).
"""
import math
import random
import sys
import uuid
from datetime import datetime, timedelta
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))
from test_befunde_runde17_termine import _module, welt  # noqa: E402,F401
from test_markt_20260926 import JOBS, K, SP  # noqa: E402
from test_markt_fg_pruefbefunde_20260927 import KM2, _aktive, _keine_rotation, _Merker, _modus, _protokoll, _start, _v  # noqa: E402
from test_markt_health_20260927 import H, OPT, STICHTAG, _aufraeumen, _doc, _mid, _rechnen, _reihe, _seg, _t  # noqa: E402


# ---------------------------------------------------------------- stochastischer Markt (reine Rechnung)
class _Markt:
    """Poisson-Zugaenge (lam je Tag), jedes Inserat geht je Tag mit Wahrscheinlichkeit mu, aendert den Preis mit rho;
    Stichprobe = die 5 guenstigsten. beobachten() schreibt ein Tagesdokument wie speicher.verarbeiten (neu im Sample =
    vorher nie gesehen, verschwunden = seit dem letzten Lauf weg, Preisaenderung gegen den letzten Schnappschuss,
    Top-3/Top-5-Wechsel gegen den letzten Lauf)."""

    def __init__(self, seed, lam, mu, rho, rows=5):
        self.r = random.Random(seed)
        self.lam, self.mu, self.rho, self.rows = lam, mu, rho, rows
        self.pool, self.nr = {}, 0
        for _ in range(int(round(lam / mu))):
            self._neu()
        self.gesehen, self.vorher = {}, None
        for _ in range(60):
            self.schritt()

    def _neu(self):
        self.nr += 1
        self.pool[f"L{self.nr}"] = 20000 + self.r.gauss(0, 1500)

    def _poisson(self, lam):
        grenze, k, p = math.exp(-lam), 0, 1.0
        while True:
            p *= self.r.random()
            if p <= grenze:
                return k
            k += 1

    def schritt(self):
        for i in list(self.pool):
            if self.r.random() < self.mu:
                del self.pool[i]
            elif self.r.random() < self.rho:
                self.pool[i] *= (1 + self.r.choice([-1, 1]) * 0.03)
        for _ in range(self._poisson(self.lam)):
            self._neu()

    def beobachten(self, d, seg):
        ids = sorted(self.pool, key=lambda i: self.pool[i])[:self.rows]
        neu, red, inc = [], 0, 0
        for i in ids:
            g = self.gesehen.get(i)
            if g is None or d - g[0] >= 14:
                neu.append(i)
            elif self.pool[i] < g[1] - 1e-9:
                red += 1
            elif self.pool[i] > g[1] + 1e-9:
                inc += 1
        prev = self.vorher
        tag = _tag(d)
        doc = {"segment_id": seg["id"], "date": tag, "model_id": seg["model_id"], **SP.kennzahlen([self.pool[i] for i in ids]),
               "listing_ids": ids, "listing_ids_alle": ids, "new_in_sample_ids": sorted(neu), "new_in_sample_today": len(neu),
               "disappeared_count": len(set(prev[1]) - set(ids)) if prev else 0, "price_reductions_today": red, "price_increases_today": inc,
               "top3_changed": (set(ids[:3]) != set(prev[1][:3])) if prev else None,
               "top5_changed": (set(ids[:5]) != set(prev[1][:5])) if prev else None,
               "data_quality": "GOOD", "valid_runs": 1, "empty_runs": 0 if ids else 1, "invalid_runs": 0, "version": 1,
               "definition_hash": "h1", "rows_soll": self.rows, "crawl_cost_usd": 0.01, "top_n_bewiesen": True,
               "vergleich_vortag": prev[0] if prev else None}
        for i in ids:
            self.gesehen[i] = (d, self.pool[i])
        self.vorher = (tag, ids)
        return doc


def _tag(d):
    return (datetime(2031, 1, 1) + timedelta(days=d)).strftime("%Y-%m-%d")


SEG_REIN = {"id": "s", "model_id": "m", "version": 1, "definition_hash": "h1", "max_items": 5, "crawls_per_day": 1}


def _score(seed, n, lam, mu, rho):
    """Score desselben Marktes, alle n Tage abgerufen (30-Tage-Fenster, SAFE_AUTO-Wirkung n Tage seit langem)."""
    m = _Markt(seed, lam, mu, rho)
    phase = random.Random(seed * 7 + n).randrange(n)
    docs = []
    for d in range(-2 * n, 30):
        if (d - phase) % n == 0:
            doc = m.beobachten(d, SEG_REIN)
            if d >= 0:
                docs.append(doc)
        m.schritt()
    seg = dict(SEG_REIN, safe_auto={"intervall_tage": n, "reduziert_seit": "2030-01-01"}) if n > 1 else SEG_REIN
    return H.segment_health(seg, docs, stichtag=_tag(29), cfg=H.FREQUENZ_STANDARD)["activity_score"] or 0


# ---------------------------------------------------------------- F2 / Pendeln
def test_r2_tagesrate_rein():
    """Momentenschaetzer: taeglich exakt Ereignisse/Bestand; gleicher Abstand geschlossen; gemischte Abstaende
    treffen die wahre Tagesrate (Erwartungswerte); Grenzfaelle."""
    assert H.tagesrate([]) is None and H.tagesrate([(0, 0, 3)]) is None
    assert H.tagesrate([(5, 1, 1), (5, 2, 1)]) == 0.3
    assert H.tagesrate([(5, 0, 4)]) == 0.0 and H.tagesrate([(5, 5, 4), (1, 1, 2)]) == 1.0
    assert abs(H.tagesrate([(5, 5 * (1 - 0.8 ** 3), 3)]) - 0.2) < 1e-9
    p = 0.3
    beob = [(1, 1 - (1 - p) ** 1, 1), (1, 1 - (1 - p) ** 4, 4), (10, 10 * (1 - (1 - p) ** 2), 2), (3, 3 * (1 - (1 - p) ** 7), 7)]
    assert abs(H.tagesrate(beob) - p) < 1e-6
    # Top-N ja/nein saettigt: 4 von 5 Vergleichen ueber 4 Tage mit Wechsel -> je Tag 33 %, nicht 80 %
    assert abs(H.tagesrate([(1, 1, 4)] * 4 + [(1, 0, 4)]) - (1 - 0.2 ** 0.25)) < 1e-9


@pytest.mark.parametrize("markt", [dict(lam=0.08, mu=0.05, rho=0.02), dict(lam=0.12, mu=0.081, rho=0.052),
                                   dict(lam=0.71, mu=0.032, rho=0.025)], ids=["duenn", "duenn_preise", "fast_45"])
def test_r2_f2_score_unabhaengig_vom_abstand_stochastischer_markt(markt):
    """Derselbe Markt (30 Seeds), nur der Abrufabstand aendert sich (1-4 Tage): der mittlere Score bleibt innerhalb
    2,5 Punkten (klar unter der Hysterese von 5). Vorher (a9005a9): duenn +3,4/+6,3/+8,3, duenn_preise +4,8/+6,9/+7,9,
    fast_45 +4,4/+6,6/+8,8 — die Top-N-Komponente je Vergleich stieg mit dem Abstand (1-(1-p)^n)."""
    seeds = range(30)
    mittel = {n: sum(_score(s, n, **markt) for s in seeds) / len(seeds) for n in (1, 2, 3, 4)}
    drift = {n: round(mittel[n] - mittel[1], 1) for n in (2, 3, 4)}
    assert all(abs(x) <= 2.5 for x in drift.values()), (mittel, drift)


# ---------------------------------------------------------------- lange Nachpruefung
def test_r2_lange_nachpruefung_nie_dauerhaft_unknown():
    """Fuer jede erlaubte Nachpruefung (1-60 Tage) und jede Lage der Laeufe: die Mindestlaufzahl ist nie groesser als
    die Laeufe, die im 30-Tage-Fenster liegen (vorher Untergrenze 3: ab 15 Tagen hoechstens 2 Laeufe im Fenster ->
    Status fuer immer UNKNOWN, die Pause unaufhebbar)."""
    for n in range(1, 61):
        w = {"pausiert": True, "intervall_tage": n, "reduziert_seit": "2030-01-01"}
        erw = H.erwartete_laeufe(w, stichtag=STICHTAG, crawls_per_day=1)
        for basis in (H.MIN_LAEUFE_BEWERTUNG, H.MIN_LAEUFE_EMPTY):
            mind = H.schwelle(basis, erw)
            assert mind >= 1
            for phase in range(n):
                im_fenster = sum(1 for d in range(H.FENSTER_TAGE) if (d - phase) % n == 0)
                if n <= H.FENSTER_TAGE or im_fenster:
                    assert im_fenster >= mind, (n, phase, im_fenster, mind)
    assert H.schwelle(14, 4) == 3 and H.schwelle(7, 10) == 7, "Nachpruefung 7 Tage / Reduktion 3 Tage unveraendert"


def test_r2_pause_mit_nachpruefung_20_tage_endet_bei_treffern(welt):
    """Admin stellt die EMPTY-Nachpruefung auf 20 Tage; SAFE_AUTO pausiert. Im Fenster liegen nur 1-2 Laeufe:
    weiter leer -> EMPTY (nicht UNKNOWN), Pause bleibt; die Nachpruefung findet 4 Autos -> 'Daten widersprechen',
    Pause aufgehoben — aber keine neue Wirkung aus 2 Laeufen (Confidence LOW), das Segment laeuft wieder taeglich.
    Vorher: UNKNOWN fuer immer, die Pause blieb trotz Autos stehen.
    Schlussrunde: die Nachpruefung ist jetzt hoechstens 14 Tage (frequenz_pruefen lehnt 20 ab, ein gespeicherter Wert 20
    wird beim Lesen auf 14 begrenzt); eine vor der Begrenzung angewendete Pause mit 20 Tagen (Altbestand) bleibt
    beurteilbar, der Tagesplan plant sie hoechstens alle 14 Tage, und sie endet mit einem NEUEN Treffer-Lauf."""
    merker = _Merker(welt)
    _start(welt)
    db = welt.db
    try:
        _modus(welt, "SAFE_AUTO")
        _keine_rotation(welt)
        with pytest.raises(H.Ungueltig):
            welt.run(OPT.frequenz_setzen(db, dict(H.FREQUENZ_STANDARD, empty_nachpruefung_tage=20), wer="test"))
        welt.run(db[K.KONFIG].update_one({"_id": K.OPTIMIERUNG_DOK},
                                         {"$set": {"frequenz": dict(H.FREQUENZ_STANDARD, empty_nachpruefung_tage=20)}}))
        einst = welt.run(OPT.einstellungen(db))
        assert einst["frequenz"]["empty_nachpruefung_tage"] == 14 and einst["frequenz_begrenzt"] is True
        seg = _seg(welt)
        _reihe(welt, seg, 20, [0])
        _rechnen(welt)
        assert [a["neu"] for a in _aktive(welt, seg["id"])] == [{"intervall_tage": 14, "crawls_per_day": 1, "pausiert": True}]
        # Altbestand: die Pause mit 20 Tagen laeuft seit Monaten: nur noch Nachpruefungen alle 20 Tage
        welt.run(db[K.AENDERUNGEN].update_many({"segment_id": seg["id"], "status": "aktiv"},
                                               {"$set": {"reduziert_seit": _t(120), "tag": _t(120), "geprueft_bis": _t(120),
                                                         "neu.intervall_tage": 20}}))
        welt.run(db[K.SEGMENTE].update_one({"id": seg["id"]}, {"$set": {"safe_auto.reduziert_seit": _t(120), "safe_auto.intervall_tage": 20,
                                                                        "safe_auto.pause_geprueft_bis": _t(120)}}))
        welt.run(db[K.TAGESSTATS].delete_many({"segment_id": seg["id"]}))
        for i in (40, 20, 0):
            _doc(welt, seg, _t(i), 0, praefix=f"l{i}")
        erg = _rechnen(welt)
        h = welt.run(db[K.HEALTH].find_one({"segment_id": seg["id"]}, {"_id": 0}))
        assert h["valid_runs"] == 2 and h["erwartete_laeufe"] == 1 and h["health"] == "EMPTY", h["health"]
        aktiv = _aktive(welt, seg["id"], "PAUSE_EMPTY")
        # 2 Laeufe: Confidence LOW -> keine neue Wirkung (die alte bleibt, Bestandsschutz); der Tagesplan begrenzt ihr
        # Intervall beim Lesen auf 14 Tage (ohne Migration)
        assert len(aktiv) == 1 and aktiv[0]["neu"]["intervall_tage"] == 20 and erg["safe_auto"]["aufgehoben"] == 0
        plan = {"safe_auto": {"intervall_tage": 20, "pausiert": True}, "last_planned_tag": STICHTAG}
        assert JOBS._ruht(plan, OPT._tag_plus(STICHTAG, 13)) is True and JOBS._ruht(plan, OPT._tag_plus(STICHTAG, 14)) is False
        # die naechste Nachpruefung (14 Tage spaeter) findet wieder Autos — ein NEUER Lauf mit Treffern
        spaeter = OPT._tag_plus(STICHTAG, 14)
        _doc(welt, seg, spaeter, 4, praefix="treffer")
        erg = _rechnen(welt, stichtag=spaeter)
        h = welt.run(db[K.HEALTH].find_one({"segment_id": seg["id"]}, {"_id": 0}))
        assert h["health"] not in ("UNKNOWN", "EMPTY") and h["confidence"] == "LOW", (h["health"], h["confidence"])
        assert erg["safe_auto"]["aufgehoben"] == 1 and _aktive(welt, seg["id"]) == []
        ende = [a for a in _protokoll(welt, seg["id"]) if a["typ"] == "PAUSE_EMPTY"][-1]
        assert ende["status"] == "aufgehoben" and ende["beendet_grund"].startswith("Daten widersprechen")
        assert "safe_auto" not in welt.run(db[K.SEGMENTE].find_one({"id": seg["id"]}, {"_id": 0}))
    finally:
        merker.zurueck()
        _aufraeumen(welt)


# ---------------------------------------------------------------- Ablehnung aufheben
def test_r2_ablehnung_aufheben_wendet_nie_das_veraltete_ziel_an(welt):
    """OBSERVE: Score 10 -> REDUCE 'alle 5 Tage', abgelehnt. Der Markt wird HOT (Tageslauf laeuft, der abgelehnte
    Vorschlag behaelt 5 Tage). Ablehnung aufheben, SAFE_AUTO sofort an: KEINE Reduktion am HOT-Segment (vorher:
    aktive Aenderung REDUCE {5 Tage} mit Score 10). Der naechste Lauf bewertet neu: HOT -> kein REDUCE."""
    merker = _Merker(welt)
    _start(welt)
    db = welt.db
    try:
        _modus(welt, "OBSERVE")
        _keine_rotation(welt)
        seg = _seg(welt)
        _reihe(welt, seg, 20, [2])
        _rechnen(welt)
        v = _v(welt, seg["id"], "REDUCE_FREQUENCY")[0]
        assert v["proposed_definition"]["intervall_tage"] == 5
        welt.run(OPT.vorschlag_entscheiden(db, v["id"], "ablehnen", wer="test-admin"))
        for i in range(20):
            _doc(welt, seg, _t(i), 5, neu=2, weg=2, red=1, top3=True, top5=True, praefix=f"h{i}")
        _rechnen(welt)
        assert welt.run(db[K.HEALTH].find_one({"segment_id": seg["id"]}))["health"] == "HOT"
        v = _v(welt, seg["id"], "REDUCE_FREQUENCY")[0]
        assert v["status"] == "REJECTED" and v["proposed_definition"]["intervall_tage"] == 5, "abgelehnt: nicht fortgeschrieben"
        auf = welt.run(OPT.ablehnung_aufheben(db, v["id"], wer="test-admin"))
        assert auf["status"] == "OBSOLETE"
        welt.run(OPT.modus_setzen(db, "SAFE_AUTO", wer="test-admin"))
        assert _aktive(welt, seg["id"], "REDUCE_FREQUENCY") == []
        _rechnen(welt)
        assert _aktive(welt, seg["id"], "REDUCE_FREQUENCY") == [] and _v(welt, seg["id"], "REDUCE_FREQUENCY")[0]["status"] == "OBSOLETE"
        assert (welt.run(db[K.SEGMENTE].find_one({"id": seg["id"]}, {"_id": 0})).get("safe_auto") or {}).get("intervall_tage", 1) == 1
    finally:
        merker.zurueck()
        _aufraeumen(welt)


# ---------------------------------------------------------------- Rennen Ablehnen <-> SAFE_AUTO
def test_r2_ablehnen_waehrend_safe_auto_laeuft(welt, monkeypatch):
    """Der Super-Admin lehnt ab, NACHDEM SAFE_AUTO die Kandidaten gelesen hat und bevor es die Aenderung eintraegt:
    keine aktive Aenderung, kein 'safe_auto' am Segment, der Vorschlag bleibt abgelehnt (vorher: Vorschlag REJECTED,
    gleichzeitig aktive Aenderung REDUCE {5 Tage} bis zum naechsten Tageslauf)."""
    merker = _Merker(welt)
    _start(welt)
    db = welt.db
    try:
        _modus(welt, "OBSERVE")
        _keine_rotation(welt)
        seg = _seg(welt)
        _reihe(welt, seg, 20, [2])
        _rechnen(welt)
        v = _v(welt, seg["id"], "REDUCE_FREQUENCY")[0]
        assert v["status"] == "PROPOSED" and v["confidence"] in ("MEDIUM", "HIGH")
        _modus(welt, "SAFE_AUTO")
        original = OPT._anwenden

        async def mit_ablehnung(db_, v_, seg_, **kw):
            await OPT.vorschlag_entscheiden(db_, v_["id"], "ablehnen", wer="test-admin")
            return await original(db_, v_, seg_, **kw)
        monkeypatch.setattr(OPT, "_anwenden", mit_ablehnung)
        welt.run(OPT.safe_auto_anwenden(db, tag=STICHTAG, model_ids=[_mid(welt)]))
        assert _aktive(welt, seg["id"]) == []
        assert "safe_auto" not in welt.run(db[K.SEGMENTE].find_one({"id": seg["id"]}, {"_id": 0}))
        assert _v(welt, seg["id"], "REDUCE_FREQUENCY")[0]["status"] == "REJECTED"
        prot = _protokoll(welt, seg["id"])
        assert len(prot) == 1 and prot[0]["status"] == "aufgehoben" and prot[0]["beendet_grund"] == "Vorschlag inzwischen abgelehnt"
    finally:
        merker.zurueck()
        _aufraeumen(welt)


# ---------------------------------------------------------------- Fassungswechsel
def test_r2_ablehnung_und_ruhe_ueberleben_den_fassungswechsel(welt):
    """Bereich 20-40k: REDUCE abgelehnt; Bereich 40-60k: nach einer Ruecknahme in Ruhe. Danach neue Fassung (z. B. MERGE
    zweier ANDERER Bereiche): dieselben Grenzen bekommen neue Segment-IDs (':v2:'). SAFE_AUTO wendet dort nichts an —
    die Ablehnung gilt fuer den Bereich, die Ruhe ebenso (vorher: beides galt nur fuer die alte Segment-ID). Aufheben
    am neuen Vorschlag hebt auch die alte Ablehnung auf -> der naechste Lauf wendet an."""
    merker = _Merker(welt)
    _start(welt, km=KM2)
    db = welt.db
    mid = _mid(welt)
    try:
        _modus(welt, "OBSERVE")
        _keine_rotation(welt)
        a, b = _seg(welt, km=KM2[0]), _seg(welt, km=KM2[1])
        _reihe(welt, a, 20, [2])
        _reihe(welt, b, 20, [2])
        _rechnen(welt)
        welt.run(OPT.vorschlag_entscheiden(db, _v(welt, a["id"], "REDUCE_FREQUENCY")[0]["id"], "ablehnen", wer="test-admin"))
        welt.run(db[K.SEGMENTE].update_one({"id": b["id"]}, {"$set": {"safe_auto_sperre_bis": _t(-30)}}))   # Ruhe nach Ruecknahme
        # neue Fassung: alte Segmente aus, neue IDs fuer dieselben Grenzen
        welt.run(db[K.MODELLE].update_one({"id": mid}, {"$set": {"version": 2, "definition_hash": "h2"}}))
        welt.run(db[K.SEGMENTE].update_many({"model_id": mid, "version": 1}, {"$set": {"enabled": False}}))
        a2, b2 = _seg(welt, km=KM2[0], version=2, dh="h2"), _seg(welt, km=KM2[1], version=2, dh="h2")
        assert a2["id"] != a["id"] and ":v2:" in a2["id"]
        _reihe(welt, a2, 20, [2])
        _reihe(welt, b2, 20, [2])
        _modus(welt, "SAFE_AUTO")
        _rechnen(welt)
        assert _aktive(welt, a2["id"]) == [], "abgelehnt bleibt abgelehnt — auch in der neuen Fassung"
        v2 = _v(welt, a2["id"], "REDUCE_FREQUENCY")[0]
        assert v2["status"] == "REJECTED" and "Familie abgelehnt" in v2["entscheidung_grund"]
        assert _aktive(welt, b2["id"]) == [], "Ruhe nach der Ruecknahme gilt fuer den Bereich"
        assert _v(welt, b2["id"], "REDUCE_FREQUENCY")[0]["status"] == "PROPOSED"
        # Aufheben am neuen Vorschlag: alte und neue Ablehnung weg -> naechster Lauf wendet an
        welt.run(OPT.ablehnung_aufheben(db, v2["id"], wer="test-admin"))
        assert _v(welt, a["id"], "REDUCE_FREQUENCY")[0]["status"] == "OBSOLETE"
        _rechnen(welt)
        assert [x["neu"]["intervall_tage"] for x in _aktive(welt, a2["id"])] == [5]
    finally:
        merker.zurueck()
        _aufraeumen(welt)


# ---------------------------------------------------------------- aenderung_id
def test_r2_aenderung_id_wird_nachgetragen(welt):
    """Ein unter d2d66db angewendeter Vorschlag (APPLIED, safe_auto, OHNE aenderung_id) bekommt beim naechsten Lauf die
    aenderung_id der aktiven Aenderung — der Knopf 'Zuruecknehmen' in der Vorschlagsliste erscheint wieder. Kein neuer
    Protokolleintrag (vorher: blieb dauerhaft ohne aenderung_id, 'unveraendert')."""
    merker = _Merker(welt)
    _start(welt)
    db = welt.db
    try:
        _modus(welt, "SAFE_AUTO")
        _keine_rotation(welt)
        heiss = _seg(welt)
        for i in range(20):
            _doc(welt, heiss, _t(i), 5, neu=2, weg=2, red=1, top3=True, top5=True, praefix=f"h{i}")
        _rechnen(welt)
        a = _aktive(welt, heiss["id"], "PRIORITIZE_HOT")
        v = _v(welt, heiss["id"], "PRIORITIZE_HOT")[0]
        assert len(a) == 1 and v["aenderung_id"] == a[0]["id"]
        welt.run(db[K.VORSCHLAEGE].update_one({"id": v["id"]}, {"$unset": {"aenderung_id": ""}}))
        _rechnen(welt)
        v = _v(welt, heiss["id"], "PRIORITIZE_HOT")[0]
        assert v["status"] == "APPLIED" and v["aenderung_id"] == a[0]["id"]
        assert len(_protokoll(welt, heiss["id"])) == 1
    finally:
        merker.zurueck()
        _aufraeumen(welt)


# ---------------------------------------------------------------- Ersparnis durch SAFE_AUTO
def test_r2_ersparnis_safe_auto_auf_kontingent_gedeckelt(welt):
    """100 Segmente, Kontingent 75/Tag, 25 warteten; SAFE_AUTO pausierte 40 EMPTY-Segmente (je 18,46 Laeufe bzw.
    0,1846 $/Monat, gerechnet mit Faktor 0,75). Heute wartet niemand mehr (66 geplant < 75): die Jobs sanken nur um
    9/Tag = 273,6 Laeufe/Monat — nicht 738 (vorher Faktor 2,7 zu hoch)."""
    tag = "2031-03-30"
    rot = OPT.rotation_aus_merker({"tag": tag, "segmente_je_tag": 75, "segmente_gesamt": 100, "ruhend": 34, "wartend": 0, "segmente": 66}, tag)
    summe = {"usd": 7.38, "usd_voll": 7.38, "laeufe": 738.4, "budget_grenze": False}
    d = OPT.aktive_wirkung_deckeln(summe, rot)
    assert d["gedeckelt"] is True and d["laeufe"] == 273.6 and d["usd"] == round(7.38 * 273.6 / 738.4, 2) and d["usd_voll"] == 7.38
    # Budgetgrenze aktiv bzw. Merker unbekannt: unveraendert
    grenze = OPT.rotation_aus_merker({"tag": tag, "segmente_je_tag": 75, "segmente_gesamt": 100, "ruhend": 0, "wartend": 25, "segmente": 75}, tag)
    assert OPT.aktive_wirkung_deckeln(summe, grenze) == summe and OPT.aktive_wirkung_deckeln(summe, OPT.ROTATION_UNBEKANNT) == summe
    # genug Luft unter dem Kontingent: nicht gedeckelt
    assert OPT.aktive_wirkung_deckeln({**summe, "laeufe": 200.0}, rot)["laeufe"] == 200.0
    # Uebersicht (Datenbank)
    merker = _Merker(welt)
    _start(welt)
    db = welt.db
    mid = _mid(welt)
    try:
        heute = K.heute_tag()
        welt.run(K.merker_setzen(db, K.TAGESPLAN_DOK, tag=heute, segmente_je_tag=75, segmente_gesamt=100, ruhend=34, wartend=0, segmente=66))
        welt.run(db[K.AENDERUNGEN].insert_many([
            {"id": uuid.uuid4().hex, "segment_id": f"{mid}:x{i}", "model_id": mid, "typ": "PAUSE_EMPTY", "status": "aktiv", "wer": "safe_auto",
             "neu": {"intervall_tage": 7, "crawls_per_day": 1, "pausiert": True}, "at": f"{heute}T01:00:00+00:00", "tag": heute,
             "estimated_monthly_saving_usd": 0.18, "laeufe_frei_monat": 18.46,
             "wirkung_je_segment": [{"segment_id": f"{mid}:x{i}", "usd_voll": 0.1846, "laeufe": 18.46}]} for i in range(40)]))
        u = welt.run(OPT.uebersicht(db))
        assert u["budget_grenze"] is False and u["ersparnis_safe_auto_gedeckelt"] is True
        assert u["laeufe_frei_safe_auto_monat"] == 273.6 and u["ersparnis_safe_auto_usd"] < 3.0
    finally:
        merker.zurueck()
        _aufraeumen(welt)
