/*
 * Zwei Tabs, zwei Konten (Befund 05.09.2026).
 *
 * Alle Tabs teilten sich EINEN Token in localStorage. Tab 1 als Super-Admin,
 * Tab 2 als Firma angemeldet — und schon schickte Tab 1 beim naechsten
 * Aufruf den Firmen-Token, waehrend die Oberflaeche noch "Super-Admin"
 * zeigte. Jetzt hat jeder Tab seinen eigenen Token (sessionStorage);
 * localStorage merkt sich nur die letzte Anmeldung fuer NEUE Tabs.
 *
 * Playwright: Seiten desselben Kontexts teilen localStorage, haben aber
 * getrennten sessionStorage — genau wie zwei Tabs eines Browsers.
 */
const { test, expect } = require("@playwright/test");
const h = require("./helpers");

async function formularLogin(page, email, password) {
  await page.goto("/login");
  await page.getByTestId("login-email").fill(email);
  await page.getByTestId("login-password").fill(password);
  await page.getByTestId("login-submit").click();
}

/** Wen haelt DIESER Tab fuer angemeldet — laut Server, mit dem Token des Tabs. */
async function werBinIch(page) {
  return page.evaluate(async () => {
    const t = window.sessionStorage.getItem("ah_token");
    const r = await fetch("/api/auth/me", { headers: { Authorization: `Bearer ${t}` } });
    if (!r.ok) return { status: r.status };
    const d = await r.json();
    return { status: r.status, role: d.user.role, id: d.user.id };
  });
}

test.describe("Zwei Tabs, zwei Konten", () => {
  let firma, sucher;

  test.beforeAll(async () => {
    firma = await h.createFirma();
    sucher = await h.createSucher(firma, { abo: true });
  });

  test.afterAll(async () => {
    await h.cleanup({ firmen: [firma] });
  });

  test("Super-Admin in Tab A bleibt Super-Admin, wenn Tab B eine Firma anmeldet", async ({ context }) => {
    const tabA = await context.newPage();
    await formularLogin(tabA, h.SUPER_ADMIN.username, h.SUPER_ADMIN.password);
    await expect(tabA).toHaveURL(/\/admin\/?$/);

    const tabB = await context.newPage();
    await formularLogin(tabB, firma.email, firma.password);
    await expect(tabB).toHaveURL(/\/app\/bestand/);

    // Genau der Befund: Tab A muss weiterhin mit dem Admin-Token arbeiten.
    const a = await werBinIch(tabA);
    const b = await werBinIch(tabB);
    expect(a.role).toBe("admin");
    expect(b.role).toBe("dealer");
    expect(a.id).not.toBe(b.id);

    // Auch nach Neuladen bleibt Tab A der Admin (und landet nicht bei der Firma).
    await tabA.reload();
    await expect(tabA).toHaveURL(/\/admin\/?$/);
    await expect(tabA.getByText("Angemeldet als")).toBeVisible();
    expect((await werBinIch(tabA)).role).toBe("admin");

    // Ein NEUER Tab macht bei der letzten Anmeldung weiter: der Firma.
    const tabC = await context.newPage();
    await tabC.goto("/app");
    await expect(tabC).toHaveURL(/\/app\/bestand/);
    expect((await werBinIch(tabC)).role).toBe("dealer");
  });

  test("Chef und Sucher derselben Firma gleichzeitig, Abmelden trifft nur den eigenen Tab", async ({ context }) => {
    const chef = await context.newPage();
    await formularLogin(chef, firma.email, firma.password);
    await expect(chef).toHaveURL(/\/app\/bestand/);

    const such = await context.newPage();
    await formularLogin(such, sucher.email, sucher.password);
    await expect(such).toHaveURL(/\/app\/vergleich/);

    expect((await werBinIch(chef)).role).toBe("dealer");
    expect((await werBinIch(such)).role).toBe("sucher");

    // Sucher meldet sich ab — der Chef-Tab darf davon nichts merken.
    await such.evaluate(async () => {
      const t = window.sessionStorage.getItem("ah_token");
      await fetch("/api/auth/logout", { method: "POST", headers: { Authorization: `Bearer ${t}` } });
      window.sessionStorage.removeItem("ah_token");
    });
    expect((await werBinIch(chef)).role).toBe("dealer");
    await chef.reload();
    await expect(chef).toHaveURL(/\/app\/bestand/);
  });
});
