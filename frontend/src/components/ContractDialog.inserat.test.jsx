/**
 * Go-Live-Prüfung 27.09.2026 (K1/K6, Zusatzfund): Aus dem Inserat
 * vorbelegte Zusicherungen gingen ohne jede Bestätigung in den Vertrag —
 * damals fragte "PDF erstellen" einmal nach.
 *
 * Entscheidung 28.09.2026 (inserat4): Zusicherungen werden gar nicht mehr
 * vorbelegt, nur noch per „Übernehmen“ gesetzt (Verhalten im Dialog:
 * ContractDialog.zusicherung_vorschlag.test.jsx). Die Rückfrage entfällt —
 * und der Dialog darf Zusicherungen nirgends selbst ins Formular schreiben.
 */
import { describe, expect, it } from "vitest";

const { default: VERTRAG } = await import("./ContractDialog.jsx?raw");

describe("ContractDialog: Zusicherungen nur per Klick", () => {
  it("submit fragt nicht mehr nach vorbelegten Zusicherungen", () => {
    const submit = VERTRAG.slice(VERTRAG.indexOf("const submit = async (e) => {"),
                                 VERTRAG.indexOf("setLoading(true);", VERTRAG.indexOf("const submit = async (e) => {")));
    expect(submit).not.toContain("ungepruefteUebernahmen");
    expect(submit).not.toContain("Aus dem Inserat vorbelegt");
    expect(submit).not.toContain("Sie stehen so als Zusicherung im Vertrag.");
  });

  it("der Knopf „Übernehmen“ ist der einzige Weg vom Vorschlag ins Formular", () => {
    // vorschlagSetzen wird nur in vorschlaegeUebernehmen aufgerufen, und das nur aus onClick
    const aufrufe = VERTRAG.match(/vorschlagSetzen\(/g) || [];
    expect(aufrufe).toHaveLength(1);
    const uebernehmen = VERTRAG.match(/vorschlaegeUebernehmen\(/g) || [];
    const perKlick = VERTRAG.match(/onClick=\{\(\) => vorschlaegeUebernehmen\(/g) || [];
    expect(perKlick).toHaveLength(2);
    expect(uebernehmen).toHaveLength(perKlick.length);
  });
});
