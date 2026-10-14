/*
 * Rollenprüfung 22.09.2026, Welle 4 (Review) — Team Bestand/Oberfläche.
 *  Review 1  "Frist +50 Tage" setzte die Frist auf 50 Tage AB HEUTE (nicht
 *            alte Frist + 50) — Knopf, Hinweis und Meldung sagen das jetzt;
 *            Hinweis mit Einzahl/Mehrzahl ("Steht es" / "Stehen sie").
 *  Review 2  KmFeld: unlesbarer Text ("50.00") ließ den Zwischenstand 50 im
 *            Formular; nach dem Aushängen (Profilwechsel) war der Fehler weg
 *            und "Speichern" schickte ±50 km. Jetzt bekommt das Formular bei
 *            unlesbarem Text den Vorwert zurück.
 */
import { act, createElement, useCallback, useState } from "react";
import { createRoot } from "react-dom/client";
import { MemoryRouter } from "react-router-dom";
import { afterEach, describe, expect, it, vi } from "vitest";

vi.mock("sonner", () => ({ toast: { success: vi.fn(), error: vi.fn(), info: vi.fn(), warning: vi.fn(), message: vi.fn() } }));
vi.mock("@/context/AuthContext", () => ({ useAuth: () => ({ user: { id: "chef1", role: "dealer" } }) }));

globalThis.IS_REACT_ACT_ENVIRONMENT = true;

const { toast } = await import("sonner");
const { api } = await import("@/lib/api");
const { featuresSetzen } = await import("@/lib/features");
const { fristErneuertText } = await import("@/lib/bestandForm");
const { KmFeld } = await import("./Einstellungen");
const { default: Bestand } = await import("./Bestand");

let wurzel = null;
let behaelter = null;
async function rendern(el) {
  behaelter = document.createElement("div");
  document.body.appendChild(behaelter);
  wurzel = createRoot(behaelter);
  await act(async () => { wurzel.render(el); });
  await act(async () => { await Promise.resolve(); });
  return behaelter;
}
afterEach(() => {
  if (wurzel) act(() => wurzel.unmount());
  behaelter?.remove();
  wurzel = null;
  behaelter = null;
  vi.restoreAllMocks();
  featuresSetzen(null);
  toast.success.mockClear();
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
function verlassen(input) {
  act(() => { input.dispatchEvent(new FocusEvent("focusout", { bubbles: true })); });
}

/** Nachbau des Regel-Editors: Wert + Feldfehler im Elternteil, Feld ein-/aushängbar. */
let stand = null;
function Editor({ start = 30000 }) {
  const [value, setValue] = useState(start);
  const [fehler, setFehler] = useState({});
  const [sichtbar, setSichtbar] = useState(true);
  const onFehler = useCallback((feld, text) => {
    setFehler((s) => ((s[feld] || "") === (text || "") ? s : { ...s, [feld]: text || "" }));
  }, []);
  stand = { value, fehler, setSichtbar };
  return sichtbar
    ? createElement(KmFeld, {
      feld: "comparison_rules.mileage", value: value ?? 30000, onChange: setValue,
      onFehler, testid: "km",
    })
    : null;
}
const offenerFehler = () => Object.values(stand.fehler).find(Boolean);

describe("Review 2: KmFeld lässt keinen halb getippten Wert im Formular", () => {
  it("Tippfehler \"50.00\", dann Profilwechsel: gespeichert würde der Vorwert, nie 50 km", async () => {
    const el = await rendern(createElement(Editor));
    const input = el.querySelector('[data-testid="km"]');
    tippen(input, "5");
    tippen(input, "50");
    expect(stand.value).toBe(50);                 // Zwischenstand (lesbar)
    tippen(input, "50.");
    tippen(input, "50.00");
    // unlesbar: Formular hat wieder den Vorwert, Eingabe und Fehler bleiben stehen
    expect(stand.value).toBe(30000);
    expect(input.value).toBe("50.00");
    expect(el.querySelector('[data-testid="km-fehler"]')).not.toBeNull();
    expect(offenerFehler()).toContain("50.000");
    verlassen(input);                              // Klick auf "Export" verlässt zuerst das Feld
    expect(stand.value).toBe(30000);
    act(() => stand.setSichtbar(false));           // Profilwechsel hängt das Feld aus
    expect(offenerFehler()).toBeUndefined();       // Speichern wäre frei ...
    expect(stand.value).toBe(30000);               // ... aber mit dem Vorwert, nicht ±50 km
  });

  it("nach dem Korrigieren gilt der neue Wert; Vorwert ist der zuletzt verlassene", async () => {
    const el = await rendern(createElement(Editor));
    const input = el.querySelector('[data-testid="km"]');
    tippen(input, "50.00");
    expect(stand.value).toBe(30000);
    tippen(input, "50.000");
    expect(stand.value).toBe(50000);
    expect(offenerFehler()).toBeUndefined();
    verlassen(input);
    expect(input.value).toBe("50.000");
    tippen(input, "60");
    tippen(input, "60.0");
    expect(stand.value).toBe(50000);               // zurück auf den verlassenen Wert
    expect(input.value).toBe("60.0");
    tippen(input, "");                              // leer = Standard, beim Verlassen
    verlassen(input);
    expect(stand.value).toBeUndefined();
    expect(input.value).toBe("30.000");
    expect(offenerFehler()).toBeUndefined();
  });
});

describe("Review 1: Frist erneuern = 50 Tage ab heute", () => {
  it("Meldung nennt \"ab heute\" und das neue Enddatum", () => {
    expect(fristErneuertText("2026-11-11T10:00:00+00:00"))
      .toBe("Frist erneuert — das Fahrzeug bleibt ab heute 50 Tage im Bestand (bis 11.11.2026)");
    expect(fristErneuertText(null)).toBe("Frist erneuert — das Fahrzeug bleibt ab heute 50 Tage im Bestand");
    expect(fristErneuertText("kaputt")).not.toContain("Invalid");
  });

  function fahrzeug(id, tage) {
    return {
      id, lifecycle: "bestand", source: "plattform", retention_days_left: tage,
      data: { make_label: "BMW", model_label: `320d ${id}` },
    };
  }
  async function zeigeBestand(items) {
    featuresSetzen({ marktplatz: false });
    vi.spyOn(api, "get").mockImplementation(async (url) => {
      if (String(url).startsWith("/bestand")) return { data: { items, counts: { bestand: items.length } } };
      return { data: [] };
    });
    return rendern(createElement(MemoryRouter, null, createElement(Bestand)));
  }

  it("Mehrzahl: \"Stehen sie noch auf dem Hof?\", Knopf sagt \"50 Tage ab heute\"", async () => {
    const el = await zeigeBestand([fahrzeug("v1", 3), fahrzeug("v2", 6)]);
    const hinweis = el.querySelector('[data-testid="bestand-frist-hinweis"]').textContent;
    expect(hinweis).toContain("2 Fahrzeuge werden");
    expect(hinweis).toContain("Stehen sie noch auf dem Hof?");
    expect(hinweis).not.toContain("Steht es");
    expect(hinweis).not.toContain("+50");
    expect(hinweis).toContain("„Frist erneuern“");
    expect(hinweis).toContain("50 Tage ab heute");
    const knopf = el.querySelector('[data-testid="bestand-verlaengern-v1"]');
    expect(knopf.textContent).toContain("Frist erneuern (50 Tage ab heute)");
    expect(knopf.textContent).not.toContain("+50");
  });

  it("Einzahl: \"Steht es noch auf dem Hof?\"; Klick meldet die neue Frist", async () => {
    const el = await zeigeBestand([fahrzeug("v1", 2), fahrzeug("v2", 40)]);
    const hinweis = el.querySelector('[data-testid="bestand-frist-hinweis"]').textContent;
    expect(hinweis).toContain("Ein Fahrzeug wird");
    expect(hinweis).toContain("Steht es noch auf dem Hof?");
    const post = vi.spyOn(api, "post").mockResolvedValue({
      data: { ok: true, lifecycle: "bestand", verlaengert: true, expires_at: "2026-11-11T10:00:00+00:00" },
    });
    await act(async () => { el.querySelector('[data-testid="bestand-verlaengern-v2"]').click(); });
    await act(async () => { await Promise.resolve(); });
    expect(post).toHaveBeenCalledWith("/vehicles/v2/decision", { decision: "bestand", von_lifecycle: "bestand" });
    expect(toast.success).toHaveBeenCalledWith(
      "Frist erneuert — das Fahrzeug bleibt ab heute 50 Tage im Bestand (bis 11.11.2026)");
  });
});
