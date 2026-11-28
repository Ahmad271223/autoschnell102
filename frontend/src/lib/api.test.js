/**
 * Runde 29 (12.09.2026, Pruefbefund): Der Browser brach jede Anfrage nach
 * 60 s ab, nginx laesst sie bis 300 s laufen. Beim Erzeugen des Kaufvertrags
 * und beim Laden von PDFs sah der Nutzer deshalb einen Fehler, obwohl der
 * Server weiterarbeitete. Diese Wege bekommen jetzt ein laengeres Zeitlimit.
 */
import { afterEach, beforeEach, describe, expect, it } from "vitest";
import {
  ABRUF_ZEITUEBERSCHREITUNG, anmeldeAdresse, api, darfWiederholen, errMsg,
  gehoertZumAktuellenToken, imGeschuetztenBereich, istAbruf, istLangeAktion,
  LANGE_AKTION_MS, wiederholenNachMs, WIEDERHOLEN_MAX,
} from "./api";
import { hatUngespeichert, ungespeichertMelden } from "./ungespeichert";

describe("istLangeAktion", () => {
  it("gilt fuer jeden Datei-Abruf (PDF, Bilder)", () => {
    expect(istLangeAktion({ url: "/beweise/b1/pdf", responseType: "blob" })).toBe(true);
    expect(istLangeAktion({ url: "/contracts/c1/pdf", responseType: "blob" })).toBe(true);
  });

  it("gilt fuer das Erzeugen des Kaufvertrags", () => {
    expect(istLangeAktion({ url: "/contracts", method: "post" })).toBe(true);
    expect(istLangeAktion({ url: "/contracts/c1/send", method: "POST" })).toBe(true);
  });

  it("gilt fuer den Protokoll-Abschluss (Abnahme 12.09.2026)", () => {
    // Der langsamste Weg der Fahrer-App: PDF bauen + zwei Unterschriften.
    expect(istLangeAktion({ url: "/driver/appointments/a1/protocol/finalize", method: "post" })).toBe(true);
    expect(istLangeAktion({ url: "/driver/appointments/a1/protocol/submit", method: "post" })).toBe(true);
    expect(istLangeAktion({ url: "/driver/appointments/a1/report", method: "post" })).toBe(true);
  });

  it("gilt NICHT fuer die schnellen Wege", () => {
    expect(istLangeAktion({ url: "/contracts", method: "get" })).toBe(false);
    expect(istLangeAktion({ url: "/mobile/compare", method: "post" })).toBe(false);
    expect(istLangeAktion({ url: "/bestand" })).toBe(false);
    expect(istLangeAktion({})).toBe(false);
  });

  it("bleibt unter dem Limit von nginx (300 s)", () => {
    expect(LANGE_AKTION_MS).toBeGreaterThan(60000);
    expect(LANGE_AKTION_MS).toBeLessThan(300000);
  });

  it("DP-04: gibt VOR dem Cloudflare-Abbruch (~100 s) auf — sonst kommt die rohe 524-Seite", () => {
    expect(LANGE_AKTION_MS).toBeLessThan(100000);
  });
});

/**
 * Pruefbericht 20.09.2026 (DP-04): Laeuft ein Fahrzeug-Abruf ins Zeitlimit,
 * arbeitet der Server (Apify) im Hintergrund weiter — die Meldung sagt das.
 */
describe("DP-04: Zeitueberschreitung beim Abruf", () => {
  it("erkennt die Abruf-Wege", () => {
    expect(istAbruf({ url: "/mobile/compare" })).toBe(true);
    expect(istAbruf({ url: "/listings/check" })).toBe(true);
    expect(istAbruf({ url: "/listings/check/j1?x=1" })).toBe(true);
    expect(istAbruf({ url: "/listings/ingest" })).toBe(true);
    expect(istAbruf({ url: "/contracts" })).toBe(false);
    expect(istAbruf({ url: "/mobile/comparex" })).toBe(false);
    expect(istAbruf({})).toBe(false);
    expect(istAbruf(undefined)).toBe(false);
  });

  it("nennt beim Abruf das Weiterladen im Hintergrund, sonst die allgemeine Meldung", () => {
    const zeit = (url) => ({ code: "ECONNABORTED", message: "timeout of 95000ms exceeded", config: { url } });
    expect(errMsg(zeit("/mobile/compare"))).toBe(ABRUF_ZEITUEBERSCHREITUNG);
    expect(errMsg(zeit("/mobile/compare"))).toMatch(/in einer Minute/);
    expect(errMsg(zeit("/contracts"))).toMatch(/nicht rechtzeitig geantwortet/);
    expect(errMsg({ code: "ECONNABORTED", message: "timeout" })).toMatch(/nicht rechtzeitig geantwortet/);
  });
});

/**
 * Pruefbericht 20.09.2026 (U-147/U-151/U-80): beendete Sitzung.
 *  U-147  /abo zaehlt wie /app und /admin (Bereich aus lib/rollen).
 *  U-151  Rueckweg (next=) mit Query und Fragment.
 *  U-80   Die Rueckfrage "ungespeichert" haelt die Umleitung nicht auf.
 */
describe("U-147: geschuetzter Bereich fuer die Umleitung", () => {
  it("deckt /app, /admin und /abo ab — nicht die oeffentlichen Seiten", () => {
    for (const p of ["/app", "/app/vergleich", "/admin", "/admin/users/5", "/abo", "/abo/x"]) {
      expect(imGeschuetztenBereich(p)).toBe(true);
    }
    for (const p of ["/", "/login", "/start", "/fahrer", "/markt", "/appartements", "/impressum"]) {
      expect(imGeschuetztenBereich(p)).toBe(false);
    }
  });
});

describe("U-151: Anmeldeadresse mit Rueckweg", () => {
  it("nimmt Pfad, Query und Fragment mit", () => {
    expect(anmeldeAdresse({ pathname: "/app/akte/5", search: "?tab=kosten", hash: "#oben" }))
      .toBe(`/login?reason=session&next=${encodeURIComponent("/app/akte/5?tab=kosten#oben")}`);
    expect(anmeldeAdresse({ pathname: "/abo" })).toBe("/login?reason=session&next=%2Fabo");
  });
});

describe("401-Abfaenger: Umleitung zur Anmeldung", () => {
  let echt;
  beforeEach(() => {
    echt = window.location;
    delete window.location;
    window.location = { pathname: "/abo", search: "?von=test", hash: "", href: "" };
    sessionStorage.clear();
    localStorage.clear();
  });
  afterEach(() => {
    window.location = echt;
    sessionStorage.clear();
  });

  /** Der eigentliche Abfaenger ist der zuletzt registrierte (nach fassungMithoeren). */
  const abfaenger = () => {
    const alle = api.interceptors.response.handlers.filter((h) => h && h.rejected);
    return alle[alle.length - 1].rejected;
  };
  const fehler401 = (url) => {
    const e = new Error("Request failed with status code 401");
    e.config = { url, headers: { Authorization: "Bearer T" } };
    e.response = { status: 401, data: { detail: "Sitzung beendet" }, headers: {} };
    return e;
  };

  it("auf /abo: Token weg, Grund gemerkt, Umleitung MIT Rueckweg, Rueckfrage aufgehoben", async () => {
    sessionStorage.setItem("ah_token", "T");
    const aufheben = ungespeichertMelden();
    expect(hatUngespeichert()).toBe(true);
    const err = fehler401("/bestand");
    await expect(abfaenger()(err)).rejects.toBe(err);
    expect(sessionStorage.getItem("ah_token")).toBeNull();
    expect(sessionStorage.getItem("ah_abmeldegrund")).toBe("Sitzung beendet");
    expect(window.location.href).toBe(`/login?reason=session&next=${encodeURIComponent("/abo?von=test")}`);
    expect(hatUngespeichert()).toBe(false);      // U-80
    aufheben();
  });

  it("auf einer oeffentlichen Seite: keine Umleitung", async () => {
    window.location.pathname = "/impressum";
    sessionStorage.setItem("ah_token", "T");
    const err = fehler401("/bestand");
    await expect(abfaenger()(err)).rejects.toBe(err);
    expect(window.location.href).toBe("");
  });
});

/**
 * Nachpruefung 20.09.2026 (Nr. 39/40/42): Eine verspaetete 401 aus einer
 * FRUEHEREN Anmeldung loeschte den gerade frisch gespeicherten Token.
 *
 * Der Ablauf, der schiefging:
 *   1. Anfrage mit Token A ist unterwegs.
 *   2. Im selben Tab meldet sich jemand als Konto B an -> Token B liegt.
 *   3. Die alte Anfrage antwortet verspaetet mit 401.
 *   4. Der Abfaenger loeschte Token B und warf Konto B zurueck zum Login.
 *
 * Befund Nr. 42 sagte ausdruecklich, dass genau dieser Fall nirgends
 * geprueft wurde — deshalb steht er jetzt hier.
 */
describe("gehoertZumAktuellenToken", () => {
  it("laesst eine 401 zum AKTUELLEN Token gelten", () => {
    const config = { headers: { Authorization: "Bearer A" } };
    expect(gehoertZumAktuellenToken(config, "A")).toBe(true);
  });

  it("verwirft die verspaetete 401 einer frueheren Anmeldung", () => {
    const config = { headers: { Authorization: "Bearer A" } };
    expect(gehoertZumAktuellenToken(config, "B")).toBe(false);
  });

  it("verwirft sie auch, wenn inzwischen gar kein Token mehr da ist", () => {
    const config = { headers: { Authorization: "Bearer A" } };
    expect(gehoertZumAktuellenToken(config, null)).toBe(false);
    expect(gehoertZumAktuellenToken(config, "")).toBe(false);
  });

  it("laesst Anfragen ohne Token unveraendert durch", () => {
    // Anmeldung selbst, oeffentliche Wege: da gibt es nichts zu schuetzen.
    expect(gehoertZumAktuellenToken({ headers: {} }, "B")).toBe(true);
    expect(gehoertZumAktuellenToken({}, "B")).toBe(true);
    expect(gehoertZumAktuellenToken(undefined, "B")).toBe(true);
  });
});

/**
 * Pruefbericht 20.09.2026 (P1): Legen zwei Sucher fast gleichzeitig einen
 * Vertrag zum SELBEN Fahrzeug an, wartet der zweite serverseitig 6 s und
 * bekam dann einen sichtbaren Fehler — er musste von Hand noch einmal
 * speichern. Der Server weiss an der Stelle, dass NICHTS geschrieben
 * wurde, und sagt es mit `X-Wiederholen: 1`.
 */
describe("darfWiederholen", () => {
  const fehler = (status, headers = {}, versuche = 0) => ({
    response: { status, headers },
    config: { __versuche: versuche },
  });

  it("wiederholt die belegte Vertragssperre", () => {
    expect(darfWiederholen(fehler(503, { "x-wiederholen": "1" }))).toBe(true);
  });

  it("wiederholt NIE einen beliebigen 503", () => {
    // Sonst entstuende beim Vertragsanlegen ein zweiter Vertrag.
    expect(darfWiederholen(fehler(503))).toBe(false);
    expect(darfWiederholen(fehler(503, { "retry-after": "3" }))).toBe(false);
    expect(darfWiederholen(fehler(503, { "x-wiederholen": "0" }))).toBe(false);
  });

  it("wiederholt keine anderen Fehler", () => {
    for (const s of [400, 401, 403, 409, 500, 502, 504]) {
      expect(darfWiederholen(fehler(s, { "x-wiederholen": "1" }))).toBe(false);
    }
    expect(darfWiederholen({})).toBe(false);        // Netzfehler ohne Antwort
    expect(darfWiederholen(undefined)).toBe(false);
  });

  it("hoert nach WIEDERHOLEN_MAX auf", () => {
    const kopf = { "x-wiederholen": "1" };
    expect(darfWiederholen(fehler(503, kopf, WIEDERHOLEN_MAX - 1))).toBe(true);
    expect(darfWiederholen(fehler(503, kopf, WIEDERHOLEN_MAX))).toBe(false);
    expect(darfWiederholen(fehler(503, kopf, 99))).toBe(false);
  });
});

describe("wiederholenNachMs", () => {
  it("nimmt Retry-After in Sekunden", () => {
    expect(wiederholenNachMs({ response: { headers: { "retry-after": "3" } } })).toBe(3000);
    expect(wiederholenNachMs({ response: { headers: { "retry-after": "10" } } })).toBe(10000);
  });

  it("faellt auf 3 s zurueck und wartet nie ewig", () => {
    expect(wiederholenNachMs({ response: { headers: {} } })).toBe(3000);
    expect(wiederholenNachMs({ response: { headers: { "retry-after": "quatsch" } } })).toBe(3000);
    expect(wiederholenNachMs({ response: { headers: { "retry-after": "-5" } } })).toBe(3000);
    expect(wiederholenNachMs({ response: { headers: { "retry-after": "9999" } } })).toBe(30000);
    expect(wiederholenNachMs(undefined)).toBe(3000);
  });
});
