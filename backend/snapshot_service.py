"""Listing-snapshot service.

Captures a full proof-of-listing artifact for any kleinanzeigen.de or
mobile.de vehicle ad URL:

  • Full-page PNG screenshot (what the seller saw at time T)
  • Single-page PDF version of the same render
  • Persists both to Emergent Object Storage
  • Inserts a `listing_snapshots` record so the user can list/download
    them later from contracts or the calendar.

Capturing happens in a FastAPI background task so `/api/mobile/compare`
returns immediately; the UI polls the snapshot endpoint until the bytes
are in storage.
"""
from __future__ import annotations

import asyncio
import base64
import json
import logging
import os
import random
import subprocess
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

# Playwright stores its bundled browsers under /pw-browsers/ in this
# container (pre-baked by the platform image). The env var must be set
# *before* `playwright.async_api` imports happen — the supervisor process
# doesn't propagate this env var, so we set it here as a fallback.
os.environ.setdefault("PLAYWRIGHT_BROWSERS_PATH", "/pw-browsers")

import requests  # noqa: E402
from playwright.async_api import async_playwright  # noqa: E402

log = logging.getLogger("autohandel.snapshot")

STORAGE_URL = "https://integrations.emergentagent.com/objstore/api/v1/storage"
APP_NAME = "autohandel"

# Lokaler Speicher-Fallback: wenn kein EMERGENT_LLM_KEY gesetzt ist,
# werden Snapshots lokal unter backend/local_storage/ abgelegt.
_LOCAL_STORAGE = Path(__file__).parent / "local_storage"
_USE_LOCAL = not os.environ.get("EMERGENT_LLM_KEY")

# Runtime state
_storage_key: Optional[str] = None

# ---------------------------------------------------------------------------
# Snapshot-Concurrency
# ---------------------------------------------------------------------------
# Frueher: asyncio.Lock() = genau 1 Snapshot gleichzeitig (Schutz gegen OOM
# bei LOKALEN Chromium-Instanzen). Mit browserless.io laeuft der Browser aber
# remote in der gehosteten Farm — der lokale Prozess haelt nur die
# WebSocket-Verbindung. Dadurch koennen wir VIELE Snapshots gleichzeitig
# anstossen; die echte Parallelitaet skaliert browserless.io.
#
# SNAPSHOT_CONCURRENCY steuert, wie viele Snapshots dieser Prozess gleichzeitig
# fahren darf. Default:
#   - mit browserless konfiguriert: 50 (an den browserless-Plan anpassen!)
#   - ohne (lokaler Chromium):       1 (wie bisher, schont RAM)
# Per ENV ueberschreibbar.
_BROWSERLESS_URL = (os.environ.get("BROWSERLESS_URL") or "").strip()
_BROWSERLESS_TOKEN = (os.environ.get("BROWSERLESS_TOKEN") or "").strip()
_default_concurrency = 50 if _BROWSERLESS_URL else 1
SNAPSHOT_CONCURRENCY = int(
    os.environ.get("SNAPSHOT_CONCURRENCY", str(_default_concurrency))
)
# Semaphore statt Lock: erlaubt N gleichzeitige Snapshots (N=1 == altes Lock).
_browser_lock = asyncio.Semaphore(max(1, SNAPSHOT_CONCURRENCY))

WORKER_TIMEOUT_SECONDS = int(os.environ.get("SNAPSHOT_WORKER_TIMEOUT", "90"))


def _run_worker(args, stdin_text=None, timeout=None):
    """Playwright-Worker als EIGENE Prozessgruppe starten und bei Timeout
    die GANZE Gruppe beenden (Pruefbericht Runde 4): subprocess.run
    killte nur den Python-Worker, die von ihm gestarteten Chromium-
    Prozesse liefen als Waisen weiter und frassen RAM/CPU."""
    import signal
    env = os.environ.copy()
    if sys.platform == "win32":
        env.pop("PLAYWRIGHT_BROWSERS_PATH", None)
        flags = {"creationflags": subprocess.CREATE_NEW_PROCESS_GROUP}
    else:
        flags = {"start_new_session": True}
    proc = subprocess.Popen(
        args, stdin=subprocess.PIPE if stdin_text is not None else None,
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, env=env, **flags)
    try:
        out, err = proc.communicate(
            input=stdin_text.encode("utf-8") if stdin_text is not None else None,
            timeout=timeout or WORKER_TIMEOUT_SECONDS)
    except subprocess.TimeoutExpired:
        try:
            if sys.platform == "win32":
                subprocess.run(["taskkill", "/F", "/T", "/PID", str(proc.pid)],
                               capture_output=True, timeout=15)
            else:
                os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
        except Exception:  # noqa: BLE001
            pass
        try:
            proc.kill()
        except Exception:  # noqa: BLE001
            pass
        proc.communicate(timeout=10)
        raise RuntimeError("Playwright-Worker: Zeitlimit ueberschritten, "
                           "Prozessgruppe beendet")
    return (out.decode("utf-8", "replace"), err.decode("utf-8", "replace"),
            proc.returncode)


# -------------------- Object Storage --------------------
def init_storage() -> Optional[str]:
    """Initialize storage session once. Safe to call repeatedly — returns
    the cached key on subsequent calls."""
    global _storage_key
    if _storage_key:
        return _storage_key
    # Read key lazily — server.py loads .env *after* this module is imported.
    emergent_key = os.environ.get("EMERGENT_LLM_KEY", "")
    if not emergent_key:
        log.warning("EMERGENT_LLM_KEY not configured — snapshot storage disabled")
        return None
    try:
        r = requests.post(
            f"{STORAGE_URL}/init",
            json={"emergent_key": emergent_key},
            timeout=30,
        )
        r.raise_for_status()
        _storage_key = r.json()["storage_key"]
        log.info("snapshot storage initialized")
        return _storage_key
    except Exception as exc:
        log.error("snapshot storage init failed: %s", exc)
        return None


def _safe_local_path(path: str) -> Path:
    """Loest einen Storage-Pfad relativ zu _LOCAL_STORAGE auf und stellt
    sicher, dass er das Verzeichnis NICHT verlaesst (Path-Traversal-Schutz,
    Defense-in-Depth). Wirft ValueError bei Ausbruchsversuch (z.B. '../').
    """
    base = _LOCAL_STORAGE.resolve()
    dest = (base / path).resolve()
    if dest != base and base not in dest.parents:
        raise ValueError(f"Ungueltiger Storage-Pfad (Traversal): {path!r}")
    return dest


def _put_object(path: str, data: bytes, content_type: str) -> dict:
    if _USE_LOCAL:
        dest = _safe_local_path(path)
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(data)
        log.info("local_storage: saved %s (%d bytes)", path, len(data))
        return {"path": path}
    key = init_storage()
    if not key:
        raise RuntimeError("Storage not initialized")
    r = requests.put(
        f"{STORAGE_URL}/objects/{path}",
        headers={"X-Storage-Key": key, "Content-Type": content_type},
        data=data,
        timeout=120,
    )
    r.raise_for_status()
    return r.json()


def get_object(path: str) -> tuple[bytes, str]:
    """Read object back. Returns (bytes, content_type)."""
    if _USE_LOCAL:
        dest = _safe_local_path(path)
        if not dest.exists():
            raise FileNotFoundError(f"local_storage: {path} not found")
        ext = dest.suffix.lower()
        ct = "application/pdf" if ext == ".pdf" else "image/jpeg"
        return dest.read_bytes(), ct
    key = init_storage()
    if not key:
        raise RuntimeError("Storage not initialized")
    r = requests.get(
        f"{STORAGE_URL}/objects/{path}",
        headers={"X-Storage-Key": key},
        timeout=60,
    )
    r.raise_for_status()
    return r.content, r.headers.get("Content-Type", "application/octet-stream")


def delete_object(path: str) -> bool:
    """Best-effort object-delete. Returns True bei 2xx/404, False sonst.
    404 gilt als OK, weil das Objekt eh weg ist."""
    if _USE_LOCAL:
        dest = _LOCAL_STORAGE / path
        try:
            dest.unlink(missing_ok=True)
        except Exception:
            pass
        return True
    key = init_storage()
    if not key:
        return False
    try:
        r = requests.delete(
            f"{STORAGE_URL}/objects/{path}",
            headers={"X-Storage-Key": key},
            timeout=30,
        )
        if r.status_code in (200, 204, 404):
            return True
        log.warning("delete_object %s -> %s", path, r.status_code)
        return False
    except Exception as exc:
        log.warning("delete_object %s failed: %s", path, exc)
        return False


# -------------------- Playwright capture --------------------
_PAGE_HEADERS = {
    "User-Agent": ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                   "AppleWebKit/537.36 (KHTML, like Gecko) "
                   "Chrome/124.0.0.0 Safari/537.36"),
    "Accept-Language": "de-DE,de;q=0.9,en-US;q=0.8,en;q=0.7",
}


# Target screenshot width — full-page screenshots regularly exceed 6000px
# height which is wasteful. We resample down to MAX_WIDTH if wider, keeping
# aspect, then save as quality-JPEG. The JPEG is also reused as the
# single-page PDF body (massively smaller than Playwright's native print).
#
# Tuning 2026: width 1280 -> 1100 and quality 75 -> 55 zusammen mit
# progressive+optimize -> rund 50-60% kleinere Artefakte bei im Druck
# weiterhin sehr gut lesbaren Inserat-Screenshots. Für Beweissicherung
# reicht das vollkommen; Schrift und Bild-Details bleiben erkennbar.
MAX_WIDTH = 1100
JPEG_QUALITY = 55
# Harte Zielgrösse pro Artefakt (Wunsch 08/2026): unter ~600 KB. Die
# Kompressionsstufen unten werden durchprobiert, bis das JPEG darunter
# liegt — erst Qualität senken, dann zusätzlich die Breite reduzieren.
TARGET_BYTES = 600 * 1024
_COMPRESSION_STEPS = (
    (MAX_WIDTH, JPEG_QUALITY),
    (MAX_WIDTH, 45),
    (MAX_WIDTH, 35),
    (900, 40),
    (900, 32),
    (750, 30),
    (640, 26),
)


def _compress_artifacts(png_bytes: bytes, pdf_fallback: bytes) -> tuple[bytes, bytes]:
    """Re-encode the raw PNG screenshot as a JPEG (much smaller) and build
    a 1-page image-PDF from the same JPEG. Probiert Stufen durch, bis das
    JPEG unter TARGET_BYTES liegt (lange Inserats-Seiten brauchen mehr
    Kompression als kurze). Falls back to the original Playwright PDF if
    Pillow rejects the screenshot for any reason."""
    try:
        from PIL import Image  # local import — Pillow is heavy
        import io
        with Image.open(io.BytesIO(png_bytes)) as im:
            im = im.convert("RGB")

            jpg_bytes = b""
            pdf_bytes = b""
            for width, quality in _COMPRESSION_STEPS:
                # Immer vom Original skalieren (kein doppeltes Resampling).
                step_im = im
                if step_im.width > width:
                    ratio = width / step_im.width
                    step_im = step_im.resize(
                        (width, int(step_im.height * ratio)), Image.LANCZOS)
                buf = io.BytesIO()
                step_im.save(buf, format="JPEG", quality=quality,
                             optimize=True, progressive=True)
                jpg_bytes = buf.getvalue()
                # resolution=72 (statt 100) -> weniger Meta-Overhead; quality
                # mitgeben, sonst kodiert Pillow das PDF-Bild mit Default-75
                # neu und das PDF wird deutlich groesser als das JPEG.
                pdf_buf = io.BytesIO()
                step_im.save(pdf_buf, format="PDF", resolution=72.0,
                             quality=quality)
                pdf_bytes = pdf_buf.getvalue()
                if len(jpg_bytes) <= TARGET_BYTES and len(pdf_bytes) <= TARGET_BYTES:
                    break
        return jpg_bytes, pdf_bytes
    except Exception as exc:
        log.warning("artifact compression failed (%s) — using raw outputs", exc)
        return png_bytes, pdf_fallback


def _ensure_browser_executable() -> None:
    """Self-heal für die berüchtigte Playwright-Meldung
    `Executable doesn't exist at /pw-browsers/chromium_headless_shell-1217/...`.

    Kubernetes/Container-Restarts können den Symlink verlieren. Wir legen ihn
    bei jedem Snapshot-Versuch wieder an, indem wir den höchstvorhandenen
    `chromium_headless_shell-XXXX`-Ordner in /pw-browsers verlinken.
    """
    base = "/pw-browsers"
    try:
        if not os.path.isdir(base):
            return
        # Welcher Pfad wird von Playwright tatsächlich verlangt?
        # Wir können den nicht direkt lesen, also stellen wir sicher,
        # dass JEDE bekannte Versions-Anfrage auf den real existierenden
        # Ordner zeigt.
        existing = sorted(
            d for d in os.listdir(base)
            if d.startswith("chromium_headless_shell-") and os.path.isdir(os.path.join(base, d))
        )
        # Welcher Ordner enthält die echte Binary?
        target = None
        for d in existing:
            shell = os.path.join(base, d, "chrome-linux", "headless_shell")
            if os.path.isfile(shell) and not os.path.islink(os.path.join(base, d)):
                target = d
                break
        if not target:
            return
        # Bekannte Build-Versionen, die Playwright in unterschiedlichen
        # Releases anfragt – wir verlinken alle, die noch fehlen.
        known_versions = ["1208", "1217", "1218", "1224", "1230"]
        for ver in known_versions:
            link = os.path.join(base, f"chromium_headless_shell-{ver}")
            if link.endswith(target):
                continue  # selbst der echte Ordner
            try:
                shell = os.path.join(link, "chrome-linux", "headless_shell")
                if os.path.isfile(shell):
                    continue
                if os.path.islink(link) or os.path.exists(link):
                    if os.path.islink(link):
                        os.unlink(link)
                    else:
                        # echter Ordner ohne Binary – nicht überschreiben
                        continue
                os.symlink(os.path.join(base, target), link)
                log.info("playwright self-heal: linked %s -> %s", link, target)
            except Exception as exc:
                log.warning("playwright self-heal failed for %s: %s", link, exc)
    except Exception as exc:
        log.warning("playwright self-heal scan failed: %s", exc)


# Domains that Playwright is allowed to screenshot (SSRF allowlist).
_ALLOWED_SNAPSHOT_DOMAINS = frozenset({
    "mobile.de",
    "suchen.mobile.de",
    "www.mobile.de",
    "kleinanzeigen.de",
    "www.kleinanzeigen.de",
})


def _assert_allowed_snapshot_url(url: str) -> None:
    """Raise ValueError if the URL's hostname is not in the allowlist.

    This prevents SSRF via a crafted URL that embeds an allowed domain name
    as a query parameter or path segment (e.g. http://evil.com/?ref=mobile.de).
    We always parse the hostname via urlparse, which is not spoofable.
    """
    from urllib.parse import urlparse
    parsed = urlparse(url)
    scheme = (parsed.scheme or "").lower()
    host = (parsed.hostname or "").lower()
    if scheme not in ("http", "https"):
        raise ValueError(f"Ungültiges URL-Schema für Snapshot: {scheme!r}")
    # Check exact hostname or suffix match (e.g. "suchen.mobile.de" ends with "mobile.de")
    allowed = any(
        host == domain or host.endswith(f".{domain}")
        for domain in _ALLOWED_SNAPSHOT_DOMAINS
    )
    if not allowed:
        raise ValueError(f"Snapshot-Domain nicht erlaubt: {host!r}")


async def _capture_with_playwright(url: str) -> tuple[bytes, bytes]:
    """Render the URL in headless Chromium, return (png_bytes, pdf_bytes).
    Läuft in einem separaten Subprocess damit Playwright auf Windows sein
    eigenes ProactorEventLoop-kompatibles asyncio.run() bekommt."""
    # SSRF guard: only screenshot known car-listing portals.
    _assert_allowed_snapshot_url(url)
    # Lokalen Browser nur sicherstellen, wenn KEIN browserless genutzt wird.
    if not _BROWSERLESS_URL:
        _ensure_browser_executable()
    async with _browser_lock:
        worker = Path(__file__).parent / "_playwright_worker.py"
        loop = asyncio.get_running_loop()

        # Eigene Prozessgruppe + Gruppen-Kill bei Timeout (siehe _run_worker).
        stdout, stderr, rc = await loop.run_in_executor(
            None, _run_worker, [sys.executable, str(worker), url])
        try:
            data = json.loads(stdout)
        except Exception:
            raise RuntimeError(f"Playwright-Worker ungültige Ausgabe: {stderr[:300]}")
        if "error" in data:
            raise RuntimeError(f"Playwright-Worker Fehler: {data['error']}")
        png = base64.b64decode(data["png"])
        pdf = base64.b64decode(data["pdf"])
        return png, pdf

    # ---- alter In-Process-Code (nie mehr erreicht, aber als Referenz) ----
    if False:  # pragma: no cover
        async with async_playwright() as p:
            browser = await p.chromium.launch(headless=True, args=["--no-sandbox"])
            try:
                ctx = await browser.new_context(
                    viewport={"width": 1280, "height": 900},
                    user_agent=_PAGE_HEADERS["User-Agent"],
                    locale="de-DE",
                    extra_http_headers={"Accept-Language": _PAGE_HEADERS["Accept-Language"]},
                )
                page = await ctx.new_page()
                # `networkidle` rarely triggers on real ad pages because of
                # ongoing tracking/analytics requests. `domcontentloaded`
                # gives us the rendered DOM in 1-3s; we then wait briefly
                # for late-loading images via the scroll trick below.
                try:
                    await page.goto(url, wait_until="domcontentloaded", timeout=30000)
                except Exception:
                    # Fallback: try a more lenient wait condition.
                    await page.goto(url, wait_until="commit", timeout=20000)
                await page.wait_for_timeout(1500)
                # Cookie / TCF consent. Both kleinanzeigen.de and mobile.de
                # render the banner inside a Sourcepoint <iframe> with id
                # `sp_message_iframe_*`. We try the iframe path first
                # (covers ≥95% of cases) and fall back to inline banners.
                async def _accept_in_iframe() -> bool:
                    for fr in page.frames:
                        if "sp_message" not in (fr.name or "") and "consent" not in (fr.url or "").lower():
                            continue
                        for sel in (
                            'button[title*="Akzeptieren"]',
                            'button[aria-label*="Akzeptieren"]',
                            'button:has-text("Alle akzeptieren")',
                            'button:has-text("Akzeptieren und weiter")',
                            'button:has-text("Akzeptieren")',
                            'button:has-text("Zustimmen")',
                            'button.message-component[title*="kzeptieren"]',
                        ):
                            try:
                                btn = fr.locator(sel).first
                                if await btn.count() > 0:
                                    await btn.click(timeout=3000)
                                    return True
                            except Exception:
                                continue
                    return False

                # Banner can take 1-3s to render (CMP-iframe lazy-load).
                consent_dismissed = False
                for attempt in range(3):
                    await page.wait_for_timeout(800)
                    if await _accept_in_iframe():
                        consent_dismissed = True
                        break
                    # Inline/native banners (no iframe)
                    for sel in (
                        '[data-testid="gdpr-banner-accept"]',
                        'button:has-text("Alle akzeptieren")',
                        'button:has-text("Akzeptieren")',
                        'button:has-text("Zustimmen")',
                        'button.gdpr-accept',
                        '#gdpr-banner-accept',
                    ):
                        try:
                            btn = page.locator(sel).first
                            if await btn.count() > 0 and await btn.is_visible():
                                await btn.click(timeout=2000)
                                consent_dismissed = True
                                break
                        except Exception:
                            continue
                    if consent_dismissed:
                        break
                if consent_dismissed:
                    # Give the banner-close-animation time to play out.
                    await page.wait_for_timeout(1200)
                else:
                    log.info("snapshot: no consent banner detected — proceeding")

                # Secondary popups: kleinanzeigen.de occasionally shows a
                # "Hallo! Willkommen…" login-hint tooltip after consent. Try
                # to close any visible close-buttons (×) in the upper area.
                for sel in (
                    'button[aria-label*="schließ" i]',
                    'button[aria-label*="close" i]',
                    'button[aria-label="Schließen"]',
                    '[data-testid*="close-btn" i]',
                    '[data-testid*="dismiss" i]',
                    'button:has-text("Später")',
                    'button:has-text("Nicht jetzt")',
                ):
                    try:
                        for btn in await page.locator(sel).all():
                            if await btn.is_visible():
                                await btn.click(timeout=1500)
                                await page.wait_for_timeout(300)
                    except Exception:
                        continue

                # Belt-and-braces: nuke any remaining floating overlays via
                # CSS so they can't cover the actual ad. Targets the known
                # KA `.login-overlay` and similar consent/footer banners.
                try:
                    await page.add_style_tag(content="""
                        .login-overlay, .site-signin-wrapper,
                        [class*="login-overlay"],
                        [id*="sp_message_container"],
                        #onetrust-banner-sdk, #onetrust-consent-sdk,
                        [class*="cookie-banner"], [class*="CookieBanner"],
                        [class*="consent-banner"], [class*="ConsentBanner"],
                        [data-testid*="footer-cookie"],
                        [aria-label*="Cookie" i] {
                            display: none !important;
                            visibility: hidden !important;
                            opacity: 0 !important;
                        }
                    """)
                    await page.wait_for_timeout(300)
                except Exception:
                    pass
                # Lazy-loaded images: scroll down then back up so they fetch.
                try:
                    await page.evaluate(
                        "() => new Promise(r => {"
                        " let y = 0; const step = () => {"
                        "  window.scrollTo(0, y); y += 500;"
                        "  if (y < document.body.scrollHeight) setTimeout(step, 80);"
                        "  else { window.scrollTo(0, 0); setTimeout(r, 600); }"
                        " }; step(); })"
                    )
                except Exception:
                    pass
                png = await page.screenshot(full_page=True, type="png")
                pdf = await page.pdf(format="A4", print_background=True,
                                     margin={"top": "12mm", "bottom": "12mm",
                                             "left": "10mm", "right": "10mm"})
                # Compress: full-page PNG screenshots from Playwright are
                # huge (1.5-4 MB). Re-encode as quality-75 JPEG and rebuild
                # the PDF from that JPEG. Saves ~70-85% on storage.
                png, pdf = _compress_artifacts(png, pdf)
                return png, pdf
            finally:
                await browser.close()


# -------------------- High-level API --------------------
async def create_snapshot(
    db,
    *,
    dealer_id: str,
    user_id: str,
    vehicle_id: Optional[str],
    mobile_ad_id: Optional[str],
    source_url: str,
    snapshot_id: Optional[str] = None,
) -> str:
    """Create a `listing_snapshots` row in 'pending' state and return its id.
    Capture/upload runs as a background task via `run_snapshot_job`.

    `snapshot_id` erlaubt es, eine VORHER reservierte ID zu verwenden — so
    kann der Aufrufer sich den Snapshot atomar sichern, bevor er ihn
    anlegt (verhindert Doppel-Snapshots bei gleichzeitigen Vergleichen)."""
    snap_id = snapshot_id or str(uuid.uuid4())
    await db.listing_snapshots.insert_one({
        "id": snap_id,
        "dealer_id": dealer_id,
        "user_id": user_id,
        "vehicle_id": vehicle_id,
        "mobile_ad_id": mobile_ad_id,
        "source_url": source_url,
        "status": "pending",
        "pdf_path": None,
        "png_path": None,
        "error": None,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "completed_at": None,
    })
    return snap_id


# Fehler, die ein erneuter Versuch NICHT heilt (kein Retry).
_PERMANENT_HINTS = ("nicht erlaubt", "not allowed", "ungültige url", "invalid url",
                    "keine url")


def _is_transient(exc: Exception) -> bool:
    """True, wenn ein erneuter Versuch sinnvoll ist. Netzwerk-/Timeout-/Bot-
    Block-Fehler dominieren bei Snapshots und sind vorübergehend — daher
    Default: wiederholen. Nur klar permanente Fehler brechen sofort ab."""
    m = str(exc).lower()
    return not any(h in m for h in _PERMANENT_HINTS)


async def _capture_with_retry(db, snap_id: str, url: str, attempts: int = 3):
    """Capture mit Backoff. Der Browser-Lock wird zwischen den Versuchen
    freigegeben (Sleep passiert außerhalb von _capture_with_playwright), damit
    andere Snapshots währenddessen laufen können."""
    last_exc = None
    for i in range(1, attempts + 1):
        try:
            return await _capture_with_playwright(url)
        except Exception as exc:
            last_exc = exc
            if i >= attempts or not _is_transient(exc):
                raise
            delay = min(45.0, 5.0 * (3 ** (i - 1))) * random.uniform(0.7, 1.3)
            log.warning("snapshot %s Versuch %d/%d fehlgeschlagen (%s) — neuer "
                        "Versuch in %.0fs", snap_id, i, attempts,
                        str(exc).splitlines()[0][:100], delay)
            try:
                await db.listing_snapshots.update_one(
                    {"id": snap_id},
                    {"$set": {"status": "retrying", "attempts": i,
                              "last_error": str(exc)[:300]}})
            except Exception:
                pass
            await asyncio.sleep(delay)
    raise last_exc


async def _render_rebuild_html(html: str) -> tuple[bytes, bytes]:
    """Lokal erzeugtes HTML (Mobile Rebuild) im Playwright-Worker rendern.
    Kein Domain-Guard noetig: die Seite kommt per stdin/set_content, alle
    Bilder sind data-URIs — es findet kein Netzwerkzugriff statt."""
    if not _BROWSERLESS_URL:
        _ensure_browser_executable()
    async with _browser_lock:
        worker = Path(__file__).parent / "_playwright_worker.py"
        loop = asyncio.get_running_loop()

        stdout, stderr, _rc = await loop.run_in_executor(
            None, _run_worker, [sys.executable, str(worker), "--html-stdin"], html)
        try:
            data = json.loads(stdout)
        except Exception:
            raise RuntimeError(f"Rebuild-Worker ungültige Ausgabe: {stderr[:300]}")
        if "error" in data:
            raise RuntimeError(f"Rebuild-Worker Fehler: {data['error']}")
        return base64.b64decode(data["png"]), base64.b64decode(data["pdf"])


async def _mobile_datenblatt_job(db, snap_id: str, doc: dict,
                                 quelle: str = "mobile") -> None:
    """Mobile-Rebuild-Variante des Snapshots (mobile.de + AutoScout24).

    Datenquelle (in dieser Reihenfolge): listings_cache (1 Jahr TTL) ->
    vehicle_cache (nur mobile) -> gespeichertes Fahrzeug. Die
    Original-Fotos werden vom Bilder-CDN geladen und mit eingebettet."""
    from datenblatt_service import datenblatt_bild, datenblatt_pdf, fotos_laden
    url = doc["source_url"]
    ad_id = doc.get("mobile_ad_id") or ""
    quelle_label = {"mobile": "mobile.de",
                    "autoscout24": "autoscout24.de"}.get(quelle, quelle)
    try:
        daten = None
        abgerufen = None
        ce = await db.listings_cache.find_one(
            {"cache_key": f"{quelle}:{ad_id}"},
            {"_id": 0, "data": 1, "fetched_at": 1})
        if ce and ce.get("data"):
            daten, abgerufen = ce["data"], ce.get("fetched_at")
        if not daten and quelle == "mobile":
            vc = await db.vehicle_cache.find_one(
                {"mobile_ad_id": ad_id}, {"_id": 0, "data": 1, "updated_at": 1})
            if vc and vc.get("data"):
                daten, abgerufen = vc["data"], vc.get("updated_at")
        if not daten and doc.get("vehicle_id"):
            v = await db.vehicles.find_one(
                {"id": doc["vehicle_id"]}, {"_id": 0, "data": 1, "updated_at": 1})
            if v and v.get("data"):
                daten, abgerufen = v["data"], v.get("updated_at")
        if not daten:
            raise RuntimeError("Keine ausgelesenen Inserats-Daten vorhanden — "
                               "Datenblatt nicht moeglich.")

        foto_urls = daten.get("images") or daten.get("image_urls") or []
        fotos = await fotos_laden(foto_urls, max_n=9)

        loop = asyncio.get_running_loop()
        # 1. Wahl: dunkle Inserats-Ansicht ("Mobile Rebuild", Wunsch 08/2026)
        # im Playwright-Worker rendern — JPG + PDF entstehen wie bei den
        # Kleinanzeigen-Snapshots aus dem Seiten-Rendering. Faellt das
        # Rendering aus (z.B. Browser kaputt), springt das helle
        # ReportLab-Datenblatt ein — lieber ein schlichter Beweis als keiner.
        from datenblatt_service import rebuild_html
        try:
            html = rebuild_html(daten, url, abgerufen, fotos,
                                quelle_label=quelle_label)
            png, pdf = await _render_rebuild_html(html)
            jpg, pdf = await loop.run_in_executor(
                None, _compress_artifacts, png, pdf)
        except Exception:
            log.exception("Mobile-Rebuild-Rendering fehlgeschlagen — "
                          "Ausweich-Datenblatt (ReportLab) fuer %s", snap_id)
            pdf = await loop.run_in_executor(
                None, lambda: datenblatt_pdf(daten, url, abgerufen, fotos,
                                             quelle_label=quelle_label))
            jpg = await loop.run_in_executor(
                None, lambda: datenblatt_bild(daten, url, abgerufen, fotos,
                                              quelle_label=quelle_label))

        ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        base = f"{APP_NAME}/snapshots/{doc['dealer_id']}/{snap_id}-{ts}"
        png_path = f"{base}.jpg"
        pdf_path = f"{base}.pdf"
        await loop.run_in_executor(None, _put_object, png_path, jpg, "image/jpeg")
        await loop.run_in_executor(None, _put_object, pdf_path, pdf, "application/pdf")
        await db.listing_snapshots.update_one(
            {"id": snap_id},
            {"$set": {
                "status": "ready",
                "art": "datenblatt",
                "png_path": png_path,
                "pdf_path": pdf_path,
                "png_bytes": len(jpg),
                "pdf_bytes": len(pdf),
                "completed_at": datetime.now(timezone.utc).isoformat(),
            }})
        log.info("snapshot %s ready als Datenblatt (%d KB jpg, %d KB pdf, %d Fotos)",
                 snap_id, len(jpg) // 1024, len(pdf) // 1024, len(fotos))
    except Exception as exc:
        log.exception("Datenblatt-Snapshot %s fehlgeschlagen", snap_id)
        await db.listing_snapshots.update_one(
            {"id": snap_id},
            {"$set": {"status": "failed", "error": str(exc)[:500],
                      "completed_at": datetime.now(timezone.utc).isoformat()}})


async def run_snapshot_job(db, snap_id: str) -> None:
    """Background-task entry point. Captures the page (mit Retry bei
    vorübergehenden Fehlern), uploads both artifacts to object storage, and
    flips the row to 'ready'/'failed'."""
    doc = await db.listing_snapshots.find_one({"id": snap_id}, {"_id": 0})
    if not doc:
        log.warning("snapshot %s vanished before capture", snap_id)
        return
    url = doc["source_url"]
    # Als 'running' markieren, damit Recovery laufende von verlorenen Jobs
    # unterscheiden kann.
    await db.listing_snapshots.update_one(
        {"id": snap_id}, {"$set": {"status": "running"}})

    # Snapshot-Aufnahmen sind ECHTE Seitenaufrufe beim Anbieter und zaehlen
    # deshalb gegen dieselbe zentrale Begrenzung wie die Datenabrufe —
    # sonst umginge der Beweis-Snapshot das Limit vollstaendig.
    from listing_identity import detect_source
    from provider_fetch import MOCK_PROVIDER_FETCH
    from provider_limiter import acquire_slot, extend_slot, release_slot
    quelle = detect_source(url) or "kleinanzeigen"
    if MOCK_PROVIDER_FETCH:
        # Im Lasttest keine echten Seitenaufrufe.
        await db.listing_snapshots.update_one(
            {"id": snap_id},
            {"$set": {"status": "failed", "error": "Mock-Modus: kein Abruf",
                      "completed_at": datetime.now(timezone.utc).isoformat()}})
        return
    # mobile.de und AutoScout24 blocken automatisierte Browser — ein
    # Playwright-Foto zeigt dort nur eine Fehlerseite. Fuer beide wird
    # deshalb das Mobile Rebuild aus den bereits ausgelesenen
    # Inserats-Daten erzeugt (klar gekennzeichnet, KEIN Nachbau der
    # Anbieterseite). Kein Provider-Slot noetig: es wird nur das
    # Bilder-CDN angesprochen, nicht die Anbieterseite.
    if quelle in ("mobile", "autoscout24"):
        await _mobile_datenblatt_job(db, snap_id, doc, quelle=quelle)
        return

    slot_id = None
    for _ in range(40):                       # bis zu ~60 s auf einen Slot warten
        slot_id = await acquire_slot(db, quelle)
        if slot_id:
            break
        await asyncio.sleep(1.5)
    if not slot_id:
        await db.listing_snapshots.update_one(
            {"id": snap_id},
            {"$set": {"status": "failed",
                      "error": "Anbieter gerade ausgelastet - bitte spaeter",
                      "completed_at": datetime.now(timezone.utc).isoformat()}})
        return
    async def _slot_frisch_halten():
        # Aufnahmen mit Wiederholungen koennen laenger dauern als die
        # Slot-Frist. Ohne Herzschlag wuerde die Frist den Slot entfernen,
        # waehrend der Seitenaufruf noch laeuft — der Zaehler bliebe zu
        # hoch und die Kapazitaet dauerhaft kleiner.
        while True:
            try:
                await asyncio.sleep(30)
                await extend_slot(db, slot_id)
            except asyncio.CancelledError:
                raise
            except Exception:
                continue

    _puls = asyncio.create_task(_slot_frisch_halten())
    try:
        png, pdf = await _capture_with_retry(db, snap_id, url)
        # Compress PNG → JPEG and rebuild a 1-page image-PDF (much smaller).
        loop = asyncio.get_running_loop()
        png, pdf = await loop.run_in_executor(None, _compress_artifacts, png, pdf)
        ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        base = f"{APP_NAME}/snapshots/{doc['dealer_id']}/{snap_id}-{ts}"
        png_path = f"{base}.jpg"
        pdf_path = f"{base}.pdf"
        await loop.run_in_executor(None, _put_object, png_path, png, "image/jpeg")
        await loop.run_in_executor(None, _put_object, pdf_path, pdf, "application/pdf")
        await db.listing_snapshots.update_one(
            {"id": snap_id},
            {"$set": {
                "status": "ready",
                "png_path": png_path,
                "pdf_path": pdf_path,
                "png_bytes": len(png),
                "pdf_bytes": len(pdf),
                "completed_at": datetime.now(timezone.utc).isoformat(),
            }},
        )
        log.info("snapshot %s ready (%d KB png, %d KB pdf)", snap_id,
                 len(png) // 1024, len(pdf) // 1024)
    except Exception as exc:
        log.exception("snapshot %s failed", snap_id)
        await db.listing_snapshots.update_one(
            {"id": snap_id},
            {"$set": {
                "status": "failed",
                "error": str(exc)[:500],
                "completed_at": datetime.now(timezone.utc).isoformat(),
            }},
        )
    finally:
        _puls.cancel()
        # Quelle mitgeben: hat die Frist das Slot-Dokument bereits entfernt,
        # koennte der Zaehler sonst nicht zurueckgesetzt werden.
        await release_slot(db, slot_id, quelle)
