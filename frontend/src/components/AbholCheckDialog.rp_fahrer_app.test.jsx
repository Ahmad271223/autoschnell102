/**
 * Rollenprüfung 22.09.2026 — Abhol-Check-Dialog (AbholCheckDialog.jsx).
 *  RP-066/RP-165  Idempotenz-Schlüssel je Dialog, derselbe bei jedem Versuch.
 *  RP-070         Schlüsselanzahl höchstens 10 (wie der Server).
 *  RP-064/RP-163  Fehlt das Protokoll noch, bleiben die Eingaben gesichert.
 */
import { act, createElement } from "react";
import { createRoot } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

globalThis.IS_REACT_ACT_ENVIRONMENT = true;

const api = vi.hoisted(() => ({ get: vi.fn(), post: vi.fn(), put: vi.fn() }));
const toastMock = vi.hoisted(() => ({ error: vi.fn(), success: vi.fn(), warning: vi.fn(), info: vi.fn() }));
const navSpy = vi.hoisted(() => vi.fn());

vi.mock("@/context/DriverContext", () => ({ driverApi: api }));
vi.mock("sonner", () => ({ toast: toastMock }));
vi.mock("react-router-dom", () => ({ useNavigate: () => navSpy }));
vi.mock("@/lib/ungespeichert", () => ({ useUngespeichert: () => {} }));
vi.mock("@/lib/bilder", () => ({ verkleinereBildDatei: vi.fn() }));

const { default: AbholCheckDialog } = await import("./AbholCheckDialog");

let wurzel;
let behaelter;

async function warten() {
  await act(async () => { await new Promise((r) => setTimeout(r, 0)); });
  await act(async () => { await new Promise((r) => setTimeout(r, 0)); });
}
const el = (id) => behaelter.querySelector(`[data-testid="${id}"]`);
async function tippen(id, wert) {
  const feld = el(id);
  const setter = Object.getOwnPropertyDescriptor(window.HTMLInputElement.prototype, "value").set;
  await act(async () => {
    setter.call(feld, wert);
    feld.dispatchEvent(new Event("input", { bubbles: true }));
  });
}
async function absenden() {
  await act(async () => { el("abholcheck-absenden").click(); });
  await warten();
}

async function oeffnen(appointment) {
  if (wurzel) await act(async () => { wurzel.unmount(); });
  wurzel = createRoot(behaelter);
  await act(async () => {
    wurzel.render(createElement(AbholCheckDialog, { appointment, onClose: vi.fn(), onDone: vi.fn() }));
  });
  await warten();
}

beforeEach(() => {
  vi.clearAllMocks();
  try { window.sessionStorage.clear(); } catch { /* egal */ }
  behaelter = document.createElement("div");
  document.body.appendChild(behaelter);
  wurzel = null;
  api.put.mockResolvedValue({ data: { ok: true } });
});

afterEach(async () => {
  if (wurzel) await act(async () => { wurzel.unmount(); });
  behaelter.remove();
});

describe("Abhol-Check", () => {
  it("RP-066: derselbe Idempotenz-Schlüssel bei jedem Versuch", async () => {
    await oeffnen({ id: "t1", status: "abgeholt", title: "Golf" });
    await tippen("abholcheck-km", "85.120");
    api.post.mockRejectedValueOnce(Object.assign(new Error("Network Error"), { code: "ERR_NETWORK" }));
    await absenden();
    expect(toastMock.error).toHaveBeenCalledTimes(1);
    api.post.mockResolvedValueOnce({ data: { ok: true, report_id: "r1", version: 1, wiederholt: true } });
    await absenden();
    const [erster, zweiter] = api.post.mock.calls.map(([, body]) => body);
    expect(erster.mileage_at_pickup).toBe(85120);
    expect(typeof erster.client_bericht_id).toBe("string");
    expect(erster.client_bericht_id.length).toBeGreaterThanOrEqual(8);
    expect(zweiter.client_bericht_id).toBe(erster.client_bericht_id);
    expect(api.put).toHaveBeenCalledWith("/driver/appointments/t1/status", { status: "abgeholt" });
  });

  it("RP-070: mehr als 10 Schlüssel werden gleich abgewiesen", async () => {
    await oeffnen({ id: "t2", status: "abgeholt", title: "Golf" });
    await tippen("abholcheck-km", "85120");
    await tippen("abholcheck-schluessel", "11");
    await absenden();
    expect(toastMock.error).toHaveBeenCalledWith(expect.stringContaining("0 bis 10"));
    expect(api.post).not.toHaveBeenCalled();
  });

  it("RP-064: Protokoll fehlt -> Eingaben bleiben für diese Fahrt erhalten", async () => {
    api.get.mockResolvedValue({ data: { protocol: { status: "entwurf" } } });
    await oeffnen({ id: "t3", status: "offen", title: "Golf" });
    await tippen("abholcheck-km", "99.000");
    await absenden();
    expect(navSpy).toHaveBeenCalledWith("/fahrer/protokoll/t3");
    expect(api.post).not.toHaveBeenCalled();
    // Später erneut geöffnet (Protokoll inzwischen unterschrieben)
    api.get.mockResolvedValue({ data: { protocol: { status: "final" } } });
    await oeffnen({ id: "t3", status: "offen", title: "Golf" });
    expect(el("abholcheck-km").value).toBe("99.000");
    await absenden();
    expect(api.post).toHaveBeenCalledTimes(1);
    expect(api.post.mock.calls[0][1].mileage_at_pickup).toBe(99000);
    // Nach dem Erfolg ist der Zwischenstand weg
    expect(window.sessionStorage.getItem("ah_abholcheck_t3")).toBeNull();
  });
});
