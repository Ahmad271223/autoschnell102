# -*- coding: utf-8 -*-
"""Zwei-Faktor-Anmeldung (TOTP nach RFC 6238) fuer Admin-/Super-Admin-Konten
(Abo-Audit 09/2026: "MFA fuer Super-Admins").

- Geheimnis: 20 Zufallsbytes, Base32 — kompatibel mit Google/Microsoft
  Authenticator, Aegis, 1Password (otpauth://-URI, SHA1, 6 Stellen, 30 s).
- Ablage: mit einem aus JWT_SECRET abgeleiteten Schluessel verschluesselt
  (Fernet); die Datenbank allein reicht nicht, um Codes zu erzeugen.
- Replay-Schutz: derselbe 30-s-Zaehler wird nur einmal akzeptiert.
- Wiederherstellungscodes: 8 Stueck, nur als SHA-256 gespeichert, einmalig.
Nur Standardbibliothek + cryptography (bereits Abhaengigkeit).
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import os
import secrets
import struct
import time
import unicodedata
from typing import List, Optional, Tuple
from urllib.parse import quote

from cryptography.fernet import Fernet, InvalidToken

SCHRITT = 30
STELLEN = 6
AUSSTELLER = os.environ.get("MFA_AUSSTELLER", "AutoSchnell")


def _fernet_fuer(geheim: str) -> Fernet:
    return Fernet(base64.urlsafe_b64encode(hashlib.sha256(geheim.encode("utf-8")).digest()))


def _schluessel() -> list:
    """Runde 15 (15.09.2026): eigener Schluessel DATEN_SCHLUESSEL fuer die
    Ablage der Zwei-Faktor-Geheimnisse — eine JWT_SECRET-Rotation (Sitzungen
    sollen sterben) macht die MFA-Daten nicht mehr unlesbar. Ohne
    DATEN_SCHLUESSEL wie bisher JWT_SECRET; beim Lesen werden beide probiert,
    damit vorhandene Geheimnisse nach dem Setzen des Zweitschluessels weiter
    gelten (neu verschluesselt wird nur mit dem ersten)."""
    daten = (os.environ.get("DATEN_SCHLUESSEL") or "").strip()
    jwt = os.environ.get("JWT_SECRET") or "dev-secret"
    return [daten, jwt] if daten and daten != jwt else [jwt]


def _fernet() -> Fernet:
    return _fernet_fuer(_schluessel()[0])


def verschluesseln(klartext: str) -> str:
    return _fernet().encrypt(klartext.encode("utf-8")).decode("ascii")


def entschluesseln(chiffrat: str) -> Optional[str]:
    for geheim in _schluessel():
        try:
            return _fernet_fuer(geheim).decrypt(chiffrat.encode("ascii")).decode("utf-8")
        except (InvalidToken, ValueError, TypeError):
            continue
    return None


def secret_erzeugen() -> str:
    return base64.b32encode(secrets.token_bytes(20)).decode("ascii").rstrip("=")


def _schluessel_bytes(secret: str) -> bytes:
    s = secret.strip().replace(" ", "").upper()
    return base64.b32decode(s + "=" * (-len(s) % 8), casefold=True)


def totp(secret: str, zaehler: Optional[int] = None) -> str:
    """Code fuer einen 30-s-Zaehler (Default: jetzt)."""
    if zaehler is None:
        zaehler = int(time.time() // SCHRITT)
    h = hmac.new(_schluessel_bytes(secret), struct.pack(">Q", zaehler), hashlib.sha1).digest()
    o = h[-1] & 0x0F
    code = (struct.unpack(">I", h[o:o + 4])[0] & 0x7FFFFFFF) % (10 ** STELLEN)
    return str(code).zfill(STELLEN)


def code_normalisieren(code: str) -> str:
    """Leerraum entfernen und Ziffern anderer Schriften in ASCII-Ziffern
    umsetzen (arabisch-indisch, vollbreit, Devanagari ... — alles mit
    Unicode-Kategorie "Nd").

    Pruefung 21.09.2026 (MFA): '١٢٣٤٥٦' oder '１２３４５６' bestanden
    isdigit(), hmac.compare_digest warf dann bei Nicht-ASCII einen TypeError
    -> 500 statt "Code ungueltig". Solche Tastaturen (iOS "Arabisch-Indisch",
    arabische Android-/Windows-Tastatur) funktionieren jetzt; alle anderen
    Zeichen (z. B. hochgestellte Ziffern) bleiben stehen und fallen bei
    code_format_ok() durch."""
    aus = []
    for c in (code or ""):
        if c.isspace():
            continue
        if not c.isascii():
            d = unicodedata.decimal(c, None)
            if d is not None:
                c = str(d)
        aus.append(c)
    return "".join(aus)


def code_format_ok(code: str) -> bool:
    """Genau STELLEN ASCII-Ziffern (nach code_normalisieren)."""
    return len(code) == STELLEN and code.isascii() and code.isdigit()


def code_pruefen(secret: str, code: str, letzter_zaehler: int = -1,
                 toleranz: int = 1) -> Optional[int]:
    """Gibt den passenden Zaehler zurueck (oder None). Zaehler <= letzter
    gelten als bereits benutzt (Replay-Schutz). Wirft nie wegen der Eingabe:
    nur ASCII-Ziffern erreichen hmac.compare_digest (Pruefung 21.09.2026)."""
    code = code_normalisieren(code)
    if not code_format_ok(code):
        return None
    jetzt = int(time.time() // SCHRITT)
    for delta in range(-toleranz, toleranz + 1):
        z = jetzt + delta
        if z <= letzter_zaehler:
            continue
        if hmac.compare_digest(totp(secret, z), code):
            return z
    return None


def provisioning_uri(secret: str, konto: str) -> str:
    return (f"otpauth://totp/{quote(AUSSTELLER)}:{quote(konto)}?secret={secret}"
            f"&issuer={quote(AUSSTELLER)}&algorithm=SHA1&digits={STELLEN}&period={SCHRITT}")


def wiederherstellungscodes(anzahl: int = 8) -> Tuple[List[str], List[str]]:
    """(Klartext-Codes fuer die einmalige Anzeige, SHA-256-Hashes fuer die DB)"""
    codes = [f"{secrets.token_hex(4)}-{secrets.token_hex(4)}" for _ in range(anzahl)]
    return codes, [code_hash(c) for c in codes]


def code_hash(code: str) -> str:
    return hashlib.sha256((code or "").strip().lower().encode("utf-8")).hexdigest()
