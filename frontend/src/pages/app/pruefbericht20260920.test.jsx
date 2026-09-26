/*
 * Prüfbericht 20.09.2026 — Seitenlogik der Sucher-Oberfläche.
 *  B19  Einstellungen: Sucher senden nur echte Änderungen (sonst wurden
 *       Chef-Vorgaben als persönliche Werte eingefroren).
 *  H37  Neuladen des Kontexts (z. B. nach dem Logo) behält Ungespeichertes.
 *  H17  Termine: Statusraster bietet Suchern nur, was der Server annimmt.
 *  H23  Archiv: "versand_vorbereitet" ist keine grüne Erfolgsmeldung.
 */
import { describe, expect, it, vi } from "vitest";

vi.mock("sonner", () => ({ toast: { success: vi.fn(), error: vi.fn(), info: vi.fn(), warning: vi.fn() } }));

const { mitOffenenAenderungen, nurGeaenderte } = await import("./Einstellungen");
const { statusSperre } = await import("./Termine");
const { vertragStatus } = await import("./PDFArchiv");

const stand = {
  profile: { company_name: "Chef GmbH", phone: "0301", whatsapp_number: "0301", logo_url: "" },
  comparison_rules: { km: { mode: "plus", value: 30000 } },
  email_template: "Hallo",
  default_terms: "",
  digital_vertragstext: "AGB alt\n\nText",
};

describe("B19: nurGeaenderte", () => {
  it("unveränderte Felder werden gar nicht gesendet", () => {
    expect(nurGeaenderte(structuredClone(stand), stand)).toEqual({});
  });

  it("nur das geänderte Profilfeld geht raus", () => {
    const f = structuredClone(stand);
    f.profile.phone = "0302";
    expect(nurGeaenderte(f, stand)).toEqual({ profile: { phone: "0302" } });
  });

  it("Vertragstext geht nur zusammen mit geleertem AGB-Feld raus", () => {
    const f = structuredClone(stand);
    f.digital_vertragstext = "neu";
    expect(nurGeaenderte(f, stand)).toEqual({ digital_vertragstext: "neu", default_terms: "" });
  });

  it("ohne bekannten Stand wird alles gesendet", () => {
    expect(nurGeaenderte(stand, null)).toBe(stand);
  });
});

describe("H37: mitOffenenAenderungen", () => {
  it("behält ungespeicherte Eingaben, übernimmt den Rest vom Server", () => {
    const alterStand = structuredClone(stand);
    const alt = { ...structuredClone(stand), _edit_profile: "export" };
    alt.profile.company_name = "Getippt, nicht gespeichert";
    const neu = structuredClone(stand);
    neu.profile.logo_url = "/api/files/logo.png";   // gerade hochgeladen
    neu.email_template = "Vom Chef geändert";
    const out = mitOffenenAenderungen(alt, alterStand, neu);
    expect(out.profile.company_name).toBe("Getippt, nicht gespeichert");
    expect(out.profile.logo_url).toBe("/api/files/logo.png");
    expect(out.email_template).toBe("Vom Chef geändert");
    expect(out._edit_profile).toBe("export");
  });

  it("nach dem Speichern (kein alter Stand) gilt der Server", () => {
    const alt = { ...structuredClone(stand), email_template: "lokal" };
    const out = mitOffenenAenderungen(alt, null, stand);
    expect(out.email_template).toBe("Hallo");
  });
});

describe("H17: statusSperre", () => {
  it("der Chef darf alles", () => {
    expect(statusSperre("offen", "abgeholt", true)).toBeNull();
  });
  it("offene Termine darf auch der Sucher frei setzen", () => {
    expect(statusSperre("storniert", "offen", false)).toBeNull();
    expect(statusSperre("abgeholt", "verschoben", false)).toBeNull();
  });
  it("abgeschlossene Termine öffnet nur der Chef wieder", () => {
    expect(statusSperre("offen", "storniert", false)).toMatch(/Hauptaccount/);
  });
  it("den Ausgang einer Abholung ändert nur der Chef", () => {
    expect(statusSperre("storniert", "abgeholt", false)).toMatch(/Hauptaccount/);
    expect(statusSperre("abgeholt", "abgeholt", false)).toBeNull();
  });
  it("zwischen anderen Endzuständen darf der Sucher wechseln", () => {
    expect(statusSperre("storniert", "nicht abgeholt", false)).toBeNull();
  });
});

describe("H23: vertragStatus", () => {
  it("nur 'versendet' ist grün, 'versand_vorbereitet' sagt die Wahrheit", () => {
    expect(vertragStatus("versendet").farbe).toBe("var(--accent-green)");
    expect(vertragStatus("versand_vorbereitet").text).toMatch(/nicht bestätigt/);
    expect(vertragStatus("versand_vorbereitet").farbe).not.toBe("var(--accent-green)");
    expect(vertragStatus("erstellt").text).toMatch(/noch nicht versendet/);
    expect(vertragStatus("unbekannt").text).toBe("unbekannt");
  });
});

describe("A-03: Stand der Inseratsdaten", async () => {
  const { datenStand } = await import("./Vergleich");
  const jetzt = Date.parse("2026-09-21T12:00:00Z");
  it("frisch abgerufen", () => {
    expect(datenStand({ abgerufen_am: "2026-09-21T11:40:00Z" }, jetzt).text).toBe("Daten eben abgerufen");
  });
  it("älter als ein Tag wird hervorgehoben", () => {
    const s = datenStand({ abgerufen_am: "2026-09-12T12:00:00Z" }, jetzt);
    expect(s.alt).toBe(true);
    expect(s.text).toMatch(/Daten vom 12\.09\.2026/);
  });
  it("ohne Angabe nichts anzeigen", () => {
    expect(datenStand({}, jetzt)).toBeNull();
  });
});

describe("U-21/M14: Eingaben der manuellen Suche", async () => {
  const { sucheFehler } = await import("./ManuelleSuche");
  it("gültige Eingaben", () => {
    expect(sucheFehler({ ezFrom: "2015", ezTo: "2020", kmMin: "10000", kmMax: "90000", kw: "110", leistungQuelle: "kw" })).toBeNull();
  });
  it("von > bis wird auf Deutsch gemeldet", () => {
    expect(sucheFehler({ kmMin: "90000", kmMax: "10000" })).toMatch(/Kilometerstand/);
    expect(sucheFehler({ ezFrom: "2021", ezTo: "2019" })).toMatch(/Erstzulassung/);
  });
  it("PS über der Grenze (2050 PS ergab 1508 kW)", () => {
    expect(sucheFehler({ ps: "2050", leistungQuelle: "ps" })).toMatch(/2039 PS/);
  });
});
