/**
 * Go-Live-Prüfung 27.09.2026 (K1/K6, Zusatzfund): Aus dem Inserat
 * vorbelegte Zusicherungen gingen ohne jede Bestätigung in den Vertrag.
 * Jetzt fragt "PDF erstellen" einmal nach, solange sie niemand angefasst hat.
 */
import { describe, expect, it } from "vitest";

const { default: VERTRAG } = await import("./ContractDialog.jsx?raw");

describe("ContractDialog: vorbelegte Zusicherungen bestätigen", () => {
  it("submit fragt nach ungeprüften Übernahmen, bevor der Vertrag angelegt wird", () => {
    const submit = VERTRAG.slice(VERTRAG.indexOf("const submit = async (e) => {"));
    const pruefung = submit.indexOf("ungepruefteUebernahmen(inseratVorschlaege?.uebernommen, form, beruehrt.current)");
    const anlegen = submit.indexOf("setLoading(true);");
    expect(pruefung).toBeGreaterThan(0);
    expect(pruefung).toBeLessThan(anlegen);
    const block = submit.slice(pruefung, anlegen);
    expect(block).toMatch(/window\.confirm\(/);
    expect(block).toMatch(/return;/);
    expect(block).toContain("Sie stehen so als Zusicherung im Vertrag.");
  });
});
