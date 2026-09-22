/*
 * Rollenprüfung 22.09.2026, Welle 3 — Kaufanfragen und Inserats-Editor
 * (Team markt_haendler), Übergaben von markt_kaeufer und termine_protokoll:
 *  RP-093(4)  Annahme ohne Preisangebot: "zum Inseratspreis (Stufe)"
 *  RP-057 b   Einkaufspreis offen (mehrere Kaufverträge, verschiedene Preise)
 */
import { describe, expect, it, vi } from "vitest";

vi.mock("sonner", () => ({ toast: { success: vi.fn(), error: vi.fn(), info: vi.fn(), warning: vi.fn(), message: vi.fn() } }));

const { preisBlock } = await import("./Anfragen");
const { einkaufspreisOffen } = await import("./Inserat");

describe("RP-093(4): vereinbarter Preis aus dem Inserat", () => {
  it("nennt die Preisstufe unter dem vereinbarten Preis", () => {
    expect(preisBlock({ status: "akzeptiert", offer: null, agreed_price: 18000, agreed_price_quelle: "inserat_b2b" }))
      .toEqual({ titel: "Vereinbarter Preis", betrag: 18000, unter: "zum Inseratspreis (B2B)" });
    expect(preisBlock({ status: "akzeptiert", agreed_price: 19000, agreed_price_quelle: "inserat_oeffentlich" }).unter)
      .toBe("zum Inseratspreis (öffentlich)");
    expect(preisBlock({ status: "akzeptiert", agreed_price: 17000, agreed_price_quelle: "inserat_netzwerk" }).unter)
      .toBe("zum Inseratspreis (Netzwerk)");
  });

  it("mit Käuferangebot bleibt das Angebot die Unterzeile, unbekannte Quellen ohne Text", () => {
    expect(preisBlock({ status: "akzeptiert", offer: 17000, agreed_price: 18000 }).unter)
      .toMatch(/Angebot des Käufers: 17\.000 €/);
    expect(preisBlock({ status: "akzeptiert", offer: null, agreed_price: 18000, agreed_price_quelle: "etwas" }).unter)
      .toBe("");
    // ohne Betrag (Altbestand) keine Stufe erfinden
    expect(preisBlock({ status: "akzeptiert", agreed_price: null, agreed_price_quelle: "inserat_b2b" }))
      .toMatchObject({ titel: "Angenommen ohne Preisangebot", unter: "" });
  });
});

describe("RP-057 b: Einkaufspreis offen", () => {
  it("nur bei 'mehrdeutig' ohne Preis", () => {
    expect(einkaufspreisOffen({ purchase_price: null, purchase_price_quelle: "mehrdeutig" })).toBe(true);
    expect(einkaufspreisOffen({ purchase_price: 19000, purchase_price_quelle: "mehrdeutig" })).toBe(false);
    expect(einkaufspreisOffen({ purchase_price: 0, purchase_price_quelle: "vertrag" })).toBe(false);
    expect(einkaufspreisOffen({ purchase_price: null, purchase_price_quelle: "keiner" })).toBe(false);
    expect(einkaufspreisOffen(undefined)).toBe(false);
  });
});
