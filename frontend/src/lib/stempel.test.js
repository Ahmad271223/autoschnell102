/** Firmenstempel (Wunsch Ahmad 02.10.2026): Textzeilen, sechs Designs, Zusammensetzen, Zuschneiden der Unterschrift. */
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import {
  VARIANTEN, baueZeilen, firmendaten, initialen, istTinte, layoutZeilen, stempelErzeugen, unterschriftZuschneiden,
} from "./stempel";

/** jsdom hat keinen 2D-Kontext — eine Attrappe, die alle genutzten Aufrufe kennt und ImageData liefert. */
function kontextAttrappe(canvas) {
  const w = () => canvas.width, h = () => canvas.height;
  let daten = new Uint8ClampedArray(0);
  const ctx = {
    font: "", fillStyle: "", strokeStyle: "", lineWidth: 1, textAlign: "", textBaseline: "",
    measureText: (t) => ({ width: String(t).length * (parseFloat(ctx.font) || 10) * 0.6 }),
    fillRect: vi.fn(), fillText: vi.fn(), beginPath: vi.fn(), moveTo: vi.fn(), lineTo: vi.fn(), arcTo: vi.fn(),
    closePath: vi.fn(), stroke: vi.fn(), fill: vi.fn(), arc: vi.fn(), save: vi.fn(), restore: vi.fn(),
    translate: vi.fn(), rotate: vi.fn(), drawImage: vi.fn(), clearRect: vi.fn(),
    getImageData: () => {
      if (daten.length !== w() * h() * 4) daten = new Uint8ClampedArray(w() * h() * 4);
      return { width: w(), height: h(), data: daten };
    },
    putImageData: vi.fn(),
    _setzeDaten: (d) => { daten = d; },
  };
  return ctx;
}

beforeEach(() => {
  vi.spyOn(HTMLCanvasElement.prototype, "getContext").mockImplementation(function () {
    if (!this.__ctx) this.__ctx = kontextAttrappe(this);
    return this.__ctx;
  });
  vi.spyOn(HTMLCanvasElement.prototype, "toDataURL").mockImplementation(() => "data:image/png;base64,ZUSCHNITT");
});
afterEach(() => vi.restoreAllMocks());

describe("Text und Daten", () => {
  it("Initialen und Beispiel-Daten", () => {
    expect(initialen("Mustermann Autohandel")).toBe("MA");
    expect(initialen("Müller")).toBe("MÜ");
    expect(initialen("")).toBe("F");
    expect(firmendaten({}).name).toBe("Mustermann Autohandel");
    expect(firmendaten({ name: " Autohaus X " }).name).toBe("Autohaus X");
    expect(firmendaten({ name: "Autohaus X" }).zusatz).toBe("");
  });
  it("Zeilen: Name groß und fett, Kontakt in einer Zeile oder getrennt, USt am Ende", () => {
    const d = { name: "Autohaus X", zusatz: "Inh. Y", strasse: "Weg 1", ort: "12345 Ort", tel: "030 1", mail: "a@b.de", ust: "DE1" };
    const L = baueZeilen(d, false, 60, 34);
    expect(L[0]).toEqual({ t: "AUTOHAUS X", bold: true, size: 60 });
    expect(L.map((l) => l.t)).toEqual(["AUTOHAUS X", "Inh. Y", "Weg 1", "12345 Ort", "Tel. 030 1  |  a@b.de", "St.-Nr./USt-IdNr. DE1"]);
    const G = baueZeilen(d, true, 60, 34);
    expect(G.map((l) => l.t)).toContain("Tel. 030 1");
    expect(G.map((l) => l.t)).toContain("a@b.de");
    // Layout: zu breite Zeilen schrumpfen, "gleich" nimmt die kleinste Groesse
    const mess = (t, size) => t.length * size;                    // sehr breite Schrift
    const h = layoutZeilen(L, 300, false, "x", 38, mess);
    expect(L.every((l) => l.size * l.t.length <= 300 || l.size === 14)).toBe(true);
    expect(h).toBeCloseTo(L.reduce((s, l) => s + l.size * 1.32, 0));
    layoutZeilen(G, 300, true, "x", 38, mess);
    expect(new Set(G.map((l) => l.size)).size).toBe(1);
  });
});

describe("Designs und Stempel", () => {
  it("sechs Designs, jedes liefert eine Leinwand mit Maßen", () => {
    expect(VARIANTEN.map((v) => v.id)).toEqual(["kasten", "rund", "ring", "text", "linien", "kapsel"]);
    const d = firmendaten({ name: "Autohaus X", strasse: "Weg 1", ort: "12345 Ort", tel: "030 1" });
    for (const v of VARIANTEN) {
      const c = v.fn(d, false);
      expect(c.width).toBeGreaterThan(100);
      expect(c.height).toBeGreaterThan(100);
    }
    const c = stempelErzeugen({ daten: d, variante: "rund", gleich: true, schraeg: true, abnutzung: true, unterschrift: null, einst: { an: true } });
    expect(c.width).toBe(800 + 100);
    expect(c.height).toBe(800 + 100);
    const ctx = c.getContext("2d");
    expect(ctx.rotate).toHaveBeenCalled();                        // schraeg
    expect(ctx.putImageData).toHaveBeenCalled();                  // Abnutzung
  });
  it("Unterschrift wird über den Stempel gezeichnet — nur wenn eingeschaltet", () => {
    const sig = { img: { width: 400, height: 150 }, w: 400, h: 150 };
    const c = stempelErzeugen({ daten: {}, variante: "kasten", unterschrift: sig, einst: { an: true, groesse: 55, x: 50, y: 65 } });
    const aufrufe = c.getContext("2d").drawImage.mock.calls;
    expect(aufrufe.length).toBe(2);                               // Basis + Unterschrift
    expect(aufrufe[1][0]).toBe(sig.img);
    const ohne = stempelErzeugen({ daten: {}, variante: "kasten", unterschrift: sig, einst: { an: false } });
    expect(ohne.getContext("2d").drawImage.mock.calls.length).toBe(1);
  });
});

describe("Unterschrift zuschneiden", () => {
  it("Tinte erkennen: dunkel und sichtbar", () => {
    expect(istTinte([0, 0, 0, 255], 0)).toBe(true);
    expect(istTinte([255, 255, 255, 255], 0)).toBe(false);        // weisser Hintergrund
    expect(istTinte([0, 0, 0, 0], 0)).toBe(false);                // durchsichtig
    expect(istTinte([17, 24, 39, 255], 0)).toBe(true);            // Strichfarbe des Feldes
  });
  it("schneidet auf die Tinte zu, macht den Rest durchsichtig; leer -> null", async () => {
    // 20x10-Bild, Tinte bei x 5..8 / y 3..4
    const w = 20, h = 10;
    const daten = new Uint8ClampedArray(w * h * 4).fill(255);
    for (let y = 3; y <= 4; y += 1) for (let x = 5; x <= 8; x += 1) { const i = (y * w + x) * 4; daten[i] = 0; daten[i + 1] = 0; daten[i + 2] = 0; }
    const laden = async (url) => ({ width: url === "quelle" ? w : 12, height: url === "quelle" ? h : 10, url });
    vi.spyOn(HTMLCanvasElement.prototype, "getContext").mockImplementation(function () {
      if (!this.__ctx) { this.__ctx = kontextAttrappe(this); if (this.width === w && this.height === h) this.__ctx._setzeDaten(daten); }
      return this.__ctx;
    });
    const erg = await unterschriftZuschneiden("quelle", laden);
    // Rand 8, aber nie ueber den Bildrand hinaus: x 0..16 -> 17 breit, y 0..9 -> 10 hoch
    expect(erg.w).toBe(17);
    expect(erg.h).toBe(10);
    expect(erg.dataUrl).toBe("data:image/png;base64,ZUSCHNITT");
    // Hintergrund durchsichtig gemacht (Alpha 0), Tinte behalten
    expect(daten[3]).toBe(0);
    expect(daten[(3 * w + 5) * 4 + 3]).toBe(255);
    expect(await unterschriftZuschneiden("", laden)).toBeNull();
    const leer = new Uint8ClampedArray(w * h * 4).fill(255);
    vi.spyOn(HTMLCanvasElement.prototype, "getContext").mockImplementation(function () {
      if (!this.__ctx) { this.__ctx = kontextAttrappe(this); this.__ctx._setzeDaten(leer); }
      return this.__ctx;
    });
    expect(await unterschriftZuschneiden("quelle", laden)).toBeNull();
  });
});
