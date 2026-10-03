/*
 * Rollenprüfung 22.09.2026 — Team termine_protokoll, Freigaben:
 *  RP-453  verworfener Fahrer-Vorschlag wird als verworfen angezeigt
 *  RP-465  FIN/HU am Handy nicht abgeschnitten (eine Spalte, Umbruch)
 *  RP-499  getippter Preis übersteht „Zurück an den Fahrer“
 */
import { describe, expect, it, vi } from "vitest";

vi.mock("sonner", () => ({ toast: { success: vi.fn(), error: vi.fn(), info: vi.fn(), warning: vi.fn() } }));

const { vorschlagHinweis, entwuerfeAbgleichen } = await import("./Freigaben");
const { default: QUELLE } = await import("./Freigaben.jsx?raw");

describe("RP-453: verworfener Vorschlag", () => {
  it("verworfen steht dran, sonst der bisherige Hinweis", () => {
    expect(vorschlagHinweis({ preis_vorschlag_verworfen: true, neuer_preis: null }, false))
      .toMatch(/verworfen/);
    expect(vorschlagHinweis({ neuer_preis: null }, false)).toMatch(/gilt, wenn du ohne eigenen Preis/);
    expect(vorschlagHinweis({ neuer_preis: 9000 }, false)).toBe("");
    expect(vorschlagHinweis({ neuer_preis: null }, true)).toBe("");
  });
});

describe("RP-499: Preis übersteht 'Zurück an den Fahrer'", () => {
  const jetzt = Date.UTC(2026, 8, 22, 12, 0, 0);
  it("Entwürfe verschwundener Protokolle fallen weg, gemerkte Preise bleiben gemerkt", () => {
    const { entwurf, gemerkt } = entwuerfeAbgleichen(
      { p1: { preis: "15.000" }, p2: { preis: "9.000" } }, new Set(["p2"]),
      { p1: { preis: "15.000", am: jetzt } }, jetzt);
    expect(entwurf).toEqual({ p2: { preis: "9.000" } });
    expect(gemerkt).toEqual({ p1: { preis: "15.000", am: jetzt } });
  });
  it("kommt das Protokoll zurück, steht der Preis wieder im Feld", () => {
    const { entwurf, gemerkt } = entwuerfeAbgleichen(
      {}, new Set(["p1"]), { p1: { preis: "15.000", am: jetzt } }, jetzt);
    expect(entwurf).toEqual({ p1: { preis: "15.000" } });
    expect(gemerkt).toEqual({});
  });
  it("neu Getipptes geht vor, alte Merker verfallen", () => {
    const acht = jetzt - 8 * 24 * 3600 * 1000;
    const { entwurf, gemerkt } = entwuerfeAbgleichen(
      { p1: { preis: "14.000" } }, new Set(["p1"]),
      { p1: { preis: "15.000", am: jetzt }, p9: { preis: "1", am: acht } }, jetzt);
    expect(entwurf).toEqual({ p1: { preis: "14.000" } });
    expect(gemerkt).toEqual({});
  });
  it("senden merkt den Preis beim Zurückschicken", () => {
    const senden = QUELLE.split("const senden = async")[1].split("const wartend =")[0];
    expect(senden).toMatch(/if \(zurueck && String\(eigener\.preis \?\? ""\)\.trim\(\)\)/);
    expect(senden).toMatch(/preisMerkerSchreiben\(m\)/);
  });
});

describe("RP-465: Vergleich am Handy", () => {
  it("eine Spalte unter sm, Werte umbrechen", () => {
    expect(QUELLE).toMatch(/grid grid-cols-1 sm:grid-cols-\[minmax\(0,9rem\)_1fr\] gap-x-3 gap-y-0\.5/);
    expect(QUELLE).toMatch(/text-\[13px\] min-w-0 \[overflow-wrap:anywhere\]/);
    expect(QUELLE).toMatch(/<span className="block sm:inline">/);
  });
});
