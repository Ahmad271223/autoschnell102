/**
 * Wunsch Ahmad 06.10.2026: Der Chef sieht in der Freigabe die Fotos des Fahrers je neuem Schaden und die
 * Lackdicke-Messungen (Wert + Fotos). Fotos kommen nur in der Sichtfrist (der Server filtert).
 */
import { act, createElement } from "react";
import { createRoot } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

const api = { get: vi.fn(), post: vi.fn(), put: vi.fn(), delete: vi.fn() };
vi.mock("@/lib/api", () => ({ api, errMsg: (e, s) => e?.message || s, openAuthedFile: vi.fn() }));
vi.mock("sonner", () => ({ toast: { success: vi.fn(), error: vi.fn(), info: vi.fn(), warning: vi.fn() } }));
vi.mock("@/context/AuthContext", () => ({ useAuth: () => ({ user: { id: "u1", role: "dealer" } }) }));
vi.mock("@/lib/freigaben", () => ({ freigabeZaehlerAktualisieren: vi.fn() }));
vi.mock("@/lib/ungespeichert", () => ({ useUngespeichert: () => {} }));
vi.mock("@/components/KiBewertungKarte", () => ({ default: () => null }));
vi.mock("@/components/AbholFoto", async () => {
  const { createElement: ce } = await import("react");
  return { default: (p) => ce("span", { "data-testid": "foto-vorschau", "data-pfad": p.pfad }) };
});

globalThis.IS_REACT_ACT_ENVIRONMENT = true;
const { default: Freigaben } = await import("./Freigaben");

const KARTE = {
  protocol_id: "p1", appointment_id: "t1", vehicle_id: "v1", status: "zur_freigabe", stand: "s1",
  fahrzeug: "BMW 320d", abholung: "06.10.2026 10:00", abholort: "Teststr. 1", verkaeufer: "Vera", fahrer: "Fritz",
  abgeschickt_am: "2026-10-06T08:00:00Z", kilometerstand: "", vergleich: [], abweichungen: [],
  neue_schaeden: [{ id: "s1", type_label: "Kratzer", zone: "Motorhaube" }],
  lackmessungen: [{ id: "l1", zone: "Dach", wert_um: 410 }],
  schaden_fotos: [{ id: "a", schaden_id: "s1", sichtbar_bis: "2026-10-13T08:00:00+00:00" },
                  { id: "b", schaden_id: "l1", sichtbar_bis: "2026-10-13T08:00:00+00:00" }],
  schluessel: "2", schluessel_vereinbart: "2", schluessel_vereinbart_fehlt: false,
  preis_vertrag: 10000, rueckfrage_antworten: [], rueckfrage_verlauf: [],
};

let wurzel;
let behaelter;
const el = (id) => behaelter.querySelector(`[data-testid="${id}"]`);

beforeEach(async () => {
  api.get.mockImplementation((url) => (url.includes("rueckfragen-offen")
    ? Promise.resolve({ data: [] })
    : Promise.resolve({ data: [KARTE], headers: {} })));
  try { window.localStorage.clear(); } catch { /* egal */ }
  behaelter = document.createElement("div");
  document.body.appendChild(behaelter);
  wurzel = createRoot(behaelter);
  await act(async () => { wurzel.render(createElement(Freigaben)); });
  for (let i = 0; i < 3; i += 1) await act(async () => { await new Promise((r) => setTimeout(r, 0)); });
});
afterEach(async () => {
  await act(async () => { wurzel.unmount(); });
  behaelter.remove();
  vi.clearAllMocks();
});

describe("Freigabe: Schadenfotos und Lackdicke", () => {
  it("Fotos je Schaden über den Chef-Weg, Lackdicke mit Wert und Foto", async () => {
    const schaden = el("schadenfotos-s1");
    expect(schaden).toBeTruthy();
    expect(schaden.querySelector('[data-testid="foto-vorschau"]').getAttribute("data-pfad"))
      .toBe("/protocols/p1/schaden-fotos/a");
    expect(el("schadenfotos-bis-s1").textContent).toContain("sichtbar bis");
    const lack = el("freigabe-lack-p1");
    expect(lack.textContent).toContain("Lackdicke gemessen: 1");
    expect(lack.textContent).toContain("Dach: 410 µm");
    expect(el("schadenfotos-l1").querySelector('[data-testid="foto-vorschau"]').getAttribute("data-pfad"))
      .toBe("/protocols/p1/schaden-fotos/b");
  });
});
