# -*- coding: utf-8 -*-
"""Signierte, kurzlebige Datei-Links (Audit 09/2026, Punkt 45).

Bisher waren alle nicht als privat markierten Dateien (z.B. Fahrzeugfotos
unter resale/) reine Bearer-Links: wer die UUID-URL kannte, konnte sie
dauerhaft und ohne Konto abrufen (Cache-Control: public). Jetzt:
- logo/            oeffentlich (Firmenlogo, absichtlich)
- protocol/ pickup/ nur ueber authentifizierte Endpunkte (404 hier)
- alles andere     nur mit gueltiger Signatur (?exp=&sig=), HMAC ueber
                   Schluessel + Ablauf mit JWT_SECRET, Standard 1 Stunde
                   (Inseratsfotos resale/: 24 Stunden, RP-098 Nr. 8),
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
from konfig import zahl_env  # Pruefung 14.09.2026: keine Abstuerze durch .env-Tippfehler
STANDARD_TTL = zahl_env("DATEI_LINK_TTL_SEKUNDEN", 3600, unten=60)
#: Rollenprüfung 22.09.2026 (RP-098 Nr. 8): Inseratsfotos (resale/) sind
#: ohnehin oeffentlich auf dem Marktplatz sichtbar. Mit einer Stunde zeigte
#: der Inserats-Editor nach einer Pause nur noch Fehlbilder (die
#: Marktplatz-Ansichten setzen schon selbst 3 Tage, marketplace.MARKT_FOTO_TTL).
#: Standard fuer resale/ daher 24 Stunden; ein ausdruecklicher ttl gewinnt.
INSERAT_FOTO_TTL = zahl_env("DATEI_LINK_TTL_INSERAT_SEKUNDEN", 24 * 3600, unten=60)
_PREFIX_TTL = (("resale/", INSERAT_FOTO_TTL),)


def standard_ttl(key: str) -> int:
    """Lebensdauer eines signierten Links ohne ausdruecklichen ttl."""
    for prefix, ttl in _PREFIX_TTL:
        if key.startswith(prefix):
            return int(ttl)
    return int(STANDARD_TTL)


def _geheimnis() -> bytes:
    # Nachpruefung Runde 14 (Nr. 25): dasselbe Geheimnis wie die Anmeldung
    # (auth.JWT_SECRET) — auch in Dev/Test ohne gesetztes JWT_SECRET, wo
    # auth.py ein Zufalls-Secret erzeugt und os.environ leer bleibt. Der
    # Import liegt in der Funktion, damit ein zur Laufzeit geaendertes
    # auth.JWT_SECRET (Tests) sofort gilt; auth importiert dateien nicht.
    import auth
    return str(auth.JWT_SECRET).encode("utf-8")


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
    exp = int(time.time()) + int(ttl or standard_ttl(key))
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
