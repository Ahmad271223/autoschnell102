import { describe, expect, it } from "vitest";
import { protokollZustand } from "./protokollZustand";

describe("protokollZustand (Go-Live 13.09.2026, N1)", () => {
  it("sperrt die Eingaben waehrend des Abschlusses und laedt nach", () => {
    const z = protokollZustand("wird_abgeschlossen");
    expect(z.wirdAbgeschlossen).toBe(true);
    expect(z.gesperrt).toBe(true);
    expect(z.nachladen).toBe(true);
    expect(z.unterschriften).toBe(true);
    expect(z.freigegeben).toBe(false);
  });

  it("Entwurf bleibt bearbeitbar, ohne Nachladen", () => {
    for (const status of [undefined, null, "", "entwurf"]) {
      const z = protokollZustand(status);
      expect(z.gesperrt).toBe(false);
      expect(z.nachladen).toBe(false);
      expect(z.unterschriften).toBe(false);
    }
  });

  it("bisherige Stati unveraendert", () => {
    expect(protokollZustand("zur_freigabe")).toMatchObject({ gesperrt: true, nachladen: true, unterschriften: false });
    expect(protokollZustand("freigegeben")).toMatchObject({ gesperrt: true, nachladen: true, unterschriften: true });
    expect(protokollZustand("final")).toMatchObject({ gesperrt: true, nachladen: false, isFinal: true });
  });
});
