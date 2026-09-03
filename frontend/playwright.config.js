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

module.exports = defineConfig({
  testDir: "./e2e",
  testMatch: /.*\.spec\.js$/,
  // Reste frueherer Laeufe (@e2etest-mail.de) vor und nach dem Lauf entfernen.
  globalSetup: "./e2e/global.js",
  globalTeardown: "./e2e/global.js",
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
  },
  projects: [
    { name: "chromium", use: { ...devices["Desktop Chrome"] } },
  ],
  webServer: {
    command: "node e2e/serve.js",
    url: BASE_URL,
    reuseExistingServer: !CI,
    timeout: 30_000,
    env: { E2E_BASE_URL: BASE_URL, E2E_API_URL: API_URL },
  },
});
