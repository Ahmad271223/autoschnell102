/*
 * Rollenprüfung 22.09.2026, Welle 3 — reine Marktplatz-Helfer (Team markt_kaeufer).
 *
 * RP-519        inseratAblauf / anfrageAblaufText: Laufzeit des Inserats
 * RP-502/093    kaeufer_geloescht hat einen lesbaren Grund (nicht nur "Beendet")
 */
import { describe, expect, it } from "vitest";
import { anfrageAblaufText, beendetText, INSERAT_BALD_TAGE, inseratAblauf } from "./marktHilfen";

const TAG = 86400000;
const jetzt = Date.parse("2026-09-22T10:00:00Z");

describe("inseratAblauf (RP-519)", () => {
  it("Datum, 'bald' ab 3 Tagen, abgelaufen", () => {
    expect(INSERAT_BALD_TAGE).toBe(3);
    expect(inseratAblauf(new Date(jetzt + 10 * TAG).toISOString(), jetzt))
      .toEqual({ bis: "02.10.2026", abgelaufen: false, bald: false });
    expect(inseratAblauf(new Date(jetzt + 2 * TAG).toISOString(), jetzt))
      .toEqual({ bis: "24.09.2026", abgelaufen: false, bald: true });
    expect(inseratAblauf(new Date(jetzt - TAG).toISOString(), jetzt).abgelaufen).toBe(true);
  });

  it("ohne oder mit kaputtem Datum -> null", () => {
    expect(inseratAblauf(null, jetzt)).toBeNull();
    expect(inseratAblauf("", jetzt)).toBeNull();
    expect(inseratAblauf("kein-datum", jetzt)).toBeNull();
    expect(inseratAblauf(12345, jetzt)).toBeNull();
  });
});

describe("anfrageAblaufText (RP-519)", () => {
  const bis = new Date(jetzt + 10 * TAG).toISOString();
  const it0 = { status: "offen", inserat_status: "veroeffentlicht", laeuft_ab_am: bis };

  it("laufende Anfrage auf veröffentlichtem Inserat -> Datum", () => {
    expect(anfrageAblaufText(it0, jetzt))
      .toBe("Das Inserat läuft am 02.10.2026 ab — danach endet deine Anfrage automatisch.");
    for (const status of ["gegenangebot", "gegenangebot_kaeufer"]) {
      expect(anfrageAblaufText({ ...it0, status }, jetzt)).toContain("02.10.2026");
    }
  });

  it("abgelaufen, aber noch nicht aufgeräumt -> eigener Hinweis", () => {
    expect(anfrageAblaufText({ ...it0, laeuft_ab_am: new Date(jetzt - 60000).toISOString() }, jetzt))
      .toMatch(/abgelaufen und wird in Kürze entfernt/);
  });

  it("beendet, ruhend, nicht veröffentlicht oder ohne Datum -> leer", () => {
    expect(anfrageAblaufText({ ...it0, status: "abgelehnt" }, jetzt)).toBe("");
    expect(anfrageAblaufText({ ...it0, status: "akzeptiert" }, jetzt)).toBe("");
    expect(anfrageAblaufText({ ...it0, anderweitig_reserviert: true }, jetzt)).toBe("");
    expect(anfrageAblaufText({ ...it0, inserat_status: "reserviert" }, jetzt)).toBe("");
    expect(anfrageAblaufText({ ...it0, laeuft_ab_am: null }, jetzt)).toBe("");
    expect(anfrageAblaufText(null, jetzt)).toBe("");
  });
});

describe("beendetText — Welle 3", () => {
  it("kaeufer_geloescht und die übrigen Server-Gründe sind lesbar", () => {
    expect(beendetText({ status: "abgelehnt", beendet_grund: "kaeufer_geloescht" }))
      .toBe("Beendet — dein Käuferkonto wurde gelöscht");
    // Jeder Grund, den der Server schreibt, hat einen eigenen Text.
    for (const grund of ["kaeufer_zurueckgezogen", "netzwerk_entfernt", "inserat_verkauft",
      "inserat_geloescht", "inserat_zurueckgezogen", "inserat_entwurf", "inserat_reserviert",
      "reservierung_aufgehoben", "inserat_abgelaufen", "inserat_weg", "kaeufer_gesperrt",
      "kaeufer_geloescht", "fahrzeug_geloescht", "doppelte_anfrage"]) {
      expect(beendetText({ status: "abgelehnt", beendet_grund: grund })).not.toBe("Beendet");
    }
  });
});
