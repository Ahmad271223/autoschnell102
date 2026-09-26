/**
 * Markt → Chancen (25.09.2026): Liste mit Filtern, regelbasiert, nur lesend;
 * Fehler als Hinweis, leere Liste als Text; kein Marktmedian.
 */
import { act, createElement as h } from "react";
import { createRoot } from "react-dom/client";
import { afterEach, describe, expect, it, vi } from "vitest";

globalThis.IS_REACT_ACT_ENVIRONMENT = true;

const netz = vi.hoisted(() => ({ gets: [], fehler: false }));
vi.mock("@/lib/api", () => ({
  errMsg: (e, s) => e?.message || s,
  api: { get: vi.fn(async (url, cfg) => {
    netz.gets.push({ url, params: cfg?.params });
    if (netz.fehler) throw new Error("Server weg");
    if (url === "/markt/modelle") return { data: { modelle: [{ id: "bmw-320d", label: "BMW 320d" }], km_buckets: [{ min_km: 55001, max_km: 85000 }] } };
    return { data: { chancen: [
      { id: "c1", typ: "neues_minimum", label: "Audi A4 40 TDI", km_label: "55–85k km", title: "Audi A4 40 TDI S line", price: 19900,
        referenz_eur: 21100, differenz_eur: -1200, differenz_pct: -5.69, mileage_km: 73000, first_registration: "06/2020", power_kw: 140,
        gearbox: "Automatik", postal_code: "70173", city: "Stuttgart", rang: 1, rang_vorher: null, text: "unter dem bisher günstigsten Angebot",
        created_at: "2026-10-01T05:00:00Z", mobile_created_at: "2026-09-30T10:00:00Z", url: "https://suchen.mobile.de/x", active_state: "seen" },
      { id: "c2", typ: "stark_reduziert", label: "BMW 320d", km_label: "55–85k km", title: "BMW 320d", price: 20400, delta_eur: -1500, delta_pct: -6.8,
        mileage_km: 60000, first_registration: "01/2019", rang: 3, rang_vorher: 9, text: "deutliche Preisreduzierung", created_at: "2026-10-01T05:00:00Z",
        first_price: 21900, active_state: "not_seen_in_sample" }], hinweis: "kein Marktmedian" } };
  }) },
}));

const { default: Chancen } = await import("./Chancen");
let wurzel; let behaelter;
const el = (t) => behaelter.querySelector(`[data-testid="${t}"]`);
async function warten() { for (let i = 0; i < 8; i += 1) await act(async () => { await new Promise((r) => setTimeout(r, 0)); }); }
async function starten() {
  behaelter = document.createElement("div"); document.body.appendChild(behaelter); wurzel = createRoot(behaelter);
  await act(async () => { wurzel.render(h(Chancen)); });
  await warten();
}
afterEach(async () => { if (wurzel) await act(async () => { wurzel.unmount(); }); wurzel = null; behaelter?.remove(); netz.gets.length = 0; netz.fehler = false; });

describe("Chancen", () => {
  it("listet Chancen mit Art, Differenz und Filtern", async () => {
    await starten();
    expect(el("chance-c1").textContent).toContain("Neues günstigstes Angebot");
    expect(el("chance-c1").textContent).toContain("19.900 €");
    expect(el("chance-c1").textContent).toContain("−1.200 € (-5,7 %) zu 21.100 €");
    expect(el("chance-c2").textContent).toContain("Stark reduziert");
    expect(el("chance-c2").textContent).toContain("−1.500 € (-6,8 %) gegenüber Vortag");
    expect(el("chance-c2").textContent).toContain("gestern 9");
    expect(el("chance-c2").textContent).toContain("kein Verkauf");
    expect(el("chancen-page").textContent).toContain("kein Marktmedian");
    expect(el("chancen-page").textContent).not.toMatch(/Marktpreis/);
    // Filter -> neue Abfrage mit Parametern
    await act(async () => {
      const s = el("chancen-typ"); s.value = "stark_reduziert"; s.dispatchEvent(new Event("change", { bubbles: true }));
    });
    await warten();
    const letzte = netz.gets.filter((g) => g.url === "/markt/chancen").pop();
    expect(letzte.params.typ).toBe("stark_reduziert");
    await act(async () => {
      const s = el("chancen-km"); s.value = "55001-85000"; s.dispatchEvent(new Event("change", { bubbles: true }));
    });
    await warten();
    const km = netz.gets.filter((g) => g.url === "/markt/chancen").pop();
    expect(km.params.km_min).toBe("55001");
    expect(km.params.km_max).toBe("85000");
  });

  it("Fehler als Hinweis, nie ein Absturz", async () => {
    netz.fehler = true;
    await starten();
    expect(el("chancen-fehler").textContent).toContain("Server weg");
    expect(el("chancen-liste").children.length).toBe(0);
  });
});
