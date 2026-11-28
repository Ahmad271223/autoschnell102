/**
 * Rollenprüfung 22.09.2026 (Review, Welle 4) — DamageSelector: "Alle entfernen"
 * mit genau einem Schaden spricht in der Einzahl ("1 Schaden entfernt"), nicht
 * "Alle 1 erfassten Schäden".
 */
import { act, createElement, useState } from "react";
import { createRoot } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

globalThis.IS_REACT_ACT_ENVIRONMENT = true;

const toastMock = vi.hoisted(() => ({ error: vi.fn(), success: vi.fn(), warning: vi.fn() }));
vi.mock("sonner", () => ({ toast: toastMock }));

const { default: DamageSelector } = await import("./DamageSelector");

const KRATZER = { id: "d1", view: "front", type_key: "kratzer", type_label: "Kratzer", abbr: "KR",
                  color: "#0ea5e9", zone: "Motorhaube", x: 765, y: 395 };

let wurzel;
let behaelter;
let stand;

function Huelle({ start }) {
  const [d, setD] = useState(start);
  stand = d;
  return createElement(DamageSelector, { damages: d, onChange: (n) => setD(n) });
}

async function allesEntfernen() {
  const knopf = behaelter.querySelector('[data-testid="damage-clear-all"]');
  await act(async () => { knopf.dispatchEvent(new MouseEvent("click", { bubbles: true })); });
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
  vi.restoreAllMocks();
});

describe("'Alle entfernen': Einzahl und Mehrzahl", () => {
  it("ein Schaden: Einzahl in Rückfrage und Meldung", async () => {
    const bestaetigen = vi.spyOn(window, "confirm").mockReturnValue(true);
    await act(async () => { wurzel.render(createElement(Huelle, { start: [KRATZER] })); });
    await allesEntfernen();
    expect(bestaetigen).toHaveBeenCalledWith("Den erfassten Schaden entfernen?");
    expect(stand).toHaveLength(0);
    expect(toastMock.success.mock.calls.at(-1)[0]).toBe("1 Schaden entfernt");
  });

  it("zwei Schäden: Mehrzahl wie bisher", async () => {
    const bestaetigen = vi.spyOn(window, "confirm").mockReturnValue(true);
    const zweiter = { ...KRATZER, id: "d2", zone: "Dach", x: 765, y: 100 };
    await act(async () => { wurzel.render(createElement(Huelle, { start: [KRATZER, zweiter] })); });
    await allesEntfernen();
    expect(bestaetigen).toHaveBeenCalledWith("Alle 2 erfassten Schäden entfernen?");
    expect(toastMock.success.mock.calls.at(-1)[0]).toBe("2 Schäden entfernt");
  });
});
