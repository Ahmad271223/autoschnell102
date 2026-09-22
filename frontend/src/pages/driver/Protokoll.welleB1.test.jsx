/**
 * Welle B1 (Entscheidung Ahmad 22.09.2026): Ausweisnummer des Verkäufers im
 * Abhol-Protokoll der Fahrer-App (Protokoll.jsx / protokollEntwurf.js).
 *  - Feld "protokoll-ausweis" neben dem Verkäufernamen, nur im Entwurf editierbar
 *  - Autosave wie der Name (getrimmt, höchstens 60 Zeichen)
 *  - beim Wiederöffnen steht der gespeicherte Wert im Feld
 *  - RP-157 Rest: der Verkäufername aus dem Entwurf steht beim Wiederöffnen im Feld
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
const { LEERER_ENTWURF, entwurfAusServer, nutzlast } = await import("./protokollEntwurf");

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
  const setter = Object.getOwnPropertyDescriptor(window.HTMLInputElement.prototype, "value").set;
  await act(async () => {
    setter.call(feld, wert);
    feld.dispatchEvent(new Event("input", { bubbles: true }));
  });
}
async function starten(daten) {
  api.get.mockResolvedValue(daten);
  behaelter = document.createElement("div");
  document.body.appendChild(behaelter);
  wurzel = createRoot(behaelter);
  await act(async () => { wurzel.render(createElement(Protokoll)); });
  await warten();
}
const putAufrufe = () => api.put.mock.calls.map(([, body]) => body);

beforeEach(() => {
  try { window.sessionStorage.clear(); } catch { /* egal */ }
  api.put.mockResolvedValue({ data: { revision: 2 } });
  api.post.mockResolvedValue({ data: { ok: true } });
});

afterEach(async () => {
  if (wurzel) await act(async () => { wurzel.unmount(); });
  wurzel = null;
  behaelter?.remove();
  vi.useRealTimers();
  vi.clearAllMocks();
});

describe("protokollEntwurf: Ausweisnummer", () => {
  it("gehört zum leeren Entwurf und kommt vom Server", () => {
    expect(LEERER_ENTWURF.seller_id_document).toBe("");
    expect(entwurfAusServer({ seller_id_document: "L01X2Y3Z4" }).seller_id_document).toBe("L01X2Y3Z4");
    expect(entwurfAusServer({}).seller_id_document).toBe("");
  });

  it("geht getrimmt und auf 60 Zeichen gekürzt in die Nutzlast", () => {
    expect(nutzlast({ ...LEERER_ENTWURF, seller_id_document: "  L01X2Y3Z4 " }).seller_id_document)
      .toBe("L01X2Y3Z4");
    expect(nutzlast({ ...LEERER_ENTWURF, seller_id_document: "x".repeat(70) }).seller_id_document)
      .toHaveLength(60);
    expect(nutzlast({ ...LEERER_ENTWURF }).seller_id_document).toBe("");
  });
});

describe("Protokoll.jsx: Ausweisnummer des Verkäufers", () => {
  it("getippte Nummer geht per Autosave mit (getrimmt)", async () => {
    await starten(antwort());
    expect(el("protokoll-ausweis")).toBeTruthy();
    expect(el("protokoll-ausweis").disabled).toBe(false);
    vi.useFakeTimers({ toFake: ["setTimeout", "clearTimeout"] });
    await tippen("protokoll-ausweis", "  L01X2Y3Z4 ");
    await act(async () => { await vi.advanceTimersByTimeAsync(1300); });
    expect(api.put).toHaveBeenCalledTimes(1);
    expect(putAufrufe()[0].seller_id_document).toBe("L01X2Y3Z4");
    // der nur aus dem Termin vorbelegte Name wird weiterhin NICHT mitgeschickt
    expect("seller_name" in putAufrufe()[0]).toBe(false);
  });

  it("beim Wiederöffnen stehen Nummer und Name aus dem Entwurf im Feld (RP-157)", async () => {
    await starten(antwort("entwurf", { seller_id_document: "L01X2Y3Z4", seller_name: "Vera Entwurf" }));
    expect(el("protokoll-ausweis").value).toBe("L01X2Y3Z4");
    expect(el("protokoll-verkaeufer").value).toBe("Vera Entwurf");
  });

  it("ab 'zur Freigabe' gesperrt, Wert wie im Protokoll", async () => {
    await starten(antwort("zur_freigabe", { seller_id_document: "L01X2Y3Z4" }));
    expect(el("protokoll-ausweis").disabled).toBe(true);
    expect(el("protokoll-ausweis").value).toBe("L01X2Y3Z4");
  });
});
