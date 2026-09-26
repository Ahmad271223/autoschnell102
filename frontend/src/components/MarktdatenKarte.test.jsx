/**
 * Market Intelligence (25.09.2026) — MarktdatenKarte im Vergleich:
 *  - laedt getrennt per GET mit kurzem Zeitlimit, NACH dem Vergleich
 *  - 404 / Timeout / leere Antwort: keine Karte, kein Fehler, kein Toast
 *  - mit Daten: "N guenstigste Vergleichsangebote", Median der N guenstigsten, Trend,
 *    Stichprobengroesse und Datenstand, nie das Wort "Marktmedian" als Wert
 *  - Reparaturwelle 6 Nr. 137/146: neutrale Feldnamen (median_sample_price), N aus den Daten,
 *    Differenz zum Median aus dem uebergebenen Preis gerechnet
 */
import { act, createElement } from "react";
import { createRoot } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

globalThis.IS_REACT_ACT_ENVIRONMENT = true;

const api = vi.hoisted(() => ({ get: vi.fn() }));
vi.mock("@/lib/api", () => ({ api, errMsg: (e, s) => e?.message || s }));

const { default: MarktdatenKarte } = await import("./MarktdatenKarte");

const DATEN = {
  segment_id: "bmw-320d:55001-85000:2019-2021", label: "BMW 320d", km_label: "55–85k km", ez_label: "EZ 2019–2021",
  sample_size: 20, sample_limit: 20, min_price: 18900, median_sample_price: 20250, avg_sample_price: 20410, max_sample_price: 21700,
  p25_price: 19600, p75_price: 21000, trend_7d_eur: -300, trend_7d_pct: -1.5, trend_30d_eur: -850, trend_30d_pct: -4.0,
  datenstand: "2026-10-01T05:10:00+00:00", datum: "2026-10-01", datenlage: "gut", beobachtete_tage: 31,
  trend_30d_bestand_eur: -640, trend_30d_bestand_pct: -3.2, anzahl_gemeinsam_30d: 9, trend_7d_bestand_eur: null, anzahl_gemeinsam: 0,
  // Backend-Differenz absichtlich FALSCH (anderer Preis) — die Karte muss selbst rechnen (Nr. 146)
  hinweis: "kein Marktmedian", preis_vs_median_eur: -9999, preis_vs_median_pct: -40, unter_sample_min: true,
  listing: { listing_id: "449438530", first_seen_at: "2026-09-13T04:00:00+00:00", first_price: 19900, current_price: 19400,
             change_since_first_eur: -500, price_reductions: 1, price_changes: 1, rank_today: 4, active_state: "seen" },
};

let wurzel; let behaelter;
const el = (t) => behaelter.querySelector(`[data-testid="${t}"]`);
async function starten(props) {
  behaelter = document.createElement("div"); document.body.appendChild(behaelter); wurzel = createRoot(behaelter);
  await act(async () => { wurzel.render(createElement(MarktdatenKarte, props)); });
  await act(async () => { for (let i = 0; i < 5; i += 1) await Promise.resolve(); });
}
beforeEach(() => { api.get.mockReset(); });
afterEach(async () => { if (wurzel) await act(async () => { wurzel.unmount(); }); wurzel = null; behaelter?.remove(); });

describe("MarktdatenKarte", () => {
  it("zeigt die Werte der N günstigsten, Trend, dieses Inserat und den Datenstand", async () => {
    api.get.mockResolvedValue({ data: DATEN });
    await starten({ vehicleId: "v1", preis: 19400 });
    expect(api.get).toHaveBeenCalledTimes(1);
    expect(api.get.mock.calls[0][0]).toBe("/market-intelligence/vehicle/v1");
    expect(api.get.mock.calls[0][1].timeout).toBeLessThanOrEqual(10000);
    const k = el("marktdaten-karte");
    expect(k).toBeTruthy();
    expect(k.textContent).toContain("20 günstigste Vergleichsangebote");
    expect(k.textContent).toContain("18.900 €");
    expect(el("marktdaten-median").textContent).toBe("20.250 €");
    expect(k.textContent).toContain("Median der 20 günstigsten");
    expect(k.textContent).not.toMatch(/Top-20/);
    expect(k.textContent).toContain("18.900 €–21.700 €");
    expect(el("marktdaten-trend").textContent).toBe("−850 € (-4 %)");
    // Review 26.09. Nr. 55: Bestandstrend (gleiche Autos) als zweite Zeile; 7 Tage ohne gemeinsame Autos bleibt weg
    expect(el("marktdaten-bestand").textContent).toContain("30 Tage, gleiche Autos: −640 € (-3,2 %) · 9 Autos");
    expect(el("marktdaten-bestand").textContent).not.toContain("7 Tage");
    expect(el("marktdaten-dieses").textContent).toContain("19.400 €");
    // Nr. 146: Differenz aus dem uebergebenen Preis (19.400 - 20.250 = -850, -4,2 %), nicht die (falsche) Backend-Differenz
    expect(el("marktdaten-differenz").textContent).toBe(" · −850 € (-4,2 %) zum Median der 20 günstigsten");
    expect(el("marktdaten-dieses").textContent).not.toContain("9.999");
    expect(el("marktdaten-dieses").textContent).not.toContain("unter dem günstigsten"), "19.400 liegt ueber dem Minimum 18.900";
    expect(el("marktdaten-verlauf").textContent).toContain("19.900 € → 19.400 €");
    expect(el("marktdaten-verlauf").textContent).toContain("1 Reduzierung");
    expect(el("marktdaten-verlauf").textContent).toContain("Platz 4 von 20");
    expect(el("marktdaten-datenlage").textContent).toContain("Datenlage gut");
    expect(k.textContent).toContain("20 Fahrzeuge");
    expect(k.textContent).toContain("kein Marktmedian");
    expect(k.textContent).not.toMatch(/Marktpreis\b/);
  });

  it("zeigt den Hinweis bei unsicherer Sortierung des letzten Abrufs (Review 26.09. Nr. 3)", async () => {
    api.get.mockResolvedValue({ data: { ...DATEN, sortierung_unsicher: true, datenlage: "unsicher" } });
    await starten({ vehicleId: "v1", preis: 19400 });
    expect(el("marktdaten-sortierung").textContent).toContain("Sortierung des letzten Abrufs unsicher");
    expect(el("marktdaten-datenlage").textContent).toContain("unsicher");
    await act(async () => { wurzel.unmount(); }); wurzel = null; behaelter?.remove();
    api.get.mockResolvedValue({ data: { ...DATEN, sortierung_unsicher: false } });
    await starten({ vehicleId: "v2", preis: 19400 });
    expect(el("marktdaten-sortierung")).toBeNull();
  });

  it("sagt 'kein exaktes Segment' bei unbekanntem Getriebe und 'Top-N nicht bewiesen' (Reparaturwelle 5 Nr. 28/1)", async () => {
    api.get.mockResolvedValue({ data: { sample_size: 0, kein_segment_grund: "Getriebe am Fahrzeug unbekannt — kein exaktes Segment" } });
    await starten({ vehicleId: "v1", preis: 19400 });
    expect(el("marktdaten-karte")).toBeNull();
    expect(el("marktdaten-karte-hinweis").textContent).toContain("Getriebe am Fahrzeug unbekannt — kein exaktes Segment");
    await act(async () => { wurzel.unmount(); }); wurzel = null; behaelter?.remove();
    api.get.mockResolvedValue({ data: { ...DATEN, top_n_bewiesen: false } });
    await starten({ vehicleId: "v2", preis: 19400 });
    expect(el("marktdaten-top-n").textContent).toContain("Top-N nicht bewiesen");
    expect(el("marktdaten-karte").textContent).toContain("18.900 €");
  });

  it("bleibt bei 404, Timeout oder leeren Daten unsichtbar", async () => {
    const e = new Error("Not found"); e.response = { status: 404 };
    api.get.mockRejectedValue(e);
    await starten({ vehicleId: "v1", preis: 1 });
    expect(el("marktdaten-karte")).toBeNull();
    await act(async () => { wurzel.unmount(); }); wurzel = null; behaelter?.remove();
    api.get.mockResolvedValue({ data: { sample_size: 0 } });
    await starten({ vehicleId: "v2", preis: 1 });
    expect(el("marktdaten-karte")).toBeNull();
    await act(async () => { wurzel.unmount(); }); wurzel = null; behaelter?.remove();
    api.get.mockImplementation(() => new Promise(() => {}));     // antwortet nie: Karte fehlt, nichts haengt
    await starten({ vehicleId: "v3", preis: 1 });
    expect(el("marktdaten-karte")).toBeNull();
  });

  it("ohne Fahrzeug-ID kein Aufruf", async () => {
    await starten({ vehicleId: "", preis: 1 });
    expect(api.get).not.toHaveBeenCalled();
  });

  it("N aus der Stichprobe (10 Zeilen), alte Feldnamen als Rückfall, ohne Preis die Backend-Differenz (Nr. 137/146)", async () => {
    api.get.mockResolvedValue({ data: { ...DATEN, sample_size: 10, median_sample_price: undefined, median_top20_price: 20250, max_sample_price: undefined, max_top20_price: 21700,
                                        preis_vs_median_eur: -1250, preis_vs_median_pct: -6.2, unter_sample_min: true, datenlage: "unvollstaendig" } });
    await starten({ vehicleId: "v1", preis: 18000 });
    expect(el("marktdaten-karte").textContent).toContain("Median der 10 günstigsten");
    expect(el("marktdaten-median").textContent).toBe("20.250 €");
    expect(el("marktdaten-differenz").textContent).toBe(" · −2.250 € (-11,1 %) zum Median der 10 günstigsten");
    expect(el("marktdaten-dieses").textContent).toContain("unter dem günstigsten beobachteten Angebot");
    expect(el("marktdaten-datenlage").textContent).toContain("unvollständig");
    await act(async () => { wurzel.unmount(); }); wurzel = null; behaelter?.remove();
    api.get.mockResolvedValue({ data: { ...DATEN, preis_vs_median_eur: -1250, preis_vs_median_pct: -6.2, unter_sample_min: false } });
    await starten({ vehicleId: "v2", preis: null });
    expect(el("marktdaten-differenz").textContent).toContain("−1.250 € (-6,2 %)");
  });
});
