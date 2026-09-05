// @ts-check
/*
 * Playwright-E2E-Suite: Produktions-Build des Frontends gegen ein echtes
 * Backend im Anbieter-Mock-Modus (MOCK_PROVIDER_FETCH=true).
 *
 *   E2E_BASE_URL  Frontend (Default http://localhost:3100)
 *   E2E_API_URL   Backend-API (Default http://localhost:8002/api)
 *
 * Lokal:  cd frontend && REACT_APP_BACKEND_URL= yarn build && yarn e2e
 * Der webServer (e2e/serve.js) liefert build/ aus und reicht /api an das
 * Backend weiter — dieselbe Herkunft wie in Produktion hinter nginx.
 */
const { defineConfig, devices } = require("@playwright/test");

const BASE_URL = process.env.E2E_BASE_URL || "http://localhost:3100";
const API_URL = process.env.E2E_API_URL || "http://localhost:8002/api";
const CI = !!process.env.CI;

// Stack-Modus (Pruefbericht Runde 7, Befund 6): derselbe Browser laeuft
// gegen den ECHTEN Compose-Stack — nginx mit HTTPS, Backend im Container,
// MongoDB mit Passwort. Dann darf Playwright keinen eigenen Test-Server
// starten (nginx liefert schon aus), und das Zertifikat ist im CI
// selbstsigniert, also muss der Browser es akzeptieren. Nur der Rauchtest
// stack.spec.js laeuft in diesem Modus; die grosse Suite bleibt beim
// schnellen Aufbau.
const STACK = process.env.E2E_STACK === "1";

module.exports = defineConfig({
  testDir: "./e2e",
  testMatch: STACK ? /stack\.spec\.js$/ : /.*\.spec\.js$/,
  // Ohne Stack-Modus wuerde der Rauchtest gegen den Test-Server laufen
  // und dort zu Recht scheitern (kein HTTPS, keine Proxy-Kopfzeilen).
  testIgnore: STACK ? undefined : /stack\.spec\.js$/,
  // Reste frueherer Laeufe (@e2etest-mail.de) vor und nach dem Lauf entfernen.
  // Im Stack-Modus gibt es keine Testdaten zum Aufraeumen.
  globalSetup: STACK ? undefined : "./e2e/global.js",
  globalTeardown: STACK ? undefined : "./e2e/global.js",
  timeout: 60_000,
  expect: { timeout: 10_000 },
  // Single-Session-Backend: jede Anmeldung eines Kontos (v.a. Super-Admin)
  // wirft dessen andere Sitzungen raus — parallele Worker wuerden sich
  // gegenseitig abmelden.
  fullyParallel: false,
  workers: 1,
  retries: CI ? 1 : 0,
  forbidOnly: CI,
  reporter: CI ? [["list"], ["html", { open: "never" }]] : [["list"]],
  use: {
    baseURL: BASE_URL,
    trace: "on-first-retry",
    screenshot: "only-on-failure",
    locale: "de-DE",
    // PWA-Service-Worker (public/service-worker.js) hat im Test nichts verloren.
    serviceWorkers: "block",
    // Im CI traegt der Stack ein selbstsigniertes Zertifikat.
    ignoreHTTPSErrors: STACK,
  },
  projects: [
    { name: "chromium", use: { ...devices["Desktop Chrome"] } },
  ],
  webServer: STACK ? undefined : {
    command: "node e2e/serve.js",
    url: BASE_URL,
    reuseExistingServer: !CI,
    timeout: 30_000,
    env: { E2E_BASE_URL: BASE_URL, E2E_API_URL: API_URL },
  },
});
