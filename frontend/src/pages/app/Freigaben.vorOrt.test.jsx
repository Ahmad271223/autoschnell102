/**
 * Wunsch Ahmad 01.10.2026: Der Chef sieht in der Freigabe, was der Fahrer vor Ort als fehlend, defekt,
 * anders oder mangelhaft angekreuzt hat (Ausstattung, Unterlagen, Zustand) — und was ohne Angabe blieb.
 * Vorher kamen die Haken zwar vom Server, die Seite zeigte sie aber nicht.
 */
import { act, createElement } from "react";
import { createRoot } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

const api = { get: vi.fn(), post: vi.fn(), put: vi.fn(), delete: vi.fn() };
vi.mock("@/lib/api", () => ({ api, errMsg: (e, s) => e?.message || s }));
vi.mock("sonner", () => ({ toast: { success: vi.fn(), error: vi.fn(), info: vi.fn(), warning: vi.fn() } }));
vi.mock("@/context/AuthContext", () => ({ useAuth: () => ({ user: { id: "u1", role: "dealer" } }) }));
vi.mock("@/lib/freigaben", () => ({ freigabeZaehlerAktualisieren: vi.fn() }));
vi.mock("@/lib/ungespeichert", () => ({ useUngespeichert: () => {} }));
vi.mock("@/components/KiBewertungKarte", () => ({ default: () => null }));

globalThis.IS_REACT_ACT_ENVIRONMENT = true;
const { default: Freigaben } = await import("./Freigaben");

const BASIS = {
  protocol_id: "p1", appointment_id: "t1", vehicle_id: "v1", status: "zur_freigabe", stand: "s1",
  fahrzeug: "BMW 320d", abholung: "01.10.2026 10:00", abholort: "Teststr. 1", verkaeufer: "Vera", fahrer: "Fritz",
  abgeschickt_am: "2026-10-01T08:00:00Z", kilometerstand: "", vergleich: [], abweichungen: [], neue_schaeden: [],
  schluessel: "2", schluessel_vereinbart: "2", schluessel_vereinbart_fehlt: false,
  preis_vertrag: 10000, rueckfrage_antworten: [], rueckfrage_verlauf: [],
};
const VOR_ORT = {
  ausstattung: [{ name: "Sitzheizung", art: "fehlt", befund: "fehlt komplett" },
                { name: "Anhängerkupplung", art: "defekt", befund: "vorhanden, defekt" },
                { name: "Klimaautomatik", art: "offen", befund: "keine Angabe" }],
  dokumente: [{ name: "Zweitsatz Reifen", art: "fehlt", befund: "fehlt" }],
  zustand: [{ name: "Batterie / Starter", schluessel: "battery", art: "mangel", befund: "schwach" },
            { name: "Sauberkeit Außen", schluessel: "clean_outside", art: "hinweis", befund: "mittel" }],
  anzahl: 4, hinweise: 1, offen: 1,
};

let liste;
let wurzel;
let behaelter;
const el = (id) => behaelter.querySelector(`[data-testid="${id}"]`);
async function starten() {
  behaelter = document.createElement("div");
  document.body.appendChild(behaelter);
  wurzel = createRoot(behaelter);
  await act(async () => { wurzel.render(createElement(Freigaben)); });
  for (let i = 0; i < 3; i += 1) await act(async () => { await new Promise((r) => setTimeout(r, 0)); });
}

beforeEach(() => {
  api.get.mockImplementation((url) => (url.includes("rueckfragen-offen")
    ? Promise.resolve({ data: [] })
    : Promise.resolve({ data: liste, headers: {} })));
  api.post.mockResolvedValue({ data: { ok: true } });
  try { window.localStorage.clear(); } catch { /* egal */ }
});
afterEach(async () => {
  if (wurzel) await act(async () => { wurzel.unmount(); });
  wurzel = null; behaelter?.remove(); vi.clearAllMocks();
});

describe("Freigabe: Vor Ort festgestellt", () => {
  it("listet fehlende/defekte Ausstattung, fehlende Unterlagen, Mängel, Hinweise und offene Angaben", async () => {
    liste = [{ ...BASIS, vor_ort: VOR_ORT }];
    await starten();
    const block = el("vor-ort-p1");
    expect(block).toBeTruthy();
    expect(block.textContent).toContain("Vor Ort festgestellt: 4 Abweichungen · 1 Hinweis · 1 ohne Angabe");
    expect(el("vor-ort-ausstattung-p1").textContent).toContain("Sitzheizung: fehlt komplett");
    expect(el("vor-ort-ausstattung-p1").textContent).toContain("Anhängerkupplung: vorhanden, defekt");
    expect(el("vor-ort-ausstattung-p1").textContent).toContain("Klimaautomatik: keine Angabe");
    expect(el("vor-ort-dokumente-p1").textContent).toContain("Zweitsatz Reifen: fehlt");
    expect(el("vor-ort-zustand-p1").textContent).toContain("Batterie / Starter: schwach");
    expect(el("vor-ort-zustand-p1").textContent).toContain("Sauberkeit Außen: mittel");
    expect(el("vor-ort-ok-p1")).toBeNull();
  });

  it("zeigt dem Chef auch den kompletten Ausstattungsstand und die Ausweisnummer", async () => {
    const ausstattung = Object.fromEntries(Array.from({ length: 120 }, (_, i) => [`Feature ${i}`, true]));
    ausstattung["Feature 119"] = "defekt";
    liste = [{
      ...BASIS,
      vor_ort: { ausstattung: [{ name: "Feature 119", art: "defekt", befund: "vorhanden, defekt" }],
                 dokumente: [], zustand: [], anzahl: 1, hinweise: 0, offen: 0 },
      ausstattung,
      ausstattung_gesamt: 120,
      ausstattung_beantwortet: 120,
      ausstattung_vorhanden: 119,
      verkaeufer_ausweis: "L01X00T47",
    }];
    await starten();
    expect(el("freigabe-ausstattung-komplett-p1").textContent).toContain("120/120");
    expect(el("freigabe-ausstattung-komplett-p1").textContent).toContain("vorhanden 119");
    expect(el("freigabe-ausweis-p1").textContent).toContain("L01X00T47");
  });

  it("ohne Befund: grüne Zeile; ohne Feld (älteres Backend): nichts", async () => {
    liste = [{ ...BASIS, vor_ort: { ausstattung: [], dokumente: [], zustand: [], anzahl: 0, hinweise: 0, offen: 0 } }];
    await starten();
    expect(el("vor-ort-ok-p1").textContent).toContain("ohne Befund");
    expect(el("vor-ort-p1")).toBeNull();
    await act(async () => { wurzel.unmount(); });
    wurzel = null; behaelter.remove();
    liste = [{ ...BASIS }];
    await starten();
    expect(el("vor-ort-ok-p1")).toBeNull();
    expect(el("vor-ort-p1")).toBeNull();
    expect(el("freigabe-p1")).toBeTruthy();
  });
});
