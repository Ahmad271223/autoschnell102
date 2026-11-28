import { describe, expect, it } from "vitest";
import { nachKorrektur } from "./versand";

describe("nachKorrektur (erneuter Versand nach Korrektur, 21.09.2026)", () => {
  it("erster Versand: keine Korrektur", () => {
    expect(nachKorrektur({ version: 1, send_status: [] })).toBe(false);
    expect(nachKorrektur({})).toBe(false);
    expect(nachKorrektur(null)).toBe(false);
  });

  it("dieselbe Fassung noch einmal: keine Korrektur", () => {
    expect(nachKorrektur({ version: 2, send_status: [
      { channel: "email", version: 2, zustellung: "versendet" }] })).toBe(false);
  });

  it("frühere Fassung ging raus: Korrektur", () => {
    expect(nachKorrektur({ version: 2, send_status: [
      { channel: "email", version: 1, zustellung: "versendet" }] })).toBe(true);
    expect(nachKorrektur({ version: 3, send_status: [
      { channel: "whatsapp", version: 2 }] })).toBe(true);
  });

  it("alter Eintrag ohne Fassung zählt als Fassung 1", () => {
    expect(nachKorrektur({ version: 2, send_status: [{ channel: "email" }] })).toBe(true);
    expect(nachKorrektur({ version: 1, send_status: [{ channel: "email" }] })).toBe(false);
  });

  it("gescheiterte Sendungen und alte Textmails zählen nicht", () => {
    expect(nachKorrektur({ version: 2, send_status: [
      { channel: "email", version: 1, zustellung: "fehlgeschlagen" },
      { channel: "email", version: 1, art: "bahn", zustellung: "versendet" }] })).toBe(false);
  });
});
