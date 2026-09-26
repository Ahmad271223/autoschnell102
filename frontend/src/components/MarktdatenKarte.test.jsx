/**
 * Market Intelligence (25.09.2026) — MarktdatenKarte im Vergleich:
 *  - laedt getrennt per GET mit kurzem Zeitlimit, NACH dem Vergleich
 *  - 404 / Timeout / leere Antwort: keine Karte, kein Fehler, kein Toast
 *  - mit Daten: "20 guenstigste Vergleichsangebote", Top-20-Median, Trend,
 *    Stichprobengroesse und Datenstand, nie das Wort "Marktmedian" als Wert
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
  sample_size: 20, min_price: 18900, median_top20_price: 20250, avg_top20_price: 20410, max_top20_price: 21700,
  p25_price: 19600, p75_price: 21000, trend_7d_eur: -300, trend_7d_pct: -1.5, trend_30d_eur: -850, trend_30d_pct: -4.0,
  datenstand: "2026-10-01T05:10:00+00:00", datum: "2026-10-01", datenlage: "gut", beobachtete_tage: 31,
  hinweis: "kein Marktmedian", preis_vs_median_eur: -850, preis_vs_median_pct: -4.2, unter_top20_min: false,
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
  it("zeigt die Top-20-Werte, Trend, dieses Inserat und den Datenstand", async () => {
    api.get.mockResolvedValue({ data: DATEN });
    await starten({ vehicleId: "v1", preis: 19400 });
    expect(api.get).toHaveBeenCalledTimes(1);
    expect(api.get.mock.calls[0][0]).toBe("/market-intelligence/vehicle/v1");
    expect(api.get.mock.calls[0][1].timeout).toBeLessThanOrEqual(10000);
    const k = el("marktdaten-karte");
    expect(k).toBeTruthy();
    expect(k.textContent).toContain("20 günstigste Vergleichsangebote");
    expect(k.textContent).toContain("18.900 €");
    expect(k.textContent).toContain("20.250 €");
    expect(k.textContent).toContain("18.900 €–21.700 €");
    expect(el("marktdaten-trend").textContent).toBe("−850 € (-4 %)");
    expect(el("marktdaten-dieses").textContent).toContain("19.400 €");
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
});
