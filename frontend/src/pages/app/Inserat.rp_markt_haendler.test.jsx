/*
 * Rollenprüfung 22.09.2026 — Inserat-Editor (Team markt_haendler).
 *  RP-037/455  Reserviert: Speichern schickt nur erlaubte Felder
 *  RP-458      geleerte Preise gehen als null mit
 *  RP-463      Stand (updated_at) geht mit
 *  RP-523      "150 Tkm" wird 150.000 km, nicht 150 km
 *  RP-036      zu lange Beschreibung wird an einer Grenze gekürzt
 *  RP-522      Foto-Aktionen behalten ungespeicherte Eingaben
 *  RP-469      Reihenfolge / Titelbild
 *  RP-460      "Verkauft" schlägt den vereinbarten Preis vor
 *  RP-468      Restlaufzeit
 */
import { describe, expect, it, vi } from "vitest";

vi.mock("sonner", () => ({ toast: { success: vi.fn(), error: vi.fn(), info: vi.fn(), warning: vi.fn(), message: vi.fn() } }));

const {
  speicherDaten, kmFehler, datenFuerServer, beschreibungKuerzen, fotoFelderUebernehmen,
  fotoReihenfolge, verkaufsVorschlag, laufzeitInfo, inseratStand,
} = await import("./Inserat");

const inserat = (extra = {}) => ({
  id: "L1", status: "veroeffentlicht", title: "Golf", description: "Kurz",
  known_defects: ["Kratzer", "", "  "], photos: { mode: "neu", uploaded_keys: ["a", "b", "c"] },
  prices: { public: 20900, b2b: null, network: 18000 }, costs: [{ label: "Transport", amount: 100 }],
  data: { mileage: "150 Tkm", make_label: "VW" }, updated_at: "2026-09-22T10:00:00+00:00",
  ...extra,
});

describe("RP-037/455: Speichern während einer Reservierung", () => {
  it("schickt keine Preise, Daten oder Mängel mit (sonst 400 vom Server)", () => {
    const d = speicherDaten(inserat({ status: "reserviert" }));
    expect(Object.keys(d).sort()).toEqual(["costs", "description", "stand", "title"]);
  });

  it("RP-532: der Foto-Modus geht nie mit (der Server entscheidet)", () => {
    expect("photo_mode" in speicherDaten(inserat({ photos: { mode: "einkauf" } }))).toBe(false);
  });

  it("außerhalb der Reservierung geht alles mit — geleerte Preise als null (RP-458)", () => {
    const d = speicherDaten(inserat());
    expect(d.price_public).toBe(20900);
    expect(d.price_b2b).toBeNull();
    expect("price_b2b" in d).toBe(true);
    expect(d.price_network).toBe(18000);
    expect(d.known_defects).toEqual(["Kratzer"]);
    expect(d.stand).toBe("2026-09-22T10:00:00+00:00");   // RP-463
  });

  it("ohne bekannten Stand wird keiner mitgeschickt", () => {
    expect("stand" in speicherDaten(inserat({ updated_at: undefined }))).toBe(false);
  });
});

describe("RP-523: Kilometerstand", () => {
  it("'150 Tkm', '150k' und '150.000' werden 150000", () => {
    expect(datenFuerServer({ mileage: "150 Tkm" }).mileage).toBe(150000);
    expect(datenFuerServer({ mileage: "150k" }).mileage).toBe(150000);
    expect(datenFuerServer({ mileage: "150.000" }).mileage).toBe(150000);
    expect(speicherDaten(inserat()).data.mileage).toBe(150000);
  });

  it("leer bleibt leer, Zahlen bleiben Zahlen", () => {
    expect(datenFuerServer({ mileage: "" }).mileage).toBe("");
    expect(datenFuerServer({ mileage: 85120 }).mileage).toBe(85120);
  });

  it("Unlesbares wird vor dem Speichern gemeldet", () => {
    expect(kmFehler({ mileage: "viel" })).toMatch(/Kilometerstand/);
    expect(kmFehler({ mileage: "150 Tkm" })).toBe("");
    expect(kmFehler({ mileage: 150000 })).toBe("");
    expect(kmFehler({})).toBe("");
  });
});

describe("RP-036: Beschreibung kürzen", () => {
  it("kürzt an einer Aufzählungsgrenze auf höchstens die Grenze", () => {
    const lang = "Ausstattung: " + Array.from({ length: 80 }, (_, i) => `Merkmal ${i}`).join(", ");
    const kurz = beschreibungKuerzen(lang, 500);
    expect(kurz.length).toBeLessThanOrEqual(500);
    expect(kurz.endsWith("…")).toBe(true);
    expect(kurz).not.toMatch(/, …$/);
    expect(lang.startsWith(kurz.slice(0, -1))).toBe(true);
  });

  it("kurze Texte bleiben unverändert", () => {
    expect(beschreibungKuerzen("Gepflegt", 500)).toBe("Gepflegt");
  });
});

describe("RP-522: Foto-Aktionen behalten ungespeicherte Eingaben", () => {
  it("übernimmt nur die Foto-Felder", () => {
    const server = inserat({ photos: { mode: "neu", uploaded_keys: ["a", "b", "c", "d"] },
                             photo_urls: [{ key: "d", url: "/x" }], updated_at: "neu" });
    const basis = inseratStand(server);
    const offen = { ...server, description: "noch nicht gespeichert", prices: { public: 19000 },
                    photos: { mode: "neu", uploaded_keys: ["a", "b", "c"] }, updated_at: "alt" };
    const out = fotoFelderUebernehmen(offen, server, basis);
    expect(out.description).toBe("noch nicht gespeichert");
    expect(out.prices.public).toBe(19000);
    expect(out.photos.uploaded_keys).toEqual(["a", "b", "c", "d"]);
    expect(out.photo_urls).toEqual([{ key: "d", url: "/x" }]);
    expect(out.updated_at).toBe("neu");       // niemand sonst hat etwas geändert
  });

  it("hat jemand anderes Preis oder Daten geändert, bleibt der alte Stand (nächstes Speichern: 409)", () => {
    const vorher = inserat();
    const basis = inseratStand(vorher);
    const server = { ...vorher, prices: { public: 25000 }, updated_at: "fremd" };
    expect(fotoFelderUebernehmen(vorher, server, basis).updated_at).toBe(vorher.updated_at);
  });
});

describe("RP-469: Reihenfolge", () => {
  it("Titelbild, vor, zurück", () => {
    expect(fotoReihenfolge(["a", "b", "c"], "c", "titel")).toEqual(["c", "a", "b"]);
    expect(fotoReihenfolge(["a", "b", "c"], "b", -1)).toEqual(["b", "a", "c"]);
    expect(fotoReihenfolge(["a", "b", "c"], "b", 1)).toEqual(["a", "c", "b"]);
    expect(fotoReihenfolge(["a", "b", "c"], "a", -1)).toBeNull();
    expect(fotoReihenfolge(["a", "b", "c"], "a", "titel")).toBeNull();
    expect(fotoReihenfolge(["a"], "x", 1)).toBeNull();
  });
});

describe("RP-460: Vorschlag für 'Verkauft'", () => {
  it("vereinbarter Preis vor öffentlichem Preis", () => {
    expect(verkaufsVorschlag({ vereinbarter_preis: 18500, prices: { public: 20900 } }))
      .toEqual({ betrag: 18500, vereinbart: true });
    expect(verkaufsVorschlag({ vereinbarter_preis: null, prices: { public: 20900 } }))
      .toEqual({ betrag: 20900, vereinbart: false });
    expect(verkaufsVorschlag({ prices: {} })).toEqual({ betrag: null, vereinbart: false });
  });
});

describe("RP-468: Laufzeit", () => {
  const jetzt = new Date("2026-09-22T12:00:00Z");
  it("zeigt die Resttage", () => {
    expect(laufzeitInfo("2026-09-25T12:00:00+00:00", jetzt)).toMatchObject({ abgelaufen: false, tage: 3 });
  });
  it("erkennt eine abgelaufene Laufzeit", () => {
    expect(laufzeitInfo("2026-09-20T12:00:00+00:00", jetzt)).toMatchObject({ abgelaufen: true, tage: 0 });
    expect(laufzeitInfo(null, jetzt)).toBeNull();
  });
});

describe("RP-044: ungespeichert", () => {
  it("jede Änderung an bearbeiteten Feldern ändert den Stand", () => {
    const l = inserat();
    expect(inseratStand({ ...l })).toBe(inseratStand(l));
    expect(inseratStand({ ...l, description: "neu" })).not.toBe(inseratStand(l));
    expect(inseratStand({ ...l, prices: { ...l.prices, b2b: 1 } })).not.toBe(inseratStand(l));
    // Foto-Felder gehören nicht dazu
    expect(inseratStand({ ...l, photos: { uploaded_keys: [] } })).toBe(inseratStand(l));
  });
});
