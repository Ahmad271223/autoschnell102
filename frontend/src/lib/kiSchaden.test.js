import { describe, expect, it } from "vitest";
import {
  SCHWERE_FRAGEN, alleVollstaendig, argumenteText, eur, kiStatusText, kiWartet, mitAntwort, nachPrioritaet,
  schadenZeile, schaedenStand, schwereFragen, schwereOffen, schwereText, vierText, vorschlaegeAnwenden, fragenFuer, mitBetrag,
  ZUSICHERUNG_FELDER, offeneVorschlaege, vorschlagSetzen, zusicherungenOhneWahlLeeren,
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
    // Technik: 3 Fragen; bei bestätigter Diagnose zwei Folgefragen (Umfang, Kostenvoranschlag)
    const symptom = { type_key: "technik", severity_data: { status: "nur Symptom bemerkt" } };
    const bestaetigt = { type_key: "technik", severity_data: { status: "Werkstatt hat Diagnose bestätigt" } };
    expect(fragenFuer(symptom).map((f) => f.key)).toEqual(["status", "fahrbereit", "warnleuchte"]);
    expect(fragenFuer(bestaetigt).map((f) => f.key)).toEqual(["status", "fahrbereit", "warnleuchte", "umfang", "kva"]);
    // Kostenvoranschlag "liegt vor" braucht einen Betrag
    const mitKva = { ...bestaetigt, severity_data: { ...bestaetigt.severity_data, fahrbereit: "ja", warnleuchte: "keine",
                                                     umfang: "Bauteil tauschen", kva: "liegt vor" } };
    expect(schwereOffen(mitKva)).toEqual(["Betrag"]);
    const mitBetragWert = mitBetrag(mitKva, "kva_eur", "1.200 €");
    expect(mitBetragWert.severity_data.kva_eur).toBe("1200");
    expect(schwereOffen(mitBetragWert)).toEqual([]);
    expect(schwereText(mitBetragWert)).toContain("liegt vor (1200 €)");
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
    expect(kiStatusText("kostendeckel")).toMatch(/Kostendeckel je Lauf/);   // 27.09.2026
    expect(kiStatusText("netz")).toMatch(/Verbindung/);
    expect(kiStatusText("ok")).toBe("");
  });

  // Stufe 3 (26.09.2026) / inserat4 (28.09.2026): Nicht-Zusicherungen nur in
  // leere Felder; Zusicherungen NIE ins Formular, nur als Vorschlag.
  it("belegt nur Nicht-Zusicherungen vor, Zusicherungen kommen als Vorschlag", () => {
    const form = { hu_valid: "", hu_until: "", service_book: "", accident_free: "Nein", schluessel_anzahl: "",
                   empfang_schluessel: false, tires: "", drivable: "", eu_import: "" };
    const vs = { felder: {
      hu_valid: { value: "Ja", source: "listing_description", source_text: "HU 07/2028" },
      hu_until: { value: "07/2028", source: "listing_description", source_text: "HU 07/2028" },
      service_book: { value: "ja", source_text: "lückenlos scheckheftgepflegt" },
      accident_free: { value: "Ja", source_text: "unfallfrei" },
      drivable: { value: "Nein", source: "listing_field", source_text: "Portalfeld „fahrbereit“: nein" },
      schluessel_anzahl: { value: "2", source_text: "2 Schlüssel" },
      tires: { value: "8-fach", source_text: "Winterreifen dabei" },
    }, hinweise: ["Hinweis A"] };
    const erg = vorschlaegeAnwenden(form, vs, { tires: true });
    // Zusicherungen bleiben, wie sie waren
    for (const feld of ZUSICHERUNG_FELDER) expect(erg.form[feld]).toBe(form[feld]);
    expect(erg.form.tires).toBe("");
    expect(erg.form.schluessel_anzahl).toBe("2");
    // Startprüfung 27.09.2026 (K4): die übernommene Anzahl hakt die
    // Empfangsbestätigung NICHT an (Kästchen bleiben für die Übergabe leer).
    expect(erg.form.empfang_schluessel).toBe(false);
    expect(erg.uebernommen.map((u) => u.feld)).toEqual(["schluessel_anzahl"]);
    expect(erg.hinweise).toEqual(["Hinweis A"]);
    // Vorschläge: HU mit Datum als EIN Vorschlag, Scheckheft als Klartext, Quelle sichtbar
    expect(erg.zusicherungen.map((z) => z.feld)).toEqual(["hu_valid", "service_book", "accident_free", "drivable"]);
    const hu = erg.zusicherungen[0];
    expect(hu.setzt).toEqual({ hu_valid: "Ja", hu_until: "07/2028" });
    expect(hu.wert).toBe("Ja, gültig bis 07/2028");
    expect(hu.quelle).toBe("Inseratstext");
    expect(erg.zusicherungen[1].wert).toBe("Ja, lückenlos");
    expect(erg.zusicherungen[3].quelle).toBe("Portalfeld");
    expect(erg.zusicherungen[3].fund).toContain("fahrbereit");
    expect(vorschlaegeAnwenden({ schluessel_anzahl: "" },
      { felder: { schluessel_anzahl: { value: "2" } } }).form).toEqual({ schluessel_anzahl: "2" });
    expect(vorschlaegeAnwenden(form, null).uebernommen).toEqual([]);
    expect(vorschlaegeAnwenden(form, null).zusicherungen).toEqual([]);
  });

  // Go-Live-Prüfung 27.09.2026 (K2): abgelaufene HU nie als "HU: Ja" anbieten
  it("bietet eine abgelaufene HU nicht an, sondern zeigt den Hinweis", () => {
    const heute = new Date(2026, 8, 27);
    const form = { hu_valid: "", hu_until: "", accident_free: "" };
    const vs = { felder: {
      hu_valid: { value: "Ja", source_text: "HU 08/2026" },
      hu_until: { value: "08/2026", source_text: "HU 08/2026" },
      accident_free: { value: "Ja", source_text: "unfallfrei" },
    }, hinweise: [] };
    const erg = vorschlaegeAnwenden(form, vs, {}, { heute });
    expect(erg.form).toEqual(form);
    expect(erg.zusicherungen.map((z) => z.feld)).toEqual(["accident_free"]);
    expect(erg.hinweise.join(" ")).toContain("HU abgelaufen (08/2026)");
    // der Server hat den Hinweis schon geschickt -> nicht doppelt
    const doppelt = vorschlaegeAnwenden(form, { ...vs, hinweise: ["HU abgelaufen (08/2026) laut Inserat"] }, {}, { heute });
    expect(doppelt.hinweise.filter((h) => h.includes("HU abgelaufen"))).toHaveLength(1);
    // laufender Monat gilt noch
    const gueltig = vorschlaegeAnwenden(form, { felder: {
      hu_valid: { value: "Ja" }, hu_until: { value: "09/2026" } } }, {}, { heute });
    expect(gueltig.zusicherungen[0].setzt).toEqual({ hu_valid: "Ja", hu_until: "09/2026" });
    expect(gueltig.form.hu_valid).toBe("");
  });

  it("Vorschlag übernehmen, bereits passende nicht erneut anbieten, Entwurf ohne Wahl leeren", () => {
    const form = { hu_valid: "", hu_until: "", service_book: "teilweise", service_book_until: "05/2020",
                   accident_free: "", drivable: "Ja", eu_import: "" };
    const { zusicherungen } = vorschlaegeAnwenden(form, { felder: {
      hu_valid: { value: "Ja" }, hu_until: { value: "07/2028" },
      service_book: { value: "ja" }, drivable: { value: "Ja" }, accident_free: { value: "Nein" },
    } });
    // "Fahrtauglich: Ja" steht schon so im Formular -> kein Vorschlag
    expect(offeneVorschlaege(zusicherungen, form).map((z) => z.feld))
      .toEqual(["hu_valid", "service_book", "accident_free"]);
    const hu = zusicherungen.find((z) => z.feld === "hu_valid");
    const nachHu = vorschlagSetzen(form, hu);
    expect(nachHu).toEqual({ ...form, hu_valid: "Ja", hu_until: "07/2028" });
    expect(offeneVorschlaege(zusicherungen, nachHu).map((z) => z.feld)).toEqual(["service_book", "accident_free"]);
    // "Ja, lückenlos" löscht das "bis" von "teilweise" wie die Auswahl von Hand
    const sb = vorschlagSetzen(form, zusicherungen.find((z) => z.feld === "service_book"));
    expect(sb.service_book).toBe("ja");
    expect(sb.service_book_until).toBe("");
    expect(vorschlagSetzen(nachHu, { setzt: { hu_valid: "Nein" } }).hu_until).toBe("");
    expect(offeneVorschlaege(undefined, form)).toEqual([]);
    // Entwurf: nur bewusst gewählte Zusicherungen bleiben
    const entwurf = { hu_valid: "Ja", hu_until: "07/2028", accident_free: "Ja", drivable: "Nein",
                      service_book: "ja", service_book_until: "", eu_import: "Ja", schluessel_anzahl: "2" };
    expect(zusicherungenOhneWahlLeeren(entwurf, { drivable: true })).toEqual({
      hu_valid: "", hu_until: "", accident_free: "", drivable: "Nein", service_book: "", service_book_until: "",
      eu_import: "", schluessel_anzahl: "2" });
    expect(zusicherungenOhneWahlLeeren(entwurf, { hu_valid: true, hu_until: true, accident_free: true,
                                                  drivable: true, service_book: true, eu_import: true }))
      .toEqual(entwurf);
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
