# -*- coding: utf-8 -*-
"""Optimierungsvorschlaege und Auto-Optimierung in Stufen (Master-Auftrag Ahmad 26.09.2026, Phasen F/G —
Abschnitte 30-34, 38-44, 49, 50).

Vorschlaege (Abschnitt 43, Sammlung market_optimization_proposals) — nur aus den Health-Dokumenten (markt.health),
also nur aus gespeicherten Tageswerten:
  REDUCE_FREQUENCY  empfohlene Frequenz niedriger als die des Suchauftrags (z. B. THIN -> alle 2 Tage)
  PAUSE_EMPTY       EMPTY -> pausieren mit Nachpruefung (Standard alle 7 Tage); Historie bleibt
  PRIORITIZE_HOT    HOT -> im Tagesplan zuerst (keine zusaetzlichen Abrufe)
  MERGE_KM_BUCKETS  zwei benachbarte km-Bereiche desselben Auftrags sind in ALLEN EZ-Jahren duenn (THIN/EMPTY,
                    je >= 14 gueltige Laeufe) und passen zusammen in die bestellten Zeilen (Abschnitt 32)
  SPLIT_KM_BUCKET   ein km-Bereich ist in allen EZ-Jahren staendig voll (>= 90 % der Laeufe) und hat grosse
                    Preisstreuung (P25-P75 im Median >= 15 % des Low-Market-Medians) — Mehrkosten (Abschnitt 33)
Jeder Vorschlag traegt evidence, confidence (schwaechste beteiligte Segment-Confidence) und
estimated_monthly_saving_usd aus den ECHTEN Crawl-Kosten (crawl_cost_usd der Tagesdokumente je Lauf).
Idempotent: eindeutiger Schluessel (schluessel, Unique-Index) — derselbe Vorschlag entsteht nie taeglich neu, er
wird fortgeschrieben (last_seen_tag, Evidenz). Status PROPOSED -> ACCEPTED / REJECTED / APPLIED; ist die Bedingung
entfallen (oder der Auftrag geaendert/pausiert), wird er OBSOLETE ("ueberholt") — nie geloescht. Ein abgelehnter
Vorschlag bleibt abgelehnt (SAFE_AUTO wendet ihn nie an).

MERGE/SPLIT werden NIE automatisch angewendet. "Uebernehmen" (nur Super-Admin) aendert den Suchauftrag ueber
auftraege.aendern: neue km-Bereiche, NEUE FASSUNG (version + 1, neue Segment-IDs, alte Historie bleibt unveraendert
und wird nie mit der neuen vermischt), der Auftrag wird PAUSIERT — Aktivieren erst nach einem neuen Testlauf
(Testlauf-Pflicht, filter_hash enthaelt die km-Bereiche). Dabei entsteht kein Abruf.

Modi (Abschnitt 31, market_config/optimierung):
  OBSERVE    Standard (Phase F) — nur Health und Vorschlaege, der Tagesplan bleibt unveraendert
  SAFE_AUTO  folgt mit Phase G
  FULL_AUTO  gesperrt (nur Anzeige).

Zeitplan: einmal taeglich im Auswertungs-Worker (markt.auswertung, nach Hot Deals und Berichten) nach dem Ende des
Crawl-Fensters — die Wirkung gilt ab dem naechsten Tagesplan. Admin-Knopf "Health jetzt berechnen" (Super-Admin).
Zwei Server: derselbe job_lock wie der Auswertungs-Worker; Wartungspause: sofort anhalten, der Rest folgt spaeter.
Dieses Modul liest nur gespeicherte Werte — es loest NIE einen Marktabruf aus (Architekturtest test_b02); Kosten 0.
"""
from __future__ import annotations

import copy
import logging
import statistics
import uuid
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional, Tuple

from pymongo.errors import DuplicateKeyError

from markt import health, konfig
from markt.konfig import KONFIG, MODELL_HEALTH, MODELLE, SEGMENTE, VORSCHLAEGE

log = logging.getLogger(__name__)

OBSERVE, SAFE_AUTO, FULL_AUTO = konfig.MODUS_OBSERVE, konfig.MODUS_SAFE_AUTO, konfig.MODUS_FULL_AUTO
MODI = (OBSERVE, SAFE_AUTO, FULL_AUTO)
MODI_GESPERRT = (FULL_AUTO,)

REDUCE, PAUSE, HOT_PRIO, MERGE, SPLIT = "REDUCE_FREQUENCY", "PAUSE_EMPTY", "PRIORITIZE_HOT", "MERGE_KM_BUCKETS", "SPLIT_KM_BUCKET"
TYPEN = (MERGE, SPLIT, REDUCE, PAUSE, HOT_PRIO)
SAFE_TYPEN = (REDUCE, PAUSE, HOT_PRIO)           # darf SAFE_AUTO anwenden
STRUKTUR_TYPEN = (MERGE, SPLIT)                  # nur der Super-Admin per "Uebernehmen" (neue Fassung)
PROPOSED, ACCEPTED, REJECTED, APPLIED, OBSOLETE = "PROPOSED", "ACCEPTED", "REJECTED", "APPLIED", "OBSOLETE"
STATUS = (PROPOSED, ACCEPTED, REJECTED, APPLIED, OBSOLETE)
OFFEN = (PROPOSED, ACCEPTED)

# ---------------------------------------------------------------- Schwellen (Konstanten mit Begruendung)
# Zusammenlegen (Abschnitt 32): beide Bereiche in JEDEM EZ-Jahr des Auftrags THIN oder EMPTY (die km-Bereiche gelten
# fuer alle EZ-Jahre gemeinsam — ein Bereich, der nur 2018 duenn ist, 2022 aber voll, darf nicht verschwinden),
# je mindestens 14 gueltige Laeufe (wie EMPTY: zwei Wochen), und zusammen im Mittel nicht mehr Autos als bestellte
# Zeilen (sonst verdraengt der eine Bereich den anderen aus der gemeinsamen Stichprobe). Nur direkt anschliessende
# Bereiche (hoechstens 1 km Luecke, z. B. 10.000-30.000 und 30.001-55.000).
MERGE_MIN_LAEUFE = health.MIN_LAEUFE_EMPTY
MERGE_LUECKE_MAX_KM = 1
# Aufteilen (Abschnitt 33): "staendig voll" = in >= 90 % der gueltigen Laeufe volle Stichprobe (der Markt hat mehr,
# als wir sehen); "grosse Preisstreuung" = P25-P75 der Stichprobe im Median >= 15 % des Low-Market-Medians — in einem
# sauber abgegrenzten Segment liegen die guenstigsten Autos eng beieinander (typisch < 8 %), 15 % bedeuten
# verschiedene Preisniveaus im selben km-Bereich (z. B. 80.000 vs. 150.000 km). Nur Bereiche ab 30.000 km Breite
# (jede Haelfte >= 15.000 km), Teilung auf 5.000 km gerundet.
SPLIT_VOLL_ANTEIL = 0.9
SPLIT_STREUUNG_PCT = 15.0
SPLIT_MIN_BREITE_KM = 30000
SPLIT_RASTER_KM = 5000
MONAT_TAGE = health.MONAT_TAGE
# Taeglicher Lauf erst nach dem Crawl-Fenster (konfig.fenster_bis, deutsche Zeit) + 1 h: dann sind die Laeufe des
# Tages gespeichert; die Wirkung gilt ab dem naechsten Tagesplan (kurz nach Mitternacht)
TAEGLICH_NACH_FENSTER_H = 1
SPERRE = "markt-auswertung"       # dieselbe Sperre wie markt.auswertung.SPERRE — nie zwei Auswertungen gleichzeitig
SPERRE_S = 3600
LIMIT_MAX = 1000

FESTE_FELDER = ("typ", "schluessel", "model_id", "version", "segment_id", "source_segment_ids", "label", "ez_label", "km_label",
                "year_from", "min_km", "max_km")
DYNAMISCHE_FELDER = ("proposed_definition", "reason", "evidence", "confidence", "estimated_monthly_saving_usd", "health",
                     "activity_score")


class Ungueltig(ValueError):
    pass


class Konflikt(Ungueltig):
    """Zustand hat sich inzwischen geaendert (Vorschlag ueberholt, gleichzeitig entschieden) -> 409."""


class NichtGefunden(Ungueltig):
    pass


# ---------------------------------------------------------------- Hilfen
def _tag_plus(tag: str, tage: int) -> str:
    return (datetime.strptime(tag, "%Y-%m-%d") + timedelta(days=tage)).strftime("%Y-%m-%d")


def frequenz_text(e: Optional[Dict[str, Any]]) -> str:
    if not e:
        return "—"
    if e.get("pausiert"):
        return f"pausiert (Nachprüfung alle {int(e.get('intervall_tage') or 7)} Tage)"
    n, k = int(e.get("intervall_tage") or 1), int(e.get("crawls_per_day") or 1)
    if n <= 1:
        return "täglich" if k <= 1 else f"{k}× täglich"
    return f"alle {n} Tage"


def _pct(x: Any) -> str:
    return "—" if x is None else f"{round(float(x) * 100)} %"


def _km(a: int, b: int) -> str:
    return f"{round(int(a) / 1000)}–{round(int(b) / 1000)}k km"


def ersparnis(h: Dict[str, Any], rate_neu: float, *, wirkung_aktiv: bool) -> Optional[float]:
    """Geschaetzte Monatsersparnis aus den echten Kosten je Lauf: (Abrufe je Tag vorher - nachher) x Kosten je Lauf x
    30,4. 'Vorher' = beobachtete Abrufe je Tag (hoechstens die des Auftrags); laeuft schon eine SAFE_AUTO-Wirkung,
    die des Auftrags (sonst rechnete sich die Ersparnis mit ihrer eigenen Wirkung klein). Ohne Kostendaten None."""
    roh = _ersparnis_roh(h, rate_neu, wirkung_aktiv=wirkung_aktiv)
    return None if roh is None else round(roh, 2)


def _ersparnis_roh(h: Dict[str, Any], rate_neu: float, *, wirkung_aktiv: bool) -> Optional[float]:
    kj = h.get("kosten_je_lauf_usd")
    if kj is None:
        return None
    konf = float(h.get("laeufe_je_tag_konfig") or 1)
    beob = h.get("laeufe_je_tag")
    basis = konf if (wirkung_aktiv or beob is None) else min(konf, float(beob))
    return float(kj) * max(0.0, basis - float(rate_neu)) * MONAT_TAGE


def monatskosten_basis(h: Dict[str, Any], *, wirkung_aktiv: bool) -> Optional[float]:
    """Monatskosten eines Segments ohne SAFE_AUTO-Wirkung (ungerundet — MERGE/SPLIT summieren ueber die EZ-Jahre)."""
    return _ersparnis_roh(h, 0.0, wirkung_aktiv=wirkung_aktiv)


def _schwaechste(confs: List[Optional[str]]) -> str:
    werte = [c for c in confs if c in health.CONFIDENCE_RANG]
    if not werte:
        return "LOW"
    return min(werte, key=lambda c: health.CONFIDENCE_RANG[c])


def _seg_felder(seg: Dict[str, Any]) -> Dict[str, Any]:
    return {"segment_id": seg["id"], "model_id": seg.get("model_id"), "version": int(seg.get("version") or 1),
            "source_segment_ids": [seg["id"]], "label": seg.get("label"), "ez_label": seg.get("ez_label"),
            "km_label": seg.get("km_label"), "year_from": seg.get("year_from"), "min_km": seg.get("min_km"), "max_km": seg.get("max_km")}


def _evidenz(h: Dict[str, Any]) -> Dict[str, Any]:
    return {"days": h.get("valid_days"), "avg_rows": h.get("avg_valid_rows"), "empty_rate": h.get("empty_rate"),
            "unique_listings": h.get("unique_listings"), "valid_runs": h.get("valid_runs"), "invalid_runs": h.get("invalid_runs"),
            "activity_score": h.get("activity_score"), "health": h.get("health"), "window_days": h.get("window_days"),
            "new_listings": h.get("new_listings"), "top5_turnover_rate": h.get("top5_turnover_rate"),
            "kosten_je_lauf_usd": h.get("kosten_je_lauf_usd"), "laeufe_je_tag": h.get("laeufe_je_tag"), "stichtag": h.get("tag")}


# ---------------------------------------------------------------- Vorschlaege (reine Regeln)
def vorschlaege_segment(seg: Dict[str, Any], h: Dict[str, Any]) -> List[Dict[str, Any]]:
    """REDUCE_FREQUENCY / PAUSE_EMPTY / PRIORITIZE_HOT eines Segments aus seinem Health-Dokument."""
    raus: List[Dict[str, Any]] = []
    status, emp = h.get("health"), h.get("empfehlung")
    wirkung = seg.get("safe_auto") if isinstance(seg.get("safe_auto"), dict) else None
    aktiv = bool(wirkung)
    konf = {"intervall_tage": 1, "crawls_per_day": int(h.get("crawls_per_day") or seg.get("crawls_per_day") or 1)}
    ort = f"{seg.get('ez_label') or 'alle EZ'} · {seg.get('km_label') or ''}".strip()
    basis = {**_seg_felder(seg), "health": status, "activity_score": h.get("activity_score"), "confidence": h.get("confidence") or "LOW",
             "evidence": _evidenz(h)}
    if status == health.EMPTY and emp:
        n = int(emp["intervall_tage"])
        raus.append({**basis, "typ": PAUSE, "schluessel": f"{PAUSE}:{seg['id']}:{n}t",
                     "proposed_definition": {"pausiert": True, "nachpruefung_tage": n, "intervall_tage": n, "crawls_per_day": 1},
                     "reason": (f"{ort}: {h.get('valid_runs')} gültige Läufe, davon {_pct(h.get('empty_rate'))} ohne Treffer (EMPTY) — "
                                f"pausieren mit Nachprüfung alle {n} Tage; nichts wird gelöscht, die Historie bleibt"),
                     "estimated_monthly_saving_usd": ersparnis(h, 1.0 / n, wirkung_aktiv=aktiv)})
    elif status in (health.HOT, health.HEALTHY, health.NORMAL, health.THIN) and emp and not emp.get("pausiert"):
        r_neu = health.rate(emp) or 0.0
        if r_neu < health.rate(konf) - 1e-9 and int(h.get("valid_runs") or 0) >= health.min_laeufe(health.MIN_LAEUFE_EMPTY, wirkung):
            n, k = int(emp["intervall_tage"]), int(emp["crawls_per_day"])
            raus.append({**basis, "typ": REDUCE, "schluessel": f"{REDUCE}:{seg['id']}:{n}t:{k}x",
                         "proposed_definition": {"intervall_tage": n, "crawls_per_day": k, "frequency_days": emp.get("frequency_days"),
                                                 "vorher": frequenz_text(konf)},
                         "reason": (f"{ort}: Activity Score {h.get('activity_score')} ({status}) — {frequenz_text(emp)} statt "
                                    f"{frequenz_text(konf)} reicht"),
                         "estimated_monthly_saving_usd": ersparnis(h, r_neu, wirkung_aktiv=aktiv)})
    if status == health.HOT:
        raus.append({**basis, "typ": HOT_PRIO, "schluessel": f"{HOT_PRIO}:{seg['id']}",
                     "proposed_definition": {"prioritaet": "HOT"},
                     "reason": f"{ort}: sehr aktiver Markt (Score {h.get('activity_score')}) — im Tagesplan zuerst planen, keine zusätzlichen Abrufe",
                     "estimated_monthly_saving_usd": 0.0})
    return raus


def _km_liste(modell: Dict[str, Any], segs: List[Dict[str, Any]]) -> List[Tuple[int, int]]:
    """Die km-Bereiche des Auftrags (eigene, sonst die der aktuellen Segmente) aufsteigend."""
    eigene = modell.get("km_buckets")
    if isinstance(eigene, list) and eigene:
        return sorted((int(b["min_km"]), int(b["max_km"])) for b in eigene)
    return sorted({(int(s["min_km"]), int(s["max_km"])) for s in segs if s.get("min_km") is not None})


def vorschlaege_modell(modell: Dict[str, Any], segs: List[Dict[str, Any]], healths: Dict[str, Dict[str, Any]]) -> List[Dict[str, Any]]:
    """MERGE_KM_BUCKETS / SPLIT_KM_BUCKET eines aktiven Suchauftrags (nur aktuelle Fassung)."""
    if (modell.get("status") or "active") != "active":
        return []
    version = int(modell.get("version") or 1)
    aktuelle = [s for s in segs if s.get("enabled", True) and int(s.get("version") or 1) == version]
    if not aktuelle:
        return []
    ez = sorted({int(s["year_from"]) for s in aktuelle if s.get("year_from")})
    km = _km_liste(modell, aktuelle)
    rows = int(modell.get("rows") or aktuelle[0].get("max_items") or konfig.rows_je_segment())
    idx = {(int(s.get("year_from") or 0), int(s["min_km"]), int(s["max_km"])): s for s in aktuelle}
    jahre = ez or [0]
    raus: List[Dict[str, Any]] = []
    km_vorher = [{"min_km": a, "max_km": b} for a, b in km]
    # ---- zusammenlegen
    for (a0, a1), (b0, b1) in zip(km, km[1:]):
        if b0 - a1 > MERGE_LUECKE_MAX_KM:
            continue
        je_ez, ok = [], True
        for j in jahre:
            sa, sb = idx.get((j, a0, a1)), idx.get((j, b0, b1))
            ha, hb = (healths.get(sa["id"]) if sa else None), (healths.get(sb["id"]) if sb else None)
            if not ha or not hb:
                ok = False
                break
            if ha.get("health") not in (health.THIN, health.EMPTY) or hb.get("health") not in (health.THIN, health.EMPTY):
                ok = False
                break
            if int(ha.get("valid_runs") or 0) < MERGE_MIN_LAEUFE or int(hb.get("valid_runs") or 0) < MERGE_MIN_LAEUFE:
                ok = False
                break
            if float(ha.get("avg_valid_rows") or 0) + float(hb.get("avg_valid_rows") or 0) > rows:
                ok = False
                break
            je_ez.append((j, sa, sb, ha, hb))
        if not ok or not je_ez:
            continue
        neu = [b for b in km_vorher if (b["min_km"], b["max_km"]) not in ((a0, a1), (b0, b1))] + [{"min_km": a0, "max_km": b1}]
        neu.sort(key=lambda b: b["min_km"])
        kosten = []
        for _, sa, sb, ha, hb in je_ez:
            ka = monatskosten_basis(ha, wirkung_aktiv=bool(sa.get("safe_auto")))
            kb = monatskosten_basis(hb, wirkung_aktiv=bool(sb.get("safe_auto")))
            if ka is not None or kb is not None:
                kosten.append(((ka or 0.0) + (kb or 0.0)) / 2)
        mittel = lambda xs: round(sum(xs) / len(xs), 2) if xs else None  # noqa: E731
        evidenz = {"days": min(min(int(x[3].get("valid_days") or 0), int(x[4].get("valid_days") or 0)) for x in je_ez),
                   "avg_rows": {"a": mittel([float(x[3].get("avg_valid_rows") or 0) for x in je_ez]),
                                "b": mittel([float(x[4].get("avg_valid_rows") or 0) for x in je_ez])},
                   "empty_rate": {"a": mittel([float(x[3].get("empty_rate") or 0) for x in je_ez]),
                                  "b": mittel([float(x[4].get("empty_rate") or 0) for x in je_ez])},
                   "unique_listings": {"a": sum(int(x[3].get("unique_listings") or 0) for x in je_ez),
                                       "b": sum(int(x[4].get("unique_listings") or 0) for x in je_ez)},
                   "rows": rows, "window_days": health.FENSTER_TAGE,
                   "je_ez": [{"ez": j, "segment_a": sa["id"], "segment_b": sb["id"], "avg_a": ha.get("avg_valid_rows"),
                              "avg_b": hb.get("avg_valid_rows"), "empty_rate_a": ha.get("empty_rate"), "empty_rate_b": hb.get("empty_rate"),
                              "health_a": ha.get("health"), "health_b": hb.get("health"), "valid_runs_a": ha.get("valid_runs"),
                              "valid_runs_b": hb.get("valid_runs")} for j, sa, sb, ha, hb in je_ez]}
        ids = [x[1]["id"] for x in je_ez] + [x[2]["id"] for x in je_ez]
        raus.append({"typ": MERGE, "schluessel": f"{MERGE}:{modell['id']}:v{version}:{a0}-{a1}+{b0}-{b1}",
                     "model_id": modell["id"], "version": version, "segment_id": None, "source_segment_ids": ids,
                     "label": modell.get("label"), "ez_label": None, "km_label": f"{_km(a0, a1)} + {_km(b0, b1)} → {_km(a0, b1)}",
                     "year_from": None, "min_km": a0, "max_km": b1, "health": None, "activity_score": None,
                     "proposed_definition": {"km_buckets": neu, "km_vorher": km_vorher, "ez_years": ez, "rows": rows,
                                             "zusammen": [{"min_km": a0, "max_km": a1}, {"min_km": b0, "max_km": b1}],
                                             "neu": {"min_km": a0, "max_km": b1}},
                     "reason": (f"km-Bereiche {_km(a0, a1)} und {_km(b0, b1)} sind in allen {len(je_ez)} EZ-Jahren dünn "
                                f"(Ø {evidenz['avg_rows']['a']} bzw. {evidenz['avg_rows']['b']} Fahrzeuge je Lauf) — zu {_km(a0, b1)} "
                                f"zusammenlegen. Neue Fassung, die alte Historie bleibt unverändert; danach Testlauf"),
                     "evidence": evidenz,
                     "confidence": _schwaechste([x[3].get("confidence") for x in je_ez] + [x[4].get("confidence") for x in je_ez]),
                     "estimated_monthly_saving_usd": round(sum(kosten), 2) if kosten else None})
    # ---- aufteilen
    for b0, b1 in km:
        if b1 - b0 < SPLIT_MIN_BREITE_KM:
            continue
        mitte = int(round((b0 + b1) / 2 / SPLIT_RASTER_KM) * SPLIT_RASTER_KM)
        if not (b0 < mitte < b1):
            continue
        je_ez, ok = [], True
        for j in jahre:
            s = idx.get((j, b0, b1))
            h = healths.get(s["id"]) if s else None
            if not h or int(h.get("valid_runs") or 0) < MERGE_MIN_LAEUFE or float(h.get("voll_anteil") or 0) < SPLIT_VOLL_ANTEIL:
                ok = False
                break
            if h.get("health") in (health.STALE, health.UNKNOWN, health.EMPTY, health.THIN) or (
                    h.get("health") == health.UNSTABLE and h.get("health_grund") != "stark_schwankend"):
                ok = False
                break
            je_ez.append((j, s, h))
        streuung = [float(x[2]["preis_streuung_pct"]) for x in je_ez if x[2].get("preis_streuung_pct") is not None]
        if not ok or not je_ez or not streuung or statistics.median(streuung) < SPLIT_STREUUNG_PCT:
            continue
        neu = [b for b in km_vorher if (b["min_km"], b["max_km"]) != (b0, b1)] + [{"min_km": b0, "max_km": mitte},
                                                                               {"min_km": mitte + 1, "max_km": b1}]
        neu.sort(key=lambda b: b["min_km"])
        kosten = [monatskosten_basis(h, wirkung_aktiv=bool(s.get("safe_auto"))) for _, s, h in je_ez]
        kosten_n = [k for k in kosten if k is not None]
        med = round(statistics.median(streuung), 1)
        raus.append({"typ": SPLIT, "schluessel": f"{SPLIT}:{modell['id']}:v{version}:{b0}-{b1}",
                     "model_id": modell["id"], "version": version, "segment_id": None, "source_segment_ids": [x[1]["id"] for x in je_ez],
                     "label": modell.get("label"), "ez_label": None, "km_label": f"{_km(b0, b1)} → {_km(b0, mitte)} + {_km(mitte + 1, b1)}",
                     "year_from": None, "min_km": b0, "max_km": b1, "health": None, "activity_score": None,
                     "proposed_definition": {"km_buckets": neu, "km_vorher": km_vorher, "ez_years": ez, "rows": rows,
                                             "teilen": {"min_km": b0, "max_km": b1},
                                             "neu": [{"min_km": b0, "max_km": mitte}, {"min_km": mitte + 1, "max_km": b1}]},
                     "reason": (f"km-Bereich {_km(b0, b1)} ist in allen {len(je_ez)} EZ-Jahren ständig voll und die Preise streuen stark "
                                f"(P25–P75 im Median {med} % des Low-Market-Medians) — aufteilen in {_km(b0, mitte)} und {_km(mitte + 1, b1)}. "
                                "Neue Fassung, die alte Historie bleibt; ein Segment je EZ mehr (Mehrkosten)"),
                     "evidence": {"days": min(int(x[2].get("valid_days") or 0) for x in je_ez), "rows": rows,
                                  "avg_rows": round(sum(float(x[2].get("avg_valid_rows") or 0) for x in je_ez) / len(je_ez), 2),
                                  "empty_rate": round(sum(float(x[2].get("empty_rate") or 0) for x in je_ez) / len(je_ez), 3),
                                  "unique_listings": sum(int(x[2].get("unique_listings") or 0) for x in je_ez),
                                  "streuung_median_pct": med, "window_days": health.FENSTER_TAGE,
                                  "je_ez": [{"ez": j, "segment_id": s["id"], "voll_anteil": h.get("voll_anteil"),
                                             "preis_streuung_pct": h.get("preis_streuung_pct"), "avg_rows": h.get("avg_valid_rows"),
                                             "valid_runs": h.get("valid_runs")} for j, s, h in je_ez]},
                     "confidence": _schwaechste([x[2].get("confidence") for x in je_ez]),
                     "estimated_monthly_saving_usd": (-round(sum(kosten_n), 2)) if kosten_n else None})
    return raus


# ---------------------------------------------------------------- Einstellungen / Modus
async def einstellungen(db) -> Dict[str, Any]:
    """Modus + Frequenz-Zuordnung (market_config/optimierung). Unlesbar oder unbekannt -> OBSERVE + Standard."""
    doc = await konfig.merker_lesen(db, konfig.OPTIMIERUNG_DOK)
    modus = doc.get("modus") if doc.get("modus") in (OBSERVE, SAFE_AUTO) else OBSERVE
    try:
        frequenz = health.frequenz_pruefen(doc["frequenz"]) if doc.get("frequenz") else copy.deepcopy(health.FREQUENZ_STANDARD)
    except health.Ungueltig:
        frequenz = copy.deepcopy(health.FREQUENZ_STANDARD)
    return {"modus": modus, "frequenz": frequenz, "frequenz_standard": copy.deepcopy(health.FREQUENZ_STANDARD),
            "modus_seit": doc.get("modus_seit"), "modus_von": doc.get("modus_von"), "frequenz_seit": doc.get("frequenz_seit"),
            "frequenz_von": doc.get("frequenz_von"), "modus_verlauf": list(doc.get("modus_verlauf") or [])[-10:]}


async def frequenz_setzen(db, roh: Dict[str, Any], *, wer: str = "") -> Dict[str, Any]:
    cfg = health.frequenz_pruefen(roh)
    jetzt_iso = konfig.jetzt_iso()
    await db[KONFIG].update_one({"_id": konfig.OPTIMIERUNG_DOK},
                                {"$set": {"frequenz": cfg, "frequenz_seit": jetzt_iso, "frequenz_von": str(wer or ""), "updated_at": jetzt_iso}},
                                upsert=True)
    return cfg


# ---------------------------------------------------------------- Vorschlaege speichern (idempotent)
async def _vorschlaege_speichern(db, liste: List[Dict[str, Any]], *, lauf_id: str, tag: str, jetzt_iso: str) -> Dict[str, int]:
    z = {"neu": 0, "fortgeschrieben": 0, "wieder_offen": 0}
    for v in liste:
        schl = {"schluessel": v["schluessel"]}
        fest = {k: v.get(k) for k in FESTE_FELDER}
        dyn = {k: v.get(k) for k in DYNAMISCHE_FELDER}
        lauf = {"last_seen_tag": tag, "last_seen_at": jetzt_iso, "lauf_id": lauf_id}
        try:
            r = await db[VORSCHLAEGE].update_one(schl, {"$setOnInsert": {"id": uuid.uuid4().hex, "status": PROPOSED, "created_at": jetzt_iso,
                                                                         "first_seen_tag": tag, **fest}, "$set": lauf}, upsert=True)
            neu = r.upserted_id is not None
        except DuplicateKeyError:          # der andere Server legte ihn eben an — fortschreiben
            await db[VORSCHLAEGE].update_one(schl, {"$set": lauf})
            neu = False
        z["neu" if neu else "fortgeschrieben"] += 1
        # Evidenz nur an offenen/angewendeten fortschreiben; ein abgelehnter bleibt, wie er entschieden wurde
        await db[VORSCHLAEGE].update_one({**schl, "status": {"$in": [PROPOSED, ACCEPTED, APPLIED]}}, {"$set": {**dyn, "updated_at": jetzt_iso}})
        r2 = await db[VORSCHLAEGE].update_one({**schl, "status": OBSOLETE},
                                              {"$set": {**dyn, "status": PROPOSED, "wieder_offen_at": jetzt_iso, "updated_at": jetzt_iso},
                                               "$unset": {"obsolet_at": "", "obsolet_grund": ""}})
        z["wieder_offen"] += int(r2.modified_count)
    return z


async def _ueberholte_markieren(db, *, lauf_id: str, jetzt_iso: str, grund: str, model_ids: Optional[List[str]] = None,
                                ausser_model_ids: Optional[List[str]] = None) -> int:
    """Vorschlaege, die dieser Lauf nicht mehr erzeugt hat, werden OBSOLETE (nie geloescht). Betrifft offene;
    uebernommene MERGE/SPLIT und abgelehnte bleiben, wie sie sind."""
    filt: Dict[str, Any] = {"lauf_id": {"$ne": lauf_id}, "status": {"$in": list(OFFEN)}}
    if model_ids is not None:
        filt["model_id"] = {"$in": list(model_ids)}
    elif ausser_model_ids is not None:
        filt["model_id"] = {"$nin": list(ausser_model_ids)}
    r = await db[VORSCHLAEGE].update_many(filt, {"$set": {"status": OBSOLETE, "obsolet_at": jetzt_iso, "obsolet_grund": grund,
                                                          "updated_at": jetzt_iso}})
    return int(r.modified_count)


# ---------------------------------------------------------------- Entscheiden / Uebernehmen (Super-Admin)
async def vorschlag_entscheiden(db, vorschlag_id: str, aktion: str, *, wer: str) -> Dict[str, Any]:
    """annehmen: PROPOSED -> ACCEPTED (Merker). ablehnen: offen -> REJECTED (bleibt abgelehnt)."""
    v = await db[VORSCHLAEGE].find_one({"id": str(vorschlag_id)}, {"_id": 0})
    if not v:
        raise NichtGefunden("Vorschlag nicht gefunden")
    jetzt_iso = konfig.jetzt_iso()
    if aktion == "annehmen":
        erlaubt, neu = [PROPOSED], ACCEPTED
    elif aktion == "ablehnen":
        erlaubt, neu = [PROPOSED, ACCEPTED], REJECTED
    else:
        raise Ungueltig("Aktion unbekannt — annehmen oder ablehnen")
    filt: Dict[str, Any] = {"id": v["id"], "status": {"$in": erlaubt}}
    r = await db[VORSCHLAEGE].update_one(filt, {"$set": {"status": neu, "entschieden_von": wer, "entschieden_at": jetzt_iso,
                                                         "updated_at": jetzt_iso}})
    if r.modified_count == 0:
        raise Konflikt(f"Vorschlag steht auf {v.get('status')} — so nicht (mehr) möglich")
    return await db[VORSCHLAEGE].find_one({"id": v["id"]}, {"_id": 0})


async def vorschlag_uebernehmen(db, vorschlag_id: str, *, wer: str) -> Dict[str, Any]:
    """MERGE/SPLIT uebernehmen: Suchauftrag ueber auftraege.aendern auf die vorgeschlagenen km-Bereiche setzen —
    NEUE FASSUNG (alte Segmente/Historie bleiben unveraendert, neue Segment-IDs), Auftrag PAUSIERT (Testlauf-Pflicht).
    Nur, wenn der Auftrag seit dem Vorschlag nicht geaendert wurde (sonst ueberholt, 409)."""
    v = await db[VORSCHLAEGE].find_one({"id": str(vorschlag_id)}, {"_id": 0})
    if not v:
        raise NichtGefunden("Vorschlag nicht gefunden")
    if v.get("typ") not in STRUKTUR_TYPEN:
        raise Ungueltig("Übernehmen gibt es nur für Zusammenlegen/Aufteilen — Frequenz-Vorschläge wendet SAFE_AUTO an")
    if v.get("status") not in OFFEN:
        raise Konflikt(f"Vorschlag steht auf {v.get('status')} — nicht mehr übernehmbar")
    jetzt_iso = konfig.jetzt_iso()
    modell = await db[MODELLE].find_one({"id": v.get("model_id")}, {"_id": 0})
    pd = v.get("proposed_definition") or {}
    segs = [s async for s in db[SEGMENTE].find({"model_id": v.get("model_id"), "enabled": True}, {"_id": 0})] if modell else []
    aktuell = [{"min_km": a, "max_km": b} for a, b in _km_liste(modell or {}, segs)] if modell else None
    if (not modell or int(modell.get("version") or 1) != int(v.get("version") or 1)
            or aktuell != list(pd.get("km_vorher") or [])):
        await db[VORSCHLAEGE].update_one({"id": v["id"], "status": {"$in": list(OFFEN)}},
                                         {"$set": {"status": OBSOLETE, "obsolet_at": jetzt_iso, "obsolet_grund": "Suchauftrag inzwischen geändert",
                                                   "updated_at": jetzt_iso}})
        raise Konflikt("Der Suchauftrag wurde seit dem Vorschlag geändert — Vorschlag überholt")
    # Beanspruchen (zwei Admins / Doppelklick): nur einer kommt durch
    r = await db[VORSCHLAEGE].update_one({"id": v["id"], "status": v["status"]},
                                         {"$set": {"status": APPLIED, "angewendet_von": wer, "angewendet_at": jetzt_iso, "updated_at": jetzt_iso}})
    if r.modified_count == 0:
        raise Konflikt("Vorschlag wird gerade bearbeitet")
    from markt import auftraege
    try:
        neu = await auftraege.aendern(db, modell["id"], {"km_buckets": [dict(b) for b in pd.get("km_buckets") or []], "status": "paused"},
                                      fassung_erhoehen=True)
    except Exception:
        await db[VORSCHLAEGE].update_one({"id": v["id"], "status": APPLIED, "angewendet_von": wer},
                                         {"$set": {"status": v["status"], "updated_at": konfig.jetzt_iso()},
                                          "$unset": {"angewendet_von": "", "angewendet_at": ""}})
        raise
    await db[VORSCHLAEGE].update_one({"id": v["id"]}, {"$set": {"alte_version": int(modell.get("version") or 1),
                                                                "neue_version": int(neu.get("version") or 1)}})
    return {"vorschlag": await db[VORSCHLAEGE].find_one({"id": v["id"]}, {"_id": 0}), "modell": neu,
            "hinweis": "Neue Fassung angelegt, Suchauftrag pausiert — vor dem Aktivieren einen Testlauf machen (alle Segmente)."}


# ---------------------------------------------------------------- Berechnung (Worker / Admin-Knopf)
async def _wartung_aktiv(db) -> bool:
    try:
        import wartung
        return bool(await wartung.aktiv_async(db))
    except Exception:  # noqa: BLE001
        return False


def _schreiber():
    try:
        import wartung
        return wartung.hintergrund_schreibt()
    except Exception:  # noqa: BLE001
        import contextlib
        return contextlib.nullcontext()


async def _modell_zaehler(db, model_id: str) -> Dict[str, Any]:
    offen = await db[VORSCHLAEGE].find({"model_id": model_id, "status": {"$in": list(OFFEN)}},
                                       {"_id": 0, "estimated_monthly_saving_usd": 1}).to_list(5000)
    return {"vorschlaege_offen": len(offen),
            "ersparnis_offen_usd": round(sum(float(v.get("estimated_monthly_saving_usd") or 0) for v in offen
                                             if float(v.get("estimated_monthly_saving_usd") or 0) > 0), 2)}


async def berechnen(db, *, stichtag: Optional[str] = None, model_ids: Optional[List[str]] = None,
                    jetzt: Optional[datetime] = None) -> Dict[str, Any]:
    """Health + Vorschlaege fuer alle Suchauftraege mit aktiven Segmenten (model_ids grenzt ein — Tests/Admin). Ein Fehler in einem Auftrag haelt die anderen nicht auf (Zaehler 'fehler' ->
    Betriebsalarm im Worker). Schreibpause: sofort anhalten ('wartung'), nichts halb Fertiges als ueberholt markieren."""
    jetzt = jetzt or konfig.jetzt()
    jetzt_iso = jetzt.isoformat()
    tag = stichtag or konfig.heute_tag(jetzt)
    einst = await einstellungen(db)
    lauf_id = uuid.uuid4().hex
    z: Dict[str, Any] = {"stichtag": tag, "modus": einst["modus"], "modelle": 0, "segmente": 0,
                         "health": {s: 0 for s in health.STATUS}, "vorschlaege": 0, "vorschlaege_neu": 0, "ueberholt": 0, "fehler": 0}
    modelle = await health.aktive_modelle(db, model_ids)
    erfolgreich: List[str] = []
    for m in modelle:
        if await _wartung_aktiv(db):
            z["wartung"] = True
            break
        try:
            erg = await health.modell_berechnen(db, m, stichtag=tag, cfg=einst["frequenz"], jetzt_iso=jetzt_iso)
            segs = erg["segs"]
            liste: List[Dict[str, Any]] = []
            for sid, h in erg["segmente"].items():
                liste += vorschlaege_segment(segs[sid], h)
                z["health"][h["health"]] = z["health"].get(h["health"], 0) + 1
            liste += vorschlaege_modell(m, list(segs.values()), erg["segmente"])
            gespeichert = await _vorschlaege_speichern(db, liste, lauf_id=lauf_id, tag=tag, jetzt_iso=jetzt_iso)
            z["vorschlaege"] += len(liste)
            z["vorschlaege_neu"] += gespeichert["neu"]
            z["modelle"] += 1
            z["segmente"] += len(erg["segmente"])
            erfolgreich.append(m["id"])
        except Exception:  # noqa: BLE001
            log.exception("Health/Vorschlaege fuer %s gescheitert", m.get("id"))
            z["fehler"] += 1
    if z.get("wartung"):
        return z
    if erfolgreich:
        z["ueberholt"] += await _ueberholte_markieren(db, lauf_id=lauf_id, jetzt_iso=jetzt_iso, model_ids=erfolgreich,
                                                      grund="Bedingung nicht mehr erfüllt")
    if model_ids is None:
        z["ueberholt"] += await _ueberholte_markieren(db, lauf_id=lauf_id, jetzt_iso=jetzt_iso, ausser_model_ids=[m["id"] for m in modelle],
                                                      grund="Suchauftrag ohne aktive Segmente (pausiert, archiviert oder geändert)")
    for mid in erfolgreich:
        await db[MODELL_HEALTH].update_one({"model_id": mid}, {"$set": await _modell_zaehler(db, mid)})
    return z


def taeglich_ab_stunde() -> int:
    return min(23, konfig.fenster_bis() + TAEGLICH_NACH_FENSTER_H)


def _kurz(erg: Dict[str, Any]) -> Dict[str, Any]:
    return {k: v for k, v in erg.items() if isinstance(v, (int, float, str, bool)) or k == "health"}


async def taeglich(db, *, jetzt: Optional[datetime] = None) -> Dict[str, Any]:
    """Aufruf aus dem Auswertungs-Worker (alle 5 Minuten): einmal je Tag nach dem Crawl-Fenster rechnen (Merker
    market_config/health). Scheiterten Auftraege, bleibt die Fehlerzahl des Tages stehen (der Alarm bleibt offen) —
    ohne alle 5 Minuten alles neu zu rechnen; am naechsten Tag wird neu gerechnet."""
    jetzt = jetzt or konfig.jetzt()
    tag = konfig.heute_tag(jetzt)
    stand = await konfig.merker_lesen(db, konfig.HEALTH_DOK)
    if stand.get("tag") == tag:
        return {"uebersprungen": "heute schon berechnet", "fehler": int(stand.get("fehler") or 0)}
    if jetzt.astimezone(konfig.ZEITZONE).hour < taeglich_ab_stunde():
        return {"uebersprungen": "vor Ende des Crawl-Fensters", "fehler": 0}
    if await _wartung_aktiv(db):
        return {"wartung": True, "fehler": 0}
    erg = await berechnen(db, stichtag=tag, jetzt=jetzt)
    if not erg.get("wartung"):
        await konfig.merker_setzen(db, konfig.HEALTH_DOK, tag=tag, letzter_lauf_at=jetzt.isoformat(), fehler=int(erg.get("fehler") or 0),
                                   ergebnis=_kurz(erg), quelle="taeglich")
    return erg


async def jetzt_berechnen(db, *, wer: str = "", jetzt: Optional[datetime] = None) -> Dict[str, Any]:
    """Admin-Knopf 'Health jetzt berechnen': dieselbe Sperre wie der Auswertungs-Worker (zwei Server rechnen nie
    gleichzeitig) -> {"gesperrt": True}, wenn gerade ein anderer Prozess auswertet."""
    from job_lock import acquire, release
    token = await acquire(db, SPERRE, ttl_seconds=SPERRE_S)
    if not token:
        return {"gesperrt": True}
    try:
        with _schreiber():
            jetzt = jetzt or konfig.jetzt()
            erg = await berechnen(db, jetzt=jetzt)
            if not erg.get("wartung"):
                await konfig.merker_setzen(db, konfig.HEALTH_DOK, tag=konfig.heute_tag(jetzt), letzter_lauf_at=jetzt.isoformat(),
                                           fehler=int(erg.get("fehler") or 0), ergebnis=_kurz(erg), quelle="admin", von=str(wer or ""))
        return erg
    finally:
        await release(db, SPERRE, token)


# ---------------------------------------------------------------- Lesen (Admin)
MODI_ANZEIGE = [
    {"modus": OBSERVE, "text": "Beobachten — nur Empfehlungen, der Tagesplan bleibt unverändert", "gesperrt": False},
    {"modus": SAFE_AUTO, "text": "Sicher automatisch — Frequenz senken, EMPTY pausieren (mit Nachprüfung), HOT vorziehen; nie km/EZ/Zeilen (folgt)", "gesperrt": True},
    {"modus": FULL_AUTO, "text": "Voll automatisch — km-Bereiche zusammenlegen/aufteilen (gesperrt, nur nach ausdrücklicher Freigabe)", "gesperrt": True},
]


def schwellen() -> Dict[str, Any]:
    return {**health.schwellen(), "merge_min_laeufe": MERGE_MIN_LAEUFE, "merge_luecke_max_km": MERGE_LUECKE_MAX_KM,
            "split_voll_anteil": SPLIT_VOLL_ANTEIL, "split_streuung_pct": SPLIT_STREUUNG_PCT, "split_min_breite_km": SPLIT_MIN_BREITE_KM,
            "taeglich_ab_stunde": taeglich_ab_stunde()}


async def uebersicht(db) -> Dict[str, Any]:
    """Modus, Frequenz-Zuordnung, Schwellen, Stand, Zaehler je Health-Status, Modell-Health der aktiven Auftraege,
    Vorschlaege je Status/Typ, geschaetzte Ersparnis der offenen Vorschlaege. Nur lesen."""
    einst = await einstellungen(db)
    aktive = {str(m) for m in await db[SEGMENTE].distinct("model_id", {"enabled": True}) if m}
    modelle = [d async for d in db[MODELL_HEALTH].find({"model_id": {"$in": sorted(aktive)}}, {"_id": 0})]
    modelle.sort(key=lambda d: str(d.get("label") or d.get("model_id") or ""))
    zaehler = {s: 0 for s in health.STATUS}
    for d in modelle:
        for s, n in (d.get("zaehler") or {}).items():
            zaehler[s] = zaehler.get(s, 0) + int(n or 0)
    je_status = {g["_id"]: g["n"] async for g in db[VORSCHLAEGE].aggregate([{"$group": {"_id": "$status", "n": {"$sum": 1}}}])}
    je_typ = {g["_id"]: g["n"] async for g in db[VORSCHLAEGE].aggregate([{"$match": {"status": {"$in": list(OFFEN)}}},
                                                                         {"$group": {"_id": "$typ", "n": {"$sum": 1}}}])}
    offen_spar = [g async for g in db[VORSCHLAEGE].aggregate([
        {"$match": {"status": {"$in": list(OFFEN)}, "estimated_monthly_saving_usd": {"$gt": 0}}},
        {"$group": {"_id": None, "usd": {"$sum": "$estimated_monthly_saving_usd"}}}])]
    return {"modus": einst["modus"], "modi": MODI_ANZEIGE, "frequenz": einst["frequenz"], "frequenz_standard": einst["frequenz_standard"],
            "modus_seit": einst["modus_seit"], "modus_von": einst["modus_von"], "modus_verlauf": einst["modus_verlauf"],
            "schwellen": schwellen(), "stand": await konfig.merker_lesen(db, konfig.HEALTH_DOK), "zaehler": zaehler,
            "modelle": modelle, "vorschlaege_je_status": je_status, "vorschlaege_offen_je_typ": je_typ,
            "ersparnis_offen_usd": round(float(offen_spar[0]["usd"]), 2) if offen_spar else 0.0,
            "hinweis": ("Health und Vorschläge entstehen nur aus gespeicherten Tageswerten — keine Zusatzabrufe, keine Kosten. "
                        "Health (Marktaktivität) ist getrennt von der technischen Datenqualität.")}


async def vorschlaege_liste(db, *, status: str = "offen", typ: Optional[str] = None, model_id: Optional[str] = None,
                            limit: int = 300) -> Dict[str, Any]:
    filt: Dict[str, Any] = {}
    if status == "offen":
        filt["status"] = {"$in": list(OFFEN)}
    elif status in STATUS:
        filt["status"] = status
    elif status != "alle":
        raise Ungueltig(f"Unbekannter Status — erlaubt: offen, alle, {', '.join(STATUS)}")
    if typ:
        if typ not in TYPEN:
            raise Ungueltig(f"Unbekannter Typ — erlaubt: {', '.join(TYPEN)}")
        filt["typ"] = typ
    if model_id:
        filt["model_id"] = str(model_id)
    n = max(1, min(int(limit or 300), LIMIT_MAX))
    liste = await db[VORSCHLAEGE].find(filt, {"_id": 0}).sort([("updated_at", -1), ("schluessel", 1)]).to_list(n)
    rang = {s: i for i, s in enumerate((PROPOSED, ACCEPTED, APPLIED, REJECTED, OBSOLETE))}
    liste.sort(key=lambda v: (rang.get(v.get("status"), 9), -(float(v.get("estimated_monthly_saving_usd") or 0)), str(v.get("schluessel"))))
    return {"vorschlaege": liste, "anzahl": len(liste), "gekuerzt": len(liste) >= n, "status": status}


async def modell_ansicht(db, model_id: str) -> Optional[Dict[str, Any]]:
    """Modellseite / Aufklappen in der Uebersicht: Modell-Health, Health je Segment, offene Vorschlaege."""
    agg = (await health.modell_health(db, [model_id])).get(str(model_id))
    segmente = await health.segmente_eines_modells(db, model_id)
    if not agg and not segmente:
        return None
    vs = await db[VORSCHLAEGE].find({"model_id": str(model_id), "status": {"$in": list(OFFEN) + [APPLIED]}}, {"_id": 0})\
        .sort([("typ", 1), ("schluessel", 1)]).to_list(500)
    return {"model_id": str(model_id), "modell": agg, "segmente": segmente, "vorschlaege": vs}
