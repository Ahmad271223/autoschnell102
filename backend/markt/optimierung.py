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
Jeder Vorschlag traegt evidence, confidence (schwaechste beteiligte Segment-Confidence) und die geschaetzte Wirkung
aus den ECHTEN Crawl-Kosten (crawl_cost_usd der Tagesdokumente je Lauf).
Idempotent: eindeutiger Schluessel (schluessel, Unique-Index) — derselbe Vorschlag entsteht nie taeglich neu, er
wird fortgeschrieben (last_seen_tag, Evidenz). Status PROPOSED -> ACCEPTED / REJECTED / APPLIED; ist die Bedingung
entfallen (oder der Auftrag geaendert/pausiert), wird ein offener Vorschlag OBSOLETE ("ueberholt") — nie geloescht.

Pruefbefunde F/G (27.09.2026):
  Schluessel = FAMILIE (F3/F7/F13): REDUCE_FREQUENCY:<segment>, PAUSE_EMPTY:<segment>, PRIORITIZE_HOT:<segment> —
    ohne Ziel-Intervall (das ist ein Feld, das sich aendern darf); MERGE/SPLIT je Auftrag + Fassung + Bereiche.
    Abgelehnt oder zurueckgenommen = diese Familie wird fuer dieses Segment NIE mehr automatisch angewendet, bis der
    Super-Admin die Ablehnung ausdruecklich aufhebt (ablehnung_aufheben, mit Protokoll; Runde 2: der Vorschlag wird
    dann ueberholt und im naechsten Lauf mit aktuellen Daten neu bewertet — nie mit dem Ziel von damals). Runde 2: die
    Ablehnung und die 30-Tage-Ruhe gelten fuer denselben Bereich (Auftrag + EZ + km-Grenzen, bereich_schluessel) auch
    in einer neuen Fassung; SAFE_AUTO beansprucht den Vorschlag nach dem Eintragen der Aenderung (Rennen mit Ablehnen
    waehrend des Laufs: die Aenderung endet sofort). Alte Schluessel mit Intervall
    (REDUCE_FREQUENCY:<seg>:<n>t:<k>x, PAUSE_EMPTY:<seg>:<n>t) wirken per Lese-Kompatibilitaet weiter: eine
    Ablehnung gilt fuer (typ, segment_id), egal unter welchem Schluessel — keine Migration.
  Bestandsschutz (F0/F5): eine angewendete SAFE_AUTO-Wirkung bleibt, solange die Daten ihr nicht WIDERSPRECHEN
    (widerspruch: EMPTY hat wieder Treffer, Score klar ausserhalb der Stufe). Fehlende Daten, UNKNOWN, STALE,
    UNSTABLE oder Confidence LOW heben sie nicht auf. Aendert sich die Empfehlung (Hysterese an der Grenze, siehe
    markt.health.empfehlung), wird die Wirkung aktualisiert (Protokoll: alt -> neu), nie aufgehoben und neu angelegt.
  Ersparnis ehrlich (F8/F16): Wirkung = geplante Laeufe unter der heutigen Drosselung (Tagesplan-Merker: Kontingent,
    wartende Segmente) minus geplante Laeufe mit Vorschlag. Ist das Budget die Grenze (Rotation: Segmente warten),
    sinken die Kosten nicht — dann 0 $ und "frei werdende Laeufe fuer andere Segmente". Summen je Segment nur die
    groesste Wirkung (MERGE und REDUCE/PAUSE derselben Segmente nicht doppelt). Runde 2: die Summe der AKTIVEN
    Wirkungen wird zur Lesezeit auf (Kontingent - heute geplant) gedeckelt (aktive_wirkung_deckeln) — hat SAFE_AUTO
    selbst die Budgetgrenze aufgehoben, sinken die Jobs nur bis zum Kontingent.

Schlussrunde (27.09.2026):
  Ersparnis je Abruf: Kontingent und 'geplant' sind SEGMENT-Plaetze, die Ersparnis sind LAEUFE. Weniger Abrufe je Tag
    (2x -> 1x) sparen immer echte Laeufe (das Segment belegt weiter seinen Platz) und werden nie gedeckelt
    (laeufe_takt); nur Intervall-Senkungen/Pausen unter Budget-Rotation (Kontingent < Segmente) werden auf die frei
    gebliebenen Segment-Plaetze gedeckelt, gewichtet mit den Abrufen je Tag der ruhenden Segmente (ruhend_abrufe).
  Pause mit Hysterese: eine PAUSE_EMPTY endet nur, wenn ein NEUER gueltiger Lauf seit der letzten Pruefung der Pause
    Treffer hatte (geprueft_bis an der Aenderung) — nie, weil ein alter leerer Lauf aus dem Fenster faellt; der Status
    haelt unter der Pause EMPTY bis unter 60 % (markt.health.EMPTY_AUFHEBEN_ANTEIL).
  Mindestverweildauer (Produktentscheidung): eine angewendete Wirkung wird fruehestens nach MIN_VERWEIL_TAGE geaendert
    (Ziel aktualisiert, ersetzt oder aufgehoben) — ausser Aufhebung einer Pause nach neuem Treffer-Lauf (nie blind) und
    Entscheidungen des Betreibers (Ruecknahme, Ablehnung, Modus OBSERVE, Auftrag pausiert/geaendert).

MERGE/SPLIT werden NIE automatisch angewendet. "Uebernehmen" (nur Super-Admin) aendert den Suchauftrag ueber
auftraege.aendern: neue km-Bereiche, NEUE FASSUNG (version + 1, neue Segment-IDs, alte Historie bleibt unveraendert
und wird nie mit der neuen vermischt), der Auftrag wird PAUSIERT — Aktivieren erst nach einem neuen Testlauf
(Testlauf-Pflicht, filter_hash enthaelt die km-Bereiche). Dabei entsteht kein Abruf.

Modi (Abschnitt 31, market_config/optimierung):
  OBSERVE    Standard — nur Health und Vorschlaege, der Tagesplan bleibt unveraendert
  SAFE_AUTO  darf NUR: Frequenz je Segment senken (REDUCE_FREQUENCY), EMPTY pausieren mit Nachpruefung
             (PAUSE_EMPTY), HOT vorziehen (PRIORITIZE_HOT). Nie km-Bereiche, EZ, Zeilen oder Filter (keine neue
             Fassung), nie mehr Abrufe als der Auftrag vorsieht (also nie ueber das Budget). Wirkung ueber das Feld
             'safe_auto' am Segment, das der Tagesplan des Crawlers nur im Modus SAFE_AUTO liest. Jede Aenderung steht
             im Protokoll (market_optimization_changes: wer = 'safe_auto', alt -> neu, Grund) und ist einzeln
             ruecknehmbar (Ruecknahme = Familie abgelehnt + 30 Tage Ruhe fuer das Segment). Neue Aenderungen nur ab
             Confidence MEDIUM. Zurueck auf OBSERVE hebt alle Wirkungen auf (spaetestens am naechsten Tagesplan).
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
from markt.konfig import AENDERUNGEN, KONFIG, MODELL_HEALTH, MODELLE, SEGMENTE, VORSCHLAEGE

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
AKTIV, ZURUECKGENOMMEN, AUFGEHOBEN = "aktiv", "zurueckgenommen", "aufgehoben"
WER_AUTO = "safe_auto"

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
# SAFE_AUTO: neue Wirkungen erst ab Confidence MEDIUM (>= 14 gueltige Laeufe); nach einer Ruecknahme 30 Tage Ruhe
SAFE_MIN_CONFIDENCE = "MEDIUM"
RUECKNAHME_SPERRE_TAGE = 30
# Mindestverweildauer (Produktentscheidung Ahmad, Schlussrunde 27.09.2026): eine angewendete SAFE_AUTO-Wirkung eines
# Segments wird fruehestens nach 7 Tagen geaendert. Eine Woche = mindestens eine Nachpruefung (Standard alle 7 Tage)
# bzw. 3-7 Laeufe unter einer Reduktion — erst dann liegt ueberhaupt ein neuer Messpunkt vor; kuerzere Wechsel waeren
# Rauschen und fuellten das Protokoll (Befund: bis zu 33 Eintraege je Segment). Ausnahmen: Aufhebung einer Pause nach
# einem neuen Treffer-Lauf (Schutz gegen Blindheit) und alle Entscheidungen des Betreibers.
MIN_VERWEIL_TAGE = 7
MONAT_TAGE = health.MONAT_TAGE
# Taeglicher Lauf erst nach dem Crawl-Fenster (konfig.fenster_bis, deutsche Zeit) + 1 h: dann sind die Laeufe des
# Tages gespeichert; die Wirkung gilt ab dem naechsten Tagesplan (kurz nach Mitternacht)
TAEGLICH_NACH_FENSTER_H = 1
SPERRE = "markt-auswertung"       # dieselbe Sperre wie markt.auswertung.SPERRE — nie zwei Auswertungen gleichzeitig
SPERRE_S = 3600
LIMIT_MAX = 1000
# Ersparnis (Pruefbefund F8): die "heutige Drosselung" kommt aus dem Merker des Tagesplans (market_config/tagesplan:
# Kontingent, Segmente, wartende). Er gilt nur, wenn er hoechstens 2 Tage vor dem Stichtag geschrieben wurde — ein
# alter Merker (Crawler aus, Testdaten) beschreibt nicht die heutige Lage; dann gilt die beobachtete Abrufrate.
ROTATION_FRISCH_TAGE = 2

FESTE_FELDER = ("typ", "schluessel", "model_id", "version", "segment_id", "source_segment_ids", "label", "ez_label", "km_label",
                "year_from", "min_km", "max_km")
DYNAMISCHE_FELDER = ("proposed_definition", "reason", "evidence", "confidence", "estimated_monthly_saving_usd", "health",
                     "activity_score", "laeufe_frei_monat", "ersparnis_budget_grenze", "wirkung_je_segment", "wirkung_betrag")
TYP_TEXT = {REDUCE: "Frequenz senken", PAUSE: "Pausieren (EMPTY)", HOT_PRIO: "HOT zuerst", MERGE: "Zusammenlegen", SPLIT: "Aufteilen"}


def familien_schluessel(typ: str, segment_id: str) -> str:
    """Pruefbefund F3/F7/F13: eine Familie je Segment und Art — ohne Ziel-Intervall."""
    return f"{typ}:{segment_id}"


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


ROTATION_UNBEKANNT: Dict[str, Any] = {"bekannt": False, "aktiv": False, "faktor": None}


def rotation_aus_merker(doc: Optional[Dict[str, Any]], stichtag: str) -> Dict[str, Any]:
    """Heutige Drosselung aus dem Tagesplan-Merker (der Tagesplan des Crawlers schreibt segmente_je_tag, segmente_gesamt, ruhend,
    wartend). aktiv = das Budget ist die Grenze (faellige Segmente warten). faktor = Anteil der faelligen Segmente,
    die an einem Tag geplant werden (1.0 ohne Rotation). Alter/fehlender Merker -> unbekannt."""
    doc = doc or {}
    tag = str(doc.get("tag") or "")[:10]
    if not tag or doc.get("segmente_je_tag") is None or not doc.get("segmente_gesamt"):
        return dict(ROTATION_UNBEKANNT)
    try:
        alter = (datetime.strptime(str(stichtag)[:10], "%Y-%m-%d") - datetime.strptime(tag, "%Y-%m-%d")).days
    except ValueError:
        return dict(ROTATION_UNBEKANNT)
    if alter < 0 or alter > ROTATION_FRISCH_TAGE:
        return dict(ROTATION_UNBEKANNT)
    aktiv = int(doc.get("wartend") or 0) > 0
    planbar = max(1, int(doc.get("segmente_gesamt") or 1) - int(doc.get("ruhend") or 0))
    faktor = min(1.0, int(doc.get("segmente_je_tag") or 0) / planbar) if aktiv else 1.0
    ruhend = int(doc.get("ruhend") or 0)
    return {"bekannt": True, "aktiv": aktiv, "faktor": round(faktor, 4), "tag": tag,
            "segmente_je_tag": int(doc.get("segmente_je_tag") or 0), "segmente_gesamt": int(doc.get("segmente_gesamt") or 0),
            "wartend": int(doc.get("wartend") or 0), "ruhend": ruhend,
            # Schlussrunde: Abrufe je Tag (des Auftrags) der ruhenden Segmente — Gewicht eines frei gebliebenen Platzes
            "ruhend_abrufe": int(doc["ruhend_abrufe"]) if doc.get("ruhend_abrufe") is not None else None,
            "geplant": int(doc["segmente"]) if doc.get("segmente") is not None else None}


def aktive_wirkung_deckeln(summe: Dict[str, Any], rotation: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    """Ersparnis durch SAFE_AUTO zur Lesezeit (Runde 2, Schlussrunde). Die gespeicherten Wirkungen wurden am Tag der
    Anwendung mit dem DAMALIGEN Rotationsfaktor gerechnet. Kontingent und 'geplant' des Tagesplans sind SEGMENT-Plaetze,
    die Summe sind LAEUFE — verglichen wird deshalb nur, was wirklich Plaetze betrifft:
      * weniger Abrufe je Tag (laeufe_takt, z. B. 2x -> 1x taeglich): das Segment wird weiter geplant und laeuft seltener
        je Tag — immer echte Laeufe und Dollar, nie gedeckelt
      * Intervall-Senkungen und Pausen (der Rest): ohne Budget-Rotation (Kontingent >= alle Segmente) ist jeder ruhende
        Tag ein gesparter Lauf — nicht gedeckelt. Unter Budget-Rotation (Kontingent < Segmente) und ohne wartende
        Segmente hat SAFE_AUTO selbst die Budgetgrenze aufgehoben: ohne die Wirkungen haette der Tagesplan 'Kontingent'
        Segmente geplant, mit ihnen 'geplant' — hoechstens (Kontingent - geplant) Plaetze je Tag sind frei geworden,
        jeder mit den Abrufen je Tag der ruhenden Segmente (ruhend_abrufe / ruhend; ohne Angabe 1)
    Dollar anteilig. Budgetgrenze aktiv: 0 $ (schon so). Ohne frischen Tagesplan-Merker: unveraendert."""
    rot = rotation or {}
    if not rot.get("bekannt") or rot.get("aktiv") or rot.get("geplant") is None:
        return summe
    laeufe = float(summe.get("laeufe") or 0)
    takt = min(laeufe, max(0.0, float(summe.get("laeufe_takt") or 0)))
    intervall = laeufe - takt
    je_tag, gesamt = int(rot.get("segmente_je_tag") or 0), int(rot.get("segmente_gesamt") or 0)
    if je_tag >= gesamt:
        return {**summe, "gedeckelt": False}               # keine Budget-Rotation: jeder ruhende Tag spart echt
    ruhend, abrufe = int(rot.get("ruhend") or 0), rot.get("ruhend_abrufe")
    gewicht = (float(abrufe) / ruhend) if (ruhend and abrufe) else 1.0
    frei_max = max(0, je_tag - int(rot["geplant"])) * gewicht * MONAT_TAGE
    if intervall <= frei_max + 1e-9:
        return {**summe, "gedeckelt": False}
    neu = takt + frei_max
    anteil = neu / laeufe if laeufe else 0.0
    return {**summe, "laeufe": round(neu, 1), "usd": round(float(summe.get("usd") or 0) * anteil, 2), "gedeckelt": True,
            "laeufe_ungedeckelt": round(laeufe, 1)}


async def rotation_lesen(db, stichtag: str) -> Dict[str, Any]:
    try:
        return rotation_aus_merker(await konfig.merker_lesen(db, konfig.TAGESPLAN_DOK), stichtag)
    except Exception:  # noqa: BLE001 — ohne Merker: beobachtete Rate
        return dict(ROTATION_UNBEKANNT)


def laeufe_heute(h: Dict[str, Any], rotation: Optional[Dict[str, Any]] = None, *, wirkung_aktiv: bool = False) -> float:
    """Tatsaechlich geplante Laeufe je Tag unter der heutigen Drosselung — OHNE SAFE_AUTO-Wirkung (Basis der
    Ersparnis): Abrufe des Auftrags x Rotationsfaktor. Ohne frischen Merker: beobachtete Abrufe je Tag (hoechstens die
    des Auftrags); laeuft schon eine Wirkung, die des Auftrags (sonst rechnete sie sich mit ihrer eigenen Wirkung klein)."""
    konf = float(h.get("laeufe_je_tag_konfig") or 1)
    rot = rotation or ROTATION_UNBEKANNT
    if rot.get("bekannt"):
        return konf * float(rot.get("faktor") or 1.0)
    beob = h.get("laeufe_je_tag")
    return konf if (wirkung_aktiv or beob is None) else min(konf, float(beob))


def wirkung_schaetzen(h: Dict[str, Any], rate_neu: float, rotation: Optional[Dict[str, Any]] = None, *,
                      wirkung_aktiv: bool = False, anteil: float = 1.0, ziel: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """Pruefbefund F8: frei werdende Laeufe je Monat = (geplant heute - geplant mit Vorschlag) x 30,4; Dollar nur,
    wenn das Budget NICHT die Grenze ist (sonst plant der Tagesplan andere Segmente in die frei werdenden Plaetze, die
    Rechnung bleibt gleich). usd_voll = Dollarwert ohne Budgetgrenze (fuer die Summen je Segment). anteil: MERGE
    verteilt die Wirkung eines weggefallenen Segments je zur Haelfte auf die beiden Bereiche.
    Schlussrunde: mit ziel ({intervall_tage, crawls_per_day} der Wirkung) getrennt nach Plaetzen und Abrufen — das
    Segment belegt heute p0 Plaetze je Tag (Rotationsfaktor, sonst die beobachtete Rate), mit der Wirkung hoechstens 1/n;
    je belegtem Platz laufen k statt der Abrufe des Auftrags. laeufe_takt = (Abrufe des Auftrags - k) x Plaetze mit
    Wirkung: weniger Abrufe je Tag sparen immer echte Laeufe (auch unter Rotation, nie gedeckelt, siehe
    aktive_wirkung_deckeln); der Rest (ruhende Plaetze x Abrufe des Auftrags) ist die Intervall-Wirkung."""
    rot = rotation or ROTATION_UNBEKANNT
    r0 = laeufe_heute(h, rot, wirkung_aktiv=wirkung_aktiv)
    takt = 0.0
    if ziel:
        konf = max(1.0, float(h.get("laeufe_je_tag_konfig") or 1))
        n = max(1, int(ziel.get("intervall_tage") or 1))
        k = min(konf, float(max(1, int(ziel.get("crawls_per_day") or 1))))
        p0 = r0 / konf
        p1 = min(p0, 1.0 / n)
        takt = (konf - k) * p1 * float(anteil)
        frei = (r0 - k * p1) * float(anteil)
    else:
        frei = (r0 - min(r0, max(0.0, float(rate_neu)))) * float(anteil)
    kj = h.get("kosten_je_lauf_usd")
    usd_voll = None if kj is None else float(kj) * frei * MONAT_TAGE
    return {"laeufe": frei * MONAT_TAGE, "laeufe_takt": takt * MONAT_TAGE, "usd_voll": usd_voll,
            "budget_grenze": bool(rot.get("aktiv")), "usd": 0.0 if rot.get("aktiv") else usd_voll}


def _wirkung_felder(teile: List[Tuple[str, Dict[str, Any]]], *, vorzeichen: float = 1.0) -> Dict[str, Any]:
    """Felder eines Vorschlags aus den Wirkungen je Segment: estimated_monthly_saving_usd (Dollar, 0 bei Budgetgrenze,
    None ohne Kostendaten), laeufe_frei_monat, ersparnis_budget_grenze, wirkung_je_segment (fuer die Summen ohne
    Doppelzaehlung), wirkung_betrag (Sortierung)."""
    je_seg = [{"segment_id": sid, "usd_voll": None if w["usd_voll"] is None else round(vorzeichen * w["usd_voll"], 4),
               "laeufe": round(vorzeichen * w["laeufe"], 2), "laeufe_takt": round(vorzeichen * float(w.get("laeufe_takt") or 0), 2)}
              for sid, w in teile]
    grenze = any(w["budget_grenze"] for _, w in teile)
    usd_teile = [w["usd"] for _, w in teile if w["usd"] is not None]
    laeufe = round(vorzeichen * sum(w["laeufe"] for _, w in teile), 1)
    if grenze:
        usd = 0.0
    else:
        usd = round(vorzeichen * sum(usd_teile), 2) if usd_teile else None
    return {"estimated_monthly_saving_usd": usd, "laeufe_frei_monat": laeufe, "ersparnis_budget_grenze": grenze,
            "wirkung_je_segment": je_seg, "wirkung_betrag": abs(laeufe)}


def ersparnis(h: Dict[str, Any], rate_neu: float, *, wirkung_aktiv: bool, rotation: Optional[Dict[str, Any]] = None) -> Optional[float]:
    """Geschaetzte Monatsersparnis in Dollar (siehe wirkung_schaetzen) — 0 bei Budgetgrenze, None ohne Kostendaten."""
    w = wirkung_schaetzen(h, rate_neu, rotation, wirkung_aktiv=wirkung_aktiv)
    return None if w["usd"] is None else round(w["usd"], 2)


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
def _min_laeufe_empty(h: Dict[str, Any], wirkung: Optional[Dict[str, Any]]) -> int:
    """Mindestzahl gueltiger Laeufe wie im Health-Dokument (unter einer Wirkung gegen die erwartbaren Laeufe)."""
    if h.get("min_laeufe_empty"):
        return int(h["min_laeufe_empty"])
    return health.min_laeufe(health.MIN_LAEUFE_EMPTY, wirkung)


def vorschlaege_segment(seg: Dict[str, Any], h: Dict[str, Any], rotation: Optional[Dict[str, Any]] = None) -> List[Dict[str, Any]]:
    """REDUCE_FREQUENCY / PAUSE_EMPTY / PRIORITIZE_HOT eines Segments aus seinem Health-Dokument. Schluessel =
    Familie (Typ + Segment, ohne Intervall); das Ziel steht in proposed_definition und darf sich aendern."""
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
        raus.append({**basis, "typ": PAUSE, "schluessel": familien_schluessel(PAUSE, seg["id"]),
                     "proposed_definition": {"pausiert": True, "nachpruefung_tage": n, "intervall_tage": n, "crawls_per_day": 1},
                     "reason": (f"{ort}: {h.get('valid_runs')} gültige Läufe, davon {_pct(h.get('empty_rate'))} ohne Treffer (EMPTY) — "
                                f"pausieren mit Nachprüfung alle {n} Tage; nichts wird gelöscht, die Historie bleibt"),
                     **_wirkung_felder([(seg["id"], wirkung_schaetzen(h, 1.0 / n, rotation, wirkung_aktiv=aktiv,
                                                                       ziel={"intervall_tage": n, "crawls_per_day": 1}))])})
    elif status in (health.HOT, health.HEALTHY, health.NORMAL, health.THIN) and emp and not emp.get("pausiert"):
        r_neu = health.rate(emp) or 0.0
        if r_neu < health.rate(konf) - 1e-9 and int(h.get("valid_runs") or 0) >= _min_laeufe_empty(h, wirkung):
            n, k = int(emp["intervall_tage"]), int(emp["crawls_per_day"])
            hyst = " (Hysterese: Score liegt nahe der Stufengrenze)" if emp.get("hysterese") else ""
            raus.append({**basis, "typ": REDUCE, "schluessel": familien_schluessel(REDUCE, seg["id"]),
                         "proposed_definition": {"intervall_tage": n, "crawls_per_day": k, "frequency_days": emp.get("frequency_days"),
                                                 "vorher": frequenz_text(konf), "hysterese": bool(emp.get("hysterese"))},
                         "reason": (f"{ort}: Activity Score {h.get('activity_score')} ({status}) — {frequenz_text(emp)} statt "
                                    f"{frequenz_text(konf)} reicht{hyst}"),
                         **_wirkung_felder([(seg["id"], wirkung_schaetzen(h, r_neu, rotation, wirkung_aktiv=aktiv,
                                                                           ziel={"intervall_tage": n, "crawls_per_day": k}))])})
    if status == health.HOT:
        raus.append({**basis, "typ": HOT_PRIO, "schluessel": familien_schluessel(HOT_PRIO, seg["id"]),
                     "proposed_definition": {"prioritaet": "HOT"},
                     "reason": f"{ort}: sehr aktiver Markt (Score {h.get('activity_score')}) — im Tagesplan zuerst planen, keine zusätzlichen Abrufe",
                     "estimated_monthly_saving_usd": 0.0, "laeufe_frei_monat": 0.0, "ersparnis_budget_grenze": bool((rotation or {}).get("aktiv")),
                     "wirkung_je_segment": [{"segment_id": seg["id"], "usd_voll": 0.0, "laeufe": 0.0}], "wirkung_betrag": 0.0})
    return raus


def _km_liste(modell: Dict[str, Any], segs: List[Dict[str, Any]]) -> List[Tuple[int, int]]:
    """Die km-Bereiche des Auftrags (eigene, sonst die der aktuellen Segmente) aufsteigend."""
    eigene = modell.get("km_buckets")
    if isinstance(eigene, list) and eigene:
        return sorted((int(b["min_km"]), int(b["max_km"])) for b in eigene)
    return sorted({(int(s["min_km"]), int(s["max_km"])) for s in segs if s.get("min_km") is not None})


def merge_zustand(seg: Optional[Dict[str, Any]], h: Optional[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    """Pruefbefund F4: Zustand eines km-Bereichs fuer die MERGE-Bedingung. Ein von SAFE_AUTO PAUSIERTES Segment zaehlt
    mit seinem letzten bekannten Zustand EMPTY (die Pause gilt per Bestandsschutz, solange keine Treffer sie
    widerlegen) — seine wenigen Nachpruefungs-Laeufe liessen es sonst als UNKNOWN/STALE aus der Bedingung fallen und den
    Vorschlag verschwinden. Mindestlaufzahl wie im Health-Dokument (unter einer Wirkung gegen die erwartbaren Laeufe)."""
    if not seg or not h:
        return None
    w = seg.get("safe_auto") if isinstance(seg.get("safe_auto"), dict) else None
    st = h.get("health")
    if w and w.get("pausiert") and st not in health.MIT_EMPFEHLUNG:
        return {"health": health.EMPTY, "laeufe_ok": True, "avg": float(h.get("avg_valid_rows") or 0.0),
                "confidence": h.get("confidence") if h.get("confidence") in ("MEDIUM", "HIGH") else "MEDIUM", "letzter_bekannter": True}
    return {"health": st, "laeufe_ok": int(h.get("valid_runs") or 0) >= _min_laeufe_empty(h, w) if w else int(h.get("valid_runs") or 0) >= MERGE_MIN_LAEUFE,
            "avg": float(h.get("avg_valid_rows") or 0.0), "confidence": h.get("confidence"), "letzter_bekannter": False}


def vorschlaege_modell(modell: Dict[str, Any], segs: List[Dict[str, Any]], healths: Dict[str, Dict[str, Any]],
                       rotation: Optional[Dict[str, Any]] = None) -> List[Dict[str, Any]]:
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
            za, zb = merge_zustand(sa, ha), merge_zustand(sb, hb)
            if not za or not zb:
                ok = False
                break
            if za["health"] not in (health.THIN, health.EMPTY) or zb["health"] not in (health.THIN, health.EMPTY):
                ok = False
                break
            if not za["laeufe_ok"] or not zb["laeufe_ok"]:
                ok = False
                break
            if za["avg"] + zb["avg"] > rows:
                ok = False
                break
            je_ez.append((j, sa, sb, {**ha, "health": za["health"], "confidence": za["confidence"]},
                          {**hb, "health": zb["health"], "confidence": zb["confidence"]}))
        if not ok or not je_ez:
            continue
        neu = [b for b in km_vorher if (b["min_km"], b["max_km"]) not in ((a0, a1), (b0, b1))] + [{"min_km": a0, "max_km": b1}]
        neu.sort(key=lambda b: b["min_km"])
        # je EZ-Jahr faellt ein Segment weg: Wirkung = Mittel der beiden Bereiche, je zur Haelfte auf beide verteilt
        teile = []
        for _, sa, sb, ha, hb in je_ez:
            teile.append((sa["id"], wirkung_schaetzen(ha, 0.0, rotation, wirkung_aktiv=bool(sa.get("safe_auto")), anteil=0.5)))
            teile.append((sb["id"], wirkung_schaetzen(hb, 0.0, rotation, wirkung_aktiv=bool(sb.get("safe_auto")), anteil=0.5)))
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
                     **_wirkung_felder(teile)})
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
        # je EZ-Jahr ein Segment mehr: Mehrbedarf = die Laeufe/Kosten des geteilten Bereichs (negativ)
        teile = [(s["id"], wirkung_schaetzen(h, 0.0, rotation, wirkung_aktiv=bool(s.get("safe_auto")))) for _, s, h in je_ez]
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
                     **_wirkung_felder(teile, vorzeichen=-1.0)})
    return raus


# ---------------------------------------------------------------- Einstellungen / Modus
async def einstellungen(db) -> Dict[str, Any]:
    """Modus + Frequenz-Zuordnung (market_config/optimierung). Unlesbar oder unbekannt -> OBSERVE + Standard."""
    doc = await konfig.merker_lesen(db, konfig.OPTIMIERUNG_DOK)
    modus = doc.get("modus") if doc.get("modus") in (OBSERVE, SAFE_AUTO) else OBSERVE
    try:
        # Schlussrunde: gespeicherte Werte ueber den Obergrenzen (vor der Begrenzung gespeichert) beim Lesen begrenzt
        frequenz = health.frequenz_begrenzen(doc["frequenz"]) if doc.get("frequenz") else copy.deepcopy(health.FREQUENZ_STANDARD)
    except health.Ungueltig:
        frequenz = copy.deepcopy(health.FREQUENZ_STANDARD)
    begrenzt = False
    if doc.get("frequenz"):
        try:
            health.frequenz_pruefen(doc["frequenz"])
        except health.Ungueltig:
            begrenzt = True             # gespeicherter Wert lag ueber einer Obergrenze (oder war ungueltig)
    return {"modus": modus, "frequenz": frequenz, "frequenz_standard": copy.deepcopy(health.FREQUENZ_STANDARD),
            "frequenz_grenzen": {"intervall_max_tage": health.FREQUENZ_MAX_INTERVALL_TAGE,
                                 "nachpruefung_max_tage": health.NACHPRUEFUNG_MAX_TAGE},
            "frequenz_begrenzt": begrenzt,
            "modus_seit": doc.get("modus_seit"), "modus_von": doc.get("modus_von"), "frequenz_seit": doc.get("frequenz_seit"),
            "frequenz_von": doc.get("frequenz_von"), "modus_verlauf": list(doc.get("modus_verlauf") or [])[-10:]}


async def modus_setzen(db, modus: str, *, wer: str = "") -> Dict[str, Any]:
    """Nur OBSERVE/SAFE_AUTO; FULL_AUTO ist gesperrt. Zurueck auf OBSERVE hebt sofort alle SAFE_AUTO-Wirkungen auf
    (der Tagesplan liest zusaetzlich den Modus); SAFE_AUTO wendet die vorhandenen Vorschlaege sofort an."""
    neu = str(modus or "").strip().upper()
    if neu in MODI_GESPERRT:
        raise Ungueltig("FULL_AUTO ist gesperrt — km-Bereiche zusammenlegen/aufteilen nur als Vorschlag mit Übernahme durch den Super-Admin")
    if neu not in (OBSERVE, SAFE_AUTO):
        raise Ungueltig(f"Unbekannter Modus — erlaubt: {OBSERVE}, {SAFE_AUTO}")
    alt = (await einstellungen(db))["modus"]
    jetzt_iso = konfig.jetzt_iso()
    await db[KONFIG].update_one({"_id": konfig.OPTIMIERUNG_DOK},
                                {"$set": {"modus": neu, "modus_seit": jetzt_iso, "modus_von": str(wer or ""), "updated_at": jetzt_iso},
                                 "$push": {"modus_verlauf": {"$each": [{"alt": alt, "neu": neu, "at": jetzt_iso, "von": str(wer or "")}],
                                                             "$slice": -50}}}, upsert=True)
    erg: Dict[str, Any] = {"modus": neu, "vorher": alt}
    if neu == OBSERVE:
        erg["aufgehoben"] = await alle_aufheben(db, grund="Modus OBSERVE", wer=str(wer or ""))
    else:
        erg["safe_auto"] = await safe_auto_anwenden(db, tag=konfig.heute_tag(), jetzt_iso=jetzt_iso)
    await zaehler_nachrechnen(db)          # Pruefbefund F14: Zaehler je Suchauftrag sofort, nicht erst am naechsten Tag
    return erg


async def frequenz_setzen(db, roh: Dict[str, Any], *, wer: str = "") -> Dict[str, Any]:
    cfg = health.frequenz_pruefen(roh)
    jetzt_iso = konfig.jetzt_iso()
    await db[KONFIG].update_one({"_id": konfig.OPTIMIERUNG_DOK},
                                {"$set": {"frequenz": cfg, "frequenz_seit": jetzt_iso, "frequenz_von": str(wer or ""), "updated_at": jetzt_iso}},
                                upsert=True)
    return cfg


# ---------------------------------------------------------------- Vorschlaege speichern (idempotent)
def bereich_schluessel(d: Optional[Dict[str, Any]]) -> Optional[str]:
    """Runde 2: derselbe Bereich ueber Fassungen hinweg — Suchauftrag + EZ-Jahr + km-Grenzen. Ab Fassung 2 traegt jede
    Segment-ID die Fassung ('<mid>:v2:<ez>:<km>'), auch fuer Bereiche, die ein MERGE/SPLIT gar nicht beruehrt; eine
    Ablehnung/Ruhezeit haengt deshalb zusaetzlich an diesem Schluessel (Segment oder Vorschlag, gleiche Felder)."""
    d = d or {}
    if not d.get("model_id") or (d.get("min_km") is None and d.get("max_km") is None and d.get("year_from") is None):
        return None
    return f"{d['model_id']}|{d.get('year_from')}|{d.get('min_km')}|{d.get('max_km')}"


async def abgelehnte_familien(db, segmente: List[Dict[str, Any]]) -> Dict[Tuple[str, str], Dict[str, Any]]:
    """Pruefbefund F3/F7/F13 (Lese-Kompatibilitaet): abgelehnte SAFE-Familien (segment_id, typ) — egal ob unter dem
    Familien-Schluessel oder einem alten Schluessel mit Intervall (REDUCE_FREQUENCY:<seg>:<n>t:<k>x) abgelehnt, und
    (Runde 2) auch, wenn dieselbe Familie fuer denselben Bereich in einer frueheren Fassung abgelehnt wurde.
    segmente: Segmente ('id') oder Vorschlaege ('segment_id') mit model_id, year_from, min_km, max_km."""
    segs = [s for s in segmente or [] if s and (s.get("segment_id") or s.get("id"))]
    if not segs:
        return {}
    ids = sorted({str(s.get("segment_id") or s.get("id")) for s in segs})
    mids = sorted({str(s["model_id"]) for s in segs if s.get("model_id")})
    oder: List[Dict[str, Any]] = [{"segment_id": {"$in": ids}}] + ([{"model_id": {"$in": mids}}] if mids else [])
    je_seg: Dict[Tuple[str, str], Dict[str, Any]] = {}
    je_bereich: Dict[Tuple[str, str], Dict[str, Any]] = {}
    async for d in db[VORSCHLAEGE].find({"typ": {"$in": list(SAFE_TYPEN)}, "status": REJECTED, "$or": oder},
                                        {"_id": 0, "id": 1, "segment_id": 1, "typ": 1, "schluessel": 1, "entschieden_von": 1,
                                         "entschieden_at": 1, "model_id": 1, "year_from": 1, "min_km": 1, "max_km": 1, "version": 1}):
        if d.get("segment_id"):
            je_seg.setdefault((d["segment_id"], d["typ"]), d)
        b = bereich_schluessel(d)
        if b:
            je_bereich.setdefault((b, d["typ"]), d)
    raus: Dict[Tuple[str, str], Dict[str, Any]] = {}
    for s in segs:
        sid, b = str(s.get("segment_id") or s.get("id")), bereich_schluessel(s)
        for typ in SAFE_TYPEN:
            d = je_seg.get((sid, typ)) or (je_bereich.get((b, typ)) if b else None)
            if d:
                raus[(sid, typ)] = d
    return raus


async def sperren_je_segment(db, segs: List[Dict[str, Any]], tag: str) -> Dict[str, str]:
    """Runde 2: Ruhezeit nach einer Ruecknahme (safe_auto_sperre_bis) je Segment — auch aus einer frueheren Fassung
    desselben Bereichs (sonst endete die 30-Tage-Ruhe mit jedem Fassungswechsel). Gebuendelt, Index markt_segment_modell."""
    mids = sorted({str(s["model_id"]) for s in segs if s.get("model_id")})
    raus = {s["id"]: str(s.get("safe_auto_sperre_bis") or "") for s in segs if str(s.get("safe_auto_sperre_bis") or "") >= tag}
    if not mids:
        return raus
    je_bereich: Dict[str, str] = {}
    async for s in db[SEGMENTE].find({"model_id": {"$in": mids}, "safe_auto_sperre_bis": {"$gte": tag}},
                                     {"_id": 0, "id": 1, "model_id": 1, "year_from": 1, "min_km": 1, "max_km": 1, "safe_auto_sperre_bis": 1}):
        b = bereich_schluessel(s)
        if b:
            je_bereich[b] = max(je_bereich.get(b, ""), str(s["safe_auto_sperre_bis"]))
    for s in segs:
        b = bereich_schluessel(s)
        if b and je_bereich.get(b):
            raus[s["id"]] = max(raus.get(s["id"], ""), je_bereich[b])
    return raus


async def _vorschlaege_speichern(db, liste: List[Dict[str, Any]], *, lauf_id: str, tag: str, jetzt_iso: str) -> Dict[str, int]:
    z = {"neu": 0, "fortgeschrieben": 0, "wieder_offen": 0}
    abgelehnt = await abgelehnte_familien(db, [v for v in liste if v.get("typ") in SAFE_TYPEN])
    for v in liste:
        schl = {"schluessel": v["schluessel"]}
        fest = {k: v.get(k) for k in FESTE_FELDER}
        dyn = {k: v.get(k) for k in DYNAMISCHE_FELDER}
        lauf = {"last_seen_tag": tag, "last_seen_at": jetzt_iso, "lauf_id": lauf_id}
        fam = abgelehnt.get((v.get("segment_id"), v.get("typ"))) if v.get("typ") in SAFE_TYPEN else None
        start = {"status": PROPOSED}
        if fam:
            # die Familie wurde (auch unter einem alten Schluessel) abgelehnt: der neue Familien-Vorschlag beginnt abgelehnt
            start = {"status": REJECTED, "entschieden_von": fam.get("entschieden_von"), "entschieden_at": fam.get("entschieden_at"),
                     "entscheidung_grund": f"Familie abgelehnt ({fam.get('schluessel')})"}
        try:
            r = await db[VORSCHLAEGE].update_one(schl, {"$setOnInsert": {"id": uuid.uuid4().hex, "created_at": jetzt_iso,
                                                                         "first_seen_tag": tag, **start, **fest}, "$set": lauf}, upsert=True)
            neu = r.upserted_id is not None
        except DuplicateKeyError:          # der andere Server legte ihn eben an — fortschreiben
            await db[VORSCHLAEGE].update_one(schl, {"$set": lauf})
            neu = False
        z["neu" if neu else "fortgeschrieben"] += 1
        # Evidenz/Ziel an offenen und angewendeten fortschreiben (das Ziel-Intervall ist ein Feld der Familie); ein
        # abgelehnter bleibt, wie er entschieden wurde
        await db[VORSCHLAEGE].update_one({**schl, "status": {"$in": [PROPOSED, ACCEPTED, APPLIED]}}, {"$set": {**dyn, "updated_at": jetzt_iso}})
        r2 = await db[VORSCHLAEGE].update_one({**schl, "status": OBSOLETE},
                                              {"$set": {**dyn, "status": REJECTED if fam else PROPOSED, "wieder_offen_at": jetzt_iso,
                                                        "updated_at": jetzt_iso},
                                               "$unset": {"obsolet_at": "", "obsolet_grund": ""}})
        z["wieder_offen"] += 0 if fam else int(r2.modified_count)
    return z


async def _ueberholte_markieren(db, *, lauf_id: str, jetzt_iso: str, grund: str, model_ids: Optional[List[str]] = None,
                                ausser_model_ids: Optional[List[str]] = None) -> int:
    """OFFENE Vorschlaege, die dieser Lauf nicht mehr erzeugt hat, werden OBSOLETE (nie geloescht). Pruefbefund F0/F5:
    von SAFE_AUTO angewendete bleiben APPLIED — ob ihre Wirkung endet, entscheidet safe_auto_anwenden nur nach den
    Daten (Bestandsschutz: fehlende Daten/UNKNOWN heben nichts auf). Uebernommene MERGE/SPLIT und abgelehnte bleiben."""
    filt: Dict[str, Any] = {"lauf_id": {"$ne": lauf_id}, "status": {"$in": list(OFFEN)}}
    if model_ids is not None:
        filt["model_id"] = {"$in": list(model_ids)}
    elif ausser_model_ids is not None:
        filt["model_id"] = {"$nin": list(ausser_model_ids)}
    r = await db[VORSCHLAEGE].update_many(filt, {"$set": {"status": OBSOLETE, "obsolet_at": jetzt_iso, "obsolet_grund": grund,
                                                          "updated_at": jetzt_iso}})
    return int(r.modified_count)


# ---------------------------------------------------------------- SAFE_AUTO (Phase G)
# Status mit echter Datengrundlage: nur sie koennen einer Wirkung WIDERSPRECHEN (Pruefbefund F0/F5, Bestandsschutz).
# UNKNOWN (zu wenig Laeufe), STALE (letzter Lauf zu alt) und UNSTABLE (Technik) sind fehlende/unsichere Daten.
DATEN_STATUS = (health.HOT, health.HEALTHY, health.NORMAL, health.THIN, health.EMPTY)


def _reduziert_seit(a: Dict[str, Any]) -> str:
    return str(a.get("reduziert_seit") or a.get("tag") or str(a.get("at") or "")[:10])


async def _wirkung_neu(db, seg_id: str) -> Optional[Dict[str, Any]]:
    """Das Feld 'safe_auto' am Segment aus den AKTIVEN Aenderungen neu bilden (Quelle der Wahrheit ist das Protokoll).
    Ohne aktive Aenderung wird es entfernt — der Tagesplan plant das Segment dann wie vom Auftrag vorgesehen.
    reduziert_seit = Beginn der laufenden Reduktionskette (fuer die erwartbaren Laeufe, markt.health)."""
    aktive = await db[AENDERUNGEN].find({"segment_id": seg_id, "status": AKTIV}, {"_id": 0}).to_list(20)
    if not aktive:
        await db[SEGMENTE].update_one({"id": seg_id}, {"$unset": {"safe_auto": ""}})
        return None
    w: Dict[str, Any] = {"intervall_tage": 1, "crawls_per_day": None, "hot": False, "pausiert": False,
                         "aenderung_ids": [], "seit": min(str(a.get("at") or "") for a in aktive), "reduziert_seit": None}
    for a in aktive:
        neu = a.get("neu") or {}
        if neu.get("intervall_tage"):
            w["intervall_tage"] = max(int(w["intervall_tage"]), int(neu["intervall_tage"]))
        if neu.get("crawls_per_day"):
            w["crawls_per_day"] = min(int(w["crawls_per_day"] or 99), int(neu["crawls_per_day"]))
        if neu.get("prioritaet") == "HOT":
            w["hot"] = True
        if neu.get("pausiert"):
            w["pausiert"] = True
            # Schlussrunde: bis wann die Laeufe unter der Pause geprueft sind (markt.health: neuer Treffer-Lauf?)
            w["pause_geprueft_bis"] = pause_geprueft_bis(a)
        if a.get("typ") in (REDUCE, PAUSE):
            rs = _reduziert_seit(a)
            w["reduziert_seit"] = min(w["reduziert_seit"] or rs, rs)
        w["aenderung_ids"].append(a["id"])
    await db[SEGMENTE].update_one({"id": seg_id}, {"$set": {"safe_auto": w}})
    return w


async def _beenden(db, a: Dict[str, Any], *, status: str, grund: str, wer: str, jetzt_iso: str) -> bool:
    r = await db[AENDERUNGEN].update_one({"id": a["id"], "status": AKTIV},
                                         {"$set": {"status": status, "beendet_at": jetzt_iso, "beendet_von": wer, "beendet_grund": grund}})
    return r.modified_count == 1


def _neu_aus_vorschlag(v: Dict[str, Any]) -> Dict[str, Any]:
    pd = v.get("proposed_definition") or {}
    if v["typ"] == REDUCE:
        return {"intervall_tage": int(pd.get("intervall_tage") or 1), "crawls_per_day": int(pd.get("crawls_per_day") or 1)}
    if v["typ"] == PAUSE:
        return {"intervall_tage": int(pd.get("nachpruefung_tage") or pd.get("intervall_tage") or 7), "crawls_per_day": 1, "pausiert": True}
    return {"prioritaet": "HOT"}


def _alt_von(seg: Dict[str, Any], typ: Optional[str] = None) -> Dict[str, Any]:
    """Stand VOR einer Aenderung — nur die Groesse, die diese Aenderung betrifft (Pruefbefund F17: eine Frequenzsenkung
    zeigt als 'alt' die Frequenz, nie die HOT-Prioritaet; HOT zeigt nur die Prioritaet)."""
    w = seg.get("safe_auto") if isinstance(seg.get("safe_auto"), dict) else {}
    if typ == HOT_PRIO:
        return {"prioritaet": "HOT" if w.get("hot") else "normal"}
    cpd = int(seg.get("crawls_per_day") or 1)
    return {"intervall_tage": int(w.get("intervall_tage") or 1), "crawls_per_day": min(cpd, int(w.get("crawls_per_day") or cpd)),
            "pausiert": bool(w.get("pausiert"))}


def aenderung_tag(a: Dict[str, Any]) -> str:
    """Tag, an dem eine Aenderung angewendet wurde (Altbestand ohne 'tag': Datum aus 'at')."""
    return str(a.get("tag") or str(a.get("at") or "")[:10])


def verweil_bis(a: Dict[str, Any]) -> str:
    """Schlussrunde (Mindestverweildauer): erster Tag, an dem die Wirkung geaendert werden darf."""
    t = aenderung_tag(a)
    return _tag_plus(t, MIN_VERWEIL_TAGE) if t else ""


def verweil_haelt(a: Optional[Dict[str, Any]], tag: str) -> bool:
    return bool(a) and bool(verweil_bis(a)) and str(tag) < verweil_bis(a)


def pause_geprueft_bis(a: Dict[str, Any]) -> str:
    """Bis zu welchem Tag die Laeufe unter einer Pause schon geprueft sind (Beginn: Tag der Anwendung)."""
    return str(a.get("geprueft_bis") or aenderung_tag(a))[:10]


def neuer_treffer_lauf(a: Optional[Dict[str, Any]], h: Optional[Dict[str, Any]]) -> bool:
    """Schlussrunde: hatte ein NEUER gueltiger Lauf (nach der letzten Pruefung der Pause) Treffer?"""
    if not a or not h:
        return False
    lt = str(h.get("letzter_treffer_tag") or "")[:10]
    return bool(lt) and lt > pause_geprueft_bis(a)


def widerspruch(typ: str, h: Optional[Dict[str, Any]], seg: Optional[Dict[str, Any]] = None,
                a: Optional[Dict[str, Any]] = None) -> Optional[str]:
    """Pruefbefund F0/F5 (Bestandsschutz): widersprechen die Daten einer aktiven Wirkung? Grund oder None.
    Nur Status mit Datengrundlage zaehlen; UNKNOWN/STALE/UNSTABLE, fehlende Daten oder eine andere Fassung nie.
      PAUSE_EMPTY       das Segment ist nicht mehr EMPTY (wieder Treffer: >= 20 % der gueltigen Laeufe mit Autos) UND
                        (Schlussrunde, mit der aktiven Aenderung a) ein NEUER gueltiger Lauf seit der letzten Pruefung
                        der Pause hatte Treffer — nie allein, weil ein alter leerer Lauf aus dem Fenster faellt
      REDUCE_FREQUENCY  die Empfehlung (mit Hysterese an der Grenze) verlangt wieder die Frequenz des Auftrags
      PRIORITIZE_HOT    nicht mehr HOT und der Score liegt klar unter der HOT-Stufe (75 - 5 Punkte) oder THIN/EMPTY
    Alle drei sind das genaue Gegenteil der Bedingung, unter der SAFE_AUTO die Wirkung anwendet — kein Wert erfuellt
    beides, also kann eine Wirkung ohne Aenderung der Daten nicht pendeln."""
    if not h:
        return None
    st = h.get("health")
    if st not in DATEN_STATUS:
        return None
    if seg and (int(h.get("version") or 1) != int(seg.get("version") or 1) or h.get("segment_id") not in (None, seg.get("id"))):
        return None
    score = h.get("activity_score")
    if typ == PAUSE:
        if st != health.EMPTY:
            if a is not None and not neuer_treffer_lauf(a, h):
                return None                  # kein neuer Lauf mit Treffern seit der letzten Pruefung: die Pause haelt
            return (f"Daten widersprechen: {st} statt EMPTY — wieder Treffer ({_pct(h.get('empty_rate'))} der Läufe leer, "
                    f"Ø {h.get('avg_valid_rows')} Autos, neuer Lauf mit Treffern am {h.get('letzter_treffer_tag') or '—'})")
        return None
    if typ == REDUCE:
        emp = h.get("empfehlung") or {}
        if st == health.EMPTY or not emp or emp.get("pausiert"):
            return None                      # EMPTY: noch seltener waere richtig (Familie PAUSE) — kein Widerspruch
        cpd = int(h.get("crawls_per_day") or (seg or {}).get("crawls_per_day") or 1)
        if (health.rate(emp) or 0.0) >= cpd - 1e-9:
            return f"Daten widersprechen: Activity Score {score} ({st}) — {frequenz_text(emp)} nötig"
        return None
    if typ == HOT_PRIO:
        if st in (health.EMPTY, health.THIN) or (
                st != health.HOT and (score is None or int(score) < health.HOT_AB_SCORE - health.HYSTERESE_PUNKTE)):
            return f"Daten widersprechen: nicht mehr HOT (Activity Score {score}, {st})"
    return None


async def _vorschlag_obsolet(db, vorschlag_id: Optional[str], grund: str, jetzt_iso: str) -> None:
    if vorschlag_id:
        await db[VORSCHLAEGE].update_one({"id": vorschlag_id, "typ": {"$in": list(SAFE_TYPEN)}, "status": {"$in": [PROPOSED, ACCEPTED, APPLIED]}},
                                         {"$set": {"status": OBSOLETE, "obsolet_at": jetzt_iso, "obsolet_grund": grund, "updated_at": jetzt_iso},
                                          "$unset": {"angewendet_von": "", "aenderung_id": ""}})


async def _vorschlag_angewendet(db, vorschlag_id: str, aenderung_id: str, jetzt_iso: str) -> bool:
    """Vorschlag als angewendet beanspruchen — atomar nur, solange er offen oder angewendet ist. False: er wurde
    inzwischen abgelehnt/ueberholt (Ablehnen waehrend des laufenden SAFE_AUTO, zweiter Server)."""
    r = await db[VORSCHLAEGE].update_one({"id": vorschlag_id, "status": {"$in": [PROPOSED, ACCEPTED, APPLIED]}},
                                         {"$set": {"status": APPLIED, "angewendet_von": WER_AUTO, "angewendet_at": jetzt_iso,
                                                   "aenderung_id": aenderung_id, "updated_at": jetzt_iso}})
    return r.matched_count == 1


async def _anwenden(db, v: Dict[str, Any], seg: Dict[str, Any], *, tag: str, jetzt_iso: str,
                   kette_start: Optional[str] = None) -> Optional[str]:
    """Einen SAFE-Vorschlag als Aenderung eintragen (Protokoll) — REDUCE und PAUSE schliessen sich aus. Rennfest ueber
    den Teil-Unique-Index (segment_id, typ) der aktiven Aenderungen. Ergebnis: 'neu', 'aktualisiert' (Ziel der
    Familie geaendert: alte Aenderung beendet 'Empfehlung geaendert: alt -> neu', neue eingetragen), 'verknuepft'
    (dieselbe Wirkung besteht schon, z. B. unter einem alten Schluessel — kein neuer Protokolleintrag) oder None."""
    typ = v["typ"]
    neu = _neu_aus_vorschlag(v)
    gegenteil = {REDUCE: PAUSE, PAUSE: REDUCE}.get(typ)
    vorher = await db[SEGMENTE].find_one({"id": seg["id"]}, {"_id": 0}) or seg
    alt = _alt_von(vorher, typ)
    kette: Optional[str] = kette_start if typ in (REDUCE, PAUSE) else None
    ersetzt = False
    async for a in db[AENDERUNGEN].find({"segment_id": seg["id"], "status": AKTIV}, {"_id": 0}):
        if a.get("typ") == gegenteil:
            grund = f"ersetzt durch {TYP_TEXT[typ]}"
            if await _beenden(db, a, status=AUFGEHOBEN, grund=grund, wer=WER_AUTO, jetzt_iso=jetzt_iso):
                kette = min(kette or _reduziert_seit(a), _reduziert_seit(a))
                await _vorschlag_obsolet(db, a.get("vorschlag_id"), grund, jetzt_iso)
        elif a.get("typ") == typ:
            if (a.get("neu") or {}) == neu:
                if a.get("vorschlag_id") != v["id"]:
                    await db[AENDERUNGEN].update_one({"id": a["id"], "status": AKTIV},
                                                     {"$set": {"vorschlag_id": v["id"], "schluessel": v["schluessel"]}})
                    await _vorschlag_obsolet(db, a.get("vorschlag_id"), "abgelöst durch den Familien-Schlüssel", jetzt_iso)
                await _vorschlag_angewendet(db, v["id"], a["id"], jetzt_iso)
                return "verknuepft"
            grund = f"Empfehlung geändert: {frequenz_text(a.get('neu'))} → {frequenz_text(neu)}"
            if await _beenden(db, a, status=AUFGEHOBEN, grund=grund, wer=WER_AUTO, jetzt_iso=jetzt_iso):
                kette = min(kette or _reduziert_seit(a), _reduziert_seit(a))
                ersetzt = True
                if a.get("vorschlag_id") != v["id"]:
                    await _vorschlag_obsolet(db, a.get("vorschlag_id"), grund, jetzt_iso)
    await _wirkung_neu(db, seg["id"])
    doc = {"id": uuid.uuid4().hex, "segment_id": seg["id"], "model_id": seg.get("model_id"), "version": int(seg.get("version") or 1),
           "typ": typ, "vorschlag_id": v["id"], "schluessel": v["schluessel"], "wer": WER_AUTO, "alt": alt, "neu": neu,
           "grund": v.get("reason"), "health": v.get("health"), "activity_score": v.get("activity_score"),
           "confidence": v.get("confidence"), "estimated_monthly_saving_usd": v.get("estimated_monthly_saving_usd"),
           "laeufe_frei_monat": v.get("laeufe_frei_monat"), "ersparnis_budget_grenze": v.get("ersparnis_budget_grenze"),
           "wirkung_je_segment": v.get("wirkung_je_segment"),
           "reduziert_seit": (kette or tag) if typ in (REDUCE, PAUSE) else None,
           "label": seg.get("label"), "ez_label": seg.get("ez_label"), "km_label": seg.get("km_label"),
           "status": AKTIV, "at": jetzt_iso, "tag": tag,
           # Schlussrunde: Mindestverweildauer (fruehestens an diesem Tag aenderbar) und, bei einer Pause, bis wann ihre
           # Laeufe geprueft sind (Beginn = Tag der Anwendung; ein neuer Treffer-Lauf muss danach liegen)
           "haelt_bis": _tag_plus(tag, MIN_VERWEIL_TAGE), **({"geprueft_bis": tag} if typ == PAUSE else {})}
    try:
        await db[AENDERUNGEN].insert_one(dict(doc))
    except DuplicateKeyError:
        return None                      # ein anderer Lauf hat dieselbe Wirkung eben eingetragen
    if not await _vorschlag_angewendet(db, v["id"], doc["id"], jetzt_iso):
        # Runde 2 (Rennen Ablehnen <-> laufendes SAFE_AUTO): der Vorschlag wurde nach dem Lesen der Kandidaten abgelehnt.
        # Der Status des Vorschlags wird NACH dem Eintragen geprueft — wer zuerst schreibt, egal: entweder beendet das
        # Ablehnen (_familie_ablehnen) die schon eingetragene Aenderung, oder diese Pruefung sieht REJECTED und beendet sie.
        await _beenden(db, doc, status=AUFGEHOBEN, grund="Vorschlag inzwischen abgelehnt", wer=WER_AUTO, jetzt_iso=jetzt_iso)
        await _wirkung_neu(db, seg["id"])
        return None
    await _wirkung_neu(db, seg["id"])
    return "aktualisiert" if ersetzt else "neu"


async def safe_auto_anwenden(db, *, tag: Optional[str] = None, jetzt_iso: Optional[str] = None,
                             model_ids: Optional[List[str]] = None) -> Dict[str, Any]:
    """SAFE_AUTO: (1) aktive Wirkungen pruefen — aufheben nur, wenn der Modus nicht SAFE_AUTO ist, das Segment inaktiv
    ist, die Familie abgelehnt wurde oder die DATEN widersprechen (widerspruch); sonst bleiben sie (Bestandsschutz,
    Pruefbefund F0/F5: fehlende Daten, UNKNOWN oder LOW heben nichts auf); (2) offene SAFE-Vorschlaege (ab Confidence
    MEDIUM, Familie nicht abgelehnt, Segment nicht gesperrt) anwenden und angewendete mit geaendertem Ziel aktualisieren.
    Aendert nur das Feld 'safe_auto' am Segment — nie Suchauftrag, km, EZ, Zeilen. Lesen gebuendelt (Rule 4)."""
    tag = tag or konfig.heute_tag()
    jetzt_iso = jetzt_iso or konfig.jetzt_iso()
    modus = (await einstellungen(db))["modus"]
    z: Dict[str, Any] = {"modus": modus, "angewendet": 0, "aktualisiert": 0, "aufgehoben": 0, "bestandsschutz": 0}
    scope: Dict[str, Any] = {"model_id": {"$in": list(model_ids)}} if model_ids is not None else {}
    beruehrt: set = set()
    aktive = await db[AENDERUNGEN].find({"status": AKTIV, **scope}, {"_id": 0}).to_list(None)
    if modus != SAFE_AUTO:
        for a in aktive:
            if await _beenden(db, a, status=AUFGEHOBEN, grund="Modus OBSERVE", wer=WER_AUTO, jetzt_iso=jetzt_iso):
                z["aufgehoben"] += 1
                beruehrt.add(a["segment_id"])
        for sid in beruehrt:
            await _wirkung_neu(db, sid)
        await db[VORSCHLAEGE].update_many({"status": APPLIED, "angewendet_von": WER_AUTO, **scope},
                                          {"$set": {"status": PROPOSED, "updated_at": jetzt_iso},
                                           "$unset": {"angewendet_von": "", "angewendet_at": "", "aenderung_id": ""}})
        return z
    kandidaten = await db[VORSCHLAEGE].find({"typ": {"$in": list(SAFE_TYPEN)}, "status": {"$in": list(OFFEN) + [APPLIED]}, **scope},
                                            {"_id": 0}).sort([("typ", 1), ("segment_id", 1)]).to_list(None)
    kandidaten = [v for v in kandidaten if v.get("status") != APPLIED or v.get("angewendet_von") == WER_AUTO]
    seg_ids = sorted({a["segment_id"] for a in aktive} | {v["segment_id"] for v in kandidaten if v.get("segment_id")})
    segs = {s["id"]: s async for s in db[SEGMENTE].find({"id": {"$in": seg_ids}}, {"_id": 0})} if seg_ids else {}
    healths = {h["segment_id"]: h async for h in db[konfig.HEALTH].find(
        {"segment_id": {"$in": seg_ids}}, {"_id": 0, "segment_id": 1, "health": 1, "activity_score": 1, "empfehlung": 1,
                                           "crawls_per_day": 1, "version": 1, "empty_rate": 1, "avg_valid_rows": 1,
                                           "letzter_treffer_tag": 1, "letzter_gueltiger_tag": 1, "tag": 1})} if seg_ids else {}
    abgelehnt = await abgelehnte_familien(db, list(segs.values()))       # auch fruehere Fassungen desselben Bereichs
    sperren = await sperren_je_segment(db, list(segs.values()), tag)
    z["zurueckgehalten"] = 0

    async def _zurueckhalten(a: Dict[str, Any], grund_neu: str) -> None:
        """Mindestverweildauer: die gewuenschte Aenderung steht am Protokolleintrag (nachvollziehbar), sie wird nicht
        ausgefuehrt. Nur schreiben, wenn sich etwas aendert (kein taeglicher Schreib-Churn)."""
        z["zurueckgehalten"] += 1
        eintrag = {"tag": tag, "grund": grund_neu, "bis": verweil_bis(a)}
        alt = a.get("zurueckgehalten") or {}
        if alt.get("grund") != grund_neu or alt.get("bis") != eintrag["bis"]:
            await db[AENDERUNGEN].update_one({"id": a["id"], "status": AKTIV}, {"$set": {"zurueckgehalten": eintrag}})
            a["zurueckgehalten"] = eintrag
    # (1) bestehende Wirkungen
    bleibt: Dict[Tuple[str, str], Dict[str, Any]] = {}
    kette_vorher: Dict[str, str] = {}
    for a in aktive:
        seg = segs.get(a["segment_id"])
        grund, obsolet = None, False
        h = healths.get(a["segment_id"])
        if not seg or not seg.get("enabled"):
            grund, obsolet = "Segment inaktiv (Auftrag pausiert/geändert)", True
        elif (a["segment_id"], a.get("typ")) in abgelehnt:
            grund = "Vorschlag abgelehnt"
        else:
            grund = widerspruch(a.get("typ"), h, seg, a)
            obsolet = bool(grund)
            # Mindestverweildauer: nur die Aufhebung einer Pause nach einem neuen Treffer-Lauf darf frueher
            if grund and verweil_haelt(a, tag) and not (a.get("typ") == PAUSE and neuer_treffer_lauf(a, h)):
                await _zurueckhalten(a, grund)
                grund, obsolet = None, False
        if not grund and a.get("typ") == PAUSE and seg and h and str(h.get("tag") or "") == tag:
            # die Pause bleibt: alle Laeufe bis heute sind geprueft — ein spaeterer Treffer-Lauf ist dann 'neu'
            lg = str(h.get("letzter_gueltiger_tag") or "")[:10]
            if lg and lg > pause_geprueft_bis(a) and lg <= tag:
                await db[AENDERUNGEN].update_one({"id": a["id"], "status": AKTIV}, {"$set": {"geprueft_bis": lg}})
                a["geprueft_bis"] = lg
                beruehrt.add(a["segment_id"])
        if grund:
            if await _beenden(db, a, status=AUFGEHOBEN, grund=grund, wer=WER_AUTO, jetzt_iso=jetzt_iso):
                z["aufgehoben"] += 1
                beruehrt.add(a["segment_id"])
                if obsolet and a.get("typ") in (REDUCE, PAUSE):
                    kette_vorher[a["segment_id"]] = _reduziert_seit(a)   # Reduktionskette laeuft weiter (erwartbare Laeufe)
                if obsolet:
                    await _vorschlag_obsolet(db, a.get("vorschlag_id"), grund, jetzt_iso)
        else:
            z["bestandsschutz"] += 1
            bleibt[(a["segment_id"], a.get("typ"))] = a
    # (2) anwenden / aktualisieren
    mindest = health.CONFIDENCE_RANG[SAFE_MIN_CONFIDENCE]
    for v in kandidaten:
        seg = segs.get(v.get("segment_id"))
        if not seg or not seg.get("enabled") or int(seg.get("version") or 1) != int(v.get("version") or 1):
            continue
        if (seg["id"], v["typ"]) in abgelehnt:
            continue                     # abgelehnt bleibt abgelehnt — fuer die ganze Familie (auch alte Schluessel)
        a = bleibt.get((seg["id"], v["typ"]))
        if v.get("status") == APPLIED and a and a.get("vorschlag_id") == v["id"] and (a.get("neu") or {}) == _neu_aus_vorschlag(v):
            if v.get("aenderung_id") != a["id"]:
                # Runde 2: unter d2d66db angewendete Vorschlaege tragen kein aenderung_id (der Knopf 'Zuruecknehmen'
                # in der Vorschlagsliste fehlte dauerhaft) — hier nachtragen
                await _vorschlag_angewendet(db, v["id"], a["id"], jetzt_iso)
            continue                     # unveraendert
        if health.CONFIDENCE_RANG.get(v.get("confidence") or "LOW", 1) < mindest:
            continue                     # LOW: nichts Neues — eine bestehende Wirkung bleibt (Bestandsschutz)
        if sperren.get(seg["id"], "") >= tag:
            continue                     # nach einer Ruecknahme: Ruhe fuer diesen Bereich (auch ueber Fassungen)
        gegen = bleibt.get((seg["id"], {REDUCE: PAUSE, PAUSE: REDUCE}.get(v["typ"], "")))
        if v["typ"] == REDUCE and gegen:
            continue                     # Schlussrunde: eine bestehende Pause endet nur ueber widerspruch (neuer Treffer-Lauf)
        halter = None
        if a and (a.get("neu") or {}) != _neu_aus_vorschlag(v) and verweil_haelt(a, tag):
            halter = a
        elif gegen and verweil_haelt(gegen, tag):
            halter = gegen
        if halter is not None:
            # Mindestverweildauer: Ziel aktualisieren bzw. ersetzen erst ab haelt_bis
            await _zurueckhalten(halter, f"{TYP_TEXT[v['typ']]}: {frequenz_text(_neu_aus_vorschlag(v))}"
                                 if v["typ"] != HOT_PRIO else TYP_TEXT[v["typ"]])
            continue
        if v.get("status") == APPLIED:
            frisch = await db[VORSCHLAEGE].find_one({"id": v["id"]}, {"_id": 0, "status": 1})
            if not frisch or frisch.get("status") != APPLIED:
                continue                 # inzwischen ersetzt/ueberholt (z. B. PAUSE ersetzte REDUCE in diesem Lauf)
        erg = await _anwenden(db, v, seg, tag=tag, jetzt_iso=jetzt_iso, kette_start=kette_vorher.get(seg["id"]))
        if erg == "neu":
            z["angewendet"] += 1
        elif erg == "aktualisiert":
            z["aktualisiert"] += 1
        beruehrt.add(seg["id"])
    for sid in beruehrt:
        await _wirkung_neu(db, sid)
    return z


async def alle_aufheben(db, *, grund: str, wer: str) -> int:
    """Modus zurueck auf OBSERVE: alle aktiven Wirkungen aufheben, die angewendeten Vorschlaege wieder offen."""
    jetzt_iso = konfig.jetzt_iso()
    n = 0
    segs: set = set()
    async for a in db[AENDERUNGEN].find({"status": AKTIV}, {"_id": 0}):
        if await _beenden(db, a, status=AUFGEHOBEN, grund=grund, wer=wer or WER_AUTO, jetzt_iso=jetzt_iso):
            n += 1
            segs.add(a["segment_id"])
    for sid in segs:
        await _wirkung_neu(db, sid)
    # auch verwaiste Felder (z. B. nach einer Wiederherstellung) entfernen
    await db[SEGMENTE].update_many({"safe_auto": {"$exists": True}}, {"$unset": {"safe_auto": ""}})
    await db[VORSCHLAEGE].update_many({"status": APPLIED, "angewendet_von": WER_AUTO},
                                      {"$set": {"status": PROPOSED, "updated_at": jetzt_iso},
                                       "$unset": {"angewendet_von": "", "angewendet_at": "", "aenderung_id": ""}})
    return n


def _verlauf(aktion: str, wer: str, jetzt_iso: str, grund: str = "") -> Dict[str, Any]:
    return {"$push": {"verlauf": {"$each": [{"aktion": aktion, "von": str(wer or ""), "at": jetzt_iso, "grund": grund}], "$slice": -20}}}


async def _familie_ablehnen(db, v: Dict[str, Any], *, wer: str, grund: str, jetzt_iso: str) -> set:
    """Pruefbefund F3/F7/F13: Ablehnen/Zuruecknehmen gilt fuer die ganze Familie (typ, segment_id) — alle offenen oder
    angewendeten Vorschlaege der Familie (auch unter alten Schluesseln) werden REJECTED, ihre aktiven Wirkungen enden.
    MERGE/SPLIT: nur der Vorschlag selbst (Familie = Auftrag + Fassung + Bereiche = Schluessel)."""
    segs: set = set()
    if v.get("typ") not in SAFE_TYPEN or not v.get("segment_id"):
        return segs
    await db[VORSCHLAEGE].update_many({"typ": v["typ"], "segment_id": v["segment_id"], "id": {"$ne": v["id"]},
                                       "status": {"$in": [PROPOSED, ACCEPTED, APPLIED]}},
                                      {"$set": {"status": REJECTED, "entschieden_von": wer, "entschieden_at": jetzt_iso,
                                                "entscheidung_grund": f"Familie abgelehnt: {grund}", "updated_at": jetzt_iso}})
    async for a in db[AENDERUNGEN].find({"segment_id": v["segment_id"], "typ": v["typ"], "status": AKTIV}, {"_id": 0}):
        if await _beenden(db, a, status=ZURUECKGENOMMEN, grund=grund, wer=wer, jetzt_iso=jetzt_iso):
            segs.add(a["segment_id"])
    return segs


async def aenderung_zuruecknehmen(db, aenderung_id: str, *, wer: str) -> Dict[str, Any]:
    """Eine SAFE_AUTO-Aenderung einzeln zuruecknehmen: Wirkung weg, Familie abgelehnt (SAFE_AUTO wendet sie fuer dieses
    Segment nie wieder an, bis die Ablehnung aufgehoben wird), Segment zusaetzlich 30 Tage ohne neue SAFE_AUTO-Wirkung."""
    a = await db[AENDERUNGEN].find_one({"id": str(aenderung_id)}, {"_id": 0})
    if not a:
        raise NichtGefunden("Änderung nicht gefunden")
    jetzt_iso = konfig.jetzt_iso()
    if not await _beenden(db, a, status=ZURUECKGENOMMEN, grund="Rücknahme durch den Betreiber", wer=wer, jetzt_iso=jetzt_iso):
        raise Konflikt("Die Änderung ist nicht mehr aktiv")
    grund = "SAFE_AUTO-Änderung zurückgenommen"
    await db[VORSCHLAEGE].update_one({"id": a.get("vorschlag_id")},
                                     {"$set": {"status": REJECTED, "entschieden_von": wer, "entschieden_at": jetzt_iso,
                                               "entscheidung_grund": grund, "updated_at": jetzt_iso},
                                      **_verlauf("zurueckgenommen", wer, jetzt_iso, grund)})
    v = await db[VORSCHLAEGE].find_one({"id": a.get("vorschlag_id")}, {"_id": 0})
    v = v or {"id": a.get("vorschlag_id"), "typ": a.get("typ"), "segment_id": a.get("segment_id")}
    await _familie_ablehnen(db, v, wer=wer, grund=grund, jetzt_iso=jetzt_iso)
    sperre = _tag_plus(konfig.heute_tag(), RUECKNAHME_SPERRE_TAGE)
    await db[SEGMENTE].update_one({"id": a["segment_id"]}, {"$set": {"safe_auto_sperre_bis": sperre}})
    await _wirkung_neu(db, a["segment_id"])
    await zaehler_nachrechnen(db, [a.get("model_id")])
    return {**(await db[AENDERUNGEN].find_one({"id": a["id"]}, {"_id": 0})), "sperre_bis": sperre}


# ---------------------------------------------------------------- Entscheiden / Uebernehmen (Super-Admin)
async def vorschlag_entscheiden(db, vorschlag_id: str, aktion: str, *, wer: str) -> Dict[str, Any]:
    """annehmen: PROPOSED -> ACCEPTED (Merker; SAFE-Typen wendet SAFE_AUTO ohnehin an). ablehnen: offen oder von
    SAFE_AUTO angewendet -> REJECTED; bei SAFE-Typen fuer die ganze Familie (Typ + Segment, auch alte Schluessel),
    aktive Wirkungen werden zurueckgenommen. Abgelehnt bleibt abgelehnt, bis ablehnung_aufheben."""
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
    if aktion == "ablehnen":
        filt = {"id": v["id"], "$or": [{"status": {"$in": erlaubt}}, {"status": APPLIED, "angewendet_von": WER_AUTO}]}
    r = await db[VORSCHLAEGE].update_one(filt, {"$set": {"status": neu, "entschieden_von": wer, "entschieden_at": jetzt_iso,
                                                         "updated_at": jetzt_iso},
                                                **_verlauf("angenommen" if neu == ACCEPTED else "abgelehnt", wer, jetzt_iso)})
    if r.modified_count == 0:
        raise Konflikt(f"Vorschlag steht auf {v.get('status')} — so nicht (mehr) möglich")
    if aktion == "ablehnen":
        segs = await _familie_ablehnen(db, v, wer=wer, grund="Vorschlag abgelehnt", jetzt_iso=jetzt_iso)
        async for a in db[AENDERUNGEN].find({"vorschlag_id": v["id"], "status": AKTIV}, {"_id": 0}):
            if await _beenden(db, a, status=ZURUECKGENOMMEN, grund="Vorschlag abgelehnt", wer=wer, jetzt_iso=jetzt_iso):
                segs.add(a["segment_id"])
        for sid in segs:
            await _wirkung_neu(db, sid)
    await zaehler_nachrechnen(db, [v.get("model_id")])
    return await db[VORSCHLAEGE].find_one({"id": v["id"]}, {"_id": 0})


async def ablehnung_aufheben(db, vorschlag_id: str, *, wer: str) -> Dict[str, Any]:
    """Pruefbefund F3/F7/F13: die Ablehnung einer Familie ausdruecklich aufheben (nur Super-Admin, Protokoll).
    Runde 2: ALLE abgelehnten Vorschlaege der Familie (Familien-Schluessel, alte Schluessel mit Intervall und dieselbe
    Familie in frueheren Fassungen desselben Bereichs, bereich_schluessel) werden OBSOLETE — nie wieder PROPOSED mit dem
    Ziel und der Confidence von damals: solange ein Vorschlag abgelehnt war, wurden seine Werte nicht fortgeschrieben,
    und ein sofort eingeschaltetes SAFE_AUTO haette das veraltete Ziel angewendet. Der naechste Lauf (Tageslauf oder
    "Health jetzt berechnen") oeffnet die Familie mit den aktuellen Daten neu — nur wenn die Bedingung dann noch gilt.
    Eine Ruhezeit nach einer Ruecknahme (safe_auto_sperre_bis) bleibt bestehen."""
    v = await db[VORSCHLAEGE].find_one({"id": str(vorschlag_id)}, {"_id": 0})
    if not v:
        raise NichtGefunden("Vorschlag nicht gefunden")
    jetzt_iso = konfig.jetzt_iso()
    grund = "Ablehnung aufgehoben — wird im nächsten Lauf mit aktuellen Daten neu bewertet"
    aufheben = {"$set": {"status": OBSOLETE, "ablehnung_aufgehoben_von": wer, "ablehnung_aufgehoben_at": jetzt_iso, "updated_at": jetzt_iso,
                         "obsolet_at": jetzt_iso, "obsolet_grund": grund},
                "$unset": {"entscheidung_grund": ""}, **_verlauf("ablehnung_aufgehoben", wer, jetzt_iso)}
    r = await db[VORSCHLAEGE].update_one({"id": v["id"], "status": REJECTED}, aufheben)
    if r.modified_count == 0:
        raise Konflikt(f"Vorschlag steht auf {v.get('status')} — keine Ablehnung zum Aufheben")
    if v.get("typ") in SAFE_TYPEN and v.get("segment_id"):
        filt: Dict[str, Any] = {"typ": v["typ"], "status": REJECTED, "id": {"$ne": v["id"]}}
        bereich = bereich_schluessel(v)
        filt["$or"] = [{"segment_id": v["segment_id"]}] + ([{"model_id": v.get("model_id")}] if bereich else [])
        async for d in db[VORSCHLAEGE].find(filt, {"_id": 0}):
            if d.get("segment_id") == v["segment_id"] or (bereich and bereich_schluessel(d) == bereich):
                await db[VORSCHLAEGE].update_one({"id": d["id"], "status": REJECTED}, aufheben)
    await zaehler_nachrechnen(db, [v.get("model_id")])
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
        await zaehler_nachrechnen(db, [v.get("model_id")])
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
    await zaehler_nachrechnen(db, [v.get("model_id")])
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


def _wirkung_pipeline(match: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Pruefbefund F16: Summe der Wirkungen OHNE Doppelzaehlung — je Segment nur die groesste Wirkung (MERGE und
    REDUCE/PAUSE derselben Segmente schliessen sich aus). Altdokumente ohne wirkung_je_segment zaehlen mit ihrer
    geschaetzten Ersparnis unter ihrem Segment bzw. Schluessel."""
    # Schlussrunde: laeufe_takt (weniger Abrufe je Tag, nie gedeckelt). Altdokumente ohne das Feld: eine reine
    # Frequenzsenkung ohne Intervall (REDUCE mit intervall_tage 1) ist ganz 'Takt', alles andere Intervall/Pause.
    nur_takt = {"$and": [{"$eq": ["$typ", REDUCE]}, {"$lte": [{"$ifNull": ["$neu.intervall_tage",
                                                                           {"$ifNull": ["$proposed_definition.intervall_tage", 1]}]}, 1]}]}
    return [{"$match": match},
            {"$project": {"_id": 0, "nur_takt": nur_takt, "w": {"$ifNull": ["$wirkung_je_segment", [{
                "segment_id": {"$ifNull": ["$segment_id", "$schluessel"]}, "usd_voll": "$estimated_monthly_saving_usd",
                "laeufe": {"$literal": None}}]]}}},
            {"$unwind": "$w"},
            {"$group": {"_id": "$w.segment_id", "usd": {"$max": "$w.usd_voll"}, "laeufe": {"$max": "$w.laeufe"},
                        "takt": {"$max": {"$ifNull": ["$w.laeufe_takt", {"$cond": ["$nur_takt", "$w.laeufe", 0]}]}}}},
            {"$group": {"_id": None, "usd": {"$sum": {"$cond": [{"$gt": ["$usd", 0]}, "$usd", 0]}},
                        "laeufe": {"$sum": {"$cond": [{"$gt": ["$laeufe", 0]}, "$laeufe", 0]}},
                        "takt": {"$sum": {"$cond": [{"$gt": ["$takt", 0]}, "$takt", 0]}}}}]


async def wirkung_summe(db, sammlung: str, match: Dict[str, Any], rotation: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    """Dollar (0 bei Budgetgrenze), Dollar ohne Budgetgrenze, frei werdende Laeufe je Monat — je Segment einmal."""
    erg = [g async for g in db[sammlung].aggregate(_wirkung_pipeline(match))]
    usd_voll = round(float(erg[0].get("usd") or 0), 2) if erg else 0.0
    laeufe = round(float(erg[0].get("laeufe") or 0), 1) if erg else 0.0
    takt = round(min(laeufe, float(erg[0].get("takt") or 0)), 1) if erg else 0.0
    grenze = bool((rotation or {}).get("aktiv"))
    return {"usd": 0.0 if grenze else usd_voll, "usd_voll": usd_voll, "laeufe": laeufe, "laeufe_takt": takt, "budget_grenze": grenze}


async def _modell_zaehler(db, model_id: str, rotation: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    offen = await db[VORSCHLAEGE].count_documents({"model_id": model_id, "status": {"$in": list(OFFEN)}})
    summe = await wirkung_summe(db, VORSCHLAEGE, {"model_id": model_id, "status": {"$in": list(OFFEN)}}, rotation)
    aktiv = await db[AENDERUNGEN].count_documents({"model_id": model_id, "status": AKTIV})
    return {"vorschlaege_offen": int(offen), "ersparnis_offen_usd": summe["usd"], "ersparnis_offen_usd_ohne_grenze": summe["usd_voll"],
            "laeufe_frei_offen_monat": summe["laeufe"], "ersparnis_budget_grenze": summe["budget_grenze"],
            "safe_auto_aktiv": int(aktiv), "zaehler_at": konfig.jetzt_iso()}


async def zaehler_nachrechnen(db, model_ids: Optional[List[Optional[str]]] = None, *,
                              rotation: Optional[Dict[str, Any]] = None) -> int:
    """Pruefbefund F14: Zaehler je Suchauftrag (offene Vorschlaege, Ersparnis, aktive SAFE_AUTO-Aenderungen) sofort
    nach jeder Entscheidung nachrechnen — nicht erst im naechsten Tageslauf. Ohne model_ids: alle mit Modell-Health
    (Moduswechsel betrifft alle). Nur Aggregat-Dokumente, die es schon gibt (kein Upsert)."""
    if model_ids is None:
        mids = sorted({str(m) for m in await db[MODELL_HEALTH].distinct("model_id") if m})
    else:
        mids = sorted({str(m) for m in model_ids if m})
    if not mids:
        return 0
    rotation = rotation if rotation is not None else await rotation_lesen(db, konfig.heute_tag())
    for mid in mids:
        await db[MODELL_HEALTH].update_one({"model_id": mid}, {"$set": await _modell_zaehler(db, mid, rotation)})
    return len(mids)


async def berechnen(db, *, stichtag: Optional[str] = None, model_ids: Optional[List[str]] = None,
                    jetzt: Optional[datetime] = None) -> Dict[str, Any]:
    """Health + Vorschlaege fuer alle Suchauftraege mit aktiven Segmenten (model_ids grenzt ein — Tests/Admin), danach
    SAFE_AUTO (nur im Modus SAFE_AUTO; sonst werden stehengebliebene Wirkungen aufgehoben). Ein Fehler in einem Auftrag haelt die anderen nicht auf (Zaehler 'fehler' ->
    Betriebsalarm im Worker). Schreibpause: sofort anhalten ('wartung'), nichts halb Fertiges als ueberholt markieren."""
    jetzt = jetzt or konfig.jetzt()
    jetzt_iso = jetzt.isoformat()
    tag = stichtag or konfig.heute_tag(jetzt)
    einst = await einstellungen(db)
    rotation = await rotation_lesen(db, tag)
    lauf_id = uuid.uuid4().hex
    z: Dict[str, Any] = {"stichtag": tag, "modus": einst["modus"], "modelle": 0, "segmente": 0,
                         "health": {s: 0 for s in health.STATUS}, "vorschlaege": 0, "vorschlaege_neu": 0, "ueberholt": 0, "fehler": 0,
                         "budget_grenze": bool(rotation.get("aktiv"))}
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
                liste += vorschlaege_segment(segs[sid], h, rotation)
                z["health"][h["health"]] = z["health"].get(h["health"], 0) + 1
            liste += vorschlaege_modell(m, list(segs.values()), erg["segmente"], rotation)
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
    z["safe_auto"] = await safe_auto_anwenden(db, tag=tag, jetzt_iso=jetzt_iso, model_ids=model_ids)
    await zaehler_nachrechnen(db, erfolgreich, rotation=rotation)
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
                                   ergebnis=_kurz(erg), quelle="taeglich", taeglich_lauf_at=jetzt.isoformat())
    return erg


async def jetzt_berechnen(db, *, wer: str = "", jetzt: Optional[datetime] = None) -> Dict[str, Any]:
    """Admin-Knopf 'Health jetzt berechnen': dieselbe Sperre wie der Auswertungs-Worker (zwei Server rechnen nie
    gleichzeitig) -> {"gesperrt": True}, wenn gerade ein anderer Prozess auswertet.
    Pruefbefund F9: setzt NICHT den Tagesmerker (tag) und nicht die Fehlerzahl des taeglichen Laufs — sonst
    uebersprang der regulaere Lauf nach dem Crawl-Fenster den Tag, und Health/SAFE_AUTO beruhten bis zum Folgetag auf
    einem Stand ohne die Laeufe des Tages. Der Knopf vermerkt nur seinen eigenen Stand (admin_*)."""
    from job_lock import acquire, release
    token = await acquire(db, SPERRE, ttl_seconds=SPERRE_S)
    if not token:
        return {"gesperrt": True}
    try:
        with _schreiber():
            jetzt = jetzt or konfig.jetzt()
            erg = await berechnen(db, jetzt=jetzt)
            if not erg.get("wartung"):
                await konfig.merker_setzen(db, konfig.HEALTH_DOK, letzter_lauf_at=jetzt.isoformat(), quelle="admin", von=str(wer or ""),
                                           admin_lauf_at=jetzt.isoformat(), admin_tag=konfig.heute_tag(jetzt),
                                           admin_fehler=int(erg.get("fehler") or 0), admin_ergebnis=_kurz(erg))
        return erg
    finally:
        await release(db, SPERRE, token)


# ---------------------------------------------------------------- Lesen (Admin)
MODI_ANZEIGE = [
    {"modus": OBSERVE, "text": "Beobachten — nur Empfehlungen, der Tagesplan bleibt unverändert", "gesperrt": False},
    {"modus": SAFE_AUTO, "text": "Sicher automatisch — Frequenz senken, EMPTY pausieren (mit Nachprüfung), HOT vorziehen; nie km/EZ/Zeilen", "gesperrt": False},
    {"modus": FULL_AUTO, "text": "Voll automatisch — km-Bereiche zusammenlegen/aufteilen (gesperrt, nur nach ausdrücklicher Freigabe)", "gesperrt": True},
]


def schwellen() -> Dict[str, Any]:
    return {**health.schwellen(), "merge_min_laeufe": MERGE_MIN_LAEUFE, "merge_luecke_max_km": MERGE_LUECKE_MAX_KM,
            "split_voll_anteil": SPLIT_VOLL_ANTEIL, "split_streuung_pct": SPLIT_STREUUNG_PCT, "split_min_breite_km": SPLIT_MIN_BREITE_KM,
            "safe_min_confidence": SAFE_MIN_CONFIDENCE, "ruecknahme_sperre_tage": RUECKNAHME_SPERRE_TAGE,
            "min_verweil_tage": MIN_VERWEIL_TAGE, "taeglich_ab_stunde": taeglich_ab_stunde()}


async def uebersicht(db) -> Dict[str, Any]:
    """Modus, Frequenz-Zuordnung, Schwellen, Stand, Zaehler je Health-Status, Modell-Health der aktiven Auftraege,
    Vorschlaege je Status/Typ, geschaetzte Wirkung (offen und durch aktive SAFE_AUTO-Wirkungen; je Segment einmal,
    Dollar nur ohne Budgetgrenze — sonst frei werdende Laeufe). Nur lesen."""
    einst = await einstellungen(db)
    rotation = await rotation_lesen(db, konfig.heute_tag())
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
    offen = await wirkung_summe(db, VORSCHLAEGE, {"status": {"$in": list(OFFEN)}}, rotation)
    aktiv = aktive_wirkung_deckeln(await wirkung_summe(db, AENDERUNGEN, {"status": AKTIV}, rotation), rotation)
    return {"modus": einst["modus"], "modi": MODI_ANZEIGE, "frequenz": einst["frequenz"], "frequenz_standard": einst["frequenz_standard"],
            "frequenz_grenzen": einst["frequenz_grenzen"], "frequenz_begrenzt": einst["frequenz_begrenzt"],
            "laeufe_frei_safe_auto_takt_monat": aktiv.get("laeufe_takt", 0.0),
            "modus_seit": einst["modus_seit"], "modus_von": einst["modus_von"], "modus_verlauf": einst["modus_verlauf"],
            "schwellen": schwellen(), "stand": await konfig.merker_lesen(db, konfig.HEALTH_DOK), "zaehler": zaehler,
            "modelle": modelle, "vorschlaege_je_status": je_status, "vorschlaege_offen_je_typ": je_typ,
            "ersparnis_offen_usd": offen["usd"], "ersparnis_offen_usd_ohne_grenze": offen["usd_voll"],
            "laeufe_frei_offen_monat": offen["laeufe"],
            "ersparnis_safe_auto_usd": aktiv["usd"], "ersparnis_safe_auto_usd_ohne_grenze": aktiv["usd_voll"],
            "laeufe_frei_safe_auto_monat": aktiv["laeufe"], "ersparnis_safe_auto_gedeckelt": bool(aktiv.get("gedeckelt")),
            "budget_grenze": bool(rotation.get("aktiv")), "rotation": rotation,
            "safe_auto_aktiv": await db[AENDERUNGEN].count_documents({"status": AKTIV}),
            "hinweis": ("Health und Vorschläge entstehen nur aus gespeicherten Tageswerten — keine Zusatzabrufe, keine Kosten. "
                        "Health (Marktaktivität) ist getrennt von der technischen Datenqualität.")}


VORSCHLAG_STATUS_RANG = (PROPOSED, ACCEPTED, APPLIED, REJECTED, OBSOLETE)
SORTIERUNGEN = ("wirkung", "datum")


def _grenzen(limit: Any, offset: Any, standard: int) -> Tuple[int, int]:
    try:
        n = max(1, min(int(limit or standard), LIMIT_MAX))
    except (TypeError, ValueError):
        n = standard
    try:
        o = max(0, int(offset or 0))
    except (TypeError, ValueError):
        o = 0
    return n, o


async def vorschlaege_liste(db, *, status: str = "offen", typ: Optional[str] = None, model_id: Optional[str] = None,
                            limit: int = 100, offset: int = 0, sortierung: str = "wirkung") -> Dict[str, Any]:
    """Pruefbefund F10/F12: gefiltert (Status, Typ, Auftrag) und seitenweise (limit/offset) MIT Gesamtzahl — nie still
    gekuerzt. Sortierung in der Datenbank VOR dem Kuerzen: Status (offen zuerst), dann Wirkung (Betrag der frei
    werdenden bzw. zusaetzlich noetigen Laeufe — so stehen auch Aufteilen-Vorschlaege mit Mehrkosten oben), dann
    Datum, zuletzt Schluessel (stabil). 'datum': neueste zuerst. je_typ: Anzahl je Typ unter den uebrigen Filtern."""
    filt: Dict[str, Any] = {}
    if status == "offen":
        filt["status"] = {"$in": list(OFFEN)}
    elif status in STATUS:
        filt["status"] = status
    elif status != "alle":
        raise Ungueltig(f"Unbekannter Status — erlaubt: offen, alle, {', '.join(STATUS)}")
    if model_id:
        filt["model_id"] = str(model_id)
    if sortierung not in SORTIERUNGEN:
        raise Ungueltig(f"Unbekannte Sortierung — erlaubt: {', '.join(SORTIERUNGEN)}")
    je_typ = {g["_id"]: g["n"] async for g in db[VORSCHLAEGE].aggregate([{"$match": dict(filt)},
                                                                         {"$group": {"_id": "$typ", "n": {"$sum": 1}}}])}
    if typ:
        if typ not in TYPEN:
            raise Ungueltig(f"Unbekannter Typ — erlaubt: {', '.join(TYPEN)}")
        filt["typ"] = typ
    n, o = _grenzen(limit, offset, 100)
    gesamt = await db[VORSCHLAEGE].count_documents(filt)
    rang = {"$switch": {"branches": [{"case": {"$eq": ["$status", s]}, "then": i} for i, s in enumerate(VORSCHLAG_STATUS_RANG)],
                        "default": 9}}
    wirkung = {"$ifNull": ["$wirkung_betrag", {"$abs": {"$ifNull": ["$estimated_monthly_saving_usd", 0]}}]}
    sort = ({"_rang": 1, "_wirkung": -1, "updated_at": -1, "schluessel": 1} if sortierung == "wirkung"
            else {"updated_at": -1, "_rang": 1, "schluessel": 1})
    liste = [d async for d in db[VORSCHLAEGE].aggregate([
        {"$match": filt}, {"$addFields": {"_rang": rang, "_wirkung": wirkung}}, {"$sort": sort}, {"$skip": o}, {"$limit": n},
        {"$project": {"_id": 0, "_rang": 0, "_wirkung": 0}}])]
    return {"vorschlaege": liste, "anzahl": len(liste), "gesamt": int(gesamt), "offset": o, "limit": n,
            "gekuerzt": o + len(liste) < gesamt, "weitere": o + len(liste) < gesamt, "status": status, "je_typ": je_typ,
            "sortierung": sortierung}


async def aenderungen_liste(db, *, status: str = "alle", model_id: Optional[str] = None, typ: Optional[str] = None,
                            limit: int = 100, offset: int = 0) -> Dict[str, Any]:
    """Protokoll der SAFE_AUTO-Aenderungen, gefiltert (Status, Auftrag, Typ) und seitenweise mit Gesamtzahl
    (Pruefbefund F12: alle Eintraege eines Laufs haben dasselbe 'at' — stabile Reihenfolge ueber Auftrag/Segment)."""
    filt: Dict[str, Any] = {}
    if status in (AKTIV, ZURUECKGENOMMEN, AUFGEHOBEN):
        filt["status"] = status
    elif status != "alle":
        raise Ungueltig("Unbekannter Status — erlaubt: alle, aktiv, zurueckgenommen, aufgehoben")
    if model_id:
        filt["model_id"] = str(model_id)
    if typ:
        if typ not in SAFE_TYPEN:
            raise Ungueltig(f"Unbekannter Typ — erlaubt: {', '.join(SAFE_TYPEN)}")
        filt["typ"] = typ
    n, o = _grenzen(limit, offset, 100)
    gesamt = await db[AENDERUNGEN].count_documents(filt)
    liste = await db[AENDERUNGEN].find(filt, {"_id": 0}).sort([("at", -1), ("label", 1), ("segment_id", 1), ("typ", 1), ("id", 1)])\
        .skip(o).limit(n).to_list(n)
    return {"aenderungen": liste, "anzahl": len(liste), "gesamt": int(gesamt), "offset": o, "limit": n,
            "gekuerzt": o + len(liste) < gesamt, "weitere": o + len(liste) < gesamt}


async def modell_ansicht(db, model_id: str) -> Optional[Dict[str, Any]]:
    """Modellseite / Aufklappen in der Uebersicht: Modell-Health, Health je Segment, offene Vorschlaege.
    Pruefbefund F18: 'aktuell' nur, wenn das Aggregat zur AKTUELLEN Fassung eines aktiven Auftrags gehoert — sonst
    zeigt die Modellseite kein altes Badge, sondern 'pausiert' bzw. 'neue Fassung noch nicht bewertet' mit Stichtag."""
    agg = (await health.modell_health(db, [model_id])).get(str(model_id))
    segmente = await health.segmente_eines_modells(db, model_id)
    if not agg and not segmente:
        return None
    modell = await db[MODELLE].find_one({"id": str(model_id)}, {"_id": 0, "version": 1, "status": 1}) or {}
    auftrag_version = int(modell.get("version") or 1)
    auftrag_status = str(modell.get("status") or "active")
    fassung_ok = bool(agg) and int(agg.get("version") or 1) == auftrag_version
    aktuell = fassung_ok and auftrag_status == "active" and any(s.get("enabled") for s in segmente)
    grund = None
    if agg and not fassung_ok:
        grund = "neue_fassung"
    elif agg and auftrag_status != "active":
        grund = "pausiert"
    elif agg and not aktuell:
        grund = "keine_aktiven_segmente"
    vs = await db[VORSCHLAEGE].find({"model_id": str(model_id), "status": {"$in": list(OFFEN) + [APPLIED]}}, {"_id": 0})\
        .sort([("typ", 1), ("schluessel", 1)]).to_list(500)
    return {"model_id": str(model_id), "modell": agg, "segmente": segmente, "vorschlaege": vs, "aktuell": aktuell,
            "nicht_aktuell_grund": grund, "auftrag_status": auftrag_status, "auftrag_version": auftrag_version,
            "health_tag": (agg or {}).get("tag"), "health_version": (agg or {}).get("version")}
