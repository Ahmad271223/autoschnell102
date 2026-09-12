/*
 * Runde 27: Kein gespeicherter Oberflächen-Zustand darf beim Kontowechsel
 * im Browser liegen bleiben (Prüfbefund P0).
 */
import {
  einstellungLesen, einstellungSchreiben, VERGLEICH_PRAEFIX, vergleichKey,
  vergleichLaden, vergleichLeeren, vergleichSichern,
} from "./vergleichSpeicher";

function speicher(start = {}) {
  const daten = { ...start };
  return {
    daten,
    getItem: (k) => (k in daten ? daten[k] : null),
    setItem: (k, v) => { daten[k] = String(v); },
    removeItem: (k) => { delete daten[k]; },
    // wie echter Browserspeicher: length + key(i)
    get length() { return Object.keys(daten).length; },
    key: (i) => Object.keys(daten)[i] ?? null,
  };
}

const A = "sucher-a";
const B = "sucher-b";

describe("Vergleichsstand je Konto", () => {
  it("speichert und liest unter einem Schlüssel mit Konto-ID", () => {
    const s = speicher();
    vergleichSichern(s, A, { url: "x", result: { ad_id: "1" } });
    expect(Object.keys(s.daten)).toEqual([vergleichKey(A)]);
    expect(vergleichLaden(s, A).result.ad_id).toBe("1");
  });

  it("gibt einem anderen Konto NICHTS vom Kollegen", () => {
    const s = speicher();
    vergleichSichern(s, A, { url: "x", result: { ad_id: "1" } });
    expect(vergleichLaden(s, B)).toBe(null);
  });

  it("ignoriert den alten globalen Schlüssel", () => {
    const s = speicher({ [VERGLEICH_PRAEFIX]: JSON.stringify({ result: { ad_id: "alt" } }) });
    expect(vergleichLaden(s, A)).toBe(null);
  });

  it("leert beim Abmelden ALLE Stände, auch fremde und den alten Schlüssel", () => {
    const s = speicher({ [VERGLEICH_PRAEFIX]: "{}", sonstiges: "bleibt" });
    vergleichSichern(s, A, { url: "a" });
    vergleichSichern(s, B, { url: "b" });
    expect(vergleichLeeren(s)).toBe(3);
    expect(Object.keys(s.daten)).toEqual(["sonstiges"]);
  });

  it("verschluckt kaputte Daten und fehlenden Speicher", () => {
    const s = speicher({ [vergleichKey(A)]: "kein json" });
    expect(vergleichLaden(s, A)).toBe(null);
    expect(vergleichLaden(null, A)).toBe(null);
    expect(vergleichSichern(null, A, {})).toBe(false);
    expect(vergleichLeeren(null)).toBe(0);
    expect(vergleichSichern(s, "", {})).toBe(false);
  });
});

describe("Persönliche Schalter", () => {
  it("trennt die Einstellungen zweier Sucher am selben PC", () => {
    const s = speicher();
    einstellungSchreiben(s, "ah_portal_mobile", A, false);
    einstellungSchreiben(s, "ah_portal_mobile", B, true);
    expect(einstellungLesen(s, "ah_portal_mobile", A, true)).toBe(false);
    expect(einstellungLesen(s, "ah_portal_mobile", B, false)).toBe(true);
  });

  it("liefert den Standard, solange nichts gespeichert ist", () => {
    const s = speicher();
    expect(einstellungLesen(s, "ah_filter_automatisch", A, true)).toBe(true);
    expect(einstellungLesen(s, "ah_filter_automatisch", A, false)).toBe(false);
  });
});
