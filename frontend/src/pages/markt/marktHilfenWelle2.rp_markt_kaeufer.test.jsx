/*
 * Rollenprüfung 22.09.2026, Welle 2 — reine Marktplatz-Helfer.
 *
 * RP-509        zugangAblauf: Datum und "bald" (7 Tage)
 * RP-098 Nr. 3  markenAusInseraten: Marken/Modelle ohne Katalog
 * RP-098 Nr. 9  listeAnhaengen: Seite 2 ohne Doppelte
 * RP-093        lesbare Gründe (Hand-Reservierung, Laufzeit …)
 * RP-456        betragHinweis: "= 12.500,00 €" schon beim Tippen
 */
import { describe, expect, it } from "vitest";
import {
  beendetText, betragHinweis, listeAnhaengen, markenAusInseraten, VERLAENGERN_AB_TAGEN, zugangAblauf,
} from "./marktHilfen";

const TAG = 86400000;

describe("zugangAblauf (RP-509)", () => {
  const jetzt = Date.parse("2026-09-22T10:00:00Z");
  it("Datum und 'bald' ab 7 Tagen", () => {
    expect(VERLAENGERN_AB_TAGEN).toBe(7);
    const bald = zugangAblauf({ active: true, expires_at: new Date(jetzt + 3 * TAG).toISOString() }, jetzt);
    expect(bald).toEqual({ bis: "25.09.2026", bald: true });
    const spaeter = zugangAblauf({ active: true, expires_at: new Date(jetzt + 20 * TAG).toISOString() }, jetzt);
    expect(spaeter.bald).toBe(false);
  });
  it("kostenlos, gesperrt, inaktiv oder ohne/kaputtes Datum -> null", () => {
    const bis = new Date(jetzt + 3 * TAG).toISOString();
    expect(zugangAblauf({ active: true, kostenlos: true, expires_at: bis }, jetzt)).toBeNull();
    expect(zugangAblauf({ active: false, gesperrt: true, expires_at: bis }, jetzt)).toBeNull();
    expect(zugangAblauf({ active: false, expires_at: bis }, jetzt)).toBeNull();
    expect(zugangAblauf({ active: true, expires_at: null }, jetzt)).toBeNull();
    expect(zugangAblauf({ active: true, expires_at: "unsinn" }, jetzt)).toBeNull();
    expect(zugangAblauf(null, jetzt)).toBeNull();
  });
});

describe("markenAusInseraten (RP-098 Nr. 3)", () => {
  it("sortiert, ohne Doppelte, Modelle je Marke", () => {
    const liste = markenAusInseraten([
      { data: { make_label: "VW", model_label: "Polo" } },
      { data: { make_label: "Škoda", model_label: "Octavia" } },
      { data: { make_label: "VW", model_label: "Golf" } },
      { data: { make_label: "VW", model_label: "Golf" } },
      { data: { make_label: "", model_label: "X" } },
      {},
    ]);
    expect(liste.map((m) => m.name)).toEqual(["Škoda", "VW"]);
    expect(liste[1].models.map((m) => m.name)).toEqual(["Golf", "Polo"]);
    expect(liste[1]).toMatchObject({ id: "VW", name: "VW" });
    expect(markenAusInseraten(null)).toEqual([]);
  });
});

describe("listeAnhaengen (RP-098 Nr. 9)", () => {
  it("hängt an, ohne schon geladene doppelt zu zeigen", () => {
    expect(listeAnhaengen([{ id: "a" }, { id: "b" }], [{ id: "b" }, { id: "c" }]).map((v) => v.id))
      .toEqual(["a", "b", "c"]);
    expect(listeAnhaengen(null, [{ id: "x" }])).toEqual([{ id: "x" }]);
  });
});

describe("beendetText — neue Gründe (Welle 2)", () => {
  it("Hand-Reservierung und Aufräumlauf lesbar", () => {
    expect(beendetText({ status: "abgelehnt", beendet_grund: "inserat_reserviert" }))
      .toMatch(/anderweitig reserviert/);
    expect(beendetText({ status: "abgelehnt", beendet_grund: "inserat_abgelaufen" }))
      .toMatch(/Laufzeit/);
    expect(beendetText({ status: "abgelehnt", beendet_grund: "fahrzeug_geloescht" }))
      .toMatch(/nicht mehr im Angebot/);
  });
});

describe("betragHinweis (RP-456)", () => {
  it("zeigt den verstandenen Betrag oder den Fehler", () => {
    expect(betragHinweis("")).toBeNull();
    expect(betragHinweis("   ")).toBeNull();
    // Intl setzt ein geschütztes Leerzeichen vor "€"
    const text = (t) => betragHinweis(t).text.replace(/\s/g, " ");
    expect(betragHinweis("12.500").fehler).toBe(false);
    expect(text("12.500")).toBe("= 12.500,00 €");
    expect(text("20.900,50")).toBe("= 20.900,50 €");
    expect(betragHinweis("abc").fehler).toBe(true);
    expect(betragHinweis("0").text).toMatch(/größer als 0/);
  });
});
