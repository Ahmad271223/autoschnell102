/**
 * Rollenprüfung 22.09.2026, Welle 2 — Fahrer-Anmeldung (DriverLogin.jsx).
 *  RP-563  Impressum, Datenschutz und AGB auch vor der Anmeldung erreichbar.
 *  RP-546  Nach einer Abmeldung mitten im Abholprotokoll dorthin zurück.
 */
import { act, createElement } from "react";
import { createRoot } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

globalThis.IS_REACT_ACT_ENVIRONMENT = true;

const navSpy = vi.hoisted(() => vi.fn());
const login = vi.hoisted(() => vi.fn());
const ruecksprung = vi.hoisted(() => ({ ziel: "" }));

vi.mock("@/context/DriverContext", () => ({
  useDriver: () => ({ driver: null, ready: true, login }),
  fahrerRuecksprungHolen: () => { const z = ruecksprung.ziel; ruecksprung.ziel = ""; return z; },
}));
vi.mock("sonner", () => ({ toast: { success: vi.fn(), error: vi.fn() } }));
vi.mock("@/lib/fassung", async (importOriginal) => ({
  ...(await importOriginal()), neueFassungLaden: () => false }));
vi.mock("@/components/InstallPWAButton", () => ({ default: () => null }));
vi.mock("react-router-dom", async () => {
  const { createElement: h } = await import("react");
  return {
    Link: ({ children, to, ...rest }) => h("a", { href: String(to), ...rest }, children),
    Navigate: () => null,
    useNavigate: () => navSpy,
    useSearchParams: () => [new URLSearchParams("")],
  };
});

const { default: DriverLogin } = await import("./DriverLogin");

let wurzel;
let behaelter;

async function warten() {
  await act(async () => { await new Promise((r) => setTimeout(r, 0)); });
}

beforeEach(async () => {
  vi.clearAllMocks();
  behaelter = document.createElement("div");
  document.body.appendChild(behaelter);
  wurzel = createRoot(behaelter);
  await act(async () => { wurzel.render(createElement(DriverLogin)); });
});

afterEach(async () => {
  await act(async () => { wurzel.unmount(); });
  behaelter.remove();
});

async function anmelden() {
  login.mockResolvedValueOnce({ id: "f1" });
  const setter = Object.getOwnPropertyDescriptor(window.HTMLInputElement.prototype, "value").set;
  for (const [id, wert] of [["driver-login-kontonummer", "FD-7K2M9QX4"], ["driver-login-password", "x"]]) {
    const feld = behaelter.querySelector(`[data-testid="${id}"]`);
    await act(async () => { setter.call(feld, wert); feld.dispatchEvent(new Event("input", { bubbles: true })); });
  }
  const form = behaelter.querySelector("form");
  await act(async () => { form.dispatchEvent(new Event("submit", { bubbles: true, cancelable: true })); });
  await warten();
}

describe("Fahrer-Anmeldung", () => {
  it("RP-563: Rechtslinks sichtbar", () => {
    const links = behaelter.querySelector('[data-testid="rechts-links"]');
    expect(links).not.toBeNull();
    const ziele = [...links.querySelectorAll("a")].map((a) => a.getAttribute("href"));
    expect(ziele).toEqual(["/impressum", "/datenschutz", "/agb"]);
  });

  it("RP-546: zurück ins Abholprotokoll, sonst zur Startseite", async () => {
    ruecksprung.ziel = "/fahrer/protokoll/t7";
    await anmelden();
    expect(navSpy).toHaveBeenLastCalledWith("/fahrer/protokoll/t7");
    await anmelden();
    expect(navSpy).toHaveBeenLastCalledWith("/fahrer");
  });
});
