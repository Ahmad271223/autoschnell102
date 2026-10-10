/**
 * Rollenprüfung 22.09.2026 — Abhol-Protokoll der Fahrer-App (Protokoll.jsx).
 *  RP-059/RP-158  Ort und Verkäufername ab "zur Freigabe" gesperrt, Anzeige = PDF-Wert.
 *  RP-060/RP-159  "15.000" als Preisvorschlag = 15.000 €, Unlesbares blockiert das Abschicken.
 *  RP-061/RP-160  Speichern nacheinander (Revision der vorigen Antwort); Konflikt ->
 *                 zusammenführen statt Überschreiben.
 *  RP-065/RP-164  Speicherfehler sichtbar, Netzfehler automatisch erneut.
 *  RP-067/RP-166  Abschnitt 5 als Ja/Nein, unbeantwortet wird nichts geschickt.
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
vi.mock("@/components/SignaturePad", async () => {
  const { createElement: h } = await import("react");
  return {
    default: ({ label, onChange }) => h("button", {
      type: "button", "data-testid": `sig-${label}`,
      onClick: () => onChange(`data:image/png;base64,${label.replace(/\W/g, "")}`),
    }, label),
  };
});

const { default: Protokoll } = await import("./Protokoll");

function antwort(status = "entwurf", protokoll = {}) {
  return {
    data: {
      protocol: { id: "p1", version: 1, status, revision: 1, freigabe_stand: "s1",
                  neuer_preis: null, preis_notiz: "", rueckfrage: "", ...protokoll },
      template: {}, vehicle: {}, appointment: { seller_name: "Vera Termin" }, damages: [],
      preis_vertrag: 10000,
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

async function klick(testId) {
  const knopf = el(testId);
  if (!knopf) throw new Error(`nicht gefunden: ${testId}`);
  await act(async () => { knopf.click(); });
  await warten();
}

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

const putAufrufe = () => api.put.mock.calls.map(([, body]) => body);

beforeEach(() => {
  // Rollenprüfung 22.09.2026 (RP-546): die Seite sichert offene Stände im Tab
  // (sessionStorage) — jeder Test beginnt ohne Sicherung.
  try { window.sessionStorage.clear(); } catch { /* egal */ }
  vi.spyOn(window, "confirm").mockReturnValue(true);
  api.put.mockResolvedValue({ data: { revision: 2 } });
  api.post.mockResolvedValue({ data: { ok: true } });
});

afterEach(async () => {
  await act(async () => { wurzel.unmount(); });
  behaelter.remove();
  vi.useRealTimers();
  vi.clearAllMocks();
  vi.restoreAllMocks();
});

describe("RP-061: Speichern nacheinander, Konflikt zusammenführen", () => {
  it("zweiter Autosave wartet auf den ersten und trägt dessen Revision", async () => {
    await starten(antwort());
    vi.useFakeTimers({ toFake: ["setTimeout", "clearTimeout"] });
    let ersterFertig;
    api.put.mockImplementationOnce(() => new Promise((r) => { ersterFertig = r; }));
    await tippen("protokoll-bemerkungen", "eins");
    await act(async () => { await vi.advanceTimersByTimeAsync(1300); });
    expect(api.put).toHaveBeenCalledTimes(1);
    expect(putAufrufe()[0]).toMatchObject({ notes: "eins", revision: 1 });

    await tippen("protokoll-bemerkungen", "zwei");
    await act(async () => { await vi.advanceTimersByTimeAsync(1300); });
    expect(api.put).toHaveBeenCalledTimes(1);          // wartet, kein paralleler PUT

    api.put.mockResolvedValueOnce({ data: { revision: 3 } });
    await act(async () => { ersterFertig({ data: { revision: 2 } }); });
    await act(async () => { await vi.advanceTimersByTimeAsync(0); });
    expect(api.put).toHaveBeenCalledTimes(2);
    expect(putAufrufe()[1]).toMatchObject({ notes: "zwei", revision: 2 });
    expect(el("protokoll-bemerkungen").value).toBe("zwei");
  });

  it("Revisionskonflikt: Serverstand holen, lokale Eingabe darüberlegen, neu speichern", async () => {
    await starten(antwort("entwurf", { revision: 1, notes: "", documents: {} }));
    await tippen("protokoll-bemerkungen", "lokal getippt");
    api.put.mockRejectedValueOnce({
      response: { status: 409, data: { detail: "Der Entwurf wurde inzwischen in einem anderen Tab "
        + "oder auf einem anderen Gerät gespeichert — bitte neu laden." } },
    });
    api.get.mockResolvedValueOnce(antwort("entwurf", {
      revision: 5, notes: "", documents: { Fahrzeugbrief: true } }));
    api.put.mockResolvedValueOnce({ data: { revision: 6 } });
    await klick("protokoll-speichern");
    expect(api.put).toHaveBeenCalledTimes(2);
    expect(putAufrufe()[1]).toMatchObject({
      revision: 5, notes: "lokal getippt", documents: { Fahrzeugbrief: true } });
    expect(el("protokoll-bemerkungen").value).toBe("lokal getippt");
    expect(toastMock.warning).toHaveBeenCalledWith(expect.stringContaining("zusammengeführt"));
    expect(toastMock.success).toHaveBeenCalledWith("Zwischenstand gespeichert");
  });
});

describe("RP-065: Speicherfehler sichtbar", () => {
  it("Serverablehnung zeigt Grund und 'Erneut speichern'", async () => {
    await starten(antwort());
    api.put.mockRejectedValueOnce({ response: { status: 403,
      data: { detail: "Die Firma ist gesperrt — bitte den Administrator kontaktieren." } } });
    await tippen("protokoll-bemerkungen", "x");
    await klick("protokoll-speichern");
    expect(el("protokoll-nicht-gespeichert").textContent).toContain("Die Firma ist gesperrt");
    expect(el("protokoll-erneut-speichern")).not.toBeNull();
    await klick("protokoll-erneut-speichern");
    expect(el("protokoll-nicht-gespeichert")).toBeNull();
  });

  it("Netzfehler beim Autosave: Hinweis, nach der Pause selbst erneut", async () => {
    await starten(antwort());
    vi.useFakeTimers({ toFake: ["setTimeout", "clearTimeout"] });
    api.put.mockRejectedValueOnce(Object.assign(new Error("Network Error"), { code: "ERR_NETWORK" }));
    await tippen("protokoll-bemerkungen", "im Funkloch");
    await act(async () => { await vi.advanceTimersByTimeAsync(1300); });
    expect(el("protokoll-nicht-gespeichert").textContent).toContain("automatisch");
    expect(el("protokoll-gespeichert")).toBeNull();
    expect(toastMock.error).not.toHaveBeenCalled();       // kein Toast-Regen im Funkloch
    await act(async () => { await vi.advanceTimersByTimeAsync(10500); });
    expect(api.put).toHaveBeenCalledTimes(2);
    expect(putAufrufe()[1]).toMatchObject({ notes: "im Funkloch" });
    expect(el("protokoll-nicht-gespeichert")).toBeNull();
    expect(el("protokoll-gespeichert")).not.toBeNull();
  });
});

describe("RP-060/RP-067: Preisvorschlag und Abschnitt 5", () => {
  it("Abschnitt 5 unbeantwortet -> nicht gesendet; Ja -> true; '15.000' -> 15000", async () => {
    await starten(antwort());
    expect(el("protokoll-schaeden-bestaetigt-ja")).not.toBeNull();
    await klick("protokoll-speichern");
    expect("damages_confirmed" in putAufrufe()[0]).toBe(false);

    await klick("protokoll-schaeden-bestaetigt-ja");
    await tippen("protokoll-preis-vorschlag", "15.000");
    expect(el("protokoll-preis-erkannt").textContent).toContain("15.000,00");
    await klick("protokoll-speichern");
    expect(putAufrufe().at(-1)).toMatchObject({ damages_confirmed: true, preis_vorschlag: 15000 });
  });

  it("unlesbarer Preis blockiert 'Zur Freigabe'", async () => {
    await starten(antwort());
    await tippen("protokoll-preis-vorschlag", "1.2.3");
    expect(el("protokoll-preis-unlesbar")).not.toBeNull();
    await klick("protokoll-zur-freigabe");
    expect(toastMock.error).toHaveBeenCalledWith(expect.stringContaining("nicht lesbar"));
    expect(api.post).not.toHaveBeenCalled();
  });
});

describe("RP-059: Ort und Verkäufer nach dem Abschicken", () => {
  it("zur Freigabe: gesperrt und mit den eingefrorenen Werten", async () => {
    await starten(antwort("zur_freigabe", { place: "Hannover", seller_name: "Vera Protokoll" }));
    expect(el("protokoll-ort").disabled).toBe(true);
    expect(el("protokoll-ort").value).toBe("Hannover");
    expect(el("protokoll-verkaeufer").disabled).toBe(true);
    expect(el("protokoll-verkaeufer").value).toBe("Vera Protokoll");
  });

  it("freigegeben: Abschluss schickt genau die angezeigten Werte", async () => {
    await starten(antwort("freigegeben", { place: "Hannover", seller_name: "Vera Protokoll",
                                           neuer_preis: 9000 }));
    expect(el("protokoll-verkaeufer").disabled).toBe(true);
    await klick("sig-Unterschrift Verkäufer");
    await klick("sig-Unterschrift Fahrer");
    await klick("protokoll-abschliessen");
    const [, body] = api.post.mock.calls.find(([url]) => url.endsWith("/protocol/finalize"));
    expect(body.place).toBe("Hannover");
    expect(body.seller_name).toBe("Vera Protokoll");
  });
});
