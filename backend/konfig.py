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


def zahl_env(name: str, standard: int, *, unten: Optional[int] = None,
             oben: Optional[int] = None) -> int:
    """Ganze Zahl aus der Umgebung; leer -> Standard; unlesbar -> Standard +
    Eintrag in FEHLERHAFT; unten/oben begrenzen mit Warnung."""
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
    if unten is not None and wert < unten:
        log.warning("%s=%s liegt unter %s — wird auf %s gesetzt", name, wert, unten, unten)
        wert = unten
    if oben is not None and wert > oben:
        log.warning("%s=%s liegt ueber %s — wird auf %s gesetzt", name, wert, oben, oben)
        wert = oben
    return wert


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
