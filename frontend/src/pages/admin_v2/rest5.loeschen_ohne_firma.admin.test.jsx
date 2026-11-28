/**
 * Pruefung Runde 4 (rest5), Nutzerliste / Loeschdialog:
 *
 * (1) Chef-Konto OHNE dealer_id (aelterer Server ohne `ist_chef`, Rolle dealer ohne Firma) zeigte
 *     dauerhaft "Löschvorschau wird geladen…", "Endgültig löschen" blieb fuer immer gesperrt
 *     (loeschenOeffnen kehrt ohne dealer_id frueh zurueck, vorschauPasst verlangt dealer_id).
 *     Jetzt: eigener Hinweis "Konto ohne Firma — keine Vorschau möglich", Loeschen wie ein einzelnes
 *     Konto (DELETE ohne firma_loeschen — so behandelt admin_delete_user ein dealer-Konto ohne Firma).
 * (2) Testluecke admin5: der zweite Schutz in submitDelete (kein DELETE, wenn die Vorschau nicht zu
 *     genau diesem Chef/dieser Firma passt) war nicht direkt abgesichert. Der Knopf ist dann gesperrt;
 *     hier wird der Klick-Handler deshalb ueber die React-Props des Knopfs direkt ausgeloest.
 */
import { act, createElement as h } from "react";
import { createRoot } from "react-dom/client";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

globalThis.IS_REACT_ACT_ENVIRONMENT = true;

const netz = vi.hoisted(() => ({ gets: [], deletes: [], haengt: new Set(), offen: {}, konten: [] }));
vi.mock("sonner", () => ({ toast: { success: vi.fn(), error: vi.fn(), info: vi.fn(), warning: vi.fn() } }));

const CHEF_A = { id: "chefA", role: "dealer", ist_chef: true, dealer_id: "dA", company_name: "Firma A",
                 kontonummer: "1001", active: true, created_at: "2026-09-01T00:00:00Z" };
const CHEF_B = { id: "chefB", role: "dealer", ist_chef: true, dealer_id: "dB", company_name: "Firma B",
                 kontonummer: "2002", active: true, created_at: "2026-09-01T00:00:00Z" };
// aelterer Server: kein ist_chef, Rolle dealer, keine Firma
const CHEF_ALT = { id: "chefAlt", role: "dealer", kontonummer: "3003", active: true,
                   created_at: "2026-09-01T00:00:00Z" };
// Server mit ist_chef, aber dealer_id leer
const CHEF_LEER = { id: "chefLeer", role: "dealer", ist_chef: true, dealer_id: null, kontonummer: "4004",
                    active: true, created_at: "2026-09-01T00:00:00Z" };

vi.mock("@/lib/api", () => ({
  errMsg: (e, s) => e?.response?.data?.detail || e?.message || s,
  api: {
    get: vi.fn(async (url) => {
      netz.gets.push(url);
      if (netz.haengt.has(url)) return new Promise((ok, nein) => { (netz.offen[url] ||= []).push({ ok, nein }); });
      if (url === "/admin/users") return { data: { users: netz.konten }, headers: {} };
      if (url === "/admin/dealers/dA/loeschvorschau") return { data: { wuerde_loeschen: { vehicles: 111 } } };
      if (url === "/admin/dealers/dB/loeschvorschau") return { data: { wuerde_loeschen: { vehicles: 2 } } };
      return { data: {} };
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
const Modul = await import("./Users");
const { default: AdminUsers } = Modul;

let wurzel;
let behaelter;
const el = (t) => behaelter.querySelector(`[data-testid="${t}"]`);
async function warten() { for (let i = 0; i < 8; i += 1) await act(async () => { await new Promise((r) => setTimeout(r, 0)); }); }
async function starten() {
  behaelter = document.createElement("div"); document.body.appendChild(behaelter); wurzel = createRoot(behaelter);
  await act(async () => {
    wurzel.render(h(MemoryRouter, { initialEntries: ["/admin/users"] },
      h(Routes, null, h(Route, { path: "/admin/users", element: h(AdminUsers) }))));
  });
  await warten();
}
async function klick(t) { const k = el(t); if (!k) throw new Error(`nicht gefunden: ${t}`); await act(async () => { k.click(); }); await warten(); }
async function aufloesen(url, wert, i = 0) { await act(async () => { netz.offen[url][i].ok(wert); }); await warten(); }
const vorschauText = () => el("admin-delete-user-vorschau")?.textContent || "";
const knopf = () => el("admin-delete-user-confirm");
/** Klick-Handler des Knopfs direkt ausloesen — auch wenn er gesperrt ist (React feuert dann kein onClick). */
async function handlerAusloesen() {
  const k = knopf();
  const props = k[Object.keys(k).find((s) => s.startsWith("__reactProps$"))];
  expect(typeof props?.onClick).toBe("function");
  await act(async () => { await props.onClick(); });
  await warten();
}

beforeEach(() => {
  netz.gets.length = 0; netz.deletes.length = 0; netz.haengt = new Set(); netz.offen = {};
  netz.konten = [CHEF_A, CHEF_B, CHEF_ALT, CHEF_LEER];
  vi.clearAllMocks();
});
afterEach(async () => {
  if (wurzel) await act(async () => { wurzel.unmount(); });
  wurzel = null; behaelter?.remove();
});

describe("Loeschdialog: Chef-Konto ohne Firma", () => {
  it.each([["chefAlt", "aelterer Server ohne ist_chef"], ["chefLeer", "ist_chef, dealer_id leer"]])(
    "%s (%s): Hinweis statt endlosem Laden, Loeschen ohne firma_loeschen", async (id) => {
      await starten();
      await klick(`user-delete-btn-${id}`);
      expect(vorschauText()).toContain("Konto ohne Firma — keine Vorschau möglich");
      expect(vorschauText()).not.toContain("wird geladen");
      expect(el("admin-delete-user-umfang").textContent).not.toMatch(/KOMPLETTE Firma/);
      expect(el("admin-delete-user-umfang").textContent).toMatch(/nur dieses Konto/);
      expect(netz.gets.filter((u) => u.includes("loeschvorschau"))).toEqual([]);
      expect(knopf().disabled).toBe(false);
      await klick("admin-delete-user-confirm");
      expect(netz.deletes).toEqual([`/admin/users/${id}`]);
      expect(toast.error).not.toHaveBeenCalled();
      expect(el("admin-delete-user-modal")).toBeNull();
    });

  it("Chef mit Firma bleibt wie bisher: erst Vorschau, dann firma_loeschen=true", async () => {
    await starten();
    await klick("user-delete-btn-chefA");
    expect(vorschauText()).toContain("111 × vehicles");
    expect(vorschauText()).not.toContain("ohne Firma");
    await klick("admin-delete-user-confirm");
    expect(netz.deletes).toEqual(["/admin/users/chefA?firma_loeschen=true"]);
  });
});

describe("Loeschdialog: zweiter Schutz in submitDelete", () => {
  it("Vorschau haengt noch: Handler direkt ausgeloest -> kein DELETE, Hinweis im Toast", async () => {
    netz.haengt.add("/admin/dealers/dA/loeschvorschau");
    await starten();
    await klick("user-delete-btn-chefA");
    expect(knopf().disabled).toBe(true);
    await handlerAusloesen();
    expect(netz.deletes).toEqual([]);
    expect(toast.error).toHaveBeenCalledWith(expect.stringContaining("gehört nicht zu dieser Firma"));
  });

  it("spaete Vorschau von A im Dialog von B: Handler direkt ausgeloest -> kein DELETE von B", async () => {
    netz.haengt.add("/admin/dealers/dA/loeschvorschau");
    netz.haengt.add("/admin/dealers/dB/loeschvorschau");
    await starten();
    await klick("user-delete-btn-chefA");
    await klick("admin-delete-user-cancel");
    await klick("user-delete-btn-chefB");
    await aufloesen("/admin/dealers/dA/loeschvorschau", { data: { wuerde_loeschen: { vehicles: 111 } } });
    await handlerAusloesen();
    expect(netz.deletes).toEqual([]);
    expect(toast.error).toHaveBeenCalledWith(expect.stringContaining("gehört nicht zu dieser Firma"));
    // B's eigene Vorschau gibt frei und loescht genau B
    await aufloesen("/admin/dealers/dB/loeschvorschau", { data: { wuerde_loeschen: { vehicles: 2 } } });
    await handlerAusloesen();
    expect(netz.deletes).toEqual(["/admin/users/chefB?firma_loeschen=true"]);
  });

  it("loeschPfad: nur passende Vorschau gibt die Firmenloeschung frei", () => {
    const { loeschPfad } = Modul;
    const zuA = { dealerId: "dA", userId: "chefA", text: "…" };
    expect(loeschPfad(CHEF_A, zuA)).toBe("/admin/users/chefA?firma_loeschen=true");
    expect(loeschPfad(CHEF_A, { ...zuA, fehler: "kaputt", text: undefined })).toBe("/admin/users/chefA?firma_loeschen=true");
    expect(loeschPfad(CHEF_A, null)).toBeNull();
    expect(loeschPfad(CHEF_B, zuA)).toBeNull();                               // Vorschau anderer Firma
    expect(loeschPfad({ ...CHEF_A, id: "chefA2" }, zuA)).toBeNull();          // gleiche Firma, anderes Konto
    expect(loeschPfad(CHEF_ALT, null)).toBe("/admin/users/chefAlt");          // Chef ohne Firma
    expect(loeschPfad(CHEF_LEER, zuA)).toBe("/admin/users/chefLeer");
    expect(loeschPfad({ id: "s1", role: "sucher", dealer_id: "dB" }, null)).toBe("/admin/users/s1");
    expect(loeschPfad(null, zuA)).toBeNull();
  });
});
