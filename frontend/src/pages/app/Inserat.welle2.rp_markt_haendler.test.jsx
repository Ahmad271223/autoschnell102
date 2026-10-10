/*
 * Rollenprüfung 22.09.2026, Welle 2 — Inserats-Editor (Team markt_haendler),
 * Übergaben von markt_kaeufer, bestand_ui, betrieb:
 *  RP-521  Hinweis, wenn der öffentliche Preis unter B2B/Netzwerk liegt
 *  RP-533  übersprungene Fotos (HEIC / vom Server abgelehnt) benennen
 *  RP-519  Ablaufdatum als TT.MM.JJJJ
 */
import { describe, expect, it, vi } from "vitest";

vi.mock("sonner", () => ({ toast: { success: vi.fn(), error: vi.fn(), info: vi.fn(), warning: vi.fn(), message: vi.fn() } }));

const { preisStufenHinweis, fotoAblehnungText, laufzeitInfo } = await import("./Inserat");

describe("RP-521: Preisstufen", () => {
  it("öffentlich unter B2B bzw. Netzwerk -> Hinweis", () => {
    expect(preisStufenHinweis({ public: 18000, b2b: 19000, network: null }))
      .toMatch(/niedriger als der B2B-Preis.*niedrigsten für sie zulässigen Preis — hier den öffentlichen/);
    expect(preisStufenHinweis({ public: 18000, b2b: 19000, network: 20000 }))
      .toMatch(/der B2B-Preis und der Netzwerkpreis/);
  });
  it("übliche Staffel (öffentlich am höchsten) -> kein Hinweis", () => {
    expect(preisStufenHinweis({ public: 20000, b2b: 19000, network: 18000 })).toBe("");
    expect(preisStufenHinweis({ public: null, b2b: 19000 })).toBe("");
    expect(preisStufenHinweis(undefined)).toBe("");
  });
});

describe("RP-533: übersprungene Fotos", () => {
  it("nennt HEIC-Dateien und vom Server abgelehnte mit Namen", () => {
    expect(fotoAblehnungText(2, [])).toBe("2 Foto(s) im HEIC-Format übersprungen — bitte als JPG speichern");
    const t = fotoAblehnungText(0, [{ name: "IMG_1.jpg", grund: "kein Bild" }]);
    expect(t).toBe("1 Foto(s) abgelehnt (IMG_1.jpg: kein Bild)");
    expect(fotoAblehnungText(0, [])).toBe("");
  });
  it("kürzt lange Listen", () => {
    const viele = Array.from({ length: 5 }, (_, i) => ({ name: `f${i}.jpg`, grund: "x" }));
    expect(fotoAblehnungText(1, viele)).toMatch(/^1 Foto\(s\) im HEIC-Format .*\. 5 Foto\(s\) abgelehnt \(f0\.jpg: x; f1\.jpg: x; f2\.jpg: x; …\)$/);
  });
});

describe("RP-519: Ablaufdatum", () => {
  it("TT.MM.JJJJ mit führender Null", () => {
    const info = laufzeitInfo("2026-10-03T12:00:00+00:00", new Date("2026-09-22T12:00:00+00:00"));
    expect(info.datum).toBe("03.10.2026");
    expect(info.tage).toBe(11);
  });
});
