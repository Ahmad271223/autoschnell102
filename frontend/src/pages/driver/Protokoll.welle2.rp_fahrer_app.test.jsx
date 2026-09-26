/**
 * Rollenprüfung 22.09.2026, Welle 2 — Abhol-Protokoll der Fahrer-App (Protokoll.jsx),
 * Übergaben anderer Teams:
 *  RP-058/157/173  Verkäufername im Autosave (getippt oder aus dem Entwurf), Vorbelegung
 *  RP-062/161      409 "Bitte zuerst die Fahrt annehmen" -> Hinweis mit Weg zur Annahme
 *  RP-068/167      "Korrektur starten" nur bei offenem Termin
 *  RP-071/170      unbekannter Status gesperrt (fail-closed)
 *  RP-535          Bemerkungen mit Grenze und Zähler
 *  RP-546          Sicherung im Tab: Eingaben und Unterschriften nach Neuanmeldung zurück
 */
import { act, createElement } from "react";
import { createRoot } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

globalThis.IS_REACT_ACT_ENVIRONMENT = true;

const api = vi.hoisted(() => ({ get: vi.fn(), post: vi.fn(), put: vi.fn() }));
const toastMock = vi.hoisted(() => ({ error: vi.fn(), success: vi.fn(), warning: vi.fn(), info: vi.fn() }));
const navSpy = vi.hoisted(() => vi.fn());

vi.mock("@/context/DriverContext", () => ({ driverApi: api, openDriverPdf: vi.fn() }));
vi.mock("sonner", () => ({ toast: toastMock }));
vi.mock("react-router-dom", () => ({ useParams: () => ({ id: "t1" }), useNavigate: () => navSpy }));
vi.mock("@/lib/ungespeichert", () => ({ useUngespeichert: () => {} }));
vi.mock("@/components/DamageSelector", () => ({ default: () => null }));
vi.mock("@/components/MonatJahrEingabe", () => ({ default: () => null }));
vi.mock("@/components/SignaturePad", async () => {
  const { createElement: h } = await import("react");
  return {
    default: ({ label, onChange, startBild }) => h("button", {
      type: "button", "data-testid": `sig-${label}`, "data-start": startBild || "",
      onClick: () => onChange(`data:image/png;base64,${label.replace(/\W/g, "")}`),
    }, label),
  };
});

const { default: Protokoll } = await import("./Protokoll");

function antwort(status = "entwurf", protokoll = {}, termin = {}) {
  return {
    data: {
      protocol: status === null ? null : {
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
  api.get.mockImplementation(async () => {
    const naechste = liste.length > 1 ? liste.shift() : liste[0];
    if (naechste instanceof Error || naechste?.response) throw naechste;
    return naechste;
  });
  behaelter = document.createElement("div");
  document.body.appendChild(behaelter);
  wurzel = createRoot(behaelter);
  await act(async () => { wurzel.render(createElement(Protokoll)); });
  await warten();
}
async function beenden() {
  if (wurzel) await act(async () => { wurzel.unmount(); });
  wurzel = null;
  behaelter?.remove();
}
const putAufrufe = () => api.put.mock.calls.map(([, body]) => body);
const ANNAHME_409 = { response: { status: 409, data: {
  detail: "Bitte zuerst die Fahrt annehmen — erst dann sind Protokoll und Dokumente zugänglich." } } };
const SICHERUNG = "ah_protokoll_sicherung_t1";

beforeEach(() => {
  try { window.sessionStorage.clear(); } catch { /* egal */ }
  vi.spyOn(window, "confirm").mockReturnValue(true);
  api.put.mockResolvedValue({ data: { revision: 2 } });
  api.post.mockResolvedValue({ data: { ok: true } });
});

afterEach(async () => {
  await beenden();
  vi.useRealTimers();
  vi.clearAllMocks();
  vi.restoreAllMocks();
});

describe("RP-058: Verkäufername im Entwurf", () => {
  it("getippter Name geht per Autosave mit (getrimmt)", async () => {
    await starten(antwort());
    vi.useFakeTimers({ toFake: ["setTimeout", "clearTimeout"] });
    await tippen("protokoll-verkaeufer", "  Vera Neu ");
    await act(async () => { await vi.advanceTimersByTimeAsync(1300); });
    expect(api.put).toHaveBeenCalledTimes(1);
    expect(putAufrufe()[0].seller_name).toBe("Vera Neu");
  });

  it("nur aus dem Termin vorbelegt -> kein seller_name im Entwurf", async () => {
    await starten(antwort());
    expect(el("protokoll-verkaeufer").value).toBe("Vera Termin");
    await tippen("protokoll-bemerkungen", "x");
    await klick("protokoll-speichern");
    expect("seller_name" in putAufrufe()[0]).toBe(false);
  });

  it("Name aus dem Entwurf hat Vorrang und bleibt im Entwurf", async () => {
    await starten(antwort("entwurf", { seller_name: "Vera Entwurf" }));
    expect(el("protokoll-verkaeufer").value).toBe("Vera Entwurf");
    await tippen("protokoll-bemerkungen", "x");
    await klick("protokoll-speichern");
    expect(putAufrufe()[0].seller_name).toBe("Vera Entwurf");
  });
});

describe("RP-062: Fahrt nicht mehr angenommen", () => {
  it("Autosave-409 zeigt den Weg zur Annahme, lädt nicht sinnlos nach, sichert die Eingabe", async () => {
    await starten(antwort());
    vi.useFakeTimers({ toFake: ["setTimeout", "clearTimeout"] });
    api.put.mockRejectedValueOnce(ANNAHME_409);
    await tippen("protokoll-bemerkungen", "vor Ort getippt");
    await act(async () => { await vi.advanceTimersByTimeAsync(1300); });
    expect(el("protokoll-annahme-fehlt")).not.toBeNull();
    expect(el("protokoll-erneut-speichern")).toBeNull();
    expect(api.get).toHaveBeenCalledTimes(1);                 // kein Nachladen in die nächste 409
    await act(async () => { el("protokoll-fahrt-annehmen").click(); });
    expect(navSpy).toHaveBeenCalledWith("/fahrer?fahrt=t1");
    const gesichert = JSON.parse(window.sessionStorage.getItem(SICHERUNG));
    expect(gesichert.f.notes).toBe("vor Ort getippt");
  });

  it("erstes Laden mit 409: Hinweis statt endlos 'lade…'", async () => {
    await starten(ANNAHME_409);
    expect(el("protokoll-ladefehler")).not.toBeNull();
    await klick("protokoll-fahrt-annehmen");
    expect(navSpy).toHaveBeenCalledWith("/fahrer?fahrt=t1");
  });
});

describe("RP-068: Korrektur nur bei offenem Termin", () => {
  it("Termin abgeholt -> kein Korrektur-Knopf, Hinweis", async () => {
    await starten(antwort("final", {}, { status: "abgeholt" }));
    expect(el("protokoll-korrektur-starten")).toBeNull();
    expect(el("protokoll-korrektur-unten")).toBeNull();
    expect(el("protokoll-korrektur-hinweis").textContent).toContain("Wiederöffnen");
    expect(el("protokoll-pdf-unten")).not.toBeNull();
  });

  it("Termin wieder offen -> Korrektur möglich", async () => {
    await starten(antwort("final", {}, { status: "offen" }));
    expect(el("protokoll-korrektur-starten")).not.toBeNull();
    expect(el("protokoll-korrektur-unten")).not.toBeNull();
    expect(el("protokoll-korrektur-hinweis")).toBeNull();
  });
});

describe("RP-071: unbekannter Status ist gesperrt", () => {
  it("keine Eingaben, kein Speichern, Hinweis", async () => {
    await starten(antwort("neuer_zustand_2027"));
    expect(el("protokoll-unbekannt")).not.toBeNull();
    expect(el("protokoll-bemerkungen").disabled).toBe(true);
    expect(el("protokoll-speichern")).toBeNull();
    expect(el("protokoll-zur-freigabe")).toBeNull();
    expect(el("protokoll-unbekannt-aktualisieren")).not.toBeNull();
  });
});

describe("RP-535: Bemerkungen", () => {
  it("Grenze 5000 und Zähler", async () => {
    await starten(antwort());
    expect(el("protokoll-bemerkungen").maxLength).toBe(5000);
    await tippen("protokoll-bemerkungen", "abc");
    expect(el("protokoll-bemerkungen-zaehler").textContent).toBe("3 / 5000");
  });
});

describe("RP-546: Sicherung im Tab", () => {
  it("nicht gespeicherte Eingaben kommen nach der Neuanmeldung zurück und werden gespeichert", async () => {
    window.sessionStorage.setItem(SICHERUNG, JSON.stringify({
      v: 1, zeit: Date.now(), f: { notes: "offline getippt", documents: { Brief: true } },
      basis: { notes: "", documents: {} }, sellerName: "Vera Neu", nameGetippt: true,
      ortGetippt: false, sigDriver: null, sigSeller: null, kennung: null }));
    await starten(antwort("entwurf", { notes: "", documents: { Schein: false } }));
    expect(el("protokoll-bemerkungen").value).toBe("offline getippt");
    expect(el("protokoll-verkaeufer").value).toBe("Vera Neu");
    expect(toastMock.info).toHaveBeenCalledWith(expect.stringContaining("wiederhergestellt"),
                                                expect.anything());
    // Der Autosave läuft 1,2 s nach dem Wiederherstellen (echte Zeit).
    await act(async () => { await new Promise((r) => setTimeout(r, 1400)); });
    await warten();
    expect(api.put).toHaveBeenCalledTimes(1);
    expect(putAufrufe()[0]).toMatchObject({
      notes: "offline getippt", documents: { Brief: true, Schein: false }, seller_name: "Vera Neu" });
    // gespeichert -> Sicherung wird nicht mehr gebraucht
    expect(window.sessionStorage.getItem(SICHERUNG)).toBeNull();
  });

  it("Unterschriften unter demselben Freigabe-Stand kommen zurück", async () => {
    window.sessionStorage.setItem(SICHERUNG, JSON.stringify({
      v: 1, zeit: Date.now(), f: {}, basis: {}, sellerName: "", nameGetippt: false,
      sigDriver: "data:image/png;base64,FAHRER", sigSeller: "data:image/png;base64,VERK",
      kennung: "s1|9000|" }));
    await starten(antwort("freigegeben", { neuer_preis: 9000, seller_name: "Vera" }));
    expect(el("sig-Unterschrift Verkäufer").dataset.start).toBe("data:image/png;base64,VERK");
    await klick("protokoll-abschliessen");
    const [, body] = api.post.mock.calls.find(([url]) => url.endsWith("/protocol/finalize"));
    expect(body.signature_driver_b64).toBe("data:image/png;base64,FAHRER");
    expect(body.signature_seller_b64).toBe("data:image/png;base64,VERK");
    expect(window.sessionStorage.getItem(SICHERUNG)).toBeNull();
  });

  it("anderer Preis seit der Unterschrift -> Unterschriften verworfen", async () => {
    window.sessionStorage.setItem(SICHERUNG, JSON.stringify({
      v: 1, zeit: Date.now(), f: {}, basis: {}, sigDriver: "data:image/png;base64,FAHRER",
      sigSeller: "data:image/png;base64,VERK", kennung: "s1|9000|" }));
    await starten(antwort("freigegeben", { neuer_preis: 8000, freigabe_stand: "s2" }));
    expect(el("sig-Unterschrift Verkäufer").dataset.start).toBe("");
    await klick("protokoll-abschliessen");
    expect(toastMock.error).toHaveBeenCalledWith("Bitte beide Unterschriften erfassen");
    expect(window.sessionStorage.getItem(SICHERUNG)).toBeNull();
  });

  it("Unterschriften werden während der Freigabe gesichert", async () => {
    await starten(antwort("freigegeben", { neuer_preis: 9000 }));
    await klick("sig-Unterschrift Verkäufer");
    await act(async () => { await new Promise((r) => setTimeout(r, 350)); });
    const s = JSON.parse(window.sessionStorage.getItem(SICHERUNG));
    expect(s.sigSeller).toContain("UnterschriftVerk");
    expect(s.kennung).toBe("s1|9000|");
  });
});
