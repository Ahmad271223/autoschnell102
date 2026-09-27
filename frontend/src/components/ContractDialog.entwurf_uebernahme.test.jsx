/**
 * Nachprüfung 28.09.2026 (inserat2, Nr. 3): Wurde der Entwurf mit aus dem
 * Inserat vorbelegten Zusicherungen gespeichert und der Dialog erneut
 * geöffnet, fehlten die Rückfrage vor "PDF erstellen" und der Kasten "Aus dem
 * Inserat übernommen (bitte prüfen)" — die Felder waren schon gefüllt, also
 * fand vorschlaegeAnwenden nichts mehr. Die Übernahmeliste geht jetzt mit dem
 * Entwurf mit (nur Felder, deren Wert noch dem übernommenen entspricht).
 */
import { act, createElement } from "react";
import { createRoot } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

globalThis.IS_REACT_ACT_ENVIRONMENT = true;

const api = vi.hoisted(() => ({ get: vi.fn(), post: vi.fn(), put: vi.fn(), delete: vi.fn() }));
const toastMock = vi.hoisted(() => ({
  success: vi.fn(), error: vi.fn(), info: vi.fn(), warning: vi.fn(), message: vi.fn(),
}));
const auth = vi.hoisted(() => ({
  dealer: { company_name: "Autohaus Test", address: "Weg 1", zip_code: "12345", city: "Berlin" },
  refresh: undefined,
  user: { id: "U1" },
}));

vi.mock("@/lib/api", () => ({
  api, errMsg: (e, f) => e?.response?.data?.detail || f || "Fehler",
}));
vi.mock("@/context/AuthContext", () => ({ useAuth: () => auth }));
vi.mock("@/components/MarktdatenKarte", () => ({ useMarktHinweis: () => null }));
vi.mock("@/lib/pdf", () => ({ openContractPdf: vi.fn() }));
vi.mock("@/lib/dateiOeffnen", () => ({ blobOeffnen: vi.fn() }));
vi.mock("@/lib/ungespeichert", () => ({ useUngespeichert: () => {} }));
vi.mock("sonner", () => ({ toast: toastMock }));
vi.mock("./KiSchadenKarte", () => ({ default: () => null }));
vi.mock("./DamageSelector", () => ({ default: () => null, damagesToText: () => "" }));

const { default: ContractDialog, entwurfSchluessel, entwurfSpeichern } = await import("./ContractDialog.jsx");
const { uebernahmenAusEntwurf, uebernahmenZusammenfuehren } = await import("@/lib/kiSchaden");

const FAHRZEUG = { id: "V1", make_label: "BMW", mileage: "100000" };
const SCHLUESSEL = entwurfSchluessel("U1", "V1");
const VORSCHLAEGE = {
  felder: { accident_free: { value: "Ja", source: "listing_description", source_text: "unfallfrei" } },
  hinweise: [],
};

let wurzel;
let behaelter;

async function warten() {
  await act(async () => { await new Promise((r) => setTimeout(r, 0)); });
  await act(async () => { await new Promise((r) => setTimeout(r, 0)); });
}
const el = (id) => behaelter.querySelector(`[data-testid="${id}"]`);

async function oeffnen() {
  behaelter = document.createElement("div");
  document.body.appendChild(behaelter);
  wurzel = createRoot(behaelter);
  await act(async () => {
    wurzel.render(createElement(ContractDialog, { open: true, onClose: () => {}, vehicle: FAHRZEUG, vehicleId: "V1" }));
  });
  await warten();
}

async function schliessen() {
  await act(async () => { wurzel.unmount(); });
  behaelter.remove();
  wurzel = null;
}

async function eingeben(testid, wert) {
  const feld = el(testid);
  const proto = feld.tagName === "SELECT" ? HTMLSelectElement.prototype : HTMLInputElement.prototype;
  await act(async () => {
    Object.getOwnPropertyDescriptor(proto, "value").set.call(feld, wert);
    feld.dispatchEvent(new Event(feld.tagName === "SELECT" ? "change" : "input", { bubbles: true }));
  });
}

async function pdfErstellen() {
  const formular = el("contract-dialog").querySelector("form");
  await act(async () => {
    formular.dispatchEvent(new Event("submit", { bubbles: true, cancelable: true }));
  });
}

beforeEach(() => {
  vi.clearAllMocks();
  window.sessionStorage.clear();
  api.get.mockImplementation(async (url) => (String(url).startsWith("/contracts/vorschlaege/")
    ? { data: VORSCHLAEGE } : { data: {} }));
  api.post.mockResolvedValue({ data: { id: "C1" } });
  window.confirm = vi.fn(() => false);
});

afterEach(async () => {
  if (wurzel) await schliessen();
});

describe("Übernahmen aus dem Inserat überleben den Entwurf", () => {
  it("Entwurf speichern, neu öffnen: Kasten und Rückfrage vor 'PDF erstellen' bleiben", async () => {
    await oeffnen();
    expect(el("contract-accident-free").value).toBe("Ja");
    expect(el("contract-inserat-vorschlaege")?.textContent).toContain("Aus dem Inserat übernommen");

    // Sucher tippt etwas anderes, der Entwurf wird gesichert (Handy: pagehide)
    await eingeben("contract-price", "15000");
    await eingeben("contract-payment", "Bar");
    await act(async () => { window.dispatchEvent(new Event("pagehide")); });
    const gespeichert = JSON.parse(window.sessionStorage.getItem(SCHLUESSEL));
    expect(gespeichert.form.accident_free).toBe("Ja");
    expect(gespeichert.uebernommen.map((u) => u.feld)).toEqual(["accident_free"]);
    await schliessen();

    // erneut öffnen: Felder sind aus dem Entwurf gefüllt
    await oeffnen();
    expect(el("contract-accident-free").value).toBe("Ja");
    const kasten = el("contract-inserat-vorschlaege");
    expect(kasten?.textContent).toContain("Aus dem Inserat übernommen (bitte prüfen)");
    expect(kasten?.textContent).toContain("Unfallfrei");

    await pdfErstellen();
    expect(window.confirm).toHaveBeenCalledTimes(1);
    expect(window.confirm.mock.calls[0][0]).toContain("Aus dem Inserat vorbelegt und noch nicht geprüft");
    expect(window.confirm.mock.calls[0][0]).toContain("Unfallfrei");
    expect(api.post).not.toHaveBeenCalled();
  });

  it("im Entwurf geänderter Wert: keine Übernahme mehr, keine Rückfrage dazu", async () => {
    entwurfSpeichern(SCHLUESSEL, {
      form: { accident_free: "Nein", purchase_price: "15000", payment_method: "Bar" },
      beruehrt: {},
      uebernommen: [{ feld: "accident_free", label: "Unfallfrei", wert: "Ja", roh: "Ja", fund: "unfallfrei" }],
    });
    await oeffnen();
    expect(el("contract-accident-free").value).toBe("Nein");
    expect(el("contract-inserat-vorschlaege")?.textContent || "").not.toContain("Aus dem Inserat übernommen");
    window.confirm = vi.fn(() => true);
    await pdfErstellen();
    expect(window.confirm).not.toHaveBeenCalled();
    expect(api.post).toHaveBeenCalledTimes(1);
  });

  it("Entwurf ohne Übernahmeliste (alter Stand) bricht nichts", async () => {
    entwurfSpeichern(SCHLUESSEL, { form: { accident_free: "Ja" }, beruehrt: {} });
    await oeffnen();
    expect(el("contract-accident-free").value).toBe("Ja");
    expect(el("contract-inserat-vorschlaege")?.textContent || "").not.toContain("Aus dem Inserat übernommen");
  });
});

describe("uebernahmenAusEntwurf / uebernahmenZusammenfuehren", () => {
  const u = (feld, roh) => ({ feld, label: feld, wert: roh, roh, fund: "" });
  it("nur Felder, deren Wert noch dem übernommenen entspricht", () => {
    const e = { form: { accident_free: "Ja", drivable: "Nein", hu_valid: "" },
                uebernommen: [u("accident_free", "Ja"), u("drivable", "Ja"), u("hu_valid", "Ja"),
                              u("fehlt", "Ja"), null, "kaputt", u("eu_import", "")] };
    expect(uebernahmenAusEntwurf(e).map((x) => x.feld)).toEqual(["accident_free"]);
    expect(uebernahmenAusEntwurf({ form: {} })).toEqual([]);
    expect(uebernahmenAusEntwurf(null)).toEqual([]);
    expect(uebernahmenAusEntwurf({ form: { a: "1" }, uebernommen: "x" })).toEqual([]);
  });
  it("neu gewinnt je Feld", () => {
    const alt = [u("accident_free", "Ja"), u("drivable", "Ja")];
    const neu = [u("drivable", "Nein")];
    expect(uebernahmenZusammenfuehren(alt, neu)).toEqual([u("accident_free", "Ja"), u("drivable", "Nein")]);
    expect(uebernahmenZusammenfuehren(undefined, undefined)).toEqual([]);
  });
});
