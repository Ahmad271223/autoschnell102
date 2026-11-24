/**
 * Überweisung mit GiroCode (Wunsch Ahmad 24.11.2026):
 *  - lib/iban: Gruppieren, Prüfziffer, Hinweis beim Tippen
 *  - Büro-Karte: Betrag offen -> "offen – Preis vor Ort noch nicht bestätigt", Knopf gesperrt
 *  - Büro-Karte: Betrag fest -> Bestätigung, POST schickt NUR den Stand (nie einen Betrag)
 *  - Fahrer: Bar -> nichts; überwiesen -> "Überwiesen am …, Betrag …"
 */
import { act, createElement } from "react";
import { createRoot } from "react-dom/client";
import { MemoryRouter } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

globalThis.IS_REACT_ACT_ENVIRONMENT = true;

const api = vi.hoisted(() => ({ get: vi.fn(), post: vi.fn(), delete: vi.fn() }));
const driverApi = vi.hoisted(() => ({ get: vi.fn(), put: vi.fn() }));
vi.mock("@/lib/api", () => ({ api, errMsg: (e, s) => e?.message || s }));
vi.mock("@/context/DriverContext", () => ({ driverApi }));
vi.mock("sonner", () => ({ toast: { success: vi.fn(), error: vi.fn(), info: vi.fn() } }));

const { ibanGruppiert, ibanHinweis, ibanNormal, ibanPruefzifferOk } = await import("@/lib/iban");
const { default: UeberweisungKarte, BETRAG_OFFEN, ueberweisungKurz } = await import("./UeberweisungKarte");
const { default: UeberweisungFahrer } = await import("./UeberweisungFahrer");

const OFFEN = {
  zahlungsart: { text: "Überweisung", ueberweisung: true, echtzeit: false }, kontodaten: true,
  empfaenger: "Jürgen Müller", iban: "DE89370400440532013000", iban_formatiert: "DE89 3704 0044 0532 0130 00",
  betrag_fest: false, betrag_cents: null, betrag_text: "", verwendungszweck: "Kaufpreis VW Golf FIN WVW1",
  warnungen: [], stand: "stand-offen-123456", ueberwiesen: null, darf_zuruecknehmen: false,
  girocode_svg: "<svg xmlns=\"http://www.w3.org/2000/svg\"></svg>",
};
const FEST = { ...OFFEN, betrag_fest: true, betrag_cents: 1420050, betrag_text: "14.200,50 €", stand: "stand-fest-987654" };

let wurzel;
let knoten;
beforeEach(() => {
  knoten = document.createElement("div");
  document.body.appendChild(knoten);
  wurzel = createRoot(knoten);
  vi.clearAllMocks();
});
afterEach(() => {
  act(() => wurzel.unmount());
  knoten.remove();
});

async function zeigen(el) {
  await act(async () => { wurzel.render(createElement(MemoryRouter, null, el)); });
  await act(async () => { await Promise.resolve(); });
}
const q = (id) => knoten.querySelector(`[data-testid="${id}"]`);

describe("lib/iban", () => {
  it("normalisiert, gruppiert und prüft", () => {
    expect(ibanNormal(" de89 3704-0044 ")).toBe("DE8937040044");
    expect(ibanGruppiert("DE89370400440532013000")).toBe("DE89 3704 0044 0532 0130 00");
    expect(ibanPruefzifferOk("DE89 3704 0044 0532 0130 00")).toBe(true);
    expect(ibanPruefzifferOk("DE89370400440532013001")).toBe(false);
    expect(ibanHinweis("DE89370400440532013001")).toMatch(/stimmt nicht/);
    expect(ibanHinweis("DE8937")).toBe("");
    expect(ibanHinweis("12")).toMatch(/zwei Buchstaben/);
  });
});

describe("UeberweisungKarte (Büro)", () => {
  it("Betrag offen: Hinweis, QR ohne Betrag, Knopf gesperrt", async () => {
    api.get.mockResolvedValue({ data: OFFEN });
    await zeigen(createElement(UeberweisungKarte, { terminId: "t1" }));
    expect(api.get).toHaveBeenCalledWith("/appointments/t1/ueberweisung");
    expect(q("ueberweisung-betrag").textContent).toBe(BETRAG_OFFEN);
    expect(q("ueberweisung-girocode").textContent).toMatch(/Ohne Betrag/);
    expect(q("ueberweisung-markieren").disabled).toBe(true);
    expect(q("ueberweisung-iban").textContent).toBe("DE89 3704 0044 0532 0130 00");
  });

  it("Betrag fest: erst Rückfrage, dann POST nur mit dem Stand", async () => {
    api.get.mockResolvedValue({ data: FEST });
    api.post.mockResolvedValue({ data: { ...FEST, ueberwiesen: { am: "2026-11-24T10:00:00+00:00",
      betrag_cents: 1420050, betrag_text: "14.200,50 €", von_name: "Carla Chef" } } });
    await zeigen(createElement(UeberweisungKarte, { terminId: "t1" }));
    expect(q("ueberweisung-betrag").textContent).toBe("14.200,50 €");
    await act(async () => { q("ueberweisung-markieren").click(); });
    expect(api.post).not.toHaveBeenCalled();
    expect(q("ueberweisung-frage").textContent).toMatch(/14\.200,50 € an Jürgen Müller/);
    await act(async () => { q("ueberweisung-frage-ja").click(); });
    expect(api.post).toHaveBeenCalledWith("/appointments/t1/ueberweisung/ueberwiesen", { stand: "stand-fest-987654" });
    expect(q("ueberweisung-ueberwiesen").textContent).toMatch(/Überwiesen am 24\.11\.2026.*14\.200,50 €/);
  });

  it("Warnungen und Barzahlung", async () => {
    api.get.mockResolvedValue({ data: { ...FEST, warnungen: [{ art: "name", text: "Kontoinhaber weicht ab" }] } });
    await zeigen(createElement(UeberweisungKarte, { terminId: "t1" }));
    expect(q("ueberweisung-warnung-name").textContent).toMatch(/weicht ab/);
    api.get.mockResolvedValue({ data: { ...OFFEN, zahlungsart: { ueberweisung: false } } });
    await zeigen(createElement(UeberweisungKarte, { terminId: "t2" }));
    expect(q("ueberweisung-bar")).not.toBeNull();
  });

  it("Kurzstand für die Akte", () => {
    expect(ueberweisungKurz(OFFEN)).toMatch(/Betrag offen – Preis vor Ort/);
    expect(ueberweisungKurz(FEST)).toBe("Jetzt überweisen: 14.200,50 €");
    expect(ueberweisungKurz({ ...FEST, kontodaten: false })).toMatch(/Kontodaten fehlen/);
  });
});

describe("UeberweisungFahrer", () => {
  it("bei Barzahlung nichts", async () => {
    await zeigen(createElement(UeberweisungFahrer, { terminId: "t1",
      start: { zahlungsart: { ueberweisung: false }, ueberwiesen: null } }));
    expect(q("ueberweisung-fahrer")).toBeNull();
    // ohne Startwert (älterer Server): nichts anzeigen, nichts abfragen
    await zeigen(createElement(UeberweisungFahrer, { terminId: "t1" }));
    expect(q("ueberweisung-fahrer")).toBeNull();
    expect(driverApi.get).not.toHaveBeenCalled();
  });

  it("erfasst Kontodaten (normalisierte IBAN) und zeigt den Überwiesen-Stand", async () => {
    const leer = { zahlungsart: { ueberweisung: true }, ueberwiesen: null, kontoinhaber: "", iban_formatiert: "" };
    driverApi.put.mockResolvedValue({ data: { zahlungsart: { ueberweisung: true }, ueberwiesen: null,
      kontoinhaber: "Max Muster", iban_formatiert: "DE89 3704 0044 0532 0130 00", erfasst_am: "2026-11-24T10:00:00Z" } });
    await zeigen(createElement(UeberweisungFahrer, { terminId: "t1", start: leer }));
    const setze = (el, wert) => {
      const setter = Object.getOwnPropertyDescriptor(window.HTMLInputElement.prototype, "value").set;
      setter.call(el, wert);
      el.dispatchEvent(new Event("input", { bubbles: true }));
    };
    await act(async () => { setze(q("ueberweisung-fahrer-inhaber"), "Max Muster"); });
    await act(async () => { setze(q("ueberweisung-fahrer-iban"), "de89 3704 0044 0532 0130 00"); });
    expect(q("ueberweisung-fahrer-iban").value).toBe("DE89 3704 0044 0532 0130 00");
    await act(async () => { q("ueberweisung-fahrer-speichern").click(); });
    expect(driverApi.put).toHaveBeenCalledWith("/driver/appointments/t1/ueberweisung",
      { iban: "DE89370400440532013000", kontoinhaber: "Max Muster" });
    expect(q("ueberweisung-fahrer-konto").textContent).toMatch(/DE89 3704/);

    await zeigen(createElement(UeberweisungFahrer, { terminId: "t9", start: { zahlungsart: { ueberweisung: true },
      kontoinhaber: "Max Muster", iban_formatiert: "DE89 3704 0044 0532 0130 00",
      ueberwiesen: { am: "2026-11-24T10:00:00+00:00", betrag_cents: 1500000, betrag_text: "15.000,00 €" } } }));
    expect(q("ueberweisung-fahrer-ueberwiesen").textContent).toMatch(/Überwiesen.*24\.11\.2026.*15\.000,00 €/);
    expect(q("ueberweisung-fahrer-aendern")).toBeNull();
  });
});
