/*
 * Programme zum Herunterladen (03.10.2026): Das Windows-Programm öffnet /app/vergleich?url=<Inserat>.
 * Die Seite übernimmt den Link, entfernt ihn aus der Adresse (kein Neustart beim Neuladen) und startet
 * den Vergleich sofort — der Server hat das Inserat beim Anklicken schon ausgelesen.
 */
import { act, createElement } from "react";
import { createRoot } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

globalThis.IS_REACT_ACT_ENVIRONMENT = true;

const api = vi.hoisted(() => ({ get: vi.fn(), post: vi.fn(), put: vi.fn() }));
const toastMock = vi.hoisted(() => ({
  error: vi.fn(), success: vi.fn(), info: vi.fn(), warning: vi.fn(), dismiss: vi.fn(),
}));
const navMock = vi.hoisted(() => vi.fn());

vi.mock("@/lib/api", () => ({
  api,
  errMsg: (e, f) => e?.response?.data?.detail || e?.message || f,
}));
vi.mock("sonner", () => ({ toast: toastMock }));
vi.mock("@/lib/clientFetch", () => ({ extensionReady: vi.fn(), fetchViaExtension: vi.fn() }));
vi.mock("@/context/AuthContext", () => ({
  useAuth: () => ({ user: { id: "u1" }, refresh: vi.fn(), setDealer: vi.fn() }),
}));
vi.mock("react-router-dom", () => ({ useNavigate: () => navMock }));
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

const KA = "https://www.kleinanzeigen.de/s-anzeige/3529833344";

let wurzel;
let behaelter;

async function warten() {
  for (let i = 0; i < 6; i += 1) {
    await act(async () => { await new Promise((r) => setTimeout(r, 0)); });
  }
}

beforeEach(() => {
  vi.clearAllMocks();
  window.sessionStorage.clear();
  behaelter = document.createElement("div");
  document.body.appendChild(behaelter);
  wurzel = createRoot(behaelter);
  api.get.mockResolvedValue({ data: { active_now: 0, today: 0 } });
  api.post.mockImplementation((pfad) => Promise.resolve(pfad === "/listings/check"
    ? { data: { status: "completed", cached: true, source: "kleinanzeigen" } }
    : { data: { vehicle_id: "v_3529833344", cache_key: "kleinanzeigen:3529833344", source: "kleinanzeigen",
                cached: true, vehicle: { make_label: "VW", model_label: "Polo", images: [], images_thumbs: [] } } }));
});

afterEach(async () => {
  await act(async () => { wurzel.unmount(); });
  behaelter.remove();
  window.history.replaceState({}, "", "/");
});

describe("Vergleich über ?url= (Programm)", () => {
  it("übernimmt den Link, räumt die Adresse auf und startet sofort", async () => {
    window.history.replaceState({}, "", `/app/vergleich?url=${encodeURIComponent(KA)}`);
    await act(async () => { wurzel.render(createElement(Vergleich)); });
    await warten();
    expect(navMock).toHaveBeenCalledWith("/app/vergleich", { replace: true });
    const pfade = api.post.mock.calls.map((c) => c[0]);
    expect(pfade).toContain("/mobile/compare");
    const vergleich = api.post.mock.calls.find((c) => c[0] === "/mobile/compare");
    expect(vergleich[1]).toMatchObject({ url: KA });
  });

  it("aus dem Programm: nur auslesen, die Filter nicht ein zweites Mal öffnen", async () => {
    // Wunsch Ahmad 03.10.2026: das Programm hat mobile.de/AutoScout24 schon geöffnet
    const { filterOeffnen } = await import("@/lib/filterOeffnen");
    api.post.mockImplementation((pfad) => Promise.resolve(pfad === "/listings/check"
      ? { data: { status: "completed", cached: true, source: "kleinanzeigen" } }
      : { data: { vehicle_id: "v_3529833344", cache_key: "kleinanzeigen:3529833344", source: "kleinanzeigen",
                  cached: true, search_url: "https://suchen.mobile.de/x", autoscout_url: "https://www.autoscout24.de/lst/x",
                  vehicle: { make_label: "VW", model_label: "Polo", images: [], images_thumbs: [] } } }));
    window.history.replaceState({}, "", `/app/vergleich?url=${encodeURIComponent(KA)}`);
    await act(async () => { wurzel.render(createElement(Vergleich)); });
    await warten();
    expect(api.post.mock.calls.map((c) => c[0])).toContain("/mobile/compare");
    expect(filterOeffnen).not.toHaveBeenCalled();
  });

  it("App schon offen: neues Auto aus dem Programm wird übernommen (ohne Filter)", async () => {
    // Wunsch Ahmad 03.10.2026: Vertrag aus dem Programm in der offenen App — kein neues Fenster
    const { filterOeffnen } = await import("@/lib/filterOeffnen");
    const { INSERAT_EREIGNIS } = await import("@/lib/programmStart");
    window.history.replaceState({}, "", "/app/vergleich");
    await act(async () => { wurzel.render(createElement(Vergleich)); });
    await warten();
    expect(api.post).not.toHaveBeenCalled();
    const neu = "https://www.kleinanzeigen.de/s-anzeige/3530379782";
    await act(async () => { window.dispatchEvent(new CustomEvent(INSERAT_EREIGNIS, { detail: neu })); });
    await warten();
    const vergleich = api.post.mock.calls.find((c) => c[0] === "/mobile/compare");
    expect(vergleich?.[1]).toMatchObject({ url: neu });
    expect(filterOeffnen).not.toHaveBeenCalled();
  });

  it("ohne ?url= startet nichts", async () => {
    window.history.replaceState({}, "", "/app/vergleich");
    await act(async () => { wurzel.render(createElement(Vergleich)); });
    await warten();
    expect(api.post).not.toHaveBeenCalled();
    expect(navMock).not.toHaveBeenCalled();
  });

  it("kaputter Link in der Adresse: Meldung, kein Vergleich", async () => {
    window.history.replaceState({}, "", `/app/vergleich?url=${encodeURIComponent("https://example.com/x")}`);
    await act(async () => { wurzel.render(createElement(Vergleich)); });
    await warten();
    expect(api.post).not.toHaveBeenCalled();
    expect(toastMock.error).toHaveBeenCalledWith(expect.stringContaining("kein gültiger Inserats-Link"));
  });
});
