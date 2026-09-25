/**
 * KI Stufe 3 (26.09.2026): Rückfrage des Chefs mit Antwortknöpfen in der
 * Fahrer-App (Protokoll.jsx / protokollEntwurf.js).
 *  - strukturierte Frage (rueckfrage_frage) zeigt die Antwortmöglichkeiten
 *  - Tipp auf einen Knopf speichert die Antwort per Autosave (rueckfrage_antworten)
 *  - eine zweite Antwort ersetzt die erste (nie zwei Antworten auf dieselbe Frage)
 *  - ohne strukturierte Frage bleibt der bisherige Hinweistext
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
vi.mock("@/components/KiFahrerKarte", () => ({
  default: ({ apptId, preisVorschlag }) => createElement("div", { "data-testid": "protokoll-ki-oeffnen", "data-appt": apptId, "data-vorschlag": String(preisVorschlag) }),
}));

const { default: Protokoll } = await import("./Protokoll");
const { LEERER_ENTWURF, entwurfAusServer } = await import("./protokollEntwurf");

const FRAGE = { source_id: "d1", question: "Ist der Lack beschädigt?", options: ["Ja", "Nein", "Unklar"] };

function antwort(status = "entwurf", protokoll = {}) {
  return {
    data: {
      protocol: {
        id: "p1", version: 1, status, revision: 1, freigabe_stand: "s1",
        neuer_preis: null, preis_notiz: "", rueckfrage: "Bitte prüfen: Ist der Lack beschädigt?",
        ...protokoll },
      template: {}, vehicle: {}, damages: [], preis_vertrag: 10000,
      appointment: { seller_name: "Vera Termin", status: "offen" },
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
async function starten(daten) {
  api.get.mockResolvedValue(daten);
  behaelter = document.createElement("div");
  document.body.appendChild(behaelter);
  wurzel = createRoot(behaelter);
  await act(async () => { wurzel.render(createElement(Protokoll)); });
  await warten();
}
const putAufrufe = () => api.put.mock.calls.map(([, body]) => body);
async function klick(testId) {
  const k = el(testId);
  if (!k) throw new Error(`nicht gefunden: ${testId}`);
  await act(async () => { k.click(); });
}

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

describe("protokollEntwurf: Antworten auf Rückfragen", () => {
  it("gehören zum leeren Entwurf und kommen vom Server", () => {
    expect(LEERER_ENTWURF.rueckfrage_antworten).toEqual([]);
    expect(entwurfAusServer({ rueckfrage_antworten: [{ answer: "Ja" }] }).rueckfrage_antworten).toEqual([{ answer: "Ja" }]);
    expect(entwurfAusServer({ rueckfrage_antworten: "kaputt" }).rueckfrage_antworten).toEqual([]);
  });
});

describe("Protokoll.jsx: Rückfrage mit Antwortknöpfen", () => {
  it("zeigt die Frage mit Knöpfen und speichert die Antwort per Autosave", async () => {
    await starten(antwort("entwurf", { rueckfrage_frage: FRAGE }));
    expect(el("protokoll-rueckfrage")).toBeTruthy();
    expect(el("protokoll-rueckfrage-frage").textContent).toContain("Ist der Lack beschädigt?");
    for (const o of FRAGE.options) expect(el(`protokoll-rueckfrage-antwort-${o}`)).toBeTruthy();
    expect(el("protokoll-rueckfrage-gespeichert")).toBeNull();
    vi.useFakeTimers({ toFake: ["setTimeout", "clearTimeout"] });
    await klick("protokoll-rueckfrage-antwort-Nein");
    expect(el("protokoll-rueckfrage-gespeichert").textContent).toContain("Nein");
    // zweite Antwort ersetzt die erste
    await klick("protokoll-rueckfrage-antwort-Unklar");
    expect(el("protokoll-rueckfrage-gespeichert").textContent).toContain("Unklar");
    await act(async () => { await vi.advanceTimersByTimeAsync(1300); });
    expect(api.put).toHaveBeenCalled();
    const letzte = putAufrufe()[putAufrufe().length - 1];
    expect(letzte.rueckfrage_antworten).toHaveLength(1);
    expect(letzte.rueckfrage_antworten[0]).toMatchObject({ source_id: "d1", question: FRAGE.question, answer: "Unklar" });
    expect(typeof letzte.rueckfrage_antworten[0].at).toBe("string");
  });

  it("zeigt eine gespeicherte Antwort beim Wiederöffnen und ohne Frage nur den Text", async () => {
    await starten(antwort("entwurf", {
      rueckfrage_frage: FRAGE,
      rueckfrage_antworten: [{ source_id: "d1", question: FRAGE.question, answer: "Ja", at: "2026-09-26T10:00:00Z" }],
    }));
    expect(el("protokoll-rueckfrage-gespeichert").textContent).toContain("Ja");
    await act(async () => { wurzel.unmount(); });
    wurzel = null; behaelter?.remove();
    await starten(antwort("entwurf", {}));
    expect(el("protokoll-rueckfrage")).toBeTruthy();
    expect(el("protokoll-rueckfrage-frage")).toBeNull();
  });

  it("ab 'zur Freigabe' keine Rückfrage-Knöpfe mehr", async () => {
    await starten(antwort("zur_freigabe", { rueckfrage_frage: FRAGE }));
    expect(el("protokoll-rueckfrage-frage")).toBeNull();
  });

  it("KI-Auswertung erst ab 'zur Freigabe' (Wunsch Ahmad 25.09.2026 abends)", async () => {
    await starten(antwort("entwurf", {}));
    expect(el("protokoll-ki-oeffnen")).toBeNull();
    await act(async () => { wurzel.unmount(); });
    wurzel = null; behaelter?.remove();
    await starten(antwort("zur_freigabe", { preis_vorschlag: 7300 }));
    const k = el("protokoll-ki-oeffnen");
    expect(k).toBeTruthy();
    expect(k.dataset.appt).toBe("t1");
    expect(k.dataset.vorschlag).toBe("7300");
    await act(async () => { wurzel.unmount(); });
    wurzel = null; behaelter?.remove();
    await starten(antwort("freigegeben", {}));
    expect(el("protokoll-ki-oeffnen")).toBeTruthy();
  });
});
