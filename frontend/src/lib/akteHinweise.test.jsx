/*
 * Rollenprüfung 22.09.2026, Welle B2 — Helfer für Bestand.jsx/FahrzeugAkte.jsx.
 *  RP-454  Rückfrage "Offene Termine stornieren und Fahrzeug löschen" mit
 *          Datum, Uhrzeit und Fahrer aus dem 409-Detail
 *  RP-474  Hinweis "Aus dem Abholprotokoll: 85.120 km, 2 neue Schäden — ins
 *          Fahrzeug übernehmen"
 */
import { describe, expect, it } from "vitest";
import {
  protokollBefundHinweis, terminZeile, termineOffenDetail, termineStornoFrage,
} from "./akteHinweise";

const fehler409 = (detail) => ({ response: { status: 409, data: { detail } } });

describe("RP-454: 409 mit Terminliste erkennen", () => {
  it("nur der eigene 409 mit Kennung und Liste zählt", () => {
    const d = { msg: "…", code: "termine_offen", termine: [{ id: "t1" }] };
    expect(termineOffenDetail(fehler409(d))).toBe(d);
    // andere 409 (Status geändert, Inserat wird angelegt): kein Dialog
    expect(termineOffenDetail(fehler409("Der Fahrzeugstatus hat sich inzwischen geändert"))).toBeNull();
    expect(termineOffenDetail(fehler409({ code: "anders", termine: [] }))).toBeNull();
    expect(termineOffenDetail(fehler409({ code: "termine_offen" }))).toBeNull();
    expect(termineOffenDetail({ response: { status: 404, data: { detail: d } } })).toBeNull();
    expect(termineOffenDetail(new Error("Network Error"))).toBeNull();
    expect(termineOffenDetail(null)).toBeNull();
  });

  it("nennt jeden Termin mit Datum, Uhrzeit und Fahrer", () => {
    expect(terminZeile({ pickup_date: "2026-09-30", pickup_time: "14:00", driver_name: "Max M." }))
      .toBe("30.09.2026 14:00 — Fahrer: Max M.");
    expect(terminZeile({ pickup_date: "2026-10-02", pickup_time: null, driver_name: null }))
      .toBe("02.10.2026 — ohne Fahrer");
    expect(terminZeile({})).toBe("ohne Datum — ohne Fahrer");
    expect(terminZeile({ pickup_date: "2026-10-02T09:00:00", pickup_time: "09:00:00" }))
      .toBe("02.10.2026 09:00 — ohne Fahrer");
  });

  it("Rückfrage: Einzahl/Mehrzahl, alle Termine, klare Folge", () => {
    const eins = termineStornoFrage({ termine: [
      { pickup_date: "2026-09-30", pickup_time: "14:00", driver_name: "Max M." }] });
    expect(eins).toContain("noch einen offenen Abholtermin:");
    expect(eins).toContain("• 30.09.2026 14:00 — Fahrer: Max M.");
    expect(eins).toContain("Offenen Termin stornieren und Fahrzeug löschen?");
    expect(eins).toContain("Vertrag und Historie bleiben erhalten");
    const zwei = termineStornoFrage({ termine: [
      { pickup_date: "2026-09-30", pickup_time: "14:00", driver_name: "Max M." },
      { pickup_date: "2026-10-02" }] });
    expect(zwei).toContain("noch 2 offene Abholtermine:");
    expect(zwei).toContain("• 02.10.2026 — ohne Fahrer");
    expect(zwei).toContain("Offene Termine stornieren und Fahrzeug löschen?");
    // ohne Liste kein Absturz
    expect(termineStornoFrage(null)).toContain("Fahrzeug löschen?");
  });
});

describe("RP-474: Hinweis aus dem Abholprotokoll", () => {
  it("km und Schäden in einem Satz", () => {
    expect(protokollBefundHinweis({ km: 85120, schaeden: 2 }))
      .toBe("Aus dem Abholprotokoll: 85.120 km, 2 neue Schäden — ins Fahrzeug übernehmen");
    expect(protokollBefundHinweis({ km: 85120, schaeden: 0 }))
      .toBe("Aus dem Abholprotokoll: 85.120 km — ins Fahrzeug übernehmen");
    expect(protokollBefundHinweis({ km: null, schaeden: 1 }))
      .toBe("Aus dem Abholprotokoll: 1 neuer Schaden — ins Fahrzeug übernehmen");
    expect(protokollBefundHinweis({ km: "85120", schaeden: 3 }))
      .toBe("Aus dem Abholprotokoll: 85.120 km, 3 neue Schäden — ins Fahrzeug übernehmen");
  });

  it("ohne verwertbare Angaben der alte Titel", () => {
    expect(protokollBefundHinweis({ km: "abc", schaeden: 0 })).toBe("Aus dem Abholprotokoll übernehmen");
    expect(protokollBefundHinweis()).toBe("Aus dem Abholprotokoll übernehmen");
  });
});
