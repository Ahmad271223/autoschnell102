/*
 * Rollenprüfung 22.09.2026, Welle 4 — Review-Befund Team "vertrag" (Oberfläche).
 *  "Weitere Verträge laden" fragt mit der Suche/dem Zeitraum der geladenen
 *  ersten Seite weiter — nicht mit dem gerade getippten, noch nicht
 *  abgeschickten Suchtext (sonst mischten sich zwei Abfragen in der Liste).
 */
import { act, createElement } from "react";
import { createRoot } from "react-dom/client";
import { MemoryRouter } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

globalThis.IS_REACT_ACT_ENVIRONMENT = true;

const api = vi.hoisted(() => ({ get: vi.fn(), post: vi.fn(), delete: vi.fn() }));
const toastMock = vi.hoisted(() => ({ error: vi.fn(), success: vi.fn(), warning: vi.fn(), info: vi.fn() }));
vi.mock("@/lib/api", () => ({ api, errMsg: (e) => e?.message || "Fehler", openAuthedFile: vi.fn() }));
vi.mock("sonner", () => ({ toast: toastMock }));
vi.mock("@/lib/pdf", () => ({ openContractPdf: vi.fn() }));
vi.mock("@/lib/bilder", () => ({ thumbSrc: (a, b) => a || b, thumbFehler: vi.fn() }));
vi.mock("@/components/BeweisCard", () => ({ default: () => null }));
vi.mock("@/components/FolgeMailDialog", () => ({ default: () => null }));
vi.mock("@/components/SendDialog", () => ({
  default: () => null, abholterminAnlegen: vi.fn(), TERMIN_MELDUNG: {},
}));

const { default: PDFArchiv, ARCHIV_SEITE } = await import("./PDFArchiv");

const vertrag = (id) => ({
  id, make: "VW", model: "Golf", status: "erstellt", created_at: "2026-09-20T10:00:00+00:00",
  contract_data: {}, seller_name: "Max", purchase_price: 1000,
});
const seite = (ids, weiter) => ({
  data: ids.map(vertrag),
  headers: weiter ? { "x-truncated": "1", "x-next-before": weiter } : { "x-truncated": "0" },
});

describe("Review: 'Weitere Verträge laden' bleibt bei der geladenen Suche", () => {
  let wurzel;
  let behaelter;
  const el = (id) => behaelter.querySelector(`[data-testid="${id}"]`);
  async function warten() {
    await act(async () => { await new Promise((r) => setTimeout(r, 0)); });
    await act(async () => { await new Promise((r) => setTimeout(r, 0)); });
  }
  async function tippen(wert) {
    const feld = el("pdf-search-input");
    const setter = Object.getOwnPropertyDescriptor(window.HTMLInputElement.prototype, "value").set;
    await act(async () => {
      setter.call(feld, wert);
      feld.dispatchEvent(new Event("input", { bubbles: true }));
    });
  }
  async function enter() {
    await act(async () => {
      el("pdf-search-input").dispatchEvent(new KeyboardEvent("keydown", { key: "Enter", bubbles: true }));
    });
    await warten();
  }
  async function mehr() {
    await act(async () => { el("pdfs-mehr-laden").click(); });
    await warten();
  }
  const params = (i) => api.get.mock.calls[i][1].params;

  beforeEach(async () => {
    api.get.mockReset();
    behaelter = document.createElement("div");
    document.body.appendChild(behaelter);
    wurzel = createRoot(behaelter);
  });
  afterEach(async () => {
    if (wurzel) await act(async () => { wurzel.unmount(); });
    behaelter.remove();
  });

  it("getippter, nicht abgeschickter Suchtext geht nicht in die nächste Seite", async () => {
    api.get
      .mockResolvedValueOnce(seite(["c1", "c2"], "2026-09-01T00:00:00"))   // erste Seite, ungefiltert
      .mockResolvedValueOnce(seite(["c3"], ""))                            // "weitere" dazu
      .mockResolvedValueOnce(seite(["g1"], "2026-08-01T00:00:00"))         // Suche "Golf"
      .mockResolvedValueOnce(seite(["g2"], ""));                           // "weitere" zur Suche
    await act(async () => {
      wurzel.render(createElement(MemoryRouter, null, createElement(PDFArchiv)));
    });
    await warten();
    expect(params(0)).toEqual({ limit: ARCHIV_SEITE });
    expect(el("pdf-card-c1")).toBeTruthy();

    // Suchtext tippen, OHNE Enter — dann "Weitere Verträge laden"
    await tippen("Golf");
    await mehr();
    expect(api.get).toHaveBeenCalledTimes(2);
    expect(params(1)).toEqual({ limit: ARCHIV_SEITE, before: "2026-09-01T00:00:00" });
    expect(el("pdf-card-c3")).toBeTruthy();

    // Jetzt wirklich suchen: erste Seite MIT q, die naechste ebenso
    await enter();
    expect(params(2)).toEqual({ limit: ARCHIV_SEITE, q: "Golf" });
    expect(el("pdf-card-c1")).toBeFalsy();
    await tippen("Passat");                     // wieder nur getippt
    await mehr();
    expect(params(3)).toEqual({ limit: ARCHIV_SEITE, q: "Golf", before: "2026-08-01T00:00:00" });
    expect(el("pdf-card-g1")).toBeTruthy();
    expect(el("pdf-card-g2")).toBeTruthy();
  });
});
