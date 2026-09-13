// Sucher fragt eine Abo-Verlaengerung an, der Super-Admin schaltet sie frei.
const { test, expect } = require("@playwright/test");
const h = require("./helpers");

test.describe("Sucher-Abo: Anfrage und Freischaltung", () => {
  let firma, sucher;

  test.beforeAll(async () => {
    firma = await h.createFirma();
    sucher = await h.createSucher(firma);            // ohne Abo
  });

  test.afterAll(async () => {
    await h.cleanup({ firmen: [firma] });
  });

  test("Verlaengerung anfragen (Monat) -> Ja, freischalten", async ({ page, browser }) => {
    await h.authPage(page, "app", sucher.token);
    await page.goto("/app/einstellungen");
    await page.getByTestId("settings-tab-abo").click();
    const renew = page.getByTestId("abo-renew-monthly");
    await expect(renew).toBeEnabled();
    await renew.click();
    await expect(page.getByTestId("abo-anfrage-offen")).toBeVisible();
    await expect(page.getByTestId("abo-anfrage-offen")).toContainText("Monat");
    await expect(renew).toBeDisabled();

    const offen = await h.superGet("/admin/plan-requests?status=offen&type=sucher_abo");
    const req = offen.find((r) => r.subject_user_id === sucher.userId);
    expect(req).toBeTruthy();

    const admin = await h.newAuthedPage(browser, "app", await h.superAdmin());
    try {
      await admin.page.goto("/admin/freischaltungen");
      const ja = admin.page.getByTestId(`abo-ja-${req.id}`);
      await expect(ja).toBeVisible();
      await expect(admin.page.getByText(firma.companyName).first()).toBeVisible();
      await ja.click();
      await expect(ja).toHaveCount(0);
    } finally {
      await admin.context.close();
    }

    // Gegenprobe: Abo aktiv, Anfrage geschlossen, Sucher sieht kein Banner mehr.
    const liste = await h.superGet(`/admin/dealers/${firma.dealerId}/sucher`);
    expect(liste.find((s) => s.id === sucher.userId)?.subscription?.active).toBe(true);
    const rest = await h.superGet("/admin/plan-requests?status=offen&type=sucher_abo");
    expect(rest.find((r) => r.id === req.id)).toBeUndefined();

    await page.getByTestId("abo-status-aktualisieren").click();
    await expect(page.getByTestId("abo-anfrage-offen")).toHaveCount(0);
    await expect(page.getByTestId("abo-status-badge")).toContainText(/aktiv/i);
  });
});
