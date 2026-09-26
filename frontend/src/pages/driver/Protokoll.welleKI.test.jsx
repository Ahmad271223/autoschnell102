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

// Review 26.09.2026 (Nr. 57): der Server vergibt je Frage eine frage_id.
const FRAGE = { frage_id: "f1", source_id: "d1", question: "Ist der Lack beschädigt?", options: ["Ja", "Nein", "Unklar"] };

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
    expect(letzte.rueckfrage_antworten[0]).toMatchObject({ frage_id: "f1", source_id: "d1", question: FRAGE.question, answer: "Unklar" });
    // Review 26.09.2026 (Nr. 134): den Zeitstempel setzt der Server, nicht die App
    expect(letzte.rueckfrage_antworten[0].at).toBeUndefined();
    // Nr. 101-105: "davon vereinbart" ist kein Feld mehr, das die App schickt
    expect("keys_expected" in letzte).toBe(false);
  });

  it("Review 26.09.2026 (Nr. 125): ohne Optionen keine erfundenen Knöpfe, sondern Freitext", async () => {
    await starten(antwort("entwurf", { rueckfrage_frage: { frage_id: "f2", question: "Was genau?", options: [], freitext: true } }));
    for (const o of ["Ja", "Nein", "Unklar"]) expect(el(`protokoll-rueckfrage-antwort-${o}`)).toBeNull();
    const feld = el("protokoll-rueckfrage-freitext");
    expect(feld).toBeTruthy();
    vi.useFakeTimers({ toFake: ["setTimeout", "clearTimeout"] });
    const setter = Object.getOwnPropertyDescriptor(window.HTMLInputElement.prototype, "value").set;
    await act(async () => {
      setter.call(feld, "Kratzer bis aufs Blech");
      feld.dispatchEvent(new Event("input", { bubbles: true }));
    });
    expect(el("protokoll-rueckfrage-gespeichert").textContent).toContain("Kratzer bis aufs Blech");
    await act(async () => { await vi.advanceTimersByTimeAsync(1300); });
    const letzte = putAufrufe()[putAufrufe().length - 1];
    expect(letzte.rueckfrage_antworten).toEqual([{ frage_id: "f2", source_id: "", question: "Was genau?", answer: "Kratzer bis aufs Blech" }]);
  });

  it("Review 26.09.2026 (Nr. 60-62): Zuordnung über frage_id; Verlauf komplett aufklappbar", async () => {
    const verlauf = Array.from({ length: 12 }, (_, i) => ({
      frage: { frage_id: `alt${i}`, question: `Frage ${i}?` }, antworten: [{ answer: i % 2 ? "Ja" : "Nein" }] }));
    await starten(antwort("entwurf", {
      rueckfrage_frage: FRAGE,
      // Antwort auf eine ALTE Frage mit gleichem Text zählt nicht als Antwort auf f1
      rueckfrage_antworten: [{ frage_id: "alt0", source_id: "d1", question: FRAGE.question, answer: "Ja" }],
      rueckfrage_verlauf: verlauf,
    }));
    expect(el("protokoll-rueckfrage-gespeichert")).toBeNull();
    const v = el("protokoll-rueckfrage-verlauf");
    expect(v.textContent).toContain("Frühere Rückfragen des Händlers (12)");
    expect(v.querySelectorAll("li")).toHaveLength(12);
    expect(v.textContent).toContain("Frage 11?");
  });

  it("Review 26.09.2026 (Nr. 59): 'Zur Freigabe schicken' verlangt die Antwort", async () => {
    await starten(antwort("entwurf", { rueckfrage_frage: FRAGE }));
    const knopf = [...behaelter.querySelectorAll("button")].find((b) => /Zur Freigabe/.test(b.textContent));
    expect(knopf).toBeTruthy();
    await act(async () => { knopf.click(); });
    expect(toastMock.error).toHaveBeenCalledWith(expect.stringContaining("Rückfrage des Chefs beantworten"));
    expect(api.post).not.toHaveBeenCalled();
  });

  it("Review 26.09.2026 (Nr. 101-105): Schlüssel 'vereinbart' nur Anzeige vom Server", async () => {
    await starten({ data: { ...antwort("entwurf", { keys_expected: 3, keys_count: 0 }).data,
                            template: { keys_expected: 3 } } });
    expect(el("protokoll-schluessel-vereinbart").textContent).toBe("3");
    expect(behaelter.querySelector('input[placeholder="z.B. 2"]').value).toBe("0");
  });

  it("zeigt eine gespeicherte Antwort beim Wiederöffnen und ohne Frage nur den Text", async () => {
    await starten(antwort("entwurf", {
      rueckfrage_frage: FRAGE,
      rueckfrage_antworten: [{ frage_id: "f1", source_id: "d1", question: FRAGE.question, answer: "Ja", at: "2026-09-26T10:00:00Z" }],
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

  // Entscheidung Ahmad 26.09.2026 (3): Bezug der Frage — "zu Schaden: …" / "zu KI-Position: …"
  it("Entscheidung 26.09. (3): zeigt den Bezug der Frage; Kasten auch ohne Notiz des Chefs", async () => {
    await starten(antwort("entwurf", {
      rueckfrage: "",
      rueckfrage_frage: { ...FRAGE, source_label: "Schaden: Delle · Tür vorne links" },
    }));
    // ohne Notiz: der Kasten erscheint trotzdem (vorher nur bei Notiztext)
    expect(el("protokoll-rueckfrage")).toBeTruthy();
    expect(el("protokoll-rueckfrage").textContent).toContain("Der Händler hat eine Rückfrage:");
    expect(el("protokoll-rueckfrage-bezug").textContent).toBe("zu Schaden: Delle · Tür vorne links");
    expect(el("protokoll-rueckfrage-antwort-Ja")).toBeTruthy();
    await act(async () => { wurzel.unmount(); });
    wurzel = null; behaelter?.remove();
    // ältere Frage ohne source_label: Bezug aus den eigenen Schäden des Protokolls
    await starten(antwort("entwurf", {
      rueckfrage_frage: { frage_id: "f3", source_id: "s7", question: "Wie tief?", options: ["oberflächlich", "tief"] },
      new_damages: [{ id: "s7", type_label: "Kratzer", zone: "Motorhaube", view: "front", type_key: "kratzer",
                      severity_data: {} }],
      rueckfrage_verlauf: [{ frage: { frage_id: "f1", question: "Lack?", source_label: "KI-Position: Lack" },
                             antworten: [{ answer: "Ja" }] }],
    }));
    expect(el("protokoll-rueckfrage-bezug").textContent).toBe("zu Schaden: Kratzer · Motorhaube");
    expect(el("protokoll-rueckfrage-verlauf").textContent).toContain("Lack? (KI-Position: Lack) Ja");
    await act(async () => { wurzel.unmount(); });
    wurzel = null; behaelter?.remove();
    // allgemeine Frage: kein Bezug; KI-Position ohne Titel: "KI-Position"
    await starten(antwort("entwurf", { rueckfrage_frage: { ...FRAGE, source_id: "" } }));
    expect(el("protokoll-rueckfrage-bezug")).toBeNull();
    await act(async () => { wurzel.unmount(); });
    wurzel = null; behaelter?.remove();
    await starten(antwort("entwurf", { rueckfrage_frage: { ...FRAGE, source_id: "dev:keys" } }));
    expect(el("protokoll-rueckfrage-bezug").textContent).toBe("zu KI-Position");
  });

  // Entscheidung Ahmad 26.09.2026 (2): Schlüsselanzahl fehlt im Vertrag -> kein Blockieren
  it("Entscheidung 26.09. (2): 'nicht im Vertrag hinterlegt' statt Strich, Fahrer trägt nur die erhaltene Anzahl ein", async () => {
    await starten({ data: { ...antwort("entwurf", { keys_count: 1 }).data,
                            template: { keys_expected: null, schluessel_vereinbart_fehlt: true } } });
    expect(el("protokoll-schluessel-vereinbart").textContent).toBe("nicht im Vertrag hinterlegt");
    expect(el("protokoll-schluessel-hinweis").textContent).toContain("trag nur ein, wie viele du erhalten hast");
    expect(behaelter.querySelector('input[placeholder="z.B. 2"]').value).toBe("1");
    await act(async () => { wurzel.unmount(); });
    wurzel = null; behaelter?.remove();
    // älteres Backend ohne Merker: leerer Wert heißt ebenfalls "nicht hinterlegt"
    await starten({ data: { ...antwort("entwurf", {}).data, template: {} } });
    expect(el("protokoll-schluessel-vereinbart").textContent).toBe("nicht im Vertrag hinterlegt");
    await act(async () => { wurzel.unmount(); });
    wurzel = null; behaelter?.remove();
    // mit Vertragswert: Zahl, kein Hinweis
    await starten({ data: { ...antwort("entwurf", { keys_expected: 2 }).data,
                            template: { keys_expected: 2, schluessel_vereinbart_fehlt: false } } });
    expect(el("protokoll-schluessel-vereinbart").textContent).toBe("2");
    expect(el("protokoll-schluessel-hinweis")).toBeNull();
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
