/**
 * Admin → Marktanalyse → Private Deals (Ahmad 26.09.2026 abends): Liste der 3 günstigsten
 * Privatangebote je Segment aus dem vorhandenen Sample. Zusammenfassung, Standard-Sortierung
 * (größte negative Abweichung zum Segment-Median), Filter als GET-Parameter, Links nur auf
 * mobile.de, Umschalter aktuell/historisch, Markierungen (heute neu, Preis reduziert, stale).
 */
import { act, createElement as h } from "react";
import { createRoot } from "react-dom/client";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

globalThis.IS_REACT_ACT_ENVIRONMENT = true;

const netz = vi.hoisted(() => ({ gets: [], fehler: null }));
vi.mock("sonner", () => ({ toast: { success: vi.fn(), error: vi.fn() } }));
const DEALS = [
  { segment_id: "bmw-320d:2020:55001-80000", model_id: "bmw-320d", segment_label: "BMW 320d", km_label: "55–80k km", ez_label: "EZ 2020", listing_id: "p1",
    title: "BMW 320d Touring", current_price: 17900, mileage_km: 71000, first_registration: "03/2020", postal_code: "80331", city: "München",
    segment_median: 21500, difference_to_segment_median_eur: -3600, difference_to_segment_median_pct: -16.74, current_rank_private: 1, best_rank_private: 1,
    rank_in_sample: 1, currently_top3: true, url: "https://suchen.mobile.de/auto-inserat/bmw-320d/p1.html", mobile_created_at: "2026-09-01T10:00:00Z",
    first_entered_top3_at: "2026-10-01T04:00:00Z", heute_neu: true, preis_reduziert: false, stale: false, active_state: "seen", power_kw: 140, fuel: "Diesel", gearbox: "Automatik" },
  { segment_id: "bmw-320d:2020:55001-80000", model_id: "bmw-320d", segment_label: "BMW 320d", km_label: "55–80k km", ez_label: "EZ 2020", listing_id: "p2",
    title: "BMW 320d Limousine", current_price: 19900, mileage_km: 78000, first_registration: "05/2020", postal_code: "30159", city: "Hannover",
    segment_median: 21500, difference_to_segment_median_eur: -1600, difference_to_segment_median_pct: -7.44, current_rank_private: 2, best_rank_private: 1,
    rank_in_sample: 4, currently_top3: true, url: "http://evil.example.com/mobile.de/p2", mobile_created_at: "2026-09-20T10:00:00Z",
    first_entered_top3_at: "2026-09-25T04:00:00Z", heute_neu: false, preis_reduziert: true, price_change_since_first_eur: -500, stale: true,
    stand_at: "2026-09-30T05:00:00Z", active_state: "seen" },
];
const HISTORISCH = { ...DEALS[1], listing_id: "p3", title: "BMW 320d alt", currently_top3: false, current_rank_private: null, best_rank_private: 3,
                     left_top3_at: "2026-09-29T04:00:00Z", url: "https://www.mobile.de.example.com/p3", difference_to_segment_median_pct: 2.1, difference_to_segment_median_eur: 450 };
vi.mock("@/lib/api", () => ({
  errMsg: (e, s) => e?.message || s,
  api: {
    get: vi.fn(async (url, opts) => {
      netz.gets.push({ url, params: opts?.params });
      if (url === "/admin/market/models") return { data: { modelle: [{ id: "bmw-320d", label: "BMW 320d" }] } };
      if (url === "/admin/market/private-deals") {
        if (netz.fehler) throw new Error(netz.fehler);
        const p = opts?.params || {};
        const deals = p.nur_aktuell === false ? [...DEALS, HISTORISCH] : DEALS;
        return { data: { zusammenfassung: { segmente_aktiv: 16, segmente_mit_deals: 5, aktuelle_top3: 11, heute_neu: 2, heute_reduziert: 1 },
                         deals, anzahl: deals.length, gekuerzt: false, sort: p.sort, nur_aktuell: p.nur_aktuell !== false,
                         hinweis: "Privatangebote nur aus dem ohnehin abgerufenen Sample" } };
      }
      return { data: {} };
    }),
  },
}));
vi.mock("@/context/AuthContext", () => ({ useAuth: () => ({ user: { id: "sa", is_super_admin: true, role: "admin" } }) }));

const { default: MarktPrivateDeals, filterParams } = await import("./MarktPrivateDeals");
const { mobileLink } = await import("@/lib/markt");

let wurzel; let behaelter;
const el = (t) => behaelter.querySelector(`[data-testid="${t}"]`);
async function warten() { for (let i = 0; i < 8; i += 1) await act(async () => { await new Promise((r) => setTimeout(r, 0)); }); }
async function starten() {
  behaelter = document.createElement("div"); document.body.appendChild(behaelter); wurzel = createRoot(behaelter);
  await act(async () => {
    wurzel.render(h(MemoryRouter, { initialEntries: ["/admin/markt/private-deals"] }, h(Routes, null,
      h(Route, { path: "/admin/markt/private-deals", element: h(MarktPrivateDeals) }))));
  });
  await warten();
}
async function klick(t) { const k = el(t); if (!k) throw new Error(`nicht gefunden: ${t}`); await act(async () => { k.click(); }); await warten(); }
async function tippen(t, wert) {
  const e = el(t); if (!e) throw new Error(`nicht gefunden: ${t}`);
  const setter = Object.getOwnPropertyDescriptor(e.tagName === "SELECT" ? window.HTMLSelectElement.prototype : window.HTMLInputElement.prototype, "value").set;
  await act(async () => { setter.call(e, wert); e.dispatchEvent(new Event(e.tagName === "SELECT" ? "change" : "input", { bubbles: true })); });
  await warten();
}
const letzteParams = () => netz.gets.filter((g) => g.url === "/admin/market/private-deals").at(-1).params;
beforeEach(() => { netz.gets.length = 0; netz.fehler = null; });
afterEach(async () => { if (wurzel) await act(async () => { wurzel.unmount(); }); wurzel = null; behaelter?.remove(); });

describe("Admin Private Deals", () => {
  it("Liste mit Zusammenfassung, Rang, Preis, Abstand, Markierungen und Standard-Sortierung", async () => {
    await starten();
    expect(letzteParams()).toMatchObject({ sort: "abstand_pct", nur_aktuell: true, limit: 300 });
    expect(letzteParams()).not.toHaveProperty("ez");
    const z = el("private-deals-zusammenfassung").textContent;
    for (const t of ["16", "5", "11", "2", "1", "Privatangebote nur aus dem ohnehin abgerufenen Sample"]) expect(z).toContain(t);
    expect(el("private-deals-liste").textContent).toContain("2 Privatangebote");
    const z1 = el("pd-deal-p1");
    expect(z1.textContent).toContain("#1 PRIVAT");
    expect(z1.textContent).toContain("17.900 €");
    expect(z1.textContent).toContain("BMW 320d");
    expect(z1.textContent).toContain("EZ 2020 · 55–80k km");
    expect(z1.textContent).toContain("80331 München");
    expect(z1.textContent).toContain("21.500 €");
    expect(el("pd-abstand-p1").textContent).toBe("−3.600 € (-16,7 %)");
    expect(z1.textContent).toContain("heute neu");
    expect(z1.textContent).toContain("Platz 1 im Sample");
    expect(z1.textContent).toContain("01.09.");          // bei mobile.de seit
    expect(z1.textContent).toContain("01.10.");          // AutoSchnell erstmals gesehen
    // Reihenfolge = Server-Sortierung (größte negative Abweichung zuerst)
    const zeilen = [...behaelter.querySelectorAll("[data-testid^='pd-deal-']")].map((e) => e.getAttribute("data-testid"));
    expect(zeilen).toEqual(["pd-deal-p1", "pd-deal-p2"]);
    const z2 = el("pd-deal-p2");
    expect(z2.textContent).toContain("#2 PRIVAT");
    expect(z2.textContent).toContain("Preis reduziert");
    expect(z2.textContent).toContain("−500 € seit Top-3");
    expect(el("pd-stale-p2").textContent).toContain("stale (letzter gültiger Lauf");
    expect(el("pd-stale-p1")).toBeNull();
    // Segmentanalyse-Link zeigt auf das Segment des Deals
    expect(el("pd-segment-p1").getAttribute("href")).toBe(`/admin/markt/bmw-320d?segment=${encodeURIComponent("bmw-320d:2020:55001-80000")}`);
  });

  it("[Inserat öffnen] nur für https-Links auf mobile.de — sonst kein Link", async () => {
    await starten();
    const a = el("pd-inserat-p1");
    expect(a.getAttribute("href")).toBe("https://suchen.mobile.de/auto-inserat/bmw-320d/p1.html");
    expect(a.getAttribute("target")).toBe("_blank");
    expect(a.getAttribute("rel")).toContain("noopener");
    expect(el("pd-inserat-p2")).toBeNull();
    expect(el("pd-kein-link-p2").textContent).toBe("kein Link");
    expect(mobileLink("https://www.mobile.de.example.com/p3")).toBeNull();
    expect(mobileLink("http://suchen.mobile.de/x")).toBeNull();
    expect(mobileLink("https://mobile.de/x")).toBe("https://mobile.de/x");
    expect(mobileLink("javascript:alert(1)")).toBeNull();
    expect(mobileLink(null)).toBeNull();
  });

  it("Filter landen als GET-Parameter (Zahlen als Zahlen, leere Felder fehlen), Sortierung und Häkchen laden sofort", async () => {
    await starten();
    await tippen("pd-filter-ez", "2020");
    await tippen("pd-filter-plz", "80");
    await tippen("pd-filter-abstand", "-5");
    await tippen("pd-filter-km-min", "50000");
    await tippen("pd-filter-preis-bis", "20000");
    await tippen("pd-filter-make", "BMW");
    const vorher = netz.gets.filter((g) => g.url === "/admin/market/private-deals").length;
    await klick("pd-filter-anwenden");
    expect(netz.gets.filter((g) => g.url === "/admin/market/private-deals").length).toBe(vorher + 1);
    expect(letzteParams()).toEqual({ sort: "abstand_pct", limit: 300, nur_aktuell: true, ez: 2020, plz: "80", abstand_pct_max: -5, km_min: 50000, preis_bis: 20000, make: "BMW" });
    await tippen("pd-filter-sort", "preis");
    expect(letzteParams().sort).toBe("preis");
    await tippen("pd-filter-modell", "bmw-320d");
    expect(letzteParams().model_id).toBe("bmw-320d");
    await klick("pd-filter-heute-neu");
    expect(letzteParams().heute_neu).toBe(true);
    await klick("pd-filter-reduziert");
    expect(letzteParams().preis_reduziert).toBe(true);
    // reine Hilfsfunktion: leere Strings fehlen, Zahlenfelder werden Zahlen
    expect(filterParams({ ez: " ", km_max: "90000", plz: " 30 " })).toEqual({ sort: "abstand_pct", limit: 300, nur_aktuell: true, km_max: 90000, plz: "30" });
  });

  it("Umschalter aktuell/historisch: historisch lädt nur_aktuell=false und zeigt herausgefallene mit 'war #3'", async () => {
    await starten();
    expect(el("pd-umschalter-aktuell").getAttribute("aria-pressed")).toBe("true");
    expect(el("pd-deal-p3")).toBeNull();
    await klick("pd-umschalter-historisch");
    expect(letzteParams().nur_aktuell).toBe(false);
    expect(el("pd-umschalter-historisch").getAttribute("aria-pressed")).toBe("true");
    const z3 = el("pd-deal-p3");
    expect(z3.textContent).toContain("war #3");
    expect(z3.textContent).toContain("herausgefallen 29.09.");
    expect(el("pd-inserat-p3")).toBeNull();          // mobile.de.example.com ist NICHT mobile.de
    await klick("pd-umschalter-aktuell");
    expect(letzteParams().nur_aktuell).toBe(true);
    expect(el("pd-deal-p3")).toBeNull();
  });

  it("Ladefehler wird angezeigt, die Seite bleibt bedienbar", async () => {
    netz.fehler = "Nur der Super-Admin (Betreiber) darf das";
    await starten();
    expect(el("private-deals-fehler").textContent).toContain("Super-Admin");
    expect(el("private-deals-filter")).toBeTruthy();
  });
});
