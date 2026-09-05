# -*- coding: utf-8 -*-
"""Signierte, kurzlebige Datei-Links (Audit 09/2026, Punkt 45).

Bisher waren alle nicht als privat markierten Dateien (z.B. Fahrzeugfotos
unter resale/) reine Bearer-Links: wer die UUID-URL kannte, konnte sie
dauerhaft und ohne Konto abrufen (Cache-Control: public). Jetzt:
- logo/            oeffentlich (Firmenlogo, absichtlich)
- protocol/ pickup/ nur ueber authentifizierte Endpunkte (404 hier)
- alles andere     nur mit gueltiger Signatur (?exp=&sig=), HMAC ueber
                   Schluessel + Ablauf mit JWT_SECRET, Standard 1 Stunde,
                   Cache-Control: private
"""
from __future__ import annotations

import hashlib
import hmac
import os
import time
from urllib.parse import quote

OEFFENTLICHE_PREFIXE = ("logo/",)
PRIVATE_PREFIXE = ("protocol/", "pickup/")
STANDARD_TTL = int(os.environ.get("DATEI_LINK_TTL_SEKUNDEN", "3600") or 3600)


def _geheimnis() -> bytes:
    return (os.environ.get("JWT_SECRET") or "dev-secret").encode("utf-8")


def _mac(key: str, exp: int) -> str:
    return hmac.new(_geheimnis(), f"{key}|{exp}".encode("utf-8"),
                    hashlib.sha256).hexdigest()[:40]


def signatur_noetig(key: str) -> bool:
    return not key.startswith(OEFFENTLICHE_PREFIXE) and not key.startswith(PRIVATE_PREFIXE)


def signierte_datei_url(key: str, ttl: int | None = None) -> str:
    """'/api/files/<key>?exp=..&sig=..' — fuer oeffentliche Prefixe ohne
    Signatur (stabil cachebar)."""
    if not key:
        return ""
    if key.startswith("http://") or key.startswith("https://"):
        return key
    pfad = "/api/files/" + quote(key, safe="/")
    if not signatur_noetig(key):
        return pfad
    exp = int(time.time()) + int(ttl or STANDARD_TTL)
    return f"{pfad}?exp={exp}&sig={_mac(key, exp)}"


def signatur_gueltig(key: str, exp, sig) -> bool:
    try:
        exp_i = int(exp)
    except (TypeError, ValueError):
        return False
    if exp_i < int(time.time()):
        return False
    if not sig or not isinstance(sig, str):
        return False
    return hmac.compare_digest(_mac(key, exp_i), sig)
