# -*- coding: utf-8 -*-
"""Zentrale Passwortregeln (Pruefbericht 09/2026, Punkt 32).

Vorher gab es die Regel dreimal (auth.py, drivers.py, ad hoc in admin.py)
und die vom Betreiber angelegten Konten prueften nur die Laenge. Jetzt:
EINE Funktion fuer alle Rollen (Firma, Sucher, Fahrer, Kaeufer, Admin).

Regeln:
- mindestens 10 Zeichen, hoechstens 72 Bytes (bcrypt-Grenze — laengere
  Passwoerter wuerden still abgeschnitten)
- mindestens eine Ziffer ODER ein Sonderzeichen
- nicht nur ein wiederholtes Zeichen, nicht in der Liste bekannter
  Allerwelts-Passwoerter (unabhaengig von Gross-/Kleinschreibung)
"""
from __future__ import annotations

MIN_LAENGE = 10
MAX_BYTES = 72
_SONDERZEICHEN = set("!@#$%^&*()_+-=[]{}|;':\",./<>?`~\\ ")

# Bekannte, haeufig kompromittierte Passwoerter (Auszug gaengiger Listen).
# Vergleich ohne Ziffern-/Sonderzeichen-Anhang, damit "Passwort123!" auch
# als Allerwelts-Passwort erkannt wird.
_VERBOTEN = {
    "password", "passwort", "passwort1", "password1", "qwertz", "qwerty",
    "123456", "1234567", "12345678", "123456789", "1234567890", "abc123",
    "111111", "letmein", "welcome", "willkommen", "admin", "administrator",
    "iloveyou", "monkey", "dragon", "sunshine", "princess", "football",
    "baseball", "master", "hallo", "hallo123", "schalke", "bayern", "ficken",
    "geheim", "sommer", "winter", "herbst", "fruehling", "test", "testtest",
    "autohaus", "autoschnell", "autohandel", "kfz", "mercedes", "bmw", "audi",
    "porsche", "hannover", "berlin", "muenchen", "hamburg", "deutschland",
    "changeme", "secret", "default", "login", "user", "root", "guest",
}


def _stamm(pw: str) -> str:
    """Kern des Passworts ohne fuehrende/abschliessende Ziffern und
    Sonderzeichen, kleingeschrieben — fuer den Abgleich mit der Liste."""
    s = pw.strip().lower()
    while s and (s[-1].isdigit() or s[-1] in _SONDERZEICHEN):
        s = s[:-1]
    while s and (s[0].isdigit() or s[0] in _SONDERZEICHEN):
        s = s[1:]
    return s


def pruefe_passwort(pw: str) -> str:
    """Wirft ValueError mit deutscher Begruendung, sonst gibt es das
    Passwort unveraendert zurueck (fuer pydantic-Validatoren)."""
    if pw is None or not isinstance(pw, str):
        raise ValueError("Passwort fehlt")
    if len(pw) < MIN_LAENGE:
        raise ValueError(f"Passwort muss mindestens {MIN_LAENGE} Zeichen lang sein")
    if len(pw.encode("utf-8")) > MAX_BYTES:
        raise ValueError(f"Passwort darf hoechstens {MAX_BYTES} Bytes lang sein")
    if not any(c.isdigit() for c in pw) and not any(c in _SONDERZEICHEN for c in pw):
        raise ValueError("Passwort muss mindestens eine Ziffer oder ein "
                         "Sonderzeichen enthalten")
    if len(set(pw)) < 3:
        raise ValueError("Passwort ist zu einfach (zu wenige verschiedene Zeichen)")
    kern = _stamm(pw)
    if kern in _VERBOTEN or pw.strip().lower() in _VERBOTEN:
        raise ValueError("Dieses Passwort ist zu bekannt/unsicher — bitte ein "
                         "anderes waehlen")
    return pw


# Rueckwaertskompatible Namen (alte Importe in auth.py / drivers.py)
_check_password_strength = pruefe_passwort
