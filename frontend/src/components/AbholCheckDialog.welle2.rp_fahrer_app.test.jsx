/**
 * Rollenprüfung 22.09.2026, Welle 2 — Abhol-Check-Dialog (AbholCheckDialog.jsx).
 *  RP-068/RP-167  km-Stand aus dem unterschriebenen Protokoll vorbelegt.
 *  RP-533         HEIC & Co.: die Meldung aus lib/bilder statt "konnte nicht gelesen werden".
 */
import { act, createElement } from "react";
import { createRoot } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

globalThis.IS_REACT_ACT_ENVIRONMENT = true;

const api = vi.hoisted(() => ({ get: vi.fn(), post: vi.fn(), put: vi.fn() }));
const toastMock = vi.hoisted(() => ({ error: vi.fn(), success: vi.fn(), warning: vi.fn(), info: vi.fn() }));
const bilder = vi.hoisted(() => ({ verkleinereBildDatei: vi.fn() }));

vi.mock("@/context/DriverContext", () => ({ driverApi: api }));
vi.mock("sonner", () => ({ toast: toastMock }));
vi.mock("react-router-dom", () => ({ useNavigate: () => vi.fn() }));
vi.mock("@/lib/ungespeichert", () => ({ useUngespeichert: () => {} }));
vi.mock("@/lib/bilder", () => bilder);

const { default: AbholCheckDialog } = await import("./AbholCheckDialog");

let wurzel;
let behaelter;

async function warten() {
  await act(async () => { await new Promise((r) => setTimeout(r, 0)); });
  await act(async () => { await new Promise((r) => setTimeout(r, 0)); });
}
const el = (id) => behaelter.querySelector(`[data-testid="${id}"]`);

async function oeffnen(appointment) {
  wurzel = createRoot(behaelter);
  await act(async () => {
    wurzel.render(createElement(AbholCheckDialog, { appointment, onClose: vi.fn(), onDone: vi.fn() }));
  });
  await warten();
}

beforeEach(() => {
  vi.clearAllMocks();
  window.sessionStorage.clear();
  behaelter = document.createElement("div");
  document.body.appendChild(behaelter);
});

afterEach(async () => {
  if (wurzel) await act(async () => { wurzel.unmount(); });
  wurzel = null;
  behaelter.remove();
});

describe("RP-068: km-Stand vorbelegt", () => {
  it("aus dem unterschriebenen Protokoll, mit Hinweis", async () => {
    api.get.mockResolvedValue({ data: { protocol: { status: "final", condition: { mileage: "123456" } } } });
    await oeffnen({ id: "k1", status: "abgeholt", title: "Golf" });
    expect(api.get).toHaveBeenCalledWith("/driver/appointments/k1/protocol");
    expect(el("abholcheck-km").value).toBe("123456");
    expect(el("abholcheck-km-aus-protokoll")).not.toBeNull();
  });

  it("ein gesicherter eigener Wert bleibt (keine Vorbelegung)", async () => {
    window.sessionStorage.setItem("ah_abholcheck_k2", JSON.stringify({ mileage: "99.000" }));
    api.get.mockResolvedValue({ data: { protocol: { status: "final", condition: { mileage: "123456" } } } });
    await oeffnen({ id: "k2", status: "abgeholt", title: "Golf" });
    expect(el("abholcheck-km").value).toBe("99.000");
    expect(el("abholcheck-km-aus-protokoll")).toBeNull();
  });

  it("ohne finales Protokoll: leer", async () => {
    api.get.mockResolvedValue({ data: { protocol: { status: "entwurf", condition: { mileage: "5" } } } });
    await oeffnen({ id: "k3", status: "offen", title: "Golf" });
    expect(el("abholcheck-km").value).toBe("");
  });
});

describe("RP-533: nicht umwandelbares Foto", () => {
  it("zeigt die Meldung aus lib/bilder", async () => {
    api.get.mockResolvedValue({ data: { protocol: null } });
    const fehler = Object.assign(new Error("HEIC-Fotos kann dieser Browser nicht umwandeln — bitte als JPEG aufnehmen."),
                                 { name: "BildFormatFehler" });
    bilder.verkleinereBildDatei.mockRejectedValueOnce(fehler);
    await oeffnen({ id: "k4", status: "abgeholt", title: "Golf" });
    const hinzu = [...behaelter.querySelectorAll("button")].find((b) => b.textContent.includes("Abweichung hinzufügen"));
    await act(async () => { hinzu.click(); });
    const feld = behaelter.querySelector('input[type="file"]');
    Object.defineProperty(feld, "files", { value: [new File(["x"], "bild.heic", { type: "image/heic" })] });
    await act(async () => { feld.dispatchEvent(new Event("change", { bubbles: true })); });
    await warten();
    expect(toastMock.error).toHaveBeenCalledWith(fehler.message);
    expect(toastMock.error).not.toHaveBeenCalledWith("Foto konnte nicht gelesen werden");
  });

  it("anderer Fehler: wie bisher", async () => {
    api.get.mockResolvedValue({ data: { protocol: null } });
    bilder.verkleinereBildDatei.mockRejectedValueOnce(new Error("kaputt"));
    await oeffnen({ id: "k5", status: "abgeholt", title: "Golf" });
    const hinzu = [...behaelter.querySelectorAll("button")].find((b) => b.textContent.includes("Abweichung hinzufügen"));
    await act(async () => { hinzu.click(); });
    const feld = behaelter.querySelector('input[type="file"]');
    Object.defineProperty(feld, "files", { value: [new File(["x"], "bild.jpg", { type: "image/jpeg" })] });
    await act(async () => { feld.dispatchEvent(new Event("change", { bubbles: true })); });
    await warten();
    expect(toastMock.error).toHaveBeenCalledWith("Foto konnte nicht gelesen werden");
  });
});
