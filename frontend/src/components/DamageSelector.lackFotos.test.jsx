/**
 * Wunsch Ahmad 06.10.2026 (nur Fahrer-Protokoll):
 *  - Lackdicke als EIGENE Markierung auf der Skizze (kein Schaden): Punkt antippen, Wert in µm
 *  - Fotos je Schaden bzw. Messung: Vorschau, Entfernen, „Foto“-Knopf; höchstens 25 je Protokoll
 *  - ohne diese Angaben (Kaufvertrag) bleibt die Skizze wie bisher
 */
import { act, createElement, useState } from "react";
import { createRoot } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

globalThis.IS_REACT_ACT_ENVIRONMENT = true;

const toastMock = vi.hoisted(() => ({ error: vi.fn(), success: vi.fn(), warning: vi.fn() }));
vi.mock("sonner", () => ({ toast: toastMock }));
vi.mock("@/components/AbholFoto", async () => {
  const { createElement: ce } = await import("react");
  return { default: (p) => ce("span", { "data-testid": "foto-vorschau", "data-pfad": p.pfad }) };
});

const { default: DamageSelector, LACK_TYP, lackWert } = await import("./DamageSelector");

let wurzel;
let behaelter;
let schaeden;
let lack;
const el = (id) => behaelter.querySelector(`[data-testid="${id}"]`);
const alle = (sel) => [...behaelter.querySelectorAll(sel)];
async function klick(ziel) {
  await act(async () => { ziel.dispatchEvent(new MouseEvent("click", { bubbles: true })); });
}
async function tippen(input, wert) {
  const setter = Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, "value").set;
  await act(async () => {
    setter.call(input, wert);
    input.dispatchEvent(new Event("input", { bubbles: true }));
  });
}

function Huelle({ start = [], startLack = [], fotos, mitLack = true }) {
  const [d, setD] = useState(start);
  const [l, setL] = useState(startLack);
  schaeden = d;
  lack = l;
  return createElement(DamageSelector, {
    damages: d, onChange: (n) => setD(n),
    ...(mitLack ? { lackMessungen: l, onLackChange: (n) => setL(n) } : {}),
    fotos,
  });
}

const KRATZER = { id: "s1", view: "front", type_key: "kratzer", type_label: "Kratzer", abbr: "KR",
                  color: "#0ea5e9", zone: "Motorhaube", x: 765, y: 300 };

beforeEach(() => {
  vi.clearAllMocks();
  behaelter = document.createElement("div");
  document.body.appendChild(behaelter);
  wurzel = createRoot(behaelter);
});
afterEach(async () => {
  await act(async () => { wurzel.unmount(); });
  behaelter.remove();
});

describe("Lackdicke-Markierung", () => {
  it("nur im Protokoll: eigene Art, Punkt antippen legt eine Messung an (kein Schaden), Wert in µm", async () => {
    await act(async () => { wurzel.render(createElement(Huelle)); });
    expect(el(`damage-type-${LACK_TYP.key}`)).toBeTruthy();
    await klick(el("damage-type-lackdicke"));
    const punkt = behaelter.querySelector("#dmg-front .damage-dot");
    await klick(punkt);
    expect(schaeden).toHaveLength(0);
    expect(lack).toHaveLength(1);
    expect(lack[0]).toMatchObject({ view: "front", wert_um: null });
    expect(lack[0].id).toMatch(/^l-/);
    expect(el("lack-list").textContent).toContain("Lackdicke gemessen (1)");
    expect(el("lack-list").textContent).toContain("nicht in den Kaufvertrag");
    // Marker LD auf der Skizze
    expect(el(`damage-marker-${lack[0].id}`).textContent).toContain("LD");
    await tippen(el(`lack-wert-${lack[0].id}`), "1.250");
    expect(lack[0].wert_um).toBe(1250);
    await tippen(el(`lack-wert-${lack[0].id}`), "");
    expect(lack[0].wert_um).toBeNull();
  });

  it("Tipp auf den LD-Marker mit Lackdicke gewählt entfernt die Messung (mit Rückfrage)", async () => {
    const m = { id: "l1", view: "front", zone: "Motorhaube", x: 765, y: 300, wert_um: 140 };
    await act(async () => { wurzel.render(createElement(Huelle, { startLack: [m] })); });
    await klick(el("damage-type-lackdicke"));
    const bestaetigen = vi.spyOn(window, "confirm").mockReturnValue(true);
    await klick(el("damage-marker-l1"));
    expect(bestaetigen).toHaveBeenCalled();
    expect(lack).toEqual([]);
    bestaetigen.mockRestore();
  });

  it("Werte werden gelesen und gedeckelt", () => {
    expect(lackWert("180")).toBe(180);
    expect(lackWert("1.250")).toBe(1250);
    expect(lackWert("99999")).toBe(5000);
    expect(lackWert("abc")).toBeNull();
    expect(lackWert("")).toBeNull();
  });

  it("Kaufvertrag (ohne onLackChange): keine Lackdicke, keine Fotos, Vertragshinweis bleibt", async () => {
    await act(async () => { wurzel.render(createElement(Huelle, { start: [KRATZER], mitLack: false })); });
    expect(el("damage-type-lackdicke")).toBeNull();
    expect(el("damage-foto-hinzu-s1")).toBeNull();
    expect(el("damage-fotos-zaehler")).toBeNull();
    expect(el("damage-list").textContent).toContain("in den Vertrag übernommen");
  });
});

describe("Fotos je Markierung", () => {
  function fotos(extra = {}) {
    return {
      liste: [{ id: "f1", schaden_id: "s1" }, { id: "f9", schaden_id: "entfernt" }],
      max: 25, sichtTage: 7,
      onHinzu: vi.fn().mockResolvedValue(true),
      onWeg: vi.fn(),
      pfad: (id) => `/driver/appointments/t1/protocol/schaden-fotos/${id}`,
      ...extra,
    };
  }

  it("Vorschau, Entfernen, Hochladen — Fotos entfernter Schäden zählen nicht", async () => {
    const f = fotos();
    await act(async () => { wurzel.render(createElement(Huelle, { start: [KRATZER], fotos: f })); });
    const vorschau = alle('[data-testid="damage-fotos-s1"] [data-testid="foto-vorschau"]');
    expect(vorschau).toHaveLength(1);
    expect(vorschau[0].getAttribute("data-pfad")).toBe("/driver/appointments/t1/protocol/schaden-fotos/f1");
    expect(el("damage-fotos-zaehler").textContent).toContain("Fotos: 1 von 25");
    expect(el("damage-fotos-zaehler").textContent).toContain("7 Tage");
    await klick(el("damage-foto-weg-f1"));
    expect(f.onWeg).toHaveBeenCalledWith("f1");
    // zwei Fotos auswählen -> nacheinander hochgeladen
    const input = el("damage-foto-input-s1");
    const a = new File(["a"], "a.jpg", { type: "image/jpeg" });
    const b = new File(["b"], "b.jpg", { type: "image/jpeg" });
    Object.defineProperty(input, "files", { value: [a, b], configurable: true });
    await act(async () => { input.dispatchEvent(new Event("change", { bubbles: true })); });
    expect(f.onHinzu.mock.calls).toEqual([["s1", a], ["s1", b]]);
  });

  it("bricht ab, wenn ein Foto scheitert; bei voller Grenze ist der Knopf gesperrt", async () => {
    const f = fotos({ onHinzu: vi.fn().mockResolvedValue(false) });
    await act(async () => { wurzel.render(createElement(Huelle, { start: [KRATZER], fotos: f })); });
    const input = el("damage-foto-input-s1");
    Object.defineProperty(input, "files", { value: [new File(["a"], "a.jpg"), new File(["b"], "b.jpg")],
                                            configurable: true });
    await act(async () => { input.dispatchEvent(new Event("change", { bubbles: true })); });
    expect(f.onHinzu).toHaveBeenCalledTimes(1);

    const voll = fotos({ liste: Array.from({ length: 25 }, (_, i) => ({ id: `f${i}`, schaden_id: "s1" })) });
    await act(async () => { wurzel.render(createElement(Huelle, { start: [KRATZER], fotos: voll })); });
    expect(el("damage-foto-input-s1").disabled).toBe(true);
    expect(el("damage-foto-hinzu-s1").getAttribute("title")).toContain("Höchstens 25");
  });

  it("auch Lackdicke-Messungen haben einen Foto-Knopf", async () => {
    const m = { id: "l1", view: "top", zone: "Dach", x: 700, y: 300, wert_um: 95 };
    await act(async () => { wurzel.render(createElement(Huelle, { startLack: [m], fotos: fotos() })); });
    expect(el("damage-foto-hinzu-l1")).toBeTruthy();
  });
});
