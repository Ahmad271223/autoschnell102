/*
 * Prüfbericht 20.09.2026 (K-04): dieselben Regeln wie backend/passwoerter.py.
 */
import { describe, expect, it } from "vitest";
import { passwortProblem, passwortVorschlag, stamm } from "./passwort";

describe("K-04: Sperrliste wie am Server", () => {
  it("bekannte Passwörter mit Ziffern/Sonderzeichen am Rand werden abgelehnt", () => {
    for (const pw of ["Passwort123!", "!!Hannover2026", "Autohaus2026#", "willkommen2026"]) {
      expect(passwortProblem(pw)).toMatch(/zu bekannt/);
    }
  });
  it("stamm schneidet nur die Ränder ab", () => {
    expect(stamm("!!Hannover2026")).toBe("hannover");
    expect(stamm("ab1cd2345")).toBe("ab1cd");
  });
  it("Umlaute zählen nicht als Sonderzeichen (wie am Server)", () => {
    expect(passwortProblem("Grünwaldstraße")).toMatch(/Ziffer oder ein Sonderzeichen/);
    expect(passwortProblem("Grünwald-straße")).toBe("");
  });
  it("Vorschläge bestehen die Prüfung", () => {
    for (let i = 0; i < 20; i++) expect(passwortProblem(passwortVorschlag())).toBe("");
  });
  it("K-06: die Längenmeldung nennt Bytes (Umlaute zählen doppelt), wie am Server", () => {
    // 40 Umlaute = 80 Bytes > 72, aber nur 40 Zeichen.
    expect(passwortProblem(`${"ä".repeat(40)}1`)).toMatch(/72 Bytes/);
    expect(passwortProblem("Sommerregen2026!".repeat(4))).toBe("");   // 64 Bytes: in Ordnung
    expect(passwortProblem(" Sommerregen2026!")).toMatch(/Leerzeichen/);
  });
});
