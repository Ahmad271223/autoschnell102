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

from deps import log

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
# Entscheidung Ahmad 16.09.2026: hoechstens 400 NEUE Abrufe je Konto und Tag
# (alle Quellen, auch Kleinanzeigen). Bekannte Links aus dem Speicher und
# Mitwarten an einem laufenden Abruf kosten nichts. Im Code 0 = aus (Tests,
# lokale Entwicklung); docker-compose.yml und .env.example setzen 400.
TAGESLIMIT_JE_KONTO = int(os.environ.get("ANBIETER_TAGESLIMIT_JE_KONTO", "0"))


class TageslimitErreicht(RuntimeError):
    """Tageslimit (Konto, Firma oder gesamt) erreicht — die Routen antworten
    429, ein Link-Job scheitert sofort ohne weitere Versuche."""
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


async def _budget_zurueck(db, belastet) -> None:
    """Runde 19 (Nr. 7): ein technisch gescheiterter Abruf zaehlt nicht."""
    for s in belastet or []:
        try:
            await db.provider_budget.update_one({"_id": s, "n": {"$gt": 0}}, {"$inc": {"n": -1}})
        except Exception:  # noqa: BLE001
            pass


async def _budget_pruefen(db, source: str, dealer_id: str, user_id: str = "") -> list:
    from datetime import datetime, timedelta, timezone
    from pymongo import ReturnDocument
    tag = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    ablauf = datetime.now(timezone.utc) + timedelta(days=2)
    belastet = []

    async def _zaehlen(schluessel: str) -> int:
        doc = await db.provider_budget.find_one_and_update(
            {"_id": schluessel},
            {"$inc": {"n": 1}, "$setOnInsert": {"ablauf": ablauf}},
            upsert=True, return_document=ReturnDocument.AFTER)
        belastet.append(schluessel)
        return doc["n"]

    async def _ablehnen(limit: int, wer: str) -> None:
        # Bei Ablehnung die Zaehlung zuruecknehmen — der abgelehnte Versuch
        # zaehlt weder fuer das Konto noch fuer Firma oder Gesamtbudget.
        for s in belastet:
            await db.provider_budget.update_one({"_id": s}, {"$inc": {"n": -1}})
        raise TageslimitErreicht(
            f"Tageslimit für neue Links erreicht ({limit}/Tag {wer}). "
            "Bekannte Links kommen weiter aus dem Speicher; neue Links "
            "bitte morgen erneut. Betreiber: Limit in der .env "
            "(ANBIETER_TAGESLIMIT_*).")

    # Konto-Limit (Entscheidung Ahmad 16.09.2026): zaehlt JEDEN echten
    # Anbieter-Abruf dieses Kontos, auch Kleinanzeigen. Gezaehlt wird immer
    # (Auswertung), gebremst nur bei TAGESLIMIT_JE_KONTO > 0.
    if user_id:
        stand = await _zaehlen(f"{tag}:konto:{user_id}")
        if TAGESLIMIT_JE_KONTO > 0 and stand > TAGESLIMIT_JE_KONTO:
            await _ablehnen(TAGESLIMIT_JE_KONTO, "je Konto")
    if source not in ("mobile", "autoscout24"):
        return belastet
    # Reihenfolge (Runde 5): ZUERST das Firmenlimit, DANN das Gesamtbudget —
    # und bei Ablehnung die Zaehlung zuruecknehmen. Vorher verbrauchte eine
    # Firma, die ihr eigenes Limit laengst ueberschritten hatte, mit jedem
    # abgelehnten Versuch weiter das GESAMTBUDGET aller anderen Firmen.
    gesamt_stand = 0
    for schluessel, limit, wer in ((f"{tag}:firma:{dealer_id or 'ohne'}",
                                    TAGESLIMIT_JE_FIRMA, "je Firma"),
                                   (f"{tag}:gesamt", TAGESLIMIT_GESAMT, "gesamt")):
        stand = await _zaehlen(schluessel)
        if schluessel.endswith(":gesamt"):
            gesamt_stand = stand
        if limit > 0 and stand > limit:
            await _ablehnen(limit, wer)
    await _warnen_wenn_viel(db, tag, gesamt_stand)
    return belastet


async def fetch_listing(db, source: str, item_id: str, url: str,
                        dealer_id: str = "", user_id: str = "") -> Dict[str, Any]:
    """Holt ein Inserat bei der Quelle — oder liefert im Lasttest-Modus
    synthetische Daten mit realistischer Verzoegerung."""
    if MOCK_PROVIDER_FETCH:
        # Lasttest 16.09.2026: Verzoegerung je Quelle einstellbar, damit sich
        # realistische Anbieterzeiten nachstellen lassen (Standard 400 ms).
        ms = os.environ.get(f"MOCK_PROVIDER_DELAY_MS_{source.upper()}") \
            or os.environ.get("MOCK_PROVIDER_DELAY_MS") or "400"
        try:
            await asyncio.sleep(max(0, int(ms)) / 1000)
        except ValueError:
            await asyncio.sleep(0.4)
        return mock_vehicle(item_id)
    belastet = await _budget_pruefen(db, source, dealer_id, user_id)
    from kleinanzeigen_service import ListingGone
    try:
        return await _abrufen(db, source, item_id, url)
    except ListingGone:
        # Befund 130 (16.09.2026): der Anbieter WURDE kontaktiert (Inserat weg/
        # 404) — dieser Abruf zaehlt fuer das Tageslimit. Zurueckgebucht werden
        # nur technische Fehler (Zeitueberschreitung, Token, Guthaben), sonst
        # liessen sich mit toten Links beliebig viele echte Abrufe erzeugen.
        raise
    except AnbieterFehler as exc:
        # Token/Guthaben -> Betriebsalarm (gedrosselt), Text geht 1:1 an
        # den Nutzer (Route: RuntimeError -> 502).
        await _budget_zurueck(db, belastet)          # Runde 19 (Nr. 7)
        await melden(db, exc)
        raise
    except Exception:
        await _budget_zurueck(db, belastet)
        raise


# Audit 13.09.2026 (#33): Der innere Scrape-Slot hatte keinen Herzschlag.
# Ein eigener Abruf dauert mit 3 Versuchen (15 s + 30 s je Phase plus
# Backoff) leicht laenger als PROVIDER_SLOT_TTL (120 s) — dann galt der
# Slot als verwaist, und die Selbstheilung gab den Platz frei, obwohl noch
# gescrapt wurde.
SCRAPE_HERZSCHLAG_SEKUNDEN = 30


async def _scrape_slot_herzschlag(db, slot_id: str) -> None:
    from provider_limiter import extend_slot
    while True:
        try:
            await asyncio.sleep(SCRAPE_HERZSCHLAG_SEKUNDEN)
            await extend_slot(db, slot_id)
        except asyncio.CancelledError:
            raise
        except Exception:  # noqa: BLE001 — naechster Schlag versucht es erneut
            continue


async def _mit_scrape_bremse(db, ueber_api: bool, holen, url: str) -> Dict[str, Any]:
    """Den eigenen HTML-Abruf unter der STRENGEN Kleinanzeigen-Grenze laufen
    lassen (Gegenpruefung 12.09.2026).

    Der Slot fuer den Abruf wird eine Ebene hoeher belegt (listing_identity),
    und zwar nach der Frage "ist ein API-Schluessel hinterlegt?" — also mit
    der lockeren Grenze des bezahlten Dienstes. Genau dann, wenn die API
    ausfaellt und wirklich gescrapt wird, waere Kleinanzeigen dadurch mit dem
    Vierfachen belastet worden. Ist der aeussere Slot aus dem API-Topf, holen
    wir hier zusaetzlich einen Slot aus dem strengen Topf.

    Ohne API-Schluessel liegt der aeussere Slot bereits im strengen Topf —
    dann NICHT noch einmal belegen (das waere eine Selbstblockade)."""
    if not ueber_api or db is None:
        return await holen(url)
    from provider_limiter import acquire_slot, release_slot
    from listing_identity import ListingBusy
    slot = None
    for _versuch in range(100):           # bis ~30 s auf die Bremse warten
        slot = await acquire_slot(db, "kleinanzeigen")
        if slot:
            break
        await asyncio.sleep(0.3)
    if not slot:
        raise ListingBusy(
            "Gerade werden viele Inserate gleichzeitig geladen - "
            "bitte in ein paar Sekunden erneut versuchen.")
    herzschlag = asyncio.create_task(_scrape_slot_herzschlag(db, slot))
    try:
        return await holen(url)
    finally:
        herzschlag.cancel()
        await release_slot(db, slot)


async def _abrufen(db, source: str, item_id: str, url: str) -> Dict[str, Any]:
    if source == "kleinanzeigen":
        from kleinanzeigen_service import fetch_kleinanzeigen_vehicle
        # Wunsch Ahmad 12.09.2026: ZUERST die API von kleinanzeigen-agent.de
        # (gemessen 0,4 s statt mehrerer Sekunden, strukturierte Daten, kein
        # Blockieren durch Kleinanzeigen). Der eigene Abruf bleibt die
        # Notloesung — faellt die API aus, merkt der Nutzer nichts ausser
        # der laengeren Wartezeit.
        import kleinanzeigen_api as _api
        v = None
        ueber_api = _api.api_verfuegbar()
        if ueber_api:
            try:
                v = await _api.hole_inserat(item_id, url)
            except _api.ApiNichtNutzbar as exc:
                log.warning("Kleinanzeigen-API nicht nutzbar (%s) — eigener Abruf fuer %s", exc, item_id)
        if v is None:
            # Gegenpruefung 12.09.2026 (schwerer Befund): Der aeussere
            # Begrenzungs-Slot wurde bereits nach "API vorhanden" gewaehlt
            # (8 gleichzeitig). Faellt die API aus, lief das Abgreifen der
            # Webseite unter dieser lockeren Grenze — also ausgerechnet dann
            # vierfach, wenn NUR noch gescrapt wird. Deshalb hier zusaetzlich
            # die strenge Bremse des Selbst-Abrufs.
            v = await _mit_scrape_bremse(db, ueber_api,
                                         fetch_kleinanzeigen_vehicle, url)
            v["_abrufweg"] = "eigener-abruf"
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
