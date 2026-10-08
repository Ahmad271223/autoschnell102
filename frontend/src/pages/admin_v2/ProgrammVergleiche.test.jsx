/*
 * Wunsch Ahmad 08.10.2026: Betreiber-Liste der Programm-Vergleiche in Blöcken zu 1.000, darin Seiten zu 100,
 * je Block die 20 meistverglichenen Modelle; die Suche fragt den Server (im gewählten Block).
 */
import { act, createElement } from "react";
import { createRoot } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

globalThis.IS_REACT_ACT_ENVIRONMENT = true;

const api = vi.hoisted(() => ({ get: vi.fn(), delete: vi.fn() }));
vi.mock("@/lib/api", () => ({ api, errMsg: (e, f) => e?.message || f }));
vi.mock("sonner", () => ({ toast: { error: vi.fn(), success: vi.fn() } }));

const { default: AdminProgrammVergleiche } = await import("./ProgrammVergleiche");

let wurzel;
let behaelter;
const el = (id) => behaelter.querySelector(`[data-testid="${id}"]`);
const listenAufrufe = () => api.get.mock.calls.filter(([p]) => p === "/admin/werkzeug-vergleiche");

function antwort({ block = 1, seite = 1, q = "" } = {}) {
  return {
    name: "AutoSchnell Vergleich", freigegeben_fuer: [10001, 10002, 10007], gesamt: 2345, bloecke: 3, block,
    block_von: (block - 1) * 1000 + 1, block_bis: Math.min(block * 1000, 2345), seite, seiten: q ? 1 : 10,
    treffer: q ? 1 : 1000, je_seite: 100, je_block: 1000, top_basis: 998, verbindungen: [],
    top_modelle: [{ modell: "VW Golf", anzahl: 120 }, { modell: "Opel Corsa", anzahl: 80 }],
    vergleiche: [{ id: `v-${block}-${seite}`, erstellt_am: "2026-10-08T10:00:00Z", fahrzeug: { marke: "VW", modell: "Golf" },
                   links: [], hinweise: [], name: "Sucher", konto: "10007-1", firma: "FS", kunden_nr: 10007 }],
  };
}

async function warten() {
  for (let i = 0; i < 4; i += 1) {
    await act(async () => { await new Promise((r) => setTimeout(r, 0)); });
  }
}

beforeEach(() => {
  vi.clearAllMocks();
  vi.useFakeTimers({ shouldAdvanceTime: true });
  api.get.mockImplementation(async (pfad, { params } = {}) => {
    if (pfad === "/admin/werkzeug-downloads") return { data: { downloads: [] } };
    return { data: antwort(params) };
  });
  behaelter = document.createElement("div");
  document.body.appendChild(behaelter);
  wurzel = createRoot(behaelter);
});

afterEach(async () => {
  await act(async () => { wurzel.unmount(); });
  behaelter.remove();
  vi.useRealTimers();
});

describe("Betreiber-Liste: Blöcke, Seiten, Top 20", () => {
  it("lädt Block 1 Seite 1, zeigt Top-Modelle, Blöcke und Seiten", async () => {
    await act(async () => { wurzel.render(createElement(AdminProgrammVergleiche)); });
    await warten();
    expect(listenAufrufe()[0][1].params).toEqual({ block: 1, seite: 1 });
    expect(el("admin-pv-top-1").textContent).toContain("VW Golf");
    expect(el("admin-pv-top-1").textContent).toContain("12 %");          // 120 von 998 = 12,02 -> "12 %" (deutsch, 1 Stelle)
    expect(el("admin-pv-top").textContent).toContain("Vergleiche 1–1.000");
    expect(el("admin-pv-block-3").textContent).toBe("2.001–2.345");
    expect(el("admin-pv-seiten").textContent).toContain("Seite 1 von 10");
    expect(el("admin-pv-seite-10")).not.toBeNull();
  });

  it("Seite und Block wechseln fragt den Server; neuer Block beginnt auf Seite 1", async () => {
    await act(async () => { wurzel.render(createElement(AdminProgrammVergleiche)); });
    await warten();
    await act(async () => { el("admin-pv-seite-3").click(); });
    await warten();
    expect(listenAufrufe().at(-1)[1].params).toEqual({ block: 1, seite: 3 });
    await act(async () => { el("admin-pv-block-2").click(); });
    await warten();
    expect(listenAufrufe().at(-1)[1].params).toEqual({ block: 2, seite: 1 });
    expect(el("admin-pv-top").textContent).toContain("Vergleiche 1.001–2.000");
  });

  it("Suche geht nach kurzer Pause an den Server", async () => {
    await act(async () => { wurzel.render(createElement(AdminProgrammVergleiche)); });
    await warten();
    const feld = el("admin-pv-suche");
    const setter = Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, "value").set;
    await act(async () => {
      setter.call(feld, "Golf");
      feld.dispatchEvent(new Event("input", { bubbles: true }));
    });
    await act(async () => { vi.advanceTimersByTime(450); });
    await warten();
    expect(listenAufrufe().at(-1)[1].params).toEqual({ block: 1, seite: 1, q: "Golf" });
    expect(el("admin-pv-seiten").textContent).toContain("1 Treffer");
  });
});
