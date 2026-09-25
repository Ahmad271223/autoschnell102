import { describe, expect, it } from "vitest";
import {
  SCHWERE_FRAGEN, alleVollstaendig, argumenteText, eur, kiStatusText, kiWartet, mitAntwort, nachPrioritaet,
  schadenZeile, schaedenStand, schwereFragen, schwereOffen, schwereText, vierText, vorschlaegeAnwenden,
} from "./kiSchaden";

// Wunsch Ahmad 25./26.09.2026: KI-Schadennachlass — feste Fragen je Schadensart
// (keine Rueckfragen der KI mehr) und Aufbereitung der vier Geldwerte.
describe("kiSchaden", () => {
  it("hat fuer jede Schadensart der Skizze 3 Fragen mit 'unbekannt' wo sinnvoll", () => {
    for (const typ of ["delle", "kratzer", "rost", "hagelschaden", "steinschlag", "beleuchtung",
                       "unfall_repariert", "unfall_nicht_repariert"]) {
      expect(SCHWERE_FRAGEN[typ].length).toBeGreaterThanOrEqual(3);
      expect(SCHWERE_FRAGEN[typ].length).toBeLessThanOrEqual(4);
    }
    expect(schwereFragen("delle").map((f) => f.key)).toEqual(["groesse", "lack", "lage"]);
    expect(schwereFragen("rost").map((f) => f.key)).toEqual(["umfang", "groesse", "stelle"]);
    expect(schwereFragen("delle")[1].options).toContain("unbekannt");
    expect(schwereFragen("sonstwas")).toEqual([]);
  });

  it("merkt Antworten am Schaden; 'unbekannt' zaehlt als beantwortet", () => {
    const d = { id: "1", type_key: "delle" };
    const a = mitAntwort(d, "groesse", "2–5 cm");
    expect(a.severity_data).toEqual({ groesse: "2–5 cm" });
    expect(schwereOffen(a)).toEqual(["Lack beschädigt?", "Lage"]);
    const b = mitAntwort(mitAntwort(a, "lack", "unbekannt"), "lage", "Fläche");
    expect(schwereText(b)).toBe("2–5 cm · unbekannt · Fläche");
    expect(schwereOffen(b)).toEqual([]);
    expect(alleVollstaendig([b])).toBe(true);
    expect(alleVollstaendig([b, a])).toBe(false);
    expect(alleVollstaendig([])).toBe(true);
    expect(mitAntwort(b, "lage", "Fläche").severity_data).toEqual({ groesse: "2–5 cm", lack: "unbekannt" });
  });

  it("gruppiert Positionen nach Prioritaet und formatiert Euro und die vier Werte", () => {
    const g = nachPrioritaet([{ priority: "rot" }, { priority: "gelb" }, { priority: "x" }, { priority: "orange" }]);
    expect(g.rot).toHaveLength(1);
    expect(g.orange).toHaveLength(1);
    expect(g.gelb).toHaveLength(2);
    expect(eur(1350)).toBe("1.350 €");
    expect(eur(null)).toBe("—");
    expect(vierText({ minimum_justified_eur: 200, best_realistic_eur: 400, negotiation_start_eur: 450 }))
      .toBe("mind. 200 € · sehr gut 400 € · Start 450 €");
    expect(argumenteText(["a", "b"])).toBe("1. a\n2. b");
  });

  it("weiss, wann die Karte noch wartet und was sie sagt", () => {
    expect(kiWartet("laeuft")).toBe(true);
    expect(kiWartet("veraltet")).toBe(true);
    expect(kiWartet("ok")).toBe(false);
    expect(kiStatusText("keine")).toMatch(/Keine preisrelevanten/);
    expect(kiStatusText("zeitlimit")).toMatch(/nicht verfügbar/);
    expect(kiStatusText("limit")).toMatch(/Stundenlimit/);
    expect(kiStatusText("budget")).toMatch(/Monatsbudget/);
    expect(kiStatusText("netz")).toMatch(/Verbindung/);
    expect(kiStatusText("ok")).toBe("");
  });

  // Stufe 3 (26.09.2026): Vorschlaege aus dem Inserat nur in leere Felder
  it("uebernimmt Inseratsvorschlaege nur in leere, nicht angefasste Felder", () => {
    const form = { hu_valid: "", hu_until: "", service_book: "", accident_free: "Nein", schluessel_anzahl: "",
                   empfang_schluessel: false, tires: "" };
    const vs = { felder: {
      hu_valid: { value: "Ja", source_text: "HU 07/2028" },
      hu_until: { value: "07/2028", source_text: "HU 07/2028" },
      service_book: { value: "ja", source_text: "lückenlos scheckheftgepflegt" },
      accident_free: { value: "Ja", source_text: "unfallfrei" },
      schluessel_anzahl: { value: "2", source_text: "2 Schlüssel" },
      tires: { value: "8-fach", source_text: "Winterreifen dabei" },
    }, hinweise: ["Hinweis A"] };
    const erg = vorschlaegeAnwenden(form, vs, { tires: true });
    expect(erg.form.hu_valid).toBe("Ja");
    expect(erg.form.hu_until).toBe("07/2028");
    expect(erg.form.service_book).toBe("ja");
    expect(erg.form.accident_free).toBe("Nein");
    expect(erg.form.tires).toBe("");
    expect(erg.form.schluessel_anzahl).toBe("2");
    expect(erg.form.empfang_schluessel).toBe(true);
    expect(erg.uebernommen.map((u) => u.feld)).toEqual(["hu_valid", "hu_until", "service_book", "schluessel_anzahl"]);
    expect(erg.uebernommen.find((u) => u.feld === "service_book").wert).toBe("Ja, lückenlos");
    expect(erg.hinweise).toEqual(["Hinweis A"]);
    const nurDatum = vorschlaegeAnwenden({ hu_valid: "Nein", hu_until: "" },
      { felder: { hu_until: { value: "07/2028" } } });
    expect(nurDatum.form.hu_until).toBe("");
    expect(vorschlaegeAnwenden(form, null).uebernommen).toEqual([]);
  });

  it("beschreibt Schaeden und erkennt Aenderungen", () => {
    const d = { id: "d1", type_key: "delle", type_label: "Delle", zone: "Kotflügel vorne rechts",
                severity_data: { groesse: "2–5 cm" } };
    expect(schadenZeile(d)).toBe("Delle – Kotflügel vorne rechts – 2–5 cm");
    const stand = schaedenStand([d]);
    expect(schaedenStand([mitAntwort(d, "lack", "nein")])).not.toBe(stand);
    expect(schaedenStand([{ ...d, x: 999 }])).toBe(stand);
  });
});
