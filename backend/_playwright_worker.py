"""Standalone Playwright-Capture-Script.

Wird von snapshot_service.py als separater Subprocess aufgerufen, damit
Playwright sein eigenes asyncio-Event-Loop (ProactorEventLoop auf Windows)
bekommt — unabhängig vom uvicorn-Worker-Loop.

Aufruf:  python _playwright_worker.py <url>
Ausgabe: JSON auf stdout: {"png": "<base64>", "pdf": "<base64>"}
Fehler:  JSON auf stdout: {"error": "<message>"}
"""
import asyncio
import base64
import json
import sys


_PAGE_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept-Language": "de-DE,de;q=0.9",
}


async def capture(url: str):
    from playwright.async_api import async_playwright

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
            try:
                await page.goto(url, wait_until="domcontentloaded", timeout=30000)
            except Exception:
                await page.goto(url, wait_until="commit", timeout=20000)
            await page.wait_for_timeout(1500)

            # Cookie/Consent-Banner akzeptieren
            async def _accept_in_iframe() -> bool:
                for fr in page.frames:
                    if "sp_message" not in (fr.name or "") and "consent" not in (fr.url or "").lower():
                        continue
                    for sel in (
                        'button[title*="Akzeptieren"]',
                        'button:has-text("Alle akzeptieren")',
                        'button:has-text("Akzeptieren")',
                        'button:has-text("Zustimmen")',
                    ):
                        try:
                            btn = fr.locator(sel).first
                            if await btn.count() > 0:
                                await btn.click(timeout=3000)
                                return True
                        except Exception:
                            continue
                return False

            consent_dismissed = False
            for _ in range(3):
                await page.wait_for_timeout(800)
                if await _accept_in_iframe():
                    consent_dismissed = True
                    break
                for sel in (
                    '[data-testid="gdpr-banner-accept"]',
                    'button:has-text("Alle akzeptieren")',
                    'button:has-text("Akzeptieren")',
                    'button:has-text("Zustimmen")',
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
                await page.wait_for_timeout(1200)

            # Overlays per CSS ausblenden
            try:
                await page.add_style_tag(content="""
                    .login-overlay, [id*="sp_message_container"],
                    #onetrust-banner-sdk, [class*="cookie-banner"],
                    [class*="consent-banner"] {
                        display: none !important;
                    }
                """)
                await page.wait_for_timeout(300)
            except Exception:
                pass

            # Lazy-Images laden durch Scrollen
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
                                  margin={"top": "0", "right": "0",
                                          "bottom": "0", "left": "0"})
            return png, pdf
        finally:
            await browser.close()


def main():
    if len(sys.argv) < 2:
        print(json.dumps({"error": "Keine URL übergeben"}))
        sys.exit(1)

    url = sys.argv[1]
    try:
        png, pdf = asyncio.run(capture(url))
        result = {
            "png": base64.b64encode(png).decode(),
            "pdf": base64.b64encode(pdf).decode(),
        }
        print(json.dumps(result))
    except Exception as exc:
        print(json.dumps({"error": str(exc)}))
        sys.exit(1)


if __name__ == "__main__":
    main()
