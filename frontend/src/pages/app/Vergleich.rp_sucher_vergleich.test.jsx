/*
 * Rollenprüfung 22.09.2026 — Team Sucher/Vergleich.
 *  RP-004/RP-103/RP-254  Abbruch-Rennen: ein abgebrochener Lauf räumt den
 *                        neuen Lauf nicht mehr ab; Pausen brechen sofort ab.
 *  RP-023/RP-122/RP-273  Gespeicherter Stand nur einmal gelesen; gelöschter
 *                        Vertrag verschwindet nach dem Wiederherstellen.
 *  RP-207/RP-358         LIVE-Zähler über den Inserats-Schlüssel.
 *  RP-409                Geteilter Text mit Link -> nur der Link.
 *  RP-205/RP-356         AutoScout-Auslandsseiten starten nicht automatisch.
 *  RP-210                Gelöschtes Fahrzeug: kein "Kaufvertrag erstellen".
 */
import { act, createElement } from "react";
import { createRoot } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { inseratsLinkAusText, istInseratsLink } from "@/lib/inseratsLink";
import { checkLink, istAbbruch, postWithRetry503, sleep } from "@/lib/linkCheck";

globalThis.IS_REACT_ACT_ENVIRONMENT = true;

const api = vi.hoisted(() => ({ get: vi.fn(), post: vi.fn(), put: vi.fn() }));
const toastMock = vi.hoisted(() => ({
  error: vi.fn(), success: vi.fn(), info: vi.fn(), warning: vi.fn(), dismiss: vi.fn(),
}));
const erweiterung = vi.hoisted(() => ({ extensionReady: vi.fn(), fetchViaExtension: vi.fn() }));
const auth = vi.hoisted(() => ({ setDealer: vi.fn(), refresh: vi.fn() }));

vi.mock("@/lib/api", () => ({ api, errMsg: (e, f) => e?.message || f }));
vi.mock("sonner", () => ({ toast: toastMock }));
vi.mock("@/lib/clientFetch", () => erweiterung);
vi.mock("@/context/AuthContext", () => ({
  useAuth: () => ({ user: { id: "u1" }, refresh: auth.refresh, setDealer: auth.setDealer }),
}));
vi.mock("react-router-dom", () => ({ useNavigate: () => () => {} }));
vi.mock("@/components/ContractDialog", () => ({ default: () => null }));
vi.mock("@/components/SendDialog", () => ({ default: () => null }));
vi.mock("@/components/BeweisCard", () => ({ default: () => null }));
vi.mock("@/components/ProfileBadge", () => ({ default: () => null }));
vi.mock("@/components/PortalBadge", () => ({ default: () => null }));
vi.mock("@/lib/pdf", () => ({ openContractPdf: vi.fn() }));
vi.mock("@/lib/filterOeffnen", () => ({ filterOeffnen: vi.fn(), FILTER_TOAST_ID: "filter" }));
vi.mock("@/lib/popup", () => ({
  fensterDanebenSetzen: vi.fn(), zweitenBildschirmAnfragen: vi.fn(async () => ({ ok: false })),
}));
vi.mock("@/lib/hinweise", () => ({ hinweiseZeigen: vi.fn(() => []) }));
vi.mock("@/lib/vergleichSpeicher", async (original) => {
  const echt = await original();
  return { ...echt, vergleichLaden: vi.fn(echt.vergleichLaden) };
});

const { default: Vergleich, liveZaehlerPfad } = await import("./Vergleich");
const speicherModul = await import("@/lib/vergleichSpeicher");

const KA_A = "https://www.kleinanzeigen.de/s-anzeige/vw-golf/3012345678-216-1234";
const KA_B = "https://www.kleinanzeigen.de/s-anzeige/vw-polo/3098765432-216-1234";

function aufgeschoben() {
  let erfuellen;
  const promise = new Promise((r) => { erfuellen = r; });
  return { promise, erfuellen };
}

let wurzel;
let behaelter;

async function warten() {
  for (let i = 0; i < 3; i += 1) {
    await act(async () => { await new Promise((r) => setTimeout(r, 0)); });
  }
}

function eingeben(wert) {
  const feld = behaelter.querySelector('[data-testid="vergleich-url-input"]');
  const setter = Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, "value").set;
  setter.call(feld, wert);
  feld.dispatchEvent(new Event("input", { bubbles: true }));
}

const knopf = (id) => behaelter.querySelector(`[data-testid="${id}"]`);

beforeEach(() => {
  vi.clearAllMocks();
  window.sessionStorage.clear();
  behaelter = document.createElement("div");
  document.body.appendChild(behaelter);
  wurzel = createRoot(behaelter);
});

afterEach(async () => {
  await act(async () => { wurzel.unmount(); });
  behaelter.remove();
});

describe("RP-254: Abbruch-Rennen", () => {
  it("der abgebrochene Lauf räumt den neuen Lauf nicht ab und zeigt kein Ergebnis", async () => {
    const erweiterungsSeite = aufgeschoben();
    const vergleichB = aufgeschoben();
    erweiterung.extensionReady.mockResolvedValue(true);
    erweiterung.fetchViaExtension.mockReturnValue(erweiterungsSeite.promise);
    api.post.mockImplementation(async (pfad, body) => {
      if (pfad === "/listings/check" && body.url === KA_A) {
        return { data: { status: "needs_client_fetch", url: KA_A } };
      }
      if (pfad === "/listings/check" && body.url === KA_B) return { data: { status: "completed" } };
      if (pfad === "/mobile/compare" && body.url === KA_B) return vergleichB.promise;
      return { data: {} };
    });
    api.get.mockResolvedValue({ data: { active_now: 1, today: 2 } });

    await act(async () => { wurzel.render(createElement(Vergleich)); });
    // Lauf 1 (Einfügen) hängt in der Erweiterung — die kennt kein Abbruchsignal.
    await act(async () => { eingeben(KA_A); });
    await warten();
    expect(knopf("vergleich-abbrechen-btn")).not.toBeNull();
    await act(async () => { knopf("vergleich-abbrechen-btn").click(); });
    expect(knopf("vergleich-abbrechen-btn")).toBeNull();

    // Lauf 2 startet sofort und wartet auf den Vergleich.
    await act(async () => { eingeben(""); });
    await act(async () => { eingeben(KA_B); });
    await warten();
    expect(knopf("vergleich-abbrechen-btn")).not.toBeNull();

    // Jetzt kommt die Erweiterung des ALTEN Laufs zurück.
    await act(async () => { erweiterungsSeite.erfuellen("<html>alt</html>"); });
    await warten();
    expect(knopf("vergleich-abbrechen-btn")).not.toBeNull();   // Lauf 2 läuft weiter, X bleibt
    expect(api.post.mock.calls.some(([p]) => p === "/listings/ingest")).toBe(false);
    expect(toastMock.error).not.toHaveBeenCalled();

    await act(async () => {
      vergleichB.erfuellen({ data: {
        vehicle: { make_label: "VW", model_label: "Polo" }, ad_id: "3098765432",
        cache_key: "kleinanzeigen:3098765432", source: "kleinanzeigen", hinweise: [],
        search_url: "https://suchen.mobile.de/x", vehicle_id: "v_3098765432",
      } });
    });
    await warten();
    expect(knopf("vehicle-title").textContent).toContain("VW Polo");
    expect(knopf("vergleich-abbrechen-btn")).toBeNull();
    expect(api.get).toHaveBeenCalledWith("/mobile/live-counter/3098765432?quelle=kleinanzeigen");
  });

  it("sleep endet beim Abbruch sofort", async () => {
    const ctrl = new AbortController();
    const start = Date.now();
    const p = sleep(10_000, ctrl.signal);
    ctrl.abort();
    await expect(p).rejects.toSatisfy((e) => istAbbruch(e));
    expect(Date.now() - start).toBeLessThan(1000);
    await expect(sleep(5, undefined)).resolves.toBeUndefined();
  });

  it("503-Wartezeit bricht beim Abbruch sofort ab (vorher bis zu 5 s)", async () => {
    const ctrl = new AbortController();
    const e503 = Object.assign(new Error("busy"), {
      response: { status: 503, headers: { "retry-after": "5" } } });
    const client = { post: vi.fn(async () => { throw e503; }) };
    const start = Date.now();
    const p = postWithRetry503(client, "/mobile/compare", {}, { signal: ctrl.signal, maxWaitMs: 60_000 });
    setTimeout(() => ctrl.abort(), 20);
    await expect(p).rejects.toSatisfy((e) => istAbbruch(e));
    expect(Date.now() - start).toBeLessThan(2000);
  });

  it("Poll-Pause bricht beim Abbruch sofort ab", async () => {
    const ctrl = new AbortController();
    const client = {
      post: vi.fn(async () => ({ data: { status: "queued", job_id: "j9" } })),
      get: vi.fn(async () => ({ data: { status: "queued" } })),
    };
    const start = Date.now();
    const p = checkLink(client, KA_A, { signal: ctrl.signal, pollMs: 10_000, maxWaitMs: 60_000 });
    setTimeout(() => ctrl.abort(), 30);
    await expect(p).rejects.toSatisfy((e) => istAbbruch(e));
    expect(Date.now() - start).toBeLessThan(2000);
  });
});

describe("RP-023: Wiederherstellung", () => {
  it("liest den gespeicherten Stand nur einmal und entfernt einen gelöschten Vertrag", async () => {
    speicherModul.vergleichSichern(window.sessionStorage, "u1", {
      url: KA_A,
      result: { vehicle: { make_label: "VW", model_label: "Golf" }, cache_key: "kleinanzeigen:3012345678",
                ad_id: "3012345678", source: "kleinanzeigen" },
      counter: null,
      contract: { id: "c_weg" },
    });
    api.get.mockImplementation(async (pfad) => {
      if (pfad === "/contracts/c_weg") throw Object.assign(new Error("weg"), { response: { status: 404 } });
      return { data: { active_now: 0, today: 0 } };
    });
    await act(async () => { wurzel.render(createElement(Vergleich)); });
    await warten();
    expect(api.get).toHaveBeenCalledWith("/contracts/c_weg");
    expect(knopf("open-pdf-btn")).toBeNull();
    // weitere Renders (Tippen) lesen den Speicher nicht erneut
    const vorher = speicherModul.vergleichLaden.mock.calls.length;
    await act(async () => { eingeben("abc"); });
    await act(async () => { eingeben("abcd"); });
    expect(speicherModul.vergleichLaden.mock.calls.length).toBe(vorher);
    expect(vorher).toBe(1);
  });

  it("ein noch vorhandener Vertrag bleibt", async () => {
    speicherModul.vergleichSichern(window.sessionStorage, "u1", {
      url: KA_A,
      result: { vehicle: { make_label: "VW", model_label: "Golf" }, cache_key: "kleinanzeigen:3012345678" },
      contract: { id: "c_da" },
    });
    api.get.mockResolvedValue({ data: { id: "c_da" } });
    await act(async () => { wurzel.render(createElement(Vergleich)); });
    await warten();
    expect(knopf("open-pdf-btn")).not.toBeNull();
  });
});

describe("RP-443: aussortiertes Fahrzeug", () => {
  it("statt 404 beim Vertrag: Hinweis und 'Neu vergleichen'", async () => {
    speicherModul.vergleichSichern(window.sessionStorage, "u1", {
      url: KA_A,
      result: { vehicle: { make_label: "VW", model_label: "Golf" }, vehicle_id: "v_weg",
                cache_key: "kleinanzeigen:3012345678" },
    });
    api.get.mockImplementation(async (pfad) => {
      if (pfad === "/vehicles/v_weg") throw Object.assign(new Error("weg"), { response: { status: 404 } });
      return { data: { active_now: 0, today: 0 } };
    });
    api.post.mockResolvedValue({ data: { status: "completed" } });
    await act(async () => { wurzel.render(createElement(Vergleich)); });
    await warten();
    expect(knopf("create-contract-btn")).toBeNull();
    expect(knopf("fahrzeug-weg-hinweis")).not.toBeNull();
    await act(async () => { knopf("neu-vergleichen-btn").click(); });
    await warten();
    expect(api.post).toHaveBeenCalledWith("/listings/check", { url: KA_A }, expect.anything());
  });
});

describe("RP-210 / RP-048: Aktionen", () => {
  it("gelöschtes Fahrzeug: statt 'Kaufvertrag erstellen' eine Erklärung", async () => {
    speicherModul.vergleichSichern(window.sessionStorage, "u1", {
      url: KA_A,
      result: { vehicle: { make_label: "VW", model_label: "Golf" }, fahrzeug_geloescht: true,
                kollege: { user_id: "s1", name: "Anna A", mitbearbeiter: false } },
    });
    api.get.mockResolvedValue({ data: { active_now: 0, today: 0 } });
    await act(async () => { wurzel.render(createElement(Vergleich)); });
    await warten();
    expect(knopf("create-contract-btn")).toBeNull();
    expect(knopf("fahrzeug-geloescht-hinweis")).not.toBeNull();
    expect(knopf("kollege-hinweis").textContent).toContain("bearbeitet bereits");
    expect(knopf("open-mobile-btn")).toBeNull();          // ohne search_url kein mobile.de-Knopf
  });
});

describe("RP-207: LIVE-Zähler", () => {
  it("fragt über den Inserats-Schlüssel, nicht über die Anzeigen-Nummer", () => {
    expect(liveZaehlerPfad({ ad_id: "uniqueRef-X", source: "autoscout24",
                             cache_key: "autoscout24:719b9573-d82f-4d29-85e0-54b5708d9aa6" }))
      .toBe("/mobile/live-counter/719b9573-d82f-4d29-85e0-54b5708d9aa6?quelle=autoscout24");
    // Altstand ohne cache_key: wie bisher
    expect(liveZaehlerPfad({ ad_id: "123", source: "mobile" }))
      .toBe("/mobile/live-counter/123?quelle=mobile");
    expect(liveZaehlerPfad(null)).toBeNull();
  });
});

describe("RP-409 / RP-205: Links erkennen", () => {
  it("zieht den Link aus geteiltem Text und schneidet Satzzeichen ab", () => {
    expect(inseratsLinkAusText(`Schau mal: ${KA_A}`)).toBe(KA_A);
    expect(inseratsLinkAusText(`Schau mal (${KA_A}).`)).toBe(KA_A);
    expect(inseratsLinkAusText(`Guck dir das an!\n${KA_A}!`)).toBe(KA_A);
    expect(inseratsLinkAusText(`  ${KA_A}  `)).toBe(KA_A);
    expect(inseratsLinkAusText("Hallo, kein Link")).toBe("");
    expect(inseratsLinkAusText("https://example.com/s-anzeige-nein")).toBe("");
    expect(inseratsLinkAusText(
      "Siehe https://example.com/x und https://suchen.mobile.de/fahrzeuge/details.html?id=412345678"))
      .toBe("https://suchen.mobile.de/fahrzeuge/details.html?id=412345678");
  });

  it("AutoScout nur mit /angebote/ (Auslandsseiten kann der Abruf-Dienst nicht lesen)", () => {
    expect(istInseratsLink("https://www.autoscout24.de/angebote/vw-golf-abc")).toBe(true);
    expect(istInseratsLink("https://www.autoscout24.com/offers/vw-golf-abc")).toBe(false);
    expect(inseratsLinkAusText("https://www.autoscout24.it/annunci/vw-golf-abc")).toBe("");
  });

  it("Einfügen von geteiltem Text startet mit dem reinen Link", async () => {
    api.post.mockImplementation(async (pfad) => {
      if (pfad === "/listings/check") return { data: { status: "completed" } };
      return { data: { vehicle: { make_label: "VW", model_label: "Golf" },
                       cache_key: "kleinanzeigen:3012345678", hinweise: [] } };
    });
    api.get.mockResolvedValue({ data: { active_now: 0, today: 0 } });
    await act(async () => { wurzel.render(createElement(Vergleich)); });
    await act(async () => { eingeben(`Schau mal: ${KA_A}`); });
    await warten();
    const checks = api.post.mock.calls.filter(([p]) => p === "/listings/check");
    expect(checks[0][1]).toEqual({ url: KA_A });
    expect(behaelter.querySelector('[data-testid="vergleich-url-input"]').value).toBe(KA_A);
  });
});

describe("RP-416 (Welle 2): eigener Vertrag schon da", () => {
  it("ohne Vertrag: normaler Knopf, kein Hinweis", async () => {
    speicherModul.vergleichSichern(window.sessionStorage, "u1", {
      url: KA_A,
      result: { vehicle: { make_label: "VW", model_label: "Golf" }, cache_key: "kleinanzeigen:3012345678" },
    });
    api.get.mockResolvedValue({ data: { active_now: 0, today: 0 } });
    await act(async () => { wurzel.render(createElement(Vergleich)); });
    await warten();
    expect(knopf("vertrag-vorhanden-hinweis")).toBeNull();
    expect(knopf("create-contract-btn").textContent).toContain("Kaufvertrag erstellen");
    expect(knopf("create-contract-btn").textContent).not.toContain("Weiteren");
  });

  it("mit Vertrag dieser Sitzung: Hinweis mit Nummer, Knopf heißt 'Weiteren …'", async () => {
    speicherModul.vergleichSichern(window.sessionStorage, "u1", {
      url: KA_A,
      result: { vehicle: { make_label: "VW", model_label: "Golf" }, cache_key: "kleinanzeigen:3012345678" },
      contract: { id: "c_da", contract_no: "KV-2026-0042" },
    });
    api.get.mockResolvedValue({ data: { id: "c_da" } });
    await act(async () => { wurzel.render(createElement(Vergleich)); });
    await warten();
    expect(knopf("vertrag-vorhanden-hinweis").textContent).toContain("KV-2026-0042");
    expect(knopf("create-contract-btn").textContent).toContain("Weiteren Kaufvertrag erstellen");
    expect(knopf("open-pdf-btn")).not.toBeNull();
  });

  it("gelöschter Vertrag: Hinweis verschwindet wieder", async () => {
    speicherModul.vergleichSichern(window.sessionStorage, "u1", {
      url: KA_A,
      result: { vehicle: { make_label: "VW", model_label: "Golf" }, cache_key: "kleinanzeigen:3012345678" },
      contract: { id: "c_weg", contract_no: "KV-1" },
    });
    api.get.mockImplementation(async (pfad) => {
      if (pfad === "/contracts/c_weg") throw Object.assign(new Error("weg"), { response: { status: 404 } });
      return { data: { active_now: 0, today: 0 } };
    });
    await act(async () => { wurzel.render(createElement(Vergleich)); });
    await warten();
    expect(knopf("vertrag-vorhanden-hinweis")).toBeNull();
    expect(knopf("create-contract-btn").textContent).not.toContain("Weiteren");
  });
});
