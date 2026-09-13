/**
 * Runde 31 (12.09.2026): Unterschriften, Kaufvertrag, Abhol-Check und
 * Wiederherstellungscodes gingen bei jedem Neuladen ohne Rueckfrage verloren.
 */
import { act, createElement, useState } from "react";
import { createRoot } from "react-dom/client";
import { afterEach, describe, expect, it, vi } from "vitest";
import { hatUngespeichert, ungespeichertMelden, useUngespeichert } from "./ungespeichert";

globalThis.IS_REACT_ACT_ENVIRONMENT = true;

afterEach(() => {
  vi.restoreAllMocks();
});

describe("ungespeichertMelden", () => {
  it("haengt die Rueckfrage an und nimmt sie mit der letzten Stelle wieder ab", () => {
    const an = vi.spyOn(window, "addEventListener");
    const ab = vi.spyOn(window, "removeEventListener");
    const a = ungespeichertMelden();
    const b = ungespeichertMelden();
    expect(hatUngespeichert()).toBe(true);
    expect(an.mock.calls.filter(([t]) => t === "beforeunload")).toHaveLength(1);
    a();
    expect(hatUngespeichert()).toBe(true);
    expect(ab.mock.calls.filter(([t]) => t === "beforeunload")).toHaveLength(0);
    b();
    expect(hatUngespeichert()).toBe(false);
    expect(ab.mock.calls.filter(([t]) => t === "beforeunload")).toHaveLength(1);
  });

  it("die Rueckfrage haelt das Verlassen wirklich an", () => {
    const aufheben = ungespeichertMelden();
    const e = new Event("beforeunload", { cancelable: true });
    window.dispatchEvent(e);
    expect(e.defaultPrevented).toBe(true);
    aufheben();
    const e2 = new Event("beforeunload", { cancelable: true });
    window.dispatchEvent(e2);
    expect(e2.defaultPrevented).toBe(false);
  });
});

describe("useUngespeichert", () => {
  it("folgt dem Zustand der Komponente und raeumt beim Aushaengen auf", () => {
    let umschalten;
    function Formular() {
      const [unterschrieben, setUnterschrieben] = useState(false);
      umschalten = setUnterschrieben;
      useUngespeichert(unterschrieben);
      return null;
    }
    const wurzel = createRoot(document.createElement("div"));
    act(() => { wurzel.render(createElement(Formular)); });
    expect(hatUngespeichert()).toBe(false);
    act(() => { umschalten(true); });
    expect(hatUngespeichert()).toBe(true);
    act(() => { wurzel.unmount(); });
    expect(hatUngespeichert()).toBe(false);
  });
});
