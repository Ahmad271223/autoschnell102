# -*- coding: utf-8 -*-
"""Bild-Proxy fuer Inseratsfotos (10.09.2026, Befund Ahmad: "sehr oft laden
die Bilder beim Vertrag erstellen nicht").

Bisher lud der Browser die Fotos DIREKT von den Portal-CDNs
(img.kleinanzeigen.de, img.classistatic.de, prod.pictures.autoscout24.net):
fremde Hosts, teils AVIF, teils 100-140 KB je Vorschaubild, abhaengig von
Werbeblockern, Referrer-Regeln und Portal-Launen. Jetzt holt der Server
das Bild EINMAL, verkleinert es zu einem JPEG-Vorschaubild (max. 640 px)
und liefert es von der eigenen Adresse aus, mit Zwischenspeicher.

Sicherheit: kein offener Proxy —
  * nur https-Adressen der bekannten Portal-Hosts (Allowliste),
  * jede Adresse traegt eine Signatur mit Ablauf (wie /api/files),
    ausgestellt nur in angemeldeten bzw. serverseitig gebauten Antworten,
  * Groessenlimit, Zeitlimit, je IP gedrosselt (server.py).
"""
from __future__ import annotations

import asyncio
import hashlib
import hmac
import io
import logging
import os
import time
import weakref
from collections import OrderedDict
from typing import Iterable, List, Optional
from urllib.parse import quote, urlparse

log = logging.getLogger("autohandel")

ERLAUBTE_HOSTS = {
    "img.kleinanzeigen.de", "img.ebay-kleinanzeigen.de",
    "img.classistatic.de", "m.classistatic.de",
    "prod.pictures.autoscout24.net", "listing-images.autoscout24.net",
}
for _h in (os.environ.get("BILD_PROXY_HOSTS") or "").split(","):
    if _h.strip():
        ERLAUBTE_HOSTS.add(_h.strip().lower())

from konfig import zahl_env  # Pruefung 14.09.2026: keine Abstuerze durch .env-Tippfehler
THUMB_KANTE = zahl_env("BILD_PROXY_KANTE", 640, unten=64, oben=4096)
MAX_BYTES = 8 * 1024 * 1024
ZEITLIMIT = 12.0
# Pruefbericht 20.09.2026 (B-09): ZEITLIMIT gilt je Phase (Verbinden, Lesen);
# mit drei Weiterleitungen und langsamem Lesen wartete ein Bild ueber eine
# Minute. Jetzt ein Gesamtdeckel je Bild.
HOLEN_GESAMT_SEKUNDEN = 15.0
# B-09: Fehlschlaege (kein Bild, fremder Host, Zeitueberschreitung) werden
# gemerkt — vorher fragte jede Vertragsseite dieselbe tote Adresse erneut ab.
NEGATIV_SEKUNDEN = zahl_env("BILD_PROXY_NEGATIV_SEKUNDEN", 600, unten=0)
_NEGATIV_MAX = 2000
STANDARD_TTL = 7 * 24 * 3600
_CACHE_MAX = zahl_env("BILD_PROXY_CACHE", 400, unten=10)
_cache: "OrderedDict[str, bytes]" = OrderedDict()
_cache_lock = asyncio.Lock()
_negativ: "OrderedDict[str, float]" = OrderedDict()      # url -> Ablauf (monotonic)
# B-09: EIN httpx-Client je Event-Loop statt einem neuen je Bild (TLS-Aufbau
# fuer jedes Foto). Je Loop, weil Verbindungen an die Schleife gebunden sind
# (Tests starten mehrere Schleifen).
_clients: "weakref.WeakKeyDictionary" = weakref.WeakKeyDictionary()
_UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
       "(KHTML, like Gecko) Chrome/128.0 Safari/537.36 AutoSchnell-Bildproxy")


def _mac(url: str, exp: int) -> str:
    from dateien import _geheimnis
    return hmac.new(_geheimnis(), f"bild|{url}|{exp}".encode("utf-8"),
                    hashlib.sha256).hexdigest()[:40]


def erlaubt(url: str) -> bool:
    try:
        p = urlparse(url)
    except Exception:
        return False
    return p.scheme == "https" and (p.hostname or "").lower() in ERLAUBTE_HOSTS \
        and len(url) <= 2000


def bild_url(url: str, ttl: Optional[int] = None) -> str:
    """Signierte Proxy-Adresse fuer ein erlaubtes Portal-Foto; alles andere
    (eigene /api/files-Links, unbekannte Hosts) bleibt unveraendert."""
    if not isinstance(url, str) or not erlaubt(url):
        return url
    exp = int(time.time()) + int(ttl or STANDARD_TTL)
    return f"/api/bild?u={quote(url, safe='')}&exp={exp}&sig={_mac(url, exp)}"


def thumbs(urls: Iterable, ttl: Optional[int] = None) -> List[str]:
    return [bild_url(u, ttl) for u in (urls or []) if isinstance(u, str) and u]


def gueltig(url: str, exp, sig) -> bool:
    try:
        exp_i = int(exp)
    except (TypeError, ValueError):
        return False
    if exp_i < int(time.time()) or not sig or not isinstance(sig, str):
        return False
    return hmac.compare_digest(_mac(url, exp_i), sig)


# Dekompressionsbomben: ein 8-MB-JPEG kann 100+ Megapixel ausgeben.
# Gilt fuer BEIDE Pfade — Vorschau und PDF (Nr. 57).
_MAX_PIXEL = 40_000_000


def _verkleinern(raw: bytes) -> bytes:
    """Vorschaubild erzeugen.

    Nachpruefung 20.09.2026, Nr. 57: hier stand `Image.open(...)` direkt
    gefolgt von `im.load()` — OHNE die Pixelpruefung, die der PDF-Pfad
    (_fuer_pdf) schon hatte. Ein stark komprimiertes Bild mit riesigen
    Abmessungen ("Dekompressionsbombe") belegte damit beim Entpacken
    hunderte MB Arbeitsspeicher, obwohl die Datei selbst klein war und die
    Byte-Grenze (MAX_BYTES) locker einhielt. Jetzt gilt dieselbe Grenze in
    beiden Pfaden — geprueft VOR dem Entpacken."""
    from PIL import Image
    im = Image.open(io.BytesIO(raw))
    w, h = im.size
    if w * h > _MAX_PIXEL:
        raise ValueError(f"Bild zu gross ({w}x{h})")
    im.draft("RGB", (THUMB_KANTE, THUMB_KANTE))   # JPEG kleiner dekodieren
    im.load()
    if im.mode not in ("RGB", "L"):
        im = im.convert("RGB")
    im.thumbnail((THUMB_KANTE, THUMB_KANTE))
    out = io.BytesIO()
    im.save(out, "JPEG", quality=80, optimize=True)
    return out.getvalue()


_WEITERLEITUNGEN = (301, 302, 303, 307, 308)


def _negativ_gemerkt(url: str) -> bool:
    ablauf = _negativ.get(url)
    if ablauf is None:
        return False
    if ablauf < time.monotonic():
        _negativ.pop(url, None)
        return False
    return True


def _negativ_merken(url: str) -> None:
    if NEGATIV_SEKUNDEN <= 0:
        return
    _negativ[url] = time.monotonic() + NEGATIV_SEKUNDEN
    _negativ.move_to_end(url)
    while len(_negativ) > _NEGATIV_MAX:
        _negativ.popitem(last=False)


async def _holen(url: str) -> Optional[bytes]:
    """Rohdaten eines erlaubten Portal-Fotos (hoechstens MAX_BYTES).

    Weiterleitungen werden von Hand verfolgt (hoechstens 3) und JEDES Ziel
    erneut gegen die Allowliste geprueft. Vorher folgte httpx jeder
    Weiterleitung selbst (follow_redirects=True) — ein Portal-Host haette so
    auf beliebige Adressen zeigen koennen, auch auf interne.
    B-09: gemeinsamer Client je Loop, Gesamtdeckel HOLEN_GESAMT_SEKUNDEN."""
    if not erlaubt(url):
        return None
    import httpx
    ziel = url
    # Gemeinsamer Client dieser Event-Loop (B-09), bei Bedarf angelegt.
    loop = asyncio.get_running_loop()
    client = _clients.get(loop)
    if client is None:
        client = httpx.AsyncClient(
            timeout=ZEITLIMIT, follow_redirects=False,
            limits=httpx.Limits(max_connections=32, max_keepalive_connections=16),
            headers={"User-Agent": _UA,
                     "Accept": "image/jpeg,image/png,image/webp;q=0.9,*/*;q=0.5"})
        _clients[loop] = client

    async def _lauf() -> Optional[bytes]:
        nonlocal ziel
        for _ in range(4):
            async with client.stream("GET", ziel) as r:
                if r.status_code in _WEITERLEITUNGEN:
                    ziel = str(httpx.URL(ziel).join(r.headers.get("location") or ""))
                    if not erlaubt(ziel):
                        log.info("Bild-Proxy: Weiterleitung auf fremden Host verworfen (%s)",
                                 ziel[:120])
                        return None
                    continue
                if r.status_code != 200:
                    return None
                ctype = (r.headers.get("content-type") or "").lower()
                if not ctype.startswith("image/"):
                    return None
                teile, gesamt = [], 0
                async for chunk in r.aiter_bytes():
                    gesamt += len(chunk)
                    if gesamt > MAX_BYTES:
                        return None
                    teile.append(chunk)
                return b"".join(teile)
        return None

    return await asyncio.wait_for(_lauf(), HOLEN_GESAMT_SEKUNDEN)


def _fuer_pdf(raw: bytes, kante: int, qualitaet: int = 0) -> bytes:
    from PIL import Image, ImageOps
    im = Image.open(io.BytesIO(raw))
    w, h = im.size
    if w * h > _MAX_PIXEL:
        raise ValueError(f"Bild zu gross ({w}x{h})")
    im.draft("RGB", (kante, kante))       # JPEG: beim Dekodieren schon verkleinern
    im.load()
    im = ImageOps.exif_transpose(im)
    if im.mode != "RGB":
        im = im.convert("RGB")
    im.thumbnail((kante, kante))
    out = io.BytesIO()
    # Ohne exif=...: Metadaten (Standort, Geraet) landen nicht im Dokument.
    # qualitaet=0 -> wie bisher nach Kantenlaenge. Das Beweisdokument gibt
    # seit Runde 26 eigene Werte vor (kleinere Datei, Wunsch Ahmad).
    q = qualitaet if qualitaet else (75 if kante >= 1200 else 70)
    im.save(out, "JPEG", quality=max(40, min(int(q), 95)), optimize=True)
    return out.getvalue()


async def laden_fuer_pdf(url: str, kante: int = 800,
                         qualitaet: int = 0) -> Optional[bytes]:
    """Inseratsfoto fuer das Beweisdokument: JPEG mit hoechstens `kante` px,
    ohne Metadaten. None, wenn das Portal nicht liefert oder die Adresse
    nicht erlaubt ist. Bewusst OHNE den Vorschau-Zwischenspeicher (andere
    Groesse; jedes Foto wird fuer genau ein Dokument einmal geladen)."""
    if _negativ_gemerkt(url):
        return None
    try:
        raw = await _holen(url)
        if not raw:
            _negativ_merken(url)
            return None
        return await asyncio.to_thread(_fuer_pdf, raw,
                                       max(200, min(int(kante), 2000)), qualitaet)
    except Exception as exc:  # noqa: BLE001
        _negativ_merken(url)
        log.info("Bild-Proxy (PDF): %s nicht ladbar (%s)", url[:120], exc.__class__.__name__)
        return None


async def laden(url: str) -> Optional[bytes]:
    """Vorschaubild (JPEG) fuer die Adresse — aus dem Zwischenspeicher oder
    frisch geholt. None, wenn das Portal nicht liefert."""
    async with _cache_lock:
        hit = _cache.get(url)
        if hit is not None:
            _cache.move_to_end(url)
            return hit
    if _negativ_gemerkt(url):
        return None
    try:
        raw = await _holen(url)
        if not raw:
            _negativ_merken(url)
            return None
        klein = await asyncio.to_thread(_verkleinern, raw)
    except Exception as exc:  # noqa: BLE001
        _negativ_merken(url)
        log.info("Bild-Proxy: %s nicht ladbar (%s)", url[:120], exc.__class__.__name__)
        return None
    async with _cache_lock:
        _cache[url] = klein
        _cache.move_to_end(url)
        while len(_cache) > _CACHE_MAX:
            _cache.popitem(last=False)
    return klein
