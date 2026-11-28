/**
 * Rollenprüfung 22.09.2026 — Startseite der Fahrer-App (DriverDashboard.jsx).
 *  RP-064/RP-163  "Abgeholt" prüft ERST das Protokoll, dann erst der Abhol-Check.
 *  RP-235         Stornierte Fahrten: keine Status-Knöpfe, kein Protokoll/PDF.
 *  RP-236         Hinweis "Notizen nach der Annahme".
 *  RP-464         Liste lädt still nach (Takt + Sichtbarkeit).
 *  RP-537         "Nicht abgeholt" nur mit Grund, der Grund geht mit.
 */
import { act, createElement } from "react";
import { createRoot } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

globalThis.IS_REACT_ACT_ENVIRONMENT = true;

const api = vi.hoisted(() => ({ get: vi.fn(), post: vi.fn(), put: vi.fn() }));
const toastMock = vi.hoisted(() => ({ error: vi.fn(), success: vi.fn(), warning: vi.fn(), info: vi.fn() }));
const navSpy = vi.hoisted(() => vi.fn());

vi.mock("@/context/DriverContext", () => ({ driverApi: api, openDriverPdf: vi.fn() }));
vi.mock("sonner", () => ({ toast: toastMock }));
vi.mock("react-router-dom", async () => {
  const { createElement: h } = await import("react");
  return { Link: ({ children, to, ...rest }) => h("a", { href: String(to), ...rest }, children),
           useNavigate: () => navSpy };
});
vi.mock("@/components/PhotoGallery", () => ({ default: () => null }));
vi.mock("@/components/AbholCheckDialog", async () => {
  const { createElement: h } = await import("react");
  return { default: () => h("div", { "data-testid": "abholcheck-offen" }) };
});

const { default: DriverDashboard } = await import("./DriverDashboard");

let wurzel;
let behaelter;
let fahrten;
let protokoll;

async function warten() {
  await act(async () => { await new Promise((r) => setTimeout(r, 0)); });
  await act(async () => { await new Promise((r) => setTimeout(r, 0)); });
}
const el = (id) => behaelter.querySelector(`[data-testid="${id}"]`);
async function klick(id) {
  const k = el(id);
  if (!k) throw new Error(`nicht gefunden: ${id}`);
  await act(async () => { k.click(); });
  await warten();
}
async function aufklappen(id) {
  await act(async () => { el(`appt-${id}`).querySelector("button").click(); });
  await warten();
}

function fahrt(id, extra = {}) {
  return { id, title: `Fahrt ${id}`, pickup_date: "2026-09-25", pickup_time: "10:00",
           status: "offen", zuteilung: "angenommen", updated_at: "u1",
           vehicle: { make: "VW", model: "Golf" }, dealer: { name: "Firma" }, ...extra };
}

async function starten() {
  await act(async () => { wurzel.render(createElement(DriverDashboard)); });
  await warten();
}

beforeEach(() => {
  vi.clearAllMocks();
  fahrten = [fahrt("a1")];
  protokoll = { status: "entwurf" };
  api.get.mockImplementation(async (url) => (url.endsWith("/protocol")
    ? { data: { protocol: protokoll } } : { data: fahrten }));
  api.put.mockResolvedValue({ data: { ok: true } });
  behaelter = document.createElement("div");
  document.body.appendChild(behaelter);
  wurzel = createRoot(behaelter);
});

afterEach(async () => {
  await act(async () => { wurzel.unmount(); });
  behaelter.remove();
  vi.useRealTimers();
});

describe("RP-064: Abhol-Check erst nach unterschriebenem Protokoll", () => {
  it("ohne finales Protokoll direkt zum Protokoll, kein Dialog", async () => {
    await starten();
    await aufklappen("a1");
    await klick("mark-pickedup-a1");
    expect(navSpy).toHaveBeenCalledWith("/fahrer/protokoll/a1");
    expect(el("abholcheck-offen")).toBeNull();
    expect(toastMock.warning).toHaveBeenCalledTimes(1);
  });

  it("mit finalem Protokoll öffnet der Abhol-Check", async () => {
    protokoll = { status: "final" };
    await starten();
    await aufklappen("a1");
    await klick("mark-pickedup-a1");
    expect(navSpy).not.toHaveBeenCalled();
    expect(el("abholcheck-offen")).not.toBeNull();
  });
});

describe("RP-537: Nicht abgeholt nur mit Grund", () => {
  it("Grund wählen, Sonstiges braucht Text, Grund geht als Notiz mit", async () => {
    await starten();
    await aufklappen("a1");
    await klick("mark-notpickedup-a1");
    expect(el("nicht-abgeholt-dialog")).not.toBeNull();
    expect(el("nicht-abgeholt-senden").disabled).toBe(true);
    await klick("nicht-abgeholt-grund-sonstiges");
    expect(el("nicht-abgeholt-senden").disabled).toBe(true);
    const feld = el("nicht-abgeholt-text");
    const setter = Object.getOwnPropertyDescriptor(window.HTMLTextAreaElement.prototype, "value").set;
    await act(async () => {
      setter.call(feld, "Reifen platt");
      feld.dispatchEvent(new Event("input", { bubbles: true }));
    });
    expect(el("nicht-abgeholt-senden").disabled).toBe(false);
    await klick("nicht-abgeholt-senden");
    expect(api.put).toHaveBeenCalledWith("/driver/appointments/a1/status", {
      status: "nicht abgeholt", notes: "Nicht abgeholt: Sonstiges — Reifen platt", stand: "u1" });
    expect(el("nicht-abgeholt-dialog")).toBeNull();
  });
});

describe("RP-235/RP-236: stornierte Fahrt und Notizen vor der Annahme", () => {
  it("storniert: keine Aktionen, kein Protokoll, kein Papier-PDF", async () => {
    fahrten = [fahrt("s1", { status: "storniert" })];
    await starten();
    await aufklappen("s1");
    expect(el("mark-pickedup-s1")).toBeNull();
    expect(el("mark-notpickedup-s1")).toBeNull();
    expect(el("protokoll-s1")).toBeNull();
    expect(el("pickup-pdf-s1")).toBeNull();
    expect(el("fahrt-geschlossen-s1").textContent).toContain("storniert");
  });

  it("vor der Annahme: Hinweis auf Notizen statt Text", async () => {
    fahrten = [fahrt("v1", { zuteilung: "offen", notizen_nach_annahme: true, notes: null })];
    await starten();
    await aufklappen("v1");
    expect(el("notizen-nach-annahme-v1")).not.toBeNull();
  });
});

describe("RP-464: still nachladen", () => {
  it("im Takt und beim Sichtbarwerden", async () => {
    vi.useFakeTimers({ toFake: ["setInterval", "clearInterval"] });
    vi.spyOn(document, "visibilityState", "get").mockReturnValue("visible");
    await starten();
    const listenAbrufe = () => api.get.mock.calls.filter(([u]) => u === "/driver/appointments").length;
    expect(listenAbrufe()).toBe(1);
    fahrten = [fahrt("a1"), fahrt("neu", { pickup_time: "12:00" })];
    await act(async () => { vi.advanceTimersByTime(60000); });
    await warten();
    expect(listenAbrufe()).toBe(2);
    expect(el("appt-neu")).not.toBeNull();
    await act(async () => { document.dispatchEvent(new Event("visibilitychange")); });
    await warten();
    expect(listenAbrufe()).toBe(3);
  });
});
