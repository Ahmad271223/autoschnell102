/*
 * Wunsch Ahmad 03.10.2026: Kamen beim Auslesen nur Daten, aber keine Fotos,
 * zeigt die Vergleichsseite den Knopf "Bilder nachholen". Er ruft das Inserat
 * über /mobile/bilder-nachholen komplett neu ab; kommen Fotos, erscheint die
 * Galerie. Mit Fotos (oder wenn der Server es ausschließt) gibt es keinen Knopf.
 */
import { act, createElement } from "react";
import { createRoot } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

globalThis.IS_REACT_ACT_ENVIRONMENT = true;

const api = vi.hoisted(() => ({ get: vi.fn(), post: vi.fn(), put: vi.fn() }));
const toastMock = vi.hoisted(() => ({
  error: vi.fn(), success: vi.fn(), info: vi.fn(), warning: vi.fn(), dismiss: vi.fn(),
}));

vi.mock("@/lib/api", () => ({
  api,
  errMsg: (e, f) => e?.response?.data?.detail || e?.message || f,
}));
vi.mock("sonner", () => ({ toast: toastMock }));
vi.mock("@/lib/clientFetch", () => ({ extensionReady: vi.fn(), fetchViaExtension: vi.fn() }));
vi.mock("@/context/AuthContext", () => ({
  useAuth: () => ({ user: { id: "u1" }, refresh: vi.fn(), setDealer: vi.fn() }),
}));
vi.mock("react-router-dom", () => ({ useNavigate: () => () => {} }));
vi.mock("@/components/ContractDialog", () => ({ default: () => null }));
vi.mock("@/components/SendDialog", () => ({ default: () => null }));
vi.mock("@/components/BeweisCard", () => ({ default: () => null }));
vi.mock("@/components/ProfileBadge", () => ({ default: () => null }));
vi.mock("@/components/PortalBadge", () => ({ default: () => null }));
vi.mock("@/components/MarktdatenKarte", () => ({ default: () => null }));
vi.mock("@/lib/pdf", () => ({ openContractPdf: vi.fn() }));
vi.mock("@/lib/filterOeffnen", () => ({ filterOeffnen: vi.fn(), FILTER_TOAST_ID: "filter" }));
vi.mock("@/lib/popup", () => ({
  fensterDanebenSetzen: vi.fn(), zweitenBildschirmAnfragen: vi.fn(async () => ({ ok: false })),
}));
vi.mock("@/lib/hinweise", () => ({ hinweiseZeigen: vi.fn(() => []) }));

const { default: Vergleich } = await import("./Vergleich");
const speicherModul = await import("@/lib/vergleichSpeicher");

const KA = "https://www.kleinanzeigen.de/s-anzeige/vw-golf/3012345678-216-1234";
const FOTOS = ["https://img.example/1.jpg", "https://img.example/2.jpg"];

let wurzel;
let behaelter;

async function warten() {
  for (let i = 0; i < 4; i += 1) {
    await act(async () => { await new Promise((r) => setTimeout(r, 0)); });
  }
}

const knopf = (id) => behaelter.querySelector(`[data-testid="${id}"]`);

async function zeigen(result) {
  speicherModul.vergleichSichern(window.sessionStorage, "u1", {
    url: KA,
    result: { vehicle_id: "v_3012345678", cache_key: "kleinanzeigen:3012345678", source: "kleinanzeigen",
              vehicle: { make_label: "VW", model_label: "Golf", images: [], images_thumbs: [] },
              ...result },
  });
  api.get.mockResolvedValue({ data: { active_now: 0, today: 0 } });
  await act(async () => { wurzel.render(createElement(Vergleich)); });
  await warten();
}

beforeEach(() => {
  vi.clearAllMocks();
  window.sessionStorage.clear();
  behaelter = document.createElement("div");
  document.body.appendChild(behaelter);
  wurzel = createRoot(behaelter);
});

afterEach(async () => {
  await act(async () => { wurzel.unmount(); });
  behaelter.remove();
});

describe("Bilder nachholen", () => {
  it("ohne Fotos: Knopf holt neu ab, danach erscheint die Galerie", async () => {
    api.post.mockResolvedValue({ data: { ok: true, bilder: 2, nachgeholt: true, images: FOTOS,
                                         images_thumbs: ["/api/bild?u=1", "/api/bild?u=2"] } });
    await zeigen({ bilder_nachholen_moeglich: true });
    expect(knopf("kleinanzeigen-gallery")).toBeNull();
    expect(knopf("bilder-nachholen-btn").textContent).toContain("Bilder nachholen");
    await act(async () => { knopf("bilder-nachholen-btn").click(); });
    await warten();
    expect(api.post).toHaveBeenCalledWith("/mobile/bilder-nachholen", { url: KA }, undefined);
    expect(toastMock.success).toHaveBeenCalledWith("2 Fotos nachgeholt.");
    expect(knopf("kleinanzeigen-gallery")).not.toBeNull();
    expect(knopf("gallery-thumb-1").querySelector("img").getAttribute("src")).toBe("/api/bild?u=2");
    expect(knopf("bilder-nachholen")).toBeNull();
  });

  it("mit Fotos oder wenn der Server es ausschließt: kein Knopf", async () => {
    await zeigen({ vehicle: { make_label: "VW", images: FOTOS, images_thumbs: [] },
                   bilder_nachholen_moeglich: false });
    expect(knopf("bilder-nachholen")).toBeNull();
    await act(async () => { wurzel.unmount(); });
    wurzel = createRoot(behaelter);
    window.sessionStorage.clear();
    await zeigen({ bilder_nachholen_moeglich: false });
    expect(knopf("bilder-nachholen")).toBeNull();
  });

  it("wieder keine Fotos: Hinweis, nach dem letzten Versuch kein Knopf mehr", async () => {
    api.post.mockResolvedValueOnce({ data: { ok: true, bilder: 0, versuche_uebrig: 2,
                                             hinweis: "Auch beim neuen Abruf kamen keine Fotos mit." } });
    await zeigen({ bilder_nachholen_moeglich: true });
    await act(async () => { knopf("bilder-nachholen-btn").click(); });
    await warten();
    expect(knopf("bilder-nachholen-hinweis").textContent).toContain("keine Fotos");
    expect(knopf("bilder-nachholen-btn")).not.toBeNull();
    api.post.mockResolvedValueOnce({ data: { ok: true, bilder: 0, versuche_uebrig: 0,
                                             hinweis: "Auch beim neuen Abruf kamen keine Fotos mit." } });
    await act(async () => { knopf("bilder-nachholen-btn").click(); });
    await warten();
    expect(knopf("bilder-nachholen-btn")).toBeNull();
  });

  it("409 vom Server: Meldung, kein Knopf mehr", async () => {
    const fehler = Object.assign(new Error("x"), {
      response: { status: 409, data: { detail: "Für dieses Inserat liegen keine Daten vor — bitte den Link neu auslesen." } },
    });
    api.post.mockRejectedValueOnce(fehler);
    await zeigen({});
    await act(async () => { knopf("bilder-nachholen-btn").click(); });
    await warten();
    expect(knopf("bilder-nachholen-hinweis").textContent).toContain("neu auslesen");
    expect(knopf("bilder-nachholen-btn")).toBeNull();
  });
});
