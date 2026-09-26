/*
 * Rollenprüfung 22.09.2026, Welle 2 — Kaufanfragen (Team markt_haendler),
 * Übergaben von markt_kaeufer, admin_abo, betrieb:
 *  RP-491         gesehenen Stand mitschicken (erwarteter_status/-betrag)
 *  RP-477         nach der Einigung der vereinbarte Preis groß
 *  RP-502/089/098 beendete Anfragen und Verlauf ohne rohe Codes
 *  RP-519         Ablaufdatum des Inserats
 */
import { describe, expect, it, vi } from "vitest";

vi.mock("sonner", () => ({ toast: { success: vi.fn(), error: vi.fn(), info: vi.fn(), warning: vi.fn() } }));

const { antwortDaten, preisBlock, beendetText, verlaufZeile, laeuftAbText } = await import("./Anfragen");

describe("RP-491: Antwort mit gesehenem Stand", () => {
  it("Annehmen eines Angebots nennt Status und Betrag", () => {
    expect(antwortDaten({ status: "offen", offer: 17000 }, "akzeptieren")).toEqual({
      action: "akzeptieren", message: "", erwarteter_status: "offen", erwarteter_betrag: 17000,
    });
  });

  it("beim Käufer-Gegenangebot zählt dessen Betrag", () => {
    const d = antwortDaten({ status: "gegenangebot_kaeufer", offer: 15000, buyer_counter_offer: 16500 }, "akzeptieren");
    expect(d.erwarteter_betrag).toBe(16500);
  });

  it("ohne Preisangebot geht null ausdrücklich mit (nicht weggelassen)", () => {
    const d = antwortDaten({ status: "offen", offer: null }, "akzeptieren");
    expect("erwarteter_betrag" in d).toBe(true);
    expect(d.erwarteter_betrag).toBeNull();
    expect(JSON.parse(JSON.stringify(d)).erwarteter_betrag).toBeNull();
  });

  it("Ablehnen/Gegenangebot: nur der Status, Zusatzfelder gewinnen", () => {
    const d = antwortDaten({ status: "offen", offer: 17000 }, "gegenangebot", { counter_offer: 18000, message: "Hallo" });
    expect(d).toEqual({ action: "gegenangebot", message: "Hallo", erwarteter_status: "offen", counter_offer: 18000 });
    expect("erwarteter_betrag" in antwortDaten({ status: "offen" }, "ablehnen")).toBe(false);
  });
});

describe("RP-477: vereinbarter Preis", () => {
  it("nach der Annahme groß der vereinbarte Preis, klein das Angebot", () => {
    const b = preisBlock({ status: "akzeptiert", offer: 17000, agreed_price: 18000 });
    expect(b.titel).toBe("Vereinbarter Preis");
    expect(b.betrag).toBe(18000);
    expect(b.unter).toMatch(/Angebot des Käufers: 17\.000 €/);
  });

  it("ohne Betrag angenommen", () => {
    expect(preisBlock({ status: "akzeptiert", offer: null, agreed_price: null }).titel)
      .toBe("Angenommen ohne Preisangebot");
  });

  it("laufende Anfrage zeigt das Käuferangebot", () => {
    expect(preisBlock({ status: "offen", offer: 9000 })).toMatchObject({ titel: "Angebot des Käufers", betrag: 9000 });
    expect(preisBlock({ status: "offen", offer: null }).titel).toBe("Ohne Preisangebot");
  });
});

describe("RP-502/089/098: beendete Anfragen und Verlauf", () => {
  it("Gründe lesbar", () => {
    expect(beendetText({ status: "abgelehnt", beendet_grund: "kaeufer_zurueckgezogen" })).toBe("Vom Käufer zurückgezogen");
    expect(beendetText({ status: "abgelehnt", beendet_grund: "netzwerk_entfernt" }))
      .toBe("Beendet — Käufer aus dem Netzwerk entfernt");
    expect(beendetText({ status: "abgelehnt", beendet_grund: "inserat_abgelaufen" })).toBe("Beendet — Inserat abgelaufen");
    expect(beendetText({ status: "abgelehnt", beendet_grund: "kaeufer_gesperrt" })).toBe("Beendet — Käufer gesperrt");
    expect(beendetText({ status: "abgelehnt", beendet_grund: "etwas_neues" })).toBe("Beendet");
  });

  it("ohne Grund: wer hat abgelehnt?", () => {
    expect(beendetText({ status: "abgelehnt", history: [{ von: "kaeufer", aktion: "ablehnen" }] })).toBe("Vom Käufer abgelehnt");
    expect(beendetText({ status: "abgelehnt", history: [{ von: "haendler", aktion: "ablehnen" }] })).toBe("Von dir abgelehnt");
    expect(beendetText({ status: "offen" })).toBeNull();
  });

  it("Verlauf ohne rohe Codes", () => {
    expect(verlaufZeile({ von: "system", aktion: "netzwerk_entfernt" }))
      .toBe("System · Beendet — Käufer aus dem Netzwerk entfernt");
    expect(verlaufZeile({ von: "kaeufer", aktion: "zurueckgezogen" })).toBe("Käufer · zurückgezogen");
    expect(verlaufZeile({ von: "haendler", aktion: "akzeptieren", angebot: 18000 })).toBe("Du · angenommen · 18.000 €");
    expect(verlaufZeile({ von: "system", aktion: "kaeufer_gesperrt" })).toBe("System · Beendet — Käufer gesperrt");
    expect(verlaufZeile({ von: "system", aktion: "ganz_neu" })).toBe("System · ganz neu");
  });
});

describe("RP-519: Ablaufdatum", () => {
  const jetzt = new Date("2026-09-22T12:00:00+00:00");
  it("zeigt TT.MM.JJJJ", () => {
    expect(laeuftAbText("2026-10-03T12:00:00+00:00", jetzt)).toContain("läuft ab am 03.10.2026");
  });
  it("abgelaufen bzw. ohne Datum", () => {
    expect(laeuftAbText("2026-09-20T12:00:00+00:00", jetzt)).toMatch(/abgelaufen/);
    expect(laeuftAbText(null, jetzt)).toBe("");
  });
});
