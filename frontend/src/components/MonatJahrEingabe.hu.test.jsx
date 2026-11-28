/**
 * Go-Live-Prüfung 27.09.2026 (K2, Zusatzfund): Der Hinweis „HU abgelaufen“
 * erschien nur nach Tippen oder Verlassen des Feldes — für einen aus dem
 * Inserat vorbelegten (oder aus dem Entwurf geladenen) Wert blieb er stumm.
 */
import { act, createElement } from "react";
import { createRoot } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import MonatJahrEingabe from "./MonatJahrEingabe";

globalThis.IS_REACT_ACT_ENVIRONMENT = true;

let wurzel;
let behaelter;

beforeEach(() => {
  vi.useFakeTimers({ toFake: ["Date"] });
  vi.setSystemTime(new Date(2026, 8, 27, 12, 0));   // 27.09.2026
  behaelter = document.createElement("div");
  document.body.appendChild(behaelter);
  wurzel = createRoot(behaelter);
});

afterEach(() => {
  act(() => wurzel.unmount());
  behaelter.remove();
  vi.useRealTimers();
});

const zeigen = (props) => act(() => {
  wurzel.render(createElement(MonatJahrEingabe, { onChange: () => {}, testid: "hu", ...props }));
});
const hinweis = () => behaelter.querySelector('[data-testid="hu-hinweis"]')?.textContent || "";

describe("MonatJahrEingabe: HU abgelaufen auch ohne Tippen", () => {
  it("zeigt den Hinweis sofort für einen vorbelegten abgelaufenen Wert", () => {
    zeigen({ value: "08/2026", art: "hu" });
    expect(hinweis()).toBe("HU abgelaufen");
  });
  it("kein Hinweis für eine gültige HU (auch im laufenden Monat)", () => {
    zeigen({ value: "09/2026", art: "hu" });
    expect(hinweis()).toBe("");
    zeigen({ value: "09/2027", art: "hu" });
    expect(hinweis()).toBe("");
  });
  it("Erstzulassung und leere Felder bekommen keinen HU-Hinweis", () => {
    zeigen({ value: "08/2020", art: "ez" });
    expect(hinweis()).toBe("");
    zeigen({ value: "", art: "hu" });
    expect(hinweis()).toBe("");
  });
});
