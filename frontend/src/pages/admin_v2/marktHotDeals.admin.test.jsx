/**
 * Admin → Marktanalyse → Hot Deals (Master-Auftrag Phase D, 27.09.2026): Zusammenfassung (Abschnitt 26),
 * Hinweis „Hot Deal ≠ guter Kauf“ (Abschnitt 58), Filter als GET-Parameter, Sortierungen, privat/Händler,
 * Umschalter aktuell/alle, Links nur auf mobile.de, Ereignis-Verlauf je Deal, Knopf „Jetzt auswerten“.
 */
import { act, createElement as h } from "react";
import { createRoot } from "react-dom/client";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

globalThis.IS_REACT_ACT_ENVIRONMENT = true;

const netz = vi.hoisted(() => ({ gets: [], posts: [], fehler: null }));
vi.mock("sonner", () => ({ toast: { success: vi.fn(), error: vi.fn() } }));
const DEALS = [
  { segment_id: "bmw-320d:2020:55001-80000", model_id: "bmw-320d", segment_label: "BMW 320d", km_label: "55–80k km", ez_label: "EZ 2020", listing_id: "h1",
    title: "BMW 320d Touring", current_price: 17500, reference_price: 20000, diff_eur: 2500, diff_pct: 12.5, klasse: "EXTREME", status: "ACTIVE",
    privat: true, seller_type: "PRIVATE", mileage_km: 71000, first_registration: "03/2020", postal_code: "80331", city: "München", rank: 1,
    url: "https://suchen.mobile.de/auto-inserat/bmw-320d/h1.html", liquiditaet: "HIGH", basis_tage: 21, hot_seit_at: "2026-10-01T05:00:00Z",
    deal_first_detected_at: "2026-09-28T05:00:00Z", heute_neu: true, stand_alter_tage: 0, stand_tag: "2026-10-01", price_change_since_detection_eur: -500 },
  { segment_id: "bmw-320d:2020:80001-110000", model_id: "bmw-320d", segment_label: "BMW 320d", km_label: "80–110k km", ez_label: "EZ 2020", listing_id: "h2",
    title: "BMW 320d Limousine", current_price: 16600, reference_price: 18000, diff_eur: 1400, diff_pct: 7.78, klasse: "DEAL", status: "ACTIVE",
    privat: false, seller_type: "DEALER", mileage_km: 95000, first_registration: "06/2020", postal_code: "30159", city: "Hannover", rank: 2,
    url: "http://evil.example.com/mobile.de/h2", liquiditaet: "LOW", basis_tage: 9, hot_seit_at: "2026-09-25T05:00:00Z",
    deal_first_detected_at: "2026-09-25T05:00:00Z", heute_neu: false, stand_alter_tage: 4, stand_tag: "2026-09-27" },
];
const VERLASSEN = { ...DEALS[1], listing_id: "h3", title: "BMW 320d alt", status: "LEFT", left_grund: "nicht_mehr_im_sample", klasse: "STRONG",
                    url: "https://www.mobile.de.example.com/h3", heute_neu: false, stand_alter_tage: 1 };
vi.mock("@/lib/api", () => ({
  errMsg: (e, s) => e?.message || s,
  api: {
    get: vi.fn(async (url, opts) => {
      netz.gets.push({ url, params: opts?.params });
      if (url === "/admin/market/models") return { data: { modelle: [{ id: "bmw-320d", label: "BMW 320d" }] } };
      if (url === "/admin/market/hot-deals") {
        if (netz.fehler) throw new Error(netz.fehler);
        const p = opts?.params || {};
        const deals = p.status === "alle" ? [...DEALS, VERLASSEN] : DEALS;
        return { data: { zusammenfassung: { tag: "2026-10-01", modelle_geprueft: 40, modelle_gueltig: 31, neue_deals_heute: 4, aktiv: 9, deal: 5, strong: 3,
                                            extreme: 1, davon_privat: 2, neue_privat_heute: 1 },
                         deals, anzahl: deals.length, gekuerzt: false, sort: p.sort, status: p.status,
                         hinweis: "Hot Deal heißt nur: auffällig günstiges Inserat gegenüber unserer beobachteten Vergleichsgruppe — nicht automatisch unfallfrei, technisch gut, seriös oder ein guter Kauf.",
                         schwellen: { referenz_fenster_tage: 30, min_basis_tage: 7, min_basis_inserate: 5,
                                      klassen: [{ klasse: "EXTREME", ab_pct: 12 }, { klasse: "STRONG", ab_pct: 8 }, { klasse: "DEAL", ab_pct: 5 }],
                                      mindest_eur_staffel: [{ ab_referenz_eur: 15000, mindest_eur: 750 }, { ab_referenz_eur: 10000, mindest_eur: 600 }, { ab_referenz_eur: 0, mindest_eur: 400 }] } } };
      }
      if (url === "/admin/market/hot-deals/ereignisse") {
        return { data: { ereignisse: [
          { typ: "NEW_HOT_DEAL", lauf_key: "a", lauf_at: "2026-09-28T05:00:00Z", klasse: "EXTREME", price: 18000, diff_pct: 10, reference_price: 20000 },
          { typ: "PRICE_DROP_HOT_DEAL", lauf_key: "b", lauf_at: "2026-10-01T05:00:00Z", klasse: "EXTREME", price: 17500, diff_pct: 12.5, reference_price: 20000 }] } };
      }
      return { data: {} };
    }),
    post: vi.fn(async (url) => { netz.posts.push(url); return { data: { ok: true, hot_deals: { ausgewertet: 3, ereignisse: 2 } } }; }),
  },
}));
vi.mock("@/context/AuthContext", () => ({ useAuth: () => ({ user: { id: "sa", is_super_admin: true, role: "admin" } }) }));

const { default: MarktHotDeals, hotDealParams } = await import("./MarktHotDeals");

let wurzel; let behaelter;
const el = (t) => behaelter.querySelector(`[data-testid="${t}"]`);
async function warten() { for (let i = 0; i < 8; i += 1) await act(async () => { await new Promise((r) => setTimeout(r, 0)); }); }
async function starten() {
  behaelter = document.createElement("div"); document.body.appendChild(behaelter); wurzel = createRoot(behaelter);
  await act(async () => {
    wurzel.render(h(MemoryRouter, { initialEntries: ["/admin/markt/hot-deals"] }, h(Routes, null,
      h(Route, { path: "/admin/markt/hot-deals", element: h(MarktHotDeals) }))));
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
const letzteParams = () => netz.gets.filter((g) => g.url === "/admin/market/hot-deals").at(-1).params;
beforeEach(() => { netz.gets.length = 0; netz.posts.length = 0; netz.fehler = null; });
afterEach(async () => { if (wurzel) await act(async () => { wurzel.unmount(); }); wurzel = null; behaelter?.remove(); });

describe("Admin Hot Deals", () => {
  it("Zusammenfassung, Hinweis, Schwellen, Liste mit Klasse, Vorteil, privat/Händler und Standard-Sortierung", async () => {
    await starten();
    expect(letzteParams()).toEqual({ sort: "vorteil_pct", status: "aktuell", limit: 300 });
    const z = el("hot-deals-zusammenfassung").textContent;
    for (const t of ["Heute geprüfte Modelle (01.10.)", "40", "31", "4", "9", "3", "1", "2 (1 neu)"]) expect(z).toContain(t);
    expect(el("hot-deals-hinweis").textContent).toContain("nicht automatisch unfallfrei");
    const s = el("hot-deals-schwellen").textContent;
    expect(s).toContain("letzten 30 Tage im selben Segment");
    expect(s).toContain("mindestens 7 gültige Tage und 5 verschiedene Inserate");
    expect(s).toContain("Deal ab 5 %, Stark ab 8 %, Extrem ab 12 %");
    expect(s).toContain("Mindestvorteil 400 € / 600 € / 750 €");
    expect(el("hot-deals-liste").textContent).toContain("2 Hot Deals");
    const z1 = el("hd-deal-h1");
    for (const t of ["BMW 320d", "EZ 2020 · 55–80k km", "Extrem", "17.500 €", "20.000 €", "−500 € seit Erkennung", "privat", "80331 München", "hoch", "heute neu", "aktiv", "Platz 1 im Sample", "21 Tage Basis"]) {
      expect(z1.textContent).toContain(t);
    }
    expect(el("hd-vorteil-h1").textContent).toBe("2.500 € (12,5 %)");
    const z2 = el("hd-deal-h2");
    expect(z2.textContent).toContain("Deal");
    expect(z2.textContent).toContain("Händler");
    expect(z2.textContent).toContain("niedrig");
    expect(el("hd-alt-h2").textContent).toBe("Stand 27.09.");
    expect(el("hd-alt-h1")).toBeNull();
    const zeilen = [...behaelter.querySelectorAll("[data-testid^='hd-deal-']")].map((e) => e.getAttribute("data-testid"));
    expect(zeilen).toEqual(["hd-deal-h1", "hd-deal-h2"]);
    expect(el("hd-segment-h1").getAttribute("href")).toBe(`/admin/markt/bmw-320d?segment=${encodeURIComponent("bmw-320d:2020:55001-80000")}`);
  });

  it("[Inserat öffnen] nur für https-Links auf mobile.de", async () => {
    await starten();
    expect(el("hd-inserat-h1").getAttribute("href")).toBe("https://suchen.mobile.de/auto-inserat/bmw-320d/h1.html");
    expect(el("hd-inserat-h1").getAttribute("rel")).toContain("noopener");
    expect(el("hd-inserat-h2")).toBeNull();
    expect(el("hd-kein-link-h2").textContent).toBe("kein Link");
  });

  it("Filter und Sortierung landen als GET-Parameter; Auswahlfelder laden sofort", async () => {
    await starten();
    await tippen("hd-filter-ez", "2020");
    await tippen("hd-filter-km-min", "50000");
    await tippen("hd-filter-make", "BMW");
    await klick("hd-filter-anwenden");
    expect(letzteParams()).toEqual({ sort: "vorteil_pct", status: "aktuell", limit: 300, ez: 2020, km_min: 50000, make: "BMW" });
    await tippen("hd-filter-privat", "privat");
    expect(letzteParams().privat).toBe(true);
    await tippen("hd-filter-privat", "haendler");
    expect(letzteParams().privat).toBe(false);
    await tippen("hd-filter-klasse", "STRONG");
    expect(letzteParams().klasse).toBe("STRONG");
    for (const sort of ["vorteil_eur", "neueste", "privat", "modell", "ez", "km", "liquiditaet", "klasse"]) {
      await tippen("hd-filter-sort", sort);
      expect(letzteParams().sort).toBe(sort);
    }
    await tippen("hd-filter-modell", "bmw-320d");
    expect(letzteParams().model_id).toBe("bmw-320d");
    await klick("hd-filter-heute-neu");
    expect(letzteParams().heute_neu).toBe(true);
    expect(hotDealParams({ privat: "", ez: " ", km_max: "90000", klasse: "DEAL" })).toEqual({ sort: "vorteil_pct", status: "aktuell", limit: 300, km_max: 90000, klasse: "DEAL" });
  });

  it("Umschalter aktuell/alle zeigt verlassene Deals mit Grund; Verlauf je Deal", async () => {
    await starten();
    expect(el("hd-deal-h3")).toBeNull();
    await klick("hd-umschalter-alle");
    expect(letzteParams().status).toBe("alle");
    expect(el("hd-umschalter-alle").getAttribute("aria-pressed")).toBe("true");
    const z3 = el("hd-deal-h3");
    expect(z3.textContent).toContain("verlassen");
    expect(z3.textContent).toContain("nicht mehr unter den günstigsten (kein Verkauf!)");
    expect(el("hd-inserat-h3")).toBeNull();
    await klick("hd-verlauf-h1");
    const ev = el("hd-ereignisse-h1").textContent;
    expect(ev).toContain("neu im Sample und gleich ein Hot Deal");
    expect(ev).toContain("Preis gesenkt (weiter Hot Deal)");
    expect(ev).toContain("17.500 € (12,5 % unter 20.000 €)");
    expect(netz.gets.find((g) => g.url === "/admin/market/hot-deals/ereignisse").params).toEqual({ segment_id: "bmw-320d:2020:55001-80000", listing_id: "h1" });
    await klick("hd-verlauf-h1");
    expect(el("hd-ereignisse-h1")).toBeNull();
  });

  it("Jetzt auswerten ruft die Super-Admin-Route und lädt neu; Ladefehler bleibt bedienbar", async () => {
    await starten();
    const vorher = netz.gets.filter((g) => g.url === "/admin/market/hot-deals").length;
    await klick("hd-auswerten");
    expect(netz.posts).toEqual(["/admin/market/hot-deals/auswerten"]);
    expect(netz.gets.filter((g) => g.url === "/admin/market/hot-deals").length).toBe(vorher + 1);
    await act(async () => { wurzel.unmount(); }); wurzel = null; behaelter?.remove();
    netz.fehler = "Nur der Super-Admin darf das";
    await starten();
    expect(el("hot-deals-fehler").textContent).toContain("Super-Admin");
    expect(el("hot-deals-filter")).toBeTruthy();
  });
});
