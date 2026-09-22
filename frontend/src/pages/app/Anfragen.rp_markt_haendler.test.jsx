/*
 * Rollenprüfung 22.09.2026 — Kaufanfragen (Team markt_haendler).
 *  RP-456/047  Gegenangebot deutsch lesen ("20.900" ist 20.900 €, nicht 20,90 €)
 *  RP-042/141  Rückfrage vor "Akzeptieren & reservieren" mit Käufer und Betrag
 *  RP-093 (f)  Systemeinträge im Verlauf heißen "System", nicht "Käufer"
 */
import { describe, expect, it, vi } from "vitest";

vi.mock("sonner", () => ({ toast: { success: vi.fn(), error: vi.fn(), info: vi.fn(), warning: vi.fn() } }));

const { gegenangebotBetrag, verlaufVon, annahmeFrage } = await import("./Anfragen");

describe("RP-456: Gegenangebot", () => {
  it("liest deutsche Beträge", () => {
    expect(gegenangebotBetrag("20.900")).toEqual({ betrag: 20900, fehler: "" });
    expect(gegenangebotBetrag("18000")).toEqual({ betrag: 18000, fehler: "" });
    expect(gegenangebotBetrag("17.250,50 €")).toEqual({ betrag: 17250.5, fehler: "" });
  });

  it("lehnt Leeres, Unsinn und 0 ab", () => {
    expect(gegenangebotBetrag("").fehler).toMatch(/Betrag/);
    expect(gegenangebotBetrag("viel").fehler).toMatch(/Zahl/);
    expect(gegenangebotBetrag("0").fehler).toMatch(/Zahl/);
  });
});

describe("RP-093 (f): Verlauf", () => {
  it("nennt System, Händler und Käufer richtig", () => {
    expect(verlaufVon("system")).toBe("System");
    expect(verlaufVon("haendler")).toBe("Du");
    expect(verlaufVon("kaeufer")).toBe("Käufer");
  });
});

describe("RP-042/141: Rückfrage vor dem Annehmen", () => {
  it("nennt Käufer, Betrag und die verbindliche Reservierung", () => {
    const text = annahmeFrage({ status: "offen", offer: 17000, buyer_name: "Handel Nord GmbH" });
    expect(text).toContain("Handel Nord GmbH");
    expect(text).toMatch(/17\.000,00\s€/);
    expect(text).toContain("verbindlich");
  });

  it("beim Gegenangebot des Käufers zählt dessen Betrag", () => {
    const text = annahmeFrage({ status: "gegenangebot_kaeufer", offer: 15000, buyer_counter_offer: 16500 });
    expect(text).toMatch(/16\.500,00\s€/);
  });

  it("ohne Betrag wird darauf hingewiesen", () => {
    // RP-093(4): ohne Angebot gilt der Inseratspreis dieses Käufers
    const text = annahmeFrage({ status: "offen", offer: null });
    expect(text).toContain("OHNE eigenes Preisangebot zum Inseratspreis");
    expect(text).not.toContain("OHNE vereinbarten Betrag");
  });
});
