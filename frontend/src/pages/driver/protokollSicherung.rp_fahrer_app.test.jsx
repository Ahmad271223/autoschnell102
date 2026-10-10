/**
 * Rollenprüfung 22.09.2026, Welle 2 — reine Hilfen der Fahrer-App.
 *  RP-071/RP-170  protokollZustand fail-closed (unbekannter Status gesperrt).
 *  RP-546         Sicherung im Tab (Lesen/Schreiben/Ablauf, Kennung).
 *  RP-062/RP-161  409 "Bitte zuerst die Fahrt annehmen" erkennen.
 *  RP-058         Verkäufername getrimmt, nur wenn übergeben.
 *  RP-068/RP-167  km-Stand aus dem unterschriebenen Protokoll.
 */
import { beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("@/context/DriverContext", () => ({ driverApi: { get: vi.fn() } }));

const { protokollZustand } = await import("@/lib/protokollZustand");
const {
  SICHERUNG_HOECHSTENS_MS, freigabeKennung, sicherungLesen, sicherungLoeschen, sicherungSchreiben,
} = await import("./protokollSicherung");
const { LEERER_ENTWURF, istAnnahmeFehlt, nutzlast } = await import("./protokollEntwurf");
const { protokollNachsehen } = await import("./fahrtPruefung");
const { driverApi } = await import("@/context/DriverContext");

beforeEach(() => {
  window.sessionStorage.clear();
  vi.clearAllMocks();
});

describe("protokollZustand fail-closed (RP-071)", () => {
  it("nur Entwurf (oder noch kein Protokoll) ist bearbeitbar", () => {
    for (const s of [undefined, null, "", "entwurf"]) {
      expect(protokollZustand(s)).toMatchObject({ gesperrt: false, nachladen: false, unbekannt: false });
    }
    const z = protokollZustand("verworfen");
    expect(z).toMatchObject({ gesperrt: true, nachladen: true, unbekannt: true,
                              unterschriften: false, isFinal: false });
    expect(protokollZustand("final")).toMatchObject({ gesperrt: true, nachladen: false, unbekannt: false });
  });
});

describe("Sicherung im Tab (RP-546)", () => {
  it("schreiben, lesen, löschen; zu alte Sicherung gilt nicht", () => {
    expect(sicherungLesen("t1")).toBeNull();
    sicherungSchreiben("t1", { f: { notes: "x" } }, 1000);
    expect(sicherungLesen("t1", 2000)).toMatchObject({ v: 1, f: { notes: "x" } });
    expect(sicherungLesen("t1", 1000 + SICHERUNG_HOECHSTENS_MS + 1)).toBeNull();
    expect(window.sessionStorage.getItem("ah_protokoll_sicherung_t1")).toBeNull();
    sicherungSchreiben("t2", { f: {} });
    sicherungLoeschen("t2");
    expect(sicherungLesen("t2")).toBeNull();
  });

  it("Speicher voll: wenigstens ohne Unterschriften", () => {
    const echt = Storage.prototype.setItem;
    const spion = vi.spyOn(Storage.prototype, "setItem").mockImplementation(function (k, v) {
      if (String(v).includes("RIESIG")) throw new Error("QuotaExceededError");
      return echt.call(this, k, v);
    });
    expect(sicherungSchreiben("t3", { f: { notes: "n" }, sigSeller: "RIESIG", sigDriver: null })).toBe(true);
    spion.mockRestore();
    expect(sicherungLesen("t3")).toMatchObject({ f: { notes: "n" }, sigSeller: null });
  });

  it("Kennung aus Freigabe-Stand, Preis und Vermerk", () => {
    expect(freigabeKennung({ freigabe_stand: "s1", neuer_preis: 9000, preis_notiz: "" })).toBe("s1|9000|");
    expect(freigabeKennung(null)).toBe("||");
  });
});

describe("Fahrt nicht angenommen (RP-062)", () => {
  it("nur die 409 der fehlenden Annahme", () => {
    expect(istAnnahmeFehlt(409, "Bitte zuerst die Fahrt annehmen — erst dann sind Protokoll "
      + "und Dokumente zugänglich.")).toBe(true);
    expect(istAnnahmeFehlt(409, "Bitte zuerst die Fahrt annehmen (oder ablehnen).")).toBe(true);
    expect(istAnnahmeFehlt(409, "Protokoll ist bereits abgeschlossen.")).toBe(false);
    expect(istAnnahmeFehlt(403, "Bitte zuerst die Fahrt annehmen")).toBe(false);
  });
});

describe("Verkäufername in der Nutzlast (RP-058)", () => {
  it("getrimmt, und nur wenn übergeben", () => {
    expect(nutzlast({ ...LEERER_ENTWURF }, "  Vera ").seller_name).toBe("Vera");
    expect("seller_name" in nutzlast({ ...LEERER_ENTWURF }, undefined)).toBe(false);
  });
});

describe("km aus dem unterschriebenen Protokoll (RP-068)", () => {
  it("nur bei final und lesbarem Wert", async () => {
    driverApi.get.mockResolvedValueOnce({ data: { protocol: { status: "final", condition: { mileage: "85.120" } } } });
    expect(await protokollNachsehen("t1")).toEqual({ final: true, kmStand: 85120 });
    driverApi.get.mockResolvedValueOnce({ data: { protocol: { status: "entwurf", condition: { mileage: "85120" } } } });
    expect(await protokollNachsehen("t1")).toEqual({ final: false, kmStand: null });
    driverApi.get.mockResolvedValueOnce({ data: { protocol: { status: "final", condition: { mileage: "viel" } } } });
    expect(await protokollNachsehen("t1")).toEqual({ final: true, kmStand: null });
    driverApi.get.mockRejectedValueOnce(new Error("Network Error"));
    expect(await protokollNachsehen("t1")).toEqual({ final: null, kmStand: null });
  });
});
