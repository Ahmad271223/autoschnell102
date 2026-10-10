# -*- coding: utf-8 -*-
"""Hot Deals (Master-Auftrag Ahmad 26.09.2026, Phase D — Abschnitte 22-26, 48, 49, 58).

Ein Hot Deal heisst AUSSCHLIESSLICH: auffaellig guenstiges Inserat gegenueber unserer beobachteten
Vergleichsgruppe — dem historischen Low-Market-Median DESSELBEN exakten Segments (Suchauftrag, Fassung,
EZ-Jahr, km-Bereich). Nie gegen ein anderes EZ-Jahr, einen anderen km-Bereich, ein anderes Getriebe oder
einen anderen Kraftstoff (Abschnitt 22) — das Segment ist die Vergleichsgruppe. Kein Urteil ueber Zustand,
Unfallfreiheit, Seriositaet oder "guten Kauf" (Abschnitt 58, HINWEIS).

Wahl des Zeitpunkts (Auftrag: "nach jedem gueltigen Lauf ODER in einem taeglichen Auswertungslauf"):
  ereignisgesteuert nach jedem gueltigen Lauf, aber aus den GESPEICHERTEN Tageswerten und im Hintergrund.
  speicher.verarbeiten setzt am Tagesdokument hot_deals_offen=True, sobald ein Lauf die Hauptwerte des Tages
  stellt; der Auswertungs-Worker (markt.auswertung, eigener Hintergrundjob in server.py) arbeitet alle paar
  Minuten die offenen Tagesdokumente in Tagesreihenfolge ab. Begruendung:
    * Aktualitaet wie "nach jedem Lauf" (Minuten statt einen Tag spaeter) — Privatangebote sind schnell weg
    * der Crawl-Weg (Worker, Budget, Leases) wartet nie auf die Auswertung und scheitert nie an ihr (Abschnitt 49)
    * Grundlage ist der Tageswert (letzter gueltiger Lauf mit Treffern) — ein leerer oder POOR-Zweitlauf
      erzeugt keine Schein-Ereignisse; Tage mit nur ungueltigen Laeufen werden nie ausgewertet; ein LEERER
      Tageswert (Marktluecke) wird erst ausgewertet, wenn ihn kein spaeterer Lauf desselben Tages mehr ersetzen
      kann: nach Tagesende UND ohne wartenden/laufenden Abruf (Segment, Tag) — hoechstens bis zur Berichtsfrist
      (Pruefbefund B0, Runde 2: ein Lauf, der vor Mitternacht startet, liefert noch danach Treffer mit tag=D)
    * nachholbar und idempotent: faellt der Worker aus, bleibt das Tagesdokument offen und wird spaeter
      ausgewertet (aeltere Tage zuerst, ein aelterer Tag nie nach einem neueren — scheitert ein Tag oder wartet
      sein leerer Tageswert noch, bleiben die neueren Tage desselben Segments offen; scheitert ein Tag dauerhaft
      und nur fuer dieses Segment, wird er nach AUFGEBEN_NACH_VERSUCHEN gezaehlten Fehlversuchen UND
      AUFGEBEN_NACH_STUNDEN mit grund='auswertung_fehler' abgeschlossen und bleibt als eigener Betriebsalarm offen;
      ein segmentuebergreifender Fehler zaehlt nicht und wird nach dem Hotfix nachgeholt); dieselbe Auswertung
      zweimal erzeugt kein Ereignis doppelt (Stand je Inserat + Unique-Index der Ereignisse)
    * Schreibpause (Sicherung/Restore): der Durchlauf haelt vor jedem Tagesdokument an, der Rest bleibt offen
    * zwei Server: ein Durchlauf haelt die Sperre markt-auswertung (job_lock, siehe markt.auswertung)
  Dieses Modul liest nur gespeicherte Tageswerte (dazu nur lesend den Status der Abrufe eines Tages, siehe
  _leer_wartet) und schreibt nur Hot-Deal-Daten — es loest NIE einen Marktabruf aus (Architekturtest test_b02);
  Kosten fuer die Auswertung: 0.

Sammlungen:
  market_hot_deals        aktueller Zustand je (segment_id, listing_id): Status ACTIVE / LEFT / REMOVED, Klasse,
                          Preis, Referenz, Vorteil, erstmals erkannt, Preisaenderung seit Erkennung
  market_hot_deal_events  Ereignis-Historie (nur einfuegen, NIE aendern): NEW_HOT_DEAL, BECAME_HOT_DEAL,
                          STILL_HOT (hoechstens einmal je Tag und Inserat), PRICE_DROP_HOT_DEAL, LEFT_HOT_ZONE, REMOVED
  Tagesdokument.hot_deals Zusammenfassung des ausgewerteten Laufs (Referenz, Basis, heisse Inserate) und die
                          Tages-Vereinigungen hot_deal_ids_tag / hot_deal_privat_ids_tag / hot_deal_neu_ids —
                          die Berichte lesen nur diese (keine Kopie der Inserate)
Keine PII (Abschnitte 25/48): Fahrzeugfelder nur aus speicher.PRIVAT_FAHRZEUGFELDER — Ort/PLZ, keine Namen,
Telefonnummern, Verkaeufer-Kennungen oder Koordinaten.
"""
from __future__ import annotations

import logging
import re
import statistics
import uuid
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional, Tuple

from pymongo.errors import DuplicateKeyError

from markt import konfig, speicher
from markt.konfig import HOTDEAL_EREIGNISSE, HOTDEALS, JOBS, LISTINGS, SEGMENTE, TAGESSTATS

log = logging.getLogger(__name__)

# ---------------------------------------------------------------- Schwellen (Abschnitt 23)
# Bewusst Konstanten (keine Umgebungsvariablen — Entscheidung Ahmad); die Oberflaeche zeigt sie an (schwellen()).
# Referenz = Median der Tages-Low-Market-Mediane der letzten 30 Kalendertage VOR dem ausgewerteten Tag (der Tag
# selbst zaehlt nicht mit, sonst zoege ein Deal seine eigene Referenz nach unten). 30 Tage = ein Monat Markt,
# lang genug gegen Ausreisser, kurz genug fuer die Preisentwicklung gebrauchter Autos.
REFERENZ_FENSTER_TAGE = 30
# Mindestens 7 gueltige Beobachtungstage (GOOD/MEDIUM, Top-N bewiesen, Treffer, gleiche Fassung): eine Woche
# deckt Wochentags-Muster ab (Haendler stellen montags/freitags ein); mit weniger Tagen verschiebt ein einziger
# Ausreisser-Tag die Referenz zu stark. Bei taeglichem Abruf ist die Basis nach gut einer Woche da.
MIN_BASIS_TAGE = 7
# Mindestens 5 verschiedene Inserate in der Basis (= eine volle Stichprobe bei 5 Zeilen): stehen immer nur
# dieselben 1-2 Autos im Segment, ist deren Median keine Vergleichsgruppe, sondern ein Einzelpreis.
MIN_BASIS_INSERATE = 5
# Klassen nach Vorteil in % gegenueber der Referenz (Vorschlag Ahmad): DEAL 5-8 %, STRONG 8-12 %, EXTREME >= 12 %
KLASSEN = (("EXTREME", 12.0), ("STRONG", 8.0), ("DEAL", 5.0))
KLASSE_RANG = {"DEAL": 1, "STRONG": 2, "EXTREME": 3}
# Mindest-Euro-Vorteil, preisabhaengig gestaffelt (Abschnitt 23 "ggf. preisabhaengig skalieren"): ab 15.000 EUR
# Referenz gilt Ahmads Vorschlag 750 EUR; darunter waeren 750 EUR fuer ein 6.000-EUR-Auto schon 12,5 % — das
# Prozent-Kriterium soll dort entscheiden, deshalb 600 EUR ab 10.000 EUR und 400 EUR darunter.
MINDEST_EUR_STAFFEL = ((15000.0, 750.0), (10000.0, 600.0), (0.0, 400.0))

EREIGNISSE = ("NEW_HOT_DEAL", "BECAME_HOT_DEAL", "STILL_HOT", "PRICE_DROP_HOT_DEAL", "LEFT_HOT_ZONE", "REMOVED")
NEW, BECAME, STILL, PRICE_DROP, LEFT, REMOVED = EREIGNISSE
AKTIV, VERLASSEN, ENTFERNT = "ACTIVE", "LEFT", "REMOVED"
GRUND_UEBER_SCHWELLE = "ueber_schwelle"             # noch im Sample, aber nicht mehr guenstig genug
GRUND_AUS_STICHPROBE = "nicht_mehr_im_sample"       # nicht mehr unter den N guenstigsten — NICHT verkauft
GRUND_ENTFERNT = "inserat_entfernt"                 # Entfernungspruefung bestaetigt: Inserat nicht mehr online
ENTFERNT_NACHSCHAU_TAGE = 30                        # so lange wird ein herausgefallener Deal auf "entfernt" geprueft
AUSWERTUNG_MAX_JE_LAUF = 3000                       # offene Tagesdokumente je Durchlauf (Rest im naechsten)
# Pruefbefund Runde 2 (B2): ein Tag, dessen Auswertung immer wieder scheitert (z. B. deterministisch an einem kaputten
# Feld), hielte sonst alle neueren Tage des Segments fuer immer zurueck (Hot Deals eingefroren, Berichte warten).
# Je Tageswert (observed_at) werden die Fehlversuche gezaehlt und der Tag mit grund='auswertung_fehler' abgeschlossen
# (die neueren Tage laufen weiter, der Tag bleibt als Betriebsalarm offen, der Bericht nennt ihn) — Pruefung Runde 3
# (hotdeals#1) erst, wenn BEIDES erreicht ist: mindestens AUFGEBEN_NACH_VERSUCHEN gezaehlte Fehlversuche UND
# AUFGEBEN_NACH_STUNDEN seit dem ersten. Die Anzahl allein ist keine Zeit: der Auswertungs-Worker laeuft auf beiden
# Servern (die Sperre verhindert nur gleichzeitige Durchlaeufe) und jeder Klick auf 'Jetzt auswerten'/'Berichte jetzt
# erstellen' loest einen weiteren aus — mit ODER war nach ~12 Minuten oder 6 Klicks aufgegeben. 6 Stunden lassen Zeit
# fuer einen Hotfix; ein aufgegebener Tag ist endgueltig (Hot Deals und Ereignisse des Tages fehlen fuer immer).
# Gezaehlt wird ein Fehlversuch nur, wenn im SELBEN Durchlauf ein anderes Segment vollstaendig ausgewertet wurde
# (status 'ausgewertet' — ein fruehes Ende ohne Basis beweist nicht, dass die Auswertung selbst funktioniert). Scheitern
# alle, ist es ein segmentuebergreifender Fehler (z. B. ein Deploy): nichts wird gezaehlt, der Tag bleibt offen und wird
# nach der Korrektur nachgeholt; der Durchlauf meldet die Fehler weiter (Betriebsalarm markt_auswertung_fehler).
AUFGEBEN_NACH_VERSUCHEN = 6
AUFGEBEN_NACH_STUNDEN = 6
GRUND_AUSWERTUNG_FEHLER = "auswertung_fehler"
# Pruefbefund B14: die Liste 'alle' zeigt die Historie der letzten 90 Tage (nach letztem Ereignis, Index
# markt_hotdeal_letztes_ereignis) — verlassene/entfernte Zustaende werden nie geloescht, ohne Fenster wuerde
# jeder Klick die ganze, stetig wachsende Sammlung lesen und sortieren
ALLE_FENSTER_TAGE = 90

# Liquiditaet (Abschnitt 56): nicht nur Anzahl, sondern Umschlag (neue + verschwundene je Tag relativ zur
# Stichprobe) und Top-5-Wechsel. Ab 3 Tagen mit Vergleichstag; darunter UNKNOWN.
LIQ_MIN_TAGE = 3
LIQ_MIN_STICHPROBE = 2.0          # im Mittel unter 2 Autos: duenner Markt -> LOW, egal wie viel wechselt
LIQ_HOCH_UMSCHLAG, LIQ_HOCH_TOP5 = 0.15, 0.5
LIQ_MITTEL_UMSCHLAG, LIQ_MITTEL_TOP5 = 0.05, 0.2
LIQ_RANG = {"HIGH": 3, "MEDIUM": 2, "LOW": 1, "UNKNOWN": 0}

HINWEIS = ("Hot Deal heißt nur: auffällig günstiges Inserat gegenüber unserer beobachteten Vergleichsgruppe "
           "(Low-Market-Median desselben Segments der letzten 30 Tage) — nicht automatisch unfallfrei, technisch gut, "
           "seriös oder ein guter Kauf. Nur aus gespeicherten Tageswerten, keine Zusatzabrufe, keine Zusatzkosten.")


# ---------------------------------------------------------------- reine Regeln
def _tag_minus(tag: str, tage: int) -> str:
    return (datetime.strptime(tag, "%Y-%m-%d") - timedelta(days=tage)).strftime("%Y-%m-%d")


def mindest_eur(referenz: Any) -> float:
    r = float(referenz or 0)
    for ab, betrag in MINDEST_EUR_STAFFEL:
        if r >= ab:
            return betrag
    return MINDEST_EUR_STAFFEL[-1][1]


def klasse_bestimmen(preis: Any, referenz: Any) -> Tuple[Optional[str], float, float]:
    """(Klasse oder None, Vorteil EUR, Vorteil %) — Vorteil positiv = guenstiger als die Referenz. Beide
    Kriterien muessen stimmen: Prozent-Schwelle UND Mindest-Euro-Vorteil."""
    ref, p = float(referenz or 0), float(preis)
    diff = round(ref - p, 2)
    pct = round(diff / ref * 100, 2) if ref else 0.0
    if not ref or diff < mindest_eur(ref):
        return None, diff, pct
    for name, ab in KLASSEN:
        if pct >= ab:
            return name, diff, pct
    return None, diff, pct


def fassung(doc: Optional[Dict[str, Any]], seg: Optional[Dict[str, Any]] = None) -> Tuple[int, Optional[str]]:
    """(version, definition_hash) eines Tagesdokuments; Altdaten ohne Felder -> Werte des Segments."""
    doc, seg = doc or {}, seg or {}
    try:
        v = int(doc.get("version") or seg.get("version") or 1)
    except (TypeError, ValueError):
        v = 1
    return v, (doc.get("definition_hash") or seg.get("definition_hash"))


def qualitaet(doc: Optional[Dict[str, Any]], rows: Optional[int] = None) -> str:
    """Datenqualitaet eines Tagesdokuments (Altdaten ohne Feld ueber speicher.qualitaet_aus_doc)."""
    return speicher.qualitaet_aus_doc(doc, rows)["data_quality"]


def ist_gueltig(doc: Optional[Dict[str, Any]]) -> bool:
    """Gueltige Beobachtung: ein gueltiger Lauf (sample_size vorhanden, auch 0 = Marktluecke) mit GOOD/MEDIUM.
    Tage mit nur ungueltigen Laeufen (ohne sample_size) sind KEINE Marktluecke und keine Beobachtung."""
    return bool(doc) and doc.get("sample_size") is not None and qualitaet(doc) in speicher.BASIS_QUALITAET


def ist_basis(doc: Optional[Dict[str, Any]]) -> bool:
    """Preisbasis: gueltig UND mit Treffern und Median."""
    return ist_gueltig(doc) and int(doc.get("sample_size") or 0) > 0 and doc.get("median_price") is not None


def liquiditaet_bewerten(docs: List[Dict[str, Any]]) -> Dict[str, Any]:
    """HIGH / MEDIUM / LOW / UNKNOWN aus Tagesdokumenten (Abschnitt 56): Umschlag = (neue + verschwundene) je
    Vergleichstag relativ zur mittleren Stichprobe, Anteil der Tage mit Top-5-Wechsel, mittlere Beobachtungs-
    dauer je Inserat (Tage im Sample — beobachtete Dauer, KEIN Verkaufsdatum)."""
    tage = [d for d in docs if d.get("sample_size") is not None]
    vergleich = [d for d in tage if d.get("vergleich_vortag")]
    mittel = (sum(int(d.get("sample_size") or 0) for d in tage) / len(tage)) if tage else 0.0
    anwesend: Dict[str, int] = {}
    for d in tage:
        for lid in set(d.get("listing_ids_alle") or d.get("listing_ids") or []):
            anwesend[str(lid)] = anwesend.get(str(lid), 0) + 1
    raus: Dict[str, Any] = {"tage": len(vergleich), "mittlere_stichprobe": round(mittel, 2),
                            "mittlere_beobachtung_tage": round(sum(anwesend.values()) / len(anwesend), 2) if anwesend else None,
                            "umschlag_pct": None, "top5_wechsel_pct": None, "neu_je_tag": None, "weg_je_tag": None}
    if len(vergleich) < LIQ_MIN_TAGE:
        return {**raus, "stufe": "UNKNOWN", "rang": LIQ_RANG["UNKNOWN"]}
    neu = sum(len(d.get("new_in_sample_ids") or []) or int(d.get("new_in_sample_today") or 0) for d in vergleich)
    weg = sum(int(d.get("disappeared_count") or 0) for d in vergleich)
    umschlag = (neu + weg) / (2 * len(vergleich) * max(1.0, mittel))
    t5 = [d.get("top5_changed") for d in vergleich if d.get("top5_changed") is not None]
    top5 = (sum(1 for x in t5 if x) / len(t5)) if t5 else 0.0
    if mittel < LIQ_MIN_STICHPROBE:
        stufe = "LOW"
    elif umschlag >= LIQ_HOCH_UMSCHLAG or top5 >= LIQ_HOCH_TOP5:
        stufe = "HIGH"
    elif umschlag >= LIQ_MITTEL_UMSCHLAG or top5 >= LIQ_MITTEL_TOP5:
        stufe = "MEDIUM"
    else:
        stufe = "LOW"
    return {**raus, "stufe": stufe, "rang": LIQ_RANG[stufe], "umschlag_pct": round(umschlag * 100, 1),
            "top5_wechsel_pct": round(top5 * 100, 1), "neu_je_tag": round(neu / len(vergleich), 2),
            "weg_je_tag": round(weg / len(vergleich), 2)}


def schwellen() -> Dict[str, Any]:
    """Die geltenden Schwellen fuer die Oberflaeche (nachvollziehbar, nicht versteckt)."""
    return {"referenz_fenster_tage": REFERENZ_FENSTER_TAGE, "min_basis_tage": MIN_BASIS_TAGE,
            "min_basis_inserate": MIN_BASIS_INSERATE, "klassen": [{"klasse": k, "ab_pct": p} for k, p in KLASSEN],
            "mindest_eur_staffel": [{"ab_referenz_eur": a, "mindest_eur": b} for a, b in MINDEST_EUR_STAFFEL]}


# ---------------------------------------------------------------- Referenz
async def referenz_berechnen(db, seg: Dict[str, Any], tag: str, fass: Tuple[int, Optional[str]]) -> Dict[str, Any]:
    """Historischer Low-Market-Median des Segments vor `tag`: nur Tage derselben Fassung, Datenqualitaet
    GOOD/MEDIUM, mit Treffern und bewiesener Top-N-Sortierung (wie die Trendbasis, Welle 6 Nr. 108)."""
    seg_id = seg["id"]
    von = _tag_minus(tag, REFERENZ_FENSTER_TAGE)
    docs = await db[TAGESSTATS].find({"segment_id": seg_id, "date": {"$gte": von, "$lt": tag}, "sample_size": {"$exists": True}},
                                     {"_id": 0, "laeufe": 0, "listings": 0, "disappeared_ids": 0}).sort("date", 1)\
        .to_list(REFERENZ_FENSTER_TAGE + 5)
    gleich = [d for d in docs if fassung(d, seg) == fass]
    basis = [d for d in gleich if ist_basis(d) and d.get("top_n_bewiesen", True) is not False]
    inserate: set = set()
    for d in basis:
        inserate |= {str(x) for x in (d.get("listing_ids_alle") or d.get("listing_ids") or [])}
    # Pruefbefund B1: Liquiditaet nur aus gueltigen Tagen (GOOD/MEDIUM) wie die Berichte — ein POOR-Tag mit
    # kleiner Stichprobe und vielen 'verschwundenen' Inseraten taeuschte sonst Umschlag vor
    raus: Dict[str, Any] = {"basis_tage": len(basis), "basis_inserate": len(inserate), "fenster_von": von,
                            "fenster_bis": _tag_minus(tag, 1), "andere_fassung_tage": len(docs) - len(gleich),
                            "basis_ok": False, "grund": "", "referenz_eur": None, "mindest_eur": None,
                            "liquiditaet": liquiditaet_bewerten([d for d in gleich if ist_gueltig(d)])}
    if len(basis) < MIN_BASIS_TAGE:
        raus["grund"] = "zu_wenig_tage"
        return raus
    if len(inserate) < MIN_BASIS_INSERATE:
        raus["grund"] = "zu_wenig_inserate"
        return raus
    ref = round(float(statistics.median([float(d["median_price"]) for d in basis])), 2)
    raus.update({"basis_ok": True, "referenz_eur": ref, "mindest_eur": mindest_eur(ref)})
    return raus


# ---------------------------------------------------------------- Schreiben (Zustand + Historie)
async def _ereignis(db, felder: Dict[str, Any]) -> bool:
    """Ereignis EINFUEGEN — nie aendern. Schluessel (Segment, Inserat, Lauf, Typ): dieselbe Auswertung zweimal
    legt nichts doppelt an. STILL_HOT traegt den Tag als Lauf-Schluessel (hoechstens einmal je Tag)."""
    schl = {k: felder[k] for k in ("segment_id", "listing_id", "lauf_key", "typ")}
    rest = {k: v for k, v in felder.items() if k not in schl}
    try:
        r = await db[HOTDEAL_EREIGNISSE].update_one(schl, {"$setOnInsert": {"id": uuid.uuid4().hex, **rest}}, upsert=True)
        return r.upserted_id is not None
    except DuplicateKeyError:
        return False


async def _zustand(db, seg_id: str, lid: str, aenderung: Dict[str, Any], lauf_at: str) -> bool:
    """Zustand nur vorwaerts schreiben (CAS auf stand_lauf_at): hat eine parallele oder fruehere Auswertung
    denselben oder einen neueren Lauf schon eingetragen, passt der Filter nicht, der Upsert trifft den Unique-
    Index (segment_id, listing_id) und es bleibt beim vorhandenen Zustand."""
    filt = {"segment_id": seg_id, "listing_id": lid,
            "$or": [{"stand_lauf_at": {"$exists": False}}, {"stand_lauf_at": {"$lt": lauf_at}}]}
    try:
        await db[HOTDEALS].update_one(filt, aenderung, upsert=True)
        return True
    except DuplicateKeyError:
        return False


def _fahrzeug(l: Optional[Dict[str, Any]], seg_id: str) -> Dict[str, Any]:
    """Nur Whitelist-Felder (speicher.PRIVAT_FAHRZEUGFELDER) — nie seller_*, nie Koordinaten."""
    if not l:
        return {}
    raus = speicher._privat_fahrzeug(l)
    raus["segment_first_seen_at"] = ((l.get("segmente") or {}).get(seg_id) or {}).get("first_seen_at") or l.get("first_seen_at")
    return raus


async def _abschliessen(db, seg_id: str, tag: str, doc: Dict[str, Any], zus: Dict[str, Any], heiss: List[str],
                        heiss_privat: List[str], neu: List[str]) -> Dict[str, Any]:
    """Tages-Zusammenfassung schreiben und den Merker loeschen — nur, wenn der Tageswert inzwischen nicht von
    einem neueren Lauf ersetzt wurde (dann bleibt hot_deals_offen und der naechste Durchlauf wertet ihn aus).
    Die Tages-Vereinigungen (heiss irgendwann am Tag) gelten in jedem Fall."""
    if heiss or heiss_privat or neu:
        await db[TAGESSTATS].update_one({"segment_id": seg_id, "date": tag},
                                        {"$addToSet": {"hot_deal_ids_tag": {"$each": list(heiss)},
                                                       "hot_deal_privat_ids_tag": {"$each": list(heiss_privat)},
                                                       "hot_deal_neu_ids": {"$each": list(neu)}}})
    filt: Dict[str, Any] = {"segment_id": seg_id, "date": tag}
    if doc.get("observed_at") is not None:
        filt["observed_at"] = doc["observed_at"]
    await db[TAGESSTATS].update_one(filt, {"$set": {"hot_deals": zus}, "$unset": {"hot_deals_offen": "", "hot_deals_fehler": ""}})
    return {"status": "ausgewertet" if zus.get("basis_ok") else "ohne_basis", **zus}


# ---------------------------------------------------------------- Auswertung eines Tages
def leer_frist(tag: str) -> datetime:
    """Spaetester Zeitpunkt (UTC), bis zu dem ein leerer Tageswert des Tages auf einen noch wartenden oder laufenden
    Abruf desselben Tages wartet: Folgetag 00:00 deutscher Zeit + BERICHT_KARENZ_STUNDEN — genau der Zeitpunkt, ab
    dem die Berichte, die mit diesem Tag enden, final werden (berichte.faellig_ab). Der Durchlauf wertet die Hot Deals
    vor den Berichten aus, deshalb ist der Tag dann ausgewertet, bevor der Bericht einfriert; bis dahin zaehlt er als
    offen (berichte._hot_deals_offen) und der Bericht wartet."""
    from markt import berichte          # spaet: berichte importiert deals
    return berichte.faellig_ab(tag)


async def _leer_wartet(db, doc: Dict[str, Any], seg_id: str, tag: str, jetzt: Optional[datetime]) -> bool:
    """Leerer Tageswert (gueltiger Lauf, 0 Treffer), den ein spaeterer Lauf desselben Tages noch ersetzen kann.

    Pruefbefund B0 (Runde 2): Kalendertag vorbei reicht nicht — ein Lauf, der vor 23:30 startet, darf bis zur Lease-
    Grenze laufen und liefert nach 00:00 noch Treffer mit tag=D. Wartende Abrufe vergangener Tage storniert der
    Crawl-Worker vor jedem Claim (alte_stornieren), sie liefern nichts mehr; solange einer von ihnen noch 'queued' steht,
    zaehlt er trotzdem (Rennen um Mitternacht). Gewaehlt statt einer festen Karenz (z. B. 6 h nach Tagesende): das
    Crawl-Fenster beginnt um 03:00 — bei 6 h Karenz waeren die Laeufe von D+1 vor dem leeren Tag D dran, und ob die
    Marktluecke D ein LEFT erzeugt, hinge von der Uhrzeit des Folgelaufs ab. Ohne offenen Abruf wird der leere Tag
    kurz nach Mitternacht ausgewertet, vor den Laeufen von D+1. Obergrenze leer_frist(tag): ein haengengebliebener
    Abruf (Prozess weg, keine Lease-Bereinigung) haelt nichts laenger auf als bis zur Berichtsfrist."""
    if int(doc.get("sample_size") or 0) != 0:
        return False
    if str(tag) >= konfig.heute_tag(jetzt):
        return True                     # der Tag laeuft noch (deutsche Zeit)
    if (jetzt or konfig.jetzt()) >= leer_frist(tag):
        return False
    offen = await db[JOBS].find_one({"segment_id": seg_id, "tag": {"$regex": f"^{re.escape(str(tag))}(#|$)"},
                                     "status": {"$in": ["queued", "running"]}}, {"_id": 0, "id": 1})
    return offen is not None


async def wartung_aktiv(db) -> bool:
    """Schreibpause (Sicherung/Restore) aktiv? Eine Stoerung gilt wie in wartung.aktiv_async als 'keine Pause'."""
    try:
        import wartung
        return bool(await wartung.aktiv_async(db))
    except Exception:  # noqa: BLE001
        return False


async def segment_tag_auswerten(db, seg_id: str, tag: str, *, jetzt: Optional[datetime] = None) -> Dict[str, Any]:
    """Hot Deals eines Segments fuer einen Tag aus dem gespeicherten Tageswert (Hauptwerte = letzter gueltiger
    Lauf mit Treffern). Liest Tagesdokumente, schreibt Zustand/Ereignisse/Zusammenfassung — kein Abruf.

    Zustandswechsel je Inserat (Ereignis in Klammern):
      heiss, kein Zustand       -> ACTIVE (NEW_HOT_DEAL, wenn heute neu im Sample, sonst BECAME_HOT_DEAL)
      heiss, ACTIVE             -> ACTIVE (PRICE_DROP_HOT_DEAL bei gesunkenem Preis, sonst STILL_HOT)
      heiss, LEFT/REMOVED       -> ACTIVE (BECAME_HOT_DEAL — derselbe Zustand, neue heisse Phase)
      nicht heiss, ACTIVE       -> LEFT   (LEFT_HOT_ZONE: ueber der Schwelle oder nicht mehr im Sample)
      nicht im Sample, entfernt -> REMOVED (REMOVED, nur mit bestaetigter Entfernungspruefung)
    Kein Ereignis, wenn: Tag mit Datenqualitaet POOR/UNKNOWN, Basis zu duenn, Altdaten ohne Preiszeilen, oder ein
    neuerer Tag desselben Segments ist schon ausgewertet (Reihenfolge). Ein leerer Tageswert, den ein spaeterer Lauf
    desselben Tages noch ersetzen kann, wird noch gar nicht ausgewertet ('leer_vorlaeufig', Merker bleibt)."""
    jetzt_iso = (jetzt or konfig.jetzt()).isoformat()
    doc = await db[TAGESSTATS].find_one({"segment_id": seg_id, "date": tag}, {"_id": 0, "laeufe": 0})
    if not doc or doc.get("sample_size") is None:
        return {"status": "kein_tageswert"}
    if await _leer_wartet(db, doc, seg_id, tag, jetzt):
        # Pruefbefund B0: ein leerer erster Lauf stellt nur vorlaeufig den Tageswert — ein spaeterer Lauf desselben
        # Tages mit Treffern ersetzt ihn (P5). Frueher ausgewertet, verliessen aktive Deals morgens die Zone und
        # kamen nachmittags als neue heisse Phase zurueck (dauerhaftes LEFT/BECAME-Paar, Schein-'heute neu').
        # Erst wenn kein Lauf des Tages mehr kommen kann, gilt der leere Tag als Marktluecke; bis dahin bleibt der
        # Merker stehen (Runde 2: auch nach Mitternacht, solange ein Abruf des Tages noch laeuft oder wartet).
        return {"status": "leer_vorlaeufig"}
    seg = await db[SEGMENTE].find_one({"id": seg_id}, {"_id": 0}) or {"id": seg_id}
    seg.setdefault("id", seg_id)
    lauf_at = str(doc.get("observed_at") or doc.get("last_run_at") or "")
    fruehere = doc.get("hot_deals") or {}
    if fruehere.get("ausgewertet_at") and doc.get("observed_at") is not None and fruehere.get("observed_at") == doc.get("observed_at"):
        # derselbe Tageswert ist schon ausgewertet (Merker erneut gesetzt): nichts neu rechnen, Merker weg
        await db[TAGESSTATS].update_one({"segment_id": seg_id, "date": tag, "observed_at": doc["observed_at"]}, {"$unset": {"hot_deals_offen": ""}})
        return {**fruehere, "status": "schon_ausgewertet"}
    fass = fassung(doc, seg)
    zus: Dict[str, Any] = {"ausgewertet_at": jetzt_iso, "observed_at": doc.get("observed_at"), "lauf_tag": doc.get("lauf_tag"),
                           "version": fass[0], "definition_hash": fass[1], "basis_ok": False, "grund": "",
                           "ids": [], "privat_ids": [], "klassen": {}, "ereignisse": {}}
    # Reihenfolge: ein aelterer Tag wird nie nach einem neueren ausgewertet (die Zustaende gehen nur vorwaerts)
    neuer = await db[TAGESSTATS].find_one({"segment_id": seg_id, "date": {"$gt": tag}, "hot_deals.ausgewertet_at": {"$exists": True}},
                                          {"_id": 0, "date": 1})
    if neuer:
        zus["grund"] = "neuerer_tag_ausgewertet"
        return await _abschliessen(db, seg_id, tag, doc, zus, [], [], [])
    dq = qualitaet(doc)
    zus["data_quality"] = dq
    if dq not in speicher.BASIS_QUALITAET:
        zus["grund"] = "datenqualitaet"
        return await _abschliessen(db, seg_id, tag, doc, zus, [], [], [])
    ref = await referenz_berechnen(db, seg, tag, fass)
    liq = ref.get("liquiditaet") or {}
    zus.update({k: ref.get(k) for k in ("referenz_eur", "mindest_eur", "basis_tage", "basis_inserate")})
    zus["liquiditaet"] = liq.get("stufe")
    if not ref["basis_ok"]:
        zus["grund"] = ref["grund"]
        return await _abschliessen(db, seg_id, tag, doc, zus, [], [], [])
    zeilen = [z for z in (doc.get("listings") or []) if z.get("listing_id") and z.get("price") is not None]
    if int(doc.get("sample_size") or 0) > 0 and not zeilen:
        zus["grund"] = "ohne_preiszeilen"          # Altdaten vor Phase C: keine Zeilen am Tagesdokument
        return await _abschliessen(db, seg_id, tag, doc, zus, [], [], [])
    zus["basis_ok"] = True
    referenz = float(ref["referenz_eur"])
    ids_heute = [str(z["listing_id"]) for z in zeilen]
    im_sample = set(ids_heute)
    neu_ids = {str(x) for x in (doc.get("new_in_sample_ids") or [])}
    zustaende: Dict[str, Dict[str, Any]] = {}
    async for s in db[HOTDEALS].find({"segment_id": seg_id, "$or": [
            {"listing_id": {"$in": ids_heute}}, {"status": AKTIV},
            {"status": VERLASSEN, "left_grund": GRUND_AUS_STICHPROBE, "left_tag": {"$gte": _tag_minus(tag, ENTFERNT_NACHSCHAU_TAGE)}}]},
            {"_id": 0}):
        zustaende[str(s["listing_id"])] = s
    brauchen = sorted(im_sample | set(zustaende))
    fahrzeuge: Dict[str, Dict[str, Any]] = {}
    if brauchen:
        proj = {"_id": 0, "listing_id": 1, "active_state": 1, "first_seen_at": 1, f"segmente.{seg_id}.first_seen_at": 1,
                **{k: 1 for k in speicher.PRIVAT_FAHRZEUGFELDER}}
        async for l in db[LISTINGS].find({"source": konfig.QUELLE, "listing_id": {"$in": brauchen}}, proj):
            fahrzeuge[str(l["listing_id"])] = l
    gemeinsam = {"model_id": seg.get("model_id") or doc.get("model_id"), "version": fass[0], "definition_hash": fass[1],
                 "segment_label": seg.get("label"), "km_label": seg.get("km_label"), "ez_label": seg.get("ez_label"),
                 "min_km": seg.get("min_km"), "max_km": seg.get("max_km"), "year_from": seg.get("year_from"),
                 "reference_price": referenz, "mindest_eur": ref["mindest_eur"], "basis_tage": ref["basis_tage"],
                 "basis_inserate": ref["basis_inserate"], "liquiditaet": liq.get("stufe"), "liquiditaet_rang": liq.get("rang", 0),
                 "market_depth": doc.get("market_depth"), "updated_at": jetzt_iso}
    heiss: List[str] = []
    heiss_privat: List[str] = []
    neu_heute: List[str] = []
    klassen: Dict[str, int] = {}
    ereignisse: Dict[str, int] = {}

    def _ereignis_felder(typ: str, lid: str, **extra) -> Dict[str, Any]:
        return {"segment_id": seg_id, "listing_id": lid, "typ": typ, "tag": tag, "lauf_key": tag if typ == STILL else lauf_at,
                "lauf_at": lauf_at, "at": jetzt_iso, "model_id": gemeinsam["model_id"], "version": fass[0],
                "definition_hash": fass[1], "reference_price": referenz, "basis_tage": ref["basis_tage"], **extra}

    for z in zeilen:
        lid, preis = str(z["listing_id"]), float(z["price"])
        kl, diff, pct = klasse_bestimmen(preis, referenz)
        privat = str(z.get("seller_type") or "").strip().upper() == "PRIVATE"
        if kl:
            heiss.append(lid)
            klassen[kl] = klassen.get(kl, 0) + 1
            if privat:
                heiss_privat.append(lid)
        st = zustaende.get(lid)
        # Idempotenz: dieser (oder ein neuerer) Lauf ist fuer das Inserat schon ausgewertet
        if st and str(st.get("stand_lauf_at") or "") >= lauf_at:
            if kl and st.get("last_event") in (NEW, BECAME) and st.get("last_event_at") == lauf_at:
                neu_heute.append(lid)
            continue
        stand = {"current_price": preis, "diff_eur": diff, "diff_pct": pct, "rank": z.get("rank"),
                 "seller_type": z.get("seller_type") or None, "privat": privat, "last_seen_at": lauf_at, "last_seen_tag": tag,
                 "stand_lauf_at": lauf_at, "stand_tag": tag}
        if kl:
            aktiv_vorher = bool(st) and st.get("status") == AKTIV
            if st is None:
                typ = NEW if lid in neu_ids else BECAME
            elif aktiv_vorher:
                typ = PRICE_DROP if preis < float(st.get("current_price") or preis) else STILL
            else:
                typ = BECAME
            erkannt_preis = float(st.get("detected_price")) if st and st.get("detected_price") is not None else preis
            setzen = {**gemeinsam, **_fahrzeug(fahrzeuge.get(lid), seg_id), **stand, "status": AKTIV, "klasse": kl,
                      "klasse_rang": KLASSE_RANG[kl], "last_event": typ, "last_event_at": lauf_at, "last_event_tag": tag,
                      "last_hot_tag": tag, "price_change_since_detection_eur": round(preis - erkannt_preis, 2)}
            aenderung: Dict[str, Any] = {"$setOnInsert": {"id": uuid.uuid4().hex, "created_at": jetzt_iso, "deal_first_detected_at": lauf_at,
                                                          "deal_first_detected_tag": tag, "detected_price": preis}}
            inc: Dict[str, int] = {}
            if not aktiv_vorher:
                setzen.update({"hot_seit_at": lauf_at, "hot_seit_tag": tag})
                inc["hot_phasen"] = 1
                aenderung["$unset"] = {"left_at": "", "left_tag": "", "left_grund": ""}
                neu_heute.append(lid)
            if st is None or st.get("last_hot_tag") != tag:
                inc["tage_hot"] = 1
            aenderung["$set"] = setzen
            if inc:
                aenderung["$inc"] = inc
            eingefuegt = await _ereignis(db, _ereignis_felder(typ, lid, klasse=kl, klasse_vorher=(st or {}).get("klasse"), price=preis,
                                                              price_vorher=(st or {}).get("current_price"), diff_eur=diff, diff_pct=pct,
                                                              rank=z.get("rank"), seller_type=z.get("seller_type") or None, privat=privat))
            await _zustand(db, seg_id, lid, aenderung, lauf_at)
        elif st and st.get("status") == AKTIV:
            typ = LEFT
            eingefuegt = await _ereignis(db, _ereignis_felder(typ, lid, klasse=None, klasse_vorher=st.get("klasse"), price=preis,
                                                              price_vorher=st.get("current_price"), diff_eur=diff, diff_pct=pct, rank=z.get("rank"),
                                                              seller_type=z.get("seller_type") or None, privat=privat, grund=GRUND_UEBER_SCHWELLE))
            await _zustand(db, seg_id, lid, {"$set": {**gemeinsam, **stand, "status": VERLASSEN, "left_at": lauf_at, "left_tag": tag,
                                                      "left_grund": GRUND_UEBER_SCHWELLE, "last_event": typ, "last_event_at": lauf_at,
                                                      "last_event_tag": tag}}, lauf_at)
        else:
            continue
        if eingefuegt:
            ereignisse[typ] = ereignisse.get(typ, 0) + 1
    # nicht (mehr) im Sample: aktive Deals verlassen die Zone; bestaetigt entfernte Inserate -> REMOVED
    for lid, st in zustaende.items():
        if lid in im_sample or str(st.get("stand_lauf_at") or "") >= lauf_at:
            continue
        entfernt = (fahrzeuge.get(lid) or {}).get("active_state") == "confirmed_removed"
        if st.get("status") == AKTIV:
            typ = REMOVED if entfernt else LEFT
        elif st.get("status") == VERLASSEN and st.get("left_grund") == GRUND_AUS_STICHPROBE and entfernt:
            typ = REMOVED
        else:
            continue
        grund = GRUND_ENTFERNT if typ == REMOVED else GRUND_AUS_STICHPROBE
        eingefuegt = await _ereignis(db, _ereignis_felder(typ, lid, klasse=None, klasse_vorher=st.get("klasse"), price=None,
                                                          price_vorher=st.get("current_price"), seller_type=st.get("seller_type"),
                                                          privat=bool(st.get("privat")), grund=grund))
        setzen = {"status": ENTFERNT if typ == REMOVED else VERLASSEN, "last_event": typ, "last_event_at": lauf_at, "last_event_tag": tag,
                  "stand_lauf_at": lauf_at, "stand_tag": tag, "updated_at": jetzt_iso}
        if st.get("status") == AKTIV:
            setzen.update({"left_at": lauf_at, "left_tag": tag, "left_grund": grund})
        if typ == REMOVED:
            setzen.update({"removed_at": jetzt_iso, "removed_tag": tag})
        await _zustand(db, seg_id, lid, {"$set": setzen}, lauf_at)
        if eingefuegt:
            ereignisse[typ] = ereignisse.get(typ, 0) + 1
    zus.update({"ids": heiss, "privat_ids": heiss_privat, "klassen": klassen, "ereignisse": ereignisse, "neu_ids": neu_heute})
    return await _abschliessen(db, seg_id, tag, doc, zus, heiss, heiss_privat, neu_heute)


async def _alarm_tag(db, seg_id: str, tag: str, **details) -> None:
    """Eigener Betriebsalarm je aufgegebenem Tag (ref hot-deals:<Segment>:<Tag>): den schliesst kein fehlerfreier
    Durchlauf (der schliesst nur ref 'auswertung') — er bleibt offen, bis ihn jemand quittiert."""
    try:
        from betrieb import alarm
        from markt.auswertung import ALARM
        await alarm(db, ALARM, ref=f"hot-deals:{seg_id}:{tag}", **details)
    except Exception:  # noqa: BLE001
        log.exception("Betriebsalarm fuer aufgegebene Hot-Deal-Auswertung %s %s nicht angelegt", seg_id, tag)


async def _fehlversuch(db, d: Dict[str, Any], fehler: BaseException, jetzt: Optional[datetime]) -> bool:
    """Pruefbefund Runde 2 (B2): einen Fehlversuch am Tagesdokument zaehlen — je Tageswert (observed_at): ersetzt ein
    neuerer Lauf den Tageswert, beginnt die Zaehlung neu. True = Tag aufgegeben und abgeschlossen (grund=
    'auswertung_fehler', hot_deals_offen weg) — erst, wenn AUFGEBEN_NACH_VERSUCHEN gezaehlte Versuche UND
    AUFGEBEN_NACH_STUNDEN seit dem ersten erreicht sind (Runde 3); die neueren Tage des Segments duerfen danach
    drankommen. Aufgerufen nur fuer segmentbezogene Fehler (auswerten_faellige). Am Dokument und im Alarm steht nur die
    Fehlerart (keine Inseratsdaten); der volle Fehler steht im Protokoll."""
    try:
        jetzt_dt = jetzt or konfig.jetzt()
        seg_id, tag, obs = str(d["segment_id"]), str(d["date"]), d.get("observed_at")
        alt = d.get("hot_deals_fehler") or {}
        gleich = bool(alt) and alt.get("observed_at") == obs
        versuche = (int(alt.get("versuche") or 0) if gleich else 0) + 1
        seit = str((alt.get("seit") if gleich else None) or jetzt_dt.isoformat())
        try:
            stunden = (jetzt_dt - datetime.fromisoformat(seit)).total_seconds() / 3600
        except (TypeError, ValueError):
            stunden = 0.0
        art = type(fehler).__name__
        filt: Dict[str, Any] = {"segment_id": seg_id, "date": tag, "hot_deals_offen": True}
        if obs is not None:
            filt["observed_at"] = obs
        if versuche < AUFGEBEN_NACH_VERSUCHEN or stunden < AUFGEBEN_NACH_STUNDEN:
            await db[TAGESSTATS].update_one(filt, {"$set": {"hot_deals_fehler": {"observed_at": obs, "versuche": versuche,
                                                                                 "seit": seit, "fehler": art}}})
            return False
        doc = await db[TAGESSTATS].find_one(filt, {"_id": 0, "observed_at": 1, "lauf_tag": 1, "version": 1, "definition_hash": 1})
        if not doc:
            return False                # inzwischen ersetzt oder ausgewertet: der naechste Durchlauf sieht den neuen Stand
        fass = fassung(doc)
        zus = {"ausgewertet_at": jetzt_dt.isoformat(), "observed_at": doc.get("observed_at"), "lauf_tag": doc.get("lauf_tag"),
               "version": fass[0], "definition_hash": fass[1], "basis_ok": False, "grund": GRUND_AUSWERTUNG_FEHLER,
               "ids": [], "privat_ids": [], "klassen": {}, "ereignisse": {}, "fehlversuche": versuche, "fehler": art,
               "fehler_seit": seit}
        await _abschliessen(db, seg_id, tag, doc, zus, [], [], [])
        log.error("Hot-Deal-Auswertung %s %s nach %d Fehlversuchen aufgegeben (%s) — neuere Tage laufen weiter",
                  seg_id, tag, versuche, art)
        await _alarm_tag(db, seg_id, tag, fehler=f"Hot-Deal-Auswertung nach {versuche} Fehlversuchen aufgegeben ({art}) — "
                                                 "Hot Deals dieses Tages fehlen, Protokoll pruefen", versuche=versuche)
        return True
    except Exception:  # noqa: BLE001
        log.exception("Fehlversuch der Hot-Deal-Auswertung %s %s nicht gezaehlt", d.get("segment_id"), d.get("date"))
        return False


async def auswerten_faellige(db, *, limit: int = AUSWERTUNG_MAX_JE_LAUF, segment_ids: Optional[List[str]] = None,
                             jetzt: Optional[datetime] = None) -> Dict[str, Any]:
    """Alle offenen Tagesdokumente (hot_deals_offen) in Tagesreihenfolge auswerten. segment_ids grenzt ein
    (Tests, Admin). Ein Fehler in einem Segment haelt die anderen nicht auf. Leere Tageswerte des laufenden Tages
    warten bis nach Tagesende (Pruefbefund B0) und werden hier gar nicht erst geladen; ein leerer Tageswert eines
    vergangenen Tages wartet, solange ein Abruf dieses Tages noch laeuft (_leer_wartet).
    Runde 3 (hotdeals#1): die Fehlversuche werden erst am Ende des Durchlaufs gezaehlt — und nur, wenn in diesem
    Durchlauf ein ANDERES Segment vollstaendig ausgewertet wurde (sonst 'fehler_uebergreifend', nichts gezaehlt: ein
    Fehler fuer alle Segmente wird nach der Korrektur nachgeholt statt verworfen). Ein aufgegebener Tag gibt die
    neueren Tage seines Segments ab dem naechsten Durchlauf frei."""
    filt: Dict[str, Any] = {"hot_deals_offen": True, "$nor": [{"sample_size": 0, "date": {"$gte": konfig.heute_tag(jetzt)}}]}
    if segment_ids is not None:
        filt["segment_id"] = {"$in": [str(s) for s in segment_ids]}
    offene = await db[TAGESSTATS].find(filt, {"_id": 0, "segment_id": 1, "date": 1, "observed_at": 1, "hot_deals_fehler": 1})\
        .sort([("date", 1), ("segment_id", 1)]).to_list(max(1, int(limit)))
    z: Dict[str, Any] = {"offen": len(offene), "ausgewertet": 0, "ohne_basis": 0, "fehler": 0, "ereignisse": 0}
    halten: set = set()          # Segmente, deren neuere Tage in diesem Durchlauf warten
    gescheitert: List[Tuple[Dict[str, Any], BaseException]] = []
    gelungen: set = set()        # Segmente mit vollstaendiger Auswertung in diesem Durchlauf (Runde 3)
    for d in offene:
        if str(d["segment_id"]) in halten:
            # Pruefbefund B2: ein aelterer Tag dieses Segments ist eben gescheitert (oder sein leerer Tageswert wartet
            # noch auf einen Lauf desselben Tages) — die neueren Tage bleiben offen und kommen in einem spaeteren
            # Durchlauf NACH ihm dran (sonst schloesse die Reihenfolge-Sperre den aelteren Tag danach ohne Ereignisse,
            # 'ein aelterer Tag nie nach einem neueren')
            z["zurueckgestellt"] = z.get("zurueckgestellt", 0) + 1
            continue
        if await wartung_aktiv(db):
            # Pruefbefund B10: Schreibpause (Sicherung/Restore) — sofort anhalten, der Rest bleibt offen; der naechste
            # Takt nach der Pause macht weiter (idempotent). Die Sicherung wartet sonst bis zu 120/180 s und laeuft
            # dann trotzdem, waehrend hier weiter geschrieben wuerde.
            z["wartung"] = True
            break
        try:
            r = await segment_tag_auswerten(db, d["segment_id"], d["date"], jetzt=jetzt)
        except Exception as e:  # noqa: BLE001
            log.exception("Hot-Deal-Auswertung %s %s gescheitert", d.get("segment_id"), d.get("date"))
            z["fehler"] += 1
            # Runde 3: gezaehlt wird erst am Ende (nur segmentbezogene Fehler); bis dahin warten die neueren Tage
            gescheitert.append((d, e))
            halten.add(str(d["segment_id"]))
            continue
        if r.get("status") == "leer_vorlaeufig":
            # Runde 2 (B0): leerer Tageswert wartet noch auf einen Lauf desselben Tages — neuere Tage des Segments danach
            z["leer_wartet"] = z.get("leer_wartet", 0) + 1
            halten.add(str(d["segment_id"]))
            continue
        if r.get("status") == "ausgewertet":
            z["ausgewertet"] += 1
            z["ereignisse"] += sum((r.get("ereignisse") or {}).values())
            gelungen.add(str(d["segment_id"]))
        elif r.get("status") == "ohne_basis":
            z["ohne_basis"] += 1
    if gescheitert and not z.get("wartung"):
        for d, e in gescheitert:
            if not (gelungen - {str(d["segment_id"])}):
                # kein anderes Segment lief in diesem Durchlauf fehlerfrei durch: segmentuebergreifend (z. B. Deploy) —
                # nicht zaehlen, der Tag bleibt offen und wird nach der Korrektur nachgeholt
                z["fehler_uebergreifend"] = z.get("fehler_uebergreifend", 0) + 1
            elif await _fehlversuch(db, d, e, jetzt):
                # dauerhaft und nur in diesem Segment gescheitert — abgeschlossen, die neueren Tage laufen ab dem
                # naechsten Durchlauf weiter
                z["aufgegeben"] = z.get("aufgegeben", 0) + 1
    return z


# ---------------------------------------------------------------- Lesen (nur Super-Admin, routes/markt_admin)
SORTIERUNGEN = {
    "vorteil_pct": [("diff_pct", -1), ("diff_eur", -1)],
    "vorteil_eur": [("diff_eur", -1), ("diff_pct", -1)],
    "neueste": [("hot_seit_at", -1), ("diff_pct", -1)],
    "privat": [("privat", -1), ("diff_pct", -1)],
    "modell": [("segment_label", 1), ("year_from", 1), ("min_km", 1), ("diff_pct", -1)],
    "ez": [("ez_year", -1), ("diff_pct", -1)],
    "km": [("mileage_km", 1), ("diff_pct", -1)],
    "liquiditaet": [("liquiditaet_rang", -1), ("diff_pct", -1)],
    "klasse": [("klasse_rang", -1), ("diff_pct", -1)],
}
LIMIT_MAX = 500
STATUS_FILTER = ("aktuell", "alle")


async def _aktive_segmente(db) -> List[str]:
    return [str(x) for x in await db[SEGMENTE].distinct("id", {"enabled": True})]


async def _anzahl_deals(db, filt: Dict[str, Any]) -> int:
    """Anzahl VERSCHIEDENER Deals (Segment, Inserat) mit passenden Ereignissen — eine zweite heisse Phase am
    selben Tag ist kein zweiter neuer Deal (Pruefbefund B0: vorher zaehlte die Kachel Ereignisse)."""
    erg = await db[HOTDEAL_EREIGNISSE].aggregate([{"$match": filt}, {"$group": {"_id": {"s": "$segment_id", "l": "$listing_id"}}},
                                                  {"$count": "n"}]).to_list(1)
    return int(erg[0]["n"]) if erg else 0


async def zusammenfassung(db) -> Dict[str, Any]:
    """Abschnitt 26: heute gepruefte / gueltige Modelle, neue Deals heute, aktive nach Klasse, davon privat.
    'Aktiv' nur in aktiven Segmenten (Deals einer frueheren Fassung zaehlen nicht mehr). Runde 3 (hotdeals#0): ein
    nach wiederholten Fehlern aufgegebener Tag (grund 'auswertung_fehler') ist nicht 'geprueft'."""
    heute = konfig.heute_tag()
    geprueft = [m for m in await db[TAGESSTATS].distinct("model_id", {"date": heute, "hot_deals.ausgewertet_at": {"$exists": True},
                                                                      "hot_deals.grund": {"$ne": GRUND_AUSWERTUNG_FEHLER}}) if m]
    gueltig = [m for m in await db[TAGESSTATS].distinct("model_id", {"date": heute, "hot_deals.basis_ok": True}) if m]
    aktiv = {"status": AKTIV, "segment_id": {"$in": await _aktive_segmente(db)}}
    neu = {"tag": heute, "typ": {"$in": [NEW, BECAME]}}
    return {"tag": heute, "modelle_geprueft": len(geprueft), "modelle_gueltig": len(gueltig),
            "neue_deals_heute": await _anzahl_deals(db, neu),
            "neue_privat_heute": await _anzahl_deals(db, {**neu, "privat": True}),
            "aktiv": await db[HOTDEALS].count_documents(aktiv),
            "deal": await db[HOTDEALS].count_documents({**aktiv, "klasse": "DEAL"}),
            "strong": await db[HOTDEALS].count_documents({**aktiv, "klasse": "STRONG"}),
            "extreme": await db[HOTDEALS].count_documents({**aktiv, "klasse": "EXTREME"}),
            "davon_privat": await db[HOTDEALS].count_documents({**aktiv, "privat": True})}


async def liste(db, *, status: str = "aktuell", klasse: Optional[str] = None, privat: Optional[bool] = None,
                model_id: Optional[str] = None, make: Optional[str] = None, ez: Optional[int] = None,
                km_min: Optional[int] = None, km_max: Optional[int] = None, segment_id: Optional[str] = None,
                heute_neu: bool = False, sort: str = "vorteil_pct", limit: int = 200) -> Dict[str, Any]:
    """Hot-Deal-Liste (Abschnitt 26) — nur lesend. status 'aktuell' = ACTIVE in aktiven Segmenten, 'alle' = auch
    verlassene/entfernte (Historie der letzten ALLE_FENSTER_TAGE nach letztem Ereignis). privat True = nur
    Privatangebote, False = Haendler/unbekannt."""
    heute = konfig.heute_tag()
    filt: Dict[str, Any] = {}
    fenster_von: Optional[str] = None
    if status == "aktuell":
        segs = await _aktive_segmente(db)
        filt["status"] = AKTIV
        filt["segment_id"] = {"$in": [s for s in segs if s == segment_id] if segment_id else segs}
    else:
        # Pruefbefund B14: Zeitfenster mit Index statt Vollscan samt Sortierung ueber die nie bereinigte Historie
        fenster_von = _tag_minus(heute, ALLE_FENSTER_TAGE)
        filt["last_event_tag"] = {"$gte": fenster_von}
        if segment_id:
            filt["segment_id"] = segment_id
    if klasse:
        filt["klasse"] = klasse
    if privat is True:
        filt["privat"] = True
    elif privat is False:
        filt["privat"] = {"$ne": True}
    if model_id:
        filt["model_id"] = str(model_id)
    if make:
        filt["make"] = {"$regex": f"^{re.escape(str(make).strip())}$", "$options": "i"}
    if ez is not None:
        filt["ez_year"] = int(ez)
    if km_min is not None or km_max is not None:
        filt["mileage_km"] = {**({"$gte": int(km_min)} if km_min is not None else {}), **({"$lte": int(km_max)} if km_max is not None else {})}
    if heute_neu:
        filt["hot_seit_tag"] = heute
    n = max(1, min(int(limit or 200), LIMIT_MAX))
    deals = await db[HOTDEALS].find(filt, {"_id": 0}).sort(SORTIERUNGEN.get(sort) or SORTIERUNGEN["vorteil_pct"]).to_list(n)
    for d in deals:
        try:
            d["stand_alter_tage"] = (datetime.strptime(heute, "%Y-%m-%d") - datetime.strptime(str(d.get("stand_tag")), "%Y-%m-%d")).days
        except (TypeError, ValueError):
            d["stand_alter_tage"] = None
        d["heute_neu"] = d.get("hot_seit_tag") == heute and d.get("status") == AKTIV
    return {"zusammenfassung": await zusammenfassung(db), "deals": deals, "anzahl": len(deals), "gekuerzt": len(deals) >= n,
            "sort": sort, "status": status, "fenster_von": fenster_von, "hinweis": HINWEIS, "schwellen": schwellen()}


async def ereignisse(db, segment_id: str, listing_id: str) -> Optional[Dict[str, Any]]:
    """Zustand + vollstaendige Ereignis-Historie eines Deals (chronologisch) — nur lesend."""
    zustand = await db[HOTDEALS].find_one({"segment_id": str(segment_id), "listing_id": str(listing_id)}, {"_id": 0})
    if not zustand:
        return None
    ev = await db[HOTDEAL_EREIGNISSE].find({"segment_id": str(segment_id), "listing_id": str(listing_id)}, {"_id": 0})\
        .sort([("lauf_at", 1), ("at", 1)]).to_list(1000)
    return {"deal": zustand, "ereignisse": ev, "hinweis": HINWEIS}
