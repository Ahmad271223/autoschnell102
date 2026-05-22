"""Extract the number of previous owners ("Halter" / "Vorbesitzer") from
free-text vehicle descriptions.

German listings use a variety of phrasings for this. The most common are:

  - "1. Hand", "2.Hand", "2:Hand", "2 Hand"
  - "Erste Hand", "Zweite Hand", "Dritte Hand", "Vierte Hand"
  - "1 Vorhalter", "Vorhalter: 2", "1 Vorbesitzer"
  - "1 Halter", "Halter: 2", "Anzahl der Fahrzeughalter: 3"
  - "Fahrzeughalter 2"

This helper returns an integer 1..15 if a clear match is found, otherwise
None so callers fall back to manual entry.
"""
from __future__ import annotations

import re
from typing import Optional

# Map German ordinals/numerals to integer.
_WORD_NUMS = {
    "erste": 1, "erster": 1, "1.": 1, "ein": 1, "eins": 1, "1": 1,
    "zweite": 2, "zweiter": 2, "2.": 2, "zwei": 2, "2": 2,
    "dritte": 3, "dritter": 3, "3.": 3, "drei": 3, "3": 3,
    "vierte": 4, "vierter": 4, "4.": 4, "vier": 4, "4": 4,
    "fünfte": 5, "fünfter": 5, "5.": 5, "fünf": 5, "5": 5,
    "sechste": 6, "sechster": 6, "6.": 6, "sechs": 6, "6": 6,
    "siebte": 7, "siebter": 7, "7.": 7, "sieben": 7, "7": 7,
    "achte": 8, "achter": 8, "8.": 8, "acht": 8, "8": 8,
    "neunte": 9, "neunter": 9, "9.": 9, "neun": 9, "9": 9,
    "zehnte": 10, "zehnter": 10, "10.": 10, "zehn": 10, "10": 10,
}


def _word_to_int(token: str) -> Optional[int]:
    if not token:
        return None
    t = token.strip().lower().rstrip(".")
    # Bare digit?
    if t.isdigit():
        n = int(t)
        return n if 1 <= n <= 15 else None
    return _WORD_NUMS.get(t) or _WORD_NUMS.get(t + ".")


# Patterns are tried in order. First match wins. The captured group must be
# a digit (1..15) OR a German ordinal word.
_NUM = r"(\d{1,2}|erste[rs]?|zweite[rs]?|dritte[rs]?|vierte[rs]?|fünfte[rs]?|sechste[rs]?|siebte[rs]?|achte[rs]?|neunte[rs]?|zehnte[rs]?)"

_PATTERNS = [
    # "Anzahl der Fahrzeughalter: 2" / "Anzahl Fahrzeughalter 2"
    re.compile(rf"anzahl\s+(?:der\s+)?fahrzeughalter\s*[:=\-]?\s*{_NUM}\b", re.I),
    # "Fahrzeughalter: 2" / "Fahrzeughalter 2"
    re.compile(rf"fahrzeughalter\s*[:=\-]?\s*{_NUM}\b", re.I),
    # "Vorhalter: 2" / "Vorhalter 2" / "2 Vorhalter"
    re.compile(rf"vorhalter\s*[:=\-]?\s*{_NUM}\b", re.I),
    re.compile(rf"\b{_NUM}\s*vorhalter\b", re.I),
    # "Vorbesitzer: 2" / "Vorbesitzer 2" / "2 Vorbesitzer"
    re.compile(rf"vorbesitzer\s*[:=\-]?\s*{_NUM}\b", re.I),
    re.compile(rf"\b{_NUM}\s*vorbesitzer\b", re.I),
    # "Halter: 2" / "Halter 2" / "2 Halter" — but NOT inside compound words
    re.compile(rf"(?:^|[^a-zäöüß])halter\s*[:=\-]?\s*{_NUM}\b", re.I),
    re.compile(rf"\b{_NUM}\s*halter\b(?!ung|los)", re.I),
    # "2. Hand", "2.Hand", "2 Hand", "2:Hand", "1.-Hand"
    re.compile(rf"\b{_NUM}\s*[.\-:]?\s*hand\b", re.I),
    # "Erste Hand", "Zweite Hand", ... (only word ordinals before "Hand")
    re.compile(r"\b(erste|zweite|dritte|vierte|fünfte|sechste|siebte|achte|neunte|zehnte)r?\s+hand\b", re.I),
]


def extract_owners_from_text(text: Optional[str]) -> Optional[int]:
    """Return the number of previous owners parsed from any free-text
    description, or None if no clear pattern was found."""
    if not text or not isinstance(text, str):
        return None
    # Normalise whitespace so regex with \s works across newlines.
    haystack = re.sub(r"\s+", " ", text)
    for pat in _PATTERNS:
        m = pat.search(haystack)
        if not m:
            continue
        n = _word_to_int(m.group(1))
        if n and 1 <= n <= 15:
            return n
    return None


__all__ = ["extract_owners_from_text"]
