// Chat Büro/Fahrer (24.11.2026): Zähler, Verlauf, Gelesen-Haken, Schnellantwort, Senden.
import { act, createElement } from "react";
import { createRoot } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

globalThis.IS_REACT_ACT_ENVIRONMENT = true;

const api = vi.hoisted(() => ({ get: vi.fn(), post: vi.fn() }));
const driverApi = vi.hoisted(() => ({ get: vi.fn(), post: vi.fn() }));
vi.mock("@/lib/api", () => ({ api, errMsg: (e, f) => f, openAuthedFile: vi.fn(() => Promise.resolve()) }));
vi.mock("@/context/DriverContext", () => ({ driverApi }));
vi.mock("sonner", () => ({ toast: { error: vi.fn(), success: vi.fn() } }));

const { default: AuftragChat, ChatNeu, gelesenText, zeitKurz, chatPfad } = await import("./AuftragChat");
const { standAusAntwort, _zuruecksetzen } = await import("@/lib/chatZaehler");

let wurzel;
let behaelter;
async function warten() {
  for (let i = 0; i < 3; i += 1) {
    await act(async () => { await new Promise((r) => setTimeout(r, 0)); });
  }
}
async function zeigen(komp, props) {
  behaelter = document.createElement("div");
  document.body.appendChild(behaelter);
  wurzel = createRoot(behaelter);
  await act(async () => { wurzel.render(createElement(komp, props)); });
  await warten();
}
const el = (id) => behaelter.querySelector(`[data-testid="${id}"]`);
async function klick(knopf) {
  if (!knopf) throw new Error("Knopf fehlt");
  await act(async () => { knopf.click(); });
  await warten();
}
function tippen(feld, wert) {
  const setter = Object.getOwnPropertyDescriptor(window.HTMLTextAreaElement.prototype, "value").set;
  act(() => { setter.call(feld, wert); feld.dispatchEvent(new Event("input", { bubbles: true })); });
}
const knopfMitText = (t) => [...behaelter.querySelectorAll("button")].find((b) => b.textContent.trim() === t);

afterEach(() => {
  act(() => wurzel?.unmount());
  behaelter?.remove();
});

const VERLAUF = {
  nachrichten: [
    { id: "m1", von: "buero", von_name: "Carla", text: "Melde mich gleich", eigene: true,
      erstellt_am: "2026-11-24T13:30:00Z", gelesen_am: "2026-11-24T13:36:00Z" },
    { id: "m2", von: "fahrer", von_name: "Fahrer Eins", text: "Bin vor Ort", eigene: false,
      erstellt_am: "2026-11-24T13:40:00Z", gelesen_am: null },
  ],
  kann_schreiben: true, schnellantworten: ["Melde mich gleich", "Passt so"], fahrer: { name: "Fahrer Eins" },
};

beforeEach(() => {
  _zuruecksetzen();
  [api, driverApi].forEach((c) => { c.get.mockReset(); c.post.mockReset(); });
});

describe("Hilfen", () => {
  it("Pfade je Rolle", () => {
    expect(chatPfad("buero", "a1")).toBe("/appointments/a1/chat");
    expect(chatPfad("fahrer", "a1")).toBe("/driver/appointments/a1/chat");
  });
  it("Gelesen-Haken unter der letzten eigenen Nachricht", () => {
    const h = gelesenText(VERLAUF.nachrichten);
    expect(h.id).toBe("m1");
    expect(h.text).toMatch(/^Gelesen \d\d:\d\d$/);
    expect(gelesenText([{ id: "x", eigene: true, gelesen_am: null }]).text).toBe("Gesendet");
    expect(gelesenText([{ id: "y", eigene: false }])).toBeNull();
  });
  it("Zeit kurz: heute nur Uhrzeit, sonst mit Datum", () => {
    const jetzt = new Date("2026-11-24T15:00:00");
    expect(zeitKurz("2026-11-24T14:36:00", jetzt)).toBe("14:36");
    expect(zeitKurz("2026-11-20T09:05:00", jetzt)).toBe("20.11. 09:05");
    expect(zeitKurz("kaputt", jetzt)).toBe("");
  });
  it("Zähler-Antwort wird gesäubert", () => {
    expect(standAusAntwort({ termine: { a: 2, b: 0, c: "x", d: 1 } })).toEqual({ termine: { a: 2, d: 1 }, gesamt: 3 });
    expect(standAusAntwort(null)).toEqual({ termine: {}, gesamt: 0 });
  });
});

describe("AuftragChat", () => {
  it("zeigt zugeklappt den Zähler „2 neu“ (Fahrer-Seite)", async () => {
    driverApi.get.mockImplementation((url) => Promise.resolve(
      url === "/driver/chat/ungelesen" ? { data: { termine: { a1: 2 }, gesamt: 2 } } : { data: {} }));
    await zeigen(AuftragChat, { rolle: "fahrer", terminId: "a1" });
    expect(el("chat-zaehler-a1").textContent).toContain("2 neu");
    expect(el("chat-oeffnen-a1").textContent).toContain("Chat mit Büro");
  });

  it("öffnet, zeigt Verlauf und Gelesen-Haken, Schnellantwort sendet mit einem Tipp", async () => {
    api.get.mockImplementation((url) => Promise.resolve(
      url === "/chat/ungelesen" ? { data: { termine: {}, gesamt: 0 } } : { data: VERLAUF }));
    api.post.mockResolvedValue({ data: { id: "m3", von: "buero", text: "Passt so", eigene: true,
                                         erstellt_am: "2026-11-24T13:50:00Z", gelesen_am: null } });
    await zeigen(AuftragChat, { rolle: "buero", terminId: "a1" });
    await klick(el("chat-oeffnen-a1"));
    expect(el("chat-nachricht-m2").textContent).toContain("Bin vor Ort");
    expect(el("chat-gelesen").textContent).toMatch(/^Gelesen/);
    expect(api.get).toHaveBeenCalledWith("/appointments/a1/chat");
    await klick(knopfMitText("Passt so"));
    expect(api.post).toHaveBeenCalledWith("/appointments/a1/chat", { text: "Passt so", schnell: true });
    expect(el("chat-gelesen").textContent).toContain("Gesendet");
  });

  it("eigener Text: Senden schickt getrimmt und leert das Feld", async () => {
    api.get.mockImplementation((url) => Promise.resolve(
      url === "/chat/ungelesen" ? { data: { termine: {} } } : { data: { ...VERLAUF, nachrichten: [] } }));
    api.post.mockResolvedValue({ data: { id: "m9", von: "buero", text: "Hallo", eigene: true } });
    await zeigen(AuftragChat, { rolle: "buero", terminId: "a2", startOffen: true });
    tippen(el("chat-eingabe-a2"), "  Hallo  ");
    await klick(el("chat-senden-a2"));
    expect(api.post).toHaveBeenCalledWith("/appointments/a2/chat", { text: "Hallo", schnell: false });
    expect(el("chat-eingabe-a2").value).toBe("");
  });

  it("ohne Fahrer: Hinweis statt Eingabe", async () => {
    api.get.mockImplementation((url) => Promise.resolve(
      url === "/chat/ungelesen" ? { data: { termine: {} } }
        : { data: { nachrichten: [], kann_schreiben: false, hinweis: "Diesem Termin ist noch kein Fahrer zugeteilt." } }));
    await zeigen(AuftragChat, { rolle: "buero", terminId: "a3", startOffen: true });
    expect(el("chat-hinweis").textContent).toContain("kein Fahrer");
    expect(el("chat-eingabe-a3")).toBeNull();
  });

  it("ChatNeu zeigt nichts bei 0", async () => {
    api.get.mockResolvedValue({ data: { termine: { b: 1 } } });
    await zeigen(ChatNeu, { rolle: "buero", terminId: "a" });
    expect(api.get).toHaveBeenCalled();
    expect(behaelter.innerHTML).toBe("");
  });
});
