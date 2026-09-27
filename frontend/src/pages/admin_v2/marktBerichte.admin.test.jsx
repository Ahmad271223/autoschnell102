/**
 * Admin → Marktanalyse → Berichte (Master-Auftrag Phase E, 27.09.2026): Übersicht aller Modelle je Periode
 * (Typ-Umschalter, Periodenwahl, Sortierungen aus Abschnitt 37, Farblogik), Knopf „Berichte jetzt erstellen“
 * (nur fällige Perioden) und der Modellbericht (final/vorläufig, Kennzahlen, Diagramme mit Lücken, Tagestabelle,
 * Fallen/Steigen/Stabil, 5-Tage-Blöcke mit Filter, Segmentdetail, Hot Deals, frühere Fassungen).
 */
import { act, createElement as h } from "react";
import { createRoot } from "react-dom/client";
import { MemoryRouter, Route, Routes, useLocation } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

globalThis.IS_REACT_ACT_ENVIRONMENT = true;

const netz = vi.hoisted(() => ({ gets: [], posts: [], fehler: null, ohneFinal: false, modellFehler: null, bericht: null, tooltips: [], diagramme: [], schemaAlt: false }));
vi.mock("sonner", () => ({ toast: { success: vi.fn(), error: vi.fn() } }));
vi.mock("recharts", () => {
  const Leer = ({ children }) => h("div", { "data-chart": "1" }, children);
  const Verlauf = ({ children, data }) => { netz.diagramme.push(data); return h("div", { "data-chart": "1" }, children); };
  return { ResponsiveContainer: Leer, ComposedChart: Verlauf, BarChart: Leer, Line: () => null, Area: () => null, Bar: () => null,
           XAxis: () => null, YAxis: () => null, Tooltip: (p) => { netz.tooltips.push(p); return null; }, CartesianGrid: () => null };
});
const ZEILEN = [
  { model_id: "bmw-320d", label: "BMW 320d", fuel: "DIESEL", gearbox: "AUTOMATIC_GEAR", richtung: "FALLING", delta_eur: -400, delta_pct: -2.1, listings: 40,
    preissenkungen: 9, preiserhoehungen: 2, hot_deals: 3, private_hot_deals: 1, liquiditaet: "HIGH", data_quality: "GOOD", health: null, kosten_usd: 1.2,
    empty_segmente: 0, confidence: "HIGH", coverage_days: 29, expected_days: 29 },
  { model_id: "vw-golf", label: "VW Golf 2.0 TDI", fuel: "DIESEL", gearbox: "MANUAL_GEAR", richtung: "RISING", delta_eur: 300, delta_pct: 1.4, listings: 25,
    preissenkungen: 1, preiserhoehungen: 5, hot_deals: 0, private_hot_deals: 0, liquiditaet: "LOW", data_quality: "POOR", health: null, kosten_usd: 2.5,
    empty_segmente: 3, confidence: "LOW", coverage_days: 20, expected_days: 29 },
  { model_id: "audi-a4", label: "Audi A4 40 TDI", fuel: "DIESEL", gearbox: "AUTOMATIC_GEAR", richtung: "STABLE", delta_eur: 20, delta_pct: 0.1, listings: 60,
    preissenkungen: 4, preiserhoehungen: 4, hot_deals: 5, private_hot_deals: 2, liquiditaet: "MEDIUM", data_quality: "MEDIUM", health: null, kosten_usd: 0.4,
    empty_segmente: 1, confidence: "MEDIUM", coverage_days: 27, expected_days: 29 },
].map((z) => ({ ...z, schema: 2 }));
const TAGE = [
  { date: "2028-02-01", median: 20000, min: 19600, p25: 19800, p75: 20200, listings: 5, segmente: 1, delta_vortag_eur: null, delta_vortag_pct: null, richtung: null,
    neue: 0, preissenkungen: 0, preiserhoehungen: 0, hot_deals: 0, data_quality: "GOOD", gueltig: true, leer: false, nur_ungueltig: false, andere_fassung: false },
  { date: "2028-02-02", median: 19800, min: 19400, listings: 5, segmente: 1, delta_vortag_eur: -200, delta_vortag_pct: -1, richtung: "FALLING",
    neue: 1, preissenkungen: 1, preiserhoehungen: 0, hot_deals: 1, data_quality: "GOOD", gueltig: true, leer: false, nur_ungueltig: false, andere_fassung: false },
  { date: "2028-02-03", median: null, min: null, listings: 0, segmente: 0, delta_vortag_eur: null, richtung: null, neue: 0, preissenkungen: 0, preiserhoehungen: 0,
    hot_deals: 0, data_quality: null, gueltig: false, leer: false, nur_ungueltig: false, andere_fassung: false },
  { date: "2028-02-04", median: null, min: null, listings: 0, segmente: 0, delta_vortag_eur: null, richtung: null, neue: 0, preissenkungen: 0, preiserhoehungen: 0,
    hot_deals: 0, data_quality: "POOR", gueltig: false, leer: false, nur_ungueltig: true, andere_fassung: false },
  { date: "2028-02-06", median: 20100, min: 19700, listings: 5, segmente: 1, delta_vortag_eur: 300, delta_vortag_pct: 1.5, richtung: "RISING",
    neue: 0, preissenkungen: 0, preiserhoehungen: 1, hot_deals: 0, data_quality: "MEDIUM", gueltig: true, leer: false, nur_ungueltig: false, andere_fassung: false },
];
const BERICHT = {
  model_id: "bmw-320d", typ: "MONTHLY", periode_von: "2028-02-01", periode_bis: "2028-02-29", status: "FINAL", revision: 1, schema: 2, erstellt_at: "2028-03-01T05:10:00Z",
  faellig_ab: "2028-03-01T05:00:00Z", stabil_zone_pct: 0.5, modell: { label: "BMW 320d", fuel: "DIESEL", gearbox: "AUTOMATIC_GEAR" },
  fassung: { version: 2, definition_hash: "h2", ab: "2028-02-01" },
  kennzahlen: { startwert: 20000, endwert: 19800, delta_eur: -200, delta_pct: -1, richtung: "FALLING", korb_segmente: 1, median_periode: 19900, mittelwert_periode: 19950,
                minimum: { date: "2028-02-02", wert: 19800 }, maximum: { date: "2028-02-06", wert: 20100 }, guenstigstes_angebot: { date: "2028-02-02", preis: 19400 },
                sample_market_change_eur: -200, sample_market_change_pct: -1, same_listing_price_change_eur: -150, same_listing_price_change_pct: -0.75, same_listing_anzahl: 4,
                neue_listings: 1, verschwundene_listings: 2, preissenkungen: 5, mittlere_senkung_eur: -210, preiserhoehungen: 1, mittlere_erhoehung_eur: 300,
                unterschiedliche_listings: 7, top3_wechsel: 1, top5_wechsel: 2, hot_deals: 2, private_hot_deals: 1, hot_deals_neu: 2, data_quality: "GOOD",
                market_depth: "FULL", liquiditaet: "MEDIUM", coverage_days: 27, expected_days: 29, confidence: "MEDIUM", confidence_gruende: ["wenige verschiedene Inserate"],
                segmente_mit_daten: 1, segmente_gesamt: 1, empty_segmente: 0, kosten_usd: 0.28, cost_per_valid_observation: 0.01, cost_per_unique_listing: 0.04, cost_per_hot_deal: 0.14 },
  bewegung: { vergleiche: 26, fallend: 6, steigend: 3, stabil: 17, fallend_pct: 23.1, steigend_pct: 11.5, stabil_pct: 65.4, summe_negativ_eur: -1150, summe_positiv_eur: 950,
              netto_eur: -200, staerkster_rueckgang_eur: { date: "2028-02-02", eur: -200, pct: -1 }, staerkster_rueckgang_pct: { date: "2028-02-06", eur: -200, pct: -1.042 },
              staerkster_anstieg_eur: { date: "2028-02-08", eur: 300, pct: 1.579 }, staerkster_anstieg_pct: { date: "2028-02-08", eur: 300, pct: 1.579 },
              laengste_fallserie: 5, laengste_steigeserie: 3, volatilitaet_pct: 0.62 },
  tage: TAGE,
  bloecke: [{ von: "2028-02-01", bis: "2028-02-05", startwert: 20000, endwert: 19800, delta_eur: -200, delta_pct: -1, richtung: "FALLING", preissenkungen: 1, preiserhoehungen: 0,
              hot_deals: 1, private_hot_deals: 1, coverage_days: 2, expected_days: 5 },
            { von: "2028-02-06", bis: "2028-02-10", startwert: 20100, endwert: 20100, delta_eur: 0, delta_pct: 0, richtung: "STABLE", preissenkungen: 0, preiserhoehungen: 1,
              hot_deals: 0, coverage_days: 1, expected_days: 5 }],
  segmente: [{ segment_id: "bmw-320d:v2:2020:20000-40000", ez_label: "EZ 2020", km_label: "20–40k km", aktueller_median: 19800, delta_eur: -200, delta_pct: -1,
               gueltige_tage: 27, erwartete_tage: 29, listings: 7, hot_deals: 2, market_depth: "THIN", data_quality: "GOOD", health: null, kosten_usd: 0.28 }],
  hot_deals_top: [{ segment_id: "bmw-320d:v2:2020:20000-40000", listing_id: "h1", tag: "2028-02-02", typ: "NEW_HOT_DEAL", klasse: "STRONG", price: 18000, reference_price: 20000,
                    diff_eur: 2000, diff_pct: 10, rank: 1, privat: true }],
  fruehere_fassungen: [{ version: 1, definition_hash: "h1", von: "2028-02-01", bis: "2028-02-03", tage: 3, startwert: 21000, endwert: 20900, delta_eur: -100, delta_pct: -0.48 }],
  hinweise: ["Fassungswechsel im Zeitraum — gerechnet wird nur die aktuelle Fassung ab 2028-02-04"],
  hinweis: "Beobachtet wird je Segment nur die günstige Marktzone",
};
// laufender März (vorläufig, Stand 02.03.): 01.03. voll, 02.03. Teilabdeckung (ein Segment ohne gültigen Lauf), ab 03.03. offen
const OFFEN = { median: null, min: null, listings: 0, segmente: 0, delta_vortag_eur: null, richtung: null, neue: 0, preissenkungen: 0, preiserhoehungen: 0,
                hot_deals: 0, data_quality: null, gueltig: false, leer: false, nur_ungueltig: false, andere_fassung: false, offen: true };
const VORL_TAGE = [
  { ...TAGE[0], date: "2028-03-01", teilabdeckung: false, fehlende_segmente: 0, offen: false },
  { ...TAGE[1], date: "2028-03-02", median: 12000, teilabdeckung: true, fehlende_segmente: 1, offen: false },
  { ...OFFEN, date: "2028-03-03" }, { ...OFFEN, date: "2028-03-04" },
];
function vorlaeufig(p) {
  return { ...BERICHT, typ: p.typ, periode_von: p.von, periode_bis: p.bis, status: "VORLAEUFIG", stand_at: "2028-03-02T08:00:00Z", faellig_ab: "2028-03-06T05:00:00Z",
           offen_ab: "2028-03-03", tage: VORL_TAGE,
           kennzahlen: { ...BERICHT.kennzahlen, coverage_days: 2, expected_days: 2, segment_tage_gueltig: 3, segment_tage_erwartet: 4, confidence_gruende: [] },
           bloecke: p.typ === "MONTHLY" ? [{ ...BERICHT.bloecke[0], von: "2028-03-01", bis: "2028-03-05", offen: false, coverage_days: 2, expected_days: 2 },
                                           { ...BERICHT.bloecke[1], von: "2028-03-06", bis: "2028-03-10", offen: true, richtung: "UNKNOWN", startwert: null, endwert: null,
                                             delta_eur: null, delta_pct: null, coverage_days: 0, expected_days: 0 }] : undefined,
           fruehere_fassungen: [], hinweise: ["vorläufig — live gerechnet"] };
}
vi.mock("@/lib/api", () => ({
  errMsg: (e, s) => e?.message || s,
  api: {
    get: vi.fn(async (url, opts) => {
      netz.gets.push({ url, params: opts?.params });
      if (netz.fehler) throw new Error(netz.fehler);
      if (url === "/admin/market/reports/periods") {
        const t = opts?.params?.typ;
        return { data: { final: netz.ohneFinal ? [] : [{ typ: t, von: "2028-02-01", bis: "2028-02-29", anzahl: 3, erstellt_at: "2028-03-01T05:10:00Z" },
                                                        { typ: t, von: "2028-01-01", bis: "2028-01-31", anzahl: 2 }],
                         laufend: [{ typ: t, von: "2028-03-01", bis: "2028-03-31", faellig_ab: "2028-04-01T04:00:00Z" }],
                         stand: { letzter_lauf_at: "2028-03-02T08:00:00Z" }, karenz_stunden: 6, stabil_zone_pct: 0.5 } };
      }
      if (url === "/admin/market/reports") {
        const zeilen = netz.schemaAlt ? ZEILEN.map((z) => (z.model_id === "vw-golf" ? { ...z, schema: 1 } : z)) : ZEILEN;
        return { data: { zeilen, anzahl: 3, hinweis: "Beobachtet wird je Segment nur die günstige Marktzone." } };
      }
      if (url === "/admin/market/reports/model/bmw-320d/list") {
        return { data: { final: netz.ohneFinal ? [] : [{ typ: "MONTHLY", periode_von: "2028-02-01", periode_bis: "2028-02-29", status: "FINAL", schema: netz.schemaAlt ? 1 : 2 }],
                         laufend: [{ typ: "FIVE_DAY", von: "2028-03-01", bis: "2028-03-05", faellig_ab: "2028-03-06T05:00:00Z" },
                                   { typ: "FIFTEEN_DAY", von: "2028-03-01", bis: "2028-03-15", faellig_ab: "2028-03-16T05:00:00Z" },
                                   { typ: "MONTHLY", von: "2028-03-01", bis: "2028-03-31", faellig_ab: "2028-04-01T04:00:00Z" }] } };
      }
      if (url === "/admin/market/reports/model/bmw-320d") {
        const p = opts?.params || {};
        if (netz.modellFehler) {
          const e = new Error(netz.modellFehler.message || "Keine Tagesdaten für dieses Modell im Zeitraum");
          e.response = { status: netz.modellFehler.status };
          throw e;
        }
        if (netz.bericht) return { data: netz.bericht };
        if (p.von === "2028-03-01") return { data: vorlaeufig(p) };
        return { data: BERICHT };
      }
      return { data: {} };
    }),
    post: vi.fn(async (url) => { netz.posts.push(url); return { data: { ok: true, berichte: { erstellt: 4, perioden: 2, wartet: 1 } } }; }),
  },
}));
vi.mock("@/context/AuthContext", () => ({ useAuth: () => ({ user: { id: "sa", is_super_admin: true, role: "admin" } }) }));

const { default: MarktBerichte } = await import("./MarktBerichte");
const { default: MarktBericht } = await import("./MarktBericht");
const { berichtAltesSchema, berichtDiagrammPunkt, berichtSortieren, periodeText, richtungAusPct } = await import("@/lib/markt");

let wurzel; let behaelter; let ort = null;
function Ort() { ort = useLocation(); return null; }
const el = (t) => behaelter.querySelector(`[data-testid="${t}"]`);
async function warten() { for (let i = 0; i < 10; i += 1) await act(async () => { await new Promise((r) => setTimeout(r, 0)); }); }
async function starten(pfad) {
  behaelter = document.createElement("div"); document.body.appendChild(behaelter); wurzel = createRoot(behaelter);
  await act(async () => {
    wurzel.render(h(MemoryRouter, { initialEntries: [pfad] }, h(Ort), h(Routes, null,
      h(Route, { path: "/admin/markt/berichte", element: h(MarktBerichte) }), h(Route, { path: "/admin/markt/berichte/:modell", element: h(MarktBericht) }))));
  });
  await warten();
}
async function klick(t) { const k = el(t); if (!k) throw new Error(`nicht gefunden: ${t}`); await act(async () => { k.click(); }); await warten(); }
async function waehlen(t, wert) {
  const e = el(t); if (!e) throw new Error(`nicht gefunden: ${t}`);
  const setter = Object.getOwnPropertyDescriptor(window.HTMLSelectElement.prototype, "value").set;
  await act(async () => { setter.call(e, wert); e.dispatchEvent(new Event("change", { bubbles: true })); });
  await warten();
}
const reihenfolge = () => [...behaelter.querySelectorAll("[data-testid^='bericht-zeile-']")].map((e) => e.getAttribute("data-testid").replace("bericht-zeile-", ""));
beforeEach(() => {
  netz.gets.length = 0; netz.posts.length = 0; netz.fehler = null; netz.ohneFinal = false; ort = null;
  netz.modellFehler = null; netz.bericht = null; netz.tooltips.length = 0; netz.diagramme.length = 0; netz.schemaAlt = false;
});
afterEach(async () => { if (wurzel) await act(async () => { wurzel.unmount(); }); wurzel = null; behaelter?.remove(); });

describe("Admin Berichte — Übersicht aller Modelle", () => {
  it("Typ Monat, neueste finale Periode, Zeilen mit Trend/Farbe, laufende Perioden nur als Hinweis", async () => {
    await starten("/admin/markt/berichte");
    expect(netz.gets.find((g) => g.url === "/admin/market/reports/periods").params).toEqual({ typ: "MONTHLY" });
    expect(netz.gets.find((g) => g.url === "/admin/market/reports").params).toEqual({ typ: "MONTHLY", von: "2028-02-01", bis: "2028-02-29" });
    expect(el("berichte-typ-MONTHLY").getAttribute("aria-pressed")).toBe("true");
    expect(el("berichte-periode").value).toBe("2028-02-01|2028-02-29");
    expect(el("berichte-uebersicht").textContent).toContain("Monat 01.02.–29.02.2028 · 3 Modelle");
    expect(el("berichte-laufend").textContent).toContain("01.03.–31.03.2028");
    expect(el("berichte-stand").textContent).toContain("6 h nach Periodenende");
    const z = el("bericht-zeile-bmw-320d");
    for (const t of ["BMW 320d", "fallend", "−400 €", "-2,1 %", "40", "9", "hoch", "gut", "1.20 $", "29/29 Tage"]) expect(z.textContent).toContain(t);
    expect(z.querySelectorAll("td")[4].getAttribute("style")).toContain("--st-gruen");
    expect(el("bericht-zeile-vw-golf").querySelectorAll("td")[4].getAttribute("style")).toContain("--st-rot");
    expect(el("bericht-zeile-vw-golf").textContent).toContain("schlecht");
    expect(el("bericht-link-bmw-320d").getAttribute("href")).toBe("/admin/markt/berichte/bmw-320d?typ=MONTHLY&von=2028-02-01&bis=2028-02-29");
    expect(reihenfolge()).toEqual(["bmw-320d", "audi-a4", "vw-golf"]);        // größter Preisrückgang zuerst
    expect(el("berichte-schema-hinweis")).toBeNull();                           // alle nach aktueller Rechenregel
  });

  it("Sortierungen nach Abschnitt 37 (UI und reine Funktion)", async () => {
    await starten("/admin/markt/berichte");
    const erwartet = { anstieg: ["vw-golf", "audi-a4", "bmw-320d"], deals: ["audi-a4", "bmw-320d", "vw-golf"], privat: ["audi-a4", "bmw-320d", "vw-golf"],
                       liquiditaet: ["bmw-320d", "audi-a4", "vw-golf"], qualitaet: ["vw-golf", "audi-a4", "bmw-320d"], leer: ["vw-golf", "audi-a4", "bmw-320d"],
                       kosten: ["vw-golf", "bmw-320d", "audi-a4"], modell: ["audi-a4", "bmw-320d", "vw-golf"] };
    for (const [sort, reihe] of Object.entries(erwartet)) {
      await waehlen("berichte-sort", sort);
      expect(reihenfolge()).toEqual(reihe);
    }
    expect(berichtSortieren([{ label: "B", delta_pct: null }, { label: "A", delta_pct: -1 }], "rueckgang").map((x) => x.label)).toEqual(["A", "B"]);
    expect(periodeText("2028-02-26", "2028-02-29")).toBe("26.02.–29.02.2028");
  });

  it("Typ-Umschalter lädt die Perioden des Typs; Berichte jetzt erstellen (nur fällige) lädt neu", async () => {
    await starten("/admin/markt/berichte");
    await klick("berichte-typ-FIVE_DAY");
    expect(netz.gets.filter((g) => g.url === "/admin/market/reports/periods").at(-1).params).toEqual({ typ: "FIVE_DAY" });
    expect(netz.gets.filter((g) => g.url === "/admin/market/reports").at(-1).params.typ).toBe("FIVE_DAY");
    await waehlen("berichte-periode", "2028-01-01|2028-01-31");
    expect(netz.gets.filter((g) => g.url === "/admin/market/reports").at(-1).params).toEqual({ typ: "FIVE_DAY", von: "2028-01-01", bis: "2028-01-31" });
    const vorher = netz.gets.filter((g) => g.url === "/admin/market/reports/periods").length;
    await klick("berichte-erstellen");
    expect(netz.posts).toEqual(["/admin/market/reports/finalize"]);
    expect(netz.gets.filter((g) => g.url === "/admin/market/reports/periods").length).toBe(vorher + 1);
    const { toast } = await import("sonner");
    expect(toast.success).toHaveBeenCalledWith(expect.stringContaining("4 Bericht(e) eingefroren"));
  });

  it("ohne finalen Bericht: Hinweis statt Tabelle; Ladefehler sichtbar", async () => {
    netz.ohneFinal = true;
    await starten("/admin/markt/berichte");
    expect(el("berichte-uebersicht").textContent).toContain("Keine Berichte");
    expect(netz.gets.some((g) => g.url === "/admin/market/reports")).toBe(false);
    await act(async () => { wurzel.unmount(); }); wurzel = null; behaelter?.remove();
    netz.fehler = "Nur Admins";
    await starten("/admin/markt/berichte");
    expect(el("berichte-fehler").textContent).toContain("Nur Admins");
  });
});

describe("Admin Berichte — Modellbericht", () => {
  it("finaler Monatsbericht: Kennzahlen, Farben, Tagestabelle mit Lücken, Bewegung, Blöcke, Segmente, Hot Deals, Fassungen", async () => {
    await starten("/admin/markt/berichte/bmw-320d?typ=MONTHLY&von=2028-02-01&bis=2028-02-29");
    expect(netz.gets.find((g) => g.url === "/admin/market/reports/model/bmw-320d").params).toEqual({ typ: "MONTHLY", von: "2028-02-01", bis: "2028-02-29" });
    expect(el("bericht-titel").textContent).toBe("BMW 320d");
    expect(el("bericht-status").textContent).toContain("final · eingefroren");
    expect(el("bericht-periode").value).toBe("MONTHLY|2028-02-01|2028-02-29");
    const kz = el("bericht-kennzahlen").textContent;
    for (const t of ["20.000 €", "19.800 €", "19.900 € / 19.950 €", "19.400 €", "Gleiche Inserate", "−150 €", "1 / 2", "5 / 1", "7", "2 / 1", "Qualität gut", "voll / mittel"]) {
      expect(kz).toContain(t);
    }
    expect(el("bericht-delta").textContent).toContain("−200 € (-1 %)");
    expect(el("bericht-delta").innerHTML).toContain("--st-gruen");
    expect(el("bericht-abdeckung").textContent).toContain("27 / 29 Tage");
    expect(el("bericht-abdeckung").textContent).toContain("Confidence mittel: wenige verschiedene Inserate");
    expect(el("bericht-kosten").textContent).toContain("0.28 $");
    expect(el("bericht-kosten").textContent).toContain("je Hot Deal 0.14 $");
    expect(el("bericht-hinweise").textContent).toContain("Fassungswechsel");
    // Tagestabelle: fallender Tag grün, steigender rot, Lücke ohne Wert, nur ungültige Läufe markiert
    expect(el("bericht-tag-2028-02-02").querySelectorAll("td")[2].getAttribute("style")).toContain("--st-gruen");
    expect(el("bericht-tag-2028-02-06").querySelectorAll("td")[2].getAttribute("style")).toContain("--st-rot");
    expect(el("bericht-luecke-2028-02-03").textContent).toBe("keine Daten");
    expect(el("bericht-tag-2028-02-03").textContent).toContain("—");
    expect(el("bericht-tag-2028-02-04").textContent).toContain("nur ungültige Läufe");
    expect(el("bericht-luecke-2028-02-04")).toBeNull();
    // Fallen / Steigen / Stabil
    const bw = el("bericht-bewegung").textContent;
    for (const t of ["6 (23,1 %)", "3 (11,5 %)", "17 (65,4 %)", "26 vergleichbare Tage", "−1.150 € / +950 €", "netto −200 €", "5 / 3 Tage", "0,62 %"]) expect(bw).toContain(t);
    // Blöcke + Blockfilter (Diagramm und Tabelle zeigen nur den Block)
    expect(el("bericht-block-2028-02-01").textContent).toContain("fallend");
    expect(el("bericht-block-2028-02-06").textContent).toContain("stabil");
    expect(behaelter.querySelectorAll("[data-testid^='bericht-tag-']").length).toBe(5);
    await klick("bericht-blockwahl-2028-02-06");
    expect(el("bericht-blockwahl-2028-02-06").getAttribute("aria-pressed")).toBe("true");
    expect([...behaelter.querySelectorAll("[data-testid^='bericht-tag-']")].map((e) => e.getAttribute("data-testid"))).toEqual(["bericht-tag-2028-02-06"]);
    await klick("bericht-block-alle");
    expect(behaelter.querySelectorAll("[data-testid^='bericht-tag-']").length).toBe(5);
    // zweite Darstellung umschalten
    expect(el("bericht-zweite-listings").getAttribute("aria-pressed")).toBe("true");
    await klick("bericht-zweite-preis");
    expect(el("bericht-zweite-preis").getAttribute("aria-pressed")).toBe("true");
    expect(el("bericht-verlauf").querySelectorAll("[data-chart]").length).toBeGreaterThanOrEqual(2);
    // Segmentdetail, Hot Deals, frühere Fassungen
    const seg = el("bericht-segment-bmw-320d:v2:2020:20000-40000").textContent;
    for (const t of ["EZ 2020 · 20–40k km", "19.800 €", "−200 € (-1 %)", "27/29", "dünn", "gut", "0.28 $"]) expect(seg).toContain(t);
    expect(el("bericht-hotdeal-h1").textContent).toContain("18.000 € statt 20.000 € (10 % günstiger)");
    expect(el("bericht-hotdeal-h1").textContent).toContain("privat");
    expect(el("bericht-hotdeals-link").getAttribute("href")).toBe("/admin/markt/hot-deals");
    expect(el("bericht-fassungen").textContent).toContain("Fassung v1: 01.02.–03.02.2028 · 3 gültige Tage");
  });

  it("ohne Periode in der Adresse: neuester finaler Bericht; Wechsel auf die laufende Periode zeigt 'vorläufig'", async () => {
    await starten("/admin/markt/berichte/bmw-320d");
    expect(ort.search).toBe("?typ=MONTHLY&von=2028-02-01&bis=2028-02-29");
    expect(el("bericht-status").textContent).toContain("final");
    await waehlen("bericht-periode", "FIVE_DAY|2028-03-01|2028-03-05");
    expect(netz.gets.filter((g) => g.url === "/admin/market/reports/model/bmw-320d").at(-1).params).toEqual({ typ: "FIVE_DAY", von: "2028-03-01", bis: "2028-03-05" });
    expect(el("bericht-status").textContent).toContain("vorläufig");
    expect(el("bericht-hinweise").textContent).toContain("vorläufig");
    expect(el("bericht-bloecke")).toBeNull();
    expect(el("bericht-fassungen")).toBeNull();
  });

  it("ohne finalen Bericht nimmt die Seite den laufenden Monat (nicht den oft noch leeren 5-Tage-Block); Ladefehler zeigt Rückweg", async () => {
    netz.ohneFinal = true;
    await starten("/admin/markt/berichte/bmw-320d");
    expect(ort.search).toBe("?typ=MONTHLY&von=2028-03-01&bis=2028-03-31");
    expect(el("bericht-status").textContent).toContain("vorläufig");
    await act(async () => { wurzel.unmount(); }); wurzel = null; behaelter?.remove();
    netz.fehler = "Serverfehler";
    await starten("/admin/markt/berichte/bmw-320d?typ=MONTHLY&von=2028-02-01&bis=2028-02-29");
    expect(el("bericht-fehler").textContent).toContain("Serverfehler");
    expect(el("bericht-fehler").querySelector("a").getAttribute("href")).toBe("/admin/markt/berichte");
  });

  it("Periode ohne Tagesdaten (404): leerer Zustand statt Fehlerkarte, Periodenwahl bleibt; Fehler verschwindet beim Wechsel", async () => {
    netz.modellFehler = { status: 404 };
    await starten("/admin/markt/berichte/bmw-320d?typ=FIVE_DAY&von=2028-03-01&bis=2028-03-05");
    expect(el("bericht-leer").textContent).toContain("Keine Tagesdaten in dieser Periode");
    expect(el("bericht-fehler")).toBeNull();
    expect(el("bericht-periode")).not.toBeNull();
    netz.modellFehler = null;
    await waehlen("bericht-periode", "MONTHLY|2028-03-01|2028-03-31");
    expect(el("bericht-leer")).toBeNull();
    expect(el("bericht-status").textContent).toContain("vorläufig");
    // anderer Fehler: rote Karte, aber die Periodenwahl bleibt — ein Wechsel lädt neu und räumt den Fehler weg
    netz.modellFehler = { status: 500, message: "Serverfehler" };
    await waehlen("bericht-periode", "FIVE_DAY|2028-03-01|2028-03-05");
    expect(el("bericht-fehler").textContent).toContain("Serverfehler");
    expect(el("bericht-periode")).not.toBeNull();
    netz.modellFehler = null;
    await waehlen("bericht-periode", "MONTHLY|2028-02-01|2028-02-29");
    expect(el("bericht-fehler")).toBeNull();
    expect(el("bericht-status").textContent).toContain("final");
  });

  it("vorläufiger Monat: künftige Tage sind offen (keine Lücke), Teilabdeckung ist eine Diagrammlücke, Segment-Tage sichtbar", async () => {
    await starten("/admin/markt/berichte/bmw-320d?typ=MONTHLY&von=2028-03-01&bis=2028-03-31");
    expect(el("bericht-status").textContent).toContain("vorläufig");
    expect([...behaelter.querySelectorAll("[data-testid^='bericht-tag-']")].map((e) => e.getAttribute("data-testid"))).toEqual(["bericht-tag-2028-03-01", "bericht-tag-2028-03-02"]);
    expect(el("bericht-tagestabelle-titel").textContent).toContain("2 Tage · 2 noch offen");
    expect(el("bericht-luecke-2028-03-03")).toBeNull();
    expect(el("bericht-abdeckung").textContent).toContain("2 / 2 Tage");
    expect(el("bericht-abdeckung").textContent).toContain("noch offen");
    expect(el("bericht-block-offen-2028-03-06").textContent).toBe("noch offen");
    expect(el("bericht-block-offen-2028-03-01")).toBeNull();
    expect(el("bericht-segmentabdeckung").textContent).toContain("3/4 Segment-Tage");
    // Teilabdeckung: Zeile markiert, im Diagramm eine Lücke (kein Scheineinbruch auf 12.000 €)
    expect(el("bericht-teil-2028-03-02").textContent).toContain("Teilabdeckung (1 Segm.");
    const reihe = netz.diagramme.at(-1);
    expect(reihe.find((x) => x.date === "2028-03-02")).toMatchObject({ median: null, min: null, p25: null, p75: null });
    expect(reihe.find((x) => x.date === "2028-03-01").median).toBe(20000);
  });

  it("Farbe von Stichprobe und Segmentdetail folgt der Stabilitätszone (±0,5 %), nicht nur dem Vorzeichen", async () => {
    netz.bericht = { ...BERICHT, kennzahlen: { ...BERICHT.kennzahlen, sample_market_change_eur: -40, sample_market_change_pct: -0.2, kosten_fassung_usd: 0.1 },
                     segmente: [{ ...BERICHT.segmente[0], segment_id: "s-klein", delta_eur: -60, delta_pct: -0.24 },
                                { ...BERICHT.segmente[0], segment_id: "s-plus", delta_eur: 20, delta_pct: 0.1 },
                                { ...BERICHT.segmente[0], segment_id: "s-fallend", delta_eur: -200, delta_pct: -1 },
                                { ...BERICHT.segmente[0], segment_id: "s-steigend", delta_eur: 300, delta_pct: 1.5 }] };
    await starten("/admin/markt/berichte/bmw-320d?typ=MONTHLY&von=2028-02-01&bis=2028-02-29");
    expect(el("bericht-stichprobe").innerHTML).toContain("--text-secondary");
    expect(el("bericht-stichprobe").innerHTML).not.toContain("--st-gruen");
    expect(el("bericht-segment-delta-s-klein").getAttribute("style")).toContain("--text-secondary");
    expect(el("bericht-segment-delta-s-plus").getAttribute("style")).toContain("--text-secondary");
    expect(el("bericht-segment-delta-s-fallend").getAttribute("style")).toContain("--st-gruen");
    expect(el("bericht-segment-delta-s-steigend").getAttribute("style")).toContain("--st-rot");
    expect(el("bericht-kosten").textContent).toContain("Basis: Kosten der gerechneten Fassung 0.10 $");
    expect([richtungAusPct(-0.5), richtungAusPct(-0.51), richtungAusPct(0.6), richtungAusPct(null), richtungAusPct(1, 2)])
      .toEqual(["STABLE", "FALLING", "RISING", "UNKNOWN", "STABLE"]);
  });

  it("helle Ansicht: aktiver Chip ohne feste weiße Schrift, Tooltips und Achsen über Tokens", async () => {
    await starten("/admin/markt/berichte/bmw-320d?typ=MONTHLY&von=2028-02-01&bis=2028-02-29");
    await klick("bericht-blockwahl-2028-02-06");
    const stil = el("bericht-blockwahl-2028-02-06").getAttribute("style");
    expect(stil).toContain("color: var(--text-primary)");
    expect(stil).not.toMatch(/#fff|rgb\(255, 255, 255\)/);
    expect(el("bericht-zweite-listings").getAttribute("style")).toContain("color: var(--text-primary)");
    expect(el("bericht-zweite-neue").getAttribute("style")).toContain("color: var(--text-secondary)");
    expect(netz.tooltips.length).toBeGreaterThanOrEqual(2);
    for (const t of netz.tooltips) {
      expect(t.contentStyle).toMatchObject({ background: "var(--bg-surface)", color: "var(--text-primary)" });
      expect(t.labelStyle).toMatchObject({ color: "var(--text-primary)" });
    }
  });
});

describe("Admin Berichte — Übersicht lädt nach Aktualisieren/Erstellen neu", () => {
  it("gleiche neueste Periode: Aktualisieren und Berichte jetzt erstellen laden die Tabelle trotzdem neu", async () => {
    await starten("/admin/markt/berichte");
    const zaehlen = () => netz.gets.filter((g) => g.url === "/admin/market/reports").length;
    expect(zaehlen()).toBe(1);
    await klick("berichte-aktualisieren");
    expect(zaehlen()).toBe(2);
    expect(netz.gets.filter((g) => g.url === "/admin/market/reports").at(-1).params).toEqual({ typ: "MONTHLY", von: "2028-02-01", bis: "2028-02-29" });
    await klick("berichte-erstellen");
    expect(zaehlen()).toBe(3);
  });
});

describe("Admin Berichte — Prüfung Runde 2 (Korbwert, laufender Tag, Rechenregel)", () => {
  it("Schema 2: Medianlinie = Korbwert (Teilabdeckung verkettet statt Lücke), heute noch laufende Segmente markiert", async () => {
    const tage = [
      { ...TAGE[0], date: "2028-03-01", median_korb: 20000, teilabdeckung: false, fehlende_segmente: 0, ausstehende_segmente: 0, offen: false },
      { ...TAGE[1], date: "2028-03-02", median: 12000, median_korb: 19950, p25: 11800, p75: 12200, teilabdeckung: true, fehlende_segmente: 1,
        ausstehende_segmente: 0, offen: false },
      { ...TAGE[1], date: "2028-03-03", median: 13000, median_korb: 19900, p25: 12800, p75: 13200, teilabdeckung: false, fehlende_segmente: 0,
        ausstehende_segmente: 21, offen: false },
      { ...OFFEN, date: "2028-03-04", median_korb: null, ausstehende_segmente: 0 },
    ];
    netz.bericht = { ...vorlaeufig({ typ: "MONTHLY", von: "2028-03-01", bis: "2028-03-31" }), offen_ab: "2028-03-04", tage };
    await starten("/admin/markt/berichte/bmw-320d?typ=MONTHLY&von=2028-03-01&bis=2028-03-31");
    const reihe = netz.diagramme.at(-1);
    expect(reihe.find((x) => x.date === "2028-03-01")).toMatchObject({ median: 20000, p25: 19800 });
    // Teilabdeckung: kein Scheineinbruch auf 12.000 € und keine Lücke — der verkettete Korbwert; das echte Minimum bleibt
    expect(reihe.find((x) => x.date === "2028-03-02")).toMatchObject({ median: 19950, min: 19400, p25: null, p75: null });
    expect(reihe.find((x) => x.date === "2028-03-03")).toMatchObject({ median: 19900, p25: null, p75: null });
    expect(reihe.find((x) => x.date === "2028-03-04").median).toBeNull();
    expect(el("bericht-teil-2028-03-02").getAttribute("title")).toContain("verkettete Korbwert 19.950 €");
    expect(el("bericht-ausstehend-2028-03-03").textContent).toContain("läuft noch (21 Segm. ausstehend)");
    expect(el("bericht-teil-2028-03-03")).toBeNull();
    expect(el("bericht-tag-2028-03-03").querySelectorAll("td")[1].getAttribute("style")).toContain("--text-dim");
    expect(el("bericht-tag-2028-03-01").querySelectorAll("td")[1].getAttribute("style")).toBeNull();
    expect(el("bericht-legende").textContent).toContain("verkettet");
    expect(el("bericht-schema-alt")).toBeNull();
    // reine Funktion: ältere Berichte ohne Korbwert — Teilabdeckung bleibt eine ganze Lücke
    expect(berichtDiagrammPunkt({ median: 12000, min: 11000, p25: 1, p75: 2, teilabdeckung: true })).toMatchObject({ median: null, min: null, p25: null, p75: null });
    expect(berichtDiagrammPunkt({ median: 12000, min: 11000, teilabdeckung: false })).toMatchObject({ median: 12000, min: 11000 });
  });

  it("Schema 1: eingefrorener Bericht nach älterer Rechenregel ist gekennzeichnet (Kopf, Periodenwahl, Übersicht)", async () => {
    netz.schemaAlt = true;
    netz.bericht = { ...BERICHT, schema: 1 };
    await starten("/admin/markt/berichte/bmw-320d?typ=MONTHLY&von=2028-02-01&bis=2028-02-29");
    expect(el("bericht-schema-alt").textContent).toBe("nach älterer Rechenregel erstellt");
    expect(el("bericht-schema-hinweis").textContent).toContain("Schema 1");
    const option = [...el("bericht-periode").querySelectorAll("option")].find((o) => o.value === "MONTHLY|2028-02-01|2028-02-29");
    expect(option.textContent).toContain("final · nach älterer Rechenregel erstellt");
    expect(el("bericht-legende").textContent).toContain("bleiben Lücken");
    await act(async () => { wurzel.unmount(); }); wurzel = null; behaelter?.remove();
    netz.schemaAlt = false; netz.bericht = null;
    await starten("/admin/markt/berichte/bmw-320d?typ=MONTHLY&von=2028-02-01&bis=2028-02-29");
    expect(el("bericht-schema-alt")).toBeNull();
    expect(el("bericht-schema-hinweis")).toBeNull();
    expect([...el("bericht-periode").querySelectorAll("option")].find((o) => o.value === "MONTHLY|2028-02-01|2028-02-29").textContent).not.toContain("älterer");
    await act(async () => { wurzel.unmount(); }); wurzel = null; behaelter?.remove();
    // Übersicht: nur die Zeile mit Schema 1 ist markiert, die Fußnote zählt sie
    netz.schemaAlt = true;
    await starten("/admin/markt/berichte");
    expect(el("bericht-schema-alt-vw-golf").textContent).toBe("ältere Rechenregel");
    expect(el("bericht-schema-alt-bmw-320d")).toBeNull();
    expect(el("berichte-schema-hinweis").textContent).toContain("1 Bericht(e) nach älterer Rechenregel erstellt");
    expect([berichtAltesSchema({ schema: 1 }), berichtAltesSchema({}), berichtAltesSchema({ schema: 2 }), berichtAltesSchema(null)])
      .toEqual([true, true, false, false]);
  });
});
