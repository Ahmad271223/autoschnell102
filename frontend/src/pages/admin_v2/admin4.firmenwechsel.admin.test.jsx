/**
 * Go-Live-Pruefung 28.09.2026 (admin4): keine Tabelle der falschen Firma nach schnellem Wechsel.
 *
 * Nachgestellt: Chef A oeffnen, /admin/dealers/dA/sucher haengt, zu Chef B wechseln, B laedt — danach kam
 * A's Antwort und die Freischaltungstabelle zeigte Firma A unter Firma B; Freischalten/Speichern/Aufheben
 * trafen Konten der falschen Firma, eine Zahlung landete bei der falschen Firma. Dasselbe fuer die
 * Zahlungen, fuer das Neuladen nach einer Aktion, die noch fuer A lief, fuer "Weitere Vertraege" und fuer
 * die Modellseite der Marktanalyse (Id aus der URL, "Jetzt crawlen" kostet Apify-Laeufe).
 * Jede Anfrage haengt hier als Promise, bis der Test sie spaet aufloest bzw. scheitern laesst.
 */
import { act, createElement as h, Fragment } from "react";
import { createRoot } from "react-dom/client";
import { MemoryRouter, Route, Routes, useNavigate } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

globalThis.IS_REACT_ACT_ENVIRONMENT = true;

const netz = vi.hoisted(() => ({ gets: [], posts: [], haengt: new Set(), offen: {}, navigieren: null, antworten: {} }));
vi.mock("sonner", () => ({ toast: { success: vi.fn(), error: vi.fn(), info: vi.fn(), warning: vi.fn() } }));
vi.mock("recharts", async () => {
  const { createElement } = await import("react");
  const Leer = ({ children }) => createElement("div", null, children);
  return { ResponsiveContainer: Leer, ComposedChart: Leer, BarChart: Leer, Line: () => null, Area: () => null, Bar: () => null,
           XAxis: () => null, YAxis: () => null, Tooltip: () => null, CartesianGrid: () => null };
});

function chef(id, dealerId, firma) {
  return { user: { id, role: "dealer", ist_chef: true, dealer_id: dealerId, company_name: firma, email: `${id}@e2etest-mail.de`,
                   active: true, created_at: "2026-09-01T00:00:00Z" }, contracts: [], gesamt: 0 };
}
function konto(id, dealerId, kontonummer) {
  return { id, role: "sucher", dealer_id: dealerId, first_name: "Test", last_name: id, kontonummer, active: true,
           subscription: { active: false, status: "none" }, ablauf_korrigierbar: false, created_at: "2026-09-02T00:00:00Z" };
}
const SUCHER_A = [konto("sA1", "dA", "1001-1")];
const SUCHER_B = [konto("sB1", "dB", "2002-1")];
const ZAHLUNGEN_A = [{ id: "zA", amount: 150, plan: "monthly", paid_at: "2026-09-01" }];
const ZAHLUNGEN_B = [{ id: "zB", amount: 1500, plan: "yearly", paid_at: "2026-09-02" }];

function standard(url, opts) {
  if (netz.antworten[url]) return netz.antworten[url]();
  const m = /^\/admin\/users\/([^/]+)\/contracts$/.exec(url);
  if (m) {
    if (m[1] === "chefA") return { data: chef("chefA", "dA", "Firma A") };
    if (m[1] === "chefB") return { data: chef("chefB", "dB", "Firma B") };
    if (m[1] === "viele") {
      return { data: { user: { id: "viele", role: "sucher", dealer_id: "d9", company_name: "Firma Viele", active: true,
                               created_at: "2026-09-01T00:00:00Z" },
                       contracts: [{ id: `v${opts?.params?.seite || 1}`, created_at: "2026-09-01T00:00:00Z", contract_data: {} }],
                       gesamt: 40, weitere: true } };
    }
  }
  if (url === "/admin/dealers/dA/sucher") return { data: SUCHER_A, headers: {} };
  if (url === "/admin/dealers/dB/sucher") return { data: SUCHER_B, headers: {} };
  if (url === "/admin/dealers/dA/zahlungen") return { data: ZAHLUNGEN_A };
  if (url === "/admin/dealers/dB/zahlungen") return { data: ZAHLUNGEN_B };
  if (url === "/admin/market/models/mA") return { data: { id: "mA", label: "Modell A", fuel: "DIESEL", version: 1, segmente: [] } };
  if (url === "/admin/market/models/mB") return { data: { id: "mB", label: "Modell B", fuel: "DIESEL", version: 1, segmente: [] } };
  return { data: {} };
}
function schluessel(url, opts) { return opts?.params?.seite > 1 ? `${url}#${opts.params.seite}` : url; }

vi.mock("@/lib/api", () => ({
  errMsg: (e, s) => e?.response?.data?.detail || e?.message || s,
  api: {
    get: vi.fn(async (url, opts) => {
      netz.gets.push(url);
      const k = schluessel(url, opts);
      if (netz.haengt.has(k)) return new Promise((ok, nein) => { netz.offen[k] = { ok, nein }; });
      return standard(url, opts);
    }),
    post: vi.fn(async (url, body) => {
      netz.posts.push({ url, body });
      if (netz.haengt.has(`POST ${url}`)) return new Promise((ok, nein) => { netz.offen[`POST ${url}`] = { ok, nein }; });
      return { data: { ok: true } };
    }),
    put: vi.fn(async () => ({ data: { ok: true } })),
    patch: vi.fn(async (url, body) => { netz.posts.push({ url, body }); return { data: { ok: true } }; }),
    delete: vi.fn(async () => ({ data: {} })),
  },
}));
vi.mock("@/context/AuthContext", () => ({ useAuth: () => ({ user: { id: "sa", is_super_admin: true, role: "admin" } }) }));
vi.mock("@/components/admin/ZugangsdatenKarte", () => ({ default: () => null }));
vi.mock("@/components/admin/PasswortFeld", () => ({ default: () => null }));
vi.mock("@/lib/dateiOeffnen", () => ({ blobOeffnen: vi.fn() }));

const { toast } = await import("sonner");
const { default: UserDetail } = await import("./UserDetail");
const { default: MarktModell } = await import("./MarktModell");

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
      h(Route, { path: "/admin/markt/:modell", element: h(Fragment, null, h(MarktModell), h(Nav)) }))));
  });
  await warten();
}
async function gehe(pfad) { await act(async () => { netz.navigieren(pfad); }); await warten(); }
async function klick(t) { const k = el(t); if (!k) throw new Error(`nicht gefunden: ${t}`); await act(async () => { k.click(); }); await warten(); }
async function aufloesen(k, wert) { await act(async () => { netz.offen[k].ok(wert); }); await warten(); }
async function ablehnen(k, text = "Zeitueberschreitung") {
  const e = new Error(text); e.response = { status: 500, data: { detail: text } };
  await act(async () => { netz.offen[k].nein(e); });
  await warten();
}
const holt = (url) => netz.gets.filter((u) => u === url).length;

beforeEach(() => {
  netz.gets.length = 0; netz.posts.length = 0; netz.haengt = new Set(); netz.offen = {}; netz.navigieren = null; netz.antworten = {};
  vi.clearAllMocks();
});
afterEach(async () => {
  vi.restoreAllMocks();
  if (wurzel) await act(async () => { wurzel.unmount(); });
  wurzel = null; behaelter?.remove();
});

describe("Kontoansicht Chef: Sucherliste der vorigen Firma", () => {
  it("spaet ankommende Liste von Firma A erscheint nicht unter Firma B; Freischalten trifft B", async () => {
    netz.haengt.add("/admin/dealers/dA/sucher");
    await starten("/admin/users/chefA");
    expect(netz.offen["/admin/dealers/dA/sucher"]).toBeTruthy();
    await gehe("/admin/users/chefB");
    expect(behaelter.textContent).toContain("Firma B");
    expect(el("sucher-kontonummer-sB1")).toBeTruthy();

    await aufloesen("/admin/dealers/dA/sucher", { data: SUCHER_A, headers: {} });
    expect(el("sucher-kontonummer-sA1")).toBeNull();
    expect(el("abo-monat-sA1")).toBeNull();
    expect(el("sucher-kontonummer-sB1")).toBeTruthy();
    // die veraltete Anfrage holt auch keine Zahlungen von A mehr nach
    expect(holt("/admin/dealers/dA/zahlungen")).toBe(0);
    expect(el("zahlung-zB")).toBeTruthy();
    expect(el("zahlung-zA")).toBeNull();

    await klick("abo-monat-sB1");
    expect(netz.posts.map((p) => p.url)).toEqual(["/admin/sucher/sB1/abo"]);
  });

  it("spaet scheiternde Liste von Firma A: keine Fehlerkarte und kein Toast bei Firma B", async () => {
    netz.haengt.add("/admin/dealers/dA/sucher");
    await starten("/admin/users/chefA");
    await gehe("/admin/users/chefB");
    await ablehnen("/admin/dealers/dA/sucher");
    expect(el("admin-sucher-ladefehler")).toBeNull();
    expect(toast.error).not.toHaveBeenCalled();
    expect(el("sucher-kontonummer-sB1")).toBeTruthy();
  });

  it("Firma B laedt noch: die Tabelle von A verschwindet sofort (lade…), keine Knoepfe fuer A-Konten", async () => {
    netz.haengt.add("/admin/dealers/dB/sucher");
    netz.haengt.add("/admin/dealers/dB/zahlungen");
    await starten("/admin/users/chefA");
    expect(el("abo-monat-sA1")).toBeTruthy();
    expect(el("zahlung-zA")).toBeTruthy();
    await gehe("/admin/users/chefB");
    expect(behaelter.textContent).toContain("Firma B");
    expect(el("abo-monat-sA1")).toBeNull();
    expect(el("zahlung-zA")).toBeNull();
    expect(el("zahlung-nachtragen").disabled).toBe(true);
    expect(el("admin-add-sucher").disabled).toBe(true);
    await aufloesen("/admin/dealers/dB/sucher", { data: SUCHER_B, headers: {} });
    expect(el("abo-monat-sB1")).toBeTruthy();
  });
});

describe("Kontoansicht Chef: Zahlungen der vorigen Firma", () => {
  it("spaet ankommende Zahlungen von A erscheinen nicht unter B", async () => {
    netz.haengt.add("/admin/dealers/dA/zahlungen");
    await starten("/admin/users/chefA");
    await gehe("/admin/users/chefB");
    expect(el("zahlung-zB")).toBeTruthy();
    await aufloesen("/admin/dealers/dA/zahlungen", { data: ZAHLUNGEN_A });
    expect(el("zahlung-zA")).toBeNull();
    expect(el("zahlung-zB")).toBeTruthy();
  });

  it("spaet scheiternde Zahlungen von A: kein Toast, keine Fehlerkarte unter B", async () => {
    netz.haengt.add("/admin/dealers/dA/zahlungen");
    await starten("/admin/users/chefA");
    await gehe("/admin/users/chefB");
    await ablehnen("/admin/dealers/dA/zahlungen");
    expect(toast.error).not.toHaveBeenCalled();
    expect(el("admin-zahlungen-ladefehler")).toBeNull();
    expect(el("zahlung-zB")).toBeTruthy();
  });

  it("Zahlungen nicht ladbar: Fehlerkarte statt 'Noch keine Zahlungen', Nachtragen gesperrt; danach geht die Zahlung an DIESE Firma", async () => {
    let fehler = true;
    netz.antworten["/admin/dealers/dB/zahlungen"] = () => {
      if (fehler) { const e = new Error("Serverfehler"); e.response = { status: 500, data: { detail: "Serverfehler" } }; throw e; }
      return { data: ZAHLUNGEN_B };
    };
    await starten("/admin/users/chefB");
    expect(el("admin-zahlungen-ladefehler").textContent).toContain("Serverfehler");
    expect(behaelter.textContent).not.toContain("Noch keine Zahlungen");
    expect(el("zahlung-nachtragen").disabled).toBe(true);

    fehler = false;
    const erneut = [...el("admin-zahlungen-ladefehler").querySelectorAll("button")].find((b) => b.textContent.includes("Erneut laden"));
    await act(async () => { erneut.click(); });
    await warten();
    expect(el("zahlung-zB")).toBeTruthy();
    expect(el("zahlung-nachtragen").disabled).toBe(false);
    vi.spyOn(window, "prompt").mockReturnValueOnce("150,00").mockReturnValueOnce("RE-1");
    vi.spyOn(window, "confirm").mockReturnValue(true);
    await klick("zahlung-nachtragen");
    expect(netz.posts).toEqual([{ url: "/admin/dealers/dB/zahlungen", body: { amount: 150, note: "RE-1" } }]);
  });
});

describe("Kontoansicht Chef: Aktionen nur fuer Konten der angezeigten Firma", () => {
  it("Freischaltung fuer A laeuft noch, Wechsel zu B: das Neuladen danach holt NICHT A's Liste unter B", async () => {
    netz.haengt.add("POST /admin/sucher/sA1/abo");
    await starten("/admin/users/chefA");
    await klick("abo-monat-sA1");
    expect(netz.offen["POST /admin/sucher/sA1/abo"]).toBeTruthy();

    netz.haengt.add("/admin/dealers/dB/sucher");
    await gehe("/admin/users/chefB");
    const vorher = holt("/admin/dealers/dA/sucher");
    await aufloesen("POST /admin/sucher/sA1/abo", { data: { ok: true } });
    expect(holt("/admin/dealers/dA/sucher")).toBe(vorher);
    expect(el("sucher-kontonummer-sA1")).toBeNull();
    expect(el("abo-monat-sA1")).toBeNull();

    // und B's noch laufende Anfrage wurde dadurch nicht verworfen
    await aufloesen("/admin/dealers/dB/sucher", { data: SUCHER_B, headers: {} });
    expect(el("sucher-kontonummer-sB1")).toBeTruthy();
    expect(el("sucher-kontonummer-sA1")).toBeNull();
  });

  it("eine Zeile mit fremder Firma (dealer_id) wird nicht bedient — nichts gesendet, Hinweis", async () => {
    netz.antworten["/admin/dealers/dB/sucher"] = () => ({ data: [...SUCHER_B, konto("sFremd", "dA", "1001-9")], headers: {} });
    await starten("/admin/users/chefB");
    vi.spyOn(window, "confirm").mockReturnValue(true);
    await klick("abo-monat-sFremd");
    await klick("sucher-sperren-sFremd");
    await klick("ki-schalten-sFremd");
    expect(netz.posts).toEqual([]);
    expect(toast.error).toHaveBeenCalledWith(expect.stringContaining("gehört nicht zu dieser Firma"));
    await klick("abo-monat-sB1");
    expect(netz.posts.map((p) => p.url)).toEqual(["/admin/sucher/sB1/abo"]);
  });
});

describe("Kontoansicht: 'Weitere Vertraege' des vorigen Nutzers", () => {
  it("spaet scheiternde Nachlade-Anfrage zeigt beim neuen Nutzer keinen Toast", async () => {
    netz.haengt.add("/admin/users/viele/contracts#2");
    await starten("/admin/users/viele");
    await klick("vertraege-mehr");
    expect(netz.offen["/admin/users/viele/contracts#2"]).toBeTruthy();
    await gehe("/admin/users/chefB");
    await ablehnen("/admin/users/viele/contracts#2");
    expect(toast.error).not.toHaveBeenCalled();
    expect(behaelter.textContent).toContain("Firma B");
  });
});

describe("Marktanalyse Modellseite: Antwort fuer das vorige Modell", () => {
  it("spaet ankommende Antwort fuer Modell A ueberschreibt Modell B nicht", async () => {
    netz.haengt.add("/admin/market/models/mA");
    await starten("/admin/markt/mA");
    await gehe("/admin/markt/mB");
    expect(el("markt-modell-titel").textContent).toBe("Modell B");
    await aufloesen("/admin/market/models/mA", standard("/admin/market/models/mA"));
    expect(el("markt-modell-titel").textContent).toBe("Modell B");
  });

  it("spaet scheiternde Anfrage fuer Modell A zeigt keine Fehlerkarte ueber Modell B", async () => {
    netz.haengt.add("/admin/market/models/mA");
    await starten("/admin/markt/mA");
    await gehe("/admin/markt/mB");
    await ablehnen("/admin/market/models/mA");
    expect(el("markt-modell-fehler")).toBeNull();
    expect(el("markt-modell-titel").textContent).toBe("Modell B");
  });

  it("Modell B laedt noch: Modell A wird nicht mehr unter der URL von B angezeigt", async () => {
    await starten("/admin/markt/mA");
    expect(el("markt-modell-titel").textContent).toBe("Modell A");
    netz.haengt.add("/admin/market/models/mB");
    await gehe("/admin/markt/mB");
    expect(el("markt-modell-titel")).toBeNull();
    await aufloesen("/admin/market/models/mB", standard("/admin/market/models/mB"));
    expect(el("markt-modell-titel").textContent).toBe("Modell B");
  });
});
