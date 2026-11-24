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

function fakeCapacitor({ phase = "bereit", heruntergeladen = true, text = "Kratzer hinten links", aufnahmeStatus = {} } = {}) {
  const aufrufe = [];
  window.Capacitor = {
    getPlatform: () => "ios",
    nativePromise: vi.fn(async (plugin, methode) => {
      aufrufe.push(`${plugin}.${methode}`);
      if (methode === "status") return { phase, fortschritt: 0, groesseMB: 632, heruntergeladen, fehler: "", ...aufnahmeStatus };
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

const warten = (ms) => act(async () => { await new Promise((r) => setTimeout(r, ms)); });

describe("DiktatKnopf — Pegel, Auto-Stopp, Rückgängig, Zahlen (24.11.2026)", () => {
  it("hört nach 2 s Stille von selbst auf und zeigt vorher den Pegel", async () => {
    const aufrufe = fakeCapacitor({ aufnahmeStatus: { aufnahme: true, pegel: 0.7, gesprochen: true, stille_ms: 2100 } });
    const onText = vi.fn();
    await rendern(createElement(DiktatKnopf, { onText }));
    await act(async () => { knopf().click(); });
    expect(behaelter.querySelector('[data-testid="diktat-knopf-pegel"]')).not.toBeNull();
    await warten(350);
    expect(aufrufe).toContain("SprachDiktat.aufnahmeStoppen");
    expect(onText).toHaveBeenCalledWith("Kratzer hinten links");
  });

  it("hört nicht auf, solange noch nichts gesagt wurde", async () => {
    const aufrufe = fakeCapacitor({ aufnahmeStatus: { aufnahme: true, pegel: 0, gesprochen: false, stille_ms: 5000 } });
    await rendern(createElement(DiktatKnopf, { onText: () => {} }));
    await act(async () => { knopf().click(); });
    await warten(350);
    expect(aufrufe).not.toContain("SprachDiktat.aufnahmeStoppen");
  });

  it("erkennt nichts, wenn nur Stille aufgenommen wurde (Whisper würde Wörter erfinden)", async () => {
    const aufrufe = fakeCapacitor({ text: "Amen.", aufnahmeStatus: { aufnahme: true, pegel: 0, gesprochen: false, stille_ms: 0 } });
    const onText = vi.fn();
    await rendern(createElement(DiktatKnopf, { onText }));
    await act(async () => { knopf().click(); });
    await warten(250);
    await act(async () => { knopf().click(); });
    expect(aufrufe).toContain("SprachDiktat.abbrechen");
    expect(aufrufe).not.toContain("SprachDiktat.aufnahmeStoppen");
    expect(onText).not.toHaveBeenCalled();
  });

  it("setzt Sprachbefehle um, hängt an und kann es rückgängig machen", async () => {
    fakeCapacitor({ text: "Delle Komma Tüv neu" });
    let wert = "Kratzer.";
    const onChange = vi.fn((v) => { wert = v; });
    await rendern(createElement(DiktatKnopf, { wert, onChange }));
    await act(async () => { knopf().click(); });
    await act(async () => { knopf().click(); });
    expect(onChange).toHaveBeenLastCalledWith("Kratzer.\nDelle, TÜV neu");
    await act(async () => { wurzel.render(createElement(DiktatKnopf, { wert, onChange })); });
    const rueck = behaelter.querySelector('[data-testid="diktat-knopf-rueckgaengig"]');
    expect(rueck).not.toBeNull();
    await act(async () => { rueck.click(); });
    expect(onChange).toHaveBeenLastCalledWith("Kratzer.");
  });

  it("macht nichts rückgängig, wenn das Feld inzwischen geändert wurde", async () => {
    fakeCapacitor({ text: "Delle" });
    const onChange = vi.fn();
    await rendern(createElement(DiktatKnopf, { wert: "", onChange }));
    await act(async () => { knopf().click(); });
    await act(async () => { knopf().click(); });
    await act(async () => { wurzel.render(createElement(DiktatKnopf, { wert: "Delle und mehr", onChange })); });
    await act(async () => { behaelter.querySelector('[data-testid="diktat-knopf-rueckgaengig"]').click(); });
    expect(onChange).toHaveBeenCalledTimes(1);
  });

  it("Mängel: jedes Diktat in eine eigene Zeile", async () => {
    fakeCapacitor({ text: "Zahnriemen fällig" });
    const onChange = vi.fn();
    await rendern(createElement(DiktatKnopf, { wert: "Kupplung rutscht", onChange, neueZeile: true }));
    await act(async () => { knopf().click(); });
    await act(async () => { knopf().click(); });
    expect(onChange).toHaveBeenCalledWith("Kupplung rutscht\nZahnriemen fällig");
  });

  it("Zahl sprechen: erst nach Bestätigung übernehmen", async () => {
    fakeCapacitor({ text: "Hundertzwanzigtausend Kilometer." });
    const confirm = vi.spyOn(window, "confirm").mockReturnValue(true);
    const onZahl = vi.fn();
    await rendern(createElement(DiktatKnopf, { zahl: "km", onZahl }));
    await act(async () => { knopf().click(); });
    await act(async () => { knopf().click(); });
    expect(confirm.mock.calls[0][0]).toBe("Erkannt: 120.000 km — übernehmen?");
    expect(onZahl).toHaveBeenCalledWith(120000);
  });

  it("Zahl sprechen: Nein übernimmt nichts, Unklares auch nicht", async () => {
    fakeCapacitor({ text: "15.000 Euro" });
    vi.spyOn(window, "confirm").mockReturnValue(false);
    const onZahl = vi.fn();
    await rendern(createElement(DiktatKnopf, { zahl: "preis", onZahl }));
    await act(async () => { knopf().click(); });
    await act(async () => { knopf().click(); });
    expect(onZahl).not.toHaveBeenCalled();
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
