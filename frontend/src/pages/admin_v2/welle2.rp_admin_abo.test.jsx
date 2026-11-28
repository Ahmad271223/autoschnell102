/*
 * Rollenprüfung 22.09.2026, Welle 2 — Übergaben an Team admin_abo.
 *  RP-045 (5)/RP-047/RP-144  Team: Fehler von /dealer/sale-plan ist nicht "kein Paket"
 *  RP-109                    Team: Planname aus lib/abo (wie die Abo-Karte)
 *  RP-563                    Betreiber-Bereich verlinkt Impressum/Datenschutz/AGB
 *  RP-511                    Freischaltungen: "mit Einladung", Ergebnis nach dem Anlegen
 *  RP-507 (3)                Kostenlos-Modus: Meldung "keine Zahlung erfasst"
 *  RP-394                    Betrieb: Stand des Aufräumlaufs
 *  RP-006/RP-105             ProtectedRoute fragt vor der Umleitung nach /abo einmal nach
 */
import { act, createElement as h } from "react";
import { createRoot } from "react-dom/client";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

globalThis.IS_REACT_ACT_ENVIRONMENT = true;

vi.mock("sonner", () => ({ toast: { success: vi.fn(), error: vi.fn(), info: vi.fn(), warning: vi.fn() } }));

const netz = { salePlanFehler: false, anfragen: [], kaeufer: [], posts: [], accessAntwort: { ok: true } };
vi.mock("@/lib/api", () => ({
  api: {
    get: vi.fn(async (url) => {
      if (url === "/dealer/sale-plan") {
        if (netz.salePlanFehler) {
          const e = new Error("weg");
          e.response = { status: 502 };
          throw e;
        }
        return { data: { active: false, plans: {} } };
      }
      if (url === "/dealer/sucher") return { data: [] };
      if (url === "/dealer/sucher-plans") return { data: { plans: {} } };
      if (url === "/features") return { data: { marktplatz: true } };
      if (url.startsWith("/admin/plan-requests")) return { data: netz.anfragen, headers: {} };
      if (url.startsWith("/admin/buyers")) return { data: netz.kaeufer, headers: {} };
      return { data: {}, headers: {} };
    }),
    post: vi.fn(async (url, body) => { netz.posts.push({ url, body }); return { data: netz.accessAntwort }; }),
    put: vi.fn(async () => ({ data: { ok: true } })),
  },
  errMsg: (e, f) => f || "Fehler",
}));

const auth = {
  user: { role: "dealer", id: "c1" },
  subscription: null,
  loading: false,
  verbindungsfehler: null,
  refresh: vi.fn(async () => null),
};
vi.mock("@/context/AuthContext", () => ({
  useAuth: () => ({ ...auth, logout: vi.fn() }),
}));

const { toast } = await import("sonner");
const { default: Team, teamPlanText, verkaufsplanZustand } = await import("../app/Team");
const { default: AdminLayout } = await import("./AdminLayout");
const { default: AdminFreischaltungen, einladungText, einladungErgebnisText } = await import("./Freischaltungen");
const { aufraeumlaufZustand } = await import("./Betrieb");
const { ProtectedRoute } = await import("@/components/ProtectedRoute");

let root;
let host;
beforeEach(() => {
  netz.salePlanFehler = false;
  netz.anfragen.length = 0;
  netz.kaeufer.length = 0;
  netz.posts.length = 0;
  netz.accessAntwort = { ok: true };
  auth.user = { role: "dealer", id: "c1" };
  auth.subscription = null;
  auth.refresh = vi.fn(async () => null);
  vi.mocked(toast.success).mockClear();
});
afterEach(async () => {
  if (root) await act(async () => { root.unmount(); });
  host?.remove();
  root = null;
});

async function rendern(element) {
  host = document.createElement("div");
  document.body.appendChild(host);
  root = createRoot(host);
  await act(async () => { root.render(element); });
  for (let i = 0; i < 3; i += 1) {
    await act(async () => { await new Promise((r) => setTimeout(r, 0)); });
  }
  return host;
}

describe("RP-109: Planname auf der Team-Seite", () => {
  it("dieselbe Tabelle wie die Abo-Karte, auch fuer probeN", () => {
    expect(teamPlanText("probe3")).toBe("Probe (3 Tage)");
    expect(teamPlanText("probe7")).toBe("Probe (7 Tage)");
    expect(teamPlanText("yearly")).toBe("Jahresabo");
    expect(teamPlanText("monthly")).toBe("Monatsabo");
    expect(teamPlanText(null)).toBe("—");
  });
});

describe("RP-045 (5)/RP-144: Verkaufsplan-Fehler", () => {
  it("Zustand: Fehler gewinnt, null ist 'laedt', nicht 'kein Paket'", () => {
    expect(verkaufsplanZustand(null, "weg")).toBe("fehler");
    expect(verkaufsplanZustand({ active: true }, "weg")).toBe("fehler");
    expect(verkaufsplanZustand(null, "")).toBe("laedt");
    expect(verkaufsplanZustand({ kostenlos: true }, "")).toBe("kostenlos");
    expect(verkaufsplanZustand({ active: true }, "")).toBe("aktiv");
    expect(verkaufsplanZustand({ active: false }, "")).toBe("ohne");
  });

  it("Team-Seite zeigt den Fehler mit 'Erneut versuchen' statt 'Kein Verkaufspaket aktiv'", async () => {
    netz.salePlanFehler = true;
    const el = await rendern(h(MemoryRouter, null, h(Team)));
    const fehler = el.querySelector('[data-testid="team-plan-fehler"]');
    expect(fehler).toBeTruthy();
    expect(fehler.textContent).toMatch(/Erneut versuchen/);
    expect(el.textContent).not.toMatch(/Kein Verkaufspaket aktiv/);
    // Erneut versuchen, diesmal klappt es
    netz.salePlanFehler = false;
    await act(async () => { fehler.querySelector("button").click(); });
    for (let i = 0; i < 3; i += 1) {
      await act(async () => { await new Promise((r) => setTimeout(r, 0)); });
    }
    expect(el.querySelector('[data-testid="team-plan-fehler"]')).toBeNull();
    expect(el.textContent).toMatch(/Kein Verkaufspaket aktiv/);
  });
});

describe("RP-563: Rechtliches im Betreiber-Bereich", () => {
  it("AdminLayout verlinkt Impressum, Datenschutz und AGB", async () => {
    auth.user = { role: "admin", is_super_admin: true, username: "chef" };
    const el = await rendern(h(MemoryRouter, { initialEntries: ["/admin"] },
      h(Routes, null, h(Route, { path: "/admin", element: h(AdminLayout) },
        h(Route, { index: true, element: h("div", null, "Inhalt") })))));
    const links = el.querySelector('[data-testid="rechts-links"]');
    expect(links).toBeTruthy();
    const ziele = [...links.querySelectorAll("a")].map((a) => a.getAttribute("href"));
    expect(ziele).toEqual(["/impressum", "/datenschutz", "/agb"]);
  });
});

describe("RP-511: Einladung in der Zugangsanfrage", () => {
  it("Texte", () => {
    expect(einladungText(null)).toBe("");
    expect(einladungText({ firma: "Autohaus A", gueltig: true })).toBe("mit Einladung von Autohaus A");
    expect(einladungText({ firma: "", gueltig: false })).toBe("mit Einladung (abgelaufen/verbraucht)");
    expect(einladungErgebnisText({})).toBe("");
    expect(einladungErgebnisText({ einladung: { eingeloest: true, firma: "A" } })).toMatch(/Einladung von A eingelöst/);
    expect(einladungErgebnisText({ einladung: { eingeloest: false, firma: "A" } })).toMatch(/nicht eingelöst/);
  });

  it("die Anfrage-Zeile nennt die Einladung", async () => {
    netz.anfragen.push({ id: "z1", type: "zugang", art: "kaeufer", status: "offen",
      company_name: "Partner GmbH", contact_person: "P", created_at: "2026-09-22T10:00:00+00:00",
      einladung: { firma: "Autohaus A", gueltig: true } });
    const el = await rendern(h(MemoryRouter, null, h(AdminFreischaltungen)));
    expect(el.querySelector('[data-testid="anfrage-einladung-z1"]').textContent).toMatch(/mit Einladung von Autohaus A/);
  });
});

describe("RP-507 (3): Freischalten im Kostenlos-Modus", () => {
  it("meldet 'keine Zahlung erfasst', wenn der Server kostenlos gebucht hat", async () => {
    netz.kaeufer.push({ id: "k1", company_name: "K1", access: { active: false, kostenlos: true, gesperrt: true } });
    netz.accessAntwort = { ok: true, active: true, zahlungsart: "kostenlos" };
    const el = await rendern(h(MemoryRouter, null, h(AdminFreischaltungen)));
    const knopf = [...el.querySelectorAll("button")].find((b) => b.textContent === "Freischalten");
    await act(async () => { knopf.click(); });
    for (let i = 0; i < 3; i += 1) {
      await act(async () => { await new Promise((r) => setTimeout(r, 0)); });
    }
    expect(netz.posts.find((p) => p.url === "/admin/buyers/k1/access").body).toEqual({ plan: "monthly" });
    expect(toast.success).toHaveBeenCalledWith(expect.stringMatching(/keine Zahlung erfasst/));
  });
});

describe("RP-394: Aufräumlauf auf der Betriebsseite", () => {
  it("Zustand", () => {
    expect(aufraeumlaufZustand(null).tone).toBe("yellow");
    expect(aufraeumlaufZustand({ letzter_lauf: "x", fehlgeschlagen: [] }))
      .toEqual({ tone: "green", text: "vollständig" });
    expect(aufraeumlaufZustand({ letzter_lauf: "x", fehlgeschlagen: ["a", "b"] }).text)
      .toBe("2 Schritt(e) gescheitert");
    expect(aufraeumlaufZustand({ letzter_lauf: "x", fehlgeschlagen: [], ueberfaellig: true, grenze_h: 3 }).tone)
      .toBe("red");
  });
});

describe("RP-006/RP-105: ProtectedRoute fragt vor /abo einmal nach", () => {
  const baum = () => h(MemoryRouter, { initialEntries: ["/app/vergleich"] },
    h(Routes, null,
      h(Route, { path: "/app/vergleich", element: h(ProtectedRoute, null, h("div", { "data-testid": "seite" }, "Seite")) }),
      h(Route, { path: "/abo", element: h("div", { "data-testid": "abo" }, "Abo") })));

  it("ohne Abo: genau ein refresh(), danach /abo", async () => {
    auth.user = { role: "sucher", id: "s1" };
    const el = await rendern(baum());
    expect(auth.refresh).toHaveBeenCalledTimes(1);
    expect(el.querySelector('[data-testid="abo"]')).toBeTruthy();
    expect(el.querySelector('[data-testid="seite"]')).toBeNull();
  });

  it("mit aktivem Abo: kein Nachfragen", async () => {
    auth.user = { role: "sucher", id: "s1" };
    auth.subscription = { active: true };
    const el = await rendern(baum());
    expect(auth.refresh).not.toHaveBeenCalled();
    expect(el.querySelector('[data-testid="seite"]')).toBeTruthy();
  });
});
