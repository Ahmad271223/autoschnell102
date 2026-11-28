import { describe, expect, it, vi } from "vitest";

vi.mock("sonner", () => ({ toast: { success: vi.fn(), error: vi.fn(), info: vi.fn(), warning: vi.fn() } }));

const { POOL_ANZEIGE_MAX, neuesteFahrzeuge } = await import("./Fahrzeugpool");

// Entscheidung Ahmad 22.09.2026: Der Fahrzeugpool zeigt nur die letzten 60.
describe("neuesteFahrzeuge", () => {
  it("zeigt hoechstens 60, die neuesten zuerst", () => {
    expect(POOL_ANZEIGE_MAX).toBe(60);
    const liste = Array.from({ length: 75 }, (_, i) => ({
      id: `v${i}`, updated_at: new Date(Date.UTC(2026, 8, 1, 0, i)).toISOString(),
    }));
    const erg = neuesteFahrzeuge(liste);
    expect(erg).toHaveLength(60);
    expect(erg[0].id).toBe("v74");
    expect(erg[59].id).toBe("v15");
  });

  it("laesst kleine Listen unveraendert und vertraegt fehlende Zeiten", () => {
    const erg = neuesteFahrzeuge([{ id: "a" }, { id: "b", updated_at: "2026-09-22T10:00:00Z" }]);
    expect(erg.map((v) => v.id)).toEqual(["b", "a"]);
    expect(neuesteFahrzeuge(null)).toEqual([]);
  });
});
