/**
 * Entscheidung Ahmad 29.10.2026: KI erst einmal fuer alle Nutzer aus — solange der Server /api/features ohne
 * ki: true liefert, erscheint keine der drei KI-Karten (Vertragsdialog, Freigaben, Fahrer-App): kein Knopf, kein Hinweis.
 */
import { act, createElement } from "react";
import { createRoot } from "react-dom/client";
import { afterEach, describe, expect, it, vi } from "vitest";
import { featuresSetzen } from "@/lib/features";

globalThis.IS_REACT_ACT_ENVIRONMENT = true;
vi.mock("sonner", () => ({ toast: { success: vi.fn(), error: vi.fn(), info: vi.fn(), warning: vi.fn() } }));
vi.mock("@/lib/api", () => ({ api: { get: vi.fn(async () => ({ data: {} })), post: vi.fn() }, errMsg: (e, f) => f }));
vi.mock("@/context/DriverContext", () => ({ driverApi: { get: vi.fn(async () => ({ data: {} })) } }));

const { default: KiSchadenKarte } = await import("./KiSchadenKarte");
const { default: KiFahrerKarte } = await import("./KiFahrerKarte");
const { default: KiBewertungKarte } = await import("./KiBewertungKarte");

let wurzel;
let behaelter;
async function rendern(el) {
  behaelter = document.createElement("div");
  document.body.appendChild(behaelter);
  wurzel = createRoot(behaelter);
  await act(async () => { wurzel.render(el); });
}
afterEach(async () => {
  if (wurzel) await act(async () => { wurzel.unmount(); });
  behaelter?.remove();
  wurzel = null;
});

describe("KI aus (Entscheidung 29.10.2026)", () => {
  it("ohne ki in /features bleibt jede Karte leer", async () => {
    featuresSetzen({ ki: false });
    const schaden = [{ type_key: "delle", zone: "Tür vorne links", view: "links", severity_data: { groesse: "klein" } }];
    await rendern(createElement(KiSchadenKarte, { vehicleId: "v1", damages: schaden }));
    expect(behaelter.innerHTML).toBe("");
    await rendern(createElement(KiFahrerKarte, { apptId: "t1", preisVertrag: 8100 }));
    expect(behaelter.innerHTML).toBe("");
    await rendern(createElement(KiBewertungKarte, { eintrag: { protocol_id: "p1", ki_bewertung: { status: "ok" } }, onPreis: vi.fn() }));
    expect(behaelter.innerHTML).toBe("");
  });

  it("mit ki: true erscheint die Fahrer-Karte wieder", async () => {
    featuresSetzen({ ki: true });
    await rendern(createElement(KiFahrerKarte, { apptId: "t1", preisVertrag: 8100 }));
    expect(behaelter.querySelector('[data-testid="protokoll-ki-oeffnen"]')).not.toBeNull();
    featuresSetzen(null);
  });
});
