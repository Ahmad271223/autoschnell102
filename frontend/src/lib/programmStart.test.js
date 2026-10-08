/*
 * Installierte App (Wunsch Ahmad 03.10.2026): Startet das Windows-Programm die App erneut mit einem Ziel,
 * übernimmt das schon offene Fenster es (launchQueue) — App-Symbol holt nur nach vorne.
 */
import { describe, expect, it, vi } from "vitest";
import {
  INSERAT_EREIGNIS, zielAusAppStart, startZieleVerfolgen, startKennung, startMelden, protokollSuche,
  erweiterungZieleVerfolgen,
} from "./programmStart";

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

describe("Browser-Helfer: App per Link-Typ web+autoschnell: (04.10.2026)", () => {
  const PROT = `web+autoschnell:vertrag?url=${encodeURIComponent(KA)}`;
  it("protokollSuche macht daraus ?url=…&vertrag=1, andere Suchen bleiben", () => {
    const s = protokollSuche(`?protokoll=${encodeURIComponent(PROT)}`);
    expect(s.get("url")).toBe(KA);
    expect(s.get("vertrag")).toBe("1");
    expect(protokollSuche(`?url=${encodeURIComponent(KA)}`).get("url")).toBe(KA);
    expect(protokollSuche(`?protokoll=${encodeURIComponent("javascript:alert(1)")}`).toString()).toBe("");
  });
  it("App schon offen (launchQueue): Ziel aus dem Link-Typ", () => {
    expect(zielAusAppStart(`${O}/app/vergleich?protokoll=${encodeURIComponent(PROT)}`, { origin: O, startAdresse: "" }))
      .toBe(`/app/vergleich?url=${encodeURIComponent(KA)}&vertrag=1`);
  });
});

describe("Browser-Helfer: Ziel aus der Erweiterung im offenen App-Fenster (04.10.2026)", () => {
  function appFenster(pfad) {
    const f = fenster(pfad);
    const hoerer = [];
    f.nachrichten = [];
    f.addEventListener = (typ, h) => hoerer.push(h);
    f.removeEventListener = vi.fn();
    f.postMessage = (data) => { f.nachrichten.push(data); };
    f.senden = (data, quelle = f) => hoerer.forEach((h) => h({ source: quelle, data }));
    return f;
  }
  it("andere Seite offen: dorthin navigieren", () => {
    const f = appFenster("/app/termine");
    const nav = vi.fn();
    erweiterungZieleVerfolgen(nav, { fenster: f });
    f.senden({ __autoschnell: true, type: "OEFFNEN", ziel: `/app/vergleich?url=${encodeURIComponent(KA)}&vertrag=1` });
    expect(nav).toHaveBeenCalledWith(`/app/vergleich?url=${encodeURIComponent(KA)}&vertrag=1`);
  });
  it("Vergleich offen: Ereignis mit Kaufvertrag", () => {
    const f = appFenster("/app/vergleich");
    const nav = vi.fn();
    erweiterungZieleVerfolgen(nav, { fenster: f });
    f.senden({ __autoschnell: true, type: "OEFFNEN", ziel: `/app/vergleich?url=${encodeURIComponent(KA)}&vertrag=1` });
    expect(nav).not.toHaveBeenCalled();
    expect(f.ereignisse[0].detail).toEqual({ link: KA, vertrag: true });
  });
  it("Browser-Helfer bestaetigt bei ungespeicherter Arbeit erst nach echtem Oeffnen", () => {
    const f = appFenster("/app/vertraege");
    const nav = vi.fn();
    let knopf = null;
    erweiterungZieleVerfolgen(nav, {
      fenster: f,
      beschaeftigt: () => true,
      nachfragen: (ausfuehren) => { knopf = ausfuehren; },
    });
    f.senden({
      __autoschnell: true,
      type: "OEFFNEN",
      ziel: `/app/vergleich?url=${encodeURIComponent(KA)}&vertrag=1`,
      reqId: "req-123",
    });
    expect(nav).not.toHaveBeenCalled();
    expect(f.ereignisse).toHaveLength(0);
    knopf();
    expect(nav).toHaveBeenCalledTimes(1);
    expect(f.nachrichten).toContainEqual({
      __autoschnell: true, type: "OEFFNEN_BESTAETIGT", reqId: "req-123",
    });
  });

  it("fremde Quelle, fremde Ziele und ungespeicherte Arbeit", () => {
    const f = appFenster("/app/termine");
    const nav = vi.fn();
    const nachfragen = vi.fn();
    erweiterungZieleVerfolgen(nav, { fenster: f, beschaeftigt: () => true, nachfragen });
    f.senden({ __autoschnell: true, type: "OEFFNEN", ziel: "/app/vertraege" }, { anderes: "fenster" });
    f.senden({ __autoschnell: true, type: "OEFFNEN", ziel: "https://boese.example/app/x" });
    f.senden({ __autoschnell: true, type: "OEFFNEN", ziel: "//boese.example/app" });
    expect(nachfragen).not.toHaveBeenCalled();
    f.senden({ __autoschnell: true, type: "OEFFNEN", ziel: "/app/vertraege" });
    expect(nachfragen).toHaveBeenCalledTimes(1);
    expect(nav).not.toHaveBeenCalled();
    nachfragen.mock.calls[0][0]();
    expect(nav).toHaveBeenCalledWith("/app/vertraege");
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

  it("AutoPointer-Kennung geht bei bereits offener Vergleichsseite im Ereignis mit", () => {
    const f = fenster("/app/vergleich");
    startZieleVerfolgen(vi.fn(), { fenster: f, startAdresse: O + "/start" });
    f.starten(MIT_START);
    expect(f.ereignisse[0].detail).toEqual({ link: KA, vertrag: false, start: S });
  });

  it("Browser-Helfer (04.10.2026): &vertrag=1 geht mit (Kaufvertrag gleich öffnen)", () => {
    const f = fenster("/app/vergleich");
    startZieleVerfolgen(vi.fn(), { fenster: f, startAdresse: `${O}/start` });
    f.starten(`${ZIEL}&vertrag=1`);
    expect(f.ereignisse[0].detail).toEqual({ link: KA, vertrag: true });
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

  it("AutoPointer-Start wird beim blossen Navigieren noch NICHT bestaetigt", () => {
    const f = fenster("/app/vertraege");
    const melden = vi.fn();
    const nav = vi.fn();
    let knopf = null;
    startZieleVerfolgen(nav, { fenster: f, startAdresse: O + "/start", beschaeftigt: () => true,
                               nachfragen: (ausfuehren) => { knopf = ausfuehren; }, melden });
    f.starten(MIT_START);
    expect(melden).not.toHaveBeenCalled();
    expect(nav).not.toHaveBeenCalled();
    knopf();
    expect(nav).toHaveBeenCalledTimes(1);
    expect(melden).not.toHaveBeenCalled();
  });

  it("freie App bestaetigt AutoPointer ebenfalls nicht vor dem geladenen Fahrzeug", () => {
    const f = fenster("/app/termine");
    const melden = vi.fn();
    const nav = vi.fn();
    startZieleVerfolgen(nav, { fenster: f, startAdresse: O + "/start", melden });
    f.starten(MIT_START);
    expect(nav).toHaveBeenCalledTimes(1);
    expect(melden).not.toHaveBeenCalled();
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
