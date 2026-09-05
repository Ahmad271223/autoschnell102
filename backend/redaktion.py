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
    """Maskiert die formatierte Log-Nachricht (inkl. Argumente)."""

    def filter(self, record: logging.LogRecord) -> bool:
        try:
            msg = record.getMessage()
            red = redigieren(msg)
            if red != msg:
                record.msg = red
                record.args = ()
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
