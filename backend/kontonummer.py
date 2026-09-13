# -*- coding: utf-8 -*-
"""Kontonummer (13.09.2026): Anmeldung mit Kontonummer statt E-Mail.

Reines Modul — KEIN Import aus deps/server/routes, damit Limiter, Tests und
Skripte es ohne Datenbank nutzen koennen.

Format:
- Chef einer Firma:   "10023"    (= dealers.kunden_nr)
- Sucher einer Firma: "10023-2"  (Zusatz aus dealers.sucher_seq)
- Kaeufer/Fahrer:     "10031"    (eigene Nummer aus derselben Reihe)
"""
import re
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


def anmeldekennung(roh) -> str:
    """Einheitlicher Schluessel fuer Limiter und Audit: die normalisierte
    Kontonummer, sonst die getrimmte, klein geschriebene Eingabe (max. 80)."""
    nr = normalisieren(roh)
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
