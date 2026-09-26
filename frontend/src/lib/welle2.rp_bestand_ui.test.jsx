/*
 * Rollenprüfung 22.09.2026, Welle 2 — Übergaben an Team Bestand/Oberfläche.
 *  RP-546           gleitende Sitzung: X-Neues-Token nur für das GERADE gültige Token
 *  RP-036/RP-286    Längenfehler (422) auf Deutsch
 *  RP-045/RP-144    /features: Fehlschlag wird nicht zwischengespeichert
 *  RP-464           Menü-Zähler "vom Fahrer abgelehnt"
 */
import { act, createElement } from "react";
import { createRoot } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

globalThis.IS_REACT_ACT_ENVIRONMENT = true;

const { api, errMsg, neuesTokenUebernehmen, validierungsText } = await import("./api");
const { TOKEN_APP, TOKEN_KAEUFER, tokenErneuern, tokenLesen, tokenLoeschen, tokenSetzen } =
  await import("./sitzung");
const { featuresLaden, featuresSetzen } = await import("./features");
const {
  abgelehntAnzahl, abgelehntDauerhaftAus, abgelehntZuruecksetzen, useAbgelehntZaehler,
} = await import("./abgelehntZaehler");

const ALT = "aaa.bbb.ccc";
const NEU = "ddd.eee.fff";
const FREMD = "xxx.yyy.zzz";

function leeren() {
  window.sessionStorage.clear();
  window.localStorage.clear();
}

describe("RP-546: tokenErneuern", () => {
  beforeEach(leeren);

  it("legt das neue Token im Tab und als letzte Anmeldung ab", () => {
    tokenSetzen(TOKEN_APP, ALT);
    expect(tokenErneuern(TOKEN_APP, ALT, NEU)).toBe(true);
    expect(window.sessionStorage.getItem(TOKEN_APP)).toBe(NEU);
    expect(window.localStorage.getItem(TOKEN_APP)).toBe(NEU);
    expect(window.localStorage.getItem("ah_letzte_anmeldung")).toBe(TOKEN_APP);
  });

  it("überschreibt keine neuere Anmeldung eines anderen Tabs", () => {
    tokenSetzen(TOKEN_APP, ALT);
    window.localStorage.setItem(TOKEN_APP, FREMD);        // anderer Tab hat sich angemeldet
    expect(tokenErneuern(TOKEN_APP, ALT, NEU)).toBe(true);
    expect(window.sessionStorage.getItem(TOKEN_APP)).toBe(NEU);
    expect(window.localStorage.getItem(TOKEN_APP)).toBe(FREMD);
  });

  it("Betreiber-Token (nur Sitzung) landet nie in localStorage", () => {
    tokenSetzen(TOKEN_APP, ALT, { nurSitzung: true });
    expect(tokenErneuern(TOKEN_APP, ALT, NEU)).toBe(true);
    expect(window.localStorage.getItem(TOKEN_APP)).toBeNull();
  });

  it("nichts nach Abmeldung, bei fremdem Tab-Token oder Unsinn", () => {
    tokenSetzen(TOKEN_APP, ALT);
    tokenLoeschen(TOKEN_APP);
    expect(tokenErneuern(TOKEN_APP, ALT, NEU)).toBe(false);
    expect(tokenLesen(TOKEN_APP)).toBeNull();

    leeren();
    tokenSetzen(TOKEN_APP, FREMD);
    expect(tokenErneuern(TOKEN_APP, ALT, NEU)).toBe(false);
    expect(window.sessionStorage.getItem(TOKEN_APP)).toBe(FREMD);

    leeren();
    tokenSetzen(TOKEN_APP, ALT);
    for (const muell of ["", "kein token", ALT, null, 42]) {
      expect(tokenErneuern(TOKEN_APP, ALT, muell)).toBe(false);
    }
    expect(tokenLesen(TOKEN_APP)).toBe(ALT);
  });
});

describe("RP-546: neuesTokenUebernehmen (Antwort-Abfänger)", () => {
  beforeEach(leeren);
  const antwort = (token, kopf = { "x-neues-token": NEU }) => ({
    headers: kopf, config: { headers: { Authorization: `Bearer ${token}` } },
  });

  it("übernimmt nur, wenn die Anfrage mit dem aktuellen Token lief", () => {
    tokenSetzen(TOKEN_APP, ALT);
    expect(neuesTokenUebernehmen(antwort(FREMD))).toBe(false);   // verspätete fremde Antwort
    expect(tokenLesen(TOKEN_APP)).toBe(ALT);
    expect(neuesTokenUebernehmen(antwort(ALT, {}))).toBe(false); // keine Kopfzeile
    expect(neuesTokenUebernehmen(antwort(ALT))).toBe(true);
    expect(tokenLesen(TOKEN_APP)).toBe(NEU);
  });

  it("Käufer-Instanz: eigener Schlüssel, App-Token bleibt unberührt", () => {
    tokenSetzen(TOKEN_APP, FREMD);
    tokenSetzen(TOKEN_KAEUFER, ALT);
    expect(neuesTokenUebernehmen(antwort(ALT), TOKEN_KAEUFER)).toBe(true);
    expect(tokenLesen(TOKEN_KAEUFER)).toBe(NEU);
    expect(tokenLesen(TOKEN_APP)).toBe(FREMD);
  });

  it("liest auch AxiosHeaders (get)", () => {
    tokenSetzen(TOKEN_APP, ALT);
    const kopf = { get: (n) => (n === "x-neues-token" ? NEU : undefined) };
    expect(neuesTokenUebernehmen(antwort(ALT, kopf))).toBe(true);
    expect(tokenLesen(TOKEN_APP)).toBe(NEU);
  });

  it("wirft nie", () => {
    expect(neuesTokenUebernehmen(null)).toBe(false);
    expect(neuesTokenUebernehmen({})).toBe(false);
  });
});

describe("RP-036/RP-286: Längenfehler auf Deutsch", () => {
  const fehler = (...detail) => ({ response: { data: { detail } } });

  it("string_too_long mit deutschem Feldnamen", () => {
    expect(errMsg(fehler({
      type: "string_too_long", loc: ["body", "description"], ctx: { max_length: 500 },
      msg: "String should have at most 500 characters",
    }))).toBe("Beschreibung: höchstens 500 Zeichen");
  });

  it("Listen-Index zählt nicht als Feldname; Listen zählen Einträge", () => {
    expect(validierungsText({ type: "string_too_long", loc: ["body", "features", 3],
      ctx: { max_length: 120 } })).toBe("Ausstattung: höchstens 120 Zeichen");
    expect(validierungsText({ type: "too_long", loc: ["body", "costs"],
      ctx: { max_length: 30, field_type: "List" } })).toBe("Kosten: höchstens 30 Einträge");
    expect(validierungsText({ type: "string_too_long", loc: ["body", "unbekannt_feld"],
      ctx: { max_length: 5 } })).toBe("unbekannt_feld: höchstens 5 Zeichen");
    expect(validierungsText({ type: "string_too_long", loc: ["body"],
      ctx: { max_length: 5 } })).toBe("Höchstens 5 Zeichen");
  });

  it("zu kurz; alles andere wie bisher über msg; mehrere mit ·", () => {
    expect(validierungsText({ type: "string_too_short", loc: ["body", "title"],
      ctx: { min_length: 1 } })).toBe("Titel: darf nicht leer sein");
    expect(validierungsText({ type: "string_too_short", loc: ["body", "title"],
      ctx: { min_length: 3 } })).toBe("Titel: mindestens 3 Zeichen");
    expect(validierungsText({ type: "too_short", loc: ["body", "deviation_ids"],
      ctx: { min_length: 1 } })).toBe("deviation_ids: mindestens ein Eintrag");
    expect(validierungsText({ type: "value_error", msg: "Value error, Ungültige PLZ" }))
      .toBe("Ungültige PLZ");
    expect(errMsg(fehler(
      { type: "string_too_long", loc: ["body", "title"], ctx: { max_length: 120 } },
      "schon deutsch"))).toBe("Titel: höchstens 120 Zeichen · schon deutsch");
    expect(errMsg({ response: { data: { detail: "Vertrag nicht gefunden" } } }))
      .toBe("Vertrag nicht gefunden");
  });
});

describe("RP-045/RP-144: /features", () => {
  afterEach(() => { vi.restoreAllMocks(); featuresSetzen(null); });

  it("Fehlschlag gilt nur für diesen Aufruf — der nächste fragt neu", async () => {
    featuresSetzen(null);
    const get = vi.spyOn(api, "get")
      .mockRejectedValueOnce(new Error("Network Error"))
      .mockResolvedValueOnce({ data: { marktplatz: true } });
    expect(await featuresLaden()).toEqual({ marktplatz: false, markt_chancen: false });
    expect(await featuresLaden()).toEqual({ marktplatz: true, markt_chancen: false });
    expect(await featuresLaden()).toEqual({ marktplatz: true, markt_chancen: false });   // jetzt zwischengespeichert
    expect(get).toHaveBeenCalledTimes(2);
  });
});

describe("RP-464: Zähler vom Fahrer abgelehnter Fahrten", () => {
  let wurzel = null;
  let behaelter = null;
  function Zeige({ aktiv = true, konto = "chef1" }) {
    const n = useAbgelehntZaehler(aktiv, konto);
    return createElement("span", { "data-testid": "n" }, String(n));
  }
  async function rendern(el) {
    behaelter = document.createElement("div");
    document.body.appendChild(behaelter);
    wurzel = createRoot(behaelter);
    await act(async () => { wurzel.render(el); });
    await act(async () => { await Promise.resolve(); });
    return behaelter;
  }
  beforeEach(() => abgelehntZuruecksetzen());
  afterEach(() => {
    if (wurzel) act(() => wurzel.unmount());
    behaelter?.remove();
    wurzel = null;
    vi.restoreAllMocks();
  });

  it("liest die Anzahl robust", () => {
    expect(abgelehntAnzahl({ anzahl: 3 })).toBe(3);
    for (const x of [null, {}, { anzahl: "x" }, { anzahl: -2 }]) expect(abgelehntAnzahl(x)).toBe(0);
    expect(abgelehntDauerhaftAus({ response: { status: 404 } })).toBe(true);
    expect(abgelehntDauerhaftAus({ response: { status: 500 } })).toBe(false);
  });

  it("zeigt die Zahl vom Server", async () => {
    const get = vi.spyOn(api, "get").mockResolvedValue({ data: { anzahl: 2 } });
    const el = await rendern(createElement(Zeige));
    expect(get).toHaveBeenCalledWith("/appointments/fahrer-abgelehnt/anzahl");
    expect(el.textContent).toBe("2");
  });

  it("fehlt der Endpunkt (404), fragt der Tab nicht weiter", async () => {
    const get = vi.spyOn(api, "get").mockRejectedValue({ response: { status: 404 } });
    const el = await rendern(createElement(Zeige));
    expect(el.textContent).toBe("0");
    await act(async () => { document.dispatchEvent(new Event("visibilitychange")); });
    expect(get).toHaveBeenCalledTimes(1);
  });

  it("RP-472: Kaufanfragen-Zähler nutzt den Zähl-Endpunkt, sonst die Listen", async () => {
    const { useAnfragenZaehler } = await import("./anfragenZaehler");
    function Anfragen({ konto }) {
      return createElement("span", null, String(useAnfragenZaehler(true, konto)));
    }
    let get = vi.spyOn(api, "get").mockResolvedValue({ data: { anzahl: 4 } });
    let el = await rendern(createElement(Anfragen, { konto: "k_zahl" }));
    expect(get).toHaveBeenCalledTimes(1);
    expect(get).toHaveBeenCalledWith("/dealer/interessen/anzahl");
    expect(el.textContent).toBe("4");
    act(() => wurzel.unmount());
    behaelter.remove();
    vi.restoreAllMocks();

    // alter Server ohne Zähl-Endpunkt (Rollout): zwei Listen wie bisher
    get = vi.spyOn(api, "get").mockImplementation(async (url) => {
      if (url === "/dealer/interessen/anzahl") throw { response: { status: 404 } };
      return { data: [{ id: 1 }] };
    });
    el = await rendern(createElement(Anfragen, { konto: "k_listen" }));
    await act(async () => { await Promise.resolve(); });
    expect(get).toHaveBeenCalledTimes(3);
    expect(el.textContent).toBe("2");
  });

  it("nicht aktiv (Sucher): kein Abruf", async () => {
    const get = vi.spyOn(api, "get").mockResolvedValue({ data: { anzahl: 5 } });
    const el = await rendern(createElement(Zeige, { aktiv: false }));
    expect(get).not.toHaveBeenCalled();
    expect(el.textContent).toBe("0");
  });
});
