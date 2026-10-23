/**
 * Rollenprüfung 22.09.2026 (RP-070/RP-169/RP-514) — DamageSelector:
 *  - Tipp auf einen Marker mit ANDERER Schadensart legt einen weiteren Schaden
 *    am selben Bauteil an (vorher: stilles Löschen);
 *  - gleiche Schadensart: Rückfrage, dann entfernen — mit "Rückgängig";
 *  - "Alle entfernen" fragt nach und lässt sich rückgängig machen.
 */
import { act, createElement, useState } from "react";
import { createRoot } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

globalThis.IS_REACT_ACT_ENVIRONMENT = true;

const toastMock = vi.hoisted(() => ({ error: vi.fn(), success: vi.fn(), warning: vi.fn() }));
vi.mock("sonner", () => ({ toast: toastMock }));

const { default: DamageSelector, markerVersatz } = await import("./DamageSelector");

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

const el = (id) => behaelter.querySelector(`[data-testid="${id}"]`);
async function tippen(id) {
  const ziel = el(id);
  if (!ziel) throw new Error(`nicht gefunden: ${id}`);
  await act(async () => { ziel.dispatchEvent(new MouseEvent("click", { bubbles: true })); });
}

async function starten(start) {
  await act(async () => { wurzel.render(createElement(Huelle, { start })); });
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

describe("Marker antippen (RP-514)", () => {
  it("andere Schadensart: zusätzlicher Schaden am selben Bauteil, nichts gelöscht", async () => {
    const bestaetigen = vi.spyOn(window, "confirm");
    await starten([KRATZER]);
    await tippen("damage-type-delle");
    await tippen("damage-marker-d1");
    expect(bestaetigen).not.toHaveBeenCalled();
    expect(stand).toHaveLength(2);
    expect(stand[0]).toEqual(KRATZER);
    expect(stand[1]).toMatchObject({ type_key: "delle", zone: "Motorhaube", x: 765, y: 395 });
  });

  it("gleiche Schadensart: erst nach Rückfrage entfernen, dann Rückgängig", async () => {
    const bestaetigen = vi.spyOn(window, "confirm").mockReturnValue(false);
    await starten([KRATZER]);                     // Kratzer ist die voreingestellte Art
    await tippen("damage-marker-d1");
    expect(bestaetigen).toHaveBeenCalledTimes(1);
    expect(stand).toHaveLength(1);
    bestaetigen.mockReturnValue(true);
    await tippen("damage-marker-d1");
    expect(stand).toHaveLength(0);
    const [, optionen] = toastMock.success.mock.calls.at(-1);
    expect(optionen.action.label).toBe("Rückgängig");
    await act(async () => { optionen.action.onClick(); });
    expect(stand).toEqual([KRATZER]);
  });

  it("'Alle entfernen' fragt nach und ist rückgängig zu machen", async () => {
    const zweiter = { ...KRATZER, id: "d2", zone: "Dach", x: 765, y: 100 };
    const bestaetigen = vi.spyOn(window, "confirm").mockReturnValue(false);
    await starten([KRATZER, zweiter]);
    await tippen("damage-clear-all");
    expect(stand).toHaveLength(2);
    bestaetigen.mockReturnValue(true);
    await tippen("damage-clear-all");
    expect(stand).toHaveLength(0);
    const [, optionen] = toastMock.success.mock.calls.at(-1);
    await act(async () => { optionen.action.onClick(); });
    expect(stand.map((d) => d.id)).toEqual(["d1", "d2"]);
  });
});

describe("Marker am selben Punkt", () => {
  it("werden nebeneinander gezeichnet (Index je Punkt)", () => {
    expect(markerVersatz([{ x: 1, y: 1 }, { x: 1, y: 1 }, { x: 2, y: 2 }, { x: 1, y: 1 }]))
      .toEqual([0, 1, 0, 2]);
  });
});
