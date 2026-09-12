/**
 * Gegenprüfung 12.09.2026: "15.000" wurde als 15 € freigegeben — und landete
 * so im unterschriebenen Abholprotokoll und im Kaufvorgang.
 */
import { describe, expect, it } from "vitest";
import { preisAusText, preisText } from "./preis";

describe("preisAusText", () => {
  it("liest deutsche Tausenderpunkte richtig", () => {
    expect(preisAusText("15.000")).toBe(15000);
    expect(preisAusText("17.250")).toBe(17250);
    expect(preisAusText("1.250.000")).toBe(1250000);
  });

  it("liest Komma als Dezimaltrenner", () => {
    expect(preisAusText("17250,50")).toBe(17250.5);
    expect(preisAusText("17.250,50")).toBe(17250.5);
    expect(preisAusText("0,99")).toBe(0.99);
  });

  it("versteht auch die einfache Schreibweise", () => {
    expect(preisAusText("15000")).toBe(15000);
    expect(preisAusText(" 15000 €")).toBe(15000);
    expect(preisAusText("15 000")).toBe(15000);
    expect(preisAusText(0)).toBe(0);
  });

  it("lässt einen einzelnen Punkt als Dezimalpunkt gelten", () => {
    // Kein Dreierblock -> kann kein Tausenderpunkt sein.
    expect(preisAusText("15.5")).toBe(15.5);
    expect(preisAusText("1234.56")).toBe(1234.56);
  });

  it("meldet Unsinn als null statt still etwas zu raten", () => {
    expect(preisAusText("")).toBeNull();
    expect(preisAusText("   ")).toBeNull();
    expect(preisAusText("abc")).toBeNull();
    expect(preisAusText("12,34,56")).toBeNull();
    expect(preisAusText("-500")).toBeNull();
    expect(preisAusText(null)).toBeNull();
    expect(preisAusText(undefined)).toBeNull();
  });
});

describe("preisText", () => {
  it("zeigt deutsche Beträge", () => {
    expect(preisText(17250)).toMatch(/17\.250,00/);
    expect(preisText(0)).toMatch(/0,00/);
  });

  it("zeigt einen Strich, wenn nichts da ist", () => {
    expect(preisText(null)).toBe("—");
    expect(preisText(undefined)).toBe("—");
    expect(preisText("")).toBe("—");
  });
});
