/** Akzentfarbe der App (Wunsch Ahmad 02.10.2026): Palette, Anwenden am Dokument, Merken im Browser. */
import { beforeEach, describe, expect, it } from "vitest";
import {
  AKZENTFARBEN, AKZENT_KEY, akzentAnwenden, akzentMerken, akzentSchluessel, akzentVomKonto, akzentWerte,
  applyStoredAkzent, gespeicherterAkzent,
} from "./akzent";

const wurzel = () => document.documentElement;

beforeEach(() => {
  window.localStorage.clear();
  wurzel().removeAttribute("data-theme");
  wurzel().removeAttribute("style");
});

describe("Palette", () => {
  it("Standard plus zehn Farben, jede mit dunklem und hellem Paar", () => {
    expect(AKZENTFARBEN[0].key).toBe("standard");
    expect(AKZENTFARBEN.length).toBe(11);
    for (const f of AKZENTFARBEN) {
      expect(f.dunkel).toHaveLength(2);
      expect(f.hell).toHaveLength(2);
      expect(f.label).toBeTruthy();
    }
    expect(AKZENTFARBEN.map((f) => f.key)).toEqual(
      ["standard", "rot", "lila", "gruen", "blau", "schwarz", "orange", "petrol", "pink", "gold", "indigo"]);
  });
  it("unbekannte Schlüssel fallen auf Standard", () => {
    expect(akzentSchluessel("neon")).toBe("standard");
    expect(akzentSchluessel(" LILA ")).toBe("lila");
    expect(akzentSchluessel(null)).toBe("standard");
  });
  it("Werte je Design; Standard setzt nichts", () => {
    expect(akzentWerte("standard")).toEqual({});
    const dunkel = akzentWerte("lila", "dark");
    expect(dunkel["--accent-red"]).toBe("#a855f7");
    expect(dunkel["--accent-red-hover"]).toBe("#7e3af2");
    expect(dunkel["--knopf-primaer"]).toBe("#a855f7");
    expect(dunkel["--knopf-primaer-dunkel"]).toBe("#7e3af2");
    expect(dunkel["--border-focus"]).toBe("rgba(168, 85, 247, 0.5)");
    expect(dunkel["--akzent-rgb"]).toBe("168, 85, 247");
    const hell = akzentWerte("lila", "light");
    expect(hell["--accent-red"]).toBe("#7c3aed");
    expect(akzentWerte("schwarz", "dark")["--accent-red"]).toBe("#6e6e73");
    expect(akzentWerte("schwarz", "light")["--accent-red"]).toBe("#1d1d1f");
  });
});

describe("Anwenden und Merken", () => {
  it("setzt die Variablen am Dokument und räumt sie bei Standard wieder weg", () => {
    expect(akzentAnwenden("gruen", "dark")).toBe("gruen");
    expect(wurzel().style.getPropertyValue("--accent-red")).toBe("#22c55e");
    expect(wurzel().style.getPropertyValue("--knopf-primaer-hover")).toMatch(/^#/);
    expect(wurzel().getAttribute("data-akzent")).toBe("gruen");
    akzentAnwenden("standard");
    expect(wurzel().style.getPropertyValue("--accent-red")).toBe("");
    expect(wurzel().getAttribute("data-akzent")).toBe("standard");
  });
  it("folgt dem Design: helle Töne im hellen Design", () => {
    wurzel().setAttribute("data-theme", "light");
    akzentAnwenden("blau");
    expect(wurzel().style.getPropertyValue("--accent-red")).toBe("#0071e3");
    wurzel().setAttribute("data-theme", "dark");
    applyStoredAkzent("dark");                       // nichts gemerkt -> Standard
    expect(wurzel().style.getPropertyValue("--accent-red")).toBe("");
  });
  it("merkt im Browser und wendet beim Start an; der Kontowert geht vor", () => {
    expect(akzentMerken("pink")).toBe("pink");
    expect(window.localStorage.getItem(AKZENT_KEY)).toBe("pink");
    expect(gespeicherterAkzent()).toBe("pink");
    applyStoredAkzent("dark");
    expect(wurzel().style.getPropertyValue("--accent-red")).toBe("#ec4899");
    expect(akzentVomKonto("gold")).toBe("gold");
    expect(window.localStorage.getItem(AKZENT_KEY)).toBe("gold");
    expect(wurzel().style.getPropertyValue("--accent-red")).toBe("#eab308");
    // kein Kontowert (älteres Backend / nichts gespeichert): lokale Wahl bleibt
    expect(akzentVomKonto(undefined)).toBe("gold");
    expect(akzentVomKonto("")).toBe("gold");
  });
});
