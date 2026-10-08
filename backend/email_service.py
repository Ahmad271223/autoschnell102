"""Zentraler E-Mail-Versand — bevorzugt über Resend, sonst über SMTP.

Absender ist IMMER unsere eigene Adresse (nur die ist bei Resend
freigeschaltet, sonst landen die Mails im Spam). Der Anzeigename darf die
Firma des Händlers nennen, z. B. „Autohaus Müller über AutoSchnell". Damit
der Empfänger trotzdem beim richtigen Menschen landet, wird die
Antwortadresse (Reply-To) auf den Sucher gesetzt, der die Mail ausgelöst
hat — antwortet der Verkäufer, schreibt er direkt ihm.

Konfiguration über .env (Resend bevorzugt):
    RESEND_API_KEY=re_...
    MAIL_FROM=AutoSchnell <vertrag@deine-domain.de>   # muss in Resend verifiziert sein
    MAIL_ABSENDER_NAME=AutoSchnell                    # optional, Anzeigename

Alternativ (oder als Rückfall) klassisch per SMTP:
    SMTP_HOST=smtp.resend.com
    SMTP_PORT=587
    SMTP_USER=resend
    SMTP_PASS=re_...
    SMTP_FROM=AutoSchnell <vertrag@deine-domain.de>

Ohne Konfiguration ist `email_configured()` False — Aufrufer sollen dann eine
verständliche Meldung liefern statt still zu scheitern.
"""
from __future__ import annotations

import asyncio
import base64
import logging
import os
import re
import smtplib
import ssl
from pathlib import Path as _Path

# Eigene .env laden: dieses Modul liest die Einstellungen beim Import. Wird
# es einmal VOR deps/auth importiert, waeren sie sonst leer und der Versand
# gaelte faelschlich als nicht eingerichtet.
try:
    from dotenv import load_dotenv as _load_dotenv
    _load_dotenv(_Path(__file__).parent / ".env")
except ImportError:  # pragma: no cover
    pass
from email.message import EmailMessage
from email.utils import formataddr, parseaddr
from typing import List, Optional, Sequence

log = logging.getLogger("autohandel")

RESEND_API_KEY = os.environ.get("RESEND_API_KEY", "").strip()
RESEND_URL = "https://api.resend.com/emails"

SMTP_HOST = os.environ.get("SMTP_HOST", "").strip()
from konfig import zahl_env  # Pruefung 14.09.2026: keine Abstuerze durch .env-Tippfehler
SMTP_PORT = zahl_env("SMTP_PORT", 587, unten=1, oben=65535)
SMTP_USER = os.environ.get("SMTP_USER", "").strip()
SMTP_PASS = os.environ.get("SMTP_PASS", "").strip()
SMTP_FROM = os.environ.get("SMTP_FROM", "").strip() or SMTP_USER
# Gemeinsame Absenderangabe für beide Wege.
MAIL_FROM = os.environ.get("MAIL_FROM", "").strip() or SMTP_FROM
MAIL_ABSENDER_NAME = os.environ.get("MAIL_ABSENDER_NAME", "AutoSchnell").strip() or "AutoSchnell"

_MAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[A-Za-z]{2,}$")


def resend_aktiv() -> bool:
    return bool(RESEND_API_KEY and absender_adresse())


def smtp_aktiv() -> bool:
    return bool(SMTP_HOST and SMTP_USER and SMTP_PASS)


def email_configured() -> bool:
    return resend_aktiv() or smtp_aktiv()


def absender_adresse() -> str:
    """Die reine Adresse aus MAIL_FROM/SMTP_FROM (ohne Anzeigename)."""
    return (parseaddr(MAIL_FROM)[1] or "").strip()


def gueltige_adresse(wert: str) -> bool:
    return bool(_MAIL_RE.match((wert or "").strip()))


def _absender(anzeigename: Optional[str] = None, *, kodiert: bool = True) -> str:
    """Baut den From-Kopf: unsere Adresse, davor ein sprechender Name.

    `anzeigename` ist die Firma des Händlers (seit 02.10.2026 ohne den Zusatz
    „über <Marke>" — Wunsch Ahmad); ohne Firma steht die Marke.

    `kodiert=True` liefert die SMTP-Form (Umlaute nach RFC 2047 kodiert),
    `kodiert=False` die reine UTF-8-Form für die Resend-Schnittstelle."""
    adresse = absender_adresse()
    name = (anzeigename or "").strip()
    # Wunsch Ahmad 02.10.2026: NUR der Name der Firma als Absender — der fruehere Zusatz
    # "<Firma> über AutoSchnell" entfaellt. Die Adresse bleibt unsere (DMARC/Resend).
    name = name or MAIL_ABSENDER_NAME
    if kodiert:
        return formataddr((name, adresse))
    # JSON ist UTF-8 — Resend kodiert den Anzeigenamen selbst korrekt.
    # Rollenpruefung 22.09.2026 (RP-433): Ein Firmenname wie "Autohaus
    # Müller, Inh. X" stand ungequotet vor der Adresse — laut RFC 5322 muss
    # ein Anzeigename mit Sonderzeichen (Komma, Semikolon, Punkt, Klammern …)
    # in Anfuehrungszeichen stehen, sonst liest ein Mailprogramm das Komma
    # als Trenner zweier Adressen. Zeilenumbrueche fliegen raus, " wird wie
    # bisher zu ', ein Backslash wird maskiert.
    name = " ".join(name.replace('"', "'").split())
    if any(z in name for z in '()<>[]:;@\\,."'):
        name = '"' + name.replace("\\", "\\\\") + '"'
    return f'{name} <{adresse}>'


def _liste(wert) -> List[str]:
    if not wert:
        return []
    if isinstance(wert, str):
        wert = [wert]
    return [w.strip() for w in wert if w and w.strip()]


# --------------------------------------------------------------- Resend
# Runde 21 (Kapazitaetsfrage 50 gleichzeitige Nutzer): Resend erlaubt
# standardmaessig 10 Anfragen je Sekunde fuer das ganze Konto; jeder
# Vertragsversand braucht zwei (Mail + Kopie an den Sucher). Schickten viele
# Sucher gleichzeitig, antwortete Resend mit 429 und der Versand scheiterte
# sofort ("E-Mail-Versand fehlgeschlagen"). Jetzt: hoechstens
# RESEND_PARALLEL gleichzeitige Anfragen je Worker, und bei 429 bzw.
# voruebergehenden Serverfehlern ein erneuter Versuch mit kurzer Wartezeit
# (Retry-After des Anbieters, sonst 0,5-4 s plus Zufall, zusammen hoechstens
# RESEND_WARTEN_MAX Sekunden). Wiederholungen sind sicher, weil jede Mail
# einen Idempotency-Key traegt (Resend stellt dann nicht doppelt zu).
# Pruefbericht 20.09.2026 (P-19): ueber konfig statt int()/float() roh — das
# Modul wird erst beim ersten Versand geladen, ein Tippfehler in der .env
# brach vorher JEDEN Mailversand beim Import ab (production_check prueft
# die Ganzzahlen jetzt ebenfalls).
from konfig import kommazahl_env as _kommazahl_env, zahl_env as _zahl_env  # noqa: E402
RESEND_PARALLEL = _zahl_env("RESEND_PARALLEL", 3, unten=1)
RESEND_VERSUCHE = _zahl_env("RESEND_VERSUCHE", 6, unten=1)
RESEND_WARTEN_MAX = _kommazahl_env("RESEND_WARTEN_MAX", 20.0, unten=0.0)
# Nachpruefung 20.09.2026 (P1, gemessen mit scripts/lasttest_mailversand.py):
# Die Obergrenze oben zaehlt GLEICHZEITIGE Anfragen, nicht Anfragen je
# Sekunde — und sie gilt je Worker. Vier Worker auf zwei Servern duerfen
# damit 24 gleichzeitig losschicken, wo Resend 10 je Sekunde erlaubt. Bei
# 30 gleichzeitigen Sendungen ging das noch knapp auf; bei 46 (Zielgroesse)
# scheiterten 29 bis 32 der 92 Aufrufe endgueltig, weil alle Wartenden im
# Gleichschritt wiederkamen und sich gegenseitig wieder in den 429 trieben.
#
# Deshalb jetzt ein TAKT: jeder Prozess laesst nur seinen Anteil am
# Konto-Limit durch (RESEND_RATE geteilt durch RESEND_PROZESSE). Das
# braucht keine gemeinsame Ablage — die Teilung ist fuer alle gleich.
# RESEND_PROZESSE = Worker je Server x Server (Standard 4 x 2).
RESEND_RATE = _kommazahl_env("RESEND_RATE", 10.0, unten=0.1)      # P-19
RESEND_PROZESSE = _zahl_env("RESEND_PROZESSE", 8, unten=1)       # P-19
_RESEND_VORUEBERGEHEND = {429, 500, 502, 503, 504}
# Befund 106 (16.09.2026): nach diesen Antworten ist UNKLAR, ob Resend die
# Mail angenommen hat (429 = sicher abgelehnt, 5xx = vielleicht angenommen).
_RESEND_UNKLAR = {500, 502, 503, 504}
# Pruefung 04.10.2026 (Nr. 22, Zusatz): Resend antwortet 409, wenn derselbe
# Idempotency-Key gerade noch bearbeitet wird ("concurrent_idempotent_requests"
# — kurz warten, dann kommt die Antwort der ersten Abgabe) oder schon mit
# ANDEREM Inhalt benutzt wurde ("invalid_idempotent_request"). Beides heisst:
# unter diesem Schluessel ging womoeglich schon eine Mail raus. Vorher galt
# 409 als "sicher abgelehnt" und der SMTP-Rueckfall haette nachgesendet.
_RESEND_KONFLIKT = 409
#: Rueckgabe-Beleg von send_email_mit_beleg, wenn der Ausgang offen ist (die
#: Mail ist vielleicht angekommen). Der Aufrufer darf dann NICHT mit neuem
#: Schluessel nachsenden.
BELEG_UNKLAR = "unklar"


class ResendUnklar(RuntimeError):
    """Resend antwortete zuletzt mit 5xx — Zustellung moeglich, Ausgang unklar.
    Der Aufrufer darf dann NICHT auf SMTP zurueckfallen (doppelte Zustellung:
    die SMTP-Idempotenz kennt Resends Schluessel nicht)."""
_resend_sperre: Optional[asyncio.Semaphore] = None
_resend_sperre_loop = None


def _resend_semaphore() -> asyncio.Semaphore:
    """Semaphore je Event-Loop (Tests erzeugen eigene Loops)."""
    global _resend_sperre, _resend_sperre_loop
    loop = asyncio.get_running_loop()
    if _resend_sperre is None or _resend_sperre_loop is not loop:
        _resend_sperre = asyncio.Semaphore(RESEND_PARALLEL)
        _resend_sperre_loop = loop
    return _resend_sperre


class _Takt:
    """Laesst hoechstens `rate` Anfragen je Sekunde durch — jede bekommt den
    naechsten freien Platz. Kein Eimer, der sich fuellt: so geht kein Schwarm
    auf einmal los, und der Abstand bleibt gleichmaessig."""

    def __init__(self, rate: float):
        self.abstand = 1.0 / rate if rate > 0 else 0.0
        self.naechste = 0.0
        self.sperre = asyncio.Lock()

    async def platz(self) -> None:
        if not self.abstand:
            return
        loop = asyncio.get_running_loop()
        async with self.sperre:
            start = max(loop.time(), self.naechste)
            self.naechste = start + self.abstand
        schlaf = start - loop.time()
        if schlaf > 0:
            await asyncio.sleep(schlaf)


# Wunsch Ahmad 06.10.2026: "die SaaS muss jederzeit hunderte Mails rausschicken koennen". Der Anteil je
# Prozess (RESEND_RATE / RESEND_PROZESSE = 1,25 je Sekunde) liess EINEN Prozess nie schneller senden — 100
# Vertraege, die auf demselben Prozess landeten, brauchten 80 s, obwohl das Konto 10 je Sekunde erlaubt.
# Jetzt vergibt ein gemeinsamer Zaehler in der Datenbank die Sendeplaetze fuer ALLE Prozesse beider Server:
# wer gerade sendet, bekommt das ganze Konto-Limit, zusammen bleibt es bei RESEND_RATE je Sekunde (100 Mails
# = 10 s). Ist die Datenbank nicht erreichbar, gilt wieder der Anteil je Prozess — nie schneller als das
# Konto erlaubt. RESEND_TAKT_GEMEINSAM=false schaltet zurueck auf nur den Anteil je Prozess.
RESEND_TAKT_GEMEINSAM = (os.environ.get("RESEND_TAKT_GEMEINSAM", "true").strip().lower()
                         not in ("0", "false", "nein", "aus"))
#: Liegt der naechste gemeinsame Platz weiter als das in der Zukunft, stimmt etwas nicht (Uhr eines Servers
#: verstellt) — dann beginnt der Zaehler neu, statt alle Mails minutenlang warten zu lassen.
RESEND_TAKT_MAX_VORLAUF_S = 600.0


class _GemeinsamerTakt:
    """Sendeplaetze fuer alle Prozesse aus EINEM Zaehler (Sammlung mail_takt, ein Dokument je Konto-Limit):
    jeder Aufruf schiebt "naechste" atomar um einen Abstand weiter und wartet bis zu seinem Platz."""

    def __init__(self, rate: float, rueckfall: _Takt):
        self.abstand = 1.0 / rate if rate > 0 else 0.0
        self.rueckfall = rueckfall
        self._gewarnt = 0.0

    async def _platz_holen(self) -> float:
        import time
        from pymongo import ReturnDocument
        from deps import db
        jetzt = time.time()
        doc = await asyncio.wait_for(db.mail_takt.find_one_and_update(
            {"_id": "resend"},
            [{"$set": {"naechste": {"$add": [{"$max": [{"$ifNull": ["$naechste", 0]}, jetzt]},
                                             self.abstand]}}}],
            upsert=True, return_document=ReturnDocument.AFTER), timeout=2.0)
        start = float(doc["naechste"]) - self.abstand
        if start - jetzt > RESEND_TAKT_MAX_VORLAUF_S:
            log.warning("email_service: gemeinsamer Mail-Takt lag %.0f s in der Zukunft — neu begonnen",
                        start - jetzt)
            await db.mail_takt.update_one({"_id": "resend"}, {"$set": {"naechste": jetzt + self.abstand}})
            start = jetzt
        return start - time.time()

    async def platz(self) -> None:
        if not self.abstand:
            return
        try:
            schlaf = await self._platz_holen()
        except Exception as exc:  # noqa: BLE001 — Datenbank weg: Anteil je Prozess (sicher unter dem Limit)
            loop = asyncio.get_running_loop()
            if loop.time() - self._gewarnt > 60:
                self._gewarnt = loop.time()
                log.warning("email_service: gemeinsamer Mail-Takt nicht erreichbar (%s) — Anteil je Prozess",
                            type(exc).__name__)
            await self.rueckfall.platz()
            return
        if schlaf > 0:
            await asyncio.sleep(schlaf)


_resend_takt: Optional[object] = None
_resend_takt_loop = None


def _takt():
    """Takt je Event-Loop (wie die Semaphore — Tests haben eigene Loops): der gemeinsame Takt aller Prozesse,
    mit dem Anteil je Prozess als Rueckfall (bzw. allein, wenn RESEND_TAKT_GEMEINSAM aus ist)."""
    global _resend_takt, _resend_takt_loop
    loop = asyncio.get_running_loop()
    if _resend_takt is None or _resend_takt_loop is not loop:
        anteil = _Takt(RESEND_RATE / RESEND_PROZESSE)
        _resend_takt = _GemeinsamerTakt(RESEND_RATE, anteil) if RESEND_TAKT_GEMEINSAM else anteil
        _resend_takt_loop = loop
    return _resend_takt


def _wartezeit(versuch: int, retry_after: Optional[str]) -> float:
    import random
    if retry_after:
        try:
            return max(0.0, min(float(retry_after), 10.0))
        except ValueError:
            pass
    # Nachpruefung 20.09.2026: vorher stand hier ein fester Backoff mit nur
    # 0-0,5 s Streuung obendrauf. Alle Wartenden kamen dadurch fast im selben
    # Moment zurueck und trieben sich gegenseitig erneut in den 429. Jetzt
    # volle Streuung: irgendwann im ganzen Fenster, nicht alle am Ende.
    fenster = min(4.0, 0.5 * (2 ** versuch))
    return random.uniform(fenster / 2, fenster)


def _resend_fehlername(r) -> str:
    try:
        return str((r.json() or {}).get("name") or "")
    except Exception:  # noqa: BLE001 — kein JSON
        return ""


async def _send_resend(*, to: str, subject: str, text: str, html: Optional[str],
                       anhang: Optional[bytes], anhang_name: str,
                       reply_to: Sequence[str], kopie: Sequence[str],
                       absender_name: Optional[str],
                       idempotency_key: Optional[str] = None) -> str:
    """Liefert die Resend-Kennung der Mail, "" bei Ablehnung."""
    import httpx
    daten = {
        "from": _absender(absender_name, kodiert=False),
        "to": [to],
        "subject": subject,
        "text": text,
    }
    if html:
        daten["html"] = html
    if reply_to:
        daten["reply_to"] = list(reply_to)
    if kopie:
        daten["bcc"] = list(kopie)
    if anhang:
        daten["attachments"] = [{
            "filename": anhang_name or "Dokument.pdf",
            "content": base64.b64encode(anhang).decode("ascii"),
        }]
    kopf = {"Authorization": f"Bearer {RESEND_API_KEY}",
            "Content-Type": "application/json"}
    if idempotency_key:
        # Resend haelt den Schluessel 24 h: gleicher Schluessel = keine
        # zweite Zustellung, sondern die Antwort der ersten.
        kopf["Idempotency-Key"] = str(idempotency_key)[:256]
    gewartet = 0.0
    r = None
    for versuch in range(RESEND_VERSUCHE):
        try:
            async with _resend_semaphore():
                # Erst den Takt abwarten, dann senden: der Anteil dieses
                # Prozesses am Konto-Limit (siehe RESEND_RATE/RESEND_PROZESSE).
                await _takt().platz()
                async with httpx.AsyncClient(timeout=30.0) as client:
                    r = await client.post(RESEND_URL, json=daten, headers=kopf)
        except (httpx.TimeoutException, httpx.TransportError) as exc:
            # Ohne Idempotency-Key koennte ein Wiederholen doppelt zustellen
            # (die erste Anfrage kam evtl. an) — dann nicht wiederholen.
            if not idempotency_key or versuch == RESEND_VERSUCHE - 1:
                raise
            warte = _wartezeit(versuch, None)
            if gewartet + warte > RESEND_WARTEN_MAX:
                raise
            log.warning("email_service: Resend nicht erreichbar (%s) — neuer Versuch in %.1f s",
                        exc.__class__.__name__, warte)
            await asyncio.sleep(warte)
            gewartet += warte
            continue
        if r.status_code in (200, 201, 202):
            try:
                return str(r.json().get("id") or "angenommen")
            except ValueError:
                return "angenommen"
        gleichzeitig = r.status_code == _RESEND_KONFLIKT and _resend_fehlername(r) == \
            "concurrent_idempotent_requests"
        if (r.status_code in _RESEND_VORUEBERGEHEND or gleichzeitig) \
                and versuch < RESEND_VERSUCHE - 1:
            warte = _wartezeit(versuch, r.headers.get("retry-after"))
            if gewartet + warte <= RESEND_WARTEN_MAX:
                log.warning("email_service: Resend HTTP %s (Tempo-Limit/voruebergehend) — "
                            "neuer Versuch in %.1f s", r.status_code, warte)
                await asyncio.sleep(warte)
                gewartet += warte
                continue
        break
    if r is not None and r.status_code == _RESEND_KONFLIKT:
        log.error("email_service: Resend meldet Schluessel-Konflikt (HTTP 409, %s) — "
                  "Ausgang unklar, kein SMTP-Rueckfall", _resend_fehlername(r) or "ohne Namen")
        raise ResendUnklar("Resend HTTP 409 (Idempotency-Key schon benutzt)")
    if r is not None and r.status_code in _RESEND_UNKLAR:
        # Befund 106 (16.09.2026): nach 5xx ist unklar, ob die Mail angenommen
        # wurde — kein Rueckfall auf SMTP (doppelte Zustellung).
        log.error("email_service: Resend antwortet zuletzt mit HTTP %s — Ausgang unklar, "
                  "kein SMTP-Rueckfall", r.status_code)
        raise ResendUnklar(f"Resend HTTP {r.status_code} nach Wiederholungen")
    # Resend meldet Fehler klar (unverifizierte Domain, falscher Schlüssel …)
    log.error("email_service: Resend lehnt ab (HTTP %s): %s",
              r.status_code if r is not None else "-", (r.text[:300] if r is not None else ""))
    return ""


# ----------------------------------------------------------------- SMTP
#: Fehler, bei denen der SMTP-Server die Mail nachweislich ABGELEHNT hat —
#: dann ist sicher nichts zugestellt und ein erneuter Versuch ist erlaubt.
#: Alles andere (z. B. abgerissene Verbindung) ist UNKLAR (Nr. 59).
_SICHER_ABGELEHNT = (smtplib.SMTPRecipientsRefused, smtplib.SMTPSenderRefused,
                     smtplib.SMTPHeloError, smtplib.SMTPAuthenticationError,
                     smtplib.SMTPNotSupportedError, ssl.SSLError,
                     ConnectionRefusedError, TimeoutError, OSError)
#: Pruefung 04.10.2026 (Nr. 21): Ablehnungen, die der Server NACH Beginn der
#: Uebergabe ausdruecklich meldet (Absender/Empfaenger/Inhalt abgewiesen).
#: Vorher galt hier dieselbe Liste wie oben — und weil smtplib.SMTPException
#: von OSError erbt, war damit JEDE SMTP-Antwort "sicher abgelehnt", auch
#: eine gescheiterte Verabschiedung (QUIT) NACH erfolgreicher Abgabe: die
#: Mail war raus, der Schluessel wurde freigegeben, der naechste Versuch
#: stellte ein zweites Mal zu.
_ABGELEHNT_BEI_UEBERGABE = (smtplib.SMTPRecipientsRefused, smtplib.SMTPSenderRefused,
                            smtplib.SMTPDataError)


def _sicher_nicht_zugestellt(exc: BaseException, fortschritt: dict) -> bool:
    """Darf nach diesem Fehler gefahrlos erneut gesendet werden?

    Nachpruefung 20.09.2026, Nr. 59: vorher wurde bei JEDER Ausnahme
    waehrend `send_message()` der Idempotenz-Eintrag freigegeben. Hatte der
    SMTP-Server die Mail aber schon angenommen und riss die Verbindung erst
    vor seiner Antwort ab, galt sie lokal als fehlgeschlagen — der naechste
    Versuch stellte sie ein ZWEITES Mal zu. Bei Resend verhindert das der
    Idempotency-Key; SMTP kennt so etwas nicht.

    Freigegeben wird jetzt nur noch, wenn die Uebergabe gar nicht begonnen
    hat (Verbindung/Anmeldung gescheitert) oder der Server ausdruecklich
    abgelehnt hat."""
    if fortschritt.get("fertig"):
        return False                    # abgegeben — was danach scheitert, aendert daran nichts
    if not fortschritt.get("uebergabe_laeuft"):
        return True
    return isinstance(exc, _ABGELEHNT_BEI_UEBERGABE)


def _send_sync(*, to: str, subject: str, text: str, html: Optional[str],
               anhang: Optional[bytes], anhang_name: str,
               reply_to: Sequence[str], kopie: Sequence[str],
               absender_name: Optional[str],
               fortschritt: Optional[dict] = None) -> None:
    msg = EmailMessage()
    msg["From"] = _absender(absender_name)
    msg["To"] = to
    msg["Subject"] = subject
    if reply_to:
        msg["Reply-To"] = ", ".join(reply_to)
    if kopie:
        msg["Bcc"] = ", ".join(kopie)
    msg.set_content(text)
    if html:
        msg.add_alternative(html, subtype="html")
    if anhang:
        msg.add_attachment(anhang, maintype="application", subtype="pdf",
                           filename=anhang_name or "Dokument.pdf")

    # Nr. 59: ab dem Aufruf von send_message() ist der Ausgang bei einem
    # Fehler nicht mehr sicher zu beurteilen — das haelt `fortschritt` fest.
    fortschritt = fortschritt if fortschritt is not None else {}
    ctx = ssl.create_default_context()
    if SMTP_PORT == 465:
        s = smtplib.SMTP_SSL(SMTP_HOST, SMTP_PORT, context=ctx, timeout=20)
    else:
        s = smtplib.SMTP(SMTP_HOST, SMTP_PORT, timeout=20)
    try:
        if SMTP_PORT != 465:
            s.starttls(context=ctx)
        s.login(SMTP_USER, SMTP_PASS)
        fortschritt["uebergabe_laeuft"] = True
        s.send_message(msg)
        fortschritt["fertig"] = True
    finally:
        _smtp_beenden(s)


def _smtp_beenden(s) -> None:
    """Pruefung 04.10.2026 (Nr. 21): Verabschiedung (QUIT) ohne Folgen — vorher
    lief sie im `with` und warf nach einer schon abgegebenen Mail noch eine
    SMTPResponseException, die als "sicher abgelehnt" galt."""
    try:
        s.quit()
    except Exception:  # noqa: BLE001
        pass
    try:
        s.close()
    except Exception:  # noqa: BLE001
        pass


# Pruefbericht 20.09.2026 (P-18): Groesse eines Anhangs, ab der NICHT gesendet
# wird (Resend nimmt 40 MB je Mail, viele Postfaecher deutlich weniger).
# Vorher ging ein zu grosses PDF ungeprueft an den Anbieter und kam als
# nichtssagender "Versand fehlgeschlagen" zurueck; jetzt meldet der Aufrufer
# den Link-Weg (WhatsApp/Download).
MAIL_ANHANG_MAX_BYTES = zahl_env("MAIL_ANHANG_MAX_BYTES", 20 * 1024 * 1024, unten=1024)


async def send_email(to: str, subject: str, text: str,
                     anhang: bytes = None, anhang_name: str = "",
                     *, html: str = None, reply_to=None, kopie=None,
                     absender_name: str = None,
                     idempotency_key: str = None) -> bool:
    """Verschickt eine Mail. True bei Erfolg, False bei Fehler (geloggt).
    Fuer den Beleg des Anbieters siehe send_email_mit_beleg.

    to            Empfänger
    text/html     Textfassung (Pflicht) und optionale HTML-Fassung
    anhang        PDF-Bytes, `anhang_name` der Dateiname
    reply_to      Antwortadresse(n) — z. B. der Sucher, damit Antworten des
                  Verkäufers direkt bei ihm landen
    kopie         stille Kopie (Bcc), z. B. an den Sucher selbst
    absender_name Anzeigename vor unserer Adresse (z. B. die Firma)
    """
    ok, _beleg = await send_email_mit_beleg(
        to, subject, text, anhang, anhang_name, html=html, reply_to=reply_to,
        kopie=kopie, absender_name=absender_name, idempotency_key=idempotency_key)
    return ok


async def send_email_mit_beleg(to: str, subject: str, text: str,
                               anhang: bytes = None, anhang_name: str = "",
                               *, html: str = None, reply_to=None, kopie=None,
                               absender_name: str = None,
                               idempotency_key: str = None):
    """Wie send_email, liefert aber (ok, beleg).

    beleg ist die Kennung, unter der der Anbieter die Mail fuehrt
    ("resend:<id>" oder "smtp"). Sie wird am Vertrag gespeichert, damit
    spaeter nachweisbar ist, DASS und WO die Mail abgegeben wurde.

    idempotency_key geht als Idempotency-Key an Resend: schickt der
    Server dieselbe Mail unter demselben Schluessel noch einmal — etwa
    weil er zwischen Abgabe und Speichern abgestuerzt war — liefert
    Resend die erste Abgabe zurueck, statt ein zweites Mal zuzustellen
    (Pruefbericht Runde 8, Befund 3: "nicht einfach erneut senden").

    Bei (False, "anhang_zu_gross") ueberschreitet der Anhang
    MAIL_ANHANG_MAX_BYTES — es wurde nichts an den Anbieter uebergeben (P-18).
    """
    if not email_configured():
        log.warning("email_service: kein Versandweg eingerichtet (RESEND_API_KEY "
                    "oder SMTP_*) — '%s' an %s NICHT gesendet", subject, to)
        return False, ""
    if anhang and len(anhang) > MAIL_ANHANG_MAX_BYTES:
        log.error("email_service: Anhang '%s' an %s ist %.1f MB gross (Grenze "
                  "%.1f MB, MAIL_ANHANG_MAX_BYTES) — NICHT gesendet",
                  anhang_name, to, len(anhang) / 1048576, MAIL_ANHANG_MAX_BYTES / 1048576)
        return False, "anhang_zu_gross"
    reply_to = [a for a in _liste(reply_to) if gueltige_adresse(a)]
    kopie = [a for a in _liste(kopie) if gueltige_adresse(a) and a.lower() != (to or "").lower()]
    argumente = dict(to=to, subject=subject, text=text, html=html, anhang=anhang,
                     anhang_name=anhang_name, reply_to=reply_to, kopie=kopie,
                     absender_name=absender_name)
    try:
        if resend_aktiv():
            try:
                beleg = await _send_resend(**argumente, idempotency_key=idempotency_key)
            except ResendUnklar as exc:
                # Befund 106: Ausgang bei Resend unklar — NICHT ueber SMTP
                # wiederholen. Pruefung 04.10.2026 (Nr. 22): als "unklar"
                # melden, damit der Versand unter DEMSELBEN Schluessel
                # wiederholt wird (Resend stellt dann nicht doppelt zu).
                log.error("email_service: '%s' an %s — %s; nicht ueber SMTP wiederholt",
                          subject, to, exc)
                return False, BELEG_UNKLAR
            except Exception as exc:  # noqa: BLE001
                if not _sicher_nicht_angekommen(exc):
                    log.error("email_service: Resend-Abgabe an %s ist UNKLAR (%s) — kein "
                              "SMTP-Rueckfall", to, exc.__class__.__name__)
                    return False, BELEG_UNKLAR
                raise
            if beleg:
                log.info("email_service: '%s' an %s über Resend gesendet (%s)",
                         subject, to, beleg)
                return True, f"resend:{beleg}"
            if not smtp_aktiv():
                return False, ""
            log.warning("email_service: Resend fehlgeschlagen — versuche SMTP")
        # Pruefung 14.09.2026 (Liste 4, Nr. 79 / Liste 5, Nr. 10): SMTP kennt
        # keinen Idempotency-Key. Deshalb merkt sich die Datenbank je Schluessel,
        # ob eine Abgabe laeuft oder geschah — eine Wiederaufnahme nach einem
        # Absturz schickt dieselbe Mail nicht ein zweites Mal, sondern meldet
        # "unklar" (der Nutzer sieht das im Versandstatus).
        if idempotency_key:
            stand = await _smtp_idempotenz_beanspruchen(idempotency_key, to)
            if stand == "gesendet":
                log.info("email_service: '%s' an %s war ueber SMTP bereits abgegeben", subject, to)
                return True, "smtp:bereits"
            if stand == "db_fehler":
                return False, ""
            if stand == "unklar":
                log.error("email_service: SMTP-Abgabe an %s unter %s ist unklar (frueherer "
                          "Versuch ohne Ergebnis) — NICHT erneut gesendet", to, idempotency_key)
                return False, BELEG_UNKLAR
        fortschritt: dict = {}
        try:
            await asyncio.to_thread(
                lambda: _send_sync(**argumente, fortschritt=fortschritt))
        except Exception as exc:
            # Nr. 59: nur freigeben, wenn sicher NICHTS zugestellt wurde.
            # Sonst bleibt der Eintrag stehen und gilt beim naechsten Mal
            # als "unklar" — dann wird NICHT automatisch erneut gesendet,
            # und der Nutzer sieht den Zustand im Versandstatus.
            if _sicher_nicht_zugestellt(exc, fortschritt):
                if idempotency_key:
                    await _smtp_idempotenz_freigeben(idempotency_key)
                raise
            log.error("email_service: SMTP-Abgabe an %s ist UNKLAR (%s nach "
                      "Uebergabe) — der Eintrag bleibt stehen, es wird nicht "
                      "automatisch erneut gesendet", to, exc.__class__.__name__)
            return False, BELEG_UNKLAR
        if idempotency_key:
            await _smtp_idempotenz_abschliessen(idempotency_key)
        log.info("email_service: '%s' an %s über SMTP gesendet", subject, to)
        return True, "smtp"
    except Exception as exc:  # noqa: BLE001
        log.error("email_service: Versand an %s fehlgeschlagen: %s", to, exc)
        return False, ""


def _sicher_nicht_angekommen(exc: BaseException) -> bool:
    """Pruefung 04.10.2026 (Nr. 22): Resend war gar nicht erreichbar (Verbindung
    nicht aufgebaut) — dann kam sicher nichts an. Brach die Verbindung erst
    NACH dem Absenden ab (Lese-Zeitlimit, abgerissene Antwort), ist der
    Ausgang offen."""
    try:
        import httpx
    except Exception:  # noqa: BLE001
        return True
    if isinstance(exc, (httpx.ConnectError, httpx.ConnectTimeout, httpx.PoolTimeout)):
        return True
    return not isinstance(exc, (httpx.TimeoutException, httpx.TransportError))


# SMTP hat – anders als Resend – keinen providerseitigen Idempotency-Key.
# Ist der Ausgang einer Uebergabe unklar, darf derselbe Schluessel deshalb
# NIEMALS automatisch erneut senden: die erste Mail koennte angekommen sein.
# Ein bewusster erneuter Versand laeuft auf Vertragsebene ueber die vorhandene
# Nutzerbestaetigung und einen neuen Schluessel.
#
# Die Konstante bleibt nur fuer Rueckwaertskompatibilitaet/Diagnose bestehen;
# sie steuert KEINE automatische Wiederaufnahme mehr.
SMTP_UNKLAR_SPERRE_SEKUNDEN = 24 * 3600


async def _smtp_idempotenz_beanspruchen(key: str, to: str) -> str:
    """'neu' = jetzt senden; 'gesendet' = schon abgegeben; 'unklar' = ein
    frueherer Versuch hat keinen sicher feststellbaren Ausgang.

    Ein vorhandenes 'laeuft' wird absichtlich nie automatisch uebernommen,
    egal wie alt es ist. SMTP kann eine bereits angenommene Mail nach einem
    Antwort-/Verbindungsabbruch nicht deduplizieren.
    """
    from datetime import datetime, timezone
    try:
        from deps import db
        jetzt = datetime.now(timezone.utc)
        r = await db.mail_idempotenz.update_one(
            {"key": key},
            {"$setOnInsert": {"key": key, "empfaenger": to, "status": "laeuft",
                              "begonnen": jetzt}},
            upsert=True)
        if r.upserted_id is not None:
            return "neu"
        alt = await db.mail_idempotenz.find_one({"key": key}, {"_id": 0}) or {}
        if alt.get("status") == "gesendet":
            return "gesendet"
        # Vorhandenes "laeuft" bedeutet: Ausgang unbekannt. Nie automatisch
        # neu senden — auch nicht nach Stunden/Tagen. Doppelte Kaufvertraege
        # sind schlimmer als ein Zustand, den der Nutzer bewusst klaeren muss.
        return "unklar"
    except Exception as exc:  # noqa: BLE001
        # Phase 3 (3.6, A22): fail-closed — ohne Datenbank ist nicht pruefbar, ob
        # dieselbe Mail schon abging; lieber nicht senden als doppelt.
        log.error("email_service: SMTP-Idempotenz nicht pruefbar (%s) — NICHT gesendet", exc)
        return "db_fehler"


async def _smtp_idempotenz_abschliessen(key: str) -> None:
    try:
        from deps import db
        from datetime import datetime, timezone
        await db.mail_idempotenz.update_one(
            {"key": key}, {"$set": {"status": "gesendet", "gesendet": datetime.now(timezone.utc)}})
    except Exception as exc:  # noqa: BLE001
        log.warning("email_service: SMTP-Idempotenz nicht gespeichert (%s)", exc)


async def _smtp_idempotenz_freigeben(key: str) -> None:
    """SMTP hat abgelehnt (keine Zustellung) — der Schluessel darf erneut."""
    try:
        from deps import db
        await db.mail_idempotenz.delete_one({"key": key, "status": "laeuft"})
    except Exception as exc:  # noqa: BLE001
        log.warning("email_service: SMTP-Idempotenz nicht freigegeben (%s)", exc)
