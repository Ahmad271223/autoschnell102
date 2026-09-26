/**
 * Admin → Marktanalyse → Suchaufträge (v3, 26.09.2026): Kostenübersicht,
 * Liste mit Aktionen, Formular mit Katalog-Auswahl, EZ-Jahren, km-Bereichen,
 * Live-Prognose, Testlauf, Speichern/Aktivieren (Rückfrage bei Budget-Überschreitung).
 */
import { act, createElement as h } from "react";
import { createRoot } from "react-dom/client";
import { MemoryRouter } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

globalThis.IS_REACT_ACT_ENVIRONMENT = true;

const netz = vi.hoisted(() => ({ posts: [], ueberschritten: false }));
vi.mock("sonner", () => ({ toast: { success: vi.fn(), error: vi.fn(), info: vi.fn(), warning: vi.fn() } }));
vi.mock("@/lib/api", () => ({
  errMsg: (e, s) => e?.message || s,
  api: {
    get: vi.fn(async (url, cfg) => {
      if (url === "/admin/market/auftraege") return { data: { auftraege: [
        { id: "bmw-320d", label: "BMW 320d", make: "BMW", model: "320", variant: "320d", fuel: "DIESEL", power_kw_min: 120, power_kw_max: 145, model_id: "10",
          ez_years: [2019, 2020, 2021, 2022], km_buckets: [{ min_km: 10000, max_km: 30000 }, { min_km: 30001, max_km: 50000 }], rows: 20, crawls_per_day: 2,
          status: "active", prognose: { segmente: 8 }, last_success_at: "2026-10-01T04:00:00Z", monatsverbrauch_usd: 1.23, crawl_status: "ok" },
        { id: "vw-polo-10tsi", label: "VW Polo 1.0 TSI", make: "Volkswagen", model: "Polo", variant: "1.0 TSI", model_id: "27", ez_years: [2020], km_buckets: [{ min_km: 0, max_km: 50000 }],
          rows: 20, crawls_per_day: 1, status: "paused", prognose: { segmente: 1 }, monatsverbrauch_usd: 0 }],
        prognose: { aktive_modelle: 1, segmente: 8, rows_tag: 320, rows_monat: 9728, budget_usd: 700, kosten_monat_usd: 8.6, verbraucht_usd: 1.23, verbleibend_usd: 698.77,
                    ueberschritten: false, preise: { start_usd: 0.005, row_usd: 0.0007, buendel: 10, actor: "scrapesmith~mobile-de-scraper" } } } };
      if (url === "/admin/market/katalog" && cfg?.params?.marke) return { data: { modelle: [{ name: "320", model_id: "10" }, { name: "520", model_id: "17" }] } };
      if (url === "/admin/market/katalog") return { data: { marken: [{ name: "BMW", make_id: "3500" }, { name: "Volkswagen", make_id: "25200" }],
        standard: { ez_years: [2019, 2020, 2021, 2022], km_buckets: [{ min_km: 10000, max_km: 30000 }, { min_km: 30001, max_km: 50000 }], rows: 20, crawls_per_day: 2 } } };
      return { data: {} };
    }),
    post: vi.fn(async (url, body) => {
      netz.posts.push({ url, body });
      if (url === "/admin/market/prognose") return { data: { entwurf: body.make ? { segmente: (body.ez_years?.length || 0) * (body.km_buckets?.length || 0), ez_jahre: body.ez_years?.length || 0, km_bereiche: body.km_buckets?.length || 0,
        rows: body.rows, crawls_per_day: body.crawls_per_day, rows_tag: 640, rows_monat: 19456, kosten_monat_usd: 14.2 } : null,
        segmente: 24, rows_monat: 29184, kosten_monat_usd: netz.ueberschritten ? 900 : 22.8, budget_usd: 700, ueberschritten: netz.ueberschritten } };
      if (url === "/admin/market/testlauf") return { data: { anzahl: 2, segment: "EZ 2019 · 10–30k km", sortiert: true, alle_ez_ok: true, alle_km_ok: true, usd: 0.0085, actor: "scrapesmith~mobile-de-scraper",
        zeilen: [{ title: "BMW 320d Touring", first_registration: "03/2019", mileage_km: 22000, price_gross: 24900, power_kw: 140, fuel: "Diesel", gearbox: "Automatic", ez_ok: true, km_ok: true },
                 { title: "BMW 320d Limousine", first_registration: "11/2019", mileage_km: 28000, price_gross: 25500, power_kw: 140, fuel: "Diesel", gearbox: "Automatic", ez_ok: true, km_ok: true }] } };
      return { data: { ok: true, modell: { id: "neu" } } };
    }),
    put: vi.fn(async (url, body) => { netz.posts.push({ url, body }); return { data: { ok: true } }; }),
  },
}));
vi.mock("@/context/AuthContext", () => ({ useAuth: () => ({ user: { id: "sa", is_super_admin: true, role: "admin" } }) }));

const { default: MarktAuftraege } = await import("./MarktAuftraege");

let wurzel; let behaelter;
const el = (t) => behaelter.querySelector(`[data-testid="${t}"]`);
// Mikrotasks statt setTimeout — die Zeitgeber sind im Test eingefroren (Prognose-Verzoegerung)
async function warten(n = 8) { for (let i = 0; i < n; i += 1) await act(async () => { for (let k = 0; k < 6; k += 1) await Promise.resolve(); }); }
async function starten() {
  behaelter = document.createElement("div"); document.body.appendChild(behaelter); wurzel = createRoot(behaelter);
  await act(async () => { wurzel.render(h(MemoryRouter, null, h(MarktAuftraege))); });
  await warten();
}
async function klick(t) { const k = el(t); if (!k) throw new Error(`nicht gefunden: ${t}`); await act(async () => { k.click(); }); await warten(); }
async function setzen(t, wert) {
  const f = el(t); if (!f) throw new Error(`nicht gefunden: ${t}`);
  await act(async () => {
    const proto = f.tagName === "SELECT" ? window.HTMLSelectElement.prototype : window.HTMLInputElement.prototype;
    Object.getOwnPropertyDescriptor(proto, "value").set.call(f, wert);
    f.dispatchEvent(new Event(f.tagName === "SELECT" ? "change" : "input", { bubbles: true }));
  });
  await warten();
}
beforeEach(() => { netz.posts.length = 0; netz.ueberschritten = false; vi.useFakeTimers({ toFake: ["setTimeout", "clearTimeout"] }); });
afterEach(async () => { if (wurzel) await act(async () => { wurzel.unmount(); }); wurzel = null; behaelter?.remove(); vi.useRealTimers(); });
async function prognoseAbwarten() { await act(async () => { await vi.advanceTimersByTimeAsync(500); }); await warten(); }

describe("Suchaufträge", () => {
  it("Kostenübersicht und Liste mit Aktionen", async () => {
    await starten();
    expect(el("auftraege-kosten").textContent).toContain("9.728");
    expect(el("auftraege-kosten").textContent).toContain("8.60 $");
    const z = el("auftrag-bmw-320d");
    expect(z.textContent).toContain("2019, 2020, 2021, 2022");
    expect(z.textContent).toContain("10–30k, 30–50k");
    expect(z.textContent).toContain("2×");
    expect(z.textContent).toContain("aktiv");
    expect(z.textContent).toContain("1.23 $");
    await klick("auftrag-pausieren-bmw-320d");
    expect(netz.posts[0]).toEqual({ url: "/admin/market/models/bmw-320d/status", body: { status: "paused" } });
    await klick("auftrag-aktivieren-vw-polo-10tsi");
    expect(netz.posts[1]).toEqual({ url: "/admin/market/models/vw-polo-10tsi/status", body: { status: "active" } });
    const bestaetigen = vi.spyOn(window, "confirm").mockReturnValue(true);
    await klick("auftrag-archivieren-bmw-320d");
    expect(netz.posts[2].body).toEqual({ status: "archived" });
    bestaetigen.mockRestore();
  });

  it("Formular: Katalog, EZ-Jahre, km-Bereiche, Live-Prognose, Testlauf, Aktivieren mit Budget-Rückfrage", async () => {
    await starten();
    await klick("auftrag-neu");
    expect(el("auftrag-formular")).toBeTruthy();
    await setzen("auftrag-marke", "BMW");
    await setzen("auftrag-modell", "320");
    await setzen("auftrag-variante", "320d");
    await setzen("auftrag-kraftstoff", "DIESEL");
    // EZ: Standard 4 Jahre vorbelegt, 2022 abwählen, 2018 dazu
    expect(el("auftrag-ez-2019").getAttribute("aria-pressed")).toBe("true");
    await klick("auftrag-ez-2022");
    await klick("auftrag-ez-2018");
    // km: dritten Bereich hinzufuegen, ersten entfernen
    await klick("auftrag-km-hinzu");
    expect(el("auftrag-km-von-2").value).toBe("50001");
    await klick("auftrag-km-weg-0");
    await setzen("auftrag-rows", "30");
    await setzen("auftrag-frequenz", "1");
    await prognoseAbwarten();
    const prog = netz.posts.filter((p) => p.url === "/admin/market/prognose").pop();
    expect(prog.body).toMatchObject({ make: "BMW", model: "320", variant: "320d", fuel: "DIESEL", rows: 30, crawls_per_day: 1, status: "active" });
    expect(prog.body.ez_years).toEqual([2018, 2019, 2020, 2021]);
    expect(prog.body.km_buckets).toHaveLength(2);
    expect(el("auftrag-prognose").textContent).toContain("8 Segmente (4 EZ × 2 km)");
    expect(el("auftrag-prognose").textContent).toContain("14.20 $");
    // Testlauf
    await klick("auftrag-testlauf-knopf");
    expect(el("auftrag-testlauf").textContent).toContain("2 Fahrzeuge");
    expect(el("auftrag-testlauf").textContent).toContain("Preis aufsteigend ✓");
    expect(el("auftrag-testlauf").textContent).toContain("BMW 320d Touring");
    // Aktivieren -> POST models mit status active; bei Budget-Ueberschreitung Rueckfrage
    netz.ueberschritten = true;
    await setzen("auftrag-rows", "40");
    await prognoseAbwarten();
    expect(el("auftrag-prognose").textContent).toContain("überschreiten");
    const bestaetigen = vi.spyOn(window, "confirm").mockReturnValue(false);
    await klick("auftrag-aktivieren");
    expect(netz.posts.some((p) => p.url === "/admin/market/models")).toBe(false);
    bestaetigen.mockReturnValue(true);
    await klick("auftrag-aktivieren");
    const anlage = netz.posts.find((p) => p.url === "/admin/market/models");
    expect(anlage.body).toMatchObject({ make: "BMW", model: "320", status: "active", rows: 40 });
    bestaetigen.mockRestore();
  });

  it("Duplizieren öffnet das Formular vorbelegt und speichert über die Duplicate-Route", async () => {
    await starten();
    await klick("auftrag-duplizieren-bmw-320d");
    expect(el("auftrag-marke").value).toBe("BMW");
    expect(el("auftrag-ez-2019").getAttribute("aria-pressed")).toBe("true");
    await setzen("auftrag-modell", "520");
    await setzen("auftrag-variante", "520d");
    await klick("auftrag-speichern");
    const dup = netz.posts.find((p) => p.url === "/admin/market/models/bmw-320d/duplicate");
    expect(dup.body).toMatchObject({ model: "520", variant: "520d", status: "paused" });
  });
});
