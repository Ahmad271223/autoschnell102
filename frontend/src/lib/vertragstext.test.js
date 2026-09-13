import { wurdeZusammengefuehrt, zusammenfuehren } from "./vertragstext";

const AGB = "AGB:\n1. Keine Gewährleistung.";
const BED = "1. Der Verkäufer übernimmt keine Garantie.\n\n2. Absagen nicht wirksam.";

describe("zusammenfuehren", () => {
  it("hängt die alten AGB unten an die Vertragsbedingungen", () => {
    expect(zusammenfuehren(AGB, BED)).toBe(`${BED}\n\n${AGB}`);
  });

  it("liefert den vorhandenen Text, wenn der andere leer ist", () => {
    expect(zusammenfuehren("", BED)).toBe(BED);
    expect(zusammenfuehren(AGB, "")).toBe(AGB);
    expect(zusammenfuehren("   ", "  ")).toBe("");
    expect(zusammenfuehren(null, undefined)).toBe("");
  });

  it("doppelt nichts, wenn der AGB-Text schon enthalten ist", () => {
    const schon = `${BED}\n\n${AGB}`;
    expect(zusammenfuehren(AGB, schon)).toBe(schon);
    expect(zusammenfuehren(AGB, schon)).toBe(zusammenfuehren(AGB, zusammenfuehren(AGB, BED)));
  });
});

describe("wurdeZusammengefuehrt", () => {
  it("meldet nur, wenn wirklich etwas dazukam", () => {
    expect(wurdeZusammengefuehrt(AGB, BED)).toBe(true);
    expect(wurdeZusammengefuehrt("", BED)).toBe(false);
    expect(wurdeZusammengefuehrt(AGB, `${BED}\n\n${AGB}`)).toBe(false);
  });
});
