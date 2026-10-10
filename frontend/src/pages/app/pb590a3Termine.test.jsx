/*
 * Prüfbericht 20.09.2026, Reparaturwelle A3 (22.09.2026) — Terminplaner.
 *  U-63  Doppelbuchungs-Prüfung: count des Servers, eigener Termin zählt
 *        nicht, Fehler sichtbar (404: Fahrer nicht mehr in der Liste)
 *  U-90  "Bevorstehend" nimmt offene Termine ohne Datum auf (zuerst)
 *  M-05  Tageszelle ist ein <button> mit aria-pressed
 *  M-20  Reiter Monat/Liste mit role=tab / aria-selected
 *  U-67  Terminkarte ohne verschachtelte Bedienelemente
 *  M-07  Termin-Dialog über useModal
 */
import { describe, expect, it, vi } from "vitest";

vi.mock("sonner", () => ({ toast: { success: vi.fn(), error: vi.fn(), info: vi.fn(), warning: vi.fn() } }));

const { konfliktAuswerten, konfliktFehlerText, bevorstehend } = await import("./Termine");
const { default: QUELLE } = await import("./Termine.jsx?raw");

describe("U-63: Doppelbuchung des Fahrers", () => {
  it("nimmt die Gesamtzahl des Servers, ohne den eigenen Termin", () => {
    const data = { conflicts: [{ id: "a1" }, { id: "a2" }], count: 2, has_more: false };
    expect(konfliktAuswerten(data, "a1")).toEqual({ anzahl: 1 });
    expect(konfliktAuswerten(data, "neu")).toEqual({ anzahl: 2 });
  });
  it("gekürzte Liste (50 von 51): die Zahl stimmt trotzdem", () => {
    const liste = Array.from({ length: 50 }, (_, i) => ({ id: `x${i}` }));
    expect(konfliktAuswerten({ conflicts: liste, count: 51, has_more: true }, "neu")).toEqual({ anzahl: 51 });
  });
  it("nur der eigene Termin oder gar keiner: keine Warnung", () => {
    expect(konfliktAuswerten({ conflicts: [{ id: "a1" }], count: 1 }, "a1")).toBeNull();
    expect(konfliktAuswerten({ conflicts: [] }, "a1")).toBeNull();
    expect(konfliktAuswerten(undefined, "a1")).toBeNull();
  });
  it("ohne count (alte Antwort) zählt die Liste", () => {
    expect(konfliktAuswerten({ conflicts: [{ id: "b" }, { id: "c" }] }, "a")).toEqual({ anzahl: 2 });
  });
  it("Fehlertext: 404 = Fahrer nicht mehr in der Liste, sonst allgemein", () => {
    expect(konfliktFehlerText({ response: { status: 404 } })).toMatch(/nicht mehr in deiner Liste/);
    expect(konfliktFehlerText({ response: { status: 500 } })).toMatch(/konnte nicht geprüft/);
    expect(konfliktFehlerText(new Error("x"))).toMatch(/konnte nicht geprüft/);
  });
  it("die Oberfläche zeigt beides und schluckt den Fehler nicht mehr still", () => {
    const dialog = QUELLE.slice(QUELLE.indexOf("function EditDialog("));
    expect(dialog).toMatch(/setConflict\(konfliktAuswerten\(r\.data, a\.id\)\)/);
    expect(dialog).toMatch(/setConflict\(\{ fehler: konfliktFehlerText\(err\) \}\)/);
    expect(dialog).toMatch(/data-testid="driver-conflict-fehler"/);
    expect(dialog).toMatch(/\(\{conflict\.anzahl\}×\)/);
    expect(dialog).not.toMatch(/\.catch\(\(\) => \{\}\)/);
  });
});

describe("U-90: Bevorstehend mit undatierten Terminen", () => {
  const heute = "2026-09-22";
  const items = [
    { id: "alt", pickup_date: "2026-09-01", status: "offen" },
    { id: "morgen", pickup_date: "2026-09-23", pickup_time: "10:00", status: "offen" },
    { id: "heute", pickup_date: "2026-09-22", pickup_time: "15:00", status: "bestätigt" },
    { id: "ohne", pickup_date: "", status: "offen" },
    { id: "ohne-fertig", status: "storniert" },
    { id: "fertig", pickup_date: "2026-09-25", status: "abgeholt" },
  ];
  it("undatierte offene Termine zuerst, dann nach Datum/Zeit; abgeschlossene und vergangene fehlen", () => {
    expect(bevorstehend(items, heute).map((a) => a.id)).toEqual(["ohne", "heute", "morgen"]);
  });
  it("begrenzt auf max und verträgt Unsinn", () => {
    expect(bevorstehend(items, heute, 2).map((a) => a.id)).toEqual(["ohne", "heute"]);
    expect(bevorstehend(null, heute)).toEqual([]);
    expect(bevorstehend([null, undefined], heute)).toEqual([]);
  });
  it("die Monatsansicht nutzt es und zeigt 'ohne Datum' an der Karte", () => {
    expect(QUELLE).toMatch(/bevorstehend\(items, format\(new Date\(\), "yyyy-MM-dd"\)\)/);
    expect(QUELLE).toMatch(/data-testid=\{`ohne-datum-\$\{a\.id\}`\}/);
  });
});

describe("M-05 / M-20 / U-67 / M-07: Tastatur und Semantik", () => {
  it("M-05: Tageszelle ist ein Knopf mit aria-pressed und Namen", () => {
    const zelle = QUELLE.slice(QUELLE.indexOf("data-testid={`cal-day-${key}`}") - 200,
                               QUELLE.indexOf("data-testid={`cal-day-${key}`}") + 400);
    expect(zelle).toMatch(/<button key=\{key\} type="button"/);
    expect(zelle).toMatch(/aria-pressed=\{isSelected\}/);
    expect(zelle).toMatch(/aria-label=\{`\$\{format\(d, "EEEE, d\. LLLL", \{ locale: de \}\)\}, \$\{dayAppts\.length\} Termin/);
    expect(QUELLE).not.toMatch(/<div key=\{key\}\s+data-testid=\{`cal-day-/);
  });
  it("M-20: Monat/Liste sind Reiter", () => {
    expect(QUELLE).toMatch(/data-testid="view-month"\s+role="tab" aria-selected=\{view === "month"\}/);
    expect(QUELLE).toMatch(/data-testid="view-list"\s+role="tab" aria-selected=\{view === "list"\}/);
  });
  it("U-67: Karte ist ein div, Titel ein Knopf, Telefon ein tel:-Link, Abholbericht ein Knopf", () => {
    const karte = QUELLE.slice(QUELLE.indexOf("function DayApptItem("), QUELLE.indexOf("function ListView("));
    expect(karte).toMatch(/<div onClick=\{\(\) => onEdit\(a\)\} data-testid=\{`appt-row-\$\{a\.id\}`\}/);
    expect(karte).not.toMatch(/<button onClick=\{\(\) => onEdit\(a\)\}/);
    expect(karte).toMatch(/data-testid=\{`appt-open-\$\{a\.id\}`\}/);
    expect(karte).toMatch(/<a href=\{telHref\(a\.driver\.phone\)\} data-testid=\{`fahrer-tel-\$\{a\.id\}`\}/);
    expect(karte).toMatch(/<button type="button" data-testid=\{`bericht-\$\{a\.id\}`\}/);
    expect(karte).not.toMatch(/role="link"|role="button"/);
  });
  it("M-07: Termin-Dialog über useModal, Escape mit derselben Rückfrage wie der Tipp daneben", () => {
    const dialog = QUELLE.slice(QUELLE.indexOf("function EditDialog("));
    expect(dialog).toMatch(/const dialogRef = useModal\(hintergrundKlick\)/);
    expect(dialog).toMatch(/ref=\{dialogRef\} \{\.\.\.MODAL_ATTRIBUTE\} aria-labelledby="edit-appt-titel"/);
    expect(dialog).toMatch(/id="edit-appt-titel"/);
  });
});
