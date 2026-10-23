/**
 * Erkennung von Inserats-Links und das Lesen der Zwischenablage
 * (18.09.2026, Wunsch Ahmad: Klick ins Feld fügt den kopierten Link ein).
 */
import { istInseratsLink, zwischenablageLesen } from "./inseratsLink";

describe("istInseratsLink", () => {
  test.each([
    ["https://www.kleinanzeigen.de/s-anzeige/vw-golf/2812345678-216-1"],
    ["https://suchen.mobile.de/fahrzeuge/details.html?id=412345678"],
    ["https://www.mobile.de/auto-inserat/vw-golf/412345678.html"],
    ["https://www.autoscout24.de/angebote/vw-golf-benzin-123abc"],
    ["  https://www.autoscout24.at/angebote/abc  "],
  ])("erkennt %s", (link) => {
    expect(istInseratsLink(link)).toBe(true);
  });

  test.each([
    ["https://suchen.mobile.de/fahrzeuge/search.html?isSearchRequest=true", "Suchseite"],
    ["https://www.kleinanzeigen.de/s-autos/", "Kategorieseite"],
    ["Hallo, hier ist der Vertrag", "einfacher Text"],
    ["", "leer"],
    [null, "null"],
    [`https://www.kleinanzeigen.de/s-anzeige/${"x".repeat(2100)}`, "unsinnig lang"],
  ])("lehnt %s ab (%s)", (link) => {
    expect(istInseratsLink(link)).toBe(false);
  });
});

describe("zwischenablageLesen", () => {
  const alt = globalThis.navigator;

  afterEach(() => {
    if (alt === undefined) delete globalThis.navigator;
    else Object.defineProperty(globalThis, "navigator", { value: alt, configurable: true });
  });

  const navigatorMit = (clipboard) =>
    Object.defineProperty(globalThis, "navigator", { value: { clipboard }, configurable: true });

  test("liefert den getrimmten Text", async () => {
    navigatorMit({ readText: async () => "  https://suchen.mobile.de/x  " });
    expect(await zwischenablageLesen()).toEqual({ text: "https://suchen.mobile.de/x", moeglich: true });
  });

  test("verweigerte Erlaubnis meldet 'nicht möglich' statt zu werfen", async () => {
    navigatorMit({ readText: async () => { throw new Error("NotAllowedError"); } });
    expect(await zwischenablageLesen()).toEqual({ text: "", moeglich: false });
  });

  test("Browser ohne Zwischenablage-Zugriff (z. B. Firefox)", async () => {
    navigatorMit({});
    expect(await zwischenablageLesen()).toEqual({ text: "", moeglich: false });
  });
});
