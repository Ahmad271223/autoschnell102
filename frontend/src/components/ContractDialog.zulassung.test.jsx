/**
 * Wunsch Ahmad 24.11.2026: „Zulassung bei Übergabe“ — abgemeldet / angemeldet / unbekannt (Standard).
 * Bei unbekannt ein gelber Hinweis; der Satz im Vertrag kommt vom Server (vertrag_zulassung.py).
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

const { default: ContractDialog, anfangsFormular, formularAusVertrag } = await import("./ContractDialog");
const { ZUSICHERUNG_STANDARD, zusicherungenOhneWahlLeeren } = await import("@/lib/kiSchaden");

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
  const proto = input.tagName === "SELECT" ? HTMLSelectElement.prototype
    : input.tagName === "TEXTAREA" ? HTMLTextAreaElement.prototype : HTMLInputElement.prototype;
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

describe("Zulassung bei Übergabe (Wunsch Ahmad 24.11.2026)", () => {
  it("Standard unbekannt mit Hinweis, nach der Auswahl ist der Hinweis weg, Wert geht an den Server", async () => {
    expect(anfangsFormular({}, DEALER, "2026-11-24").zulassung).toBe("");
    rendern(createElement(ContractDialog, { open: true, onClose: () => {}, vehicleId: "v1", onCreated: () => {}, vehicle: FAHRZEUG }));
    await act(async () => { await Promise.resolve(); });
    const z = feld("contract-zulassung");
    expect(z).not.toBeNull();
    expect(z.value).toBe("");
    expect([...z.options].map((o) => o.label)).toEqual(["Unbekannt", "Angemeldet", "Abgemeldet"]);
    expect([...z.options].map((o) => o.value)).toEqual(["", "angemeldet", "abgemeldet"]);
    expect(behaelter.textContent).toContain("Zulassung bei Übergabe");
    const hinweis = feld("contract-zulassung-hinweis");
    expect(hinweis).not.toBeNull();
    expect(hinweis.textContent).toBe("Bitte klären, ob das Auto angemeldet ist.");
    expect(hinweis.getAttribute("style")).toContain("var(--st-amber)");
    tippen(z, "abgemeldet");
    expect(feld("contract-zulassung-hinweis")).toBeNull();
    tippen(feld("contract-price"), "4.500");
    tippen(feld("contract-payment"), "Bar");
    await absenden();
    expect(post).toHaveBeenCalledTimes(1);
    expect(post.mock.calls[0][1].zulassung).toBe("abgemeldet");
  });

  it("unbekannt hält das Erstellen nicht auf (Feld freiwillig)", async () => {
    rendern(createElement(ContractDialog, { open: true, onClose: () => {}, vehicleId: "v1", onCreated: () => {}, vehicle: FAHRZEUG }));
    await act(async () => { await Promise.resolve(); });
    tippen(feld("contract-price"), "4.500");
    tippen(feld("contract-payment"), "Bar");
    await absenden();
    expect(post).toHaveBeenCalledTimes(1);
    expect(post.mock.calls[0][1].zulassung).toBe("");
  });

  it("bestehender Vertrag: gespeicherter Wert steht wieder im Feld, Altvertrag ohne Feld = unbekannt", () => {
    const leer = anfangsFormular({}, DEALER, "2026-11-24");
    expect(formularAusVertrag(leer, { contract_no: "KV-1", contract_data: { zulassung: "angemeldet" } }).zulassung).toBe("angemeldet");
    expect(formularAusVertrag(leer, { contract_no: "KV-2", contract_data: {} }).zulassung || "").toBe("");
  });
});