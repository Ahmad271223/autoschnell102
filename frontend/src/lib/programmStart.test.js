/*
 * Installierte App (Wunsch Ahmad 03.10.2026): Startet das Windows-Programm die App erneut mit einem Ziel,
 * übernimmt das schon offene Fenster es (launchQueue) — App-Symbol holt nur nach vorne.
 */
import { describe, expect, it, vi } from "vitest";
import { INSERAT_EREIGNIS, zielAusAppStart, startZieleVerfolgen } from "./programmStart";

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
