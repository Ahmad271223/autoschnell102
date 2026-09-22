/*
 * Prüfbericht 20.09.2026 (U-150): Nachladefehler (fehlende Datei nach einem
 * Update) und Fehler beim Zeichnen bekommen verschiedene Meldungen; die
 * Grenze funktioniert auch ohne Router (index.jsx).
 */
import { act, createElement as h } from "react";
import { createRoot } from "react-dom/client";
import { MemoryRouter } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import NachladeFehler, { FehlerGrenze, istNachladefehler } from "./NachladeFehler";

globalThis.IS_REACT_ACT_ENVIRONMENT = true;

function Kaputt({ fehler }) {
  throw fehler;
}

let root;
let host;
let echt;
let konsole;

beforeEach(() => {
  echt = window.location;
  delete window.location;
  window.location = { assign: vi.fn(), reload: vi.fn() };
  // React meldet gefangene Fehler auf der Konsole — hier nicht von Belang.
  konsole = vi.spyOn(console, "error").mockImplementation(() => {});
});
afterEach(async () => {
  if (root) await act(async () => { root.unmount(); });
  host?.remove();
  root = null;
  window.location = echt;
  konsole.mockRestore();
});

async function rendern(element) {
  host = document.createElement("div");
  document.body.appendChild(host);
  root = createRoot(host);
  await act(async () => { root.render(element); });
  return host;
}

describe("istNachladefehler", () => {
  it("erkennt die Meldungen der Browser fuer fehlende Seitendateien", () => {
    for (const text of [
      "Failed to fetch dynamically imported module: https://app.example/static/js/Termine.abc.chunk.js",
      "error loading dynamically imported module: https://app.example/static/js/x.js",
      "Importing a module script failed.",
      "Loading chunk 12 failed",
    ]) {
      expect(istNachladefehler(new Error(text))).toBe(true);
    }
    const chunk = new Error("x");
    chunk.name = "ChunkLoadError";
    expect(istNachladefehler(chunk)).toBe(true);
  });

  it("ein Fehler beim Zeichnen ist keiner", () => {
    expect(istNachladefehler(new TypeError("Cannot read properties of undefined (reading 'map')"))).toBe(false);
    expect(istNachladefehler(null)).toBe(false);
  });
});

describe("U-150: zwei Meldungen", () => {
  it("Nachladefehler: Update-Hinweis mit 'Neu laden'", async () => {
    const fehler = new Error("Failed to fetch dynamically imported module: https://app.example/static/js/a.js");
    const el = await rendern(h(MemoryRouter, null, h(NachladeFehler, null, h(Kaputt, { fehler }))));
    const grenze = el.querySelector('[data-testid="nachlade-fehler"]');
    expect(grenze.getAttribute("data-art")).toBe("nachladen");
    expect(grenze.textContent).toMatch(/nach einem Update/);
    expect(el.querySelector('[data-testid="nachlade-fehler-start"]')).toBeNull();
    await act(async () => { el.querySelector('[data-testid="nachlade-fehler-neu"]').click(); });
    expect(window.location.reload).toHaveBeenCalledTimes(1);
  });

  it("Render-Fehler: neutraler Text mit 'Zur Startseite'", async () => {
    const fehler = new TypeError("Cannot read properties of undefined (reading 'map')");
    const el = await rendern(h(MemoryRouter, null, h(NachladeFehler, null, h(Kaputt, { fehler }))));
    const grenze = el.querySelector('[data-testid="nachlade-fehler"]');
    expect(grenze.getAttribute("data-art")).toBe("fehler");
    expect(grenze.textContent).not.toMatch(/nach einem Update/);
    expect(grenze.textContent).toMatch(/Zur Startseite/);
    await act(async () => { el.querySelector('[data-testid="nachlade-fehler-start"]').click(); });
    expect(window.location.assign).toHaveBeenCalledWith("/start");
  });

  it("FehlerGrenze arbeitet ohne Router (zweite Grenze in index.jsx)", async () => {
    const el = await rendern(h(FehlerGrenze, null, h(Kaputt, { fehler: new Error("kaputt") })));
    expect(el.querySelector('[data-testid="nachlade-fehler"]')).toBeTruthy();
  });

  it("ohne Fehler: die Kinder", async () => {
    const el = await rendern(h(FehlerGrenze, null, h("p", { "data-testid": "ok" }, "ok")));
    expect(el.querySelector('[data-testid="ok"]')).toBeTruthy();
  });
});
