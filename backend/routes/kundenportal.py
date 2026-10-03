# -*- coding: utf-8 -*-
"""Firmenseite + Kundenportal (Wunsch Ahmad 29.09.2026).

Jede Firma bekommt eine eigene oeffentliche Seite — erreichbar ueber ihre Adresse
(Unterdomain <slug>.<FIRMEN_DOMAIN>, eine eigene Kundendomain per DNS/CNAME oder als
Rueckfall /firma/<slug> auf der Hauptadresse): Logo, kurzer "Ueber uns"-Text, ein paar
Bilder, Kontakt/Impressum und unten der Kasten "Kundenportal".

Ablauf:
  1. Sucher oder Chef gibt in der App einen Kaufvertrag frei (POST /contracts/{id}/portal):
     das System erzeugt einen 6-stelligen Code (ohne verwechselbare Zeichen), gebunden an
     GENAU diese Vertragsfassung, gueltig PORTAL_CODE_TAGE Tage; der Sucher gibt ihn dem Kunden.
  2. Der Kunde gibt den Code auf der Firmenseite ein (POST /public/portal/oeffnen) — je Adresse
     und je Firma gedrosselt (raten ist damit praktisch unmoeglich) — bekommt eine kurze
     Sitzung, sieht den Vertrag (GET .../pdf) und unterschreibt mit Finger oder Maus
     (POST .../unterschreiben).
  3. Die Unterschrift wird ins Vertrags-PDF gesetzt (Druckfassung, Kasten "Verkaeufer";
     im Kasten "Kaeufer" die in den Einstellungen hinterlegte Unterschrift des Chefs), mit
     Zeitstempel, Name und Herkunftsadresse als Nachweis; Sucher und Chef bekommen eine
     Meldung in der App ("Kaufvertrag bestaetigt"). Keine E-Mails (Entscheidung Ahmad).

Einrichtung (Entscheidung Ahmad 29.09.2026, "Weg A"): der Betreiber holt und verwaltet die
Kundendomains selbst und richtet die Firmenseite im Admin ein — dieselben Funktionen wie
fuer den Chef, plus "Domain pruefen" (DNS -> Proxy -> HTTPS -> Firmenseite).

Sicherheit: Codes sind je Firma eindeutig (Unique-Index, nur offene), laufen ab, gelten nur
fuer die aktuelle Fassung (eine Neuerzeugung macht den Code ungueltig), ein zurueckgezogener
Code ist tot. Falsche Eingaben werden je Besucheradresse (fail-closed) und je Firma gezaehlt.
Der oeffentliche PDF-Abruf traegt Cache-Control: no-store und noindex. Die Sitzung ist ein
kurzlebiges signiertes Token (JWT, 45 Minuten) — nie der Code selbst in der Adresse.
"""
import asyncio
import base64
import hashlib
import ipaddress
import logging
import os
import re
import secrets
import socket
from datetime import datetime, timedelta, timezone
from typing import Annotated, Any, Dict, List, Optional
from urllib.parse import urlparse

import jwt
from fastapi import APIRouter, Depends, Header, HTTPException, Request, Response
from pydantic import BaseModel, Field
from pymongo.errors import DuplicateKeyError

import auth as _auth
from deps import (current_chef, current_firma, current_super_admin, db, firma_gesperrt, ist_haupt_chef,
                  log_activity_sicher, now_iso)
from konfig import zahl_env
from rate_limiter import SlidingWindowRateLimiter, client_ip

log = logging.getLogger("autohandel.kundenportal")
router = APIRouter()

# ---------------------------------------------------------------- Einstellungen
PORTAL_CODE_TAGE = zahl_env("PORTAL_CODE_TAGE", 7, unten=1, oben=90)
PORTAL_SITZUNG_MINUTEN = 45
#: so lange gehoert ein Unterschrifts-Versuch dem Aufruf, der ihn begonnen hat (PDF erzeugen, speichern)
SIGNIER_ANSPRUCH_S = 60
CODE_LAENGE = 6
# ohne 0/O und 1/I — am Telefon und auf dem Zettel nicht zu verwechseln
CODE_ZEICHEN = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"
SLUG_RE = re.compile(r"^[a-z0-9](?:[a-z0-9-]{1,38}[a-z0-9])$")
HOST_RE = re.compile(r"^(?=.{4,253}$)(?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z]{2,63}$")
# Unterdomains, die nie eine Firma sein duerfen (Betrieb, Technik, Verwechslung)
RESERVIERTE_SLUGS = frozenset({
    "app", "www", "api", "admin", "mail", "smtp", "imap", "ftp", "static", "cdn", "kunden", "login",
    "fahrer", "markt", "status", "help", "hilfe", "support", "dev", "test", "staging", "ns1", "ns2",
    "autodiscover", "webmail", "portal", "firma", "auto-schnellkauf", "autoschnell"})
UEBER_UNS_MAX = 2000
BILDER_MAX = 6
DOMAINS_MAX = 5
UNTERSCHRIFT_B64_MAX = 3_000_000
MELDUNG_TAGE = 90
PRUEFUNG_ZEITLIMIT_S = 8

# Falsche Codes: je Besucheradresse hart (fail-closed — ohne Datenbank lieber sperren als raten
# lassen), je Firma weich (ein Buero mit vielen Kunden hinter einer Adresse bleibt arbeitsfaehig).
# Pruefliste 30.09.2026: gezaehlt wird jeder Aufruf VOR der Pruefung (atomar), ein ERFOLGREICHER
# Aufruf gibt seinen Versuch danach zurueck (erstatten) — vorher sperrten zehn richtige Codes aus
# demselben WLAN (Autohaus, Hotel, Mobilfunk) den elften Kunden aus. Ebenso bei der Unterschrift.
_code_limiter_ip = SlidingWindowRateLimiter(max_attempts=10, window_seconds=600,
                                            name="portal_code_ip", fail_closed=True)
_code_limiter_firma = SlidingWindowRateLimiter(max_attempts=200, window_seconds=600,
                                               name="portal_code_firma")
_unterschrift_limiter = SlidingWindowRateLimiter(max_attempts=20, window_seconds=600,
                                                 name="portal_unterschrift")
# Wunsch Ahmad 30.09.2026: ueber dieselbe Adresse (IP) sind 500 Aufrufe je Minute moeglich, danach
# 2 Minuten warten — fuer die Code-Eingabe, die Unterschrift (Kunde) und das Erzeugen von Codes
# (Sucher/Chef). Zaehlt JEDEN Aufruf, auch erfolgreiche. Die Bremsen fuer FALSCHE Codes oben bleiben
# daneben bestehen: sie verhindern das Durchprobieren von Codes und zaehlen nur Fehlversuche.
PORTAL_IP_JE_MINUTE = zahl_env("PORTAL_IP_JE_MINUTE", 500, unten=1)
PORTAL_IP_WARTEN_S = zahl_env("PORTAL_IP_WARTEN_SEKUNDEN", 120, unten=0)
_portal_limiter_ip = SlidingWindowRateLimiter(max_attempts=PORTAL_IP_JE_MINUTE, window_seconds=60,
                                              name="portal_ip_minute", sperre_sekunden=PORTAL_IP_WARTEN_S)
_erzeugen_limiter_ip = SlidingWindowRateLimiter(max_attempts=PORTAL_IP_JE_MINUTE, window_seconds=60,
                                                name="portal_erzeugen_ip", sperre_sekunden=PORTAL_IP_WARTEN_S)


def _zu_viele_anfragen() -> HTTPException:
    minuten = max(1, round(PORTAL_IP_WARTEN_S / 60))
    return HTTPException(429, f"Zu viele Anfragen von diesem Anschluss — bitte in {minuten} Minuten erneut.",
                         headers={"Retry-After": str(max(1, PORTAL_IP_WARTEN_S))})


# ---------------------------------------------------------------- Adressen
def firmen_domain() -> str:
    """Basisdomain der Firmen-Unterdomains: FIRMEN_DOMAIN, sonst die Hauptadresse (FRONTEND_URL)
    ohne fuehrendes app./www. — aus app.auto-schnellkauf.de wird auto-schnellkauf.de."""
    d = (os.environ.get("FIRMEN_DOMAIN") or "").strip().lower().strip(".")
    if d:
        return d
    host = (urlparse(os.environ.get("FRONTEND_URL") or "http://localhost:3000").hostname or "localhost").lower()
    for praefix in ("app.", "www."):
        if host.startswith(praefix):
            host = host[len(praefix):]
    return host


def _hauptadresse() -> str:
    return (os.environ.get("FRONTEND_URL") or "http://localhost:3000").split("?")[0].rstrip("/")


def unterdomain_moeglich() -> bool:
    """Lokal (localhost, IP) gibt es keine Unterdomains — dann zaehlt nur der Pfad."""
    d = firmen_domain()
    return "." in d and not re.fullmatch(r"[0-9.]+", d) and d != "localhost"


def firmen_url(slug: str) -> str:
    """Adresse der Firmenseite: https://<slug>.<domain>, lokal <FRONTEND_URL>/firma/<slug>."""
    if unterdomain_moeglich():
        return f"https://{slug}.{firmen_domain()}"
    return f"{_hauptadresse()}/firma/{slug}"


def slug_aus_host(host: Optional[str]) -> Optional[str]:
    """<slug>.<firmen_domain> -> slug; Hauptadresse, reservierte Namen und fremde Hosts -> None."""
    h = (host or "").strip().lower().split(":")[0].strip(".")
    basis = firmen_domain()
    if not h or not basis or not h.endswith("." + basis):
        return None
    sub = h[:-(len(basis) + 1)]
    if not sub or "." in sub or sub in RESERVIERTE_SLUGS or not SLUG_RE.match(sub):
        return None
    return sub


def host_normalisieren(host: Optional[str]) -> str:
    return (host or "").strip().lower().split(":")[0].strip(".")[:253]


def proxy_hosts() -> List[str]:
    """FIRMEN_HOSTS (dieselbe Liste wie im Proxy): Wildcards und Domains, Leerzeichen-getrennt."""
    return [h.strip().lower() for h in (os.environ.get("FIRMEN_HOSTS") or "").replace(",", " ").split() if h.strip()]


def proxy_kennt(host: str) -> bool:
    """Bedient der Proxy diese Adresse? Exakt oder per Wildcard (*.domain = genau eine Ebene)."""
    h = host_normalisieren(host)
    haupt = (urlparse(_hauptadresse()).hostname or "").lower()
    if h == haupt:
        return True
    for eintrag in proxy_hosts():
        if eintrag == h:
            return True
        if eintrag.startswith("*.") and h.endswith(eintrag[1:]) and "." not in h[:-len(eintrag[1:])]:
            return True
    return False


# ---------------------------------------------------------------- Firma (oeffentlich)
_FIRMA_FELDER = {"_id": 0, "id": 1, "user_id": 1, "company_name": 1, "logo_url": 1, "address": 1,
                 "zip_code": 1, "city": 1, "phone": 1, "email": 1, "opening_hours": 1,
                 "webseite": 1, "unterschrift_key": 1, "active": 1, "loeschung": 1}


async def firma_offen(d: Optional[dict]) -> bool:
    """Darf diese Firma oeffentlich auftreten (Firmenseite, Code-Eingabe, laufende Portal-Sitzung)?
    Firmenseite eingeschaltet, Firma nicht gesperrt (gesperrter Chef = gesperrte Firma, deps.firma_gesperrt)
    und nicht in Loeschung. Pruefliste 30.09.2026: vorher blieb die Seite einer gesperrten Firma
    erreichbar, und eine laufende Sitzung ueberlebte das Abschalten der Firmenseite."""
    if not d or d.get("active") is False or not (d.get("webseite") or {}).get("aktiv"):
        return False
    if (d.get("loeschung") or {}).get("status") == "laeuft":
        return False
    return not await firma_gesperrt(d.get("id"))


async def firma_laden(*, host: Optional[str] = None, slug: Optional[str] = None) -> Optional[dict]:
    """Firma zur Adresse: erst der Pfad-Slug, dann die Unterdomain, dann eine eingetragene
    Kundendomain. Nur aktive Firmenseiten (webseite.aktiv) gesperrter Firmen bleiben aus."""
    filt: Optional[Dict[str, Any]] = None
    if slug:
        s = slug.strip().lower()
        if SLUG_RE.match(s):
            filt = {"webseite.slug": s}
    elif host:
        h = host_normalisieren(host)
        s = slug_aus_host(h)
        if s:
            filt = {"webseite.slug": s}
        elif HOST_RE.match(h):
            filt = {"webseite.domains": h}
    if not filt:
        return None
    d = await db.dealers.find_one({**filt, "webseite.aktiv": True}, _FIRMA_FELDER)
    if not await firma_offen(d):
        return None
    return d


def firma_oeffentlich(d: dict) -> dict:
    w = d.get("webseite") or {}
    bilder = [f"/api/files/{k}" for k in (w.get("bilder") or []) if isinstance(k, str)]
    return {
        "slug": w.get("slug"),
        "firma": d.get("company_name") or "",
        "logo_url": d.get("logo_url") or "",
        "ueber_uns": w.get("ueber_uns") or "",
        "bilder": bilder,
        # Vorlage Ahmad 29.09.2026: Titelbild = erstes Bild, grosse Ueberschrift + Unterzeile, Social-Links
        "titelbild": bilder[0] if bilder else "",
        "titel": (w.get("titel") or "").strip() or titel_vorgabe(d.get("city")),
        "untertitel": (w.get("untertitel") or "").strip() or UNTERTITEL_VORGABE,
        "social": {k: (w.get(k) or "") for k in SOCIAL_FELDER},
        "plattform_url": _hauptadresse(),
        "kontakt": {"adresse": d.get("address") or "", "plz": d.get("zip_code") or "",
                    "ort": d.get("city") or "", "telefon": d.get("phone") or "",
                    "email": d.get("email") or "", "oeffnungszeiten": d.get("opening_hours") or ""},
        "url": firmen_url(w.get("slug") or ""),
        "portal_aktiv": True,
    }


@router.get("/public/firma")
async def public_firma(host: Optional[str] = None, slug: Optional[str] = None):
    """Oeffentliche Firmendaten fuer die Firmenseite (ohne Anmeldung). 404, wenn es zu der
    Adresse keine aktive Firmenseite gibt — bewusst ohne Unterschied zwischen 'gibt es nicht'
    und 'abgeschaltet'."""
    d = await firma_laden(host=host, slug=slug)
    if not d:
        raise HTTPException(404, "Zu dieser Adresse gibt es keine Firmenseite.")
    return firma_oeffentlich(d)


# ---------------------------------------------------------------- Firmenseite (Chef / Betreiber)
class WebseiteIn(BaseModel):
    slug: Optional[str] = Field(default=None, max_length=60)
    aktiv: Optional[bool] = None
    ueber_uns: Optional[str] = Field(default=None, max_length=UEBER_UNS_MAX)
    domains: Optional[List[str]] = Field(default=None, max_length=DOMAINS_MAX)
    # Vorlage Ahmad 29.09.2026 (Bild "Norden Autoankauf"): grosse Ueberschrift + Unterzeile ueber dem Titelbild,
    # unten "Follow Us" — leer = Vorgabe ("Ihr Partner fuer den Autoankauf in <Ort>", "Schnell, sicher & fair")
    titel: Optional[str] = Field(default=None, max_length=90)
    untertitel: Optional[str] = Field(default=None, max_length=140)
    facebook: Optional[str] = Field(default=None, max_length=200)
    instagram: Optional[str] = Field(default=None, max_length=200)


SOCIAL_FELDER = ("facebook", "instagram")


def _social_pruefen(wert: str, name: str) -> str:
    """Nur echte https-Adressen (kein javascript:, keine nackten Namen) — leer entfernt den Link."""
    w = (wert or "").strip()
    if not w:
        return ""
    if not re.match(r"^https://[a-z0-9.-]+\.[a-z]{2,}(/[^\s]*)?$", w, re.I):
        raise HTTPException(400, f"{name.capitalize()}: bitte die vollständige Adresse mit https:// eintragen.")
    return w[:200]


def titel_vorgabe(ort: Optional[str]) -> str:
    o = (ort or "").strip()
    return f"Ihr Partner für den Autoankauf in {o}" if o else "Ihr Partner für den Autoankauf"


UNTERTITEL_VORGABE = "Schnell, sicher & fair"


class BildIn(BaseModel):
    bild_b64: str = Field(max_length=12_000_000)      # ~8 MB Bild als Base64 (data-URL erlaubt)


class UnterschriftIn(BaseModel):
    bild_b64: str = Field(max_length=UNTERSCHRIFT_B64_MAX)


def _webseite_antwort(d: dict, ist_chef: bool) -> dict:
    w = dict(d.get("webseite") or {})
    slug = w.get("slug") or ""
    return {
        "webseite": {"slug": slug, "aktiv": bool(w.get("aktiv")), "ueber_uns": w.get("ueber_uns") or "",
                     "bilder": [{"key": k, "url": f"/api/files/{k}"} for k in (w.get("bilder") or [])],
                     "domains": list(w.get("domains") or []),
                     "titel": w.get("titel") or "", "untertitel": w.get("untertitel") or "",
                     "facebook": w.get("facebook") or "", "instagram": w.get("instagram") or ""},
        "titel_vorgabe": titel_vorgabe(d.get("city")), "untertitel_vorgabe": UNTERTITEL_VORGABE,
        "url": firmen_url(slug) if slug else "",
        "url_pfad": f"{_hauptadresse()}/firma/{slug}" if slug else "",
        "firmen_domain": firmen_domain(),
        "unterdomain_moeglich": unterdomain_moeglich(),
        "unterschrift_vorhanden": bool(d.get("unterschrift_key")),
        "ist_chef": ist_chef,
        "firma": d.get("company_name") or "",
        # Stempel & Unterschrift (Wunsch Ahmad 02.10.2026): Vorbelegung des Stempel-Generators
        "firma_daten": {"name": d.get("company_name") or "", "strasse": d.get("address") or "",
                        "plz": d.get("zip_code") or "", "ort": d.get("city") or "",
                        "tel": d.get("phone") or "", "mail": d.get("email") or ""},
        "slug_vorschlag": slug_vorschlag(d.get("company_name") or ""),
        "proxy_hosts": proxy_hosts(),
        "bilder_max": BILDER_MAX, "domains_max": DOMAINS_MAX, "code_tage": PORTAL_CODE_TAGE,
    }


def slug_vorschlag(name: str) -> str:
    """Firmenname -> Vorschlag fuer die Unterdomain (kfz-mueller-gmbh)."""
    t = (name or "").lower()
    for a, b in (("ä", "ae"), ("ö", "oe"), ("ü", "ue"), ("ß", "ss")):
        t = t.replace(a, b)
    t = re.sub(r"[^a-z0-9]+", "-", t).strip("-")
    t = re.sub(r"-{2,}", "-", t)[:40].strip("-")
    if len(t) < 3:
        t = (t + "-firma")[:40]
    return t


async def _firma_dok(dealer_id: str) -> dict:
    d = await db.dealers.find_one({"id": dealer_id}, _FIRMA_FELDER)
    if not d:
        raise HTTPException(404, "Firma nicht gefunden")
    return d


async def _nur_chef(user) -> None:
    if not await ist_haupt_chef(user):
        raise HTTPException(403, "Die Firmenseite ändert nur der Chef.")


async def _webseite_setzen(dealer_id: str, body: WebseiteIn, wer: str) -> dict:
    """Slug, Ein/Aus, Ueber-uns-Text und Kundendomains setzen (Chef ueber /dealer, Betreiber ueber /admin)."""
    setzen: Dict[str, Any] = {}
    if body.slug is not None:
        s = body.slug.strip().lower()
        if not SLUG_RE.match(s):
            raise HTTPException(400, "Adresse: 3–40 Zeichen, nur Kleinbuchstaben, Ziffern und Bindestrich "
                                     "(nicht am Anfang oder Ende), z. B. kfz-mueller")
        if s in RESERVIERTE_SLUGS:
            raise HTTPException(400, "Diese Adresse ist reserviert — bitte eine andere wählen.")
        setzen["webseite.slug"] = s
    if body.aktiv is not None:
        setzen["webseite.aktiv"] = bool(body.aktiv)
    if body.ueber_uns is not None:
        setzen["webseite.ueber_uns"] = body.ueber_uns.strip()
    if body.titel is not None:
        setzen["webseite.titel"] = " ".join(body.titel.split())[:90]
    if body.untertitel is not None:
        setzen["webseite.untertitel"] = " ".join(body.untertitel.split())[:140]
    for feld in SOCIAL_FELDER:
        wert = getattr(body, feld)
        if wert is not None:
            setzen[f"webseite.{feld}"] = _social_pruefen(wert, feld)
    if body.domains is not None:
        doms: List[str] = []
        basis = firmen_domain()
        haupt = (urlparse(_hauptadresse()).hostname or "").lower()
        for roh in body.domains:
            h = host_normalisieren(roh)
            if not h:
                continue
            if not HOST_RE.match(h):
                raise HTTPException(400, f"„{roh}“ ist keine gültige Domain (z. B. kfz-mueller.de).")
            if h == basis or h == haupt or h.endswith("." + basis):
                raise HTTPException(400, f"„{h}“ gehört zur Plattform — Unterdomains entstehen automatisch "
                                         f"aus der Adresse (<name>.{basis}).")
            if h not in doms:
                doms.append(h)
        fremd = await db.dealers.find_one({"webseite.domains": {"$in": doms}, "id": {"$ne": dealer_id}},
                                          {"_id": 0, "webseite.domains": 1}) if doms else None
        if fremd:
            belegt = [x for x in (fremd.get("webseite") or {}).get("domains") or [] if x in doms]
            raise HTTPException(409, f"Domain schon vergeben: {', '.join(belegt)}")
        setzen["webseite.domains"] = doms
    if not setzen:
        raise HTTPException(400, "Nichts zu ändern")
    setzen["webseite.aktualisiert_am"] = now_iso()
    try:
        r = await db.dealers.update_one({"id": dealer_id}, {"$set": setzen})
    except DuplicateKeyError:
        raise HTTPException(409, "Diese Adresse ist schon vergeben — bitte eine andere wählen.")
    if not r.matched_count:
        raise HTTPException(404, "Firma nicht gefunden")
    await log_activity_sicher(dealer_id, wer, "firma.webseite.geaendert",
                              meta={k.split(".")[-1]: (v if k != "webseite.ueber_uns" else len(v))
                                    for k, v in setzen.items() if k != "webseite.aktualisiert_am"})
    return await _firma_dok(dealer_id)


def _bild_bytes(b64: str, wo: str, max_bytes: int) -> bytes:
    try:
        raw = base64.b64decode(b64.split(",")[-1], validate=False)
    except (ValueError, TypeError):
        raise HTTPException(400, f"{wo} konnte nicht gelesen werden")
    if not raw:
        raise HTTPException(400, f"{wo}: leere Datei")
    if len(raw) > max_bytes:
        raise HTTPException(400, f"{wo} zu groß (max. {max_bytes // (1024 * 1024)} MB)")
    from storage_service import StorageError, bild_lesbar_pruefen, validate_image_bytes
    try:
        validate_image_bytes(raw, wo=wo)
        bild_lesbar_pruefen(raw, wo=wo)
    except StorageError as exc:
        raise HTTPException(400, str(exc))
    return raw


async def _bild_hochladen(dealer_id: str, b64: str) -> dict:
    """Ein Bild fuer die Firmenseite (hoechstens BILDER_MAX, JPEG verkleinert, oeffentlich)."""
    from storage_service import StorageError, bild_verkleinern, make_key, save_async, loeschen_oder_vormerken
    raw = _bild_bytes(b64, "Bild", 8 * 1024 * 1024)
    try:
        raw = await asyncio.to_thread(bild_verkleinern, raw, "Bild", "JPEG")
        key = make_key("firma", dealer_id, "bild.jpg")
        await save_async(key, raw)
    except StorageError as exc:
        raise HTTPException(400, str(exc))
    try:
        r = await db.dealers.update_one(
            {"id": dealer_id,
             "$expr": {"$lt": [{"$size": {"$ifNull": ["$webseite.bilder", []]}}, BILDER_MAX]}},
            {"$push": {"webseite.bilder": key}, "$set": {"webseite.aktualisiert_am": now_iso()}})
    except Exception:
        # Pruefliste 30.09.2026: die Datei liegt schon im Speicher — ohne Verweis bliebe sie fuer immer.
        await loeschen_oder_vormerken(db, key=key, grund="firmenseite_bild_db_fehler", dealer_id=dealer_id)
        raise
    if not r.modified_count:
        await loeschen_oder_vormerken(db, key=key, grund="firmenseite_bild_zu_viele", dealer_id=dealer_id)
        raise HTTPException(400, f"Höchstens {BILDER_MAX} Bilder — erst eines entfernen.")
    return await _firma_dok(dealer_id)


async def _bild_entfernen(dealer_id: str, key: str) -> dict:
    if not key.startswith(f"firma/{dealer_id}/"):
        raise HTTPException(404, "Bild nicht gefunden")
    r = await db.dealers.update_one({"id": dealer_id},
                                    {"$pull": {"webseite.bilder": key}, "$set": {"webseite.aktualisiert_am": now_iso()}})
    if r.modified_count:
        from storage_service import loeschen_oder_vormerken
        await loeschen_oder_vormerken(db, key=key, grund="firmenseite_bild_entfernt", dealer_id=dealer_id)
    return await _firma_dok(dealer_id)


@router.get("/dealer/webseite")
async def get_webseite(user=Depends(current_firma)):
    return _webseite_antwort(await _firma_dok(user["dealer_id"]), await ist_haupt_chef(user))


@router.put("/dealer/webseite")
async def put_webseite(body: WebseiteIn, user=Depends(current_firma)):
    """Slug, Ein/Aus, Ueber-uns-Text und Kundendomains — nur der Hauptchef."""
    await _nur_chef(user)
    return _webseite_antwort(await _webseite_setzen(user["dealer_id"], body, user["id"]), True)


@router.post("/dealer/webseite/bilder")
async def bild_hochladen(body: BildIn, user=Depends(current_firma)):
    await _nur_chef(user)
    return _webseite_antwort(await _bild_hochladen(user["dealer_id"], body.bild_b64), True)


@router.delete("/dealer/webseite/bilder/{key:path}")
async def bild_entfernen(key: str, user=Depends(current_firma)):
    await _nur_chef(user)
    return _webseite_antwort(await _bild_entfernen(user["dealer_id"], key), True)


# ---------------------------------------------------------------- Unterschrift des Chefs
def _unterschrift_pruefen(raw: bytes, wo: str) -> None:
    """Wie im Abholprotokoll: lesbar, mit Tinte, hoechstens 2 MB."""
    from routes.protocols import unterschrift_hat_tinte
    if not unterschrift_hat_tinte(raw):
        raise HTTPException(400, f"{wo} ist leer — bitte eine sichtbare Unterschrift verwenden")
    if len(raw) > 2 * 1024 * 1024:
        raise HTTPException(400, f"{wo} zu groß (max. 2 MB)")


async def _unterschrift_setzen(dealer_id: str, b64: str, wer: str) -> None:
    from storage_service import StorageError, bild_verkleinern, make_key, save_async, loeschen_oder_vormerken
    raw = _bild_bytes(b64, "Unterschrift", 2 * 1024 * 1024)
    _unterschrift_pruefen(raw, "Unterschrift")
    try:
        raw = await asyncio.to_thread(bild_verkleinern, raw, "Unterschrift", "PNG")
        key = make_key("unterschrift", dealer_id, "unterschrift.png")
        await save_async(key, raw)
    except StorageError as exc:
        raise HTTPException(400, str(exc))
    try:
        alt = await db.dealers.find_one({"id": dealer_id}, {"_id": 0, "unterschrift_key": 1})
        if alt is not None:
            await db.dealers.update_one({"id": dealer_id},
                                        {"$set": {"unterschrift_key": key, "unterschrift_am": now_iso()}})
    except Exception:
        # Pruefliste 30.09.2026: Unterschriftsbild ohne Verweis nie liegen lassen (Personendaten).
        await loeschen_oder_vormerken(db, key=key, grund="chef_unterschrift_db_fehler", dealer_id=dealer_id)
        raise
    if alt is None:
        await loeschen_oder_vormerken(db, key=key, grund="chef_unterschrift_ohne_firma", dealer_id=dealer_id)
        raise HTTPException(404, "Firma nicht gefunden")
    if alt.get("unterschrift_key") and alt["unterschrift_key"] != key:
        await loeschen_oder_vormerken(db, key=alt["unterschrift_key"], grund="chef_unterschrift_ersetzt",
                                      dealer_id=dealer_id)
    await log_activity_sicher(dealer_id, wer, "firma.unterschrift.hochgeladen")


async def _unterschrift_loeschen(dealer_id: str) -> None:
    d = await db.dealers.find_one({"id": dealer_id}, {"_id": 0, "unterschrift_key": 1})
    key = (d or {}).get("unterschrift_key")
    await db.dealers.update_one({"id": dealer_id}, {"$unset": {"unterschrift_key": "", "unterschrift_am": ""}})
    if key:
        from storage_service import loeschen_oder_vormerken
        await loeschen_oder_vormerken(db, key=key, grund="chef_unterschrift_entfernt", dealer_id=dealer_id)


async def _unterschrift_antwort(dealer_id: str) -> Response:
    d = await db.dealers.find_one({"id": dealer_id}, {"_id": 0, "unterschrift_key": 1})
    key = (d or {}).get("unterschrift_key")
    daten = await _datei_bytes(key) if key else None
    if not daten:
        raise HTTPException(404, "Keine Unterschrift hinterlegt")
    return Response(content=daten, media_type="image/png",
                    headers={"Cache-Control": "private, no-store", "X-Robots-Tag": "noindex"})


@router.post("/dealer/unterschrift")
async def unterschrift_hochladen(body: UnterschriftIn, user=Depends(current_chef)):
    """Bild der Unterschrift des Chefs (PNG) — landet im Kasten "Kaeufer / Haendler" jedes ueber
    das Kundenportal unterschriebenen Vertrags. Nur der Hauptchef."""
    await _nur_chef(user)
    await _unterschrift_setzen(user["dealer_id"], body.bild_b64, user["id"])
    return {"ok": True, "unterschrift_vorhanden": True}


@router.delete("/dealer/unterschrift")
async def unterschrift_entfernen(user=Depends(current_chef)):
    await _nur_chef(user)
    await _unterschrift_loeschen(user["dealer_id"])
    return {"ok": True, "unterschrift_vorhanden": False}


@router.get("/dealer/unterschrift")
async def unterschrift_anzeigen(user=Depends(current_chef)):
    """Vorschau fuer die Einstellungen — NUR der Chef (Pruefliste 01.10.2026, Nr. 4: vorher konnte jeder Sucher
    der Firma das Unterschriftsbild seines Chefs als PNG laden). Sucher sehen nur "hinterlegt: ja/nein"
    (GET /dealer/webseite -> unterschrift_vorhanden). Nie ueber /api/files."""
    return await _unterschrift_antwort(user["dealer_id"])


async def _datei_bytes(key: Optional[str]) -> Optional[bytes]:
    if not key:
        return None
    try:
        from storage_service import load_async
        daten = await load_async(key)
        return daten if daten and len(daten) <= 3 * 1024 * 1024 else None
    except Exception as exc:  # noqa: BLE001
        log.info("Datei %s nicht ladbar: %s", key, exc)
        return None


# ---------------------------------------------------------------- Domain pruefen (Weg A)
def adresse_oeffentlich(ip: str) -> bool:
    """Pruefliste 01.10.2026 (Nr. 3): der Server spricht bei der Domain-Pruefung nur OEFFENTLICHE Adressen an —
    kein 127.0.0.1, kein 10.x/172.16.x/192.168.x, nichts Link-Local oder Reserviertes, auch nicht als
    IPv4-in-IPv6. Vorher reichte ein DNS-Name, der auf ein internes Netz zeigt, um aus unserem Backend heraus
    interne Dienste anzusprechen (Rest-Risiko DNS-Rebinding bleibt klein: zwei Aufloesungen in wenigen Sekunden)."""
    try:
        a = ipaddress.ip_address(str(ip or "").split("%", 1)[0].strip("[]"))
    except ValueError:
        return False
    if getattr(a, "ipv4_mapped", None):
        a = a.ipv4_mapped
    return bool(a.is_global) and not a.is_multicast


async def _dns(host: str) -> List[str]:
    infos = await asyncio.wait_for(asyncio.to_thread(socket.getaddrinfo, host, 443, type=socket.SOCK_STREAM),
                                   PRUEFUNG_ZEITLIMIT_S)
    return sorted({i[4][0] for i in infos})


async def _https_firma(host: str) -> Dict[str, Any]:
    """GET https://<host>/api/public/firma?host=<host> wie ein Besucher — Status, Cloudflare-Kopf, Slug."""
    import httpx
    async with httpx.AsyncClient(timeout=PRUEFUNG_ZEITLIMIT_S, follow_redirects=False,
                                 headers={"User-Agent": "AutoSchnell-Domainpruefung/1.0"}) as client:
        r = await client.get(f"https://{host}/api/public/firma", params={"host": host})
    slug = None
    if r.status_code == 200:
        try:
            slug = (r.json() or {}).get("slug")
        except ValueError:
            slug = None
    return {"status": r.status_code, "cloudflare": bool(r.headers.get("cf-ray")) or "cloudflare" in (r.headers.get("server") or "").lower(),
            "slug": slug}


async def domain_pruefen(domain: str, slug: str) -> Dict[str, Any]:
    """Weg A, Schritt fuer Schritt: DNS (zeigt die Domain irgendwohin?) -> Proxy (FIRMEN_HOSTS) ->
    HTTPS (Zertifikat, Verbindung, Cloudflare) -> Firmenseite (antwortet die Plattform mit DIESER Firma?).
    Jeder Schritt mit ok/Text; 'naechster_schritt' sagt in Klartext, was als Naechstes zu tun ist."""
    h = host_normalisieren(domain)
    if not HOST_RE.match(h) or re.fullmatch(r"[0-9.]+", h) or h.endswith(".localhost") or h == "localhost":
        raise HTTPException(400, "Keine gültige Domain (z. B. kfz-mueller.de).")
    haupt = (urlparse(_hauptadresse()).hostname or "app.auto-schnellkauf.de")
    schritte: List[Dict[str, Any]] = []
    naechster = ""
    # 1. DNS
    try:
        ips = await _dns(h)
        intern = [ip for ip in ips if not adresse_oeffentlich(ip)]
        if intern:
            # Pruefliste 01.10.2026 (Nr. 3): zeigt die Domain auf ein internes Netz, wird sie NICHT angesprochen
            schritte.append({"schritt": "dns", "ok": False,
                             "text": f"Domain zeigt auf eine interne Adresse ({', '.join(intern)}) — das ist nicht erlaubt"})
            ips = []
            naechster = (f"DNS: {h} muss auf eine öffentliche Adresse zeigen — bei Cloudflare als CNAME auf {haupt} "
                         "(Proxy an), nicht auf ein internes Netz.")
        else:
            schritte.append({"schritt": "dns", "ok": bool(ips), "text": f"Domain zeigt auf {', '.join(ips)}" if ips else "Domain zeigt nirgendwohin"})
    except Exception as exc:  # noqa: BLE001
        ips = []
        schritte.append({"schritt": "dns", "ok": False, "text": f"Domain nicht auflösbar ({type(exc).__name__})"})
    if not ips and not naechster:
        naechster = (f"DNS: bei Cloudflare für {h} die Einträge @ und www als CNAME auf {haupt} anlegen (Proxy an) "
                     "— oder die Nameserver der Domain zuerst auf Cloudflare umstellen.")
    # 2. Proxy
    bekannt = proxy_kennt(h)
    schritte.append({"schritt": "proxy", "ok": bekannt,
                     "text": "Proxy kennt die Domain (FIRMEN_HOSTS)" if bekannt else "Proxy kennt die Domain noch nicht"})
    if not naechster and not bekannt:
        naechster = (f"Proxy: auf beiden Servern  sh deploy/env_setzen.sh 'FIRMEN_HOSTS=<bisher> {h}'  und danach "
                     "docker compose up -d --force-recreate --no-deps proxy  — bis dahin antwortet die Domain nicht (444).")
    # 3. HTTPS + 4. Firmenseite (nur wenn DNS steht)
    https: Dict[str, Any] = {}
    if ips:
        try:
            https = await _https_firma(h)
            ok = 200 <= https["status"] < 500
            schritte.append({"schritt": "https", "ok": ok,
                             "text": f"HTTPS antwortet (Status {https['status']}{', über Cloudflare' if https['cloudflare'] else ''})"
                             if ok else f"HTTPS antwortet mit Status {https['status']}"})
        except Exception as exc:  # noqa: BLE001
            schritte.append({"schritt": "https", "ok": False, "text": f"HTTPS-Verbindung scheitert ({type(exc).__name__}: {str(exc)[:120]})"})
            if not naechster:
                naechster = ("HTTPS: Zertifikat/Verbindung — bei Cloudflare den Proxy (orange Wolke) einschalten und unter "
                             "SSL/TLS den Modus „Full“ wählen; ein paar Minuten warten.")
        if https:
            if https["status"] == 200 and https.get("slug") == slug:
                schritte.append({"schritt": "firmenseite", "ok": True, "text": f"Firmenseite antwortet für „{slug}“"})
            elif https["status"] == 200:
                schritte.append({"schritt": "firmenseite", "ok": False, "text": f"Domain gehört zu einer anderen Firma („{https.get('slug')}“)"})
                naechster = naechster or "Die Domain ist bei einer anderen Firma eingetragen — dort entfernen."
            elif https["status"] == 404:
                schritte.append({"schritt": "firmenseite", "ok": False, "text": "Plattform kennt die Domain nicht (404)"})
                naechster = naechster or "Domain in der Firmenseite eintragen, speichern und die Firmenseite einschalten."
            elif https["status"] in (444, 403, 421):
                schritte.append({"schritt": "firmenseite", "ok": False, "text": f"Proxy weist die Domain ab ({https['status']})"})
                naechster = naechster or "Proxy: FIRMEN_HOSTS ergänzen und den Proxy neu erzeugen (siehe oben)."
            else:
                schritte.append({"schritt": "firmenseite", "ok": False, "text": f"Unerwartete Antwort ({https['status']})"})
                naechster = naechster or "Unerwartete Antwort — Betriebsseite und Proxy-Log prüfen."
    alles_ok = bool(schritte) and all(s["ok"] for s in schritte)
    return {"domain": h, "ok": alles_ok, "schritte": schritte,
            "naechster_schritt": "" if alles_ok else naechster,
            "url": f"https://{h}", "geprueft_am": now_iso()}


@router.get("/dealer/webseite/domain-pruefung")
async def dealer_domain_pruefung(domain: str, user=Depends(current_firma)):
    """Der Chef prueft nur seine eigenen Domains (keine Abfragen fremder Adressen ueber uns)."""
    d = await _firma_dok(user["dealer_id"])
    w = d.get("webseite") or {}
    h = host_normalisieren(domain)
    if h not in (w.get("domains") or []) and slug_aus_host(h) != (w.get("slug") or "-"):
        raise HTTPException(400, "Bitte zuerst die Domain in der Firmenseite eintragen und speichern.")
    return await domain_pruefen(h, w.get("slug") or "")


# ---------------------------------------------------------------- Betreiber (Weg A: Ahmad richtet ein)
@router.get("/admin/dealers/{dealer_id}/webseite")
async def admin_get_webseite(dealer_id: str, admin=Depends(current_super_admin)):
    return _webseite_antwort(await _firma_dok(dealer_id), True)


@router.put("/admin/dealers/{dealer_id}/webseite")
async def admin_put_webseite(dealer_id: str, body: WebseiteIn, admin=Depends(current_super_admin)):
    return _webseite_antwort(await _webseite_setzen(dealer_id, body, admin["id"]), True)


@router.post("/admin/dealers/{dealer_id}/webseite/bilder")
async def admin_bild_hochladen(dealer_id: str, body: BildIn, admin=Depends(current_super_admin)):
    await _firma_dok(dealer_id)
    return _webseite_antwort(await _bild_hochladen(dealer_id, body.bild_b64), True)


@router.delete("/admin/dealers/{dealer_id}/webseite/bilder/{key:path}")
async def admin_bild_entfernen(dealer_id: str, key: str, admin=Depends(current_super_admin)):
    return _webseite_antwort(await _bild_entfernen(dealer_id, key), True)


@router.post("/admin/dealers/{dealer_id}/unterschrift")
async def admin_unterschrift_hochladen(dealer_id: str, body: UnterschriftIn, admin=Depends(current_super_admin)):
    await _unterschrift_setzen(dealer_id, body.bild_b64, admin["id"])
    return {"ok": True, "unterschrift_vorhanden": True}


@router.delete("/admin/dealers/{dealer_id}/unterschrift")
async def admin_unterschrift_entfernen(dealer_id: str, admin=Depends(current_super_admin)):
    await _firma_dok(dealer_id)
    await _unterschrift_loeschen(dealer_id)
    return {"ok": True, "unterschrift_vorhanden": False}


@router.get("/admin/dealers/{dealer_id}/unterschrift")
async def admin_unterschrift_anzeigen(dealer_id: str, admin=Depends(current_super_admin)):
    return await _unterschrift_antwort(dealer_id)


@router.get("/admin/dealers/{dealer_id}/webseite/domain-pruefung")
async def admin_domain_pruefung(dealer_id: str, domain: str, admin=Depends(current_super_admin)):
    d = await _firma_dok(dealer_id)
    return await domain_pruefen(domain, ((d.get("webseite") or {}).get("slug")) or "")


# ---------------------------------------------------------------- Code-Freigabe (Sucher/Chef)
def code_erzeugen() -> str:
    return "".join(secrets.choice(CODE_ZEICHEN) for _ in range(CODE_LAENGE))


def code_normalisieren(roh: str) -> str:
    return re.sub(r"[^A-Z0-9]", "", (roh or "").upper())[:CODE_LAENGE]


def _laeuft_ab(f: Optional[dict]) -> Optional[datetime]:
    try:
        return datetime.fromisoformat((f or {}).get("laeuft_ab") or "")
    except ValueError:
        return None


def portal_offen(p: Optional[dict], version: int) -> bool:
    """Offener, nicht abgelaufener Code fuer GENAU diese Fassung."""
    if not p or p.get("status") != "offen" or int(p.get("version") or 0) != int(version or 1):
        return False
    ab = _laeuft_ab(p)
    return bool(ab and ab > datetime.now(timezone.utc))


def _portal_antwort(c: dict, slug: str) -> dict:
    p = dict(c.get("portal") or {})
    version = int(c.get("version") or 1)
    status = p.get("status") or "keiner"
    if status == "offen" and not portal_offen(p, version):
        status = "abgelaufen" if int(p.get("version") or 0) == version else "fassung_veraltet"
    # Kompletter Lauf 30.09.2026: unterschreibt der Kunde online und aendert der Chef danach bei der Abholung den
    # Preis (neue Fassung), gehoert die Unterschrift zur ALTEN Fassung — das muss die App sagen, statt weiter
    # "digital unterschrieben" zu zeigen. Die alte unterschriebene Fassung bleibt abrufbar.
    if status == "unterschrieben" and int(p.get("version") or 0) != version:
        status = "unterschrieben_alt"
    return {"status": status, "code": p.get("code") if status == "offen" else None,
            "unterschrieben_version": ((int(c.get("pdf_signiert_version") or 0) or None)
                                       if c.get("pdf_signiert_b64") else None),
            "laeuft_ab": p.get("laeuft_ab"), "version": p.get("version"), "aktuelle_version": version,
            "unterschrieben_am": p.get("unterschrieben_am"), "name": p.get("name"),
            "url": firmen_url(slug) if slug else "", "slug": slug,
            "pdf_signiert": bool(c.get("pdf_signiert_b64")), "code_tage": PORTAL_CODE_TAGE}


async def _vertrag_und_slug(contract_id: str, user: dict) -> tuple:
    from routes.contracts import _vertrag_bereich
    c = await db.generated_pdfs.find_one({"id": contract_id, **_vertrag_bereich(user)},
                                         {"_id": 0, "id": 1, "version": 1, "portal": 1, "contract_no": 1,
                                          "pdf_signiert_b64": 1, "pdf_signiert_version": 1, "user_id": 1})
    if c is None:
        raise HTTPException(404, "Vertrag nicht gefunden")
    d = await db.dealers.find_one({"id": user["dealer_id"]}, {"_id": 0, "webseite": 1})
    w = (d or {}).get("webseite") or {}
    return c, (w.get("slug") or ""), bool(w.get("aktiv"))


@router.post("/contracts/{contract_id}/portal")
async def portal_freigeben(contract_id: str, request: Request = None, user=Depends(current_firma)):
    """Code fuer den Kunden erzeugen (oder den noch laufenden derselben Fassung zurueckgeben)."""
    from routes.contracts import _vertrag_bereich
    # 500 Codes je Minute ueber dieselbe Adresse, danach 2 Minuten warten (Wunsch Ahmad 30.09.2026)
    if request is not None and not await _erzeugen_limiter_ip.check(client_ip(request)):
        raise _zu_viele_anfragen()
    c, slug, aktiv = await _vertrag_und_slug(contract_id, user)
    if not slug or not aktiv:
        raise HTTPException(409, "Erst die Firmenseite einrichten (Einstellungen → Firmenseite & Kundenportal): "
                                 "Adresse festlegen und einschalten.")
    bereich = _vertrag_bereich(user)
    # Wunsch Ahmad 02.10.2026: der Kunde bekommt die Fassung ohne Empfangsbestaetigung
    await _portal_dokument_sicherstellen(contract_id, bereich)
    for _ in range(6):
        c = await db.generated_pdfs.find_one({"id": contract_id, **bereich},
                                             {"_id": 0, "id": 1, "version": 1, "portal": 1, "contract_no": 1,
                                              "pdf_signiert_b64": 1, "pdf_signiert_version": 1})
        if c is None:
            raise HTTPException(404, "Vertrag nicht gefunden")
        version = int(c.get("version") or 1)
        p = c.get("portal") or {}
        if p.get("status") == "unterschrieben" and int(p.get("version") or 0) == version:
            raise HTTPException(409, "Dieser Vertrag ist schon vom Kunden unterschrieben.")
        if portal_offen(p, version) and (_laeuft_ab(p) - datetime.now(timezone.utc)) > timedelta(hours=1):
            return _portal_antwort(c, slug)
        neu = {"code": code_erzeugen(), "status": "offen", "erstellt_am": now_iso(),
               "laeuft_ab": (datetime.now(timezone.utc) + timedelta(days=PORTAL_CODE_TAGE)).isoformat(),
               "erstellt_von": user.get("id"), "version": version, "versuche": 0}
        filt = {"id": contract_id, **bereich, "version": c.get("version")}
        if p:
            filt["portal.erstellt_am"] = p.get("erstellt_am")
        else:
            filt["portal"] = {"$exists": False}
        try:
            r = await db.generated_pdfs.update_one(filt, {"$set": {"portal": neu}})
        except DuplicateKeyError:
            continue                                    # Code in dieser Firma schon offen -> neuer Code
        if r.modified_count:
            await log_activity_sicher(user["dealer_id"], user["id"], "vertrag.portal.freigegeben",
                                      ref=contract_id, meta={"version": version, "laeuft_ab": neu["laeuft_ab"]})
            return _portal_antwort({**c, "portal": neu}, slug)
    raise HTTPException(409, "Der Vertrag wurde gerade geändert — bitte die Seite neu laden.")


@router.get("/contracts/{contract_id}/portal")
async def portal_stand(contract_id: str, user=Depends(current_firma)):
    c, slug, _aktiv = await _vertrag_und_slug(contract_id, user)
    return _portal_antwort(c, slug)


@router.delete("/contracts/{contract_id}/portal")
async def portal_zurueckziehen(contract_id: str, user=Depends(current_firma)):
    """Offenen Code sofort ungueltig machen (der Kunde kommt damit nicht mehr hinein)."""
    from routes.contracts import _vertrag_bereich
    r = await db.generated_pdfs.update_one(
        {"id": contract_id, **_vertrag_bereich(user), "portal.status": "offen"},
        {"$set": {"portal.status": "zurueckgezogen", "portal.zurueckgezogen_am": now_iso(),
                  "portal.zurueckgezogen_von": user.get("id")}})
    if r.matched_count:
        await log_activity_sicher(user["dealer_id"], user["id"], "vertrag.portal.zurueckgezogen", ref=contract_id)
    c, slug, _aktiv = await _vertrag_und_slug(contract_id, user)
    return _portal_antwort(c, slug)


@router.get("/contracts/{contract_id}/portal/pdf")
async def portal_pdf(contract_id: str, fassung: Optional[int] = None, user=Depends(current_firma)):
    """Der vom Kunden unterschriebene Vertrag (PDF mit beiden Unterschriften). Ohne `fassung` die zuletzt
    unterschriebene; mit `fassung` genau diese (auch eine aeltere aus dem Archiv, 30.09.2026)."""
    from routes.contracts import _vertrag_bereich
    from vertrag_dateiname import content_disposition
    c = await db.generated_pdfs.find_one({"id": contract_id, **_vertrag_bereich(user)},
                                         {"_id": 0, "pdf_signiert_b64": 1, "pdf_signiert_version": 1,
                                          "contract_no": 1, "version": 1})
    if c is None:
        raise HTTPException(404, "Vertrag nicht gefunden")
    b64, version = c.get("pdf_signiert_b64"), int(c.get("pdf_signiert_version") or c.get("version") or 1)
    if fassung is not None and (not b64 or int(fassung) != version):
        alt = await db.generated_pdf_versions.find_one(
            {"contract_id": contract_id, "version": int(fassung), "pdf_signiert_b64": {"$type": "string"}},
            {"_id": 0, "pdf_signiert_b64": 1})
        b64, version = (alt or {}).get("pdf_signiert_b64"), int(fassung)
    if not b64:
        raise HTTPException(404, "Noch keine Unterschrift des Kunden")
    aktuell = version == int(c.get("version") or 1)
    zusatz = "" if aktuell else f"-Fassung-{version}"
    name = f"Kaufvertrag-{c.get('contract_no') or contract_id}{zusatz}-unterschrieben.pdf"
    return Response(content=base64.b64decode(b64), media_type="application/pdf",
                    headers={"Content-Disposition": content_disposition(name),
                             "X-Vertrag-Version": str(version), "Cache-Control": "no-store"})


# ---------------------------------------------------------------- Kundenportal (oeffentlich)
class OeffnenIn(BaseModel):
    code: str = Field(min_length=1, max_length=20)
    slug: Optional[str] = Field(default=None, max_length=60)
    host: Optional[str] = Field(default=None, max_length=253)


class UnterschreibenIn(BaseModel):
    signature_b64: str = Field(min_length=20, max_length=UNTERSCHRIFT_B64_MAX)
    name: str = Field(min_length=2, max_length=120)
    einverstanden: bool = False


def _portal_kennung(p: Optional[dict]) -> str:
    """Kennung GENAU dieser Freigabe (Code + Zeitpunkt). Pruefliste 30.09.2026: die Sitzung hing nur an
    Vertrag und Fassung — wurde Code A zurueckgezogen und Code B fuer dieselbe Fassung erzeugt, lebte
    die Sitzung von Code A wieder auf. Jetzt gilt eine Sitzung nur fuer den Code, mit dem sie entstand."""
    p = p or {}
    roh = f"{p.get('code') or ''}|{p.get('erstellt_am') or ''}"
    return hashlib.sha256(roh.encode("utf-8")).hexdigest()[:20]


def _sitzung_token(c: dict) -> str:
    return jwt.encode({"typ": "portal", "cid": c["id"], "v": int(c.get("version") or 1), "d": c.get("dealer_id"),
                       "k": _portal_kennung(c.get("portal")),
                       "exp": datetime.now(timezone.utc) + timedelta(minutes=PORTAL_SITZUNG_MINUTEN),
                       "jti": secrets.token_hex(8)}, _auth.JWT_SECRET, algorithm=_auth.JWT_ALG)


# Pruefliste 01.10.2026 (Nr. 2): unterschreiben konnte, wer das Dokument nie bekommen hatte — die Oberflaeche
# liess den Knopf auch nach einem gescheiterten PDF-Abruf zu, und der Server verlangte keinen Nachweis.
# Jetzt vermerkt die PDF-Auslieferung, WELCHES Dokument (Pruefsumme, Fassung) an WELCHE Sitzung (Kennung
# des Codes) ging; die Unterschrift gilt nur, wenn genau das zusammenpasst.
GELESEN_HINWEIS = ("Bitte zuerst den Vertrag laden und lesen — die Unterschrift gilt nur für das angezeigte "
                   "Dokument. Seite neu laden und erneut versuchen.")


async def _gelesen_vermerken(c: dict, pdf: bytes) -> None:
    import portal_pdf
    p = c.get("portal") or {}
    if p.get("status") != "offen":
        return
    await db.generated_pdfs.update_one(
        {"id": c["id"], "version": c.get("version"), "portal.status": "offen", "portal.code": p.get("code")},
        {"$set": {"portal.gelesen": {"kennung": _portal_kennung(p), "fassung": int(c.get("version") or 1),
                                     "sha256": portal_pdf.pruefsumme(pdf), "am": now_iso()}}})


def _gelesen_pruefen(c: dict) -> None:
    import portal_pdf
    p = c.get("portal") or {}
    g = p.get("gelesen") or {}
    dokument = _portal_dokument(c)
    erwartet = portal_pdf.pruefsumme(dokument) if dokument else ""
    if (not g or g.get("kennung") != _portal_kennung(p) or int(g.get("fassung") or 0) != int(c.get("version") or 1)
            or not erwartet or g.get("sha256") != erwartet):
        raise HTTPException(409, GELESEN_HINWEIS)


def _vertrag_kurz(c: dict, firma: dict) -> dict:
    cd = c.get("contract_data") or {}
    p = c.get("portal") or {}
    return {"contract_no": c.get("contract_no") or "", "marke": c.get("make") or cd.get("vehicle_make") or "",
            "modell": c.get("model") or cd.get("vehicle_model") or "",
            "verkaeufer": c.get("seller_name") or cd.get("seller_name") or "",
            "kaufpreis": c.get("purchase_price") if c.get("purchase_price") is not None else cd.get("purchase_price"),
            "abholung": c.get("pickup_date") or cd.get("pickup_date") or "",
            "firma": firma.get("company_name") or "", "version": int(c.get("version") or 1),
            "status": p.get("status") or "offen", "unterschrieben_am": p.get("unterschrieben_am"),
            "laeuft_ab": p.get("laeuft_ab")}


_VERTRAG_FELDER = {"_id": 0, "id": 1, "dealer_id": 1, "user_id": 1, "version": 1, "portal": 1, "contract_no": 1,
                   "make": 1, "model": 1, "seller_name": 1, "purchase_price": 1, "pickup_date": 1,
                   "contract_data": 1, "vehicle_id": 1, "pdf_b64": 1, "pdf_signiert_b64": 1,
                   "pdf_portal_b64": 1, "pdf_portal_version": 1}


def _portal_dokument(c: dict) -> Optional[bytes]:
    """Das Dokument, das der Kunde liest und unterschreibt: die Portal-Fassung dieser Vertragsfassung
    (ohne Empfangsbestaetigung, Wunsch Ahmad 02.10.2026); fehlt sie (Code vor der Umstellung erzeugt,
    Erzeugung gescheitert), die gespeicherte Druckfassung — nie ein heute neu gerechnetes PDF."""
    if c.get("pdf_portal_b64") and int(c.get("pdf_portal_version") or 0) == int(c.get("version") or 1):
        return base64.b64decode(c["pdf_portal_b64"])
    return base64.b64decode(c["pdf_b64"]) if c.get("pdf_b64") else None


async def portal_dokument_erzeugen(doc: dict) -> bytes:
    """Wunsch Ahmad 02.10.2026: die Fassung fuer das Kundenportal — Druckfassung OHNE Empfangsbestaetigung
    (kein "Kaufpreis erhalten", keine Schluesselanzahl, kein "Datum und Ort"); unten nur die Unterschrift
    des Kunden und die der Firma. Aufgebaut wie die Neuerzeugung einer Fassung (Kaeufer = Ersteller des
    Vertrags, eingefrorene Kaeuferdaten und Logo, Altvertrag ohne Text -> Nachtraeglich-Hinweis)."""
    from auftraggeber import kaeufer_basis
    from pdf_service import DIGITAL_NACHTRAEGLICH, generate_contract_pdf
    from routes.contracts import _apply_contract_overrides, _logo_einsetzen
    cd = dict(doc.get("contract_data") or {})
    v = await db.vehicles.find_one({"id": doc.get("vehicle_id"), "dealer_id": doc["dealer_id"]}, {"_id": 0}) or {}
    vehicle = dict(v.get("data") or {})
    termin = await db.appointments.find_one({"contract_id": doc["id"], "dealer_id": doc["dealer_id"]},
                                            {"_id": 0, "created_by": 1}) or {}
    dealer = await kaeufer_basis(dealer_id=doc["dealer_id"], user_ids=(doc.get("user_id"), termin.get("created_by"))) or {}
    vehicle, dealer = _apply_contract_overrides(contract=cd, vehicle=vehicle, dealer=dealer)
    dealer = await _logo_einsetzen(dealer, cd)
    if not (cd.get("digital_vertragstext") or "").strip():
        cd["digital_vertragstext"] = DIGITAL_NACHTRAEGLICH
    return await asyncio.to_thread(generate_contract_pdf, dealer=dealer, vehicle=vehicle, contract=cd, portal=True)


async def _portal_dokument_sicherstellen(contract_id: str, bereich: dict) -> bool:
    """Vor der Code-Erzeugung: liegt die Portal-Fassung fuer DIESE Vertragsfassung vor? Sonst erzeugen und
    am Vertrag ablegen (pdf_portal_b64 / pdf_portal_version). Scheitert die Erzeugung, bleibt der Rueckfall
    auf die Druckfassung (_portal_dokument) — der Code wird trotzdem erzeugt. True = Portal-Fassung liegt vor."""
    doc = await db.generated_pdfs.find_one({"id": contract_id, **bereich},
                                           {"_id": 0, "pdf_b64": 0, "pdf_digital_b64": 0, "pdf_signiert_b64": 0,
                                            "pdf_portal_b64": 0})
    if not doc:
        return False
    version = int(doc.get("version") or 1)
    if int(doc.get("pdf_portal_version") or 0) == version:
        return True
    try:
        pdf = await portal_dokument_erzeugen(doc)
    except Exception:  # noqa: BLE001 — Rueckfall Druckfassung, nie den Code verhindern
        log.exception("Kundenportal: Portal-Fassung fuer Vertrag %s nicht erzeugt — Druckfassung als Rueckfall", contract_id)
        return False
    r = await db.generated_pdfs.update_one({"id": contract_id, "version": doc.get("version")},
                                           {"$set": {"pdf_portal_b64": base64.b64encode(pdf).decode(),
                                                     "pdf_portal_version": version}})
    return bool(r.matched_count)

CODE_FALSCH = "Code ungültig oder abgelaufen. Bitte den Code vom Autohaus prüfen."


@router.post("/public/portal/oeffnen")
async def portal_oeffnen(body: OeffnenIn, request: Request):
    """Kunde gibt den Code auf der Firmenseite ein -> kurze Sitzung + Vertragskurzdaten."""
    ip = client_ip(request)
    if not await _portal_limiter_ip.check(ip):
        raise _zu_viele_anfragen()
    if not await _code_limiter_ip.check(ip):
        raise HTTPException(429, "Zu viele falsche Codes — bitte in 10 Minuten erneut.")
    firma = await firma_laden(host=body.host, slug=body.slug)
    if not firma:
        raise HTTPException(404, "Zu dieser Adresse gibt es keine Firmenseite.")
    if not await _code_limiter_firma.check(f"firma:{firma['id']}"):
        raise HTTPException(429, "Zu viele Versuche — bitte später erneut.")
    code = code_normalisieren(body.code)
    if len(code) != CODE_LAENGE:
        raise HTTPException(404, CODE_FALSCH)
    # Pruefliste 30.09.2026 (Nr. 29): pruefen und zaehlen in EINER Operation — vorher wurde erst gelesen,
    # dann gezaehlt; wurde der Code genau dazwischen zurueckgezogen, bekam der Kunde noch eine Sitzung,
    # die beim naechsten Aufruf 410 lieferte. Offen, nicht abgelaufen, fuer GENAU diese Fassung.
    from pymongo import ReturnDocument
    c = await db.generated_pdfs.find_one_and_update(
        {"dealer_id": firma["id"], "portal.code": code, "portal.status": "offen",
         "loeschung.status": {"$ne": "laeuft"}, "portal.laeuft_ab": {"$gt": now_iso()},
         "$expr": {"$eq": ["$portal.version", {"$ifNull": ["$version", 1]}]}},
        {"$inc": {"portal.abrufe": 1}, "$set": {"portal.zuletzt_geoeffnet": now_iso()}},
        projection=_VERTRAG_FELDER, return_document=ReturnDocument.AFTER)
    if not c or not portal_offen(c.get("portal"), int(c.get("version") or 1)):
        await log_activity_sicher(firma["id"], "", "vertrag.portal.code_falsch", meta={"ip": ip})
        raise HTTPException(404, CODE_FALSCH)
    await log_activity_sicher(firma["id"], "", "vertrag.portal.geoeffnet", ref=c["id"], meta={"ip": ip})
    # richtiger Code: dieser Aufruf war kein Fehlversuch
    await _code_limiter_ip.erstatten(ip)
    await _code_limiter_firma.erstatten(f"firma:{firma['id']}")
    return {"sitzung": _sitzung_token(c), "vertrag": _vertrag_kurz(c, firma), "firma": firma_oeffentlich(firma),
            "sitzung_minuten": PORTAL_SITZUNG_MINUTEN}


#: Pruefliste 30.09.2026: die Sitzung reist in einer Kopfzeile, nicht mehr in der Adresse — Adressen landen
#: in Server-, Proxy- und Ueberwachungsprotokollen (beim Fahrer wurde das Muster schon frueher entfernt).
SitzungKopf = Annotated[str, Header(alias="X-Portal-Sitzung", max_length=4000)]


async def _sitzung_pruefen(sitzung: str) -> dict:
    try:
        nutz = jwt.decode(sitzung or "", _auth.JWT_SECRET, algorithms=[_auth.JWT_ALG])
    except jwt.PyJWTError:
        raise HTTPException(401, "Die Sitzung ist abgelaufen — bitte den Code erneut eingeben.")
    if nutz.get("typ") != "portal" or not nutz.get("cid"):
        raise HTTPException(401, "Ungültige Sitzung")
    c = await db.generated_pdfs.find_one({"id": nutz["cid"], "dealer_id": nutz.get("d"),
                                          "loeschung.status": {"$ne": "laeuft"}}, _VERTRAG_FELDER)
    if not c or int(c.get("version") or 1) != int(nutz.get("v") or 0):
        raise HTTPException(410, "Der Vertrag wurde inzwischen geändert — bitte einen neuen Code beim Autohaus anfordern.")
    p = c.get("portal") or {}
    if p.get("status") not in ("offen", "unterschrieben") or int(p.get("version") or 0) != int(c.get("version") or 1):
        raise HTTPException(410, "Die Freigabe wurde zurückgezogen oder ist abgelaufen.")
    if p.get("status") == "offen" and not portal_offen(p, int(c.get("version") or 1)):
        raise HTTPException(410, "Der Code ist abgelaufen — bitte einen neuen beim Autohaus anfordern.")
    # nur fuer GENAU den Code, mit dem die Sitzung entstand (nicht fuer einen spaeter neu erzeugten)
    if not nutz.get("k") or nutz.get("k") != _portal_kennung(p):
        raise HTTPException(410, "Die Freigabe wurde zurückgezogen oder ersetzt — bitte den neuen Code "
                                 "beim Autohaus anfordern.")
    # Firmenseite abgeschaltet, Firma gesperrt oder in Loeschung: auch laufende Sitzungen enden
    if not await firma_offen(await db.dealers.find_one({"id": c["dealer_id"]}, _FIRMA_FELDER)):
        raise HTTPException(410, "Das Kundenportal dieses Autohauses ist derzeit nicht erreichbar.")
    return c


@router.get("/public/portal/vertrag")
async def portal_sitzung(sitzung: SitzungKopf):
    c = await _sitzung_pruefen(sitzung)
    firma = await db.dealers.find_one({"id": c["dealer_id"]}, _FIRMA_FELDER) or {}
    return {"vertrag": _vertrag_kurz(c, firma), "pdf_signiert": bool(c.get("pdf_signiert_b64"))}


@router.get("/public/portal/vertrag/pdf")
async def portal_sitzung_pdf(sitzung: SitzungKopf):
    """Der Vertrag fuer den Kunden: vor der Unterschrift die Druckfassung, danach die unterschriebene."""
    from vertrag_dateiname import content_disposition
    c = await _sitzung_pruefen(sitzung)
    if (c.get("portal") or {}).get("status") == "unterschrieben":
        pdf = base64.b64decode(c["pdf_signiert_b64"]) if c.get("pdf_signiert_b64") else None
    else:
        pdf = _portal_dokument(c)                    # Portal-Fassung ohne Empfangsbestaetigung (02.10.2026)
    if not pdf:
        raise HTTPException(404, "Das Vertrags-PDF fehlt — bitte das Autohaus ansprechen.")
    name = f"Kaufvertrag-{c.get('contract_no') or c['id']}.pdf"
    await _gelesen_vermerken(c, pdf)                # Pruefliste 01.10.2026 (Nr. 2)
    return Response(content=pdf, media_type="application/pdf",
                    headers={"Content-Disposition": content_disposition(name),
                             "Cache-Control": "private, no-store", "X-Robots-Tag": "noindex"})


async def _signiertes_pdf(c: dict, verkaeufer_png: bytes, kaeufer_png: Optional[bytes],
                          name: str, wann: datetime) -> tuple:
    """Das unterschriebene Dokument: die GESPEICHERTE Druckfassung (genau das, was der Kunde gelesen hat)
    mit den Unterschriften in den Feldern und einem angefuegten Signaturnachweis (portal_pdf). Liefert
    (PDF, Pruefsumme des gelesenen Dokuments, Bilder in den Feldern?).

    Pruefliste 30.09.2026 (Nr. 4/5): vorher wurde hier ein NEUES PDF aus Vertragsdaten und dem heutigen
    Fahrzeugstand erzeugt — mit dem Datum des Unterschriftstages und womoeglich anderen Angaben als im
    gelesenen Dokument."""
    import portal_pdf
    original = _portal_dokument(c)                   # genau das, was der Kunde gelesen hat (02.10.2026)
    if not original:
        raise ValueError("Vertragsdokument fehlt")
    firma = await db.dealers.find_one({"id": c.get("dealer_id")}, {"_id": 0, "company_name": 1}) or {}
    zeit = wann.astimezone(_BERLIN).strftime("%d.%m.%Y, %H:%M")
    return await asyncio.to_thread(
        portal_pdf.unterschreiben, original, verkaeufer_png=verkaeufer_png, kaeufer_png=kaeufer_png, name=name,
        zeit=zeit, firma=(firma.get("company_name") or "Autohändler").strip(),
        vertragsnummer=str(c.get("contract_no") or c.get("id") or ""), fassung=int(c.get("version") or 1))


try:
    from zoneinfo import ZoneInfo
    _BERLIN = ZoneInfo("Europe/Berlin")
except Exception:  # noqa: BLE001
    _BERLIN = timezone.utc


@router.post("/public/portal/vertrag/unterschreiben")
async def portal_unterschreiben(sitzung: SitzungKopf, body: UnterschreibenIn, request: Request):
    """Der Kunde unterschreibt: Bild pruefen, speichern, PDF mit beiden Unterschriften erzeugen,
    Vertrag als unterschrieben markieren (nur einmal, nur diese Fassung), Meldung an Sucher und Chef."""
    ip = client_ip(request)
    if not await _portal_limiter_ip.check(ip):
        raise _zu_viele_anfragen()
    if not await _unterschrift_limiter.check(ip):
        raise HTTPException(429, "Zu viele Versuche — bitte später erneut.")
    if not body.einverstanden:
        raise HTTPException(400, "Bitte bestätigen, dass Sie den Vertrag gelesen haben und ihm zustimmen.")
    c = await _sitzung_pruefen(sitzung)
    p = c.get("portal") or {}
    if p.get("status") != "offen":
        raise HTTPException(409, "Dieser Vertrag ist bereits unterschrieben.")
    _gelesen_pruefen(c)                             # Pruefliste 01.10.2026 (Nr. 2)
    name = " ".join(body.name.split())
    raw = _bild_bytes(body.signature_b64, "Unterschrift", 2 * 1024 * 1024)
    _unterschrift_pruefen(raw, "Unterschrift")
    from storage_service import StorageError, make_key, save_async, loeschen_oder_vormerken
    # Pruefliste 30.09.2026 (Nr. 7): ERST beanspruchen, dann rechnen. Vorher kam der Abgleich erst nach
    # Bildpruefung, PDF und Speichern — zwanzig gleichzeitige Absendungen rechneten zwanzig PDFs, eine gewann.
    wann = datetime.now(timezone.utc)
    offen_filter = {"id": c["id"], "version": c.get("version"), "portal.status": "offen", "portal.code": p.get("code")}
    anspruch = secrets.token_hex(8)
    r = await db.generated_pdfs.update_one(
        {**offen_filter, "$or": [{"portal.anspruch_bis": {"$exists": False}}, {"portal.anspruch_bis": None},
                                 {"portal.anspruch_bis": {"$lt": wann.isoformat()}}]},
        {"$set": {"portal.anspruch": anspruch,
                  "portal.anspruch_bis": (wann + timedelta(seconds=SIGNIER_ANSPRUCH_S)).isoformat()}})
    if not r.modified_count:
        stand = await db.generated_pdfs.find_one({"id": c["id"]}, {"_id": 0, "portal.status": 1})
        if ((stand or {}).get("portal") or {}).get("status") == "unterschrieben":
            raise HTTPException(409, "Dieser Vertrag ist bereits unterschrieben.")
        raise HTTPException(409, "Die Unterschrift wird gerade verarbeitet — bitte einen Moment warten.")
    eigener = {**offen_filter, "portal.anspruch": anspruch}

    async def _anspruch_zurueck():
        try:
            await db.generated_pdfs.update_one(eigener, {"$unset": {"portal.anspruch": "", "portal.anspruch_bis": ""}})
        except Exception:  # noqa: BLE001 — laeuft nach SIGNIER_ANSPRUCH_S von selbst ab
            pass
    key = None
    try:
        firma = await db.dealers.find_one({"id": c["dealer_id"]}, {**_FIRMA_FELDER, "user_id": 1}) or {}
        kaeufer_png = await _datei_bytes(firma.get("unterschrift_key"))
        try:
            pdf, gelesen_sha, in_feldern = await _signiertes_pdf(c, raw, kaeufer_png, name, wann)
        except Exception as exc:  # noqa: BLE001
            log.exception("Kundenportal: unterschriebenes PDF fuer Vertrag %s nicht erzeugt", c["id"])
            raise HTTPException(500, f"Der unterschriebene Vertrag konnte nicht erzeugt werden ({type(exc).__name__}).")
        try:
            key = make_key("portal", c["dealer_id"], "unterschrift-verkaeufer.png")
            await save_async(key, raw)
        except StorageError as exc:
            key = None
            raise HTTPException(400, f"Unterschrift konnte nicht gespeichert werden: {exc}")
        r = await db.generated_pdfs.update_one(
            eigener,
            {"$set": {"portal.status": "unterschrieben", "portal.unterschrieben_am": wann.isoformat(),
                      "portal.name": name[:120], "portal.ip": ip,
                      "portal.user_agent": (request.headers.get("user-agent") or "")[:200],
                      "portal.unterschrift_key": key, "portal.kaeufer_unterschrift": bool(kaeufer_png),
                      # Pruefsumme des Dokuments, das der Kunde gelesen hat (steht auch im Signaturnachweis)
                      "portal.gelesen_sha256": gelesen_sha, "portal.unterschrift_in_feldern": bool(in_feldern),
                      "pdf_signiert_b64": base64.b64encode(pdf).decode(),
                      "pdf_signiert_sha256": hashlib.sha256(pdf).hexdigest(),
                      "pdf_signiert_version": int(c.get("version") or 1), "kunde_unterschrieben_am": wann.isoformat(),
                      "updated_at": now_iso()},
             "$unset": {"portal.anspruch": "", "portal.anspruch_bis": ""}})
    except BaseException:
        # Pruefliste 30.09.2026: Unterschriftsbild des Kunden ohne Verweis nie liegen lassen; Anspruch frei
        if key:
            await loeschen_oder_vormerken(db, key=key, grund="portal_unterschrift_db_fehler", dealer_id=c["dealer_id"])
        await _anspruch_zurueck()
        raise
    if not r.modified_count:
        await loeschen_oder_vormerken(db, key=key, grund="portal_unterschrift_verworfen", dealer_id=c["dealer_id"])
        await _anspruch_zurueck()
        raise HTTPException(409, "Dieser Vertrag wurde gerade schon unterschrieben oder geändert.")
    empfaenger = [u for u in {firma.get("user_id"), c.get("user_id")} if u]
    text = (f"Kaufvertrag {c.get('contract_no') or ''} ({c.get('make') or ''} {c.get('model') or ''}) wurde von "
            f"{name} digital unterschrieben.").replace("  ", " ")
    await meldung_anlegen(c["dealer_id"], empfaenger, "vertrag_unterschrieben", text, ref=c["id"],
                          contract_no=c.get("contract_no"))
    await log_activity_sicher(c["dealer_id"], "", "vertrag.portal.unterschrieben", ref=c["id"],
                              meta={"name": name[:120], "version": int(c.get("version") or 1), "ip": ip,
                                    "kaeufer_unterschrift": bool(kaeufer_png)})
    await _unterschrift_limiter.erstatten(ip)            # erfolgreich unterschrieben: kein Fehlversuch
    return {"ok": True, "unterschrieben_am": wann.isoformat(), "contract_no": c.get("contract_no") or ""}


# ---------------------------------------------------------------- Meldungen in der App
async def meldung_anlegen(dealer_id: str, empfaenger: List[str], typ: str, text: str, *,
                          ref: Optional[str] = None, contract_no: Optional[str] = None) -> Optional[str]:
    """Eine Meldung fuer Chef/Sucher (in-App). Laeuft nach MELDUNG_TAGE Tagen aus (TTL-Index)."""
    if not empfaenger:
        return None
    import uuid
    doc = {"id": uuid.uuid4().hex, "dealer_id": dealer_id, "empfaenger": sorted(set(empfaenger)), "typ": typ,
           "text": text[:500], "ref": ref, "contract_no": contract_no, "erstellt_am": now_iso(),
           "gelesen": {}, "laeuft_ab": datetime.now(timezone.utc) + timedelta(days=MELDUNG_TAGE)}
    try:
        await db.meldungen.insert_one(dict(doc))
    except Exception:  # noqa: BLE001 — eine Meldung darf den Vorgang nie scheitern lassen
        log.exception("Meldung %s fuer %s nicht gespeichert", typ, dealer_id)
        return None
    return doc["id"]


def _meldung_sicht(m: dict, user_id: str) -> dict:
    return {"id": m["id"], "typ": m.get("typ"), "text": m.get("text"), "ref": m.get("ref"),
            "contract_no": m.get("contract_no"), "erstellt_am": m.get("erstellt_am"),
            "gelesen": bool((m.get("gelesen") or {}).get(user_id))}


@router.get("/meldungen")
async def meldungen_liste(limit: int = 50, user=Depends(current_firma)):
    limit = max(1, min(200, int(limit)))
    cur = db.meldungen.find({"dealer_id": user["dealer_id"], "empfaenger": user["id"]}, {"_id": 0}) \
        .sort("erstellt_am", -1).limit(limit)
    return [_meldung_sicht(m, user["id"]) async for m in cur]


@router.get("/meldungen/anzahl")
async def meldungen_anzahl(user=Depends(current_firma)):
    """Fuer den Zaehler im Menue (wie /protocols/zur-freigabe/anzahl): ungelesene Meldungen mit IDs."""
    ids = [m["id"] async for m in db.meldungen.find(
        {"dealer_id": user["dealer_id"], "empfaenger": user["id"], f"gelesen.{user['id']}": {"$exists": False}},
        {"_id": 0, "id": 1}).sort("erstellt_am", -1).limit(100)]
    return {"ungelesen": len(ids), "ids": ids}


@router.post("/meldungen/{meldung_id}/gelesen")
async def meldung_gelesen(meldung_id: str, user=Depends(current_firma)):
    await db.meldungen.update_one({"id": meldung_id, "dealer_id": user["dealer_id"], "empfaenger": user["id"]},
                                  {"$set": {f"gelesen.{user['id']}": now_iso()}})
    return {"ok": True}


@router.post("/meldungen/alle-gelesen")
async def meldungen_alle_gelesen(user=Depends(current_firma)):
    r = await db.meldungen.update_many({"dealer_id": user["dealer_id"], "empfaenger": user["id"],
                                        f"gelesen.{user['id']}": {"$exists": False}},
                                       {"$set": {f"gelesen.{user['id']}": now_iso()}})
    return {"ok": True, "gelesen": int(r.modified_count)}
