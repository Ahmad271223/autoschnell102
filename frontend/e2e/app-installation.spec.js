// Installierbare App (09/2026): Manifest und Symbole, der App-Einstieg
// /start, der Anleitungs-Dialog und die Offline-Seite des Service Workers.
const { test, expect } = require("@playwright/test");
const h = require("./helpers");

/** Breite x Hoehe aus dem PNG-Kopf (IHDR). */
function pngGroesse(puffer) {
  return `${puffer.readUInt32BE(16)}x${puffer.readUInt32BE(20)}`;
}

test.describe("Installierbare App", () => {
  test("Manifest: Name, Einstieg, Symbole in der angegebenen Groesse", async ({ request }) => {
    const r = await request.get("/manifest.json");
    expect(r.status()).toBe(200);
    const m = await r.json();
    expect(m.name).toBe("AutoSchnell");
    // Kennung frueherer Installationen (Fahrer-Portal) — so erreichen sie
    // Name, Symbol und Einstieg ohne Neuinstallation (DEPLOYMENT.md).
    expect(m.id).toBe("/driver-login");
    expect(m.start_url).toBe("/start");
    expect(m.display).toBe("standalone");
    const zweck = (i) => (i.purpose || "any").split(" ");
    expect(m.icons.some((i) => i.sizes === "512x512" && zweck(i).includes("any"))).toBeTruthy();
    expect(m.icons.some((i) => zweck(i).includes("maskable"))).toBeTruthy();
    const symbole = [...m.icons, ...(m.shortcuts || []).flatMap((s) => s.icons || [])];
    for (const s of symbole) {
      const bild = await request.get(s.src);
      expect(bild.status(), s.src).toBe(200);
      expect(bild.headers()["content-type"], s.src).toContain("image/png");
      expect(pngGroesse(await bild.body()), s.src).toBe(s.sizes);
    }
  });

  test("/start auf einem neuen Geraet: Auswahl Firma / Fahrer / Marktplatz", async ({ page }) => {
    await page.goto("/start");
    await expect(page.getByTestId("start-auswahl")).toBeVisible();
    await page.getByTestId("start-fahrer").click();
    await expect(page).toHaveURL(/\/fahrer\/login$/);
  });

  test("/start: zuletzt als Fahrer angemeldet -> Fahrer-Login", async ({ page }) => {
    await page.addInitScript(() => window.localStorage.setItem("ah_letzte_anmeldung", "ah_driver_token"));
    await page.goto("/start");
    await expect(page).toHaveURL(/\/fahrer\/login$/);
  });

  test("Fahrer-Login aufgerufen, dann die App gestartet -> wieder Fahrer-Login", async ({ page }) => {
    await page.goto("/fahrer/login");
    await expect(page.getByTestId("driver-login-page")).toBeVisible();
    await page.goto("/start");
    await expect(page).toHaveURL(/\/fahrer\/login$/);
  });

  test("alter Einstieg des Fahrer-Portals (/driver-login) fuehrt zu /start", async ({ page }) => {
    await page.goto("/driver-login");
    await expect(page).toHaveURL(/\/start$/);
    await expect(page.getByTestId("start-auswahl")).toBeVisible();
  });

  test("Knopf auf der Anmeldung oeffnet die Anleitung", async ({ page }) => {
    // Playwright-Chromium ohne Service Worker meldet sich nicht als
    // installierbar -> Weg "Anleitung" (wie Chrome vor der ersten Nutzung).
    await page.goto("/login");
    await page.getByTestId("pwa-install-btn").click();
    await expect(page.getByTestId("pwa-anleitung")).toBeVisible();
    await expect(page.getByTestId("pwa-anleitung")).toContainText("Als App installieren");
    // Der Knopf steht im Anmelde-Formular: er darf es nicht absenden.
    await expect(page).toHaveURL(/\/login$/);
  });

  test.describe("angemeldet", () => {
    let firma;
    let sucher;

    test.beforeAll(async () => {
      firma = await h.createFirma();
      sucher = await h.createSucher(firma, { abo: true });
    });

    test.afterAll(async () => {
      await h.cleanup({ firmen: [firma] });
    });

    test("/start bringt den Sucher direkt zum Vergleich", async ({ page }) => {
      await h.authPage(page, "app", sucher.token);
      await page.goto("/start");
      await expect(page).toHaveURL(/\/app\/vergleich$/);
    });

    test("/start bringt den Chef direkt zum Bestand", async ({ page }) => {
      await h.authPage(page, "app", firma.token);
      await page.goto("/start");
      await expect(page).toHaveURL(/\/app\/bestand$/);
    });

    test("Server nicht erreichbar: 'Keine Verbindung' statt Abmeldung, danach geht es weiter", async ({ page }) => {
      // Runde 22: Funkloch/502 beim Laden meldete frueher ab (Token geloescht).
      await h.authPage(page, "app", sucher.token);
      await page.route("**/api/auth/me", (r) => r.abort("internetdisconnected"));
      await page.goto("/start");
      await expect(page.getByTestId("verbindungsfehler")).toBeVisible();
      await page.goto("/app/vergleich");
      await expect(page.getByTestId("verbindungsfehler")).toBeVisible();
      expect(await page.evaluate(() => window.localStorage.getItem("ah_token"))).toBe(sucher.token);
      await page.unroute("**/api/auth/me");
      await page.getByTestId("verbindungsfehler-erneut").click();
      await expect(page.getByTestId("vergleich-page")).toBeVisible();
    });

    test("Sitzung inzwischen beendet: /start zeigt die Anmeldung MIT Grund", async ({ page }) => {
      const alt = sucher.token;
      // Neue Anmeldung desselben Kontos beendet die alte Sitzung (Single-Session).
      sucher.token = await h.login(sucher.email, sucher.password);
      await h.authPage(page, "app", alt);
      await page.goto("/start");
      await expect(page).toHaveURL(/\/login\?reason=session$/);
      await expect(page.getByTestId("login-abmeldegrund")).toBeVisible();
    });
  });

  test.describe("Service Worker", () => {
    // Die uebrige Suite sperrt Service Worker (playwright.config.js).
    test.use({ serviceWorkers: "allow" });

    test("ohne Netz: klare Meldung statt Fehlerseite des Browsers", async ({ page, context }) => {
      await page.goto("/login");
      await page.evaluate(() => navigator.serviceWorker.ready.then(() => true));
      await expect.poll(() => page.evaluate(() => !!navigator.serviceWorker.controller)).toBe(true);

      await context.setOffline(true);
      try {
        await page.goto("/app/termine").catch(() => {});
        await expect(page.getByRole("heading", { name: "Keine Internetverbindung" })).toBeVisible();
        await expect(page.getByRole("link", { name: "Erneut versuchen" })).toBeVisible();
      } finally {
        await context.setOffline(false);
      }
      // Netz zurueck: die Seite laedt von selbst neu (oder per Knopf) — ohne
      // Anmeldung landet /app/termine bei der Anmeldung. Der Klick darf nicht
      // haengen, falls das Neuladen ihm zuvorkommt.
      const knopf = page.getByRole("link", { name: "Erneut versuchen" });
      if (await knopf.isVisible().catch(() => false)) await knopf.click({ timeout: 3000 }).catch(() => {});
      await expect(page.getByTestId("login-email")).toBeVisible();
    });
  });
});
