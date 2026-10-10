/*
 * Rollenpruefung 22.09.2026 (RP-557): "bekanntes Geraet". Die Anmeldung
 * schickt den gemerkten Geraete-Schluessel mit und legt einen neuen aus der
 * Antwort ab — ein bekanntes Geraet ist von der Konto-Sperre nach vielen
 * Fehlversuchen (Angriff von fremden IPs) entlastet.
 */
import { act, createElement as h } from "react";
import { createRoot } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

globalThis.IS_REACT_ACT_ENVIRONMENT = true;

const gesendet = [];
vi.mock("@/lib/api", () => ({
  api: {
    get: vi.fn(async () => ({ data: {} })),
    post: vi.fn(async (url, body) => {
      gesendet.push({ url, body });
      return { data: { token: "t", user: { role: "b2b_buyer" }, geraet_id: "Neuer-Schluessel_1234567890" } };
    }),
  },
  aboNeuLadenAnmelden: vi.fn(),
}));

const { AuthProvider, useAuth, geraetIdLesen, geraetIdMerken, GERAET_SCHLUESSEL } =
  await import("./AuthContext");

let root;
let host;
let ctx;
beforeEach(() => { gesendet.length = 0; try { localStorage.clear(); } catch { /* egal */ } });
afterEach(async () => {
  if (root) await act(async () => { root.unmount(); });
  host?.remove();
  root = null;
});

function Fang() { ctx = useAuth(); return null; }

describe("RP-557: Geraete-Schluessel", () => {
  it("liest nur gueltige Werte", () => {
    expect(geraetIdLesen()).toBeUndefined();
    localStorage.setItem(GERAET_SCHLUESSEL, "kurz");
    expect(geraetIdLesen()).toBeUndefined();
    localStorage.setItem(GERAET_SCHLUESSEL, "Gueltiger-Schluessel_123");
    expect(geraetIdLesen()).toBe("Gueltiger-Schluessel_123");
  });

  it("merkt nur gueltige Werte aus der Antwort", () => {
    geraetIdMerken({ geraet_id: "<script>" });
    expect(localStorage.getItem(GERAET_SCHLUESSEL)).toBeNull();
    geraetIdMerken({});
    expect(localStorage.getItem(GERAET_SCHLUESSEL)).toBeNull();
    geraetIdMerken({ geraet_id: "Aus-Der-Antwort_0123456789" });
    expect(localStorage.getItem(GERAET_SCHLUESSEL)).toBe("Aus-Der-Antwort_0123456789");
  });

  it("die Anmeldung schickt den Schluessel mit und legt den neuen ab", async () => {
    localStorage.setItem(GERAET_SCHLUESSEL, "Altes-Geraet_0123456789");
    host = document.createElement("div");
    document.body.appendChild(host);
    root = createRoot(host);
    await act(async () => { root.render(h(AuthProvider, null, h(Fang))); });
    await act(async () => { await ctx.login("K7ABC12", "Passwort-123"); });
    const anmeldung = gesendet.find((g) => g.url === "/auth/login");
    expect(anmeldung.body).toEqual({ kontonummer: "K7ABC12", password: "Passwort-123",
                                     geraet_id: "Altes-Geraet_0123456789" });
    expect(localStorage.getItem(GERAET_SCHLUESSEL)).toBe("Neuer-Schluessel_1234567890");
  });
});
