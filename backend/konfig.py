# -*- coding: utf-8 -*-
"""Zahlen aus der Umgebung lesen, ohne den Start zu kippen.

Pruefung 14.09.2026 (A18/B30): mehrere Module lasen Werte mit int(os.environ
...) direkt beim Import. Ein Tippfehler in der .env wie FAHRERFOTO_TAGE=14d
warf dann beim Import einen ValueError und liess den Backend-Worker gar nicht
erst starten — ohne verstaendliche Meldung. Jetzt: Standardwert, Fehlermeldung
im Log, und production_check meldet die fehlerhaften Namen (in Produktion als
Fehler, sonst als Warnung).

Reines Modul: keine Importe aus deps/server/routes.
"""
from __future__ import annotations

import logging
import os
from typing import Dict, Optional

log = logging.getLogger("autohandel")

# name -> roher Wert, der sich nicht lesen liess (fuer production_check)
FEHLERHAFT: Dict[str, str] = {}
# Pruefbericht 20.09.2026 (AL-21): name -> (eingestellt, wirksam) fuer Werte,
# die unten/oben verletzt haben und geklemmt wurden. Bisher stand das nur
# als Warnung im Log; jetzt sehen es production_check und /admin/betrieb.
GEKLEMMT: Dict[str, tuple] = {}


def _klemmen(name: str, wert, unten, oben):
    """unten/oben durchsetzen, Verletzung in GEKLEMMT festhalten (AL-21)."""
    roh = wert
    if unten is not None and wert < unten:
        log.warning("%s=%s liegt unter %s — wird auf %s gesetzt", name, wert, unten, unten)
        wert = unten
    if oben is not None and wert > oben:
        log.warning("%s=%s liegt ueber %s — wird auf %s gesetzt", name, wert, oben, oben)
        wert = oben
    if wert != roh:
        GEKLEMMT[name] = (roh, wert)
    return wert


def zahl_env(name: str, standard: int, *, unten: Optional[int] = None,
             oben: Optional[int] = None) -> int:
    """Ganze Zahl aus der Umgebung; leer -> Standard; unlesbar -> Standard +
    Eintrag in FEHLERHAFT; unten/oben begrenzen mit Warnung (+ GEKLEMMT)."""
    roh = os.environ.get(name, "")
    roh = roh.strip() if isinstance(roh, str) else ""
    if not roh:
        wert = int(standard)
    else:
        try:
            wert = int(roh)
        except ValueError:
            try:
                zahl = float(roh)
                if zahl != int(zahl):
                    raise ValueError(roh)
                wert = int(zahl)
            except ValueError:
                FEHLERHAFT[name] = roh
                log.error("%s=%r ist keine ganze Zahl — Standard %s wird verwendet",
                          name, roh, standard)
                wert = int(standard)
    return _klemmen(name, wert, unten, oben)


def kommazahl_env(name: str, standard: float, *, unten: Optional[float] = None,
                  oben: Optional[float] = None) -> float:
    """Pruefbericht 20.09.2026 (P-19): Kommazahl aus der Umgebung — dieselben
    Regeln wie zahl_env (leer -> Standard, unlesbar -> Standard + FEHLERHAFT,
    unten/oben klemmen). Vorher las email_service RESEND_RATE/-WARTEN_MAX roh
    per float(): ein Tippfehler brach jeden Mailversand beim Import ab."""
    roh = os.environ.get(name, "")
    roh = roh.strip() if isinstance(roh, str) else ""
    if not roh:
        wert = float(standard)
    else:
        try:
            wert = float(roh.replace(",", "."))
            if wert != wert or wert in (float("inf"), float("-inf")):
                raise ValueError(roh)
        except ValueError:
            FEHLERHAFT[name] = roh
            log.error("%s=%r ist keine Zahl — Standard %s wird verwendet",
                      name, roh, standard)
            wert = float(standard)
    return _klemmen(name, wert, unten, oben)


def zahl_pruefen(name: str) -> Optional[str]:
    """Nur pruefen (kein Standard): None, wenn leer oder ganze Zahl; sonst der
    rohe Wert. Fuer production_check bei Variablen, die anderswo mit eigenem
    Rueckfall gelesen werden."""
    roh = os.environ.get(name, "")
    roh = roh.strip() if isinstance(roh, str) else ""
    if not roh:
        return None
    try:
        int(roh)
        return None
    except ValueError:
        return roh


def schalter_env(name: str, standard: bool = False) -> bool:
    """Ja/Nein-Schalter aus der Umgebung (true/1/ja/yes/on)."""
    wert = (os.environ.get(name) or "").strip().lower()
    if not wert:
        return standard
    return wert in ("1", "true", "ja", "yes", "on")


def marktplatz_aktiv() -> bool:
    """Go-Live 15.09.2026 (Wunsch Ahmad): B2B-Marktplatz und das Inserieren
    sind abgeschaltet ("Demnaechst verfuegbar") — Fokus Firmenchef, Sucher,
    Fahrer. Freischalten mit MARKTPLATZ_AKTIV=true (Test/CI: gesetzt). Der
    Code bleibt vollstaendig erhalten, nur der Schalter entscheidet."""
    return schalter_env("MARKTPLATZ_AKTIV", False)

