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


def _put_object(path: str, data: bytes, content_type: str) -> dict:
    if _USE_LOCAL:
        dest = _LOCAL_STORAGE / path
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
        dest = _LOCAL_STORAGE / path
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


def _compress_artifacts(png_bytes: bytes, pdf_fallback: bytes) -> tuple[bytes, bytes]:
    """Re-encode the raw PNG screenshot as a JPEG (much smaller) and build
    a 1-page image-PDF from the same JPEG. Falls back to the original
    Playwright PDF if Pillow rejects the screenshot for any reason."""
    try:
        from PIL import Image  # local import — Pillow is heavy
        import io
        with Image.open(io.BytesIO(png_bytes)) as im:
            im = im.convert("RGB")
            if im.width > MAX_WIDTH:
                ratio = MAX_WIDTH / im.width
                new_size = (MAX_WIDTH, int(im.height * ratio))
                im = im.resize(new_size, Image.LANCZOS)
            jpg_buf = io.BytesIO()
            im.save(jpg_buf, format="JPEG", quality=JPEG_QUALITY,
                    optimize=True, progressive=True)
            jpg_bytes = jpg_buf.getvalue()
            pdf_buf = io.BytesIO()
            # resolution=72 (statt 100) lässt ReportLab die Seite
            # entsprechend grösser anlegen -> gleiches Bild, weniger
            # Meta-Overhead, und vor allem keine Neu-Kompression des
            # Bildes (PIL bettet JPEG direkt ein).
            im.save(pdf_buf, format="PDF", resolution=72.0)
            pdf_bytes = pdf_buf.getvalue()
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

        def _run():
            # Auf Windows zeigt PLAYWRIGHT_BROWSERS_PATH auf den Linux-Pfad
            # /pw-browsers (aus server.py). Wir entfernen ihn damit Playwright
            # seinen eigenen Windows-Default-Pfad nutzt.
            env = os.environ.copy()
            if sys.platform == "win32":
                env.pop("PLAYWRIGHT_BROWSERS_PATH", None)
            result = subprocess.run(
                [sys.executable, str(worker), url],
                capture_output=True, text=True, timeout=90,
                env=env,
            )
            return result.stdout, result.stderr, result.returncode

        stdout, stderr, rc = await loop.run_in_executor(None, _run)
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
) -> str:
    """Create a `listing_snapshots` row in 'pending' state and return its id.
    Capture/upload runs as a background task via `run_snapshot_job`."""
    snap_id = str(uuid.uuid4())
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


async def run_snapshot_job(db, snap_id: str) -> None:
    """Background-task entry point. Captures the page, uploads both
    artifacts to object storage, and flips the row to 'ready'/'failed'."""
    doc = await db.listing_snapshots.find_one({"id": snap_id}, {"_id": 0})
    if not doc:
        log.warning("snapshot %s vanished before capture", snap_id)
        return
    url = doc["source_url"]
    try:
        png, pdf = await _capture_with_playwright(url)
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
