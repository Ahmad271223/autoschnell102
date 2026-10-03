/*
 * Rollenprüfung 22.09.2026 — Team Bestand/Oberfläche, Seitenlogik.
 *  RP-005/104/255/425  active_profile geht nie mit den Einstellungen raus
 *  RP-426/RP-138       auch der Chef schickt nur Geändertes (Logo, zweiter Tab)
 *  RP-423              alte AGB: Standardtext als Grundlage, Merge wird gespeichert
 *  RP-010/109/260      Probe-Abos lesbar
 *  RP-006/RP-105       Abo-Stand weicht vom Kontext ab -> nachladen
 *  RP-414              Kilometer-Regel deutsch ("50.000" = 50.000 km)
 *  RP-411              Manuelles Fahrzeug: km/EK deutsch, PS ganzzahlig
 *  RP-024/RP-274       Länder-Auswahl mit echten Farb-Tokens
 */
import { act, createElement } from "react";
import { createRoot } from "react-dom/client";
import { afterEach, describe, expect, it, vi } from "vitest";

vi.mock("sonner", () => ({ toast: { success: vi.fn(), error: vi.fn(), info: vi.fn(), warning: vi.fn(), message: vi.fn() } }));

globalThis.IS_REACT_ACT_ENVIRONMENT = true;

const {
  KmFeld, aboKontextVeraltet, formAus, kmAnzeige, planText, speicherPayload,
} = await import("./Einstellungen");
const { manuellFormAus, manuellPayload } = await import("./Bestand");
const { default: CountryPicker } = await import("@/components/CountryPicker");

const DEALER = {
  company_name: "Chef GmbH", phone: "0301", whatsapp_number: "", logo_url: "/api/files/logo-alt.png",
  comparison_rules: { mileage: { mode: "plus", value: 30000 } }, export_rules: {},
  active_profile: "inland", email_template: "Hallo", default_terms: "",
  digital_vertragstext: "", digital_vertragstext_standard: "1. Standard\n\n2. Klauseln",
};

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

// React hört auf das native "input"-Ereignis; den Wert über den Setter des
// Prototyps setzen, sonst merkt React die Änderung nicht.
function tippen(input, wert) {
  const setter = Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, "value").set;
  act(() => {
    setter.call(input, wert);
    input.dispatchEvent(new Event("input", { bubbles: true }));
  });
}

describe("RP-005/RP-425/RP-426/RP-138: was beim Speichern rausgeht", () => {
  it("Chef ohne Änderung schickt nichts — auch kein active_profile", () => {
    const stand = formAus(DEALER);
    expect(speicherPayload(structuredClone(stand), stand, { istChef: true })).toEqual({});
  });

  it("veralteter Kontext: Live-Schalter und Logo bleiben unangetastet", () => {
    const stand = formAus(DEALER);
    const form = structuredClone(stand);
    form.active_profile = "export";                  // anderswo umgeschaltet
    form.profile.company_name = "Neu GmbH";          // das Einzige, was der Chef tippt
    const payload = speicherPayload(form, stand, { istChef: true });
    expect(payload).toEqual({ profile: { company_name: "Neu GmbH" } });
    expect("active_profile" in payload).toBe(false);
    expect(payload.profile.logo_url).toBeUndefined();
  });

  it("Logo entfernen geht weiterhin raus", () => {
    const stand = formAus(DEALER);
    const form = structuredClone(stand);
    form.profile.logo_url = "";
    expect(speicherPayload(form, stand, { istChef: true })).toEqual({ profile: { logo_url: "" } });
  });

  it("Sucher: gleiche Regel wie bisher (B19), auch ohne active_profile", () => {
    const stand = formAus(DEALER);
    const form = { ...structuredClone(stand), active_profile: "export", email_template: "Moin" };
    expect(speicherPayload(form, stand)).toEqual({ email_template: "Moin" });
  });
});

describe("RP-423: alte AGB", () => {
  const MIT_AGB = { ...DEALER, default_terms: "AGB alt" };

  it("Standardtext bleibt Grundlage, AGB darunter", () => {
    const f = formAus(MIT_AGB);
    expect(f.digital_vertragstext).toBe("1. Standard\n\n2. Klauseln\n\nAGB alt");
    expect(f._agb_zusammengefuehrt).toBe(true);
    expect(f._agb_standard_genutzt).toBe(true);
  });

  it("der Chef speichert den zusammengeführten Text und leert das alte Feld", () => {
    const stand = formAus(MIT_AGB);
    expect(speicherPayload(structuredClone(stand), stand, { istChef: true })).toEqual({
      digital_vertragstext: "1. Standard\n\n2. Klauseln\n\nAGB alt", default_terms: "",
    });
    // Sucher nicht — das würde die Chef-Vorgabe einfrieren (B19)
    expect(speicherPayload(structuredClone(stand), stand)).toEqual({});
  });
});

describe("RP-010/RP-109/RP-260 und RP-006/RP-105: Abo", () => {
  it("Probe-Abos lesbar", () => {
    expect(planText("probe3")).toBe("Probe (3 Tage)");
    expect(planText("probe5")).toBe("Probe (5 Tage)");
    expect(planText("probe7")).toBe("Probe (7 Tage)");
    expect(planText("yearly")).toBe("Jahresabo");
    expect(planText(null)).toBe("—");
  });

  it("Freischaltung durch den Betreiber lädt den Kontext nach", () => {
    expect(aboKontextVeraltet({ active: false }, { active: true })).toBe(true);
    expect(aboKontextVeraltet(null, { active: true })).toBe(true);
    expect(aboKontextVeraltet({ active: true }, { active: false })).toBe(true);
    expect(aboKontextVeraltet({ active: true }, { active: true })).toBe(false);
    expect(aboKontextVeraltet({ active: false }, null)).toBe(false);
  });
});

describe("RP-414: Kilometer-Regel", () => {
  it("\"50.000\" wird 50.000 km, Unlesbares meldet sich und blockiert", () => {
    const onChange = vi.fn();
    const onFehler = vi.fn();
    const el = rendern(createElement(KmFeld, {
      feld: "comparison_rules.mileage", value: 30000, onChange, onFehler, testid: "km",
    }));
    const input = el.querySelector('[data-testid="km"]');
    expect(input.type).toBe("text");
    expect(input.value).toBe("30.000");
    tippen(input, "50.000");
    expect(onChange).toHaveBeenLastCalledWith(50000);
    tippen(input, "150 Tkm");
    expect(onChange).toHaveBeenLastCalledWith(150000);
    onChange.mockClear();
    tippen(input, "50.0");
    expect(onChange).not.toHaveBeenCalled();
    expect(onFehler).toHaveBeenLastCalledWith("comparison_rules.mileage", expect.stringContaining("50.000"));
    expect(el.querySelector('[data-testid="km-fehler"]')).not.toBeNull();
    tippen(input, "80000");
    expect(onChange).toHaveBeenLastCalledWith(80000);
    expect(onFehler).toHaveBeenLastCalledWith("comparison_rules.mileage", "");
    expect(kmAnzeige(80000)).toBe("80.000");
    expect(kmAnzeige(undefined)).toBe("");
  });

  it("ein Altwert aus dem number-Feld (50 statt 50.000) wird angezeigt", () => {
    const el = rendern(createElement(KmFeld, {
      feld: "export_rules.mileage", value: 50, onChange: () => {}, onFehler: () => {}, testid: "km",
    }));
    expect(el.querySelector('[data-testid="km-hinweis"]').textContent).toContain("50.000 km");
  });
});

describe("RP-411: manuelles Fahrzeug", () => {
  const leer = manuellFormAus(null);

  it("\"150.000\" km und \"12.990\" € werden richtig gelesen", () => {
    const { payload, fehler } = manuellPayload({
      ...leer, make_label: " BMW ", model_label: "320d", mileage: "150.000",
      power_ps: "190", purchase_price: "12.990", features: "Navi, LED,",
    });
    expect(fehler).toBeNull();
    expect(payload).toMatchObject({
      make_label: "BMW", model_label: "320d", mileage: 150000, power_ps: 190,
      power_kw: null, purchase_price: 12990, features: ["Navi", "LED"],
    });
    expect(manuellPayload({ ...leer, make_label: "A", model_label: "B", mileage: "150 Tkm",
                            purchase_price: "12.990,50" }).payload)
      .toMatchObject({ mileage: 150000, purchase_price: 12990.5 });
  });

  it("Unlesbares wird gemeldet statt still falsch gespeichert", () => {
    const basis = { ...leer, make_label: "BMW", model_label: "320d" };
    expect(manuellPayload({ ...basis, mileage: "150,5" }).fehler).toMatch(/Kilometerstand/);
    expect(manuellPayload({ ...basis, power_ps: "1.5" }).fehler).toMatch(/Leistung/);
    expect(manuellPayload({ ...basis, purchase_price: "zwölf" }).fehler).toMatch(/Einkaufspreis/);
    expect(manuellPayload({ ...leer, model_label: "320d" }).fehler).toMatch(/Pflichtfelder/);
    expect(manuellPayload(basis).payload).toMatchObject({ mileage: null, purchase_price: null, power_ps: null });
  });

  it("Bearbeiten: Formular aus dem Fahrzeug, kW bleibt erhalten", () => {
    const fz = { id: "m_1", purchase_price: 12990,
                 data: { make_label: "BMW", model_label: "320d", mileage: 150000, power_kw: 140,
                         power_ps: 190, features: ["Navi", "LED"] } };
    const f = manuellFormAus(fz);
    expect(f).toMatchObject({ mileage: "150.000", purchase_price: "12.990", power_ps: "190",
                              features: "Navi, LED" });
    expect(manuellPayload(f, fz).payload).toMatchObject({ mileage: 150000, purchase_price: 12990,
                                                          power_kw: 140 });
  });
});

describe("RP-024/RP-274: Länder-Auswahl", () => {
  it("Chips und Auswahl nutzen echte Farben (keine HSL-Tripel)", () => {
    const el = rendern(createElement(CountryPicker, {
      value: { mode: "exact", codes: ["DE", "AT"] }, onChange: () => {},
    }));
    const chip = el.querySelector('[data-testid="country-chip-AT"]');
    expect(chip.getAttribute("style")).toContain("var(--accent-blue)");
    expect(el.innerHTML).not.toMatch(/var\(--(card|border|accent)\)/);
    const option = el.querySelector('[data-testid="country-option-AT"]');
    expect(option.getAttribute("aria-pressed")).toBe("true");
  });
});
