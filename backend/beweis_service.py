# -*- coding: utf-8 -*-
"""Beweisdokumente je Inserat: Vormerken, Erzeugen, Aufbewahren.

Ersetzt die Snapshots (Playwright/Browser) vollstaendig, Wunsch 10.09.2026:
"wenn ein neuer link zum ersten mal von egal wem benutzt wird wird ein
beweis dokument ... in eine pdf eintragen".

Ablauf
  1. Ein Inserat wird zum ersten Mal vom Server abgerufen
     (listing_identity.get_or_fetch_listing, Miss-Zweig) oder verglichen
     (routes/listings.compare). Beide rufen beweis_vormerken() auf.
  2. beweis_vormerken legt EINE Zeile je Inserat an (Unique-Index auf
     cache_key, $setOnInsert). Wer spaeter kommt — gleiche oder andere
     Firma — bekommt dieselbe Zeile: nie zwei Beweisdokumente je Inserat.
  3. Jeder Backend-Worker betreibt eine kleine Schleife
     (run_beweis_worker_forever). Eine Zeile wird per atomarem Statuswechsel
     offen -> in_arbeit beansprucht; genau EIN Worker gewinnt. Faellt er aus,
     gibt die Aufraeumung die Zeile nach BEARBEITUNG_SEKUNDEN wieder frei.
  4. Erzeugen: Inseratsdaten aus dem beim Vormerken eingefrorenen Stand
     (quelle_daten, Runde 23 — Altbestand ohne ihn: listings_cache), Fotos ueber
     bild_proxy.laden_fuer_pdf (Allowliste, Groessenlimit), PDF ueber
     beweis_pdf.beweis_pdf (ohne Browser), Ablage im Datei-Speicher unter
     beweise/<quelle>/<id>.pdf (firmenneutral).
  5. Aufbewahrung: BEWEIS_AUFBEWAHRUNG_TAGE (Standard 90) ab Erstellung;
     laenger, solange irgendeine Firma ein Fahrzeug zu diesem Inserat
     fuehrt (Vertrag, Bestand ...). Danach wird die Datei geloescht und die
     Zeile bleibt als Grabstein (status geloescht) — so entsteht fuer
     denselben Link kein zweites "erstes" Dokument.

Statuswerte: offen | in_arbeit | fertig | fehlgeschlagen | geloescht
"""
from __future__ import annotations

import asyncio
import hashlib
import logging
import os
import time
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional, Set

from pymongo import ReturnDocument
from pymongo.errors import DuplicateKeyError

log = logging.getLogger("autohandel")


def _zahl_env(name: str, standard: int, unten: int, oben: int) -> int:
    roh = os.environ.get(name, "")
    try:
        wert = int(roh) if str(roh).strip() else standard
    except ValueError:
        log.warning("%s=%r ist keine ganze Zahl — Standard %s", name, roh, standard)
        wert = standard
    if not unten <= wert <= oben:
        log.warning("%s=%s liegt ausserhalb %s..%s — wird begrenzt", name, wert, unten, oben)
    return max(unten, min(oben, wert))


# Wie viele Beweisdokumente EIN Worker-Prozess gleichzeitig erzeugt
# (4 Worker x 2 Server = 8 gleichzeitig bei Standard 1).
BEWEIS_PARALLEL = _zahl_env("BEWEIS_PARALLEL", 1, 1, 8)
# Hoechstzahl eingebetteter Fotos je Dokument (alle Adressen stehen im Anhang).
BEWEIS_FOTOS_MAX = _zahl_env("BEWEIS_FOTOS_MAX", 20, 0, 60)
# Runde 26 (12.09.2026, Wunsch Ahmad: Dokument kleiner): Fotos werden fuer
# das Beweisdokument staerker verkleinert. Gemessen mit 9 Fotos: 666 KB ->
# rund 374 KB, ohne dass Fahrzeug oder Schaeden schlechter erkennbar sind.
_FOTO_KANTE_ERSTE = _zahl_env("BEWEIS_FOTO_KANTE_ERSTE", 1000, 400, 2000)
_FOTO_KANTE = _zahl_env("BEWEIS_FOTO_KANTE", 640, 300, 2000)
_FOTO_QUALITAET_ERSTE = _zahl_env("BEWEIS_FOTO_QUALITAET_ERSTE", 68, 40, 95)
_FOTO_QUALITAET = _zahl_env("BEWEIS_FOTO_QUALITAET", 62, 40, 95)
BEWEIS_AUFBEWAHRUNG_TAGE = _zahl_env("BEWEIS_AUFBEWAHRUNG_TAGE", 90, 1, 3650)
# Name/Anschrift/Telefon auch privater Anbieter drucken (Standard: nein).
BEWEIS_PRIVATDATEN = (os.environ.get("BEWEIS_PRIVATDATEN", "") or "").strip().lower() \
    in ("1", "true", "ja", "yes")
BEARBEITUNG_SEKUNDEN = 300
MAX_VERSUCHE = 3
FOTO_PARALLEL = 4
# Gesamtfrist je Erzeugung (unter der Lease) und Herzschlag, der die Lease
# verlaengert, solange die Erzeugung laeuft (sonst uebernaehme ein zweiter
# Worker nach BEARBEITUNG_SEKUNDEN und beide schrieben).
ERZEUGUNG_MAX_SEKUNDEN = BEARBEITUNG_SEKUNDEN - 60
HERZSCHLAG_SEKUNDEN = 60
# Endgueltig gescheiterte Dokumente werden beim naechsten Gebrauch des Links
# wieder in die Warteschlange gestellt — fruehestens nach dieser Pause.
WIEDERBELEBEN_MINUTEN = 15
# Lebenszyklen OHNE Geschaeftsbezug: diese Fahrzeuge halten ein
# Beweisdokument NICHT ueber die Frist hinaus (sonst griffe sie nie).
OHNE_GESCHAEFT = ("verglichen", "gefunden", "storniert", "nicht_abgeholt",
                  "archiviert", "geloescht")

_WORKER = f"{os.getpid()}-{uuid.uuid4().hex[:6]}"
# Die Garantie "EIN Dokument je Inserat" haengt am Unique-Index auf
# cache_key. Der Serverstart legt ihn an; beweis_vormerken stellt ihn
# zusaetzlich einmal je Prozess und Datenbank sicher (Befund 10.09.2026:
# ohne Index legten 25 gleichzeitige Erstabrufe zwei Dokumente an).
_index_sicher: Set[str] = set()
_wecker: Optional[asyncio.Event] = None
_wecker_loop = None
_laufend: Set[asyncio.Task] = set()


def _jetzt() -> datetime:
    return datetime.now(timezone.utc)


def _event() -> asyncio.Event:
    """Wecker je Event-Loop (Tests erzeugen eigene Loops)."""
    global _wecker, _wecker_loop
    loop = asyncio.get_running_loop()
    if _wecker is None or _wecker_loop is not loop:
        _wecker = asyncio.Event()
        _wecker_loop = loop
    return _wecker


def _wecken() -> None:
    try:
        _event().set()
    except RuntimeError:  # kein laufender Loop
        pass


async def ensure_beweis_indexes(db) -> None:
    await db.inserat_beweise.create_index("cache_key", unique=True, name="beweis_je_inserat")
    await db.inserat_beweise.create_index("id", unique=True, name="beweis_id")
    await db.inserat_beweise.create_index([("status", 1), ("erstellt_am", 1)],
                                          name="beweis_status")
    await db.vehicles.create_index("inserat_schluessel", name="fahrzeug_inserat",
                                   sparse=True)


# Audit 13.09.2026 (#37): Nach einem gescheiterten Aufbau erst nach dieser
# Pause erneut versuchen (je Prozess und Datenbank).
INDEX_NEUVERSUCH_SEKUNDEN = 300
_index_versuch: Dict[str, float] = {}


async def beweis_indizes_sichern(db) -> bool:
    """Audit 13.09.2026 (#37): Wie ensure_beweis_indexes, aber wirft nie und
    trennt den Unique-Index (die Garantie "EIN Dokument je Inserat") von den
    uebrigen. Fehlt er, gibt es einen Betriebsalarm (sichtbar in
    /admin/betrieb und als Warnung in /ready) statt nur einer Log-Zeile; steht
    er wieder, wird der Alarm geschlossen. Die anderen drei Indizes sind
    Beiwerk und blockieren nichts. Liefert True, wenn der Unique-Index steht.

    Beweis-Dubletten werden bewusst NICHT automatisch bereinigt: beide Zeilen
    koennen PDFs mit IDs tragen, die Oberflaeche und Fahrer-App schon kennen."""
    import betrieb
    ref = "inserat_beweise.cache_key"
    try:
        await db.inserat_beweise.create_index("cache_key", unique=True,
                                              name="beweis_je_inserat")
    except Exception as exc:  # noqa: BLE001 — z.B. Dubletten im Altbestand
        log.error("Beweisdokumente: Unique-Index beweis_je_inserat fehlt (%s) — "
                  "doppelte Dokumente moeglich", exc)
        await betrieb.alarm(db, "unique_index_fehlt", ref=ref, fehler=str(exc)[:300],
                            hinweis="Doppelte cache_key in inserat_beweise von Hand "
                                    "pruefen; der Index wird danach automatisch angelegt.")
        return False
    await betrieb.alarm_schliessen(db, "unique_index_fehlt", ref=ref)
    try:
        await db.inserat_beweise.create_index("id", unique=True, name="beweis_id")
        await db.inserat_beweise.create_index([("status", 1), ("erstellt_am", 1)],
                                              name="beweis_status")
        await db.vehicles.create_index("inserat_schluessel", name="fahrzeug_inserat",
                                       sparse=True)
    except Exception as exc:  # noqa: BLE001
        log.warning("Beweis-Indizes unvollstaendig: %s", exc)
    return True


async def beweise_haengend(db, minuten: int = 15) -> int:
    """Audit 13.09.2026 (#38): Beweisdokumente, die abholbereit laenger als
    `minuten` warten. Prozessunabhaengig — faellt der Beweis-Worker flotten-
    weit aus, bleibt /ready sonst gruen, und die Nutzer sehen nur 409. Der
    Index beweis_status (status, erstellt_am) deckt die Abfrage."""
    jetzt = _jetzt()
    return await db.inserat_beweise.count_documents(
        {"status": "offen", "erstellt_am": {"$lt": jetzt - timedelta(minutes=minuten)},
         "$or": [{"naechster_versuch_ab": None},
                 {"naechster_versuch_ab": {"$lte": jetzt}}]})


def kanonische_url(quelle: Any, item_id: Any, url: Any) -> str:
    """Firmenneutrale Inserats-Adresse fuer Dokument und Oberflaeche: ohne
    Such-/Tracking-Parameter und Fragment (die gingen sonst an alle Firmen
    und ins PDF), Sonderzeichen im Pfad kodiert. mobile.de braucht die ID als
    Parameter und bekommt deshalb immer die Standardadresse."""
    from urllib.parse import quote, urlsplit, urlunsplit
    iid = str(item_id or "").strip()
    if str(quelle or "").startswith("mobile") and iid:
        return f"https://suchen.mobile.de/fahrzeuge/details.html?id={quote(iid, safe='')}"
    try:
        teile = urlsplit(str(url or "").strip())
    except ValueError:
        return ""
    if teile.scheme not in ("http", "https") or not teile.netloc:
        return ""
    pfad = quote(teile.path or "/", safe="/-._~%")
    return urlunsplit(("https", teile.netloc.lower(), pfad, "", ""))[:2000]


def oeffentlich(doc: Optional[dict]) -> Optional[dict]:
    """Sachfelder fuer die Oberflaeche — ohne Speicherpfad und Bearbeiter.
    Das Dokument kennt keine Ersteller-Firma: es gehoert zum Inserat."""
    if not doc:
        return None
    felder = ("id", "quelle", "item_id", "url", "status", "erstellt_am",
              "fertig_am", "daten_abgerufen_am", "pdf_bytes", "pdf_sha256",
              "fotos_eingebettet", "fotos_gesamt", "fehler")
    aus = {}
    for k in felder:
        v = doc.get(k)
        if isinstance(v, datetime):
            # Motor liefert Zeiten ohne Zeitzone (UTC) — ohne Zusatz laese der
            # Browser sie als Ortszeit (2 h daneben).
            v = (v if v.tzinfo else v.replace(tzinfo=timezone.utc)).isoformat()
        aus[k] = v
    return aus


# Runde 23 (11.09.2026): Datenstand beim ersten Gebrauch einfrieren. Vorher
# las der Worker die Fahrzeugdaten erst spaeter aus dem veraenderlichen
# listings_cache — nach Verzoegerung, Fehlversuch, Wiederbelebung oder einem
# erneuten Portalabruf zeigte das PDF einen NEUEREN Stand als beim
# ausloesenden Erstgebrauch (oder scheiterte, wenn der Eintrag per TTL weg war).
# Nicht eingefroren wird, was das PDF nie liest: interne Parser-Felder
# (_mock, _resolved_*), die Vorschaubilder der Antwort und die doppelte
# Fotoliste image_urls (foto_urls nimmt images vor image_urls).
_ANBIETER_ERSATZNAMEN = ("händler", "privatverkäufer", "privatanbieter")
_ANBIETER_KONTAKT = ("seller_address", "seller_phone", "seller_email")


def quelle_einfrieren(quelle: Any, daten: Any) -> Optional[Dict[str, Any]]:
    """Inseratsdaten fuer quelle_daten: nur, was das PDF braucht — und bei
    privaten/unbekannten Anbietern schon maskiert.

    Die Beweiszeile lebt laenger als der Zwischenspeicher (90 Tage und mehr,
    solange ein Vorgang sie haelt). Sie speichert deshalb nie mehr
    Personendaten, als das PDF selbst enthaelt: Freitexte wie
    beweis_pdf.kontaktdaten_maskieren, Name/Anschrift/Telefon/E-Mail als
    Platzhalter (das PDF druckt sie bei privaten Anbietern ohnehin nicht; der
    Platzhalter erhaelt dort den Hinweis auf entfernte Angaben). PLZ/Ort und
    die Ersatznamen ("Privatverkäufer") bleiben — PLZ/Ort stehen im PDF, die
    Ersatznamen bestimmen die Anbieterart. Haendler (und BEWEIS_PRIVATDATEN)
    bleiben unveraendert, weil das PDF sie vollstaendig druckt."""
    if not isinstance(daten, dict) or not daten:
        return None
    aus = {k: v for k, v in daten.items()
           if not str(k).startswith("_") and k != "images_thumbs"}
    if aus.get("images"):
        aus.pop("image_urls", None)
    from beweis_pdf import KONTAKT_ENTFERNT, kontaktdaten_maskieren, verkaeufer_art
    if BEWEIS_PRIVATDATEN or verkaeufer_art(aus, quelle) == "haendler":
        return aus
    gemaskt = kontaktdaten_maskieren(aus)
    for feld in _ANBIETER_KONTAKT:
        if gemaskt.get(feld):
            gemaskt[feld] = KONTAKT_ENTFERNT
    name = gemaskt.get("seller_name")
    if name and str(name).strip().lower() not in _ANBIETER_ERSATZNAMEN:
        gemaskt["seller_name"] = KONTAKT_ENTFERNT
    if gemaskt != aus:
        # Merker fuer _fuer_pdf ("_" = nie im PDF): hier wurde maskiert.
        gemaskt["_kontakt_maskiert"] = True
    return gemaskt


def _fuer_pdf(daten: Dict[str, Any]) -> Dict[str, Any]:
    """Eingefrorene Daten sind schon maskiert. beweis_pdf erkennt Maskierung
    aber nur am Unterschied vor/nach seiner eigenen und liesse den Hinweis
    auf entfernte Kontaktangaben weg, wenn nur Titel/Beschreibung eine
    enthielten. seller_email wird bei privaten Anbietern nie gedruckt, loest
    den Hinweis aber aus — nur fuer den Aufbau, nie gespeichert."""
    if daten.get("_kontakt_maskiert") and not BEWEIS_PRIVATDATEN \
            and not any(daten.get(f) for f in _ANBIETER_KONTAKT):
        from beweis_pdf import KONTAKT_ENTFERNT
        return dict(daten, seller_email=KONTAKT_ENTFERNT)
    return daten


async def beweis_vormerken(db, *, cache_key: str, quelle: str, item_id: Any,
                           url: str, anlass: str, daten: Optional[dict] = None,
                           abgerufen_am: Optional[datetime] = None) -> Optional[dict]:
    """Beweisdokument fuer ein Inserat vormerken — idempotent, wirft nie.
    Liefert die (neue oder bestehende) Zeile als oeffentliche Sachfelder.

    daten/abgerufen_am (Runde 23): Stand des ausloesenden Abrufs. Er wird nur
    beim Anlegen gespeichert ($setOnInsert) — spaetere Aufrufe, andere
    Firmen, Wiederbelebung und neue Portalabrufe aendern ihn nie."""
    if not cache_key:
        return None
    marke = f"{id(db.client) if hasattr(db, 'client') else id(db)}:{getattr(db, 'name', '')}"
    if marke not in _index_sicher:
        # Audit 13.09.2026 (#37): fail-open bleibt (Beweis darf den Abruf nie
        # brechen), aber sichtbar (Betriebsalarm) und gedrosselt — vorher
        # startete jeder Vergleich einen scheiternden Unique-Aufbau.
        zuletzt = _index_versuch.get(marke)
        if zuletzt is None or time.monotonic() - zuletzt >= INDEX_NEUVERSUCH_SEKUNDEN:
            if await beweis_indizes_sichern(db):
                _index_sicher.add(marke)
                _index_versuch.pop(marke, None)
            else:
                _index_versuch[marke] = time.monotonic()
    felder = {"_id": 0, "id": 1, "status": 1, "quelle": 1, "item_id": 1, "url": 1,
              "erstellt_am": 1, "fertig_am": 1, "pdf_bytes": 1, "fehler": 1,
              "fotos_eingebettet": 1, "fotos_gesamt": 1, "daten_abgerufen_am": 1,
              "pdf_sha256": 1}
    neu = {"id": str(uuid.uuid4()), "cache_key": cache_key,
           "quelle": quelle, "item_id": str(item_id or ""),
           "url": kanonische_url(quelle, item_id, url), "status": "offen", "versuche": 0,
           "anlass": anlass, "erstellt_am": _jetzt()}
    try:
        eingefroren = quelle_einfrieren(quelle, daten)
    except Exception as exc:  # noqa: BLE001 — dann wie Altbestand aus dem Cache
        log.warning("Beweisdokument %s: Datenstand nicht eingefroren: %s", cache_key, exc)
        eingefroren = None
    if eingefroren:
        neu["quelle_daten"] = eingefroren
        neu["quelle_abgerufen_am"] = abgerufen_am or _jetzt()
    try:
        try:
            doc = await db.inserat_beweise.find_one_and_update(
                {"cache_key": cache_key},
                {"$setOnInsert": neu},
                upsert=True, projection=felder, return_document=ReturnDocument.AFTER)
        except DuplicateKeyError:
            # Zwei gleichzeitige Erstnutzungen: der andere hat angelegt.
            doc = await db.inserat_beweise.find_one({"cache_key": cache_key}, felder)
    except Exception as exc:  # noqa: BLE001 — Beweis darf den Abruf nie brechen
        log.warning("Beweisdokument fuer %s nicht vorgemerkt: %s", cache_key, exc)
        return None
    if doc and doc.get("status") == "fehlgeschlagen":
        # Nach einer Stoerung (Speicher, Neustart) bekaeme das Inserat sonst
        # nie ein Dokument: beim naechsten Gebrauch erneut versuchen, aber
        # fruehestens nach WIEDERBELEBEN_MINUTEN (kein Dauerfeuer). Ein
        # Grabstein (geloescht) wird nie wiederbelebt.
        try:
            r = await db.inserat_beweise.update_one(
                {"cache_key": cache_key, "status": "fehlgeschlagen",
                 "$or": [{"fehlgeschlagen_am": {"$exists": False}},
                         {"fehlgeschlagen_am": None},
                         {"fehlgeschlagen_am": {"$lt": _jetzt() - timedelta(
                             minutes=WIEDERBELEBEN_MINUTEN)}}]},
                {"$set": {"status": "offen", "versuche": 0, "fehler": None,
                          "naechster_versuch_ab": None, "bearbeitung_bis": None}})
            if r.modified_count:
                doc = dict(doc, status="offen", fehler=None)
        except Exception as exc:  # noqa: BLE001
            log.warning("Beweisdokument %s nicht wiederbelebt: %s", cache_key, exc)
    if doc and doc.get("status") == "offen":
        _wecken()
    return oeffentlich(doc)


# ---------------------------------------------------------------- Worker --
async def _aufraeumen(db) -> None:
    """Verwaiste Bearbeitungen (Worker abgestuerzt/neu gestartet) freigeben."""
    async for d in db.inserat_beweise.find(
            {"status": "in_arbeit", "bearbeitung_bis": {"$lt": _jetzt()}},
            {"_id": 0, "id": 1, "versuche": 1}):
        if (d.get("versuche") or 0) >= MAX_VERSUCHE:
            await db.inserat_beweise.update_one(
                {"id": d["id"], "status": "in_arbeit"},
                {"$set": {"status": "fehlgeschlagen", "bearbeitung_bis": None,
                          "fehlgeschlagen_am": _jetzt(),
                          "fehler": "Erstellung mehrfach abgebrochen"}})
        else:
            await db.inserat_beweise.update_one(
                {"id": d["id"], "status": "in_arbeit"},
                {"$set": {"status": "offen", "bearbeitung_bis": None}})


async def _beanspruchen(db) -> Optional[dict]:
    jetzt = _jetzt()
    return await db.inserat_beweise.find_one_and_update(
        {"status": "offen",
         "$or": [{"naechster_versuch_ab": {"$exists": False}},
                 {"naechster_versuch_ab": None},
                 {"naechster_versuch_ab": {"$lte": jetzt}}]},
        {"$set": {"status": "in_arbeit", "bearbeiter": _WORKER,
                  "bearbeitung_bis": jetzt + timedelta(seconds=BEARBEITUNG_SEKUNDEN)},
         "$inc": {"versuche": 1}},
        sort=[("erstellt_am", 1)], projection={"_id": 0},
        return_document=ReturnDocument.AFTER)


def foto_urls(daten: Dict[str, Any]) -> List[str]:
    roh = daten.get("images") or daten.get("image_urls") or []
    aus: List[str] = []
    for u in roh if isinstance(roh, list) else []:
        if isinstance(u, str) and u.startswith("https://") and u not in aus:
            aus.append(u)
    return aus


async def _fotos_laden(urls: List[str]) -> List[Optional[bytes]]:
    from bild_proxy import laden_fuer_pdf
    sperre = asyncio.Semaphore(FOTO_PARALLEL)

    async def _eins(i: int, u: str) -> Optional[bytes]:
        async with sperre:
            try:
                return await laden_fuer_pdf(
                    u,
                    _FOTO_KANTE_ERSTE if i == 0 else _FOTO_KANTE,
                    _FOTO_QUALITAET_ERSTE if i == 0 else _FOTO_QUALITAET)
            except Exception:  # noqa: BLE001 — ein Foto darf das Dokument nie verhindern
                return None

    return list(await asyncio.gather(*(_eins(i, u) for i, u in enumerate(urls))))


def speicher_key(doc: dict) -> str:
    """Alter, fester Schluessel (Dokumente vor dem Versuchs-Schluessel)."""
    from beweis_pdf import quelle_norm
    return f"beweise/{quelle_norm(doc.get('quelle'))}/{doc['id']}.pdf"


def neuer_speicher_key(doc: dict) -> str:
    """Schluessel je Versuch: eine verspaetete Datei eines abgebrochenen
    Versuchs ueberschreibt nie das fertige Dokument (Pruefsumme stimmt)."""
    from beweis_pdf import quelle_norm
    return f"beweise/{quelle_norm(doc.get('quelle'))}/{doc['id']}-{uuid.uuid4().hex[:10]}.pdf"


async def beweis_erzeugen(db, doc: dict) -> bool:
    """Eine beanspruchte Zeile ausfuehren. True = fertig."""
    eingefroren = doc.get("quelle_daten")
    if isinstance(eingefroren, dict) and eingefroren:
        # Runde 23 (11.09.2026): der beim Erstgebrauch eingefrorene Stand —
        # nie der (inzwischen evtl. neu abgerufene oder abgelaufene) Cache.
        daten = eingefroren
        abgerufen_am, cache_url = doc.get("quelle_abgerufen_am"), None
    else:
        # Altbestand (vor Runde 23 vorgemerkt) und Vormerkungen ohne Daten
        # (routes/listings.compare zu altem Cache-Eintrag): wie bisher.
        cache = await db.listings_cache.find_one(
            {"cache_key": doc["cache_key"]},
            {"_id": 0, "data": 1, "fetched_at": 1, "url": 1})
        daten = (cache or {}).get("data")
        if not isinstance(daten, dict) or not daten:
            raise RuntimeError("Inseratsdaten fehlen im Zwischenspeicher")
        abgerufen_am, cache_url = (cache or {}).get("fetched_at"), (cache or {}).get("url")
    urls = foto_urls(daten)
    fotos = await _fotos_laden(urls[:BEWEIS_FOTOS_MAX])
    from beweis_pdf import beweis_pdf
    erstellt = _jetzt()
    pdf = await asyncio.to_thread(
        beweis_pdf, quelle=doc.get("quelle"), daten=_fuer_pdf(daten),
        url=kanonische_url(doc.get("quelle"), doc.get("item_id"),
                           doc.get("url") or cache_url
                           or daten.get("detail_url") or ""),
        item_id=doc.get("item_id") or "", beweis_id=doc["id"],
        abgerufen_am=abgerufen_am, erstellt_am=erstellt,
        fotos=fotos, foto_urls=urls, privatdaten=BEWEIS_PRIVATDATEN)
    key = neuer_speicher_key(doc)
    # Erst vermerken, dann schreiben: jeder je geschriebene Schluessel steht in
    # alle_keys und wird beim Verfall mit geloescht (auch verwaiste).
    await db.inserat_beweise.update_one({"id": doc["id"]}, {"$addToSet": {"alle_keys": key}})
    from storage_service import save_async
    await save_async(key, pdf)
    r = await db.inserat_beweise.update_one(
        {"id": doc["id"], "status": "in_arbeit", "bearbeiter": doc.get("bearbeiter")},
        {"$set": {"status": "fertig", "pdf_key": key, "pdf_bytes": len(pdf),
                  "pdf_sha256": hashlib.sha256(pdf).hexdigest(),
                  "fertig_am": erstellt,
                  "daten_abgerufen_am": abgerufen_am,
                  "fotos_eingebettet": sum(1 for f in fotos if f),
                  "fotos_gesamt": len(urls), "fehler": None,
                  "bearbeitung_bis": None, "naechster_versuch_ab": None}})
    # modified_count 0: ein anderer Worker hat uebernommen; die eigene Datei
    # steht in alle_keys und wird beim Verfall geloescht.
    return r.modified_count == 1


async def _herzschlag(db, doc: dict) -> None:
    """Lease verlaengern, solange die Erzeugung laeuft; endet, sobald die
    Zeile nicht mehr diesem Worker gehoert."""
    while True:
        await asyncio.sleep(HERZSCHLAG_SEKUNDEN)
        try:
            r = await db.inserat_beweise.update_one(
                {"id": doc["id"], "status": "in_arbeit", "bearbeiter": doc.get("bearbeiter")},
                {"$set": {"bearbeitung_bis": _jetzt() + timedelta(seconds=BEARBEITUNG_SEKUNDEN)}})
            if r.matched_count == 0:
                return
        except asyncio.CancelledError:
            raise
        except Exception:  # noqa: BLE001 — naechster Schlag versucht es erneut
            continue


async def _bearbeiten(db, doc: dict) -> None:
    puls = asyncio.create_task(_herzschlag(db, doc))
    try:
        await asyncio.wait_for(beweis_erzeugen(db, doc), timeout=ERZEUGUNG_MAX_SEKUNDEN)
    except Exception as exc:  # noqa: BLE001 — auch Zeitlimit (TimeoutError)
        versuche = doc.get("versuche") or 1
        endgueltig = versuche >= MAX_VERSUCHE
        grund = ("Zeitlimit ueberschritten" if isinstance(exc, asyncio.TimeoutError)
                 else str(exc) or exc.__class__.__name__)[:300]
        log.warning("Beweisdokument %s (%s) Versuch %s fehlgeschlagen: %s",
                    doc.get("id"), doc.get("cache_key"), versuche, grund)
        setzen = {"status": "fehlgeschlagen" if endgueltig else "offen",
                  "fehler": grund, "bearbeitung_bis": None,
                  "naechster_versuch_ab": None if endgueltig
                  else _jetzt() + timedelta(seconds=60 * versuche)}
        if endgueltig:
            setzen["fehlgeschlagen_am"] = _jetzt()
        await db.inserat_beweise.update_one(
            {"id": doc["id"], "status": "in_arbeit", "bearbeiter": doc.get("bearbeiter")},
            {"$set": setzen})
        if endgueltig:
            try:
                import betrieb
                await betrieb.alarm(db, "beweis_fehlgeschlagen", ref=doc.get("cache_key") or "",
                                    fehler=grund)
            except Exception:  # noqa: BLE001 — Alarm ist Beiwerk
                pass
    finally:
        puls.cancel()


async def run_beweis_worker_forever(db) -> None:
    sperre = asyncio.Semaphore(BEWEIS_PARALLEL)
    letzte_aufraeumung = 0.0
    while True:
        try:
            if time.monotonic() - letzte_aufraeumung > 60:
                letzte_aufraeumung = time.monotonic()
                await _aufraeumen(db)
            await sperre.acquire()
            try:
                doc = await _beanspruchen(db)
            except BaseException:
                sperre.release()
                raise
            if not doc:
                sperre.release()
                wecker = _event()
                wecker.clear()
                try:
                    await asyncio.wait_for(wecker.wait(), timeout=5.0)
                except asyncio.TimeoutError:
                    pass
                continue

            async def _lauf(d=doc):
                try:
                    await _bearbeiten(db, d)
                finally:
                    sperre.release()

            aufgabe = asyncio.create_task(_lauf())
            _laufend.add(aufgabe)
            aufgabe.add_done_callback(_laufend.discard)
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001 — Schleife darf nie sterben
            log.warning("Beweis-Schleife: %s", exc)
            await asyncio.sleep(5)


# ------------------------------------------------------------ Fahrzeuge --
def inserat_schluessel(fahrzeug: Optional[dict]) -> Optional[str]:
    """cache_key des Inserats zu einem Fahrzeug. Neu verglichene Fahrzeuge
    tragen inserat_schluessel; aeltere werden ueber die Inserats-Adresse
    zugeordnet (bei AutoScout24 weicht die Anzeigen-ID von der ID in der
    Adresse ab — deshalb NICHT aus mobile_ad_id raten)."""
    if not fahrzeug:
        return None
    if fahrzeug.get("inserat_schluessel"):
        return str(fahrzeug["inserat_schluessel"])
    daten = fahrzeug.get("data") or {}
    from listing_identity import get_listing_identity
    for u in (daten.get("detail_url"), daten.get("kleinanzeigen_url")):
        if isinstance(u, str) and u.startswith("http"):
            try:
                return get_listing_identity(u)["cache_key"]
            except Exception:  # noqa: BLE001
                continue
    quelle, ad = fahrzeug.get("quelle"), fahrzeug.get("mobile_ad_id")
    if quelle in ("mobile", "kleinanzeigen") and ad:
        return f"{quelle}:{ad}"
    return None


async def beweis_fuer_schluessel(db, cache_key: Optional[str]) -> Optional[dict]:
    if not cache_key:
        return None
    return await db.inserat_beweise.find_one({"cache_key": cache_key}, {"_id": 0})


# ---------------------------------------------------------- Aufbewahrung --
async def _altbestand_zuordnen(db, limit: int = 500, filt: Optional[dict] = None) -> int:
    """Fahrzeuge von vor der Umstellung bekommen ihren inserat_schluessel
    (sonst hielte ihr Kaufvertrag das Dokument nicht). Nicht zuordenbare
    bekommen None und werden nicht erneut angefasst."""
    n = 0
    async for v in db.vehicles.find(
            {"inserat_schluessel": {"$exists": False}, **(filt or {})},
            {"_id": 0, "id": 1, "dealer_id": 1, "quelle": 1, "mobile_ad_id": 1,
             "data.detail_url": 1, "data.kleinanzeigen_url": 1}).limit(limit):
        await db.vehicles.update_one(
            {"id": v["id"], "dealer_id": v.get("dealer_id"),
             "inserat_schluessel": {"$exists": False}},
            {"$set": {"inserat_schluessel": inserat_schluessel(v)}})
        n += 1
    return n


async def _gehalten(db, cache_key: str) -> bool:
    """Haelt ein echter Vorgang das Dokument? Bestand/Kauf/Abholung/Verkauf
    oder ein Vertrag, Termin bzw. Inserat zum Fahrzeug der jeweiligen Firma.
    Bloss verglichene, stornierte oder archivierte Fahrzeuge halten nicht."""
    fz = await db.vehicles.find(
        {"inserat_schluessel": cache_key},
        {"_id": 0, "id": 1, "dealer_id": 1, "lifecycle": 1}).to_list(500)
    if not fz:
        return False
    if any((v.get("lifecycle") or "verglichen") not in OHNE_GESCHAEFT for v in fz):
        return True
    paare = [{"vehicle_id": v["id"], "dealer_id": v.get("dealer_id")} for v in fz]
    for coll in ("generated_pdfs", "appointments", "resale_listings"):
        if await db[coll].count_documents({"$or": paare}, limit=1):
            return True
    return False


async def beweise_verfallen(db, now: Optional[datetime] = None, seite: int = 500,
                            max_seiten: int = 20, altbestand_filter: Optional[dict] = None) -> int:
    """Dateien nach Ablauf der Frist loeschen, sofern kein echter Vorgang
    das Dokument haelt. Die Zeile bleibt als Grabstein. Gehaltene Zeilen
    werden erst nach einem Tag erneut geprueft — sonst blockierten 500
    gehaltene Zeilen jeden Lauf und juengere verfielen nie."""
    now = now or _jetzt()
    try:
        await _altbestand_zuordnen(db, filt=altbestand_filter)
    except Exception as exc:  # noqa: BLE001
        log.warning("Beweisdokumente: Altbestand-Zuordnung: %s", exc)
    grenze = now - timedelta(days=BEWEIS_AUFBEWAHRUNG_TAGE)
    filt = {"status": {"$in": ["fertig", "fehlgeschlagen"]}, "erstellt_am": {"$lt": grenze},
            "$or": [{"verfall_pruefen_ab": {"$exists": False}}, {"verfall_pruefen_ab": None},
                    {"verfall_pruefen_ab": {"$lte": now}}]}
    geloescht = 0
    for _ in range(max_seiten):
        kandidaten = await db.inserat_beweise.find(
            filt, {"_id": 0, "id": 1, "cache_key": 1, "quelle": 1, "pdf_key": 1,
                   "alle_keys": 1}).sort("erstellt_am", 1).to_list(seite)
        if not kandidaten:
            break
        for d in kandidaten:
            if await _gehalten(db, d["cache_key"]):
                await db.inserat_beweise.update_one(
                    {"id": d["id"]}, {"$set": {"verfall_pruefen_ab": now + timedelta(days=1)}})
                continue
            from storage_service import loeschen_oder_vormerken
            keys = {k for k in (d.get("alle_keys") or []) if k}
            keys.add(d.get("pdf_key") or speicher_key(d))
            for key in sorted(keys):
                # False = nicht geloescht, aber sicher vorgemerkt (Nachholung).
                await loeschen_oder_vormerken(
                    db, key=key, grund="beweis_verfall",
                    ref={"collection": "inserat_beweise", "id": d["id"]})
            await db.inserat_beweise.update_one(
                {"id": d["id"]},
                {"$set": {"status": "geloescht", "geloescht_am": now, "pdf_key": None},
                 # Runde 23: der eingefrorene Datenstand geht mit — Grabstein
                 # ohne Inseratsdaten.
                 "$unset": {"url": "", "pdf_sha256": "", "fehler": "", "alle_keys": "",
                            "quelle_daten": ""}})
            geloescht += 1
        if len(kandidaten) < seite:
            break
    if geloescht:
        log.info("[cleanup] %s Beweisdokumente nach %s Tagen geloescht",
                 geloescht, BEWEIS_AUFBEWAHRUNG_TAGE)
    return geloescht
