import { describe, expect, it } from "vitest";
import {
  argumenteText, eur, kiStatusText, kiWartet, mitAntwort, nachPrioritaet, schwereFragen,
  schwereOffen, schwereText,
} from "./kiSchaden";

// Wunsch Ahmad 25.09.2026: KI-Schadennachlass — Zusatzfragen je Schadensart
// und Aufbereitung der Antwort.
describe("kiSchaden", () => {
  it("kennt Zusatzfragen fuer Delle und Kratzer, keine fuer unbekannte Arten", () => {
    expect(schwereFragen("delle").map((f) => f.key)).toEqual(["groesse", "lack"]);
    expect(schwereFragen("kratzer").map((f) => f.key)).toEqual(["laenge", "tiefe"]);
    expect(schwereFragen("sonstwas")).toEqual([]);
  });

  it("merkt Antworten am Schaden und kann sie wieder abwaehlen", () => {
    const d = { id: "1", type_key: "delle" };
    const a = mitAntwort(d, "groesse", "2–5 cm");
    expect(a.severity_data).toEqual({ groesse: "2–5 cm" });
    expect(schwereOffen(a)).toEqual(["Lack beschädigt?"]);
    const b = mitAntwort(a, "lack", "nein");
    expect(schwereText(b)).toBe("2–5 cm · nein");
    expect(schwereOffen(b)).toEqual([]);
    expect(mitAntwort(b, "lack", "nein").severity_data).toEqual({ groesse: "2–5 cm" });
  });

  it("gruppiert Positionen nach Prioritaet und formatiert Euro", () => {
    const g = nachPrioritaet([{ priority: "rot" }, { priority: "gelb" }, { priority: "x" }, { priority: "orange" }]);
    expect(g.rot).toHaveLength(1);
    expect(g.orange).toHaveLength(1);
    expect(g.gelb).toHaveLength(2);
    expect(eur(1350)).toBe("1.350 €");
    expect(eur(null)).toBe("—");
    expect(argumenteText(["a", "b"])).toBe("1. a\n2. b");
  });

  it("weiss, wann die Karte noch wartet und was sie sagt", () => {
    expect(kiWartet("laeuft")).toBe(true);
    expect(kiWartet("veraltet")).toBe(true);
    expect(kiWartet("ok")).toBe(false);
    expect(kiStatusText("keine")).toMatch(/Keine preisrelevanten/);
    expect(kiStatusText("zeitlimit")).toMatch(/nicht verfügbar/);
    expect(kiStatusText("ok")).toBe("");
  });
});
