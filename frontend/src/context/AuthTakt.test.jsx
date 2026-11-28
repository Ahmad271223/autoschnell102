/*
 * Prüfbericht 20.09.2026 (U-149): leichter Sitzungs-Takt im Anmelde-Kontext —
 * GET /auth/me alle 60 s und beim Sichtbarwerden des Tabs, fuer alle Rollen.
 * Eine beendete Sitzung faellt so auch ausserhalb der Vergleichsseite auf
 * (die 401 loest die bestehende Umleitung in lib/api aus).
 */
import { act, createElement as h } from "react";
import { createRoot } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

globalThis.IS_REACT_ACT_ENVIRONMENT = true;

const ME = { data: { user: { id: "u1", role: "sucher" }, dealer: { id: "d1" },
                     subscription: { active: true } } };
vi.mock("@/lib/api", () => ({
  api: {
    get: vi.fn(async () => ME),
    post: vi.fn(async () => ({ data: {} })),
  },
  aboNeuLadenAnmelden: vi.fn(),
}));

const { api } = await import("@/lib/api");
const { AuthProvider, useAuth, SITZUNG_TAKT_MS, SITZUNG_SICHTBAR_MIN_MS } = await import("./AuthContext");

let root;
let host;
let ctx;
function Fang() { ctx = useAuth(); return null; }

const meAufrufe = () => api.get.mock.calls.filter(([url]) => url === "/auth/me").length;

function sichtbarkeit(wert) {
  Object.defineProperty(document, "visibilityState", { value: wert, configurable: true });
  document.dispatchEvent(new Event("visibilitychange"));
}

beforeEach(() => {
  vi.useFakeTimers();
  api.get.mockClear();
  try { sessionStorage.clear(); localStorage.clear(); } catch { /* egal */ }
});
afterEach(async () => {
  if (root) await act(async () => { root.unmount(); });
  host?.remove();
  root = null;
  Object.defineProperty(document, "visibilityState", { value: "visible", configurable: true });
  vi.useRealTimers();
});

async function starten({ token = "T" } = {}) {
  if (token) sessionStorage.setItem("ah_token", token);
  host = document.createElement("div");
  document.body.appendChild(host);
  root = createRoot(host);
  await act(async () => { root.render(h(AuthProvider, null, h(Fang))); });
}

const weiter = (ms) => act(async () => { await vi.advanceTimersByTimeAsync(ms); });

describe("U-149: Sitzungs-Takt", () => {
  it("angemeldet: alle 60 s ein /auth/me, ohne den Zustand anzufassen", async () => {
    await starten();
    expect(ctx.user?.id).toBe("u1");
    expect(meAufrufe()).toBe(1);                       // das Laden der App
    const vorher = ctx.user;
    await weiter(SITZUNG_TAKT_MS - 1000);
    expect(meAufrufe()).toBe(1);
    await weiter(1000);
    expect(meAufrufe()).toBe(2);
    await weiter(SITZUNG_TAKT_MS);
    expect(meAufrufe()).toBe(3);
    expect(ctx.user).toBe(vorher);                     // kein Neuzeichnen im Takt
  });

  it("im Hintergrund-Tab wird nichts gesendet; beim Sichtbarwerden einmal", async () => {
    await starten();
    sichtbarkeit("hidden");
    await weiter(SITZUNG_TAKT_MS * 2);
    expect(meAufrufe()).toBe(1);
    await act(async () => { sichtbarkeit("visible"); });
    expect(meAufrufe()).toBe(2);
  });

  it("Sichtbarwerden kurz nach der letzten Pruefung loest nichts aus", async () => {
    await starten();
    await weiter(SITZUNG_SICHTBAR_MIN_MS - 1000);
    sichtbarkeit("hidden");
    await act(async () => { sichtbarkeit("visible"); });
    expect(meAufrufe()).toBe(1);
  });

  it("nicht angemeldet: kein Takt", async () => {
    await starten({ token: null });
    expect(ctx.user).toBeNull();
    await weiter(SITZUNG_TAKT_MS * 3);
    expect(meAufrufe()).toBe(0);
  });

  it("nach dem Abmelden endet der Takt", async () => {
    await starten();
    await act(async () => { await ctx.logout(); });
    api.get.mockClear();
    await weiter(SITZUNG_TAKT_MS * 2);
    expect(meAufrufe()).toBe(0);
  });
});
