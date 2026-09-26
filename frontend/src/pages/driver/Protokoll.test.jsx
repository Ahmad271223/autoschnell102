/**
 * Go-Live 13.09.2026 (P4): Unterschriften, die unter einem ALTEN Freigabe-Stand
 * geleistet wurden, duerfen nach Rueckfrage und erneuter Freigabe nicht still
 * mit dem neuen Preis abgeschickt werden.
 *
 * Ablauf vorher: freigegeben (s1, 9.000) -> beide unterschreiben -> Chef schickt
 * zurueck (entwurf) -> Fahrer schickt erneut ab -> Chef gibt mit 8.000 frei (s3).
 * Die Kennung war beim Entwurf auf null zurueckgesetzt worden, der Wechsel
 * null -> s3 loeschte nichts: "abschliessen" schickte die alten PNGs.
 */
import { act, createElement } from "react";
import { createRoot } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

globalThis.IS_REACT_ACT_ENVIRONMENT = true;

const api = vi.hoisted(() => ({ get: vi.fn(), post: vi.fn(), put: vi.fn() }));
const toastMock = vi.hoisted(() => ({ error: vi.fn(), success: vi.fn(), warning: vi.fn() }));

vi.mock("@/context/DriverContext", () => ({ driverApi: api, openDriverPdf: vi.fn() }));
vi.mock("sonner", () => ({ toast: toastMock }));
vi.mock("react-router-dom", () => ({ useParams: () => ({ id: "t1" }), useNavigate: () => vi.fn() }));
vi.mock("@/lib/ungespeichert", () => ({ useUngespeichert: () => {} }));
vi.mock("@/components/DamageSelector", () => ({ default: () => null }));
vi.mock("@/components/MonatJahrEingabe", () => ({ default: () => null }));
// Unterschriftsfeld: ein Knopf, der eine "Unterschrift" meldet.
vi.mock("@/components/SignaturePad", async () => {
  const { createElement: h } = await import("react");
  return {
    default: ({ label, onChange }) => h("button", {
      type: "button", "data-testid": `sig-${label}`,
      onClick: () => onChange(`data:image/png;base64,${label.replace(/\W/g, "")}`),
    }, label),
  };
});

const { default: Protokoll } = await import("./Protokoll");

function antwort(status, stand, preis = null) {
  return {
    data: {
      protocol: { id: "p1", version: 1, status, freigabe_stand: stand, neuer_preis: preis,
                  preis_notiz: "", rueckfrage: status === "entwurf" ? "Bitte Schluessel pruefen" : "" },
      template: {}, vehicle: {}, appointment: { seller_name: "Vera" }, damages: [],
      preis_vertrag: 10000,
    },
  };
}

let wurzel;
let behaelter;

async function warten() {
  await act(async () => { await new Promise((r) => setTimeout(r, 0)); });
  await act(async () => { await new Promise((r) => setTimeout(r, 0)); });
}

async function klick(testId) {
  const el = behaelter.querySelector(`[data-testid="${testId}"]`);
  if (!el) throw new Error(`nicht gefunden: ${testId}`);
  await act(async () => { el.click(); });
  await warten();
}

async function starten(antworten) {
  const liste = [...antworten];
  api.get.mockImplementation(async () => (liste.length > 1 ? liste.shift() : liste[0]));
  behaelter = document.createElement("div");
  document.body.appendChild(behaelter);
  wurzel = createRoot(behaelter);
  await act(async () => { wurzel.render(createElement(Protokoll)); });
  await warten();
}

const konflikt = () => Promise.reject(Object.assign(new Error("409"), { response: { status: 409 } }));
const finalizeAufrufe = () => api.post.mock.calls.filter(([url]) => url.endsWith("/protocol/finalize"));

beforeEach(() => {
  // Rollenprüfung 22.09.2026 (RP-546): die Seite sichert offene Stände im Tab
  // (sessionStorage) — jeder Test beginnt ohne Sicherung.
  try { window.sessionStorage.clear(); } catch { /* egal */ }
  vi.spyOn(window, "confirm").mockReturnValue(true);
  api.put.mockResolvedValue({ data: {} });
});

afterEach(async () => {
  await act(async () => { wurzel.unmount(); });
  behaelter.remove();
  vi.useRealTimers();
  vi.clearAllMocks();
  vi.restoreAllMocks();
});

describe("Protokoll.jsx — Unterschriften und Freigabe-Stand (Go-Live 13.09.2026, P4)", () => {
  it("nach Rueckfrage und erneuter Freigabe muessen beide neu unterschreiben", async () => {
    await starten([
      antwort("freigegeben", "s1", 9000),
      antwort("entwurf", "s2"),       // Chef hat zurueckgeschickt
      antwort("zur_freigabe", "s3"),  // Fahrer hat erneut abgeschickt
      antwort("freigegeben", "s4", 8000),
    ]);
    await klick("sig-Unterschrift Verkäufer");
    await klick("sig-Unterschrift Fahrer");

    api.post.mockImplementationOnce(konflikt);       // Abschluss: 409, neu laden -> entwurf
    await klick("protokoll-abschliessen");
    expect(behaelter.querySelector('[data-testid="protokoll-rueckfrage"]')).not.toBeNull();

    api.post.mockResolvedValue({ data: { ok: true } });
    await klick("protokoll-zur-freigabe");           // -> zur_freigabe
    await klick("protokoll-warten-aktualisieren");   // -> freigegeben mit 8.000
    expect(behaelter.querySelector('[data-testid="protokoll-neuer-preis"]').textContent).toContain("8");

    const vorher = finalizeAufrufe().length;
    await klick("protokoll-abschliessen");
    expect(finalizeAufrufe()).toHaveLength(vorher);
    expect(toastMock.error).toHaveBeenCalledWith("Bitte beide Unterschriften erfassen");
    expect(toastMock.warning).toHaveBeenCalledTimes(1);
  });

  it("gescheiterter Abschluss mit gleichem Stand behaelt die Unterschriften", async () => {
    await starten([
      antwort("freigegeben", "s1", 9000),
      antwort("wird_abgeschlossen", "s1", 9000),
      antwort("freigegeben", "s1", 9000),
    ]);
    await klick("sig-Unterschrift Verkäufer");
    await klick("sig-Unterschrift Fahrer");
    api.post.mockImplementationOnce(konflikt);
    await klick("protokoll-abschliessen");              // -> wird_abgeschlossen
    await klick("protokoll-abschluss-aktualisieren");   // -> wieder freigegeben (Rollback)

    api.post.mockResolvedValue({ data: { ok: true } });
    await klick("protokoll-abschliessen");
    const [, body] = finalizeAufrufe().at(-1);
    expect(body.signature_seller_b64).toContain("UnterschriftVerk");
    expect(body.signature_driver_b64).toContain("UnterschriftFahrer");
    expect(body.freigabe_stand_gesehen).toBe("s1");
    expect(toastMock.warning).not.toHaveBeenCalled();
  });

  it("ohne Unterschriften kein Hinweis, wenn der Chef den Preis aendert", async () => {
    // Nachladen alle 15 s: nur setInterval faelschen, setTimeout bleibt echt.
    vi.useFakeTimers({ toFake: ["setInterval", "clearInterval"] });
    vi.spyOn(document, "visibilityState", "get").mockReturnValue("visible");
    await starten([
      antwort("freigegeben", "s1", 9000),
      antwort("freigegeben", "s2", 8000),
    ]);
    await act(async () => { vi.advanceTimersByTime(15000); });
    await warten();
    expect(behaelter.querySelector('[data-testid="protokoll-neuer-preis"]').textContent).toContain("8");
    expect(toastMock.warning).not.toHaveBeenCalled();

    // Danach wird normal unterschrieben — mit dem neuen Stand.
    await klick("sig-Unterschrift Verkäufer");
    await klick("sig-Unterschrift Fahrer");
    api.post.mockResolvedValue({ data: { ok: true } });
    await klick("protokoll-abschliessen");
    const [, body] = finalizeAufrufe().at(-1);
    expect(body.freigabe_stand_gesehen).toBe("s2");
    expect(body.neuer_preis_gesehen).toBe(8000);
  });
});
