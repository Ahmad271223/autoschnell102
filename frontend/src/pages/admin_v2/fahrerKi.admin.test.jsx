/**
 * KI-Freischaltung je Fahrer + Fahrer-Deckel (Wunsch Ahmad 26.09.2026 abends):
 *  - Admin → Fahrer: je Fahrer Badge "KI: ja/nein" und Knopf "KI freischalten" /
 *    "KI sperren" (POST /admin/drivers/{id}/ki {aktiv}); Sperren fragt nach;
 *    danach wird die Liste neu geladen. Ohne Super-Admin ist der Knopf gesperrt.
 *  - Admin → Betrieb, KI-Kasten: "Kostenbremse" nennt "10 € je Fahrer".
 */
import { act, createElement as h } from "react";
import { createRoot } from "react-dom/client";
import { MemoryRouter } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

globalThis.IS_REACT_ACT_ENVIRONMENT = true;

const netz = vi.hoisted(() => ({ posts: [], superAdmin: true, kiF1: false }));
vi.mock("sonner", () => ({ toast: { success: vi.fn(), error: vi.fn(), info: vi.fn(), warning: vi.fn() } }));
vi.mock("@/lib/api", () => ({
  errMsg: (e, s) => e?.message || s,
  api: {
    get: vi.fn(async (url) => {
      if (url === "/admin/drivers") {
        return { data: [
          { id: "f1", display_name: "Fahrer Eins", kontonummer: "FD-1", active: true, ki_aktiv: netz.kiF1,
            firmen: ["Demo GmbH"], verknuepfungen: 1, termine: 2, termine_offen: 1, created_at: "2026-09-01T00:00:00Z" },
          { id: "f2", display_name: "Fahrer Zwei", kontonummer: "FD-2", active: true, ki_aktiv: true,
            firmen: [], verknuepfungen: 0, termine: 0, termine_offen: 0, created_at: "2026-09-02T00:00:00Z" },
        ], headers: {} };
      }
      if (url === "/admin/ki") {
        return { data: { aktiv: true, modell: "claude-sonnet", zeitraum_tage: 30, bewertungen: 3, je_art: {}, je_status: { ok: 3 },
                         dauer_median_ms: 1200, dauer_p95_ms: 4000, kosten_usd_geschaetzt: 0.12, tokens: {},
                         budget: { monat_eur: 15, lauf_max_ct: 15, fahrer_eur: 10 }, lernfaelle: { gesamt: 0 },
                         erfahrungswerte: {}, letzte_fehler: [] } };
      }
      if (url === "/admin/betrieb") {
        return { data: { alarme: [], alarm_uebersicht: { gesamt: 0, je_typ: [] }, datei_loeschungen_offen: 0,
                         abo_vorgaenge_haengend: 0, backup: {}, wartungsmodus: false, alarm_empfaenger: "x@y.de" } };
      }
      return { data: {}, headers: {} };
    }),
    post: vi.fn(async (url, body) => { netz.posts.push({ url, body }); if (url.endsWith("/ki")) netz.kiF1 = body.aktiv; return { data: { ok: true } }; }),
    put: vi.fn(async () => ({ data: {} })),
    patch: vi.fn(async () => ({ data: {} })),
    delete: vi.fn(async () => ({ data: {} })),
  },
}));
vi.mock("@/context/AuthContext", () => ({ useAuth: () => ({ user: { id: "sa", is_super_admin: netz.superAdmin, role: "admin" } }) }));
vi.mock("@/components/admin/FahrerAnlegenDialog", () => ({ default: () => null }));
vi.mock("@/components/admin/KontoPruefen", () => ({ default: () => null }));
vi.mock("@/components/admin/PasswortFeld", () => ({ default: () => null }));

const { default: AdminFahrer } = await import("./Fahrer");
const { default: AdminBetrieb } = await import("./Betrieb");
const { api } = await import("@/lib/api");

let wurzel;
let behaelter;
const el = (t) => behaelter.querySelector(`[data-testid="${t}"]`);
async function warten() {
  for (let i = 0; i < 6; i += 1) await act(async () => { await new Promise((r) => setTimeout(r, 0)); });
}
async function starten(Komponente) {
  behaelter = document.createElement("div");
  document.body.appendChild(behaelter);
  wurzel = createRoot(behaelter);
  await act(async () => { wurzel.render(h(MemoryRouter, null, h(Komponente))); });
  await warten();
}
async function klick(t) {
  const k = el(t);
  if (!k) throw new Error(`nicht gefunden: ${t}`);
  await act(async () => { k.click(); });
  await warten();
}

beforeEach(() => { netz.posts.length = 0; netz.superAdmin = true; netz.kiF1 = false; vi.clearAllMocks(); });
afterEach(async () => { if (wurzel) await act(async () => { wurzel.unmount(); }); wurzel = null; behaelter?.remove(); });

describe("KI-Freischaltung je Fahrer (Admin → Fahrer)", () => {
  it("zeigt je Fahrer den Stand und schaltet per Knopf frei bzw. sperrt mit Rückfrage", async () => {
    await starten(AdminFahrer);
    expect(el("fahrer-ki-f1").textContent).toContain("KI: nein");
    expect(el("fahrer-ki-f2").textContent).toContain("KI: ja");
    expect(el("fahrer-ki-schalten-f1").textContent).toContain("KI freischalten");
    expect(el("fahrer-ki-schalten-f2").textContent).toContain("KI sperren");
    await klick("fahrer-ki-schalten-f1");
    expect(netz.posts).toEqual([{ url: "/admin/drivers/f1/ki", body: { aktiv: true } }]);
    expect(api.get.mock.calls.filter(([u]) => u === "/admin/drivers").length).toBeGreaterThanOrEqual(2);
    expect(el("fahrer-ki-f1").textContent).toContain("KI: ja");
    // Sperren: Rückfrage — abgelehnt = kein Aufruf
    const bestaetigen = vi.spyOn(window, "confirm").mockReturnValue(false);
    await klick("fahrer-ki-schalten-f2");
    expect(netz.posts).toHaveLength(1);
    bestaetigen.mockReturnValue(true);
    await klick("fahrer-ki-schalten-f2");
    expect(netz.posts[1]).toEqual({ url: "/admin/drivers/f2/ki", body: { aktiv: false } });
    expect(bestaetigen.mock.calls.at(-1)[0]).toMatch(/Fahrer Zwei/);
    bestaetigen.mockRestore();
  });

  it("ohne Super-Admin ist der Knopf gesperrt", async () => {
    netz.superAdmin = false;
    await starten(AdminFahrer);
    expect(el("fahrer-ki-schalten-f1").disabled).toBe(true);
    expect(el("fahrer-ki-f1").textContent).toContain("KI: nein");
  });
});

describe("Fahrer-Deckel im Betrieb-Kasten", () => {
  it("Kostenbremse nennt 10 € je Fahrer", async () => {
    await starten(AdminBetrieb);
    const text = el("ki-betrieb-budget").textContent;
    expect(text).toMatch(/15 € je Nutzer\/Firma und Monat/);
    expect(text).toMatch(/10 € je Fahrer/);
    expect(text).toMatch(/15 ct je Lauf/);
  });
});
