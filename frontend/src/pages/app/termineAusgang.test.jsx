/*
 * Prüfbericht 20.09.2026 (V-12), Entscheidung Ahmad 21.09.2026:
 * Der Chef darf eine abgeholte/erledigte Abholung — auch mit unterschriebenem
 * Protokoll — nachträglich auf „storniert“ oder „nicht abgeholt“ setzen, aber
 * nur nach einer Rückfrage (der Server verlangt ausgang_bestaetigt).
 * Für Sucher bleibt der Ausgang gesperrt: auch bei „erledigt“, bei
 * unterschriebenem Protokoll und nach einer Änderung durch den Chef.
 */
import { describe, expect, it, vi } from "vitest";

vi.mock("sonner", () => ({ toast: { success: vi.fn(), error: vi.fn(), info: vi.fn(), warning: vi.fn() } }));

const { statusSperre, ausgangFrage, ausgangHinweis, ausgangWirksam, terminHatKauf } =
  await import("./Termine");
const { aktionText } = await import("@/lib/fahrzeugStatus");
const { default: QUELLE } = await import("./Termine.jsx?raw");

describe("V-12: statusSperre für Sucher", () => {
  it("storniert mit Protokoll -> nicht abgeholt: nur der Hauptaccount", () => {
    expect(statusSperre("nicht abgeholt", "storniert", false, { protokoll: true })).toMatch(/Hauptaccount/);
  });
  it("erledigt zählt wie abgeholt", () => {
    expect(statusSperre("storniert", "erledigt", false)).toMatch(/Hauptaccount/);
    expect(statusSperre("nicht abgeholt", "erledigt", false)).toMatch(/Hauptaccount/);
  });
  it("ein vom Chef geänderter Ausgang bleibt Chefsache", () => {
    expect(statusSperre("abgeholt", "storniert", false, { ausgang: true })).toMatch(/Hauptaccount/);
  });
  it("ohne Protokoll/Chef-Änderung bleibt der Wechsel zwischen anderen Endzuständen frei", () => {
    expect(statusSperre("storniert", "nicht abgeholt", false)).toBeNull();
    expect(statusSperre("nicht abgeholt", "storniert", false, { protokoll: false })).toBeNull();
  });
  it("offene Termine mit Protokoll (wieder geöffnet) sperren nichts", () => {
    expect(statusSperre("verschoben", "offen", false, { protokoll: true })).toBeNull();
  });
  it("der Chef darf weiterhin alles", () => {
    expect(statusSperre("storniert", "abgeholt", true, { protokoll: true })).toBeNull();
    expect(statusSperre("nicht abgeholt", "erledigt", true, { protokoll: true, ausgang: true })).toBeNull();
  });
});

describe("V-12: ausgangFrage", () => {
  it("nur abgeholt/erledigt -> storniert/nicht abgeholt fragt nach", () => {
    expect(ausgangFrage("abgeholt", "abgeholt", true)).toBeNull();
    expect(ausgangFrage("abgeholt", "offen", true)).toBeNull();
    expect(ausgangFrage("storniert", "nicht abgeholt", true)).toBeNull();
    expect(ausgangFrage("offen", "storniert", false)).toBeNull();
    expect(ausgangFrage("abgeholt", "storniert", true)).toBeTruthy();
    expect(ausgangFrage("erledigt", "nicht abgeholt", false)).toBeTruthy();
  });
  it("mit Protokoll und storniert: alle Punkte", () => {
    const f = ausgangFrage("abgeholt", "storniert", true);
    expect(f).toMatch(/mit unterschriebenem Abholprotokoll abgeschlossen/);
    expect(f).toMatch(/auf „storniert“ setzt/);
    expect(f).toMatch(/Kauf nicht mehr als abgeholt/);
    expect(f).toMatch(/Einkaufspreis aus dieser Abholung wird entfernt/);
    expect(f).toMatch(/Protokoll und Kaufvertrag unverändert als Nachweis/);
    expect(f).toMatch(/Fahrer die Unterlagen dieser Fahrt nicht mehr/);
    expect(f).toMatch(/im Verlauf festgehalten/);
    expect(f).toMatch(/Wirklich ändern\?$/);
  });
  it("nicht abgeholt: ohne den Fahrer-Punkt", () => {
    const f = ausgangFrage("abgeholt", "nicht abgeholt", true);
    expect(f).toMatch(/Protokoll und Kaufvertrag/);
    expect(f).not.toMatch(/Fahrer/);
  });
  it("ohne Protokoll: ohne den Protokoll-Teil", () => {
    const f = ausgangFrage("abgeholt", "storniert", false);
    expect(f).toMatch(/^Diese Abholung ist bereits abgeschlossen\./);
    expect(f).not.toMatch(/Protokoll/);
    expect(f).toMatch(/Kauf nicht mehr als abgeholt/);
  });
});

describe("V-12: Anzeige", () => {
  it("Hinweis im Termin-Dialog", () => {
    const h = ausgangHinweis({ am: "2026-09-21T12:30:00+00:00", von_status: "abgeholt",
      nach_status: "storniert", protokoll_id: "p1" });
    expect(h).toMatch(/vom Hauptaccount nachträglich geändert/);
    expect(h).toMatch(/„abgeholt“ → „storniert“/);
    expect(h).toMatch(/21\.09\.2026/);
    expect(h).toMatch(/Abholprotokoll bleibt als Nachweis/);
    expect(ausgangHinweis({ nach_status: "storniert" })).not.toMatch(/Abholprotokoll/);
    expect(ausgangHinweis(null)).toBeNull();
  });
  it("Verlaufstext in der Fahrzeugakte", () => {
    expect(aktionText("termin.ausgang.geaendert"))
      .toBe("Ausgang der Abholung nachträglich geändert (Hauptaccount)");
  });
});

/*
 * Prüfung 21.09.2026 (V-12, Nachprüfung): wieder geöffnete Termine, „erledigt“
 * ohne Kauf, genaue Rückfrage, Merker nur für den geltenden Status, Rückfrage
 * auf Verlangen des Servers.
 */
describe("V-12 Nachprüfung: wieder geöffneter Termin mit Protokoll", () => {
  it("Sucher: storniert / nicht abgeholt gesperrt, andere offene Zustände frei", () => {
    for (const alt of ["offen", "bestätigt", "in Bearbeitung", "verschoben"]) {
      expect(statusSperre("storniert", alt, false, { protokoll: true })).toMatch(/Hauptaccount/);
      expect(statusSperre("nicht abgeholt", alt, false, { protokoll: true })).toMatch(/Hauptaccount/);
      expect(statusSperre("verschoben", alt === "verschoben" ? "offen" : alt, false,
        { protokoll: true })).toBeNull();
    }
    // ohne Protokoll wie bisher frei
    expect(statusSperre("storniert", "offen", false)).toBeNull();
    // der Chef bleibt frei (er bekommt die Rückfrage)
    expect(statusSperre("storniert", "offen", true, { protokoll: true })).toBeNull();
  });
  it("Kauf schon zurückgenommen (Storno, wieder geöffnet): nichts mehr belegt", () => {
    for (const lc of ["abholung_geplant", "storniert", "nicht_abgeholt"]) {
      expect(statusSperre("storniert", "offen", false, { protokoll: true, lifecycle: lc })).toBeNull();
      expect(ausgangFrage("offen", "storniert", true, { lifecycle: lc })).toBeNull();
    }
    // Fahrzeug abgeholt bzw. schon im Bestand: belegt
    expect(statusSperre("storniert", "offen", false, { protokoll: true, lifecycle: "abgeholt" }))
      .toMatch(/Hauptaccount/);
    expect(statusSperre("nicht abgeholt", "offen", false, { protokoll: true, lifecycle: "bestand" }))
      .toMatch(/Hauptaccount/);
    expect(ausgangFrage("offen", "storniert", true, { lifecycle: "bestand" }))
      .toMatch(/samt Einkaufspreis unverändert/);
    // ohne Fahrzeug zählt das Protokoll
    expect(statusSperre("storniert", "offen", false, { protokoll: true, hatFahrzeug: false }))
      .toMatch(/Hauptaccount/);
  });
  it("Chef: Rückfrage auch aus dem offenen Status, mit eigenem Kopf", () => {
    const f = ausgangFrage("offen", "storniert", true, { lifecycle: "abgeholt" });
    expect(f).toMatch(/zur Korrektur wieder geöffnet/);
    expect(f).toMatch(/Kauf nicht mehr als abgeholt/);
    expect(f).toMatch(/Wirklich ändern\?$/);
    expect(ausgangFrage("in Bearbeitung", "nicht abgeholt", true)).toBeTruthy();
    // ohne Protokoll keine Rückfrage (der Server verlangt keine)
    expect(ausgangFrage("offen", "storniert", false)).toBeNull();
    // offen -> verschoben ist kein Ausgang
    expect(ausgangFrage("offen", "verschoben", true)).toBeNull();
  });
});

describe("V-12 Nachprüfung: „erledigt“ nur mit Kauf/Fahrzeug", () => {
  it("terminHatKauf", () => {
    expect(terminHatKauf({ vehicle_id: "v" })).toBe(true);
    expect(terminHatKauf({ contract_id: "c" })).toBe(true);
    expect(terminHatKauf({ kaufvorgang_id: "k" })).toBe(true);
    expect(terminHatKauf({ title: "Werkstatt" })).toBe(false);
    expect(terminHatKauf(null)).toBe(false);
  });
  it("Sucher: allgemeiner Termin erledigt <-> storniert bleibt frei", () => {
    expect(statusSperre("storniert", "erledigt", false, { hatKauf: false })).toBeNull();
    expect(statusSperre("nicht abgeholt", "erledigt", false, { hatKauf: false })).toBeNull();
    // mit Kauf oder Protokoll gesperrt, "abgeholt" immer
    expect(statusSperre("storniert", "erledigt", false, { hatKauf: true })).toMatch(/Hauptaccount/);
    expect(statusSperre("storniert", "erledigt", false, { hatKauf: false, protokoll: true }))
      .toMatch(/Hauptaccount/);
    expect(statusSperre("storniert", "abgeholt", false, { hatKauf: false })).toMatch(/Hauptaccount/);
  });
  it("Chef: keine Kauf-Rückfrage für einen allgemeinen erledigten Termin", () => {
    expect(ausgangFrage("erledigt", "storniert", false, { hatKauf: false, hatFahrzeug: false }))
      .toBeNull();
    // mit Protokoll fragt der Server — also auch die Oberfläche
    expect(ausgangFrage("erledigt", "storniert", true, { hatKauf: false, hatFahrzeug: false }))
      .toBeTruthy();
    // "abgeholt" ohne Kauf: Rückfrage ohne Einkaufspreis
    const f = ausgangFrage("abgeholt", "storniert", false, { hatKauf: false, hatFahrzeug: false });
    expect(f).toMatch(/gilt die Abholung nicht mehr als erfolgt/);
    expect(f).not.toMatch(/Einkaufspreis|Fahrzeug/);
  });
});

describe("V-12 Nachprüfung: Rückfrage sagt nur zu, was der Server tut", () => {
  it("ohne Fahrzeug: kein Fahrzeug- und Preisteil", () => {
    const f = ausgangFrage("abgeholt", "storniert", true, { hatKauf: true, hatFahrzeug: false });
    expect(f).toMatch(/gilt der Kauf nicht mehr als abgeholt,/);
    expect(f).not.toMatch(/Entscheidung fällig|Einkaufspreis/);
  });
  it("Fahrzeug schon im Bestand/Weiterverkauf: bleibt samt Einkaufspreis", () => {
    for (const lc of ["bestand", "verkaufsentwurf", "verkaufsbereit", "veroeffentlicht",
      "reserviert", "verkauft", "archiviert"]) {
      const f = ausgangFrage("abgeholt", "storniert", true, { lifecycle: lc });
      expect(f).toMatch(/steht bereits im Bestand bzw\. im Weiterverkauf/);
      expect(f).toMatch(/samt Einkaufspreis unverändert/);
      expect(f).not.toMatch(/wird entfernt/);
    }
  });
  it("Fahrzeug abgeholt: Doppel-Abholung als Bedingung", () => {
    const f = ausgangFrage("abgeholt", "nicht abgeholt", true, { lifecycle: "abgeholt" });
    expect(f).toMatch(/sofern keine andere Abholung dieses Fahrzeugs abgeschlossen ist/);
    expect(f).toMatch(/Einkaufspreis aus dieser Abholung wird entfernt/);
    expect(f).not.toMatch(/Bestand/);
  });
  it("Fahrzeugzustand unbekannt: Aussage bleibt bedingt", () => {
    const f = ausgangFrage("abgeholt", "storniert", true);
    expect(f).toMatch(/sofern das Fahrzeug nicht schon im Bestand\/Weiterverkauf steht/);
    expect(f).toMatch(/keine andere Abholung dieses Fahrzeugs/);
  });
  it("Termin ohne Kauf mit Fahrzeug: kein Einkaufspreis", () => {
    const f = ausgangFrage("abgeholt", "storniert", false,
      { hatKauf: false, hatFahrzeug: true, lifecycle: "abgeholt" });
    expect(f).toMatch(/Entscheidung fällig/);
    expect(f).not.toMatch(/Einkaufspreis/);
  });
  it("belegt: Rückfrage auf Verlangen des Servers", () => {
    expect(ausgangFrage("offen", "storniert", false)).toBeNull();
    expect(ausgangFrage("offen", "storniert", false, { belegt: true })).toMatch(/Wirklich ändern\?$/);
    // aus storniert heraus nie
    expect(ausgangFrage("storniert", "nicht abgeholt", true, { belegt: true })).toBeNull();
  });
});

describe("V-12 Nachprüfung: Merker nur für den geltenden Status", () => {
  const ag = { am: "2026-09-21T12:30:00+00:00", von_status: "abgeholt", nach_status: "storniert" };
  it("ausgangWirksam", () => {
    expect(ausgangWirksam({ status: "storniert", ausgang_geaendert: ag })).toBe(true);
    expect(ausgangWirksam({ status: "abgeholt", ausgang_geaendert: ag })).toBe(false);
    expect(ausgangWirksam({ status: "offen", ausgang_geaendert: ag })).toBe(false);
    expect(ausgangWirksam({ status: "storniert" })).toBe(false);
    expect(ausgangWirksam(null)).toBe(false);
  });
  it("Dialog: Hinweis und Sucher-Sperre hängen an ausgangWirksam", () => {
    expect(QUELLE).toMatch(/!isNew && ausgangWirksam\(appt\) && \(/);
    expect(QUELLE).toMatch(/ausgang: ausgangWirksam\(appt\), hatKauf: terminHatKauf\(appt\)/);
    expect(QUELLE).not.toMatch(/ausgang: !!appt\?\.ausgang_geaendert/);
  });
  it("Speichern: 409 mit Rückfrage des Servers fragt nach und speichert mit Bestätigung", () => {
    const save = QUELLE.split("const save = async (a) =>")[1].split("const remove =")[0];
    expect(save).toMatch(/Rückfrage im Terminplaner/);
    expect(save).toMatch(/return "ausgang";/);
    expect(save.indexOf('return "ausgang";')).toBeLessThan(save.indexOf("/neu laden/i"));
    const dialog = QUELLE.split("const speichern = async () =>")[1].split("const loeschen =")[0];
    expect(dialog).toMatch(/ok === "ausgang"/);
    expect(dialog).toMatch(/belegt: true/);
    expect(dialog).toMatch(/ausgang_bestaetigt: true/);
    expect(dialog).toMatch(/lifecycle: appt\?\.vehicle\?\.lifecycle/);
  });
});
