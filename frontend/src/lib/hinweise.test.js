/*
 * Runde 24 (11.09.2026): doppelte Hinweis-Toasts (Befund Ahmad — dieselbe
 * gelbe AutoScout-Warnung zweimal untereinander).
 */
import {
  eindeutigeHinweise, hinweisToastId, hinweiseZeigen, HINWEIS_DAUER_MS,
} from "./hinweise";

const KOMBI =
  "AutoScout24 hat keine passende Kategorie fuer 'Kombi' — der AutoScout-Link filtert nicht nach Kategorie.";
const HUBRAUM = "Hubraum filtert nur mobile.de — der AutoScout-Link zeigt alle Hubraeume.";

// Nachbau des sonner-Verhaltens: gleiche id ersetzt, ohne id wird gestapelt.
function fakeToaster() {
  const sichtbar = new Map();
  const aufrufe = [];
  let zaehler = 0;
  return {
    sichtbar,
    aufrufe,
    warning(text, opts = {}) {
      aufrufe.push(["warning", text, opts]);
      sichtbar.set(opts.id ?? `auto-${++zaehler}`, text);
    },
    dismiss(id) {
      aufrufe.push(["dismiss", id]);
      sichtbar.delete(id);
    },
  };
}

describe("eindeutigeHinweise", () => {
  test("Dubletten, Leeres und Nicht-Texte fallen weg, Reihenfolge bleibt", () => {
    expect(eindeutigeHinweise([KOMBI, HUBRAUM, KOMBI, "", "  ", null, 42, ` ${KOMBI} `]))
      .toEqual([KOMBI, HUBRAUM]);
  });

  test("ohne Liste gibt es keine Hinweise", () => {
    expect(eindeutigeHinweise(undefined)).toEqual([]);
    expect(eindeutigeHinweise(null)).toEqual([]);
    expect(eindeutigeHinweise("kein Array")).toEqual([]);
  });
});

describe("hinweiseZeigen", () => {
  test("jeder Text bekommt eine feste id und die gewohnte Anzeigedauer", () => {
    const t = fakeToaster();
    const ids = hinweiseZeigen(t, [KOMBI]);
    expect(ids).toEqual([hinweisToastId(KOMBI)]);
    expect(hinweisToastId(KOMBI)).toBe(`hinweis:${KOMBI}`);
    expect(t.aufrufe).toEqual([
      ["warning", KOMBI, { id: `hinweis:${KOMBI}`, duration: HINWEIS_DAUER_MS }],
    ]);
  });

  test("derselbe Hinweis in einer Antwort doppelt: nur ein Toast", () => {
    const t = fakeToaster();
    hinweiseZeigen(t, [KOMBI, KOMBI]);
    expect([...t.sichtbar.values()]).toEqual([KOMBI]);
  });

  test("der Befund: zweiter Lauf mit demselben Hinweis stapelt nicht", () => {
    // Einfuegen startet den Vergleich, danach noch "Auslesen" geklickt.
    const t = fakeToaster();
    let ids = hinweiseZeigen(t, [KOMBI]);
    ids = hinweiseZeigen(t, [KOMBI], ids);
    expect([...t.sichtbar.values()]).toEqual([KOMBI]);
    // Der noch gueltige Hinweis wird nicht geschlossen (sonner wuerde den
    // gleich danach wieder gezeigten sonst mit ausblenden).
    expect(t.aufrufe.filter(([art]) => art === "dismiss")).toEqual([]);
    expect(ids).toEqual([hinweisToastId(KOMBI)]);
  });

  test("Hinweise des vorigen Laufs, die nicht mehr gelten, gehen zu", () => {
    const t = fakeToaster();
    let ids = hinweiseZeigen(t, [KOMBI, HUBRAUM]);
    ids = hinweiseZeigen(t, [HUBRAUM], ids);
    expect([...t.sichtbar.values()]).toEqual([HUBRAUM]);
    expect(t.aufrufe).toContainEqual(["dismiss", hinweisToastId(KOMBI)]);
    expect(ids).toEqual([hinweisToastId(HUBRAUM)]);
  });

  test("leere Antwort schliesst alle alten Hinweise", () => {
    const t = fakeToaster();
    const ids = hinweiseZeigen(t, [KOMBI, HUBRAUM]);
    expect(hinweiseZeigen(t, undefined, ids)).toEqual([]);
    expect(t.sichtbar.size).toBe(0);
  });
});
