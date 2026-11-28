/*
 * Rollenprüfung 22.09.2026, Welle 2 — Marktplatz-Seite (Team markt_kaeufer),
 * gerendert mit nachgebildetem Server (buyerApi):
 *
 * RP-098 Nr. 3  ohne Anmeldung: öffentliche Fahrzeuge statt Umleitung zur
 *               Anmeldung; Marken aus der Liste; kein "Meine Anfragen"
 *               (Einladungslink und Bezahlmodus führen weiter zur Anmeldung)
 * RP-098 Nr. 9  X-Truncated -> "Weitere Fahrzeuge laden" holt Seite 2
 * RP-509        "Zugang bis …" und ab 7 Tagen vorher "Verlängerung anfragen"
 * RP-456        Gegenangebot zeigt beim Tippen "= 12.500,00 €"
 * RP-519        (Welle 3) Ablauf des Inserats im Detail und unter laufenden
 *               eigenen Anfragen
 */
import { act, createElement as h } from "react";
import { createRoot } from "react-dom/client";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("sonner", () => ({ toast: { success: vi.fn(), error: vi.fn(), info: vi.fn(), warning: vi.fn() } }));

globalThis.IS_REACT_ACT_ENVIRONMENT = true;

const { BuyerAuthProvider, buyerApi } = await import("@/context/BuyerContext");
const { TOKEN_KAEUFER, tokenSetzen } = await import("@/lib/sitzung");
const { katalogVergessen } = await import("@/lib/katalog");
const { default: Marktplatz } = await import("./Marktplatz");

const karte = (id, marke = "VW", modell = "Golf") => ({
  id, dealer_id: "d1", title: `${marke} ${modell} ${id}`, status: "veroeffentlicht",
  data: { make_label: marke, model_label: modell, mileage: 1000 },
  photos: [], dealer_photos: [], price: 9900, price_level: "oeffentlich",
  dealer: { id: "d1", company_name: "Autohaus Test", city: "Hannover", phone: "" },
});

let root;
let host;
let zugang;
let listen;          // (url) -> Antwort der Fahrzeugliste
let get;
let post;
let interessen;

function antwortFuer(url) {
  if (url === "/buyer/me") return { data: { id: "k1", role: "b2b_buyer", company_name: "Käufer GmbH" } };
  if (url === "/marktplatz/zugang") return { data: zugang };
  if (url === "/manual/makes") return { data: [] };
  if (url === "/marktplatz/favoriten") return { data: { listing_ids: [] } };
  if (url.startsWith("/marktplatz/listings")) return listen(url);
  if (url === "/buyer/interessen/zaehler") return { data: { am_zug: 0, neu: 0 } };
  if (url === "/buyer/interessen") return { data: interessen };
  throw new Error(`unerwartet: ${url}`);
}

beforeEach(() => {
  window.localStorage.clear();
  window.sessionStorage.clear();
  katalogVergessen();
  zugang = { active: true, kostenlos: true, gesperrt: false };
  listen = () => ({ data: [karte("l1"), karte("l2", "Škoda", "Octavia")] });
  interessen = [];
  get = vi.spyOn(buyerApi, "get").mockImplementation(async (url) => antwortFuer(url));
  post = vi.spyOn(buyerApi, "post").mockResolvedValue({ data: { ok: true, hinweis: "ok" } });
  window.scrollTo = () => {};
});

afterEach(async () => {
  if (root) await act(async () => { root.unmount(); });
  host?.remove();
  root = null;
  vi.restoreAllMocks();
});

async function rendern(pfad = "/markt") {
  host = document.createElement("div");
  document.body.appendChild(host);
  root = createRoot(host);
  await act(async () => {
    root.render(h(MemoryRouter, { initialEntries: [pfad] },
      h(BuyerAuthProvider, null,
        h(Routes, null,
          h(Route, { path: "/markt", element: h(Marktplatz) }),
          h(Route, { path: "/markt/login", element: h("div", { "data-testid": "login-seite" }) })))));
  });
  for (let i = 0; i < 6; i += 1) await act(async () => { await Promise.resolve(); });
  return host;
}

const knopf = (el, testid) => el.querySelector(`[data-testid="${testid}"]`);

describe("Marktplatz ohne Anmeldung (RP-098 Nr. 3)", () => {
  it("zeigt die öffentlichen Fahrzeuge statt zur Anmeldung umzuleiten", async () => {
    const el = await rendern();
    expect(knopf(el, "login-seite")).toBeNull();
    expect(knopf(el, "markt-page")).toBeTruthy();
    expect(knopf(el, "markt-l1")).toBeTruthy();
    expect(knopf(el, "markt-anmelden")).toBeTruthy();
    // nur mit Konto
    expect(knopf(el, "meine-anfragen-btn")).toBeNull();
    expect(knopf(el, "filter-favoriten")).toBeNull();
    // keine Konto-Abrufe ohne Anmeldung
    const urls = get.mock.calls.map((c) => c[0]);
    expect(urls).not.toContain("/marktplatz/zugang");
    expect(urls).not.toContain("/manual/makes");
    // Marken aus der Liste (Katalog gibt es nur angemeldet)
    const marken = [...el.querySelectorAll('[data-testid="markt-filter"] select')[0].options].map((o) => o.value);
    expect(marken).toEqual(["", "Škoda", "VW"]);
  });

  it("Einladungslink führt weiter zur Anmeldung", async () => {
    const el = await rendern("/markt?invite=tok1");
    expect(knopf(el, "login-seite")).toBeTruthy();
  });

  it("Bezahlmodus (401 ohne Anmeldung) führt zur Anmeldung", async () => {
    listen = () => {
      const e = new Error("401");
      e.response = { status: 401, data: { detail: "Nicht authentifiziert" } };
      throw e;
    };
    const el = await rendern();
    expect(knopf(el, "login-seite")).toBeTruthy();
  });
});

describe("Marktplatz angemeldet (Welle 2)", () => {
  beforeEach(() => { tokenSetzen(TOKEN_KAEUFER, "tok"); });

  it("RP-098 Nr. 9: X-Truncated -> 'Weitere Fahrzeuge laden' holt Seite 2 und hängt an", async () => {
    listen = (url) => (url.includes("page=2")
      ? { data: [karte("l2"), karte("l3")], headers: { "x-truncated": "0" } }
      : { data: [karte("l1"), karte("l2")], headers: { "x-truncated": "1" } });
    const el = await rendern();
    expect(el.textContent).toMatch(/2\+ Fahrzeuge/);
    await act(async () => { knopf(el, "markt-mehr-laden").click(); });
    for (let i = 0; i < 4; i += 1) await act(async () => { await Promise.resolve(); });
    expect(get.mock.calls.map((c) => c[0])).toContain("/marktplatz/listings?page=2");
    expect(knopf(el, "markt-l3")).toBeTruthy();
    expect(el.querySelectorAll('[data-testid="markt-l2"]').length).toBe(1);
    expect(knopf(el, "markt-mehr-laden")).toBeNull();
  });

  it("RP-509: Ablaufdatum im Kopf, Verlängerung erst kurz vor Ablauf", async () => {
    const in3 = new Date(Date.now() + 3 * 86400000);
    zugang = { active: true, kostenlos: false, gesperrt: false, expires_at: in3.toISOString() };
    const el = await rendern();
    const datum = in3.toLocaleDateString("de-DE", { day: "2-digit", month: "2-digit", year: "numeric" });
    expect(knopf(el, "markt-zugang-bis").textContent).toBe(`Zugang bis ${datum}`);
    await act(async () => { knopf(el, "markt-verlaengern").click(); });
    expect(post).toHaveBeenCalledWith("/buyer/zugang-anfrage");
  });

  it("RP-509: 30 Tage Restlaufzeit -> Datum, aber kein Verlängern; kostenlos -> nichts", async () => {
    zugang = { active: true, kostenlos: false, gesperrt: false,
               expires_at: new Date(Date.now() + 30 * 86400000).toISOString() };
    let el = await rendern();
    expect(knopf(el, "markt-zugang-bis")).toBeTruthy();
    expect(knopf(el, "markt-verlaengern")).toBeNull();
    await act(async () => { root.unmount(); });
    host.remove();
    zugang = { active: true, kostenlos: true, gesperrt: false };
    el = await rendern();
    expect(knopf(el, "markt-zugang-bis")).toBeNull();
  });

  it("RP-456: Gegenangebot zeigt beim Tippen, wie der Betrag verstanden wird", async () => {
    interessen = [{ id: "i-gg", listing_id: "l3", dealer_id: "d1", listing_title: "Passat",
                    status: "gegenangebot", offer: 20000, counter_offer: 21000,
                    created_at: "2026-09-20T08:00:00+00:00", updated_at: "2026-09-20T08:00:00+00:00",
                    history: [] }];
    const el = await rendern();
    await act(async () => { knopf(el, "meine-anfragen-btn").click(); });
    for (let i = 0; i < 4; i += 1) await act(async () => { await Promise.resolve(); });
    await act(async () => { knopf(el, "kaeufer-gegenangebot-i-gg").click(); });
    const feld = knopf(el, "kaeufer-gegenangebot-betrag-i-gg");
    const tippen = async (wert) => act(async () => {
      const setter = Object.getOwnPropertyDescriptor(window.HTMLInputElement.prototype, "value").set;
      setter.call(feld, wert);
      feld.dispatchEvent(new Event("input", { bubbles: true }));
    });
    await tippen("12.500");
    expect(knopf(el, "kaeufer-gegenangebot-verstanden-i-gg").textContent.replace(/\s/g, " "))
      .toBe("= 12.500,00 €");
    await tippen("zwölf");
    expect(feld.getAttribute("aria-invalid")).toBe("true");
  });

  it("RP-519: Detail zeigt, bis wann das Inserat online ist", async () => {
    const bis = new Date(Date.now() + 10 * 86400000);
    listen = () => ({ data: [{ ...karte("l1"), laeuft_ab_am: bis.toISOString() }, karte("l2")] });
    const el = await rendern();
    await act(async () => { knopf(el, "markt-l1").click(); });
    const datum = bis.toLocaleDateString("de-DE", { day: "2-digit", month: "2-digit", year: "numeric" });
    expect(knopf(el, "detail-ablauf").textContent).toContain(`Inserat online bis ${datum}`);
    // ohne Datum (nicht veröffentlicht / ältere Antwort) -> nichts
    await act(async () => { knopf(el, "markt-l2").click(); });
    expect(knopf(el, "detail-ablauf")).toBeNull();
  });

  it("RP-519: Ablauf nur unter laufenden Anfragen auf veröffentlichten Inseraten", async () => {
    const bis = new Date(Date.now() + 5 * 86400000).toISOString();
    const basis = { listing_id: "l1", dealer_id: "d1", listing_title: "Golf", offer: 20000,
                    created_at: "2026-09-20T08:00:00+00:00", updated_at: "2026-09-20T08:00:00+00:00",
                    history: [], laeuft_ab_am: bis };
    interessen = [
      { ...basis, id: "i-lauf", status: "offen", inserat_status: "veroeffentlicht" },
      { ...basis, id: "i-ruht", status: "offen", inserat_status: "reserviert",
        anderweitig_reserviert: true, laeuft_ab_am: null },
      { ...basis, id: "i-zu", status: "abgelehnt", beendet_grund: "inserat_abgelaufen",
        inserat_status: "veroeffentlicht" },
    ];
    const el = await rendern();
    await act(async () => { knopf(el, "meine-anfragen-btn").click(); });
    for (let i = 0; i < 4; i += 1) await act(async () => { await Promise.resolve(); });
    expect(knopf(el, "meine-anfrage-ablauf-i-lauf").textContent).toMatch(/läuft am \d{2}\.\d{2}\.\d{4} ab/);
    expect(knopf(el, "meine-anfrage-ablauf-i-ruht")).toBeNull();
    expect(knopf(el, "meine-anfrage-ablauf-i-zu")).toBeNull();
    expect(knopf(el, "meine-anfrage-i-zu").textContent).toContain("die Laufzeit des Inserats ist abgelaufen");
  });
});
