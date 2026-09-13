/**
 * Runde 31 (12.09.2026): Erkennung einer neueren Fassung.
 * Vorfall: Fahrer-App und Super-Admin liefen nach einem Rollout auf eine
 * tote Seite, weil die Oberflaeche von der neuen Fassung nichts wusste.
 */
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import {
  _zuruecksetzen, fassungAbonnieren, fassungMithoeren, fassungPruefen,
  fassungsZeit, istNeuer, nachladenGescheitert, neueFassungLaden, veralteteFassung,
} from "./fassung";
import { ungespeichertMelden } from "./ungespeichert";

const ALT = "1757600000-aaaaaaa";
const NEU = "1757680000-bbbbbbb";
const NOCH_NEUER = "1757690000-ccccccc";
const antwort = (wert) => ({ headers: { "x-ah-fassung": wert } });

beforeEach(() => {
  _zuruecksetzen();
  window.sessionStorage.clear();
});

describe("fassungsZeit / istNeuer", () => {
  it("liest die Commit-Zeit aus dem Stempel", () => {
    expect(fassungsZeit(NEU)).toBe(1757680000);
    expect(fassungsZeit(" 1757680000-3af35e8 ")).toBe(1757680000);
  });

  it("verwirft alles, was kein Stempel ist", () => {
    for (const x of ["", null, undefined, "main.C-jXRoF8.js", "1757680000", "abc-123", "1757680000-xyz!"]) {
      expect(fassungsZeit(x)).toBeNull();
    }
  });

  it("meldet nur ECHT neuere Fassungen", () => {
    expect(istNeuer(NEU, ALT)).toBe(true);
    expect(istNeuer(ALT, NEU)).toBe(false);   // Rollout: alter Server antwortet
    expect(istNeuer(NEU, NEU)).toBe(false);
  });

  it("ohne eigenen Stempel (lokaler Bau, Tests) nie", () => {
    expect(istNeuer(NEU, "")).toBe(false);
  });
});

describe("fassungPruefen", () => {
  it("meldet eine neuere Fassung einmal an alle Hoerer", () => {
    const hoerer = vi.fn();
    fassungAbonnieren(hoerer);
    fassungPruefen(antwort(NEU), ALT);
    fassungPruefen(antwort(NEU), ALT);   // dieselbe Fassung noch einmal: keine zweite Meldung
    expect(hoerer).toHaveBeenCalledTimes(1);
    expect(veralteteFassung()).toEqual({ grund: "fassung", fassung: NEU });
  });

  it("springt im Rollout-Fenster nicht zurueck", () => {
    fassungPruefen(antwort(NEU), ALT);
    fassungPruefen(antwort(ALT), ALT);   // naechste Antwort vom alten Server
    expect(veralteteFassung().fassung).toBe(NEU);
    fassungPruefen(antwort(NOCH_NEUER), ALT);
    expect(veralteteFassung().fassung).toBe(NOCH_NEUER);
  });

  it("ignoriert gleiche oder aeltere Fassungen und fehlende Kopfzeilen", () => {
    fassungPruefen(antwort(ALT), ALT);
    fassungPruefen(antwort(""), ALT);
    fassungPruefen({ headers: {} }, ALT);
    fassungPruefen(undefined, ALT);
    expect(veralteteFassung()).toBeNull();
  });

  it("versteht die AxiosHeaders-Form (get)", () => {
    fassungPruefen({ headers: { get: (n) => (n === "x-ah-fassung" ? NEU : undefined) } }, ALT);
    expect(veralteteFassung()?.fassung).toBe(NEU);
  });
});

describe("fassungMithoeren", () => {
  it("wertet Erfolgs- UND Fehlerantworten aus", async () => {
    const abgefangen = [];
    const instanz = { interceptors: { response: { use: (ok, fehler) => abgefangen.push(ok, fehler) } } };
    fassungMithoeren(instanz);
    const [ok, fehler] = abgefangen;
    const r = antwort("1");
    expect(ok(r)).toBe(r);
    const err = { response: antwort("1") };
    await expect(fehler(err)).rejects.toBe(err);
  });
});

describe("nachladenGescheitert", () => {
  it("markiert die laufende Fassung als alt, ueberschreibt aber keine bekannte", () => {
    nachladenGescheitert();
    expect(veralteteFassung()).toEqual({ grund: "nachladen", fassung: "" });
    _zuruecksetzen();
    fassungPruefen(antwort(NEU), ALT);
    nachladenGescheitert();
    expect(veralteteFassung().grund).toBe("fassung");
  });
});

describe("neueFassungLaden", () => {
  let echt;
  beforeEach(() => {
    echt = window.location;
    delete window.location;
    window.location = { assign: vi.fn() };
  });
  afterEach(() => {
    window.location = echt;
  });

  it("tut nichts ohne erkannte neue Fassung", () => {
    expect(neueFassungLaden("/fahrer")).toBe(false);
    nachladenGescheitert();   // nur "nachladen" — kein Stempel bekannt
    expect(neueFassungLaden("/fahrer")).toBe(false);
    expect(window.location.assign).not.toHaveBeenCalled();
  });

  it("laedt die Zielseite frisch — je Fassung nur einmal", () => {
    fassungPruefen(antwort(NEU), ALT);
    expect(neueFassungLaden("/fahrer/protokoll/a1")).toBe(true);
    expect(window.location.assign).toHaveBeenCalledWith("/fahrer/protokoll/a1");
    // Landete das Neuladen im Rollout noch auf dem alten Server: keine Schleife.
    expect(neueFassungLaden("/fahrer")).toBe(false);
    expect(window.location.assign).toHaveBeenCalledTimes(1);
    // Eine NOCH neuere Fassung darf wieder laden.
    fassungPruefen(antwort(NOCH_NEUER), ALT);
    expect(neueFassungLaden("/fahrer")).toBe(true);
  });

  it("nie, solange ungespeicherte Arbeit gemeldet ist", () => {
    fassungPruefen(antwort(NEU), ALT);
    const aufheben = ungespeichertMelden();
    expect(neueFassungLaden("/fahrer")).toBe(false);
    expect(window.location.assign).not.toHaveBeenCalled();
    aufheben();
    expect(neueFassungLaden("/fahrer")).toBe(true);
  });
});
