/*
 * Rollenprüfung 22.09.2026, Welle 2 — Fahrzeugakte.
 *  RP-045/RP-144  "Speichern & weiterverkaufen" nur mit freigeschaltetem Marktplatz
 *  RP-474         km/Schäden aus dem unterschriebenen Abholprotokoll übernehmen
 *                 (nur was noch fehlt; POST apply-deviations ["abholprotokoll"])
 */
import { act, createElement } from "react";
import { createRoot } from "react-dom/client";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { afterEach, describe, expect, it, vi } from "vitest";

vi.mock("sonner", () => ({ toast: { success: vi.fn(), error: vi.fn(), info: vi.fn(), warning: vi.fn(), message: vi.fn() } }));
vi.mock("@/context/AuthContext", () => ({ useAuth: () => ({ user: { id: "chef1", role: "dealer" } }) }));
vi.mock("@/components/BeweisCard", () => ({ default: () => null }));
vi.mock("@/components/AbholFoto", () => ({ default: () => null }));
vi.mock("@/components/AbholberichtDialog", () => ({ default: () => null, fotosBis: () => null }));

globalThis.IS_REACT_ACT_ENVIRONMENT = true;

const { api } = await import("@/lib/api");
const { featuresSetzen } = await import("@/lib/features");
const { default: FahrzeugAkte } = await import("./FahrzeugAkte");

function akte({ lifecycle = "abgeholt", befund = null, known = [] } = {}) {
  return {
    vehicle: {
      id: "v1", dealer_id: "d1", lifecycle, source: "manuell", known_defects: known,
      data: { make_label: "BMW", model_label: "320d", mileage: 100000 }, bestand: {},
    },
    protokoll_befund: befund,
    einkaufspreis: {}, kaufvorgaenge: [], kaufvorgaenge_gesamt: 0, owner: null, mitbearbeiter: [],
    retention_days_left: null, contracts: [], contracts_gesamt: 0, appointments: [],
    appointments_gesamt: 0, pickup_report: null, pickup_reports: [], pickup_reports_gesamt: 0,
    comparisons: [], comparisons_gesamt: 0, listings: [], protocols: [], protocols_gesamt: 0,
    history: [], history_gekuerzt: false, fahrerfoto_tage: 30,
  };
}

let wurzel = null;
let behaelter = null;
async function zeigeAkte(daten, { marktplatz }) {
  featuresSetzen({ marktplatz });
  vi.spyOn(api, "get").mockImplementation(async (url) => {
    if (String(url).startsWith("/vehicles/v1/akte")) return { data: daten };
    return { data: {} };
  });
  behaelter = document.createElement("div");
  document.body.appendChild(behaelter);
  wurzel = createRoot(behaelter);
  await act(async () => {
    wurzel.render(createElement(MemoryRouter, { initialEntries: ["/app/akte/v1"] },
      createElement(Routes, null,
        createElement(Route, { path: "/app/akte/:id", element: createElement(FahrzeugAkte) }))));
  });
  await act(async () => { await Promise.resolve(); });
  return behaelter;
}

afterEach(() => {
  if (wurzel) act(() => wurzel.unmount());
  behaelter?.remove();
  wurzel = null;
  behaelter = null;
  vi.restoreAllMocks();
  featuresSetzen(null);
});

describe("RP-045/RP-144: Weiterverkaufen hängt am Marktplatz-Schalter", () => {
  it("Marktplatz aus: nur speichern / löschen", async () => {
    const el = await zeigeAkte(akte(), { marktplatz: false });
    expect(el.textContent).toContain("Nur speichern");
    expect(el.textContent).not.toContain("Speichern & weiterverkaufen");
    expect(el.querySelector('[data-testid="akte-weiterverkaufen"]')).toBeNull();
  });

  it("Marktplatz an: Knopf da", async () => {
    const el = await zeigeAkte(akte(), { marktplatz: true });
    expect(el.querySelector('[data-testid="akte-weiterverkaufen"]')).not.toBeNull();
  });
});

describe("RP-057 b: mehrdeutiger Einkaufspreis", () => {
  it("sagt, warum kein Preis dasteht", async () => {
    const daten = akte({ lifecycle: "abholung_geplant" });
    daten.einkaufspreis = { preis: null, quelle: "mehrdeutig", kaufvorgang_id: null };
    const el = await zeigeAkte(daten, { marktplatz: false });
    expect(el.textContent).toContain("mehrere Verträge mit verschiedenen Preisen");
  });
});

describe("RP-474: Befund aus dem Abholprotokoll", () => {
  it("zeigt nur Neues und übernimmt mit der Protokoll-Kennung", async () => {
    const el = await zeigeAkte(akte({
      known: ["Delle Heck"],
      befund: { km: 123456, schaeden: ["Delle Heck", "Kratzer: Tür links"] },
    }), { marktplatz: false });
    const karte = el.querySelector('[data-testid="akte-protokoll-befund"]');
    expect(karte).not.toBeNull();
    expect(karte.textContent).toContain("123.456 km");
    expect(karte.textContent).toContain("Kratzer: Tür links");
    expect(karte.textContent).not.toContain("Delle Heck");
    const post = vi.spyOn(api, "post").mockResolvedValue({ data: { applied: [{}, {}] } });
    await act(async () => {
      el.querySelector('[data-testid="akte-protokoll-uebernehmen"]').click();
    });
    expect(post).toHaveBeenCalledWith("/vehicles/v1/apply-deviations",
      { deviation_ids: ["abholprotokoll"] });
  });

  it("alles schon übernommen oder Fahrzeug abgeschlossen: keine Karte", async () => {
    let el = await zeigeAkte(akte({
      known: ["Kratzer: Tür links"], befund: { km: 100000, schaeden: ["Kratzer: Tür links"] },
    }), { marktplatz: false });
    expect(el.querySelector('[data-testid="akte-protokoll-befund"]')).toBeNull();
    act(() => wurzel.unmount());
    behaelter.remove();
    vi.restoreAllMocks();
    el = await zeigeAkte(akte({ lifecycle: "verkauft", befund: { km: 1, schaeden: [] } }),
      { marktplatz: false });
    expect(el.querySelector('[data-testid="akte-protokoll-befund"]')).toBeNull();
  });
});
