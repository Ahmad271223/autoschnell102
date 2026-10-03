/**
 * KI-Freischaltung je Konto (Wunsch Ahmad 25.09.2026 abends, wie das Abo):
 * Firmenansicht des Betreibers zeigt je Sucher eine Spalte "KI" mit
 * Badge (ja/nein) und Knopf "KI freischalten" / "KI sperren"
 * (POST /admin/sucher/{id}/ki {aktiv}); Sperren fragt nach; danach wird
 * die Liste neu geladen. Ohne Super-Admin ist der Knopf gesperrt.
 */
import { act, createElement as h } from "react";
import { createRoot } from "react-dom/client";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

globalThis.IS_REACT_ACT_ENVIRONMENT = true;

const netz = vi.hoisted(() => ({ posts: [], superAdmin: true, kiChef: false }));
vi.mock("sonner", () => ({ toast: { success: vi.fn(), error: vi.fn(), info: vi.fn(), warning: vi.fn() } }));
vi.mock("@/lib/api", () => ({
  errMsg: (e, s) => e?.message || s,
  api: {
    get: vi.fn(async (url) => {
      if (url === "/admin/users/u1/contracts") {
        return { data: { user: { id: "u1", role: "dealer", dealer_id: "d1", ist_hauptchef: true, company_name: "Demo GmbH",
                                 email: "chef@e2etest-mail.de", active: true, created_at: "2026-09-01T00:00:00Z" },
                         contracts: [], gesamt: 0 } };
      }
      if (url === "/admin/dealers/d1/sucher") {
        return { data: [
          { id: "u1", role: "dealer", ist_chef: true, active: true, ki_aktiv: netz.kiChef, kontonummer: "1001",
            subscription: { active: false }, created_at: "2026-09-01T00:00:00Z" },
          { id: "s2", role: "sucher", first_name: "Susi", last_name: "S", active: true, ki_aktiv: true, kontonummer: "1002",
            subscription: { active: true, plan: "monthly", expires_at: "2026-10-25T00:00:00Z" }, created_at: "2026-09-02T00:00:00Z" },
        ], headers: {} };
      }
      if (url === "/admin/dealers/d1/zahlungen") return { data: [] };
      return { data: {} };
    }),
    post: vi.fn(async (url, body) => { netz.posts.push({ url, body }); if (url.endsWith("/ki")) netz.kiChef = body.aktiv; return { data: { ok: true } }; }),
    put: vi.fn(async () => ({ data: {} })),
    patch: vi.fn(async () => ({ data: {} })),
    delete: vi.fn(async () => ({ data: {} })),
  },
}));
vi.mock("@/context/AuthContext", () => ({ useAuth: () => ({ user: { id: "sa", is_super_admin: netz.superAdmin, role: "admin" } }) }));
vi.mock("@/components/admin/ZugangsdatenKarte", () => ({ default: () => null }));
vi.mock("@/components/admin/PasswortFeld", () => ({ default: () => null }));
vi.mock("@/lib/dateiOeffnen", () => ({ blobOeffnen: vi.fn() }));

const { default: UserDetail } = await import("./UserDetail");
const { api } = await import("@/lib/api");

let wurzel;
let behaelter;
const el = (t) => behaelter.querySelector(`[data-testid="${t}"]`);
async function warten() {
  for (let i = 0; i < 6; i += 1) await act(async () => { await new Promise((r) => setTimeout(r, 0)); });
}
async function starten() {
  behaelter = document.createElement("div");
  document.body.appendChild(behaelter);
  wurzel = createRoot(behaelter);
  await act(async () => {
    wurzel.render(h(MemoryRouter, { initialEntries: ["/admin/users/u1"] },
      h(Routes, null, h(Route, { path: "/admin/users/:id", element: h(UserDetail) }))));
  });
  await warten();
}
async function klick(t) {
  const k = el(t);
  if (!k) throw new Error(`nicht gefunden: ${t}`);
  await act(async () => { k.click(); });
  await warten();
}

beforeEach(() => { netz.posts.length = 0; netz.superAdmin = true; netz.kiChef = false; vi.clearAllMocks(); });
afterEach(async () => { if (wurzel) await act(async () => { wurzel.unmount(); }); wurzel = null; behaelter?.remove(); });

describe("KI-Freischaltung je Konto", () => {
  it("zeigt je Konto den Stand und schaltet per Knopf frei bzw. sperrt mit Rückfrage", async () => {
    await starten();
    expect(el("ki-zelle-u1").textContent).toContain("KI: nein");
    expect(el("ki-zelle-s2").textContent).toContain("KI: ja");
    expect(el("ki-schalten-u1").textContent).toContain("KI freischalten");
    expect(el("ki-schalten-s2").textContent).toContain("KI sperren");
    await klick("ki-schalten-u1");
    expect(netz.posts).toEqual([{ url: "/admin/sucher/u1/ki", body: { aktiv: true } }]);
    expect(api.get.mock.calls.filter(([u]) => u === "/admin/dealers/d1/sucher").length).toBeGreaterThanOrEqual(2);
    expect(el("ki-zelle-u1").textContent).toContain("KI: ja");
    // Sperren: Rückfrage — abgelehnt = kein Aufruf
    const bestaetigen = vi.spyOn(window, "confirm").mockReturnValue(false);
    await klick("ki-schalten-s2");
    expect(netz.posts).toHaveLength(1);
    bestaetigen.mockReturnValue(true);
    await klick("ki-schalten-s2");
    expect(netz.posts[1]).toEqual({ url: "/admin/sucher/s2/ki", body: { aktiv: false } });
    bestaetigen.mockRestore();
  });

  it("ohne Super-Admin ist der Knopf gesperrt", async () => {
    netz.superAdmin = false;
    await starten();
    expect(el("ki-schalten-u1").disabled).toBe(true);
  });
});
