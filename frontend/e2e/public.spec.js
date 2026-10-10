// Oeffentliche Rechtsseiten rendern ohne Anmeldung.
const { test, expect } = require("@playwright/test");

const SEITEN = [
  ["/datenschutz", "Datenschutzerklärung"],
  ["/agb", "Allgemeine Geschäftsbedingungen"],
  ["/impressum", "Impressum"],
];

for (const [pfad, titel] of SEITEN) {
  test(`${pfad} zeigt Ueberschrift "${titel}"`, async ({ page }) => {
    await page.goto(pfad);
    await expect(page.locator("h1")).toHaveCount(1);
    await expect(page.locator("h1")).toContainText(titel);
    await expect(page.locator("h2").first()).toBeVisible();
    await expect(page.locator('a[href="/impressum"]').first()).toBeVisible();
  });
}
