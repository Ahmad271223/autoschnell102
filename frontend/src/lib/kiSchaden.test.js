import { describe, expect, it } from "vitest";
import {
  SCHWERE_FRAGEN, alleVollstaendig, argumenteText, eur, kiStatusText, kiWartet, mitAntwort, nachPrioritaet,
  schadenZeile, schaedenStand, schwereFragen, schwereOffen, schwereText, vierText, vorschlaegeAuswerten, fragenFuer, mitBetrag,
  INSERAT_FELDER, eigeneWahlText, offeneVorschlaege, vorschlaegeFuerAlle, vorschlagSetzen, vorschlagWeichtAb,
  zusicherungenOhneWahlLeeren,
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

  // Stufe 3 (26.09.2026) / inserat4 / inserat5 (28.09.2026): NICHTS aus dem
  // Inserat ins Formular — auch Bereifung und Schlüsselanzahl nur als Vorschlag.
  it("schreibt nichts ins Formular, jedes Feld kommt als Vorschlag", () => {
    const form = { hu_valid: "", hu_until: "", service_book: "", accident_free: "Nein", schluessel_anzahl: "",
                   empfang_schluessel: false, tires: "", drivable: "", eu_import: "" };
    const vorher = { ...form };
    const vs = { felder: {
      hu_valid: { value: "Ja", source: "listing_description", source_text: "HU 07/2028" },
      hu_until: { value: "07/2028", source: "listing_description", source_text: "HU 07/2028" },
      service_book: { value: "ja", source_text: "lückenlos scheckheftgepflegt" },
      accident_free: { value: "Ja", source_text: "unfallfrei" },
      drivable: { value: "Nein", source: "listing_field", source_text: "Portalfeld „fahrbereit“: nein" },
      schluessel_anzahl: { value: "2", source_text: "2 Schlüssel" },
      tires: { value: "8-fach", source_text: "Winterreifen dabei" },
    }, hinweise: ["Hinweis A"] };
    const erg = vorschlaegeAuswerten(vs, form);
    // das Formular bleibt unverändert (kein form im Ergebnis, Eingabe nicht verändert)
    expect(erg.form).toBeUndefined();
    expect(erg.uebernommen).toBeUndefined();
    expect(form).toEqual(vorher);
    expect(erg.hinweise).toEqual(["Hinweis A"]);
    // Vorschläge in Dialog-Reihenfolge: Bereifung, HU (mit Datum als EIN Vorschlag),
    // Scheckheft als Klartext, …, Schlüsselanzahl zuletzt
    expect(erg.vorschlaege.map((z) => z.feld))
      .toEqual(["tires", "hu_valid", "service_book", "accident_free", "drivable", "schluessel_anzahl"]);
    expect(INSERAT_FELDER).toEqual(["tires", "hu_valid", "hu_until", "service_book", "accident_free", "drivable",
                                    "eu_import", "schluessel_anzahl"]);
    const hu = erg.vorschlaege[1];
    expect(hu.setzt).toEqual({ hu_valid: "Ja", hu_until: "07/2028" });
    expect(hu.wert).toBe("Ja, gültig bis 07/2028");
    expect(hu.quelle).toBe("Inseratstext");
    expect(erg.vorschlaege[0]).toMatchObject({ setzt: { tires: "8-fach" }, wert: "8-fach (Sommer + Winter)",
                                               fund: "Winterreifen dabei" });
    expect(erg.vorschlaege[2].wert).toBe("Ja, lückenlos");
    expect(erg.vorschlaege[4].quelle).toBe("Portalfeld");
    expect(erg.vorschlaege[4].fund).toContain("fahrbereit");
    // Startprüfung 27.09.2026 (K4): die Schlüsselanzahl setzt nur schluessel_anzahl,
    // nie das Kästchen der Empfangsbestätigung
    expect(erg.vorschlaege[5].setzt).toEqual({ schluessel_anzahl: "2" });
    expect(vorschlagSetzen(form, erg.vorschlaege[5]).empfang_schluessel).toBe(false);
    expect(vorschlaegeAuswerten(null, form)).toEqual({ hinweise: [], vorschlaege: [] });
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
    const erg = vorschlaegeAuswerten(vs, form, { heute });
    expect(erg.vorschlaege.map((z) => z.feld)).toEqual(["accident_free"]);
    expect(erg.hinweise.join(" ")).toContain("HU abgelaufen (08/2026)");
    // der Server hat den Hinweis schon geschickt -> nicht doppelt
    const doppelt = vorschlaegeAuswerten({ ...vs, hinweise: ["HU abgelaufen (08/2026) laut Inserat"] }, form, { heute });
    expect(doppelt.hinweise.filter((h) => h.includes("HU abgelaufen"))).toHaveLength(1);
    // laufender Monat gilt noch
    const gueltig = vorschlaegeAuswerten({ felder: {
      hu_valid: { value: "Ja" }, hu_until: { value: "09/2026" } } }, form, { heute });
    expect(gueltig.vorschlaege[0].setzt).toEqual({ hu_valid: "Ja", hu_until: "09/2026" });
  });

  it("Vorschlag übernehmen, bereits passende nicht erneut anbieten, Entwurf ohne Wahl leeren", () => {
    const form = { hu_valid: "", hu_until: "", service_book: "teilweise", service_book_until: "05/2020",
                   accident_free: "", drivable: "Ja", eu_import: "" };
    const { vorschlaege: zusicherungen } = vorschlaegeAuswerten({ felder: {
      hu_valid: { value: "Ja" }, hu_until: { value: "07/2028" },
      service_book: { value: "ja" }, drivable: { value: "Ja" }, accident_free: { value: "Nein" },
    } }, form);
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

  // inserat5 (Prüfung Runde 4, Nr. 5): ein von Hand getipptes „HU gültig bis“
  // bleibt stehen, nur „HU/AU vorhanden“ wird geleert.
  it("Entwurf: getipptes HU-Datum bleibt, nur hu_valid wird geleert", () => {
    const entwurf = { hu_valid: "Ja", hu_until: "05/2027", accident_free: "" };
    expect(zusicherungenOhneWahlLeeren(entwurf, { hu_until: true })).toEqual({ ...entwurf, hu_valid: "" });
    // ohne eigene Eingabe geht das Datum mit
    expect(zusicherungenOhneWahlLeeren(entwurf, {})).toEqual({ ...entwurf, hu_valid: "", hu_until: "" });
    // Datum ohne HU-Wahl, aber getippt: bleibt
    expect(zusicherungenOhneWahlLeeren({ hu_valid: "", hu_until: "05/2027" }, { hu_until: true }))
      .toEqual({ hu_valid: "", hu_until: "05/2027" });
  });

  // inserat5 (Nr. 1): alte Entwürfe mit ohne Klick eingetragener Bereifung/Schlüsselanzahl
  it("Entwurf: früher automatisch eingetragene Bereifung/Schlüssel werden wieder Vorschlag", () => {
    const entwurf = { tires: "8-fach", schluessel_anzahl: "2", accident_free: "" };
    const alt = [{ feld: "tires", roh: "8-fach" }, { feld: "schluessel_anzahl", roh: "2" }];
    expect(zusicherungenOhneWahlLeeren(entwurf, {}, alt)).toEqual({ tires: "", schluessel_anzahl: "", accident_free: "" });
    // selbst gewählt oder inzwischen geändert: bleibt
    expect(zusicherungenOhneWahlLeeren(entwurf, { tires: true, schluessel_anzahl: true }, alt)).toEqual(entwurf);
    expect(zusicherungenOhneWahlLeeren({ ...entwurf, schluessel_anzahl: "3" }, {}, alt))
      .toEqual({ tires: "", schluessel_anzahl: "3", accident_free: "" });
    // ohne Liste (kein Hinweis auf automatische Eintragung) bleibt alles
    expect(zusicherungenOhneWahlLeeren(entwurf, {})).toEqual(entwurf);
  });

  // inserat5 (Nr. 2): „Alle Vorschläge übernehmen“ respektiert die eigene Wahl
  it("von Hand abweichend gewählte Felder gehören nicht zu „Alle“", () => {
    const { vorschlaege } = vorschlaegeAuswerten({ felder: {
      accident_free: { value: "Ja" }, drivable: { value: "Ja" }, tires: { value: "8-fach" },
      hu_valid: { value: "Ja" }, hu_until: { value: "07/2028" },
    } }, { accident_free: "", drivable: "", tires: "", hu_valid: "", hu_until: "" });
    const form = { accident_free: "Nein", drivable: "", tires: "", hu_valid: "Ja", hu_until: "05/2027" };
    const [reifen, hu, unfall, fahr] = vorschlaege;
    // seit inserat5 trägt nichts mehr automatisch ein: jeder Wert ist eigene Wahl
    expect(vorschlagWeichtAb(unfall, form)).toBe(true);
    expect(vorschlagWeichtAb(hu, form)).toBe(true);               // anderes Datum getippt
    expect(vorschlagWeichtAb(fahr, form)).toBe(false);            // leer = keine Wahl
    expect(vorschlagWeichtAb(reifen, form)).toBe(false);
    expect(vorschlagWeichtAb(hu, { hu_valid: "Ja", hu_until: "" })).toBe(false);   // Datum ergänzt nur
    expect(vorschlaegeFuerAlle(vorschlaege, form).map((z) => z.feld)).toEqual(["tires", "drivable"]);
    expect(offeneVorschlaege(vorschlaege, form).map((z) => z.feld))
      .toEqual(["tires", "hu_valid", "accident_free", "drivable"]);
    expect(eigeneWahlText(unfall, form)).toBe("Nein");
    expect(eigeneWahlText(hu, form)).toBe("Ja, gültig bis 05/2027");
    expect(eigeneWahlText(reifen, form)).toBe("—");
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
