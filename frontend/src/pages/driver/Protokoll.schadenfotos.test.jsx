/**
 * Wunsch Ahmad 06.10.2026: Fotos und Lackdicke im Fahrer-Protokoll (Protokoll.jsx / protokollEntwurf.js).
 *  - Lackdicke-Messungen gehören zum Entwurf (Autosave, Zusammenführung wie alle Felder)
 *  - ein Foto: erst den wartenden Stand speichern (die Markierung muss beim Server sein), dann hochladen
 *  - Fehler beim Hochladen: Hinweis, weitere Fotos werden nicht versucht
 *  - Entfernen mit Rückfrage; Abschicken nur mit Wert bei jeder Lackdicke-Messung
 */
import { act, createElement } from "react";
import { createRoot } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

globalThis.IS_REACT_ACT_ENVIRONMENT = true;

const api = vi.hoisted(() => ({ get: vi.fn(), post: vi.fn(), put: vi.fn(), delete: vi.fn() }));
const toastMock = vi.hoisted(() => ({ error: vi.fn(), success: vi.fn(), warning: vi.fn(), info: vi.fn() }));
const skizze = vi.hoisted(() => ({ props: null }));

vi.mock("@/context/DriverContext", () => ({ driverApi: api, openDriverPdf: vi.fn() }));
vi.mock("sonner", () => ({ toast: toastMock }));
vi.mock("react-router-dom", () => ({ useParams: () => ({ id: "t1" }), useNavigate: () => vi.fn() }));
vi.mock("@/lib/ungespeichert", () => ({ useUngespeichert: () => {} }));
vi.mock("@/lib/bilder", () => ({ verkleinereBildDatei: vi.fn().mockResolvedValue("data:image/jpeg;base64,QUJD") }));
vi.mock("@/components/DamageSelector", () => ({ default: (p) => { skizze.props = p; return null; } }));
vi.mock("@/components/MonatJahrEingabe", () => ({ default: () => null }));
vi.mock("@/components/SignaturePad", () => ({ default: () => null }));

const { default: Protokoll } = await import("./Protokoll");
const { LEERER_ENTWURF, entwurfAusServer } = await import("./protokollEntwurf");

const LACK = { id: "l1", view: "top", zone: "Dach", x: 700, y: 300 };
function antwort(protokoll = {}) {
  return {
    data: {
      protocol: { id: "p1", version: 1, status: "entwurf", revision: 1, rueckfrage: "",
                  new_damages: [{ id: "s1", type_label: "Kratzer", zone: "Motorhaube", view: "front" }],
                  lackmessungen: [{ ...LACK, wert_um: 140 }],
                  schaden_fotos: [{ id: "f1", schaden_id: "s1", erstellt_am: "2026-10-06T10:00:00+00:00" }],
                  ...protokoll },
      template: { schadenfoto_max: 25, schadenfoto_sicht_tage: 7 }, vehicle: {}, damages: [], preis_vertrag: 10000,
      appointment: { seller_name: "Vera", status: "offen" },
    },
  };
}

let wurzel;
let behaelter;
const el = (id) => behaelter.querySelector(`[data-testid="${id}"]`);
async function warten() {
  for (let i = 0; i < 3; i += 1) await act(async () => { await new Promise((r) => setTimeout(r, 0)); });
}
async function starten(daten) {
  api.get.mockResolvedValue(daten);
  behaelter = document.createElement("div");
  document.body.appendChild(behaelter);
  wurzel = createRoot(behaelter);
  await act(async () => { wurzel.render(createElement(Protokoll)); });
  await warten();
}

beforeEach(() => {
  try { window.sessionStorage.clear(); } catch { /* egal */ }
  skizze.props = null;
  api.put.mockResolvedValue({ data: { revision: 2 } });
  api.post.mockResolvedValue({ data: { id: "f2", schaden_id: "s1", erstellt_am: "2026-10-06T11:00:00+00:00" } });
  api.delete.mockResolvedValue({ data: { ok: true } });
});
afterEach(async () => {
  if (wurzel) await act(async () => { wurzel.unmount(); });
  wurzel = null;
  behaelter?.remove();
  vi.clearAllMocks();
});

describe("Entwurf: Lackdicke", () => {
  it("gehört zum leeren Entwurf und kommt vom Server", () => {
    expect(LEERER_ENTWURF.lackmessungen).toEqual([]);
    expect(entwurfAusServer({ lackmessungen: [LACK] }).lackmessungen).toEqual([LACK]);
    expect(entwurfAusServer({}).lackmessungen).toEqual([]);
  });
});

describe("Fotos im Protokoll", () => {
  it("die Skizze bekommt Messungen, Fotos, Grenze und den Fahrer-Weg", async () => {
    await starten(antwort());
    const p = skizze.props;
    expect(p.lackMessungen).toEqual([{ ...LACK, wert_um: 140 }]);
    expect(p.fotos.liste.map((x) => x.id)).toEqual(["f1"]);
    expect(p.fotos.max).toBe(25);
    expect(p.fotos.sichtTage).toBe(7);
    expect(p.fotos.pfad("f1")).toBe("/driver/appointments/t1/protocol/schaden-fotos/f1");
    expect(p.fotos.client).toBe(api);
  });

  it("Foto: erst speichern, dann hochladen; danach steht es in der Liste", async () => {
    await starten(antwort());
    await act(async () => { skizze.props.onLackChange([{ ...LACK, wert_um: 150 }]); });
    let ok;
    await act(async () => { ok = await skizze.props.fotos.onHinzu("s1", new File(["x"], "x.jpg")); });
    expect(ok).toBe(true);
    expect(api.put).toHaveBeenCalled();
    expect(api.put.mock.invocationCallOrder[0]).toBeLessThan(api.post.mock.invocationCallOrder[0]);
    expect(api.put.mock.calls[0][1].lackmessungen).toEqual([{ ...LACK, wert_um: 150 }]);
    expect(api.post).toHaveBeenCalledWith("/driver/appointments/t1/protocol/schaden-fotos",
                                          { schaden_id: "s1", photo_b64: "data:image/jpeg;base64,QUJD" });
    expect(skizze.props.fotos.liste.map((x) => x.id)).toEqual(["f1", "f2"]);
  });

  it("Fehler beim Hochladen: Hinweis und false (weitere Fotos werden nicht versucht)", async () => {
    await starten(antwort());
    api.post.mockRejectedValueOnce({ response: { status: 409, data: { detail: "Höchstens 25 Schadenfotos" } },
                                     message: "Höchstens 25 Schadenfotos" });
    let ok;
    await act(async () => { ok = await skizze.props.fotos.onHinzu("s1", new File(["x"], "x.jpg")); });
    expect(ok).toBe(false);
    expect(toastMock.error).toHaveBeenCalled();
    expect(skizze.props.fotos.liste).toHaveLength(1);
  });

  it("Entfernen mit Rückfrage", async () => {
    await starten(antwort());
    const nein = vi.spyOn(window, "confirm").mockReturnValueOnce(false).mockReturnValueOnce(true);
    await act(async () => { await skizze.props.fotos.onWeg("f1"); });
    expect(api.delete).not.toHaveBeenCalled();
    await act(async () => { await skizze.props.fotos.onWeg("f1"); });
    expect(api.delete).toHaveBeenCalledWith("/driver/appointments/t1/protocol/schaden-fotos/f1");
    expect(skizze.props.fotos.liste).toEqual([]);
    nein.mockRestore();
  });

  it("Abschicken nur mit Wert bei jeder Lackdicke-Messung", async () => {
    await starten(antwort({ lackmessungen: [{ ...LACK }] }));
    await act(async () => { el("protokoll-zur-freigabe").dispatchEvent(new MouseEvent("click", { bubbles: true })); });
    const meldung = toastMock.error.mock.calls.map((c) => c[0]).join(" ");
    expect(meldung).toContain("Lackdicke");
    expect(meldung).toContain("Dach");
    expect(api.post).not.toHaveBeenCalledWith("/driver/appointments/t1/protocol/submit", expect.anything());
  });

  it("gesperrt (beim Chef): Messungen und Foto-Zahl als Text", async () => {
    await starten(antwort({ status: "zur_freigabe" }));
    const text = el("protokoll-vorort-gesperrt").textContent;
    expect(text).toContain("Kratzer — Motorhaube");
    expect(text).toContain("1 Foto(s)");
    expect(text).toContain("Lackdicke — Dach: 140 µm");
  });
});
