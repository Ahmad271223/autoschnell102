# -*- coding: utf-8 -*-
"""Admin-Marktanalyse (Auftrag Ahmad 25.09.2026, Teil von Phase 1):
Modelle -> Modell -> Segment mit Verlauf; Status, Budget, Konfiguration.
Lesen: current_admin; Schreiben (Modelle an/aus, Bereiche, Budget, Crawl
jetzt): current_super_admin. Diagrammdaten kommen aus den Tagesaggregaten."""
from __future__ import annotations

from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from deps import current_admin, current_super_admin, db, log_activity_sicher
from markt import abfrage, auftraege, budget, jobs, katalog, konfig, segmente

router = APIRouter()


class ModellSchalterIn(BaseModel):
    enabled: bool
    ez_years: Optional[List[int]] = Field(default=None, max_length=12)


class BereichIn(BaseModel):
    min_km: Optional[int] = None
    max_km: Optional[int] = None
    year_from: Optional[int] = None
    year_to: Optional[int] = None


class CrawlerSchalterIn(BaseModel):
    aktiv: bool


class KonfigIn(BaseModel):
    km_buckets: Optional[List[BereichIn]] = None
    ez_buckets: Optional[List[BereichIn]] = None
    rows_je_segment: Optional[int] = Field(default=None, ge=1, le=200)
    budget_usd: Optional[float] = Field(default=None, ge=0, le=1000000)


@router.get("/admin/market/status")
async def admin_market_status(_=Depends(current_admin)):
    return await abfrage.status(db)


@router.get("/admin/market/models")
async def admin_market_models(_=Depends(current_admin)):
    return {"modelle": await abfrage.modelle_uebersicht(db), "hinweis": abfrage.HINWEIS,
            "km_buckets": await segmente.km_buckets(db), "ez_buckets": await segmente.ez_buckets(db)}


@router.post("/admin/market/jobs/{job_id}/cancel")
async def admin_market_job_cancel(job_id: str, admin=Depends(current_super_admin)):
    if not await jobs.abbrechen(db, job_id):
        raise HTTPException(404, "Job nicht wartend oder nicht gefunden")
    return {"ok": True}


@router.get("/admin/market/models/{model_id}")
async def admin_market_model(model_id: str, _=Depends(current_admin)):
    d = await abfrage.modell_detail(db, model_id)
    if not d:
        raise HTTPException(404, "Modell nicht gefunden")
    return d


@router.get("/admin/market/segments/{segment_id}/summary")
async def admin_market_segment(segment_id: str, _=Depends(current_admin)):
    d = await abfrage.segment_zusammenfassung(db, segment_id)
    if not d:
        raise HTTPException(404, "Segment nicht gefunden")
    return d


@router.get("/admin/market/segments/{segment_id}/history")
async def admin_market_history(segment_id: str, range: str = "30d", _=Depends(current_admin)):  # noqa: A002
    return await abfrage.segment_verlauf(db, segment_id, range)


@router.get("/admin/market/segments/{segment_id}/listings")
async def admin_market_listings(segment_id: str, _=Depends(current_admin)):
    return await abfrage.segment_listings(db, segment_id)


@router.get("/admin/market/listings/{listing_id}/history")
async def admin_market_listing(listing_id: str, _=Depends(current_admin)):
    d = await abfrage.listing_verlauf(db, listing_id)
    if not d:
        raise HTTPException(404, "Listing nicht gefunden")
    return d


@router.get("/admin/market/opportunities")
async def admin_market_opportunities(typ: Optional[str] = None, model_id: Optional[str] = None, tage: int = 7,
                                     limit: int = 200, _=Depends(current_admin)):
    return {"chancen": await abfrage.chancen(db, typ=typ, model_id=model_id, tage=tage, limit=limit)}


# ---------------------------------------------------------------- Suchauftraege (Auftrag v3)
class AuftragIn(BaseModel):
    """Freies Formular — Pruefung in markt.auftraege.entwurf_pruefen."""
    model_config = {"extra": "allow"}


@router.get("/admin/market/katalog")
async def admin_market_katalog(marke: Optional[str] = None, _=Depends(current_admin)):
    """Marken bzw. Modelle einer Marke aus dem mobile.de-Katalog (fuer das Formular)."""
    if marke:
        return {"modelle": katalog.modelle_der_marke(marke)}
    return {"marken": katalog.marken(), "kraftstoffe": list(auftraege.KRAFTSTOFFE), "getriebe": list(auftraege.GETRIEBE),
            "verkaeufer": list(auftraege.VERKAEUFER), "karosserie": list(auftraege.KAROSSERIE),
            "standard": {"ez_years": [b["year_from"] for b in konfig.EZ_BUCKETS_STANDARD],
                         "km_buckets": [dict(b) for b in konfig.KM_BUCKETS_STANDARD],
                         "rows": konfig.rows_je_segment(), "crawls_per_day": konfig.crawls_je_tag_standard()}}


@router.get("/admin/market/auftraege")
async def admin_market_auftraege(archiv: bool = False, _=Depends(current_admin)):
    return {"auftraege": await abfrage.modelle_uebersicht(db, mit_archiv=archiv), "prognose": await abfrage_prognose(None, None)}


async def abfrage_prognose(entwurf, ohne_id):
    return await auftraege.prognose(db, entwurf, ohne_id=ohne_id)


@router.post("/admin/market/prognose")
async def admin_market_prognose(body: AuftragIn, ohne_id: Optional[str] = None, _=Depends(current_super_admin)):
    """Kostenprognose fuer einen (ungespeicherten) Entwurf plus alle aktiven Marktanalysen — nur Rechnung."""
    e = body.model_dump()
    try:
        entwurf = auftraege.entwurf_pruefen({**e, "status": e.get("status") or "active"}) if e.get("make") else None
    except auftraege.Ungueltig as ex:
        return {"fehler": str(ex), **(await auftraege.prognose(db, None, ohne_id=ohne_id))}
    return await auftraege.prognose(db, entwurf, ohne_id=ohne_id)


@router.post("/admin/market/testlauf")
async def admin_market_testlauf(body: AuftragIn, n: int = 5, admin=Depends(current_super_admin)):
    """Ein Buendel-Lauf ueber alle Segmente des Entwurfs (hoechstens 20, je 2 Treffer) — prueft
    Filter und leere Segmente, bevor Daten gesammelt werden (kostet Budget; Review 26.09. Nr. 39)."""
    if not konfig.token():
        raise HTTPException(400, "APIFY_TOKEN fehlt")
    try:
        erg = await auftraege.testlauf(body.model_dump(), n=n)
    except auftraege.Ungueltig as ex:
        raise HTTPException(400, str(ex))
    except Exception as ex:  # noqa: BLE001
        raise HTTPException(502, f"Testlauf gescheitert: {str(ex)[:200]}")
    await log_activity_sicher("", admin["id"], "admin.markt.testlauf", meta={"url": erg.get("url"), "anzahl": erg.get("anzahl")})
    return erg


@router.post("/admin/market/models")
async def admin_market_model_create(body: AuftragIn, admin=Depends(current_super_admin)):
    try:
        doc = await auftraege.anlegen(db, body.model_dump())
    except auftraege.Ungueltig as ex:
        raise HTTPException(400, str(ex))
    await log_activity_sicher("", admin["id"], "admin.markt.auftrag.neu", ref=doc["id"], meta={"label": doc.get("label")})
    return {"ok": True, "modell": doc, "takt": await jobs.intervall(db)}


@router.put("/admin/market/models/{model_id}")
async def admin_market_model_update(model_id: str, body: AuftragIn, admin=Depends(current_super_admin)):
    try:
        doc = await auftraege.aendern(db, model_id, body.model_dump())
    except auftraege.Ungueltig as ex:
        raise HTTPException(400 if "nicht gefunden" not in str(ex) else 404, str(ex))
    await log_activity_sicher("", admin["id"], "admin.markt.auftrag.geaendert", ref=model_id)
    return {"ok": True, "modell": doc, "takt": await jobs.intervall(db)}


class StatusIn(BaseModel):
    status: str


@router.post("/admin/market/models/{model_id}/status")
async def admin_market_model_status(model_id: str, body: StatusIn, admin=Depends(current_super_admin)):
    try:
        doc = await auftraege.status_setzen(db, model_id, body.status)
    except auftraege.Ungueltig as ex:
        raise HTTPException(400 if "nicht gefunden" not in str(ex) else 404, str(ex))
    await log_activity_sicher("", admin["id"], "admin.markt.auftrag." + body.status, ref=model_id)
    return {"ok": True, "modell": doc, "takt": await jobs.intervall(db)}


@router.post("/admin/market/models/{model_id}/duplicate")
async def admin_market_model_duplicate(model_id: str, body: AuftragIn, admin=Depends(current_super_admin)):
    try:
        doc = await auftraege.duplizieren(db, model_id, body.model_dump())
    except auftraege.Ungueltig as ex:
        raise HTTPException(400 if "nicht gefunden" not in str(ex) else 404, str(ex))
    await log_activity_sicher("", admin["id"], "admin.markt.auftrag.dupliziert", ref=doc["id"], meta={"von": model_id})
    return {"ok": True, "modell": doc}


# ---------------------------------------------------------------- Schreiben (Super-Admin)
@router.post("/admin/market/models/{model_id}/enabled")
async def admin_market_model_enabled(model_id: str, body: ModellSchalterIn, admin=Depends(current_super_admin)):
    m = await db[konfig.MODELLE].find_one({"id": model_id}, {"_id": 0, "model_id": 1})
    if not m:
        raise HTTPException(404, "Modell nicht gefunden")
    if body.enabled and not m.get("model_id"):
        raise HTTPException(400, "Modell hat keine mobile.de-ID — kann nicht beobachtet werden")
    setzen: Dict[str, Any] = {"enabled": bool(body.enabled), "status": "active" if body.enabled else "paused",
                              "updated_at": konfig.jetzt_iso()}
    if body.ez_years is not None:
        setzen["ez_years"] = [int(j) for j in body.ez_years if 1980 <= int(j) <= 2100] or None
    await db[konfig.MODELLE].update_one({"id": model_id}, {"$set": setzen})
    erg = await segmente.synchronisieren(db)
    await log_activity_sicher("", admin["id"], "admin.markt.modell." + ("an" if body.enabled else "aus"), ref=model_id)
    return {"ok": True, "enabled": body.enabled, **erg}


@router.put("/admin/market/config")
async def admin_market_config(body: KonfigIn, admin=Depends(current_super_admin)):
    try:
        if body.km_buckets is not None:
            await segmente.km_buckets_setzen(db, [b.model_dump() for b in body.km_buckets])
        if body.ez_buckets is not None:
            await segmente.ez_buckets_setzen(db, [b.model_dump() for b in body.ez_buckets])
    except ValueError as e:
        raise HTTPException(400, str(e))
    if body.rows_je_segment is not None:
        await segmente.einstellungen_setzen(db, rows_je_segment=body.rows_je_segment)
    if body.budget_usd is not None:
        await budget.budget_setzen(db, body.budget_usd)
    erg = await segmente.synchronisieren(db)
    await log_activity_sicher("", admin["id"], "admin.markt.konfig", meta=body.model_dump(exclude_none=True))
    return {"ok": True, **erg, "takt": await jobs.intervall(db)}


@router.post("/admin/market/crawler")
async def admin_market_crawler(body: CrawlerSchalterIn, admin=Depends(current_super_admin)):
    """Crawler per Knopf an/aus (Wunsch Ahmad 26.09.2026) — gespeichert in market_config,
    geht vor MARKT_AKTIV. An: alle aktiven Suchauftraege laufen nach Tagesplan von selbst."""
    if body.aktiv and not konfig.token():
        raise HTTPException(400, "APIFY_TOKEN fehlt — ohne Scraper-Zugang kann der Crawler nicht laufen")
    an = await konfig.crawler_schalten(db, body.aktiv, wer=admin["id"])
    await log_activity_sicher("", admin["id"], "admin.markt.crawler", meta={"aktiv": an})
    return {"ok": True, "aktiv": an, "takt": await jobs.intervall(db)}


@router.post("/admin/market/sync")
async def admin_market_sync(admin=Depends(current_super_admin)):
    """Startliste einspielen (fehlende Modelle) und Segmente aufbauen."""
    m = await segmente.modelle_einspielen(db)
    s = await segmente.synchronisieren(db)
    return {"ok": True, "modelle": m, "segmente": s, "takt": await jobs.intervall(db)}


@router.post("/admin/market/plan")
async def admin_market_plan(sofort: bool = False, admin=Depends(current_super_admin)):
    """Tagesplan jetzt anlegen (sofort=true: alle faelligen ab jetzt statt im Fenster)."""
    if not konfig.token():
        raise HTTPException(400, "APIFY_TOKEN fehlt")
    erg = await jobs.tagesplan(db, sofort=sofort)
    await log_activity_sicher("", admin["id"], "admin.markt.plan", meta=erg)
    return {"ok": True, **erg}


@router.post("/admin/market/segments/{segment_id}/crawl-now")
async def admin_market_crawl_now(segment_id: str, admin=Depends(current_super_admin)):
    if not konfig.token():
        raise HTTPException(400, "APIFY_TOKEN fehlt")
    try:
        job = await jobs.job_sofort(db, segment_id)
    except ValueError as e:
        # Review 26.09.2026 Nr. 52: inaktives Segment -> 400 mit Klartext (nicht gefunden -> 404)
        raise HTTPException(404 if "nicht gefunden" in str(e) else 400, str(e))
    await log_activity_sicher("", admin["id"], "admin.markt.crawl_jetzt", ref=segment_id)
    return {"ok": True, "job": job, "hinweis": "Der Worker holt den Job innerhalb einer Minute (MARKT_AKTIV muss an sein)."}


@router.post("/admin/market/worker/einmal")
async def admin_market_worker_einmal(admin=Depends(current_super_admin)):
    """Einen Worker-Takt im Vordergrund (Betreiber-Test; laeuft auch ohne MARKT_AKTIV)."""
    if not konfig.token():
        raise HTTPException(400, "APIFY_TOKEN fehlt")
    return {"ok": True, **(await jobs.einmal(db))}
