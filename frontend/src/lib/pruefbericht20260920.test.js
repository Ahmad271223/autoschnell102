/*
 * Prüfbericht 20.09.2026 — Sucher-Dashboard, Blocker B1–B19.
 * Reine Bausteine, ohne Browser: jede Funktion hier stand vorher so im Code,
 * dass sie abstürzte, still scheiterte oder falsche Daten schrieb.
 */
import { afterEach, describe, expect, it, vi } from "vitest";

const toastMock = vi.hoisted(() => ({ success: vi.fn(), error: vi.fn(), info: vi.fn() }));
vi.mock("sonner", () => ({ toast: toastMock }));

const { lesen, lokalerSpeicher, schreiben, sitzungsSpeicher } = await import("./speicher");
const { abmeldegrundMerken, blobFehlerLesbar, istFirmensperre } = await import("./api");
const { blobOeffnen, klickNochFrisch } = await import("./dateiOeffnen");
const { vergleichEntfernen, vergleichKey, vergleichSichern } = await import("./vergleichSpeicher");

function speicher(start = {}) {
  const daten = { ...start };
  return {
    daten,
    getItem: (k) => (k in daten ? daten[k] : null),
    setItem: (k, v) => { daten[k] = String(v); },
    removeItem: (k) => { delete daten[k]; },
    get length() { return Object.keys(daten).length; },
    key: (i) => Object.keys(daten)[i] ?? null,
  };
}

afterEach(() => {
  vi.restoreAllMocks();
  toastMock.success.mockClear();
});

describe("B1/B4: gesperrter Browser-Speicher", () => {
  it("wirft nie, auch wenn schon der Zugriff auf window.localStorage wirft", () => {
    const echt = Object.getOwnPropertyDescriptor(window, "localStorage");
    Object.defineProperty(window, "localStorage", {
      configurable: true,
      get() { throw new DOMException("blockiert", "SecurityError"); },
    });
    try {
      expect(lokalerSpeicher()).toBeNull();
      expect(lesen(lokalerSpeicher(), "ah_theme", "dark")).toBe("dark");
      expect(schreiben(lokalerSpeicher(), "ah_theme", "light")).toBe(false);
    } finally {
      Object.defineProperty(window, "localStorage", echt);
    }
  });

  it("liest und schreibt normal, wenn der Speicher geht", () => {
    const s = speicher();
    expect(schreiben(s, "a", "1")).toBe(true);
    expect(lesen(s, "a")).toBe("1");
    expect(lesen(s, "fehlt", "x")).toBe("x");
    // getItem wirft (Quota/Privatmodus) -> Ersatzwert
    expect(lesen({ getItem: () => { throw new Error("x"); } }, "a", "e")).toBe("e");
  });

  it("sitzungsSpeicher liefert den echten Speicher oder null", () => {
    const s = sitzungsSpeicher();
    expect(s === null || typeof s.getItem === "function").toBe(true);
  });
});

describe("B2/H1: Firmensperre meldet ab — und nur sie", () => {
  it("erkennt nur die 403 mit X-Sperre: firma", () => {
    expect(istFirmensperre({ response: { status: 403, headers: { "x-sperre": "firma" } } })).toBe(true);
    expect(istFirmensperre({ response: { status: 403, headers: {} } })).toBe(false);
    expect(istFirmensperre({ response: { status: 401, headers: { "x-sperre": "firma" } } })).toBe(false);
    expect(istFirmensperre({})).toBe(false);
  });

  it("merkt den Grund für die Anmeldeseite, ohne zu werfen", () => {
    expect(() => abmeldegrundMerken("Die Firma ist gesperrt")).not.toThrow();
    expect(() => abmeldegrundMerken(undefined)).not.toThrow();
  });
});

describe("M34: Fehlertext bei PDF-Abrufen", () => {
  it("macht aus einer JSON-Blob-Antwort wieder ein lesbares detail", async () => {
    const blob = new Blob([JSON.stringify({ detail: "PDF konnte nicht erzeugt werden" })],
                          { type: "application/json" });
    const err = { response: { status: 400, data: blob } };
    await blobFehlerLesbar(err);
    expect(err.response.data.detail).toBe("PDF konnte nicht erzeugt werden");
  });

  it("lässt alles andere unverändert", async () => {
    const err = { response: { status: 500, data: { detail: "x" } } };
    await blobFehlerLesbar(err);
    expect(err.response.data.detail).toBe("x");
    const pdf = new Blob(["%PDF"], { type: "application/pdf" });
    const err2 = { response: { status: 500, data: pdf } };
    await blobFehlerLesbar(err2);
    expect(err2.response.data).toBe(pdf);
  });
});

describe("B15: PDF öffnen nach langem Laden", () => {
  it("fragt die Browser-Aktivierung, wenn es sie gibt", () => {
    expect(klickNochFrisch(0, 99999, { userActivation: { isActive: true } })).toBe(true);
    expect(klickNochFrisch(0, 1, { userActivation: { isActive: false } })).toBe(false);
  });

  it("ohne Aktivierungs-API zählt nur ein sehr kurzer Abstand", () => {
    expect(klickNochFrisch(1000, 1500, {})).toBe(true);
    expect(klickNochFrisch(1000, 9000, {})).toBe(false);
  });

  it("zeigt einen Hinweis mit Knopf, wenn der Klick verfallen ist", () => {
    const nav = Object.getOwnPropertyDescriptor(globalThis.navigator, "userActivation");
    Object.defineProperty(globalThis.navigator, "userActivation", {
      configurable: true, value: { isActive: false },
    });
    const createObjectURL = vi.fn(() => "blob:test");
    const revoke = vi.fn();
    globalThis.URL.createObjectURL = createObjectURL;
    globalThis.URL.revokeObjectURL = revoke;
    try {
      const art = blobOeffnen(new Blob(["%PDF"], { type: "application/pdf" }),
                              { startMs: 0, titel: "Der Kaufvertrag" });
      expect(art).toBe("hinweis");
      expect(toastMock.success).toHaveBeenCalledTimes(1);
      const [text, optionen] = toastMock.success.mock.calls[0];
      expect(text).toContain("Der Kaufvertrag ist fertig");
      expect(optionen.action.label).toBe("Öffnen");
      // M36: nicht nach 1 s freigeben
      expect(revoke).not.toHaveBeenCalled();
    } finally {
      if (nav) Object.defineProperty(globalThis.navigator, "userActivation", nav);
      else delete globalThis.navigator.userActivation;
    }
  });
});

describe("H8: gescheiterter Vergleich holt nicht das alte Auto zurück", () => {
  it("entfernt nur den Stand DIESES Kontos", () => {
    const s = speicher();
    vergleichSichern(s, "u1", { url: "a" });
    vergleichSichern(s, "u2", { url: "b" });
    expect(vergleichEntfernen(s, "u1")).toBe(true);
    expect(s.daten[vergleichKey("u1")]).toBeUndefined();
    expect(s.daten[vergleichKey("u2")]).toBeDefined();
    expect(vergleichEntfernen(null, "u1")).toBe(false);
  });
});

describe("U-08/U-29: Netzfehler auf Deutsch", async () => {
  const { errMsg } = await import("./api");
  it("Zeitüberschreitung ohne Serverantwort", () => {
    expect(errMsg({ code: "ECONNABORTED", message: "timeout of 60000ms exceeded" }))
      .toMatch(/nicht rechtzeitig geantwortet/);
  });
  it("keine Verbindung", () => {
    expect(errMsg({ code: "ERR_NETWORK", message: "Network Error" })).toMatch(/Keine Verbindung/);
  });
  it("Serverantworten bleiben unverändert", () => {
    expect(errMsg({ response: { data: { detail: "Vertrag nicht gefunden" } } })).toBe("Vertrag nicht gefunden");
  });
});
