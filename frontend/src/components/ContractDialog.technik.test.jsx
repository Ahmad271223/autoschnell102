/**
 * Wunsch Ahmad 09.10.2026: beim Vertrag-Erstellen fragt der Dialog nach Motor, Getriebe und Kupplung.
 * Wunsch Ahmad 13.10.2026: daraus EIN freiwilliger Punkt „Motor / Getriebe / Kupplung“ (`technik_zustand`,
 * „in Ordnung“ oder „Schaden vorhanden“, leer = keine Angabe; Schadentext bei „Schaden vorhanden“). Altverträge mit
 * den drei Einzelfeldern zeigen beim Bearbeiten den zusammengefassten Stand.
 */
import { act, createElement } from "react";
import { createRoot } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

const post = vi.fn();
const get = vi.fn();
const refresh = vi.fn();
const DEALER = {
  company_name: "Käufer GmbH", address: "Weg 1", zip_code: "10115", city: "Berlin",
  default_terms: "", digital_vertragstext: "", digital_vertragstext_standard: "Standard",
};

vi.mock("sonner", () => ({ toast: { success: vi.fn(), error: vi.fn(), info: vi.fn(), warning: vi.fn() } }));
vi.mock("@/lib/api", () => ({
  api: { get: (...a) => get(...a), post: (...a) => post(...a), put: vi.fn(), delete: vi.fn() },
  errMsg: (e, f) => e?.response?.data?.detail || e?.message || f,
}));
vi.mock("@/context/AuthContext", () => ({
  useAuth: () => ({ dealer: DEALER, user: { id: "u1", role: "sucher" }, refresh }),
}));
vi.mock("@/components/MarktdatenKarte", () => ({ useMarktHinweis: () => null }));
vi.mock("./KiSchadenKarte", () => ({ default: () => null }));
vi.mock("./DamageSelector", () => ({ default: () => null, damagesToText: () => "" }));
vi.mock("@/lib/pdf", () => ({ openContractPdf: vi.fn() }));
vi.mock("@/lib/dateiOeffnen", () => ({ blobOeffnen: vi.fn() }));
vi.mock("@/lib/ungespeichert", () => ({ useUngespeichert: () => {} }));

globalThis.IS_REACT_ACT_ENVIRONMENT = true;

const { default: ContractDialog, anfangsFormular, formularAusVertrag, technikGesamt, TECHNIK_FELD, TECHNIK_FELDER } =
  await import("./ContractDialog");

let wurzel = null;
let behaelter = null;
function rendern(el) {
  behaelter = document.createElement("div");
  document.body.appendChild(behaelter);
  wurzel = createRoot(behaelter);
  act(() => { wurzel.render(el); });
  return behaelter;
}
afterEach(() => {
  if (wurzel) act(() => wurzel.unmount());
  behaelter?.remove();
  wurzel = null;
  behaelter = null;
});
beforeEach(() => {
  post.mockReset();
  get.mockReset();
  refresh.mockReset();
  get.mockResolvedValue({ data: null });
  refresh.mockResolvedValue({ dealer: DEALER });
  post.mockResolvedValue({ data: { id: "c1", contract_no: "KV-1" } });
  window.sessionStorage.clear();
});

function tippen(input, wert) {
  const proto = input.tagName === "SELECT" ? HTMLSelectElement.prototype : HTMLInputElement.prototype;
  const setter = Object.getOwnPropertyDescriptor(proto, "value").set;
  act(() => {
    setter.call(input, wert);
    input.dispatchEvent(new Event(input.tagName === "SELECT" ? "change" : "input", { bubbles: true }));
  });
}
const feld = (id) => behaelter.querySelector(`[data-testid="${id}"]`);
const FAHRZEUG = { seller_name: "Vera Verkauf", make_label: "BMW", model_label: "320d", seller_email: "vera@beispiel.de" };

async function absenden() {
  const form = behaelter.querySelector("form");
  await act(async () => {
    form.dispatchEvent(new Event("submit", { bubbles: true, cancelable: true }));
    await Promise.resolve();
    await Promise.resolve();
  });
}

describe("Motor / Getriebe / Kupplung im Vertragsdialog (ein Punkt, freiwillig)", () => {
  it("reine Helfer: Startwert leer, ein Feld, Zusammenfassung alter Verträge", () => {
    const f = anfangsFormular({}, DEALER, "2026-10-13");
    expect([f.technik_zustand, f.technik_schaden_text]).toEqual(["", ""]);
    expect(TECHNIK_FELD).toBe("technik_zustand");
    expect(TECHNIK_FELDER.map(([k]) => k)).toEqual(["technik_zustand"]);
    // neuer Vertrag
    const neu = formularAusVertrag(f, { contract_no: "KV-9", contract_data: {
      technik_zustand: "Schaden vorhanden", technik_schaden_text: "springt raus" } });
    expect([neu.technik_zustand, neu.technik_schaden_text]).toEqual(["Schaden vorhanden", "springt raus"]);
    // Altvertrag mit drei Einzelfeldern: ein Schaden zaehlt als Schaden …
    const alt = formularAusVertrag(f, { contract_no: "KV-8", contract_data: {
      motor_zustand: "in Ordnung", getriebe_zustand: "Schaden vorhanden", kupplung_zustand: "in Ordnung",
      technik_schaden_text: "Ölverlust" } });
    expect(alt.technik_zustand).toBe("Schaden vorhanden");
    expect(alt.getriebe_zustand).toBe("Schaden vorhanden");           // bleibt erhalten (geht unveraendert mit)
    // … alles in Ordnung bleibt in Ordnung, nichts angegeben bleibt leer
    expect(technikGesamt({ motor_zustand: "in Ordnung", kupplung_zustand: "in Ordnung" })).toBe("in Ordnung");
    expect(technikGesamt({})).toBe("");
    expect(technikGesamt({ technik_zustand: "in Ordnung", getriebe_zustand: "Schaden vorhanden" })).toBe("in Ordnung");
  });

  it("beim Erstellen: ein freiwilliges Feld, Schadentext erst bei „Schaden vorhanden“, Payload passt", async () => {
    rendern(createElement(ContractDialog, { open: true, onClose: () => {}, vehicleId: "v1", onCreated: () => {}, vehicle: FAHRZEUG }));
    await act(async () => { await Promise.resolve(); });
    const sel = feld("contract-technik-zustand");
    expect(sel).not.toBeNull();
    expect(sel.required).toBe(false);
    expect(sel.value).toBe("");
    expect([...sel.options].map((o) => o.value)).toEqual(["", "in Ordnung", "Schaden vorhanden"]);
    expect(feld("contract-motor-zustand")).toBeNull();
    expect(feld("contract-getriebe-zustand")).toBeNull();
    expect(feld("contract-kupplung-zustand")).toBeNull();
    expect(feld("contract-technik-schaden")).toBeNull();
    // ohne Angabe geht der Vertrag durch (freiwillig)
    tippen(feld("contract-price"), "4.500");
    tippen(feld("contract-payment"), "Bar");
    await absenden();
    expect(post).toHaveBeenCalledTimes(1);
    expect(post.mock.calls[0][1].technik_zustand).toBe("");
    // mit Schaden: Text erscheint und geht mit
    tippen(sel, "Schaden vorhanden");
    expect(feld("contract-technik-schaden")).not.toBeNull();
    tippen(feld("contract-technik-schaden"), "springt im 3. Gang raus");
    await absenden();
    const body = post.mock.calls[1][1];
    expect(body.technik_zustand).toBe("Schaden vorhanden");
    expect(body.technik_schaden_text).toBe("springt im 3. Gang raus");
    tippen(sel, "in Ordnung");
    expect(feld("contract-technik-schaden")).toBeNull();
  });

  it("beim nachträglichen Ändern: gespeicherter Stand drin, auch aus einem Altvertrag", async () => {
    rendern(createElement(ContractDialog, {
      open: true, onClose: () => {}, vehicleId: "v1", onCreated: () => {},
      vehicle: { make_label: "BMW", model_label: "320d" },
      vertrag: { id: "c1", contract_no: "KV-1", contract_data: {
        seller_name: "Vera", purchase_price: 4500, payment_method: "Bar",
        motor_zustand: "Schaden vorhanden", getriebe_zustand: "in Ordnung", kupplung_zustand: "in Ordnung",
        technik_schaden_text: "Ölverlust" } },
    }));
    await act(async () => { await Promise.resolve(); });
    expect(feld("contract-technik-zustand").value).toBe("Schaden vorhanden");
    expect(feld("contract-technik-zustand").required).toBe(false);
    expect(feld("contract-technik-schaden").value).toBe("Ölverlust");
  });
});
