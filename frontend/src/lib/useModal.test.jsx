/*
 * Prüfbericht 20.09.2026 (M-07): gemeinsames Dialog-Verhalten (lib/useModal).
 *  - Anfangsfokus auf das erste Bedienelement bzw. [data-autofocus]
 *  - Escape schließt (über die mitgegebene Funktion — beim Kaufvertrag die
 *    mit Rückfrage), nicht aber, solange darin ein weiteres Overlay offen ist
 *  - Fokusfalle: Tab am Ende springt zum Anfang, Umschalt+Tab umgekehrt
 *  - Fokus-Rückgabe an den Öffner
 *  - geschachtelte Dialoge: Escape trifft nur den obersten
 */
import { act, createElement as h, useState } from "react";
import { createRoot } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { MODAL_ATTRIBUTE, dialogTaste, fokussierbare, useModal } from "./useModal";

globalThis.IS_REACT_ACT_ENVIRONMENT = true;

function Dialog({ onClose, offen = true, overlay = false, autofocus = false, children }) {
  const ref = useModal(onClose, { offen });
  if (!offen) return null;
  return h("div", { className: "fixed inset-0" },
    h("div", { ref, ...MODAL_ATTRIBUTE, "data-testid": "rahmen" },
      h("button", { type: "button", "data-testid": "erster" }, "Schließen"),
      h("input", { "data-testid": "feld", ...(autofocus ? { "data-autofocus": "" } : {}) }),
      h("button", { type: "button", "data-testid": "letzter" }, "Speichern"),
      overlay ? h("div", { className: "fixed inset-0", "data-testid": "lightbox" }) : null,
      children));
}

let wurzel; let root;
beforeEach(() => {
  wurzel = document.createElement("div");
  document.body.appendChild(wurzel);
  root = createRoot(wurzel);
});
afterEach(() => {
  act(() => root.unmount());
  wurzel.remove();
});

const taste = (key, extra = {}) => act(() => {
  document.dispatchEvent(new KeyboardEvent("keydown", { key, bubbles: true, cancelable: true, ...extra }));
});
const by = (id) => document.querySelector(`[data-testid="${id}"]`);

describe("useModal", () => {
  it("setzt role/aria-modal, fokussiert das erste Bedienelement und gibt den Fokus zurück", () => {
    const oeffner = document.createElement("button");
    document.body.appendChild(oeffner);
    oeffner.focus();
    expect(document.activeElement).toBe(oeffner);
    const onClose = vi.fn();
    act(() => root.render(h(Dialog, { onClose })));
    expect(by("rahmen").getAttribute("role")).toBe("dialog");
    expect(by("rahmen").getAttribute("aria-modal")).toBe("true");
    expect(document.activeElement).toBe(by("erster"));
    act(() => root.unmount());
    expect(document.activeElement).toBe(oeffner);
    oeffner.remove();
    root = createRoot(wurzel);   // afterEach darf noch einmal unmounten
  });

  it("[data-autofocus] gewinnt vor dem ersten Knopf", () => {
    act(() => root.render(h(Dialog, { onClose: vi.fn(), autofocus: true })));
    expect(document.activeElement).toBe(by("feld"));
  });

  it("Escape ruft onClose — die Rückfrage bleibt Sache des Aufrufers", () => {
    const onClose = vi.fn();
    act(() => root.render(h(Dialog, { onClose })));
    taste("Escape");
    expect(onClose).toHaveBeenCalledTimes(1);
  });

  it("Escape lässt den Dialog stehen, solange darin ein Overlay (Lightbox) offen ist", () => {
    const onClose = vi.fn();
    act(() => root.render(h(Dialog, { onClose, overlay: true })));
    taste("Escape");
    expect(onClose).not.toHaveBeenCalled();
  });

  it("Tab am letzten Element springt zum ersten, Umschalt+Tab umgekehrt", () => {
    act(() => root.render(h(Dialog, { onClose: vi.fn() })));
    by("letzter").focus();
    taste("Tab");
    expect(document.activeElement).toBe(by("erster"));
    taste("Tab", { shiftKey: true });
    expect(document.activeElement).toBe(by("letzter"));
    // Fokus außerhalb (z. B. nach Klick auf den Hintergrund): Tab holt ihn zurück
    document.body.focus();
    taste("Tab");
    expect(document.activeElement).toBe(by("erster"));
  });

  it("bei geschachtelten Dialogen schließt Escape nur den obersten", () => {
    const aussen = vi.fn();
    const innen = vi.fn();
    function Beide() {
      const [zwei, setZwei] = useState(true);
      return h(Dialog, { onClose: aussen },
        zwei ? h(Dialog, { onClose: () => { innen(); setZwei(false); } }) : null);
    }
    act(() => root.render(h(Beide)));
    taste("Escape");
    expect(innen).toHaveBeenCalledTimes(1);
    expect(aussen).not.toHaveBeenCalled();
    taste("Escape");
    expect(aussen).toHaveBeenCalledTimes(1);
  });

  it("offen=false: keine Tastenbehandlung, kein Fokusklau", () => {
    const onClose = vi.fn();
    act(() => root.render(h(Dialog, { onClose, offen: false })));
    taste("Escape");
    expect(onClose).not.toHaveBeenCalled();
  });
});

describe("dialogTaste / fokussierbare (ohne React)", () => {
  it("überspringt versteckte und deaktivierte Elemente", () => {
    const el = document.createElement("div");
    el.innerHTML = '<button disabled>a</button><div hidden><button>b</button></div>'
      + '<button id="c">c</button><span aria-hidden="true"><a href="#">d</a></span>';
    expect(fokussierbare(el).map((e) => e.id)).toEqual(["c"]);
  });

  it("ohne Bedienelemente bleibt der Fokus auf dem Rahmen", () => {
    const el = document.createElement("div");
    el.tabIndex = -1;
    document.body.appendChild(el);
    const e = new KeyboardEvent("keydown", { key: "Tab", cancelable: true });
    expect(dialogTaste(e, el, () => {})).toBe("fokus");
    expect(document.activeElement).toBe(el);
    el.remove();
  });

  it("andere Tasten tun nichts", () => {
    const el = document.createElement("div");
    const schliessen = vi.fn();
    expect(dialogTaste(new KeyboardEvent("keydown", { key: "Enter" }), el, schliessen)).toBeNull();
    expect(schliessen).not.toHaveBeenCalled();
  });
});
