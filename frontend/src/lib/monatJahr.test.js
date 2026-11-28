/**
 * Wunsch Ahmad (12.09.2026): Erstzulassung und TÜV nur mit Ziffern, der "/"
 * kommt nach zwei Monatsziffern von selbst, danach vier Jahresziffern.
 */
import { describe, expect, it } from "vitest";
import {
  monatJahrAusText, monatJahrFehler, monatJahrPruefen, monatJahrText, monatJahrTippen, ziffernVorCursor,
} from "./monatJahr";

/** Tippt Zeichen fuer Zeichen wie am Handy und liefert den Feldinhalt. */
function tippe(zeichen, start = "") {
  let wert = start;
  for (const z of zeichen) wert = monatJahrTippen(wert + z, wert).wert;
  return wert;
}

describe("monatJahrTippen", () => {
  it("setzt den Schrägstrich nach zwei Monatsziffern selbst", () => {
    expect(tippe("12")).toBe("12/");
    expect(tippe("122020")).toBe("12/2020");
    expect(tippe("09")).toBe("09/");
    expect(tippe("092026")).toBe("09/2026");
  });

  it("eine erste Ziffer 2–9 ist sofort der Monat", () => {
    expect(tippe("9")).toBe("09/");
    expect(tippe("92020")).toBe("09/2020");
  });

  it("nimmt nur Ziffern und höchstens vier Jahresziffern", () => {
    expect(tippe("1a2b/2x0-2y0")).toBe("12/2020");
    expect(tippe("1220201")).toBe("12/2020");
  });

  it("lehnt Monate außerhalb 01–12 ab", () => {
    expect(monatJahrTippen("13", "1")).toEqual({ wert: "1", hinweis: "Monat 01–12" });
    expect(monatJahrTippen("00", "0")).toEqual({ wert: "0", hinweis: "Monat 01–12" });
  });

  it("liest einen einstelligen Monat mit Trenner", () => {
    expect(monatJahrTippen("1/", "1").wert).toBe("01/");
    expect(monatJahrTippen("3.", "03/").wert).toBe("03/");
  });

  it("ein getippter Schrägstrich hinter dem automatischen stört nicht", () => {
    expect(monatJahrTippen("12//", "12/").wert).toBe("12/");
  });

  it("Löschen hängt nichts an und bleibt nicht am Schrägstrich hängen", () => {
    expect(monatJahrTippen("12", "12/", { loeschen: true }).wert).toBe("1");
    expect(monatJahrTippen("12/202", "12/2020", { loeschen: true }).wert).toBe("12/202");
    expect(monatJahrTippen("12/", "12/2", { loeschen: true }).wert).toBe("12");
    expect(monatJahrTippen("", "1", { loeschen: true }).wert).toBe("");
  });

  // Gegenpruefung 12.09.2026: Monat im fertigen Wert markieren und ueberschreiben.
  it("Monat überschreiben behält das Jahr", () => {
    expect(monatJahrTippen("1/2020", "12/2020").wert).toBe("1/2020");     // wartet auf 2. Ziffer
    expect(monatJahrTippen("11/2020", "1/2020").wert).toBe("11/2020");
    expect(monatJahrTippen("0/2020", "05/2020").wert).toBe("0/2020");     // Jahr bleibt
    expect(monatJahrTippen("09/2020", "0/2020").wert).toBe("09/2020");
    expect(monatJahrTippen("5/2020", "12/2020").wert).toBe("05/2020");
  });

  it("ungültiger Monat lässt den bisherigen Wert stehen", () => {
    expect(monatJahrTippen("19/2020", "1/2020")).toEqual({ wert: "1/2020", hinweis: "Monat 01–12" });
    expect(monatJahrTippen("132020", "12/2020")).toEqual({ wert: "12/2020", hinweis: "Monat 01–12" });
  });

  it("Rücktaste direkt hinter dem / löscht die Monatsziffer davor", () => {
    expect(monatJahrTippen("052020", "05/2020", { loeschen: true }).wert).toBe("0/2020");
    expect(monatJahrTippen("52020", "5/2020", { loeschen: true }).wert).toBe("/2020");
  });
});

describe("ziffernVorCursor", () => {
  it("am Ende getippt: Cursor ans Ende", () => {
    expect(ziffernVorCursor("9", 1, "09/")).toBe(Number.POSITIVE_INFINITY);
    expect(ziffernVorCursor("12/202", 6, "12/202")).toBe(Number.POSITIVE_INFINITY);
  });

  it("Rücktaste hinter dem /: Cursor bleibt hinter der verbliebenen Monatsziffer", () => {
    // "11/|2020" + Rücktaste -> Feld "11|2020" (Cursor 2), Regel macht "1/2020"
    expect(ziffernVorCursor("112020", 2, "1/2020")).toBe(1);
  });

  it("führende 0 schiebt den Cursor mit", () => {
    expect(ziffernVorCursor("5/2020", 1, "05/2020")).toBe(2);
  });

  it("abgelehnte Ziffer: Cursor bleibt davor", () => {
    expect(ziffernVorCursor("19/2020", 2, "1/2020")).toBe(1);
  });
});

describe("monatJahrFehler", () => {
  it.each(["1", "12", "06/20", "1/2020", "/2020", "12/"])("sperrt halb getippt: %s", (w) => {
    expect(monatJahrFehler(w)).toBe("Bitte MM/JJJJ vollständig eingeben");
  });

  it("sperrt einen unmöglichen Monat", () => {
    expect(monatJahrFehler("13/2020")).toBe("Monat 01–12");
  });

  it.each(["", "12/2020", "2018", "Neu", "keine HU", "032020"])("lässt %s durch", (w) => {
    expect(monatJahrFehler(w)).toBe("");
  });
});

describe("monatJahrAusText", () => {
  const heute = new Date(2026, 8, 12);
  it.each([
    ["01/2020", "01/2020"],
    ["1/2020", "01/2020"],
    ["1.2020", "01/2020"],
    [" 3 / 2019 ", "03/2019"],
    ["2020-01", "01/2020"],
    ["2020/1", "01/2020"],
    ["01.03.2020", "03/2020"],
    ["032020", "03/2020"],
    ["März 2019", "03/2019"],
    ["dez. 2020", "12/2020"],
  ])("liest %s", (eingabe, erwartet) => {
    expect(monatJahrAusText(eingabe, { heute })).toBe(erwartet);
  });

  it("zweistellige Jahre: HU in diesem Jahrhundert, EZ je nach Jahr", () => {
    expect(monatJahrAusText("06/26", { art: "hu", heute })).toBe("06/2026");
    expect(monatJahrAusText("01/95", { art: "ez", heute })).toBe("01/1995");
    expect(monatJahrAusText("01/20", { art: "ez", heute })).toBe("01/2020");
  });

  it.each(["", "2018", "Neu", "13/2020", "00/2020", "HU bis", "12/1850"])("verwirft %s", (eingabe) => {
    expect(monatJahrAusText(eingabe, { heute })).toBeNull();
  });

  it("monatJahrText lässt Unlesbares unverändert", () => {
    expect(monatJahrText("3/2019")).toBe("03/2019");
    expect(monatJahrText("Neu")).toBe("Neu");
    expect(monatJahrText("2018")).toBe("2018");
  });
});

describe("monatJahrPruefen", () => {
  const heute = new Date(2026, 8, 12); // September 2026

  it("leer ist in Ordnung (Pflicht regelt die Seite)", () => {
    expect(monatJahrPruefen("", { heute }).ok).toBe(true);
  });

  it("verlangt das vollständige Format", () => {
    expect(monatJahrPruefen("12/20", { heute })).toMatchObject({ ok: false, fehler: "Bitte MM/JJJJ vollständig eingeben" });
  });

  it("Erstzulassung nicht in der Zukunft", () => {
    expect(monatJahrPruefen("09/2026", { art: "ez", heute }).ok).toBe(true);
    expect(monatJahrPruefen("10/2026", { art: "ez", heute })).toMatchObject({ ok: false });
    expect(monatJahrPruefen("01/1899", { art: "ez", heute })).toMatchObject({ ok: false });
  });

  it("HU: höchstens drei Jahre voraus, abgelaufen nur als Hinweis", () => {
    expect(monatJahrPruefen("09/2029", { art: "hu", heute }).ok).toBe(true);
    expect(monatJahrPruefen("10/2029", { art: "hu", heute })).toMatchObject({ ok: false });
    expect(monatJahrPruefen("03/2026", { art: "hu", heute })).toEqual({ ok: true, fehler: "", hinweis: "HU abgelaufen" });
    expect(monatJahrPruefen("01/2010", { art: "hu", heute })).toMatchObject({ ok: false });
  });
});
