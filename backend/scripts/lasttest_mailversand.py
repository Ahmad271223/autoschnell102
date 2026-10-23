# -*- coding: utf-8 -*-
"""Lasttest: 30 Sucher verschicken GLEICHZEITIG Vertragsmails.

Pruefbericht 20.09.2026 (P1): "Dein Code geht selbst von ~10 Resend-Anfragen
pro Sekunde aus. Je Worker duerfen 3 gleichzeitig laufen, bei 4 Workern also
12 je Server, mit zwei Servern mehr. Ein Vertragsversand ist Kundenmail +
Kopie an den Sucher, 30 Sendungen sind also ~60 Aufrufe. 429 werden zwar
wiederholt, aber es gibt keinen zentralen Resend-Begrenzer."

Der Einwand stimmt. Bewiesen war er nie — und echte Mails zum Messen zu
verschicken verbietet sich. Dieses Skript stellt deshalb Resend NACH:

  * ein Eimer mit RATE Anfragen je Sekunde fuers ganze Konto (Standard 10),
    genau wie Resend es tut
  * alles darueber bekommt 429 mit Retry-After, wie bei Resend
  * die echte Sendefunktion (email_service._send_resend) laeuft unveraendert,
    samt Semaphore, Backoff, Idempotency-Key und Wartebudget

Simuliert wird der GANZE Verbund: PROZESSE (Standard 8 = 4 Worker x 2 Server)
mal RESEND_PARALLEL gleichzeitige Anfragen treffen auf den einen Eimer.

Aufruf (keine Netzverbindung, keine echten Mails):
    python -X utf8 scripts/lasttest_mailversand.py
    python -X utf8 scripts/lasttest_mailversand.py --sendungen 30 --rate 10

Exit 0 = jede Mail ging raus, 1 = mindestens eine scheiterte sichtbar.
"""
import argparse
import asyncio
import os
import statistics
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

os.environ.setdefault("RESEND_API_KEY", "re_lasttest_kein_echter_schluessel")


class _Antwort:
    """Das Wenige, das _send_resend von einer httpx-Antwort liest."""

    def __init__(self, status: int, retry_after=None, kennung: str = ""):
        self.status_code = status
        self.headers = {"retry-after": str(retry_after)} if retry_after else {}
        self.text = "" if status < 400 else '{"message":"Too many requests"}'
        self._kennung = kennung

    def json(self) -> dict:
        return {"id": self._kennung}


class Eimer:
    """Resends Tempolimit: RATE Anfragen je Sekunde fuers ganze Konto."""

    def __init__(self, rate: float, retry_after):
        self.rate = rate
        self.retry_after = retry_after
        self.erlaubt = 0
        self.abgelehnt = 0
        self._fenster = None
        self._im_fenster = 0
        self._sperre = asyncio.Lock()

    async def anfrage(self) -> _Antwort:
        async with self._sperre:
            jetzt = time.monotonic()
            if self._fenster is None or jetzt - self._fenster >= 1.0:
                self._fenster = jetzt
                self._im_fenster = 0
            if self._im_fenster >= self.rate:
                self.abgelehnt += 1
                return _Antwort(429, self.retry_after)
            self._im_fenster += 1
            self.erlaubt += 1
            nr = self.erlaubt
        # Resend antwortet nicht sofort — ohne diese Zeit misst der Test zu gut.
        await asyncio.sleep(0.05)
        return _Antwort(200, kennung=f"mail_{nr}")


def _client_klasse(eimer: Eimer):
    class _Client:
        def __init__(self, *a, **kw):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        async def post(self, url, json=None, headers=None):
            return await eimer.anfrage()
    return _Client


async def lauf(sendungen: int, rate: float, prozesse: int, retry_after) -> int:
    import httpx
    import email_service as ES

    eimer = Eimer(rate, retry_after)
    httpx.AsyncClient = _client_klasse(eimer)          # nur in diesem Prozess

    # Der ganze Verbund trifft auf EINEN Eimer: so viele gleichzeitige
    # Anfragen, wie alle Worker zusammen zulassen.
    gesamt_parallel = ES.RESEND_PARALLEL * prozesse
    ES._resend_sperre = asyncio.Semaphore(gesamt_parallel)
    ES._resend_sperre_loop = asyncio.get_running_loop()
    # Dieser eine Prozess stellt den GANZEN Verbund dar. Der Takt muss also
    # das volle Konto-Limit fuehren, nicht den Anteil eines einzelnen Workers.
    ES._resend_takt = ES._Takt(ES.RESEND_RATE)
    ES._resend_takt_loop = asyncio.get_running_loop()

    aufrufe = sendungen * 2        # Kundenmail + Kopie an den Sucher
    print(f"  Sendungen ................. {sendungen}  (= {aufrufe} Mail-Aufrufe)")
    print(f"  Resend-Tempolimit ......... {rate:g}/s"
          + (f", Retry-After {retry_after}s" if retry_after else ", ohne Retry-After"))
    print(f"  gleichzeitig im Verbund ... {gesamt_parallel} "
          f"({prozesse} Prozesse x RESEND_PARALLEL {ES.RESEND_PARALLEL})")
    print(f"  Wiederholungen ............ {ES.RESEND_VERSUCHE} Versuche, "
          f"Budget {ES.RESEND_WARTEN_MAX:g}s")

    async def eine(i: int):
        t0 = time.monotonic()
        try:
            kennung = await ES._send_resend(
                to=f"kunde{i}@example.invalid", subject="Ihr Kaufvertrag",
                text="Anbei der Kaufvertrag.", html=None,
                anhang=None, anhang_name="Vertrag.pdf",
                reply_to=[], kopie=[], absender_name="AutoSchnell",
                idempotency_key=f"lasttest-{i:04d}")
            # WICHTIG: _send_resend wirft bei einer Ablehnung NICHT, es liefert
            # einen leeren Beleg zurueck. Der Versand faellt dann auf SMTP
            # zurueck — und ohne SMTP sieht der Nutzer "E-Mail-Versand
            # fehlgeschlagen". Ein leerer Beleg ist hier also ein Fehlschlag.
            if not kennung:
                return (False, time.monotonic() - t0,
                        "von Resend abgewiesen (429 nach allen Versuchen)")
            return True, time.monotonic() - t0, kennung
        except Exception as exc:                        # noqa: BLE001
            return False, time.monotonic() - t0, f"{exc.__class__.__name__}: {exc}"

    t0 = time.monotonic()
    ergebnis = await asyncio.gather(*[eine(i) for i in range(aufrufe)])
    gesamt = time.monotonic() - t0

    ok = [e for e in ergebnis if e[0]]
    schlecht = [e for e in ergebnis if not e[0]]
    zeiten = sorted(e[1] for e in ergebnis)
    print(f"\n  Von Resend angenommen ..... {len(ok)} von {aufrufe}")
    print(f"  Bei Resend gescheitert .... {len(schlecht)}"
          + ("   <- faellt auf SMTP zurueck; OHNE SMTP sieht der Nutzer "
             "einen Fehler" if schlecht else ""))
    print(f"  Von Resend abgewiesen (429) {eimer.abgelehnt}  "
          f"(alle wiederholt, Idempotency-Key = keine Doppelzustellung)")
    if zeiten:
        print(f"  Wartezeit Mitte/langsamste  {statistics.median(zeiten):.2f}s / "
              f"{zeiten[-1]:.2f}s")
    print(f"  Ansturm insgesamt ......... {gesamt:.2f}s")
    for e in schlecht[:5]:
        print(f"     FEHLER nach {e[1]:.1f}s: {e[2]}")
    return len(schlecht)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Lasttest gleichzeitiger Mailversand")
    ap.add_argument("--sendungen", type=int, default=30,
                    help="gleichzeitige Vertragssendungen (je 2 Mail-Aufrufe)")
    ap.add_argument("--rate", type=float, default=10.0,
                    help="Resend-Tempolimit je Sekunde fuers ganze Konto")
    ap.add_argument("--prozesse", type=int, default=8,
                    help="Worker im Verbund (4 je Server x 2 Server)")
    ap.add_argument("--retry-after", type=float, default=None,
                    help="Retry-After, das der Anbieter mitschickt (Sekunden)")
    a = ap.parse_args(argv)

    print("Lasttest Mailversand — Resend wird nachgestellt, es geht KEINE echte "
          "Mail raus.\n")
    schlecht = asyncio.run(lauf(a.sendungen, a.rate, a.prozesse, a.retry_after))
    print("\nERGEBNIS")
    if schlecht:
        print(f"  {schlecht} Mails scheiterten sichtbar — der Versand haelt "
              f"{a.sendungen} gleichzeitige Sendungen NICHT aus.")
    else:
        print(f"  Alle Mails gingen raus. {a.sendungen} gleichzeitige Sendungen "
              f"sind kein Problem.")
    return 1 if schlecht else 0


if __name__ == "__main__":
    raise SystemExit(main())
