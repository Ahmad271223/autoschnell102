/**
 * Go-Live-Pruefung 28.09.2026 (admin5): Loeschvorschau gehoert immer zur gewaehlten Firma;
 * keine alten Modelldaten beim Modellwechsel.
 *
 * Nachgestellt: Loeschdialog fuer Chef A oeffnen, /admin/dealers/dA/loeschvorschau haengt, Abbrechen,
 * Dialog fuer Chef B oeffnen — danach kam A's Antwort, der Dialog von B zeigte "Wuerde loeschen" von
 * Firma A und "Endgueltig loeschen" war frei (Firma B waere mit ?firma_loeschen=true geloescht worden).
 * Dazu die Modellseite (Health von A unter B) und die Berichtsseite (Fehler/Bericht von A unter B).
 * Jede Anfrage haengt hier als Promise, bis der Test sie spaet aufloest bzw. scheitern laesst.
 */
import { act, createElement as h, Fragment } from "react";
import { createRoot } from "react-dom/client";
import { MemoryRouter, Route, Routes, useNavigate } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

globalThis.IS_REACT_ACT_ENVIRONMENT = true;

const netz = vi.hoisted(() => ({ gets: [], deletes: [], haengt: new Set(), offen: {}, navigieren: null, antworten: {} }));
vi.mock("sonner", () => ({ toast: { success: vi.fn(), error: vi.fn(), info: vi.fn(), warning: vi.fn() } }));
vi.mock("recharts", async () => {
  const { createElement } = await import("react");
  const Leer = ({ children }) => createElement("div", null, children);
  return { ResponsiveContainer: Leer, ComposedChart: Leer, BarChart: Leer, Line: () => null, Area: () => null, Bar: () => null,
           XAxis: () => null, YAxis: () => null, Tooltip: () => null, CartesianGrid: () => null };
});

const KONTEN = [
  { id: "chefA", role: "dealer", ist_chef: true, dealer_id: "dA", company_name: "Firma A", kontonummer: "1001",
    active: true, created_at: "2026-09-01T00:00:00Z" },
  { id: "chefB", role: "dealer", ist_chef: true, dealer_id: "dB", company_name: "Firma B", kontonummer: "2002",
    active: true, created_at: "2026-09-01T00:00:00Z" },
  { id: "s1", role: "sucher", dealer_id: "dB", first_name: "Sam", last_name: "Sucher", kontonummer: "2002-1",
    active: true, created_at: "2026-09-02T00:00:00Z" },
];
const VORSCHAU_A = { wuerde_loeschen: { vehicles: 111, contracts: 0 } };
const VORSCHAU_B = { wuerde_loeschen: { vehicles: 2, contracts: 3 } };

const HEALTH_A = { aktuell: true, modell: { health: "HEALTHY", zaehler: { HEALTHY: 7 }, tag: "2026-09-27", vorschlaege_offen: 4 }, segmente: [] };
const LISTE_A = { final: [{ typ: "MONTHLY", periode_von: "2026-08-01", periode_bis: "2026-08-31" }], laufend: [] };
const BERICHT_A = { typ: "MONTHLY", periode_von: "2026-08-01", periode_bis: "2026-08-31", status: "FINAL",
                    erstellt_at: "2026-09-01T02:00:00Z", modell: { label: "Modell A" }, kennzahlen: {}, tage: [] };

function standard(url) {
  if (netz.antworten[url]) return netz.antworten[url]();
  if (url === "/admin/users") return { data: { users: KONTEN }, headers: {} };
  if (url === "/admin/dealers/dA/loeschvorschau") return { data: VORSCHAU_A };
  if (url === "/admin/dealers/dB/loeschvorschau") return { data: VORSCHAU_B };
  if (url === "/admin/market/models/mA") return { data: { id: "mA", label: "Modell A", fuel: "DIESEL", version: 1, segmente: [] } };
  if (url === "/admin/market/models/mB") return { data: { id: "mB", label: "Modell B", fuel: "DIESEL", version: 1, segmente: [] } };
  if (url === "/admin/market/health/models/mA") return { data: HEALTH_A };
  if (url === "/admin/market/reports/model/mA/list") return { data: LISTE_A };
  if (url === "/admin/market/reports/model/mB/list") return { data: { final: [], laufend: [] } };
  if (url === "/admin/market/reports/model/mA") return { data: BERICHT_A };
  return { data: {} };
}

vi.mock("@/lib/api", () => ({
  errMsg: (e, s) => e?.response?.data?.detail || e?.message || s,
  api: {
    get: vi.fn(async (url) => {
      netz.gets.push(url);
      // mehrere haengende Anfragen an dieselbe Adresse: je Adresse eine Warteschlange
      if (netz.haengt.has(url)) return new Promise((ok, nein) => { (netz.offen[url] ||= []).push({ ok, nein }); });
      return standard(url);
    }),
    post: vi.fn(async () => ({ data: { ok: true } })),
    put: vi.fn(async () => ({ data: { ok: true } })),
    patch: vi.fn(async () => ({ data: { ok: true } })),
    delete: vi.fn(async (url) => { netz.deletes.push(url); return { data: {} }; }),
  },
}));
vi.mock("@/context/AuthContext", () => ({ useAuth: () => ({ user: { id: "sa", is_super_admin: true, role: "admin" } }) }));
vi.mock("@/components/admin/ZugangsdatenKarte", () => ({ default: () => null }));
vi.mock("@/components/admin/PasswortFeld", () => ({ default: () => null }));
vi.mock("@/components/admin/KontoPruefen", () => ({ default: () => null }));

const { toast } = await import("sonner");
const { default: AdminUsers } = await import("./Users");
const { default: MarktModell } = await import("./MarktModell");
const { default: MarktBericht } = await import("./MarktBericht");

let wurzel;
let behaelter;
const el = (t) => behaelter.querySelector(`[data-testid="${t}"]`);
async function warten() { for (let i = 0; i < 8; i += 1) await act(async () => { await new Promise((r) => setTimeout(r, 0)); }); }
function Nav() { netz.navigieren = useNavigate(); return null; }
async function starten(pfad) {
  behaelter = document.createElement("div"); document.body.appendChild(behaelter); wurzel = createRoot(behaelter);
  await act(async () => {
    wurzel.render(h(MemoryRouter, { initialEntries: [pfad] }, h(Routes, null,
      h(Route, { path: "/admin/users", element: h(AdminUsers) }),
      h(Route, { path: "/admin/markt/berichte/:modell", element: h(Fragment, null, h(MarktBericht), h(Nav)) }),
      h(Route, { path: "/admin/markt/:modell", element: h(Fragment, null, h(MarktModell), h(Nav)) }))));
  });
  await warten();
}
async function gehe(pfad) { await act(async () => { netz.navigieren(pfad); }); await warten(); }
async function klick(t) { const k = el(t); if (!k) throw new Error(`nicht gefunden: ${t}`); await act(async () => { k.click(); }); await warten(); }
// i = welche der haengenden Anfragen an diese Adresse (0 = die aelteste)
async function aufloesen(url, wert, i = 0) { await act(async () => { netz.offen[url][i].ok(wert); }); await warten(); }
async function ablehnen(url, i = 0, text = "Zeitueberschreitung") {
  const e = new Error(text); e.response = { status: 500, data: { detail: text } };
  await act(async () => { netz.offen[url][i].nein(e); });
  await warten();
}
const vorschauText = () => el("admin-delete-user-vorschau")?.textContent || "";
const knopf = () => el("admin-delete-user-confirm");

beforeEach(() => {
  netz.gets.length = 0; netz.deletes.length = 0; netz.haengt = new Set(); netz.offen = {}; netz.navigieren = null; netz.antworten = {};
  vi.clearAllMocks();
});
afterEach(async () => {
  vi.restoreAllMocks();
  if (wurzel) await act(async () => { wurzel.unmount(); });
  wurzel = null; behaelter?.remove();
});

describe("Nutzerliste: Loeschvorschau gehoert zur gewaehlten Firma", () => {
  it("spaete Vorschau von Firma A erscheint nicht im Dialog von B und gibt den Knopf nicht frei", async () => {
    netz.haengt.add("/admin/dealers/dA/loeschvorschau");
    netz.haengt.add("/admin/dealers/dB/loeschvorschau");
    await starten("/admin/users");
    await klick("user-delete-btn-chefA");
    await klick("admin-delete-user-cancel");
    await klick("user-delete-btn-chefB");
    expect(el("admin-delete-user-daten").textContent).toContain("chefB");

    await aufloesen("/admin/dealers/dA/loeschvorschau", { data: VORSCHAU_A });
    expect(vorschauText()).toContain("wird geladen");
    expect(vorschauText()).not.toContain("111 × vehicles");
    expect(knopf().disabled).toBe(true);

    await aufloesen("/admin/dealers/dB/loeschvorschau", { data: VORSCHAU_B });
    expect(vorschauText()).toContain("2 × vehicles, 3 × contracts");
    expect(knopf().disabled).toBe(false);
    await klick("admin-delete-user-confirm");
    expect(netz.deletes).toEqual(["/admin/users/chefB?firma_loeschen=true"]);
  });

  it("spaete Vorschau von A ueberschreibt die schon angezeigte Vorschau von B nicht", async () => {
    netz.haengt.add("/admin/dealers/dA/loeschvorschau");
    await starten("/admin/users");
    await klick("user-delete-btn-chefA");
    await klick("admin-delete-user-cancel");
    await klick("user-delete-btn-chefB");
    expect(vorschauText()).toContain("2 × vehicles");
    await aufloesen("/admin/dealers/dA/loeschvorschau", { data: VORSCHAU_A });
    expect(vorschauText()).toContain("2 × vehicles, 3 × contracts");
    expect(vorschauText()).not.toContain("111");
  });

  it("spaeter Fehler der Vorschau von A gibt den Knopf im Dialog von B nicht frei", async () => {
    netz.haengt.add("/admin/dealers/dA/loeschvorschau");
    netz.haengt.add("/admin/dealers/dB/loeschvorschau");
    await starten("/admin/users");
    await klick("user-delete-btn-chefA");
    await klick("admin-delete-user-modal");          // Klick daneben schliesst den Dialog
    expect(el("admin-delete-user-modal")).toBeNull();
    await klick("user-delete-btn-chefB");
    await ablehnen("/admin/dealers/dA/loeschvorschau");
    expect(vorschauText()).toContain("wird geladen");
    expect(vorschauText()).not.toContain("Zeitueberschreitung");
    expect(knopf().disabled).toBe(true);
    // B's eigener Fehler gibt wie bisher frei (AD-24: Vorschau ODER ihr Fehler)
    await ablehnen("/admin/dealers/dB/loeschvorschau", 0, "Vorschau kaputt");
    expect(vorschauText()).toContain("Vorschau kaputt");
    expect(knopf().disabled).toBe(false);
  });

  it("Dialog von A erneut geoeffnet: die Antwort der ersten (abgebrochenen) Anfrage zaehlt nicht", async () => {
    netz.haengt.add("/admin/dealers/dA/loeschvorschau");
    await starten("/admin/users");
    await klick("user-delete-btn-chefA");
    await klick("admin-delete-user-cancel");
    await klick("user-delete-btn-chefA");
    await aufloesen("/admin/dealers/dA/loeschvorschau", { data: { wuerde_loeschen: { vehicles: 999 } } }, 0);
    expect(vorschauText()).not.toContain("999");
    expect(knopf().disabled).toBe(true);
    await aufloesen("/admin/dealers/dA/loeschvorschau", { data: VORSCHAU_A }, 1);
    expect(vorschauText()).toContain("111 × vehicles");
    expect(knopf().disabled).toBe(false);
  });

  it("Sucher-Konto: kein Warten auf eine Vorschau, Loeschen ohne firma_loeschen", async () => {
    netz.haengt.add("/admin/dealers/dA/loeschvorschau");
    await starten("/admin/users");
    await klick("user-delete-btn-chefA");
    await klick("admin-delete-user-cancel");
    await klick("user-delete-btn-s1");
    expect(el("admin-delete-user-vorschau")).toBeNull();
    await aufloesen("/admin/dealers/dA/loeschvorschau", { data: VORSCHAU_A });
    expect(knopf().disabled).toBe(false);
    await klick("admin-delete-user-confirm");
    expect(netz.deletes).toEqual(["/admin/users/s1"]);
    expect(toast.error).not.toHaveBeenCalled();
  });
});

describe("Modellseite: keine Health des vorigen Modells", () => {
  it("Health von Modell A verschwindet beim Wechsel zu B sofort, auch wenn B's Health haengt", async () => {
    netz.haengt.add("/admin/market/health/models/mB");
    await starten("/admin/markt/mA");
    expect(el("markt-modell-health-zeile")).toBeTruthy();
    await gehe("/admin/markt/mB");
    expect(el("markt-modell-titel").textContent).toBe("Modell B");
    expect(el("markt-modell-health-zeile")).toBeNull();
    expect(el("markt-modell-health-veraltet")).toBeNull();
    expect(behaelter.textContent).not.toContain("4 Vorschlag");
  });
});

describe("Berichtsseite: keine Liste, kein Fehler und kein Bericht des vorigen Modells", () => {
  it("Fehlerkarte von Modell A bleibt nicht ueber Modell B stehen", async () => {
    netz.haengt.add("/admin/market/reports/model/mA/list");
    netz.haengt.add("/admin/market/reports/model/mB/list");
    await starten("/admin/markt/berichte/mA");
    await ablehnen("/admin/market/reports/model/mA/list", 0, "Berichte kaputt");
    expect(el("bericht-fehler")).toBeTruthy();
    await gehe("/admin/markt/berichte/mB");
    expect(el("bericht-fehler")).toBeNull();
    await aufloesen("/admin/market/reports/model/mB/list", { data: { final: [], laufend: [] } });
    expect(el("bericht-fehler")).toBeNull();
    expect(behaelter.textContent).toContain("Noch kein Bericht");
  });

  it("Bericht und Perioden von Modell A erscheinen nicht unter Modell B, solange B laedt", async () => {
    netz.haengt.add("/admin/market/reports/model/mB/list");
    await starten("/admin/markt/berichte/mA");
    expect(el("bericht-titel").textContent).toBe("Modell A");
    expect(el("bericht-periode").textContent).toContain("final");
    await gehe("/admin/markt/berichte/mB");
    expect(el("bericht-titel").textContent).toBe("mB");
    expect(el("bericht-kopf")).toBeNull();
    expect(el("bericht-periode").textContent).not.toContain("final");
  });
});
