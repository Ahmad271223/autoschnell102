/**
 * Wunsch Ahmad 26.09.2026 abends: Die Kundennummer für Verträge setzt die
 * Firma selbst (Chef UND Sucher) — App → Einstellungen, eigenes Feld mit
 * eigenem Speichern (PUT /dealer/vertrags-kundennummer), 409-Text unter
 * dem Feld, frischer Serverstand übernimmt, solange nichts getippt ist.
 */
import { act, createElement } from "react";
import { createRoot } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

const put = vi.fn();
const toast = { success: vi.fn(), error: vi.fn(), info: vi.fn(), warning: vi.fn(), message: vi.fn() };
vi.mock("sonner", () => ({ toast }));
vi.mock("@/lib/api", () => ({
  api: { get: vi.fn(), post: vi.fn(), put: (...a) => put(...a), delete: vi.fn() },
  errMsg: (e, f) => e?.response?.data?.detail || e?.message || f,
}));
vi.mock("@/context/AuthContext", () => ({ useAuth: () => ({ user: { id: "u1", role: "sucher" } }) }));
vi.mock("@/lib/features", () => ({ useFeatures: () => ({ marktplatz: false }) }));
vi.mock("@/lib/ungespeichert", () => ({ ungespeichertMelden: () => undefined }));

globalThis.IS_REACT_ACT_ENVIRONMENT = true;

const { VertragsKundennummerFeld } = await import("./Einstellungen");
const { default: QUELLE } = await import("./Einstellungen.jsx?raw");

let wurzel = null;
let behaelter = null;
function rendern(el) {
  behaelter = document.createElement("div");
  document.body.appendChild(behaelter);
  wurzel = createRoot(behaelter);
  act(() => { wurzel.render(el); });
  return behaelter;
}
afterEach(() => {
  if (wurzel) act(() => wurzel.unmount());
  behaelter?.remove();
  wurzel = null;
  behaelter = null;
});
beforeEach(() => {
  put.mockReset();
  toast.success.mockReset();
  toast.error.mockReset();
});

function tippen(input, wert) {
  const setter = Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, "value").set;
  act(() => {
    setter.call(input, wert);
    input.dispatchEvent(new Event("input", { bubbles: true }));
  });
}
const feld = (id) => behaelter.querySelector(`[data-testid="${id}"]`);
const klick = async (el) => {
  await act(async () => {
    el.dispatchEvent(new MouseEvent("click", { bubbles: true }));
    await Promise.resolve();
    await Promise.resolve();
  });
};

describe("Einstellungen: Kundennummer für Verträge selbst setzen", () => {
  it("Feld ist editierbar, vorbelegt; Speichern nur bei Änderung, ruft die eigene Route", async () => {
    const refresh = vi.fn().mockResolvedValue({});
    put.mockResolvedValue({ data: { vertrags_kundennummer: "AH-2026", geaendert: true } });
    rendern(createElement(VertragsKundennummerFeld, { dealer: { vertrags_kundennummer: "482913" }, refresh }));
    const input = feld("set-vertrags-kundennummer");
    const knopf = feld("set-vertrags-kundennummer-speichern");
    expect(input.value).toBe("482913");
    expect(input.disabled).toBe(false);
    expect(input.maxLength).toBe(20);
    expect(knopf.disabled, "unverändert: nichts zu speichern").toBe(true);
    tippen(input, " AH-2026 ");
    expect(knopf.disabled).toBe(false);
    await klick(knopf);
    expect(put).toHaveBeenCalledWith("/dealer/vertrags-kundennummer", { vertrags_kundennummer: "AH-2026" });
    expect(refresh).toHaveBeenCalledTimes(1);
    expect(toast.success).toHaveBeenCalled();
    expect(feld("set-vertrags-kundennummer-fehler")).toBeNull();
  });
  it("409 vom Server steht unter dem Feld, verschwindet beim Weitertippen; kein refresh", async () => {
    const refresh = vi.fn();
    const text = "Kundennummer bereits vergeben — eine andere Firma nutzt sie schon (oder sie ist eine Anmeldenummer). Bitte eine andere wählen.";
    put.mockRejectedValue({ response: { status: 409, data: { detail: text } } });
    rendern(createElement(VertragsKundennummerFeld, { dealer: { vertrags_kundennummer: "482913" }, refresh }));
    tippen(feld("set-vertrags-kundennummer"), "FREMD-1");
    await klick(feld("set-vertrags-kundennummer-speichern"));
    expect(feld("set-vertrags-kundennummer-fehler").textContent).toBe(text);
    expect(refresh).not.toHaveBeenCalled();
    expect(feld("set-vertrags-kundennummer").value).toBe("FREMD-1");
    tippen(feld("set-vertrags-kundennummer"), "FREMD-2");
    expect(feld("set-vertrags-kundennummer-fehler")).toBeNull();
  });
  it("ohne Firmen-Kundennummer (Altbestand) ist das Feld leer und speicherbar", async () => {
    put.mockResolvedValue({ data: {} });
    rendern(createElement(VertragsKundennummerFeld, { dealer: {}, refresh: vi.fn().mockResolvedValue({}) }));
    expect(feld("set-vertrags-kundennummer").value).toBe("");
    tippen(feld("set-vertrags-kundennummer"), "NEU-1");
    await klick(feld("set-vertrags-kundennummer-speichern"));
    expect(put).toHaveBeenCalledWith("/dealer/vertrags-kundennummer", { vertrags_kundennummer: "NEU-1" });
  });
  it("Quelltext: Feld sitzt im Profil (Chef und Sucher), Farben nur als Token", () => {
    expect(QUELLE).toContain("<VertragsKundennummerFeld dealer={dealer} refresh={refresh} />");
    const block = QUELLE.slice(QUELLE.indexOf("export function VertragsKundennummerFeld"),
                               QUELLE.indexOf("function AppleField("));
    expect(block).toContain('data-testid="vertrags-kundennummer"');
    expect(block).not.toMatch(/#[0-9a-fA-F]{3,6}\b/);
    expect(block).toContain('color: "var(--accent-red)"');
    // kein Chef-Vorbehalt im Feld — Sucher setzen sie wie die übrigen Firmendaten
    expect(block).not.toMatch(/role === "dealer"|istChef/);
  });
});
