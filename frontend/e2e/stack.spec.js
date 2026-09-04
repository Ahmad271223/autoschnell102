/*
 * Rauchtest gegen den ECHTEN Produktions-Stack (Pruefbericht Runde 7,
 * Befund 6).
 *
 * Die uebrige E2E-Suite laeuft gegen ein direkt gestartetes uvicorn, eine
 * MongoDB ohne Passwort und einen kleinen Test-Server. Das prueft die
 * Anwendung, aber nicht den Weg, den der Kunde spaeter wirklich nimmt:
 *
 *     Browser -> nginx (HTTPS) -> Backend im Container -> MongoDB mit Auth
 *
 * Genau dieser Weg wird hier einmal komplett durchlaufen. Absichtlich klein
 * gehalten: der Test soll beweisen, dass der Produktions-Aufbau traegt —
 * Fachlogik prueft die grosse Suite. Ein kurzer, stabiler Test, der bei
 * jedem Push laeuft, ist mehr wert als ein langer, der flackert.
 *
 * Laeuft nur, wenn E2E_STACK=1 gesetzt ist (siehe playwright.config.js).
 */
const { test, expect } = require("@playwright/test");

const BENUTZER = process.env.E2E_SUPER_ADMIN_USERNAME
  || process.env.SUPER_ADMIN_USERNAME || "";
const PASSWORT = process.env.E2E_SUPER_ADMIN_PASSWORD
  || process.env.SUPER_ADMIN_PASSWORD || "";

test.describe("Produktions-Stack (nginx + Container + MongoDB mit Auth)", () => {
  test("Oberflaeche kommt ueber HTTPS durch den Proxy", async ({ page }) => {
    const antwort = await page.goto("/");
    expect(antwort.status()).toBe(200);
    expect(page.url().startsWith("https://")).toBeTruthy();
    // Die SPA muss wirklich gerendert haben, nicht nur ein leeres Geruest.
    await expect(page.locator("#root")).not.toBeEmpty();
  });

  test("Sicherheits-Kopfzeilen kommen vom echten Proxy", async ({ request }) => {
    const r = await request.get("/");
    const k = r.headers();
    expect(k["strict-transport-security"]).toBeTruthy();
    expect(k["x-content-type-options"]).toBe("nosniff");
    expect(k["x-frame-options"]).toBe("DENY");
    expect(k["referrer-policy"]).toBeTruthy();
    expect(k["content-security-policy"]).toContain("frame-ancestors");
  });

  test("API antwortet unter derselben Herkunft", async ({ request }) => {
    const gesund = await request.get("/api/health");
    expect(gesund.status()).toBe(200);
    expect((await gesund.json()).db).toBe("up");

    // Bereitschaft: hier faellt auf, wenn Migration, Speicherplatz oder
    // Datei-Speicher im Container nicht stimmen.
    const bereit = await request.get("/api/ready");
    const daten = await bereit.json();
    expect(daten.fehler, `nicht bereit: ${JSON.stringify(daten.fehler)}`).toEqual([]);
    expect(bereit.status()).toBe(200);
    expect(daten.ready).toBe(true);
  });

  test("Anmeldung und ein geschuetzter Aufruf gehen durch den ganzen Weg",
    async ({ page }) => {
      test.skip(!BENUTZER || !PASSWORT, "Keine Super-Admin-Zugangsdaten gesetzt");
      await page.goto("/login");
      await page.getByTestId("login-email").fill(BENUTZER);
      await page.getByTestId("login-password").fill(PASSWORT);
      await page.getByTestId("login-submit").click();
      await expect(page).toHaveURL(/\/admin\/?$/);
      await expect(page.getByText("Angemeldet als")).toBeVisible();

      // Ein geschuetzter API-Aufruf MIT dem Token aus dem Browser: beweist,
      // dass der Proxy den Authorization-Kopf durchreicht und die MongoDB
      // mit Passwort erreichbar ist.
      const antwort = await page.evaluate(async () => {
        const t = localStorage.getItem("ah_token");
        const r = await fetch("/api/admin/stats", {
          headers: { Authorization: `Bearer ${t}` },
        });
        return { status: r.status, text: (await r.text()).slice(0, 200) };
      });
      expect(antwort.status, antwort.text).toBe(200);
    });

  test("Unbekannte Adresse liefert die Oberflaeche, keinen Serverfehler",
    async ({ request }) => {
      // Bei einer SPA muss der Proxy jeden unbekannten Pfad auf index.html
      // legen — sonst ist jeder direkt aufgerufene Link (z.B. aus einer
      // E-Mail) kaputt.
      const r = await request.get("/app/bestand");
      expect(r.status()).toBe(200);
      expect((await r.text()).toLowerCase()).toContain("<div id=\"root\"");
    });
});
