/**
 * Wunsch Ahmad 06.10.2026: Der Chef sieht die Fotos des Fahrers je Schaden/Messung (Freigabe und
 * Fahrzeugakte) — geladen über den Chef-Weg, mit „sichtbar bis“ (7 Tage ab dem Hochladen).
 * AbholFoto lädt im pfad-Modus mit der übergebenen Anmeldung und öffnet groß in der App.
 */
import { act, createElement } from "react";
import { createRoot } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

const api = { get: vi.fn() };
vi.mock("@/lib/api", () => ({ api, openAuthedFile: vi.fn() }));
vi.mock("sonner", () => ({ toast: { error: vi.fn() } }));

globalThis.IS_REACT_ACT_ENVIRONMENT = true;
const { default: SchadenFotos } = await import("./SchadenFotos");
const { default: AbholFoto } = await import("./AbholFoto");

let wurzel;
let behaelter;
const el = (id) => behaelter.querySelector(`[data-testid="${id}"]`);
const warten = async () => { for (let i = 0; i < 3; i += 1) await act(async () => { await Promise.resolve(); }); };

beforeEach(() => {
  behaelter = document.createElement("div");
  document.body.appendChild(behaelter);
  wurzel = createRoot(behaelter);
  api.get.mockResolvedValue({ data: new Blob(["x"], { type: "image/jpeg" }) });
  globalThis.URL.createObjectURL = vi.fn(() => "blob:foto");
  globalThis.URL.revokeObjectURL = vi.fn();
});
afterEach(async () => {
  await act(async () => { wurzel.unmount(); });
  behaelter.remove();
  vi.clearAllMocks();
});

describe("SchadenFotos (Chef)", () => {
  it("zeigt nur die Fotos dieser Markierung, über den Chef-Weg, mit Sichtfrist", async () => {
    const fotos = [{ id: "a", schaden_id: "s1", sichtbar_bis: "2026-10-13T10:00:00+00:00" },
                   { id: "b", schaden_id: "s2", sichtbar_bis: "2026-10-13T10:00:00+00:00" }];
    await act(async () => {
      wurzel.render(createElement(SchadenFotos, { protokollId: "p1", schadenId: "s1", fotos }));
    });
    await warten();
    expect(api.get).toHaveBeenCalledTimes(1);
    expect(api.get).toHaveBeenCalledWith("/protocols/p1/schaden-fotos/a", { responseType: "blob" });
    expect(el("schadenfotos-bis-s1").textContent).toContain("sichtbar bis 13.10.");
  });

  it("ohne Fotos (oder nach der Sichtfrist, dann liefert der Server keine) nichts", async () => {
    await act(async () => {
      wurzel.render(createElement(SchadenFotos, { protokollId: "p1", schadenId: "s1", fotos: [] }));
    });
    expect(behaelter.innerHTML).toBe("");
  });
});

describe("AbholFoto im pfad-Modus", () => {
  it("lädt mit der übergebenen Anmeldung und öffnet groß in der App", async () => {
    const fahrerApi = { get: vi.fn().mockResolvedValue({ data: new Blob(["x"]) }) };
    await act(async () => {
      wurzel.render(createElement(AbholFoto, { pfad: "/driver/appointments/t1/protocol/schaden-fotos/f1",
                                                client: fahrerApi, label: "Schadenfoto" }));
    });
    await warten();
    expect(fahrerApi.get).toHaveBeenCalledWith("/driver/appointments/t1/protocol/schaden-fotos/f1",
                                               { responseType: "blob" });
    expect(api.get).not.toHaveBeenCalled();
    await act(async () => { el("abholfoto").dispatchEvent(new MouseEvent("click", { bubbles: true })); });
    expect(el("abholfoto-gross")).toBeTruthy();
    await act(async () => { el("abholfoto-gross").dispatchEvent(new MouseEvent("click", { bubbles: true })); });
    expect(el("abholfoto-gross")).toBeNull();
  });
});
