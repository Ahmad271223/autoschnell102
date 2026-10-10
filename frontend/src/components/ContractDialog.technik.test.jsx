/**
 * Wunsch Ahmad 09.10.2026: beim Vertrag-Erstellen fragt der Dialog nach Motor, Getriebe und Kupplung
 * („in Ordnung“ oder „Schaden vorhanden“, Pflicht beim Erstellen, Schadentext bei „Schaden vorhanden“);
 * beim nachträglichen Ändern stehen die gespeicherten Werte drin und sind keine Pflicht.
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

const { default: ContractDialog, anfangsFormular, formularAusVertrag, TECHNIK_FELDER } = await import("./ContractDialog");

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

describe("Motor / Getriebe / Kupplung im Vertragsdialog", () => {
  it("reine Helfer: Startwerte leer, Felderliste, Übernahme aus dem Vertrag", () => {
    const f = anfangsFormular({}, DEALER, "2026-10-09");
    expect([f.motor_zustand, f.getriebe_zustand, f.kupplung_zustand, f.technik_schaden_text]).toEqual(["", "", "", ""]);
    expect(TECHNIK_FELDER.map(([k]) => k)).toEqual(["motor_zustand", "getriebe_zustand", "kupplung_zustand"]);
    const aus = formularAusVertrag(f, { contract_no: "KV-9", contract_data: {
      motor_zustand: "in Ordnung", getriebe_zustand: "Schaden vorhanden", kupplung_zustand: "in Ordnung",
      technik_schaden_text: "springt raus" } });
    expect([aus.motor_zustand, aus.getriebe_zustand, aus.kupplung_zustand, aus.technik_schaden_text])
      .toEqual(["in Ordnung", "Schaden vorhanden", "in Ordnung", "springt raus"]);
  });

  it("beim Erstellen: drei Pflichtfragen, Schadentext erst bei „Schaden vorhanden“, alles geht im Payload mit", async () => {
    rendern(createElement(ContractDialog, { open: true, onClose: () => {}, vehicleId: "v1", onCreated: () => {}, vehicle: FAHRZEUG }));
    await act(async () => { await Promise.resolve(); });
    for (const id of ["contract-motor-zustand", "contract-getriebe-zustand", "contract-kupplung-zustand"]) {
      expect(feld(id)).not.toBeNull();
      expect(feld(id).required).toBe(true);
      expect(feld(id).value).toBe("");
      expect([...feld(id).options].map((o) => o.value)).toEqual(["", "in Ordnung", "Schaden vorhanden"]);
    }
    expect(feld("contract-technik-schaden")).toBeNull();
    tippen(feld("contract-motor-zustand"), "in Ordnung");
    tippen(feld("contract-getriebe-zustand"), "Schaden vorhanden");
    tippen(feld("contract-kupplung-zustand"), "in Ordnung");
    expect(feld("contract-technik-schaden")).not.toBeNull();
    tippen(feld("contract-technik-schaden"), "springt im 3. Gang raus");
    tippen(feld("contract-price"), "4.500");
    tippen(feld("contract-payment"), "Bar");
    await absenden();
    expect(post).toHaveBeenCalled();
    const body = post.mock.calls[0][1];
    expect(body.motor_zustand).toBe("in Ordnung");
    expect(body.getriebe_zustand).toBe("Schaden vorhanden");
    expect(body.kupplung_zustand).toBe("in Ordnung");
    expect(body.technik_schaden_text).toBe("springt im 3. Gang raus");
  });

  it("beim nachträglichen Ändern: gespeicherte Werte stehen drin, keine Pflicht", async () => {
    rendern(createElement(ContractDialog, {
      open: true, onClose: () => {}, vehicleId: "v1", onCreated: () => {},
      vehicle: { make_label: "BMW", model_label: "320d" },
      vertrag: { id: "c1", contract_no: "KV-1", contract_data: {
        seller_name: "Vera", purchase_price: 4500, payment_method: "Bar",
        motor_zustand: "Schaden vorhanden", getriebe_zustand: "in Ordnung", kupplung_zustand: "in Ordnung",
        technik_schaden_text: "Ölverlust" } },
    }));
    await act(async () => { await Promise.resolve(); });
    expect(feld("contract-motor-zustand").value).toBe("Schaden vorhanden");
    expect(feld("contract-motor-zustand").required).toBe(false);
    expect(feld("contract-technik-schaden").value).toBe("Ölverlust");
  });
});
