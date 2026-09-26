/*
 * Rollenprüfung 22.09.2026, Welle 4 (Review) — Inserats-Editor (Team markt_haendler):
 *  RP-492/Nr. 399  Statuswechsel sperrt die Status-Knöpfe, bis der neue Stand
 *                  geladen ist — ein Doppelklick auf "Vom Marktplatz nehmen"
 *                  bzw. "Zurück zu Entwurf" schickt nur EINEN Wechsel (vorher
 *                  kam der zweite mit dem alten von_status und bekam 409).
 */
import { act, createElement } from "react";
import { createRoot } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

globalThis.IS_REACT_ACT_ENVIRONMENT = true;

const api = vi.hoisted(() => ({ get: vi.fn(), post: vi.fn(), put: vi.fn(), delete: vi.fn() }));
const toastMock = vi.hoisted(() => ({
  success: vi.fn(), error: vi.fn(), info: vi.fn(), warning: vi.fn(), message: vi.fn(),
}));

vi.mock("@/lib/api", () => ({
  api, errMsg: (e, f) => e?.response?.data?.detail || f || "Fehler", openAuthedFile: vi.fn(),
}));
vi.mock("@/lib/pdf", () => ({ openContractPdf: vi.fn() }));
vi.mock("@/lib/bilder", () => ({
  thumbSrc: (u) => u, thumbFehler: () => {}, verkleinereBildDatei: async (f) => f,
}));
vi.mock("@/lib/ungespeichert", () => ({ useUngespeichert: () => {} }));
vi.mock("sonner", () => ({ toast: toastMock }));
vi.mock("react-router-dom", async () => {
  const { createElement: ce } = await import("react");
  return {
    Link: ({ children, to, ...rest }) => ce("a", { href: String(to), ...rest }, children),
    useNavigate: () => vi.fn(),
    useParams: () => ({ id: "L1" }),
  };
});

const { default: Inserat } = await import("./Inserat");

const inserat = (status, extra = {}) => ({
  id: "L1", status, title: "Golf", description: "Gepflegt", known_defects: [],
  visibility: "public", vehicle_id: null,
  photos: { mode: "neu", uploaded_keys: ["a"] }, photo_urls: [],
  prices: { public: 20900, b2b: null, network: null }, costs: [],
  data: { mileage: 90000 }, margin: null, updated_at: "2026-09-22T10:00:00+00:00",
  ...extra,
});

let wurzel;
let behaelter;

async function warten() {
  await act(async () => { await new Promise((r) => setTimeout(r, 0)); });
  await act(async () => { await new Promise((r) => setTimeout(r, 0)); });
}
const el = (id) => behaelter.querySelector(`[data-testid="${id}"]`);

beforeEach(() => {
  vi.clearAllMocks();
  behaelter = document.createElement("div");
  document.body.appendChild(behaelter);
  wurzel = createRoot(behaelter);
});

afterEach(async () => {
  await act(async () => { wurzel.unmount(); });
  behaelter.remove();
});

describe("Statuswechsel sperrt die Knöpfe bis zum neuen Stand", () => {
  it("Doppelklick auf 'Vom Marktplatz nehmen' schickt nur einen Wechsel", async () => {
    let stand = inserat("veroeffentlicht");
    api.get.mockImplementation(async () => ({ data: stand }));
    let freigeben;
    api.post.mockImplementation(() => new Promise((r) => { freigeben = r; }));
    await act(async () => { wurzel.render(createElement(Inserat)); });
    await warten();
    const knopf = el("inserat-vom-marktplatz");
    expect(knopf).not.toBeNull();
    expect(knopf.disabled).toBe(false);
    // zwei Klicks im selben Takt (vor dem nächsten Rendern) und einer danach
    await act(async () => { knopf.click(); knopf.click(); });
    expect(knopf.disabled).toBe(true);
    await act(async () => { knopf.click(); });
    expect(api.post).toHaveBeenCalledTimes(1);
    expect(api.post.mock.calls[0][0]).toBe("/resale/L1/status");
    expect(api.post.mock.calls[0][1]).toMatchObject({ status: "zurueckgezogen", von_status: "veroeffentlicht" });
    // Server schreibt, Seite lädt neu -> neuer Stand, Knopf weg
    stand = inserat("zurueckgezogen");
    await act(async () => { freigeben({ data: { ok: true, status: "zurueckgezogen" } }); });
    await warten();
    expect(el("inserat-vom-marktplatz")).toBeNull();
    expect(toastMock.success).toHaveBeenCalledTimes(1);
    expect(toastMock.error).not.toHaveBeenCalled();
  });

  it("'Zurück zu Entwurf' ist nach einem Fehler wieder frei", async () => {
    api.get.mockResolvedValue({ data: inserat("verkaufsbereit") });
    api.post.mockRejectedValue({ response: { status: 400, data: { detail: "nicht erlaubt" } } });
    await act(async () => { wurzel.render(createElement(Inserat)); });
    await warten();
    const knopf = el("inserat-zurueck-entwurf");
    await act(async () => { knopf.click(); knopf.click(); });
    await warten();
    expect(api.post).toHaveBeenCalledTimes(1);
    expect(toastMock.error).toHaveBeenCalledWith("nicht erlaubt");
    expect(el("inserat-zurueck-entwurf").disabled).toBe(false);
  });
});
