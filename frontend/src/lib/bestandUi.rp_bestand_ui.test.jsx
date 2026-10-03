/*
 * Rollenprüfung 22.09.2026 — Team Bestand/Oberfläche, reine Helfer.
 *  RP-449/RP-565  Akte-Kosten deutsch lesen ("1.200" = 1.200 €)
 *  RP-462         Neuladen behält ungespeicherte Eingaben
 *  RP-012/111/262 Abmelden mit beendeter Sitzung: keine zweite Umleitung
 *  RP-423         Alte AGB hängen am Standardtext, statt ihn zu ersetzen
 *  RP-143         Wechsel in der App fragt bei ungespeicherten Eingaben nach
 *  RP-533         HEIC wird klar abgelehnt statt das Fotopaket zu reißen
 *  RP-472         Zähler der Kaufanfragen
 */
import { afterEach, describe, expect, it, vi } from "vitest";
import {
  betragAlsText, bestandFormAus, bestandGeaendert, bestandMitOffenenAenderungen, kostenLesen,
} from "./bestandForm";
import { loestAbmeldungAus } from "./api";
import { vertragstextFuerFormular, zusammenfuehren } from "./vertragstext";
import { hatUngespeichert, ungespeichertMelden, verlassenBestaetigen } from "./ungespeichert";
import { BildFormatFehler, istHochladbaresBild, verkleinereBildDatei } from "./bilder";
import { anfragenZaehlen } from "./anfragenZaehler";
import { aktionText } from "./fahrzeugStatus";

afterEach(() => {
  vi.restoreAllMocks();
  delete globalThis.createImageBitmap;
});

describe("RP-449/RP-565: Kosten der Akte", () => {
  it("zeigt Beträge deutsch und liest sie deutsch zurück", () => {
    expect(betragAlsText(1200)).toBe("1.200");
    expect(betragAlsText(249.9)).toBe("249,9");
    expect(betragAlsText(null)).toBe("");
    const form = bestandFormAus({ costs: [{ label: "Reifen", amount: 1200 }], stand: "s1" });
    expect(form.costs).toEqual([{ label: "Reifen", amount: "1.200" }]);
    expect(form.stand).toBe("s1");
    expect(kostenLesen(form.costs)).toEqual({ costs: [{ label: "Reifen", amount: 1200 }], fehler: null });
  });

  it("\"1.200\" ist tausendzweihundert, \"249,90\" bleibt 249,90", () => {
    const { costs, fehler } = kostenLesen([
      { label: "Transport", amount: "1.200" },
      { label: "Aufbereitung", amount: "249,90" },
      { label: "", amount: "" },                 // ganz leer: fällt weg
      { label: "", amount: "50" },               // ohne Bezeichnung: "Kosten"
      { label: "Gutachten", amount: "" },        // ohne Betrag: 0 €
    ]);
    expect(fehler).toBeNull();
    expect(costs).toEqual([
      { label: "Transport", amount: 1200 },
      { label: "Aufbereitung", amount: 249.9 },
      { label: "Kosten", amount: 50 },
      { label: "Gutachten", amount: 0 },
    ]);
  });

  it("unlesbare Beträge blockieren das Speichern", () => {
    for (const roh of ["12,5,0", "-3", "abc", "12 Euro"]) {
      const r = kostenLesen([{ label: "X", amount: roh }]);
      expect(r.costs).toBeNull();
      expect(r.fehler).toContain(roh);
    }
  });
});

describe("RP-462: Neuladen behält offene Eingaben", () => {
  const stand = bestandFormAus({ location: "Hof", notes: "", costs: [], stand: "a" });

  it("geänderte Felder bleiben, der Rest und der Stand kommen vom Server", () => {
    const alt = { ...stand, costs: [{ label: "Reifen", amount: "400" }] };
    const neu = bestandFormAus({ location: "Halle 2", notes: "vom Kollegen", costs: [], stand: "b" });
    const out = bestandMitOffenenAenderungen(alt, stand, neu);
    expect(out.costs).toEqual([{ label: "Reifen", amount: "400" }]);
    expect(out.location).toBe("Halle 2");
    expect(out.notes).toBe("vom Kollegen");
    expect(out.stand).toBe("b");
    expect(bestandGeaendert(out, neu)).toBe(true);
    expect(bestandGeaendert(neu, neu)).toBe(false);
  });

  it("erstes Laden bzw. nach dem Speichern: Serverstand", () => {
    const neu = bestandFormAus({ location: "X" });
    expect(bestandMitOffenenAenderungen(null, null, neu)).toBe(neu);
    expect(bestandMitOffenenAenderungen({ ...stand, notes: "offen" }, null, neu)).toBe(neu);
  });
});

describe("RP-012/RP-111/RP-262: 401 beim Abmelden", () => {
  it("Anmelden und Abmelden lösen keine Umleitung aus, alles andere schon", () => {
    expect(loestAbmeldungAus("/auth/logout")).toBe(false);
    expect(loestAbmeldungAus("/auth/login")).toBe(false);
    expect(loestAbmeldungAus("/auth/login/mfa")).toBe(false);
    expect(loestAbmeldungAus("/auth/me")).toBe(true);
    expect(loestAbmeldungAus("/vehicles/x/akte")).toBe(true);
    expect(loestAbmeldungAus("")).toBe(true);
  });
});

describe("RP-423: alte AGB und der Standardtext", () => {
  const STANDARD = "1. Standard\n\n2. Klauseln";
  it("leeres Feld + AGB: Standardtext bleibt, AGB kommen darunter", () => {
    const r = vertragstextFuerFormular("AGB alt", "", STANDARD);
    expect(r.text).toBe(`${STANDARD}\n\nAGB alt`);
    expect(r.zusammengefuehrt).toBe(true);
    expect(r.standardGenutzt).toBe(true);
    // vorher: nur die AGB — die vier Klauseln waren beim Speichern weg
    expect(zusammenfuehren("AGB alt", "")).toBe("AGB alt");
  });

  it("ohne AGB bleibt ein leeres Feld leer (= Standard gilt)", () => {
    expect(vertragstextFuerFormular("", "", STANDARD))
      .toEqual({ text: "", zusammengefuehrt: false, standardGenutzt: false });
    expect(vertragstextFuerFormular(null, "Eigener Text", STANDARD).text).toBe("Eigener Text");
    expect(vertragstextFuerFormular("AGB", "Eigener Text", STANDARD).text).toBe("Eigener Text\n\nAGB");
  });
});

describe("RP-143: Wechsel innerhalb der App", () => {
  it("ohne offene Eingaben keine Rückfrage", () => {
    const frage = vi.spyOn(window, "confirm");
    expect(hatUngespeichert()).toBe(false);
    expect(verlassenBestaetigen()).toBe(true);
    expect(frage).not.toHaveBeenCalled();
  });

  it("mit offenen Eingaben entscheidet der Nutzer", () => {
    const aufheben = ungespeichertMelden();
    const frage = vi.spyOn(window, "confirm").mockReturnValueOnce(false).mockReturnValueOnce(true);
    expect(verlassenBestaetigen()).toBe(false);
    expect(verlassenBestaetigen()).toBe(true);
    expect(frage).toHaveBeenCalledTimes(2);
    aufheben();
  });
});

describe("RP-533: Bildformate", () => {
  it("nur Formate, die der Server annimmt", () => {
    expect(istHochladbaresBild("data:image/jpeg;base64,AAA")).toBe(true);
    expect(istHochladbaresBild("data:image/png;base64,AAA")).toBe(true);
    expect(istHochladbaresBild("data:image/webp;base64,AAA")).toBe(true);
    expect(istHochladbaresBild("data:image/heic;base64,AAA")).toBe(false);
    expect(istHochladbaresBild("data:application/octet-stream;base64,AAA")).toBe(false);
    expect(istHochladbaresBild(null)).toBe(false);
  });

  it("HEIC, das der Browser nicht umwandeln kann: klarer Fehler statt Originaldatei", async () => {
    globalThis.createImageBitmap = vi.fn().mockRejectedValue(new Error("kann ich nicht"));
    const heic = new File([new Uint8Array([0, 0, 0, 24, 102, 116, 121, 112])], "IMG_0001.HEIC",
                          { type: "image/heic" });
    await expect(verkleinereBildDatei(heic)).rejects.toBeInstanceOf(BildFormatFehler);
    await expect(verkleinereBildDatei(heic)).rejects.toThrow(/IMG_0001\.HEIC.*JPG/s);
  });

  it("ein PNG, das nicht umgezeichnet werden kann, geht weiter als Original", async () => {
    globalThis.createImageBitmap = vi.fn().mockRejectedValue(new Error("kann ich nicht"));
    const png = new File([new Uint8Array([137, 80, 78, 71, 13, 10, 26, 10])], "logo.png",
                         { type: "image/png" });
    await expect(verkleinereBildDatei(png)).resolves.toMatch(/^data:image\/png;base64,/);
  });
});

describe("RP-472/RP-483: Zähler und Historie", () => {
  it("zählt offene Anfragen und Gegenangebote zusammen", () => {
    expect(anfragenZaehlen([[{ id: 1 }, { id: 2 }], [{ id: 3 }]])).toBe(3);
    expect(anfragenZaehlen([null, undefined])).toBe(0);
    expect(anfragenZaehlen(null)).toBe(0);
  });

  it("neue Historie-Einträge sind lesbar", () => {
    expect(aktionText("fahrzeug.bestand.verlaengert")).toBe("Bestandsfrist um 50 Tage verlängert");
    expect(aktionText("pdf.gesendet.email")).toBe("Kaufvertrag per E-Mail verschickt");
    expect(aktionText("pdf.folgemail.bahn")).toBe("Nachricht zum Kaufvertrag verschickt");
    expect(aktionText("protokoll.zur_freigabe")).toBe("Abholprotokoll zur Freigabe eingereicht");
  });
});
