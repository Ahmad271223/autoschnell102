/**
 * Admin → Marktanalyse → Segment-Optimierung (Master-Auftrag Phase F, 27.09.2026): Modus (OBSERVE, FULL_AUTO gesperrt),
 * Frequenz-Zuordnung bearbeiten, Health je Suchauftrag und Segment mit Farben nach Abschnitt 47 (nur CSS-Variablen),
 * Vorschläge mit Annehmen/Ablehnen/Übernehmen (Übernehmen nur Zusammenlegen/Aufteilen, mit Rückfrage), Ersparnis,
 * „Health jetzt berechnen“; Health-Badge auf der Modellseite. Phase G: Modus-Schalter OBSERVE ↔ SAFE_AUTO mit Rückfrage,
 * Protokoll der SAFE_AUTO-Änderungen mit Rücknahme, Wirkung je Segment.
 */
import { act, createElement as h } from "react";
import { createRoot } from "react-dom/client";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

globalThis.IS_REACT_ACT_ENVIRONMENT = true;

const netz = vi.hoisted(() => ({ gets: [], posts: [], puts: [], superAdmin: true, fehler: null, warten: {}, postWarten: null, viele: false,
  detailFehler: 0, modellHealth: null, uebersicht: null, segmentWirkung: null }));
vi.mock("sonner", () => ({ toast: { success: vi.fn(), error: vi.fn() } }));
vi.mock("recharts", () => {
  const Leer = ({ children }) => h("div", { "data-chart": "1" }, children);
  return { ResponsiveContainer: Leer, ComposedChart: Leer, BarChart: Leer, Line: () => null, Area: () => null, Bar: () => null,
           XAxis: () => null, YAxis: () => null, Tooltip: () => null, CartesianGrid: () => null };
});
const STANDARD = { stufen: [{ ab: 75, crawls_per_day: 2, intervall_tage: 1, intervall_tage_bis: null }, { ab: 45, crawls_per_day: 1, intervall_tage: 1, intervall_tage_bis: null },
  { ab: 20, crawls_per_day: 1, intervall_tage: 2, intervall_tage_bis: null }, { ab: 0, crawls_per_day: 1, intervall_tage: 3, intervall_tage_bis: 7 }], empty_nachpruefung_tage: 7 };
const UEBERSICHT = {
  modus: "OBSERVE", frequenz: STANDARD, frequenz_standard: STANDARD,
  modi: [{ modus: "OBSERVE", text: "Beobachten — nur Empfehlungen", gesperrt: false },
         { modus: "SAFE_AUTO", text: "Sicher automatisch — Frequenz senken, EMPTY pausieren, HOT vorziehen", gesperrt: false },
         { modus: "FULL_AUTO", text: "Voll automatisch (gesperrt)", gesperrt: true }],
  ersparnis_safe_auto_usd: 0.26, safe_auto_aktiv: 1,
  schwellen: { fenster_tage: 30, min_laeufe_empty: 14, empty_anteil: 0.8, thin_max_zeilen: 2, hot_ab_score: 75, healthy_ab_score: 45,
               unstable_ungueltig_anteil: 0.3, unstable_volatilitaet_pct: 20, stale_puffer_tage: 2, merge_min_laeufe: 14, split_voll_anteil: 0.9, split_streuung_pct: 15 },
  stand: { letzter_lauf_at: "2026-10-01T08:30:00Z", quelle: "taeglich", fehler: 0 },
  zaehler: { HOT: 1, HEALTHY: 3, THIN: 2, EMPTY: 1, UNKNOWN: 4 },
  modelle: [{ model_id: "bmw-320d", label: "BMW 320d", health: "HOT", version: 2, tag: "2026-10-01", zaehler: { HOT: 1, HEALTHY: 3 }, activity_score_mittel: 71.5,
              vorschlaege_offen: 2, ersparnis_offen_usd: 1.23 },
            { model_id: "vw-golf", label: "VW Golf", health: "THIN", version: 1, tag: "2026-10-01", zaehler: { THIN: 2, EMPTY: 1, UNKNOWN: 4 }, activity_score_mittel: 8,
              vorschlaege_offen: 1, ersparnis_offen_usd: 0.61 }],
  vorschlaege_je_status: { PROPOSED: 3, ACCEPTED: 1 }, ersparnis_offen_usd: 1.84,
  hinweis: "Health und Vorschläge entstehen nur aus gespeicherten Tageswerten — getrennt von der technischen Datenqualität.",
};
const VORSCHLAEGE = [
  { id: "v1", typ: "MERGE_KM_BUCKETS", status: "PROPOSED", model_id: "vw-golf", label: "VW Golf", version: 1, km_label: "20–40k km + 40–60k km → 20–60k km",
    reason: "km-Bereiche 20–40k km und 40–60k km sind in allen 2 EZ-Jahren dünn", confidence: "MEDIUM", estimated_monthly_saving_usd: 0.61,
    evidence: { days: 20, avg_rows: { a: 1, b: 1 }, empty_rate: { a: 0.25, b: 0.25 }, je_ez: [{}, {}] } },
  { id: "v2", typ: "SPLIT_KM_BUCKET", status: "ACCEPTED", model_id: "vw-golf", label: "VW Golf", version: 1, km_label: "60–130k km → 60–95k km + 95–130k km",
    reason: "ständig voll, große Preisstreuung", confidence: "MEDIUM", estimated_monthly_saving_usd: -0.61,
    evidence: { days: 20, avg_rows: 5, streuung_median_pct: 20, je_ez: [{}] } },
  { id: "v3", typ: "REDUCE_FREQUENCY", status: "PROPOSED", model_id: "bmw-320d", segment_id: "bmw-320d:v2:2020:20000-40000", label: "BMW 320d", version: 2,
    ez_label: "EZ 2020", km_label: "20–40k km", health: "NORMAL", reason: "Activity Score 25 (NORMAL) — alle 2 Tage statt täglich reicht", confidence: "HIGH",
    estimated_monthly_saving_usd: 0.15, evidence: { days: 28, valid_runs: 28, avg_rows: 5, empty_rate: 0, activity_score: 25 } },
];
// Prüfbefunde F/G: angewendet durch SAFE_AUTO (Rücknahme auch hier) und abgelehnt (Ablehnung aufheben)
const EXTRA_VORSCHLAEGE = [
  { id: "v4", typ: "PAUSE_EMPTY", status: "APPLIED", angewendet_von: "safe_auto", aenderung_id: "a1", model_id: "bmw-320d", segment_id: "bmw-320d:v2:2020:3",
    label: "BMW 320d", version: 2, ez_label: "EZ 2020", km_label: "30–40k km", health: "EMPTY", reason: "EMPTY — pausieren", confidence: "HIGH",
    estimated_monthly_saving_usd: 0, laeufe_frei_monat: 10.9, ersparnis_budget_grenze: true, evidence: { days: 30, valid_runs: 4 } },
  { id: "v5", typ: "REDUCE_FREQUENCY", status: "REJECTED", entscheidung_grund: "Familie abgelehnt", model_id: "bmw-320d", segment_id: "bmw-320d:v2:2020:6",
    label: "BMW 320d", version: 2, ez_label: "EZ 2020", km_label: "60–70k km", reason: "alle 4 Tage statt täglich", confidence: "MEDIUM",
    estimated_monthly_saving_usd: 0.2, evidence: {} },
];
const SEGMENTE = [["HOT", "var(--st-lila)"], ["HEALTHY", "var(--st-gruen)"], ["THIN", "var(--st-gelb)"], ["EMPTY", "var(--st-grau)"],
  ["UNSTABLE", "var(--st-rot)"], ["STALE", "var(--st-amber)"], ["NORMAL", "var(--text-secondary)"], ["UNKNOWN", "var(--text-dim)"]].map(([st], i) => ({
  segment_id: `bmw-320d:v2:2020:${i}`, ez_label: "EZ 2020", km_label: `${i}0–${i + 1}0k km`, health: st, health_text: `Grund ${st}`, activity_score: 90 - i * 10,
  recommended_frequency_days: st === "EMPTY" ? 7 : st === "HOT" ? 0.5 : 1, recommended_pause: st === "EMPTY", valid_runs: 20, invalid_runs: st === "UNSTABLE" ? 9 : 0,
  avg_valid_rows: 4.2, empty_rate: st === "EMPTY" ? 1 : 0, data_quality: st === "UNSTABLE" ? "POOR" : "GOOD", confidence: "MEDIUM", enabled: true,
  safe_auto_wirkung: st === "EMPTY" ? { intervall_tage: 7, pausiert: true } : st === "HOT" ? { hot: true, intervall_tage: 1 } : null }));
// Phase G: Protokoll der SAFE_AUTO-Änderungen
const AENDERUNGEN = [
  { id: "a1", segment_id: "bmw-320d:v2:2020:3", model_id: "bmw-320d", label: "BMW 320d", ez_label: "EZ 2020", km_label: "30–40k km", typ: "PAUSE_EMPTY", wer: "safe_auto",
    alt: { intervall_tage: 1, crawls_per_day: 1, prioritaet: "normal", pausiert: false }, neu: { intervall_tage: 7, crawls_per_day: 1, pausiert: true },
    grund: "EZ 2020 · 30–40k km: 20 gültige Läufe, davon 100 % ohne Treffer (EMPTY)", status: "aktiv", at: "2026-10-01T09:00:00Z", estimated_monthly_saving_usd: 0.26 },
  { id: "a2", segment_id: "bmw-320d:v2:2020:6", model_id: "bmw-320d", label: "BMW 320d", ez_label: "EZ 2020", km_label: "60–70k km", typ: "REDUCE_FREQUENCY", wer: "safe_auto",
    alt: { intervall_tage: 1, crawls_per_day: 1, prioritaet: "normal", pausiert: false }, neu: { intervall_tage: 2, crawls_per_day: 1 },
    grund: "Activity Score 25 (NORMAL) — alle 2 Tage statt täglich reicht", status: "zurueckgenommen", at: "2026-09-30T09:00:00Z", beendet_at: "2026-10-01T10:00:00Z",
    beendet_von: "sa", beendet_grund: "Rücknahme durch den Betreiber", estimated_monthly_saving_usd: 0.15 },
  { id: "a3", segment_id: "bmw-320d:v2:2020:0", model_id: "bmw-320d", label: "BMW 320d", ez_label: "EZ 2020", km_label: "0–10k km", typ: "PRIORITIZE_HOT", wer: "safe_auto",
    alt: { intervall_tage: 1, crawls_per_day: 1, prioritaet: "normal", pausiert: false }, neu: { prioritaet: "HOT" }, grund: "sehr aktiver Markt",
    status: "aufgehoben", at: "2026-09-29T09:00:00Z", beendet_at: "2026-09-30T09:00:00Z", beendet_von: "safe_auto", beendet_grund: "Modus OBSERVE", estimated_monthly_saving_usd: 0 },
];
const FARBEN = Object.fromEntries([["HOT", "var(--st-lila)"], ["HEALTHY", "var(--st-gruen)"], ["THIN", "var(--st-gelb)"], ["EMPTY", "var(--st-grau)"],
  ["UNSTABLE", "var(--st-rot)"], ["STALE", "var(--st-amber)"], ["NORMAL", "var(--text-secondary)"], ["UNKNOWN", "var(--text-dim)"]]);

vi.mock("@/lib/api", () => ({
  errMsg: (e, s) => e?.message || s,
  api: {
    get: vi.fn(async (url, opts) => {
      netz.gets.push({ url, params: opts?.params });
      const warte = netz.warten[`${url}|${opts?.params?.status ?? ""}`];
      if (warte) await warte;
      if (url === "/admin/market/optimierung") { if (netz.fehler) throw new Error(netz.fehler); return { data: netz.uebersicht || UEBERSICHT }; }
      if (url === "/admin/market/optimierung/vorschlaege") {
        const p = opts?.params || {};
        if (netz.viele) {
          const alle = Array.from({ length: 150 }, (_, i) => ({ ...VORSCHLAEGE[2], id: `n${i}`, segment_id: `bmw-320d:v2:2020:${i}` }));
          const teil = alle.slice(p.offset || 0, (p.offset || 0) + p.limit);
          return { data: { vorschlaege: teil, anzahl: teil.length, gesamt: 150, weitere: (p.offset || 0) + teil.length < 150 } };
        }
        const liste = [...VORSCHLAEGE, ...EXTRA_VORSCHLAEGE].filter((v) => (!p.typ || v.typ === p.typ) && (!p.model_id || v.model_id === p.model_id)
          && (p.status === "alle" ? true : p.status === "offen" ? ["PROPOSED", "ACCEPTED"].includes(v.status) : v.status === p.status));
        return { data: { vorschlaege: liste, anzahl: liste.length, gesamt: liste.length, weitere: false } };
      }
      if (url === "/admin/market/optimierung/aenderungen") {
        const st = opts?.params?.status;
        const liste = AENDERUNGEN.filter((a) => (!st || st === "alle" || a.status === st) && (!opts?.params?.model_id || a.model_id === opts.params.model_id));
        return { data: { aenderungen: liste, anzahl: liste.length, gesamt: liste.length, weitere: false } };
      }
      if (url === "/admin/market/health/models/bmw-320d") {
        if (netz.detailFehler > 0) { netz.detailFehler -= 1; throw new Error("Segmente: Serverfehler"); }
        if (netz.modellHealth) return { data: netz.modellHealth };
        const segmente = netz.segmentWirkung ? SEGMENTE.map((s, i) => (i === 3 ? { ...s, safe_auto_wirkung: netz.segmentWirkung } : s)) : SEGMENTE;
        return { data: { model_id: "bmw-320d", modell: UEBERSICHT.modelle[0], segmente, vorschlaege: [], aktuell: true } };
      }
      if (url === "/admin/market/models/bmw-320d") return { data: { id: "bmw-320d", label: "BMW 320d", fuel: "DIESEL", version: 2, segmente: [
        { id: "bmw-320d:v2:2020:0", model_id: "bmw-320d", min_km: 0, max_km: 10000, km_label: "0–10k km", year_from: 2020, ez_label: "EZ 2020", enabled: true, version: 2 },
        { id: "bmw-320d:v2:2020:2", model_id: "bmw-320d", min_km: 20000, max_km: 30000, km_label: "20–30k km", year_from: 2020, ez_label: "EZ 2020", enabled: true, version: 2 }] } };
      if (url.endsWith("/summary")) return { data: { stats: null, letzter_job: null, historisch: false } };
      if (url.endsWith("/listings")) return { data: { listings: [] } };
      if (url.endsWith("/history")) return { data: { reihe: [], wochen: [], auswertung: {} } };
      if (url.endsWith("/private-deals")) return { data: { top3: [], historie: [] } };
      return { data: {} };
    }),
    post: vi.fn(async (url) => {
      netz.posts.push(url);
      if (netz.postWarten) await netz.postWarten;
      if (url.endsWith("/uebernehmen")) return { data: { ok: true, modell: { version: 2 } } };
      return { data: { ok: true, segmente: 12, modelle: 2, vorschlaege_neu: 1 } };
    }),
    put: vi.fn(async (url, body) => {
      netz.puts.push({ url, body });
      if (url.endsWith("/modus")) return { data: { ok: true, modus: body.modus, vorher: "OBSERVE", safe_auto: { angewendet: 2 }, aufgehoben: 0 } };
      return { data: { ok: true, frequenz: body } };
    }),
  },
}));
vi.mock("@/context/AuthContext", () => ({ useAuth: () => ({ user: { id: "sa", is_super_admin: netz.superAdmin, role: "admin" } }) }));

const { default: MarktOptimierung, stufenZuFormular, formularZuFrequenz } = await import("./MarktOptimierung");
const { default: MarktModell } = await import("./MarktModell");
const markt = await import("@/lib/markt");

let wurzel; let behaelter;
const el = (t) => behaelter.querySelector(`[data-testid="${t}"]`);
async function warten() { for (let i = 0; i < 8; i += 1) await act(async () => { await new Promise((r) => setTimeout(r, 0)); }); }
async function starten(pfad = "/admin/markt/optimierung") {
  behaelter = document.createElement("div"); document.body.appendChild(behaelter); wurzel = createRoot(behaelter);
  await act(async () => {
    wurzel.render(h(MemoryRouter, { initialEntries: [pfad] }, h(Routes, null,
      h(Route, { path: "/admin/markt/optimierung", element: h(MarktOptimierung) }), h(Route, { path: "/admin/markt/:modell", element: h(MarktModell) }))));
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
const vorschlagParams = () => netz.gets.filter((g) => g.url === "/admin/market/optimierung/vorschlaege").at(-1).params;
beforeEach(() => {
  netz.gets.length = 0; netz.posts.length = 0; netz.puts.length = 0; netz.superAdmin = true; netz.fehler = null; netz.warten = {};
  netz.postWarten = null; netz.viele = false; netz.detailFehler = 0; netz.modellHealth = null; netz.uebersicht = null; netz.segmentWirkung = null;
});
afterEach(async () => { if (wurzel) await act(async () => { wurzel.unmount(); }); wurzel = null; behaelter?.remove(); });

describe("Admin Segment-Optimierung", () => {
  it("Modus OBSERVE aktiv, FULL_AUTO gesperrt, Hinweis, Stand, Schwellen, Zähler und Ersparnis", async () => {
    await starten();
    expect(el("opt-modus").textContent).toContain("Modus: Beobachten (nur Empfehlungen)");
    expect(el("opt-modus-OBSERVE").textContent).toContain("OBSERVE · aktiv");
    expect(el("opt-modus-FULL_AUTO").textContent).toContain("gesperrt");
    expect(el("opt-modus-FULL_AUTO").querySelector("svg")).toBeTruthy();
    expect(el("opt-hinweis").textContent).toContain("getrennt von der technischen Datenqualität");
    expect(el("opt-stand").textContent).toContain("täglich");
    expect(el("opt-schwellen").textContent).toContain("EMPTY ab 14 gültigen Läufen mit ≥ 80 % ohne Treffer");
    expect(el("opt-zaehler").textContent).toBe("1 HOT3 HEALTHY2 THIN1 EMPTY4 offen");
    expect(el("opt-ersparnis").textContent).toContain("−1,84 $/Monat");
    expect(el("opt-uebersicht").textContent).toContain("Offene Vorschläge4");
    expect(vorschlagParams()).toEqual({ status: "offen", limit: 100, offset: 0 });
  });

  it("Health je Suchauftrag und Segment in den Farben aus Abschnitt 47 (nur CSS-Variablen, HOT hervorgehoben)", async () => {
    await starten();
    const hot = el("opt-modell-health-bmw-320d");
    expect(hot.style.color).toBe("var(--st-lila)");
    expect(hot.style.fontWeight).toBe("700");
    expect(el("opt-modell-health-vw-golf").style.color).toBe("var(--st-gelb)");
    expect(el("opt-modell-bmw-320d").textContent).toContain("71.5");
    await klick("opt-modell-segmente-bmw-320d");
    expect(netz.gets.some((g) => g.url === "/admin/market/health/models/bmw-320d")).toBe(true);
    for (const s of SEGMENTE) {
      const b = el(`opt-segment-health-${s.segment_id}`);
      expect(b.style.color).toBe(FARBEN[s.health]);
      expect(b.getAttribute("data-health")).toBe(s.health);
    }
    expect(el(`opt-segment-health-${SEGMENTE[1].segment_id}`).style.fontWeight).toBe("");
    const leer = el(`opt-segment-${SEGMENTE[3].segment_id}`).textContent;
    expect(leer).toContain("pausiert · Nachprüfung alle 7 Tage");
    expect(el(`opt-segment-${SEGMENTE[0].segment_id}`).textContent).toContain("2× täglich");
    expect(el(`opt-segment-${SEGMENTE[4].segment_id}`).textContent).toContain("(+9 ungültig)");
    expect(el(`opt-segment-${SEGMENTE[4].segment_id}`).textContent).toContain("schlecht");
    await klick("opt-modell-segmente-bmw-320d");
    expect(el("opt-modell-detail-bmw-320d")).toBeNull();
  });

  it("Vorschläge: Annehmen/Ablehnen; Übernehmen nur für Zusammenlegen/Aufteilen und nur nach Rückfrage", async () => {
    await starten();
    expect(el("opt-vorschlag-v1").textContent).toContain("km-Bereiche zusammenlegen");
    expect(el("opt-evidenz-v1").textContent).toContain("20 Tage · Ø 1 / 1 Autos je Lauf · leer 25 % / 25 % · 2 EZ-Jahre");
    expect(el("opt-ersparnis-v1").textContent).toBe("−0,61 $/Monat");
    expect(el("opt-ersparnis-v2").textContent).toBe("+0,61 $/Monat Mehrkosten");
    expect(el("opt-uebernehmen-v1")).toBeTruthy();
    expect(el("opt-uebernehmen-v2")).toBeTruthy();
    expect(el("opt-uebernehmen-v3")).toBeNull();
    expect(el("opt-annehmen-v2")).toBeNull();
    expect(el("opt-vorschlag-health-v3").style.color).toBe("var(--text-secondary)");
    await klick("opt-annehmen-v3");
    expect(netz.posts).toEqual(["/admin/market/optimierung/vorschlaege/v3/annehmen"]);
    await klick("opt-ablehnen-v2");
    expect(netz.posts.at(-1)).toBe("/admin/market/optimierung/vorschlaege/v2/ablehnen");
    const confirm = vi.spyOn(window, "confirm").mockReturnValueOnce(false);
    await klick("opt-uebernehmen-v1");
    expect(confirm).toHaveBeenCalledTimes(1);
    expect(confirm.mock.calls[0][0]).toContain("NEUE FASSUNG");
    expect(confirm.mock.calls[0][0]).toContain("PAUSIERT");
    expect(netz.posts.filter((u) => u.endsWith("/uebernehmen"))).toEqual([]);
    confirm.mockReturnValueOnce(true);
    await klick("opt-uebernehmen-v1");
    expect(netz.posts.at(-1)).toBe("/admin/market/optimierung/vorschlaege/v1/uebernehmen");
    confirm.mockRestore();
  });

  it("Filter Status/Typ als GET-Parameter", async () => {
    await starten();
    await tippen("opt-filter-status", "alle");
    expect(vorschlagParams()).toEqual({ status: "alle", limit: 100, offset: 0 });
    await tippen("opt-filter-typ", "SPLIT_KM_BUCKET");
    expect(vorschlagParams()).toEqual({ status: "alle", limit: 100, offset: 0, typ: "SPLIT_KM_BUCKET" });
    expect(el("opt-vorschlag-v1")).toBeNull();
    expect(el("opt-vorschlag-v2")).toBeTruthy();
  });

  it("Frequenz-Zuordnung bearbeiten: PUT mit Zahlen, ungültige Eingabe wird nie gesendet, Standard wiederherstellen", async () => {
    await starten();
    await tippen("opt-frequenz-ab-2", "25");
    await tippen("opt-frequenz-bis-3", "10");
    await tippen("opt-frequenz-nach", "14");
    await klick("opt-frequenz-speichern");
    expect(netz.puts).toHaveLength(1);
    expect(netz.puts[0].url).toBe("/admin/market/optimierung/frequenz");
    expect(netz.puts[0].body).toEqual({ stufen: [{ ab: 75, crawls_per_day: 2, intervall_tage: 1, intervall_tage_bis: null },
      { ab: 45, crawls_per_day: 1, intervall_tage: 1, intervall_tage_bis: null }, { ab: 25, crawls_per_day: 1, intervall_tage: 2, intervall_tage_bis: null },
      { ab: 0, crawls_per_day: 1, intervall_tage: 3, intervall_tage_bis: 10 }], empty_nachpruefung_tage: 14 });
    await tippen("opt-frequenz-n-1", "1,5");
    expect(el("opt-frequenz-ungueltig").textContent).toContain("nur ganze Zahlen");
    expect(el("opt-frequenz-speichern").disabled).toBe(true);
    await klick("opt-frequenz-standard");
    expect(el("opt-frequenz-ungueltig")).toBeNull();
    expect(el("opt-frequenz-ab-2").value).toBe("20");
    expect(el("opt-frequenz-nach").value).toBe("7");
  });

  it("Health jetzt berechnen (Super-Admin) lädt neu; ohne Super-Admin keine Schreibknöpfe; Ladefehler sichtbar", async () => {
    await starten();
    const vorher = netz.gets.filter((g) => g.url === "/admin/market/optimierung").length;
    await klick("opt-berechnen");
    expect(netz.posts).toEqual(["/admin/market/optimierung/berechnen"]);
    expect(netz.gets.filter((g) => g.url === "/admin/market/optimierung").length).toBe(vorher + 1);
    await act(async () => { wurzel.unmount(); }); wurzel = null; behaelter?.remove();
    netz.superAdmin = false;
    await starten();
    expect(el("opt-berechnen")).toBeNull();
    expect(el("opt-modus-setzen-SAFE_AUTO")).toBeNull();
    expect(el("opt-zuruecknehmen-a1")).toBeNull();
    expect(el("opt-annehmen-v3")).toBeNull();
    expect(el("opt-uebernehmen-v1")).toBeNull();
    expect(el("opt-frequenz-speichern")).toBeNull();
    expect(el("opt-frequenz-ab-0").disabled).toBe(true);
    await act(async () => { wurzel.unmount(); }); wurzel = null; behaelter?.remove();
    netz.fehler = "Nur Admins";
    await starten();
    expect(el("opt-fehler").textContent).toContain("Nur Admins");
  });

  it("Modellseite: Health-Badge des Modells und je Segment", async () => {
    await starten("/admin/markt/bmw-320d");
    expect(netz.gets.some((g) => g.url === "/admin/market/health/models/bmw-320d")).toBe(true);
    expect(el("markt-modell-titel").textContent).toBe("BMW 320d");
    expect(el("markt-modell-health").style.color).toBe("var(--st-lila)");
    expect(el("markt-modell-health-link").getAttribute("href")).toBe("/admin/markt/optimierung");
    expect(el("markt-modell-health-zaehler").textContent).toBe("1 HOT3 HEALTHY");
    expect(el("markt-segment-health-bmw-320d:v2:2020:2").style.color).toBe("var(--st-gelb)");
    expect(el("markt-segment-health-bmw-320d:v2:2020:0").getAttribute("title")).toBe("Grund HOT");
  });

  it("Phase G: Modus umschalten nur nach Rückfrage (PUT), FULL_AUTO ohne Knopf, Ersparnis durch SAFE_AUTO", async () => {
    await starten();
    expect(el("opt-modus-setzen-OBSERVE")).toBeNull();
    expect(el("opt-modus-setzen-FULL_AUTO")).toBeNull();
    expect(el("opt-ersparnis-safe-auto").textContent).toContain("1 aktive Änderungen");
    expect(el("opt-ersparnis-safe-auto").textContent).toContain("−0,26 $/Monat");
    const confirm = vi.spyOn(window, "confirm").mockReturnValueOnce(false);
    await klick("opt-modus-setzen-SAFE_AUTO");
    expect(confirm.mock.calls[0][0]).toContain("NUR");
    expect(confirm.mock.calls[0][0]).toContain("Nie km-Bereiche, EZ-Jahre, Zeilen oder Filter");
    expect(netz.puts).toEqual([]);
    confirm.mockReturnValueOnce(true);
    await klick("opt-modus-setzen-SAFE_AUTO");
    expect(netz.puts).toEqual([{ url: "/admin/market/optimierung/modus", body: { modus: "SAFE_AUTO" } }]);
    confirm.mockRestore();
  });

  it("Phase G: Protokoll der SAFE_AUTO-Änderungen (wer, alt → neu, Grund, Status), Rücknahme mit Rückfrage, Filter", async () => {
    await starten();
    expect(netz.gets.find((g) => g.url === "/admin/market/optimierung/aenderungen").params).toEqual({ status: "alle", limit: 100, offset: 0 });
    const a1 = el("opt-aenderung-a1").textContent;
    for (const t of ["SAFE_AUTO", "BMW 320d", "EZ 2020 · 30–40k km", "pausieren (EMPTY, mit Nachprüfung)", "100 % ohne Treffer", "−0,26 $/Monat", "aktiv"]) expect(a1).toContain(t);
    expect(el("opt-aenderung-wirkung-a1").textContent).toBe("täglich → pausiert · Nachprüfung alle 7 Tage");
    expect(el("opt-aenderung-wirkung-a2").textContent).toBe("täglich → alle 2 Tage");
    expect(el("opt-aenderung-wirkung-a3").textContent).toBe("normal geplant → zuerst geplant (HOT)");
    expect(el("opt-aenderung-a2").textContent).toContain("zurückgenommen");
    expect(el("opt-aenderung-a2").textContent).toContain("von sa: Rücknahme durch den Betreiber");
    expect(el("opt-aenderung-a3").textContent).toContain("von SAFE_AUTO: Modus OBSERVE");
    expect(el("opt-zuruecknehmen-a2")).toBeNull();
    expect(el("opt-zuruecknehmen-a3")).toBeNull();
    const confirm = vi.spyOn(window, "confirm").mockReturnValueOnce(false);
    await klick("opt-zuruecknehmen-a1");
    expect(netz.posts).toEqual([]);
    confirm.mockReturnValueOnce(true);
    await klick("opt-zuruecknehmen-a1");
    expect(netz.posts).toEqual(["/admin/market/optimierung/aenderungen/a1/zuruecknehmen"]);
    expect(confirm.mock.calls[1][0]).toContain("täglich → pausiert · Nachprüfung alle 7 Tage");
    confirm.mockRestore();
    await tippen("opt-protokoll-status", "aktiv");
    expect(netz.gets.filter((g) => g.url === "/admin/market/optimierung/aenderungen").at(-1).params).toEqual({ status: "aktiv", limit: 100, offset: 0 });
    expect(el("opt-aenderung-a2")).toBeNull();
    expect(el("opt-aenderung-a1")).toBeTruthy();
    // Wirkung je Segment in der Health-Tabelle
    await klick("opt-modell-segmente-bmw-320d");
    expect(el(`opt-segment-wirkung-${SEGMENTE[3].segment_id}`).textContent).toBe("pausiert · Nachprüfung alle 7 Tage");
    expect(el(`opt-segment-wirkung-${SEGMENTE[0].segment_id}`).textContent).toBe("zuerst geplant (HOT)");
    expect(el(`opt-segment-wirkung-${SEGMENTE[1].segment_id}`).textContent).toBe("—");
  });

  it("Hilfsfunktionen: Formular ↔ Anfrage, Frequenz- und Ersparnistext, Health-Stil", () => {
    expect(formularZuFrequenz(stufenZuFormular(STANDARD), "7")).toEqual(STANDARD);
    expect(formularZuFrequenz([{ ab: "x", crawls_per_day: "1", intervall_tage: "1", intervall_tage_bis: "" }], "7")).toBeNull();
    expect(formularZuFrequenz(stufenZuFormular(STANDARD), "")).toBeNull();
    expect(markt.frequenzText(0.5)).toBe("2× täglich");
    expect(markt.frequenzText(1)).toBe("täglich");
    expect(markt.frequenzText(3)).toBe("alle 3 Tage");
    expect(markt.frequenzText(7, true)).toBe("pausiert · Nachprüfung alle 7 Tage");
    expect(markt.frequenzText(null)).toBe("—");
    expect(markt.ersparnisText(0)).toBe("keine Kostenänderung");
    expect(markt.ersparnisText(null)).toBe("—");
    expect(markt.healthStil("HOT")).toEqual({ color: "var(--st-lila)", borderColor: "var(--st-lila)", fontWeight: 700, background: "var(--wa-08)" });
    expect(markt.healthStil("gibt-es-nicht").color).toBe("var(--text-dim)");
    expect(markt.HEALTH_REIHE).toEqual(["HOT", "HEALTHY", "NORMAL", "THIN", "EMPTY", "UNSTABLE", "STALE", "UNKNOWN"]);
    for (const st of markt.HEALTH_REIHE) expect(markt.HEALTH[st].farbe.startsWith("var(--")).toBe(true);
  });

  // ---------------------------------------------------------------- Prüfbefunde Phase F/G (27.09.2026)
  it("F11: Wettlauf — nur die Antwort der letzten Anfrage zählt; Neuladen nach einer Aktion mit dem AKTUELLEN Filter", async () => {
    await starten();
    // langsame Antwort für „alle“, danach schnell „abgelehnt“: die späte „alle“-Antwort überschreibt nichts
    let loesen;
    netz.warten["/admin/market/optimierung/vorschlaege|alle"] = new Promise((r) => { loesen = r; });
    await tippen("opt-filter-status", "alle");
    await tippen("opt-filter-status", "REJECTED");
    expect(el("opt-vorschlag-v5")).toBeTruthy();
    await act(async () => { loesen(); });
    await warten();
    expect(el("opt-vorschlag-v1")).toBeNull();
    expect(el("opt-vorschlag-v5")).toBeTruthy();
    expect(el("opt-filter-status").value).toBe("REJECTED");
    // „Health jetzt berechnen“ dauert; währenddessen Filter auf „alle“ — neu geladen wird mit „alle“, nicht „REJECTED“
    let fertig;
    netz.postWarten = new Promise((r) => { fertig = r; });
    const k = el("opt-berechnen");
    await act(async () => { k.click(); });
    await tippen("opt-filter-status", "alle");
    netz.postWarten = null;
    await act(async () => { fertig(); });
    await warten();
    expect(vorschlagParams()).toEqual({ status: "alle", limit: 100, offset: 0 });
    expect(el("opt-vorschlag-v1")).toBeTruthy();
  });

  it("F10/F12: „x von y“, weitere laden (offset), Auftragsfilter; Rücknahme auch in der Vorschlagsliste, Ablehnung aufheben", async () => {
    netz.viele = true;
    await starten();
    expect(el("opt-vorschlaege-zahl").textContent).toContain("100 von 150");
    await klick("opt-vorschlaege-weitere");
    expect(vorschlagParams()).toEqual({ status: "offen", limit: 100, offset: 100 });
    expect(el("opt-vorschlaege-zahl").textContent).toContain("150 von 150");
    expect(el("opt-vorschlaege-weitere")).toBeNull();
    expect(el("opt-vorschlag-n0")).toBeTruthy();
    expect(el("opt-vorschlag-n149")).toBeTruthy();
    netz.viele = false;
    await tippen("opt-filter-auftrag", "bmw-320d");
    expect(vorschlagParams()).toEqual({ status: "offen", limit: 100, offset: 0, model_id: "bmw-320d" });
    await tippen("opt-protokoll-auftrag", "bmw-320d");
    expect(netz.gets.filter((g) => g.url === "/admin/market/optimierung/aenderungen").at(-1).params).toEqual({ status: "alle", limit: 100, offset: 0, model_id: "bmw-320d" });
    await tippen("opt-filter-status", "alle");
    expect(el("opt-ersparnis-v4").textContent).toBe("10,9 Läufe/Monat frei");
    const confirm = vi.spyOn(window, "confirm").mockReturnValue(true);
    await klick("opt-vorschlag-zuruecknehmen-v4");
    expect(netz.posts.at(-1)).toBe("/admin/market/optimierung/aenderungen/a1/zuruecknehmen");
    expect(el("opt-aufheben-v4")).toBeNull();
    await klick("opt-aufheben-v5");
    expect(netz.posts.at(-1)).toBe("/admin/market/optimierung/vorschlaege/v5/ablehnung-aufheben");
    expect(confirm.mock.calls.at(-1)[0]).toContain("Ablehnung aufheben?");
    confirm.mockRestore();
    expect(el("opt-vorschlag-zuruecknehmen-v1")).toBeNull();
  });

  it("F10/F12/F15: Ladefehler der Listen und der Segmenttabelle mit „Erneut versuchen“; aufgeklappte Tabelle lädt nach Aktionen neu", async () => {
    netz.detailFehler = 1;
    await starten();
    await klick("opt-modell-segmente-bmw-320d");
    expect(el("opt-modell-detail-fehler-bmw-320d").textContent).toContain("Serverfehler");
    await klick("opt-modell-detail-erneut-bmw-320d");
    expect(el("opt-modell-detail-fehler-bmw-320d")).toBeNull();
    expect(el(`opt-segment-wirkung-${SEGMENTE[3].segment_id}`).textContent).toBe("pausiert · Nachprüfung alle 7 Tage");
    // Rücknahme im Protokoll -> die aufgeklappte Segmenttabelle lädt neu und zeigt den neuen Stand
    const vorher = netz.gets.filter((g) => g.url === "/admin/market/health/models/bmw-320d").length;
    netz.segmentWirkung = null;
    const confirm = vi.spyOn(window, "confirm").mockReturnValue(true);
    netz.segmentWirkung = { intervall_tage: 1, crawls_per_day: null, hot: false, pausiert: false };
    await klick("opt-zuruecknehmen-a1");
    confirm.mockRestore();
    expect(netz.gets.filter((g) => g.url === "/admin/market/health/models/bmw-320d").length).toBe(vorher + 1);
    expect(el(`opt-segment-wirkung-${SEGMENTE[3].segment_id}`).textContent).toBe("täglich");
    // Listenfehler: sichtbar mit „Erneut versuchen“
    netz.warten["/admin/market/optimierung/vorschlaege|PROPOSED"] = Promise.reject(new Error("Vorschläge: Zeitüberschreitung"));
    netz.warten["/admin/market/optimierung/vorschlaege|PROPOSED"].catch(() => {});
    await tippen("opt-filter-status", "PROPOSED");
    expect(el("opt-vorschlaege-fehler").textContent).toContain("Zeitüberschreitung");
    delete netz.warten["/admin/market/optimierung/vorschlaege|PROPOSED"];
    await klick("opt-vorschlaege-erneut");
    expect(el("opt-vorschlaege-fehler")).toBeNull();
    expect(el("opt-vorschlag-v3")).toBeTruthy();
  });

  it("F8/F9: Budgetgrenze -> frei werdende Läufe statt Dollar; Stand von Knopf und täglichem Lauf getrennt", async () => {
    netz.uebersicht = { ...UEBERSICHT, budget_grenze: true, laeufe_frei_offen_monat: 52.1, laeufe_frei_safe_auto_monat: 10.9,
                        ersparnis_offen_usd: 0, ersparnis_safe_auto_usd: 0,
                        stand: { letzter_lauf_at: "2026-10-01T05:30:00Z", quelle: "admin", taeglich_lauf_at: "2026-09-30T08:30:00Z", fehler: 0 } };
    await starten();
    expect(el("opt-ersparnis").textContent).toContain("52,1 Läufe/Monat frei");
    expect(el("opt-ersparnis").textContent).toContain("keine Dollar-Ersparnis");
    expect(el("opt-ersparnis-safe-auto").textContent).toContain("10,9 Läufe/Monat frei");
    expect(el("opt-budget-grenze").textContent).toContain("Monatsbudget ist die Grenze");
    expect(el("opt-stand").textContent).toContain("per Knopf");
    expect(el("opt-stand").textContent).toContain("täglicher Lauf");
  });

  it("F17: Frequenzsenkung bleibt neben HOT sichtbar; Protokoll zeigt je Änderung nur die betroffene Größe", () => {
    expect(markt.wirkungText({ hot: true, intervall_tage: 1, crawls_per_day: 2, pausiert: false })).toBe("2× täglich · zuerst geplant (HOT)");
    expect(markt.wirkungText({ hot: true, intervall_tage: 1, crawls_per_day: null, pausiert: false })).toBe("zuerst geplant (HOT)");
    expect(markt.wirkungText({ hot: false, intervall_tage: 3, crawls_per_day: 1, pausiert: false })).toBe("alle 3 Tage");
    // alter Protokolleintrag: 'alt' trug die HOT-Priorität mit — die Frequenzsenkung zeigt „4× täglich → 2× täglich“
    const alt = { intervall_tage: 1, crawls_per_day: 4, prioritaet: "HOT", pausiert: false };
    expect(`${markt.wirkungText(alt, "REDUCE_FREQUENCY")} → ${markt.wirkungText({ intervall_tage: 1, crawls_per_day: 2 }, "REDUCE_FREQUENCY")}`).toBe("4× täglich → 2× täglich");
    expect(markt.wirkungText({ prioritaet: "normal" }, "PRIORITIZE_HOT")).toBe("normal geplant");
    expect(markt.wirkungText({ prioritaet: "HOT" })).toBe("zuerst geplant (HOT)");
    expect(markt.laeufeText(-12.5)).toBe("12,5 Läufe/Monat zusätzlich");
    expect(markt.wirkungGeldText(0.26, 26.1, false)).toBe("−0,26 $/Monat");
  });

  it("F18: Modellseite — pausierter Auftrag bzw. neue Fassung: kein altes Badge, Grund und Stichtag sichtbar", async () => {
    netz.modellHealth = { model_id: "bmw-320d", modell: UEBERSICHT.modelle[0], segmente: SEGMENTE, vorschlaege: [], aktuell: false,
                          nicht_aktuell_grund: "pausiert", auftrag_status: "paused", auftrag_version: 2, health_tag: "2026-09-01", health_version: 2 };
    await starten("/admin/markt/bmw-320d");
    expect(el("markt-modell-health")).toBeNull();
    expect(el("markt-segment-health-bmw-320d:v2:2020:2")).toBeNull();
    expect(el("markt-modell-health-veraltet").textContent).toContain("pausiert");
    expect(el("markt-modell-health-veraltet").textContent).toContain("2026-09-01");
    await act(async () => { wurzel.unmount(); }); wurzel = null; behaelter?.remove();
    netz.modellHealth = { ...netz.modellHealth, nicht_aktuell_grund: "neue_fassung", auftrag_status: "active", auftrag_version: 3 };
    await starten("/admin/markt/bmw-320d");
    expect(el("markt-modell-health")).toBeNull();
    expect(el("markt-modell-health-veraltet").textContent).toContain("neue Fassung v3 noch nicht bewertet");
  });

  // ---------------------------------------------------------------- Prüfbefunde Runde 2 (27.09.2026)
  it("F15 Runde 2: eine zwischendurch ZUGEKLAPPTE Segmenttabelle lädt beim Aufklappen neu und zeigt nie den alten Stand", async () => {
    await starten();
    const gets = () => netz.gets.filter((g) => g.url === "/admin/market/health/models/bmw-320d").length;
    await klick("opt-modell-segmente-bmw-320d");
    expect(el(`opt-segment-wirkung-${SEGMENTE[3].segment_id}`).textContent).toBe("pausiert · Nachprüfung alle 7 Tage");
    await klick("opt-modell-segmente-bmw-320d");                 // zuklappen
    expect(el(`opt-segment-wirkung-${SEGMENTE[3].segment_id}`)).toBeNull();
    const vorher = gets();
    netz.segmentWirkung = { intervall_tage: 1, crawls_per_day: null, hot: false, pausiert: false };
    const confirm = vi.spyOn(window, "confirm").mockReturnValue(true);
    await klick("opt-zuruecknehmen-a1");
    confirm.mockRestore();
    expect(gets()).toBe(vorher);                                 // zugeklappt: kein Laden im Hintergrund
    // langsame Antwort: bis sie da ist, steht NICHT der alte Stand da ("lade…"), danach der neue
    let loesen;
    netz.warten["/admin/market/health/models/bmw-320d|"] = new Promise((r) => { loesen = r; });
    await klick("opt-modell-segmente-bmw-320d");
    expect(gets()).toBe(vorher + 1);
    expect(el(`opt-segment-wirkung-${SEGMENTE[3].segment_id}`)).toBeNull();
    await act(async () => { loesen(); });
    await warten();
    expect(el(`opt-segment-wirkung-${SEGMENTE[3].segment_id}`).textContent).toBe("täglich");
    // ohne Aktion dazwischen: Aufklappen lädt trotzdem den aktuellen Stand
    delete netz.warten["/admin/market/health/models/bmw-320d|"];
    await klick("opt-modell-segmente-bmw-320d");
    await klick("opt-modell-segmente-bmw-320d");
    expect(gets()).toBe(vorher + 2);
  });

  it("Runde 2: scheitert die Liste nach einem Filterwechsel, bleibt die ALTE Liste nie mit Knöpfen stehen", async () => {
    await starten();
    expect(el("opt-vorschlag-v1")).toBeTruthy();
    netz.warten["/admin/market/optimierung/vorschlaege|REJECTED"] = Promise.reject(new Error("Vorschläge: Zeitüberschreitung"));
    netz.warten["/admin/market/optimierung/vorschlaege|REJECTED"].catch(() => {});
    await tippen("opt-filter-status", "REJECTED");
    expect(el("opt-filter-status").value).toBe("REJECTED");
    expect(el("opt-vorschlaege-fehler").textContent).toContain("Zeitüberschreitung");
    for (const id of ["v1", "v2", "v3"]) expect(el(`opt-vorschlag-${id}`)).toBeNull();
    expect(behaelter.textContent).not.toContain("Annehmen");
    delete netz.warten["/admin/market/optimierung/vorschlaege|REJECTED"];
    await klick("opt-vorschlaege-erneut");
    expect(el("opt-vorschlag-v5")).toBeTruthy();
    expect(el("opt-vorschlag-v1")).toBeNull();
  });
});
