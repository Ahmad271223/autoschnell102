/*
 * Installierte App (Wunsch Ahmad 03.10.2026): Startet das Windows-Programm die App erneut mit einem Ziel,
 * übernimmt das schon offene Fenster es (launchQueue) — App-Symbol holt nur nach vorne.
 */
import { describe, expect, it, vi } from "vitest";
import { INSERAT_EREIGNIS, zielAusAppStart, startZieleVerfolgen, startKennung, startMelden } from "./programmStart";

const O = "https://app.auto-schnellkauf.de";
const KA = "https://www.kleinanzeigen.de/s-anzeige/3530379782";
const ZIEL = `${O}/app/vergleich?url=${encodeURIComponent(KA)}`;

function fenster(pfad = "/app/termine") {
  let verbraucher = null;
  const ereignisse = [];
  return {
    location: { origin: O, pathname: pfad, href: O + pfad },
    launchQueue: { setConsumer: (f) => { verbraucher = f; } },
    dispatchEvent: (e) => ereignisse.push(e),
    starten: (url) => verbraucher({ targetURL: url }),
    ereignisse,
  };
}

describe("zielAusAppStart", () => {
  it("Vergleich-Ziel aus dem Programm", () => {
    expect(zielAusAppStart(ZIEL, { origin: O, startAdresse: `${O}/start` }))
      .toBe(`/app/vergleich?url=${encodeURIComponent(KA)}`);
  });
  it("App-Symbol (Startseite) holt nur nach vorne", () => {
    expect(zielAusAppStart(`${O}/start`, { origin: O, startAdresse: `${O}/app` })).toBeNull();
    expect(zielAusAppStart(`${O}/`, { origin: O, startAdresse: `${O}/app` })).toBeNull();
  });
  it("frisch geöffnetes Fenster steht schon dort; fremde Seiten nie", () => {
    expect(zielAusAppStart(ZIEL, { origin: O, startAdresse: ZIEL })).toBeNull();
    expect(zielAusAppStart("https://boese.example/app/vergleich", { origin: O, startAdresse: "" })).toBeNull();
    expect(zielAusAppStart("kaputt", { origin: O, startAdresse: "" })).toBeNull();
    expect(zielAusAppStart(undefined, { origin: O })).toBeNull();
  });
});

describe("startZieleVerfolgen", () => {
  it("andere Seite offen: dorthin navigieren", () => {
    const f = fenster("/app/termine");
    const nav = vi.fn();
    expect(startZieleVerfolgen(nav, { fenster: f, startAdresse: `${O}/start` })).toBe(true);
    f.starten(ZIEL);
    expect(nav).toHaveBeenCalledWith(`/app/vergleich?url=${encodeURIComponent(KA)}`);
    expect(f.ereignisse).toHaveLength(0);
  });

  it("Vergleich schon offen: Ereignis statt Navigation (Seite übernimmt das Auto)", () => {
    const f = fenster("/app/vergleich");
    const nav = vi.fn();
    startZieleVerfolgen(nav, { fenster: f, startAdresse: `${O}/start` });
    f.starten(ZIEL);
    expect(nav).not.toHaveBeenCalled();
    expect(f.ereignisse[0].type).toBe(INSERAT_EREIGNIS);
    expect(f.ereignisse[0].detail).toBe(KA);
  });

  it("ungespeicherte Arbeit: erst fragen, erst auf Knopfdruck wechseln", () => {
    const f = fenster("/app/vertraege");
    const nav = vi.fn();
    let knopf = null;
    startZieleVerfolgen(nav, { fenster: f, startAdresse: `${O}/start`, beschaeftigt: () => true,
                               nachfragen: (ausfuehren) => { knopf = ausfuehren; } });
    f.starten(ZIEL);
    expect(nav).not.toHaveBeenCalled();
    knopf();
    expect(nav).toHaveBeenCalledTimes(1);
  });

  it("Browser ohne launchQueue: nichts", () => {
    expect(startZieleVerfolgen(vi.fn(), { fenster: { location: { origin: O } } })).toBe(false);
  });
});

describe("Pruefbericht 03.10.2026 (Nr. 12): Rueckmeldung an das Programm", () => {
  const S = "0123456789abcdef0123456789abcdef";
  const MIT_START = `${ZIEL}&start=${S}`;

  it("Kennung nur in der Form des Programms (32 Hex-Zeichen)", () => {
    expect(startKennung(MIT_START)).toBe(S);
    expect(startKennung(`/app/vergleich?url=x&start=${S}`, O)).toBe(S);
    expect(startKennung(ZIEL)).toBeNull();
    expect(startKennung(`${ZIEL}&start=../../admin`)).toBeNull();
    expect(startKennung(`${ZIEL}&start=${S.toUpperCase()}`)).toBeNull();
    expect(startKennung("kaputt::")).toBeNull();
  });

  it("startMelden schickt genau eine Anfrage, Fehler sind egal", async () => {
    const client = { post: vi.fn(() => Promise.resolve({ data: { ok: true } })) };
    expect(await startMelden(client, S)).toBe(true);
    expect(client.post).toHaveBeenCalledWith(`/werkzeuge/app-start/${S}`);
    const kaputt = { post: vi.fn(() => Promise.reject(new Error("401"))) };
    expect(await startMelden(kaputt, S)).toBe(false);
    expect(await startMelden(client, "nein")).toBe(false);
    expect(client.post).toHaveBeenCalledTimes(1);
  });

  it("offenes Fenster meldet den Start sofort — auch wenn wegen Ungespeichertem erst gefragt wird", () => {
    const f = fenster("/app/vertraege");
    const melden = vi.fn();
    startZieleVerfolgen(vi.fn(), { fenster: f, startAdresse: `${O}/start`, beschaeftigt: () => true,
                                   nachfragen: () => {}, melden });
    f.starten(MIT_START);
    expect(melden).toHaveBeenCalledWith(S);
  });

  it("ohne Kennung (App-Symbol, alte Programmversion) wird nichts gemeldet", () => {
    const f = fenster("/app/termine");
    const melden = vi.fn();
    startZieleVerfolgen(vi.fn(), { fenster: f, startAdresse: `${O}/start`, melden });
    f.starten(ZIEL);
    f.starten(`${O}/start`);
    expect(melden).not.toHaveBeenCalled();
  });
});
