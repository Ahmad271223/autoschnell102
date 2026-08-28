"""Listing endpoints: mobile/compare, live-counter, snapshots, vehicles,
listings/extract, listings/resolve."""
import logging
import os
import uuid
from datetime import datetime, timedelta, timezone

from pymongo import ReturnDocument
from typing import Any, Dict, Optional

# Cache-Lebensdauer fuer abgerufene Inserate. Hoehere TTL = weniger echte
# Scrape-/Proxy-Requests (dasselbe Inserat wird nur 1x je TTL geladen).
# Default 24h, per ENV anpassbar.
# Einmal verglichen = dauerhaft gespeichert (Wunsch 08/2026): dieselbe URL
# wird NICHT erneut von Kleinanzeigen/mobile geladen, sondern aus unserem
# Speicher bedient. Default 1 Jahr; per ENV anpassbar.
LISTING_CACHE_TTL_HOURS = int(os.environ.get("LISTING_CACHE_TTL_HOURS", "8760"))
# Client-Einreichungen (Browser-HTML) sind nur Momentaufnahmen: kurze TTL in
# der Quarantaene, und auch nach unabhaengiger Bestaetigung deutlich kuerzer
# als Server-Abrufe.
CLIENT_INGEST_TTL_HOURS = int(os.environ.get("CLIENT_INGEST_TTL_HOURS", "24"))
CLIENT_CONFIRMED_TTL_HOURS = int(os.environ.get("CLIENT_CONFIRMED_TTL_HOURS", "168"))

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Request, Response
from pydantic import BaseModel, Field

from auth import decode_token
from autoscout_service import build_search_url as build_autoscout_url
from deps import (
    current_user, db, log_activity, now_iso, require_active_sub,
)
from kleinanzeigen_service import (
    ListingGone, fetch_kleinanzeigen_vehicle,
    parse_kleinanzeigen_html, looks_like_kleinanzeigen_listing,
)
from provider_fetch import fetch_listing
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
CLIENT_FETCH_KLEINANZEIGEN = os.environ.get(
    "CLIENT_FETCH_KLEINANZEIGEN", "").strip().lower() in ("1", "true", "yes")
from mobile_service import (
    DEFAULT_EXPORT_RULES, DEFAULT_RULES, MOBILE_PASS, MOBILE_SANDBOX_MODE,
    MOBILE_USER, build_search_url, get_vehicle,
)
from snapshot_service import (
    create_snapshot, get_object as snapshot_get_object, run_snapshot_job,
)

log = logging.getLogger("autohandel")

router = APIRouter()


# ---------- Models ----------
class CompareIn(BaseModel):
    url: str


class ListingURLIn(BaseModel):
    url: str


# =========================================================
#                  MOBILE.DE COMPARE
# =========================================================
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

    if source == "autoscout24":
        raise HTTPException(
            400,
            "AutoScout24-Links sind noch nicht freigeschaltet (API-Zugang folgt). "
            "Bitte aktuell einen kleinanzeigen.de-Link verwenden.",
        )
    # mobile.de als QUELLE braucht den offiziellen API-Zugang. Ohne den (und
    # ohne ausdrücklichen Sandbox-Modus) früh und verständlich abbrechen —
    # statt tief im Fetcher mit einer technischen Meldung.
    if source == "mobile" and not (MOBILE_USER and MOBILE_PASS) and not MOBILE_SANDBOX_MODE:
        raise HTTPException(
            400,
            "mobile.de-Links sind noch nicht freigeschaltet (API-Zugang folgt). "
            "Bitte aktuell einen kleinanzeigen.de-Link verwenden.",
        )

    # CLIENT-SEITIGES ABRUFEN (nur Kleinanzeigen): Ist der Modus an und der
    # Link weder global noch in der EIGENEN Quarantaene vorhanden, holt
    # nicht der Server — der Browser des Nutzers wird gebeten, die Seite zu
    # holen und per /listings/ingest zu schicken. Danach ruft das Frontend
    # compare erneut auf -> Treffer (global oder eigene Quarantaene).
    client_hit = None
    if source == "kleinanzeigen" and CLIENT_FETCH_KLEINANZEIGEN:
        client_hit = await peek_cached_listing(
            db, raw_url, dealer_id=user.get("dealer_id"))
        if client_hit is None:
            return {
                "needs_client_fetch": True,
                "url": raw_url,
                "source": "kleinanzeigen",
                "hint": "Bitte über die Browser-Erweiterung laden.",
            }

    async def _fetcher(src: str, iid: str, url: str) -> dict:
        """Wird nur bei Cache-MISS aufgerufen."""
        return await fetch_listing(db, src, iid, url)

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
    if active == "export":
        rules = (dealer or {}).get("export_rules") or DEFAULT_EXPORT_RULES
    else:
        rules = (dealer or {}).get("comparison_rules") or DEFAULT_RULES
    search_url = build_search_url(vehicle, rules)
    autoscout_url = build_autoscout_url(vehicle, rules)

    # Track comparison (anonym)
    expires_at = datetime.now(timezone.utc) + timedelta(days=14)
    await db.vehicle_comparisons.insert_one({
        "id": str(uuid.uuid4()),
        "mobile_ad_id": ad_id,
        "source": source,
        "cached": was_cached,
        "dealer_id": user["dealer_id"],
        "user_id": user["id"],
        "created_at": now_iso(),
        "expires_at_dt": expires_at,
    })

    # Persist vehicle for re-use (PDF, Termine)
    vid = f"v_{ad_id}"
    await db.vehicles.update_one(
        {"id": vid, "dealer_id": user["dealer_id"]},
        {"$set": {
            "id": vid, "dealer_id": user["dealer_id"],
            "mobile_ad_id": ad_id, "data": {k: v for k, v in vehicle.items() if not k.startswith("_")},
            "updated_at": now_iso(),
        },
         "$setOnInsert": {"created_at": now_iso(), "status": "verglichen",
                          "lifecycle": "verglichen", "source": "plattform",
                          "lifecycle_changed_at": now_iso()}},
        upsert=True,
    )
    await log_activity(user["dealer_id"], user["id"], "vergleich.gestartet", ref=ad_id)

    # Proof-of-listing Snapshot.
    snap_id = None
    is_web_url = raw_url.startswith("http") and (
        "kleinanzeigen.de" in raw_url or "mobile.de" in raw_url
    )

    async def _reuse_cached_snapshot(sid: str) -> Optional[str]:
        # Bewusst OHNE dealer-Filter: der ERSTE Snapshot einer Anzeige wird
        # von ALLEN uebernommen — nie doppelt fotografieren (Wunsch 08/2026).
        doc = await db.listing_snapshots.find_one(
            {"id": sid},
            {"_id": 0, "status": 1, "id": 1},
        )
        if not doc:
            return None
        if doc.get("status") in ("failed", "expired"):
            return None
        return doc["id"]

    if is_web_url and was_cached and cached_snapshot_id:
        snap_id = await _reuse_cached_snapshot(cached_snapshot_id)

    # Zweite Reuse-Stufe: existiert fuer diese Anzeige-URL BEREITS irgendein
    # brauchbarer Snapshot (egal von wem), wird er uebernommen — es wird nie
    # doppelt fotografiert, auch nicht in Rennsituationen.
    if is_web_url and not snap_id:
        existing = await db.listing_snapshots.find_one(
            {"source_url": raw_url, "status": {"$nin": ["failed", "expired"]}},
            {"_id": 0, "id": 1}, sort=[("created_at", -1)])
        if existing:
            snap_id = existing["id"]
            try:
                await set_cache_snapshot(db, raw_url, snap_id)
            except Exception:
                pass

    if is_web_url and not snap_id:
        # ATOMARE RESERVIERUNG (Beschluss 08/2026): Vergleichen mehrere
        # Nutzer im SELBEN Moment denselben NEUEN Link, darf nur EINER den
        # Snapshot anlegen. Vorher war das ein "pruefen, dann anlegen" —
        # gleichzeitige Vergleiche erzeugten mehrere Snapshots (mehrfacher
        # Abruf derselben Anzeige = Block-Risiko, mehrfacher Speicher).
        # Wer die ID im Cache setzt, gewinnt; alle anderen erben sie.
        _ck = identity["cache_key"]
        _reserved = str(uuid.uuid4())
        _now = datetime.now(timezone.utc)
        won = await db.listings_cache.find_one_and_update(
            {"cache_key": _ck,
             "$or": [{"snapshot_id": {"$exists": False}}, {"snapshot_id": None}]},
            {"$set": {"snapshot_id": _reserved, "snapshot_reserved_at": _now}},
            projection={"_id": 0, "snapshot_id": 1},
            return_document=ReturnDocument.AFTER,
        )

        async def _anlegen(sid: str) -> Optional[str]:
            try:
                new_id = await create_snapshot(
                    db,
                    dealer_id=user["dealer_id"], user_id=user["id"],
                    vehicle_id=vid, mobile_ad_id=ad_id, source_url=raw_url,
                    snapshot_id=sid,
                )
                background.add_task(run_snapshot_job, db, new_id)
                return new_id
            except Exception as exc:
                log.warning("could not schedule snapshot for %s: %s", raw_url, exc)
                # Reservierung freigeben, sonst blockiert sie alle anderen.
                await db.listings_cache.update_one(
                    {"cache_key": _ck, "snapshot_id": sid},
                    {"$unset": {"snapshot_id": "", "snapshot_reserved_at": ""}})
                return None

        if won and won.get("snapshot_id") == _reserved:
            snap_id = await _anlegen(_reserved)
        else:
            # Jemand war schneller -> dessen Snapshot uebernehmen.
            doc = await db.listings_cache.find_one(
                {"cache_key": _ck},
                {"_id": 0, "snapshot_id": 1, "snapshot_reserved_at": 1})
            snap_id = (doc or {}).get("snapshot_id")
            if snap_id and not await db.listing_snapshots.find_one(
                    {"id": snap_id}, {"_id": 1}):
                # Reservierung zeigt ins Leere (Gewinner abgebrochen). Erst
                # nach einer Schonfrist uebernehmen — sonst wuerde man dem
                # Gewinner die ID wegschnappen, der gerade anlegt.
                res_at = (doc or {}).get("snapshot_reserved_at")
                if isinstance(res_at, datetime):
                    if res_at.tzinfo is None:
                        res_at = res_at.replace(tzinfo=timezone.utc)
                    veraltet = (_now - res_at).total_seconds() > 120
                else:
                    veraltet = True
                if veraltet:
                    taken = await db.listings_cache.find_one_and_update(
                        {"cache_key": _ck, "snapshot_id": snap_id},
                        {"$set": {"snapshot_id": _reserved,
                                  "snapshot_reserved_at": _now}},
                        projection={"_id": 0, "snapshot_id": 1},
                        return_document=ReturnDocument.AFTER)
                    if taken and taken.get("snapshot_id") == _reserved:
                        snap_id = await _anlegen(_reserved)
            if not snap_id:
                # Kein Cache-Eintrag vorhanden (Sonderfall): normal anlegen.
                snap_id = await _anlegen(str(uuid.uuid4()))
                if snap_id:
                    try:
                        await set_cache_snapshot(db, raw_url, snap_id)
                    except Exception:
                        pass

    return {
        "vehicle_id": vid,
        "ad_id": ad_id,
        "vehicle": vehicle,
        "search_url": search_url,
        "autoscout_url": autoscout_url,
        "rules_applied": rules,
        "active_profile": active,
        "source": source,
        "cached": was_cached,
        "cache_key": identity["cache_key"],
        "snapshot_id": snap_id,
        "snapshot_reused": bool(was_cached and cached_snapshot_id and snap_id == cached_snapshot_id),
    }


class IngestIn(BaseModel):
    url: str
    html: str = Field(min_length=500, max_length=6_000_000)


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

    # Schon global freigegeben ODER eigene frische Einreichung vorhanden?
    # Dann nichts tun.
    if await peek_cached_listing(db, raw_url,
                                 dealer_id=user.get("dealer_id")) is not None:
        return {"ok": True, "already_cached": True}

    if not looks_like_kleinanzeigen_listing(body.html, url=raw_url):
        raise HTTPException(422, "Das übermittelte HTML sieht nicht nach einer "
                                 "Kleinanzeigen-Fahrzeugseite aus.")
    try:
        parsed = parse_kleinanzeigen_html(raw_url, body.html)
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
    if source == "autoscout24":
        raise HTTPException(400, "AutoScout24-Links sind noch nicht "
                                 "freigeschaltet.")
    if source == "mobile" and not (MOBILE_USER and MOBILE_PASS) \
            and not MOBILE_SANDBOX_MODE:
        raise HTTPException(400, "mobile.de-Links sind noch nicht "
                                 "freigeschaltet (API-Zugang folgt).")

    if await peek_cached_listing(db, raw_url,
                                 dealer_id=user.get("dealer_id")) is not None:
        return {"status": "completed", "cached": True,
                "source": source, "item_id": identity["item_id"]}

    if source == "kleinanzeigen" and CLIENT_FETCH_KLEINANZEIGEN:
        return {"status": "needs_client_fetch", "url": raw_url,
                "source": source,
                "hint": "Bitte über die Browser-Erweiterung laden."}

    from link_jobs import enqueue_job
    job = await enqueue_job(db, raw_url, dealer_id=user.get("dealer_id") or "")
    return {"status": job["status"], "job_id": job["id"],
            "source": source, "item_id": identity["item_id"]}


@router.get("/listings/check/{job_id}")
async def listings_check_status(job_id: str, user=Depends(current_user)):
    """Status eines Linkpruefungs-Jobs: queued | processing | completed |
    failed. Bei completed liegt das Inserat im Cache — /mobile/compare
    liefert dann sofort."""
    from link_jobs import get_job
    job = await get_job(db, job_id)
    if not job:
        raise HTTPException(404, "Job nicht gefunden (evtl. abgelaufen)")
    out = {"status": job["status"], "job_id": job["id"],
           "source": job.get("source"), "item_id": job.get("item_id"),
           "error": job.get("error")}
    if job["status"] == "queued":
        out["vor_dir"] = await db.link_jobs.count_documents(
            {"status": "queued", "created_at": {"$lt": job["created_at"]}})
    return out


@router.get("/mobile/live-counter/{ad_id}")
async def live_counter(ad_id: str, user=Depends(current_user)):
    five_min = (datetime.now(timezone.utc) - timedelta(minutes=10)).isoformat()
    today = datetime.now(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0).isoformat()
    active = await db.vehicle_comparisons.count_documents({
        "mobile_ad_id": ad_id, "created_at": {"$gte": five_min},
        "dealer_id": {"$ne": user["dealer_id"]},
    })
    today_total = await db.vehicle_comparisons.count_documents({
        "mobile_ad_id": ad_id, "created_at": {"$gte": today},
    })
    return {"active_now": active, "today": today_total}


# =========================================================
#                  LISTING SNAPSHOTS
# =========================================================
async def _load_snapshot_or_404(snap_id: str) -> dict:
    # Snapshots dokumentieren OEFFENTLICHE Inserate und werden haendler-
    # uebergreifend wiederverwendet -> lesbar fuer jeden eingeloggten Nutzer.
    snap = await db.listing_snapshots.find_one(
        {"id": snap_id}, {"_id": 0},
    )
    if not snap:
        raise HTTPException(404, "Snapshot nicht gefunden")
    return snap


@router.get("/snapshots/{snap_id}")
async def snapshot_status(snap_id: str, user=Depends(current_user)):
    snap = await _load_snapshot_or_404(snap_id)
    snap.pop("png_path", None)
    snap.pop("pdf_path", None)
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

    snap = await _load_snapshot_or_404(snap_id)
    if snap.get("status") != "ready":
        raise HTTPException(409, f"Snapshot ist nicht bereit (status={snap.get('status')})")
    path = snap.get(f"{kind}_path")
    if not path:
        raise HTTPException(404, f"Kein {kind.upper()}-Artefakt für diesen Snapshot")
    try:
        data, ctype = snapshot_get_object(path)
    except Exception as exc:
        log.exception("snapshot fetch failed")
        raise HTTPException(502, "Snapshot-Storage nicht erreichbar.")
    return Response(content=data, media_type=ctype)


@router.get("/snapshots")
async def list_snapshots(vehicle_id: Optional[str] = None,
                         user=Depends(current_user)):
    q: Dict[str, Any] = {"dealer_id": user["dealer_id"]}
    if vehicle_id:
        q["vehicle_id"] = vehicle_id
    items = await db.listing_snapshots.find(q, {"_id": 0, "png_path": 0, "pdf_path": 0}) \
        .sort("created_at", -1).to_list(200)
    return items


# =========================================================
#                       VEHICLES
# =========================================================
@router.get("/vehicles/{vehicle_id}")
async def get_vehicle_detail(vehicle_id: str, user=Depends(current_user)):
    v = await db.vehicles.find_one({"id": vehicle_id, "dealer_id": user["dealer_id"]}, {"_id": 0})
    if not v:
        raise HTTPException(404, "Fahrzeug nicht gefunden")
    return v


@router.get("/vehicles")
async def list_vehicles(user=Depends(current_user)):
    items = await db.vehicles.find(
        {"dealer_id": user["dealer_id"]}, {"_id": 0},
    ).sort("updated_at", -1).to_list(500)
    return items


# =========================================================
#               LISTING IDENTITY / CACHE
# =========================================================
@router.post("/listings/extract")
async def listings_extract(body: ListingURLIn, _user=Depends(current_user)):
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
        return await fetch_listing(db, source, item_id, url)

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
