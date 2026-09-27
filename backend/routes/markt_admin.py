# -*- coding: utf-8 -*-
"""Admin-Marktanalyse (Auftrag Ahmad 25.09.2026, Teil von Phase 1):
Modelle -> Modell -> Segment mit Verlauf; Status, Budget, Konfiguration.
Lesen: current_admin; Schreiben (Modelle an/aus, Bereiche, Budget, Crawl
jetzt): current_super_admin. Diagrammdaten kommen aus den Tagesaggregaten."""
from __future__ import annotations

from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
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


# ---------------------------------------------------------------- Private Deals (Ahmad 26.09.2026 abends)
# NUR der Super-Admin: die 3 guenstigsten Privatangebote je Segment aus dem ohnehin abgerufenen Sample.
# Nichts davon in /markt/chancen der Firmen oder in der Fahrzeugkarte.
@router.get("/admin/market/private-deals")
async def admin_market_private_deals(make: Optional[str] = None, model_id: Optional[str] = None, ez: Optional[int] = None,
                                     km_min: Optional[int] = None, km_max: Optional[int] = None,
                                     preis_von: Optional[float] = None, preis_bis: Optional[float] = None,
                                     abstand_pct_max: Optional[float] = None, plz: Optional[str] = None,
                                     nur_aktuell: bool = True, heute_neu: bool = False, preis_reduziert: bool = False,
                                     sort: str = "abstand_pct", limit: int = Query(200, ge=1, le=500),
                                     _=Depends(current_super_admin)):
    if sort not in abfrage.PRIVATE_SORTIERUNGEN:
        raise HTTPException(400, f"Unbekannte Sortierung — erlaubt: {', '.join(abfrage.PRIVATE_SORTIERUNGEN)}")
    return await abfrage.private_deals(db, make=make, model_id=model_id, ez=ez, km_min=km_min, km_max=km_max,
                                       preis_von=preis_von, preis_bis=preis_bis, abstand_pct_max=abstand_pct_max, plz=plz,
                                       nur_aktuell=nur_aktuell, heute_neu=heute_neu, preis_reduziert=preis_reduziert,
                                       sort=sort, limit=limit)


@router.get("/admin/market/segments/{segment_id}/private-deals")
async def admin_market_segment_private_deals(segment_id: str, _=Depends(current_super_admin)):
    d = await abfrage.segment_private_deals(db, segment_id)
    if not d:
        raise HTTPException(404, "Segment nicht gefunden")
    return d


# ---------------------------------------------------------------- Hot Deals (Master-Auftrag Phase D, 27.09.2026)
# NUR der Super-Admin: auffaellig guenstige Inserate gegenueber dem historischen Low-Market-Median desselben
# Segments — nur aus gespeicherten Tageswerten (kein Abruf). Nichts davon in /markt/* der Firmen oder der Karte.
@router.get("/admin/market/hot-deals")
async def admin_market_hot_deals(status: str = "aktuell", klasse: Optional[str] = None, privat: Optional[bool] = None,
                                 model_id: Optional[str] = None, make: Optional[str] = None, ez: Optional[int] = None,
                                 km_min: Optional[int] = None, km_max: Optional[int] = None, segment_id: Optional[str] = None,
                                 heute_neu: bool = False, sort: str = "vorteil_pct", limit: int = Query(200, ge=1, le=500),
                                 _=Depends(current_super_admin)):
    from markt import deals
    if sort not in deals.SORTIERUNGEN:
        raise HTTPException(400, f"Unbekannte Sortierung — erlaubt: {', '.join(deals.SORTIERUNGEN)}")
    if status not in deals.STATUS_FILTER:
        raise HTTPException(400, f"Unbekannter Status — erlaubt: {', '.join(deals.STATUS_FILTER)}")
    if klasse and klasse not in deals.KLASSE_RANG:
        raise HTTPException(400, f"Unbekannte Klasse — erlaubt: {', '.join(deals.KLASSE_RANG)}")
    return await deals.liste(db, status=status, klasse=klasse, privat=privat, model_id=model_id, make=make, ez=ez,
                             km_min=km_min, km_max=km_max, segment_id=segment_id, heute_neu=heute_neu, sort=sort, limit=limit)


@router.get("/admin/market/hot-deals/ereignisse")
async def admin_market_hot_deal_ereignisse(segment_id: str, listing_id: str, _=Depends(current_super_admin)):
    from markt import deals
    d = await deals.ereignisse(db, segment_id, listing_id)
    if not d:
        raise HTTPException(404, "Hot Deal nicht gefunden")
    return d


@router.post("/admin/market/hot-deals/auswerten")
async def admin_market_hot_deals_auswerten(admin=Depends(current_super_admin)):
    """Offene Tageswerte jetzt auswerten (sonst alle 5 Minuten im Hintergrund) — liest nur Gespeichertes."""
    from markt import auswertung
    erg = await auswertung.durchlauf(db)
    if erg.get("gesperrt"):
        raise HTTPException(409, "Die Auswertung läuft gerade in einem anderen Prozess — bitte gleich noch einmal")
    await log_activity_sicher("", admin["id"], "admin.markt.auswertung", meta={"hot_deals": (erg.get("hot_deals") or {}).get("ausgewertet", 0)})
    return {"ok": True, **erg}


# ---------------------------------------------------------------- Berichte 5/15/Monat (Master-Auftrag Phase E, 27.09.2026)
# Lesen: current_admin; Einfrieren (nur faellige Perioden): current_super_admin. Alles aus gespeicherten Tageswerten —
# ein finaler Bericht wird unveraendert ausgeliefert, eine laufende Periode nur vorlaeufig live gerechnet (nie gespeichert).
@router.get("/admin/market/reports/periods")
async def admin_market_report_periods(typ: Optional[str] = None, _=Depends(current_admin)):
    from markt import berichte
    if typ and typ not in berichte.TYPEN:
        raise HTTPException(400, f"Unbekannter Berichtstyp — erlaubt: {', '.join(berichte.TYPEN)}")
    return await berichte.perioden_liste(db, typ)


@router.get("/admin/market/reports")
async def admin_market_reports(typ: str, von: str, bis: str, _=Depends(current_admin)):
    """Abschnitt 37: Uebersicht aller Modelle einer Periode (aus den eingefrorenen Berichten)."""
    from markt import berichte
    if not berichte.periode_gueltig(typ, von, bis):
        raise HTTPException(400, "Keine gültige Berichtsperiode (5 Tage: 01–05 … 26–Monatsende, 15 Tage: 01–15 / 16–Monatsende, Monat)")
    return await berichte.uebersicht(db, typ, von, bis)


@router.get("/admin/market/reports/model/{model_id}")
async def admin_market_model_report(model_id: str, typ: str, von: str, bis: str, _=Depends(current_admin)):
    from markt import berichte
    try:
        b = await berichte.modell_bericht(db, model_id, typ, von, bis)
    except ValueError as ex:
        raise HTTPException(400, str(ex))
    if not b:
        raise HTTPException(404, "Keine Tagesdaten für dieses Modell im Zeitraum")
    return b


@router.get("/admin/market/reports/model/{model_id}/list")
async def admin_market_model_report_list(model_id: str, _=Depends(current_admin)):
    from markt import berichte
    return await berichte.modell_berichte(db, model_id)


@router.post("/admin/market/reports/finalize")
async def admin_market_reports_finalize(admin=Depends(current_super_admin)):
    """'Berichte jetzt erstellen': derselbe Durchlauf wie der Hintergrundjob — Hot Deals auswerten, dann NUR die
    faelligen Perioden (Periodenende + Karenz) einfrieren; laufende Perioden nie. Idempotent."""
    from markt import auswertung
    erg = await auswertung.durchlauf(db)
    if erg.get("gesperrt"):
        raise HTTPException(409, "Die Auswertung läuft gerade in einem anderen Prozess — bitte gleich noch einmal")
    await log_activity_sicher("", admin["id"], "admin.markt.berichte", meta={k: v for k, v in (erg.get("berichte") or {}).items() if isinstance(v, int)})
    return {"ok": True, **erg}


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
async def admin_market_auftraege(archiv: bool = False, needs_review: bool = False, _=Depends(current_admin)):
    """Liste der Suchauftraege (+ Prognose). needs_review=true: nur Masterlisten-Zeilen mit Pruefbedarf."""
    from markt import masterliste
    liste = await abfrage.modelle_uebersicht(db, mit_archiv=archiv)
    if needs_review:
        liste = [m for m in liste if m.get("needs_review")]
    stand = await konfig.merker_lesen(db, masterliste.MASTER_DOK)
    return {"auftraege": liste, "prognose": await abfrage_prognose(None, None), "masterliste": stand or None,
            "testlauf_alle": await masterliste.testlauf_alle_status(db)}


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
        erg = await auftraege.testlauf(body.model_dump(), n=n, db=db)      # Welle 5 Nr. 18: gegen das Marktbudget
    except auftraege.Ungueltig as ex:
        raise HTTPException(400, str(ex))
    except Exception as ex:  # noqa: BLE001
        raise HTTPException(502, f"Testlauf gescheitert: {str(ex)[:200]}")
    await log_activity_sicher("", admin["id"], "admin.markt.testlauf", meta={"url": erg.get("url"), "anzahl": erg.get("anzahl"),
                                                                              "bestanden": erg.get("bestanden"), "usd": erg.get("usd")})
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
    except auftraege.Konflikt as ex:
        # Reparaturwelle 6 Nr. 130: gleichzeitig geaendert (CAS) -> 409, nichts ueberschrieben
        raise HTTPException(409, str(ex))
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


@router.post("/admin/market/config/anwenden")
async def admin_market_config_anwenden(admin=Depends(current_super_admin)):
    """Welle 5 (Oberflaeche): die zentralen Vorbelegungen (km-Bereiche, EZ-Jahre, Zeilen) auf ALLE
    aktiven Suchauftraege uebertragen — bestehende Auftraege behalten sonst ihre eigenen Werte."""
    km = await segmente.km_buckets(db)
    ez = sorted({j for b in (await segmente.ez_buckets(db)) if b.get("year_from")
                 for j in range(int(b["year_from"]), int(b.get("year_to") or b["year_from"]) + 1)})
    rows = (await segmente.einstellungen(db))["rows_je_segment"]
    if not ez:
        raise HTTPException(400, "Zentrale EZ-Bereiche sind leer — Aufträge brauchen mindestens ein EZ-Jahr")
    erg = await auftraege.konfig_anwenden(db, km_buckets=km, ez_years=ez, rows=rows)
    await log_activity_sicher("", admin["id"], "admin.markt.konfig.anwenden", meta={"geaendert": erg["geaendert"], "fehler": len(erg["fehler"])})
    return {"ok": True, **erg, "takt": await jobs.intervall(db)}


@router.post("/admin/market/crawler")
async def admin_market_crawler(body: CrawlerSchalterIn, admin=Depends(current_super_admin)):
    """Crawler per Knopf an/aus (Wunsch Ahmad 26.09.2026) — gespeichert in market_config,
    geht vor MARKT_AKTIV. An: alle aktiven Suchauftraege laufen nach Tagesplan von selbst."""
    if body.aktiv and not konfig.token():
        raise HTTPException(400, "APIFY_TOKEN fehlt — ohne Scraper-Zugang kann der Crawler nicht laufen")
    # Reparaturwelle 5 Nr. 23: aus = wartende Jobs stornieren; an = frischer Tagesplan fuer heute
    erg = await jobs.crawler_schalten(db, body.aktiv, wer=admin["id"])
    await log_activity_sicher("", admin["id"], "admin.markt.crawler", meta={"aktiv": erg["aktiv"], "storniert": erg.get("storniert", 0)})
    return {"ok": True, **erg, "takt": await jobs.intervall(db)}


@router.post("/admin/market/sync")
async def admin_market_sync(admin=Depends(current_super_admin)):
    """Masterliste einspielen (idempotent, nie automatisch aktiv) und Segmente aufbauen."""
    m = await segmente.modelle_einspielen(db)
    s = await segmente.synchronisieren(db)
    return {"ok": True, "modelle": m, "segmente": s, "takt": await jobs.intervall(db)}


@router.post("/admin/market/masterliste/importieren")
async def admin_market_masterliste_importieren(admin=Depends(current_super_admin)):
    """Master-Auftrag Phase A: die Fahrzeug-Masterliste (170 Zeilen) einspielen bzw. nachziehen — ruft
    dieselbe Migrationsfunktion wie Migration 18 (idempotent; Altbestand klassifiziert UNCHANGED/CHANGED/
    NEW/DEPRECATED; nichts wird automatisch aktiv, Aktivieren erst nach Testlauf)."""
    from markt import masterliste
    z = await masterliste.importieren(db)
    await log_activity_sicher("", admin["id"], "admin.markt.masterliste.import", meta={k: v for k, v in z.items() if isinstance(v, (int, float))})
    return {"ok": True, **z, "takt": await jobs.intervall(db)}


@router.get("/admin/market/actor-meta")
async def admin_market_actor_meta(_=Depends(current_admin)):
    """Master-Auftrag Phase C: Feldnamen + Typen je Scraper-Build (nie Werte) — Grundlage, um ein verlaessliches
    Gesamttreffer-Feld einzutragen (normalisieren.MARKT_GESAMT_FELD; bis dahin Vollstaendigkeit UNKNOWN)."""
    from markt import normalisieren
    docs = await db[konfig.KONFIG].find({"_id": {"$regex": "^actor_meta_"}}).to_list(20)
    return {"actors": [{**{k: v for k, v in d.items() if k != "_id"}, "id": d["_id"]} for d in docs],
            "markt_gesamt_feld": normalisieren.MARKT_GESAMT_FELD}


class TestlaufAlleIn(BaseModel):
    aktivieren: bool = True
    mit_review: bool = False


_SAMMEL_TASKS: set = set()


@router.post("/admin/market/masterliste/testlauf-alle")
async def admin_market_testlauf_alle(body: TestlaufAlleIn, admin=Depends(current_super_admin)):
    """Master-Auftrag Phase A: Sammel-Testlauf — jeder pausierte Masterlisten-Auftrag bekommt EINEN Testlauf ueber
    alle seine Segmente (gegen das Marktbudget); bestandene werden mit aktivieren=true aktiviert. Zeilen 'zu pruefen'
    nur mit mit_review=true. Laeuft im Hintergrund (Stand in GET /admin/market/auftraege -> testlauf_alle); ein
    zweiter Start waehrend eines Laufs -> 409."""
    import asyncio
    from markt import masterliste
    if not konfig.token():
        raise HTTPException(400, "APIFY_TOKEN fehlt")
    try:
        lauf_id = await masterliste.testlauf_alle_beanspruchen(db, aktivieren=body.aktivieren, mit_review=body.mit_review, wer=admin["id"])
    except masterliste.LaeuftSchon as ex:
        raise HTTPException(409, str(ex))
    task = asyncio.get_running_loop().create_task(
        masterliste.testlauf_alle_ausfuehren(db, lauf_id, aktivieren=body.aktivieren, mit_review=body.mit_review))
    _SAMMEL_TASKS.add(task)
    task.add_done_callback(_SAMMEL_TASKS.discard)
    await log_activity_sicher("", admin["id"], "admin.markt.masterliste.testlauf_alle",
                              meta={"aktivieren": body.aktivieren, "mit_review": body.mit_review})
    return {"ok": True, "lauf_id": lauf_id, **(await masterliste.testlauf_alle_status(db))}


@router.post("/admin/market/plan")
async def admin_market_plan(sofort: bool = False, admin=Depends(current_super_admin)):
    """Tagesplan jetzt anlegen (sofort=true: das Tageskontingent ab jetzt statt im Fenster —
    Reparaturwelle 5 Nr. 32: nie mehr als segmente_je_tag, der Rest wartet; 'hinweis' sagt es)."""
    if not konfig.token():
        raise HTTPException(400, "APIFY_TOKEN fehlt")
    erg = await jobs.tagesplan(db, sofort=sofort)
    await log_activity_sicher("", admin["id"], "admin.markt.plan", meta={k: v for k, v in erg.items() if k != "hinweis"})
    return {"ok": True, **erg}


@router.post("/admin/market/segments/{segment_id}/crawl-now")
async def admin_market_crawl_now(segment_id: str, admin=Depends(current_super_admin)):
    if not konfig.token():
        raise HTTPException(400, "APIFY_TOKEN fehlt")
    try:
        job = await jobs.job_sofort(db, segment_id)
    except jobs.SchonEingereiht as e:
        # Reparaturwelle 6 Nr. 77: Doppelklick / laufender Job -> 409 mit Klartext, kein zweiter Job
        raise HTTPException(409, str(e))
    except ValueError as e:
        # Review 26.09.2026 Nr. 52: inaktives Segment -> 400 mit Klartext (nicht gefunden -> 404)
        raise HTTPException(404 if "nicht gefunden" in str(e) else 400, str(e))
    await log_activity_sicher("", admin["id"], "admin.markt.crawl_jetzt", ref=segment_id)
    return {"ok": True, "job": job, "hinweis": "Der Worker holt den Job innerhalb einer Minute (MARKT_AKTIV muss an sein)."}


@router.post("/admin/market/worker/einmal")
async def admin_market_worker_einmal(admin=Depends(current_super_admin)):
    """Einen Worker-Takt im Vordergrund (Betreiber-Test; laeuft auch ohne MARKT_AKTIV).
    Reparaturwelle 5 Nr. 33: hoechstens EIN Buendel je Aufruf, ohne Warten auf Nachzuegler —
    'wartend' sagt, wie viele faellige Jobs noch liegen."""
    if not konfig.token():
        raise HTTPException(400, "APIFY_TOKEN fehlt")
    return {"ok": True, **(await jobs.einmal(db, max_buendel=1, nachzuegler_s=0))}
