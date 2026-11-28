# -*- coding: utf-8 -*-
"""Zentrale Passwortregeln (Pruefbericht 09/2026, Punkt 32).

Vorher gab es die Regel dreimal (auth.py, drivers.py, ad hoc in admin.py)
und die vom Betreiber angelegten Konten prueften nur die Laenge. Jetzt:
EINE Funktion fuer alle Rollen (Firma, Sucher, Fahrer, Kaeufer, Admin).

Regeln:
- kein Leerzeichen am Anfang oder Ende (Pruefbericht 20.09.2026, K-06)
- mindestens 10 Zeichen, hoechstens 72 Bytes (bcrypt-Grenze — laengere
  Passwoerter wuerden still abgeschnitten)
- mindestens einen Buchstaben UND eine Ziffer oder ein Sonderzeichen
  (Nachpruefung 15.09.2026: rein numerische Passwoerter wie 5837294615 galten
  vorher als stark genug)
- keine eigenen Kontodaten (Kontonummer, Name, E-Mail, Firma, Fahrer-ID),
  wenn der Aufrufer sie mitgibt (persoenlich=...)
- nicht nur ein wiederholtes Zeichen, nicht in der Liste bekannter
  Allerwelts-Passwoerter (unabhaengig von Gross-/Kleinschreibung)
"""
from __future__ import annotations

import re

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


_HEX = re.compile(r"[0-9a-f]+")
# Allerweltswoerter aus Firmennamen/Adressen zaehlen nicht als Kontodaten
# (sonst waere "autohaus" in jedem Haendler-Passwort verboten).
_GENERISCH = {"firma", "gmbh", "autohaus", "autohandel", "handel", "haendler", "sucher",
              "fahrer", "kaeufer", "kunde", "kunden", "admin", "betreiber", "chef", "autos",
              "test", "tests", "smoke", "example", "e2etest", "mail", "info", "kontakt",
              "musterfirma", "anfrage", "anlage", "regel"}
_WORT_MIN = 5


def _persoenliche_teile(wert) -> list:
    """Kontodaten in vergleichbare Stuecke zerlegen: 'Auto Schnell GmbH' ->
    ['autoschnellgmbh', 'schnell'], '10023-2' -> ['10023', '100232'],
    'FD-7K2M9QX4' -> ['7k2m9qx4', 'fd7k2m9qx4'], 'anna.muster@x.de' ->
    ['annamuster'] (E-Mail nur als ganzer Teil vor dem @). Woerter erst ab
    5 Zeichen, Allerweltswoerter und reine Hex-Stuecke (Codes, Zufalls-
    suffixe) zaehlen nicht — Bindestrich-Teile einer Kennung (Kontonummer,
    Fahrer-ID) dagegen immer."""
    s = str(wert or "").strip().lower()
    if not s:
        return []
    teile = set()
    if "@" in s:
        ganz = "".join(c for c in s.split("@", 1)[0] if c.isalnum())
        if len(ganz) >= _WORT_MIN and not (_HEX.fullmatch(ganz) and not ganz.isdigit()):
            teile.add(ganz)
        return sorted(teile)
    ganz = "".join(c for c in s if c.isalnum())
    # Ganzer Wert: immer bei Nummern (Kontonummer), nicht bei Hash-artigen
    # Zufallswerten (Hex mit Buchstaben, z.B. Test-Suffixe).
    if len(ganz) >= 4 and not (_HEX.fullmatch(ganz) and not ganz.isdigit())             and ganz not in _GENERISCH:
        teile.add(ganz)
    for t in "".join(c if c.isalnum() else " " for c in s).split():
        if len(t) >= _WORT_MIN and not _HEX.fullmatch(t) and t not in _GENERISCH:
            teile.add(t)
    if "-" in s:
        for t in s.split("-"):
            t = "".join(c for c in t if c.isalnum())
            if len(t) >= 4 and t not in _GENERISCH:
                teile.add(t)
    return sorted(teile)


def pruefe_passwort(pw: str, persoenlich=()) -> str:
    """Wirft ValueError mit deutscher Begruendung, sonst gibt es das
    Passwort unveraendert zurueck (fuer pydantic-Validatoren).
    `persoenlich`: Kontodaten (Kontonummer, Name, E-Mail, Firma, Fahrer-ID),
    die nicht im Passwort stecken duerfen (Nachpruefung 15.09.2026)."""
    if pw is None or not isinstance(pw, str):
        raise ValueError("Passwort fehlt")
    if pw != pw.strip():
        # Pruefbericht 20.09.2026 (K-06): wie die Vorpruefung der Oberflaeche
        # (frontend/src/lib/passwort.js) — ein Leerzeichen am Rand geht beim
        # Abtippen oder Vorlesen verloren, das Passwort "klappt" dann nie.
        raise ValueError("Passwort darf nicht mit einem Leerzeichen beginnen oder enden")
    if len(pw) < MIN_LAENGE:
        raise ValueError(f"Passwort muss mindestens {MIN_LAENGE} Zeichen lang sein")
    if len(pw.encode("utf-8")) > MAX_BYTES:
        raise ValueError(f"Passwort darf hoechstens {MAX_BYTES} Bytes lang sein")
    if not any(c.isdigit() for c in pw) and not any(c in _SONDERZEICHEN for c in pw):
        raise ValueError("Passwort muss mindestens eine Ziffer oder ein "
                         "Sonderzeichen enthalten")
    if not any(c.isalpha() for c in pw):
        # Nachpruefung 15.09.2026 (Anmeldung Nr. 8): nur Ziffern/Sonderzeichen
        # (z.B. 5837294615) sind deutlich schwaecher als beabsichtigt.
        raise ValueError("Passwort muss mindestens einen Buchstaben enthalten — "
                         "nur Ziffern oder Sonderzeichen reichen nicht")
    if len(set(pw)) < 3:
        raise ValueError("Passwort ist zu einfach (zu wenige verschiedene Zeichen)")
    kern = _stamm(pw)
    if kern in _VERBOTEN or pw.strip().lower() in _VERBOTEN:
        raise ValueError("Dieses Passwort ist zu bekannt/unsicher — bitte ein "
                         "anderes waehlen")
    klein = pw.lower()
    for wert in persoenlich or ():
        for teil in _persoenliche_teile(wert):
            if teil in klein:
                raise ValueError("Passwort darf keine eigenen Kontodaten enthalten "
                                 "(Kontonummer, Name, E-Mail, Firma, Fahrer-ID)")
    return pw


def persoenliche_werte(konto: dict) -> list:
    """Kontodaten eines users-/driver_accounts-Dokuments fuer pruefe_passwort."""
    k = konto or {}
    return [k.get(f) for f in ("kontonummer", "email", "username", "first_name", "last_name",
                               "display_name", "driver_code", "company_name", "contact_person")
            if k.get(f)]


# Rueckwaertskompatible Namen (alte Importe in auth.py / drivers.py)
_check_password_strength = pruefe_passwort
