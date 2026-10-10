/*
 * Wunsch Ahmad 08.10.2026: EINE Portalwahl je Konto. Die Schalter auf der Vergleichsseite lesen den Kontowert
 * (user.vergleich_portale), speichern ihn per PUT /auth/vergleich-portale (gilt dann auch für das
 * Windows-Programm und die Browser-Erweiterung) und lassen nie beide Portale aus.
 */
import { act, createElement } from "react";
import { createRoot } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

globalThis.IS_REACT_ACT_ENVIRONMENT = true;

const api = vi.hoisted(() => ({ get: vi.fn(), post: vi.fn(), put: vi.fn() }));
const toastMock = vi.hoisted(() => ({
  error: vi.fn(), success: vi.fn(), info: vi.fn(), warning: vi.fn(), dismiss: vi.fn(),
}));
const konto = vi.hoisted(() => ({ user: { id: "u1" } }));

vi.mock("@/lib/api", () => ({ api, errMsg: (e, f) => e?.message || f }));
vi.mock("sonner", () => ({ toast: toastMock }));
vi.mock("@/lib/clientFetch", () => ({ extensionReady: vi.fn(), fetchViaExtension: vi.fn() }));
vi.mock("@/context/AuthContext", () => ({
  useAuth: () => ({ user: konto.user, refresh: vi.fn(), setDealer: vi.fn() }),
}));
vi.mock("react-router-dom", () => ({ useNavigate: () => () => {} }));
vi.mock("@/components/ContractDialog", () => ({ default: () => null }));
vi.mock("@/components/SendDialog", () => ({ default: () => null }));
vi.mock("@/components/BeweisCard", () => ({ default: () => null }));
vi.mock("@/components/ProfileBadge", () => ({ default: () => null }));
vi.mock("@/components/PortalBadge", () => ({ default: () => null }));
vi.mock("@/lib/pdf", () => ({ openContractPdf: vi.fn() }));
vi.mock("@/lib/filterOeffnen", () => ({ filterOeffnen: vi.fn(), FILTER_TOAST_ID: "filter" }));
vi.mock("@/lib/popup", () => ({
  fensterDanebenSetzen: vi.fn(), zweitenBildschirmAnfragen: vi.fn(async () => ({ ok: false })),
}));
vi.mock("@/lib/hinweise", () => ({ hinweiseZeigen: vi.fn(() => []) }));

const { default: Vergleich } = await import("./Vergleich");

let wurzel;
let behaelter;
const knopf = (id) => behaelter.querySelector(`[data-testid="${id}"]`);
const an = (id) => knopf(id).getAttribute("aria-pressed") === "true";

beforeEach(() => {
  vi.clearAllMocks();
  window.sessionStorage.clear();
  window.localStorage.clear();
  api.get.mockResolvedValue({ data: {} });
  api.put.mockResolvedValue({ data: {} });
  behaelter = document.createElement("div");
  document.body.appendChild(behaelter);
  wurzel = createRoot(behaelter);
});

afterEach(async () => {
  await act(async () => { wurzel.unmount(); });
  behaelter.remove();
});

describe("Portalwahl je Konto (08.10.2026)", () => {
  it("liest den Kontowert und speichert eine Änderung im Konto", async () => {
    konto.user = { id: "u1", vergleich_portale: { mobile: false, autoscout: true } };
    await act(async () => { wurzel.render(createElement(Vergleich)); });
    expect(an("toggle-mobile")).toBe(false);
    expect(an("toggle-autoscout")).toBe(true);
    expect(knopf("toggle-mobile").title).toContain("Windows-Programm");

    await act(async () => { knopf("toggle-mobile").click(); });
    expect(an("toggle-mobile")).toBe(true);
    expect(api.put).toHaveBeenCalledWith("/auth/vergleich-portale", { mobile: true, autoscout: true });
  });

  it("beide aus geht nicht — Hinweis statt Speichern", async () => {
    konto.user = { id: "u1", vergleich_portale: { mobile: false, autoscout: true } };
    await act(async () => { wurzel.render(createElement(Vergleich)); });
    await act(async () => { knopf("toggle-autoscout").click(); });
    expect(an("toggle-autoscout")).toBe(true);
    expect(api.put).not.toHaveBeenCalled();
    expect(toastMock.info).toHaveBeenCalledWith("Mindestens ein Portal muss an sein.");
  });

  it("scheitert das Speichern, springt der Schalter zurück", async () => {
    konto.user = { id: "u1", vergleich_portale: { mobile: true, autoscout: true } };
    api.put.mockRejectedValueOnce(new Error("Netz weg"));
    await act(async () => { wurzel.render(createElement(Vergleich)); });
    await act(async () => { knopf("toggle-autoscout").click(); });
    await act(async () => { await new Promise((r) => setTimeout(r, 0)); });
    expect(an("toggle-autoscout")).toBe(true);
    expect(toastMock.error).toHaveBeenCalledWith("Netz weg");
  });

  it("ohne Kontowert (altes Konto) gilt wie bisher der Browser-Speicher, Standard beide an", async () => {
    konto.user = { id: "u1" };
    await act(async () => { wurzel.render(createElement(Vergleich)); });
    expect(an("toggle-mobile")).toBe(true);
    expect(an("toggle-autoscout")).toBe(true);
  });
});
