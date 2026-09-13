// Super-Admin: Firma suchen (Name / #Kundennummer), Sucher-Abo freischalten,
// Doppelklick-Schutz beim Freischalten.
const { test, expect } = require("@playwright/test");
const h = require("./helpers");

test.describe("Super-Admin: Firma & Sucher-Freischaltung", () => {
  let firma, sucher, kundenNr;

  test.beforeAll(async () => {
    firma = await h.createFirma();
    sucher = await h.createSucher(firma);
    const users = await h.superGet("/admin/users");
    kundenNr = users.find((u) => u.id === firma.userId)?.kunden_nr;
    expect(kundenNr).toBeTruthy();
  });

  test.afterAll(async () => {
    await h.cleanup({ firmen: [firma] });
  });

  test("Suche nach Name und #Kundennummer, dann 150 €/M freischalten", async ({ page }) => {
    await h.authPage(page, "app", await h.superAdmin());
    await page.goto("/admin/users");
    const search = page.getByTestId("admin-users-search");
    const row = page.getByTestId(`user-row-${firma.userId}`);

    // Firmenname trifft den Chef UND seinen Sucher (gleiche Firma) — sonst niemanden.
    await search.fill(firma.companyName);
    await expect(row).toBeVisible();
    await expect(page.getByTestId(`user-row-${sucher.userId}`)).toBeVisible();
    await expect(page.locator('[data-testid^="user-row-"]')).toHaveCount(2);
    await expect(row).toContainText(`#${kundenNr}`);

    await search.fill(`#${kundenNr}`);
    await expect(row).toBeVisible();

    await row.locator(`a[href="/admin/users/${firma.userId}"]`).first().click();
    await expect(page).toHaveURL(new RegExp(`/admin/users/${firma.userId}$`));
    await expect(page.getByText("Chef & Sucher — Freischaltung")).toBeVisible();

    const sucherRow = page.locator("tr", { hasText: sucher.email });
    await expect(sucherRow.getByText("Sucher-Funktion: nein")).toBeVisible();
    await sucherRow.getByTestId(`abo-monat-${sucher.userId}`).click();
    await expect(sucherRow.getByText("Sucher-Funktion: ja")).toBeVisible();

    // Gegenprobe ueber die API
    const liste = await h.superGet(`/admin/dealers/${firma.dealerId}/sucher`);
    expect(liste.find((s) => s.id === sucher.userId)?.subscription?.active).toBe(true);
  });

  test("Doppelklick-Schutz: drei Klicks, genau EIN Abo-Request", async ({ page }) => {
    const firma2 = await h.createFirma();
    const sucher2 = await h.createSucher(firma2);
    try {
      await h.authPage(page, "app", await h.superAdmin());
      await page.goto(`/admin/users/${firma2.userId}`);
      const btn = page.getByTestId(`abo-monat-${sucher2.userId}`);
      await expect(btn).toBeEnabled();

      const aboRequests = [];
      page.on("request", (r) => {
        if (r.method() === "POST" && r.url().includes(`/api/admin/sucher/${sucher2.userId}/abo`)) {
          aboRequests.push(r.url());
        }
      });
      // Drei Klicks im selben Tick — schneller als jeder Mensch.
      await btn.evaluate((el) => { el.click(); el.click(); el.click(); });

      const sucherRow = page.locator("tr", { hasText: sucher2.email });
      await expect(sucherRow.getByText("Sucher-Funktion: ja")).toBeVisible();
      await page.waitForTimeout(1000);
      expect(aboRequests).toHaveLength(1);

      // Auch serverseitig nur eine Zahlung erfasst.
      const zahlungen = await h.superGet(`/admin/dealers/${firma2.dealerId}/zahlungen`);
      expect(zahlungen.filter((z) => z.subject_user_id === sucher2.userId)).toHaveLength(1);
    } finally {
      await h.cleanup({ firmen: [firma2] });
    }
  });
});
