/**
 * Startpruefung 27.09.2026 (Go-Live-Pruefbericht, gegengeprueft):
 *  G3   Markt-Budget: "700,00" wurde 0 $ (Planung pausiert), "1.000" wurde 1 $ (Budget erschoepft) — und
 *       der Wert galt als Vorgabe auch fuer die Folgemonate. Jetzt deutsche Schreibweise, Unlesbares wird
 *       NICHT gespeichert, 0 < Betrag < 5 $ nur nach Rueckfrage (bestaetigt=true), Toast nennt den Betrag.
 *  H10  UserDetail: Ladefehler (500/Netz) zeigte dauerhaft "Nutzer nicht gefunden" — jetzt eigene Karte
 *       "Konnte nicht geladen werden" mit "Erneut versuchen"; nur 404 heisst "nicht gefunden".
 *  H9   UserDetail: "Speichern" (nur Datum, keine Zahlung) auch bei per Datum abgelaufenem Abo.
 */
import { act, createElement as h } from "react";
import { createRoot } from "react-dom/client";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

globalThis.IS_REACT_ACT_ENVIRONMENT = true;

const netz = vi.hoisted(() => ({ puts: [], patches: [], vertraegeFehler: null, sucher: [] }));
vi.mock("sonner", () => ({ toast: { success: vi.fn(), error: vi.fn(), info: vi.fn(), warning: vi.fn() } }));
vi.mock("@/lib/api", () => ({
  errMsg: (e, s) => e?.response?.data?.detail || e?.message || s,
  api: {
    get: vi.fn(async (url) => {
      if (url === "/admin/market/models") return { data: { modelle: [] } };
      if (url === "/admin/market/status") {
        return { data: { aktiv: false, segmente: 16, modelle: 1, listings: 0, snapshots: 0, token_vorhanden: true,
          budget: { _id: "2026-10", budget_usd: 450, used_usd: 0, reserved_usd: 0, rows: 0, runs: 0 },
          takt: { intervall_tage: 3, segmente_je_tag: 6, buendel: 10, start_usd: 0.005, row_usd: 0.0007 },
          jobs: {}, km_buckets: [{ min_km: 10000, max_km: 30000 }], ez_buckets: [{ year_from: 2019, year_to: 2021 }],
          einstellungen: { rows_je_segment: 20 } } };
      }
      if (url === "/admin/users/u1/contracts") {
        if (netz.vertraegeFehler) {
          const e = new Error(netz.vertraegeFehler.text);
          e.response = { status: netz.vertraegeFehler.status, data: { detail: netz.vertraegeFehler.text } };
          throw e;
        }
        return { data: { user: { id: "u1", role: "dealer", dealer_id: "d1", ist_chef: true, company_name: "Demo GmbH",
                                 email: "chef@e2etest-mail.de", active: true, created_at: "2026-09-01T00:00:00Z" },
                         contracts: [], gesamt: 0 } };
      }
      if (url === "/admin/dealers/d1/sucher") return { data: netz.sucher, headers: {} };
      if (url === "/admin/dealers/d1/zahlungen") return { data: [] };
      return { data: {} };
    }),
    post: vi.fn(async () => ({ data: { ok: true } })),
    put: vi.fn(async (url, body) => { netz.puts.push({ url, body }); return { data: { ok: true, segmente: 16, takt: { intervall_tage: 3 } } }; }),
    patch: vi.fn(async (url, body) => { netz.patches.push({ url, body }); return { data: { ok: true } }; }),
    delete: vi.fn(async () => ({ data: {} })),
  },
}));
vi.mock("@/context/AuthContext", () => ({ useAuth: () => ({ user: { id: "sa", is_super_admin: true, role: "admin" } }) }));
vi.mock("@/components/admin/ZugangsdatenKarte", () => ({ default: () => null }));
vi.mock("@/components/admin/PasswortFeld", () => ({ default: () => null }));
vi.mock("@/lib/dateiOeffnen", () => ({ blobOeffnen: vi.fn() }));
vi.mock("@/components/MarktQualitaet", () => ({ QualitaetZaehler: () => null }));

const { default: Markt, konfigLesen, usdText } = await import("./Markt");
const { default: UserDetail, ablaufKorrigierbar } = await import("./UserDetail");
const { toast } = await import("sonner");

let wurzel;
let behaelter;
const el = (t) => behaelter.querySelector(`[data-testid="${t}"]`);
async function warten() { for (let i = 0; i < 8; i += 1) await act(async () => { await new Promise((r) => setTimeout(r, 0)); }); }
async function starten(pfad) {
  behaelter = document.createElement("div"); document.body.appendChild(behaelter); wurzel = createRoot(behaelter);
  await act(async () => {
    wurzel.render(h(MemoryRouter, { initialEntries: [pfad] }, h(Routes, null,
      h(Route, { path: "/admin/markt", element: h(Markt) }), h(Route, { path: "/admin/users/:id", element: h(UserDetail) }))));
  });
  await warten();
}
async function klick(t) { const k = el(t); if (!k) throw new Error(`nicht gefunden: ${t}`); await act(async () => { k.click(); }); await warten(); }
async function tippen(t, wert) {
  const feld = el(t);
  if (!feld) throw new Error(`nicht gefunden: ${t}`);
  const setzen = Object.getOwnPropertyDescriptor(window.HTMLInputElement.prototype, "value").set;
  await act(async () => { setzen.call(feld, wert); feld.dispatchEvent(new Event("input", { bubbles: true })); });
  await warten();
}

beforeEach(() => { netz.puts.length = 0; netz.patches.length = 0; netz.vertraegeFehler = null; netz.sucher = []; vi.clearAllMocks(); });
afterEach(async () => { vi.restoreAllMocks(); if (wurzel) await act(async () => { wurzel.unmount(); }); wurzel = null; behaelter?.remove(); });

const FORM = { km: "10000-30000", ez: "2019-2021", rows: "20" };

describe("G3: Markt-Budget liest deutsche Betraege", () => {
  it("konfigLesen: Komma/Punkt deutsch, Unlesbares -> Fehler statt 0 oder 1", () => {
    expect(konfigLesen({ ...FORM, budget: "700,00" }).werte.budget_usd).toBe(700);
    expect(konfigLesen({ ...FORM, budget: "1.000" }).werte.budget_usd).toBe(1000);
    expect(konfigLesen({ ...FORM, budget: "1.000,50" }).werte.budget_usd).toBe(1000.5);
    expect(konfigLesen({ ...FORM, budget: "450" }).werte.budget_usd).toBe(450);
    expect(konfigLesen({ ...FORM, budget: "12.5" }).werte.budget_usd).toBe(12.5);
    expect(konfigLesen({ ...FORM, budget: "700 $" }).werte.budget_usd).toBe(700);
    expect(konfigLesen({ ...FORM, budget: "0" }).werte.budget_usd).toBe(0);          // bewusst pausieren bleibt erlaubt
    for (const falsch of ["abc", "-5", "", "  ", "7,0,0", "1.00.0"]) {
      const r = konfigLesen({ ...FORM, budget: falsch });
      expect(r.werte).toBeUndefined();
      expect(r.fehler).toMatch(/Monatsbudget .* kein gültiger Betrag/);
    }
  });

  it("konfigLesen: Nachbarfelder ebenso streng (km mit Tausenderpunkt, Zeilen)", () => {
    const r = konfigLesen({ km: "10.000-30.000, 30.001-60.000", ez: "2019-2021, -2016", rows: "20", budget: "450" });
    expect(r.werte).toEqual({ km_buckets: [{ min_km: 10000, max_km: 30000 }, { min_km: 30001, max_km: 60000 }],
      ez_buckets: [{ year_from: 2019, year_to: 2021 }, { year_from: null, year_to: 2016 }], rows_je_segment: 20, budget_usd: 450 });
    expect(konfigLesen({ ...FORM, rows: "abc", budget: "450" }).fehler).toMatch(/Zeilen je Segment/);
    expect(konfigLesen({ ...FORM, rows: "0", budget: "450" }).fehler).toMatch(/Zeilen je Segment/);
    expect(konfigLesen({ ...FORM, rows: "201", budget: "450" }).fehler).toMatch(/Zeilen je Segment/);
    expect(konfigLesen({ ...FORM, km: "10000-x", budget: "450" }).fehler).toMatch(/km-Bereich/);
    expect(konfigLesen({ ...FORM, ez: "19-21", budget: "450" }).fehler).toMatch(/EZ-Bereich/);
    expect(usdText(1000.5)).toBe("1.000,50");
  });

  it("Formular: '1.000,50' speichert 1000.5 und der Toast nennt den Betrag", async () => {
    await starten("/admin/markt");
    await klick("markt-konfig-oeffnen");
    expect(el("markt-konfig-budget").value).toBe("450");
    await tippen("markt-konfig-budget", "1.000,50");
    await klick("markt-konfig-speichern");
    expect(netz.puts).toHaveLength(1);
    expect(netz.puts[0].body.budget_usd).toBe(1000.5);
    expect(netz.puts[0].body.bestaetigt).toBeUndefined();
    expect(toast.success).toHaveBeenCalledWith(expect.stringContaining("Monatsbudget 1.000,50 $"));
    await tippen("markt-konfig-budget", "700,00");
    await klick("markt-konfig-speichern");
    expect(netz.puts[1].body.budget_usd).toBe(700);
    expect(toast.success).toHaveBeenLastCalledWith(expect.stringContaining("Monatsbudget 700,00 $"));
  });

  it("Formular: Unsinn wird NICHT gespeichert, sondern gemeldet", async () => {
    await starten("/admin/markt");
    await klick("markt-konfig-oeffnen");
    await tippen("markt-konfig-budget", "siebenhundert");
    await klick("markt-konfig-speichern");
    expect(netz.puts).toHaveLength(0);
    expect(toast.error).toHaveBeenCalledWith(expect.stringContaining("Nicht gespeichert"));
    expect(el("markt-konfig-fehler").textContent).toContain("kein gültiger Betrag");
    expect(toast.success).not.toHaveBeenCalled();
  });

  it("Formular: 1 $ nur nach Rueckfrage (bestaetigt=true), 0 $ mit Hinweis auf Pause", async () => {
    await starten("/admin/markt");
    await klick("markt-konfig-oeffnen");
    const frage = vi.spyOn(window, "confirm").mockReturnValue(false);
    await tippen("markt-konfig-budget", "1");
    await klick("markt-konfig-speichern");
    expect(frage.mock.calls[0][0]).toContain("meinten Sie 1.000,00 $");
    expect(netz.puts).toHaveLength(0);
    frage.mockReturnValue(true);
    await klick("markt-konfig-speichern");
    expect(netz.puts[0].body).toEqual(expect.objectContaining({ budget_usd: 1, bestaetigt: true }));
    frage.mockClear();
    frage.mockReturnValue(false);
    await tippen("markt-konfig-budget", "0");
    await klick("markt-konfig-speichern");
    expect(frage.mock.calls[0][0]).toContain("pausiert die Marktbeobachtung");
    expect(netz.puts).toHaveLength(1);
    frage.mockReturnValue(true);
    await klick("markt-konfig-speichern");
    expect(netz.puts[1].body.budget_usd).toBe(0);
    expect(netz.puts[1].body.bestaetigt).toBeUndefined();
  });
});

describe("H10: Ladefehler statt 'Nutzer nicht gefunden'", () => {
  it("500 -> 'Konnte nicht geladen werden' + 'Erneut versuchen' laedt nach", async () => {
    netz.vertraegeFehler = { status: 500, text: "Serverfehler" };
    await starten("/admin/users/u1");
    expect(el("nutzer-ladefehler").textContent).toContain("Konnte nicht geladen werden");
    expect(el("nutzer-ladefehler").textContent).toContain("Serverfehler");
    expect(behaelter.textContent).not.toContain("Nutzer nicht gefunden");
    netz.vertraegeFehler = null;
    await klick("nutzer-erneut-laden");
    expect(el("nutzer-ladefehler")).toBeNull();
    expect(behaelter.textContent).toContain("Demo GmbH");
  });

  it("Netzfehler ohne Antwort -> ebenfalls Ladefehler", async () => {
    netz.vertraegeFehler = { status: undefined, text: "Network Error" };
    await starten("/admin/users/u1");
    expect(el("nutzer-ladefehler")).toBeTruthy();
  });

  it("404 -> 'Nutzer nicht gefunden' ohne Knopf", async () => {
    netz.vertraegeFehler = { status: 404, text: "Nutzer nicht gefunden" };
    await starten("/admin/users/u1");
    expect(behaelter.textContent).toContain("Nutzer nicht gefunden");
    expect(el("nutzer-ladefehler")).toBeNull();
    expect(el("nutzer-erneut-laden")).toBeNull();
  });
});

describe("H9: Ablaufdatum auch nach Ablauf korrigierbar", () => {
  it("ablaufKorrigierbar: aktiv, Server-Feld, alter Server", () => {
    expect(ablaufKorrigierbar({ subscription: { active: true } })).toBe(true);
    expect(ablaufKorrigierbar({ subscription: { active: false, status: "expired" }, ablauf_korrigierbar: true })).toBe(true);
    expect(ablaufKorrigierbar({ subscription: { active: false, status: "expired" }, ablauf_korrigierbar: false })).toBe(false);
    expect(ablaufKorrigierbar({ subscription: { active: false, status: "expired" } })).toBe(true);
    expect(ablaufKorrigierbar({ subscription: { active: false, status: "none" } })).toBe(false);
    expect(ablaufKorrigierbar(null)).toBe(false);
  });

  it("per Datum abgelaufen: 'Speichern' da und aendert nur das Datum; aufgehoben: kein 'Speichern'", async () => {
    netz.sucher = [
      { id: "u1", role: "dealer", ist_chef: true, active: true, kontonummer: "1001", subscription: { active: false, status: "none" },
        ablauf_korrigierbar: false, created_at: "2026-09-01T00:00:00Z" },
      { id: "s3", role: "sucher", first_name: "Vera", last_name: "Tipp", active: true, kontonummer: "1003",
        subscription: { active: false, status: "expired", plan: "yearly", expires_at: "2025-09-27T21:59:59+00:00" },
        ablauf_korrigierbar: true, naechste_zahlung_am: "2025-09-27T21:59:59+00:00", created_at: "2026-09-02T00:00:00Z" },
      { id: "s4", role: "sucher", first_name: "Anna", last_name: "Auf", active: true, kontonummer: "1004",
        subscription: { active: false, status: "expired", plan: "monthly", expires_at: "2026-09-20T10:00:00+00:00" },
        ablauf_korrigierbar: false, created_at: "2026-09-03T00:00:00Z" },
    ];
    await starten("/admin/users/u1");
    expect(el("gueltig-bis-speichern-s3")).toBeTruthy();
    expect(el("abo-abgelaufen-s3").textContent).toContain("Neues Datum wählen");
    expect(el("abo-monat-s3")).toBeTruthy();                 // Bezahl-Knoepfe bleiben daneben
    expect(el("gueltig-bis-speichern-s4")).toBeNull();       // aufgehoben: Server nimmt kein Datum an
    expect(el("gueltig-bis-speichern-u1")).toBeNull();       // nie ein Abo
    await tippen("gueltig-bis-s3", "2026-09-27");
    vi.spyOn(window, "prompt").mockReturnValue("Jahr vertippt (2025 statt 2026)");
    await klick("gueltig-bis-speichern-s3");
    expect(netz.patches).toEqual([{ url: "/admin/sucher/s3/abo-gueltig-bis",
      body: { gueltig_bis: "2026-09-27", grund: "Jahr vertippt (2025 statt 2026)" } }]);
  });
});
