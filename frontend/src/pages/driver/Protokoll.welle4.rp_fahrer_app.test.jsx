/**
 * Rollenprüfung 22.09.2026 (Review, Welle 4) — Abhol-Protokoll der Fahrer-App.
 *  - Nach einem zusammengeführten Revisionskonflikt stand die Seite dauerhaft
 *    auf "wird gespeichert …" (Vergleich per Objektidentität). Jetzt nach Inhalt.
 *  - Name aus dem Entwurf weicht vom Namen am Termin ab (Chef hat korrigiert,
 *    nachdem die Korrektur-Version angelegt war): Hinweis + "Übernehmen".
 */
import { act, createElement } from "react";
import { createRoot } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

globalThis.IS_REACT_ACT_ENVIRONMENT = true;

const api = vi.hoisted(() => ({ get: vi.fn(), post: vi.fn(), put: vi.fn() }));
const toastMock = vi.hoisted(() => ({ error: vi.fn(), success: vi.fn(), warning: vi.fn(), info: vi.fn() }));

vi.mock("@/context/DriverContext", () => ({ driverApi: api, openDriverPdf: vi.fn() }));
vi.mock("sonner", () => ({ toast: toastMock }));
vi.mock("react-router-dom", () => ({ useParams: () => ({ id: "t1" }), useNavigate: () => vi.fn() }));
vi.mock("@/lib/ungespeichert", () => ({ useUngespeichert: () => {} }));
vi.mock("@/components/DamageSelector", () => ({ default: () => null }));
vi.mock("@/components/MonatJahrEingabe", () => ({ default: () => null }));
vi.mock("@/components/SignaturePad", () => ({ default: () => null }));

const { default: Protokoll } = await import("./Protokoll");
const { nutzlastText } = await import("./protokollEntwurf");

function antwort(status = "entwurf", protokoll = {}, termin = {}) {
  return {
    data: {
      protocol: {
        id: "p1", version: 1, status, revision: 1, freigabe_stand: "s1",
        neuer_preis: null, preis_notiz: "", rueckfrage: "", ...protokoll },
      template: {}, vehicle: {}, damages: [], preis_vertrag: 10000,
      appointment: { seller_name: "Vera Termin", status: "offen", ...termin },
    },
  };
}

let wurzel;
let behaelter;

async function warten() {
  await act(async () => { await new Promise((r) => setTimeout(r, 0)); });
  await act(async () => { await new Promise((r) => setTimeout(r, 0)); });
}
const el = (testId) => behaelter.querySelector(`[data-testid="${testId}"]`);
async function tippen(testId, wert) {
  const feld = el(testId);
  if (!feld) throw new Error(`nicht gefunden: ${testId}`);
  const proto = feld.tagName === "TEXTAREA" ? window.HTMLTextAreaElement.prototype
    : window.HTMLInputElement.prototype;
  const setter = Object.getOwnPropertyDescriptor(proto, "value").set;
  await act(async () => {
    setter.call(feld, wert);
    feld.dispatchEvent(new Event("input", { bubbles: true }));
  });
}
async function starten(...antworten) {
  const liste = [...antworten];
  api.get.mockImplementation(async () => (liste.length > 1 ? liste.shift() : liste[0]));
  behaelter = document.createElement("div");
  document.body.appendChild(behaelter);
  wurzel = createRoot(behaelter);
  await act(async () => { wurzel.render(createElement(Protokoll)); });
  await warten();
}
const KONFLIKT = { response: { status: 409, data: {
  detail: "Der Entwurf wurde inzwischen in einem anderen Tab gespeichert — bitte neu laden." } } };

beforeEach(() => {
  try { window.sessionStorage.clear(); } catch { /* egal */ }
  vi.spyOn(window, "confirm").mockReturnValue(true);
  api.put.mockResolvedValue({ data: { revision: 2 } });
});

afterEach(async () => {
  if (wurzel) await act(async () => { wurzel.unmount(); });
  wurzel = null;
  behaelter?.remove();
  vi.useRealTimers();
  vi.clearAllMocks();
  vi.restoreAllMocks();
});

describe("Revisionskonflikt zusammengeführt -> danach 'gespeichert'", () => {
  it("Anzeige springt nach dem erfolgreichen zweiten PUT auf 'gespeichert'", async () => {
    await starten(antwort(), antwort("entwurf", { revision: 3 }));
    vi.useFakeTimers({ toFake: ["setTimeout", "clearTimeout"] });
    let loesen;
    api.put.mockRejectedValueOnce(KONFLIKT)
      .mockImplementationOnce(() => new Promise((r) => { loesen = r; }));
    await tippen("protokoll-bemerkungen", "Kratzer hinten");
    // Autosave: 409 -> Serverstand holen -> zusammenführen -> zweiter PUT (hängt)
    await act(async () => { await vi.advanceTimersByTimeAsync(1300); });
    expect(api.put).toHaveBeenCalledTimes(2);
    expect(api.put.mock.calls[1][1]).toMatchObject({ notes: "Kratzer hinten", revision: 3 });
    // React rendert WÄHREND des PUT (fRef zeigt dann auf ein neues Objekt)
    await act(async () => { await vi.advanceTimersByTimeAsync(50); });
    await act(async () => { loesen({ data: { revision: 4 } }); await vi.advanceTimersByTimeAsync(50); });
    expect(el("protokoll-gespeichert")?.textContent || "").toMatch(/^gespeichert /);
    // kein weiterer PUT ohne Änderung
    await act(async () => { await vi.advanceTimersByTimeAsync(5000); });
    expect(api.put).toHaveBeenCalledTimes(2);
  });

  it("nutzlastText ist unabhängig von der Reihenfolge der Felder", () => {
    expect(nutzlastText({ a: 1, b: { y: 2, x: [1, { q: 1, p: 2 }] } }))
      .toBe(nutzlastText({ b: { x: [1, { p: 2, q: 1 }], y: 2 }, a: 1 }));
    expect(nutzlastText({ a: 1 })).not.toBe(nutzlastText({ a: 2 }));
  });
});

describe("Name aus dem Entwurf vs. Name am Termin", () => {
  it("abweichend: Hinweis, 'Übernehmen' setzt den Termin-Namen und speichert ihn", async () => {
    await starten(antwort("entwurf", { seller_name: "Müller" }, { seller_name: "Meyer" }));
    expect(el("protokoll-verkaeufer").value).toBe("Müller");
    expect(el("protokoll-name-termin").textContent).toContain("„Meyer“");
    vi.useFakeTimers({ toFake: ["setTimeout", "clearTimeout"] });
    await act(async () => { el("protokoll-name-termin-uebernehmen").click(); });
    expect(el("protokoll-verkaeufer").value).toBe("Meyer");
    expect(el("protokoll-name-termin")).toBeNull();
    await act(async () => { await vi.advanceTimersByTimeAsync(1300); });
    expect(api.put).toHaveBeenCalledTimes(1);
    expect(api.put.mock.calls[0][1].seller_name).toBe("Meyer");
  });

  it("kein Hinweis: gleicher Name, nur Termin-Name, oder beim Chef gesperrt", async () => {
    await starten(antwort("entwurf", { seller_name: "Meyer" }, { seller_name: "Meyer" }));
    expect(el("protokoll-name-termin")).toBeNull();
    await act(async () => { wurzel.unmount(); });
    behaelter.remove();
    await starten(antwort("entwurf", {}, { seller_name: "Meyer" }));
    expect(el("protokoll-verkaeufer").value).toBe("Meyer");
    expect(el("protokoll-name-termin")).toBeNull();
    await act(async () => { wurzel.unmount(); });
    behaelter.remove();
    await starten(antwort("zur_freigabe", { seller_name: "Müller" }, { seller_name: "Meyer" }));
    expect(el("protokoll-name-termin")).toBeNull();
  });

  it("selbst getippter Name: kein Hinweis", async () => {
    await starten(antwort("entwurf", { seller_name: "Müller" }, { seller_name: "Meyer" }));
    await tippen("protokoll-verkaeufer", "Müller-Lüdenscheidt");
    expect(el("protokoll-name-termin")).toBeNull();
  });
});
