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
SMTP_PORT = int(os.environ.get("SMTP_PORT", "587") or 587)
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

    `anzeigename` darf die Firma sein; der Zusatz „über <Marke>" macht für
    den Empfänger sichtbar, worüber die Nachricht verschickt wurde, und
    verhindert den Eindruck einer gefälschten Absenderadresse.

    `kodiert=True` liefert die SMTP-Form (Umlaute nach RFC 2047 kodiert),
    `kodiert=False` die reine UTF-8-Form für die Resend-Schnittstelle."""
    adresse = absender_adresse()
    name = (anzeigename or "").strip()
    if name and name.lower() != MAIL_ABSENDER_NAME.lower():
        name = f"{name} über {MAIL_ABSENDER_NAME}"
    name = name or MAIL_ABSENDER_NAME
    if kodiert:
        return formataddr((name, adresse))
    # JSON ist UTF-8 — Resend kodiert den Anzeigenamen selbst korrekt.
    name = name.replace('"', "'")
    return f'{name} <{adresse}>'


def _liste(wert) -> List[str]:
    if not wert:
        return []
    if isinstance(wert, str):
        wert = [wert]
    return [w.strip() for w in wert if w and w.strip()]


# --------------------------------------------------------------- Resend
async def _send_resend(*, to: str, subject: str, text: str, html: Optional[str],
                       anhang: Optional[bytes], anhang_name: str,
                       reply_to: Sequence[str], kopie: Sequence[str],
                       absender_name: Optional[str]) -> bool:
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
    async with httpx.AsyncClient(timeout=30.0) as client:
        r = await client.post(
            RESEND_URL, json=daten,
            headers={"Authorization": f"Bearer {RESEND_API_KEY}",
                     "Content-Type": "application/json"})
    if r.status_code in (200, 201, 202):
        return True
    # Resend meldet Fehler klar (unverifizierte Domain, falscher Schlüssel …)
    log.error("email_service: Resend lehnt ab (HTTP %s): %s",
              r.status_code, r.text[:300])
    return False


# ----------------------------------------------------------------- SMTP
def _send_sync(*, to: str, subject: str, text: str, html: Optional[str],
               anhang: Optional[bytes], anhang_name: str,
               reply_to: Sequence[str], kopie: Sequence[str],
               absender_name: Optional[str]) -> None:
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

    ctx = ssl.create_default_context()
    if SMTP_PORT == 465:
        with smtplib.SMTP_SSL(SMTP_HOST, SMTP_PORT, context=ctx, timeout=20) as s:
            s.login(SMTP_USER, SMTP_PASS)
            s.send_message(msg)
    else:
        with smtplib.SMTP(SMTP_HOST, SMTP_PORT, timeout=20) as s:
            s.starttls(context=ctx)
            s.login(SMTP_USER, SMTP_PASS)
            s.send_message(msg)


async def send_email(to: str, subject: str, text: str,
                     anhang: bytes = None, anhang_name: str = "",
                     *, html: str = None, reply_to=None, kopie=None,
                     absender_name: str = None) -> bool:
    """Verschickt eine Mail. True bei Erfolg, False bei Fehler (geloggt).

    to            Empfänger
    text/html     Textfassung (Pflicht) und optionale HTML-Fassung
    anhang        PDF-Bytes, `anhang_name` der Dateiname
    reply_to      Antwortadresse(n) — z. B. der Sucher, damit Antworten des
                  Verkäufers direkt bei ihm landen
    kopie         stille Kopie (Bcc), z. B. an den Sucher selbst
    absender_name Anzeigename vor unserer Adresse (z. B. die Firma)
    """
    if not email_configured():
        log.warning("email_service: kein Versandweg eingerichtet (RESEND_API_KEY "
                    "oder SMTP_*) — '%s' an %s NICHT gesendet", subject, to)
        return False
    reply_to = [a for a in _liste(reply_to) if gueltige_adresse(a)]
    kopie = [a for a in _liste(kopie) if gueltige_adresse(a) and a.lower() != (to or "").lower()]
    argumente = dict(to=to, subject=subject, text=text, html=html, anhang=anhang,
                     anhang_name=anhang_name, reply_to=reply_to, kopie=kopie,
                     absender_name=absender_name)
    try:
        if resend_aktiv():
            ok = await _send_resend(**argumente)
            if ok:
                log.info("email_service: '%s' an %s über Resend gesendet", subject, to)
                return True
            if not smtp_aktiv():
                return False
            log.warning("email_service: Resend fehlgeschlagen — versuche SMTP")
        await asyncio.to_thread(lambda: _send_sync(**argumente))
        log.info("email_service: '%s' an %s über SMTP gesendet", subject, to)
        return True
    except Exception as exc:  # noqa: BLE001
        log.error("email_service: Versand an %s fehlgeschlagen: %s", to, exc)
        return False
