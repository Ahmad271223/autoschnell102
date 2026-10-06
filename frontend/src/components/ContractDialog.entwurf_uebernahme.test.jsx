/**
 * Nachprüfung 28.09.2026 (inserat2, Nr. 3): Aus dem Inserat vorbelegte Werte
 * gingen mit dem Entwurf verloren bzw. standen danach ohne Hinweis im Vertrag.
 *
 * inserat4 (28.09.2026): Zusicherungen belegt der Dialog nicht mehr vor.
 * inserat5 (28.09.2026): AUCH Bereifung und Schlüsselanzahl nicht — nichts aus
 * dem Inserat steht ungefragt im Vertrag. Der Kasten „Aus dem Inserat
 * übernommen“ entfällt; der Entwurf trägt keine Übernahmeliste mehr. Alte
 * Entwürfe mit einer solchen Liste: die dort ohne Klick eingetragenen Werte
 * werden beim Wiederherstellen wieder zum Vorschlag.
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
const { uebernahmenAusEntwurf } = await import("@/lib/kiSchaden");

const FAHRZEUG = { id: "V1", make_label: "BMW", mileage: "100000", seller_phone: "0170 1234567" };  // Kontakt Pflicht (06.10.2026)
const SCHLUESSEL = entwurfSchluessel("U1", "V1");
const VORSCHLAEGE = {
  felder: { schluessel_anzahl: { value: "2", source: "listing_description", source_text: "2 Schlüssel" } },
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

async function klicken(testid) {
  const knopf = el(testid);
  expect(knopf, testid).toBeTruthy();
  await act(async () => { knopf.dispatchEvent(new MouseEvent("click", { bubbles: true })); });
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

describe("Entwurf und Inserat-Vorschläge (inserat5: nichts ungefragt)", () => {
  it("Entwurf speichern, neu öffnen: Schlüsselanzahl bleibt Vorschlag, bis „Übernehmen“ geklickt ist", async () => {
    await oeffnen();
    expect(el("contract-schluessel-anzahl").value).toBe("");
    expect(el("contract-vorschlag-schluessel_anzahl")).toBeTruthy();
    expect(el("contract-inserat-vorschlaege").textContent).not.toContain("Aus dem Inserat übernommen");

    await eingeben("contract-price", "15000");
    await eingeben("contract-payment", "Bar");
    await act(async () => { window.dispatchEvent(new Event("pagehide")); });
    let gespeichert = JSON.parse(window.sessionStorage.getItem(SCHLUESSEL));
    expect(gespeichert.form.schluessel_anzahl).toBe("");
    expect(gespeichert.uebernommen).toBeUndefined();
    await schliessen();

    await oeffnen();
    expect(el("contract-schluessel-anzahl").value).toBe("");
    expect(el("contract-vorschlag-schluessel_anzahl")).toBeTruthy();
    // erst der Klick trägt ein — und das überlebt Entwurf und Neuöffnen
    await klicken("contract-vorschlag-uebernehmen-schluessel_anzahl");
    expect(el("contract-schluessel-anzahl").value).toBe("2");
    await act(async () => { window.dispatchEvent(new Event("pagehide")); });
    gespeichert = JSON.parse(window.sessionStorage.getItem(SCHLUESSEL));
    expect(gespeichert.beruehrt.schluessel_anzahl).toBe(true);
    await schliessen();
    await oeffnen();
    expect(el("contract-schluessel-anzahl").value).toBe("2");
    expect(el("contract-vorschlag-schluessel_anzahl")).toBeNull();

    await pdfErstellen();
    expect(window.confirm).not.toHaveBeenCalled();
    expect(api.post).toHaveBeenCalledTimes(1);
    expect(api.post.mock.calls[0][1].schluessel_anzahl).toBe("2");
  });

  it("alter Entwurf: ohne Klick eingetragene Schlüsselanzahl wird wieder Vorschlag", async () => {
    entwurfSpeichern(SCHLUESSEL, {
      form: { schluessel_anzahl: "2", purchase_price: "15000", payment_method: "Bar" },
      beruehrt: {},
      uebernommen: [{ feld: "schluessel_anzahl", label: "Schlüssel", wert: "2", roh: "2", fund: "2 Schlüssel" }],
    });
    await oeffnen();
    expect(el("contract-schluessel-anzahl").value).toBe("");
    expect(el("contract-vorschlag-schluessel_anzahl")).toBeTruthy();
    await pdfErstellen();
    expect(api.post.mock.calls[0][1].schluessel_anzahl).toBe("");
  });

  it("alter Entwurf mit im Entwurf geändertem Wert: der eigene Wert bleibt", async () => {
    entwurfSpeichern(SCHLUESSEL, {
      form: { schluessel_anzahl: "3", purchase_price: "15000", payment_method: "Bar" },
      beruehrt: {},
      uebernommen: [{ feld: "schluessel_anzahl", label: "Schlüssel", wert: "2", roh: "2", fund: "2 Schlüssel" }],
    });
    await oeffnen();
    expect(el("contract-schluessel-anzahl").value).toBe("3");
    // Vorschlag „2“ bleibt sichtbar, gekennzeichnet als Abweichung
    expect(el("contract-vorschlag-abweichung-schluessel_anzahl").textContent).toContain("(du: 3)");
    await pdfErstellen();
    expect(window.confirm).not.toHaveBeenCalled();
    expect(api.post.mock.calls[0][1].schluessel_anzahl).toBe("3");
  });

  it("Entwurf ohne Übernahmeliste (alter Stand) bricht nichts", async () => {
    entwurfSpeichern(SCHLUESSEL, { form: { schluessel_anzahl: "2" }, beruehrt: {} });
    await oeffnen();
    expect(el("contract-schluessel-anzahl").value).toBe("2");
    expect(el("contract-inserat-vorschlaege")?.textContent || "").not.toContain("Aus dem Inserat übernommen");
  });
});

describe("uebernahmenAusEntwurf", () => {
  const u = (feld, roh) => ({ feld, label: feld, wert: roh, roh, fund: "" });
  it("nur Felder, deren Wert noch dem übernommenen entspricht", () => {
    const e = { form: { schluessel_anzahl: "2", tires: "4-fach", accident_free: "Ja", drivable: "Nein", hu_valid: "" },
                uebernommen: [u("schluessel_anzahl", "2"), u("tires", "8-fach"), u("accident_free", "Ja"),
                              u("drivable", "Ja"), u("hu_valid", "Ja"), u("fehlt", "Ja"), null, "kaputt",
                              u("eu_import", "")] };
    // inserat4: Zusicherungen (accident_free) zählen nie als Übernahme
    expect(uebernahmenAusEntwurf(e).map((x) => x.feld)).toEqual(["schluessel_anzahl"]);
    expect(uebernahmenAusEntwurf({ form: {} })).toEqual([]);
    expect(uebernahmenAusEntwurf(null)).toEqual([]);
    expect(uebernahmenAusEntwurf({ form: { a: "1" }, uebernommen: "x" })).toEqual([]);
  });
});
