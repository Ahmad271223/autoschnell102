/**
 * Startprüfung 27.09.2026 (K5): Ausstattung mit „fehlt“/„defekt“/„anders“ ist
 * ein Nein mit Grund — auch in der gesperrten Ansicht (ab „zur Freigabe“),
 * damit der Fahrer genau das sieht, was im PDF unterschrieben wird.
 */
import { act, createElement } from "react";
import { createRoot } from "react-dom/client";
import { afterEach, describe, expect, it, vi } from "vitest";

globalThis.IS_REACT_ACT_ENVIRONMENT = true;

const api = vi.hoisted(() => ({ get: vi.fn(), post: vi.fn(), put: vi.fn() }));
const toastMock = vi.hoisted(() => ({ error: vi.fn(), success: vi.fn(), warning: vi.fn(), info: vi.fn() }));

vi.mock("@/context/DriverContext", () => ({ driverApi: api, openDriverPdf: vi.fn() }));
vi.mock("sonner", () => ({ toast: toastMock }));
vi.mock("react-router-dom", () => ({ useParams: () => ({ id: "t1" }), useNavigate: () => vi.fn() }));
vi.mock("@/lib/ungespeichert", () => ({ useUngespeichert: () => {} }));
vi.mock("@/components/DamageSelector", () => ({ default: () => null }));
vi.mock("@/components/MonatJahrEingabe", () => ({ default: () => null }));
vi.mock("@/components/SignaturePad", () => ({ default: () => null }));
vi.mock("@/components/KiFahrerKarte", () => ({ default: () => null }));

const { default: Protokoll } = await import("./Protokoll");

const FEATURES = { Sitzheizung: "fehlt", Navigationssystem: "defekt", Tempomat: "anders",
                   Klimaanlage: true, Alufelgen: false };

function antwort(status) {
  return {
    data: {
      protocol: { id: "p1", version: 1, status, revision: 1, freigabe_stand: "s1",
                  neuer_preis: null, preis_notiz: "", features: FEATURES },
      template: { features: Object.keys(FEATURES) }, vehicle: {}, damages: [], preis_vertrag: 10000,
      appointment: { seller_name: "Vera Termin", status: "offen" },
    },
  };
}

let wurzel;
let behaelter;
const el = (testId) => behaelter.querySelector(`[data-testid="${testId}"]`);

async function starten(daten) {
  api.get.mockResolvedValue(daten);
  behaelter = document.createElement("div");
  document.body.appendChild(behaelter);
  wurzel = createRoot(behaelter);
  await act(async () => { wurzel.render(createElement(Protokoll)); });
  await act(async () => { await new Promise((r) => setTimeout(r, 0)); });
  await act(async () => { await new Promise((r) => setTimeout(r, 0)); });
}

afterEach(async () => {
  if (wurzel) await act(async () => { wurzel.unmount(); });
  wurzel = null;
  behaelter?.remove();
  try { window.sessionStorage.clear(); } catch { /* egal */ }
  vi.clearAllMocks();
});

describe("Protokoll.jsx: Grund eines Ausstattungs-Nein", () => {
  it("gesperrt (zur Freigabe): Grund als Text, keine Auswahlknöpfe", async () => {
    await starten(antwort("zur_freigabe"));
    expect(el("protokoll-ausstattung-grund-Sitzheizung").textContent).toBe("Nein — fehlt komplett");
    expect(el("protokoll-ausstattung-grund-Navigationssystem").textContent).toBe("Nein — vorhanden, defekt");
    expect(el("protokoll-ausstattung-grund-Tempomat").textContent).toBe("Nein — anders als beschrieben");
    // Ja und ein altes Nein ohne Grund (False) bekommen keine Grundzeile
    expect(el("protokoll-ausstattung-grund-Klimaanlage")).toBeNull();
    expect(el("protokoll-ausstattung-grund-Alufelgen")).toBeNull();
    expect(el("protokoll-ausstattung-art-Sitzheizung")).toBeNull();
  });

  it("Entwurf: Auswahlknöpfe statt Grundzeile", async () => {
    await starten(antwort("entwurf"));
    expect(el("protokoll-ausstattung-grund-Sitzheizung")).toBeNull();
    expect(el("protokoll-ausstattung-art-Sitzheizung")).toBeTruthy();
    expect(el("protokoll-ausstattung-Sitzheizung-fehlt").textContent).toBe("fehlt komplett");
  });

  it("Entwurf: alle Ausstattungen lassen sich mit einem Klick auf Ja setzen", async () => {
    const viele = Object.fromEntries(Array.from({ length: 120 }, (_, i) => [`Feature ${i}`, undefined]));
    const daten = antwort("entwurf");
    daten.data.protocol.features = {};
    daten.data.template.features = Object.keys(viele);
    await starten(daten);
    expect(el("protokoll-ausstattung-alle-ja")).toBeTruthy();
    expect(el("protokoll-ausstattung-alle-ja").textContent).toContain("Alle auf Ja");
    await act(async () => { el("protokoll-ausstattung-alle-ja").click(); });
    expect(behaelter.textContent).toContain("120 Ausstattungen");
  });
});
