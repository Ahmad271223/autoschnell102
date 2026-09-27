# -*- coding: utf-8 -*-
"""Auswertungs-Worker der Marktanalyse (Master-Auftrag Ahmad 26.09.2026, Phase D/E; Abschnitte 39, 49, 50).

Eigener Hintergrundjob in server.py ('markt_auswertung'), getrennt vom Crawl-Worker ('markt'):
  * laeuft auch bei ausgeschaltetem Crawler — er liest nur, was schon gespeichert ist
  * Hot Deals: offene Tagesdokumente (hot_deals_offen) auswerten (markt.deals)
  * Berichte (Phase E): faellige Perioden (Periodenende + Karenz) einfrieren (markt.berichte) — idempotent,
    ein Bericht je Modell/Periode (Unique-Index), laufende Perioden nie
  * haelt waehrend einer Schreibpause (Sicherung/Restore) an: wartung.aktiv_async(db) vor dem Durchlauf, vor
    jedem Tagesdokument (markt.deals) und vor den Berichten (Pruefbefund B10)
  * zwei Server: ein Durchlauf haelt die Sperre 'markt-auswertung' (job_lock); der andere wartet
  * Fehler: Protokoll + EIN Betriebsalarm 'markt_auswertung_fehler' — nie ein Einfluss auf Vergleich,
    Vertrag, PDF, Versand oder Fahrer; der Alarm schliesst sich nach dem naechsten fehlerfreien Durchlauf,
    gleich in welchem Prozess (auch Admin-Knopf) — ohne prozesslokalen Merker (Pruefbefund B11). Scheitert ein
    Tagesdokument dauerhaft und nur in seinem Segment, schliesst markt.deals es nach gezaehlten Fehlversuchen UND
    einer Mindestdauer ab (neuere Tage laufen weiter; ein segmentuebergreifender Fehler zaehlt nicht, Runde 3) und
    legt dafuer einen eigenen Alarm (ref 'hot-deals:<Segment>:<Tag>') an, den kein Durchlauf schliesst (Pruefbefund
    Runde 2, B2); der eingefrorene Bericht nennt solche Tage
Dieses Modul loest NIE einen Marktabruf aus (Architekturtest test_b02) — Kosten der Auswertung: 0.
"""
from __future__ import annotations

import asyncio
import logging
from datetime import datetime
from typing import Any, Callable, Dict, Optional

from markt import berichte, deals, konfig, optimierung

log = logging.getLogger(__name__)

AUSWERTUNG_TAKT_S = 300          # alle 5 Minuten: neue Tageswerte sind spaetestens dann ausgewertet
SPERRE = "markt-auswertung"
SPERRE_S = 3600                  # ein Durchlauf (auch das erste Nachholen) bleibt deutlich darunter
ALARM = "markt_auswertung_fehler"


async def _alarm(db, typ: str, ref: str = "", **details) -> None:
    try:
        from betrieb import alarm
        await alarm(db, typ, ref=ref, **details)
    except Exception:  # noqa: BLE001
        pass


async def _alarm_zu(db, typ: str, ref: str = "") -> None:
    try:
        from betrieb import alarm_schliessen
        await alarm_schliessen(db, typ, ref=ref)
    except Exception:  # noqa: BLE001
        pass


def _schreiber():
    """Der Durchlauf zaehlt als Hintergrund-Schreiber (die Sicherung wartet darauf)."""
    try:
        import wartung
        return wartung.hintergrund_schreibt()
    except Exception:  # noqa: BLE001
        import contextlib
        return contextlib.nullcontext()


async def durchlauf(db, *, jetzt: Optional[datetime] = None) -> Dict[str, Any]:
    """Ein Durchlauf (Worker und Admin-Knopf): Sperre nehmen, Hot Deals auswerten, Stand merken.
    {"gesperrt": True}, wenn gerade ein anderer Prozess auswertet."""
    from job_lock import acquire, release
    token = await acquire(db, SPERRE, ttl_seconds=SPERRE_S)
    if not token:
        return {"gesperrt": True}
    try:
        with _schreiber():
            erg: Dict[str, Any] = {"hot_deals": await deals.auswerten_faellige(db, jetzt=jetzt)}
            if erg["hot_deals"].get("wartung") or await deals.wartung_aktiv(db):
                # Pruefbefund B10: Schreibpause begann waehrend des Durchlaufs — keine Berichte, kein Stand-Merker;
                # der naechste Takt nach der Pause macht weiter (offene Tage/Perioden bleiben offen)
                erg["wartung"] = True
                return erg
            # Phase E: erst die Hot Deals (die Berichte frieren die Hot Deals des Zeitraums mit ein), dann die
            # faelligen Berichte — laufende Perioden werden nie final gespeichert
            erg["berichte"] = await berichte.faellige_finalisieren(db, jetzt=jetzt)
            erg["health"] = await optimierung.taeglich(db, jetzt=jetzt)      # Phase F: einmal je Tag, nach Hot Deals und Berichten
            await konfig.merker_setzen(db, konfig.AUSWERTUNG_DOK, letzter_lauf_at=konfig.jetzt_iso(), ergebnis=erg)
        if not fehler_anzahl(erg):
            # Pruefbefund B11: ein fehlerfreier Durchlauf schliesst den Alarm, egal welcher Prozess (oder der
            # Admin-Knopf) ihn oeffnete — der fruehere prozesslokale Merker ging bei Neustart/Deploy verloren,
            # der Alarm blieb offen und unterdrueckte die Mail beim naechsten echten Ausfall
            await _alarm_zu(db, ALARM, ref="auswertung")
        return erg
    finally:
        await release(db, SPERRE, token)


def fehler_anzahl(erg: Dict[str, Any]) -> int:
    return sum(int((teil or {}).get("fehler") or 0) for teil in erg.values() if isinstance(teil, dict))


async def stand(db) -> Dict[str, Any]:
    return await konfig.merker_lesen(db, konfig.AUSWERTUNG_DOK)


async def worker_forever(db, erfolg: Optional[Callable[[], None]] = None, takt_s: int = AUSWERTUNG_TAKT_S) -> None:
    """Dauerschleife je Prozess. Wirft nie nach aussen (ein Fehler wird gemeldet, der naechste Takt versucht es
    erneut) — der Job bleibt 'laeuft' und nimmt die Instanz nie aus dem Lastverteiler."""
    await asyncio.sleep(30)
    while True:
        try:
            import wartung
            if await wartung.aktiv_async(db):
                await asyncio.sleep(60)
                continue
            erg = await durchlauf(db)          # schliesst den Alarm selbst nach einem fehlerfreien Durchlauf
            fehler_n = fehler_anzahl(erg)
            if fehler_n:
                # einzelne Segmente/Modelle scheiterten (die anderen liefen weiter) — ein Alarm, kein Abbruch
                await _alarm(db, ALARM, ref="auswertung", fehler=f"{fehler_n} Auswertung(en) gescheitert — Protokoll pruefen")
            if erfolg:
                erfolg()
        except Exception as e:  # noqa: BLE001
            log.exception("Markt-Auswertung gescheitert")
            await _alarm(db, ALARM, ref="auswertung", fehler=str(e)[:300])
        await asyncio.sleep(takt_s)
