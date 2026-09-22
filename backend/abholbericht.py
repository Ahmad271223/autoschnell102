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

Pruefbericht 20.09.2026 (P-37): Die 100 juengsten Berichte wurden geladen und
ERST DANACH galt der Vorrang abgeholter Termine — ab 100 Terminen zum selben
Fahrzeug fiel der Bericht der Abholung still aus der Auswahl. Jetzt werden
zuerst die abgeholten Termine des Fahrzeugs bestimmt (ueber die Termin-IDs
aller aktuellen Berichte, ohne Grenze) und deren Berichte gezielt geladen;
nur ohne abgeholten Termin greift die allgemeine Abfrage.
"""
from typing import Optional

# Obergrenze nur gegen Ausreisser (Berichte je Termin: hoechstens einige Versionen).
_BERICHTE_MAX = 100


def _hoechste_je_termin(berichte: list) -> list:
    """Je Termin nur die hoechste Version (Doppelzustaende aus abgebrochenen
    Laeufen, siehe routes/drivers.py driver_submit_report)."""
    je_termin: dict = {}
    for b in berichte:
        schluessel = b.get("appointment_id")
        alt = je_termin.get(schluessel)
        if alt is None or (b.get("version") or 0) > (alt.get("version") or 0):
            je_termin[schluessel] = b
    return list(je_termin.values())


async def massgeblicher_bericht(db, vehicle_id: str, dealer_id: str,
                                nur_termine: Optional[list] = None) -> Optional[dict]:
    """nur_termine (Runde 21): Auswahl nur unter den Berichten dieser Termine
    — fuer Sucher. Vorher waehlte die Akte den massgeblichen Bericht der
    GANZEN Firma und blendete ihn fuer den Sucher aus, wenn er zum Termin
    eines Kollegen gehoerte; den eigenen Bericht samt Fotos sah er dann nie."""
    if not vehicle_id or not dealer_id:
        return None
    filter_: dict = {"vehicle_id": vehicle_id, "dealer_id": dealer_id,
                     "superseded": {"$ne": True}}
    if nur_termine is not None:
        termine = [t for t in nur_termine if t]
        if not termine:
            return None
        filter_["appointment_id"] = {"$in": termine}
    sortierung = [("created_at", -1), ("version", -1)]
    # P-37: Termine ALLER aktuellen Berichte (distinct, ohne Grenze), davon
    # die abgeholten — deren Berichte gezielt laden.
    termin_ids = [t for t in await db.pickup_reports.distinct("appointment_id", filter_) if t]
    if termin_ids:
        termine: dict = {}
        async for a in db.appointments.find(
                {"id": {"$in": termin_ids}, "dealer_id": dealer_id, "status": "abgeholt"},
                {"_id": 0, "id": 1, "pickup_date": 1}):
            termine[a["id"]] = a
        if termine:
            abgeholt = _hoechste_je_termin(await db.pickup_reports.find(
                {**filter_, "appointment_id": {"$in": sorted(termine)}},
                {"_id": 0},
            ).sort(sortierung).to_list(_BERICHTE_MAX))
            if abgeholt:
                abgeholt.sort(
                    key=lambda b: ((termine.get(b.get("appointment_id")) or {}).get("pickup_date") or "",
                                   b.get("created_at") or ""),
                    reverse=True)
                return abgeholt[0]
    berichte = await db.pickup_reports.find(filter_, {"_id": 0}).sort(sortierung).to_list(_BERICHTE_MAX)
    if not berichte:
        return None
    kandidaten = _hoechste_je_termin(berichte)
    kandidaten.sort(key=lambda b: (b.get("created_at") or ""), reverse=True)
    return kandidaten[0]
