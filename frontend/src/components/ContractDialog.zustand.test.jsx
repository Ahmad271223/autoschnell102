/**
 * Wunsch Ahmad 04.11.2026: Scheckheftgepflegt „Ja, lückenlos“, Fahrtauglich „Ja“ und Gewerblich genutzt seit EZ
 * „Nein“ stehen immer automatisch im Dialog (änderbar); bei Motor/Getriebe/Kupplung heißt es „alles in Ordnung“;
 * neben Unfallfrei gibt es den neuen Punkt „Nachlackierungsfrei“ (Ja/Nein).
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

describe("Standardwerte, „alles in Ordnung“ und Nachlackierungsfrei (Wunsch Ahmad 04.11.2026)", () => {
  it("Startwerte: Scheckheft ja, Fahrtauglich Ja, Gewerblich Nein, Nachlackierungsfrei leer", () => {
    const f = anfangsFormular({}, DEALER, "2026-11-04");
    expect([f.service_book, f.drivable, f.commercial_since_ez, f.nachlackierungsfrei]).toEqual(["ja", "Ja", "Nein", ""]);
    expect(f.eu_import).toBe("Nein");  // Wunsch Ahmad 25.11.2026
    expect(ZUSICHERUNG_STANDARD).toEqual({ service_book: "ja", drivable: "Ja", commercial_since_ez: "Nein", eu_import: "Nein" });
    // bestehender Vertrag: was gespeichert ist, gilt — auch leer; ohne das neue Feld bleibt es leer
    const alt = formularAusVertrag(f, { contract_no: "KV-1", contract_data: { drivable: "", service_book: "nein",
                                                                            commercial_since_ez: "" } });
    expect([alt.drivable, alt.service_book, alt.commercial_since_ez, alt.nachlackierungsfrei]).toEqual(["", "nein", "", ""]);
    // Entwurf wiederherstellen: die Standardwerte gelten nicht als Inserat-Vorbelegung und bleiben stehen
    const entwurf = { service_book: "ja", drivable: "Ja", accident_free: "Ja", hu_valid: "", hu_until: "" };
    expect(zusicherungenOhneWahlLeeren(entwurf, {})).toEqual({ ...entwurf, accident_free: "" });
  });

  it("im Dialog: Felder vorbelegt, Nachlackierungsfrei neben Unfallfrei, Wortlaut „alles in Ordnung“, Payload", async () => {
    rendern(createElement(ContractDialog, { open: true, onClose: () => {}, vehicleId: "v1", onCreated: () => {}, vehicle: FAHRZEUG }));
    await act(async () => { await Promise.resolve(); });
    expect(feld("contract-service-book").value).toBe("ja");
    expect(feld("contract-drivable").value).toBe("Ja");
    expect(feld("contract-commercial").value).toBe("Nein");
    const nl = feld("contract-nachlackierungsfrei");
    expect(nl).not.toBeNull();
    expect(nl.value).toBe("");
    expect([...nl.options].map((o) => o.value)).toEqual(["", "Ja", "Nein"]);
    expect(nl.closest(".grid")).toBe(feld("contract-accident-free").closest(".grid"));
    const technik = feld("contract-technik-zustand");
    expect([...technik.options].map((o) => o.label)).toEqual(["— keine Angabe —", "alles in Ordnung", "Schaden vorhanden"]);
    expect([...technik.options].map((o) => o.value)).toEqual(["", "in Ordnung", "Schaden vorhanden"]);
    expect(behaelter.textContent).toContain("alles in Ordnung oder Schaden");
    tippen(nl, "Nein");
    tippen(technik, "in Ordnung");
    tippen(feld("contract-price"), "4.500");
    tippen(feld("contract-payment"), "Bar");
    await absenden();
    expect(post).toHaveBeenCalledTimes(1);
    const body = post.mock.calls[0][1];
    expect([body.service_book, body.drivable, body.commercial_since_ez]).toEqual(["ja", "Ja", "Nein"]);
    expect(body.nachlackierungsfrei).toBe("Nein");
    expect(body.technik_zustand).toBe("in Ordnung");
  });
});

describe("Sonstige Vereinbarung (Wunsch Ahmad 04.11.2026)", () => {
  it("eigenes Feld, leer zu Beginn, geht mit dem Vertrag an den Server", async () => {
    expect(anfangsFormular({}, DEALER, "2026-11-04").sonstige_vereinbarung).toBe("");
    rendern(createElement(ContractDialog, { open: true, onClose: () => {}, vehicleId: "v1", onCreated: () => {}, vehicle: FAHRZEUG }));
    await act(async () => { await Promise.resolve(); });
    const sv = feld("contract-sonstige-vereinbarung");
    expect(sv).not.toBeNull();
    expect(sv.value).toBe("");
    expect(behaelter.textContent).toContain("Sonstige Vereinbarung (im Vertrag unter „Zusätzliche Vereinbarung“)");
    expect(behaelter.textContent).toContain("Sonstige Schäden (erscheint im Vertrag bei den Fahrzeugdaten)");
    tippen(sv, "8-fach bereift, Winterräder auf Felgen");
    tippen(feld("contract-price"), "4.500");
    tippen(feld("contract-payment"), "Bar");
    await absenden();
    expect(post).toHaveBeenCalledTimes(1);
    expect(post.mock.calls[0][1].sonstige_vereinbarung).toBe("8-fach bereift, Winterräder auf Felgen");
    // Bearbeiten: der gespeicherte Text steht wieder im Feld
    const f = formularAusVertrag(anfangsFormular({}, DEALER, "2026-11-04"),
                                 { contract_no: "KV-1", contract_data: { sonstige_vereinbarung: "2 Schlüssel" } });
    expect(f.sonstige_vereinbarung).toBe("2 Schlüssel");
  });
});
