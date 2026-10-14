/*
 * Rollenprüfung 22.09.2026, Welle 4 (Review) — Marktplatz (Team markt_kaeufer),
 * gerendert mit nachgebildetem Server (buyerApi):
 *
 * RP-520  "gesehen" ist der Stand der ANGEZEIGTEN Liste (jüngstes updated_at,
 *         Serverzeit) — nicht "jetzt" beim Öffnen/Schließen. Eine Annahme, die
 *         bei offenem Fenster kam, bleibt neu (Zähler + "Neu"-Marke).
 * RP-531  Der gesicherte Entwurf öffnet das Fahrzeug bzw. "Meine Anfragen"
 *         höchstens EINMAL; jeder Sendefehler außer 401 löscht ihn.
 */
import { act, createElement as h } from "react";
import { createRoot } from "react-dom/client";
import { MemoryRouter } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("sonner", () => ({ toast: { success: vi.fn(), error: vi.fn(), info: vi.fn(), warning: vi.fn() } }));

globalThis.IS_REACT_ACT_ENVIRONMENT = true;

const { toast } = await import("sonner");
const { BuyerAuthProvider, buyerApi } = await import("@/context/BuyerContext");
const { TOKEN_KAEUFER, tokenSetzen } = await import("@/lib/sitzung");
const { katalogVergessen } = await import("@/lib/katalog");
const {
  anfragenGesehen, anfragenGesehenMerken, anfragenStand, entwurfLesen, entwurfMerken,
} = await import("./marktHilfen");
const { default: Marktplatz } = await import("./Marktplatz");

const karte = (id) => ({
  id, dealer_id: "d1", title: `VW Golf ${id}`, status: "veroeffentlicht",
  data: { make_label: "VW", model_label: "Golf", mileage: 1000 },
  photos: [], dealer_photos: [], price: 9900, price_level: "oeffentlich",
  dealer: { id: "d1", company_name: "Autohaus Test", city: "Hannover", phone: "" },
});
const anfrage = (id, updated_at, von, status = "offen", extra = {}) => ({
  id, listing_id: `l-${id}`, dealer_id: "d1", listing_title: `Auto ${id}`, status, offer: 10000,
  counter_offer: status === "gegenangebot" ? 11000 : null,
  created_at: "2026-09-19T08:00:00+00:00", updated_at,
  history: [{ von: "kaeufer", aktion: "interesse", angebot: 10000 }, ...(von ? [{ von, aktion: "x" }] : [])],
  ...extra,
});

let root;
let host;
let interessen;
let interessenFehler;
let get;
let post;

function fehler(status) {
  const e = new Error(`HTTP ${status}`);
  e.response = { status, data: { detail: `Fehler ${status}` } };
  return e;
}

function antwortFuer(url) {
  if (url === "/buyer/me") return { data: { id: "k1", role: "b2b_buyer", company_name: "Käufer GmbH" } };
  if (url === "/marktplatz/zugang") return { data: { active: true, kostenlos: true, gesperrt: false } };
  if (url === "/manual/makes") return { data: [] };
  if (url === "/marktplatz/favoriten") return { data: { listing_ids: [] } };
  if (url.startsWith("/marktplatz/listings")) return { data: [karte("l1")] };
  if (url === "/buyer/interessen/zaehler") return { data: { am_zug: 0, neu: 0 } };
  if (url === "/buyer/interessen") {
    if (interessenFehler) throw fehler(500);
    return { data: interessen };
  }
  throw new Error(`unerwartet: ${url}`);
}

beforeEach(() => {
  window.localStorage.clear();
  window.sessionStorage.clear();
  katalogVergessen();
  tokenSetzen(TOKEN_KAEUFER, "tok");
  interessen = [];
  interessenFehler = false;
  get = vi.spyOn(buyerApi, "get").mockImplementation(async (url) => antwortFuer(url));
  post = vi.spyOn(buyerApi, "post").mockResolvedValue({ data: { ok: true } });
  vi.spyOn(window, "confirm").mockReturnValue(true);
  window.scrollTo = () => {};
  toast.info.mockClear();
});

afterEach(async () => {
  if (root) await act(async () => { root.unmount(); });
  host?.remove();
  root = null;
  vi.restoreAllMocks();
});

const warten = async (n = 6) => {
  for (let i = 0; i < n; i += 1) await act(async () => { await Promise.resolve(); });
};

async function rendern() {
  if (root) { await act(async () => { root.unmount(); }); host?.remove(); }
  host = document.createElement("div");
  document.body.appendChild(host);
  root = createRoot(host);
  await act(async () => {
    root.render(h(MemoryRouter, { initialEntries: ["/markt"] },
      h(BuyerAuthProvider, null, h(Marktplatz))));
  });
  await warten();
  return host;
}

const knopf = (el, testid) => el.querySelector(`[data-testid="${testid}"]`);
const klick = async (el, testid) => { await act(async () => { knopf(el, testid).click(); }); await warten(4); };
const tippen = async (feld, wert) => act(async () => {
  const proto = feld.tagName === "TEXTAREA" ? window.HTMLTextAreaElement : window.HTMLInputElement;
  Object.getOwnPropertyDescriptor(proto.prototype, "value").set.call(feld, wert);
  feld.dispatchEvent(new Event("input", { bubbles: true }));
});
const zaehlerSeit = () => get.mock.calls.filter((c) => c[0] === "/buyer/interessen/zaehler")
  .map((c) => c[1]?.params?.seit);

describe("anfragenStand (RP-520, Review)", () => {
  it("jüngstes updated_at unverändert, Mikrosekunden entscheiden bei gleicher Millisekunde", () => {
    expect(anfragenStand([
      { updated_at: "2026-09-21T10:00:00+00:00" },
      { updated_at: "2026-09-22T08:00:00.123400+00:00" },
      { updated_at: "2026-09-22T08:00:00.123456+00:00" },
      { updated_at: "Unsinn" }, {}, null,
    ])).toBe("2026-09-22T08:00:00.123456+00:00");
    expect(anfragenStand([])).toBe("");
    expect(anfragenStand(null)).toBe("");
  });

  it("anfragenGesehenMerken braucht eine echte Zeit (kein 'jetzt' der Geräteuhr mehr)", () => {
    expect(anfragenGesehenMerken()).toBe(false);
    expect(anfragenGesehenMerken("kaputt")).toBe(false);
    expect(anfragenGesehen()).toBe("");
    anfragenGesehenMerken("2026-09-22T08:00:00+00:00");
    expect(anfragenGesehen()).toBe("2026-09-22T08:00:00+00:00");
  });
});

describe("Meine Anfragen: gesehen = angezeigter Stand (RP-520, Review)", () => {
  it("Öffnen merkt das jüngste updated_at der Liste, Schließen merkt NICHT 'jetzt'", async () => {
    interessen = [
      anfrage("a", "2026-09-21T10:00:00.123456+00:00", "haendler", "akzeptiert"),
      anfrage("b", "2026-09-22T08:00:00.654321+00:00", null),
    ];
    const el = await rendern();
    await klick(el, "meine-anfragen-btn");
    expect(anfragenGesehen()).toBe("2026-09-22T08:00:00.654321+00:00");
    await klick(el, "close-meine-anfragen");
    expect(anfragenGesehen()).toBe("2026-09-22T08:00:00.654321+00:00");
    // der Zähler nach dem Schließen fragt ab genau diesem Serverstand
    expect(zaehlerSeit().at(-1)).toBe("2026-09-22T08:00:00.654321+00:00");
  });

  it("Annahme bei offenem Fenster bleibt neu: beim nächsten Öffnen 'Neu'", async () => {
    anfragenGesehenMerken("2026-09-20T00:00:00+00:00");
    interessen = [anfrage("x", "2026-09-21T10:00:00+00:00", "haendler", "abgelehnt")];
    const el = await rendern();
    await klick(el, "meine-anfragen-btn");
    expect(knopf(el, "meine-anfrage-neu-x")).toBeTruthy();
    // Während das Fenster offen ist, nimmt der Händler eine andere Anfrage an.
    interessen = [...interessen, anfrage("y", "2026-09-22T10:02:00+00:00", "haendler", "akzeptiert")];
    await klick(el, "close-meine-anfragen");
    expect(anfragenGesehen()).toBe("2026-09-21T10:00:00+00:00");
    await klick(el, "meine-anfragen-btn");
    expect(knopf(el, "meine-anfrage-neu-y")).toBeTruthy();
    expect(knopf(el, "meine-anfrage-neu-x")).toBeNull();
    expect(anfragenGesehen()).toBe("2026-09-22T10:02:00+00:00");
  });

  it("Ladefehler merkt nichts als gesehen", async () => {
    anfragenGesehenMerken("2026-09-20T00:00:00+00:00");
    interessenFehler = true;
    const el = await rendern();
    await klick(el, "meine-anfragen-btn");
    expect(knopf(el, "meine-anfragen-fehler")).toBeTruthy();
    await klick(el, "close-meine-anfragen");
    expect(anfragenGesehen()).toBe("2026-09-20T00:00:00+00:00");
  });
});

describe("Entwurf nur einmal und nur nach 401 (RP-531, Review)", () => {
  async function anfrageSenden(el) {
    await klick(el, "markt-l1");
    await klick(el, "interesse-btn-l1");
    await tippen(knopf(el, "interesse-betrag"), "12.500");
    await tippen(knopf(el, "interesse-nachricht"), "Hallo");
    await klick(el, "interesse-senden");
  }

  it("409 beim Senden löscht den Entwurf, das Formular behält die Eingaben", async () => {
    post.mockImplementation(async (url) => {
      if (url.endsWith("/interesse")) throw fehler(409);
      return { data: { ok: true } };
    });
    const el = await rendern();
    await anfrageSenden(el);
    expect(post).toHaveBeenCalledWith("/marktplatz/listings/l1/interesse", { offer: 12500, message: "Hallo" });
    expect(entwurfLesen("anfrage")).toBeNull();
    expect(knopf(el, "interesse-betrag").value).toBe("12.500");
    // neuer Seitenaufbau: nichts öffnet sich von selbst
    const neu = await rendern();
    expect(knopf(neu, "interesse-betrag")).toBeNull();
    expect(toast.info).not.toHaveBeenCalled();
  });

  it("Netzfehler (ohne Antwort) löscht den Entwurf ebenfalls", async () => {
    post.mockImplementation(async () => { throw new Error("Network Error"); });
    await anfrageSenden(await rendern());
    expect(entwurfLesen("anfrage")).toBeNull();
  });

  it("401 (Sitzung beendet) behält den Entwurf; die Wiederherstellung geschieht genau einmal", async () => {
    post.mockImplementation(async () => { throw fehler(401); });
    await anfrageSenden(await rendern());
    expect(entwurfLesen("anfrage")).toMatchObject({ listing_id: "l1", betrag: "12.500", nachricht: "Hallo" });
    // nach der erneuten Anmeldung: Fahrzeug offen, Eingaben wieder da
    post.mockResolvedValue({ data: { ok: true } });
    let el = await rendern();
    expect(knopf(el, "interesse-betrag").value).toBe("12.500");
    expect(knopf(el, "interesse-nachricht").value).toBe("Hallo");
    expect(toast.info).toHaveBeenCalledTimes(1);
    expect(entwurfLesen("anfrage")).toBeNull();
    // Käufer gibt bewusst auf: der nächste Seitenaufbau öffnet nichts mehr
    el = await rendern();
    expect(knopf(el, "interesse-betrag")).toBeNull();
    expect(toast.info).toHaveBeenCalledTimes(1);
  });

  it("Entwurf zu einem Fahrzeug, das nicht mehr in der Liste ist, wird verworfen", async () => {
    entwurfMerken("anfrage", { listing_id: "l-weg", betrag: "5.000", nachricht: "" });
    const el = await rendern();
    expect(knopf(el, "interesse-betrag")).toBeNull();
    expect(entwurfLesen("anfrage")).toBeNull();
  });

  it("Gegenangebot: einmal wiederhergestellt, 409 beim Senden löscht den neuen Entwurf", async () => {
    interessen = [anfrage("gg", "2026-09-21T10:00:00+00:00", "haendler", "gegenangebot")];
    entwurfMerken("gegenangebot", { interest_id: "gg", betrag: "10.500" });
    let el = await rendern();
    expect(knopf(el, "meine-anfragen-modal")).toBeTruthy();
    expect(knopf(el, "kaeufer-gegenangebot-betrag-gg").value).toBe("10.500");
    expect(entwurfLesen("gegenangebot")).toBeNull();
    post.mockImplementation(async () => { throw fehler(409); });
    await klick(el, "kaeufer-gegenangebot-senden-gg");
    expect(post).toHaveBeenCalledWith("/interessen/gg/kaeufer-antwort",
      { action: "gegenangebot", message: "", counter_offer: 10500 });
    expect(entwurfLesen("gegenangebot")).toBeNull();
    el = await rendern();
    expect(knopf(el, "meine-anfragen-modal")).toBeNull();
  });
});
