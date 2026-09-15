/**
 * Pruefung 14.09.2026 (Fahrer-App):
 *  A1  Ein Ladefehler zeigte "Noch keine Fahrten" — jetzt eine Fehlerkarte
 *      mit "Erneut versuchen".
 *  A2  Gelungener Statuswechsel + gescheitertes Nachladen ergab einen
 *      widerspruechlichen Fehler-Toast — jetzt Erfolg + Warnung.
 *  A7  Fahrten eines Tages nach Uhrzeit sortiert.
 */
import { act, createElement } from "react";
import { createRoot } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

globalThis.IS_REACT_ACT_ENVIRONMENT = true;

const api = vi.hoisted(() => ({ get: vi.fn(), post: vi.fn(), put: vi.fn() }));
const toastMock = vi.hoisted(() => ({ error: vi.fn(), success: vi.fn(), warning: vi.fn() }));

vi.mock("@/context/DriverContext", () => ({ driverApi: api, openDriverPdf: vi.fn() }));
vi.mock("sonner", () => ({ toast: toastMock }));
vi.mock("react-router-dom", async () => {
  const { createElement: h } = await import("react");
  return { Link: ({ children, to }) => h("a", { href: String(to) }, children) };
});
vi.mock("@/components/PhotoGallery", () => ({ default: () => null }));
vi.mock("@/components/AbholCheckDialog", () => ({ default: () => null }));

const { default: DriverDashboard } = await import("./DriverDashboard");

let wurzel;
let behaelter;

async function warten() {
  await act(async () => { await new Promise((r) => setTimeout(r, 0)); });
  await act(async () => { await new Promise((r) => setTimeout(r, 0)); });
}

function fahrt(id, zeit) {
  return { id, pickup_date: "2026-09-20", pickup_time: zeit, status: "offen",
           zuteilung: "angenommen", vehicle: { make: "VW", model: "Golf" }, dealer: { name: "Firma" } };
}

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

describe("Fahrer-Startseite (Pruefung 14.09.2026)", () => {
  it("A1: Ladefehler zeigt eine Fehlerkarte statt 'Noch keine Fahrten' und laedt erneut", async () => {
    api.get.mockRejectedValueOnce({ response: { status: 502 } });
    await act(async () => { wurzel.render(createElement(DriverDashboard)); });
    await warten();
    expect(behaelter.textContent).not.toContain("Noch keine Fahrten");
    expect(behaelter.querySelector('[data-testid="driver-ladefehler"]')).not.toBeNull();
    api.get.mockResolvedValueOnce({ data: [] });
    await act(async () => {
      behaelter.querySelector('[data-testid="driver-erneut-laden"]').click();
    });
    await warten();
    expect(behaelter.querySelector('[data-testid="driver-ladefehler"]')).toBeNull();
    expect(behaelter.textContent).toContain("Noch keine Fahrten");
  });

  it("A7: Fahrten eines Tages stehen nach Uhrzeit sortiert", async () => {
    api.get.mockResolvedValueOnce({ data: [fahrt("spaet", "14:00"), fahrt("frueh", "09:00")] });
    await act(async () => { wurzel.render(createElement(DriverDashboard)); });
    await warten();
    const html = behaelter.innerHTML;
    expect(html.indexOf("09:00")).toBeGreaterThan(-1);
    expect(html.indexOf("09:00")).toBeLessThan(html.indexOf("14:00"));
  });

  it("A2: Fahrt annehmen gelingt, Nachladen scheitert -> Erfolg + Warnung, kein Fehler", async () => {
    const offen = { ...fahrt("z1", "10:00"), zuteilung: "offen" };
    api.get.mockResolvedValueOnce({ data: [offen] });
    await act(async () => { wurzel.render(createElement(DriverDashboard)); });
    await warten();
    // Karte aufklappen, dann annehmen
    const kopf = behaelter.querySelector('[data-testid="driver-appt-z1"]')
      || behaelter.querySelector("button");
    await act(async () => { kopf.click(); });
    await warten();
    api.put.mockResolvedValueOnce({ data: { ok: true } });
    api.get.mockRejectedValueOnce(new Error("Netz weg"));
    const knopf = behaelter.querySelector('[data-testid="zuteilung-annehmen-z1"]');
    expect(knopf).not.toBeNull();
    await act(async () => { knopf.click(); });
    await warten();
    expect(toastMock.success).toHaveBeenCalledTimes(1);
    expect(toastMock.warning).toHaveBeenCalledTimes(1);
    expect(toastMock.error).not.toHaveBeenCalled();
  });
});
