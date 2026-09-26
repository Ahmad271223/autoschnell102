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

const netz = vi.hoisted(() => ({ posts: [], ueberschritten: false, verworfen: 0 }));
vi.mock("sonner", () => ({ toast: { success: vi.fn(), error: vi.fn(), info: vi.fn(), warning: vi.fn() } }));
vi.mock("@/lib/api", () => ({
  errMsg: (e, s) => e?.message || s,
  api: {
    get: vi.fn(async (url, cfg) => {
      if (url === "/admin/market/auftraege") return { data: { auftraege: [
        // Review 26.09. abends P7: Karosserie als Code; P4: Fassung 2 nach materieller Aenderung
        { id: "bmw-320d", label: "BMW 320d", make: "BMW", model: "320", variant: "320d", fuel: "DIESEL", body: "EstateCar", version: 2, power_kw_min: 120, power_kw_max: 145, model_id: "10",
          ez_years: [2019, 2020, 2021, 2022], km_buckets: [{ min_km: 10000, max_km: 30000 }, { min_km: 30001, max_km: 50000 }], rows: 20, crawls_per_day: 2,
          status: "active", prognose: { segmente: 8 }, last_success_at: "2026-10-01T04:00:00Z", monatsverbrauch_usd: 1.23, crawl_status: "ok" },
        // Welle 5 Nr. 65: Polo hat einen bestandenen Testlauf fuer seine Definition -> Aktivieren frei; Astra ohne -> gesperrt
        { id: "vw-polo-10tsi", label: "VW Polo 1.0 TSI", make: "Volkswagen", model: "Polo", variant: "1.0 TSI", fuel: "PETROL", model_id: "27", ez_years: [2020], km_buckets: [{ min_km: 0, max_km: 50000 }],
          rows: 20, crawls_per_day: 1, status: "paused", prognose: { segmente: 1 }, monatsverbrauch_usd: 0, definition_hash: "h-polo", testlauf_ok_hash: "h-polo", testlauf_ok_at: "2026-10-01T05:00:00Z" },
        { id: "opel-astra-ohne", label: "Opel Astra 1.2", make: "Opel", model: "Astra", variant: "1.2 Turbo", fuel: "PETROL", model_id: "9", ez_years: [2020], km_buckets: [{ min_km: 0, max_km: 50000 }],
          rows: 20, crawls_per_day: 1, status: "paused", prognose: { segmente: 1 }, monatsverbrauch_usd: 0, definition_hash: "h-astra", testlauf_ok_hash: "h-alt" }],
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
        rows: body.rows, crawls_per_day: body.crawls_per_day, rows_tag: 640, rows_monat: 19456, kosten_monat_usd: 14.2, kosten_monat_ersatz_usd: 61.8 } : null,
        segmente: 24, rows_monat: 29184, kosten_monat_usd: netz.ueberschritten ? 900 : 22.8, budget_usd: 700, ueberschritten: netz.ueberschritten } };
      if (url === "/admin/market/testlauf") return { data: { anzahl: 2, segment: "EZ 2019 · 10–30k km", sortiert: true, alle_ez_ok: true, alle_km_ok: true, usd: 0.0085, actor: "scrapesmith~mobile-de-scraper",
        zeilen: [{ title: "BMW 320d Touring", first_registration: "03/2019", mileage_km: 22000, price_gross: 24900, power_kw: 140, fuel: "Diesel", gearbox: "Automatic", ez_ok: true, km_ok: true, gueltig: true, grund: null },
                 { title: "BMW 320d Limousine", first_registration: "11/2019", mileage_km: 28000, price_gross: 25500, power_kw: 140, fuel: "Diesel", gearbox: "Automatic", ez_ok: true, km_ok: true, gueltig: true, grund: null }],
        // Review 26.09. Nr. 39: alle Segmente in einem Lauf; Welle 5 Nr. 20: geliefert/gueltig/verworfen (Grund) je Segment; Nr. 65: bestanden -> testlauf_ok
        segmente: [{ label: "EZ 2019 · 10–30k km", anzahl: 2, geliefert: 2, gueltig: 2, verworfen: 0, gruende: [], ez_ok: true, km_ok: true },
                   { label: "EZ 2019 · 30–50k km", anzahl: 1, geliefert: 1, gueltig: 0, verworfen: 1, gruende: ["ez 2021 > 2019"], ez_ok: false, km_ok: true },
                   { label: "EZ 2020 · 10–30k km", anzahl: 0, geliefert: 0, gueltig: 0, verworfen: 0, gruende: [], ez_ok: null, km_ok: null }], leer: 1, segmente_geprueft: 3, segmente_gesamt: 3,
        segmente_max: 40, geliefert_gesamt: 3, gueltig_gesamt: 2, verworfen_gesamt: netz.verworfen, bestanden: netz.verworfen === 0,
        definition_hash: "h-neu", testlauf_ok_at: netz.verworfen === 0 ? "2026-10-01T05:00:00Z" : null, testlauf_ok_hash: netz.verworfen === 0 ? "h-neu" : null } };
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
beforeEach(() => { netz.posts.length = 0; netz.ueberschritten = false; netz.verworfen = 0; vi.useFakeTimers({ toFake: ["setTimeout", "clearTimeout"] }); });
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
    expect(z.textContent).toContain("Kombi");              // P7: Karosserie in der Liste
    expect(z.textContent).toContain("Fassung v2");          // P4: geaenderte Fassung sichtbar
    expect(el("auftrag-vw-polo-10tsi").textContent).not.toContain("Fassung");
    expect(z.textContent).toContain("1.23 $");
    await klick("auftrag-pausieren-bmw-320d");
    expect(netz.posts[0]).toEqual({ url: "/admin/market/models/bmw-320d/status", body: { status: "paused" } });
    await klick("auftrag-aktivieren-vw-polo-10tsi");
    expect(netz.posts[1]).toEqual({ url: "/admin/market/models/vw-polo-10tsi/status", body: { status: "active" } });
    // Welle 5 Nr. 65: ohne bestandenen Testlauf fuer die aktuelle Definition ist Aktivieren gesperrt
    expect(el("auftrag-aktivieren-opel-astra-ohne").disabled).toBe(true);
    expect(el("auftrag-aktivieren-opel-astra-ohne").getAttribute("title")).toContain("erst Testlauf");
    expect(el("auftrag-aktivieren-vw-polo-10tsi").disabled).toBe(false);
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
    // Welle 5 Nr. 5: ohne Kraftstoff/Getriebe/kW/Karosserie warnt das Formular — die Variante filtert nicht
    expect(el("auftrag-variante-hinweis").textContent).toContain("Mindestens Kraftstoff, Getriebe, kW oder Karosserie");
    // Nr. 65: Aktivieren erst nach Testlauf
    expect(el("auftrag-aktivieren").disabled).toBe(true);
    expect(el("auftrag-aktivieren-hinweis").textContent).toBe("erst Testlauf");
    await setzen("auftrag-kraftstoff", "DIESEL");
    expect(el("auftrag-variante-hinweis").textContent).toContain("Nur Beschriftung");
    // P7: Karosserie als Auswahl (Code), kein Freitext
    expect(el("auftrag-karosserie").tagName).toBe("SELECT");
    expect([...el("auftrag-karosserie").options].map((o) => o.value)).toEqual(["", "Limousine", "EstateCar", "OffRoad", "Cabrio", "SportsCar", "SmallCar", "Van"]);
    await setzen("auftrag-karosserie", "EstateCar");
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
    expect(prog.body).toMatchObject({ make: "BMW", model: "320", variant: "320d", fuel: "DIESEL", body: "EstateCar", rows: 30, crawls_per_day: 1, status: "active" });
    expect(prog.body.ez_years).toEqual([2018, 2019, 2020, 2021]);
    expect(prog.body.km_buckets).toHaveLength(2);
    expect(el("auftrag-prognose").textContent).toContain("8 Segmente (4 EZ × 2 km)");
    expect(el("auftrag-prognose").textContent).toContain("14.20 $");
    // Review 26.09. Nr. 53: Obergrenze bei Ersatz-Scraper als Hinweis
    expect(el("auftrag-prognose-ersatz").textContent).toContain("bei Ersatz-Scraper bis zu 61.80 $");
    // Testlauf: erst nicht bestanden (eine Zeile verworfen) -> Aktivieren bleibt gesperrt
    netz.verworfen = 1;
    await klick("auftrag-testlauf-knopf");
    expect(el("auftrag-testlauf").textContent).toContain("2 Fahrzeuge");
    expect(el("auftrag-testlauf").textContent).toContain("Preis aufsteigend ✓");
    expect(el("auftrag-testlauf").textContent).toContain("BMW 320d Touring");
    expect(el("auftrag-testlauf-ergebnis").textContent).toContain("Nicht bestanden — 2 gültig, 1 verworfen");
    expect(el("auftrag-aktivieren").disabled).toBe(true);
    const segTab = el("auftrag-testlauf-segmente");
    expect(segTab.textContent).toContain("Alle Segmente (3, je bis zu 2 Treffer) · 1 leer");
    expect(segTab.textContent).toContain("EZ 2019 · 30–50k km");
    expect(segTab.querySelectorAll("tbody tr")).toHaveLength(3);
    // Welle 5 Nr. 20: geliefert / gueltig / verworfen (Grund) je Segment
    expect(segTab.textContent).toContain("verworfen (Grund)");
    expect(segTab.querySelectorAll("tbody tr")[1].textContent).toContain("1 (ez 2021 > 2019)");
    expect(segTab.querySelectorAll("tbody tr")[1].textContent).toContain("✗");
    expect(segTab.querySelectorAll("tbody tr")[2].textContent).toContain("—");
    expect(el("auftrag-testlauf").textContent).toContain("höchstens 40");
    // bestanden -> Aktivieren frei; eine materielle Aenderung (Getriebe) sperrt wieder, rows nicht
    netz.verworfen = 0;
    await klick("auftrag-testlauf-knopf");
    expect(el("auftrag-testlauf-ergebnis").textContent).toContain("Bestanden");
    expect(el("auftrag-aktivieren").disabled).toBe(false);
    await setzen("auftrag-getriebe", "MANUAL_GEAR");
    expect(el("auftrag-aktivieren").disabled).toBe(true);
    await klick("auftrag-testlauf-knopf");
    expect(el("auftrag-aktivieren").disabled).toBe(false);
    // Aktivieren -> POST models mit status active (+ Testlauf-Nachweis); bei Budget-Ueberschreitung Rueckfrage
    netz.ueberschritten = true;
    await setzen("auftrag-rows", "40");
    await prognoseAbwarten();
    expect(el("auftrag-prognose").textContent).toContain("überschreiten");
    expect(el("auftrag-aktivieren").disabled).toBe(false);
    const bestaetigen = vi.spyOn(window, "confirm").mockReturnValue(false);
    await klick("auftrag-aktivieren");
    expect(netz.posts.some((p) => p.url === "/admin/market/models")).toBe(false);
    bestaetigen.mockReturnValue(true);
    await klick("auftrag-aktivieren");
    const anlage = netz.posts.find((p) => p.url === "/admin/market/models");
    expect(anlage.body).toMatchObject({ make: "BMW", model: "320", status: "active", rows: 40, testlauf_ok_at: "2026-10-01T05:00:00Z", testlauf_ok_hash: "h-neu" });
    expect(anlage.body.seed_version).toBeUndefined();
    bestaetigen.mockRestore();
  });

  it("Duplizieren öffnet das Formular vorbelegt und speichert über die Duplicate-Route", async () => {
    await starten();
    await klick("auftrag-duplizieren-bmw-320d");
    expect(el("auftrag-marke").value).toBe("BMW");
    expect(el("auftrag-karosserie").value).toBe("EstateCar");
    expect(el("auftrag-ez-2019").getAttribute("aria-pressed")).toBe("true");
    await setzen("auftrag-modell", "520");
    await setzen("auftrag-variante", "520d");
    await klick("auftrag-speichern");
    const dup = netz.posts.find((p) => p.url === "/admin/market/models/bmw-320d/duplicate");
    expect(dup.body).toMatchObject({ model: "520", variant: "520d", status: "paused" });
  });
});
