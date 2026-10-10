/**
 * Welle B1 / Befund aus Welle A9 (22.09.2026): Abmeldegrund lesen ohne zu
 * löschen (api.js) und die SPA-Umleitung ohne Nutzer trägt reason=session,
 * sobald ein Grund gemerkt ist (ProtectedRoute.anmeldeZiel).
 */
import { afterEach, describe, expect, it, vi } from "vitest";

vi.mock("@/context/AuthContext", () => ({ useAuth: () => ({}) }));

const { ABMELDEGRUND_SCHLUESSEL, abmeldegrundLesen, abmeldegrundMerken, abmeldegrundVergessen }
  = await import("@/lib/api");
const { anmeldeZiel } = await import("./ProtectedRoute");

afterEach(() => { sessionStorage.clear(); });

describe("Abmeldegrund lesen / vergessen", () => {
  it("lesen löscht nichts, vergessen räumt auf", () => {
    abmeldegrundMerken("Firma gesperrt");
    expect(abmeldegrundLesen()).toBe("Firma gesperrt");
    expect(abmeldegrundLesen()).toBe("Firma gesperrt");
    expect(sessionStorage.getItem(ABMELDEGRUND_SCHLUESSEL)).toBe("Firma gesperrt");
    abmeldegrundVergessen();
    expect(abmeldegrundLesen()).toBe("");
    expect(sessionStorage.getItem(ABMELDEGRUND_SCHLUESSEL)).toBeNull();
  });

  it("ohne Text (kein detail) bleibt der Grund leer", () => {
    abmeldegrundMerken(undefined);
    expect(abmeldegrundLesen()).toBe("");
  });
});

describe("ProtectedRoute.anmeldeZiel", () => {
  const loc = { pathname: "/app/termine", search: "?tab=offen", hash: "#termin-1" };
  const ziel = encodeURIComponent("/app/termine?tab=offen#termin-1");

  it("ohne Grund: nur der Rückweg (U-151)", () => {
    expect(anmeldeZiel(loc, "")).toBe(`/login?next=${ziel}`);
    expect(anmeldeZiel(loc)).toBe(`/login?next=${ziel}`);
  });

  it("mit gemerktem Grund: reason=session dazu", () => {
    abmeldegrundMerken("Firma gesperrt");
    expect(anmeldeZiel(loc)).toBe(`/login?reason=session&next=${ziel}`);
    expect(anmeldeZiel(loc, "x")).toBe(`/login?reason=session&next=${ziel}`);
  });
});
