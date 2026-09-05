// Rollentrennung in der Navigation: Sucher, normaler Admin, Super-Admin.
const { test, expect } = require("@playwright/test");
const h = require("./helpers");

test.describe("Rollen-Navigation", () => {
  let firma, sucher, normalAdmin;

  test.beforeAll(async () => {
    firma = await h.createFirma();
    sucher = await h.createSucher(firma, { abo: true });
    normalAdmin = await h.createNormalAdmin();
  });

  test.afterAll(async () => {
    await h.cleanup({ firmen: [firma], admins: [normalAdmin] });
  });

  test("Sucher sieht keine Haendler-Funktionen", async ({ page }) => {
    await h.authPage(page, "app", sucher.token);
    await page.goto("/app/vergleich");
    await expect(page.getByTestId("nav-vergleich")).toBeVisible();
    await expect(page.getByTestId("nav-einstellungen")).toBeVisible();
    await expect(page.getByTestId("nav-bestand")).toHaveCount(0);
    await expect(page.getByTestId("nav-anfragen")).toHaveCount(0);
    await expect(page.getByTestId("nav-team")).toHaveCount(0);

    await page.goto("/app/einstellungen");
    await expect(page.getByTestId("settings-tab-abo")).toBeVisible();
    await expect(page.getByTestId("settings-tab-markt")).toHaveCount(0);

    // Direkter Aufruf der Kaufanfragen wird zum Vergleich umgeleitet.
    await page.goto("/app/anfragen");
    await expect(page).toHaveURL(/\/app\/vergleich/);
  });

  test("Normaler Admin: keine Betreiber-Funktionen, Nutzerliste nur lesen", async ({ page }) => {
    await h.authPage(page, "app", normalAdmin.token);
    await page.goto("/admin");
    await expect(page.locator('a[href="/admin/users"]').first()).toBeVisible();
    await expect(page.locator('a[href="/admin/freischaltungen"]')).toHaveCount(0);
    await expect(page.locator('a[href="/admin/betrieb"]')).toHaveCount(0);

    await page.goto("/admin/users");
    await expect(page.getByText("nur lesen").first()).toBeVisible();
    await expect(page.getByTestId("admin-create-user-btn")).toBeDisabled();
    await expect(page.locator('[data-testid^="user-pw-btn-"]')).toHaveCount(0);
    await expect(page.locator('[data-testid^="user-toggle-active-btn-"]')).toHaveCount(0);
    await expect(page.locator('[data-testid^="user-delete-btn-"]')).toHaveCount(0);
  });

  test("Super-Admin sieht Freischaltungen und Betrieb", async ({ page }) => {
    await h.authPage(page, "app", await h.superAdmin());
    await page.goto("/admin");
    await expect(page.locator('a[href="/admin/freischaltungen"]').first()).toBeVisible();
    await expect(page.locator('a[href="/admin/betrieb"]').first()).toBeVisible();

    await page.goto("/admin/users");
    await expect(page.getByTestId("admin-create-user-btn")).toBeEnabled();
    await expect(page.getByTestId(`user-pw-btn-${firma.userId}`)).toBeVisible();
  });

  test("Admin landet NIE auf einer Haendler-Seite", async ({ page }) => {
    // Befund 05.09.2026: Der Super-Admin sah "Fahrzeugbestand &
    // Weiterverkauf" mit der Admin-Seitenleiste daneben und der Meldung
    // "Nur fuer Haendler-Accounts". Direkter Aufruf, Startseite und der
    // Weg ueber ?next werden hier alle drei geprueft.
    await h.authPage(page, "app", await h.superAdmin());

    for (const ziel of ["/app/bestand", "/app/vertraege", "/app/team",
                        "/app/termine", "/abo"]) {
      await page.goto(ziel);
      await expect(page, `${ziel} war fuer den Admin erreichbar`)
        .toHaveURL(/\/admin\/?$/);
      await expect(page.getByText("Nur für Händler-Accounts")).toHaveCount(0);
      await expect(page.getByText("Fahrzeugbestand & Weiterverkauf")).toHaveCount(0);
    }

    await page.goto("/app");
    await expect(page).toHaveURL(/\/admin\/?$/);
  });

  test("Haendler landet NIE im Admin-Bereich", async ({ page }) => {
    await h.authPage(page, "app", firma.token);
    for (const ziel of ["/admin", "/admin/users", "/admin/betrieb"]) {
      await page.goto(ziel);
      await expect(page, `${ziel} war fuer den Haendler erreichbar`)
        .toHaveURL(/\/app\/bestand/);
    }
  });

  test("Anmeldung folgt keinem Ziel aus einem fremden Bereich", async ({ page }) => {
    // Genau der Weg aus dem Befund: jemand wird auf einer Haendler-Seite
    // abgemeldet, danach meldet sich der Betreiber auf derselben
    // Anmeldeseite an.
    await page.goto("/login?next=%2Fapp%2Fbestand");
    await page.getByTestId("login-email").fill(h.SUPER_ADMIN.username);
    await page.getByTestId("login-password").fill(h.SUPER_ADMIN.password);
    await page.getByTestId("login-submit").click();
    await expect(page).toHaveURL(/\/admin\/?$/);
    await expect(page.getByText("Fahrzeugbestand & Weiterverkauf")).toHaveCount(0);
  });
});
