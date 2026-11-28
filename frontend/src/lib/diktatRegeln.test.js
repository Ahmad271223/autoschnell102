import { describe, expect, it } from "vitest";
import { fachwoerter, nachbearbeiten, sprachbefehle, zahlAusSprache, zahlwort } from "./diktatRegeln";

describe("Sprachbefehle", () => {
  it("macht aus Befehlen Zeichen, auch wenn Whisper selbst Satzzeichen setzt", () => {
    expect(sprachbefehle("Kratzer Komma Delle Punkt")).toBe("Kratzer, Delle.");
    expect(sprachbefehle("Kratzer, Komma, Delle, Punkt.")).toBe("Kratzer, Delle.");
    expect(sprachbefehle("Kratzer hinten neue Zeile Delle vorne")).toBe("Kratzer hinten\nDelle vorne");
    expect(sprachbefehle("Achtung Doppelpunkt zwei Schlüssel")).toBe("Achtung: zwei Schlüssel");
    expect(sprachbefehle("alles gut Punkt zwei Schlüssel Punkt")).toBe("Alles gut. Zwei Schlüssel.");
  });
  it("lässt Wörter in Ruhe, die nur so ähnlich heißen", () => {
    expect(sprachbefehle("Druckpunkt der Kupplung hoch")).toBe("Druckpunkt der Kupplung hoch");
    expect(sprachbefehle("Kommata")).toBe("Kommata");
  });
  it("ändert normalen Text nicht", () => {
    expect(sprachbefehle("Kratzer an der Stoßstange hinten links.")).toBe("Kratzer an der Stoßstange hinten links.");
  });
});

describe("Fachwörter", () => {
  it("schreibt Abkürzungen richtig", () => {
    expect(fachwoerter("A H K abnehmbar, Tüv neu, H.U. bis 2027")).toBe("AHK abnehmbar, TÜV neu, HU bis 2027");
    expect(fachwoerter("Zahn Riemen neu, Stein Schlag")).toBe("Zahnriemen neu, Steinschlag");
    // so kam es echt aus Whisper (Messung 24.11.2026)
    expect(fachwoerter("Das Auto ist checkheft gepflegt, Checkheft liegt bei"))
      .toBe("Das Auto ist scheckheftgepflegt, Scheckheft liegt bei");
  });
  it("lässt normale Wörter stehen", () => {
    expect(fachwoerter("Hupe geht, Hut ab")).toBe("Hupe geht, Hut ab");
  });
  it("beides zusammen", () => {
    expect(nachbearbeiten("Tüv neu Komma A H K")).toBe("TÜV neu, AHK");
  });
});

describe("Zahlen", () => {
  it("liest Zahlwörter", () => {
    expect(zahlwort("hundertzwanzigtausend")).toBe(120000);
    expect(zahlwort("dreiundachtzigtausendfünfhundert")).toBe(83500);
    expect(zahlwort("fünfzehntausend")).toBe(15000);
    expect(zahlwort("zweihundertfünfzigtausend")).toBe(250000);
    expect(zahlwort("eine Million")).toBe(1000000);
    expect(zahlwort("Kratzer")).toBeNull();
  });
  it("liest, was Whisper schreibt", () => {
    expect(zahlAusSprache("120.000 Kilometer.")).toBe(120000);
    expect(zahlAusSprache("Kilometerstand 86 000")).toBe(86000);
    expect(zahlAusSprache("15.000 Euro")).toBe(15000);
    expect(zahlAusSprache("120 tausend")).toBe(120000);
    expect(zahlAusSprache("Hundertzwanzigtausend Kilometer")).toBe(120000);
    expect(zahlAusSprache("ungefähr 12,5 Tausend")).toBe(12500);
  });
  it("gibt null bei Unklarem", () => {
    expect(zahlAusSprache("weiß ich nicht")).toBeNull();
    expect(zahlAusSprache("")).toBeNull();
  });
});
