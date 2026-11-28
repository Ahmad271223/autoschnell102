# -*- coding: utf-8 -*-
"""SAFE_AUTO-Schleife OHNE Datenbank mit den echten Regeln (markt.health.segment_health, markt.optimierung.
vorschlaege_segment / widerspruch / verweil_haelt / neuer_treffer_lauf) an einem stochastischen Markt — fuer die
Schlussrunde (Pendeln der PAUSE_EMPTY an der 80-%-Grenze, Vergleich mit einem Orakel mit taeglicher Messung).

Kein Test (kein test_-Praefix): wird von test_markt_schlussrunde_20260927 benutzt und kann fuer die Messung direkt
aufgerufen werden (python tests/markt_pause_sim_hilfe.py <segmente> <tage>).

Varianten:
  neu     heutige Regeln (Hysterese unter der Pause, Aufhebung nur nach neuem Treffer-Lauf, Mindestverweildauer)
  alt     Regeln vor der Schlussrunde nachgestellt: keine Hysterese (EMPTY_AUFHEBEN_ANTEIL = EMPTY_ANTEIL),
          widerspruch ohne Aenderung (jede Nicht-EMPTY-Beurteilung hebt auf), keine Mindestverweildauer, eine
          REDUCE-Empfehlung ersetzt eine bestehende Pause
  orakel  taegliche Messung (jeder Tag abgerufen, auch waehrend der Pause): pausiert genau dann, wenn der Status aus
          den vollstaendigen Daten EMPTY ist (UNKNOWN/STALE/UNSTABLE aendern nichts)
Gezaehlt werden die Wechsel mit PAUSE-Bezug (pausiert <-> nicht pausiert) und die Protokolleintraege je Segment.
"""
import random
import sys
from datetime import datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from markt import health as H  # noqa: E402
from markt import optimierung as OPT  # noqa: E402
from markt import speicher as SP  # noqa: E402

LAM, MU, RHO, ZEILEN = 0.082, 0.116, 0.02, 10          # Befund: lam 0,082, Abgang 11,6 %/Tag, 10 Zeilen
PAUSE, REDUCE = OPT.PAUSE, OPT.REDUCE


def _tag(d):
    return (datetime(2031, 1, 1) + timedelta(days=d)).strftime("%Y-%m-%d")


class Markt:
    """Poisson-Zugaenge (lam je Tag), jedes Inserat geht je Tag mit mu, aendert den Preis mit rho; Stichprobe = die
    ZEILEN guenstigsten. beobachten() liefert ein Tagesdokument wie speicher.verarbeiten."""

    def __init__(self, seed):
        self.r = random.Random(seed)
        self.pool, self.nr = {}, 0
        for _ in range(int(round(LAM / MU))):
            self._neu()
        self.gesehen, self.vorher = {}, None
        for _ in range(60):
            self.schritt()

    def _neu(self):
        self.nr += 1
        self.pool[f"L{self.nr}"] = 20000 + self.r.gauss(0, 1500)

    def _poisson(self, lam):
        import math
        grenze, k, p = math.exp(-lam), 0, 1.0
        while True:
            p *= self.r.random()
            if p <= grenze:
                return k
            k += 1

    def schritt(self):
        for i in list(self.pool):
            if self.r.random() < MU:
                del self.pool[i]
            elif self.r.random() < RHO:
                self.pool[i] *= (1 + self.r.choice([-1, 1]) * 0.03)
        for _ in range(self._poisson(LAM)):
            self._neu()

    def beobachten(self, d, seg):
        ids = sorted(self.pool, key=lambda i: self.pool[i])[:ZEILEN]
        neu = [i for i in ids if i not in self.gesehen or d - self.gesehen[i][0] >= 14]
        prev = self.vorher
        tag = _tag(d)
        doc = {"segment_id": seg["id"], "date": tag, "model_id": seg["model_id"], **SP.kennzahlen([self.pool[i] for i in ids]),
               "listing_ids": ids, "listing_ids_alle": ids, "new_in_sample_ids": sorted(neu), "new_in_sample_today": len(neu),
               "disappeared_count": len(set(prev[1]) - set(ids)) if prev else 0, "price_reductions_today": 0, "price_increases_today": 0,
               "top3_changed": (set(ids[:3]) != set(prev[1][:3])) if prev else None,
               "top5_changed": (set(ids[:5]) != set(prev[1][:5])) if prev else None,
               "data_quality": "GOOD", "valid_runs": 1, "empty_runs": 0 if ids else 1, "invalid_runs": 0, "version": 1,
               "definition_hash": "h1", "rows_soll": ZEILEN, "crawl_cost_usd": 0.01, "top_n_bewiesen": True,
               "vergleich_vortag": prev[0] if prev else None}
        for i in ids:
            self.gesehen[i] = (d, self.pool[i])
        self.vorher = (tag, ids)
        return doc


def _feld(a, kette):
    """Feld 'safe_auto' am Segment wie optimierung._wirkung_neu (nur REDUCE/PAUSE)."""
    if not a:
        return None
    neu = a["neu"]
    w = {"intervall_tage": int(neu.get("intervall_tage") or 1), "crawls_per_day": neu.get("crawls_per_day"),
         "pausiert": bool(neu.get("pausiert")), "reduziert_seit": kette, "seit": a["tag"]}
    if w["pausiert"]:
        w["pause_geprueft_bis"] = OPT.pause_geprueft_bis(a)
    return w


def segment(seed, tage, art):
    """Ein Segment ueber 'tage' Tage. Liefert (Wechsel mit PAUSE-Bezug, Protokolleintraege, Tage pausiert)."""
    m = Markt(seed)
    seg = {"id": f"s{seed}", "model_id": "m", "version": 1, "definition_hash": "h1", "max_items": ZEILEN, "crawls_per_day": 1}
    docs, a, kette, letzter_plan = [], None, None, None
    pausiert_vorher, wechsel, eintraege, tage_pause = False, 0, 0, 0
    for d in range(tage):
        tag = _tag(d)
        n = min(int(a["neu"]["intervall_tage"]), 14) if (a and art != "orakel") else 1
        if letzter_plan is None or d - letzter_plan >= n:
            docs.append(m.beobachten(d, seg))
            letzter_plan = d
        m.schritt()
        von = _tag(d - 29)
        docs = [x for x in docs if x["date"] >= von]
        w = _feld(a, kette) if art != "orakel" else None
        sw = dict(seg, safe_auto=w) if w else seg
        h = H.segment_health(sw, docs, stichtag=tag, cfg=H.FREQUENZ_STANDARD, jetzt_iso="2031-01-01T00:00:00+00:00")
        if art == "orakel":
            pausiert = (h["health"] == H.EMPTY) if h["health"] in OPT.DATEN_STATUS else pausiert_vorher
        else:
            kette_vorher = None
            if a:
                g = OPT.widerspruch(a["typ"], h, sw, a if art == "neu" else None)
                if g and art == "neu" and OPT.verweil_haelt(a, tag) and not (a["typ"] == PAUSE and OPT.neuer_treffer_lauf(a, h)):
                    g = None
                if not g and art == "neu" and a["typ"] == PAUSE:
                    lg = str(h.get("letzter_gueltiger_tag") or "")[:10]
                    if lg and lg > OPT.pause_geprueft_bis(a):
                        a["geprueft_bis"] = lg
                if g:
                    kette_vorher, a, kette = kette, None, None
            for v in OPT.vorschlaege_segment(sw, h):
                if v["typ"] not in (REDUCE, PAUSE) or H.CONFIDENCE_RANG.get(v.get("confidence") or "LOW", 1) < 2:
                    continue
                ziel = OPT._neu_aus_vorschlag(v)
                if a and a["typ"] == v["typ"] and a["neu"] == ziel:
                    continue
                if art == "neu" and a and ((v["typ"] == REDUCE and a["typ"] == PAUSE) or OPT.verweil_haelt(a, tag)):
                    continue
                kette = kette or kette_vorher or tag
                a = {"typ": v["typ"], "neu": ziel, "tag": tag, "geprueft_bis": tag}
                eintraege += 1
            pausiert = bool(a and a["neu"].get("pausiert"))
        wechsel += int(pausiert != pausiert_vorher)
        tage_pause += int(pausiert)
        pausiert_vorher = pausiert
    return wechsel, eintraege, tage_pause


def lauf(segmente, tage, art, seed0=0):
    """Summe ueber 'segmente' Segmente (Seeds seed0 .. seed0 + segmente - 1)."""
    alt_grenze = H.EMPTY_AUFHEBEN_ANTEIL
    if art == "alt":
        H.EMPTY_AUFHEBEN_ANTEIL = H.EMPTY_ANTEIL           # keine Hysterese (Stand vor der Schlussrunde)
    try:
        erg = [segment(seed0 + i, tage, art) for i in range(segmente)]
    finally:
        H.EMPTY_AUFHEBEN_ANTEIL = alt_grenze
    return {"wechsel": sum(e[0] for e in erg), "eintraege": sum(e[1] for e in erg), "max_eintraege": max(e[1] for e in erg),
            "tage_pausiert": sum(e[2] for e in erg)}


if __name__ == "__main__":
    n_seg = int(sys.argv[1]) if len(sys.argv) > 1 else 80
    n_tage = int(sys.argv[2]) if len(sys.argv) > 2 else 240
    for art in ("alt", "neu", "orakel"):
        print(art, lauf(n_seg, n_tage, art))
