/**
 * Startpruefung 28.09.2026 (Restpunkte zu 3bee24d):
 *  H9-Rest  Chef mit eigenem, nur per Datum abgelaufenem Abo ohne Firmen-Abo (Anzeige 'none') bzw. Abo ohne
 *           lesbares Datum ('ungueltig'): der Server meldet ablauf_korrigierbar + ablauf_abo_bis, die
 *           Oberflaeche zeigt "Speichern" UND sagt, warum gesperrt ist (vorher nur "—").
 *  Laden    UserDetail: eine spaet scheiternde (oder spaet ankommende) Anfrage fuer die VORIGE Nutzer-Id
 *           leerte bzw. ueberschrieb den schon geladenen neuen Nutzer.
 *  Filter   Private Deals: "km von 15.000" wurde 15 km (Number(v)); jetzt kmAusText/preisAusText.
 */
import { act, createElement as h, Fragment } from "react";
import { createRoot } from "react-dom/client";
import { MemoryRouter, Route, Routes, useNavigate } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

globalThis.IS_REACT_ACT_ENVIRONMENT = true;

const netz = vi.hoisted(() => ({ patches: [], gets: [], sucher: [], offen: {}, navigieren: null }));
vi.mock("sonner", () => ({ toast: { success: vi.fn(), error: vi.fn(), info: vi.fn(), warning: vi.fn() } }));

function nutzer(id, name) {
  return { user: { id, role: "sucher", dealer_id: "d9", company_name: name, email: `${id}@e2etest-mail.de`, active: true,
                   created_at: "2026-09-01T00:00:00Z" }, contracts: [], gesamt: 0 };
}

vi.mock("@/lib/api", () => ({
  errMsg: (e, s) => e?.response?.data?.detail || e?.message || s,
  api: {
    get: vi.fn(async (url, opts) => {
      netz.gets.push({ url, params: opts?.params });
      // Vertraege je Nutzer: die Anfrage fuer "alt" haengt, bis der Test sie aufloest/scheitern laesst
      const m = /^\/admin\/users\/([^/]+)\/contracts$/.exec(url);
      if (m) {
        if (m[1] === "chef") {
          return { data: { user: { id: "chef", role: "dealer", dealer_id: "d1", ist_chef: true, company_name: "Chef GmbH",
                                   email: "chef@e2etest-mail.de", active: true, created_at: "2026-09-01T00:00:00Z" },
                           contracts: [], gesamt: 0 } };
        }
        if (m[1] === "alt") return new Promise((ok, nein) => { netz.offen.alt = { ok, nein }; });
        return { data: nutzer(m[1], `Firma ${m[1]}`) };
      }
      if (url === "/admin/dealers/d1/sucher") return { data: netz.sucher, headers: {} };
      if (url === "/admin/dealers/d1/zahlungen") return { data: [] };
      if (url === "/admin/market/models") return { data: { modelle: [] } };
      if (url === "/admin/market/private-deals") {
        return { data: { zusammenfassung: {}, deals: [], anzahl: 0, gekuerzt: false, hinweis: "" } };
      }
      return { data: {} };
    }),
    post: vi.fn(async () => ({ data: { ok: true } })),
    put: vi.fn(async () => ({ data: { ok: true } })),
    patch: vi.fn(async (url, body) => { netz.patches.push({ url, body }); return { data: { ok: true } }; }),
    delete: vi.fn(async () => ({ data: {} })),
  },
}));
vi.mock("@/context/AuthContext", () => ({ useAuth: () => ({ user: { id: "sa", is_super_admin: true, role: "admin" } }) }));
vi.mock("@/components/admin/ZugangsdatenKarte", () => ({ default: () => null }));
vi.mock("@/components/admin/PasswortFeld", () => ({ default: () => null }));
vi.mock("@/lib/dateiOeffnen", () => ({ blobOeffnen: vi.fn() }));

const { default: UserDetail } = await import("./UserDetail");
const { default: MarktPrivateDeals, filterLesen, filterParams } = await import("./MarktPrivateDeals");

let wurzel;
let behaelter;
const el = (t) => behaelter.querySelector(`[data-testid="${t}"]`);
async function warten() { for (let i = 0; i < 8; i += 1) await act(async () => { await new Promise((r) => setTimeout(r, 0)); }); }
function Nav() { netz.navigieren = useNavigate(); return null; }
async function starten(pfad) {
  behaelter = document.createElement("div"); document.body.appendChild(behaelter); wurzel = createRoot(behaelter);
  await act(async () => {
    wurzel.render(h(MemoryRouter, { initialEntries: [pfad] }, h(Routes, null,
      h(Route, { path: "/admin/users/:id", element: h(Fragment, null, h(UserDetail), h(Nav)) }),
      h(Route, { path: "/admin/markt/private-deals", element: h(MarktPrivateDeals) }))));
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
const dealAbrufe = () => netz.gets.filter((g) => g.url === "/admin/market/private-deals");

beforeEach(() => {
  netz.patches.length = 0; netz.gets.length = 0; netz.sucher = []; netz.offen = {}; netz.navigieren = null;
  vi.clearAllMocks();
});
afterEach(async () => { vi.restoreAllMocks(); if (wurzel) await act(async () => { wurzel.unmount(); }); wurzel = null; behaelter?.remove(); });

describe("H9-Rest: Ablaufdatum auch beim Chef und ohne lesbares Datum korrigierbar", () => {
  it("Chef mit vertipptem Datum ('none') und Konto ohne Datum ('ungueltig'): Hinweis + 'Speichern' aendert nur das Datum", async () => {
    netz.sucher = [
      // Chef: eigenes Abo per Datum abgelaufen, kein Firmen-Abo -> subscription = Firmen-Rueckfall 'none'
      { id: "chef", role: "dealer", ist_chef: true, active: true, kontonummer: "2001",
        subscription: { active: false, status: "none", plan: null, expires_at: null },
        ablauf_korrigierbar: true, ablauf_abo_bis: "2025-09-27T21:59:59+00:00", created_at: "2026-09-01T00:00:00Z" },
      // Sucher: Abo ohne lesbares Ablaufdatum
      { id: "s5", role: "sucher", first_name: "Nina", last_name: "Ohne", active: true, kontonummer: "2005",
        subscription: { active: false, status: "ungueltig", plan: "monthly", expires_at: "irgendwann" },
        ablauf_korrigierbar: true, ablauf_abo_bis: "irgendwann", created_at: "2026-09-02T00:00:00Z" },
      // nie ein Abo: weder Hinweis noch 'Speichern'
      { id: "s6", role: "sucher", first_name: "Olaf", last_name: "Nie", active: true, kontonummer: "2006",
        subscription: { active: false, status: "none" }, ablauf_korrigierbar: false, ablauf_abo_bis: null,
        created_at: "2026-09-03T00:00:00Z" },
    ];
    await starten("/admin/users/chef");
    expect(el("gueltig-bis-speichern-chef")).toBeTruthy();
    expect(el("abo-datum-korrigierbar-chef").textContent).toContain("abgelaufen am 27.09.2025");
    expect(el("abo-datum-korrigierbar-chef").textContent).toContain("„Speichern“ (keine Zahlung)");
    expect(el("abo-monat-chef")).toBeTruthy();                        // Bezahl-Knoepfe bleiben daneben
    expect(el("gueltig-bis-speichern-s5")).toBeTruthy();
    expect(el("abo-datum-korrigierbar-s5").textContent).toContain("Ablaufdatum fehlt oder ist unlesbar");
    expect(el("gueltig-bis-speichern-s6")).toBeNull();
    expect(el("abo-datum-korrigierbar-s6")).toBeNull();

    await tippen("gueltig-bis-chef", "2026-09-27");
    vi.spyOn(window, "prompt").mockReturnValue("Jahr vertippt");
    await klick("gueltig-bis-speichern-chef");
    expect(netz.patches).toEqual([{ url: "/admin/sucher/chef/abo-gueltig-bis",
      body: { gueltig_bis: "2026-09-27", grund: "Jahr vertippt" } }]);
  });
});

describe("Kontoansicht: Antworten fuer die vorige Nutzer-Id werden verworfen", () => {
  it("spaet scheiternde Anfrage fuer den vorigen Nutzer leert den neuen nicht", async () => {
    await starten("/admin/users/alt");
    expect(netz.offen.alt).toBeTruthy();
    await act(async () => { netz.navigieren("/admin/users/neu"); });
    await warten();
    expect(behaelter.textContent).toContain("Firma neu");
    const e = new Error("Zeitueberschreitung"); e.response = { status: 500, data: { detail: "Zeitueberschreitung" } };
    await act(async () => { netz.offen.alt.nein(e); });
    await warten();
    expect(el("nutzer-ladefehler")).toBeNull();
    expect(behaelter.textContent).not.toContain("Nutzer nicht gefunden");
    expect(behaelter.textContent).toContain("Firma neu");
  });

  it("spaet ankommende Antwort fuer den vorigen Nutzer ueberschreibt den neuen nicht", async () => {
    await starten("/admin/users/alt");
    await act(async () => { netz.navigieren("/admin/users/neu"); });
    await warten();
    await act(async () => { netz.offen.alt.ok({ data: nutzer("alt", "Firma alt") }); });
    await warten();
    expect(behaelter.textContent).toContain("Firma neu");
    expect(behaelter.textContent).not.toContain("Firma alt");
  });
});

describe("Private Deals: Filter lesen deutsche Zahlen", () => {
  it("filterLesen/filterParams: Tausenderpunkt, km-Zusatz, Komma, Prozent; Unlesbares wird gemeldet", () => {
    expect(filterParams({ km_min: "15.000", km_max: "80.000 km", preis_von: "15.000", preis_bis: "20.000,50 €",
                          abstand_pct_max: "-5,5 %", ez: "2020", plz: " 80 " }))
      .toEqual({ sort: "abstand_pct", limit: 300, nur_aktuell: true, km_min: 15000, km_max: 80000, preis_von: 15000,
                 preis_bis: 20000.5, abstand_pct_max: -5.5, ez: 2020, plz: "80" });
    expect(filterParams({ km_min: "150 Tkm", preis_bis: "9500" })).toMatchObject({ km_min: 150000, preis_bis: 9500 });
    const r = filterLesen({ km_min: "abc", preis_von: "1.00.0", ez: "20", abstand_pct_max: "viel" });
    expect(r.params).toEqual({ sort: "abstand_pct", limit: 300, nur_aktuell: true });
    expect(r.fehler).toHaveLength(4);
    expect(r.fehler.join(" | ")).toMatch(/km von: „abc“/);
    expect(r.fehler.join(" | ")).toMatch(/Preis von/);
    expect(r.fehler.join(" | ")).toMatch(/EZ \(Jahr\)/);
    expect(r.fehler.join(" | ")).toMatch(/Abstand zum Median/);
  });

  it("Formular: 'km von 15.000' geht als 15000 raus; Unlesbares wird nicht gesendet, sondern gemeldet", async () => {
    await starten("/admin/markt/private-deals");
    await tippen("pd-filter-km-min", "15.000");
    await tippen("pd-filter-preis-bis", "20.000");
    await klick("pd-filter-anwenden");
    expect(dealAbrufe().at(-1).params).toMatchObject({ km_min: 15000, preis_bis: 20000 });

    const vorher = dealAbrufe().length;
    await tippen("pd-filter-km-max", "viel");
    await klick("pd-filter-anwenden");
    expect(dealAbrufe().length).toBe(vorher);
    expect(el("private-deals-fehler").textContent).toContain("km bis: „viel“");

    // halbes Baujahr beim Tippen: kein Abruf, keine Fehlermeldung; vollstaendig: sofort geladen
    await tippen("pd-filter-km-max", "");
    await tippen("pd-filter-ez", "20");
    expect(dealAbrufe().length).toBe(vorher);
    await tippen("pd-filter-ez", "2020");
    expect(dealAbrufe().length).toBe(vorher + 1);
    expect(dealAbrufe().at(-1).params).toMatchObject({ ez: 2020, km_min: 15000 });
    expect(el("private-deals-fehler")).toBeNull();
  });
});
