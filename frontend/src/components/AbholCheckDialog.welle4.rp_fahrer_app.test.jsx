/**
 * Rollenprüfung 22.09.2026 (Review, Welle 4) — Abhol-Check-Dialog: Der
 * Idempotenz-Schlüssel aus einem alten Zwischenstand gehört zu einem schon
 * gespeicherten Bericht. Antwortet der Server "schon mit anderen Angaben
 * gespeichert" (409), bekommen die geänderten Angaben einen NEUEN Schlüssel —
 * der nächste Tipp meldet sie als neue Version. Andere 409 behalten ihn.
 */
import { act, createElement } from "react";
import { createRoot } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

globalThis.IS_REACT_ACT_ENVIRONMENT = true;

const api = vi.hoisted(() => ({ get: vi.fn(), post: vi.fn(), put: vi.fn() }));
const toastMock = vi.hoisted(() => ({ error: vi.fn(), success: vi.fn(), warning: vi.fn(), info: vi.fn() }));

vi.mock("@/context/DriverContext", () => ({ driverApi: api }));
vi.mock("sonner", () => ({ toast: toastMock }));
vi.mock("react-router-dom", () => ({ useNavigate: () => vi.fn() }));
vi.mock("@/lib/ungespeichert", () => ({ useUngespeichert: () => {} }));

const { default: AbholCheckDialog, istAndererInhalt } = await import("./AbholCheckDialog");

// Wortlaut von BERICHT_ANDERER_INHALT (backend/routes/drivers.py).
const ANDERER_INHALT = "Dieser Abhol-Check wurde schon mit anderen Angaben gespeichert — "
  + "die Änderungen sind noch nicht übernommen. Bitte erneut senden, dann gehen sie als neue Version ein.";
const fehler409 = (detail) => ({ response: { status: 409, data: { detail } } });
const ALT = "alter-schluessel-123";

let wurzel;
let behaelter;

async function warten() {
  await act(async () => { await new Promise((r) => setTimeout(r, 0)); });
  await act(async () => { await new Promise((r) => setTimeout(r, 0)); });
}
const el = (id) => behaelter.querySelector(`[data-testid="${id}"]`);
const entwurf = (id) => JSON.parse(window.sessionStorage.getItem(`ah_abholcheck_${id}`) || "null");

async function oeffnen(appointment) {
  wurzel = createRoot(behaelter);
  await act(async () => {
    wurzel.render(createElement(AbholCheckDialog, { appointment, onClose: vi.fn(), onDone: vi.fn() }));
  });
  await warten();
}
async function absenden() {
  await act(async () => { el("abholcheck-absenden").click(); });
  await warten();
}

beforeEach(() => {
  vi.clearAllMocks();
  window.sessionStorage.clear();
  behaelter = document.createElement("div");
  document.body.appendChild(behaelter);
  api.get.mockResolvedValue({ data: { protocol: { status: "final", condition: {} } } });
  api.put.mockResolvedValue({ data: { ok: true } });
});

afterEach(async () => {
  if (wurzel) await act(async () => { wurzel.unmount(); });
  wurzel = null;
  behaelter.remove();
});

describe("Review: alter Schlüssel mit geänderten Angaben", () => {
  it("409 'anderer Inhalt' -> neuer Schlüssel, der nächste Tipp sendet damit", async () => {
    window.sessionStorage.setItem("ah_abholcheck_k7",
      JSON.stringify({ mileage: "90.000", berichtId: ALT }));
    api.post.mockRejectedValueOnce(fehler409(ANDERER_INHALT))
      .mockResolvedValueOnce({ data: { ok: true, version: 2 } });
    await oeffnen({ id: "k7", status: "abgeholt", title: "Golf" });
    await absenden();
    expect(api.post.mock.calls[0][1].client_bericht_id).toBe(ALT);
    expect(toastMock.error).toHaveBeenCalledWith(ANDERER_INHALT);
    const neu = entwurf("k7").berichtId;
    expect(neu).toBeTruthy();
    expect(neu).not.toBe(ALT);
    expect(entwurf("k7").mileage).toBe("90.000");
    await absenden();
    expect(api.post).toHaveBeenCalledTimes(2);
    expect(api.post.mock.calls[1][1].client_bericht_id).toBe(neu);
    expect(api.post.mock.calls[1][1].mileage_at_pickup).toBe(90000);
  });

  it("anderer 409 (wird gerade gespeichert) behält den Schlüssel", async () => {
    window.sessionStorage.setItem("ah_abholcheck_k8",
      JSON.stringify({ mileage: "1000", berichtId: ALT }));
    api.post.mockRejectedValue(fehler409("Dieser Abhol-Check wird gerade noch gespeichert — "
      + "bitte einen Moment warten und die Fahrten neu laden."));
    await oeffnen({ id: "k8", status: "abgeholt", title: "Golf" });
    await absenden();
    await absenden();
    expect(api.post.mock.calls.map(([, b]) => b.client_bericht_id)).toEqual([ALT, ALT]);
  });

  it("istAndererInhalt erkennt nur 409 mit dem Text", () => {
    expect(istAndererInhalt(fehler409(ANDERER_INHALT))).toBe(true);
    expect(istAndererInhalt({ response: { status: 400, data: { detail: ANDERER_INHALT } } })).toBe(false);
    expect(istAndererInhalt(fehler409("Termin ist bereits 'abgeholt'"))).toBe(false);
    expect(istAndererInhalt(new Error("Network Error"))).toBe(false);
  });
});
