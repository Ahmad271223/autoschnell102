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
    einem Tag ein Segment des Korbs technisch (ungueltiger oder kein Lauf), ist der Tag 'Teilabdeckung'. Median/
    Mittel/Minimum/Maximum der Periode und sample_market_change rechnen mit dem Korbwert je Tag (Schema 2, Pruefung
    Runde 2): vollstaendige Tage = beobachtetes Niveau, Teilabdeckung = verkettet ueber die Segmente, die an beiden
    Vergleichstagen vorhanden sind (technisch fehlende fallen auf BEIDEN Seiten heraus) — ein Ausfall ist so weder
    Markteinbruch noch verworfener Tag; Marktinformation (EMPTY, neue oder abgeschaltete Segmente) bleibt im Wert
  * erwartet wird je Segment nur, solange es eingeschaltet war (Zu-/Abschalten ist keine Datenluecke), im
    vorlaeufigen Bericht heute nur, wenn es heute schon gelaufen ist
  * geplant vs. ausgefallen (Schema 3, Pruefung Runde 3): technisch fehlend ist ein Segment-Tag nur, wenn fuer
    (Segment, Tag) ein Abruf-Job existierte und kein gueltiger Lauf da ist (failed, data_invalid, veraltet storniert,
    nur POOR). Ohne Job ist der Segment-Tag NICHT GEPLANT (Budget-Rotation im Tagesplan, spaeter SAFE_AUTO-Frequenz,
    Pause, Crawler aus) — weder Luecke noch Teilabdeckung, und er zaehlt nicht als erwarteter (Segment-)Tag. Die Jobs
    werden nur gelesen (market_crawl_jobs, nie geloescht), gebuendelt je Modell und Zeitraum im async-Teil. Im
    Korbwert steht ein nicht geplantes Segment mit seinem letzten geplanten Wert (hoechstens TRAGEN_MAX_TAGE alt;
    war der letzte geplante Lauf ein Ausfall, nichts) — sonst waere bei rotierender Planung jeder Tag ein anderer
    Teilkorb. Ein Anker (beobachtetes Euro-Niveau) braucht ANKER_MIN_ABDECKUNG des Korbgewichts; ohne gueltigen Anker
    bleiben die Euro-Niveauwerte der Periode leer (ehrliche Luecke statt Teilkorb-Niveau)
  * Tagesplan-Protokoll (Schema 4, Pruefung Runde 4): 'nicht geplant' gilt nur fuer Kalendertage, an denen
    nachweislich entschieden wurde (konfig.TAGESPLAN_LOG: Tagesplan lief, Crawler bewusst aus, Budget). Ein Tag ohne
    Protokoll ist ab dessen Einfuehrung ein technischer Ausfall des Plans (erwarteter Tag, Luecke); davor gilt ein Tag
    als gelaufen, wenn es fuer irgendein Segment einen Tagesplan-Job gibt, sonst ist die Planung unbekannt (erwartet).
    Budget vor dem Plan erschoepft und Budget danach erschoepft (Storno mit budget_wait) sind einheitlich 'nicht geplant
    wegen Budget'. Serienende auf Ebene der Fassung (aktiv bis zum letzten Plan irgendeines Segments bzw. bis zum
    Abschalten, Jobs bis heute), Serienstart: schon angelegte, noch nicht beobachtete Segmente fehlen (kein Teilkorb-
    Anker) — auch laengst angelegte (Neustart, Wieder-Einschalten) und solche ohne Basistag im Zeitraum (Serienstart am
    letzten Periodentag). Ein Anker braucht IMMER die Mindestabdeckung gegen den vollen Korb; ein nicht geplantes Segment
    ohne tragbaren Wert (Intervall > TRAGEN_MAX_TAGE) ist keine technische Luecke, sein Wert fehlt aber (Nenner der
    Abdeckung) — bei laengeren Intervallen bleibt das Euro-Niveau ehrlich leer. Start/Ende ueber die wirksamen Werte
    (beobachtet oder getragen) je Korb-Segment, in Euro nur ab ANKER_MIN_ABDECKUNG des Korbgewichts. Fehlt das
    Protokoll eines Tages ab Einfuehrung, belegt irgendein Tagesplan-Job an dem Tag den Plan (Protokoll gescheitert)
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
from typing import Any, Dict, List, NamedTuple, Optional, Tuple

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
# Rechenregel der gespeicherten Kennzahlen — steht in jedem Bericht (eingefrorene Berichte behalten ihre Nummer):
#   1  Phase E (27.09.2026) — vor UND nach der ersten Pruefrunde (789e3a6 hat die Bedeutung von Tagesbewegung/
#      Volatilitaet, cost_per_*, Periodenwerten, P25/P75 und Confidence geaendert, ohne die Nummer zu erhoehen;
#      beide Staende sind deshalb nicht unterscheidbar und gelten gemeinsam als 'aeltere Rechenregel')
#   2  Pruefung Runde 2: alle Regeln der ersten Pruefrunde (B3-B7) plus Periodenwerte aus dem verketteten Korbwert
#      (Teilabdeckung verwirft keinen Tag mehr), Segmentabdeckung mit Einschaltfenstern je Segment und im
#      vorlaeufigen Bericht heute nur gelaufene Segmente
#   3  Pruefung Runde 3: technisch fehlend nur mit Abruf-Job fuer (Segment, Tag) — nicht geplante Segment-Tage
#      (Budget-Rotation) sind weder Luecke noch Teilabdeckung und stehen im Korbwert mit dem letzten geplanten Wert;
#      Anker nur mit Mindestabdeckung ANKER_MIN_ABDECKUNG (sonst Euro-Niveauwerte None statt Teilkorb-Niveau);
#      expected_days/Abdeckung ohne Kalendertage, an denen kein Segment erwartet bzw. geplant war
#   4  Pruefung Runde 4: Tagesplan-Protokoll (ganztaegiger Ausfall des Plans = Luecke, nicht 'nicht geplant'), Budget
#      einheitlich 'nicht geplant wegen Budget', Korb an Serienanfang/-ende (Fassungsebene, schon angelegte Segmente
#      fehlen), Anker immer mit Mindestabdeckung gegen den vollen Korb, Korb inkl. tragbarer Vorlauf-Segmente,
#      abgelaufene Werte fallen aus dem wirksamen Korb, Confidence mit Mindesttagen; Runde 4b (vor der Auslieferung,
#      gleiche Nummer): abgelaufene Werte fehlen (Nenner), laengst angelegte bzw. nie beobachtete Segmente fehlen
#      ('vorab'), Start/Ende ueber die wirksamen Werte, Budget-Storno ganzer Tage, Protokoll-Luecke mit Job-Beleg
# Die Oberflaeche kennzeichnet aeltere Berichte ("nach aelterer Rechenregel erstellt") — Vergleiche ueber Perioden
# sollen die Definitionen nicht unbemerkt mischen.
SCHEMA = 4
# Pruefung Runde 4 (#6): Confidence nach Tagen. Ein Modell wird unter Budget-Rotation oft als Block geplant — ein
# FIVE_DAY-Zeitraum kann dann nur EINEN gueltigen Tag haben (expected 1, coverage 1/1). Aus einem Tag gibt es weder
# Richtung noch Tagesbewegung, Median/Min/Max waeren derselbe Wert: das ist nie 'hoch'.
#   HIGH   ab CONF_HOCH_MIN_TAGE = 3 gueltigen Tagen (erst dann gibt es zwei Tagesvergleiche und einen Median, der
#          nicht Minimum oder Maximum ist) UND mindestens CONF_HOCH_KALENDER_ANTEIL = 50 % der Kalendertage der Periode
#          (bis heute) mit gueltigem Lauf oder gueltig getragenem Wert — sonst beschreibt der Bericht weniger als die
#          Haelfte des Zeitraums (z. B. ein am 10. pausierter Monatsauftrag)
#   MEDIUM ab CONF_MITTEL_MIN_TAGE = 2 gueltigen Tagen (ein Vergleich, Start != Ende moeglich)
#   LOW    sonst
CONF_HOCH_MIN_TAGE = 3
CONF_MITTEL_MIN_TAGE = 2
CONF_HOCH_KALENDER_ANTEIL = 0.5
# Pruefung Runde 4 (#1): Planungsstand je Kalendertag (aus dem Tagesplan-Protokoll, siehe _protokoll_laden)
PLAN_LIEF = "lief"                 # Tagesplan lief — ein Segment ohne Job ist nicht geplant (Rotation)
PLAN_BUDGET = "budget"             # Tagesplan lief, aber das Budget liess nichts/kaum etwas zu — nicht geplant wegen Budget
PLAN_AUS = "crawler_aus"           # Crawler bewusst aus — nicht geplant
PLAN_AUSFALL = "ausfall"           # kein Protokoll ab Einfuehrung — technischer Ausfall des Plans (erwartet, Luecke)
PLAN_UNBEKANNT = "unbekannt"       # Altdaten vor dem Protokoll ohne jeden Tagesplan-Job — Planung unbekannt (erwartet)
PLAN_OFFEN = "offen"               # heute (vorlaeufig), Tagesplan noch nicht gelaufen
PLAN_ERWARTET = (PLAN_AUSFALL, PLAN_UNBEKANNT, PLAN_OFFEN)
BUDGET_GRUENDE = (konfig.BUDGET_GRUND_OHNE, konfig.BUDGET_GRUND_ERSCHOEPFT)
ABGELAUFEN = "abgelaufen"          # _getragen: letzter geplanter Tag aelter als TRAGEN_MAX_TAGE
# Pruefung Runde 3 (#0): Mindestabdeckung eines Ankers als Anteil des Korbgewichts (Segmente gewichtet mit ihrer
# mittleren Stichprobe, wie das Niveau selbst). Der Anker liefert das Euro-Niveau der ganzen Kette; fehlt ein Teil des
# Korbs, ist sein Niveau ein Teilkorb (Mix-Effekt): Fehler <= fehlender Gewichtsanteil x Abstand der fehlenden Segmente
# zum Korbniveau. Bei Segmenten zwischen 12.000 und 35.000 EUR (+-50 % um das Niveau) begrenzt 95 % den Fehler auf
# hoechstens ~2,5 %, typisch (einzelne zufaellige Ausfaelle) auf ~0,2 %. 80 % liesse bis ~10 % zu — genau die
# Groessenordnung des Befunds (-12,8 %). Nicht geplante Segmente (Budget-Rotation) zaehlen dabei als abgedeckt, weil
# sie mit ihrem letzten geplanten Wert im Korb stehen; es fehlen nur technisch ausgefallene. Runde 4 (#3): auch ein
# Tag ohne technisch fehlendes Segment ist nur Anker, wenn er diese Grenze gegen den VOLLEN Korb erreicht (am
# Serienanfang/-ende fielen sonst Segmente still aus dem Korb, und der Rest wurde Anker); ohne solchen Tag hoechstens
# EIN Tag (der am besten abgedeckte), und nur ab dieser Grenze. Nicht geplante Segmente ohne tragbaren Wert (abgelaufen,
# Intervall > TRAGEN_MAX_TAGE) zaehlen dabei als nicht gedeckt (Runde 4b: aus dem Nenner genommen wurde der beste Tag
# Ersatz-Anker mit Teilkorb-Niveau, Intervall 21: +2,8 %, Kette bis -8,7 %, HIGH) — ein laengeres Intervall laesst das
# Euro-Niveau ehrlich leer (Hinweis, nicht HIGH); die Taktung plant bei knappem Budget ehrlich seltener (Warnung).
ANKER_MIN_ABDECKUNG = 0.95
# Ein nicht geplantes Segment steht im Korbwert mit dem Wert seines letzten GEPLANTEN Tages, hoechstens so viele Tage
# zurueck wie der geladene Vorlauf (VORLAUF_TAGE): die Rotation plant jedes Segment spaetestens alle
# konfig.MAX_INTERVALL_TAGE = 14 Tage (Runde 4 #2: die Taktung im Crawl-Modul und MARKT_CRAWL_INTERVALL_TAGE sind darauf begrenzt)
# — bei 14 Tagen weicht der Gebrauchtwagenmarkt typisch unter 1 % ab. War der letzte geplante Lauf ein technischer
# Ausfall, wird nichts getragen (eine Luecke wird nie aufgefuellt); ist er aelter (Budget reicht nicht fuer 14 Tage,
# SAFE_AUTO-Ruhe), fehlt sein Wert an diesem Tag (keine technische Luecke, aber nicht gedeckt; Hinweis).
TRAGEN_MAX_TAGE = VORLAUF_TAGE
JOB_TAG_INDEX = "markt_job_tag_status"     # (tag, status) — Altdaten: gab es an einem Tag irgendeinen Tagesplan-Job?
# Abruf-Jobs, die als geplant gelten: jeder Status ausser 'cancelled'; storniert nur, wenn der Tagesplan veraltet war
# (Job nie gelaufen: Worker-Ausfall, Wartung, Budget voll — ein geplanter Tag ohne Lauf, also technisch). Andere
# Stornos sind Einstellungen (Crawler aus, Suchauftrag pausiert/geaendert, Doppel-Job, Admin-Abbruch): nicht geplant.
# Der Text ist der Anfang von STORNO_ALT im Crawl-Modul, das hier nicht importiert wird (Architekturtest test_b02);
# test_markt_berichte_20260927 prueft die Uebereinstimmung.
STORNO_TECHNISCH = "Tagesplan veraltet"
JOB_INDEX = "markt_job_je_tag"     # Unique (segment_id, tag) aus indizes.markt_indizes — traegt die Job-Abfrage je Modell
BERICHTE_DOK = "berichte"          # market_config/berichte: erledigte Perioden + letzter Lauf
INDEX_REF = "market_model_reports.markt_bericht_je_periode"
PERIODEN_INDEX = "markt_bericht_periode"   # (typ, periode_von, periode_bis) — deckt die Periodenliste ab (Befunde B12/B13)
RICHTUNGEN = ("FALLING", "RISING", "STABLE", "UNKNOWN")
HINWEIS = ("Beobachtet wird je Segment nur die günstige Marktzone (die N günstigsten Angebote) — kein Marktwert. "
           "Modellwerte sind nach Stichprobengröße gewichtete Mittel der Segment-Mediane derselben Fassung; Lücken werden "
           "nicht aufgefüllt (nicht geplante Segmente der Budget-Rotation stehen im Korbwert mit ihrem letzten geplanten "
           "Wert). Aus gespeicherten Tageswerten, keine Zusatzabrufe.")


class Korbwerte(NamedTuple):
    """Ergebnis von _Daten.korbwerte fuer die Tage eines Zeitraums (je Tag mit Tageswert)."""
    werte: Dict[str, Optional[float]]    # Korbwert je Kalendertag (None = Luecke)
    fehlend: Dict[str, int]              # technisch fehlende Segmente (geplant, kein gueltiger Lauf) -> Teilabdeckung
    ausstehend: Dict[str, int]           # heute geplant, noch ohne Lauf (nur vorlaeufig)
    nicht_geplant: Dict[str, int]        # Korb-Segmente ohne Abruf an diesem Tag (Rotation) — keine Luecke
    getragen: Dict[str, int]             # davon mit dem Wert ihres letzten geplanten Tages im Korbwert
    abdeckung: Dict[str, float]          # Anteil des Korbgewichts mit Wert (beobachtet, leer oder getragen)
    anker: List[str]                     # Ankertage (beobachtetes Niveau des wirksamen Korbs)
    mit: List[str]                       # Tage mit mindestens einem beobachteten Basis-Segment
    # Runde 4
    korb_n: int                          # Segmente im wirksamen Korb des Zeitraums
    budget: Dict[str, int]               # davon nicht geplant wegen Budget (Budget-Tag bzw. Storno mit budget_wait)
    abgelaufen: Dict[str, int]           # nicht geplant, letzter geplanter Wert zu alt -> Wert fehlt (nicht gedeckt, keine Luecke)
    vorab: Dict[str, int]                # schon angelegt/eingeschaltet, noch nie beobachtet, nicht geplant -> fehlt
    kandidaten: List[str]                # Tage ohne ausstehende Laeufe (moegliche Anker)
    tragbar: Dict[str, float]            # Tage OHNE Basis-Segment: Anteil des Korbgewichts mit tragbarem Wert
    # Runde 4b: je Tag mit Basis-Segment die wirksamen Werte (Segment -> beobachtetes oder getragenes Tagesdokument) und
    # das Korbgewicht je Segment — Start-/Endwert ueber denselben Korb (kennzahlen)
    wirksam: Dict[str, Dict[str, Dict[str, Any]]]
    gewicht: Dict[str, float]


class Fehlende(NamedTuple):
    """Ergebnis von _Daten.fehlende_ids: Korb-Segmente ohne gueltigen Lauf an einem Tag, nach Grund."""
    tech: set                            # technisch fehlend (geplant, kein gueltiger Lauf) -> Teilabdeckung
    aus: set                             # heute geplant, noch ohne Lauf (nur vorlaeufig)
    ng: set                              # nicht geplant, laeuft aber noch (Rotation, Budget) -> getragener Wert
    vorab: set                           # Runde 4 #4: schon angelegt/eingeschaltet, noch nie beobachtet, nicht geplant
    budget: set                          # Teilmenge von ng: nicht geplant wegen Budget


# ---------------------------------------------------------------- Perioden
def _d(jahr: int, monat: int, tag: int) -> str:
    return f"{jahr:04d}-{monat:02d}-{tag:02d}"


def _datum(tag: str) -> datetime:
    return datetime.strptime(str(tag)[:10], "%Y-%m-%d")


def _plus(tag: str, tage: int) -> str:
    return (_datum(tag) + timedelta(days=tage)).strftime("%Y-%m-%d")


def _tag_aus_zeit(wert: Any) -> Optional[str]:
    """Deutscher Kalendertag eines Zeitstempels (ISO-Text oder datetime, ohne Zone = UTC); None, wenn unlesbar."""
    if not wert:
        return None
    try:
        z = wert if isinstance(wert, datetime) else datetime.fromisoformat(str(wert).replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return None
    return konfig.heute_tag(z if z.tzinfo else z.replace(tzinfo=timezone.utc))


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
               segment_tage: Optional[int] = None, segment_tage_erwartet: Optional[int] = None, *,
               kalendertage: Optional[int] = None, tage_mit_wert: Optional[int] = None,
               tage_ohne_plan: int = 0, einschraenkungen: Optional[List[str]] = None) -> Tuple[str, List[str]]:
    """Abschnitt 53, regelbasiert: HIGH ohne Einschraenkung; MEDIUM bei >= 50 % Abdeckung (Kalendertage UND
    Segment-Tage), >= 70 % brauchbarer Qualitaet und >= 5 Inseraten; sonst LOW. Die Gruende stehen dabei.
    Segment-Tage (Befund B5): gueltige Segment-Tage / (Segmente der Fassung x erwartete Tage) — ein Tag, an dem
    nur eines von 24 Segmenten geliefert hat, ist kein voll abgedeckter Modelltag.
    Runde 4 (#6): HIGH erst ab CONF_HOCH_MIN_TAGE gueltigen Tagen (abdeckung) UND CONF_HOCH_KALENDER_ANTEIL der
    Kalendertage (kalendertage, bis heute) mit gueltigem Lauf oder gueltig getragenem Wert (tage_mit_wert); MEDIUM erst
    ab CONF_MITTEL_MIN_TAGE gueltigen Tagen. Scheitert eine der beiden Mindestgrenzen, steht auch die Zahl der Tage
    ohne Plan als Grund dabei (Budget-Rotation, Pause, Crawler aus — erklaert, warum es so wenige Tage sind).
    einschraenkungen (Runde 4b): weitere Gruende aus dem Korb (Segment-Tage ohne tragbaren Wert, kein Ankertag) — mit
    ihnen nie HIGH."""
    anteil = abdeckung / erwartet if erwartet else 0.0
    mit_wert = abdeckung if tage_mit_wert is None else tage_mit_wert
    kal_anteil = (mit_wert / kalendertage) if kalendertage else None
    seg_anteil = (segment_tage or 0) / segment_tage_erwartet if segment_tage_erwartet else None
    n = sum(dq.values()) or 1
    brauchbar = (dq.get("GOOD", 0) + dq.get("MEDIUM", 0)) / n
    gut = dq.get("GOOD", 0) / n
    duenn = ((tiefe.get("THIN", 0) + tiefe.get("EMPTY", 0)) / sum(tiefe.values())) if tiefe else 1.0
    gruende = []
    zu_wenig = False
    if abdeckung < CONF_HOCH_MIN_TAGE:
        gruende.append(f"nur {abdeckung} gültige(r) Tag(e) (hoch ab {CONF_HOCH_MIN_TAGE})")
        zu_wenig = True
    if kal_anteil is not None and kal_anteil < CONF_HOCH_KALENDER_ANTEIL:
        gruende.append(f"{mit_wert}/{kalendertage} Kalendertage mit gültigem Wert (hoch ab {round(CONF_HOCH_KALENDER_ANTEIL * 100)} %)")
        zu_wenig = True
    if zu_wenig and tage_ohne_plan:
        gruende.append(f"{tage_ohne_plan} Tage ohne Plan")
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
    gruende += list(einschraenkungen or [])
    if not gruende:
        return "HIGH", []
    if (abdeckung >= CONF_MITTEL_MIN_TAGE and anteil >= 0.5 and (seg_anteil is None or seg_anteil >= SEGMENT_ABDECKUNG_MITTEL)
            and brauchbar >= 0.7 and inserate >= 5):
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
    """Tagesdokumente eines Modells, nach Fassung getrennt und je Segment/Tag indiziert. geplant (Pruefung Runde 3):
    Segment -> Kalendertage mit Abruf-Job (Vorlauf + Zeitraum, siehe _geplant_laden); None = Planung unbekannt (keine
    Jobs geladen, z. B. reine Rechnung oder Altdaten) — dann gilt wie bisher jedes eingeschaltete Segment als geplant.
    planung (Pruefung Runde 4, nur mit geplant): {'tage': Kalendertag -> PLAN_* aus dem Tagesplan-Protokoll (fehlt ein
    Tag: PLAN_LIEF), 'budget': Segment -> Tage mit Budget-Storno, 'erster': Segment -> erster Job-Tag im geladenen
    Zeitraum, 'letzter': Segment -> letzter geplanter Job-Tag bis heute (auch nach 'bis')}."""

    def __init__(self, segs: Dict[str, Dict[str, Any]], docs: List[Dict[str, Any]], von: str, bis: str, heute: Optional[str] = None,
                 geplant: Optional[Dict[str, set]] = None, planung: Optional[Dict[str, Any]] = None):
        self.segs, self.von, self.bis = segs, von, bis
        self.geplant = geplant
        planung = planung or {}
        self.tag_status: Dict[str, str] = dict(planung.get("tage") or {}) if geplant is not None else {}
        self.budget_storno: Dict[str, set] = {str(k): set(v) for k, v in (planung.get("budget") or {}).items()}
        # erster Job je Segment (Runde 4 #4: schon angelegt/eingeschaltet?) — reine Rechnung: erster geplanter Tag
        self.erster_job: Dict[str, str] = dict(planung.get("erster") or {}) or {sid: min(g) for sid, g in (geplant or {}).items() if g}
        letzter_job: Dict[str, str] = {str(k): str(v) for k, v in (planung.get("letzter") or {}).items() if v}
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
        if geplant is not None:
            # auch ein inzwischen abgeschaltetes Segment dieser Fassung, das im Zeitraum geplant war (z. B. nur Ausfaelle)
            self.segmente_fassung |= {sid for sid, g in geplant.items() if sid in segs and deals.fassung({}, segs[sid]) == self.haupt
                                      and any(von <= t <= bis for t in g)}
        # alle geladenen Kalendertage (Vorlauf + Zeitraum) mit Position — fuer den Blick zurueck auf den letzten geplanten Tag
        self.alle_tage = tage_zwischen(_plus(von, -VORLAUF_TAGE), bis)
        self.pos = {t: i for i, t in enumerate(self.alle_tage)}
        # letzter Tag mit Abruf-Job oder Tagesdokument je Segment (nur mit bekannter Planung) — laeuft das Segment noch?
        # Runde 4 (#3): Jobs bis HEUTE (auch nach 'bis'; ein Bericht wird erst Stunden nach Periodenende eingefroren)
        self.letzter_plan: Dict[str, str] = {}
        if geplant is not None:
            for sid in set(geplant) | set(self.idx) | set(letzter_job):
                tage_s = set(geplant.get(sid) or ()) | set(self.idx.get(sid) or {}) | ({letzter_job[sid]} if sid in letzter_job else set())
                if tage_s:
                    self.letzter_plan[sid] = max(tage_s)
        # Serienende auf Ebene der Fassung (Runde 4 #3): letzter geplanter Tag irgendeines Segments der Fassung — unter
        # Rotation lag der eigene letzte Plan eines Segments oft schon vor dem letzten Lauftag des Auftrags
        self.fassung_letzter = max((self.letzter_plan[sid] for sid in self.segmente_fassung if sid in self.letzter_plan), default="")
        # Beginn der Fassung im geladenen Zeitraum (erstes Tagesdokument) — Serienstart ohne created_at
        alle_eigenen = [t for s in self.idx.values() for t in s]
        self.fassung_start = min(alle_eigenen) if alle_eigenen else ""
        # Befund B17: im vorlaeufigen Bericht (heute gesetzt) sind kuenftige Tage 'offen' — weder erwartet noch
        # Luecke. Heute zaehlt erst, wenn fuer heute schon ein Lauf gespeichert ist (Laeufe verteilen sich ueber den Tag).
        self.heute = heute
        self.offen_ab: Optional[str] = None
        if heute:
            self.offen_ab = _plus(heute, 1) if any(d["date"] == heute for d in im_zeitraum) else heute
        # Befund B3 (fester Segmentkorb): je Segment die Tage mit gueltiger Beobachtung (inkl. EMPTY und Vorlauf),
        # der erste davon und der letzte Tag mit irgendeinem Tagesdokument — fuer fehlende()
        self.gueltig_je_seg = {sid: {t for t, d in s.items() if deals.ist_gueltig(d)} for sid, s in self.idx.items()}
        self.erster_gueltig = {sid: min(g) for sid, g in self.gueltig_je_seg.items() if g}
        self.letzter_doc = {sid: max(s) for sid, s in self.idx.items() if s}
        # Pruefung Runde 2 (#2): Einschaltfenster je Segment der Fassung — erwartet nur, solange es eingeschaltet war
        self.fenster = {sid: self._fenster(sid) for sid in self.segmente_fassung}

    def _fenster(self, sid: str) -> Tuple[str, str]:
        """(erster, letzter) Tag, an dem das Segment im Zeitraum eingeschaltet war. Eine Einschalt-Historie gibt es
        nicht (market_segments kennt nur 'enabled' und 'created_at'), die Grenzen kommen deshalb aus belegbaren Daten:
        Beginn — hat das Segment schon VOR dem Zeitraum ein Tagesdokument dieser Fassung (Vorlauf), lief es schon: kein
        Einschnitt. Sonst ist es im Zeitraum dazugekommen: ab dem Anlagetag (created_at, deutsche Zeit), wenn er im
        Zeitraum liegt — ein neues Segment, dessen erste Laeufe scheitern, bleibt so eine Luecke —, sonst ab dem ersten
        Tagesdokument (wieder eingeschaltet: der erste Lauf ist der frueheste belegbare Einschalttag). Ein eingeschaltetes
        Segment ohne jedes Tagesdokument bleibt fuer alle Tage ab Anlage erwartet (nie gelaufen = Betriebsproblem, B5).
        Ende — ist das Segment heute abgeschaltet, bis zu seinem letzten Tagesdokument: beim Abschalten werden wartende
        Jobs storniert und der Worker prueft 'enabled' vor jedem Lauf, danach entsteht kein Lauf mehr. Ausfaelle direkt
        vor dem Abschalten sind davon nicht zu unterscheiden und zaehlen als abgeschaltet (keine Schein-Luecke)."""
        s = self.idx.get(sid) or {}
        seg = self.segs.get(sid) or {}
        im = sorted(t for t in s if self.von <= t <= self.bis)
        ab, bis = self.von, self.bis
        angelegt = _tag_aus_zeit(seg.get("created_at"))
        if im and not any(t < self.von for t in s):
            ab = angelegt if angelegt and self.von <= angelegt <= im[0] else im[0]
        elif not im and angelegt and angelegt > self.von:
            ab = angelegt
        if im and not seg.get("enabled"):
            bis = im[-1]
        return ab, bis

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

    def _real_geplant(self, sid: str, tag: str) -> bool:
        """Fuer (Segment, Tag) gab es einen geplanten Abruf-Job (siehe _geplant_laden) oder irgendein Tagesdokument."""
        return tag in (self.geplant.get(sid) or ()) or tag in self.idx.get(sid, {})

    def ist_geplant(self, sid: str, tag: str) -> bool:
        """Pruefung Runde 3: war fuer (Segment, Tag) ein Abruf geplant? Ein Abruf-Job (siehe _geplant_laden) oder
        irgendein Tagesdokument (auch ungueltig/POOR — dann lief ein Abruf). Planung unbekannt: immer geplant.
        Runde 4 (#1): an einem Tag, an dem der Tagesplan nicht nachweislich lief (PLAN_AUSFALL: kein Protokoll;
        PLAN_UNBEKANNT: Altdaten ohne jeden Tagesplan-Job; PLAN_OFFEN: heute, Plan steht noch aus), gilt jedes Segment
        als geplant, das an diesem Tag schon lief bzw. angelegt war und noch nicht abgeschaltet — sonst waere ein
        ganztaegiger Ausfall still 'nicht geplant, keine Datenluecke'."""
        if self.geplant is None:
            return True
        if self._real_geplant(sid, tag):
            return True
        if self.tag_status.get(tag) in PLAN_ERWARTET:
            return self._noch_aktiv(sid, tag) and (self.erster_gueltig.get(sid, "9999") < tag or self._existiert(sid, tag))
        return False

    def _aktiv_bis(self, sid: str) -> Optional[str]:
        """Runde 4 (#3): letzter Tag, an dem das Segment noch lief (None = eingeschaltet). Serienende auf Ebene der
        FASSUNG: bis zum letzten geplanten Tag irgendeines Segments der Fassung (Jobs bis heute) bzw. bis zum
        Abschaltzeitpunkt (updated_at beim Deaktivieren, segmente.synchronisieren) — sonst fiele unter Rotation am
        letzten Lauftag ein Teil der Segmente still aus dem Korb, und der Rest wuerde Anker. Pausieren nach Periodenende
        (vor dem Einfrieren) aendert daran nichts: die Jobs der Periode und danach sind bekannt."""
        seg = self.segs.get(sid) or {}
        if seg.get("enabled"):
            return None
        return max(self.fassung_letzter, _tag_aus_zeit(seg.get("updated_at")) or "", self.letzter_plan.get(sid, ""))

    def _noch_aktiv(self, sid: str, tag: str) -> bool:
        """Laeuft das Segment an diesem Tag noch (nur mit bekannter Planung)? Eingeschaltet oder vor dem Serienende der
        Fassung (_aktiv_bis) — danach ist es aus dem Korb (Mix, keine Luecke, nichts getragen)."""
        bis = self._aktiv_bis(sid)
        return bis is None or tag <= bis

    def _existiert(self, sid: str, tag: str) -> bool:
        """Runde 4 (#4): war das Segment an diesem Tag schon angelegt bzw. eingeschaltet (nur mit bekannter Planung)?
        Erster Job <= Tag; sonst Anlage (created_at) <= Tag; ohne created_at der Beginn der Fassung im geladenen Zeitraum
        <= Tag. Runde 4b: auch ein LAENGST angelegtes Segment (created_at vor dem geladenen Fenster), dessen erster Job
        erst spaeter kommt, existiert — Neustart nach mehr als VORLAUF_TAGE ohne Plan (Budget, Crawler aus, Planausfall),
        Wieder-Einschalten eines pausierten Auftrags oder Intervall ueber TRAGEN_MAX_TAGE: sein Wert fehlt ('vorab'),
        der Tag ist kein Anker. Vorher galt es als neu (Mix) — der Teilkorb des ersten Tages wurde still zum Niveau.
        Neu (Mix) ist nur ein Segment, das an diesem Tag nachweislich noch nicht angelegt war."""
        if self.geplant is None:
            return False
        erster = self.erster_job.get(sid)
        if erster and erster <= tag:
            return True
        angelegt = _tag_aus_zeit((self.segs.get(sid) or {}).get("created_at"))
        if angelegt:
            return angelegt <= tag
        return bool(self.fassung_start) and self.fassung_start <= tag

    def _wegen_budget(self, sid: str, tag: str) -> bool:
        """Runde 4 (#1): nicht geplant wegen Budget — Tagesplan mit erschoepftem/keinem Budget oder der Job des Tages
        wurde storniert, nachdem er am Budget gewartet hatte (budget_wait). Beides einheitlich, kein technischer Ausfall."""
        return tag in self.budget_storno.get(sid, ()) or self.tag_status.get(tag) == PLAN_BUDGET

    def _getragen(self, sid: str, tag: str) -> Any:
        """Wert eines an diesem Tag NICHT geplanten Segments fuer den Korbwert: Stand seines letzten geplanten Tages davor
        (hoechstens TRAGEN_MAX_TAGE zurueck). ('basis', Tagesdokument) | ('leer', None) — gueltig leer (Marktluecke) bleibt
        Marktinformation | None — der letzte geplante Tag ist technisch ausgefallen: nichts tragen (das Segment faellt
        an diesem Tag wie ein Ausfall aus dem Vergleich) | ABGELAUFEN — kein geplanter Tag in TRAGEN_MAX_TAGE (Runde 4 #2:
        aus dem wirksamen Korb, KEIN fehlendes Gewicht). Gezaehlt werden nur echt geplante Tage (Job oder Tagesdokument);
        ein Tag ohne Tagesplan (Ausfall) war fuer das Segment nicht geplant und wird uebersprungen."""
        i = self.pos.get(tag)
        if i is None:
            return None
        tage_docs = self.idx.get(sid, {})
        for j in range(i - 1, max(-1, i - 1 - TRAGEN_MAX_TAGE), -1):
            t = self.alle_tage[j]
            if not self._real_geplant(sid, t):
                continue
            d = tage_docs.get(t)
            if d is not None and deals.ist_basis(d):
                return "basis", d
            if d is not None and deals.ist_gueltig(d):
                return "leer", None
            return None
        return ABGELAUFEN

    def seg_erwartet(self, sid: str, tag: str) -> bool:
        """Wird das Segment an diesem (erwarteten) Kalendertag erwartet? Heute im vorlaeufigen Bericht nur, wenn es heute
        schon ein Tagesdokument hat (#1: die Laeufe verteilen sich ueber den Tag — ein noch ausstehender Lauf ist weder
        fehlend noch Teilabdeckung). Mit bekannter Planung (Runde 3) nur an Tagen mit Abruf-Job: ein nicht geplanter
        Segment-Tag (Budget-Rotation, Pause, Crawler aus) ist keine Luecke; ein geplanter ohne gueltigen Lauf schon —
        auch vor dem Abschalten (die Jobs unterscheiden das, das Einschaltfenster nicht). Runde 4: an einem Tag ohne
        Tagesplan-Protokoll jedes laufende Segment (ist_geplant). Ohne Planung (Altdaten, reine Rechnung) wie bisher nur
        im Einschaltfenster (#2)."""
        if tag == self.heute and tag not in self.idx.get(sid, {}):
            return False
        if self.geplant is not None:
            return self.ist_geplant(sid, tag)
        ab, bis = self.fenster.get(sid, (self.von, self.bis))
        return ab <= tag <= bis

    def erwartete_tage_seg(self, sid: str, tage: List[str]) -> List[str]:
        return [t for t in self.erwartete_tage(tage) if self.seg_erwartet(sid, t)]

    def erwartet_je_tag(self, tage: List[str]) -> Dict[str, int]:
        """Erwartete Kalendertage -> Anzahl erwarteter Segmente an diesem Tag (0 = kein Segment erwartet/geplant: der Tag
        zaehlt nicht als erwarteter Tag, Runde 3 #1)."""
        return {t: sum(1 for sid in self.segmente_fassung if self.seg_erwartet(sid, t)) for t in self.erwartete_tage(tage)}

    def korb(self, tage_set: set) -> set:
        """Fester Segmentkorb eines Zeitraums: Segmente der Fassung mit mindestens einem Basistag darin. Runde 4 (#5):
        mit bekannter Planung auch Segmente, die am ersten Tag des Zeitraums noch laufen und einen tragbaren Basiswert aus
        dem Vorlauf haben (letzter geplanter Lauf hoechstens TRAGEN_MAX_TAGE zurueck) — sonst waere unter Rotation der
        erste Tag jeder Periode (vorlaeufig) ein Teilkorb-Anker, und die Zusammensetzung wechselte von Block zu Block
        (5-Tage-Bloecke, Intervalle >= 5)."""
        k = {sid for sid, s in self.idx.items() if any(t in tage_set and deals.ist_basis(d) for t, d in s.items())}
        if self.geplant is not None and tage_set:
            erster = min(tage_set)
            for sid in set(self.idx) - k:
                if self._noch_aktiv(sid, erster):
                    g = self._getragen(sid, erster)
                    if isinstance(g, tuple) and g[0] == "basis":
                        k.add(sid)
            # Runde 4b: auch Segmente der Fassung OHNE Wert — schon angelegt und laufend an einem Tag des Zeitraums, aber
            # im geladenen Fenster nie mit Preisen beobachtet (Serienstart am letzten Periodentag, Neustart, Intervall
            # ueber TRAGEN_MAX_TAGE) oder mit einem Ausfall als letztem geplanten Lauf. Sie fehlen dann im Korb ('vorab'
            # bzw. ohne tragbaren Wert) — sonst deckte der Rest 100 % eines Teilkorbs und wuerde Anker. Nur gueltig LEER
            # beobachtete Segmente (EMPTY, Marktinformation) bleiben wie bisher draussen.
            for sid in self.segmente_fassung - k:
                if self.gueltig_je_seg.get(sid) and not any(deals.ist_basis(d) for d in (self.idx.get(sid) or {}).values()):
                    continue
                if any(self._noch_aktiv(sid, t) and self._existiert(sid, t) for t in tage_set if not self.offen(t)):
                    k.add(sid)
        return k

    def fehlende_ids(self, tag: str, korb: set) -> Fehlende:
        """Korb-Segmente ohne gueltigen Lauf an diesem Tag nach Grund (Befund B3, Runde 3/4). Betrachtet werden Segmente,
        die vorher schon gueltig beobachtet wurden (auch im Vorlauf); EMPTY ist eine gueltige Beobachtung und fehlt nie;
        ein neu hinzukommendes Segment (vorher nie gueltig) ist Mix, keine Luecke — Runde 4 (#4): mit bekannter Planung
        nur, wenn es an diesem Tag noch nicht angelegt war (_existiert; Runde 4b: ein laengst angelegtes, im geladenen
        Fenster nie beobachtetes Segment ist nie neu, sondern fehlt).
          * technisch fehlend: fuer (Segment, Tag) war ein Abruf geplant (Job bzw. Tagesdokument; Runde 4: auch jedes
            laufende Segment an einem Tag ohne Tagesplan) — kein Tagesdokument oder nur ungueltig/POOR. Ohne bekannte
            Planung wie bisher: noch aktiv (eingeschaltet oder spaeter noch ein Tagesdokument)
          * nicht geplant (nur mit bekannter Planung): kein Job an diesem Tag, das Segment laeuft aber noch (Rotation,
            Budget) — keine Luecke, im Korbwert mit dem letzten geplanten Wert (_getragen); budget: davon wegen Budget
          * vorab (Runde 4 #4): schon angelegt/eingeschaltet, noch nie gueltig beobachtet und an diesem Tag nicht geplant
            (Serienstart unter Rotation: der Rest des Korbs kommt am Folgetag) — fehlt im Korb (kein Anker), aber kein
            technischer Ausfall
          * ausstehend (#1): im vorlaeufigen Bericht HEUTE geplant, aber noch ohne jedes Tagesdokument — der Lauf kommt
            evtl. noch; keine Teilabdeckung, fuer den Korbwert aber aus dem Vergleich genommen. Ausstehend kann heute jedes
            Segment der Fassung sein, das vorher schon gueltig lief."""
        tech, aus, ng, vorab, budget = set(), set(), set(), set(), set()
        heute = tag == self.heute
        for sid in (korb | self.segmente_fassung) if heute else korb:
            if tag in self.gueltig_je_seg.get(sid, ()):
                continue
            erster = self.erster_gueltig.get(sid)
            if erster is None or erster >= tag:
                # noch nie gueltig beobachtet: neu (Mix) — ausser das Segment war schon da (bekannte Planung)
                if self.geplant is None or not self._noch_aktiv(sid, tag) or not self._existiert(sid, tag):
                    continue
                if not self.ist_geplant(sid, tag):
                    if sid in korb:
                        vorab.add(sid)
                    continue
            elif self.geplant is None:
                if not (self.segs.get(sid) or {}).get("enabled") and self.letzter_doc.get(sid, "") < tag:
                    continue
            elif not self.ist_geplant(sid, tag):
                if sid in korb and self._noch_aktiv(sid, tag):
                    ng.add(sid)
                    if self._wegen_budget(sid, tag):
                        budget.add(sid)
                continue
            if heute and tag not in self.idx.get(sid, {}):
                aus.add(sid)
            elif sid in korb:
                tech.add(sid)
        return Fehlende(tech, aus, ng, vorab, budget)

    def fehlende(self, tag: str, korb: set) -> int:
        return len(self.fehlende_ids(tag, korb).tech)

    def korbwerte(self, tage: List[str], korb: set) -> Korbwerte:
        """Korbwert je Tag (Pruefung Runde 2 #0, Runde 3 #0, Runde 4) und je Tag mit Tageswert die Zahl der technisch
        fehlenden, (heute) ausstehenden und nicht geplanten Segmente.

        Die fruehere Regel 'Teilabdeckung zaehlt nicht' skaliert nicht: bei 2 % Ausfall je Segment-Tag ist bei 180
        Segmenten fast jeder Tag teilabgedeckt, Minimum/Maximum/Stichproben-Aenderung kaemen aus 0-2 Tagen. Eine
        Quote ('Tag zaehlt ab 80 % des Korbgewichts') verwirft zwar keine Tage mehr, holt aber den Mix-Effekt zurueck
        (fehlt das teuerste von 24 Segmenten, sinkt das Niveau um ~2 % — das Monatsminimum waere der Tag mit dem
        unguenstigsten Ausfall). Deshalb ein verketteter Index ueber den WIRKSAMEN Korb eines Tages:
          * wirksam sind die beobachteten Basis-Segmente plus die nicht geplanten mit ihrem letzten geplanten Wert
            (Runde 3: bei Budget-Rotation laeuft ein Teil der Segmente nur jeden zweiten, dritten ... Tag — ohne das
            waere jeder Tag ein anderer Teilkorb, z. B. abwechselnd die teure und die guenstige Haelfte)
          * nicht geplante Segmente ohne tragbaren Wert (Runde 4 #2: letzter Plan aelter als TRAGEN_MAX_TAGE) fallen aus
            dem wirksamen Korb: auf beiden Seiten des Vergleichs heraus — keine technische Luecke; Runde 4b: ihr Gewicht
            bleibt im Nenner der Abdeckung (Wert fehlt)
          * Anker (Runde 4 #3) = Tag ohne technisch fehlendes/ausstehendes/vorab fehlendes/abgelaufenes Segment, dessen
            Wert mindestens ANKER_MIN_ABDECKUNG des VOLLEN Korbgewichts deckt (neue, noch nicht angelegte, schon
            abgeschaltete und abgelaufene Segmente zaehlen als nicht gedeckt) — Niveau des wirksamen Korbs (ohne Ausfaelle
            und ohne Rotation exakt das beobachtete Tagesniveau)
          * sonst Wert des vorigen Tages mit Wert x Verhaeltnis der Niveaus beider Tage ueber die Segmente, die an
            BEIDEN Tagen einen wirksamen Wert haben (ein technisch fehlendes faellt auf beiden Seiten heraus: kein
            Scheineinbruch, nichts aufgefuellt); vor dem ersten Anker rueckwaerts vom naechsten Tag mit Wert
          * Marktinformation bleibt im Verhaeltnis: ein EMPTY-, neues oder abgeschaltetes Segment steht nur auf einer
            Seite (sample_market_change bleibt die Aenderung der Stichprobe inkl. Mix, Abschnitt 54)
          * gibt es keinen solchen Tag (180 Segmente: 0,98^180 ~ 3 % je Tag), wird der am besten abgedeckte Tag Anker —
            nur, wenn er ANKER_MIN_ABDECKUNG erreicht. Sonst gibt es keinen Anker und alle Korbwerte bleiben leer
            (ehrliche Luecke). Ein Tag mit noch ausstehenden Laeufen (heute) ist nie Anker
        Tage ohne beobachteten Tageswert bleiben Luecken (nicht interpoliert); ein Tag ohne vergleichbares Segment bleibt
        ohne Wert. tragbar: fuer Tage OHNE Basis-Segment der Anteil des Korbgewichts, der einen tragbaren Wert haette
        (nur Confidence 'gueltig getragener Wert', nie ein Tageswert)."""
        basis = {t: self.basis_tag(t) for t in tage}
        mit = [t for t in tage if basis[t]]
        tage_set = set(tage)
        # Korbgewicht je Segment: mittlere Stichprobe seiner Basistage im Zeitraum (wie das Niveau gewichtet); ein nur
        # getragenes Segment (Runde 4 #5) mit der mittleren Stichprobe seiner geladenen Basistage
        gewicht: Dict[str, float] = {}
        ohne_n: List[str] = []
        for sid in korb:
            s = self.idx.get(sid, {})
            ns = ([_n(d) for t, d in s.items() if t in tage_set and deals.ist_basis(d)]
                  or [_n(d) for d in s.values() if deals.ist_basis(d)])
            if ns:
                gewicht[sid] = sum(ns) / len(ns)
            else:
                ohne_n.append(sid)
        # Runde 4b: ein Korb-Segment ohne jede Preisbeobachtung (Serienstart, Neustart) zaehlt mit dem mittleren Gewicht
        # der uebrigen — mit 1,0 fiele sein Fehlen bei grossen Stichproben unter die Grenze von 5 %
        mittel = (sum(gewicht.values()) / len(gewicht)) if gewicht else 1.0
        for sid in ohne_n:
            gewicht[sid] = mittel
        wirksam: Dict[str, Dict[str, Dict[str, Any]]] = {}
        weg: Dict[str, set] = {}
        luecke: Dict[str, bool] = {}
        fehlend: Dict[str, int] = {}
        ausstehend: Dict[str, int] = {}
        nicht_geplant: Dict[str, int] = {}
        getragen: Dict[str, int] = {}
        budget: Dict[str, int] = {}
        abgelaufen: Dict[str, int] = {}
        vorab: Dict[str, int] = {}
        abdeckung: Dict[str, float] = {}
        for t in mit:
            f = self.fehlende_ids(t, korb)
            eff = dict(basis[t])
            ohne = f.tech | f.aus | f.vorab
            da = {sid for sid in korb if t in self.gueltig_je_seg.get(sid, ())}       # beobachtet (Basis oder EMPTY)
            raus: set = set()
            n_getragen = 0
            for sid in f.ng:
                g = self._getragen(sid, t)
                if g is None:
                    ohne.add(sid)
                    continue
                if g == ABGELAUFEN:
                    raus.add(sid)
                    continue
                da.add(sid)
                if g[0] == "basis":
                    eff[sid] = g[1]
                    n_getragen += 1
            fehlend[t], ausstehend[t], nicht_geplant[t], getragen[t] = len(f.tech), len(f.aus), len(f.ng), n_getragen
            budget[t], abgelaufen[t], vorab[t] = len(f.budget), len(raus), len(f.vorab)
            wirksam[t], weg[t], luecke[t] = eff, ohne | raus, bool(ohne)
            # voller Korb (Runde 4 #3): alle Korb-Segmente — neu/abgeschaltet zaehlen als nicht gedeckt. Runde 4b: auch
            # abgelaufene (Intervall > TRAGEN_MAX_TAGE) bleiben im Nenner — ihr Wert fehlt; ohne sie im Nenner wurde der
            # am besten abgedeckte Tag Ersatz-Anker mit einem Teilkorb-Niveau (Intervall 21: +2,8 %, Kette bis -8,7 %)
            w_da = sum(gewicht[sid] for sid in da)
            w_voll = sum(gewicht.values())
            abdeckung[t] = (w_da / w_voll) if w_voll > 0 else 0.0

        def _niveau(t: str, ohne: set) -> Optional[float]:
            return _gewichtet([(float(d["median_price"]), _n(d)) for sid, d in wirksam[t].items() if sid not in ohne])

        def _faktor(a: str, b: str) -> Optional[float]:
            ohne = weg[a] | weg[b]
            x, y = _niveau(a, ohne), _niveau(b, ohne)
            return (y / x) if x and y is not None else None
        kandidaten = [t for t in mit if not ausstehend[t]]
        anker = [t for t in kandidaten if not luecke[t] and not abgelaufen[t] and abdeckung[t] >= ANKER_MIN_ABDECKUNG]
        if not anker and kandidaten:
            best = min(kandidaten, key=lambda t: (-abdeckung[t], t))
            anker = [best] if abdeckung[best] >= ANKER_MIN_ABDECKUNG else []
        wert: Dict[str, float] = {}
        for t in anker:
            v = _niveau(t, set())
            if v is not None:
                wert[t] = v
        for i in range(1, len(mit)):                    # vorwaerts vom letzten Tag mit Wert
            t, vor = mit[i], mit[i - 1]
            if t not in wert and vor in wert:
                f = _faktor(vor, t)
                if f is not None:
                    wert[t] = wert[vor] * f
        for i in range(len(mit) - 2, -1, -1):           # vor dem ersten Anker: rueckwaerts
            t, nach = mit[i], mit[i + 1]
            if t not in wert and nach in wert:
                f = _faktor(t, nach)
                if f:
                    wert[t] = wert[nach] / f
        # Tage ohne Basis-Segment: welcher Anteil des Korbs haette einen tragbaren Wert (nur fuer die Confidence)
        tragbar: Dict[str, float] = {}
        w_korb = sum(gewicht.values())
        if self.geplant is not None and w_korb > 0:
            for t in tage:
                if basis[t] or self.offen(t):
                    continue
                w = sum(gewicht[sid] for sid in korb if self._noch_aktiv(sid, t) and isinstance(self._getragen(sid, t), tuple))
                tragbar[t] = w / w_korb
        # Runde 4b (Budget danach, ganzer Tag): auch an Tagen OHNE Basis-Segment die Segmente, die wegen Budget nicht
        # geplant waren (Storno mit budget_wait bzw. Budget-Tag) — sonst stand ein ganztaegiger Budget-Storno nirgends
        if self.geplant is not None:
            for t in tage:
                if basis[t] or self.offen(t):
                    continue
                n = sum(1 for sid in korb | self.segmente_fassung
                        if t not in self.gueltig_je_seg.get(sid, ()) and self._noch_aktiv(sid, t) and self._wegen_budget(sid, t)
                        and (self.erster_gueltig.get(sid, "9999") < t or self._existiert(sid, t)) and not self.ist_geplant(sid, t))
                if n:
                    budget[t] = n
        return Korbwerte({t: wert.get(t) for t in tage}, fehlend, ausstehend, nicht_geplant, getragen, abdeckung,
                         [t for t in anker if t in wert], mit, len(korb), budget, abgelaufen, vorab, kandidaten, tragbar,
                         wirksam, gewicht)


def _ausschnitt(kw: Korbwerte, tage: List[str]) -> Korbwerte:
    """Korbwerte eines Zeitraums auf einen Teil seiner Tage beschraenkt (5-Tage-Bloecke im 15-Tage-/Monatsbericht,
    Runde 4 #5): derselbe Korb und dieselbe Kette wie die Periode — die Zusammensetzung wechselt nicht von Block zu
    Block, und die Rechnung laeuft nur einmal."""
    ts = set(tage)

    def _d(x: Dict[str, Any]) -> Dict[str, Any]:
        return {t: v for t, v in x.items() if t in ts}
    return Korbwerte(_d(kw.werte), _d(kw.fehlend), _d(kw.ausstehend), _d(kw.nicht_geplant), _d(kw.getragen), _d(kw.abdeckung),
                     [t for t in kw.anker if t in ts], [t for t in kw.mit if t in ts], kw.korb_n, _d(kw.budget), _d(kw.abgelaufen),
                     _d(kw.vorab), [t for t in kw.kandidaten if t in ts], _d(kw.tragbar), _d(kw.wirksam), kw.gewicht)


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


def kennzahlen(daten: _Daten, tage: List[str], kosten_docs: List[Dict[str, Any]], zone: float = STABIL_PCT,
               korbwerte: Optional[Korbwerte] = None) -> Dict[str, Any]:
    """Kennzahlen eines Zeitraums (Periode oder 5-Tage-Block) — nur Tage der gerechneten Fassung. korbwerte: schon
    gerechnetes Ergebnis von daten.korbwerte fuer genau diese Tage (spart die zweite Rechnung)."""
    tage_set = set(tage)
    docs = [d for s in daten.idx.values() for t, d in s.items() if t in tage_set]
    gueltig = [d for d in docs if deals.ist_gueltig(d)]
    basis = [d for d in gueltig if deals.ist_basis(d)]
    kw = korbwerte if korbwerte is not None else daten.korbwerte(tage, daten.korb(tage_set))
    # Segmentkorb: je Segment erster und letzter gueltiger Tag im Zeitraum (mind. zwei verschiedene Tage)
    korb = []
    for seg_id, tage_docs in daten.idx.items():
        bt = sorted(t for t, d in tage_docs.items() if t in tage_set and deals.ist_basis(d))
        if len(bt) >= 2:
            w = sum(_n(tage_docs[t]) for t in bt) / len(bt)
            korb.append((tage_docs[bt[0]], tage_docs[bt[-1]], w))
    # Runde 4b: mit bekannter Planung Start/Ende ueber die WIRKSAMEN Werte (beobachtet oder getragen, wie der Korbwert):
    # je Korb-Segment der wirksame Wert am ersten und letzten Tag, an dem es einen hat (mind. zwei Tage mit Wert). Nur
    # beobachtete Basistage liessen unter Rotation/SAFE_AUTO-Intervallen (>= 3 Tage) die selten abgerufenen Segmente
    # heraus — Start/Ende waren ein Teilkorb (24 Segmente, 10 im Start-/Endkorb: 17.900 statt 23.500 EUR)
    wkorb = []
    if daten.geplant is not None:
        for sid, w in kw.gewicht.items():
            wt = [t for t in tage if sid in kw.wirksam.get(t, {})]
            if len(wt) >= 2:
                wkorb.append((kw.wirksam[wt[0]][sid], kw.wirksam[wt[-1]][sid], w))
    sk = wkorb if daten.geplant is not None else korb
    start = _gewichtet([(float(a["median_price"]), w) for a, _, w in sk])
    ende = _gewichtet([(float(b["median_price"]), w) for _, b, w in sk])
    delta_eur = (ende - start) if (start is not None and ende is not None) else None
    delta_pct = (delta_eur / start * 100) if (delta_eur is not None and start) else None
    # deckt der Start-/Endkorb weniger als ANKER_MIN_ABDECKUNG des Korbgewichts (Serienstart, Intervall ueber
    # TRAGEN_MAX_TAGE), waeren Start/Ende in Euro ein Teilkorb-Niveau: leer; die Aenderung in % (derselbe Korb an
    # beiden Enden) und die Richtung bleiben
    w_ges = sum(kw.gewicht.values())
    start_ende_anteil = (sum(w for _, _, w in wkorb) / w_ges) if (daten.geplant is not None and w_ges > 0) else None
    start_ende_teilkorb = start_ende_anteil is not None and start is not None and start_ende_anteil < ANKER_MIN_ABDECKUNG
    # gleiche Inserate (Abschnitt 54): Preis am ersten und letzten gueltigen (beobachteten) Tag desselben Segments
    paare = []
    for a, b, _ in korb:
        pa, pb = _preise(a), _preise(b)
        paare += [(pa[lid], pb[lid]) for lid in set(pa) & set(pb)]
    gleich_eur = _r(sum(y - x for x, y in paare) / len(paare)) if paare else None
    gleich_pct = _r(sum(y - x for x, y in paare) / sum(x for x, _ in paare) * 100, 3) if paare and sum(x for x, _ in paare) else None
    # Tageswerte (Mix inkl.) fuer Median/Mittel/Min/Max und sample_market_change aus dem Korbwert (Befund B3,
    # Pruefung Runde 2 #0): vollstaendige Tage = beobachtetes Niveau; fehlt ein Segment technisch, ist der Tageswert
    # ueber die an beiden Vergleichstagen vorhandenen Segmente verkettet — weder Scheineinbruch noch verworfener Tag.
    # Runde 3 #0: nicht geplante Segmente (Rotation) mit ihrem letzten geplanten Wert; ohne Anker mit Mindestabdeckung
    # bleiben alle Euro-Niveauwerte leer. Teilabgedeckte Tage bleiben in der Tagestabelle markiert (roh + Korbwert).
    werte_korb, fehlend = kw.werte, kw.fehlend
    teil = {t for t, n in fehlend.items() if n}
    mit = [(t, werte_korb[t]) for t in tage if werte_korb.get(t) is not None]
    werte = [v for _, v in mit]
    smc_eur = _r(mit[-1][1] - mit[0][1]) if len(mit) >= 2 else None
    smc_pct = _r((mit[-1][1] - mit[0][1]) / mit[0][1] * 100, 3) if len(mit) >= 2 and mit[0][1] else None
    tief = min(mit, key=lambda x: x[1]) if mit else None
    hoch = max(mit, key=lambda x: x[1]) if mit else None
    # guenstigstes Angebot = echte Einzelbeobachtung, auch an teilabgedeckten Tagen (ein fehlendes Segment verbirgt
    # hoechstens ein Angebot, erfindet keins); bei Gleichstand der fruehere Tag
    billig: Optional[Tuple[str, float]] = None
    for d in basis:
        if d.get("min_price") is not None and (billig is None or (float(d["min_price"]), d["date"]) < (billig[1], billig[0])):
            billig = (d["date"], float(d["min_price"]))
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
    # Segmentabdeckung (Befund B5): gueltige Segment-Tage gegen erwartete Segment-Tage. Erwartet wird je Segment nur
    # in seinem Einschaltfenster (Pruefung Runde 2 #2: bewusstes Zu-/Abschalten ist keine Datenluecke) bzw. mit
    # bekannter Planung nur an Tagen mit Abruf-Job (Runde 3 #0) und heute im vorlaeufigen Bericht nur, wenn es schon
    # gelaufen ist (#1). Runde 3 #1: ein Kalendertag, an dem kein Segment erwartet war (Suchauftrag pausiert oder neu
    # angelegt, kein Job fuer das Modell), ist kein erwarteter Tag — sonst 'Abdeckung 9/30' fuer einen am 10. pausierten
    # Auftrag mit 216/216 Segment-Tagen
    je_tag = daten.erwartet_je_tag(tage)
    erwartete = {t for t, n in je_tag.items() if n > 0}
    erwartet = len(erwartete)
    tage_ohne_plan = len(je_tag) - erwartet
    abgedeckt = sorted({d["date"] for d in gueltig if d["date"] in erwartete})
    gueltig_je_tag: Dict[str, int] = {}
    for d in gueltig:
        if d["date"] in je_tag:
            gueltig_je_tag[d["date"]] = gueltig_je_tag.get(d["date"], 0) + 1
    segment_tage = sum(gueltig_je_tag.values())
    segment_tage_erwartet = sum(je_tag.values())
    # fehlende Segment-Tage an Tagen, an denen das Modell Daten hat (ganze Luecken-Tage stehen schon in der Abdeckung)
    segment_luecken = sum(max(0, je_tag[t] - n) for t, n in gueltig_je_tag.items())
    # Runde 4 (#6): Kalendertage der Periode (bis heute, ab Beginn der Fassung) mit gueltigem Lauf oder gueltig
    # getragenem Wert — getragen zaehlt nur ein Tag OHNE Plan (keine Luecke), an dem mindestens ANKER_MIN_ABDECKUNG
    # des Korbs einen tragbaren Wert hat (Block-Planung unter Rotation); ein technisch ausgefallener Tag nie
    kalender = daten.erwartete_tage(tage)
    getragene_tage = [t for t, n in je_tag.items() if n == 0 and kw.tragbar.get(t, 0.0) >= ANKER_MIN_ABDECKUNG]
    tage_mit_wert = len(abgedeckt) + len(getragene_tage)
    # Runde 4b: mit Segment-Tagen ohne tragbaren Wert (Intervall ueber TRAGEN_MAX_TAGE) ist der Bericht nie 'hoch' —
    # vorher HIGH ohne Gruende neben einem Teilkorb-Niveau
    einschraenkungen = []
    if sum(kw.abgelaufen.values()):
        einschraenkungen.append(f"{sum(kw.abgelaufen.values())} Segment-Tag(e) ohne tragbaren Wert (Intervall über "
                                f"{TRAGEN_MAX_TAGE} Tage)")
    conf, conf_gruende = confidence(len(abgedeckt), erwartet, dq, tiefe, len(inserate), segment_tage, segment_tage_erwartet,
                                    kalendertage=len(kalender), tage_mit_wert=tage_mit_wert, tage_ohne_plan=tage_ohne_plan,
                                    einschraenkungen=einschraenkungen)
    # Runde 4 (#1): Planungsstand der Kalendertage (nur mit bekannter Planung). Ausfall/unbekannt zaehlen nur, wenn an
    # dem Tag ein Segment dieses Modells erwartet war (sonst lief das Modell ohnehin nicht — keine Luecke)
    status = [st for t in kalender if t != daten.heute
              for st in [daten.tag_status.get(t, PLAN_LIEF)]
              if st not in (PLAN_AUSFALL, PLAN_UNBEKANNT) or je_tag.get(t, 0) > 0] if daten.geplant is not None else []
    # Runde 4b: ganztaegig wegen Budget nicht geplant — Budget vor dem Plan (PLAN_BUDGET) ODER der Plan lief, aber kein
    # Segment des Modells war erwartet und die Jobs des Tages scheiterten am Budget (Storno mit budget_wait)
    tage_budget = sum(1 for t in kalender if t != daten.heute and daten.geplant is not None
                      and (daten.tag_status.get(t, PLAN_LIEF) == PLAN_BUDGET
                           or (je_tag.get(t, 0) == 0 and daten.tag_status.get(t, PLAN_LIEF) == PLAN_LIEF and kw.budget.get(t, 0) > 0)))
    segs_mit = {d["segment_id"] for d in gueltig}
    leere_segmente = sum(1 for s in segs_mit if not any(d["segment_id"] == s for d in basis))
    kosten = round(sum(float(d.get("crawl_cost_usd") or 0) for d in kosten_docs if d["date"] in tage_set), 4)
    # Kosteneffizienz (Abschnitt 38, Befund B7): Zaehler und Nenner auf derselben Basis — Kosten der gerechneten
    # Fassung (inkl. ihrer ungueltigen Laeufe) je gueltiger Beobachtung (inkl. EMPTY wie deals.ist_gueltig), je
    # Inserat und je Hot Deal derselben Fassung. kosten_usd bleibt die reale Ausgabe ueber alle Fassungen.
    kosten_fassung = round(sum(float(d.get("crawl_cost_usd") or 0) for d in docs), 4)
    beobachtungen = len(gueltig)
    # Runde 3 (hotdeals#0): Tage, deren Hot-Deal-Auswertung nach wiederholten Fehlern aufgegeben wurde — ihre Hot Deals
    # fehlen im Bericht (die Berichte warten auf solche Tage nicht mehr)
    hot_aufgegeben = sum(1 for d in docs if (d.get("hot_deals") or {}).get("grund") == deals.GRUND_AUSWERTUNG_FEHLER)
    return {
        "startwert": None if start_ende_teilkorb else _r(start), "endwert": None if start_ende_teilkorb else _r(ende),
        "delta_eur": None if start_ende_teilkorb else _r(delta_eur), "delta_pct": _r(delta_pct, 3),
        "richtung": richtung(delta_pct, zone), "korb_segmente": len(sk),
        "start_ende_korb_pct": _r(start_ende_anteil * 100, 1) if start_ende_anteil is not None else None,
        "sample_market_change_eur": smc_eur, "sample_market_change_pct": smc_pct,
        "same_listing_price_change_eur": gleich_eur, "same_listing_price_change_pct": gleich_pct, "same_listing_anzahl": len(paare),
        "median_periode": _r(statistics.median(werte)) if werte else None, "mittelwert_periode": _r(sum(werte) / len(werte)) if werte else None,
        "minimum": {"date": tief[0], "wert": _r(tief[1])} if tief else None,
        "maximum": {"date": hoch[0], "wert": _r(hoch[1])} if hoch else None,
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
        "segment_tage_gueltig": segment_tage, "segment_tage_erwartet": segment_tage_erwartet, "segment_luecken": segment_luecken,
        "teilabgedeckte_tage": len(teil), "niveau_tage": len(mit),
        # Runde 3: Planung (Abruf-Jobs gelesen oder unbekannt), Anker, Rotation, Tage ohne erwartetes Segment
        "planung": "jobs" if daten.geplant is not None else "unbekannt",
        "anker_tage": len(kw.anker), "anker_abdeckung_pct": _r(min(kw.abdeckung[t] for t in kw.anker) * 100, 1) if kw.anker else None,
        "tage_mit_basis": len(kw.mit), "tage_ohne_plan": tage_ohne_plan,
        "nicht_geplante_segment_tage": sum(kw.nicht_geplant.values()), "getragene_segment_tage": sum(kw.getragen.values()),
        "hot_deal_tage_aufgegeben": hot_aufgegeben,
        # Runde 4: Tagesplan-Protokoll, Budget, Korb am Serienanfang, abgelaufene Werte, Kalendertage mit Wert
        "tage_tagesplan_ausfall": status.count(PLAN_AUSFALL), "tage_planung_unbekannt": status.count(PLAN_UNBEKANNT),
        "tage_crawler_aus": status.count(PLAN_AUS), "tage_budget": tage_budget,
        "budget_segment_tage": sum(kw.budget.values()), "abgelaufene_segment_tage": sum(kw.abgelaufen.values()),
        "vorab_segment_tage": sum(kw.vorab.values()), "korb_wirksam_segmente": kw.korb_n,
        "kalendertage": len(kalender), "tage_mit_wert": tage_mit_wert, "getragene_tage": len(getragene_tage),
        "anker_kandidaten": len(kw.kandidaten), "tage_heute_ausstehend": sum(1 for t in kw.mit if kw.ausstehend.get(t)),
        "segmente_mit_daten": len(segs_mit), "segmente_gesamt": len(daten.segmente_fassung), "empty_segmente": leere_segmente,
        "gueltige_beobachtungen": beobachtungen, "kosten_usd": kosten, "kosten_fassung_usd": kosten_fassung,
        "cost_per_valid_observation": round(kosten_fassung / beobachtungen, 4) if beobachtungen else None,
        "cost_per_unique_listing": round(kosten_fassung / len(inserate), 4) if inserate else None,
        "cost_per_hot_deal": round(kosten_fassung / len(hot), 4) if hot else None,
    }


def _tageszeilen(daten: _Daten, tage: List[str], kosten_docs: List[Dict[str, Any]], zone: float,
                 korbwerte: Korbwerte) -> Tuple[List[Dict[str, Any]], Dict[str, Any]]:
    bewegungen: Dict[str, Optional[Dict[str, Any]]] = {}
    zeilen = []
    werte_korb, fehlend_je_tag, ausstehend_je_tag = korbwerte.werte, korbwerte.fehlend, korbwerte.ausstehend
    anker = set(korbwerte.anker)
    for t in tage:
        docs = daten.docs_tag(t)
        basis = daten.basis_tag(t)
        gueltig = [d for d in docs if deals.ist_gueltig(d)]
        vortag = daten.basis_tag(_plus(t, -1))
        b = tagesbewegung(basis, vortag) if basis and vortag else None
        bewegungen[t] = b
        nv = niveau(list(basis.values()))
        # Befund B3: Tageswert mit technisch fehlenden Segmenten = Teilabdeckung; median bleibt der rohe Tageswert,
        # median_korb ist der verkettete Korbwert (Periodenkennzahlen, Diagrammlinie — Schema 2)
        fehlend = fehlend_je_tag.get(t, 0)
        mit_vergleich = [d for d in gueltig if d.get("vergleich_vortag")]
        dq: Dict[str, int] = {}
        for d in docs:
            q = deals.qualitaet(d)
            dq[q] = dq.get(q, 0) + 1
        zeilen.append({
            "date": t, **nv, "median_korb": _r(werte_korb.get(t)), "delta_vortag_eur": (b or {}).get("eur"), "delta_vortag_pct": (b or {}).get("pct"),
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
            # nur vorlaeufig, heute: Segmente ohne Lauf, die heute noch laufen koennen (keine Teilabdeckung, #1)
            "ausstehende_segmente": ausstehend_je_tag.get(t, 0),
            # Runde 3: an diesem Tag nicht geplante Korb-Segmente (Rotation, keine Luecke — im Korbwert mit dem letzten
            # geplanten Wert), Anteil des Korbgewichts mit Wert, Ankertag (beobachtetes Niveau statt verkettet)
            "nicht_geplante_segmente": korbwerte.nicht_geplant.get(t, 0),
            "korb_abdeckung_pct": _r(korbwerte.abdeckung[t] * 100, 1) if t in korbwerte.abdeckung else None,
            "anker": t in anker,
            # Runde 4: Planungsstand des Tages (Tagesplan-Protokoll), davon wegen Budget nicht geplant, ohne tragbaren
            # Wert aus dem Korb gefallen, schon angelegt aber noch nicht beobachtet (Serienstart)
            "plan_status": (daten.tag_status.get(t, PLAN_LIEF) if daten.geplant is not None and not daten.offen(t) else None),
            "budget_segmente": korbwerte.budget.get(t, 0), "abgelaufene_segmente": korbwerte.abgelaufen.get(t, 0),
            "vorab_segmente": korbwerte.vorab.get(t, 0),
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
                     "gueltige_tage": len(gueltig), "erwartete_tage": len(daten.erwartete_tage_seg(seg_id, tage)),
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
                    heute: Optional[str] = None, geplant: Optional[Dict[str, set]] = None,
                    planung: Optional[Dict[str, Any]] = None) -> Optional[Dict[str, Any]]:
    """Reine Rechnung (ohne Datenbank) — derselbe Code fuer finale und vorlaeufige Berichte. None ohne Tagesdaten.
    heute (nur vorlaeufig, deutscher Kalendertag): spaetere Tage sind 'offen' statt fehlend (Befund B17).
    geplant (Pruefung Runde 3): Segment -> Kalendertage mit Abruf-Job (_geplant_laden); None = Planung unbekannt.
    planung (Pruefung Runde 4): Tagesplan-Protokoll und Job-Zusatzdaten (_planung_laden); None = jeder Tag 'lief'."""
    daten = _Daten(segs, docs, von, bis, heute, geplant, planung)
    if not daten.im_zeitraum or daten.haupt is None:
        return None
    tage = tage_zwischen(von, bis)
    kosten_docs = [d for d in docs if von <= d["date"] <= bis]          # Kosten ueber ALLE Fassungen (reale Ausgaben)
    korbwerte = daten.korbwerte(tage, daten.korb(set(tage)))            # einmal fuer Tagestabelle und Periodenkennzahlen
    zeilen, bewegungen = _tageszeilen(daten, tage, kosten_docs, zone, korbwerte)
    kz = kennzahlen(daten, tage, kosten_docs, zone, korbwerte)
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
            # Runde 4 (#5): derselbe Korb und dieselbe Kette wie die Periode (stabil ueber die Bloecke)
            k = kennzahlen(daten, bt, kosten_docs, zone, _ausschnitt(korbwerte, bt))
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
        dat = _Daten(segs, [d for d in docs if daten.fassung(d) == f], von, bis, geplant=geplant, planung=planung)
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
    # nur echte Segment-Luecken an Tagen mit Daten (nicht abgeschaltete, nicht heute noch ausstehende Segmente)
    if kz["segment_luecken"]:
        bericht["hinweise"].append(f"{kz['segment_tage_gueltig']} / {kz['segment_tage_erwartet']} gültige Segment-Tage "
                                   f"({kz['segmente_gesamt']} Segmente) — fehlende Segmente werden nicht aufgefüllt")
    if kz["teilabgedeckte_tage"]:
        bericht["hinweise"].append(f"{kz['teilabgedeckte_tage']} Tag(e) mit Teilabdeckung (Segmente ohne gültigen Lauf) — für "
                                   "Median, Minimum, Maximum und Stichproben-Änderung der Periode ist ihr Tageswert über die an "
                                   "beiden Vergleichstagen vorhandenen Segmente verkettet (kein Scheineinbruch, nichts aufgefüllt)")
    # Pruefung Runde 3
    # Pruefung Runde 4 (#1): Tagesplan-Protokoll
    if kz["tage_tagesplan_ausfall"]:
        bericht["hinweise"].append(f"Tagesplan lief nicht: {kz['tage_tagesplan_ausfall']} Kalendertag(e) ohne Tagesplan-Protokoll (z. B. "
                                   "fehlender Scraper-Token, ganztägige Wartung, Fehler im Planer oder beide Server aus) — technischer "
                                   "Ausfall, die Tage zählen als erwartete Tage (Lücke)")
    if kz["tage_planung_unbekannt"]:
        bericht["hinweise"].append(f"Planung unbekannt: {kz['tage_planung_unbekannt']} Kalendertag(e) vor Einführung des Tagesplan-"
                                   "Protokolls ohne jeden Tagesplan-Job — zählen als erwartete Tage")
    if kz["tage_crawler_aus"]:
        bericht["hinweise"].append(f"Crawler bewusst ausgeschaltet: {kz['tage_crawler_aus']} Kalendertag(e) ohne Planung — keine "
                                   "Datenlücke, zählen nicht als erwartete Tage")
    if kz["tage_budget"] or kz["budget_segment_tage"]:
        bericht["hinweise"].append(f"Nicht geplant wegen Budget: {kz['tage_budget']} Kalendertag(e) mit aufgebrauchtem bzw. fehlendem "
                                   f"Monatsbudget, {kz['budget_segment_tage']} Segment-Tag(e) im Korb ohne Abruf wegen Budget — keine "
                                   "technische Lücke; im Korbwert steht dort der letzte geplante Wert")
    if kz["tage_ohne_plan"]:
        bericht["hinweise"].append(f"{kz['tage_ohne_plan']} Kalendertag(e) ohne geplanten Abruf dieses Modells (Budget-Rotation, "
                                   "Budget, Pause, neu angelegt oder Crawler aus — laut Tagesplan-Protokoll entschieden) — keine "
                                   "Datenlücke, zählen nicht als erwartete Tage")
    if kz["nicht_geplante_segment_tage"]:
        bericht["hinweise"].append(f"Budget-Rotation: {kz['nicht_geplante_segment_tage']} Segment-Tag(e) ohne geplanten Abruf — keine "
                                   f"Lücke; im Korbwert steht dort der letzte geplante Wert des Segments ({kz['getragene_segment_tage']} "
                                   f"Segment-Tage, höchstens {TRAGEN_MAX_TAGE} Tage alt), technische Ausfälle werden nie aufgefüllt")
    if kz["abgelaufene_segment_tage"]:
        bericht["hinweise"].append(f"{kz['abgelaufene_segment_tage']} Segment-Tag(e) ohne tragbaren Wert (letzter geplanter Lauf älter "
                                   f"als {TRAGEN_MAX_TAGE} Tage, Intervall über {konfig.MAX_INTERVALL_TAGE} Tage — z. B. weil das "
                                   "Budget nicht für jedes Segment alle 14 Tage reicht) — keine technische Lücke, aber ihr Wert fehlt "
                                   "im Korb: fehlt mehr als "
                                   f"{round((1 - ANKER_MIN_ABDECKUNG) * 100)} % des Korbgewichts, ist der Tag kein Anker")
    if kz["startwert"] is None and kz["start_ende_korb_pct"] is not None and kz["delta_pct"] is not None:
        bericht["hinweise"].append(f"Start-/Endwert nur über {kz['start_ende_korb_pct']} % des Korbgewichts — die Euro-Werte bleiben "
                                   "leer (Teilkorb), die Änderung in % und die Richtung beziehen sich auf diesen Teilkorb")
    if kz["vorab_segment_tage"]:
        bericht["hinweise"].append(f"Serienstart: {kz['vorab_segment_tage']} Segment-Tag(e) von schon angelegten, aber noch nicht "
                                   "beobachteten Segmenten — diese Tage sind kein Anker, ihr Wert ist vom Folgetag rückwärts verkettet")
    if kz["tage_mit_basis"] and not kz["anker_tage"]:
        # Runde 4 (#5): stehen nur noch Laeufe von heute aus, ist das der Grund — nicht die Korbabdeckung
        if kz["tage_heute_ausstehend"]:
            bericht["hinweise"].append("Läufe von heute stehen noch aus — Median, Mittel, Minimum, Maximum und Stichproben-Änderung "
                                       "folgen, sobald ein Tag mit vollständigem Korb vorliegt")
        if kz["anker_kandidaten"]:
            bericht["hinweise"].append(f"Kein Tag mit ausreichender Korbabdeckung (mindestens {round(ANKER_MIN_ABDECKUNG * 100)} % des "
                                       "Korbgewichts mit Wert) — Median, Mittel, Minimum, Maximum und Stichproben-Änderung der Periode "
                                       "bleiben leer (ehrliche Lücke statt Teilkorb-Niveau)")
    if kz["hot_deal_tage_aufgegeben"]:
        bericht["hinweise"].append(f"{kz['hot_deal_tage_aufgegeben']} Tageswert(e) ohne Hot-Deal-Auswertung (nach wiederholten Fehlern "
                                   "aufgegeben) — Hot Deals können im Bericht fehlen")
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


def _jobs_pipeline(seg_ids: List[str], von: str, bis: str, bis_jetzt: Optional[str] = None) -> List[Dict[str, Any]]:
    """Abruf-Jobs der Segmente eines Modells im geladenen Zeitraum (Vorlauf + Periode), serverseitig je Segment zu
    den geplanten Kalendertagen verdichtet (Feld tag 'YYYY-MM-DD', zweiter/manueller Lauf mit '#...'). Die
    Bereichsabfrage (segment_id $in, tag von..bis) laeuft ueber den Unique-Index (segment_id, tag) — zurueck kommen
    hoechstens so viele kleine Dokumente wie Segmente (180 x <= 45 Tage), nie die Jobs selbst.
    Runde 4: geplant = nicht storniert oder 'Tagesplan veraltet' OHNE budget_wait (nie gelaufen: technisch); budget =
    'Tagesplan veraltet' MIT budget_wait (am Budget gescheitert: nicht geplant wegen Budget); plan = Tage mit einem
    Tagesplan-Job (Tag ohne '#', jeder Status: der Plan lief); erster = erster Job-Tag im Zeitraum; letzter = letzter
    geplanter Tag bis bis_jetzt (heute) — das Serienende der Fassung haengt auch an Jobs nach 'bis'."""
    alt = {"$eq": [{"$substrCP": [{"$ifNull": ["$error", ""]}, 0, len(STORNO_TECHNISCH)]}, STORNO_TECHNISCH]}
    wartete = {"$eq": [{"$ifNull": ["$budget_wait", False]}, True]}
    budget = {"$and": [{"$eq": ["$status", "cancelled"]}, alt, wartete]}
    geplant = {"$or": [{"$ne": ["$status", "cancelled"]}, {"$and": [alt, {"$not": [wartete]}]}]}
    ende = _plus(bis, 1)
    im = {"$lt": ["$tag", ende]}
    tag10 = {"$substrCP": ["$tag", 0, 10]}
    return [{"$match": {"segment_id": {"$in": list(seg_ids)}, "tag": {"$gte": _plus(von, -VORLAUF_TAGE),
                                                                      "$lt": _plus(max(bis, bis_jetzt or bis), 1)}}},
            {"$group": {"_id": "$segment_id", "jobs": {"$sum": {"$cond": [im, 1, 0]}},
                        "tage": {"$addToSet": {"$cond": [{"$and": [im, geplant]}, tag10, None]}},
                        "budget": {"$addToSet": {"$cond": [{"$and": [im, budget]}, tag10, None]}},
                        "plan": {"$addToSet": {"$cond": [{"$and": [im, {"$eq": [{"$strLenCP": "$tag"}, 10]}]}, tag10, None]}},
                        "erster": {"$min": {"$cond": [im, tag10, None]}},
                        "letzter": {"$max": {"$cond": [geplant, tag10, None]}}}}]


async def _jobs_laden(db, seg_ids: List[str], von: str, bis: str, bis_jetzt: Optional[str] = None) -> Optional[Dict[str, Any]]:
    """Pruefung Runde 3/4: die Abruf-Jobs der Segmente (konfig.JOBS, nur lesen — dieses Modul importiert den
    Crawl-Worker nicht), EINE Aggregation je Modell und Zeitraum (_jobs_pipeline). Gibt es fuer die Segmente im ganzen
    geladenen Zeitraum KEINEN Job (Altdaten vor dem Crawl-Worker, Import), ist die Planung unbekannt (None) — dann
    rechnet der Bericht wie bisher (jedes eingeschaltete Segment gilt als geplant). Sonst {geplant, budget, plan,
    erster, letzter} (siehe _jobs_pipeline)."""
    if not seg_ids:
        return None
    pipeline = _jobs_pipeline(seg_ids, von, bis, bis_jetzt)
    try:
        gruppen = await db[konfig.JOBS].aggregate(pipeline, hint=JOB_INDEX).to_list(None)
    except OperationFailure:
        # Index fehlt (Anlage gescheitert, steht im Merker market_config/indizes) — dann ohne Hinweis an den Planer
        log.warning("Berichte: Index %s fehlt — Abruf-Jobs ohne Index-Hinweis gelesen", JOB_INDEX)
        gruppen = await db[konfig.JOBS].aggregate(pipeline).to_list(None)
    if not sum(int(g.get("jobs") or 0) for g in gruppen):
        return None

    def _menge(g: Dict[str, Any], feld: str) -> set:
        return {str(t) for t in (g.get(feld) or []) if t}
    return {"geplant": {str(g["_id"]): _menge(g, "tage") for g in gruppen},
            "budget": {str(g["_id"]): _menge(g, "budget") for g in gruppen if g.get("budget") and any(g["budget"])},
            "plan": set().union(*[_menge(g, "plan") for g in gruppen]) if gruppen else set(),
            "erster": {str(g["_id"]): str(g["erster"]) for g in gruppen if g.get("erster")},
            "letzter": {str(g["_id"]): str(g["letzter"]) for g in gruppen if g.get("letzter")}}


async def _geplant_laden(db, seg_ids: List[str], von: str, bis: str) -> Optional[Dict[str, set]]:
    """Pruefung Runde 3: geplante Kalendertage je Segment (siehe _jobs_laden) — None = Planung unbekannt."""
    jobs = await _jobs_laden(db, seg_ids, von, bis)
    return jobs["geplant"] if jobs is not None else None


async def _job_am_tag(db, tag: str) -> bool:
    """Altdaten (vor dem Tagesplan-Protokoll): gab es an diesem Tag IRGENDEINEN Tagesplan-Job (tag exakt)? Ein
    Index-Schluessel ueber (tag, status) — markt_job_tag_status."""
    try:
        return bool(await db[konfig.JOBS].find_one({"tag": tag}, {"_id": 0, "tag": 1}, hint=JOB_TAG_INDEX))
    except OperationFailure:
        return bool(await db[konfig.JOBS].find_one({"tag": tag}, {"_id": 0, "tag": 1}))


async def _protokoll_laden(db, von: str, bis: str, heute: Optional[str], plan_tage: set) -> Dict[str, str]:
    """Pruefung Runde 4 (#1): Planungsstand je Kalendertag des geladenen Zeitraums (Vorlauf + Periode) aus dem
    Tagesplan-Protokoll (konfig.TAGESPLAN_LOG, nur lesen): eine Bereichsabfrage + der erste Protokolltag (beides ueber
    den Unique-Index auf 'tag'); nur fuer Tage ohne Protokoll und ohne Job dieses Modells je ein Index-Treffer in
    market_crawl_jobs (gebuendelt in einem gather — im Normalbetrieb keiner).
      * Protokoll 'lief' -> PLAN_LIEF (bzw. PLAN_BUDGET, wenn das Budget nichts/kaum etwas zuliess)
      * ein Tagesplan-Job dieses Modells an dem Tag -> PLAN_LIEF (der Plan lief nachweislich)
      * Protokoll 'Crawler bewusst aus' -> PLAN_AUS
      * heute (vorlaeufig) ohne Protokoll -> PLAN_OFFEN (der Plan kommt noch)
      * sonst irgendein Tagesplan-Job an dem Tag -> PLAN_LIEF (Runde 4b: auch ab Einfuehrung — Protokoll gescheitert)
      * ohne Job ab Einfuehrung des Protokolls (erster Protokolltag) -> PLAN_AUSFALL (technischer Ausfall des Plans),
        davor (Altdaten) PLAN_UNBEKANNT"""
    start = _plus(von, -VORLAUF_TAGE)
    protokoll = {str(d.get("tag")): d for d in await db[konfig.TAGESPLAN_LOG].find(
        {"tag": {"$gte": start, "$lte": bis}}, {"_id": 0}).to_list(None)}
    erster = await db[konfig.TAGESPLAN_LOG].find_one({}, {"_id": 0, "tag": 1}, sort=[("tag", 1)])
    eingefuehrt = str(erster["tag"]) if erster and erster.get("tag") else None
    status: Dict[str, str] = {}
    alt: List[str] = []
    for t in tage_zwischen(start, bis):
        if heute and t > heute:
            continue
        p = protokoll.get(t) or {}
        if p.get("lief_at"):
            status[t] = PLAN_BUDGET if p.get("budget_grund") in BUDGET_GRUENDE else PLAN_LIEF
        elif t in plan_tage:
            status[t] = PLAN_LIEF
        elif p.get("crawler_aus_at"):
            status[t] = PLAN_AUS
        elif heute and t == heute:
            status[t] = PLAN_OFFEN
        else:
            alt.append(t)
    if alt:
        # Runde 4b: auch ab Einfuehrung zuerst der Job-Beleg — das Protokoll wird nach dem Tagesmerker geschrieben und
        # darf scheitern (Failover, Prozessende dazwischen, Rollback auf eine Fassung ohne Protokoll). Irgendein
        # Tagesplan-Job an dem Tag heisst: der Plan lief — sonst galt jedes Modell ohne eigenen Job (Rotation) als
        # technisch ausgefallen, und die Schein-Luecke wurde eingefroren. Ohne Job: ab Einfuehrung Ausfall, davor unbekannt
        treffer = await asyncio.gather(*[_job_am_tag(db, t) for t in alt])
        for t, ok in zip(alt, treffer):
            status[t] = PLAN_LIEF if ok else PLAN_AUSFALL if (eingefuehrt and t >= eingefuehrt) else PLAN_UNBEKANNT
    return status


async def _planung_laden(db, seg_ids: List[str], von: str, bis: str, heute: Optional[str] = None
                         ) -> Tuple[Optional[Dict[str, set]], Optional[Dict[str, Any]]]:
    """Runde 3/4: (geplant, planung) fuer bericht_rechnen — Jobs (eine Aggregation, bis heute) und Tagesplan-Protokoll
    (zwei Index-Abfragen, Altdaten-Tage gebuendelt). Beides im async-Teil, die Rechnung im Hilfsthread."""
    jobs = await _jobs_laden(db, seg_ids, von, bis, konfig.heute_tag())
    if jobs is None:
        return None, None
    tage = await _protokoll_laden(db, von, bis, heute, jobs["plan"])
    return jobs["geplant"], {"tage": tage, "budget": jobs["budget"], "erster": jobs["erster"], "letzter": jobs["letzter"]}


async def bericht_berechnen(db, model_id: str, typ: str, von: str, bis: str, *, heute: Optional[str] = None) -> Optional[Dict[str, Any]]:
    """Bericht aus den gespeicherten Daten rechnen — schreibt nichts. Die reine Rechnung laeuft in einem
    Hilfsthread (Abschnitt 49, Befund B9): ein Monatsbericht mit 180 Segmenten braucht ueber 1 s CPU, und der
    Event-Loop dieses Web-Prozesses bedient gleichzeitig Vergleich, Vertrag und Versand — er darf nie stehen.
    bericht_rechnen liest nur seine Argumente (keine Datenbank, kein geteilter Zustand). Die Abruf-Jobs (geplant vs.
    ausgefallen, Runde 3) und das Tagesplan-Protokoll (Runde 4) liest der async-Teil gebuendelt je Modell und Zeitraum."""
    modell, segs, docs, ereignisse = await _laden(db, model_id, von, bis)
    geplant, planung = await _planung_laden(db, sorted(segs), von, bis, heute)
    return await asyncio.to_thread(bericht_rechnen, modell, segs, docs, typ, von, bis, ereignisse, heute=heute, geplant=geplant,
                                   planung=planung)


async def _hot_deals_offen(db, segment_ids: List[str], von: str, bis: str) -> int:
    if not segment_ids:
        return 0
    return int(await db[TAGESSTATS].count_documents({"segment_id": {"$in": segment_ids}, "date": {"$gte": von, "$lte": bis}, "hot_deals_offen": True}))


async def finalisieren(db, model_id: str, typ: str, von: str, bis: str, *, jetzt: Optional[datetime] = None) -> str:
    """Einen Bericht final einfrieren: 'erstellt' | 'vorhanden' | 'nicht_faellig' | 'keine_daten' | 'wartet_auf_hot_deals'.
    Nur nach Periodenende + Karenz; ein vorhandener Bericht wird NIE geaendert (Insert gegen den Unique-Index —
    zwei Server: der zweite bekommt DuplicateKeyError und laesst den ersten stehen). Tage ohne Hot-Deal-Auswertung
    stehen als Hinweis im eingefrorenen Bericht: noch offene (nach HOTDEAL_WARTEN_STUNDEN) und — Runde 3 — nach
    wiederholten Fehlern aufgegebene (hot_deals.grund 'auswertung_fehler', gezaehlt in kennzahlen aus den schon
    geladenen Tagesdokumenten); auf aufgegebene Tage wartet der Bericht nicht, sie werden nie mehr ausgewertet."""
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
    bericht["kennzahlen"]["hot_deal_tage_offen"] = offen
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
            "status": b.get("status"), "erstellt_at": b.get("erstellt_at"), "schema": b.get("schema")}


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
                                                             "status": 1, "schema": 1, "kennzahlen.richtung": 1, "kennzahlen.delta_pct": 1})\
        .sort([("periode_von", -1), ("typ", 1)]).to_list(1000)
    vorhanden = {(b["typ"], b["periode_von"], b["periode_bis"]) for b in final}
    laufend = [p for p in laufende_perioden() if (p["typ"], p["von"], p["bis"]) not in vorhanden]
    return {"model_id": model_id, "final": final, "laufend": laufend}
