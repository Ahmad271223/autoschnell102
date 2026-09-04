"""Ein Eingang fuer ALLE externen Inserats-Abrufe.

Vorher entschied jede Route selbst, wie sie an Fahrzeugdaten kommt — der
Lasttest-Mock galt deshalb nur fuer /mobile/compare, waehrend
/listings/resolve echte Abrufe ausloeste. Hier liegt die Entscheidung
EINMAL, damit kein Pfad daran vorbeikommt.
"""
import asyncio
from anbieter_fehler import AnbieterFehler, melden
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


# Tagesbudget fuer KOSTENPFLICHTIGE Abrufe (mobile.de/AutoScout via Apify,
# ~0,4-0,6 Cent je Abruf). Cache-Treffer kosten nichts und zaehlen nicht —
# nur echte Frisch-Abrufe landen hier.
#
# 0 (oder kleiner) = KEIN Limit. Betreiber-Entscheidung 09/2026: Sucher
# sollen unbegrenzt bei mobile.de und AutoScout abrufen duerfen. Gezaehlt
# wird trotzdem weiter — davon leben die Auswertung und die Warnung.
TAGESLIMIT_JE_FIRMA = int(os.environ.get("ANBIETER_TAGESLIMIT_JE_FIRMA", "0"))
TAGESLIMIT_GESAMT = int(os.environ.get("ANBIETER_TAGESLIMIT_GESAMT", "0"))
# Ab dieser Zahl Abrufe an einem Tag gibt es EINEN Betriebsalarm — ein
# Hinweis, kein Riegel. So faellt ein Ausreisser auf, bevor die Rechnung
# kommt. 0 schaltet auch die Warnung ab.
TAGESWARNUNG = int(os.environ.get("ANBIETER_TAGESWARNUNG", "500"))


async def _warnen_wenn_viel(db, tag: str, stand: int) -> None:
    """Einmal je Tag einen Betriebsalarm, wenn ungewoehnlich viel
    abgerufen wurde. Bremst nichts — meldet nur, damit eine unerwartet
    hohe Rechnung nicht unbemerkt entsteht."""
    if TAGESWARNUNG <= 0 or stand != TAGESWARNUNG:
        return
    try:
        from betrieb import alarm
        kosten = stand * 0.005
        await alarm(db, "anbieter_viele_abrufe", ref=tag,
                    abrufe=stand, tag=tag,
                    geschaetzte_kosten_eur=f"{kosten:.2f}",
                    hinweis="Kostenpflichtige Abrufe bei mobile.de/AutoScout. "
                            "Kein Limit gesetzt (ANBIETER_TAGESLIMIT_*=0) — "
                            "bei Bedarf Guthaben bei Apify pruefen.")
    except Exception:                       # noqa: BLE001
        pass                                # Warnung darf nie bremsen


async def _budget_pruefen(db, source: str, dealer_id: str) -> None:
    if source not in ("mobile", "autoscout24"):
        return
    from datetime import datetime, timedelta, timezone
    from pymongo import ReturnDocument
    tag = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    ablauf = datetime.now(timezone.utc) + timedelta(days=2)
    # Reihenfolge (Runde 5): ZUERST das Firmenlimit, DANN das Gesamtbudget —
    # und bei Ablehnung die Zaehlung zuruecknehmen. Vorher verbrauchte eine
    # Firma, die ihr eigenes Limit laengst ueberschritten hatte, mit jedem
    # abgelehnten Versuch weiter das GESAMTBUDGET aller anderen Firmen.
    belastet = []
    gesamt_stand = 0
    for schluessel, limit in ((f"{tag}:firma:{dealer_id or 'ohne'}",
                               TAGESLIMIT_JE_FIRMA),
                              (f"{tag}:gesamt", TAGESLIMIT_GESAMT)):
        doc = await db.provider_budget.find_one_and_update(
            {"_id": schluessel},
            {"$inc": {"n": 1}, "$setOnInsert": {"ablauf": ablauf}},
            upsert=True, return_document=ReturnDocument.AFTER)
        belastet.append(schluessel)
        if schluessel.endswith(":gesamt"):
            gesamt_stand = doc["n"]
        if limit > 0 and doc["n"] > limit:
            for s in belastet:
                await db.provider_budget.update_one({"_id": s}, {"$inc": {"n": -1}})
            raise RuntimeError(
                "Tageslimit für kostenpflichtige Anbieter-Abrufe erreicht "
                f"({limit}/Tag). Bekannte Links kommen weiter aus dem "
                "Speicher; neue Links bitte morgen erneut — oder das Limit "
                "in der .env erhöhen (ANBIETER_TAGESLIMIT_*).")
    await _warnen_wenn_viel(db, tag, gesamt_stand)


async def fetch_listing(db, source: str, item_id: str, url: str,
                        dealer_id: str = "") -> Dict[str, Any]:
    """Holt ein Inserat bei der Quelle — oder liefert im Lasttest-Modus
    synthetische Daten mit realistischer Verzoegerung."""
    if MOCK_PROVIDER_FETCH:
        await asyncio.sleep(0.4)
        return mock_vehicle(item_id)
    await _budget_pruefen(db, source, dealer_id)
    try:
        return await _abrufen(db, source, item_id, url)
    except AnbieterFehler as exc:
        # Token/Guthaben -> Betriebsalarm (gedrosselt), Text geht 1:1 an
        # den Nutzer (Route: RuntimeError -> 502).
        await melden(db, exc)
        raise


async def _abrufen(db, source: str, item_id: str, url: str) -> Dict[str, Any]:
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
    if source == "autoscout24":
        from autoscout_service import fetch_autoscout_vehicle
        v = await fetch_autoscout_vehicle(url, item_id)
        if not v:
            raise RuntimeError(
                "AutoScout24-Inserat konnte nicht geladen werden — evtl. "
                "entfernt oder Abruf vorübergehend nicht möglich.")
        return v
    raise RuntimeError(f"Source '{source}' ist aktuell nicht angebunden.")
