/**
 * Wunsch Ahmad 26.09.2026 abends: Vertragsnummer und Kundennummer im
 * Kaufvertrag selbst vergeben — Abschnitt „Nummern“ im Dialog, beide Felder
 * gehen im Payload an POST /contracts (leer = automatisch bzw. Firmenwert),
 * Kundennummer mit der Firmen-Kundennummer vorbelegt, 409 „Vertragsnummer
 * bereits vergeben“ steht unter dem Feld.
 */
import { act, createElement } from "react";
import { createRoot } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

const post = vi.fn();
const get = vi.fn();
const refresh = vi.fn();
const DEALER = {
  company_name: "Käufer GmbH", address: "Weg 1", zip_code: "10115", city: "Berlin",
  vertrags_kundennummer: "482913", default_terms: "", digital_vertragstext: "",
  digital_vertragstext_standard: "Standard", sondervereinbarungen_effektiv: "Nr. {kundennummer}",
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

const { default: ContractDialog, anfangsFormular, nummernFehlerAusAntwort } = await import("./ContractDialog");
const { default: QUELLE } = await import("./ContractDialog.jsx?raw");

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

async function dialogOeffnenUndAusfuellen() {
  rendern(createElement(ContractDialog, {
    open: true, onClose: () => {}, vehicleId: "v1", onCreated: () => {},
    vehicle: { seller_name: "Vera Verkauf", make_label: "BMW", model_label: "320d" },
  }));
  await act(async () => { await Promise.resolve(); });
  tippen(feld("contract-price"), "4.500");
  tippen(feld("contract-payment"), "Bar");
}

async function absenden() {
  const form = behaelter.querySelector("form");
  await act(async () => {
    form.dispatchEvent(new Event("submit", { bubbles: true, cancelable: true }));
    await Promise.resolve();
    await Promise.resolve();
  });
}

describe("Nummern: Vorbelegung und Fehlertext (reine Helfer)", () => {
  it("anfangsFormular: Vertragsnummer leer (= automatisch), Kundennummer = Firmenwert", () => {
    const f = anfangsFormular({}, DEALER, "2026-09-26");
    expect(f.contract_no).toBe("");
    expect(f.kundennummer).toBe("482913");
    expect(anfangsFormular({}, {}, "2026-09-26").kundennummer).toBe("");
    expect(anfangsFormular({}, null, "2026-09-26").kundennummer).toBe("");
  });
  it("nummernFehlerAusAntwort: nur der 409 zur Vertragsnummer", () => {
    const text = "Vertragsnummer bereits vergeben: „AH-1“ trägt in eurer Firma schon ein anderer Kaufvertrag";
    expect(nummernFehlerAusAntwort({ response: { status: 409, data: { detail: text } } })).toBe(text);
    expect(nummernFehlerAusAntwort({ response: { status: 409, data: { detail: { code: "vertrag_vorhanden" } } } })).toBe("");
    expect(nummernFehlerAusAntwort({ response: { status: 422, data: { detail: text } } })).toBe("");
    expect(nummernFehlerAusAntwort(new Error("Network Error"))).toBe("");
  });
  it("Quelltext: Abschnitt „Nummern“ mit beiden Feldern, Payload nimmt das ganze Formular", () => {
    const block = QUELLE.slice(QUELLE.indexOf('<Section title="Nummern"'), QUELLE.indexOf('title="Verkäufer / Halter"'));
    expect(block).toMatch(/testid="contract-vertragsnummer"/);
    expect(block).toMatch(/testid="contract-kundennummer"/);
    expect(block).toMatch(/maxLength=\{40\}/);
    expect(block).toMatch(/maxLength=\{30\}/);
    expect(block).toContain('data-testid="contract-nummern-fehler"');
    expect(block).toContain('color: "var(--accent-red)"');
    expect(QUELLE).toMatch(/const buildPayload = \(\) => \(\{\s*vehicle_id: vehicleId,\s*\.\.\.form,/);
  });
});

describe("Nummern: im Dialog", () => {
  it("Felder stehen im Dialog, Kundennummer vorbelegt, beide gehen im Payload an /contracts", async () => {
    post.mockResolvedValue({ data: { id: "c1", contract_no: "AH-2026-0042" } });
    await dialogOeffnenUndAusfuellen();
    expect(feld("contract-kundennummer").value).toBe("482913");
    expect(feld("contract-vertragsnummer").value).toBe("");
    tippen(feld("contract-vertragsnummer"), "AH-2026-0042");
    tippen(feld("contract-kundennummer"), "K-77");
    await absenden();
    const aufruf = post.mock.calls.find((c) => c[0] === "/contracts");
    expect(aufruf, "POST /contracts").toBeTruthy();
    expect(aufruf[1].contract_no).toBe("AH-2026-0042");
    expect(aufruf[1].kundennummer).toBe("K-77");
    expect(aufruf[1].purchase_price).toBe(4500);
    expect(feld("contract-nummern-fehler")).toBeNull();
  });
  it("leer gelassen: leere Vertragsnummer (= automatisch) und Firmen-Kundennummer im Payload", async () => {
    post.mockResolvedValue({ data: { id: "c2" } });
    await dialogOeffnenUndAusfuellen();
    await absenden();
    const aufruf = post.mock.calls.find((c) => c[0] === "/contracts");
    expect(aufruf[1].contract_no).toBe("");
    expect(aufruf[1].kundennummer).toBe("482913");
  });
  it("409 „Vertragsnummer bereits vergeben“ steht unter dem Feld und verschwindet beim Ändern", async () => {
    const text = "Vertragsnummer bereits vergeben: „AH-1“ trägt in eurer Firma schon ein anderer Kaufvertrag — bitte eine andere Nummer wählen.";
    post.mockRejectedValue({ response: { status: 409, data: { detail: text } } });
    await dialogOeffnenUndAusfuellen();
    tippen(feld("contract-vertragsnummer"), "AH-1");
    await absenden();
    expect(feld("contract-nummern-fehler")).not.toBeNull();
    expect(feld("contract-nummern-fehler").textContent).toBe(text);
    tippen(feld("contract-vertragsnummer"), "AH-2");
    expect(feld("contract-nummern-fehler")).toBeNull();
  });
});
