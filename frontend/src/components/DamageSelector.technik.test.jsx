/**
 * Technischer Mangel im Schadenformular (Wunsch Ahmad 25.09.2026 abends):
 *  - eigener Block ohne Skizze: "+ Mangel hinzufügen" -> Bereich tippen
 *  - der Schaden hat view "technik", zone = Bereich, severity_data.bereich,
 *    drei Fragen (Stand / Fahrbereit / Warnleuchte) und ein Beschreibungsfeld
 *  - Beschreibung landet im Vertragstext (damagesToText) in Klammern
 *  - keine leere "()"-Ansichtsangabe in der Zeile; Skizzen-Schäden unverändert
 */
import { act, createElement, useState } from "react";
import { createRoot } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

globalThis.IS_REACT_ACT_ENVIRONMENT = true;

const toastMock = vi.hoisted(() => ({ error: vi.fn(), success: vi.fn(), warning: vi.fn() }));
vi.mock("sonner", () => ({ toast: toastMock }));

const { default: DamageSelector, damagesToText } = await import("./DamageSelector");
const { alleVollstaendig, schadenZeile, technikSchaden } = await import("@/lib/kiSchaden");

let wurzel;
let behaelter;
let stand;
let text;

function Huelle({ start }) {
  const [d, setD] = useState(start);
  stand = d;
  return createElement(DamageSelector, { damages: d, onChange: (n, t) => { setD(n); text = t; } });
}
const el = (id) => behaelter.querySelector(`[data-testid="${id}"]`);
async function tippen(id) {
  const ziel = el(id);
  if (!ziel) throw new Error(`nicht gefunden: ${id}`);
  await act(async () => { ziel.dispatchEvent(new MouseEvent("click", { bubbles: true })); });
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
});

describe("Technischer Mangel", () => {
  it("wird über Bereich angelegt, hat Fragen und Beschreibung, steht im Vertragstext", async () => {
    await act(async () => { wurzel.render(createElement(Huelle, { start: [] })); });
    expect(el("damage-technik")).toBeTruthy();
    expect(el("damage-technik-bereiche")).toBeNull();
    await tippen("damage-technik-oeffnen");
    expect(el("damage-technik-bereiche")).toBeTruthy();
    await tippen("damage-technik-Getriebe/Kupplung");
    expect(stand).toHaveLength(1);
    const d = stand[0];
    expect(d).toMatchObject({ type_key: "technik", type_label: "Technischer Mangel", view: "technik",
                              zone: "Getriebe/Kupplung", severity_data: { bereich: "Getriebe/Kupplung" } });
    expect(d.x).toBeUndefined();
    expect(el("damage-technik-bereiche")).toBeNull();          // Auswahl schließt sich
    expect(alleVollstaendig(stand)).toBe(false);               // Stand/Fahrbereit/Warnleuchte fehlen
    // Zeile ohne leere Ansichtsangabe
    const liste = el("damage-list").textContent;
    expect(liste).toContain("Getriebe/Kupplung");
    expect(liste).not.toContain("()");
    // Fragen beantworten
    await tippen(`damage-frage-${d.id}-status-nur Symptom bemerkt`);
    await tippen(`damage-frage-${d.id}-fahrbereit-ja`);
    await tippen(`damage-frage-${d.id}-warnleuchte-keine`);
    expect(alleVollstaendig(stand)).toBe(true);
    // Beschreibung
    const feld = el(`damage-note-${d.id}`);
    expect(feld).toBeTruthy();
    await act(async () => {
      const setter = Object.getOwnPropertyDescriptor(window.HTMLInputElement.prototype, "value").set;
      setter.call(feld, "Automatik ruckelt beim Kaltstart");
      feld.dispatchEvent(new Event("input", { bubbles: true }));
    });
    expect(stand[0].note).toBe("Automatik ruckelt beim Kaltstart");
    expect(text).toBe("• Technischer Mangel: Getriebe/Kupplung (Automatik ruckelt beim Kaltstart)");
    expect(schadenZeile(stand[0])).toContain("„Automatik ruckelt beim Kaltstart“");
    expect(toastMock.success).toHaveBeenCalled();
  });

  it("damagesToText: Skizzen-Schäden wie bisher, Technik ohne Beschreibung nur mit Bereich", () => {
    const kratzer = { id: "k", view: "front", type_key: "kratzer", type_label: "Kratzer", zone: "Motorhaube" };
    expect(damagesToText([kratzer, technikSchaden("Motor", "t")]))
      .toBe("• Kratzer: Motorhaube\n• Technischer Mangel: Motor");
  });
});
