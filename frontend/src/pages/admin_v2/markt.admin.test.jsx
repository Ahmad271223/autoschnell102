/**
 * Admin → Marktanalyse (25.09.2026): Modellliste mit Kennzahlen, Status,
 * Klick auf Modell → Segmentwahl (km × EZ) → Kennzahlen, Verlauf aus den
 * Tagesaggregaten, Auswertung, Tagestabelle, Top-N-Liste, Listing-Historie.
 * Alles nur lesend; Super-Admin-Knoepfe rufen die Schreibrouten.
 */
import { act, createElement as h } from "react";
import { createRoot } from "react-dom/client";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

globalThis.IS_REACT_ACT_ENVIRONMENT = true;

const netz = vi.hoisted(() => ({ posts: [], gets: [], aktiv: true, stats: null, crawlFehler: null, ohneBudget: false, topN: null }));
vi.mock("sonner", () => ({ toast: { success: vi.fn(), error: vi.fn(), info: vi.fn(), warning: vi.fn() } }));
vi.mock("recharts", () => {
  const Leer = ({ children }) => h("div", { "data-chart": "1" }, children);
  return { ResponsiveContainer: Leer, ComposedChart: Leer, BarChart: Leer, Line: () => null, Area: () => null, Bar: () => null,
           XAxis: () => null, YAxis: () => null, Tooltip: () => null, CartesianGrid: () => null };
});
const SEG = { id: "bmw-320d:55001-85000:2019-2021", model_id: "bmw-320d", label: "BMW 320d", min_km: 55001, max_km: 85000,
              km_label: "55–85k km", year_from: 2019, year_to: 2021, ez_label: "EZ 2019–2021", enabled: true,
              last_success_at: "2026-10-01T04:00:00+00:00", stats: { sample_size: 20 } };
// Review 26.09. abends P1: letzter Lauf lieferte unsortierte Daten -> Job 'data_invalid' -> Status "ungültig"
const SEG2 = { ...SEG, id: "bmw-320d:55001-85000:2016-2018", year_from: 2016, year_to: 2018, ez_label: "EZ 2016–2018", stats: null, version: 2,
               letzter_job: { status: "data_invalid", error: "Sortierung unsicher" } };
// Befund 26.09. abends: altes, inaktives Segment (frueherer km-Bereich) darf Auswahl und Tabelle nicht aufblaehen;
// P4: es stammt aus Fassung 1, der Auftrag steht auf Fassung 2 -> Hinweis
const SEG_ALT = { ...SEG, id: "bmw-320d:2019-2021:0-50000", min_km: 0, max_km: 50000, km_label: "0–50k km", enabled: false, stats: null, version: 1 };
const STATS = { sample_size: 20, min_price: 18900, median_price: 20250, avg_price: 20410, max_price: 21700, p25_price: 19600,
                p75_price: 21000, trend_7d_eur: -420, trend_7d_pct: -2.1, trend_30d_eur: -850, trend_30d_pct: -4.0,
                new_listings_7d: 14, price_reductions_7d: 9, beobachtete_tage: 31, datenlage: "gut", updated_at: "2026-10-01T05:10:00+00:00",
                // Review 26.09. Nr. 55: Bestandstrend (gleiche Autos); Nr. 43/44: 30-Tage-Basis fehlt -> null
                trend_7d_bestand_eur: -210, trend_7d_bestand_pct: -1.1, anzahl_gemeinsam: 14,
                trend_30d_bestand_eur: null, trend_30d_bestand_pct: null, anzahl_gemeinsam_30d: 0 };
vi.mock("@/lib/api", () => ({
  errMsg: (e, s) => e?.message || s,
  api: {
    get: vi.fn(async (url) => {
      netz.gets.push(url);
      if (url === "/admin/market/models") return { data: { modelle: [
        // Reparaturwelle 6 Nr. 128/140: listings = aktuelle Fassung, listings_historisch = alle Fassungen; median_sample_mittel statt median_top20_mittel
        { id: "bmw-320d", label: "BMW 320d", fuel: "DIESEL", power_kw_min: 120, power_kw_max: 145, enabled: true, model_id: "10",
          segmente_aktiv: 16, segmente_mit_daten: 4, last_success_at: "2026-10-01T04:00:00+00:00", listings: 312, listings_historisch: 450,
          min_price: 18900, median_sample_mittel: 20250, trend_7d_pct: -2.1, trend_30d_pct: -4.0, crawl_status: "ok" },
        { id: "vw-golf-20tdi", label: "VW Golf 2.0 TDI", fuel: "DIESEL", enabled: false, model_id: "14", segmente_aktiv: 0, segmente_mit_daten: 0,
          listings: 0, listings_historisch: 0, min_price: null, median_sample_mittel: null, trend_7d_pct: null, trend_30d_pct: null, crawl_status: "wartet" },
        // P1: heute ein Lauf mit ungueltigen Daten (data_invalid) -> eigener Status, kein "fehler"
        { id: "audi-a4-40tdi", label: "Audi A4 40 TDI", fuel: "DIESEL", enabled: true, model_id: "9", segmente_aktiv: 30, segmente_mit_daten: 12,
          listings: 80, listings_historisch: 80, min_price: 21000, median_top20_mittel: 24000, trend_7d_pct: null, trend_30d_pct: null, crawl_status: "ungueltig" }] } };
      if (url === "/admin/market/status") return { data: { aktiv: netz.aktiv, aktiv_quelle: "env", segmente: 16, modelle: 1, listings: 312, snapshots: 4000, token_vorhanden: true,
        actor: "sourabhbgp~mobile-de-scraper", budget: { _id: "2026-10", budget_usd: 450, used_usd: 12.5, reserved_usd: 0, rows: 4000, runs: 200 },
        // Reparaturwelle 5 Nr. 30/31/38 + Oberflaeche: Restbudget/Resttage, Entfernungskosten, Preise fuer die Kostenformel, "ohne Budget pausiert"
        takt: { intervall_tage: 3, segmente_je_tag: 6, buendel: 10, kosten_je_tag_usd: 0.38, kosten_je_monat_usd: 11.7, automatisch: true,
                restbudget_usd: 437.5, rest_tage: 5, entfernung_je_tag_usd: 0.29, start_usd: 0.005, row_usd: 0.0007, actor: "scrapesmith~mobile-de-scraper",
                puffer_faktor: 0.3, puffer_max: 10, ohne_budget: netz.ohneBudget, status: netz.ohneBudget ? "ohne Budget pausiert" : "ok" },
        crawls_je_tag_standard: 2,
        monitoring: { tag: "2026-10-01", geplant: 6, erfolgreich: 6, fehlgeschlagen: 0, wartend: 0, laufend: 0, rows_heute: 120, rows_monat: 4000,
                      kosten_heute_usd: 0.11, kosten_monat_usd: 12.5, budget_uebrig_usd: 437.5, budget_anteil_pct: 2.8, mittlere_laufzeit_s: 9.4,
                      letzter_erfolg: { finished_at: "2026-10-01T05:10:00Z", segment_id: "bmw-320d:2019:50001-85000" },
                      alarme: [{ typ: "segment_veraltet", text: "3 Segment(e) seit über 48 h nicht erfolgreich aktualisiert", stufe: "warn" }] },
        jobs: { tag: "2026-10-01", completed: 6, running: 0, queued: 0, failed: 0, data_invalid: 1, naechster: null }, km_buckets: [{ min_km: 10000, max_km: 30000 }],
        ez_buckets: [{ year_from: 2019, year_to: 2021 }], einstellungen: { rows_je_segment: 20 } } };
      if (url === "/admin/market/models/bmw-320d") return { data: { id: "bmw-320d", label: "BMW 320d", fuel: "DIESEL", make_id: "3500", model_id: "10", version: 2, segmente: [{ ...SEG, version: 2 }, SEG2, SEG_ALT] } };
      if (url.includes("/listings/449438530/history")) return { data: { listing: { title: "BMW 320d Touring", active_state: "seen", mileage_km: 78000,
        first_registration: "03/2020", postal_code: "30159", city: "Hannover", price_history: [{ at: "2026-09-13T04:00:00Z", price: 19400 }, { at: "2026-10-01T04:00:00Z", price: 18900 }] },
        snapshots: [{ date: "2026-09-13", segment_id: SEG.id, price: 19400, rank_in_sample: 5 }], hinweis_zustand: "", gekuerzt: true } };   // Nr. 61
      // Reparaturwelle 6 Nr. 123: das v1-Segment ist eine fruehere Fassung -> historisch mit eigener Definition (Diesel, 10 Zeilen)
      if (url.endsWith(`/segments/${SEG_ALT.id}/summary`)) return { data: { segment: SEG_ALT, modell: { version: 2, fuel: "PETROL" }, stats: null, letzter_job: null, historisch: true, fassung_version: 1,
        fassung: { fuel: "DIESEL", gearbox: "AUTOMATIC_GEAR", body: null, power_kw_min: 120, power_kw_max: 145, country: "DE", zip: null, radius_km: null, seller_type: null, rows: 10 },
        qualitaet: { daten_seit: "2026-08-01", tage_beobachtet: 3, tage_mit_treffern: 3, abdeckung_pct: 50, erfolgreiche_crawls: 3, erwartete_crawls: 6, sample_size: 0, datenlage: "keine", crawls_per_day: 2 } } };
      if (url.endsWith("/summary")) return { data: { segment: SEG, stats: netz.stats || STATS, letzter_job: { status: "completed" }, historisch: false,
        qualitaet: { daten_seit: "2026-09-01", tage_beobachtet: 31, tage_mit_treffern: 29, abdeckung_pct: 96.9, erfolgreiche_crawls: 60, erwartete_crawls: 62, sample_size: 20, datenlage: "gut", crawls_per_day: 2,
                     laeufe_nur_monoton: 0, top_n_bewiesen: !netz.topN, top_n_hinweis: netz.topN } } };
      // Nr. 58: ein Tag mit 0 Treffern kommt mit (Luecke statt Sprung)
      if (url.endsWith("/history")) return { data: { reihe: [
        { date: "2026-09-29", sample_size: 20, min: 19100, median: 20500, avg: 20600, max: 21800, p25: 19700, p75: 21100, change_eur: null, change_pct: null },
        { date: "2026-09-30", sample_size: 0, min: null, median: null, avg: null, max: null, p25: null, p75: null, change_eur: null, change_pct: null, kein_angebot: true },
        { date: "2026-10-01", sample_size: 20, min: 18900, median: 20250, avg: 20410, max: 21700, p25: 19600, p75: 21000, change_eur: -250, change_pct: -1.22,
          laeufe: [{ at: "2026-10-01T04:00:00Z", tag: "2026-10-01", median: 20300 }, { at: "2026-10-01T16:00:00Z", tag: "2026-10-01#2", median: 20250 }] }],
        wochen: [{ woche: "2026-W40", median: 20375, tage: 2 }],
        auswertung: { veraenderung_eur: -250, veraenderung_pct: -1.22, groesster_rueckgang: { date: "2026-10-01", change_eur: -250 }, groesster_anstieg: null,
                      tage_fallend: 1, tage_steigend: 0, tage_unveraendert: 0, hoechster_median: 20500, niedrigster_median: 20250, tage: 3, tage_ohne_angebot: 1 } } };
      if (url.endsWith("/listings")) return { data: { date: "2026-10-01", lauf_tag: "2026-10-01#2", top_n_bewiesen: true, listings: [
        { listing_id: "449438530", title: "BMW 320d Touring", price_today: 18900, current_price: 18900, mileage_km: 78000, first_registration: "03/2020",
          power_kw: 140, fuel: "Diesel", gearbox: "Automatik", postal_code: "30159", city: "Hannover", seller_type: "DEALER", price_rating_today: "GOOD_PRICE",
          mobile_created_at: "2026-09-05T11:46:02.000Z", first_seen_at: "2026-09-13T04:00:00+00:00", change_since_first_eur: -500, rank_today: 1, rank_yesterday: 3,
          price_change_eur: -200, first_price: 19400, price_reductions: 2,
          // Reparaturwelle 6 Nr. 139: in DIESEM Segment erst heute gesehen (global seit 13.09.)
          segment_first_seen_at: "2026-10-01T04:00:00+00:00", segment_first_rank: 1, in_letztem_lauf: true, neu_im_segment: true }] } };
      return { data: {} };
    }),
    post: vi.fn(async (url, body) => {
      netz.posts.push({ url, body });
      if (netz.crawlFehler && url.endsWith("/crawl-now")) { const e = new Error(netz.crawlFehler); e.response = { status: netz.crawlStatus || 400, data: { detail: netz.crawlFehler } }; throw e; }
      return { data: { ok: true, neu: 6, tag: "2026-10-01", modelle: { neu: 0 }, segmente: { segmente: 16 }, erledigt: 1 } };
    }),
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
beforeEach(() => { netz.posts.length = 0; netz.gets.length = 0; netz.aktiv = true; netz.stats = null; netz.crawlFehler = null; netz.crawlStatus = 0; netz.ohneBudget = false; netz.topN = null; });
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
    // Reparaturwelle 6 Nr. 128/140: Listings der aktuellen Fassung, Historie getrennt; alter Feldname als Rueckfall
    expect(el("markt-historisch-bmw-320d").textContent).toBe(" (450 historisch)");
    expect(el("markt-historisch-audi-a4-40tdi")).toBeNull();
    expect(el("markt-modell-audi-a4-40tdi").textContent).toContain("24.000 €");
    expect(el("markt-modell-vw-golf-20tdi").textContent).toContain("pausiert");
    // P1: ungueltiger Lauf heute -> Status "ungültig" (nicht "fehler"), Kachel zaehlt ihn getrennt
    expect(el("markt-modell-audi-a4-40tdi").textContent).toContain("ungültig");
    expect(el("markt-modell-audi-a4-40tdi").textContent).not.toContain("fehler");
    expect(el("markt-status").textContent).toContain("1 ungültig (Sortierung unsicher, nichts gespeichert)");
    expect(el("markt-modelle").textContent).toContain("kein Marktmedian");
    await klick("markt-modell-schalten-vw-golf-20tdi");
    expect(netz.posts[0]).toEqual({ url: "/admin/market/models/vw-golf-20tdi/enabled", body: { enabled: true } });
    await klick("markt-plan");
    expect(netz.posts[1].url).toBe("/admin/market/plan");
    // Reparaturwelle 5 Nr. 31/38: Taktung sagt "an Crawl-Tagen 2x", Restbudget fuer Resttage, Entfernungskosten
    expect(el("markt-taktung").textContent).toContain("an Crawl-Tagen 2×; jedes Segment alle 3 Tag(e)");
    expect(el("markt-taktung").textContent).toContain("Restbudget 437.50 $ für 5 Tage");
    expect(el("markt-taktung").textContent).toContain("Entfernungsprüfung bis 0.29 $/Tag");
    await klick("markt-konfig-oeffnen");
    expect(el("markt-konfig-km").value).toBe("10000-30000");
    expect(el("markt-konfig-ez").value).toBe("2019-2021");
    // Oberflaeche: Kostenformel aus status.takt (nicht mehr hart 0,004 + 0,003), Hinweis "nur Vorbelegung", Knopf "anwenden"
    expect(el("markt-konfig-formel").textContent).toContain("0,005 $ + Zeilen × 0,0007 $");
    expect(el("markt-konfig-formel").textContent).not.toContain("0,004");
    expect(el("markt-konfig-formel").textContent).toContain("+30 %, höchstens +10");
    expect(el("markt-konfig-hinweis").textContent).toContain("nur die Vorbelegung für NEUE Aufträge");
    await klick("markt-konfig-speichern");
    const put = netz.posts.find((p) => p.url === "/admin/market/config");
    expect(put.body.km_buckets).toEqual([{ min_km: 10000, max_km: 30000 }]);
    expect(put.body.ez_buckets).toEqual([{ year_from: 2019, year_to: 2021 }]);
    expect(put.body.budget_usd).toBe(450);
    const frage = vi.spyOn(window, "confirm").mockReturnValue(false);
    await klick("markt-konfig-anwenden");
    expect(netz.posts.some((p) => p.url === "/admin/market/config/anwenden")).toBe(false);
    frage.mockReturnValue(true);
    await klick("markt-konfig-anwenden");
    expect(netz.posts.some((p) => p.url === "/admin/market/config/anwenden")).toBe(true);
    frage.mockRestore();
  });

  it("Taktung: Budget 0 -> 'ohne Budget pausiert' (Reparaturwelle 5 Nr. 30)", async () => {
    netz.ohneBudget = true;
    await starten("/admin/markt");
    expect(el("markt-taktung").textContent).toContain("ohne Budget pausiert");
    expect(el("markt-taktung").textContent).toContain("keine Planung, keine Jobs");
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

  it("Modellseite: Segmentwahl, Kennzahlen, Verlauf, Auswertung, Tabelle, Top-N und Listing-Historie", async () => {
    await starten("/admin/markt/bmw-320d");
    expect(el("markt-modell-titel").textContent).toBe("BMW 320d");
    expect(el("markt-km-55001").getAttribute("aria-pressed")).toBe("true");
    // inaktives Segment: kein km-Chip, keine Tabellenzeile — erst nach dem Schalter
    expect(el("markt-km-0")).toBeNull();
    expect(el(`markt-segmentzeile-${SEG_ALT.id}`)).toBeNull();
    expect(el("markt-inaktive-schalter").textContent).toContain("1 inaktive");
    await klick("markt-inaktive-schalter");
    expect(el(`markt-segmentzeile-${SEG_ALT.id}`)).toBeTruthy();
    // P4: das inaktive Segment stammt aus Fassung 1, der Auftrag steht auf v2 -> Hinweis; aktuelle ohne Hinweis
    expect(el(`markt-fassung-${SEG_ALT.id}`).textContent).toBe("Fassung v1 (aktuell v2)");
    expect(el(`markt-fassung-${SEG.id}`)).toBeNull();
    // P1: Segment mit letztem Job 'data_invalid' -> Badge "ungültig" statt "wartet"
    expect(el(`markt-segmentzeile-${SEG2.id}`).textContent).toContain("ungültig");
    await klick("markt-inaktive-schalter");
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
    // Review 26.09. Nr. 17: zwei Abrufe am selben Tag -> kleiner Hinweis, nur ein Tageseintrag
    expect(el("markt-laeufe-2026-10-01").textContent).toBe("2 Läufe");
    expect(el("markt-laeufe-2026-09-30")).toBeNull();
    // Welle 5 Nr. 58: Tag ohne Angebot als Marker in der Tabelle und Hinweis am Diagramm
    expect(el("markt-kein-angebot-2026-09-30").textContent).toBe("kein Angebot");
    expect(el("markt-kein-angebot-2026-10-01")).toBeNull();
    expect(el("markt-verlauf").textContent).toContain("Lücken = Tage ohne Angebot (1)");
    expect(el("markt-listings").textContent).toContain("letzter Lauf 2");
    // Nr. 137/138: keine feste "20" mehr in Ueberschriften (die Kennzahlen sagen "Top-N" mit N = sample_size)
    expect(el("markt-tagestabelle").textContent).not.toMatch(/Top-20/);
    expect(el("markt-kennzahlen").textContent).toContain("Median Top-20");
    // Nr. 139: "neu in diesem Segment" mit globalem Erstdatum daneben
    expect(el("markt-neu-segment-449438530").textContent).toBe("neu in diesem Segment");
    expect(el("markt-listing-449438530").textContent).toContain("(global 13.09.)");
    expect(el("markt-fassung-historisch")).toBeNull();
    // Zeitraum wechseln -> neue Verlaufsabfrage mit range
    await klick("markt-bereich-90d");
    expect(netz.gets.filter((u) => u.endsWith("/history")).length).toBeGreaterThanOrEqual(2);
    // EZ wechseln -> anderes Segment
    await klick("markt-ez-2016");
    expect(el("markt-ez-2016").getAttribute("aria-pressed")).toBe("true");
    // Top-N und Historie
    const zeile = el("markt-listing-449438530");
    expect(zeile.textContent).toContain("BMW 320d Touring");
    expect(zeile.textContent).toContain("Händler");
    expect(zeile.textContent).toContain("GOOD_PRICE");
    expect(zeile.textContent).toContain("−500 €");
    await klick("markt-listing-oeffnen-449438530");
    expect(el("markt-listing-historie")).toBeTruthy();
    expect(el("markt-preisverlauf").textContent).toContain("19.400 €");
    expect(el("markt-preisverlauf").textContent).toContain("18.900 €");
    expect(el("markt-historie-gekuerzt").textContent).toContain("gekürzt");          // Welle 5 Nr. 61
    // Crawl jetzt (Super-Admin)
    await klick("markt-crawl-jetzt");
    expect(netz.posts.some((p) => p.url.endsWith("/crawl-now"))).toBe(true);
  });

  it("Bestandstrend als zweite Zeile, fehlende Trend-Basis als Strich, Abdeckung (Review 26.09. Nr. 43/44/45/55)", async () => {
    const { toast } = await import("sonner");
    await starten("/admin/markt/bmw-320d");
    expect(el("markt-trend-7d").textContent).toContain("−420 €");
    expect(el("markt-trend-7d").textContent).toContain("gleiche Autos: −210 € (-1,1 %) · 14 Autos");
    expect(el("markt-trend-30d").textContent).toContain("gleiche Autos: —");
    expect(el("markt-qualitaet").textContent).toContain("31 Tage beobachtet (29 mit Treffern)");
    expect(el("markt-qualitaet").textContent).toContain("Abdeckung 96,9 % der erwarteten Läufe");
    expect(el("markt-qualitaet").textContent).toContain("60 von 62 erwarteten Crawls gültig");
    expect(el("markt-top-n-hinweis")).toBeNull();
    // Nr. 52: inaktives Segment -> 400 mit Klartext -> toast.error mit dem Servertext
    netz.crawlFehler = "Segment inaktiv — der Suchauftrag ist pausiert";
    await klick("markt-crawl-jetzt");
    expect(toast.error).toHaveBeenCalledWith(expect.stringContaining("Segment inaktiv"));
    // Reparaturwelle 6 Nr. 77: Doppelklick -> 409 mit Klartext, kein zweiter Job
    netz.crawlFehler = "Für dieses Segment wartet schon ein Job (manuell, 1a2b3c4d) — kein zweiter Lauf, bitte abwarten"; netz.crawlStatus = 409;
    await klick("markt-crawl-jetzt");
    expect(toast.error).toHaveBeenLastCalledWith(expect.stringContaining("wartet schon ein Job"));
    await act(async () => { wurzel.unmount(); }); wurzel = null; behaelter?.remove();
    // Nr. 123/124: historische Fassung zeigt ihre eigene Definition
    await starten(`/admin/markt/bmw-320d?segment=${encodeURIComponent(SEG_ALT.id)}`);
    expect(el("markt-fassung-historisch").textContent).toContain("Frühere Fassung v1 (Auftrag heute v2)");
    expect(el("markt-fassung-historisch").textContent).toContain("Kraftstoff DIESEL · Getriebe AUTOMATIC_GEAR · 120–145 kW · 10 Zeilen");
    await act(async () => { wurzel.unmount(); }); wurzel = null; behaelter?.remove();
    // Nr. 43/44: kein Datensatz in der Toleranz -> trend null -> "—" statt Zahl
    netz.stats = { ...STATS, trend_7d_eur: null, trend_7d_pct: null, trend_7d_basis_date: null, trend_7d_bestand_eur: null, anzahl_gemeinsam: 0 };
    // Welle 5 Nr. 1: Top-N nicht bewiesen -> Hinweis in der Datenqualitaet
    netz.topN = "Top-N nicht bewiesen (Scraper ohne Positionsnummer — nur monoton sortiert)";
    await starten("/admin/markt/bmw-320d");
    const t7 = el("markt-trend-7d").textContent;
    expect(t7).toContain("—");
    expect(t7).not.toMatch(/€.*€/);
    expect(el("markt-top-n-hinweis").textContent).toContain("Top-N nicht bewiesen");
  });
});
