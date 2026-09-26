/*
 * Rollenprüfung 22.09.2026 — Marktplatz-Seite (Team markt_kaeufer), gerendert
 * mit nachgebildetem Server (buyerApi):
 *
 * RP-097/RP-347  Fehler beim Zugang -> Meldung + "Erneut versuchen" statt "Lädt…"
 * RP-505/RP-504  Karte zeigt den Händler-Titel; keine Portal-Unfall-Zusicherung
 * RP-520         Zähler am Knopf "Meine Anfragen"
 * RP-477         vereinbarter Preis und Händler-Kontakt bei einer Annahme (RP-503)
 * RP-478         "für einen anderen Käufer reserviert": Hinweis, kein Annehmen
 * RP-502         laufende Anfrage: "Angebot ändern" und "Zurückziehen"
 * RP-495         Annehmen schickt den gesehenen Betrag mit
 * RP-501         Gegenangebot "12.500" geht als 12500 raus (nach Rückfrage)
 * RP-563         Impressum/Datenschutz/AGB verlinkt
 */
import { act, createElement as h } from "react";
import { createRoot } from "react-dom/client";
import { MemoryRouter } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("sonner", () => ({ toast: { success: vi.fn(), error: vi.fn(), info: vi.fn(), warning: vi.fn() } }));

globalThis.IS_REACT_ACT_ENVIRONMENT = true;

const { BuyerAuthProvider, buyerApi } = await import("@/context/BuyerContext");
const { TOKEN_KAEUFER, tokenSetzen } = await import("@/lib/sitzung");
const { katalogVergessen } = await import("@/lib/katalog");
const { default: Marktplatz } = await import("./Marktplatz");

const LISTING = {
  id: "l1", dealer_id: "d1", title: "Golf 8 GTI Händlertitel", status: "veroeffentlicht",
  data: { make_label: "VW", model_label: "Golf", mileage: "", accident_free: null },
  photos: [], dealer_photos: [], price: 18500, price_level: "b2b",
  dealer: { id: "d1", company_name: "Autohaus Test", city: "Hannover", phone: "" },
};
const INTERESSEN = [
  { id: "i-akz", listing_id: "l1", dealer_id: "d1", listing_title: "alt", status: "akzeptiert",
    offer: 17000, agreed_price: 18000, created_at: "2026-09-20T10:00:00+00:00",
    updated_at: "2026-09-21T10:00:00+00:00",
    history: [{ von: "kaeufer", aktion: "interesse", angebot: 17000 }, { von: "haendler", aktion: "akzeptieren", angebot: 18000 }],
    haendler: { company_name: "Autohaus Test", slug: "a", kontakt: { phone: "0511 123", email: "a@test.de", city: "Hannover", contact_person: "Herr A" } },
    inserat: { title: "Golf 8 GTI Händlertitel", make_label: "VW", model_label: "Golf", foto: "" } },
  { id: "i-res", listing_id: "l2", dealer_id: "d1", listing_title: "Polo", status: "gegenangebot",
    offer: 9000, counter_offer: 9500, anderweitig_reserviert: true, inserat_status: "reserviert",
    created_at: "2026-09-20T09:00:00+00:00", updated_at: "2026-09-20T09:00:00+00:00", history: [] },
  { id: "i-gg", listing_id: "l3", dealer_id: "d1", listing_title: "Passat", status: "gegenangebot",
    offer: 20000, counter_offer: 21000, created_at: "2026-09-20T08:00:00+00:00",
    updated_at: "2026-09-20T08:00:00+00:00", history: [] },
  { id: "i-off", listing_id: "l4", dealer_id: "d1", listing_title: "Tiguan", status: "offen",
    offer: 25000, created_at: "2026-09-20T07:00:00+00:00", updated_at: "2026-09-20T07:00:00+00:00", history: [] },
];

let root;
let host;
let zugangFehler;
let post;

function antwortFuer(url) {
  if (url === "/buyer/me") return { data: { id: "k1", role: "b2b_buyer", company_name: "Käufer GmbH" } };
  if (url === "/marktplatz/zugang") {
    if (zugangFehler) {
      const e = new Error("Serverfehler");
      e.response = { status: 500, data: { detail: "Datenbank nicht erreichbar" } };
      throw e;
    }
    return { data: { active: true, kostenlos: true, gesperrt: false } };
  }
  if (url === "/manual/makes") return { data: [] };
  if (url === "/marktplatz/favoriten") return { data: { listing_ids: [] } };
  if (url.startsWith("/marktplatz/listings")) return { data: [LISTING] };
  if (url === "/buyer/interessen/zaehler") return { data: { am_zug: 1, neu: 1 } };
  if (url === "/buyer/interessen") return { data: INTERESSEN };
  throw new Error(`unerwartet: ${url}`);
}

beforeEach(() => {
  window.localStorage.clear();
  window.sessionStorage.clear();
  katalogVergessen();
  zugangFehler = false;
  tokenSetzen(TOKEN_KAEUFER, "tok");
  vi.spyOn(buyerApi, "get").mockImplementation(async (url) => antwortFuer(url));
  post = vi.spyOn(buyerApi, "post").mockResolvedValue({ data: { ok: true } });
  window.scrollTo = () => {};
});

afterEach(async () => {
  if (root) await act(async () => { root.unmount(); });
  host?.remove();
  root = null;
  vi.restoreAllMocks();
});

async function rendern() {
  host = document.createElement("div");
  document.body.appendChild(host);
  root = createRoot(host);
  await act(async () => {
    root.render(h(MemoryRouter, { initialEntries: ["/markt"] },
      h(BuyerAuthProvider, null, h(Marktplatz))));
  });
  // Effekte und Antworten abwarten
  for (let i = 0; i < 5; i += 1) await act(async () => { await Promise.resolve(); });
  return host;
}

const knopf = (el, testid) => el.querySelector(`[data-testid="${testid}"]`);

describe("Marktplatz (Rollenprüfung 22.09.2026)", () => {
  it("RP-097: Zugangsfehler zeigt Meldung und Erneut-Knopf, der erneut lädt", async () => {
    zugangFehler = true;
    const el = await rendern();
    expect(knopf(el, "markt-zugang-fehler")).toBeTruthy();
    expect(el.textContent).toMatch(/Datenbank nicht erreichbar/);
    zugangFehler = false;
    await act(async () => { knopf(el, "markt-zugang-erneut").click(); });
    for (let i = 0; i < 5; i += 1) await act(async () => { await Promise.resolve(); });
    expect(knopf(el, "markt-page")).toBeTruthy();
  });

  it("Karte: Händler-Titel, kein 0 km, Zähler und Rechtslinks", async () => {
    const el = await rendern();
    const karte = knopf(el, "markt-l1");
    expect(karte.textContent).toMatch(/Golf 8 GTI Händlertitel/);
    expect(karte.textContent).not.toMatch(/0 km/);
    expect(knopf(el, "meine-anfragen-zaehler").textContent).toBe("2");
    expect(knopf(el, "rechts-links")).toBeTruthy();
  });

  it("Meine Anfragen: vereinbarter Preis, Kontakt, Reservierungs-Hinweis, Ändern/Zurückziehen", async () => {
    const el = await rendern();
    await act(async () => { knopf(el, "meine-anfragen-btn").click(); });
    for (let i = 0; i < 5; i += 1) await act(async () => { await Promise.resolve(); });
    const akz = knopf(el, "meine-anfrage-i-akz");
    expect(akz.textContent).toMatch(/Vereinbarter Preis: 18\.000 €/);
    expect(akz.textContent).toMatch(/Dein ursprüngliches Angebot: 17\.000 €/);
    expect(knopf(el, "haendler-kontakt-i-akz").textContent).toMatch(/0511 123/);
    expect(akz.textContent).toMatch(/Angenommen/);
    // RP-478: fremd reserviert -> Hinweis, kein Annehmen/Gegenangebot, Ablehnen bleibt
    expect(knopf(el, "anderweitig-reserviert-i-res")).toBeTruthy();
    expect(knopf(el, "gegenangebot-annehmen-i-res")).toBeNull();
    expect(knopf(el, "kaeufer-gegenangebot-i-res")).toBeNull();
    expect(knopf(el, "gegenangebot-ablehnen-i-res")).toBeTruthy();
    // RP-502: offene Anfrage ändern/zurückziehen
    expect(knopf(el, "anfrage-aendern-i-off")).toBeTruthy();
    expect(knopf(el, "anfrage-zurueckziehen-i-off")).toBeTruthy();
    // RP-495: Annehmen schickt den gesehenen Betrag mit
    await act(async () => { knopf(el, "gegenangebot-annehmen-i-gg").click(); });
    expect(post).toHaveBeenCalledWith("/interessen/i-gg/kaeufer-antwort",
      { action: "annehmen", message: "", erwarteter_betrag: 21000 });
  });

  it("RP-501: Gegenangebot '12.500' geht nach Rückfrage als 12500 raus; Zurückziehen fragt nach", async () => {
    const frage = vi.spyOn(window, "confirm").mockReturnValue(true);
    const el = await rendern();
    await act(async () => { knopf(el, "meine-anfragen-btn").click(); });
    for (let i = 0; i < 5; i += 1) await act(async () => { await Promise.resolve(); });
    await act(async () => { knopf(el, "kaeufer-gegenangebot-i-gg").click(); });
    const feld = knopf(el, "kaeufer-gegenangebot-betrag-i-gg");
    expect(feld.getAttribute("type")).toBe("text");
    await act(async () => {
      const setter = Object.getOwnPropertyDescriptor(window.HTMLInputElement.prototype, "value").set;
      setter.call(feld, "12.500");
      feld.dispatchEvent(new Event("input", { bubbles: true }));
    });
    await act(async () => { knopf(el, "kaeufer-gegenangebot-senden-i-gg").click(); });
    expect(frage.mock.calls[0][0]).toMatch(/Dein Gegenangebot: 12\.500,00/);
    expect(post).toHaveBeenCalledWith("/interessen/i-gg/kaeufer-antwort",
      { action: "gegenangebot", message: "", counter_offer: 12500 });
    await act(async () => { knopf(el, "anfrage-zurueckziehen-i-off").click(); });
    expect(post).toHaveBeenCalledWith("/interessen/i-off/kaeufer-antwort",
      { action: "zurueckziehen", message: "" });
  });
});
