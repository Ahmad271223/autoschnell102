/*
 * Prüfung 04.10.2026 (Nr. 22): Der Server fragt bei einem zweiten Versand derselben Fassung an dieselbe
 * Adresse selbst nach (409 "bereits_versendet") — auch nach dem Neuladen der Seite. Der Dialog zeigt die
 * Rückfrage und sendet nur bestätigt mit erneut=true. "Unklar" ist kein "fehlgeschlagen".
 * Dazu: WhatsApp-Ersatzfenster ohne "noopener" (damit liefert window.open immer null).
 */
import { describe, expect, it } from "vitest";

const { default: SEND } = await import("./SendDialog.jsx?raw");

describe("Versand-Dialog: nie unbemerkt doppelt", () => {
  it("bestätigter zweiter Versand trägt erneut=true", () => {
    expect(SEND).toMatch(/const send = async \(channel, erneut = false\)/);
    expect(SEND).toMatch(/\.\.\.\(erneut \? \{ erneut: true \} : \{\}\)/);
  });

  it("Rückfrage des Servers (409) wird gezeigt, nur bestätigt wird erneut gesendet", () => {
    expect(SEND).toContain('code === "bereits_versendet" || code === "frueherer_versand_unklar"');
    expect(SEND).toMatch(/window\.confirm\(d\.msg[\s\S]{0,200}await send\(channel, true\)/);
  });

  it("unklarer Versand ist eine Warnung, kein Fehler", () => {
    expect(SEND).toMatch(/code === "versand_unklar"\) \{\s*\/\/[^\n]*\n\s*toast\.warning\(d\.msg/);
  });

  it("unklare eigene Belegkopie darf nicht wie fehlgeschlagener Kundenversand wirken", () => {
    expect(SEND).toContain('data?.kopie === "unklar"');
    expect(SEND).toContain("der Vertrag ist beim Kunden angekommen");
    expect(SEND).toContain("NICHT");
  });

  it("WhatsApp-Ersatzfenster ohne noopener (sonst gilt ein offener Tab als blockiert)", () => {
    expect(SEND).not.toMatch(/window\.open\([^)]*"noopener"/);
  });
});
