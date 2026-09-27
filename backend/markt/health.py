# -*- coding: utf-8 -*-
"""Segment-Health (Master-Auftrag Ahmad 26.09.2026, Phase F — Abschnitte 27-30, 39, 42, 47, 49, 50, 53, 56).

Wie sinnvoll ist ein Segment (Suchauftrag x Fassung x EZ-Jahr x km-Bereich)? Beurteilt NUR aus gespeicherten
Tageswerten der letzten 30 Tage (market_segment_daily_stats) — nie aus einem neuen Abruf.

Getrennt von der technischen Datenqualitaet (Abschnitt 27): ein Segment mit zwei echten Autos und sauberem Lauf ist
data_quality GOOD und health THIN. Die Datenqualitaet entscheidet nur, WELCHE Laeufe zaehlen (nur GOOD/MEDIUM sind
gueltige Beobachtungen; POOR und ungueltige Laeufe zaehlen als ungueltig).

Status (Abschnitte 27-29, Pruefreihenfolge):
  STALE     letzter gueltiger Lauf zu alt (erwarteter Abstand + STALE_PUFFER_TAGE)
  UNSTABLE  viele ungueltige Laeufe (>= 30 %, mindestens 3) — oder stark schwankende Werte (s. u.)
  UNKNOWN   noch zu wenig gueltige Laeufe fuer ein Urteil (< 7; EMPTY-Kandidaten < 14) — Master-Auftrag
            "Bei Unsicherheit UNKNOWN statt erfundener Werte"; zaehlt nicht als Bewertung
  EMPTY     mindestens 14 gueltige Laeufe UND >= 80 % davon ohne ein einziges Auto (Abschnitt 28; nie loeschen)
  THIN      nicht ueberwiegend leer, aber im Mittel < 2 gueltige Fahrzeuge je Lauf (Abschnitt 29)
  UNSTABLE  Tages-Low-Market-Median schwankt stark (Variationskoeffizient >= 20 % ueber >= 5 Tage)
  HOT       Activity Score >= 75
  HEALTHY   Activity Score >= 45 und Stichprobe im Mittel >= 60 % der bestellten Zeilen gefuellt
  NORMAL    alles andere mit genug Daten

Activity Score 0-100 (Abschnitt 30) aus Trefferquote, neuen Listings, Top-N-Wechseln, Preisaenderungen und
Liquiditaet. Pruefbefund F2 (27.09.2026): jede Kennzahl so normiert, dass derselbe Markt bei jedem Abrufabstand
denselben Score ergibt — sonst senkte eine SAFE_AUTO-Reduktion den Score selbst weiter:
  - Top-N-Wechsel (ja/nein je Vergleich) JE GUELTIGEM LAUFVERGLEICH: ein Ja/Nein-Wert saettigt — ueber 3 Tage
    kann sich die Top-5 nur einmal "aendern"; je Kalendertag geteilt fiele die Komponente bei Intervall n auf 1/n.
  - Zaehler (neue/verschwundene Inserate, Preisaenderungen) JE KALENDERTAG: sie sammeln sich ueber den Abstand an
    (3 Tage = ~3x so viele Ereignisse im Vergleich); je Vergleich stiege der Score mit der Reduktion.

Empfohlene Frequenz aus der KONFIGURIERBAREN Zuordnung (market_config/optimierung.frequenz, Admin): Standard
75-100 -> 2x taeglich, 45-74 -> 1x taeglich, 20-44 -> alle 2 Tage, 0-19 -> alle 3-7 Tage, EMPTY -> pausiert mit
Nachpruefung alle 7 Tage. Die Empfehlung wirkt nur in SAFE_AUTO (markt.optimierung) und dort nur als Reduktion.
Pruefbefund F1/F6: die Zielstufe ist die Zuordnung EXAKT; die Hysterese (5 Punkte) haelt nur die aktuelle Stufe,
solange der Score weniger als 5 Punkte jenseits der Grenze zur Zielstufe liegt (empfehlung(aktuell=...)).
Pruefbefund F0/F5: unter einer SAFE_AUTO-Wirkung messen Mindestlaufzahl und Confidence gegen die bei der aktuellen
Frequenz erwartbaren Laeufe (mit Puffer fuer Ausfaelle), nicht gegen feste 14/24 Laeufe (erwartete_laeufe/schwelle).

Sammlungen:
  market_segment_health          aktueller Stand je Segment (Abschnitt 42) — ein Dokument je Segment
  market_segment_health_history  nur WECHSEL des Status (nicht jeden Tag ein Dokument; 4.068 Segmente)
  market_model_health            Aggregat je Suchauftrag (Zaehler je Status, Modell-Status, Mittel Score)

Dieses Modul liest nur gespeicherte Tageswerte und schreibt nur Health-Dokumente — es loest NIE einen Marktabruf aus
(Architekturtest test_b02). Kosten: 0.
"""
from __future__ import annotations

import asyncio
import statistics
from datetime import datetime, timedelta
from typing import Any, Dict, Iterable, List, Optional, Tuple

from pymongo import UpdateOne

from markt import konfig, speicher
from markt.konfig import HEALTH, HEALTH_HISTORIE, MODELL_HEALTH, MODELLE, SEGMENTE, SEGMENTSTATS, TAGESSTATS

# ---------------------------------------------------------------- Status
HOT, HEALTHY, NORMAL, THIN, EMPTY, UNSTABLE, STALE, UNKNOWN = (
    "HOT", "HEALTHY", "NORMAL", "THIN", "EMPTY", "UNSTABLE", "STALE", "UNKNOWN")
STATUS = (HOT, HEALTHY, NORMAL, THIN, EMPTY, UNSTABLE, STALE, UNKNOWN)
BEWERTET = (HOT, HEALTHY, NORMAL, THIN, EMPTY, UNSTABLE, STALE)       # UNKNOWN ist kein Urteil
MIT_EMPFEHLUNG = (HOT, HEALTHY, NORMAL, THIN, EMPTY)                    # STALE/UNSTABLE: erst Technik pruefen
CONFIDENCE_RANG = {"LOW": 1, "MEDIUM": 2, "HIGH": 3}

# ---------------------------------------------------------------- Schwellen (Konstanten, keine Umgebungsvariablen)
# Fenster 30 Kalendertage bis einschliesslich Stichtag (Auftrag): ein Monat Markt — lang genug, um Wochentags-Muster
# und einzelne Ausfaelle auszugleichen, kurz genug, damit ein sich aendernder Markt (Saison) nach Wochen sichtbar wird.
FENSTER_TAGE = 30
# Erste Beurteilung ab 7 gueltigen Laeufen (eine Woche taeglicher Abrufe): darunter entscheidet ein einzelner Ausreisser-
# Tag das Urteil. EMPTY verlangt mehr (Abschnitt 28: "mindestens 14 gueltige Crawls") — Pausieren ist die staerkste
# Wirkung und darf nicht auf einer Woche beruhen.
MIN_LAEUFE_BEWERTUNG = 7
MIN_LAEUFE_EMPTY = 14
EMPTY_ANTEIL = 0.8                    # Abschnitt 28: >= 80 % der gueltigen Laeufe ohne echten Treffer
THIN_MAX_ZEILEN = 2.0                 # Abschnitt 29: im Mittel weniger als ca. 2 gueltige Fahrzeuge je Lauf
# UNSTABLE: ab 30 % ungueltiger Laeufe (POOR/ungueltig, mindestens 3) — dann fehlen an fast jedem dritten Tag
# verlaessliche Werte, die Zeitreihe ist technisch nicht tragfaehig; einzelne Ausfaelle (Wartung, Scraper-Schluckauf)
# bleiben darunter.
UNSTABLE_UNGUELTIG_ANTEIL = 0.3
UNSTABLE_UNGUELTIG_MIN = 3
# "stark schwankende Werte": Variationskoeffizient der Tages-Low-Market-Mediane >= 20 % (mindestens 5 Tage mit
# Treffern). Ein sauber abgegrenztes Segment bewegt sich im Monat um wenige Prozent (auch ein deutlicher Trend von
# -10 % ergibt nur ~3 % VK); 20 % heisst: die Stichprobe mischt verschiedene Fahrzeuge oder die Werte springen
# technisch. Nur fuer nicht duenne Segmente (bei < 2 Autos ist jeder Median ein Einzelpreis).
UNSTABLE_VOLATILITAET_PCT = 20.0
VOLATILITAET_MIN_TAGE = 5
# STALE: der Crawler plant taeglich — erst wenn der letzte gueltige Lauf mehr als "erwarteter Abstand + 2 Tage"
# zurueckliegt (bei taeglichem Abruf: 3 Tage ohne gueltigen Lauf), ist das mehr als ein einzelner Ausfall.
# Erwarteter Abstand = max(1, SAFE_AUTO-Intervall, typischer Abstand der gueltigen Tage im Fenster) — ein vom Budget
# oder von SAFE_AUTO bewusst seltener geplantes Segment ist nicht veraltet.
STALE_PUFFER_TAGE = 2
ABSTAND_MIN_LUECKEN = 3               # typischer Abstand erst ab 3 Luecken (sonst 1 Tag)
# HOT ab Score 75 (entspricht der Standard-Frequenzstufe "2x taeglich"); HEALTHY ab 45 ("1x taeglich") UND die
# Stichprobe ist im Mittel zu >= 60 % gefuellt (dieselbe Grenze wie Markttiefe NORMAL, speicher.TIEFE_NORMAL_ANTEIL).
HOT_AB_SCORE = 75
HEALTHY_AB_SCORE = 45
HEALTHY_MIN_FUELLUNG = speicher.TIEFE_NORMAL_ANTEIL
# Confidence (Abschnitt 53, regelbasiert): LOW unter 14 gueltigen Laeufen; HIGH ab 24 gueltigen Tagen (80 % des
# Fensters) UND Daten ueber das ganze Fenster (Abschnitt 28: "nach 30 Tagen hoehere Confidence"); sonst MEDIUM.
CONFIDENCE_HOCH_TAGE = 24
# Activity Score: Gewichte (Summe 100) und "volle Punktzahl ab" je Komponente. Kalibriert an den Probelaeufen
# (5 Zeilen, taeglich): ein ruhiges, volles Segment (1 neues Auto/Tag, Top-5-Wechsel an jedem 2. Tag) landet bei
# ~60 (1x taeglich); nur wirklich bewegte Segmente (>= 30 % neue Autos je Tag, Top-N-Wechsel an 70 % der Tage)
# erreichen 75+; ein duennes, fast statisches Segment bleibt unter 20.
GEWICHTE = {"treffer": 25, "neu": 25, "top": 20, "preis": 15, "liquiditaet": 15}
NEU_VOLL_ANTEIL = 0.3                 # neue Inserate je Tag relativ zu den bestellten Zeilen
TOP_VOLL = 0.7                        # Anteil der Tage mit Top-3/Top-5-Wechsel
PREIS_VOLL_ANTEIL = 0.15              # Preisaenderungen (gleiche listing_id) je Tag relativ zu den Zeilen
LUECKE_MAX_TAGE = 7                   # Abstand zum Vergleichstag wird hoechstens mit 7 Tagen gerechnet
# Liquiditaet (Abschnitt 56): Umschlag (neue + verschwundene je Tag relativ zur mittleren Stichprobe) — dieselben
# Stufen wie die Hot Deals (deals.LIQ_HOCH_UMSCHLAG / LIQ_MITTEL_UMSCHLAG), aber je Kalendertag; im Mittel unter
# 2 Autos hoechstens LOW (ein Markt mit 1 Auto ist nicht liquide, auch wenn es wechselt).
LIQ_HOCH, LIQ_MITTEL = 0.15, 0.05
LIQ_DUENN_DECKEL = 0.2
LIQ_MIN_TAGE = 3
# Frequenz: THIN nie taeglich (Abschnitt 29 "muss nicht taeglich teuer gecrawlt werden") — mindestens alle 2 Tage.
THIN_MIN_INTERVALL_TAGE = 2
# Hysterese (Pruefbefund F1/F6): ist eine SAFE_AUTO-Reduktion aktiv, wechselt die Empfehlung von der aktuellen Stufe
# erst, wenn der Score mindestens 5 Punkte jenseits der Grenze zur Zielstufe liegt (in beide Richtungen) — sonst
# pendelte ein Segment an der Grenze taeglich. Die Zielstufe selbst ist immer die konfigurierte Zuordnung EXAKT.
# 5 Punkte = die Tagesschwankung eines ruhigen Segments (ein neues Inserat mehr oder weniger je Woche bewegt den Score
# um 2-4 Punkte); groesser liesse echte Marktaenderungen wochenlang liegen.
HYSTERESE_PUNKTE = 5
# Unter einer SAFE_AUTO-Wirkung (Pruefbefund F0/F5): Mindestlaufzahl/Confidence gegen die bei der AKTUELLEN Frequenz
# erwartbaren Laeufe im Fenster (Laeufe vor der Reduktion zaehlen mit). Puffer: hoechstens 75 % der erwarteten Laeufe
# bzw. erwartete minus eins — ein einzelner Ausfall (Apify-Fehler, POOR, Wartung) kippt kein Urteil. Nie unter 3
# gueltige Laeufe: darunter entscheidet ein einzelner Lauf (dann bleibt die Wirkung per Bestandsschutz stehen).
ERWARTET_PUFFER_ANTEIL = 0.75
MIN_LAEUFE_UNTER_WIRKUNG = 3
MONAT_TAGE = 30.4

# Standard-Zuordnung Score -> Frequenz (Abschnitt 30); im Admin aenderbar, gespeichert in market_config/optimierung
FREQUENZ_STANDARD: Dict[str, Any] = {
    "stufen": [
        {"ab": 75, "crawls_per_day": 2, "intervall_tage": 1, "intervall_tage_bis": None},
        {"ab": 45, "crawls_per_day": 1, "intervall_tage": 1, "intervall_tage_bis": None},
        {"ab": 20, "crawls_per_day": 1, "intervall_tage": 2, "intervall_tage_bis": None},
        {"ab": 0, "crawls_per_day": 1, "intervall_tage": 3, "intervall_tage_bis": 7},
    ],
    "empty_nachpruefung_tage": 7,
}

GRUND_TEXT = {
    "keine_laeufe": "noch keine Läufe", "letzter_lauf_zu_alt": "letzter gültiger Lauf zu alt",
    "viele_ungueltige_laeufe": "viele ungültige Läufe", "zu_wenig_laeufe": "noch zu wenig gültige Läufe",
    "empty_kandidat": "fast immer leer — noch zu wenig Läufe für EMPTY", "ueberwiegend_leer": "überwiegend ohne Treffer",
    "wenige_fahrzeuge": "im Mittel unter 2 Fahrzeuge je Lauf", "stark_schwankend": "Low-Market-Median schwankt stark",
    "sehr_aktiv": "sehr aktiver Markt", "aktiv_und_gefuellt": "aktiv und gut gefüllt", "": "",
}


class Ungueltig(ValueError):
    pass


# ---------------------------------------------------------------- Hilfen
def _datum(tag: str) -> datetime:
    return datetime.strptime(str(tag)[:10], "%Y-%m-%d")


def _tag_minus(tag: str, tage: int) -> str:
    return (_datum(tag) - timedelta(days=tage)).strftime("%Y-%m-%d")


def _tage_zwischen(a: str, b: str) -> int:
    return (_datum(b) - _datum(a)).days


def _r(x: Optional[float], n: int = 2) -> Optional[float]:
    return None if x is None else round(float(x), n)


def fassung(doc: Optional[Dict[str, Any]], seg: Optional[Dict[str, Any]] = None) -> Tuple[int, Optional[str]]:
    """(version, definition_hash) eines Tagesdokuments; Altdaten ohne Felder -> Werte des Segments (wie markt.deals)."""
    doc, seg = doc or {}, seg or {}
    try:
        v = int(doc.get("version") or seg.get("version") or 1)
    except (TypeError, ValueError):
        v = 1
    return v, (doc.get("definition_hash") or seg.get("definition_hash"))


def qualitaet(doc: Optional[Dict[str, Any]], rows: Optional[int] = None) -> str:
    return speicher.qualitaet_aus_doc(doc, rows)["data_quality"]


def ist_gueltiger_tag(doc: Optional[Dict[str, Any]], rows: Optional[int] = None) -> bool:
    """Gueltige Tagesbeobachtung: gueltiger Lauf (sample_size vorhanden, auch 0 = Marktluecke) mit GOOD/MEDIUM."""
    return bool(doc) and doc.get("sample_size") is not None and qualitaet(doc, rows) in speicher.BASIS_QUALITAET


def laeufe_aus_doc(doc: Dict[str, Any], rows: Optional[int] = None) -> List[Tuple[str, int, str]]:
    """(art, zeilen, datenqualitaet) je Lauf eines Tagesdokuments — art 'gueltig' (GOOD/MEDIUM), 'poor' (Lauf mit
    Datenqualitaet POOR) oder 'ungueltig' (data_invalid). Aus 'laeufe' (Phase C: je Lauf Qualitaet und Stichprobe),
    fuer Altdaten ohne 'laeufe' aus valid_runs / empty_runs / invalid_runs / sample_size abgeleitet."""
    laeufe = doc.get("laeufe")
    raus: List[Tuple[str, int, str]] = []
    if isinstance(laeufe, list) and laeufe:
        for x in laeufe:
            if not isinstance(x, dict):
                continue
            if x.get("ungueltig"):
                raus.append(("ungueltig", 0, "POOR"))
                continue
            dq = str(x.get("data_quality") or ("MEDIUM" if x.get("top_n_bewiesen") is False else "GOOD"))
            n = int(x.get("sample_size") or 0)
            raus.append(("poor", n, dq) if dq not in speicher.BASIS_QUALITAET else ("gueltig", n, dq))
        return raus
    raus += [("ungueltig", 0, "POOR")] * max(0, int(doc.get("invalid_runs") or 0))
    if doc.get("sample_size") is not None:
        dq = qualitaet(doc, rows)
        n = int(doc.get("sample_size") or 0)
        gueltig = max(1, int(doc.get("valid_runs") or 1))
        leer = gueltig if n == 0 else min(gueltig - 1, max(0, int(doc.get("empty_runs") or 0)))
        art = "gueltig" if dq in speicher.BASIS_QUALITAET else "poor"
        raus += [(art, 0, dq)] * leer + [(art, n, dq)] * (gueltig - leer)
    return raus


def _wirkung_rate(wirkung: Optional[Dict[str, Any]], crawls_per_day: int) -> float:
    """Abrufe je Tag unter einer SAFE_AUTO-Wirkung (Intervall n Tage, ggf. weniger Abrufe je Tag)."""
    w = wirkung or {}
    n = max(1, int(w.get("intervall_tage") or 1))
    cpd = max(1, int(crawls_per_day or 1))
    k = min(cpd, int(w["crawls_per_day"])) if w.get("crawls_per_day") else cpd
    return max(1, k) / n


def erwartete_laeufe(wirkung: Optional[Dict[str, Any]], *, stichtag: str, crawls_per_day: int = 1,
                     erster_tag: Optional[str] = None, typischer_abstand: int = 1) -> Optional[int]:
    """Pruefbefund F0/F5: wie viele Laeufe sind im 30-Tage-Fenster bei der AKTUELLEN Frequenz zu erwarten?
    None ohne Reduktion (dann gelten die festen Schwellen 7/14/24 des Auftrags). Unter einer Reduktion seit
    'reduziert_seit' (Tag der ersten Reduktion der laufenden Kette): Tage davor mit den Abrufen des Auftrags (die Laeufe
    vor der Reduktion zaehlen mit), danach mit der reduzierten Rate. Liegt die ganze Beobachtung unter der Wirkung,
    die garantierte Mindestzahl floor(30 x Rate) — mit dem beobachteten typischen Abstand, falls die Budget-Rotation das
    Segment noch seltener plant."""
    cpd = max(1, int(crawls_per_day or 1))
    if not reduktion_aktiv(wirkung, cpd):
        return None
    w = wirkung or {}
    rate_n = _wirkung_rate(w, cpd)
    von = _tag_minus(stichtag, FENSTER_TAGE - 1)
    seit = str(w.get("reduziert_seit") or w.get("seit") or "")[:10] or None
    start = max(von, str(erster_tag)[:10]) if erster_tag else von
    if not seit or seit <= start:
        n = max(1, int(w.get("intervall_tage") or 1))
        n_eff = max(n, int(typischer_abstand or 1)) if n > 1 else n
        return int(FENSTER_TAGE * rate_n * n / n_eff + 1e-9)
    if seit > stichtag:
        return None
    tage_vor = _tage_zwischen(start, seit) + 1          # inkl. Tag der Anwendung (die Laeufe liefen vorher)
    tage_nach = _tage_zwischen(seit, stichtag)
    return int(tage_vor * cpd + tage_nach * rate_n + 1e-9)


def schwelle(basis: int, erwartet: Optional[int]) -> int:
    """Mindestzahl gueltiger Laeufe: ohne Wirkung die feste Schwelle (basis); unter einer Wirkung hoechstens 75 % der
    erwarteten Laeufe bzw. erwartete minus eins (ein Ausfall kippt nichts), nie unter MIN_LAEUFE_UNTER_WIRKUNG."""
    if erwartet is None:
        return basis
    puffer = min(int(erwartet) - 1, int(ERWARTET_PUFFER_ANTEIL * int(erwartet)))
    return max(MIN_LAEUFE_UNTER_WIRKUNG, min(basis, puffer))


def min_laeufe(basis: int, wirkung: Optional[Dict[str, Any]], erwartet: Optional[int] = None) -> int:
    """Mindestzahl gueltiger Laeufe fuer ein Urteil (siehe schwelle). Ohne ausdrueckliche Erwartung: die ganze
    Beobachtung liegt unter der Wirkung (z. B. alle 7 Tage -> 4 erwartete Laeufe -> 3 genuegen)."""
    if erwartet is None and reduktion_aktiv(wirkung, 1):
        erwartet = int(FENSTER_TAGE * _wirkung_rate(wirkung, 1) + 1e-9)
    return schwelle(basis, erwartet)


# ---------------------------------------------------------------- Kennzahlen (reine Rechnung)
def kennzahlen(docs: List[Dict[str, Any]], *, rows_soll: int, stichtag: str, crawls_per_day: int = 1,
               letzter_gueltiger_tag_ausserhalb: Optional[str] = None,
               wirkung: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """Kennzahlen eines Segments aus den Tagesdokumenten des Fensters (nur aktuelle Fassung, aufsteigend nach Datum).
    Gueltig sind nur Laeufe/Tage mit Datenqualitaet GOOD/MEDIUM; POOR und ungueltige Laeufe zaehlen als ungueltig."""
    rows = max(1, int(rows_soll or 1))
    zeilen: List[int] = []
    medium = poor = ungueltig = 0
    gueltige_tage: List[Dict[str, Any]] = []
    kosten = 0.0
    for d in docs:
        for art, n, dq in laeufe_aus_doc(d, rows):
            if art == "gueltig":
                zeilen.append(n)
                medium += 1 if dq == "MEDIUM" else 0
            elif art == "poor":
                poor += 1
            else:
                ungueltig += 1
        kosten += float(d.get("crawl_cost_usd") or 0)
        if ist_gueltiger_tag(d, rows):
            gueltige_tage.append(d)
    valid = len(zeilen)
    invalid = poor + ungueltig
    leer = sum(1 for n in zeilen if n == 0)
    inserate: set = set()
    for d in gueltige_tage:
        inserate |= {str(x) for x in (d.get("listing_ids_alle") or d.get("listing_ids") or [])}
    # Vergleichstage (Tageswerte mit gueltigem Vortag): Zaehler je KALENDERTAG (Abstand zum Vergleichstag, hoechstens
    # 7), Top-N-Wechsel (ja/nein) je VERGLEICH — Begruendung im Modulkopf (Pruefbefund F2)
    vergleich = [d for d in gueltige_tage if d.get("vergleich_vortag")]
    luecken = 0
    neu = weg = senk = erhoeh = 0
    t3: List[bool] = []
    t5: List[bool] = []
    for d in vergleich:
        try:
            abstand = max(1, min(LUECKE_MAX_TAGE, _tage_zwischen(str(d["vergleich_vortag"]), str(d["date"]))))
        except (TypeError, ValueError):
            abstand = 1
        luecken += abstand
        neu += len(d.get("new_in_sample_ids") or []) or int(d.get("new_in_sample_today") or 0)
        weg += int(d.get("disappeared_count") or 0)
        senk += int(d.get("price_reductions_today") or 0)
        erhoeh += int(d.get("price_increases_today") or 0)
        if d.get("top3_changed") is not None:
            t3.append(bool(d["top3_changed"]))
        if d.get("top5_changed") is not None:
            t5.append(bool(d["top5_changed"]))
    mittel = (sum(zeilen) / valid) if valid else 0.0
    medians = [(str(d["date"]), float(d["median_price"])) for d in gueltige_tage
               if int(d.get("sample_size") or 0) > 0 and d.get("median_price") is not None]
    aenderung_pct = None
    if len(medians) >= 2 and medians[0][1]:
        aenderung_pct = (medians[-1][1] - medians[0][1]) / medians[0][1] * 100
    vola = None
    if len(medians) >= VOLATILITAET_MIN_TAGE:
        werte = [m for _, m in medians]
        mw = sum(werte) / len(werte)
        vola = (statistics.pstdev(werte) / mw * 100) if mw else None
    streuung_tage = [(float(d["p75_price"]) - float(d["p25_price"])) / float(d["median_price"]) * 100 for d in gueltige_tage
                     if int(d.get("sample_size") or 0) >= 2 and d.get("median_price") and d.get("p25_price") is not None
                     and d.get("p75_price") is not None]
    # letzter gueltiger Lauf und typischer Abstand der gueltigen Tage
    tage_gueltig = sorted({str(d["date"]) for d in gueltige_tage})
    letzter = tage_gueltig[-1] if tage_gueltig else letzter_gueltiger_tag_ausserhalb
    abstaende = [_tage_zwischen(a, b) for a, b in zip(tage_gueltig, tage_gueltig[1:])]
    typisch = int(statistics.median(abstaende)) if len(abstaende) >= ABSTAND_MIN_LUECKEN else 1
    erwartet = max(1, typisch, int((wirkung or {}).get("intervall_tage") or 1))
    alter = _tage_zwischen(letzter, stichtag) if letzter else None
    stale_grenze = erwartet + STALE_PUFFER_TAGE
    erster = str(docs[0]["date"]) if docs else None
    beob_tage = (_tage_zwischen(erster, stichtag) + 1) if erster else 0
    laeufe_gesamt = valid + invalid
    # Pruefbefund F0/F5: Schwellen unter einer SAFE_AUTO-Wirkung gegen die erwartbaren Laeufe
    erw_laeufe = erwartete_laeufe(wirkung, stichtag=stichtag, crawls_per_day=crawls_per_day, erster_tag=erster,
                                  typischer_abstand=typisch)
    top_n = len(t3) + len(t5)
    return {
        "erwartete_laeufe": erw_laeufe, "min_laeufe_bewertung": schwelle(MIN_LAEUFE_BEWERTUNG, erw_laeufe),
        "min_laeufe_empty": schwelle(MIN_LAEUFE_EMPTY, erw_laeufe), "confidence_hoch_ab": schwelle(CONFIDENCE_HOCH_TAGE, erw_laeufe),
        "window_days": FENSTER_TAGE, "stichtag": stichtag, "rows_soll": rows,
        "valid_runs": valid, "invalid_runs": invalid, "poor_runs": poor, "data_invalid_runs": ungueltig, "empty_runs": leer,
        "empty_rate": _r(leer / valid, 3) if valid else None,
        "ungueltig_anteil": _r(invalid / laeufe_gesamt, 3) if laeufe_gesamt else None,
        "medium_anteil": _r(medium / valid, 3) if valid else None,
        "avg_valid_rows": _r(mittel, 2) if valid else None, "median_valid_rows": _r(statistics.median(zeilen), 1) if valid else None,
        "fuellung": _r(min(1.0, mittel / rows), 3) if valid else None,
        "voll_anteil": _r(sum(1 for n in zeilen if n >= rows) / valid, 3) if valid else None,
        "valid_days": len(tage_gueltig), "vergleich_tage": len(vergleich), "vergleich_kalendertage": luecken,
        "unique_listings": len(inserate), "new_listings": neu, "disappeared_listings": weg,
        "price_drop_events": senk, "price_increase_events": erhoeh,
        "top3_turnover_rate": _r(sum(t3) / len(t3), 3) if t3 else None, "top5_turnover_rate": _r(sum(t5) / len(t5), 3) if t5 else None,
        "_top_anteil": ((sum(t3) + sum(t5)) / top_n) if top_n else 0.0,
        "median_price_change_pct": _r(aenderung_pct, 2), "price_volatility_pct": _r(vola, 2),
        "preis_streuung_pct": _r(statistics.median(streuung_tage), 2) if streuung_tage else None,
        "letzter_gueltiger_tag": letzter, "letzter_lauf_alter_tage": alter, "erwarteter_abstand_tage": erwartet,
        "stale_grenze_tage": stale_grenze, "stale": alter is not None and alter > stale_grenze,
        "erster_tag_im_fenster": erster, "beobachtungs_tage": beob_tage, "laeufe_gesamt": laeufe_gesamt,
        "crawl_cost_usd_fenster": round(kosten, 4),
        "kosten_je_lauf_usd": round(kosten / laeufe_gesamt, 5) if (laeufe_gesamt and kosten > 0) else None,
        "laeufe_je_tag": _r(laeufe_gesamt / beob_tage, 3) if beob_tage else None,
        "laeufe_je_tag_konfig": max(1, min(4, int(crawls_per_day or 1))),
        "monatskosten_usd": round(kosten / beob_tage * MONAT_TAGE, 2) if (beob_tage and kosten > 0) else None,
        "_neu_je_tag": (neu / luecken) if luecken else 0.0, "_weg_je_tag": (weg / luecken) if luecken else 0.0,
        "_preis_je_tag": ((senk + erhoeh) / luecken) if luecken else 0.0, "_mittel": mittel,
    }


def activity(m: Dict[str, Any]) -> Tuple[int, Dict[str, float], str]:
    """(Score 0-100, Komponenten 0..1, Liquiditaet HIGH/MEDIUM/LOW/UNKNOWN) — Abschnitt 30/56."""
    rows = max(1, int(m.get("rows_soll") or 1))
    if not m.get("valid_runs"):
        return 0, {k: 0.0 for k in GEWICHTE}, "UNKNOWN"
    mittel = float(m.get("_mittel") or 0.0)
    umschlag = (float(m.get("_neu_je_tag") or 0) + float(m.get("_weg_je_tag") or 0)) / (2 * max(1.0, mittel))
    liq_k = min(1.0, umschlag / LIQ_HOCH)
    if mittel < THIN_MAX_ZEILEN:
        liq_k = min(liq_k, LIQ_DUENN_DECKEL)
    k = {"treffer": min(1.0, mittel / rows),
         "neu": min(1.0, float(m.get("_neu_je_tag") or 0) / (rows * NEU_VOLL_ANTEIL)),
         "top": min(1.0, float(m.get("_top_anteil", m.get("_top_je_tag")) or 0) / TOP_VOLL),
         "preis": min(1.0, float(m.get("_preis_je_tag") or 0) / (rows * PREIS_VOLL_ANTEIL)),
         "liquiditaet": liq_k}
    if int(m.get("vergleich_tage") or 0) < LIQ_MIN_TAGE:
        liq = "UNKNOWN"
    elif mittel < THIN_MAX_ZEILEN:
        liq = "LOW"
    else:
        liq = "HIGH" if umschlag >= LIQ_HOCH else "MEDIUM" if umschlag >= LIQ_MITTEL else "LOW"
    score = int(round(sum(GEWICHTE[n] * k[n] for n in GEWICHTE)))
    return max(0, min(100, score)), {n: round(v, 3) for n, v in k.items()}, liq


def health_bestimmen(m: Dict[str, Any], score: int, *, wirkung: Optional[Dict[str, Any]] = None) -> Tuple[str, str]:
    """(Status, Grund) nach der Pruefreihenfolge im Modulkopf."""
    valid, invalid = int(m.get("valid_runs") or 0), int(m.get("invalid_runs") or 0)
    if not valid and not invalid and not m.get("letzter_gueltiger_tag"):
        return UNKNOWN, "keine_laeufe"
    if m.get("stale"):
        return STALE, "letzter_lauf_zu_alt"
    if invalid >= UNSTABLE_UNGUELTIG_MIN and float(m.get("ungueltig_anteil") or 0) >= UNSTABLE_UNGUELTIG_ANTEIL:
        return UNSTABLE, "viele_ungueltige_laeufe"
    leer = float(m.get("empty_rate") or 0) >= EMPTY_ANTEIL
    erw = m.get("erwartete_laeufe")
    min_bew = int(m["min_laeufe_bewertung"]) if m.get("min_laeufe_bewertung") else min_laeufe(MIN_LAEUFE_BEWERTUNG, wirkung, erw)
    min_empty = int(m["min_laeufe_empty"]) if m.get("min_laeufe_empty") else min_laeufe(MIN_LAEUFE_EMPTY, wirkung, erw)
    if valid < min_bew:
        return UNKNOWN, ("empty_kandidat" if valid and leer else "zu_wenig_laeufe")
    if leer:
        if valid >= min_empty:
            return EMPTY, "ueberwiegend_leer"
        return UNKNOWN, "empty_kandidat"
    if float(m.get("avg_valid_rows") or 0) < THIN_MAX_ZEILEN:
        return THIN, "wenige_fahrzeuge"
    if m.get("price_volatility_pct") is not None and float(m["price_volatility_pct"]) >= UNSTABLE_VOLATILITAET_PCT:
        return UNSTABLE, "stark_schwankend"
    if score >= HOT_AB_SCORE:
        return HOT, "sehr_aktiv"
    if score >= HEALTHY_AB_SCORE and float(m.get("fuellung") or 0) >= HEALTHY_MIN_FUELLUNG:
        return HEALTHY, "aktiv_und_gefuellt"
    return NORMAL, ""


def confidence_bestimmen(m: Dict[str, Any], status: str, stichtag: str) -> str:
    """LOW / MEDIUM / HIGH (Abschnitt 53). Ohne Wirkung: MEDIUM ab 14, HIGH ab 24 gueltigen Tagen ueber das ganze
    Fenster. Unter einer SAFE_AUTO-Wirkung (Pruefbefund F0/F5) dieselben Stufen gemessen an den erwartbaren Laeufen
    (schwelle) — sonst waere jede Wirkung ab Intervall 3 dauerhaft LOW und nie mehr anpassbar."""
    min_empty = int(m.get("min_laeufe_empty") or MIN_LAEUFE_EMPTY)
    hoch_ab = int(m.get("confidence_hoch_ab") or CONFIDENCE_HOCH_TAGE)
    if status == UNKNOWN or int(m.get("valid_runs") or 0) < min_empty:
        return "LOW"
    erster = m.get("erster_tag_im_fenster")
    ganzes_fenster = bool(erster) and _tage_zwischen(erster, stichtag) >= FENSTER_TAGE - 1
    if m.get("erwartete_laeufe") is not None:
        # unter einer Wirkung beginnt das Fenster mit dem ersten geplanten Lauf — 'ganzes Fenster' = erster Lauf
        # hoechstens ein Intervall nach dem Fensterbeginn
        ganzes_fenster = bool(erster) and _tage_zwischen(erster, stichtag) >= FENSTER_TAGE - 1 - int(m.get("erwarteter_abstand_tage") or 1)
    if int(m.get("valid_days") or 0) >= hoch_ab and ganzes_fenster:
        return "HIGH"
    return "MEDIUM"


def market_depth_zusammen(m: Dict[str, Any]) -> str:
    """Markttiefe ueber das Fenster (gleiche Stufen wie speicher.market_depth_bewerten, aus dem Mittel)."""
    if not m.get("valid_runs"):
        return "UNKNOWN"
    if float(m.get("empty_rate") or 0) >= EMPTY_ANTEIL:
        return "EMPTY"
    mittel, rows = float(m.get("_mittel") or 0), max(1, int(m.get("rows_soll") or 1))
    if mittel >= rows:
        return "FULL"
    return "NORMAL" if mittel >= rows * speicher.TIEFE_NORMAL_ANTEIL else "THIN"


def data_quality_zusammen(m: Dict[str, Any]) -> str:
    """Technische Datenqualitaet ueber das Fenster — nur zur Anzeige, getrennt vom Health-Status."""
    if not m.get("valid_runs") and not m.get("invalid_runs"):
        return "UNKNOWN"
    if float(m.get("ungueltig_anteil") or 0) >= UNSTABLE_UNGUELTIG_ANTEIL:
        return "POOR"
    if not m.get("valid_runs"):
        return "POOR"
    return "MEDIUM" if float(m.get("medium_anteil") or 0) >= UNSTABLE_UNGUELTIG_ANTEIL else "GOOD"


# ---------------------------------------------------------------- Frequenz-Zuordnung (konfigurierbar)
def frequenz_pruefen(roh: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    """Zuordnung Score -> Frequenz pruefen und normalisieren. 1-6 Stufen, 'ab' streng fallend, letzte Stufe ab 0;
    2x taeglich (crawls_per_day > 1) nur mit Intervall 1 Tag; Intervalle 1-30 Tage; 'bis' (Spanne wie 3-7) >= von;
    EMPTY-Nachpruefung 1-60 Tage."""
    roh = roh or {}
    stufen_roh = roh.get("stufen")
    if not isinstance(stufen_roh, list) or not 1 <= len(stufen_roh) <= 6:
        raise Ungueltig("1 bis 6 Frequenzstufen")

    def _zahl(w: Any, name: str, unten: int, oben: int) -> int:
        try:
            z = int(w)
        except (TypeError, ValueError):
            raise Ungueltig(f"{name}: keine Zahl")
        if z < unten or z > oben:
            raise Ungueltig(f"{name}: {z} liegt außerhalb {unten}–{oben}")
        return z
    stufen = []
    for i, s in enumerate(stufen_roh, 1):
        if not isinstance(s, dict):
            raise Ungueltig(f"Stufe {i}: ungültig")
        ab = _zahl(s.get("ab"), f"Stufe {i} ab Score", 0, 100)
        cpd = _zahl(s.get("crawls_per_day") or 1, f"Stufe {i} Abrufe je Tag", 1, 4)
        n = _zahl(s.get("intervall_tage") or 1, f"Stufe {i} Intervall", 1, 30)
        bis = s.get("intervall_tage_bis")
        bis = None if bis in (None, "", 0) else _zahl(bis, f"Stufe {i} Intervall bis", 1, 30)
        if bis is not None and bis < n:
            raise Ungueltig(f"Stufe {i}: Intervall bis ({bis}) liegt unter von ({n})")
        if cpd > 1 and (n > 1 or (bis or 1) > 1):
            raise Ungueltig(f"Stufe {i}: mehrere Abrufe je Tag nur mit Intervall 1 Tag")
        stufen.append({"ab": ab, "crawls_per_day": cpd, "intervall_tage": n, "intervall_tage_bis": bis if bis and bis > n else None})
    for a, b in zip(stufen, stufen[1:]):
        if b["ab"] >= a["ab"]:
            raise Ungueltig("Stufen: 'ab Score' muss von oben nach unten streng fallen")
    if stufen[-1]["ab"] != 0:
        raise Ungueltig("die letzte Stufe muss bei Score 0 beginnen")
    nach = _zahl(roh.get("empty_nachpruefung_tage") or 7, "EMPTY-Nachprüfung", 1, 60)
    return {"stufen": stufen, "empty_nachpruefung_tage": nach}


def empfehlung(status: str, score: int, cfg: Optional[Dict[str, Any]] = None, *,
               aktuell: Optional[Dict[str, Any]] = None) -> Optional[Dict[str, Any]]:
    """Empfohlene Frequenz. None fuer UNKNOWN/STALE/UNSTABLE (erst Daten/Technik pruefen, keine Frequenzaenderung).
    frequency_days: 0.5 = 2x taeglich, 1 = taeglich, n = alle n Tage.
    Zielstufe = konfigurierte Zuordnung EXAKT (Pruefbefund F1/F6). aktuell = die gerade wirkende SAFE_AUTO-Reduktion
    ({intervall_tage, crawls_per_day}): die Hysterese haelt sie nur, solange der Score weniger als HYSTERESE_PUNKTE
    jenseits der Grenze zur Zielstufe liegt — sie verschiebt nie die Zielstufe selbst."""
    cfg = cfg or FREQUENZ_STANDARD
    ziel = _zuordnung(status, score, cfg)
    if not ziel or ziel.get("pausiert") or not aktuell or aktuell.get("pausiert"):
        return ziel
    jetzt = (int(aktuell.get("intervall_tage") or 1), int(aktuell.get("crawls_per_day") or 1))
    r_jetzt = jetzt[1] / max(1, jetzt[0])
    r_ziel = rate(ziel) or 0.0
    if abs(r_ziel - r_jetzt) < 1e-9:
        return ziel
    # Die Zuordnung ist monoton (hoeherer Score -> haeufiger). Der Score liegt mindestens HYSTERESE_PUNKTE jenseits
    # der Grenze der aktuellen Stufe, wenn auch der um HYSTERESE_PUNKTE zurueckgeschobene Score schon jenseits liegt
    # (Rate echt groesser bzw. kleiner als die aktuelle) — sonst bleibt die aktuelle Stufe. Auch im linearen Band
    # (3-7 Tage, jede Tagesstufe nur ~5 Punkte breit) haelt das die Stufe, bis der Score die Grenze klar ueberschreitet.
    haeufiger = r_ziel > r_jetzt
    verschoben = max(0, min(100, int(score) + (-HYSTERESE_PUNKTE if haeufiger else HYSTERESE_PUNKTE)))
    r_zurueck = rate(_zuordnung(status, verschoben, cfg)) or 0.0
    jenseits = r_zurueck > r_jetzt + 1e-9 if haeufiger else r_zurueck < r_jetzt - 1e-9
    if not jenseits:
        return {"pausiert": False, "intervall_tage": jetzt[0], "crawls_per_day": jetzt[1],
                "frequency_days": round(jetzt[0] / max(1, jetzt[1]), 2), "stufe_ab": ziel.get("stufe_ab"),
                "hysterese": True, "ziel_exakt": ziel}
    return ziel


def _zuordnung(status: str, score: int, cfg: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """Die konfigurierte Zuordnung Score -> Frequenz, exakt (ohne Hysterese)."""
    if status == EMPTY:
        n = int(cfg.get("empty_nachpruefung_tage") or 7)
        return {"pausiert": True, "intervall_tage": n, "crawls_per_day": 1, "frequency_days": float(n), "stufe_ab": None}
    if status not in MIT_EMPFEHLUNG:
        return None
    s = max(0, int(score))
    stufen = cfg.get("stufen") or FREQUENZ_STANDARD["stufen"]
    wahl, n, cpd = stufen[-1], int(stufen[-1]["intervall_tage"]), 1
    for i, st in enumerate(stufen):
        if s >= int(st["ab"]):
            wahl = st
            n = int(st["intervall_tage"])
            bis = int(st.get("intervall_tage_bis") or n)
            if bis > n:
                oben = int(stufen[i - 1]["ab"]) - 1 if i > 0 else 100
                anteil = (s - int(st["ab"])) / max(1, oben - int(st["ab"]))
                n = int(round(bis - min(1.0, max(0.0, anteil)) * (bis - n)))
            cpd = int(st.get("crawls_per_day") or 1) if n == 1 else 1
            break
    if status == THIN and n < THIN_MIN_INTERVALL_TAGE:
        n, cpd = THIN_MIN_INTERVALL_TAGE, 1
    return {"pausiert": False, "intervall_tage": n, "crawls_per_day": cpd, "frequency_days": round(n / cpd, 2),
            "stufe_ab": int(wahl["ab"])}


def rate(e: Optional[Dict[str, Any]]) -> Optional[float]:
    """Abrufe je Tag einer Empfehlung/Wirkung (2x taeglich = 2.0, alle 3 Tage = 0.333)."""
    if not e:
        return None
    return int(e.get("crawls_per_day") or 1) / max(1, int(e.get("intervall_tage") or 1))


def reduktion_aktiv(wirkung: Optional[Dict[str, Any]], crawls_per_day: int) -> bool:
    w = wirkung or {}
    return bool(w.get("pausiert") or int(w.get("intervall_tage") or 1) > 1
                or (w.get("crawls_per_day") and int(w["crawls_per_day"]) < int(crawls_per_day or 1)))


# ---------------------------------------------------------------- ein Segment
def segment_health(seg: Dict[str, Any], docs: List[Dict[str, Any]], *, stichtag: str, cfg: Dict[str, Any],
                   letzter_gueltiger_tag_ausserhalb: Optional[str] = None, jetzt_iso: Optional[str] = None) -> Dict[str, Any]:
    """Health-Dokument (Abschnitt 42) eines Segments aus den Tagesdokumenten des Fensters — reine Rechnung.
    docs werden auf die aktuelle Fassung des Segments gefiltert (andere Fassung = eigene Zeitreihe)."""
    fass = (int(seg.get("version") or 1), seg.get("definition_hash"))
    eigene = sorted((d for d in docs if fassung(d, seg) == fass), key=lambda d: str(d["date"]))
    rows = int(seg.get("max_items") or 0) or konfig.rows_je_segment()
    cpd = max(1, min(4, int(seg.get("crawls_per_day") or 1)))
    wirkung = seg.get("safe_auto") if isinstance(seg.get("safe_auto"), dict) else None
    m = kennzahlen(eigene, rows_soll=rows, stichtag=stichtag, crawls_per_day=cpd,
                   letzter_gueltiger_tag_ausserhalb=letzter_gueltiger_tag_ausserhalb, wirkung=wirkung)
    score, komponenten, liq = activity(m)
    status, grund = health_bestimmen(m, score, wirkung=wirkung)
    reduziert = reduktion_aktiv(wirkung, cpd)
    aktuell = None
    if reduziert and not (wirkung or {}).get("pausiert"):
        aktuell = {"intervall_tage": int(wirkung.get("intervall_tage") or 1),
                   "crawls_per_day": min(cpd, int(wirkung.get("crawls_per_day") or cpd))}
    emp = empfehlung(status, score, cfg, aktuell=aktuell)
    doc = {k: v for k, v in m.items() if not k.startswith("_")}
    doc.update({
        "segment_id": seg["id"], "model_id": seg.get("model_id"), "version": fass[0], "definition_hash": fass[1],
        "label": seg.get("label"), "ez_label": seg.get("ez_label"), "km_label": seg.get("km_label"),
        "year_from": seg.get("year_from"), "min_km": seg.get("min_km"), "max_km": seg.get("max_km"),
        "crawls_per_day": cpd, "enabled": bool(seg.get("enabled", True)),
        "data_quality": data_quality_zusammen(m), "market_depth": market_depth_zusammen(m),
        "health": status, "health_grund": grund, "health_text": GRUND_TEXT.get(grund, grund),
        "confidence": confidence_bestimmen(m, status, stichtag),
        "activity_score": score if m.get("valid_runs") else None, "activity_komponenten": komponenten, "liquiditaet": liq,
        "recommended_frequency_days": (emp or {}).get("frequency_days"),
        "recommended_crawls_per_day": (emp or {}).get("crawls_per_day"),
        "recommended_intervall_tage": (emp or {}).get("intervall_tage"),
        "recommended_pause": bool((emp or {}).get("pausiert")), "empfehlung": emp,
        "safe_auto_wirkung": wirkung, "safe_auto_reduziert": reduziert,
        "tag": stichtag, "calculated_at": jetzt_iso or konfig.jetzt_iso(),
    })
    return doc


# ---------------------------------------------------------------- Modell-Health (Aggregat)
def modell_status(zaehler: Dict[str, int]) -> str:
    """Modell-Status aus den Segment-Status (nur bewertete, UNKNOWN zaehlt nicht): >= 50 % STALE -> STALE;
    >= 30 % UNSTABLE -> UNSTABLE; >= 50 % EMPTY -> EMPTY; >= 50 % EMPTY+THIN -> THIN; >= 30 % HOT -> HOT;
    >= 50 % HOT+HEALTHY -> HEALTHY; sonst NORMAL; ohne bewertetes Segment UNKNOWN."""
    n = sum(int(zaehler.get(s) or 0) for s in BEWERTET)
    if not n:
        return UNKNOWN

    def anteil(*st: str) -> float:
        return sum(int(zaehler.get(s) or 0) for s in st) / n
    if anteil(STALE) >= 0.5:
        return STALE
    if anteil(UNSTABLE) >= 0.3:
        return UNSTABLE
    if anteil(EMPTY) >= 0.5:
        return EMPTY
    if anteil(EMPTY, THIN) >= 0.5:
        return THIN
    if anteil(HOT) >= 0.3:
        return HOT
    if anteil(HOT, HEALTHY) >= 0.5:
        return HEALTHY
    return NORMAL


def modell_aggregat(modell: Dict[str, Any], docs: Iterable[Dict[str, Any]], *, stichtag: str,
                    jetzt_iso: Optional[str] = None) -> Dict[str, Any]:
    liste = list(docs)
    zaehler = {s: 0 for s in STATUS}
    for d in liste:
        zaehler[d.get("health") or UNKNOWN] = zaehler.get(d.get("health") or UNKNOWN, 0) + 1
    scores = [int(d["activity_score"]) for d in liste if d.get("activity_score") is not None and d.get("health") in BEWERTET]
    return {"model_id": modell.get("id"), "label": modell.get("label"), "version": int(modell.get("version") or 1),
            "segmente": len(liste), "segmente_bewertet": sum(zaehler[s] for s in BEWERTET), "zaehler": zaehler,
            "health": modell_status(zaehler), "activity_score_mittel": round(sum(scores) / len(scores), 1) if scores else None,
            "monatskosten_usd": round(sum(float(d.get("monatskosten_usd") or 0) for d in liste), 2),
            "tag": stichtag, "calculated_at": jetzt_iso or konfig.jetzt_iso()}


# ---------------------------------------------------------------- Datenbank
_PROJEKTION = {"_id": 0, "listings": 0, "disappeared_ids": 0, "price_reduced_ids": 0, "price_increase_ids": 0,
               "hot_deals": 0, "hot_deal_ids_tag": 0, "hot_deal_privat_ids_tag": 0, "hot_deal_neu_ids": 0}


async def modell_berechnen(db, modell: Dict[str, Any], *, stichtag: str, cfg: Dict[str, Any],
                           jetzt_iso: Optional[str] = None) -> Dict[str, Any]:
    """Health aller aktiven Segmente eines Suchauftrags (aktuelle Fassung) aus den gespeicherten Tageswerten rechnen,
    speichern (ein Dokument je Segment), Status-WECHSEL in die Historie, Modell-Aggregat speichern. Kein Abruf."""
    jetzt_iso = jetzt_iso or konfig.jetzt_iso()
    mid = modell["id"]
    segs = [s async for s in db[SEGMENTE].find({"model_id": mid, "enabled": True}, {"_id": 0})]
    ids = sorted(s["id"] for s in segs)
    von = _tag_minus(stichtag, FENSTER_TAGE - 1)
    je_seg: Dict[str, List[Dict[str, Any]]] = {i: [] for i in ids}
    alt: Dict[str, Dict[str, Any]] = {}
    ausserhalb: Dict[str, Optional[str]] = {}
    if ids:
        async for d in db[TAGESSTATS].find({"segment_id": {"$in": ids}, "date": {"$gte": von, "$lte": stichtag}}, _PROJEKTION):
            je_seg.setdefault(d["segment_id"], []).append(d)
        async for h in db[HEALTH].find({"segment_id": {"$in": ids}}, {"_id": 0, "segment_id": 1, "health": 1, "tag": 1}):
            alt[h["segment_id"]] = h
        async for st in db[SEGMENTSTATS].find({"_id": {"$in": ids}}, {"_id": 1, "letzter_gueltiger_lauf_at": 1}):
            lauf = st.get("letzter_gueltiger_lauf_at")
            z = speicher._zeitpunkt(lauf) if lauf else None
            ausserhalb[st["_id"]] = konfig.heute_tag(z) if z else None
    docs: Dict[str, Dict[str, Any]] = {}
    ops, hist = [], []
    for s in segs:
        letzter_aussen = ausserhalb.get(s["id"])
        if letzter_aussen and letzter_aussen > stichtag:
            letzter_aussen = None           # Stichtag in der Vergangenheit: spaetere Laeufe zaehlen nicht
        h = segment_health(s, je_seg.get(s["id"]) or [], stichtag=stichtag, cfg=cfg,
                           letzter_gueltiger_tag_ausserhalb=letzter_aussen, jetzt_iso=jetzt_iso)
        docs[s["id"]] = h
        vorher = alt.get(s["id"]) or {}
        if vorher.get("health") != h["health"]:
            h["health_seit"] = stichtag
            h["health_vorher"] = vorher.get("health")
            hist.append(UpdateOne({"segment_id": s["id"], "tag": stichtag, "von": vorher.get("health"), "nach": h["health"]},
                                  {"$setOnInsert": {"model_id": mid, "version": h["version"], "activity_score": h["activity_score"],
                                                    "grund": h["health_grund"], "confidence": h["confidence"], "at": jetzt_iso}},
                                  upsert=True))
        ops.append(UpdateOne({"segment_id": s["id"]}, {"$set": h}, upsert=True))
    if ops:
        await db[HEALTH].bulk_write(ops, ordered=False)
    if hist:
        try:
            await db[HEALTH_HISTORIE].bulk_write(hist, ordered=False)
        except Exception:  # noqa: BLE001 — Rennen zweier Laeufe am Unique-Index: der Wechsel steht schon da
            pass
    agg = modell_aggregat(modell, docs.values(), stichtag=stichtag, jetzt_iso=jetzt_iso)
    await db[MODELL_HEALTH].update_one({"model_id": mid}, {"$set": agg}, upsert=True)
    await asyncio.sleep(0)          # zwischen den Modellen den Event-Loop freigeben (Abschnitt 49)
    return {"modell": agg, "segmente": docs, "segs": {s["id"]: s for s in segs}}


async def aktive_modelle(db, model_ids: Optional[List[str]] = None) -> List[Dict[str, Any]]:
    """Suchauftraege mit aktiven Segmenten (nur diese werden bewertet)."""
    mids = [str(m) for m in await db[SEGMENTE].distinct("model_id", {"enabled": True}) if m]
    if model_ids is not None:
        erlaubt = {str(m) for m in model_ids}
        mids = [m for m in mids if m in erlaubt]
    if not mids:
        return []
    return await db[MODELLE].find({"id": {"$in": sorted(mids)}}, {"_id": 0}).sort("id", 1).to_list(len(mids))


# ---------------------------------------------------------------- Lesen (Berichte, Modellseite, Admin)
async def health_je_segment(db, segment_ids: Iterable[str]) -> Dict[str, Dict[str, Any]]:
    """Kompakter Health-Stand je Segment — fuer die Berichte (Phase E laesst dort eine Health-Spalte leer) und die
    Modellseite. Nur lesen."""
    ids = sorted({str(s) for s in segment_ids or [] if s})
    if not ids:
        return {}
    raus: Dict[str, Dict[str, Any]] = {}
    async for h in db[HEALTH].find({"segment_id": {"$in": ids}},
                                   {"_id": 0, "segment_id": 1, "health": 1, "health_grund": 1, "activity_score": 1, "confidence": 1,
                                    "recommended_frequency_days": 1, "recommended_pause": 1, "tag": 1, "calculated_at": 1}):
        raus[h["segment_id"]] = h
    return raus


async def modell_health(db, model_ids: Optional[Iterable[str]] = None) -> Dict[str, Dict[str, Any]]:
    """Modell-Health (Aggregat) je Suchauftrag — fuer Berichte, Modellseite, Uebersicht. Nur lesen."""
    filt: Dict[str, Any] = {}
    if model_ids is not None:
        filt["model_id"] = {"$in": sorted({str(m) for m in model_ids if m})}
    return {d["model_id"]: d async for d in db[MODELL_HEALTH].find(filt, {"_id": 0})}


async def segmente_eines_modells(db, model_id: str) -> List[Dict[str, Any]]:
    """Health-Dokumente aller Segmente eines Suchauftrags (aktive zuerst, dann EZ/km)."""
    docs = await db[HEALTH].find({"model_id": str(model_id)}, {"_id": 0}).to_list(2000)
    aktive = {s["id"] async for s in db[SEGMENTE].find({"model_id": str(model_id), "enabled": True}, {"_id": 0, "id": 1})}
    for d in docs:
        d["enabled"] = d["segment_id"] in aktive
    docs.sort(key=lambda d: (not d["enabled"], d.get("year_from") or 0, d.get("min_km") or 0, d["segment_id"]))
    return docs


async def historie(db, segment_id: str, limit: int = 100) -> List[Dict[str, Any]]:
    return await db[HEALTH_HISTORIE].find({"segment_id": str(segment_id)}, {"_id": 0}).sort([("tag", -1), ("at", -1)]).to_list(limit)


def schwellen() -> Dict[str, Any]:
    """Die geltenden Schwellen fuer die Oberflaeche (nachvollziehbar, nicht versteckt)."""
    return {"fenster_tage": FENSTER_TAGE, "min_laeufe_bewertung": MIN_LAEUFE_BEWERTUNG, "min_laeufe_empty": MIN_LAEUFE_EMPTY,
            "empty_anteil": EMPTY_ANTEIL, "thin_max_zeilen": THIN_MAX_ZEILEN, "unstable_ungueltig_anteil": UNSTABLE_UNGUELTIG_ANTEIL,
            "unstable_volatilitaet_pct": UNSTABLE_VOLATILITAET_PCT, "stale_puffer_tage": STALE_PUFFER_TAGE,
            "hot_ab_score": HOT_AB_SCORE, "healthy_ab_score": HEALTHY_AB_SCORE, "healthy_min_fuellung": HEALTHY_MIN_FUELLUNG,
            "gewichte": dict(GEWICHTE), "thin_min_intervall_tage": THIN_MIN_INTERVALL_TAGE, "hysterese_punkte": HYSTERESE_PUNKTE,
            "confidence_hoch_tage": CONFIDENCE_HOCH_TAGE, "erwartet_puffer_anteil": ERWARTET_PUFFER_ANTEIL,
            "min_laeufe_unter_wirkung": MIN_LAEUFE_UNTER_WIRKUNG}
