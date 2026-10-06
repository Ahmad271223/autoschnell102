/**
 * Wunsch Ahmad 04.10.2026: Kaufvertrag schneller abschließen.
 *  - die wichtigsten Angaben (Name, Telefon, E-Mail, Adresse, PLZ, Ort, Kaufpreis) sind markiert, solange sie
 *    fehlen, und stehen unten unter „Noch offen“ (Klick springt zum Feld)
 *  - kein Feld „Ansprechpartner“ mehr beim Käufer; der Ansprechpartner aus dem Inserat steht nur als Info
 *    unter dem Namen des Verkäufers und kommt nie in den Vertrag
 *  - Schäden von Hand UNTER der Skizze (Abschnitt „Schäden“), nicht mehr oben bei „Unfallfrei“
 *  - Abschnitte als Karten mit Symbol, Überschriften wie im PDF („Zustand“, „Beschreibung“)
 */
import { act, createElement } from "react";
import { createRoot } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

const post = vi.fn();
const get = vi.fn();
const refresh = vi.fn();
const DEALER = {
  company_name: "Käufer GmbH", contact_person: "Chef Person", address: "Weg 1", zip_code: "10115",
  city: "Berlin", default_terms: "", digital_vertragstext: "", digital_vertragstext_standard: "Standard",
};

vi.mock("sonner", () => ({ toast: { success: vi.fn(), error: vi.fn(), info: vi.fn(), warning: vi.fn() } }));
vi.mock("@/lib/api", () => ({
  api: { get: (...a) => get(...a), post: (...a) => post(...a), put: vi.fn(), delete: vi.fn() },
  errMsg: (e, f) => e?.response?.data?.detail || e?.message || f,
}));
const rolle = vi.hoisted(() => ({ wert: "sucher" }));
vi.mock("@/context/AuthContext", () => ({
  useAuth: () => ({ dealer: DEALER, user: { id: "u1", role: rolle.wert }, refresh }),
}));
vi.mock("@/components/MarktdatenKarte", () => ({ useMarktHinweis: () => null }));
vi.mock("./KiSchadenKarte", () => ({ default: () => null }));
vi.mock("./DamageSelector", () => ({
  default: () => createElement("div", { "data-testid": "skizze" }), damagesToText: () => "",
}));
vi.mock("@/lib/pdf", () => ({ openContractPdf: vi.fn() }));
vi.mock("@/lib/dateiOeffnen", () => ({ blobOeffnen: vi.fn() }));
vi.mock("@/lib/ungespeichert", () => ({ useUngespeichert: () => {} }));

globalThis.IS_REACT_ACT_ENVIRONMENT = true;

const { default: ContractDialog, fehlendeWichtige, WICHTIGE_FELDER, KONTAKT_PFLICHT } = await import("./ContractDialog");
const { toast } = await import("sonner");

let wurzel = null;
let behaelter = null;
afterEach(() => {
  if (wurzel) act(() => wurzel.unmount());
  behaelter?.remove();
  wurzel = null;
  behaelter = null;
});
beforeEach(() => {
  rolle.wert = "sucher";
  post.mockReset();
  get.mockReset();
  get.mockResolvedValue({ data: null });
  refresh.mockResolvedValue({ dealer: DEALER });
  window.sessionStorage.clear();
});

async function oeffnen(vehicle) {
  behaelter = document.createElement("div");
  document.body.appendChild(behaelter);
  wurzel = createRoot(behaelter);
  act(() => {
    wurzel.render(createElement(ContractDialog, { open: true, onClose: () => {}, vehicleId: "v1", onCreated: () => {},
                                                   vehicle }));
  });
  await act(async () => { await Promise.resolve(); });
}
const feld = (id) => behaelter.querySelector(`[data-testid="${id}"]`);
function tippen(input, wert) {
  const proto = input.tagName === "SELECT" ? HTMLSelectElement.prototype
    : input.tagName === "TEXTAREA" ? HTMLTextAreaElement.prototype : HTMLInputElement.prototype;
  const setter = Object.getOwnPropertyDescriptor(proto, "value").set;
  act(() => {
    setter.call(input, wert);
    input.dispatchEvent(new Event(input.tagName === "SELECT" ? "change" : "input", { bubbles: true }));
  });
}

describe("Wichtige Angaben markiert + „Noch offen“", () => {
  it("reine Helfer: sieben Felder, leer = offen", () => {
    expect(WICHTIGE_FELDER.map((f) => f.label)).toEqual(["Name", "Telefon", "E-Mail", "Adresse", "PLZ", "Ort",
                                                         "Kaufpreis"]);
    expect(fehlendeWichtige({ seller_name: "A", seller_phone: " ", purchase_price: "1.000" }).map((f) => f.key))
      .toEqual(["seller_phone", "seller_email", "seller_address", "seller_zip", "seller_city"]);
  });

  it("leere Felder tragen „fehlt“, unten stehen sie als Knöpfe — ausgefüllt verschwinden sie", async () => {
    await oeffnen({ seller_name: "Vera Verkauf", seller_city: "Berlin", make_label: "BMW", model_label: "320d" });
    expect(feld("contract-seller-name-fehlt")).toBeNull();
    expect(feld("contract-seller-city-fehlt")).toBeNull();
    for (const id of ["contract-seller-phone", "contract-seller-email", "contract-seller-address",
                      "contract-seller-zip", "contract-price"]) {
      expect(feld(`${id}-fehlt`), id).not.toBeNull();
      expect(feld(id).className).toContain("vf-fehlt");
    }
    const offen = feld("contract-noch-offen");
    expect(offen.textContent).toContain("Noch offen");
    expect(feld("contract-offen-seller_phone")).not.toBeNull();
    expect(feld("contract-offen-seller_name")).toBeNull();
    tippen(feld("contract-price"), "4.500");
    tippen(feld("contract-seller-phone"), "0170 1234567");
    tippen(feld("contract-seller-email"), "v@beispiel.de");
    tippen(feld("contract-seller-address"), "Allee 1");
    tippen(feld("contract-seller-zip"), "10115");
    expect(feld("contract-price-fehlt")).toBeNull();
    expect(feld("contract-price").className).not.toContain("vf-fehlt");
    expect(feld("contract-noch-offen").textContent).toContain("Alles Wichtige ausgefüllt");
  });
});

describe("kein Ansprechpartner, Schäden unter der Skizze, Karten", () => {
  it("Käufer ohne Ansprechpartner-Feld; der aus dem Inserat nur als Info, nichts davon im Payload", async () => {
    await oeffnen({ seller_name: "Autohaus Nord GmbH", seller_ansprechpartner: "Herr Meier", make_label: "VW",
                   seller_phone: "0511 123456" });
    expect(feld("contract-dealer-contact")).toBeNull();
    expect(feld("contract-seller-name-hinweis").textContent)
      .toContain("Ansprechpartner laut Inserat: Herr Meier (nur zur Info, kommt nicht in den Vertrag)");
    tippen(feld("contract-price"), "4.500");
    tippen(feld("contract-payment"), "Bar");
    post.mockResolvedValue({ data: { id: "c1" } });
    await act(async () => {
      behaelter.querySelector("form").dispatchEvent(new Event("submit", { bubbles: true, cancelable: true }));
      await Promise.resolve();
      await Promise.resolve();
    });
    const aufruf = post.mock.calls.find((c) => c[0] === "/contracts");
    expect(aufruf, "POST /contracts").toBeTruthy();
    expect(aufruf[1].dealer_contact).toBeUndefined();
  });

  it("Schadens-Text steht unter der Skizze; Unfall-Beschreibung nur bei „Unfallfrei: Nein“", async () => {
    await oeffnen({ seller_name: "V", make_label: "VW" });
    const skizze = feld("skizze");
    const text = feld("contract-schaeden-text");
    expect(text).not.toBeNull();
    expect(skizze.compareDocumentPosition(text) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
    expect(text.contains(feld("contract-veh-damage"))).toBe(true);
    expect(feld("contract-veh-damage").tagName).toBe("TEXTAREA");
    expect(feld("contract-accident-loc")).toBeNull();
    tippen(feld("contract-accident-free"), "Nein");
    expect(text.contains(feld("contract-accident-loc"))).toBe(true);
    expect(feld("contract-accident-hinweis").textContent).toContain("unter der Skizze");
  });

  it("Abschnitte als Karten mit Überschriften wie im PDF", async () => {
    await oeffnen({ seller_name: "V", make_label: "VW" });
    const titel = [...behaelter.querySelectorAll(".vf-karte .vf-kopf .overline")].map((e) => e.textContent);
    expect(titel).toEqual(expect.arrayContaining(["Verkäufer", "Käufer (du)", "Fahrzeugdaten", "Zustand",
                                                  "Schäden / Beschädigungen", "Konditionen", "Nummern", "Beschreibung"]));
    expect(titel).not.toContain("Zusicherungen & Zustand");
    expect(titel.indexOf("Konditionen")).toBeLessThan(titel.indexOf("Nummern"));
  });
});

describe("Abholuhrzeit (Wunsch Ahmad 06.10.2026)", () => {
  it("der Sucher sieht nur das Abholdatum — die Uhrzeit legt der Terminplaner fest", async () => {
    await oeffnen({ seller_name: "V", make_label: "VW" });
    expect(feld("contract-pickup-date")).not.toBeNull();
    expect(feld("contract-pickup-time")).toBeNull();
  });

  it("der Chef sieht die Uhrzeit weiter", async () => {
    rolle.wert = "dealer";
    await oeffnen({ seller_name: "V", make_label: "VW" });
    expect(feld("contract-pickup-time")).not.toBeNull();
  });
});

describe("Telefon oder E-Mail ist Pflicht (Wunsch Ahmad 06.10.2026)", () => {
  it("ist eins da, fehlt das andere nicht", () => {
    const offen = (f) => fehlendeWichtige({ seller_name: "A", purchase_price: "1.000", ...f }).map((x) => x.key);
    expect(offen({})).toEqual(expect.arrayContaining(["seller_phone", "seller_email"]));
    expect(offen({ seller_phone: "0170 1" })).not.toContain("seller_email");
    expect(offen({ seller_email: "a@b.de" })).not.toContain("seller_phone");
  });

  it("ohne beides: Hinweis am Feld, Erstellen gesperrt — mit E-Mail allein geht es", async () => {
    await oeffnen({ seller_name: "V", make_label: "VW" });
    expect(feld("contract-kontakt-pflicht").textContent).toContain("eins von beiden reicht");
    tippen(feld("contract-price"), "4.500");
    tippen(feld("contract-payment"), "Bar");
    post.mockResolvedValue({ data: { id: "c1" } });
    const absenden = async () => {
      await act(async () => {
        behaelter.querySelector("form").dispatchEvent(new Event("submit", { bubbles: true, cancelable: true }));
        await Promise.resolve();
        await Promise.resolve();
      });
    };
    await absenden();
    expect(toast.error).toHaveBeenCalledWith(KONTAKT_PFLICHT);
    expect(post.mock.calls.find((c) => c[0] === "/contracts")).toBeUndefined();
    tippen(feld("contract-seller-email"), "v@beispiel.de");
    expect(feld("contract-kontakt-pflicht")).toBeNull();
    await absenden();
    expect(post.mock.calls.find((c) => c[0] === "/contracts")).toBeTruthy();
  });
});
