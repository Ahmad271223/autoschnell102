/**
 * Rollenprüfung 22.09.2026 — reine Hilfen der Fahrer-App.
 *  RP-060/RP-159  Preisvorschlag deutsch lesen ("15.000" = 15.000 €, nicht 15 €).
 *  RP-061/RP-160  Drei-Wege-Zusammenführung nach einem Revisionskonflikt.
 *  RP-067/RP-166  Abschnitt 5 startet unbeantwortet (null).
 *  RP-537         Grund für "nicht abgeholt".
 */
import { describe, expect, it, vi } from "vitest";

vi.mock("@/context/DriverContext", () => ({ driverApi: { get: vi.fn() } }));

const {
  LEERER_ENTWURF, entwurfAusServer, entwurfZusammenfuehren, istRevisionsKonflikt,
  nutzlast, preisVorschlagLesen,
} = await import("./protokollEntwurf");
const { nichtAbgeholtNotiz, protokollIstFinal } = await import("./fahrtPruefung");
const { driverApi } = await import("@/context/DriverContext");

describe("Preisvorschlag (RP-060)", () => {
  it("liest deutsche Beträge", () => {
    expect(preisVorschlagLesen("15.000")).toEqual({ lesbar: true, wert: 15000 });
    expect(preisVorschlagLesen("12,50")).toEqual({ lesbar: true, wert: 12.5 });
    expect(preisVorschlagLesen("17.250,50")).toEqual({ lesbar: true, wert: 17250.5 });
    expect(preisVorschlagLesen("")).toEqual({ lesbar: true, wert: 0 });
    expect(preisVorschlagLesen("1.2.3")).toEqual({ lesbar: false, wert: null });
  });

  it("Nutzlast: Zahl statt Text, unlesbar = Feld fehlt, Abschnitt 5 nur beantwortet", () => {
    const n = nutzlast({ ...LEERER_ENTWURF, preis_vorschlag: "15.000", notes: "x" }, "Vera");
    expect(n.preis_vorschlag).toBe(15000);
    expect(n.seller_name).toBe("Vera");
    expect("damages_confirmed" in n).toBe(false);
    const kaputt = nutzlast({ ...LEERER_ENTWURF, preis_vorschlag: "1.2.3", damages_confirmed: false });
    expect("preis_vorschlag" in kaputt).toBe(false);
    expect(kaputt.damages_confirmed).toBe(false);
    expect(nutzlast({ ...LEERER_ENTWURF }).preis_vorschlag).toBe(0);
  });
});

describe("Serverstand -> Formular (RP-067)", () => {
  it("Abschnitt 5 bleibt unbeantwortet, Preis deutsch", () => {
    expect(LEERER_ENTWURF.damages_confirmed).toBeNull();
    expect(entwurfAusServer({}).damages_confirmed).toBeNull();
    expect(entwurfAusServer({ damages_confirmed: null }).damages_confirmed).toBeNull();
    expect(entwurfAusServer({ damages_confirmed: false }).damages_confirmed).toBe(false);
    expect(entwurfAusServer({ preis_vorschlag: 15000.5 }).preis_vorschlag).toBe("15000,5");
    expect(entwurfAusServer({ preis_vorschlag: 0 }).preis_vorschlag).toBe("");
    expect(preisVorschlagLesen(entwurfAusServer({ preis_vorschlag: 15000.5 }).preis_vorschlag).wert)
      .toBe(15000.5);
  });
});

describe("Zusammenführen nach Revisionskonflikt (RP-061)", () => {
  it("lokale Änderungen gewinnen, fremde Änderungen bleiben erhalten", () => {
    const basis = entwurfAusServer({ notes: "alt", documents: { Brief: true }, keys_count: "2" });
    const server = entwurfAusServer({ notes: "alt", documents: { Brief: true, Schein: false },
                                      keys_count: "3" });           // zweiter Tab: Schein + Schlüssel
    const lokal = { ...basis, notes: "neu getippt", documents: { Brief: false } };
    const z = entwurfZusammenfuehren(server, lokal, basis);
    expect(z.notes).toBe("neu getippt");
    expect(z.documents).toEqual({ Brief: false, Schein: false });
    expect(z.keys_count).toBe("3");
  });

  it("erkennt nur Revisionskonflikte", () => {
    expect(istRevisionsKonflikt(409, "Der Entwurf wurde inzwischen in einem anderen Tab oder auf "
      + "einem anderen Gerät gespeichert — bitte neu laden.")).toBe(true);
    expect(istRevisionsKonflikt(409, "Bitte die App neu laden — der Entwurf braucht den aktuellen "
      + "Stand (Revision).")).toBe(true);
    expect(istRevisionsKonflikt(409, "Das Protokoll liegt beim Händler zur Freigabe")).toBe(false);
    expect(istRevisionsKonflikt(500, "anderen Tab")).toBe(false);
  });
});

describe("Fahrt-Prüfungen (RP-064, RP-537)", () => {
  it("Protokoll final?", async () => {
    driverApi.get.mockResolvedValueOnce({ data: { protocol: { status: "final" } } });
    expect(await protokollIstFinal("t1")).toBe(true);
    driverApi.get.mockResolvedValueOnce({ data: { protocol: null, template: {} } });
    expect(await protokollIstFinal("t1")).toBe(false);
    driverApi.get.mockRejectedValueOnce({ response: { status: 409 } });
    expect(await protokollIstFinal("t1")).toBe(false);
    driverApi.get.mockRejectedValueOnce(new Error("Network Error"));
    expect(await protokollIstFinal("t1")).toBeNull();
  });

  it("Grund Pflicht, Sonstiges nur mit Erklärung", () => {
    expect(nichtAbgeholtNotiz("", "")).toBeNull();
    expect(nichtAbgeholtNotiz("sonstiges", "ab")).toBeNull();
    expect(nichtAbgeholtNotiz("sonstiges", "Reifen platt")).toBe("Nicht abgeholt: Sonstiges — Reifen platt");
    expect(nichtAbgeholtNotiz("abgesagt", "")).toBe("Nicht abgeholt: Verkäufer hat abgesagt");
    expect(nichtAbgeholtNotiz("abgesagt", "x".repeat(5000)).length).toBeLessThanOrEqual(1900);
  });
});
