# -*- coding: utf-8 -*-
"""Fortlaufende Kundennummer je Vertrag (Wunsch Ahmad 25.11.2026).

Jede Firma hat EINEN Zaehler (counters._id = "vertrag_kundennr:<dealer_id>"), den Chef und alle Sucher
der Firma gemeinsam nutzen: der erste Vertrag bekommt 1002, jeder weitere eins mehr — zwei Vertraege
derselben Firma bekommen nie dieselbe Nummer (atomares $inc). Andere Firmen zaehlen unabhaengig.

Eine im Vertragsdialog selbst eingetragene Kundennummer gilt weiter und verbraucht keine Nummer.
Die Vorschau zeigt die naechste Nummer, ohne sie zu verbrauchen; neue Fassungen behalten ihre Nummer.
"""
from pymongo import ReturnDocument
from pymongo.errors import DuplicateKeyError

#: erste Nummer = START + 1 = 1002
START = 1001


def _schluessel(dealer_id: str) -> str:
    return f"vertrag_kundennr:{dealer_id}"


async def ziehen(db, dealer_id: str) -> str:
    """Naechste Nummer der Firma vergeben (atomar)."""
    for _ in range(3):
        try:
            doc = await db.counters.find_one_and_update(
                {"_id": _schluessel(dealer_id)}, {"$inc": {"seq": 1}},
                upsert=True, return_document=ReturnDocument.AFTER)
        except DuplicateKeyError:      # zwei gleichzeitige erste Vertraege: der zweite liest neu
            continue
        return str(START + int(doc["seq"]))
    raise RuntimeError("Kundennummer: Zaehler nicht erreichbar")  # pragma: no cover


async def naechste(db, dealer_id: str) -> str:
    """Die Nummer, die der naechste Vertrag bekaeme (fuer die Vorschau) — verbraucht nichts."""
    doc = await db.counters.find_one({"_id": _schluessel(dealer_id)}, {"seq": 1}) or {}
    return str(START + int(doc.get("seq") or 0) + 1)
