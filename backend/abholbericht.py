"""Massgeblicher Abholbericht je Fahrzeug (Nachpruefung Runde 14, Nr. 38).

Die Leser in routes/bestand.py und routes/resale.py holten den Bericht per
find_one({vehicle_id, dealer_id, superseded != True}) OHNE Sortierung —
welcher Bericht kam, entschied der Index, den der Planer waehlte. Hat ein
Fahrzeug mehrere Termine (z. B. eine "nicht abgeholt"-Fahrt und spaeter die
erfolgreiche Abholung), war damit auch der TERMIN zufaellig. Hier steht die
Regel EINMAL:

  1. bevorzugt der Bericht des Termins mit Status "abgeholt" — bei mehreren
     der mit dem juengsten pickup_date (dann juengstes created_at);
  2. sonst der juengste aktuelle Bericht (created_at absteigend).

Je Termin zaehlt nur die hoechste nicht ersetzte Version. Rueckgabe ohne
_id, oder None, wenn es keinen aktuellen Bericht gibt.
"""
from typing import Optional


async def massgeblicher_bericht(db, vehicle_id: str, dealer_id: str) -> Optional[dict]:
    if not vehicle_id or not dealer_id:
        return None
    berichte = await db.pickup_reports.find(
        {"vehicle_id": vehicle_id, "dealer_id": dealer_id,
         "superseded": {"$ne": True}},
        {"_id": 0},
    ).sort([("created_at", -1), ("version", -1)]).to_list(100)
    if not berichte:
        return None
    # Je Termin nur die hoechste Version (Doppelzustaende aus abgebrochenen
    # Laeufen, siehe routes/drivers.py driver_submit_report).
    je_termin: dict = {}
    for b in berichte:
        schluessel = b.get("appointment_id")
        alt = je_termin.get(schluessel)
        if alt is None or (b.get("version") or 0) > (alt.get("version") or 0):
            je_termin[schluessel] = b
    kandidaten = list(je_termin.values())
    termine = {}
    async for a in db.appointments.find(
            {"id": {"$in": [k for k in je_termin if k]}, "dealer_id": dealer_id},
            {"_id": 0, "id": 1, "status": 1, "pickup_date": 1}):
        termine[a["id"]] = a
    abgeholt = [b for b in kandidaten
                if (termine.get(b.get("appointment_id")) or {}).get("status") == "abgeholt"]
    if abgeholt:
        abgeholt.sort(
            key=lambda b: ((termine.get(b.get("appointment_id")) or {}).get("pickup_date") or "",
                           b.get("created_at") or ""),
            reverse=True)
        return abgeholt[0]
    kandidaten.sort(key=lambda b: (b.get("created_at") or ""), reverse=True)
    return kandidaten[0]
