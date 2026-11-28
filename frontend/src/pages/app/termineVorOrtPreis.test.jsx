import { describe, expect, it, vi } from "vitest";

vi.mock("sonner", () => ({ toast: { success: vi.fn(), error: vi.fn(), info: vi.fn(), warning: vi.fn() } }));

const { vorOrtPreisSichtbar } = await import("./Termine");

// Entscheidung Ahmad 22.09.2026: Bei einer Abholung OHNE Abholprotokoll gibt
// es im Termin wieder das Feld "vor Ort vereinbarter Preis" — sonst kaeme der
// Preis nur ueber Protokoll + Freigabe in den Vertrag, und eine Buero-Abholung
// haette keinen Weg, den vor Ort ausgehandelten Preis festzuhalten.
describe("vorOrtPreisSichtbar", () => {
  it("zeigt das Feld nur bei abgeholt/erledigt ohne Protokoll", () => {
    expect(vorOrtPreisSichtbar("abgeholt", false)).toBe(true);
    expect(vorOrtPreisSichtbar("erledigt", false)).toBe(true);
  });

  it("versteckt es, sobald ein Protokoll da ist (Preis kommt ueber die Freigabe)", () => {
    expect(vorOrtPreisSichtbar("abgeholt", true)).toBe(false);
    expect(vorOrtPreisSichtbar("erledigt", true)).toBe(false);
  });

  it("versteckt es bei offenen, stornierten und neuen Terminen", () => {
    for (const status of ["offen", "geplant", "unterwegs", "storniert", "nicht_abgeholt", undefined, ""]) {
      expect(vorOrtPreisSichtbar(status, false)).toBe(false);
    }
    expect(vorOrtPreisSichtbar("abgeholt", false, true)).toBe(false);
  });
});
