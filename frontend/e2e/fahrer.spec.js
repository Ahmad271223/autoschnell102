// Fahrer-App: zugeteilte Fahrt annehmen, Haendler sieht "angenommen".
const { test, expect } = require("@playwright/test");
const h = require("./helpers");

test.describe("Fahrer-App", () => {
  let firma, driver, appt;

  test.beforeAll(async () => {
    firma = await h.createFirma();
    driver = await h.createDriver();
    await h.post("/drivers/add", { driver_code: driver.driverCode }, { token: firma.token });
    appt = await h.createAppointment(firma, {
      title: `E2E Abholung ${firma.s}`, pickup_date: h.isoDate(0), pickup_time: "10:00",
      driver_id: driver.id, status: "offen", seller_name: "Max Verkaeufer",
      seller_phone: "0511 987654", pickup_address: "Musterstrasse 1, 30159 Hannover",
    });
    expect(appt.zuteilung).toBe("offen");
  });

  test.afterAll(async () => {
    await h.cleanup({ firmen: [firma], drivers: [driver] });
  });

  test("Fahrt annehmen -> Abhol-Buttons, Terminplaner zeigt 'angenommen'", async ({ page, browser }) => {
    await page.goto("/fahrer/login");
    await page.getByTestId("driver-login-email").fill(driver.email);
    await page.getByTestId("driver-login-password").fill(driver.password);
    await page.getByTestId("driver-login-submit").click();
    await expect(page).toHaveURL(/\/fahrer\/?$/);

    const card = page.getByTestId(`appt-${appt.id}`);
    await expect(card).toBeVisible();
    await card.locator("button").first().click();                       // aufklappen
    await expect(card.getByText("Neue Fahrt zugeteilt")).toBeVisible();
    await expect(card.getByTestId(`mark-pickedup-${appt.id}`)).toHaveCount(0);

    await card.getByTestId(`zuteilung-annehmen-${appt.id}`).click();
    await expect(card.getByTestId(`mark-pickedup-${appt.id}`)).toBeVisible();
    await expect(card.getByTestId(`mark-notpickedup-${appt.id}`)).toBeVisible();
    await expect(card.getByTestId(`zuteilung-annehmen-${appt.id}`)).toHaveCount(0);

    // Haendler-Sicht: Terminplaner, Listenansicht
    const dealer = await h.newAuthedPage(browser, "app", firma.token);
    try {
      await dealer.page.goto("/app/termine");
      await dealer.page.getByTestId("view-list").click();
      const fahrer = dealer.page.getByTestId(`fahrer-${appt.id}`);
      await expect(fahrer).toBeVisible();
      await expect(fahrer).toContainText(driver.displayName);
      await expect(fahrer).toContainText("angenommen");
      await expect(fahrer).not.toContainText("wartet auf Annahme");
    } finally {
      await dealer.context.close();
    }
  });
});
