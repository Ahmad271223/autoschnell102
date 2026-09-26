/*
 * Prüfbericht 20.09.2026 — Nachträge vom 21.09.2026.
 *  U-54  Termine: mit eingeteiltem Fahrer entsteht "abgeholt"/"erledigt" nur
 *        über das unterschriebene Protokoll — die Oberfläche bietet den
 *        Handweg nicht mehr an (der Server lehnte ihn ohnehin ab).
 */
import { describe, expect, it, vi } from "vitest";

vi.mock("sonner", () => ({ toast: { success: vi.fn(), error: vi.fn(), info: vi.fn(), warning: vi.fn() } }));

const { statusSperre } = await import("./Termine");

describe("U-54: statusSperre mit Fahrer", () => {
  it("mit Fahrer und ohne Protokoll: abgeholt/erledigt gesperrt — auch für den Chef", () => {
    for (const ziel of ["abgeholt", "erledigt"]) {
      expect(statusSperre(ziel, "offen", true, { fahrer: true })).toMatch(/Abholprotokoll/);
      expect(statusSperre(ziel, "offen", false, { fahrer: true })).toMatch(/Abholprotokoll/);
    }
  });
  it("gilt auch beim Anlegen (kein bisheriger Status)", () => {
    expect(statusSperre("abgeholt", null, true, { fahrer: true })).toMatch(/Abholprotokoll/);
  });
  it("andere Status bleiben mit Fahrer frei", () => {
    expect(statusSperre("storniert", "offen", true, { fahrer: true })).toBeNull();
    expect(statusSperre("nicht abgeholt", "offen", true, { fahrer: true })).toBeNull();
  });
  it("liegt das unterschriebene Protokoll vor, darf der Chef wieder schließen", () => {
    expect(statusSperre("abgeholt", "offen", true, { fahrer: true, protokoll: true })).toBeNull();
  });
  it("ohne Fahrer bleibt der Handweg offen (Abholung durch den Händler selbst)", () => {
    expect(statusSperre("abgeholt", "offen", true, { fahrer: false })).toBeNull();
    expect(statusSperre("abgeholt", "offen", true)).toBeNull();
  });
  it("der schon gesetzte Status bleibt anwählbar", () => {
    expect(statusSperre("abgeholt", "abgeholt", true, { fahrer: true })).toBeNull();
  });
});
