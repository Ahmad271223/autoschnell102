/**
 * Sprachdiktat (Wunsch Ahmad 24.11.2026): Knopf nur in der iPhone-App mit
 * Plugin "SprachDiktat"; im Browser/Android unsichtbar. Erkannter Text wird
 * angehängt, nie ersetzt.
 */
import { act, createElement } from "react";
import { createRoot } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

globalThis.IS_REACT_ACT_ENVIRONMENT = true;
vi.mock("sonner", () => ({ toast: { success: vi.fn(), error: vi.fn(), info: vi.fn(), warning: vi.fn() } }));

const { default: DiktatKnopf } = await import("./DiktatKnopf");
const { _diktatMerkerLeeren, textAnhaengen } = await import("@/lib/sprachdiktat");

let wurzel;
let behaelter;
async function rendern(el) {
  behaelter = document.createElement("div");
  document.body.appendChild(behaelter);
  wurzel = createRoot(behaelter);
  await act(async () => { wurzel.render(el); });
  await act(async () => { await Promise.resolve(); });
}
const knopf = () => behaelter.querySelector('[data-testid="diktat-knopf"]');

function fakeCapacitor({ phase = "bereit", heruntergeladen = true, text = "Kratzer hinten links" } = {}) {
  const aufrufe = [];
  window.Capacitor = {
    getPlatform: () => "ios",
    nativePromise: vi.fn(async (plugin, methode) => {
      aufrufe.push(`${plugin}.${methode}`);
      if (methode === "status") return { phase, fortschritt: 0, groesseMB: 632, heruntergeladen, fehler: "" };
      if (methode === "aufnahmeStoppen") return { text };
      return {};
    }),
  };
  return aufrufe;
}

beforeEach(() => { _diktatMerkerLeeren(); });
afterEach(async () => {
  if (wurzel) await act(async () => { wurzel.unmount(); });
  behaelter?.remove();
  wurzel = null;
  delete window.Capacitor;
  vi.restoreAllMocks();
});

describe("DiktatKnopf", () => {
  it("ist im Browser ohne App-Hülle unsichtbar", async () => {
    await rendern(createElement(DiktatKnopf, { onText: () => {} }));
    expect(knopf()).toBeNull();
  });

  it("ist auf Android (Capacitor, aber nicht iOS) unsichtbar", async () => {
    window.Capacitor = { getPlatform: () => "android", nativePromise: vi.fn() };
    await rendern(createElement(DiktatKnopf, { onText: () => {} }));
    expect(knopf()).toBeNull();
    expect(window.Capacitor.nativePromise).not.toHaveBeenCalled();
  });

  it("nimmt auf und übergibt den erkannten Text", async () => {
    const aufrufe = fakeCapacitor();
    const onText = vi.fn();
    await rendern(createElement(DiktatKnopf, { onText }));
    expect(knopf()).not.toBeNull();
    expect(knopf().textContent).toContain("Diktieren");

    await act(async () => { knopf().click(); });
    expect(aufrufe).toContain("SprachDiktat.aufnahmeStarten");
    expect(knopf().textContent).toContain("Stopp");

    await act(async () => { knopf().click(); });
    expect(aufrufe).toContain("SprachDiktat.aufnahmeStoppen");
    expect(onText).toHaveBeenCalledWith("Kratzer hinten links");
    expect(knopf().textContent).toContain("Diktieren");
  });

  it("fragt vor dem ersten Modell-Download nach und lädt bei Nein nichts", async () => {
    const aufrufe = fakeCapacitor({ phase: "fehlt", heruntergeladen: false });
    const confirm = vi.spyOn(window, "confirm").mockReturnValue(false);
    await rendern(createElement(DiktatKnopf, { onText: () => {} }));
    await act(async () => { knopf().click(); });
    expect(confirm).toHaveBeenCalledOnce();
    expect(confirm.mock.calls[0][0]).toContain("632 MB");
    expect(aufrufe).not.toContain("SprachDiktat.vorbereiten");
    expect(aufrufe).not.toContain("SprachDiktat.aufnahmeStarten");
  });

  it("bricht eine laufende Aufnahme beim Schließen ab", async () => {
    const aufrufe = fakeCapacitor();
    await rendern(createElement(DiktatKnopf, { onText: () => {} }));
    await act(async () => { knopf().click(); });
    await act(async () => { wurzel.unmount(); });
    wurzel = null;
    expect(aufrufe).toContain("SprachDiktat.abbrechen");
  });
});

describe("textAnhaengen", () => {
  it("hängt an statt zu ersetzen", () => {
    expect(textAnhaengen("", "Delle Tür")).toBe("Delle Tür");
    expect(textAnhaengen("Kratzer", "Delle Tür")).toBe("Kratzer Delle Tür");
    expect(textAnhaengen("Kratzer.", "Delle Tür")).toBe("Kratzer.\nDelle Tür");
    expect(textAnhaengen("abc", "   ")).toBe("abc");
  });
  it("hält die Feldgrenze ein", () => {
    expect(textAnhaengen("12345", "6789", 8)).toBe("12345 67");
  });
});
