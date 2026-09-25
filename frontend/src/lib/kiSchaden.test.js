import { describe, expect, it } from "vitest";
import {
  argumenteText, eur, frageSchluessel, kiStatusText, kiWartet, mitAntwort, mitRueckfrageAntwort,
  nachPrioritaet, schadenZeile, schaedenStand, schwereFragen, schwereOffen, schwereText,
  vorschlaegeAnwenden,
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
    expect(kiStatusText("limit")).toMatch(/Stundenlimit/);
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
    expect(erg.form.accident_free).toBe("Nein");          // schon gefuellt
    expect(erg.form.tires).toBe("");                      // angefasst -> gesperrt
    expect(erg.form.schluessel_anzahl).toBe("2");
    expect(erg.form.empfang_schluessel).toBe(true);
    expect(erg.uebernommen.map((u) => u.feld)).toEqual(["hu_valid", "hu_until", "service_book", "schluessel_anzahl"]);
    expect(erg.uebernommen.find((u) => u.feld === "service_book").wert).toBe("Ja, lückenlos");
    expect(erg.hinweise).toEqual(["Hinweis A"]);
    // HU-Datum nur mit "HU vorhanden: Ja"
    const nurDatum = vorschlaegeAnwenden({ hu_valid: "Nein", hu_until: "" },
      { felder: { hu_until: { value: "07/2028" } } });
    expect(nurDatum.form.hu_until).toBe("");
    expect(vorschlaegeAnwenden(form, null).uebernommen).toEqual([]);
  });

  it("beschreibt Schaeden, erkennt Aenderungen und legt Rueckfrage-Antworten am Schaden ab", () => {
    const d = { id: "d1", type_key: "delle", type_label: "Delle", zone: "Kotflügel vorne rechts",
                severity_data: { groesse: "2–5 cm" } };
    expect(schadenZeile(d)).toBe("Delle – Kotflügel vorne rechts – 2–5 cm");
    const stand = schaedenStand([d]);
    expect(schaedenStand([mitAntwort(d, "lack", "nein")])).not.toBe(stand);
    expect(schaedenStand([{ ...d, x: 999 }])).toBe(stand);   // Koordinaten zaehlen nicht
    expect(frageSchluessel("Ist der Lack beschädigt?")).toBe("frage_ist_der_lack_beschädigt");
    const neu = mitRueckfrageAntwort([d, { id: "d2", type_key: "kratzer" }],
      { source_id: "d1", question: "Ist der Lack beschädigt?" }, "Nein");
    expect(neu[0].severity_data).toEqual({ groesse: "2–5 cm", "frage_ist_der_lack_beschädigt": "Nein" });
    expect(neu[1].severity_data).toBeUndefined();
  });
});
