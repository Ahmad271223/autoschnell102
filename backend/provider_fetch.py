"""Ein Eingang fuer ALLE externen Inserats-Abrufe.

Vorher entschied jede Route selbst, wie sie an Fahrzeugdaten kommt — der
Lasttest-Mock galt deshalb nur fuer /mobile/compare, waehrend
/listings/resolve echte Abrufe ausloeste. Hier liegt die Entscheidung
EINMAL, damit kein Pfad daran vorbeikommt.
"""
import asyncio
import os
from typing import Any, Dict

# NUR fuer Staging-Lasttests: externe Abrufe durch synthetische Daten
# ersetzen (Cache-, Lease- und Begrenzungslogik laeuft trotzdem echt).
# NIE in Produktion setzen.
MOCK_PROVIDER_FETCH = os.environ.get(
    "MOCK_PROVIDER_FETCH", "").strip().lower() in ("1", "true", "yes")


def mock_vehicle(item_id: str) -> Dict[str, Any]:
    return {"mobile_ad_id": item_id, "kleinanzeigen_id": item_id,
            "title": f"Lasttest Fahrzeug {item_id}",
            "make_label": "VW", "model_label": "Golf",
            "list_price": 15000, "price": "15.000 \u20ac",
            "mileage": 90000, "first_registration": "01/2020",
            "fuel_label": "Benzin", "power_ps": 110,
            "seller_zip": "30159", "seller_city": "Hannover",
            "images": [], "_mock": True}


async def fetch_listing(db, source: str, item_id: str, url: str) -> Dict[str, Any]:
    """Holt ein Inserat bei der Quelle — oder liefert im Lasttest-Modus
    synthetische Daten mit realistischer Verzoegerung."""
    if MOCK_PROVIDER_FETCH:
        await asyncio.sleep(0.4)
        return mock_vehicle(item_id)
    if source == "kleinanzeigen":
        from kleinanzeigen_service import fetch_kleinanzeigen_vehicle
        v = await fetch_kleinanzeigen_vehicle(url)
        v["mobile_ad_id"] = v.get("kleinanzeigen_id") or item_id
        v.setdefault("kleinanzeigen_id", item_id)
        return v
    if source == "mobile":
        from mobile_service import get_vehicle
        # url mitgeben: der Apify-Scraper ruft dann direkt die eingefuegte
        # Inserats-URL ab statt sie aus der ID rekonstruieren zu muessen.
        v = await get_vehicle(db, item_id, url=url)
        if not v:
            raise RuntimeError("Fahrzeug konnte nicht geladen werden.")
        v.setdefault("mobile_ad_id", item_id)
        v.pop("_source", None)
        return v
    raise RuntimeError(f"Source '{source}' ist aktuell nicht angebunden.")
