/*
 * Rollenpruefung 22.09.2026 — Team admin_abo (Betreiber-Oberflaeche).
 *  RP-224/RP-375  Anfrage-Zeile nennt den echten Plan; Proben werden hier nicht
 *                 freigeschaltet (vorher "1 Monat · 150 €" und still kostenlos)
 *  RP-225/RP-376  "Ja, freischalten" bindet die Buchung an die Anfrage (anfrage_id)
 *  RP-509         Verlaengern-Knopf fuer aktive, bezahlte Kaeufer-Zugaenge
 *  RP-033/RP-132  Chef = Server-Feld ist_chef (Zeiger), nicht die rohe Rolle
 *  RP-232/RP-383  Team-Liste: Probe-Abo nicht mehr als "monatlich"
 */
import { act, createElement as h } from "react";
import { createRoot } from "react-dom/client";
import { MemoryRouter } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

globalThis.IS_REACT_ACT_ENVIRONMENT = true;

vi.mock("sonner", () => ({ toast: { success: vi.fn(), error: vi.fn(), info: vi.fn(), warning: vi.fn() } }));

const anfragen = [];
const kaeufer = [];
const aufrufe = [];
vi.mock("@/lib/api", () => ({
  api: {
    get: vi.fn(async (url) => {
      if (url.startsWith("/admin/plan-requests")) return { data: anfragen, headers: {} };
      if (url.startsWith("/admin/buyers")) return { data: kaeufer, headers: {} };
      return { data: [], headers: {} };
    }),
    post: vi.fn(async (url, body) => { aufrufe.push({ url, body }); return { data: { ok: true } }; }),
    put: vi.fn(async (url, body) => { aufrufe.push({ url, body }); return { data: { ok: true } }; }),
  },
  errMsg: (e, f) => f || "Fehler",
}));

const { default: AdminFreischaltungen, anfragePlan, verlaengerungText } = await import("./Freischaltungen");
const { istChefKonto } = await import("./Users");
const { istHauptchef, neuerSchluessel } = await import("./UserDetail");
const { teamPlanText } = await import("../app/Team");

let root;
let host;
beforeEach(() => { anfragen.length = 0; kaeufer.length = 0; aufrufe.length = 0; });
afterEach(async () => {
  if (root) await act(async () => { root.unmount(); });
  host?.remove();
  root = null;
});

async function rendern() {
  host = document.createElement("div");
  document.body.appendChild(host);
  root = createRoot(host);
  await act(async () => { root.render(h(MemoryRouter, null, h(AdminFreischaltungen))); });
  await act(async () => { await new Promise((r) => setTimeout(r, 0)); });
  return host;
}

const anfrage = (id, wanted_plan) => ({
  id, type: "sucher_abo", status: "offen", subject_user_id: `s-${id}`,
  sucher_name: `Sucher ${id}`, company_name: "Firma", wanted_plan, created_at: "2026-09-22T10:00:00+00:00",
});

describe("RP-224: Plan einer Sucher-Abo-Anfrage", () => {
  it("Tabelle statt 'alles ausser Jahr ist 150 €'", () => {
    expect(anfragePlan("monthly")).toEqual({ text: "1 Monat · 150 €", freischaltbar: true });
    expect(anfragePlan("yearly").text).toBe("1 Jahr · 1.500 €");
    expect(anfragePlan(undefined).text).toBe("1 Monat · 150 €");
    expect(anfragePlan("probe3").freischaltbar).toBe(false);
    expect(anfragePlan("probe5").text).toMatch(/Probe · 5 Tage/);
    expect(anfragePlan("quatsch")).toEqual(expect.objectContaining({ freischaltbar: false }));
  });

  it("Probe-Anfrage: richtiger Text und KEIN Freischalt-Knopf", async () => {
    anfragen.push(anfrage("a1", "probe3"), anfrage("a2", "monthly"));
    const el = await rendern();
    expect(el.querySelector('[data-testid="anfrage-plan-a1"]').textContent).toMatch(/Probe · 3 Tage/);
    expect(el.querySelector('[data-testid="abo-ja-a1"]')).toBeNull();
    expect(el.querySelector('[data-testid="abo-nein-a1"]')).toBeTruthy();
    expect(el.querySelector('[data-testid="anfrage-plan-a2"]').textContent).toMatch(/1 Monat · 150 €/);
    expect(el.querySelector('[data-testid="abo-ja-a2"]')).toBeTruthy();
  });

  it("RP-225: 'Ja, freischalten' schickt die Anfrage-ID mit", async () => {
    anfragen.push(anfrage("a3", "yearly"));
    const el = await rendern();
    await act(async () => { el.querySelector('[data-testid="abo-ja-a3"]').click(); });
    await act(async () => { await new Promise((r) => setTimeout(r, 0)); });
    const buchung = aufrufe.find((a) => a.url === "/admin/sucher/s-a3/abo");
    expect(buchung.body).toEqual({ plan: "yearly", anfrage_id: "a3" });
  });
});

describe("RP-509: Kaeufer-Zugang verlaengern", () => {
  it("bezahlter aktiver Zugang: Knopf 'Verlaengern'; kostenlos: keiner", async () => {
    kaeufer.push(
      { id: "k1", company_name: "K1", access: { active: true, kostenlos: false, expires_at: "2026-10-01" } },
      { id: "k2", company_name: "K2", access: { active: true, kostenlos: true } });
    const el = await rendern();
    expect(el.querySelector('[data-testid="buyer-verlaengern-k1"]')).toBeTruthy();
    expect(el.querySelector('[data-testid="buyer-verlaengern-k2"]')).toBeNull();
    await act(async () => { el.querySelector('[data-testid="buyer-verlaengern-k1"]').click(); });
    await act(async () => { await new Promise((r) => setTimeout(r, 0)); });
    expect(aufrufe.find((a) => a.url === "/admin/buyers/k1/access").body).toEqual({ plan: "monthly" });
  });
});

// Rollenprüfung 22.09.2026 (RP-509, Welle 3): Käufer-Anfrage "Verlängerung"
describe("RP-509: Verlaengerungs-Anfrage eines Kaeufers", () => {
  it("Badge-Text nennt das bisherige Ablaufdatum", () => {
    expect(verlaengerungText({ verlaengerung: true, zugang_bis: "2026-10-01T21:59:59+00:00" }))
      .toBe("Verlängerung (bis 01.10.2026)");
    expect(verlaengerungText({ verlaengerung: true, zugang_bis: null })).toBe("Verlängerung");
    expect(verlaengerungText({ verlaengerung: true, zugang_bis: "kaputt" })).toBe("Verlängerung");
    expect(verlaengerungText({ verlaengerung: false, zugang_bis: "2026-10-01" })).toBe("");
    expect(verlaengerungText(null)).toBe("");
  });

  it("Zeile: Badge nur bei Verlaengerung", async () => {
    anfragen.push(
      { id: "b1", type: "buyer_access", status: "offen", company_name: "K1", buyer_user_id: "k1",
        wanted: "Verlängerung Marktplatz-Zugang (kostenlos)", verlaengerung: true,
        zugang_bis: "2026-10-01T21:59:59+00:00", created_at: "2026-09-22T10:00:00+00:00" },
      { id: "b2", type: "buyer_access", status: "offen", company_name: "K2", buyer_user_id: "k2",
        wanted: "Marktplatz-Zugang (kostenlos)", verlaengerung: false, zugang_bis: null,
        created_at: "2026-09-22T10:00:00+00:00" });
    const el = await rendern();
    expect(el.querySelector('[data-testid="anfrage-verlaengerung-b1"]').textContent)
      .toBe("Verlängerung (bis 01.10.2026)");
    expect(el.querySelector('[data-testid="anfrage-verlaengerung-b2"]')).toBeNull();
  });
});

describe("RP-033: Chef am Zeiger", () => {
  it("Server-Feld gewinnt, alte Server: Rolle", () => {
    expect(istChefKonto({ role: "dealer", ist_chef: false })).toBe(false);
    expect(istChefKonto({ role: "dealer", ist_chef: true })).toBe(true);
    expect(istChefKonto({ role: "dealer" })).toBe(true);
    expect(istChefKonto({ role: "sucher" })).toBe(false);
    expect(istHauptchef({ role: "dealer", ist_chef: false })).toBe(false);
    expect(istHauptchef({ role: "dealer" })).toBe(true);
    expect(istHauptchef(null)).toBe(false);
  });
});

describe("RP-225: Schluessel je Klick", () => {
  it("passt ins Server-Muster und ist eindeutig", () => {
    const a = neuerSchluessel();
    const b = neuerSchluessel();
    expect(a).toMatch(/^[A-Za-z0-9_-]{8,80}$/);
    expect(a).not.toBe(b);
  });
});

describe("RP-232: Team-Liste", () => {
  // Welle 2 (RP-109): Namen aus lib/abo.js — dieselben wie auf der Abo-Karte.
  it("Probe ist nicht 'monatlich'", () => {
    expect(teamPlanText("probe3")).toBe("Probe (3 Tage)");
    expect(teamPlanText("probe5")).toBe("Probe (5 Tage)");
    expect(teamPlanText("yearly")).toBe("Jahresabo");
    expect(teamPlanText("monthly")).toBe("Monatsabo");
    expect(teamPlanText("neu")).toBe("neu");
  });
});
