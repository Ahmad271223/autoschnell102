/**
 * Wunsch Ahmad 24.11.2026: nummerierte Abweichungen (Büro und Fahrer dieselbe Liste) und
 * „Was ist neu“ (Seite, Zähler, ein Hinweis pro Tag mit „Später“ und „Gelesen“).
 */
import { act, createElement } from "react";
import { createRoot } from "react-dom/client";
import { MemoryRouter } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

const api = { get: vi.fn(), post: vi.fn() };
vi.mock("@/lib/api", () => ({ api, errMsg: (e, s) => e?.message || s }));
vi.mock("sonner", () => ({ toast: { success: vi.fn(), error: vi.fn(), info: vi.fn(), warning: vi.fn() } }));

globalThis.IS_REACT_ACT_ENVIRONMENT = true;
const { default: AbweichungenListe, abweichungZeile } = await import("./AbweichungenListe");
const { default: AbweichungenDialog } = await import("./AbweichungenDialog");
const { default: NeuigkeitenHinweis } = await import("./NeuigkeitenHinweis");
const { default: Neuigkeiten } = await import("@/pages/app/Neuigkeiten");
const { NEUIGKEITEN_EREIGNIS, appLink, datumText } = await import("@/lib/neuigkeiten");

const DATEN = {
  anzahl: 3,
  eintraege: [],
  gruppen: [
    { key: "schaeden", titel: "Neue Schäden", eintraege: [{ nr: 2, schluessel: "sch:s1", titel: "Kratzer · Tür hinten links", text: "neu vor Ort festgestellt" }] },
    { key: "ausstattung_fehlt", titel: "Ausstattung fehlt", eintraege: [
      { nr: 3, schluessel: "aus:Navi", titel: "Navi", text: "fehlt komplett" },
      { nr: 5, schluessel: "aus:AHK", titel: "AHK", text: "fehlt komplett" }] },
  ],
};

let host; let root;
beforeEach(() => {
  host = document.createElement("div");
  document.body.appendChild(host);
  root = createRoot(host);
  api.get.mockReset(); api.post.mockReset();
});
afterEach(() => {
  act(() => root.unmount());
  host.remove();
  document.body.innerHTML = "";
});
const rendern = async (el) => { await act(async () => { root.render(createElement(MemoryRouter, null, el)); }); };
const warten = async () => { await act(async () => { await new Promise((r) => setTimeout(r, 0)); }); };
const q = (id) => document.querySelector(`[data-testid="${id}"]`);

describe("AbweichungenListe", () => {
  it("zeigt feste Nummern und die Gruppe „Ausstattung fehlt“", async () => {
    await rendern(createElement(AbweichungenListe, { daten: DATEN, testId: "abw" }));
    expect(q("abw").textContent).toContain("3 Abweichungen");
    expect(q("abw-nr-2").textContent).toContain("Nr. 2 · Kratzer · Tür hinten links");
    expect(q("abw-nr-5").textContent).toContain("Nr. 5");
    expect(q("abw-gruppe-ausstattung_fehlt").textContent).toContain("Ausstattung fehlt");
    expect(abweichungZeile({ nr: 3, titel: "Navi" })).toBe("Nr. 3 · Navi");
  });
  it("ohne Abweichung: nichts bzw. der Leertext; älteres Backend: nichts", async () => {
    await rendern(createElement(AbweichungenListe, { daten: { anzahl: 0, gruppen: [] }, testId: "abw" }));
    expect(q("abw")).toBeNull();
    await rendern(createElement(AbweichungenListe, { daten: undefined }));
    expect(host.textContent).toBe("");
    await rendern(createElement(AbweichungenListe, { daten: { anzahl: 0, gruppen: [] }, testId: "abw", leerText: "Keine" }));
    expect(q("abw-keine").textContent).toContain("Keine");
  });
  it("Dialog lädt die Liste des Termins", async () => {
    api.get.mockResolvedValue({ data: { ...DATEN, protocol_id: "p1" } });
    await rendern(createElement(AbweichungenDialog, { appt: { id: "t1", title: "Golf" }, onClose: () => {} }));
    await warten();
    expect(api.get).toHaveBeenCalledWith("/appointments/t1/abweichungen");
    expect(q("termin-abweichungen-nr-3").textContent).toContain("Navi");
  });
});

describe("Was ist neu", () => {
  const EINTRAG = { id: "e1", datum: "2026-11-24", titel: "Chat mit dem Fahrer", bereich: "Abholung", art: "neu",
                    text: "Kurz erklärt.", punkte: ["Punkt A"], link: "/app/termine", wichtig: true, gelesen: false };

  it("Helfer: Datum und nur App-Links", () => {
    expect(datumText("2026-11-24")).toBe("24.11.2026");
    expect(appLink("/app/termine")).toBe("/app/termine");
    expect(appLink("https://fremd.example")).toBeNull();
  });

  it("Seite: gelesen je Eintrag und alle, meldet den Zähler", async () => {
    api.get.mockResolvedValue({ data: { eintraege: [EINTRAG, { ...EINTRAG, id: "e2", titel: "Zwei", gelesen: false }], ungelesen: 2 } });
    api.post.mockResolvedValue({ data: { ungelesen: 1, ids: ["e2"] } });
    const gemeldet = vi.fn();
    window.addEventListener(NEUIGKEITEN_EREIGNIS, gemeldet);
    await rendern(createElement(Neuigkeiten));
    await warten();
    expect(q("neuigkeit-details-e1").textContent).toContain("Punkt A");
    await act(async () => { q("neuigkeit-umschalten-e1").click(); });
    expect(q("neuigkeit-details-e1")).toBeNull();
    await act(async () => { q("neuigkeit-gelesen-e1").click(); });
    expect(api.post).toHaveBeenCalledWith("/neuigkeiten/e1/gelesen");
    expect(q("neuigkeit-gelesen-e1")).toBeNull();
    expect(gemeldet).toHaveBeenCalled();
    api.post.mockResolvedValue({ data: { ungelesen: 0, ids: [] } });
    await act(async () => { q("neuigkeiten-alle-gelesen").click(); });
    expect(api.post).toHaveBeenCalledWith("/neuigkeiten/alle-gelesen");
    expect(q("neuigkeit-gelesen-e2")).toBeNull();
    window.removeEventListener(NEUIGKEITEN_EREIGNIS, gemeldet);
  });

  it("Hinweis: einmal fragen, „Später“ schließt ohne gelesen", async () => {
    api.post.mockResolvedValue({ data: { hinweis: EINTRAG } });
    await rendern(createElement(NeuigkeitenHinweis, { aktiv: true, kontoId: "k1" }));
    await warten();
    expect(api.post).toHaveBeenCalledWith("/neuigkeiten/hinweis");
    expect(q("neuigkeiten-hinweis").textContent).toContain("Chat mit dem Fahrer");
    await act(async () => { q("neuigkeiten-hinweis-spaeter").click(); });
    expect(q("neuigkeiten-hinweis")).toBeNull();
    expect(api.post).toHaveBeenCalledTimes(1);
    // erneuter Aufbau (Seitenwechsel) fragt nicht noch einmal
    await rendern(createElement("div"));
    await rendern(createElement(NeuigkeitenHinweis, { aktiv: true, kontoId: "k1" }));
    await warten();
    expect(api.post).toHaveBeenCalledTimes(1);
  });

  it("Hinweis: „Gelesen“ merkt den Eintrag; Fahrer mit eigenem Weg; kein Hinweis -> kein Fenster", async () => {
    const fahrerApi = { post: vi.fn()
      .mockResolvedValueOnce({ data: { hinweis: { ...EINTRAG, link: null } } })
      .mockResolvedValueOnce({ data: { ungelesen: 0, ids: [] } }) };
    await rendern(createElement(NeuigkeitenHinweis, { aktiv: true, kontoId: "f1", client: fahrerApi,
                                                     basis: "/driver/neuigkeiten", alleLink: null }));
    await warten();
    expect(fahrerApi.post).toHaveBeenCalledWith("/driver/neuigkeiten/hinweis");
    expect(q("neuigkeiten-hinweis-ansehen")).toBeNull();
    await act(async () => { q("neuigkeiten-hinweis-gelesen").click(); });
    await warten();
    expect(fahrerApi.post).toHaveBeenLastCalledWith("/driver/neuigkeiten/e1/gelesen");
    expect(q("neuigkeiten-hinweis")).toBeNull();

    api.post.mockResolvedValue({ data: { hinweis: null } });
    await rendern(createElement(NeuigkeitenHinweis, { aktiv: true, kontoId: "k2" }));
    await warten();
    expect(q("neuigkeiten-hinweis")).toBeNull();
  });
});
