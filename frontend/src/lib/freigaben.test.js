/**
 * Wunsch Ahmad (12.09.2026): Der Chef soll merken, dass Fahrer auf seine
 * Freigabe warten — auch wenn er gerade nicht auf der Termine-Seite ist.
 */
import { describe, expect, it } from "vitest";
import { neuHinzugekommen, neueWartende, titelMitZahl } from "./freigaben";

describe("titelMitZahl", () => {
  it("setzt, ersetzt und entfernt die Zahl im Tab-Titel", () => {
    expect(titelMitZahl("AutoSchnell", 2)).toBe("(2) AutoSchnell");
    expect(titelMitZahl("(2) AutoSchnell", 3)).toBe("(3) AutoSchnell");
    expect(titelMitZahl("(3) AutoSchnell", 0)).toBe("AutoSchnell");
    expect(titelMitZahl("", 1)).toBe("(1) ");
  });
});

describe("neuHinzugekommen", () => {
  it("meldet nur, was seit dem letzten Abruf dazukam", () => {
    expect(neuHinzugekommen(1, 3)).toBe(2);
    expect(neuHinzugekommen(3, 1)).toBe(0);
    expect(neuHinzugekommen(2, 2)).toBe(0);
  });

  it("beim ersten Abruf kein Hinweis für das, was schon wartet", () => {
    expect(neuHinzugekommen(null, 4)).toBe(0);
    expect(neuHinzugekommen(undefined, 4)).toBe(0);
  });
});

describe("neueWartende", () => {
  it("meldet neue Protokolle auch bei gleicher Zahl", () => {
    const merk = {};
    expect(neueWartende(merk, "u1", ["a"])).toEqual([]);          // Altbestand
    expect(neueWartende(merk, "u1", ["a", "b"])).toEqual(["b"]);
    expect(neueWartende(merk, "u1", ["b"])).toEqual([]);          // a freigegeben
    expect(neueWartende(merk, "u1", ["c"])).toEqual(["c"]);       // Zahl gleich, trotzdem neu
  });

  it("ein anderes Konto im selben Tab beginnt ohne Hinweis", () => {
    const merk = {};
    neueWartende(merk, "u1", ["a"]);
    expect(neueWartende(merk, "u2", ["x", "y"])).toEqual([]);
    expect(neueWartende(merk, "u2", ["x", "y", "z"])).toEqual(["z"]);
  });
});
