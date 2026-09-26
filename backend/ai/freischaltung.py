# -*- coding: utf-8 -*-
"""KI-Freischaltung je Konto (Wunsch Ahmad 25.09.2026 abends).

Wie beim Abo schaltet der Betreiber (Super-Admin) die KI-Bewertung je
Sucher-Konto einzeln frei: `users.ki_aktiv` (Standard aus). Ohne
Freischaltung laeuft alles wie bisher, nur ohne KI-Karte — Status
"freischaltung" mit Grund, nie ein Fehler.

Wer zaehlt wofuer:
- Vertrag (Sucher legt den Kaufvertrag an): das EIGENE Konto.
- Abholung (Freigabe beim Chef, Fahrer-Ansicht, Start beim Abschicken):
  das Hauptchef-Konto der Firma (dealers.user_id) — die Abholung ist eine
  Firmenfunktion, der Fahrer hat kein eigenes Konto dafuer.
- Abholung ZUSAETZLICH (Wunsch Ahmad 26.09.2026 abends): der Fahrer des
  Termins muss selbst freigeschaltet sein (driver_accounts.ki_aktiv) —
  "wenn ein Fahrer keine KI-Abholung freigeschaltet hat, dann allgemein
  nein". Beides muss gelten; die Sperre wird nie abgelegt.
"""
from __future__ import annotations

from typing import Any, Dict, Optional

from deps import db as _db

STATUS = "freischaltung"
GRUND = "KI-Bewertung für dieses Konto nicht freigeschaltet"
GRUND_FAHRER = "Fahrer nicht für die KI-Abholbewertung freigeschaltet"


async def konto_freigeschaltet(user_id: Optional[str], db=None) -> bool:
    """users.ki_aktiv des Kontos (nur True zaehlt). Wirft nie."""
    if not user_id:
        return False
    db = db if db is not None else _db
    try:
        u = await db.users.find_one({"id": user_id}, {"_id": 0, "ki_aktiv": 1})
        return bool(u and u.get("ki_aktiv") is True)
    except Exception:  # noqa: BLE001
        return False


async def firma_freigeschaltet(dealer_id: Optional[str], db=None) -> bool:
    """Abholung: das Hauptchef-Konto der Firma entscheidet."""
    if not dealer_id:
        return False
    db = db if db is not None else _db
    try:
        d = await db.dealers.find_one({"id": dealer_id}, {"_id": 0, "user_id": 1})
        chef_id = (d or {}).get("user_id")
        if not chef_id:
            # Altbestand ohne Zeiger: aeltestes dealer-Konto der Firma
            u = await db.users.find_one({"dealer_id": dealer_id, "role": "dealer"},
                                        {"_id": 0, "id": 1}, sort=[("created_at", 1)])
            chef_id = (u or {}).get("id")
        return await konto_freigeschaltet(chef_id, db)
    except Exception:  # noqa: BLE001
        return False


async def fahrer_freigeschaltet(driver_id: Optional[str], db=None) -> bool:
    """driver_accounts.ki_aktiv des Fahrers (nur True zaehlt). Wirft nie.
    Ohne Fahrer (kein driver_id) gilt: nicht freigeschaltet."""
    if not driver_id:
        return False
    db = db if db is not None else _db
    try:
        d = await db.driver_accounts.find_one({"id": driver_id}, {"_id": 0, "ki_aktiv": 1})
        return bool(d and d.get("ki_aktiv") is True)
    except Exception:  # noqa: BLE001
        return False


def gesperrt(grund: str = GRUND, **extra: Any) -> Dict[str, Any]:
    """Antwort fuer die Karten (gleiche Form wie 'aus'). `grund` nennt, WER
    nicht freigeschaltet ist (Konto/Firma oder Fahrer)."""
    return {"status": STATUS, "grund": grund, "ergebnis": None, **extra}
