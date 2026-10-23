# -*- coding: utf-8 -*-
"""Redaktion sensibler Inhalte in Logs und Fehlerberichten (Audit 09/2026,
Punkt 44): E-Mail-Adressen, Bearer-/JWT-Token, API-Schluessel, Passwoerter
in Query-/Body-Fragmenten und lange Hex-Token werden maskiert, BEVOR sie in
stdout, error_logs oder client_errors landen."""
from __future__ import annotations

import logging
import re

_MUSTER = [
    (re.compile(r"Bearer\s+[A-Za-z0-9\-._~+/]+=*", re.I), "Bearer [redigiert]"),
    (re.compile(r"eyJ[A-Za-z0-9_\-]{10,}\.[A-Za-z0-9_\-]{10,}\.[A-Za-z0-9_\-]{5,}"), "[jwt-redigiert]"),
    (re.compile(r"\b(sk|pk|rk|whsec)_(live|test)_[A-Za-z0-9]{8,}\b"), r"\1_\2_[redigiert]"),
    (re.compile(r"(?i)(password|passwort|pass|secret|token|api[_-]?key|authorization)"
                r"(\s*[=:]\s*)([^\s&,;\"']{3,})"), r"\1\2[redigiert]"),
    (re.compile(r"\b[A-Fa-f0-9]{32,}\b"), "[hex-redigiert]"),
    # Pruefbericht 20.09.2026 (P-12): IBAN (deutsch, mit oder ohne Leerzeichen)
    # VOR der E-Mail-Regel, damit die Ziffernfolge nicht halb uebrig bleibt.
    (re.compile(r"\bDE\d{2}[ ]?(?:\d{4}[ ]?){4}\d{2}\b"), "[iban-redigiert]"),
    # P-12: Telefonnummern (+49 ..., 0511/..., 0176 ...). Bewusst eng: vorne
    # kein Wort-/Zahlzeichen und kein Bindestrich (sonst traefe es Datums-
    # und Zeitstempel wie 2026-09-22 03:00), mindestens 8 Ziffern insgesamt.
    (re.compile(r"(?<![\w.+\-/])(?:\+49[ ]?|0)[1-9](?:[ /\-]?\d){6,13}(?![\w\-])"),
     "[tel-redigiert]"),
    (re.compile(r"\b([A-Za-z0-9._%+\-])[A-Za-z0-9._%+\-]*@([A-Za-z0-9.\-]+\.[A-Za-z]{2,})\b"),
     r"\1***@\2"),
]


def redigieren(text) -> str:
    """Sensible Muster maskieren; niemals selbst scheitern."""
    if text is None:
        return ""
    try:
        s = str(text)
        for muster, ersatz in _MUSTER:
            s = muster.sub(ersatz, s)
        return s
    except Exception:
        return "[nicht redigierbar]"


class RedaktionsFilter(logging.Filter):
    """Maskiert die formatierte Log-Nachricht (inkl. Argumente) und — P-12 —
    auch die Rueckverfolgung (exc_info): die gab der Formatter bisher
    ungeschwaerzt aus, obwohl Ausnahmetexte E-Mail, Token oder Telefon
    tragen koennen (z. B. ValueError mit dem Eingabewert)."""

    def filter(self, record: logging.LogRecord) -> bool:
        try:
            msg = record.getMessage()
            red = redigieren(msg)
            if red != msg:
                record.msg = red
                record.args = ()
        except Exception:
            pass
        try:
            if record.exc_info and not record.exc_text:
                # Selbst formatieren, redigieren, als exc_text hinterlegen —
                # der Formatter haengt exc_text an und formatiert exc_info
                # nicht noch einmal (logging.Formatter.format).
                import traceback
                text = "".join(traceback.format_exception(*record.exc_info)).rstrip("\n")
                record.exc_text = redigieren(text)
                record.exc_info = None
        except Exception:
            pass
        return True


def logging_redaktion_aktivieren() -> None:
    root = logging.getLogger()
    if not any(isinstance(f, RedaktionsFilter) for f in root.filters):
        root.addFilter(RedaktionsFilter())
    for h in root.handlers:
        if not any(isinstance(f, RedaktionsFilter) for f in h.filters):
            h.addFilter(RedaktionsFilter())
