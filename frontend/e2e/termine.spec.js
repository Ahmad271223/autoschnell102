// Terminplaner (Liste): kommende Termine oben, abgeschlossene/vergangene unten.
const { test, expect } = require("@playwright/test");
const h = require("./helpers");

test.describe("Terminplaner Listenansicht", () => {
  let firma, heute, morgen, vergangen;

  test.beforeAll(async () => {
    firma = await h.createFirma();
    heute = await h.createAppointment(firma, { title: `E2E Heute ${firma.s}`, pickup_date: h.isoDate(0), pickup_time: "09:00" });
    morgen = await h.createAppointment(firma, { title: `E2E Morgen ${firma.s}`, pickup_date: h.isoDate(1), pickup_time: "11:00" });
    vergangen = await h.createAppointment(firma, {
      title: `E2E Vergangen ${firma.s}`, pickup_date: h.isoDate(-3), pickup_time: "08:00", status: "abgeholt",
    });
  });

  test.afterAll(async () => {
    await h.cleanup({ firmen: [firma] });
  });

  test("Kommende Termine stehen vor 'Abgeschlossen & vergangen'", async ({ page }) => {
    await h.authPage(page, "app", firma.token);
    await page.goto("/app/termine");
    await page.getByTestId("view-list").click();

    const kommend = page.getByTestId("termine-abschnitt-kommend");
    const abgeschlossen = page.getByTestId("termine-abschnitt-vergangen");
    await expect(kommend).toBeVisible();
    await expect(kommend).toContainText("Kommende Termine");
    await expect(kommend).toContainText("2");
    await expect(abgeschlossen).toBeVisible();
    await expect(abgeschlossen).toContainText("Abgeschlossen & vergangen");

    const rowHeute = page.getByTestId(`appt-row-${heute.id}`);
    const rowMorgen = page.getByTestId(`appt-row-${morgen.id}`);
    const rowVergangen = page.getByTestId(`appt-row-${vergangen.id}`);
    for (const l of [rowHeute, rowMorgen, rowVergangen]) await expect(l).toBeVisible();

    const y = async (loc) => (await loc.boundingBox()).y;
    expect(await y(kommend)).toBeLessThan(await y(rowHeute));
    expect(await y(rowHeute)).toBeLessThan(await y(rowMorgen));
    expect(await y(rowMorgen)).toBeLessThan(await y(abgeschlossen));
    expect(await y(abgeschlossen)).toBeLessThan(await y(rowVergangen));

    await expect(page.getByText("Heute", { exact: true }).first()).toBeVisible();
    await expect(page.getByText("Morgen", { exact: true }).first()).toBeVisible();
    await expect(rowVergangen).toContainText("abgeholt");
  });
});
