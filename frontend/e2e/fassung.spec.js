// Neue Fassung: Hinweis und stilles Nachladen an sicheren Stellen.
//
// Hintergrund (Vorfall 12.09.2026): Fahrer-App und Super-Admin standen nach
// einem Rollout auf "Die Seite konnte nicht geladen werden". Die Oberflaeche
// wusste nichts von der neuen Fassung und lief gegen eine fehlende Datei.
// Seit Runde 31 traegt jede API-Antwort X-AH-Fassung; ist sie neuer als der
// eigene Stempel, zeigt die Oberflaeche einen Hinweis und laedt die neue
// Fassung direkt nach der Anmeldung bzw. beim naechsten Seitenwechsel.
//
// Der E2E-Build braucht dafuer einen ALTEN Stempel:
//   APP_FASSUNG=1700000000-aaaaaaa REACT_APP_BACKEND_URL= yarn build
// Den neueren Stempel des Servers spielt der Test ueber page.route ein —
// das Backend muss dafuer nicht anders gestartet werden.
const { test, expect } = require("@playwright/test");
const h = require("./helpers");

const HANDY = { width: 375, height: 812 };
const ALT = "1700000000-aaaaaaa";
const NEU = "1800000000-bbbbbbb";
const NOCH_NEUER = "1900000000-ccccccc";

async function bauHatAltenStempel(page) {
  const html = await (await page.request.get("/")).text();
  const main = (html.match(/\/static\/js\/main[.][A-Za-z0-9_-]+[.]js/) || [])[0];
  if (!main) return false;
  return (await (await page.request.get(main)).text()).includes(ALT);
}

/** Spielt in jede API-Antwort den Stempel aus `stand.wert` ein (leer = keiner). */
async function fassungEinspielen(page, stand) {
  await page.route("**/api/**", async (route) => {
    const antwort = await route.fetch();
    if (!stand.wert) {
      await route.fulfill({ response: antwort });
      return;
    }
    await route.fulfill({ response: antwort, headers: { ...antwort.headers(), "x-ah-fassung": stand.wert } });
  });
}

function ladevorgaengeZaehlen(page) {
  const zaehler = { n: 0 };
  page.on("load", () => { zaehler.n += 1; });
  return zaehler;
}

async function anmelden(page, driver) {
  await page.goto("/fahrer/login");
  await page.getByTestId("driver-login-email").fill(driver.email);
  await page.getByTestId("driver-login-password").fill(driver.password);
  await page.getByTestId("driver-login-submit").click();
  await expect(page).toHaveURL(/\/fahrer\/?$/);
}

/** Welcher Mechanismus hat neu geladen? Versionswechsel oder fehlendes Seitenteil. */
async function merker(page) {
  return page.evaluate(() => ({
    fassung: sessionStorage.getItem("ah_fassung_neu_geladen"),
    seitenteil: sessionStorage.getItem("ah_seite_neu_geladen_um"),
  }));
}

test.describe("Neue Fassung (Runde 31)", () => {
  let driver;

  test.beforeAll(async () => {
    driver = await h.createDriver();
  });

  test.afterAll(async () => {
    await h.cleanup({ firmen: [], drivers: [driver] });
  });

  test("direkt nach der Anmeldung wird die neue Fassung vollstaendig geladen", async ({ page }) => {
    test.skip(!(await bauHatAltenStempel(page)), `E2E-Build ohne Stempel ${ALT}`);
    await page.setViewportSize(HANDY);
    const ladungen = ladevorgaengeZaehlen(page);
    await fassungEinspielen(page, { wert: NEU });

    await page.goto("/fahrer/login");
    await expect(page.getByTestId("driver-login-email")).toBeVisible();
    const vorher = ladungen.n;
    await page.getByTestId("driver-login-email").fill(driver.email);
    await page.getByTestId("driver-login-password").fill(driver.password);
    await page.getByTestId("driver-login-submit").click();

    await expect(page).toHaveURL(/\/fahrer\/?$/);
    // Genau ein vollstaendiges Laden statt des Wechsels im Speicher ...
    await expect.poll(() => ladungen.n).toBe(vorher + 1);
    expect(await merker(page)).toEqual({ fassung: NEU, seitenteil: null });
    // ... und die Anmeldung hat es ueberlebt.
    await expect(page.getByTestId("driver-header-name")).toBeVisible();
  });

  test("Hinweis liegt frei, Seitenwechsel laden neu — je Fassung genau einmal", async ({ page }) => {
    test.skip(!(await bauHatAltenStempel(page)), `E2E-Build ohne Stempel ${ALT}`);
    await page.setViewportSize(HANDY);
    const ladungen = ladevorgaengeZaehlen(page);
    const stand = { wert: NEU };
    await fassungEinspielen(page, stand);
    await anmelden(page, driver);

    // Nach der Anmeldung ist genau einmal neu geladen worden — durch den
    // Versionswechsel, nicht durch ein fehlendes Seitenteil.
    await expect.poll(() => ladungen.n).toBe(2);
    expect(await merker(page)).toEqual({ fassung: NEU, seitenteil: null });

    // Der Server meldet weiter die neuere Fassung: Hinweis sichtbar ...
    const hinweis = page.getByTestId("fassungs-hinweis");
    await expect(hinweis).toBeVisible();
    await expect(hinweis).toContainText("Neue Version verfügbar");
    // ... und er verdeckt weder die Kopfzeile (Name, Abmelden) noch die Tableiste.
    const b = await hinweis.boundingBox();
    const kopf = await page.locator("header").first().boundingBox();
    const tabs = await page.getByTestId("fahrer-tableiste").boundingBox();
    expect(b.y, "Hinweis ragt in die Kopfzeile").toBeGreaterThanOrEqual(kopf.y + kopf.height - 1);
    expect(b.y + b.height, "Hinweis liegt auf der Tableiste").toBeLessThanOrEqual(tabs.y);
    expect(b.x).toBeGreaterThanOrEqual(0);
    expect(b.x + b.width).toBeLessThanOrEqual(HANDY.width);

    // Keine Schleife: fuer DIESELBE Fassung laden weitere Seitenwechsel nicht
    // noch einmal (z.B. weil das Neuladen im Rollout beim alten Server landete).
    await page.getByTestId("tab-einstellungen").click();
    await expect(page).toHaveURL(/\/fahrer\/einstellungen$/);
    await page.getByTestId("tab-termine").click();
    await expect(page).toHaveURL(/\/fahrer\/?$/);
    await page.waitForTimeout(1500);
    expect(ladungen.n, "fuer dieselbe Fassung nur einmal neu laden").toBe(2);

    // Eine NOCH neuere Fassung: der naechste Seitenwechsel laedt wieder neu.
    stand.wert = NOCH_NEUER;
    await page.getByTestId("tab-einstellungen").click();
    await page.getByTestId("tab-termine").click();
    await page.getByTestId("tab-einstellungen").click();
    await expect.poll(() => ladungen.n).toBe(3);
    await expect(page).toHaveURL(/\/fahrer\/(einstellungen)?$/);
    expect((await merker(page)).fassung).toBe(NOCH_NEUER);
    await page.waitForTimeout(1500);
    expect(ladungen.n, "auch die neuere Fassung nur einmal").toBe(3);

    // "Später" blendet den Hinweis aus.
    await expect(hinweis).toBeVisible();
    await page.getByRole("button", { name: "Später" }).click();
    await expect(hinweis).toBeHidden();
  });

  test("ohne neuere Fassung bleibt alles still", async ({ page }) => {
    await page.setViewportSize(HANDY);
    const ladungen = ladevorgaengeZaehlen(page);
    await anmelden(page, driver);
    await page.getByTestId("tab-einstellungen").click();
    await expect(page).toHaveURL(/\/fahrer\/einstellungen$/);
    await page.getByTestId("tab-termine").click();
    await expect(page).toHaveURL(/\/fahrer\/?$/);
    await page.waitForTimeout(1000);
    await expect(page.getByTestId("fassungs-hinweis")).toHaveCount(0);
    // Nur das erste Oeffnen der Seite — Anmeldung und Tabs wechseln im Speicher.
    expect(ladungen.n, "ohne neue Fassung kein zusaetzliches Laden").toBe(1);
    expect(await merker(page)).toEqual({ fassung: null, seitenteil: null });
  });
});
