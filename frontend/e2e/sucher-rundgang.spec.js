// Rundgang als Sucher mit Abo (Pruefbericht 20.09.2026, T-18): jeder
// Menuepunkt oeffnet, zeigt sein Kernelement — ohne Skriptfehler und ohne
// Fehler-Meldung. Bisher kamen Suche, Fahrzeugpool, Vertraege, Termine und
// Fahrer als Sucher in keiner Spec vor.
const { test, expect } = require("@playwright/test");
const h = require("./helpers");

// nav-<testid> -> Kernelement der Seite (und Fehleranzeigen, die NICHT da sein duerfen)
const SEITEN = [
  { nav: "nav-vergleich", pfad: "/app/vergleich", kern: "vergleich-url-input", fehler: [] },
  { nav: "nav-suche", pfad: "/app/suche", kern: "suche-page", fehler: ["manual-makes-fehler"] },
  { nav: "nav-vertraege", pfad: "/app/vertraege", kern: "pdfs-page", fehler: ["pdfs-fehler"] },
  { nav: "nav-termine", pfad: "/app/termine", kern: "termine-page", fehler: ["termine-ladefehler"] },
  { nav: "nav-fahrzeuge", pfad: "/app/fahrzeuge", kern: "vehicles-page", fehler: [] },
  { nav: "nav-fahrer", pfad: "/app/fahrer", kern: "drivers-page", fehler: ["drivers-ladefehler"] },
  { nav: "nav-einstellungen", pfad: "/app/einstellungen", kern: "settings-page", fehler: [] },
];
const NUR_CHEF = ["nav-freigaben", "nav-bestand", "nav-anfragen", "nav-team"];

test.describe("Sucher-Rundgang", () => {
  let firma, sucher;

  test.beforeAll(async () => {
    firma = await h.createFirma();
    sucher = await h.createSucher(firma, { abo: true });
  });

  test.afterAll(async () => {
    await h.cleanup({ firmen: [firma] });
  });

  test("alle Sucher-Menuepunkte oeffnen ohne Skript- und Ladefehler", async ({ page }) => {
    const skriptfehler = [];
    page.on("pageerror", (e) => skriptfehler.push(String(e?.message || e)));
    await h.authPage(page, "app", sucher.token);
    await page.goto("/app/vergleich");
    await expect(page.getByTestId("app-sidebar")).toBeVisible();

    for (const s of SEITEN) {
      const link = page.getByTestId(s.nav);
      await expect(link, `${s.nav} fehlt im Menue`).toBeVisible();
      await link.click();
      await expect(page, s.nav).toHaveURL(new RegExp(s.pfad.replace(/\//g, "\\/")));
      await expect(page.getByTestId(s.kern), `${s.pfad}: Kernelement ${s.kern}`).toBeVisible();
      // Kurz stehen lassen: Nachlade-Anfragen und Fehler-Meldungen kommen asynchron.
      await page.waitForTimeout(700);
      for (const f of s.fehler) {
        await expect(page.getByTestId(f), `${s.pfad}: Fehleranzeige ${f}`).toHaveCount(0);
      }
      const fehlerToasts = page.locator('[data-sonner-toast][data-type="error"]');
      expect(await fehlerToasts.count(), `${s.pfad}: Fehler-Meldung(en): ${await fehlerToasts.allTextContents()}`).toBe(0);
      expect(skriptfehler, `${s.pfad}: Skriptfehler`).toEqual([]);
    }
    for (const nav of NUR_CHEF) {
      await expect(page.getByTestId(nav), `${nav} darf der Sucher nicht sehen`).toHaveCount(0);
    }
    // Abo-Punkt: aktiv (gruener Punkt fuehrt zu den Einstellungen)
    await expect(page.getByTestId("sub-status-badge")).toHaveAttribute("title", /Aktiv/);
  });
});
