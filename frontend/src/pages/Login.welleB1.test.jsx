/**
 * Welle B1 / Befund aus Welle A9 (22.09.2026): Nach einer Firmensperre (403,
 * X-Sperre: firma) zeigte die Anmeldeseite den allgemeinen Satz statt des
 * Servertexts. Ablauf: api.js merkt den Grund und setzt die harte Umleitung
 * /login?reason=session — davor rendert React aber schon ProtectedRoute mit
 * user=null (<Navigate to="/login?next=…">), und Login.jsx las UND löschte den
 * Grund einmalig. Jetzt: lesen ohne löschen, Anzeige auch ohne reason=session,
 * vergessen erst nach der gelungenen Anmeldung.
 */
import { act, createElement as h } from "react";
import { createRoot } from "react-dom/client";
import { MemoryRouter } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

globalThis.IS_REACT_ACT_ENVIRONMENT = true;

const auth = vi.hoisted(() => ({ login: vi.fn(), loginMfa: vi.fn() }));
vi.mock("@/context/AuthContext", () => ({ useAuth: () => auth }));
vi.mock("@/context/BuyerContext", () => ({ useBuyer: () => null }));
vi.mock("sonner", () => ({ toast: { success: vi.fn(), error: vi.fn(), message: vi.fn() } }));
vi.mock("@/lib/fassung", () => ({ neueFassungLaden: () => false, fassungMithoeren: () => {} }));
vi.mock("@/components/InstallPWAButton", () => ({ default: () => null }));
vi.mock("@/components/RechtsLinks", () => ({ default: () => null }));

const { default: Login } = await import("./Login");
const { ABMELDEGRUND_SCHLUESSEL } = await import("@/lib/api");

const SPERRE = "Diese Firma ist gesperrt — bitte den Betreiber kontaktieren.";
const ALLGEMEIN = "Du wurdest abgemeldet";

const wurzeln = [];
async function rendern(adresse) {
  const host = document.createElement("div");
  document.body.appendChild(host);
  const root = createRoot(host);
  await act(async () => {
    root.render(h(MemoryRouter, { initialEntries: [adresse] }, h(Login)));
  });
  wurzeln.push({ root, host });
  return host;
}
const grund = (host) => host.querySelector('[data-testid="login-abmeldegrund"]');
async function tippen(host, testId, wert) {
  const feld = host.querySelector(`[data-testid="${testId}"]`);
  const setter = Object.getOwnPropertyDescriptor(window.HTMLInputElement.prototype, "value").set;
  await act(async () => {
    setter.call(feld, wert);
    feld.dispatchEvent(new Event("input", { bubbles: true }));
  });
}

beforeEach(() => { sessionStorage.clear(); });
afterEach(async () => {
  while (wurzeln.length) {
    const { root, host } = wurzeln.pop();
    await act(async () => { root.unmount(); });
    host.remove();
  }
  sessionStorage.clear();
  vi.clearAllMocks();
});

describe("Welle A9: Sperrgrund kommt auf der Anmeldeseite an", () => {
  it("SPA-Umleitung ohne reason, danach harte Umleitung mit reason: beide zeigen den Servertext", async () => {
    sessionStorage.setItem(ABMELDEGRUND_SCHLUESSEL, SPERRE);
    // 1. <Navigate to="/login?next=…"> aus ProtectedRoute (user=null)
    const erste = await rendern("/login?next=%2Fapp%2Fbestand");
    expect(grund(erste)?.textContent).toBe(SPERRE);
    // der Grund bleibt gemerkt — die harte Umleitung kommt gleich
    expect(sessionStorage.getItem(ABMELDEGRUND_SCHLUESSEL)).toBe(SPERRE);
    // 2. harte Umleitung aus api.js: /login?reason=session (neue Seite)
    const zweite = await rendern("/login?reason=session&next=%2Fapp%2Fbestand");
    expect(grund(zweite)?.textContent).toBe(SPERRE);
    expect(grund(zweite)?.textContent).not.toContain(ALLGEMEIN);
  });

  it("ohne gemerkten Grund: allgemeiner Satz nur mit reason=session", async () => {
    const ohne = await rendern("/login?next=%2Fapp");
    expect(grund(ohne)).toBeNull();
    const mit = await rendern("/login?reason=session");
    expect(grund(mit)?.textContent).toContain(ALLGEMEIN);
  });

  it("nach der gelungenen Anmeldung ist der Grund vergessen", async () => {
    sessionStorage.setItem(ABMELDEGRUND_SCHLUESSEL, SPERRE);
    auth.login.mockResolvedValue({ role: "dealer", kontonummer: "10023" });
    const host = await rendern("/login?reason=session");
    await tippen(host, "login-kontonummer", "10023");
    await tippen(host, "login-password", "geheim");
    await act(async () => {
      host.querySelector("form").dispatchEvent(new Event("submit", { bubbles: true, cancelable: true }));
    });
    await act(async () => { await new Promise((r) => setTimeout(r, 0)); });
    expect(auth.login).toHaveBeenCalledWith("10023", "geheim");
    expect(sessionStorage.getItem(ABMELDEGRUND_SCHLUESSEL)).toBeNull();
  });
});
