import { dateiTeilen, kannDateiTeilen, pdfDatei } from "./teilen";

const datei = new File(["%PDF-1.4"], "Kaufvertrag.pdf", { type: "application/pdf" });

describe("kannDateiTeilen", () => {
  test("ohne Web Share API: nein (PC)", () => {
    expect(kannDateiTeilen(datei, {})).toBe(false);
    expect(kannDateiTeilen(datei, undefined)).toBe(false);
  });

  test("share vorhanden, aber Dateien nicht erlaubt: nein", () => {
    const nav = { share: jest.fn(), canShare: () => false };
    expect(kannDateiTeilen(datei, nav)).toBe(false);
  });

  test("Handy mit Datei-Teilen: ja — ohne Datei trotzdem nein", () => {
    const nav = { share: jest.fn(), canShare: ({ files }) => files?.length === 1 };
    expect(kannDateiTeilen(datei, nav)).toBe(true);
    expect(kannDateiTeilen(null, nav)).toBe(false);
  });

  test("canShare wirft: nein statt Absturz", () => {
    const nav = { share: jest.fn(), canShare: () => { throw new TypeError("x"); } };
    expect(kannDateiTeilen(datei, nav)).toBe(false);
  });
});

describe("dateiTeilen", () => {
  test("uebergibt Datei, Text und Titel und meldet 'geteilt'", async () => {
    const share = jest.fn().mockResolvedValue(undefined);
    const nav = { share, canShare: () => true };
    const r = await dateiTeilen({ datei, text: "Hallo", titel: "Kaufvertrag" }, nav);
    expect(r).toBe("geteilt");
    expect(share).toHaveBeenCalledWith({ files: [datei], text: "Hallo", title: "Kaufvertrag" });
  });

  test("Nutzer schliesst das Menue: 'abgebrochen', kein Fehler", async () => {
    const err = new Error("cancel"); err.name = "AbortError";
    const nav = { share: jest.fn().mockRejectedValue(err), canShare: () => true };
    expect(await dateiTeilen({ datei, text: "x" }, nav)).toBe("abgebrochen");
  });

  test("anderer Fehler oder kein Teilen: 'nicht_moeglich' (-> Link-Weg)", async () => {
    const nav = { share: jest.fn().mockRejectedValue(new Error("boom")), canShare: () => true };
    expect(await dateiTeilen({ datei, text: "x" }, nav)).toBe("nicht_moeglich");
    expect(await dateiTeilen({ datei, text: "x" }, {})).toBe("nicht_moeglich");
  });
});

describe("pdfDatei", () => {
  test("liefert eine PDF-Datei mit sauberem Namen und .pdf-Endung", () => {
    const f = pdfDatei(new Blob(["%PDF"]), "Kaufvertrag VW/Golf:2026");
    expect(f).toBeInstanceOf(File);
    expect(f.type).toBe("application/pdf");
    expect(f.name).toBe("Kaufvertrag VW_Golf_2026.pdf");
    expect(pdfDatei(new Blob(["x"])).name).toBe("Kaufvertrag.pdf");
  });
});
