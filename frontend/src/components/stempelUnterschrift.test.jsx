/** Stempel-Werkzeug (Wunsch Ahmad 02.10.2026): Felder vorbelegt, sechs Designs, Unterschrift Pflicht, Übernehmen liefert das PNG. */
import { act, createElement } from "react";
import { createRoot } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

const toast = { success: vi.fn(), error: vi.fn(), info: vi.fn(), warning: vi.fn() };
vi.mock("sonner", () => ({ toast }));
// Das echte Feld braucht einen 2D-Kontext — hier ein Knopf, der "unterschreibt", und einer, der löscht.
vi.mock("@/components/SignaturePad", () => ({
  default: ({ onChange }) => createElement("div", null,
    createElement("button", { type: "button", "data-testid": "pad", onClick: () => onChange("data:image/png;base64,SIG") }, "unterschreiben"),
    createElement("button", { type: "button", "data-testid": "pad-leer", onClick: () => onChange(null) }, "löschen")),
}));
const stempelErzeugen = vi.fn();
const unterschriftZuschneiden = vi.fn();
vi.mock("@/lib/stempel", () => ({
  VARIANTEN: [{ id: "kasten", name: "Klassisch" }, { id: "rund", name: "Rund" }, { id: "ring", name: "Ring modern" },
              { id: "text", name: "Nur Schrift" }, { id: "linien", name: "Linien" }, { id: "kapsel", name: "Kapsel" }],
  VARIANTE_STANDARD: "kasten",
  stempelErzeugen: (...a) => stempelErzeugen(...a),
  unterschriftZuschneiden: (...a) => unterschriftZuschneiden(...a),
}));

globalThis.IS_REACT_ACT_ENVIRONMENT = true;
const { default: StempelUnterschrift, datenAusFirma } = await import("./StempelUnterschrift");

let wurzel = null;
let behaelter = null;
const el = (id) => behaelter.querySelector(`[data-testid="${id}"]`);
async function rendern(props) {
  behaelter = document.createElement("div");
  document.body.appendChild(behaelter);
  wurzel = createRoot(behaelter);
  await act(async () => { wurzel.render(createElement(StempelUnterschrift, props)); });
  await act(async () => { await new Promise((r) => setTimeout(r, 20)); });
}
async function klick(id) { await act(async () => { el(id).click(); await new Promise((r) => setTimeout(r, 20)); }); }

beforeEach(() => {
  vi.clearAllMocks();
  const ctx = { fillStyle: "", fillRect: vi.fn(), drawImage: vi.fn() };
  vi.spyOn(HTMLCanvasElement.prototype, "getContext").mockImplementation(() => ctx);
  stempelErzeugen.mockImplementation((o) => ({ width: 1000, height: 400, getContext: () => ctx,
                                                toDataURL: () => `data:image/png;base64,STEMPEL-${o.variante}` }));
  unterschriftZuschneiden.mockImplementation(async (url) => (url ? { img: {}, w: 400, h: 150, dataUrl: url } : null));
});
afterEach(async () => {
  if (wurzel) await act(async () => { wurzel.unmount(); });
  wurzel = null; behaelter?.remove(); vi.restoreAllMocks();
});

describe("StempelUnterschrift", () => {
  it("datenAusFirma: PLZ und Ort in einer Zeile, Rest leer", () => {
    expect(datenAusFirma({ name: "KFZ Müller", strasse: "Hauptstraße 1", plz: "12345", ort: "Berlin", tel: "030", mail: "a@b" }))
      .toEqual({ name: "KFZ Müller", zusatz: "", strasse: "Hauptstraße 1", ort: "12345 Berlin", tel: "030", mail: "a@b", ust: "" });
    expect(datenAusFirma(null).name).toBe("");
  });

  it("Felder vorbelegt, sechs Designs, ohne Unterschrift kein Übernehmen, mit Unterschrift das PNG des gewählten Designs", async () => {
    const onUebernehmen = vi.fn(async () => {});
    await rendern({ firma: { name: "KFZ Müller", strasse: "Hauptstraße 1", plz: "12345", ort: "Berlin" }, onUebernehmen });
    expect(el("stempel-feld-name").value).toBe("KFZ Müller");
    expect(el("stempel-feld-ort").value).toBe("12345 Berlin");
    expect(behaelter.querySelectorAll('[data-testid^="stempel-design-"]').length).toBe(6);
    expect(el("stempel-design-kasten").getAttribute("aria-pressed")).toBe("true");
    expect(el("stempel-uebernehmen").disabled).toBe(true);
    expect(el("stempel-hinweis").textContent).toContain("Erst unterschreiben");
    // Vorschau wurde gezeichnet (Hauptbild + sechs Kacheln)
    expect(stempelErzeugen.mock.calls.length).toBeGreaterThanOrEqual(7);
    await klick("stempel-design-rund");
    expect(el("stempel-design-rund").getAttribute("aria-pressed")).toBe("true");
    await klick("pad");
    expect(unterschriftZuschneiden).toHaveBeenCalledWith("data:image/png;base64,SIG");
    expect(el("stempel-uebernehmen").disabled).toBe(false);
    expect(el("stempel-hinweis")).toBeNull();
    await klick("stempel-uebernehmen");
    expect(onUebernehmen).toHaveBeenCalledWith("data:image/png;base64,STEMPEL-rund");
    // Unterschrift mit in den Stempel gezeichnet
    const letzter = stempelErzeugen.mock.calls[stempelErzeugen.mock.calls.length - 1][0];
    expect(letzter.unterschrift).toEqual({ img: {}, w: 400, h: 150, dataUrl: "data:image/png;base64,SIG" });
    expect(letzter.einst).toEqual({ an: true, groesse: 55, x: 50, y: 65 });
    // Unterschrift löschen -> wieder gesperrt
    await klick("pad-leer");
    expect(el("stempel-uebernehmen").disabled).toBe(true);
  });

  it("Fehler beim Übernehmen wird gemeldet", async () => {
    await rendern({ firma: { name: "X" }, onUebernehmen: vi.fn(async () => { throw new Error("Server weg"); }) });
    await klick("pad");
    await klick("stempel-uebernehmen");
    expect(toast.error).toHaveBeenCalledWith("Server weg");
  });
});
