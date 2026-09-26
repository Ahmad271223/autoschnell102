// Handy-Ansicht (375x812): kein horizontales Scrollen, dunkles Design als Standard.
const { test, expect } = require("@playwright/test");
const h = require("./helpers");

test.use({ viewport: { width: 375, height: 812 }, isMobile: true, hasTouch: true });

const metrics = (page) => page.evaluate(() => {
  const bg = getComputedStyle(document.body).backgroundColor;
  const m = bg.match(/\d+/g) || [];
  const [r, g, b] = m.map(Number);
  return {
    scrollWidth: document.documentElement.scrollWidth,
    innerWidth: window.innerWidth,
    theme: document.documentElement.getAttribute("data-theme"),
    luminance: m.length >= 3 ? (0.2126 * r + 0.7152 * g + 0.0722 * b) : null,
  };
});

test.describe("Mobile Ansicht", () => {
  let firma, sucher;

  test.beforeAll(async () => {
    firma = await h.createFirma();
    sucher = await h.createSucher(firma, { abo: true });
  });

  test.afterAll(async () => {
    await h.cleanup({ firmen: [firma] });
  });

  test("Login-Seite ohne horizontalen Ueberlauf, dunkel", async ({ page }) => {
    await page.goto("/login");
    await expect(page.getByTestId("login-submit")).toBeVisible();
    const m = await metrics(page);
    expect(m.scrollWidth).toBeLessThanOrEqual(m.innerWidth);
    expect(m.theme).toBe("dark");
    expect(m.luminance).toBeLessThan(60);
  });

  test("Vergleich (Sucher) ohne horizontalen Ueberlauf, dunkel", async ({ page }) => {
    await h.authPage(page, "app", sucher.token);
    await page.goto("/app/vergleich");
    await expect(page.getByTestId("nav-vergleich")).toBeVisible();
    await expect(page.getByTestId("app-sidebar")).toBeVisible();
    const m = await metrics(page);
    expect(m.scrollWidth).toBeLessThanOrEqual(m.innerWidth);
    expect(m.theme).toBe("dark");
    expect(m.luminance).toBeLessThan(60);
  });
});
