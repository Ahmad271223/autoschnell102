// Anmeldung per Formular fuer alle Rollen + Fehlermeldung bei falschem Passwort.
// Kontonummer (13.09.2026): alle Masken melden mit der Kontonummer an (der
// Super-Admin mit seinem Benutzernamen), Einladungslinks ueber /markt/login.
const { test, expect } = require("@playwright/test");
const h = require("./helpers");

test.describe("Anmeldung per Formular", () => {
  let firma, sucher, driver, buyer, einladung;

  test.beforeAll(async () => {
    firma = await h.createFirma();
    sucher = await h.createSucher(firma, { abo: true });
    driver = await h.createDriver();
    buyer = await h.createBuyer();
    // Einladung mit dem API-Token der Firma, BEVOR ein Formular-Login es ersetzt.
    einladung = await h.post("/dealer/invites", { validity_hours: 24, max_uses: 1 }, { token: firma.token });
  });

  test.afterAll(async () => {
    await h.cleanup({ firmen: [firma], drivers: [driver], buyers: [buyer] });
  });

  test("Maske fragt nach der Kontonummer, ohne Hinweis auf den Betreiber-Zugang", async ({ page }) => {
    await page.goto("/login");
    await expect(page.getByTestId("login-kontonummer")).toBeVisible();
    await expect(page.getByText("Kontonummer", { exact: true })).toBeVisible();
    await expect(page.getByText(/Benutzername/)).toHaveCount(0);
  });

  test("Haendler-Chef landet im Bestand", async ({ page }) => {
    await h.formLogin(page, "auth", firma);
    await expect(page).toHaveURL(/\/app\/bestand/);
    await expect(page.getByTestId("app-sidebar")).toBeVisible();
    await expect(page.getByTestId("nav-bestand")).toBeVisible();
  });

  test("Sucher landet im Vergleich", async ({ page }) => {
    expect(sucher.kontonummer).toMatch(/^\d+-\d+$/);
    await h.formLogin(page, "auth", sucher);
    await expect(page).toHaveURL(/\/app\/vergleich/);
    await expect(page.getByTestId("nav-vergleich")).toBeVisible();
  });

  test("Super-Admin (Benutzername) landet im Admin-Bereich", async ({ page }) => {
    await h.formLogin(page, "auth", h.SUPER_ADMIN);
    await expect(page).toHaveURL(/\/admin\/?$/);
    await expect(page.getByText("Angemeldet als")).toBeVisible();
  });

  test("Zwischenhaendler landet auf dem Marktplatz", async ({ page }) => {
    await h.formLogin(page, "buyer", buyer);
    await expect(page).toHaveURL(/\/markt\/?$/);
    await expect(page.getByTestId("markt-page")).toBeVisible();
  });

  test("Zwischenhaendler ueber die normale Anmeldung landet angemeldet im Marktplatz (U-145)", async ({ page }) => {
    // Pruefbericht 20.09.2026: vorher lag das Token unter dem App-Schluessel,
    // /markt kannte niemanden und schickte zur zweiten Anmeldung.
    await h.formLogin(page, "auth", buyer);
    await expect(page).toHaveURL(/\/markt\/?$/);
    await expect(page.getByTestId("markt-page")).toBeVisible();
  });

  test("Einladungslink: Zwischenhaendler meldet sich an und tritt dem Netzwerk bei", async ({ page }) => {
    expect(einladung.link).toBe(`/markt/login?invite=${einladung.token}`);
    await h.formLogin(page, "buyer", buyer, { pfad: einladung.link });
    await expect(page.getByText(`Netzwerk beigetreten: ${firma.companyName}`)).toBeVisible();
    await expect(page).toHaveURL(/\/markt\/?$/);
    // Gegenprobe beim Haendler (frische Anmeldung — das Formular-Login oben hat
    // das API-Token der Firma ersetzt).
    const chefToken = await h.login(firma.kontonummer, firma.password);
    const mitglieder = await h.get("/dealer/network/members", { token: chefToken });
    expect(mitglieder.some((m) => m.buyer_user_id === buyer.id)).toBe(true);
  });

  test("Alter Registrieren-Link fuehrt mit Einladung zur Anmeldung", async ({ page }) => {
    await page.goto("/markt/registrieren?invite=abc123");
    await expect(page).toHaveURL(/\/markt\/login\?invite=abc123$/);
    await expect(page.getByTestId("buyer-login-kontonummer")).toBeVisible();
  });

  test("Fahrer landet in der Fahrer-App", async ({ page }) => {
    await h.formLogin(page, "driver", driver);
    await expect(page).toHaveURL(/\/fahrer\/?$/);
    await expect(page.getByTestId("driver-dashboard")).toBeVisible();
    await expect(page.getByTestId("driver-header-name")).toHaveText(driver.displayName);
  });

  test("Falsches Passwort zeigt eine Fehlermeldung", async ({ page }) => {
    await h.formLogin(page, "auth", firma, { passwort: "falsches-Passwort-1!" });
    // Kontonummer (13.09.2026), Schritt 5: ein neutraler Text fuer alle Masken
    await expect(page.getByText("Kontonummer oder Passwort falsch")).toBeVisible();
    await expect(page).toHaveURL(/\/login/);
    await expect(page.getByTestId("login-submit")).toBeEnabled();
  });
});
