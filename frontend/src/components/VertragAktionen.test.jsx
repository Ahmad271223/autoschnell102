/*
 * Wunsch Ahmad 03.10.2026: Im Vertragsarchiv statt acht Symbol-Knöpfen nur
 * "Ansehen", "Senden" und "Mehr" — der Rest beschriftet im Menü.
 */
import { act, createElement } from "react";
import { createRoot } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import VertragAktionen from "./VertragAktionen";

globalThis.IS_REACT_ACT_ENVIRONMENT = true;

let wurzel;
let behaelter;
const knopf = (id) => document.querySelector(`[data-testid="${id}"]`);

function aktionen() {
  return {
    termin: vi.fn(), ansehen: vi.fn(), digital: vi.fn(), senden: vi.fn(), portal: vi.fn(),
    folgeMail: vi.fn(), aendern: vi.fn(), korrektur: vi.fn(), loeschen: vi.fn(),
  };
}

async function zeigen(a, zustand = {}) {
  await act(async () => {
    wurzel.render(createElement(VertragAktionen, { it: { id: "c1" }, a, zustand }));
  });
}

beforeEach(() => {
  behaelter = document.createElement("div");
  document.body.appendChild(behaelter);
  wurzel = createRoot(behaelter);
});

afterEach(async () => {
  await act(async () => { wurzel.unmount(); });
  behaelter.remove();
});

describe("VertragAktionen", () => {
  it("zeigt nur Ansehen, Senden und Mehr — beschriftet", async () => {
    const a = aktionen();
    await zeigen(a);
    const leiste = knopf("vertrag-aktionen-c1");
    expect(leiste.querySelectorAll("button")).toHaveLength(3);
    expect(knopf("open-pdf-c1").textContent).toContain("Ansehen");
    expect(knopf("senden-c1").textContent).toContain("Senden");
    expect(knopf("pdf-mehr-c1").textContent).toContain("Mehr");
    expect(knopf("termin-anlegen-c1")).toBeNull();
    expect(knopf("del-pdf-c1")).toBeNull();               // erst im Menü
    await act(async () => { knopf("open-pdf-c1").click(); });
    await act(async () => { knopf("senden-c1").click(); });
    expect(a.ansehen).toHaveBeenCalledTimes(1);
    expect(a.senden).toHaveBeenCalledTimes(1);
  });

  it("Mehr öffnet das Menü mit allen übrigen Aktionen, ein Klick schließt es", async () => {
    const a = aktionen();
    await zeigen(a);
    await act(async () => { knopf("pdf-mehr-c1").click(); });
    expect(knopf("pdf-mehr-c1").getAttribute("aria-expanded")).toBe("true");
    const menu = knopf("pdf-mehr-menu-c1");
    expect(menu.getAttribute("role")).toBe("menu");
    const text = menu.textContent;
    for (const t of ["Digitale Fassung öffnen", "Online unterschreiben lassen", "Hinweis nach dem Kauf",
                     "Kaufvertrag ändern", "Verkäuferdaten korrigieren", "Vertrag löschen"]) {
      expect(text).toContain(t);
    }
    const ziele = [["open-pdf-digital-c1", "digital"], ["kundenportal-c1", "portal"],
                   ["folgemail-c1", "folgeMail"], ["vertrag-aendern-c1", "aendern"],
                   ["verkaeufer-korrektur-c1", "korrektur"], ["del-pdf-c1", "loeschen"]];
    for (const [id, name] of ziele) {
      if (!knopf("pdf-mehr-menu-c1")) await act(async () => { knopf("pdf-mehr-c1").click(); });
      await act(async () => { knopf(id).click(); });
      expect(a[name]).toHaveBeenCalledTimes(1);
      expect(knopf("pdf-mehr-menu-c1")).toBeNull();     // nach der Auswahl zu
    }
  });

  it("Escape und Klick daneben schließen das Menü", async () => {
    await zeigen(aktionen());
    await act(async () => { knopf("pdf-mehr-c1").click(); });
    await act(async () => { document.dispatchEvent(new KeyboardEvent("keydown", { key: "Escape" })); });
    expect(knopf("pdf-mehr-menu-c1")).toBeNull();
    await act(async () => { knopf("pdf-mehr-c1").click(); });
    expect(knopf("pdf-mehr-menu-c1")).not.toBeNull();
    await act(async () => { document.body.dispatchEvent(new MouseEvent("mousedown", { bubbles: true })); });
    expect(knopf("pdf-mehr-menu-c1")).toBeNull();
  });

  it("fehlender Abholtermin bleibt sichtbar; online unterschrieben wird grün benannt", async () => {
    const a = aktionen();
    await zeigen(a, { termin: true, portalUnterschrieben: true, loeschtId: "x" });
    expect(knopf("termin-anlegen-c1").textContent).toContain("Termin anlegen");
    await act(async () => { knopf("termin-anlegen-c1").click(); });
    expect(a.termin).toHaveBeenCalledTimes(1);
    await act(async () => { knopf("pdf-mehr-c1").click(); });
    expect(knopf("kundenportal-c1").textContent).toContain("Online unterschrieben – ansehen");
    expect(knopf("del-pdf-c1").disabled).toBe(true);      // Löschen läuft gerade
  });
});
