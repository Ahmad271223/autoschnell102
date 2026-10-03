/*
 * Rollenprüfung 22.09.2026 — Team termine_protokoll, Terminplaner:
 *  RP-043/142  Tipp neben den Dialog verwirft keine Eingaben ohne Rückfrage
 *  RP-418      „Sonstige Kosten“ in deutscher Schreibweise („1.200“ = 1.200 €)
 *  RP-466      Monatsansicht am Handy: eine Spalte, min-w-0
 *  RP-482      offene Protokoll-Korrektur: Rückfrage und Verwerfen
 *  RP-497      Löschen mit Abholbericht nur nach eigener Rückfrage
 */
import { describe, expect, it, vi } from "vitest";

vi.mock("sonner", () => ({ toast: { success: vi.fn(), error: vi.fn(), info: vi.fn(), warning: vi.fn() } }));

const { dialogGeaendert, kostenAusEingabe, kostenAlsText, KORREKTUR_VERWERFEN_FRAGE,
  BERICHT_LOESCHEN_FRAGE } = await import("./Termine");
const { default: QUELLE } = await import("./Termine.jsx?raw");

describe("RP-043/142: Dialog nur mit Rückfrage verwerfen", () => {
  const appt = { id: "t1", title: "BMW abholen", notes: "", driver_id: null, extra_costs: 12 };
  it("unverändert ist unverändert — auch null/leer/fehlend", () => {
    expect(dialogGeaendert({ ...appt }, appt)).toBe(false);
    expect(dialogGeaendert({ ...appt, driver_id: "", seller_name: undefined }, appt)).toBe(false);
  });
  it("eine getippte Notiz oder ein anderes Datum ist eine Änderung", () => {
    expect(dialogGeaendert({ ...appt, notes: "Treffpunkt Hof" }, appt)).toBe(true);
    expect(dialogGeaendert({ ...appt, pickup_date: "2026-09-30" }, appt)).toBe(true);
  });
  it("der Hintergrund schließt nicht mehr direkt", () => {
    expect(QUELLE).not.toMatch(/apple-modal-backdrop"\s*\n?\s*onClick=\{onClose\}/);
    const dialog = QUELLE.split("function EditDialog")[1];
    expect(dialog).toMatch(/onClick=\{hintergrundKlick\}/);
    expect(dialog).toMatch(/window\.confirm\("Änderungen verwerfen\?"\)/);
  });
});

describe("RP-418: Sonstige Kosten", () => {
  it("deutsche Schreibweise", () => {
    expect(kostenAusEingabe("1.200")).toEqual({ wert: 1200, fehler: false });
    expect(kostenAusEingabe("12,50")).toEqual({ wert: 12.5, fehler: false });
    expect(kostenAusEingabe("1.200,50 €")).toEqual({ wert: 1200.5, fehler: false });
    expect(kostenAusEingabe("0")).toEqual({ wert: 0, fehler: false });
    expect(kostenAusEingabe("")).toEqual({ wert: null, fehler: false });
    expect(kostenAusEingabe("abc").fehler).toBe(true);
    expect(kostenAusEingabe("-5").fehler).toBe(true);
  });
  it("Anzeige und Rückweg passen zusammen", () => {
    expect(kostenAlsText(1200)).toBe("1.200");
    expect(kostenAlsText(12.5)).toBe("12,5");
    expect(kostenAlsText(null)).toBe("");
    expect(kostenAusEingabe(kostenAlsText(1234.56)).wert).toBe(1234.56);
  });
  it("kein type=number mit Number() mehr", () => {
    expect(QUELLE).not.toMatch(/set\("extra_costs", e\.target\.value === "" \? null : Number/);
    expect(QUELLE).toMatch(/data-testid="edit-extra-costs" type="text" inputMode="decimal"/);
  });
});

describe("RP-466: Monatsansicht am Handy", () => {
  it("eine Spalte unter lg, min-w-0 für beide Teile", () => {
    expect(QUELLE).toMatch(/grid grid-cols-1 lg:grid-cols-\[minmax\(0,1fr\)_380px\]/);
    expect(QUELLE).not.toMatch(/"grid lg:grid-cols-\[1fr_380px\]/);
    expect(QUELLE).toMatch(/apple-surface-gloss p-4 lg:p-5 min-w-0/);
    expect(QUELLE).toMatch(/space-y-5 min-w-0/);
  });
});

describe("RP-482: offene Korrektur", () => {
  it("save meldet 'korrektur' (nur Chef) vor dem Neu-laden-Zweig", () => {
    const save = QUELLE.split("const save = async (a) =>")[1].split("const remove =")[0];
    expect(save).toMatch(/return "korrektur";/);
    expect(save).toMatch(/chef && !a\.korrektur_verwerfen/);
    expect(save.indexOf('return "korrektur";')).toBeLessThan(save.indexOf("/neu laden/i"));
  });
  it("der Dialog fragt und schickt korrektur_verwerfen", () => {
    const dialog = QUELLE.split("const speichern = async () =>")[1].split("const loeschen =")[0];
    expect(dialog).toMatch(/ok === "korrektur"/);
    expect(dialog).toMatch(/window\.confirm\(KORREKTUR_VERWERFEN_FRAGE\)/);
    expect(dialog).toMatch(/korrektur_verwerfen: true/);
    expect(KORREKTUR_VERWERFEN_FRAGE).toMatch(/verwerfen/);
  });
});

describe("RP-497: Löschen mit Abholbericht", () => {
  it("eigene Rückfrage und bericht_loeschen=1", () => {
    const remove = QUELLE.split("const remove = async")[1].split("// Group appointments")[0];
    expect(remove).toMatch(/Abholbericht des Fahrers/);
    expect(remove).toMatch(/window\.confirm\(BERICHT_LOESCHEN_FRAGE\)/);
    expect(remove).toMatch(/params: \{ bericht_loeschen: 1 \}/);
    expect(BERICHT_LOESCHEN_FRAGE).toMatch(/stornieren statt löschen/);
  });
});
