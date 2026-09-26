/**
 * Admin → Marktanalyse (25.09.2026): Modellliste mit Kennzahlen, Status,
 * Klick auf Modell → Segmentwahl (km × EZ) → Kennzahlen, Verlauf aus den
 * Tagesaggregaten, Auswertung, Tagestabelle, Top-20-Liste, Listing-Historie.
 * Alles nur lesend; Super-Admin-Knoepfe rufen die Schreibrouten.
 */
import { act, createElement as h } from "react";
import { createRoot } from "react-dom/client";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

globalThis.IS_REACT_ACT_ENVIRONMENT = true;

const netz = vi.hoisted(() => ({ posts: [], gets: [], aktiv: true }));
vi.mock("sonner", () => ({ toast: { success: vi.fn(), error: vi.fn(), info: vi.fn(), warning: vi.fn() } }));
vi.mock("recharts", () => {
  const Leer = ({ children }) => h("div", { "data-chart": "1" }, children);
  return { ResponsiveContainer: Leer, ComposedChart: Leer, BarChart: Leer, Line: () => null, Area: () => null, Bar: () => null,
           XAxis: () => null, YAxis: () => null, Tooltip: () => null, CartesianGrid: () => null };
});
const SEG = { id: "bmw-320d:55001-85000:2019-2021", model_id: "bmw-320d", label: "BMW 320d", min_km: 55001, max_km: 85000,
              km_label: "55–85k km", year_from: 2019, year_to: 2021, ez_label: "EZ 2019–2021", enabled: true,
              last_success_at: "2026-10-01T04:00:00+00:00", stats: { sample_size: 20 } };
const SEG2 = { ...SEG, id: "bmw-320d:55001-85000:2016-2018", year_from: 2016, year_to: 2018, ez_label: "EZ 2016–2018", stats: null };
const STATS = { sample_size: 20, min_price: 18900, median_price: 20250, avg_price: 20410, max_price: 21700, p25_price: 19600,
                p75_price: 21000, trend_7d_eur: -420, trend_7d_pct: -2.1, trend_30d_eur: -850, trend_30d_pct: -4.0,
                new_listings_7d: 14, price_reductions_7d: 9, beobachtete_tage: 31, datenlage: "gut", updated_at: "2026-10-01T05:10:00+00:00" };
vi.mock("@/lib/api", () => ({
  errMsg: (e, s) => e?.message || s,
  api: {
    get: vi.fn(async (url) => {
      netz.gets.push(url);
      if (url === "/admin/market/models") return { data: { modelle: [
        { id: "bmw-320d", label: "BMW 320d", fuel: "DIESEL", power_kw_min: 120, power_kw_max: 145, enabled: true, model_id: "10",
          segmente_aktiv: 16, segmente_mit_daten: 4, last_success_at: "2026-10-01T04:00:00+00:00", listings: 312,
          min_price: 18900, median_top20_mittel: 20250, trend_7d_pct: -2.1, trend_30d_pct: -4.0, crawl_status: "ok" },
        { id: "vw-golf-20tdi", label: "VW Golf 2.0 TDI", fuel: "DIESEL", enabled: false, model_id: "14", segmente_aktiv: 0, segmente_mit_daten: 0,
          listings: 0, min_price: null, median_top20_mittel: null, trend_7d_pct: null, trend_30d_pct: null, crawl_status: "wartet" }] } };
      if (url === "/admin/market/status") return { data: { aktiv: netz.aktiv, aktiv_quelle: "env", segmente: 16, modelle: 1, listings: 312, snapshots: 4000, token_vorhanden: true,
        actor: "sourabhbgp~mobile-de-scraper", budget: { _id: "2026-10", budget_usd: 450, used_usd: 12.5, reserved_usd: 0, rows: 4000, runs: 200 },
        takt: { intervall_tage: 3, segmente_je_tag: 6, buendel: 10, kosten_je_tag_usd: 0.38, kosten_je_monat_usd: 11.7, automatisch: true },
        monitoring: { tag: "2026-10-01", geplant: 6, erfolgreich: 6, fehlgeschlagen: 0, wartend: 0, laufend: 0, rows_heute: 120, rows_monat: 4000,
                      kosten_heute_usd: 0.11, kosten_monat_usd: 12.5, budget_uebrig_usd: 437.5, budget_anteil_pct: 2.8, mittlere_laufzeit_s: 9.4,
                      letzter_erfolg: { finished_at: "2026-10-01T05:10:00Z", segment_id: "bmw-320d:2019:50001-85000" },
                      alarme: [{ typ: "segment_veraltet", text: "3 Segment(e) seit über 48 h nicht erfolgreich aktualisiert", stufe: "warn" }] },
        jobs: { tag: "2026-10-01", completed: 6, running: 0, queued: 0, failed: 0, naechster: null }, km_buckets: [{ min_km: 10000, max_km: 30000 }],
        ez_buckets: [{ year_from: 2019, year_to: 2021 }], einstellungen: { rows_je_segment: 20 } } };
      if (url === "/admin/market/models/bmw-320d") return { data: { id: "bmw-320d", label: "BMW 320d", fuel: "DIESEL", make_id: "3500", model_id: "10", segmente: [SEG, SEG2] } };
      if (url.includes("/listings/449438530/history")) return { data: { listing: { title: "BMW 320d Touring", active_state: "seen", mileage_km: 78000,
        first_registration: "03/2020", postal_code: "30159", city: "Hannover", price_history: [{ at: "2026-09-13T04:00:00Z", price: 19400 }, { at: "2026-10-01T04:00:00Z", price: 18900 }] },
        snapshots: [{ date: "2026-09-13", segment_id: SEG.id, price: 19400, rank_in_sample: 5 }], hinweis_zustand: "" } };
      if (url.endsWith("/summary")) return { data: { segment: SEG, stats: STATS, letzter_job: { status: "completed" } } };
      if (url.endsWith("/history")) return { data: { reihe: [
        { date: "2026-09-30", sample_size: 20, min: 19100, median: 20500, avg: 20600, max: 21800, p25: 19700, p75: 21100, change_eur: null, change_pct: null },
        { date: "2026-10-01", sample_size: 20, min: 18900, median: 20250, avg: 20410, max: 21700, p25: 19600, p75: 21000, change_eur: -250, change_pct: -1.22 }],
        wochen: [{ woche: "2026-W40", median: 20375, tage: 2 }],
        auswertung: { veraenderung_eur: -250, veraenderung_pct: -1.22, groesster_rueckgang: { date: "2026-10-01", change_eur: -250 }, groesster_anstieg: null,
                      tage_fallend: 1, tage_steigend: 0, tage_unveraendert: 0, hoechster_median: 20500, niedrigster_median: 20250, tage: 2 } } };
      if (url.endsWith("/listings")) return { data: { date: "2026-10-01", listings: [
        { listing_id: "449438530", title: "BMW 320d Touring", price_today: 18900, current_price: 18900, mileage_km: 78000, first_registration: "03/2020",
          power_kw: 140, fuel: "Diesel", gearbox: "Automatik", postal_code: "30159", city: "Hannover", seller_type: "DEALER", price_rating_today: "GOOD_PRICE",
          mobile_created_at: "2026-09-05T11:46:02.000Z", first_seen_at: "2026-09-13T04:00:00+00:00", change_since_first_eur: -500, rank_today: 1, rank_yesterday: 3,
          price_change_eur: -200, first_price: 19400, price_reductions: 2 }] } };
      return { data: {} };
    }),
    post: vi.fn(async (url, body) => { netz.posts.push({ url, body }); return { data: { ok: true, neu: 6, tag: "2026-10-01", modelle: { neu: 0 }, segmente: { segmente: 16 }, erledigt: 1 } }; }),
    put: vi.fn(async (url, body) => { netz.posts.push({ url, body }); return { data: { ok: true, segmente: 16, takt: { intervall_tage: 3 } } }; }),
  },
}));
vi.mock("@/context/AuthContext", () => ({ useAuth: () => ({ user: { id: "sa", is_super_admin: true, role: "admin" } }) }));

const { default: Markt } = await import("./Markt");
const { default: MarktModell } = await import("./MarktModell");

let wurzel; let behaelter;
const el = (t) => behaelter.querySelector(`[data-testid="${t}"]`);
async function warten() { for (let i = 0; i < 8; i += 1) await act(async () => { await new Promise((r) => setTimeout(r, 0)); }); }
async function starten(pfad) {
  behaelter = document.createElement("div"); document.body.appendChild(behaelter); wurzel = createRoot(behaelter);
  await act(async () => {
    wurzel.render(h(MemoryRouter, { initialEntries: [pfad] }, h(Routes, null,
      h(Route, { path: "/admin/markt", element: h(Markt) }), h(Route, { path: "/admin/markt/:modell", element: h(MarktModell) }))));
  });
  await warten();
}
async function klick(t) { const k = el(t); if (!k) throw new Error(`nicht gefunden: ${t}`); await act(async () => { k.click(); }); await warten(); }
beforeEach(() => { netz.posts.length = 0; netz.gets.length = 0; netz.aktiv = true; });
afterEach(async () => { if (wurzel) await act(async () => { wurzel.unmount(); }); wurzel = null; behaelter?.remove(); });

describe("Admin Marktanalyse", () => {
  it("Modellliste mit Kennzahlen, Status, Taktung und Aktionen", async () => {
    await starten("/admin/markt");
    expect(el("markt-status").textContent).toContain("jedes Segment alle 3 Tag(e)");
    expect(el("markt-status").textContent).toContain("12.50 $ von 450 $");
    expect(el("markt-monitoring").textContent).toContain("6 / 6 / 0");
    expect(el("markt-monitoring").textContent).toContain("437.50 $");
    expect(el("markt-alarme").textContent).toContain("48 h");
    expect(el("markt-chancen-link")).toBeTruthy();
    const z = el("markt-modell-bmw-320d");
    expect(z.textContent).toContain("BMW 320d");
    expect(z.textContent).toContain("18.900 €");
    expect(z.textContent).toContain("20.250 €");
    expect(z.textContent).toContain("-4 %");
    expect(z.textContent).toContain("4/16");
    expect(el("markt-modell-vw-golf-20tdi").textContent).toContain("pausiert");
    expect(el("markt-modelle").textContent).toContain("kein Marktmedian");
    await klick("markt-modell-schalten-vw-golf-20tdi");
    expect(netz.posts[0]).toEqual({ url: "/admin/market/models/vw-golf-20tdi/enabled", body: { enabled: true } });
    await klick("markt-plan");
    expect(netz.posts[1].url).toBe("/admin/market/plan");
    await klick("markt-konfig-oeffnen");
    expect(el("markt-konfig-km").value).toBe("10000-30000");
    expect(el("markt-konfig-ez").value).toBe("2019-2021");
    await klick("markt-konfig-speichern");
    const put = netz.posts.find((p) => p.url === "/admin/market/config");
    expect(put.body.km_buckets).toEqual([{ min_km: 10000, max_km: 30000 }]);
    expect(put.body.ez_buckets).toEqual([{ year_from: 2019, year_to: 2021 }]);
    expect(put.body.budget_usd).toBe(450);
  });

  it("Crawler-Knopf: aus -> Rückfrage mit Kosten -> POST aktiv:true; an -> POST aktiv:false ohne Rückfrage", async () => {
    netz.aktiv = false;
    await starten("/admin/markt");
    expect(el("markt-status").textContent).toContain("aus");
    expect(el("markt-crawler-schalter").textContent).toContain("Crawler einschalten");
    const frage = vi.spyOn(window, "confirm").mockReturnValue(false);
    await klick("markt-crawler-schalter");
    expect(frage.mock.calls[0][0]).toContain("12 $ im Monat");
    expect(netz.posts.some((p) => p.url === "/admin/market/crawler")).toBe(false);
    frage.mockReturnValue(true);
    await klick("markt-crawler-schalter");
    expect(netz.posts.find((p) => p.url === "/admin/market/crawler").body).toEqual({ aktiv: true });
    frage.mockRestore();
    netz.aktiv = true; netz.posts.length = 0;
    await act(async () => { wurzel.unmount(); });
    await starten("/admin/markt");
    expect(el("markt-status").textContent).toContain("läuft automatisch");
    const keineFrage = vi.spyOn(window, "confirm").mockReturnValue(false);
    await klick("markt-crawler-schalter");
    expect(keineFrage).not.toHaveBeenCalled();
    expect(netz.posts.find((p) => p.url === "/admin/market/crawler").body).toEqual({ aktiv: false });
    keineFrage.mockRestore();
  });

  it("Modellseite: Segmentwahl, Kennzahlen, Verlauf, Auswertung, Tabelle, Top-20 und Listing-Historie", async () => {
    await starten("/admin/markt/bmw-320d");
    expect(el("markt-modell-titel").textContent).toBe("BMW 320d");
    expect(el("markt-km-55001").getAttribute("aria-pressed")).toBe("true");
    expect(el("markt-ez-2019").getAttribute("aria-pressed")).toBe("true");
    const kz = el("markt-kennzahlen").textContent;
    for (const t of ["20 Fahrzeuge", "18.900 €", "20.250 €", "20.410 €", "21.700 €", "19.600 € / 21.000 €", "−420 € (-2,1 %)", "−850 € (-4 %)", "14", "9"]) {
      expect(kz).toContain(t);
    }
    expect(el("markt-segment-datenlage").textContent).toContain("Datenlage gut");
    expect(el("markt-segment").textContent).toContain("nur auf die beobachteten 20 günstigsten");
    expect(netz.gets.some((u) => u.endsWith("/history"))).toBe(true);
    expect(el("markt-auswertung").textContent).toContain("−250 € (-1,2 %)");
    expect(el("markt-auswertung").textContent).toContain("1 / 0 / 0");
    expect(el("markt-wochen").textContent).toContain("2026-W40");
    expect(el("markt-tagestabelle").textContent).toContain("2026-10-01");
    expect(el("markt-tagestabelle").textContent).toContain("-1,2 %");
    // Zeitraum wechseln -> neue Verlaufsabfrage mit range
    await klick("markt-bereich-90d");
    expect(netz.gets.filter((u) => u.endsWith("/history")).length).toBeGreaterThanOrEqual(2);
    // EZ wechseln -> anderes Segment
    await klick("markt-ez-2016");
    expect(el("markt-ez-2016").getAttribute("aria-pressed")).toBe("true");
    // Top-20 und Historie
    const zeile = el("markt-listing-449438530");
    expect(zeile.textContent).toContain("BMW 320d Touring");
    expect(zeile.textContent).toContain("Händler");
    expect(zeile.textContent).toContain("GOOD_PRICE");
    expect(zeile.textContent).toContain("−500 €");
    await klick("markt-listing-oeffnen-449438530");
    expect(el("markt-listing-historie")).toBeTruthy();
    expect(el("markt-preisverlauf").textContent).toContain("19.400 €");
    expect(el("markt-preisverlauf").textContent).toContain("18.900 €");
    // Crawl jetzt (Super-Admin)
    await klick("markt-crawl-jetzt");
    expect(netz.posts.some((p) => p.url.endsWith("/crawl-now"))).toBe(true);
  });
});
