# -*- coding: utf-8 -*-
"""Kontonummer (13.09.2026): Anmeldung mit Kontonummer statt E-Mail.

Reines Modul — KEIN Import aus deps/server/routes, damit Limiter, Tests und
Skripte es ohne Datenbank nutzen koennen.

Format:
- Chef einer Firma:   "10023"    (= dealers.kunden_nr)
- Sucher einer Firma: "10023-2"  (Zusatz aus dealers.sucher_seq)
- Fahrer:             "10031"    (eigene Nummer aus derselben Reihe)
- Zwischenhaendler:   "6FE7K2M"  (Kaeufer-Code, seit 14.09.2026 — Wunsch
                                  Ahmad: B2B-Konten sollen sich auf den ersten
                                  Blick von Firmen- und Fahrernummern
                                  unterscheiden; Buchstaben+Ziffern, bis 9
                                  Stellen; alte numerische Kaeufernummern
                                  bleiben gueltig)
"""
import re
import secrets
import unicodedata
from typing import Optional

# Basis 4-9 Stellen ohne fuehrende Null, optional ein Zusatz 1-4 Stellen.
MUSTER = re.compile(r"^[1-9]\d{3,8}(-[1-9]\d{0,3})?$")

# Gedankenstriche/Minuszeichen, die Handy-Tastaturen und Kopieren aus
# PDFs/Mails gern statt "-" liefern.
_STRICHE = ("–", "—", "‐", "−", "‑")

# Genau EIN Trenner zwischen zwei Zifferngruppen: Leerzeichen / . _ -
_ROH = re.compile(r"^(\d{1,12})(?:[ /._-](\d{1,12}))?$")


def normalisieren(roh) -> Optional[str]:
    """Eingabe -> kanonische Kontonummer oder None.

    '10023 2', '10023/2', ' 10023-02 ' -> '10023-2'; 'ci-superadmin' -> None.
    Beide Gruppen ohne fuehrende Nullen (int), danach Pruefung gegen MUSTER."""
    if not isinstance(roh, str):
        return None
    s = unicodedata.normalize("NFKC", roh).strip()
    if not s or len(s) > 40:
        return None
    for strich in _STRICHE:
        s = s.replace(strich, "-")
    m = _ROH.match(s)
    if not m:
        return None
    basis = str(int(m.group(1)))
    kandidat = basis if m.group(2) is None else f"{basis}-{int(m.group(2))}"
    return kandidat if MUSTER.match(kandidat) else None


# Kaeufer-Code (14.09.2026): Alphabet ohne I, O, 0, 1 (Verwechslung auf dem
# Handy), mindestens ein Buchstabe (sonst waere es eine Nummer der Reihe),
# erzeugt werden 7 Zeichen, angenommen 6-9 (Spielraum fuer spaetere Formate).
KAEUFER_ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"
KAEUFER_LAENGE = 7
KAEUFER_MUSTER = re.compile(r"^(?=.*[A-Z])[A-HJ-NP-Z2-9]{6,9}$")
_KAEUFER_TRENNER = re.compile(r"[ \-._/]")


def kaeufer_code_erzeugen() -> str:
    """Zufaelliger Kaeufer-Code (kryptografischer Zufall, 32^7 Moeglichkeiten);
    reine Ziffernfolgen werden verworfen."""
    while True:
        code = "".join(secrets.choice(KAEUFER_ALPHABET) for _ in range(KAEUFER_LAENGE))
        if KAEUFER_MUSTER.match(code):
            return code


def kaeufer_normalisieren(roh) -> Optional[str]:
    """Eingabe -> kanonischer Kaeufer-Code oder None.

    ' 6fe7k2m ', '6FE-7K2M', '6FE 7K2M' -> '6FE7K2M'. Eine Nummer der Reihe
    ('10023') ist KEIN Code (kein Buchstabe) — die beiden Muster sind
    disjunkt, eine Kennung ist also nie beides."""
    if not isinstance(roh, str):
        return None
    s = unicodedata.normalize("NFKC", roh).strip()
    if not s or len(s) > 40:
        return None
    for strich in _STRICHE:
        s = s.replace(strich, "-")
    s = _KAEUFER_TRENNER.sub("", s).upper()
    return s if KAEUFER_MUSTER.match(s) else None


def ist_kaeufer_code(s) -> bool:
    return isinstance(s, str) and bool(KAEUFER_MUSTER.match(s))


def kennung_normalisieren(roh) -> Optional[str]:
    """Kontonummer ODER Kaeufer-Code in kanonischer Form, sonst None."""
    return normalisieren(roh) or kaeufer_normalisieren(roh)


def anmeldekennung(roh) -> str:
    """Einheitlicher Schluessel fuer Limiter und Audit: die normalisierte
    Kontonummer bzw. der Kaeufer-Code, sonst die getrimmte, klein
    geschriebene Eingabe (max. 80)."""
    nr = kennung_normalisieren(roh)
    if nr:
        return nr
    return (roh if isinstance(roh, str) else "").strip().lower()[:80]


def nummer_bedingung(bedingung) -> dict:
    """Kontonummer (13.09.2026): Bedingung fuer das Feld `kontonummer`, die den
    Teil-Index kontonummer_eindeutig (partialFilterExpression
    {kontonummer: {$type: 'string'}}) wirklich nutzt. Der Planer erkennt
    eine reine Gleichheit oder ein $regex NICHT als Teilmenge des Filters
    (COLLSCAN, per explain belegt); mit dem mitgefuehrten Typfilter wird es
    ein IXSCAN.
    '10023' -> {'$eq': '10023', '$type': 'string'};
    {'$regex': '^10023-'} -> {'$regex': '^10023-', '$type': 'string'}."""
    if isinstance(bedingung, dict):
        return {**bedingung, "$type": "string"}
    return {"$eq": bedingung, "$type": "string"}


def sucher_nummer(kunden_nr, zusatz) -> str:
    return f"{int(kunden_nr)}-{int(zusatz)}"


def ist_kontonummer(s) -> bool:
    return isinstance(s, str) and bool(MUSTER.match(s))


def ist_kontonummer_dublette(exc) -> bool:
    """True NUR, wenn ein DuplicateKeyError den Kontonummer-Index betrifft.

    Jede andere Dublette (z.B. 'email_1') bleibt beim Aufrufer die bisherige
    409 — ein neuer Versuch mit neuer Nummer wuerde sie nie aufloesen."""
    try:
        from pymongo.errors import DuplicateKeyError
    except Exception:           # pragma: no cover - pymongo ist immer da
        return False
    if not isinstance(exc, DuplicateKeyError):
        return False
    details = getattr(exc, "details", None) or {}
    muster = details.get("keyPattern") if isinstance(details, dict) else None
    if muster:
        return "kontonummer" in muster
    text = str(exc)
    return bool(re.search(r"index:\s*kontonummer", text)
                or "kontonummer_eindeutig" in text
                or re.search(r"dup key:\s*\{\s*kontonummer\s*:", text))
