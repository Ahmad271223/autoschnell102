/**
 * Runde 29 (12.09.2026, Pruefbefund): Der Browser brach jede Anfrage nach
 * 60 s ab, nginx laesst sie bis 300 s laufen. Beim Erzeugen des Kaufvertrags
 * und beim Laden von PDFs sah der Nutzer deshalb einen Fehler, obwohl der
 * Server weiterarbeitete. Diese Wege bekommen jetzt ein laengeres Zeitlimit.
 */
import { describe, expect, it } from "vitest";
import { istLangeAktion, LANGE_AKTION_MS } from "./api";

describe("istLangeAktion", () => {
  it("gilt fuer jeden Datei-Abruf (PDF, Bilder)", () => {
    expect(istLangeAktion({ url: "/beweise/b1/pdf", responseType: "blob" })).toBe(true);
    expect(istLangeAktion({ url: "/contracts/c1/pdf", responseType: "blob" })).toBe(true);
  });

  it("gilt fuer das Erzeugen des Kaufvertrags", () => {
    expect(istLangeAktion({ url: "/contracts", method: "post" })).toBe(true);
    expect(istLangeAktion({ url: "/contracts/c1/send", method: "POST" })).toBe(true);
  });

  it("gilt fuer den Protokoll-Abschluss (Abnahme 12.09.2026)", () => {
    // Der langsamste Weg der Fahrer-App: PDF bauen + zwei Unterschriften.
    expect(istLangeAktion({ url: "/driver/appointments/a1/protocol/finalize", method: "post" })).toBe(true);
    expect(istLangeAktion({ url: "/driver/appointments/a1/protocol/submit", method: "post" })).toBe(true);
    expect(istLangeAktion({ url: "/driver/appointments/a1/report", method: "post" })).toBe(true);
  });

  it("gilt NICHT fuer die schnellen Wege", () => {
    expect(istLangeAktion({ url: "/contracts", method: "get" })).toBe(false);
    expect(istLangeAktion({ url: "/mobile/compare", method: "post" })).toBe(false);
    expect(istLangeAktion({ url: "/bestand" })).toBe(false);
    expect(istLangeAktion({})).toBe(false);
  });

  it("bleibt unter dem Limit von nginx (300 s)", () => {
    expect(LANGE_AKTION_MS).toBeGreaterThan(60000);
    expect(LANGE_AKTION_MS).toBeLessThan(300000);
  });
});
