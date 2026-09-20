/**
 * Runde 29 (12.09.2026, Pruefbefund): Der Browser brach jede Anfrage nach
 * 60 s ab, nginx laesst sie bis 300 s laufen. Beim Erzeugen des Kaufvertrags
 * und beim Laden von PDFs sah der Nutzer deshalb einen Fehler, obwohl der
 * Server weiterarbeitete. Diese Wege bekommen jetzt ein laengeres Zeitlimit.
 */
import { describe, expect, it } from "vitest";
import {
  darfWiederholen, gehoertZumAktuellenToken, istLangeAktion,
  LANGE_AKTION_MS, wiederholenNachMs, WIEDERHOLEN_MAX,
} from "./api";

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
