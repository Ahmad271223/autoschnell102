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
vi.mock("@/lib/dateiOeffnen", () => ({ blobOeffnen: vi.fn() }));

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
    if (pfad === "/admin/werkzeug-leseseiten") {
      return { data: { gesamt: 1, tage: 14, leseseiten: [{ id: "ls-1", url: "https://suchen.mobile.de/fahrzeuge/details.html?id=4711",
        portal: "mobile", item_id: "4711", grund: "Auf der Seite stehen keine Inseratsdaten (Prüfseite von mobile.de oder Inserat nicht mehr online).",
        groesse: 120000, erstellt_am: "2026-10-09T12:00:00Z", name: "Sucher", konto: "10007-1", firma: "FS", kunden_nr: 10007 }] } };
    }
    if (pfad === "/admin/werkzeug-lesebilder") {
      return { data: { gesamt: 1, tage: 30, lesebilder: [{ id: "lb-1", grund: "modell_unbekannt", grund_text: "Modell nicht erkannt",
        fehlt: [], rohtext: "Marke, Modell: Hyundai IBO", fahrzeug: { marke_modell_text: "Hyundai IBO", inserat_id: "476271020" },
        vorschau_b64: "/9j/4AAQ", erstellt_am: "2026-10-09T10:00:00Z", name: "Sucher", konto: "10007-1", firma: "FS", kunden_nr: 10007 }] } };
    }
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

  it("zeigt die nicht erkannten Anzeigen mit Vorschau und Grund (Wunsch Ahmad 09.10.2026)", async () => {
    await act(async () => { wurzel.render(createElement(AdminProgrammVergleiche)); });
    await warten();
    const karte = el("admin-pv-lesebild-lb-1");
    expect(karte).not.toBeNull();
    expect(karte.textContent).toContain("Modell nicht erkannt");
    expect(karte.textContent).toContain("Hyundai IBO");
    expect(karte.querySelector("img").getAttribute("src")).toBe("data:image/jpeg;base64,/9j/4AAQ");
    await act(async () => { el("admin-pv-lesebild-loeschen-lb-1").click(); });
    await warten();
    expect(api.delete).toHaveBeenCalledWith("/admin/werkzeug-lesebilder/lb-1");
    // bei der Erweiterung gibt es keine Lesebilder (nur das Windows-Programm liest den Bildschirm)
    await act(async () => { el("admin-pv-werkzeug-browser-helfer").click(); });
    await warten();
    expect(el("admin-pv-lesebilder")).toBeNull();
  });

  it("Erweiterung: zeigt die nicht lesbaren Inseratsseiten mit Grund und Löschen (Prüfung 09.10.2026, Mokka-e)", async () => {
    await act(async () => { wurzel.render(createElement(AdminProgrammVergleiche)); });
    await warten();
    expect(el("admin-pv-leseseiten")).toBeNull();                    // nur in der Erweiterungs-Ansicht
    expect(api.get.mock.calls.some(([p]) => p === "/admin/werkzeug-leseseiten")).toBe(false);
    await act(async () => { el("admin-pv-werkzeug-browser-helfer").click(); });
    await warten();
    const zeile = el("admin-pv-leseseite-ls-1");
    expect(zeile).not.toBeNull();
    expect(zeile.textContent).toContain("keine Inseratsdaten");
    expect(zeile.textContent).toContain("mobile 4711");
    expect(zeile.querySelector("a").getAttribute("href")).toBe("https://suchen.mobile.de/fahrzeuge/details.html?id=4711");
    await act(async () => { el("admin-pv-leseseite-loeschen-ls-1").click(); });
    await warten();
    expect(api.delete).toHaveBeenCalledWith("/admin/werkzeug-leseseiten/ls-1");
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

  it("Browser-Erweiterung umschalten: fragt mit werkzeug, beginnt bei Block 1 (Prüfung 08.10.2026)", async () => {
    await act(async () => { wurzel.render(createElement(AdminProgrammVergleiche)); });
    await warten();
    await act(async () => { el("admin-pv-seite-3").click(); });
    await warten();
    await act(async () => { el("admin-pv-werkzeug-browser-helfer").click(); });
    await warten();
    expect(listenAufrufe().at(-1)[1].params).toEqual({ block: 1, seite: 1, werkzeug: "browser-helfer" });
    expect(behaelter.textContent).toContain("Verbundene Browser");
    await act(async () => { el("admin-pv-werkzeug-programm").click(); });
    await warten();
    expect(listenAufrufe().at(-1)[1].params).toEqual({ block: 1, seite: 1 });
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
