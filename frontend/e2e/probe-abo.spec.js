// Probe-Abo ueber die Firmenansicht des Betreibers (Pruefbericht 20.09.2026,
// T-25; Wunsch Ahmad 20.09.2026): kostenlos, 3 bzw. 5 Tage, danach sperrt die
// Sucher-Funktion automatisch. Der Sucher kommt sofort in den Vergleich.
const { test, expect } = require("@playwright/test");
const h = require("./helpers");

test.describe("Probe-Abo (UserDetail)", () => {
  let firma, sucher;

  test.beforeAll(async () => {
    firma = await h.createFirma();
    sucher = await h.createSucher(firma);              // ohne Abo
  });

  test.afterAll(async () => {
    await h.cleanup({ firmen: [firma] });
  });

  test("Probe 3 Tage vergeben -> Zeile zeigt Probe, Sucher darf vergleichen", async ({ page, browser }) => {
    // Vorher: ohne Abo landet der Sucher auf /abo
    const vorher = await h.newAuthedPage(browser, "app", sucher.token);
    try {
      await vorher.page.goto("/app/vergleich");
      await expect(vorher.page).toHaveURL(/\/abo(\/|$)/);
    } finally {
      await vorher.context.close();
    }

    await h.authPage(page, "app", await h.superAdmin());
    await page.goto(`/admin/users/${firma.userId}`);
    const zeile = page.locator("tr", { has: page.getByTestId(`sucher-kontonummer-${sucher.userId}`) });
    await expect(zeile).toContainText("Sucher-Funktion: nein");
    await page.getByTestId(`abo-probe3-${sucher.userId}`).click();
    await expect(page.getByText("Abo freigeschaltet (Probe, 3 Tage) — kostenlos, sperrt danach automatisch")).toBeVisible();
    await expect(zeile).toContainText("Sucher-Funktion: ja");
    await expect(zeile).toContainText("Probe · 3 Tage");
    await expect(zeile.getByTestId(`abo-probe3-${sucher.userId}`)).toHaveCount(0);
    await expect(zeile).toContainText("danach automatisch gesperrt");

    // Gegenprobe per API: aktiv, Plan probe3, Ablauf in ~3 Tagen, kein Geld erfasst
    const liste = await h.superGet(`/admin/dealers/${firma.dealerId}/sucher`);
    const s = liste.find((x) => x.id === sucher.userId);
    expect(s?.subscription?.active).toBe(true);
    expect(s.subscription.plan).toBe("probe3");
    expect(s.probe_vergeben_am).toBeTruthy();
    const ablauf = new Date(s.subscription.expires_at || s.naechste_zahlung_am).getTime();
    const tage = (ablauf - Date.now()) / 86_400_000;
    expect(tage).toBeGreaterThan(2.5);
    expect(tage).toBeLessThan(3.5);
    const zahlungen = await h.superGet(`/admin/dealers/${firma.dealerId}/zahlungen`);
    const bezahlt = (Array.isArray(zahlungen) ? zahlungen : zahlungen.items || zahlungen.zahlungen || [])
      .filter((z) => Number(z.amount) > 0);
    expect(bezahlt).toEqual([]);

    // Sucher: jetzt ohne Umleitung in den Vergleich
    const nachher = await h.newAuthedPage(browser, "app", sucher.token);
    try {
      await nachher.page.goto("/app/vergleich");
      await expect(nachher.page).toHaveURL(/\/app\/vergleich/);
      await expect(nachher.page.getByTestId("vergleich-url-input")).toBeVisible();
      await expect(nachher.page.getByTestId("sub-status-badge")).toHaveAttribute("title", /Aktiv/);
    } finally {
      await nachher.context.close();
    }
  });
});
