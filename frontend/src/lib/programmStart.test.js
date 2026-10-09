/*
 * Installierte App (Wunsch Ahmad 03.10.2026): Startet das Windows-Programm die App erneut mit einem Ziel,
 * übernimmt das schon offene Fenster es (launchQueue) — App-Symbol holt nur nach vorne.
 * Prüfung 09.10.2026 (Befund Ahmad "Vertrag hängt"): Rückkanal (navigieren, wenn keine Vergleichsseite zuhört),
 * Meldung mit Zustand, gemerktes Ziel, Antwort an die Erweiterung.
 */
import { describe, expect, it, vi } from "vitest";
import {
  INSERAT_EREIGNIS, zielAusAppStart, startZieleVerfolgen, startKennung, startMelden, protokollSuche,
  erweiterungZieleVerfolgen, zielUebernehmen, zielMerken, zielGemerkt, zielVergessen, ZIEL_MERKER,
} from "./programmStart";

const O = "https://app.auto-schnellkauf.de";
const KA = "https://www.kleinanzeigen.de/s-anzeige/3530379782";
const ZIEL = `${O}/app/vergleich?url=${encodeURIComponent(KA)}`;

/** Fenster-Attrappe; `hoert` = eine Vergleichsseite nimmt das Ereignis an (setzt detail.uebernommen). */
function fenster(pfad = "/app/termine", { hoert = true } = {}) {
  let verbraucher = null;
  const ereignisse = [];
  return {
    location: { origin: O, pathname: pfad, href: O + pfad },
    launchQueue: { setConsumer: (f) => { verbraucher = f; } },
    dispatchEvent: (e) => { ereignisse.push(e); if (hoert && e.detail && typeof e.detail === "object") e.detail.uebernommen = true; },
    postMessage: vi.fn(),
    starten: (url) => verbraucher({ targetURL: url }),
    ereignisse,
  };
}

function speicher() {
  const m = new Map();
  return { getItem: (k) => (m.has(k) ? m.get(k) : null), setItem: (k, v) => m.set(k, v), removeItem: (k) => m.delete(k) };
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
  function appFenster(pfad, opts) {
    const f = fenster(pfad, opts);
    const hoerer = [];
    f.addEventListener = (typ, h) => hoerer.push(h);
    f.removeEventListener = vi.fn();
    f.senden = (data, quelle = f) => hoerer.forEach((h) => h({ source: quelle, data }));
    return f;
  }
  it("andere Seite offen: dorthin navigieren — und der Erweiterung „uebernommen“ melden", () => {
    const f = appFenster("/app/termine");
    const nav = vi.fn();
    erweiterungZieleVerfolgen(nav, { fenster: f });
    const ziel = `/app/vergleich?url=${encodeURIComponent(KA)}&vertrag=1`;
    f.senden({ __autoschnell: true, type: "OEFFNEN", ziel });
    expect(nav).toHaveBeenCalledWith(ziel);
    expect(f.postMessage).toHaveBeenCalledWith(
      { __autoschnell: true, type: "OEFFNEN_ERGEBNIS", ziel, stand: "uebernommen" }, O);
  });
  it("Vergleich offen: Ereignis mit Kaufvertrag", () => {
    const f = appFenster("/app/vergleich");
    const nav = vi.fn();
    erweiterungZieleVerfolgen(nav, { fenster: f });
    f.senden({ __autoschnell: true, type: "OEFFNEN", ziel: `/app/vergleich?url=${encodeURIComponent(KA)}&vertrag=1` });
    expect(nav).not.toHaveBeenCalled();
    expect(f.ereignisse[0].detail).toEqual({ link: KA, vertrag: true, lesungFehlt: false, uebernommen: true });
  });
  it("fremde Quelle, fremde Ziele und ungespeicherte Arbeit (dann „nachgefragt“ an die Erweiterung)", () => {
    const f = appFenster("/app/termine");
    const nav = vi.fn();
    const nachfragen = vi.fn();
    erweiterungZieleVerfolgen(nav, { fenster: f, beschaeftigt: () => true, nachfragen });
    f.senden({ __autoschnell: true, type: "OEFFNEN", ziel: "/app/vertraege" }, { anderes: "fenster" });
    f.senden({ __autoschnell: true, type: "OEFFNEN", ziel: "https://boese.example/app/x" });
    f.senden({ __autoschnell: true, type: "OEFFNEN", ziel: "//boese.example/app" });
    expect(nachfragen).not.toHaveBeenCalled();
    expect(f.postMessage).not.toHaveBeenCalled();
    f.senden({ __autoschnell: true, type: "OEFFNEN", ziel: "/app/vertraege" });
    expect(nachfragen).toHaveBeenCalledTimes(1);
    expect(nachfragen.mock.calls[0][1]).toBe("/app/vertraege");
    expect(nav).not.toHaveBeenCalled();
    expect(f.postMessage).toHaveBeenLastCalledWith(
      { __autoschnell: true, type: "OEFFNEN_ERGEBNIS", ziel: "/app/vertraege", stand: "nachgefragt" }, O);
    nachfragen.mock.calls[0][0]();
    expect(nav).toHaveBeenCalledWith("/app/vertraege");
  });
  it("nicht angemeldet / kein Abo: die Erweiterung erfährt den Grund", () => {
    const f = appFenster("/login");
    erweiterungZieleVerfolgen(vi.fn(), { fenster: f, zustand: () => "anmeldung" });
    f.senden({ __autoschnell: true, type: "OEFFNEN", ziel: "/app/vertraege" });
    expect(f.postMessage.mock.calls[0][0].stand).toBe("anmeldung");
  });
});

describe("zielUebernehmen (Prüfung 09.10.2026): Rückkanal und Pfadabgleich", () => {
  it("Vergleichsseite hört zu: nur das Ereignis", () => {
    const f = fenster("/app/vergleich");
    const nav = vi.fn();
    expect(zielUebernehmen(`/app/vergleich?url=${encodeURIComponent(KA)}`, { fenster: f, navigieren: nav })).toBe("uebernommen");
    expect(f.ereignisse).toHaveLength(1);
    expect(nav).not.toHaveBeenCalled();
  });
  it("Route steht auf /app/vergleich, aber niemand hört zu (Lade…, Abo-Prüfung): navigieren statt verlieren", () => {
    const f = fenster("/app/vergleich", { hoert: false });
    const nav = vi.fn();
    zielUebernehmen(`/app/vergleich?url=${encodeURIComponent(KA)}`, { fenster: f, navigieren: nav });
    expect(nav).toHaveBeenCalledWith(`/app/vergleich?url=${encodeURIComponent(KA)}`);
  });
  it("Schrägstrich am Ende / Großschreibung zählen wie die Seite selbst", () => {
    const f = fenster("/App/Vergleich/");
    const nav = vi.fn();
    zielUebernehmen(`/app/vergleich?url=${encodeURIComponent(KA)}&lesung=fehlt`, { fenster: f, navigieren: nav });
    expect(nav).not.toHaveBeenCalled();
    expect(f.ereignisse[0].detail).toMatchObject({ link: KA, lesungFehlt: true, uebernommen: true });
  });
});

describe("gemerktes Ziel (Prüfung 09.10.2026): ein verpasster Hinweis ist nie endgültig", () => {
  it("merken, lesen, vergessen — nur Pfade in der App, höchstens 15 Minuten", () => {
    const s = speicher();
    zielMerken("/app/vergleich?url=x", s);
    expect(zielGemerkt(s)).toBe("/app/vergleich?url=x");
    expect(zielGemerkt(s, Date.now() + 16 * 60 * 1000)).toBeNull();
    expect(s.getItem(ZIEL_MERKER)).toBeNull();
    zielMerken("//boese.example/app", s);
    expect(zielGemerkt(s)).toBeNull();
    zielMerken("/app/termine", s);
    zielVergessen(s);
    expect(zielGemerkt(s)).toBeNull();
    s.setItem(ZIEL_MERKER, "kaputt");
    expect(zielGemerkt(s)).toBeNull();
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
    expect(f.ereignisse[0].detail).toEqual({ link: KA, vertrag: false, lesungFehlt: false, uebernommen: true });
  });

  it("Browser-Helfer (04.10.2026): &vertrag=1 geht mit (Kaufvertrag gleich öffnen)", () => {
    const f = fenster("/app/vergleich");
    startZieleVerfolgen(vi.fn(), { fenster: f, startAdresse: `${O}/start` });
    f.starten(`${ZIEL}&vertrag=1`);
    expect(f.ereignisse[0].detail).toMatchObject({ link: KA, vertrag: true });
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

  it("startMelden schickt genau eine Anfrage (mit Zustand als Körper), Fehler sind egal", async () => {
    const client = { post: vi.fn(() => Promise.resolve({ data: { ok: true } })) };
    expect(await startMelden(client, S)).toBe(true);
    expect(client.post).toHaveBeenCalledWith(`/werkzeuge/app-start/${S}`);
    expect(await startMelden(client, S, "nachgefragt")).toBe(true);
    expect(client.post).toHaveBeenLastCalledWith(`/werkzeuge/app-start/${S}`, { zustand: "nachgefragt" });
    expect(await startMelden(client, S, "quatsch")).toBe(true);
    expect(client.post).toHaveBeenLastCalledWith(`/werkzeuge/app-start/${S}`);
    const kaputt = { post: vi.fn(() => Promise.reject(new Error("401"))) };
    expect(await startMelden(kaputt, S)).toBe(false);
    expect(await startMelden(client, "nein")).toBe(false);
    expect(client.post).toHaveBeenCalledTimes(3);
  });

  it("offenes Fenster meldet den Start sofort — mit „nachgefragt“, wenn wegen Ungespeichertem erst gefragt wird", () => {
    const f = fenster("/app/vertraege");
    const melden = vi.fn();
    startZieleVerfolgen(vi.fn(), { fenster: f, startAdresse: `${O}/start`, beschaeftigt: () => true,
                                   nachfragen: () => {}, melden });
    f.starten(MIT_START);
    expect(melden).toHaveBeenCalledWith(S, "nachgefragt");
    expect(zielGemerkt()).toBe(`/app/vergleich?url=${encodeURIComponent(KA)}&start=${S}`);
    zielVergessen();
  });

  it("ohne Ungespeichertes: „offen“", () => {
    const f = fenster("/app/termine");
    const melden = vi.fn();
    startZieleVerfolgen(vi.fn(), { fenster: f, startAdresse: `${O}/start`, melden });
    f.starten(MIT_START);
    expect(melden).toHaveBeenCalledWith(S, "offen");
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
