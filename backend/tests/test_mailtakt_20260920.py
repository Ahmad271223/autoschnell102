# -*- coding: utf-8 -*-
"""Nachpruefung 20.09.2026 (P1): Takt fuer den Resend-Versand.

Der Bericht: "Standardmaessig duerfen pro Backend-Worker 3 Resend-Aufrufe
gleichzeitig laufen. Bei 4 Workern sind das bis zu 12 auf einem Server, mit
zwei Servern mehr. 30 gleichzeitige Sendungen koennen ~60 Mail-Aufrufe
erzeugen. Es gibt keinen zentralen Resend-Limiter — ich kann nicht
garantieren, dass alle ohne sichtbaren Fehler durchgehen."

Nachgemessen mit scripts/lasttest_mailversand.py (Resend nachgestellt, echte
Sendefunktion):

    30 Sendungen  ->   0 Fehlschlaege   (knapp, ein Lauf hatte 7)
    46 Sendungen  ->  29-32 von 92 Aufrufen endgueltig abgewiesen
    46 + Retry-After 1 -> 32 von 92

Der Einwand war also berechtigt. Grund: die Obergrenze zaehlt GLEICHZEITIGE
Anfragen, nicht Anfragen je Sekunde; und alle Wartenden kamen im Gleichschritt
zurueck (fester Backoff, nur 0-0,5 s Streuung). Seit dem Takt:

    30 / 46 / 60 Sendungen -> je 0 Fehlschlaege, 345 Ablehnungen runter auf 6

Diese Tests halten beides fest: den Takt und die volle Streuung.
"""
import asyncio
import sys
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND))

import email_service as E  # noqa: E402


def test_01_takt_haelt_das_konto_limit_ein():
    """Zehn Anfragen bei 10/s brauchen rund eine Sekunde — nicht null."""
    async def lauf():
        takt = E._Takt(10.0)
        loop = asyncio.get_running_loop()
        t0 = loop.time()
        await asyncio.gather(*[takt.platz() for _ in range(10)])
        return loop.time() - t0

    gebraucht = asyncio.run(lauf())
    assert 0.8 <= gebraucht <= 1.6, (
        f"zehn Anfragen bei 10/s brauchten {gebraucht:.2f}s — der Takt greift nicht")


def test_02_takt_verteilt_gleichmaessig_statt_im_schwarm():
    """Der Sinn des Takts: kein Pulk, sondern gleiche Abstaende."""
    async def lauf():
        takt = E._Takt(20.0)          # 50 ms Abstand
        loop = asyncio.get_running_loop()
        zeiten = []

        async def eine():
            await takt.platz()
            zeiten.append(loop.time())

        await asyncio.gather(*[eine() for _ in range(8)])
        zeiten.sort()
        return [b - a for a, b in zip(zeiten, zeiten[1:])]

    abstaende = asyncio.run(lauf())
    assert all(d >= 0.03 for d in abstaende), (
        f"Anfragen kamen im Pulk statt im Takt: {[round(d, 3) for d in abstaende]}")


def test_03_ohne_rate_kein_abstand():
    """Rate 0 = abgeschaltet (so schalten Tests den Takt weg)."""
    async def lauf():
        takt = E._Takt(0)
        loop = asyncio.get_running_loop()
        t0 = loop.time()
        await asyncio.gather(*[takt.platz() for _ in range(50)])
        return loop.time() - t0

    assert asyncio.run(lauf()) < 0.2


def test_04_jeder_prozess_bekommt_nur_seinen_anteil():
    """8 Prozesse teilen sich die 10 Anfragen je Sekunde des Kontos."""
    async def lauf():
        E._resend_takt = None                      # neu bauen lassen
        E._resend_takt_loop = None
        return E._takt().abstand

    abstand = asyncio.run(lauf())
    erwartet = 1.0 / (E.RESEND_RATE / E.RESEND_PROZESSE)
    assert abs(abstand - erwartet) < 1e-9, (
        f"Abstand {abstand:.3f}s statt {erwartet:.3f}s — der Anteil je Prozess "
        "stimmt nicht")
    assert E.RESEND_PROZESSE >= 8, (
        "RESEND_PROZESSE muss mindestens Worker x Server abdecken (4 x 2)")


def test_05_wartezeit_streut_voll():
    """Vorher: fester Backoff + 0-0,5 s. Alle kamen fast gleichzeitig zurueck
    und trieben sich gegenseitig wieder in den 429. Jetzt volle Streuung."""
    werte = [E._wartezeit(3, None) for _ in range(200)]
    fenster = min(4.0, 0.5 * (2 ** 3))
    assert min(werte) >= fenster / 2 - 1e-9 and max(werte) <= fenster + 1e-9, (
        f"Wartezeit liegt ausserhalb des Fensters: {min(werte):.2f}-{max(werte):.2f}")
    # Die Streuung muss einen nennenswerten Teil des Fensters abdecken —
    # sonst kommen wieder alle im selben Moment zurueck.
    assert max(werte) - min(werte) > fenster * 0.3, (
        "die Wartezeiten liegen zu dicht beieinander (Schwarm-Gefahr)")


def test_06_retry_after_des_anbieters_geht_vor():
    """Sagt Resend selbst, wie lange zu warten ist, gilt das — gedeckelt."""
    assert E._wartezeit(0, "2") == 2.0
    assert E._wartezeit(0, "9999") == 10.0, "Retry-After muss gedeckelt bleiben"
    assert E._wartezeit(0, "quatsch") <= 0.5, "unlesbares Retry-After -> Streuung"


def test_07_der_takt_sitzt_im_sendeweg():
    """Gegenprobe an der Quelle: der Takt wird vor dem Senden abgewartet."""
    quelle = (BACKEND / "email_service.py").read_text(encoding="utf-8")
    anfang = quelle.index("async def _send_resend")
    block = quelle[anfang:anfang + 4000]
    code = "\n".join(z.split("#", 1)[0] for z in block.splitlines())
    assert "await _takt().platz()" in code, (
        "der Sendeweg wartet den Takt nicht ab — dann greift er nirgends")
    assert code.index("await _takt().platz()") < code.index("client.post("), (
        "der Takt wird erst NACH dem Senden abgewartet")
