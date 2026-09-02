"""Zentraler E-Mail-Versand über SMTP.

Konfiguration über .env:
    SMTP_HOST=smtp.example.com
    SMTP_PORT=587                # 587 = STARTTLS (Default), 465 = SSL
    SMTP_USER=postfach@example.com
    SMTP_PASS=geheim
    SMTP_FROM=AutoSchnell <postfach@example.com>   # optional, Default: SMTP_USER

Ohne Konfiguration ist `email_configured()` False — Aufrufer sollen dann eine
verständliche Meldung liefern statt still zu scheitern. Funktioniert mit jedem
Standard-SMTP-Anbieter (z.B. IONOS, Strato, Gmail-App-Passwort, Brevo, Resend).
"""
from __future__ import annotations

import asyncio
import logging
import os
import smtplib
import ssl
from email.message import EmailMessage
from email.utils import formataddr

log = logging.getLogger("autohandel")

SMTP_HOST = os.environ.get("SMTP_HOST", "").strip()
SMTP_PORT = int(os.environ.get("SMTP_PORT", "587") or 587)
SMTP_USER = os.environ.get("SMTP_USER", "").strip()
SMTP_PASS = os.environ.get("SMTP_PASS", "").strip()
SMTP_FROM = os.environ.get("SMTP_FROM", "").strip() or SMTP_USER


def email_configured() -> bool:
    return bool(SMTP_HOST and SMTP_USER and SMTP_PASS)


def _send_sync(to: str, subject: str, text: str,
               anhang: bytes = None, anhang_name: str = "") -> None:
    msg = EmailMessage()
    msg["From"] = SMTP_FROM if "<" in SMTP_FROM else formataddr(("AutoSchnell", SMTP_FROM))
    msg["To"] = to
    msg["Subject"] = subject
    msg.set_content(text)
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
                     anhang: bytes = None, anhang_name: str = "") -> bool:
    """Versand im Thread (SMTP blockiert). True bei Erfolg, False bei Fehler —
    Fehler werden geloggt, aber nicht geworfen (Aufrufer entscheidet über UX)."""
    if not email_configured():
        log.warning("email_service: SMTP nicht konfiguriert — '%s' an %s NICHT gesendet",
                    subject, to)
        return False
    try:
        await asyncio.to_thread(_send_sync, to, subject, text, anhang, anhang_name)
        log.info("email_service: '%s' an %s gesendet", subject, to)
        return True
    except Exception as exc:
        log.error("email_service: Versand an %s fehlgeschlagen: %s", to, exc)
        return False
