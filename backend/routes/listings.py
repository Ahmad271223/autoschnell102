"""Listing endpoints: mobile/compare, live-counter, snapshots, vehicles,
listings/extract, listings/resolve."""
import logging
import os
import uuid
from datetime import datetime, timedelta, timezone

from pymongo import ReturnDocument
from pymongo.errors import DuplicateKeyError
from typing import Annotated, Any, Dict, Optional

# Cache-Lebensdauer fuer abgerufene Inserate. Hoehere TTL = weniger echte
# Scrape-/Proxy-Requests (dasselbe Inserat wird nur 1x je TTL geladen).
# Default 24h, per ENV anpassbar.
# Einmal verglichen = dauerhaft gespeichert (Wunsch 08/2026): dieselbe URL
# wird NICHT erneut von Kleinanzeigen/mobile geladen, sondern aus unserem
# Speicher bedient. Default 1 Jahr; per ENV anpassbar.
# Audit 09/2026 (Punkt 39): Inserats-Cache (kann Verkaeuferangaben enthalten)
# hoechstens 90 Tage statt ein Jahr.
LISTING_CACHE_TTL_HOURS = int(os.environ.get("LISTING_CACHE_TTL_HOURS", "2160"))
# Client-Einreichungen (Browser-HTML) sind nur Momentaufnahmen: kurze TTL in
# der Quarantaene, und auch nach unabhaengiger Bestaetigung deutlich kuerzer
# als Server-Abrufe.
CLIENT_INGEST_TTL_HOURS = int(os.environ.get("CLIENT_INGEST_TTL_HOURS", "24"))
CLIENT_CONFIRMED_TTL_HOURS = int(os.environ.get("CLIENT_CONFIRMED_TTL_HOURS", "168"))

from fastapi import (APIRouter, BackgroundTasks, Depends, HTTPException, Query,
                     Request, Response)
from pydantic import BaseModel, Field

from auth import decode_token
from autoscout_service import build_search_url as build_autoscout_url
from autoscout_service import regeln_nicht_abgebildet
from autoscout_service import autoscout_quelle_verfuegbar
from deps import (
    besitzer_anreichern, besitzer_namen, current_firma, eigene_fahrzeug_ids,
    fahrzeug_bereich, fahrzeug_im_bereich, ist_sucher,
    current_user, db, log_activity, now_iso, require_active_sub,
)
from kleinanzeigen_service import (
    ListingGone, fetch_kleinanzeigen_vehicle,
    parse_kleinanzeigen_html, looks_like_kleinanzeigen_listing,
)
from provider_fetch import fetch_listing
from rate_limiter import SlidingWindowRateLimiter
from listing_identity import (
    ListingBusy, ListingIdentityError, get_listing_identity,
    get_or_fetch_listing, peek_cached_listing, set_cache_snapshot,
    store_client_listing,
)

# Client-seitiges Abrufen (nur Kleinanzeigen): ist es an, holt NICHT der
# Server neue Kleinanzeigen-Seiten, sondern der Browser des Nutzers — und
# schickt das HTML an /listings/ingest. Verteilt die Abrufe auf viele IPs
# (kein Server-Block bei Massen-Vergleichen). Default AUS = bisheriges
# Verhalten (Server holt selbst). mobile.de/AutoScout laufen IMMER server-
# seitig (offizielle API, keine Block-Gefahr).
# Client-Abruf (Browser-Erweiterung) — im Lasttest-/CI-Mock-Modus wird er
# grundsaetzlich ignoriert: der Mock existiert, damit KEINE echten Abrufe
# noetig sind; needs_client_fetch wuerde dort jeden Test/Lasttest brechen.
CLIENT_FETCH_KLEINANZEIGEN = os.environ.get(
    "CLIENT_FETCH_KLEINANZEIGEN", "").strip().lower() in ("1", "true", "yes")     and os.environ.get("MOCK_PROVIDER_FETCH", "").strip().lower() not in (
        "1", "true", "yes")
from mobile_service import (
    DEFAULT_EXPORT_RULES, DEFAULT_RULES, MOBILE_PASS, MOBILE_SANDBOX_MODE,
    MOBILE_USER, build_search_url, get_vehicle, mobile_quelle_verfuegbar,
)

log = logging.getLogger("autohandel")

router = APIRouter()


# ---------- Models ----------
# Runde 15 (Nr. 6): Inserats-Adressen sind serverseitig begrenzt — vorher
# lief ein beliebig langer String (bis client_max_body_size) durch Regex,
# Cache-Schluessel und Snapshot-Speicherung. Echte Inserats-URLs haben
# unter 500 Zeichen; 2048 laesst Tracking-Parameter zu.
URL_MAX = 2048


class CompareIn(BaseModel):
    url: str = Field(min_length=1, max_length=URL_MAX)
    # Rueckfall 09/2026: Browser ohne Abruf-Helfer -> Server holt selbst,
    # statt den Nutzer mit "Erweiterung installieren" zu blockieren.
    ohne_erweiterung: bool = False


class ListingURLIn(BaseModel):
    url: str = Field(min_length=1, max_length=URL_MAX)
    ohne_erweiterung: bool = False


# =========================================================
#                  MOBILE.DE COMPARE
# =========================================================
# Rueckfall ohne Browser-Erweiterung (Audit 09/2026): Der Helfer holt
# Kleinanzeigen-Seiten normalerweise ueber den Browser des Nutzers, damit
# die Server-IP nicht auffaellt. Wer den Helfer nicht installiert hat,
# darf trotzdem arbeiten — aber wie oft, entscheidet der SERVER: pro Firma
# und Tag gedeckelt und protokolliert. Vorher genuegte ein Feld in der
# Anfrage ("ohne_erweiterung"), um den Schutz beliebig oft auszuhebeln.
RUECKFALL_TAGESLIMIT = int(os.environ.get("ABRUF_RUECKFALL_TAGESLIMIT", "25"))


async def _rueckfall_erlaubt(gewuenscht: bool, user: dict) -> bool:
    """True = der Server darf dieses eine Mal selbst abrufen."""
    if not gewuenscht:
        return False
    if RUECKFALL_TAGESLIMIT <= 0:
        return False
    tag = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    # Runde 26 (12.09.2026, Vorgabe Ahmad): Das Tageslimit gilt je KONTO, nicht
    # je Firma — sonst verbrauchen 30 Sucher gemeinsam 25 Rueckfaelle und der
    # 26. Link des Tages scheitert fuer alle. Jeder Sucher hat sein eigenes
    # Budget; Limits werden nie von einem Konto auf ein anderes uebertragen.
    schluessel = f"{tag}:rueckfall:{user.get('id') or user.get('dealer_id') or 'ohne'}"
    doc = await db.provider_budget.find_one_and_update(
        {"_id": schluessel},
        {"$inc": {"n": 1},
         "$setOnInsert": {"ablauf": datetime.now(timezone.utc) + timedelta(days=2)}},
        upsert=True, return_document=ReturnDocument.AFTER)
    if doc["n"] > RUECKFALL_TAGESLIMIT:
        await db.provider_budget.update_one({"_id": schluessel}, {"$inc": {"n": -1}})
        log.warning("Rueckfall-Limit erreicht fuer %s", schluessel)
        return False
    return True


async def _fahrzeug_id(source: str, ad_id: str, dealer_id: str) -> str:
    """Runde 17 (Nr. 382/383): Fahrzeug-ID quellen-eindeutig. Vorher war sie
    fuer alle Quellen "v_<Anzeigen-ID>" — ein mobile.de-Inserat und eine
    Kleinanzeige mit derselben Nummer landeten im selben Dokument (Daten
    ueberschrieben, Termine/Vertraege am falschen Auto). Kleinanzeigen
    behaelt das alte Muster (Bestand, Snapshots, Vertraege haengen daran);
    andere Quellen bekommen "v_<quelle>_<id>".

    Legacy-Rueckfall: gibt es fuer eine Nicht-Kleinanzeigen-Quelle noch kein
    Dokument unter der neuen ID, aber eines unter "v_<id>" mit derselben
    mobile_ad_id, dessen quelle fehlt oder passt, wird das alte Dokument
    weiterverwendet (Vertraege/Termine daran bleiben erreichbar)."""
    if source == "kleinanzeigen":
        return f"v_{ad_id}"
    neu = f"v_{source}_{ad_id}"
    if await db.vehicles.count_documents({"id": neu, "dealer_id": dealer_id}, limit=1):
        return neu
    alt = await db.vehicles.find_one(
        {"id": f"v_{ad_id}", "dealer_id": dealer_id, "mobile_ad_id": ad_id},
        {"_id": 0, "quelle": 1})
    if alt is not None and (not alt.get("quelle") or alt.get("quelle") == source):
        return f"v_{ad_id}"
    return neu


# Lebenszyklen, in denen ein erneuter Vergleich die Inseratsdaten (data)
# ueberschreiben darf. Alles andere traegt Korrekturen des Haendlers —
# dort landen frische Daten nur unter inserat_aktuell (Runde 10).
_NEUVERGLEICH_UEBERSCHREIBT = ("verglichen", "gefunden", "storniert", "nicht_abgeholt")


async def _als_mitbearbeiter_eintragen(user: dict, filt: dict, besitzer: str,
                                       seit: Optional[str]) -> dict:
    """Wunsch Ahmad 09.09.2026: Vergleicht ein zweiter Sucher ein Fahrzeug,
    das schon einem Kollegen gehoert, wird er MITBEARBEITER (Fahrzeug
    erscheint in seinem Bereich), Hauptbearbeiter bleibt, wer zuerst
    verglichen hat. Liefert den Kollegen-Hinweis fuer die Antwort.
    Runde 23 (11.09.2026): eigene Funktion, weil auch der verlorene
    gleichzeitige Erstvergleich (Upsert-Zweig) hier landet."""
    await db.vehicles.update_one(filt, {"$addToSet": {"mitbearbeiter_ids": user["id"]}})
    namen = await besitzer_namen(user["dealer_id"], [besitzer])
    return {"user_id": besitzer,
            "name": namen.get(besitzer) or "ein Kollege",
            "seit": seit,
            "mitbearbeiter": True}


async def _fahrzeug_uebernehmen(user: dict, vid: str, ad_id: str,
                                frisch: dict,
                                quelle: Optional[str] = None) -> Optional[dict]:
    """Fahrzeug in den Pool des Kontos uebernehmen (Runde 16).

    Liefert {"user_id", "name", "seit"}, wenn das Fahrzeug bereits einem
    KOLLEGEN derselben Firma gehoert — dann bleibt es unangetastet, der
    Sucher bekommt nur das Vergleichsergebnis. Sonst None. Der Chef darf
    jedes Fahrzeug der Firma aktualisieren (Besitzer bleibt).
    Runde 10: Ist das Fahrzeug schon ueber "verglichen" hinaus (Bestand,
    Verkauf, abgeholt ...), tragen seine Daten Korrekturen des Haendlers —
    die frischen Inseratsdaten landen dann getrennt unter inserat_aktuell.
    Nach einem Seitenausgang (storniert, nicht abgeholt) ist ein erneuter
    Vergleich ein Neuanfang. Altbestand ohne Besitzer uebernimmt, wer ihn
    (wieder) vergleicht.

    Runde 23 (11.09.2026): Lesen und Schreiben sind gegen parallele Zugriffe
    abgesichert (mehrere Worker, zwei Server):
    - Befund 1: Zwei Sucher vergleichen denselben NEUEN Link gleichzeitig.
      Beide lasen "gibt es noch nicht"; nur einer wurde per $setOnInsert
      Hauptbearbeiter, der andere aber NICHT Mitbearbeiter (das geschah nur
      im Zweig "vorhanden"). Jetzt meldet der Upsert den Vorzustand
      (ReturnDocument.BEFORE — "seit" ist wie im Zweig "vorhanden" der Stand
      VOR dem eigenen Schreiben); gehoert das Fahrzeug einem Kollegen, wird
      der aktuelle Sucher Mitbearbeiter. "id"/"dealer_id" stehen nicht mehr
      im $set (kommen beim Einfuegen aus dem Gleichheitsfilter) — sonst kann
      MongoDB einen DuplicateKey des Upserts nicht selbst wiederholen. Ein
      trotzdem gemeldeter DuplicateKeyError fuehrt zu EINEM zweiten
      Durchlauf ab dem Lesen (dann im Zweig "vorhanden", mit CAS).
    - Befund 2: Neue Inseratsdaten werden nur noch geschrieben, solange der
      GELESENE Lebenszyklus noch gilt (CAS wie lifecycle.set_lifecycle).
      Wechselte er dazwischen (Vertrag durch Kollegen, Chef uebernimmt ...),
      bleibt data unangetastet und die frischen Daten landen wie bei
      fortgeschrittenen Lebenszyklen unter inserat_aktuell. updated_at ist
      bewusst NICHT Teil der Bedingung (parallele Vergleiche setzen es
      staendig).
    - Ist das Fahrzeug zwischen Lesen und Schreiben ganz verschwunden (z. B.
      Pool-Begrenzung), wird es wie beim Erstvergleich neu angelegt."""
    dealer_id = user["dealer_id"]
    filt = {"id": vid, "dealer_id": dealer_id}
    # Runde 17 (Nr. 383): Quelle am Fahrzeug festhalten (auch am Altbestand
    # beim naechsten Vergleich) — Grundlage fuer den Legacy-Rueckfall.
    quelle_set = {"quelle": quelle} if quelle else {}
    kollege = None
    # Runde 27 (Gegenpruefung): DREI Versuche — der Fall 'Upsert traf ein
    # vorhandenes Dokument' schickt uns noch einmal durch den Zweig
    # 'vorhanden' und verbraucht dabei einen Durchlauf.
    for versuch in range(3):
        kollege = None
        vorhanden = await db.vehicles.find_one(
            filt, {"_id": 0, "lifecycle": 1, "owner_user_id": 1, "updated_at": 1})
        if vorhanden is not None:
            besitzer = vorhanden.get("owner_user_id")
            if ist_sucher(user) and besitzer and besitzer != user["id"]:
                # Die Inseratsdaten werden wie bei jedem Vergleich
                # aktualisiert (nur solange das Fahrzeug noch "verglichen"
                # ist, siehe unten).
                kollege = await _als_mitbearbeiter_eintragen(
                    user, filt, besitzer, vorhanden.get("updated_at"))
            daten_geschrieben = False
            if (vorhanden.get("lifecycle") or "verglichen") in _NEUVERGLEICH_UEBERSCHREIBT:
                # Runde 23 (Befund 2): CAS auf den gelesenen Lebenszyklus;
                # Altdokumente ohne Feld: es darf nicht inzwischen entstanden sein.
                cas = {**filt, "lifecycle": vorhanden["lifecycle"]
                       if "lifecycle" in vorhanden else {"$exists": False}}
                r = await db.vehicles.update_one(cas, {"$set": {
                    "mobile_ad_id": ad_id, "data": frisch,
                    "updated_at": now_iso(), **quelle_set}})
                daten_geschrieben = r.matched_count > 0
            if not daten_geschrieben:
                r = await db.vehicles.update_one(
                    filt,
                    {"$set": {"inserat_aktuell": frisch, "inserat_aktuell_am": now_iso(),
                              "updated_at": now_iso(), **quelle_set}})
                if r.matched_count == 0:
                    # Zwischenzeitlich entfernt — einmal wie ein Erstvergleich.
                    kollege = None
                    if versuch < 2:
                        continue
                    break
            if not besitzer:
                await db.vehicles.update_one(
                    {**filt, "owner_user_id": None},
                    {"$set": {"owner_user_id": user["id"]}})
            break
        # Erstvergleich: atomar anlegen oder — wenn ein Kollege gleichzeitig
        # schneller war — dessen Dokument aktualisieren (Befund 1).
        try:
            # Runde 27 (12.09.2026, Pruefbefund): NICHTS mehr im $set. Gab es
            # das Fahrzeug schon (Kollege war im selben Moment schneller und
            # ist womoeglich schon weiter — Vertrag, Termin), wuerde ein $set
            # dessen gemeinsame Inseratsdaten ueberschreiben. Beim Einfuegen
            # liefert $setOnInsert alles; existiert es bereits, schreibt der
            # Aufruf nichts und der zweite Durchlauf geht durch den Zweig
            # "vorhanden" (CAS auf den Lebenszyklus + Mitbearbeiter).
            vorher = await db.vehicles.find_one_and_update(
                filt,
                {"$setOnInsert": {"mobile_ad_id": ad_id, "data": frisch,
                                  "updated_at": now_iso(), **quelle_set,
                                  "created_at": now_iso(), "status": "verglichen",
                                  "lifecycle": "verglichen", "source": "plattform",
                                  "lifecycle_changed_at": now_iso(),
                                  "owner_user_id": user["id"]}},
                projection={"_id": 0, "owner_user_id": 1, "updated_at": 1},
                upsert=True, return_document=ReturnDocument.BEFORE)
        except DuplicateKeyError:
            if versuch < 2:
                continue
            raise
        if vorher is not None:
            # Das Dokument gab es schon (paralleler Erstvergleich): noch einmal
            # von vorn — diesmal ueber den Zweig "vorhanden", der die frischen
            # Daten nur mit CAS schreibt und sonst unter inserat_aktuell ablegt.
            if versuch < 2:
                continue
            # Letzter Versuch (drei Rennen hintereinander): wenigstens
            # Herkunft, Zeitstempel und die frischen Daten als
            # inserat_aktuell festhalten — sonst bliebe das Fahrzeug
            # voellig unberuehrt (Gegenpruefung 12.09.2026).
            await db.vehicles.update_one(
                filt,
                {"$set": {"inserat_aktuell": frisch,
                          "inserat_aktuell_am": now_iso(),
                          "updated_at": now_iso(), **quelle_set}})
            besitzer = vorher.get("owner_user_id")
            if not besitzer:
                await db.vehicles.update_one(
                    {**filt, "owner_user_id": None},
                    {"$set": {"owner_user_id": user["id"]}})
            elif ist_sucher(user) and besitzer != user["id"]:
                kollege = await _als_mitbearbeiter_eintragen(
                    user, filt, besitzer, vorher.get("updated_at"))
        break
    # Fahrzeugpool je Konto auf die neuesten 30 Vergleiche begrenzen
    try:
        from fahrzeugpool import fahrzeugpool_trimmen
        entfernt = await fahrzeugpool_trimmen(db, dealer_id, owner_user_id=user["id"])
        if entfernt:
            log.info("Fahrzeugpool %s/%s: %d alte Vergleiche entfernt",
                     dealer_id, user["id"], entfernt)
    except Exception:
        log.exception("Fahrzeugpool-Begrenzung fehlgeschlagen")
    return kollege


@router.post("/mobile/compare")
async def compare(body: CompareIn, background: BackgroundTasks,
                  user=Depends(require_active_sub)):
    raw_url = (body.url or "").strip()

    # Unified cache key = f"{source}:{item_id}". Dadurch werden
    # Kleinanzeigen-/mobile.de-/AutoScout-URLs innerhalb der TTL nur EINMAL
    # wirklich vom jeweiligen Anbieter gezogen — egal mit welchen Tracking-
    # oder Such-Parametern die URL daherkommt.
    try:
        identity = get_listing_identity(raw_url)
    except ListingIdentityError as exc:
        raise HTTPException(
            400,
            str(exc) or "Keine gültige mobile.de- oder kleinanzeigen.de-Fahrzeug-URL erkannt.",
        )

    source = identity["source"]
    item_id = identity["item_id"]

    if source == "autoscout24" and not autoscout_quelle_verfuegbar():
        raise HTTPException(
            400,
            "AutoScout24-Links sind noch nicht freigeschaltet (kein Zugang "
            "hinterlegt). Bitte aktuell einen kleinanzeigen.de-Link verwenden.",
        )
    # mobile.de als QUELLE braucht offiziellen API-Zugang ODER den
    # Apify-Scraper (APIFY_TOKEN). Ohne beides (und ohne ausdrücklichen
    # Sandbox-Modus) früh und verständlich abbrechen — statt tief im
    # Fetcher mit einer technischen Meldung.
    if source == "mobile" and not mobile_quelle_verfuegbar():
        raise HTTPException(
            400,
            "mobile.de-Links sind noch nicht freigeschaltet (kein Zugang "
            "hinterlegt). Bitte aktuell einen kleinanzeigen.de-Link verwenden.",
        )

    # CLIENT-SEITIGES ABRUFEN (nur Kleinanzeigen): Ist der Modus an und der
    # Link weder global noch in der EIGENEN Quarantaene vorhanden, holt
    # nicht der Server — der Browser des Nutzers wird gebeten, die Seite zu
    # holen und per /listings/ingest zu schicken. Danach ruft das Frontend
    # compare erneut auf -> Treffer (global oder eigene Quarantaene).
    client_hit = None
    if source == "kleinanzeigen" and CLIENT_FETCH_KLEINANZEIGEN:
        # Nachpruefung Runde 14 (Nr. 86): ERST den Cache pruefen, DANN den
        # Rueckfall zaehlen. Vorher zaehlte _rueckfall_erlaubt sofort ($inc),
        # auch wenn das Inserat laengst im Cache lag und gar kein Abruf
        # noetig war — jeder Cache-Treffer frass einen der 25 Firmenabrufe
        # (das Frontend sendet ohne_erweiterung=true, sobald der Helfer
        # fehlt). /listings/check macht es seit jeher in dieser Reihenfolge.
        client_hit = await peek_cached_listing(
            db, raw_url, dealer_id=user.get("dealer_id"))
        if client_hit is None and not await _rueckfall_erlaubt(
                body.ohne_erweiterung, user):
            return {
                "needs_client_fetch": True,
                "url": raw_url,
                "source": "kleinanzeigen",
                "hint": "Bitte über die Browser-Erweiterung laden.",
            }

    async def _fetcher(src: str, iid: str, url: str) -> dict:
        """Wird nur bei Cache-MISS aufgerufen."""
        return await fetch_listing(db, src, iid, url,
                                   dealer_id=user.get("dealer_id") or "")

    try:
        if client_hit is not None:
            # Daten aus der (eigenen) Quarantaene bzw. dem freigegebenen
            # Client-Cache — kein Server-Abruf im Client-Fetch-Modus.
            vehicle, was_cached, cached_snapshot_id = (
                dict(client_hit[0]), True, client_hit[1])
        else:
            vehicle, was_cached, cached_snapshot_id = await get_or_fetch_listing(
                db, raw_url, _fetcher, ttl_hours=LISTING_CACHE_TTL_HOURS,
            )
    except ListingIdentityError as exc:
        raise HTTPException(400, str(exc))
    except ListingGone as exc:
        raise HTTPException(404, str(exc))
    except ListingBusy as exc:
        # 503 + Retry-After: das Frontend (und jeder Proxy) weiss, dass es
        # sich um vorübergehendes Warten handelt — kein Server-Fehler.
        raise HTTPException(503, str(exc), headers={"Retry-After": "5"})
    except RuntimeError as exc:
        raise HTTPException(502, str(exc))
    except Exception as exc:
        log.exception("compare fetch failed for %s", raw_url)
        if source == "kleinanzeigen":
            raise HTTPException(500, "Fahrzeugdaten konnten nicht geladen werden (Kleinanzeigen).")
        raise HTTPException(500, "Fahrzeugdaten konnten nicht geladen werden.")

    ad_id = vehicle.get("mobile_ad_id") or item_id
    vehicle["mobile_ad_id"] = ad_id

    from deps import effective_dealer
    dealer = await effective_dealer(user)
    active = (dealer or {}).get("active_profile", "inland")
    from regeln import regeln_lesen
    # Nachpruefung Runde 10: Lesepfad heilt Alt-Dokumente (z.B. damage als
    # String) statt mit 500 abzubrechen — der Schreibpfad bleibt streng.
    if active == "export":
        rules = regeln_lesen((dealer or {}).get("export_rules"), DEFAULT_EXPORT_RULES)
    else:
        rules = regeln_lesen((dealer or {}).get("comparison_rules"), DEFAULT_RULES)
    search_url = build_search_url(vehicle, rules)
    autoscout_url = build_autoscout_url(vehicle, rules)

    # Track comparison (anonym)
    expires_at = datetime.now(timezone.utc) + timedelta(days=14)
    # Runde 27 (Pruefbefund 12.09.2026): Ein technischer Neulauf ist keine
    # neue Nachfrage — Profilwechsel Inland/Export, Doppelklick oder eine
    # Wiederholung nach Zeitueberschreitung starteten denselben Vergleich
    # erneut und blaehten den LIVE-Zaehler auf. Solche Eintraege bleiben
    # erhalten (Beweiszugriff, Chef-Statistik), zaehlen aber nicht mit.
    _seit = (datetime.now(timezone.utc)
             - timedelta(minutes=LIVE_FENSTER_MIN)).isoformat()
    _wiederholung = await db.vehicle_comparisons.count_documents(
        {"cache_key": identity["cache_key"], "user_id": user["id"],
         "created_at": {"$gte": _seit}}, limit=1) > 0
    await db.vehicle_comparisons.insert_one({
        "id": str(uuid.uuid4()),
        "mobile_ad_id": ad_id,
        "source": source,
        "wiederholung": _wiederholung,
        # Beweisdokument: Zugriff fuer Firmen, die das Inserat verglichen haben
        "cache_key": identity["cache_key"],
        "cached": was_cached,
        "dealer_id": user["dealer_id"],
        "user_id": user["id"],
        "created_at": now_iso(),
        "expires_at_dt": expires_at,
    })

    # Persist vehicle for re-use (PDF, Termine). Runde 17 (Nr. 382): ID je
    # Quelle eindeutig (Legacy-Rueckfall in _fahrzeug_id); die tatsaechlich
    # verwendete ID geht in Antwort und Snapshot.
    vid = await _fahrzeug_id(source, ad_id, user["dealer_id"])
    frisch = {k: v for k, v in vehicle.items() if not k.startswith("_")}
    kollege = await _fahrzeug_uebernehmen(user, vid, ad_id, frisch, quelle=source)
    # Beweisdokument: das Fahrzeug kennt sein Inserat (bei AutoScout24 weicht
    # die Anzeigen-ID von der ID in der Adresse ab — deshalb der cache_key).
    await db.vehicles.update_one(
        {"id": vid, "dealer_id": user["dealer_id"]},
        {"$set": {"inserat_schluessel": identity["cache_key"]}})
    await log_activity(user["dealer_id"], user["id"], "vergleich.gestartet", ref=ad_id,
                       meta={"kollege": kollege["user_id"]} if kollege else None)

    # Beweisdokument (ersetzt die Snapshots, 10.09.2026): EIN PDF je Inserat,
    # beim ersten Gebrauch (egal welche Firma), fuer alle geteilt. Meist hat
    # der Abruf-Zweig es schon vorgemerkt; hier idempotent nachziehen (auch
    # fuer Cache-Eintraege von vor der Umstellung). Nicht aus ungepruefter
    # Browser-Lieferung (Kleinanzeigen-Erweiterung, Quarantaene).
    beweis = None
    if client_hit is None:
        from beweis_service import beweis_vormerken
        beweis = await beweis_vormerken(
            db, cache_key=identity["cache_key"], quelle=source,
            item_id=identity["item_id"], url=raw_url, anlass="vergleich")

    hinweise = regeln_nicht_abgebildet(vehicle, rules)
    # Runde 29 (12.09.2026, Regel Ahmad): Ein Sucher erfaehrt NICHT, welcher
    # Kollege dasselbe Auto bearbeitet — weder den Namen noch die Konto-ID.
    # Wie gefragt das Auto ist, sagt ihm der anonyme LIVE-Zaehler. Der Chef
    # sieht die Namen weiterhin (er soll wissen, wer woran arbeitet).
    if ist_sucher(user):
        kollege = None
    if kollege:
        # Runde 26 (12.09.2026): Seit dem Umbau auf Kaufvorgaenge hat JEDER
        # Vertrag seinen eigenen Abholtermin — der alte Hinweis sagte das
        # Gegenteil und haette die Sucher in der Schulung verwirrt.
        hinweise = [f"Dieses Fahrzeug vergleicht auch {kollege['name']} — ihr "
                    "arbeitet unabhängig voneinander: Jeder kann einen eigenen "
                    "Kaufvertrag mit eigenem Abholtermin anlegen."] + list(hinweise)
    # Befund 10.09.2026: Vorschaubilder ueber den eigenen Bild-Proxy (klein,
    # zwischengespeichert, kein Fremdhost im Browser). Nur in der Antwort,
    # nie im gespeicherten Fahrzeug (die Links laufen ab).
    from bild_proxy import thumbs as _thumbs
    _bilder = (vehicle or {}).get("images") or (vehicle or {}).get("image_urls") or []
    return {
        "vehicle_id": vid,
        "ad_id": ad_id,
        "vehicle": {**(vehicle or {}), "images_thumbs": _thumbs(_bilder[:40])},
        "search_url": search_url,
        "autoscout_url": autoscout_url,
        # Runde 11: welche Firmenregeln der AutoScout-Link nicht umsetzt
        "hinweise": hinweise,
        # Runde 16: gehoert das Fahrzeug einem Kollegen, bleibt es dort —
        # das Frontend blendet Vertrag/Snapshot aus und zeigt den Hinweis.
        "kollege": kollege,
        "rules_applied": rules,
        "active_profile": active,
        "source": source,
        "cached": was_cached,
        "cache_key": identity["cache_key"],
        "beweis": beweis,
    }


class IngestIn(BaseModel):
    url: str = Field(min_length=1, max_length=URL_MAX)
    html: str = Field(min_length=500, max_length=6_000_000)


# Runde 17 (Nr. 292): je Konto hoechstens 20 HTML-Einreichungen pro Minute —
# vorher liess sich der CPU-teure Parser (bis 6 MB je Aufruf) unbegrenzt
# anstossen. Fester Name = ein Zaehler ueber alle Worker (rate_limiter).
_ingest_limiter = SlidingWindowRateLimiter(max_attempts=20, window_seconds=60,
                                           name="ingest")


@router.post("/listings/ingest")
async def ingest_client_html(body: IngestIn, user=Depends(require_active_sub)):
    """Nimmt vom BROWSER DES NUTZERS geladenes Kleinanzeigen-HTML entgegen,
    wertet es aus und legt die Daten in den Speicher — danach ruft das
    Frontend /mobile/compare erneut auf (dann Cache-Treffer, alles Weitere
    wie gewohnt). So holt der Server neue Kleinanzeigen-Seiten NICHT selbst.

    Sicherheit: nur Kleinanzeigen-URLs; HTML wird auf Plausibilität geprüft;
    ist der Link schon im Speicher, wird NICHTS überschrieben (first-wins).
    """
    raw_url = (body.url or "").strip()
    try:
        identity = get_listing_identity(raw_url)
    except ListingIdentityError as exc:
        raise HTTPException(400, str(exc) or "Ungültige URL.")
    if identity["source"] != "kleinanzeigen":
        raise HTTPException(400, "Client-Abruf ist nur für Kleinanzeigen vorgesehen.")
    if not await _ingest_limiter.check(str(user.get("id") or "")):
        raise HTTPException(429, "Zu viele Einreichungen — bitte eine Minute warten")

    # Schon global freigegeben ODER eigene frische Einreichung vorhanden?
    # Dann nichts tun.
    if await peek_cached_listing(db, raw_url,
                                 dealer_id=user.get("dealer_id")) is not None:
        return {"ok": True, "already_cached": True}

    import asyncio as _asyncio
    if not await _asyncio.to_thread(looks_like_kleinanzeigen_listing,
                                    body.html, url=raw_url):
        raise HTTPException(422, "Das übermittelte HTML sieht nicht nach einer "
                                 "Kleinanzeigen-Fahrzeugseite aus.")
    try:
        # CPU-Parse (BS4/lxml, bis 6 MB Client-HTML) nie im Loop.
        parsed = await _asyncio.to_thread(parse_kleinanzeigen_html,
                                          raw_url, body.html)
    except ListingGone as exc:
        raise HTTPException(404, str(exc))
    except Exception:
        log.exception("ingest parse failed for %s", raw_url)
        raise HTTPException(422, "Die Seite konnte nicht ausgewertet werden.")

    # Pflichtfelder: Titel, Preis UND Marke muessen vorhanden sein — eine
    # "leere" Seite deutet auf manipuliertes HTML hin.
    if not (parsed.get("title") or "").strip() or not parsed.get("list_price"):
        raise HTTPException(422, "Das HTML enthält keine verwertbaren "
                                 "Fahrzeugdaten (Titel/Preis fehlen).")
    if not (parsed.get("make_id") or (parsed.get("make_label") or "").strip()):
        raise HTTPException(422, "Das HTML enthält keine erkennbare "
                                 "Fahrzeugmarke.")

    iid = identity["item_id"]
    parsed["mobile_ad_id"] = parsed.get("kleinanzeigen_id") or iid
    parsed.setdefault("kleinanzeigen_id", iid)
    # Nachvollziehbarkeit: WER hat diese Daten geliefert? (Kein
    # Server-Abruf — bei Missbrauch laesst sich der Nutzer sperren.)
    parsed["ingested_by_user"] = user.get("id") or user.get("email") or ""
    parsed["ingested_by_dealer"] = user.get("dealer_id") or ""

    # QUARANTAENE statt globalem Speicher: die Daten sind zunaechst nur
    # fuer den einreichenden Haendler sichtbar. Erst wenn ein ZWEITER,
    # unabhaengiger Haendler dieselben Kerndaten einreicht, wird das
    # Inserat global freigegeben — gefaelschtes HTML eines Einzelnen
    # erreicht damit nie die anderen Haendler.
    try:
        status = await store_client_listing(
            db, raw_url, parsed, user.get("dealer_id") or "",
            ttl_hours=CLIENT_INGEST_TTL_HOURS,
            confirmed_ttl_hours=CLIENT_CONFIRMED_TTL_HOURS)
    except Exception:
        log.exception("ingest cache write failed for %s", raw_url)
        raise HTTPException(500, "Konnte die Daten nicht speichern.")
    return {"ok": True, "already_cached": False, "status": status}


# =========================================================
#        LINKPRUEFUNG ALS HINTERGRUNDJOB (Phase 08/2026)
# =========================================================
@router.post("/listings/check")
async def listings_check(body: ListingURLIn, user=Depends(require_active_sub)):
    """Schneller Vorab-Check beim Einfuegen eines Links.

    - Inserat bekannt (Cache oder eigene Quarantaene): sofort
      {"status": "completed"} — das Frontend ruft direkt /mobile/compare.
    - Client-Fetch-Modus und unbekannt: {"status": "needs_client_fetch"}
      (Erweiterungs-Weg bleibt unveraendert).
    - Sonst unbekannt: idempotenter Hintergrundjob, sofortige Antwort mit
      job_id. Fuer dasselbe Inserat bekommen ALLE Wartenden dieselbe
      Job-ID; es laeuft hoechstens ein Anbieter-Abruf.
    """
    raw_url = (body.url or "").strip()
    try:
        identity = get_listing_identity(raw_url)
    except ListingIdentityError as exc:
        raise HTTPException(400, str(exc) or "Ungültige URL.")
    source = identity["source"]
    if source == "autoscout24" and not autoscout_quelle_verfuegbar():
        raise HTTPException(400, "AutoScout24-Links sind noch nicht "
                                 "freigeschaltet (kein Zugang hinterlegt).")
    if source == "mobile" and not mobile_quelle_verfuegbar():
        raise HTTPException(400, "mobile.de-Links sind noch nicht "
                                 "freigeschaltet (kein Zugang hinterlegt).")

    if await peek_cached_listing(db, raw_url,
                                 dealer_id=user.get("dealer_id")) is not None:
        return {"status": "completed", "cached": True,
                "source": source, "item_id": identity["item_id"]}

    if (source == "kleinanzeigen" and CLIENT_FETCH_KLEINANZEIGEN
            and not await _rueckfall_erlaubt(body.ohne_erweiterung, user)):
        return {"status": "needs_client_fetch", "url": raw_url,
                "source": source,
                "hint": "Bitte über die Browser-Erweiterung laden."}

    from link_jobs import enqueue_job, process_one_now, WarteschlangeVoll
    try:
        # Runde 28: Der Job gehoert auch dem KONTO — dadurch bedient der
        # Worker die Sucher reihum, und ein einzelnes Konto kann die
        # Warteschlange nicht mehr fuer alle fuellen.
        job = await enqueue_job(db, raw_url,
                                dealer_id=user.get("dealer_id") or "",
                                user_id=user.get("id") or "")
    except WarteschlangeVoll as voll:
        raise HTTPException(429, voll.text)
    if job.get("status") == "queued":
        # Sofort-Anstoss (begrenzt/dedupliziert, Audit 09/2026 Punkt 17)
        from link_jobs import anstossen
        anstossen(db)
    return {"status": job["status"], "job_id": job["id"],
            "source": source, "item_id": identity["item_id"]}


_status_limiter = SlidingWindowRateLimiter(max_attempts=40, window_seconds=10)


@router.get("/listings/check/{job_id}")
async def listings_check_status(job_id: str, user=Depends(require_active_sub)):
    """Status eines Linkpruefungs-Jobs: queued | processing | completed |
    failed. Bei completed liegt das Inserat im Cache — /mobile/compare
    liefert dann sofort.

    Runde 15 (Nr. 7): dieselbe Bezahlschranke wie beim Anlegen des Jobs
    (require_active_sub) — vorher konnte ein abgelaufenes Abo weiter pollen.

    Long-Poll mit Backoff (Audit 09/2026, Punkt 16): statt ~20 DB-Lesungen
    in 2,4 s jetzt hoechstens 6 (0,15 / 0,3 / 0,5 / 0,7 / 0,75 s), dazu ein
    Limit je Konto. Mandantenbindung (Punkt 33): nur Firmen, die diesen
    Link eingereicht haben, sehen den Status; sonst 404."""
    import asyncio as _aio
    from link_jobs import get_job
    if not await _status_limiter.check(f"status:{user.get('id')}"):
        raise HTTPException(429, "Zu viele Statusabfragen — bitte kurz warten")
    pausen = (0.15, 0.3, 0.5, 0.7, 0.75)
    schritt = 0
    while True:
        job = await get_job(db, job_id)
        if not job:
            raise HTTPException(404, "Job nicht gefunden (evtl. abgelaufen)")
        eigene = user.get("dealer_id")
        if eigene not in (job.get("dealer_ids") or []) \
                and job.get("requested_by_dealer") != eigene:
            raise HTTPException(404, "Job nicht gefunden (evtl. abgelaufen)")
        # Runde 28: Ein Sucher sieht nur Jobs, die er selbst eingereicht hat
        # — Status und Fehler eines Kollegen gehen ihn nichts an. Altjobs
        # ohne Konto-Angabe bleiben fuer die Firma sichtbar.
        konten = job.get("user_ids") or []
        if (ist_sucher(user) and konten
                and user.get("id") not in konten
                and job.get("requested_by_user") != user.get("id")):
            raise HTTPException(404, "Job nicht gefunden (evtl. abgelaufen)")
        if job["status"] in ("completed", "failed") or schritt >= len(pausen):
            break
        await _aio.sleep(pausen[schritt])
        schritt += 1
    out = {"status": job["status"], "job_id": job["id"],
           "source": job.get("source"), "item_id": job.get("item_id"),
           "error": job.get("error")}
    if job["status"] == "queued":
        # Nachpruefung Runde 14 (Nr. 11): nur die EIGENEN wartenden Jobs
        # zaehlen. Die globale Position verriet die Plattformauslastung
        # (fremde Firmen), das Frontend wertet den Wert ohnehin nicht aus.
        # Runde 28: die eigenen wartenden Jobs — fuer Sucher je Konto.
        vor_filter = {"status": "queued",
                      "created_at": {"$lt": job["created_at"]}}
        if ist_sucher(user):
            vor_filter["user_ids"] = user.get("id")
        else:
            vor_filter["dealer_ids"] = eigene
        out["vor_dir"] = await db.link_jobs.count_documents(vor_filter)
    return out


# Runde 27 (12.09.2026, Beschluss Ahmad): Der LIVE-Zaehler ist ein rein
# ANONYMES Nachfrage-Signal zum Inserat. Er gehoert weder zu einem Konto
# noch zu einer Firma — jeder Sucher jeder Firma sieht dieselbe Zahl.
LIVE_FENSTER_MIN = 10


@router.get("/mobile/live-counter/{ad_id}")
async def live_counter(ad_id: str, quelle: Optional[str] = None,
                       user=Depends(current_firma)):
    """Wie oft wurde dieses INSERAT zuletzt verglichen (anonym)?

    Zwei Befunde vom 12.09.2026 sind hier behoben:
      * Die eigene Firma wurde aus 'active_now' herausgerechnet, aus
        'today' aber nicht — zwei Sucher derselben Firma sahen oben 0 und
        unten 8. Jetzt zaehlen beide Werte ALLE Firmen.
      * Gezaehlt wurde nur nach "mobile_ad_id": ein mobile.de-Inserat und
        eine Kleinanzeige mit derselben Nummer fielen zusammen. Mit
        ?quelle=... zaehlt der Schluessel 'quelle:id' (cache_key).

    Geliefert werden Vergleichsvorgaenge, nicht Personen — die Oberflaeche
    schreibt deshalb "Vergleiche", nicht "Haendler"."""
    seit = (datetime.now(timezone.utc)
            - timedelta(minutes=LIVE_FENSTER_MIN)).isoformat()
    heute = datetime.now(timezone.utc).replace(
        hour=0, minute=0, second=0, microsecond=0).isoformat()
    basis = ({"cache_key": f"{quelle}:{ad_id}"} if quelle
             else {"mobile_ad_id": ad_id})
    # Wiederholungen desselben Kontos zaehlen nicht als neue Nachfrage.
    basis["wiederholung"] = {"$ne": True}
    aktuell = await db.vehicle_comparisons.count_documents({
        **basis, "created_at": {"$gte": seit}})
    heute_gesamt = await db.vehicle_comparisons.count_documents({
        **basis, "created_at": {"$gte": heute}})
    return {"active_now": aktuell, "today": heute_gesamt,
            "fenster_minuten": LIVE_FENSTER_MIN}


# =========================================================
#   LISTING SNAPSHOTS — Altbestand, nur noch lesen (seit 10.09.2026
#   entstehen Beweisdokumente: routes/beweise.py)
# =========================================================
async def _load_snapshot_or_404(snap_id: str, user: Optional[dict] = None) -> dict:
    """Snapshot laden — NUR fuer die Firma, die das Inserat selbst fuehrt
    (Vergleich/Fahrzeugpool/Vertrag) oder fuer Admins (Runde 5). Vorher
    reichte "angemeldet" — eine bekannte Snapshot-ID lieferte fremde PDFs."""
    snap = await db.listing_snapshots.find_one(
        {"id": snap_id}, {"_id": 0},
    )
    if not snap:
        raise HTTPException(404, "Snapshot nicht gefunden")
    if user is not None and user.get("role") != "admin":
        dealer_id = user.get("dealer_id")
        if ist_sucher(user):
            # Runde 16: Sucher nur im eigenen Bereich — selbst erzeugt,
            # eigenes Fahrzeug oder eigener Vertrag zum Fahrzeug.
            # Runde 17 (Uebergabe-Regel): das Fahrzeug ist der Anker — nach
            # einer Uebergabe verliert der bisherige Bearbeiter auch seine
            # selbst erzeugten Snapshots; ohne Fahrzeug zaehlt der Ersteller.
            # Umbau Kaufvorgaenge: der Kleinanzeigen-Snapshot gehoert zum
            # INSERAT — wer es verglichen hat (Bereich) oder einen eigenen
            # Vertrag dazu besitzt, darf ihn sehen.
            vid = snap.get("vehicle_id")
            erlaubt = snap.get("user_id") == user["id"] or (
                bool(vid) and (
                    await fahrzeug_im_bereich(user, vid)
                    or await db.generated_pdfs.count_documents(
                        {"vehicle_id": vid, "dealer_id": dealer_id,
                         "user_id": user["id"]}, limit=1) > 0))
        else:
            erlaubt = bool(dealer_id) and (
                snap.get("dealer_id") == dealer_id
                or await db.vehicles.count_documents(
                    {"id": snap.get("vehicle_id"), "dealer_id": dealer_id}) > 0
                or await db.generated_pdfs.count_documents(
                    {"vehicle_id": snap.get("vehicle_id"), "dealer_id": dealer_id}) > 0)
        if not erlaubt:
            raise HTTPException(404, "Snapshot nicht gefunden")
    return snap


@router.get("/snapshots/{snap_id}")
async def snapshot_status(snap_id: str, user=Depends(current_user)):
    snap = await _load_snapshot_or_404(snap_id, user)
    snap.pop("png_path", None)
    snap.pop("pdf_path", None)
    # Nachpruefung Runde 14 (Nr. 10): Snapshots sind je Inserat geteilt
    # (v_<Anzeigen-ID> bei mehreren Firmen identisch). Eine FREMDE Firma,
    # die dasselbe Fahrzeug fuehrt, darf den Stand sehen — aber nicht, WER
    # (dealer_id/user_id) den Snapshot erzeugt hat. Deshalb fuer fremde
    # Firmen nur die Sachfelder (BeweisCard, Alt-Anzeige, braucht status/error/
    # completed_at); die eigene Firma und Admins sehen wie bisher alles.
    if user.get("role") != "admin" and snap.get("dealer_id") != user.get("dealer_id"):
        felder = ("id", "vehicle_id", "mobile_ad_id", "source_url", "status",
                  "art", "error", "created_at", "completed_at",
                  "png_bytes", "pdf_bytes")
        snap = {k: snap.get(k) for k in felder if k in snap}
    return snap


@router.get("/snapshots/{snap_id}/{kind}")
async def snapshot_download(snap_id: str, kind: str,
                            user=Depends(current_user)):
    """Stream the captured PDF or PNG.

    Nur `Authorization: Bearer …` — ?auth=<token> wird seit 08/2026 nicht
    mehr akzeptiert (Token stand sonst in Browser-Verlauf und Logs); das
    Frontend laedt die Datei per fetch und zeigt eine Blob-URL an.
    Die Anmeldepruefung uebernimmt bewusst current_user: die frueher hier
    handgebaute Kette (Token, Konto aktiv, Einzel-Sitzung) war bereits von
    der zentralen Version abgewichen."""
    if kind not in ("pdf", "png"):
        raise HTTPException(400, "kind muss 'pdf' oder 'png' sein")

    snap = await _load_snapshot_or_404(snap_id, user)
    if snap.get("status") != "ready":
        raise HTTPException(409, f"Snapshot ist nicht bereit (status={snap.get('status')})")
    path = snap.get(f"{kind}_path")
    if not path:
        raise HTTPException(404, f"Kein {kind.upper()}-Artefakt für diesen Snapshot")
    try:
        from snapshot_service import get_object_async
        data, ctype = await get_object_async(path)
    except Exception as exc:
        log.exception("snapshot fetch failed")
        raise HTTPException(502, "Snapshot-Storage nicht erreichbar.")
    return Response(content=data, media_type=ctype)


@router.get("/snapshots")
async def list_snapshots(vehicle_id: Optional[str] = None,
                         user=Depends(current_firma),
                         limit: Annotated[int, Query(ge=1, le=500)] = 200,
                         before: Optional[str] = None,
                         response: Response = None):
    """Runde 17 (Nr. 324): statt stiller Kappung bei 200 jetzt ein Cursor
    ueber created_at (`before` = created_at des letzten Eintrags der vorigen
    Seite). Gibt es mehr als `limit`, meldet der Header X-Truncated: 1 und
    X-Next-Before den Cursor fuer die naechste Seite; die Antwort bleibt
    eine Liste (Frontend-kompatibel)."""
    q: Dict[str, Any] = {"dealer_id": user["dealer_id"]}
    if ist_sucher(user):
        # Runde 16: nur eigene Snapshots bzw. die zu eigenen Fahrzeugen und
        # eigenen Vertraegen — vorher sah ein Sucher alle Beweise der Firma
        # samt user_id der Kollegen.
        # Runde 17 (Uebergabe-Regel): nur Fahrzeuge im eigenen Bereich; ohne
        # Fahrzeug zaehlt der Ersteller.
        vids = set(await eigene_fahrzeug_ids(user) or [])
        vids |= set(await db.generated_pdfs.distinct(
            "vehicle_id", {"dealer_id": user["dealer_id"], "user_id": user["id"]}))
        q["$or"] = [{"vehicle_id": {"$in": list(vids)}}, {"user_id": user["id"]}]
    if vehicle_id:
        q["vehicle_id"] = vehicle_id
    if before:
        q["created_at"] = {"$lt": before}
    items = await db.listing_snapshots.find(q, {"_id": 0, "png_path": 0, "pdf_path": 0}) \
        .sort("created_at", -1).to_list(limit + 1)
    if len(items) > limit:
        items = items[:limit]
        if response is not None:
            response.headers["X-Truncated"] = "1"
            response.headers["X-Next-Before"] = str(items[-1].get("created_at") or "")
    return items


# =========================================================
#                       VEHICLES
# =========================================================
@router.get("/vehicles/{vehicle_id}")
async def get_vehicle_detail(vehicle_id: str, user=Depends(current_firma)):
    # Runde 16: Sucher nur eigene Fahrzeuge (owner_user_id), Chef die Firma.
    v = await db.vehicles.find_one({"id": vehicle_id, **fahrzeug_bereich(user)}, {"_id": 0})
    if not v:
        raise HTTPException(404, "Fahrzeug nicht gefunden")
    (await besitzer_anreichern(user, [v]))
    # Runde 23 (11.09.2026, Befund A): Sucher sehen nur den eigenen Einkaufspreis.
    return await __import__("kaufvorgang").einkauf_fuer_sucher_maskieren(user, v)


@router.get("/vehicles")
async def list_vehicles(user=Depends(current_firma)):
    items = await db.vehicles.find(
        fahrzeug_bereich(user), {"_id": 0},
    ).sort("updated_at", -1).to_list(500)
    # Runde 23 (11.09.2026, Befund A): Sucher sehen nur den eigenen Einkaufspreis.
    return await __import__("kaufvorgang").einkauf_fuer_sucher_maskieren(
        user, await besitzer_anreichern(user, items))


# =========================================================
#               LISTING IDENTITY / CACHE
# =========================================================
@router.post("/listings/extract")
async def listings_extract(body: ListingURLIn, _user=Depends(current_firma)):
    if _user.get("role") not in ("dealer", "sucher"):
        raise HTTPException(403, "Nur für Händler-/Sucher-Accounts")
    """Erkennt Quelle (kleinanzeigen / mobile / autoscout24) + item_id aus
    einer URL. Liefert source, item_id und cache_key — ohne externen Fetch.
    Auth required: prevents unauthenticated probing of URL patterns / item IDs."""
    try:
        return get_listing_identity(body.url)
    except ListingIdentityError as exc:
        raise HTTPException(400, str(exc))


@router.post("/listings/resolve")
async def listings_resolve(body: ListingURLIn, user=Depends(require_active_sub)):
    """Cache-aware Resolver."""
    async def _fetcher(source: str, item_id: str, url: str) -> dict:
        return await fetch_listing(db, source, item_id, url,
                                   dealer_id=user.get("dealer_id") or "")

    # Eigene Quarantaene zuerst: sonst wuerde der Server eine Anzeige selbst
    # abrufen, die der Nutzer per Erweiterung bereits geliefert hat.
    eigen = await peek_cached_listing(db, body.url,
                                      dealer_id=user.get("dealer_id"))
    if eigen is not None:
        ident = get_listing_identity(body.url)
        return {"source": ident["source"], "item_id": ident["item_id"],
                "cache_key": ident["cache_key"], "cached": True,
                "vehicle": eigen[0], "snapshot_id": eigen[1]}

    try:
        data, was_cached, cached_snapshot_id = await get_or_fetch_listing(
            db, body.url, _fetcher, ttl_hours=LISTING_CACHE_TTL_HOURS,
        )
    except ListingIdentityError as exc:
        raise HTTPException(400, str(exc))
    except ListingBusy as exc:
        raise HTTPException(503, str(exc), headers={"Retry-After": "5"})
    except RuntimeError as exc:
        raise HTTPException(502, str(exc))

    identity = get_listing_identity(body.url)
    return {
        "source": identity["source"],
        "item_id": identity["item_id"],
        "cache_key": identity["cache_key"],
        "cached": was_cached,
        "snapshot_id": cached_snapshot_id,
        "vehicle": data,
    }
