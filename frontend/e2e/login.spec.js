// Anmeldung per Formular fuer alle Rollen + Fehlermeldung bei falschem Passwort.
const { test, expect } = require("@playwright/test");
const h = require("./helpers");

test.describe("Anmeldung per Formular", () => {
  let firma, sucher, driver, buyer;

  test.beforeAll(async () => {
    firma = await h.createFirma();
    sucher = await h.createSucher(firma, { abo: true });
    driver = await h.createDriver();
    buyer = await h.createBuyer();
  });

  test.afterAll(async () => {
    await h.cleanup({ firmen: [firma], drivers: [driver], buyers: [buyer] });
  });

  async function appLogin(page, email, password) {
    await page.goto("/login");
    await page.getByTestId("login-email").fill(email);
    await page.getByTestId("login-password").fill(password);
    await page.getByTestId("login-submit").click();
  }

  test("Haendler-Chef landet im Bestand", async ({ page }) => {
    await appLogin(page, firma.email, firma.password);
    await expect(page).toHaveURL(/\/app\/bestand/);
    await expect(page.getByTestId("app-sidebar")).toBeVisible();
    await expect(page.getByTestId("nav-bestand")).toBeVisible();
  });

  test("Sucher landet im Vergleich", async ({ page }) => {
    await appLogin(page, sucher.email, sucher.password);
    await expect(page).toHaveURL(/\/app\/vergleich/);
    await expect(page.getByTestId("nav-vergleich")).toBeVisible();
  });

  test("Super-Admin (Benutzername) landet im Admin-Bereich", async ({ page }) => {
    await appLogin(page, h.SUPER_ADMIN.username, h.SUPER_ADMIN.password);
    await expect(page).toHaveURL(/\/admin\/?$/);
    await expect(page.getByText("Angemeldet als")).toBeVisible();
  });

  test("Zwischenhaendler landet auf dem Marktplatz", async ({ page }) => {
    await page.goto("/markt/login");
    await page.locator('input[type="email"]').fill(buyer.email);
    await page.locator('input[type="password"]').fill(buyer.password);
    await page.locator('button[type="submit"]').click();
    await expect(page).toHaveURL(/\/markt\/?$/);
    await expect(page.getByTestId("markt-page")).toBeVisible();
  });

  test("Fahrer landet in der Fahrer-App", async ({ page }) => {
    await page.goto("/fahrer/login");
    await page.getByTestId("driver-login-email").fill(driver.email);
    await page.getByTestId("driver-login-password").fill(driver.password);
    await page.getByTestId("driver-login-submit").click();
    await expect(page).toHaveURL(/\/fahrer\/?$/);
    await expect(page.getByTestId("driver-dashboard")).toBeVisible();
    await expect(page.getByTestId("driver-header-name")).toHaveText(driver.displayName);
  });

  test("Falsches Passwort zeigt eine Fehlermeldung", async ({ page }) => {
    await appLogin(page, firma.email, "falsches-Passwort-1!");
    await expect(page.getByText("E-Mail/Benutzername oder Passwort falsch")).toBeVisible();
    await expect(page).toHaveURL(/\/login/);
    await expect(page.getByTestId("login-submit")).toBeEnabled();
  });
});
