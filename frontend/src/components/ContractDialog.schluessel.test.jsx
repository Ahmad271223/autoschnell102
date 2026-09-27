/**
 * Entscheidung Ahmad 26.09.2026 (2): Schlüsselanzahl im Kaufvertrag.
 * Der Block „Übergabe & Empfangsbestätigung“ wird seit 24.09. nicht mehr
 * abgefragt — damit fehlte das einzige Feld für schluessel_anzahl. Jetzt steht
 * es unter „Zusicherungen & Zustand“ mit gelbem Hinweis, solange es leer ist
 * (nicht blockierend); der Fahrer gleicht vor Ort dagegen ab.
 */
import { describe, expect, it } from "vitest";

const { default: VERTRAG } = await import("./ContractDialog.jsx?raw");

describe("ContractDialog: Schlüsselanzahl (Entscheidung 26.09.2026)", () => {
  it("Feld mit setSchluesselAnzahl und gelbem Hinweis, solange leer", () => {
    const block = VERTRAG.slice(VERTRAG.indexOf('label="Schlüssel (Anzahl laut Vertrag)"'),
                                VERTRAG.indexOf('data-testid="contract-schluessel-hinweis"') + 400);
    expect(block).toMatch(/onChange=\{setSchluesselAnzahl\}/);
    expect(block).toMatch(/testid="contract-schluessel-anzahl"/);
    expect(block).toMatch(/inputMode="numeric"/);
    // Hinweis nur bei leerem Feld, Token-Farbe (helle und dunkle Ansicht)
    expect(block).toMatch(/\{!String\(form\.schluessel_anzahl \?\? ""\)\.trim\(\) && \(/);
    expect(block).toMatch(/color: "var\(--st-amber\)"/);
    expect(block).toContain("Bitte Schlüsselanzahl eintragen — sonst kann der Fahrer fehlende Schlüssel nicht abgleichen.");
    // nicht blockierend: kein required am Feld
    expect(block).not.toMatch(/required/);
  });
  it("führende Nullen fallen weg", () => {
    expect(VERTRAG).toMatch(/const n = cleanIntStr\(raw\)\.replace\(\/\^0\+\/, ""\);/);
  });
  // Startprüfung 27.09.2026 (K4): Die Empfangsbestätigung wird seit 24.09.
  // nicht mehr abgefragt — kein Weg im Dialog darf sie automatisch ankreuzen.
  it("Schlüsselanzahl hakt die Empfangsbestätigung nicht an", () => {
    const fn = VERTRAG.slice(VERTRAG.indexOf("const setSchluesselAnzahl"),
                             VERTRAG.indexOf("const preis = kaufpreisPruefen"));
    expect(fn).toMatch(/schluessel_anzahl: n/);
    expect(fn).not.toMatch(/empfang_/);
    // Nirgends im Dialog wird ein Empfangs-Kästchen auf true gesetzt.
    expect(VERTRAG).not.toMatch(/empfang_(schluessel|zulassungsbescheinigung|kaufpreis)\s*[:=]\s*(true|n \?)/);
    expect(VERTRAG).toMatch(/empfang_schluessel: false,/);
  });
});
