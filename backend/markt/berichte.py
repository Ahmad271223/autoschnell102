# -*- coding: utf-8 -*-
"""Berichte je Marktmodell: 5 Tage, 15 Tage, Monat (Master-Auftrag Ahmad 26.09.2026, Phase E —
Abschnitte 12-21, 34-39, 46, 50-55).

Grundsaetze:
  * nur aus gespeicherten Daten (market_segment_daily_stats inkl. Hot-Deal-Zusammenfassung des Tages,
    market_segments, market_models, market_hot_deal_events) — NIE ein Marktabruf, Kosten 0 (Architekturtest
    test_b02); das Kostenfeld reporting_cost der Prognose bleibt 0
  * Preisbasis nur Tage mit Datenqualitaet GOOD/MEDIUM (Altdaten ueber speicher.qualitaet_aus_doc); Tage mit nur
    ungueltigen Laeufen sind KEINE Marktluecke (sie zaehlen nur bei Datenqualitaet und Kosten)
  * nie verschiedene Fassungen (version/definition_hash) zusammenrechnen: gerechnet wird die Fassung des letzten
    gueltigen Tages im Zeitraum; fruehere Fassungen im Zeitraum stehen getrennt (neue Zeitreihe, Abschnitt 34)
  * Modellwerte nie ungewichtet ueber alle Segmente (Abschnitt 12): Tagesniveau = mit der Stichprobengroesse
    gewichteter Mittelwert der Segment-Mediane; Start/Ende und Differenz ueber denselben Segmentkorb (je Segment
    erster und letzter gueltiger Tag im Zeitraum); Tagesbewegung nur ueber Segmente mit gueltigem Wert an BEIDEN
    aufeinanderfolgenden Kalendertagen — keine doppelte Interpretation fehlender Tage, keine Interpolation; fehlt an
    einem Tag ein Segment des Korbs technisch (ungueltiger oder kein Lauf), ist der Tag 'Teilabdeckung' und zaehlt
    nicht fuer Median/Mittel/Minimum/Maximum der Periode (sonst waere ein Ausfall ein Markteinbruch)
  * getrennt ausgewiesen (Abschnitt 54): sample_market_change (Aenderung der taeglichen Stichprobe inkl. Mix) und
    same_listing_price_change (Preisaenderung derselben Inserate); Preissenkungen/-erhoehungen nur je listing_id
  * eingefroren (Abschnitte 35/36): ein finaler Bericht wird einmal gespeichert (market_model_reports, Unique je
    model_id/typ/periode_von/periode_bis) und danach nie mehr geaendert — kein Codepfad aktualisiert ihn; die
    laufende Periode wird nur 'vorlaeufig' live gerechnet und nie gespeichert. Eine spaetere Korrektur waere eine
    neue Revision (Entscheidung offen, siehe Bericht an Ahmad)
  * kompakt: ein Bericht je Modell und Periode; Segmentdetails als Kennzahlen, Hot Deals nur mit IDs/Preisen/Raengen

Perioden (Europe/Berlin, kalenderkorrekt 28/29/30/31 Tage):
  FIVE_DAY    01.-05., 06.-10., 11.-15., 16.-20., 21.-25., 26.-Monatsende
  FIFTEEN_DAY 01.-15. (der "grosse Bericht" am 15., Abschnitt 16) und 16.-Monatsende. Die zweite Haelfte ist die
              Entsprechung fuer das Monatsende: zusammen decken beide jeden Kalendertag genau einmal ab, beide bestehen
              aus ganzen 5-Tage-Bloecken (01-05/06-10/11-15 bzw. 16-20/21-25/26-Ende) und sind damit blockweise
              vergleichbar — ein rollendes 15-Tage-Fenster waere gegen Abschnitt 13
  MONTHLY     01.-Monatsende
Final erst BERICHT_KARENZ_STUNDEN nach Periodenende (deutsche Zeit): die letzten Laeufe eines Tages starten vor 23:30,
ein Buendel darf bis knapp eine Stunde laufen (Lease), ein Ergebnis kann waehrend der Sicherung (03:00) zwischen-
gespeichert werden und wird danach ausgewertet, und die Hot-Deal-Auswertung des letzten Tages laeuft danach im
Hintergrund. 6 Stunden decken das ab; stehen dann noch offene Hot-Deal-Auswertungen im Zeitraum, wartet der Bericht
bis zu HOTDEAL_WARTEN_STUNDEN weiter und friert danach mit Hinweis ein.
"""
from __future__ import annotations

import asyncio
import calendar
import logging
import statistics
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional, Tuple

from pymongo.errors import DuplicateKeyError, OperationFailure

from markt import deals, konfig, speicher
from markt.konfig import BERICHTE, HOTDEAL_EREIGNISSE, MODELLE, SEGMENTE, TAGESSTATS

log = logging.getLogger(__name__)

TYPEN = ("FIVE_DAY", "FIFTEEN_DAY", "MONTHLY")
FUENF_TAGE_BLOECKE = ((1, 5), (6, 10), (11, 15), (16, 20), (21, 25), (26, 31))
FUENFZEHN_TAGE = ((1, 15), (16, 31))
# Abschnitt 14: Stabilitaetszone -0,5 % bis +0,5 % -> STABLE. Konstante (keine Umgebungsvariable); jeder Bericht
# speichert die verwendete Schwelle (stabil_zone_pct), damit er auch nach einer Aenderung reproduzierbar bleibt.
STABIL_PCT = 0.5
BERICHT_KARENZ_STUNDEN = 6
HOTDEAL_WARTEN_STUNDEN = 24
NACHHOL_TAGE = 62                  # faellige Perioden der letzten ~2 Monate werden nachgeholt (Worker-Ausfall)
VORLAUF_TAGE = 14                  # Tage vor Periodenbeginn: Vortag des ersten Tages, Vorpreise fuer Preisaenderungen
PERZENTIL_MIN_STICHPROBE = 4       # P25/P75 nur, wenn ALLE Basis-Segmente des Tages mind. 4 Angebote haben (Abschnitt 15)
SEGMENT_ABDECKUNG_HOCH = 0.8       # Confidence HIGH erst ab 80 % gueltiger Segment-Tage (Abschnitte 52/53, Befund B5)
SEGMENT_ABDECKUNG_MITTEL = 0.5
HOT_TOP_MAX = 10
SCHEMA = 1
BERICHTE_DOK = "berichte"          # market_config/berichte: erledigte Perioden + letzter Lauf
INDEX_REF = "market_model_reports.markt_bericht_je_periode"
PERIODEN_INDEX = "markt_bericht_periode"   # (typ, periode_von, periode_bis) — deckt die Periodenliste ab (Befunde B12/B13)
RICHTUNGEN = ("FALLING", "RISING", "STABLE", "UNKNOWN")
HINWEIS = ("Beobachtet wird je Segment nur die günstige Marktzone (die N günstigsten Angebote) — kein Marktwert. "
           "Modellwerte sind nach Stichprobengröße gewichtete Mittel der Segment-Mediane derselben Fassung; Lücken werden "
           "nicht aufgefüllt. Aus gespeicherten Tageswerten, keine Zusatzabrufe.")


# ---------------------------------------------------------------- Perioden
def _d(jahr: int, monat: int, tag: int) -> str:
    return f"{jahr:04d}-{monat:02d}-{tag:02d}"


def _datum(tag: str) -> datetime:
    return datetime.strptime(str(tag)[:10], "%Y-%m-%d")


def _plus(tag: str, tage: int) -> str:
    return (_datum(tag) + timedelta(days=tage)).strftime("%Y-%m-%d")


def tage_zwischen(von: str, bis: str) -> List[str]:
    n = (_datum(bis) - _datum(von)).days
    return [_plus(von, i) for i in range(max(0, n) + 1)]


def perioden_im_monat(jahr: int, monat: int) -> List[Tuple[str, str, str]]:
    ende = calendar.monthrange(jahr, monat)[1]
    raus = [("FIVE_DAY", _d(jahr, monat, a), _d(jahr, monat, min(b, ende))) for a, b in FUENF_TAGE_BLOECKE]
    raus += [("FIFTEEN_DAY", _d(jahr, monat, a), _d(jahr, monat, min(b, ende))) for a, b in FUENFZEHN_TAGE]
    raus.append(("MONTHLY", _d(jahr, monat, 1), _d(jahr, monat, ende)))
    return raus


def periode_gueltig(typ: str, von: str, bis: str) -> bool:
    try:
        d = _datum(von)
    except (TypeError, ValueError):
        return False
    return (typ, von, bis) in perioden_im_monat(d.year, d.month)


def faellig_ab(bis: str) -> datetime:
    """Zeitpunkt (UTC), ab dem die Periode final erstellt wird: Folgetag 00:00 deutscher Zeit + Karenz."""
    ende = _datum(bis).replace(tzinfo=konfig.ZEITZONE) + timedelta(days=1, hours=BERICHT_KARENZ_STUNDEN)
    return ende.astimezone(timezone.utc)


def ist_faellig(bis: str, jetzt: Optional[datetime] = None) -> bool:
    return (jetzt or konfig.jetzt()) >= faellig_ab(bis)


def _monate(von_tag: str, bis_tag: str) -> List[Tuple[int, int]]:
    a, b = _datum(von_tag), _datum(bis_tag)
    raus, j, m = [], a.year, a.month
    while (j, m) <= (b.year, b.month):
        raus.append((j, m))
        j, m = (j + 1, 1) if m == 12 else (j, m + 1)
    return raus


def faellige_perioden(jetzt: Optional[datetime] = None, nachhol_tage: int = NACHHOL_TAGE) -> List[Tuple[str, str, str]]:
    jetzt = jetzt or konfig.jetzt()
    heute = konfig.heute_tag(jetzt)
    grenze = _plus(heute, -nachhol_tage)
    raus = []
    for j, m in _monate(grenze, heute):
        raus += [p for p in perioden_im_monat(j, m) if p[2] >= grenze and ist_faellig(p[2], jetzt)]
    return raus


def laufende_perioden(jetzt: Optional[datetime] = None) -> List[Dict[str, Any]]:
    """Begonnene, noch nicht faellige Perioden (aktueller und voriger Monat) — nur 'vorlaeufig' live rechenbar."""
    jetzt = jetzt or konfig.jetzt()
    heute = konfig.heute_tag(jetzt)
    raus = []
    for j, m in _monate(_plus(heute, -31), heute):
        for typ, von, bis in perioden_im_monat(j, m):
            if von <= heute and not ist_faellig(bis, jetzt):
                raus.append({"typ": typ, "von": von, "bis": bis, "faellig_ab": faellig_ab(bis).isoformat()})
    return raus


# ---------------------------------------------------------------- reine Rechnung
def richtung(pct: Optional[float], zone: float = STABIL_PCT) -> str:
    if pct is None:
        return "UNKNOWN"
    if pct < -zone:
        return "FALLING"
    if pct > zone:
        return "RISING"
    return "STABLE"


def _r(x: Optional[float], n: int = 2) -> Optional[float]:
    return None if x is None else round(float(x), n)


def _gewichtet(paare: List[Tuple[float, float]]) -> Optional[float]:
    g = sum(w for _, w in paare)
    return (sum(v * w for v, w in paare) / g) if paare and g > 0 else None


def _n(d: Dict[str, Any]) -> int:
    return int(d.get("sample_size") or 0)


def _ids(d: Dict[str, Any], feld: str) -> set:
    return {str(x) for x in (d.get(feld) or [])}


def _hot_ids(d: Dict[str, Any]) -> set:
    return _ids(d, "hot_deal_ids_tag") or {str(x) for x in ((d.get("hot_deals") or {}).get("ids") or [])}


def _hot_privat_ids(d: Dict[str, Any]) -> set:
    return _ids(d, "hot_deal_privat_ids_tag") or {str(x) for x in ((d.get("hot_deals") or {}).get("privat_ids") or [])}


def _preise(d: Optional[Dict[str, Any]]) -> Dict[str, float]:
    return {str(z["listing_id"]): float(z["price"]) for z in ((d or {}).get("listings") or []) if z.get("listing_id") and z.get("price") is not None}


def niveau(basis: List[Dict[str, Any]]) -> Dict[str, Any]:
    """Modellwert eines Tages aus den gueltigen Segmenten: Median-Niveau gewichtet mit der Stichprobe, Minimum =
    guenstigstes beobachtetes Angebot. P25/P75 ueber DIESELBE Segmentmenge und dieselben Gewichte wie der Median
    (Befund B6): erfuellt ein Basis-Segment die Mindeststichprobe nicht, bleiben beide leer (Luecke) — sonst
    beziehen sich Median und Quartile auf verschiedene Fahrzeugmengen und P25 kann ueber dem Median liegen."""
    if not basis:
        return {"median": None, "min": None, "p25": None, "p75": None, "listings": 0, "segmente": 0}
    med = _gewichtet([(float(d["median_price"]), _n(d)) for d in basis])
    zonen = all(_n(d) >= PERZENTIL_MIN_STICHPROBE and d.get("p25_price") is not None and d.get("p75_price") is not None for d in basis)
    mins = [float(d["min_price"]) for d in basis if d.get("min_price") is not None]
    return {"median": _r(med), "min": min(mins) if mins else None,
            "p25": _r(_gewichtet([(float(d["p25_price"]), _n(d)) for d in basis])) if zonen else None,
            "p75": _r(_gewichtet([(float(d["p75_price"]), _n(d)) for d in basis])) if zonen else None,
            "listings": sum(_n(d) for d in basis), "segmente": len(basis)}


def tagesbewegung(heute: Dict[str, Dict[str, Any]], gestern: Dict[str, Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    """Bewegung zum VORTAG nur ueber Segmente mit gueltigem Wert an beiden Tagen (gewichtet) — ohne gemeinsame
    Segmente keine Bewegung (None). EUR und % kommen aus DEMSELBEN Aggregat (Befund B4): eur = Summe w*(b-a) /
    Summe w, pct = Summe w*(b-a) / Summe w*a — beide haben damit immer dasselbe Vorzeichen (Klasse, Summen,
    Extreme und Farbe widersprechen sich nie, auch bei Segmenten mit sehr verschiedenem Preisniveau)."""
    gemeinsam = sorted(set(heute) & set(gestern))
    paare = []
    for s in gemeinsam:
        a, b = float(gestern[s]["median_price"]), float(heute[s]["median_price"])
        if not a:
            continue
        paare.append((b - a, a, (_n(gestern[s]) + _n(heute[s])) / 2))
    gewicht = sum(w for _, _, w in paare)
    grundwert = sum(a * w for _, a, w in paare)
    if not paare or gewicht <= 0 or grundwert <= 0:
        return None
    diff = sum(d * w for d, _, w in paare)
    return {"eur": _r(diff / gewicht), "pct": _r(diff / grundwert * 100, 3), "segmente": len(paare)}


def _qualitaet_zusammen(z: Dict[str, int]) -> str:
    n = sum(z.values())
    if not n:
        return "UNKNOWN"
    if (z.get("POOR", 0) + z.get("UNKNOWN", 0)) / n > 0.2:
        return "POOR"
    if z.get("GOOD", 0) / n >= 0.8:
        return "GOOD"
    return "MEDIUM"


TIEFE_SCHWERE = {"EMPTY": 4, "THIN": 3, "UNKNOWN": 2, "NORMAL": 1, "FULL": 0}


def _tiefe_zusammen(z: Dict[str, int]) -> str:
    if not z:
        return "UNKNOWN"
    return max(z.items(), key=lambda kv: (kv[1], TIEFE_SCHWERE.get(kv[0], 0)))[0]


def confidence(abdeckung: int, erwartet: int, dq: Dict[str, int], tiefe: Dict[str, int], inserate: int,
               segment_tage: Optional[int] = None, segment_tage_erwartet: Optional[int] = None) -> Tuple[str, List[str]]:
    """Abschnitt 53, regelbasiert: HIGH ohne Einschraenkung; MEDIUM bei >= 50 % Abdeckung (Kalendertage UND
    Segment-Tage), >= 70 % brauchbarer Qualitaet und >= 5 Inseraten; sonst LOW. Die Gruende stehen dabei.
    Segment-Tage (Befund B5): gueltige Segment-Tage / (Segmente der Fassung x erwartete Tage) — ein Tag, an dem
    nur eines von 24 Segmenten geliefert hat, ist kein voll abgedeckter Modelltag."""
    anteil = abdeckung / erwartet if erwartet else 0.0
    seg_anteil = (segment_tage or 0) / segment_tage_erwartet if segment_tage_erwartet else None
    n = sum(dq.values()) or 1
    brauchbar = (dq.get("GOOD", 0) + dq.get("MEDIUM", 0)) / n
    gut = dq.get("GOOD", 0) / n
    duenn = ((tiefe.get("THIN", 0) + tiefe.get("EMPTY", 0)) / sum(tiefe.values())) if tiefe else 1.0
    gruende = []
    if anteil < 0.8:
        gruende.append(f"Abdeckung {abdeckung}/{erwartet} Tage")
    if seg_anteil is not None and seg_anteil < SEGMENT_ABDECKUNG_HOCH:
        gruende.append(f"Segmentabdeckung {segment_tage or 0}/{segment_tage_erwartet} Segment-Tage")
    if brauchbar < 0.9 or gut < 0.7:
        gruende.append("Datenqualität eingeschränkt")
    if inserate < 10:
        gruende.append("wenige verschiedene Inserate")
    if duenn >= 0.5:
        gruende.append("überwiegend dünner/leerer Markt")
    if not gruende:
        return "HIGH", []
    if anteil >= 0.5 and (seg_anteil is None or seg_anteil >= SEGMENT_ABDECKUNG_MITTEL) and brauchbar >= 0.7 and inserate >= 5:
        return "MEDIUM", gruende
    return "LOW", gruende


def bewegung_statistik(tage: List[str], bewegungen: Dict[str, Optional[Dict[str, Any]]], zone: float = STABIL_PCT) -> Dict[str, Any]:
    """Abschnitte 19/20: fallende/steigende/stabile Tage (nur direkt vergleichbare Vortage), Summen, Extreme,
    laengste Serien (ein Tag ohne Vergleich unterbricht die Serie), Volatilitaet (Standardabweichung der
    Tagesaenderungen in %)."""
    werte = [(t, bewegungen[t]) for t in tage if bewegungen.get(t)]
    klassen = {t: richtung(b["pct"], zone) for t, b in werte}
    n = len(werte)
    zaehler = {k: sum(1 for v in klassen.values() if v == k) for k in ("FALLING", "RISING", "STABLE")}
    neg = [(t, b) for t, b in werte if b["eur"] < 0]
    pos = [(t, b) for t, b in werte if b["eur"] > 0]
    # Extreme in % nur aus Tagen mit passendem %-Vorzeichen (Befund B4: ein 'staerkster Rueckgang' ist nie positiv)
    neg_pct = [(t, b) for t, b in werte if b["pct"] is not None and b["pct"] < 0]
    pos_pct = [(t, b) for t, b in werte if b["pct"] is not None and b["pct"] > 0]

    def _extrem(liste, feld, fn):
        if not liste:
            return None
        t, b = fn(liste, key=lambda x: x[1][feld])
        return {"date": t, "eur": b["eur"], "pct": b["pct"]}

    def _serie(ziel):
        best = cur = 0
        for t in tage:
            if klassen.get(t) == ziel:
                cur += 1
                best = max(best, cur)
            else:
                cur = 0
        return best
    pcts = [b["pct"] for _, b in werte]
    return {"vergleiche": n, "fallend": zaehler["FALLING"], "steigend": zaehler["RISING"], "stabil": zaehler["STABLE"],
            "fallend_pct": _r(zaehler["FALLING"] / n * 100, 1) if n else None,
            "steigend_pct": _r(zaehler["RISING"] / n * 100, 1) if n else None,
            "stabil_pct": _r(zaehler["STABLE"] / n * 100, 1) if n else None,
            "summe_negativ_eur": _r(sum(b["eur"] for _, b in neg)) if n else None,
            "summe_positiv_eur": _r(sum(b["eur"] for _, b in pos)) if n else None,
            "netto_eur": _r(sum(b["eur"] for _, b in werte)) if n else None,
            "staerkster_rueckgang_eur": _extrem(neg, "eur", min), "staerkster_rueckgang_pct": _extrem(neg_pct, "pct", min),
            "staerkster_anstieg_eur": _extrem(pos, "eur", max), "staerkster_anstieg_pct": _extrem(pos_pct, "pct", max),
            "laengste_fallserie": _serie("FALLING"), "laengste_steigeserie": _serie("RISING"),
            "volatilitaet_pct": _r(statistics.pstdev(pcts), 3) if len(pcts) >= 2 else None}


class _Daten:
    """Tagesdokumente eines Modells, nach Fassung getrennt und je Segment/Tag indiziert."""

    def __init__(self, segs: Dict[str, Dict[str, Any]], docs: List[Dict[str, Any]], von: str, bis: str, heute: Optional[str] = None):
        self.segs, self.von, self.bis = segs, von, bis
        self.alle = docs
        im_zeitraum = [d for d in docs if von <= d["date"] <= bis]
        self.im_zeitraum = im_zeitraum
        gueltige = [d for d in im_zeitraum if deals.ist_gueltig(d)]
        kandidaten = gueltige or im_zeitraum
        self.haupt = self.fassung(max(kandidaten, key=lambda d: (d["date"], str(d.get("observed_at") or "")))) if kandidaten else None
        self.idx: Dict[str, Dict[str, Dict[str, Any]]] = {}
        for d in docs:
            if self.fassung(d) == self.haupt:
                self.idx.setdefault(d["segment_id"], {})[d["date"]] = d
        eigene = [d["date"] for s in self.idx.values() for d in s.values() if von <= d["date"] <= bis]
        self.fassung_ab = min(eigene) if eigene else von
        # Fassungswechsel im Zeitraum: die gerechnete Zeitreihe beginnt erst mit der neuen Fassung — erwartet werden
        # nur deren Tage; ohne Wechsel zaehlt jeder Kalendertag der Periode (auch vor dem ersten Lauf)
        self.wechsel = any(self.fassung(d) != self.haupt for d in im_zeitraum)
        self.erwartet_ab = self.fassung_ab if self.wechsel else von
        mit_daten = {sid for sid, s in self.idx.items() if any(von <= t <= bis for t in s)}
        self.segmente_fassung = {sid for sid, s in segs.items()
                                 if deals.fassung({}, s) == self.haupt and s.get("enabled")} | mit_daten
        # Befund B17: im vorlaeufigen Bericht (heute gesetzt) sind kuenftige Tage 'offen' — weder erwartet noch
        # Luecke. Heute zaehlt erst, wenn fuer heute schon ein Lauf gespeichert ist (Laeufe verteilen sich ueber den Tag).
        self.offen_ab: Optional[str] = None
        if heute:
            self.offen_ab = _plus(heute, 1) if any(d["date"] == heute for d in im_zeitraum) else heute
        # Befund B3 (fester Segmentkorb): je Segment die Tage mit gueltiger Beobachtung (inkl. EMPTY und Vorlauf),
        # der erste davon und der letzte Tag mit irgendeinem Tagesdokument — fuer fehlende()
        self.gueltig_je_seg = {sid: {t for t, d in s.items() if deals.ist_gueltig(d)} for sid, s in self.idx.items()}
        self.erster_gueltig = {sid: min(g) for sid, g in self.gueltig_je_seg.items() if g}
        self.letzter_doc = {sid: max(s) for sid, s in self.idx.items() if s}

    def fassung(self, d: Dict[str, Any]) -> Tuple[int, Optional[str]]:
        return deals.fassung(d, self.segs.get(d["segment_id"]))

    def docs_tag(self, tag: str) -> List[Dict[str, Any]]:
        return [s[tag] for s in self.idx.values() if tag in s]

    def basis_tag(self, tag: str) -> Dict[str, Dict[str, Any]]:
        return {d["segment_id"]: d for d in self.docs_tag(tag) if deals.ist_basis(d)}

    def offen(self, tag: str) -> bool:
        return self.offen_ab is not None and tag >= self.offen_ab

    def erwartete_tage(self, tage: List[str]) -> List[str]:
        """Erwartete Kalendertage: ab Beginn der gerechneten Fassung, ohne offene (kuenftige) Tage."""
        return [t for t in tage if t >= self.erwartet_ab and not self.offen(t)]

    def korb(self, tage_set: set) -> set:
        """Fester Segmentkorb eines Zeitraums: Segmente der Fassung mit mindestens einem Basistag darin."""
        return {sid for sid, s in self.idx.items() if any(t in tage_set and deals.ist_basis(d) for t, d in s.items())}

    def fehlende(self, tag: str, korb: set) -> int:
        """Segmente des Korbs, die an diesem Tag TECHNISCH fehlen (Befund B3): vorher schon gueltig beobachtet (auch
        im Vorlauf), noch aktiv (eingeschaltet oder spaeter noch ein Tagesdokument) und heute ohne gueltigen Lauf —
        kein Tagesdokument (endgueltig gescheiterter Abruf) oder nur ungueltig/POOR. EMPTY ist eine gueltige
        Beobachtung und fehlt nie; ein neu hinzukommendes Segment (vorher nie gueltig) ist Mix, keine Luecke."""
        n = 0
        for sid in korb:
            if tag in self.gueltig_je_seg.get(sid, ()):
                continue
            erster = self.erster_gueltig.get(sid)
            if erster is None or erster >= tag:
                continue
            if not (self.segs.get(sid) or {}).get("enabled") and self.letzter_doc.get(sid, "") < tag:
                continue
            n += 1
        return n


def _preisaenderungen(daten: _Daten, tage: List[str], feld: str) -> Tuple[int, int, Optional[float]]:
    """(Ereignisse, verschiedene Inserate, mittlere Aenderung EUR) — nur dieselbe listing_id im selben Segment;
    der Vorpreis kommt aus dem letzten frueheren Tagesdokument des Segments (auch aus dem Vorlauf)."""
    ereignisse, inserate, betraege = 0, set(), []
    for seg_id, tage_docs in daten.idx.items():
        letzter: Dict[str, float] = {}
        for t in sorted(tage_docs):
            d = tage_docs[t]
            preise = _preise(d)
            if t in tage and deals.ist_gueltig(d):
                for lid in _ids(d, feld):
                    ereignisse += 1
                    inserate.add(lid)
                    if lid in letzter and lid in preise:
                        betraege.append(preise[lid] - letzter[lid])
            letzter.update(preise)
    return ereignisse, len(inserate), (_r(sum(betraege) / len(betraege)) if betraege else None)


def kennzahlen(daten: _Daten, tage: List[str], kosten_docs: List[Dict[str, Any]], zone: float = STABIL_PCT) -> Dict[str, Any]:
    """Kennzahlen eines Zeitraums (Periode oder 5-Tage-Block) — nur Tage der gerechneten Fassung."""
    tage_set = set(tage)
    docs = [d for s in daten.idx.values() for t, d in s.items() if t in tage_set]
    gueltig = [d for d in docs if deals.ist_gueltig(d)]
    basis = [d for d in gueltig if deals.ist_basis(d)]
    # Segmentkorb: je Segment erster und letzter gueltiger Tag im Zeitraum (mind. zwei verschiedene Tage)
    korb = []
    for seg_id, tage_docs in daten.idx.items():
        bt = sorted(t for t, d in tage_docs.items() if t in tage_set and deals.ist_basis(d))
        if len(bt) >= 2:
            w = sum(_n(tage_docs[t]) for t in bt) / len(bt)
            korb.append((tage_docs[bt[0]], tage_docs[bt[-1]], w))
    start = _gewichtet([(float(a["median_price"]), w) for a, _, w in korb])
    ende = _gewichtet([(float(b["median_price"]), w) for _, b, w in korb])
    delta_eur = (ende - start) if (start is not None and ende is not None) else None
    delta_pct = (delta_eur / start * 100) if (delta_eur is not None and start) else None
    # gleiche Inserate (Abschnitt 54): Preis am ersten und letzten gueltigen Tag desselben Segments
    paare = []
    for a, b, _ in korb:
        pa, pb = _preise(a), _preise(b)
        paare += [(pa[lid], pb[lid]) for lid in set(pa) & set(pb)]
    gleich_eur = _r(sum(y - x for x, y in paare) / len(paare)) if paare else None
    gleich_pct = _r(sum(y - x for x, y in paare) / sum(x for x, _ in paare) * 100, 3) if paare and sum(x for x, _ in paare) else None
    # Tagesniveaus (Mix inkl.) fuer Median/Mittel/Min/Max und sample_market_change — nur Tage mit vollem
    # Segmentkorb (Befund B3): fehlt ein Segment technisch (ungueltiger oder kein Lauf), ist der Tageswert ein
    # anderer Segment-Mix und kein Marktsignal. Solche Tage bleiben in der Tagestabelle (markiert), zaehlen hier nicht.
    korb_ids = daten.korb(tage_set)
    niveaus = [(t, niveau(list(daten.basis_tag(t).values()))) for t in tage]
    alle_mit = [(t, n) for t, n in niveaus if n["median"] is not None]
    teil = {t for t, _ in alle_mit if daten.fehlende(t, korb_ids)}
    mit = [(t, n) for t, n in alle_mit if t not in teil]
    werte = [n["median"] for _, n in mit]
    smc_eur = _r(mit[-1][1]["median"] - mit[0][1]["median"]) if len(mit) >= 2 else None
    smc_pct = _r((mit[-1][1]["median"] - mit[0][1]["median"]) / mit[0][1]["median"] * 100, 3) if len(mit) >= 2 and mit[0][1]["median"] else None
    tief = min(mit, key=lambda x: x[1]["median"]) if mit else None
    hoch = max(mit, key=lambda x: x[1]["median"]) if mit else None
    # guenstigstes Angebot = echte Einzelbeobachtung, auch an teilabgedeckten Tagen (ein fehlendes Segment verbirgt
    # hoechstens ein Angebot, erfindet keins)
    billig = min(((t, n["min"]) for t, n in alle_mit if n["min"] is not None), key=lambda x: x[1], default=None)
    # Markt-Aktivitaet (nur gueltige Tage; neu/verschwunden nur mit Vergleichstag)
    mit_vergleich = [d for d in gueltig if d.get("vergleich_vortag")]
    neu = set().union(*[_ids(d, "new_in_sample_ids") for d in mit_vergleich]) if mit_vergleich else set()
    weg_ids = set().union(*[_ids(d, "disappeared_ids") for d in mit_vergleich]) if mit_vergleich else set()
    weg_n = len(weg_ids) or sum(int(d.get("disappeared_count") or 0) for d in mit_vergleich)
    senk_n, senk_inserate, senk_mittel = _preisaenderungen(daten, tage, "price_reduced_ids")
    erh_n, erh_inserate, erh_mittel = _preisaenderungen(daten, tage, "price_increase_ids")
    inserate = set().union(*[(_ids(d, "listing_ids_alle") or _ids(d, "listing_ids")) for d in gueltig]) if gueltig else set()
    hot = set().union(*[_hot_ids(d) for d in docs]) if docs else set()
    hot_privat = set().union(*[_hot_privat_ids(d) for d in docs]) if docs else set()
    hot_neu = set().union(*[_ids(d, "hot_deal_neu_ids") for d in docs]) if docs else set()
    dq: Dict[str, int] = {}
    for d in docs:
        q = deals.qualitaet(d)
        dq[q] = dq.get(q, 0) + 1
    tiefe: Dict[str, int] = {}
    for d in gueltig:
        t = speicher.qualitaet_aus_doc(d)["market_depth"]
        tiefe[t] = tiefe.get(t, 0) + 1
    abgedeckt = sorted({d["date"] for d in gueltig})
    erwartete = set(daten.erwartete_tage(tage))
    erwartet = len(erwartete)
    # Segmentabdeckung (Befund B5): gueltige Segment-Tage gegen Segmente der Fassung x erwartete Tage
    segment_tage = sum(1 for d in gueltig if d["date"] in erwartete)
    segment_tage_erwartet = len(daten.segmente_fassung) * erwartet
    conf, conf_gruende = confidence(len(abgedeckt), erwartet, dq, tiefe, len(inserate), segment_tage, segment_tage_erwartet)
    segs_mit = {d["segment_id"] for d in gueltig}
    leere_segmente = sum(1 for s in segs_mit if not any(d["segment_id"] == s for d in basis))
    kosten = round(sum(float(d.get("crawl_cost_usd") or 0) for d in kosten_docs if d["date"] in tage_set), 4)
    # Kosteneffizienz (Abschnitt 38, Befund B7): Zaehler und Nenner auf derselben Basis — Kosten der gerechneten
    # Fassung (inkl. ihrer ungueltigen Laeufe) je gueltiger Beobachtung (inkl. EMPTY wie deals.ist_gueltig), je
    # Inserat und je Hot Deal derselben Fassung. kosten_usd bleibt die reale Ausgabe ueber alle Fassungen.
    kosten_fassung = round(sum(float(d.get("crawl_cost_usd") or 0) for d in docs), 4)
    beobachtungen = len(gueltig)
    return {
        "startwert": _r(start), "endwert": _r(ende), "delta_eur": _r(delta_eur), "delta_pct": _r(delta_pct, 3),
        "richtung": richtung(delta_pct, zone), "korb_segmente": len(korb),
        "sample_market_change_eur": smc_eur, "sample_market_change_pct": smc_pct,
        "same_listing_price_change_eur": gleich_eur, "same_listing_price_change_pct": gleich_pct, "same_listing_anzahl": len(paare),
        "median_periode": _r(statistics.median(werte)) if werte else None, "mittelwert_periode": _r(sum(werte) / len(werte)) if werte else None,
        "minimum": {"date": tief[0], "wert": tief[1]["median"]} if tief else None,
        "maximum": {"date": hoch[0], "wert": hoch[1]["median"]} if hoch else None,
        "guenstigstes_angebot": {"date": billig[0], "preis": billig[1]} if billig else None,
        "neue_listings": len(neu), "verschwundene_listings": weg_n,
        "preissenkungen": senk_n, "reduzierte_listings": senk_inserate, "mittlere_senkung_eur": senk_mittel,
        "preiserhoehungen": erh_n, "erhoehte_listings": erh_inserate, "mittlere_erhoehung_eur": erh_mittel,
        "unterschiedliche_listings": len(inserate),
        "top3_wechsel": sum(1 for d in gueltig if d.get("top3_changed") is True),
        "top5_wechsel": sum(1 for d in gueltig if d.get("top5_changed") is True),
        "hot_deals": len(hot), "private_hot_deals": len(hot_privat), "hot_deals_neu": len(hot_neu),
        "data_quality": _qualitaet_zusammen(dq), "data_quality_zaehler": dq,
        "market_depth": _tiefe_zusammen(tiefe), "market_depth_zaehler": tiefe,
        "liquiditaet": deals.liquiditaet_bewerten(gueltig).get("stufe"),
        "coverage_days": len(abgedeckt), "expected_days": erwartet, "confidence": conf, "confidence_gruende": conf_gruende,
        "segment_tage_gueltig": segment_tage, "segment_tage_erwartet": segment_tage_erwartet,
        "teilabgedeckte_tage": len(teil),
        "segmente_mit_daten": len(segs_mit), "segmente_gesamt": len(daten.segmente_fassung), "empty_segmente": leere_segmente,
        "gueltige_beobachtungen": beobachtungen, "kosten_usd": kosten, "kosten_fassung_usd": kosten_fassung,
        "cost_per_valid_observation": round(kosten_fassung / beobachtungen, 4) if beobachtungen else None,
        "cost_per_unique_listing": round(kosten_fassung / len(inserate), 4) if inserate else None,
        "cost_per_hot_deal": round(kosten_fassung / len(hot), 4) if hot else None,
    }


def _tageszeilen(daten: _Daten, tage: List[str], kosten_docs: List[Dict[str, Any]], zone: float) -> Tuple[List[Dict[str, Any]], Dict[str, Any]]:
    bewegungen: Dict[str, Optional[Dict[str, Any]]] = {}
    zeilen = []
    korb = daten.korb(set(tage))
    for t in tage:
        docs = daten.docs_tag(t)
        basis = daten.basis_tag(t)
        gueltig = [d for d in docs if deals.ist_gueltig(d)]
        vortag = daten.basis_tag(_plus(t, -1))
        b = tagesbewegung(basis, vortag) if basis and vortag else None
        bewegungen[t] = b
        nv = niveau(list(basis.values()))
        # Befund B3: Tageswert mit technisch fehlenden Segmenten = Teilabdeckung (Diagramm: Luecke, keine Kennzahl)
        fehlend = daten.fehlende(t, korb) if nv["median"] is not None else 0
        mit_vergleich = [d for d in gueltig if d.get("vergleich_vortag")]
        dq: Dict[str, int] = {}
        for d in docs:
            q = deals.qualitaet(d)
            dq[q] = dq.get(q, 0) + 1
        zeilen.append({
            "date": t, **nv, "delta_vortag_eur": (b or {}).get("eur"), "delta_vortag_pct": (b or {}).get("pct"),
            "vergleich_segmente": (b or {}).get("segmente"), "richtung": richtung((b or {}).get("pct"), zone) if b else None,
            "neue": len(set().union(*[_ids(d, "new_in_sample_ids") for d in mit_vergleich])) if mit_vergleich else 0,
            "verschwundene": sum(int(d.get("disappeared_count") or 0) for d in mit_vergleich),
            "preissenkungen": sum(len(_ids(d, "price_reduced_ids")) for d in gueltig),
            "preiserhoehungen": sum(len(_ids(d, "price_increase_ids")) for d in gueltig),
            "top3_wechsel": sum(1 for d in gueltig if d.get("top3_changed") is True),
            "top5_wechsel": sum(1 for d in gueltig if d.get("top5_changed") is True),
            "hot_deals": len(set().union(*[_hot_ids(d) for d in docs])) if docs else 0,
            "private_hot_deals": len(set().union(*[_hot_privat_ids(d) for d in docs])) if docs else 0,
            "data_quality": _qualitaet_zusammen(dq) if docs else None,
            "gueltig": bool(gueltig), "leer": bool(gueltig) and not basis, "nur_ungueltig": bool(docs) and not gueltig,
            "andere_fassung": daten.wechsel and t < daten.fassung_ab,
            "teilabdeckung": fehlend > 0, "fehlende_segmente": fehlend, "offen": daten.offen(t),
            "kosten_usd": round(sum(float(d.get("crawl_cost_usd") or 0) for d in kosten_docs if d["date"] == t), 4)})
    return zeilen, bewegungen


def _segmentdetail(daten: _Daten, tage: List[str], kosten_docs: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Abschnitt 21: jede EZ x jeder km-Bereich einzeln (nur Kennzahlen, keine Inseratskopien)."""
    tage_set = set(tage)
    raus = []
    for seg_id, tage_docs in daten.idx.items():
        im = {t: d for t, d in tage_docs.items() if t in tage_set}
        if not im:
            continue
        seg = daten.segs.get(seg_id) or {}
        gueltig = sorted(t for t, d in im.items() if deals.ist_gueltig(d))
        basis = sorted(t for t, d in im.items() if deals.ist_basis(d))
        a, b = (im[basis[0]], im[basis[-1]]) if len(basis) >= 2 else (None, None)
        delta = (float(b["median_price"]) - float(a["median_price"])) if a is not None else None
        dq: Dict[str, int] = {}
        for d in im.values():
            q = deals.qualitaet(d)
            dq[q] = dq.get(q, 0) + 1
        letzter = im[gueltig[-1]] if gueltig else None
        inserate = set().union(*[(_ids(im[t], "listing_ids_alle") or _ids(im[t], "listing_ids")) for t in gueltig]) if gueltig else set()
        raus.append({"segment_id": seg_id, "ez_label": seg.get("ez_label"), "km_label": seg.get("km_label"),
                     "year_from": seg.get("year_from"), "min_km": seg.get("min_km"), "max_km": seg.get("max_km"),
                     "enabled": seg.get("enabled"), "version": daten.haupt[0] if daten.haupt else None,
                     "aktueller_median": float(im[basis[-1]]["median_price"]) if basis else None,
                     "delta_eur": _r(delta), "delta_pct": _r(delta / float(a["median_price"]) * 100, 3) if (delta is not None and float(a["median_price"])) else None,
                     "gueltige_tage": len(gueltig), "erwartete_tage": len(daten.erwartete_tage(tage)),
                     "listings": len(inserate), "hot_deals": len(set().union(*[_hot_ids(d) for d in im.values()])),
                     "private_hot_deals": len(set().union(*[_hot_privat_ids(d) for d in im.values()])),
                     "market_depth": speicher.qualitaet_aus_doc(letzter)["market_depth"] if letzter else "UNKNOWN",
                     "data_quality": _qualitaet_zusammen(dq), "health": None,
                     "top5_wechsel": sum(1 for t in gueltig if im[t].get("top5_changed") is True),
                     "kosten_usd": round(sum(float(d.get("crawl_cost_usd") or 0) for d in kosten_docs
                                             if d["segment_id"] == seg_id and d["date"] in tage_set), 4)})
    raus.sort(key=lambda s: (s.get("year_from") or 0, s.get("min_km") or 0, s["segment_id"]))
    return raus


def bericht_rechnen(modell: Dict[str, Any], segs: Dict[str, Dict[str, Any]], docs: List[Dict[str, Any]], typ: str, von: str, bis: str,
                    ereignisse: Optional[List[Dict[str, Any]]] = None, *, zone: float = STABIL_PCT,
                    heute: Optional[str] = None) -> Optional[Dict[str, Any]]:
    """Reine Rechnung (ohne Datenbank) — derselbe Code fuer finale und vorlaeufige Berichte. None ohne Tagesdaten.
    heute (nur vorlaeufig, deutscher Kalendertag): spaetere Tage sind 'offen' statt fehlend (Befund B17)."""
    daten = _Daten(segs, docs, von, bis, heute)
    if not daten.im_zeitraum or daten.haupt is None:
        return None
    tage = tage_zwischen(von, bis)
    kosten_docs = [d for d in docs if von <= d["date"] <= bis]          # Kosten ueber ALLE Fassungen (reale Ausgaben)
    zeilen, bewegungen = _tageszeilen(daten, tage, kosten_docs, zone)
    kz = kennzahlen(daten, tage, kosten_docs, zone)
    bericht: Dict[str, Any] = {
        "id": uuid.uuid4().hex, "model_id": modell.get("id"), "typ": typ, "periode_von": von, "periode_bis": bis, "revision": 1,
        "schema": SCHEMA, "stabil_zone_pct": zone, "faellig_ab": faellig_ab(bis).isoformat(),
        "modell": {k: modell.get(k) for k in ("label", "make", "model", "variant", "fuel", "gearbox", "power_kw_min", "power_kw_max", "status")},
        "fassung": {"version": daten.haupt[0], "definition_hash": daten.haupt[1], "ab": daten.fassung_ab},
        "kennzahlen": kz, "bewegung": bewegung_statistik(tage[1:], bewegungen, zone), "tage": zeilen,
        "segmente": _segmentdetail(daten, tage, kosten_docs), "hinweise": [], "hinweis": HINWEIS}
    if typ in ("FIFTEEN_DAY", "MONTHLY"):
        bloecke = []
        monat = _datum(von)
        # die festen 5-Tage-Bloecke des Kalendermonats, die im Zeitraum liegen (16.-Ende: 16-20, 21-25, 26-Ende)
        for typ_b, bv, bb in perioden_im_monat(monat.year, monat.month):
            if typ_b != "FIVE_DAY" or bv < von or bb > bis:
                continue
            bt = tage_zwischen(bv, bb)
            k = kennzahlen(daten, bt, kosten_docs, zone)
            bloecke.append({"von": bv, "bis": bb, "offen": all(daten.offen(t) for t in bt),
                            **{f: k[f] for f in ("startwert", "endwert", "delta_eur", "delta_pct", "richtung",
                                                 "neue_listings", "verschwundene_listings", "preissenkungen",
                                                 "preiserhoehungen", "hot_deals", "private_hot_deals",
                                                 "coverage_days", "expected_days", "data_quality", "kosten_usd")},
                            "bewegung": {f: v for f, v in bewegung_statistik(bt[1:], bewegungen, zone).items()
                                         if f in ("fallend", "steigend", "stabil", "netto_eur")}})
        bericht["bloecke"] = bloecke
    # fruehere Fassungen im Zeitraum: getrennt, nie mit der gerechneten vermischt
    andere: Dict[Tuple[int, Optional[str]], List[Dict[str, Any]]] = {}
    for d in daten.im_zeitraum:
        f = daten.fassung(d)
        if f != daten.haupt:
            andere.setdefault(f, []).append(d)
    fruehere = []
    for f, fd in sorted(andere.items(), key=lambda kv: min(d["date"] for d in kv[1])):
        dat = _Daten(segs, [d for d in docs if daten.fassung(d) == f], von, bis)
        k = kennzahlen(dat, sorted({d["date"] for d in fd}), [], zone) if dat.haupt == f else {}
        fruehere.append({"version": f[0], "definition_hash": f[1], "von": min(d["date"] for d in fd), "bis": max(d["date"] for d in fd),
                         "tage": len({d["date"] for d in fd if deals.ist_gueltig(d)}), "startwert": k.get("startwert"),
                         "endwert": k.get("endwert"), "delta_eur": k.get("delta_eur"), "delta_pct": k.get("delta_pct")})
    bericht["fruehere_fassungen"] = fruehere
    if fruehere:
        bericht["hinweise"].append("Fassungswechsel im Zeitraum — gerechnet wird nur die aktuelle Fassung ab "
                                   f"{daten.fassung_ab}; frühere Fassungen stehen getrennt (neue Zeitreihe)")
    # Hot Deals des Zeitraums: kompakt aus der Ereignis-Historie (nur IDs, Preise, Ränge)
    eigene_segs = set(daten.idx)
    beste: Dict[Tuple[str, str], Dict[str, Any]] = {}
    for e in ereignisse or []:
        if e.get("segment_id") not in eigene_segs or not (von <= str(e.get("tag") or "") <= bis):
            continue
        if e.get("typ") not in ("NEW_HOT_DEAL", "BECAME_HOT_DEAL", "PRICE_DROP_HOT_DEAL", "STILL_HOT") or e.get("diff_pct") is None:
            continue
        key = (e["segment_id"], e["listing_id"])
        if key not in beste or float(e["diff_pct"]) > float(beste[key]["diff_pct"]):
            beste[key] = {f: e.get(f) for f in ("segment_id", "listing_id", "tag", "typ", "klasse", "price", "reference_price",
                                                "diff_eur", "diff_pct", "rank", "privat")}
    bericht["hot_deals_top"] = sorted(beste.values(), key=lambda x: -float(x["diff_pct"]))[:HOT_TOP_MAX]
    if kz["coverage_days"] < kz["expected_days"]:
        bericht["hinweise"].append(f"{kz['coverage_days']} / {kz['expected_days']} gültige Tage — Lücken werden nicht aufgefüllt")
    if kz["segment_tage_gueltig"] < kz["segmente_gesamt"] * kz["coverage_days"]:
        bericht["hinweise"].append(f"{kz['segment_tage_gueltig']} / {kz['segment_tage_erwartet']} gültige Segment-Tage "
                                   f"({kz['segmente_gesamt']} Segmente) — fehlende Segmente werden nicht aufgefüllt")
    if kz["teilabgedeckte_tage"]:
        bericht["hinweise"].append(f"{kz['teilabgedeckte_tage']} Tag(e) mit Teilabdeckung (Segmente ohne gültigen Lauf) — "
                                   "zählen nicht für Median, Minimum, Maximum und Stichproben-Änderung der Periode")
    if daten.offen_ab is not None and daten.offen_ab <= bis:
        bericht["offen_ab"] = daten.offen_ab
    return bericht


# ---------------------------------------------------------------- Datenbank (nur lesen) + Einfrieren
async def _laden(db, model_id: str, von: str, bis: str) -> Tuple[Dict[str, Any], Dict[str, Dict[str, Any]], List[Dict[str, Any]], List[Dict[str, Any]]]:
    modell = await db[MODELLE].find_one({"id": model_id}, {"_id": 0}) or {"id": model_id}
    segs = {s["id"]: s async for s in db[SEGMENTE].find({"model_id": model_id}, {"_id": 0})}
    docs: List[Dict[str, Any]] = []
    if segs:
        docs = await db[TAGESSTATS].find({"segment_id": {"$in": sorted(segs)}, "date": {"$gte": _plus(von, -VORLAUF_TAGE), "$lte": bis}},
                                         {"_id": 0, "laeufe": 0}).to_list(None)
    ereignisse = await db[HOTDEAL_EREIGNISSE].find({"model_id": model_id, "tag": {"$gte": von, "$lte": bis},
                                                    "typ": {"$in": ["NEW_HOT_DEAL", "BECAME_HOT_DEAL", "PRICE_DROP_HOT_DEAL", "STILL_HOT"]}},
                                                   {"_id": 0}).to_list(20000)
    return modell, segs, docs, ereignisse


async def bericht_berechnen(db, model_id: str, typ: str, von: str, bis: str, *, heute: Optional[str] = None) -> Optional[Dict[str, Any]]:
    """Bericht aus den gespeicherten Daten rechnen — schreibt nichts. Die reine Rechnung laeuft in einem
    Hilfsthread (Abschnitt 49, Befund B9): ein Monatsbericht mit 180 Segmenten braucht ueber 1 s CPU, und der
    Event-Loop dieses Web-Prozesses bedient gleichzeitig Vergleich, Vertrag und Versand — er darf nie stehen.
    bericht_rechnen liest nur seine Argumente (keine Datenbank, kein geteilter Zustand)."""
    modell, segs, docs, ereignisse = await _laden(db, model_id, von, bis)
    return await asyncio.to_thread(bericht_rechnen, modell, segs, docs, typ, von, bis, ereignisse, heute=heute)


async def _hot_deals_offen(db, segment_ids: List[str], von: str, bis: str) -> int:
    if not segment_ids:
        return 0
    return int(await db[TAGESSTATS].count_documents({"segment_id": {"$in": segment_ids}, "date": {"$gte": von, "$lte": bis}, "hot_deals_offen": True}))


async def finalisieren(db, model_id: str, typ: str, von: str, bis: str, *, jetzt: Optional[datetime] = None) -> str:
    """Einen Bericht final einfrieren: 'erstellt' | 'vorhanden' | 'nicht_faellig' | 'keine_daten' | 'wartet_auf_hot_deals'.
    Nur nach Periodenende + Karenz; ein vorhandener Bericht wird NIE geaendert (Insert gegen den Unique-Index —
    zwei Server: der zweite bekommt DuplicateKeyError und laesst den ersten stehen)."""
    jetzt = jetzt or konfig.jetzt()
    if not periode_gueltig(typ, von, bis):
        raise ValueError(f"keine gueltige Periode: {typ} {von}..{bis}")
    if not ist_faellig(bis, jetzt):
        return "nicht_faellig"
    schluessel = {"model_id": model_id, "typ": typ, "periode_von": von, "periode_bis": bis}
    if await db[BERICHTE].find_one(schluessel, {"_id": 1}):
        return "vorhanden"
    seg_ids = sorted(str(x) for x in await db[SEGMENTE].distinct("id", {"model_id": model_id}))
    hinweise = []
    offen = await _hot_deals_offen(db, seg_ids, von, bis)
    if offen:
        if jetzt < faellig_ab(bis) + timedelta(hours=HOTDEAL_WARTEN_STUNDEN):
            return "wartet_auf_hot_deals"
        hinweise.append(f"{offen} Tageswert(e) ohne Hot-Deal-Auswertung — Hot Deals können im Bericht fehlen")
    bericht = await bericht_berechnen(db, model_id, typ, von, bis)
    if not bericht:
        return "keine_daten"
    bericht.update({"status": "FINAL", "erstellt_at": jetzt.isoformat()})
    bericht["hinweise"] = list(bericht.get("hinweise") or []) + hinweise
    try:
        await db[BERICHTE].insert_one(bericht)
    except DuplicateKeyError:
        return "vorhanden"
    return "erstellt"


async def _index_fehlt(db) -> bool:
    return INDEX_REF in ((await konfig.merker_lesen(db, konfig.INDIZES_DOK)).get("alle_fehler") or [])


async def faellige_finalisieren(db, *, jetzt: Optional[datetime] = None, model_ids: Optional[List[str]] = None) -> Dict[str, Any]:
    """Alle faelligen Perioden finalisieren (nur die faelligen — laufende nie). Je Periode die Modelle mit
    Tagesdokumenten im Zeitraum; eine vollstaendig erledigte Periode wird gemerkt (market_config/berichte) und
    nicht erneut geprueft. model_ids grenzt ein (Tests) und schreibt keinen Merker."""
    jetzt = jetzt or konfig.jetzt()
    z: Dict[str, Any] = {"perioden": 0, "erstellt": 0, "vorhanden": 0, "keine_daten": 0, "wartet": 0, "fehler": 0}
    if await _index_fehlt(db):
        log.error("Berichte: Unique-Index %s fehlt — keine Finalisierung (Doppel-Berichte waeren moeglich)", INDEX_REF)
        return {**z, "gesperrt": "index_fehlt", "fehler": 1}
    merker = await konfig.merker_lesen(db, BERICHTE_DOK)
    erledigt = dict(merker.get("perioden") or {})
    nur = set(model_ids) if model_ids is not None else None
    for typ, von, bis in faellige_perioden(jetzt):
        schluessel = f"{typ}:{von}:{bis}"
        if nur is None and schluessel in erledigt:
            continue
        z["perioden"] += 1
        mids = [str(m) for m in await db[TAGESSTATS].distinct("model_id", {"date": {"$gte": von, "$lte": bis}}) if m]
        if nur is not None:
            mids = [m for m in mids if m in nur]
        vorhanden = {str(m) for m in await db[BERICHTE].distinct("model_id", {"typ": typ, "periode_von": von, "periode_bis": bis})}
        komplett = True
        for mid in sorted(set(mids) - vorhanden):
            try:
                r = await finalisieren(db, mid, typ, von, bis, jetzt=jetzt)
            except Exception:  # noqa: BLE001
                log.exception("Bericht %s %s %s..%s gescheitert", mid, typ, von, bis)
                z["fehler"] += 1
                komplett = False
                continue
            if r == "erstellt":
                z["erstellt"] += 1
            elif r == "vorhanden":
                z["vorhanden"] += 1
            elif r == "keine_daten":
                z["keine_daten"] += 1
            elif r == "wartet_auf_hot_deals":
                z["wartet"] += 1
                komplett = False
            await asyncio.sleep(0)          # zwischen den Modellen den Loop freigeben (Abschnitt 49)
        if komplett and nur is None:
            erledigt[schluessel] = jetzt.isoformat()
    if nur is None:
        grenze = _plus(konfig.heute_tag(jetzt), -(NACHHOL_TAGE + 40))
        erledigt = {k: v for k, v in erledigt.items() if k.split(":")[-1] >= grenze}
        await konfig.merker_setzen(db, BERICHTE_DOK, perioden=erledigt, letzter_lauf_at=jetzt.isoformat(),
                                   letztes_ergebnis={k: v for k, v in z.items() if isinstance(v, int)})
    return z


# ---------------------------------------------------------------- Lesen (Admin)
UEBERSICHT_PROJEKTION = {"_id": 0, "tage": 0, "segmente": 0, "bloecke": 0, "hot_deals_top": 0, "bewegung": 0, "fruehere_fassungen": 0}


def uebersicht_zeile(b: Dict[str, Any]) -> Dict[str, Any]:
    k, m = b.get("kennzahlen") or {}, b.get("modell") or {}
    return {"model_id": b.get("model_id"), "label": m.get("label") or b.get("model_id"), "fuel": m.get("fuel"), "gearbox": m.get("gearbox"),
            "richtung": k.get("richtung"), "delta_eur": k.get("delta_eur"), "delta_pct": k.get("delta_pct"),
            "listings": k.get("unterschiedliche_listings"), "preissenkungen": k.get("preissenkungen"), "preiserhoehungen": k.get("preiserhoehungen"),
            "hot_deals": k.get("hot_deals"), "private_hot_deals": k.get("private_hot_deals"), "liquiditaet": k.get("liquiditaet"),
            "data_quality": k.get("data_quality"), "health": None, "kosten_usd": k.get("kosten_usd"), "empty_segmente": k.get("empty_segmente"),
            "confidence": k.get("confidence"), "coverage_days": k.get("coverage_days"), "expected_days": k.get("expected_days"),
            "segmente_mit_daten": k.get("segmente_mit_daten"), "segmente_gesamt": k.get("segmente_gesamt"),
            "status": b.get("status"), "erstellt_at": b.get("erstellt_at")}


async def uebersicht(db, typ: str, von: str, bis: str) -> Dict[str, Any]:
    """Abschnitt 37: alle Modelle einer (finalen) Periode — aus den eingefrorenen Berichten, ohne Neuberechnung."""
    berichte = await db[BERICHTE].find({"typ": typ, "periode_von": von, "periode_bis": bis}, UEBERSICHT_PROJEKTION).to_list(5000)
    zeilen = sorted((uebersicht_zeile(b) for b in berichte), key=lambda z: str(z.get("label") or ""))
    return {"typ": typ, "von": von, "bis": bis, "zeilen": zeilen, "anzahl": len(zeilen), "final": ist_faellig(bis),
            "faellig_ab": faellig_ab(bis).isoformat(), "hinweis": HINWEIS}


async def perioden_liste(db, typ: Optional[str] = None) -> Dict[str, Any]:
    """Finale Perioden mit Anzahl der Modelle. Befunde B12/B13: die eingefrorenen Berichte werden nie geloescht
    (rund 1.500 Dokumente zu 15-100 KB je Monat) — die Gruppierung nutzt deshalb NUR die Felder des Index
    markt_bericht_periode (typ, periode_von, periode_bis) und laeuft als abgedeckter Index-Scan, ohne ein einziges
    Berichtsdokument zu lesen (kein Cache-Verdraengen auf dem gemeinsamen Primary)."""
    match: Dict[str, Any] = {"typ": typ} if typ else {}
    pipeline = [{"$match": match},
                {"$group": {"_id": {"typ": "$typ", "von": "$periode_von", "bis": "$periode_bis"}, "anzahl": {"$sum": 1}}},
                {"$sort": {"_id.von": -1, "_id.typ": 1}}, {"$limit": 400}]
    try:
        gruppen = await db[BERICHTE].aggregate(pipeline, hint=PERIODEN_INDEX).to_list(400)
    except OperationFailure:
        # Index fehlt (Anlage gescheitert, steht im Merker market_config/indizes) — dann eben ohne Hinweis
        log.warning("Berichte: Index %s fehlt — Periodenliste ohne abgedeckten Index-Scan", PERIODEN_INDEX)
        gruppen = await db[BERICHTE].aggregate(pipeline).to_list(400)
    final = [{"typ": g["_id"]["typ"], "von": g["_id"]["von"], "bis": g["_id"]["bis"], "anzahl": g["anzahl"]} for g in gruppen]
    laufend = [p for p in laufende_perioden() if not typ or p["typ"] == typ]
    return {"final": final, "laufend": laufend, "stand": await konfig.merker_lesen(db, BERICHTE_DOK),
            "karenz_stunden": BERICHT_KARENZ_STUNDEN, "stabil_zone_pct": STABIL_PCT}


async def modell_bericht(db, model_id: str, typ: str, von: str, bis: str) -> Optional[Dict[str, Any]]:
    """Finaler Bericht (unveraendert aus der Sammlung) oder — solange die Periode nicht final ist — eine
    vorlaeufige Live-Rechnung, die NICHT gespeichert wird."""
    if not periode_gueltig(typ, von, bis):
        raise ValueError(f"keine gueltige Periode: {typ} {von}..{bis}")
    b = await db[BERICHTE].find_one({"model_id": model_id, "typ": typ, "periode_von": von, "periode_bis": bis}, {"_id": 0})
    if b:
        return b
    # Befund B17: kuenftige Tage der laufenden Periode sind 'offen', nicht fehlend (Abdeckung/Confidence nur bis heute)
    b = await bericht_berechnen(db, model_id, typ, von, bis, heute=konfig.heute_tag())
    if not b:
        return None
    b["status"] = "VORLAEUFIG"
    b["stand_at"] = konfig.jetzt_iso()
    b["hinweise"] = list(b.get("hinweise") or []) + [
        "vorläufig — live aus den bisherigen Tageswerten gerechnet; eingefroren wird der Bericht erst nach Periodenende + "
        f"{BERICHT_KARENZ_STUNDEN} h" if not ist_faellig(bis) else "vorläufig — die Finalisierung steht noch aus"]
    return b


async def modell_berichte(db, model_id: str) -> Dict[str, Any]:
    final = await db[BERICHTE].find({"model_id": model_id}, {"_id": 0, "typ": 1, "periode_von": 1, "periode_bis": 1, "erstellt_at": 1,
                                                             "status": 1, "kennzahlen.richtung": 1, "kennzahlen.delta_pct": 1})\
        .sort([("periode_von", -1), ("typ", 1)]).to_list(1000)
    vorhanden = {(b["typ"], b["periode_von"], b["periode_bis"]) for b in final}
    laufend = [p for p in laufende_perioden() if (p["typ"], p["von"], p["bis"]) not in vorhanden]
    return {"model_id": model_id, "final": final, "laufend": laufend}
