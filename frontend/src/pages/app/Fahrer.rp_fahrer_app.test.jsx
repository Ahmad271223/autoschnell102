/**
 * Rollenprüfung 22.09.2026 — Fahrer-Verwaltung des Chefs (Fahrer.jsx).
 *  RP-041/RP-140  Tabelle am Handy quer scrollbar; Rückfrage nennt die offenen
 *                 Fahrten, danach Hinweis mit Weg zum Terminplaner.
 *  RP-542         Telefonnummer des Fahrers als tel:-Link.
 */
import { act, createElement } from "react";
import { createRoot } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

globalThis.IS_REACT_ACT_ENVIRONMENT = true;

const api = vi.hoisted(() => ({ get: vi.fn(), post: vi.fn(), delete: vi.fn() }));
const toastMock = vi.hoisted(() => ({ error: vi.fn(), success: vi.fn(), warning: vi.fn() }));
const navSpy = vi.hoisted(() => vi.fn());

vi.mock("@/lib/api", () => ({ api, errMsg: (e, f) => e?.response?.data?.detail || f }));
vi.mock("@/context/AuthContext", () => ({ useAuth: () => ({ user: { id: "c1", role: "dealer" } }) }));
vi.mock("sonner", () => ({ toast: toastMock }));
vi.mock("react-router-dom", () => ({ useNavigate: () => navSpy }));

const { default: Fahrer, entfernenRueckfrage } = await import("./Fahrer");

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
  vi.restoreAllMocks();
});

describe("Rückfrage beim Entfernen", () => {
  it("nennt die Zahl der offenen Fahrten", () => {
    expect(entfernenRueckfrage("Max", 2)).toContain("2 offene Fahrten verlieren");
    expect(entfernenRueckfrage("Max", 1)).toContain("1 offene Fahrt verliert");
    expect(entfernenRueckfrage("Max", 0)).toContain("keine offenen Fahrten");
    expect(entfernenRueckfrage("Max", undefined)).toBe("„Max“ aus deiner Liste entfernen?");
  });
});

describe("Fahrerliste des Chefs", () => {
  it("Telefon, Querscrollen, Rückfrage und Hinweis nach dem Entfernen", async () => {
    api.get.mockResolvedValue({ data: [{ id: "f1", name: "Max", driver_code: "FD-AAAAAAAA",
                                          email: "max@example.test", phone: "0511 4711",
                                          active: true, offene_fahrten: 2 }] });
    api.delete.mockResolvedValue({ data: { ok: true, offene_termine_getrennt: 2,
                                           abgeschlossene_termine_archiviert: 0 } });
    const frage = vi.spyOn(window, "confirm").mockReturnValue(true);
    await act(async () => { wurzel.render(createElement(Fahrer)); });
    await warten();
    expect(el("drivers-tabelle").className).toContain("overflow-x-auto");
    expect(el("driver-phone-f1").getAttribute("href")).toBe("tel:05114711");
    await act(async () => { el("del-driver-f1").click(); });
    await warten();
    expect(frage.mock.calls[0][0]).toContain("2 offene Fahrten");
    const [text, optionen] = toastMock.warning.mock.calls[0];
    expect(text).toContain("2 offene Fahrten sind jetzt ohne Fahrer");
    optionen.action.onClick();
    expect(navSpy).toHaveBeenCalledWith("/app/termine");
  });
});
