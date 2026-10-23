# -*- coding: utf-8 -*-
"""Dateinamen fuer PDF-Antworten (Content-Disposition) — EINE Stelle.

Rollenpruefung 22.09.2026 (RP-200/RP-351): Der Dateiname eines Kaufvertrags
wird aus Marke und Modell gebaut ("Kaufvertrag_Škoda_Octavia_….pdf"). Die
Kopfzeilen einer HTTP-Antwort kodiert Starlette aber als latin-1 — ein
'Š' (U+0160), ein Gedankenstrich '–', ein geschuetzter Bindestrich (U+2011)
oder ein Emoji liessen die Antwort mit UnicodeEncodeError scheitern: 500
statt PDF, und zwar bei JEDEM Abruf dieses Vertrags.

Jetzt:
  * `filename="…"` traegt nur noch ASCII (Umlaute werden ausgeschrieben,
    Akzente entfernt, alles andere wird zu '_');
  * `filename*=UTF-8''…` (RFC 5987/6266) traegt den Originalnamen — moderne
    Browser zeigen ihn so an, wie er gemeint war.

Ohne FastAPI/Datenbank, damit auch routes/admin.py und routes/appointments.py
denselben Helfer nutzen koennen.
"""
import re
import unicodedata
from urllib.parse import quote

#: Umlaute nicht einfach wegwerfen ("Müller" -> "Mller"), sondern so
#: schreiben, wie man es ohne Umlaut-Taste tun wuerde.
_UMSCHRIFT = str.maketrans({
    "ä": "ae", "ö": "oe", "ü": "ue", "Ä": "Ae", "Ö": "Oe", "Ü": "Ue", "ß": "ss",
})

#: Hoechstlaenge des Dateinamens (Kopfzeilen bleiben ueberschaubar).
MAX_LAENGE = 200


def _roh(name) -> str:
    """Steuerzeichen, Anfuehrungszeichen und Backslash raus — sie koennten
    eine weitere Kopfzeile einschleusen oder den Parser verwirren."""
    text = str(name or "")
    text = re.sub(r"[\x00-\x1f\x7f\"\\]", "", text)
    return text.strip()


def ascii_dateiname(name, fallback: str = "document.pdf") -> str:
    """Nur [A-Za-z0-9._-]; alles andere wird '_' (mehrere hintereinander
    zu einem). Leeres Ergebnis -> fallback.

    Rollenpruefung 22.09.2026 (RP-407): wie routes/appointments.
    _content_disposition — Striche der Unicode-Kategorie Pd (Gedankenstrich
    '–', geschuetzter Bindestrich U+2011 aus dem Katalog) werden zu '-'
    statt ersatzlos zu verschwinden ("Mercedes–Benz" -> "Mercedes-Benz",
    nicht "MercedesBenz")."""
    text = "".join("-" if unicodedata.category(ch) == "Pd" else ch for ch in _roh(name))
    text = text.translate(_UMSCHRIFT)
    text = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode("ascii")
    text = re.sub(r"[^A-Za-z0-9._-]+", "_", text)
    text = re.sub(r"_+", "_", text).strip("._")
    return text[:MAX_LAENGE] or fallback


def sicherer_dateiname(name, fallback: str = "document.pdf") -> str:
    """Der Originalname ohne gefaehrliche Zeichen (fuer filename*). Leer ->
    fallback."""
    return _roh(name)[:MAX_LAENGE] or fallback


def content_disposition(name, art: str = "inline",
                        fallback: str = "document.pdf") -> str:
    """Kopfzeile fuer PDF-/Datei-Antworten: ASCII-Name plus UTF-8-Name.

    Beispiel "Kaufvertrag_Škoda.pdf" ->
    inline; filename="Kaufvertrag_Skoda.pdf"; filename*=UTF-8''Kaufvertrag_%C5%A0koda.pdf
    """
    art = "attachment" if str(art).strip().lower() == "attachment" else "inline"
    ascii_name = ascii_dateiname(name, fallback=ascii_dateiname(fallback))
    original = sicherer_dateiname(name, fallback=fallback)
    kopf = f'{art}; filename="{ascii_name}"'
    if original != ascii_name:
        kopf += f"; filename*=UTF-8''{quote(original, safe='')}"
    return kopf
