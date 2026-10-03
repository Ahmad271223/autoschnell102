/*
 * Prüfbericht 20.09.2026, Reparaturwelle A3 (22.09.2026) — Vertrag/Versand/Archiv.
 *  U-73  Fassung aus der Kopfzeile X-Vertrag-Version, mitgeschickt beim Teilen
 *  U-83  Rückfrage beim Idempotenz-Konflikt mit Preis des vorhandenen Vertrags
 *  U-82  nacharbeit_hinweis / bereits_vorhanden nach dem Anlegen
 *  K-14  "erneut" -> noch einmal tippen; Link-Weg nur nach Rückfrage
 *  U-89  "Fertig — zum Vertragsarchiv"
 *  U-92  contract?. und Rücksetzen beim Vertragswechsel, key im Archiv
 *  U-93  status_vermerk = nicht_gespeichert wird gemeldet
 *  U-88  Hinweis "Nach der Abholung neu erstellt" auch ohne Versandmerker,
 *        Fassungs-Tooltip kennt alle Gründe
 *  M-07/M-11/M-12/M-13/M-20  Dialog-Semantik, Trefferfläche, Eingabemodi,
 *        Raster, Reiter
 *  M-16  Wisch in der Galerie schließt sie nicht
 */
import { describe, expect, it, vi } from "vitest";

vi.mock("sonner", () => ({ toast: { success: vi.fn(), error: vi.fn(), info: vi.fn(), warning: vi.fn() } }));

const { fassungAusKopf } = await import("./SendDialog");
const { idempotenzKonfliktFrage } = await import("./ContractDialog");
const { nachAbholungTeile, fassungGrundText } = await import("@/pages/app/PDFArchiv");

const { default: SEND } = await import("./SendDialog.jsx?raw");
const { default: VERTRAG } = await import("./ContractDialog.jsx?raw");
const { default: ARCHIV } = await import("@/pages/app/PDFArchiv.jsx?raw");
const { default: BESTAND } = await import("@/pages/app/Bestand.jsx?raw");
const { default: KORREKTUR } = await import("./VerkaeuferKorrekturDialog.jsx?raw");
const { default: FOLGE } = await import("./FolgeMailDialog.jsx?raw");

describe("U-73: Fassung der vorab geladenen Datei", () => {
  it("liest die Kopfzeile (klein/groß), sonst null", () => {
    expect(fassungAusKopf({ "x-vertrag-version": "3" })).toBe(3);
    expect(fassungAusKopf({ "X-Vertrag-Version": "2" })).toBe(2);
    expect(fassungAusKopf({})).toBeNull();
    expect(fassungAusKopf(undefined)).toBeNull();
    expect(fassungAusKopf({ "x-vertrag-version": "abc" })).toBeNull();
    expect(fassungAusKopf({ "x-vertrag-version": "0" })).toBeNull();
  });
  it("wird zum Blob gemerkt und beim Teilen mitgeschickt; 409 lädt neu", () => {
    expect(SEND).toMatch(/pdfFassung\.current = fassungAusKopf\(r\.headers\)/);
    expect(SEND).toMatch(/methode: "teilen",\s*\n\s*\.\.\.\(fassung \? \{ version: fassung \} : \{\}\)/);
    expect(SEND).toMatch(/d\?\.code === "fassung_veraltet"/);
    const teilen = SEND.slice(SEND.indexOf("const teilen = async"), SEND.indexOf("const beweisAnfordern"));
    expect(teilen).toMatch(/setPdfStand\(\(n\) => n \+ 1\)/);
    // kein Erfolgs-Toast, wenn die alte Fassung geteilt wurde
    expect(teilen).toMatch(/if \(vermerkt\) \{/);
  });
});

describe("U-83 / U-82: Anlegen des Kaufvertrags", () => {
  it("Rückfrage nennt den Preis des vorhandenen Vertrags", () => {
    const f = idempotenzKonfliktFrage({ contract_id: "c1", purchase_price: 12500 });
    expect(f).toMatch(/Kaufpreis 12\.500,00\s€/);   // Intl setzt ein geschütztes Leerzeichen
    expect(f).toMatch(/OK = jetzt trotzdem einen NEUEN Kaufvertrag/);
    expect(f).toMatch(/Abbrechen = nichts anlegen/);
    expect(idempotenzKonfliktFrage({})).not.toMatch(/Kaufpreis/);
    expect(idempotenzKonfliktFrage(null)).toMatch(/schon ein Kaufvertrag angelegt\./);
  });
  it("neuer Schlüssel (auch im Entwurf), Formular bleibt, vorhandener Vertrag zum Öffnen", () => {
    const submit = VERTRAG.slice(VERTRAG.indexOf("const submit = async"), VERTRAG.indexOf("return (\n"));
    expect(submit).toMatch(/d\?\.code === "idempotenz_konflikt"/);
    expect(submit).toMatch(/idempotenz\.current = neuerIdempotenzSchluessel\(\);\s*\n\s*if \(bearbeitet\.current\) \{\s*\n\s*entwurfSpeichern\(/);
    expect(submit).toMatch(/window\.confirm\(idempotenzKonfliktFrage\(d\)\)/);
    expect(submit).toMatch(/openContractPdf\(d\.contract_id\)/);
    // RP-416 (zweiter Vertrag) bleibt erhalten
    expect(submit).toMatch(/zweiter_vertrag_bestaetigt: true/);
  });
  it("U-82: Nacharbeit als Warnung, 'bereits vorhanden' als Hinweis", () => {
    expect(VERTRAG).toMatch(/if \(data\?\.nacharbeit_hinweis\) toast\.warning\(data\.nacharbeit_hinweis/);
    expect(VERTRAG).toMatch(/if \(data\?\.bereits_vorhanden\) \{\s*\n\s*toast\.info\("Dieser Kaufvertrag war schon angelegt/);
  });
});

describe("K-14 / U-89 / U-92 / U-93: Versand-Dialog", () => {
  const teilen = SEND.slice(SEND.indexOf("const teilen = async"), SEND.indexOf("const beweisAnfordern"));
  it("K-14: 'erneut' bittet um einen zweiten Tipp; der Link-Weg nur nach Rückfrage", () => {
    expect(teilen).toMatch(/ergebnis === "erneut"/);
    expect(teilen).toMatch(/toast\.warning\("Bitte noch einmal auf „Per WhatsApp teilen“ tippen\."\)/);
    expect(teilen).toMatch(/window\.confirm\("Teilen ist auf diesem Gerät nicht möglich\./);
    expect(teilen).not.toMatch(/toast\.info\("Teilen ist auf diesem Gerät nicht möglich/);
    // send("whatsapp") steht NUR im bestätigten Zweig
    const nachFrage = teilen.slice(teilen.indexOf("window.confirm("));
    expect(nachFrage).toMatch(/await send\("whatsapp"\)/);
    expect(teilen.slice(0, teilen.indexOf("window.confirm("))).not.toMatch(/send\("whatsapp"\)/);
  });
  it("U-89: Fertig-Knopf sagt, was er tut", () => {
    expect(SEND).toMatch(/Fertig — zum Vertragsarchiv/);
    expect(SEND).toMatch(/toast\.success\("Der Vertrag liegt im Vertragsarchiv"\)/);
    expect(SEND).not.toMatch(/toast\.success\("PDF gespeichert"\)|\/> PDF speichern/);
    expect(SEND).toMatch(/data-testid="save-only-btn"/);
  });
  it("U-92: contract?. und Rücksetzen je Vertrag; Archiv gibt key mit", () => {
    expect(SEND).toMatch(/useState\(contract\?\.seller_phone \|\| ""\)/);
    expect(SEND).toMatch(/useState\(contract\?\.seller_email \|\| ""\)/);
    expect(SEND).toMatch(/setPhone\(contract\?\.seller_phone \|\| ""\);\s*\n\s*setEmail\(contract\?\.seller_email \|\| ""\);/);
    expect(SEND).toMatch(/\}, \[contract\?\.id\]\);/);
    expect(ARCHIV).toMatch(/<SendDialog open contract=\{senden\} key=\{senden\.id\}/);
  });
  it("U-93: fehlender Vermerk wird nach jedem Versandweg gemeldet", () => {
    expect(SEND).toMatch(/data\?\.status_vermerk === "nicht_gespeichert"/);
    expect((SEND.match(/vermerkPruefen\(data\)/g) || []).length).toBe(2);
  });
});

describe("U-88 / M-16: Vertragsarchiv", () => {
  it("Änderungsliste nach der Abholung", () => {
    expect(nachAbholungTeile({ preis: 11900, preis_vorher: 12500, felder: ["vehicle_vin", "x"],
                               neue_schaeden: 2, sondervereinbarung: true }))
      .toEqual(["neuer Preis 12.500 € → 11.900 €", "FIN", "x", "2 neue(r) Schaden/Schäden", "Sondervereinbarung"]);
    expect(nachAbholungTeile({ preis: 9000 })).toEqual(["neuer Preis 9.000 €"]);
    expect(nachAbholungTeile(null)).toEqual([]);
  });
  it("Hinweis bleibt nach dem Versand stehen — nur der Senden-Knopf hängt am Merker", () => {
    const hinweis = ARCHIV.slice(ARCHIV.indexOf("function NachAbholungHinweis("), ARCHIV.indexOf("function SpecsZeile("));
    expect(hinweis).toMatch(/if \(!ae\) return null;/);
    expect(hinweis).not.toMatch(/if \(!item\.nach_abholung_versand_offen \|\| !ae\) return null;/);
    expect(hinweis).toMatch(/\{offen && \(\s*\n\s*<button onClick=\{onSenden\} data-testid=\{`nach-abholung-senden-\$\{item\.id\}`\}/);
  });
  it("Fassungs-Tooltip kennt alle Archiv-Gründe des Backends", () => {
    expect(fassungGrundText("abholtermin_geaendert")).toBe("Abholtermin geändert");
    expect(fassungGrundText("abholung_abgeschlossen")).toBe("Preis/Daten nach Abholung");
    expect(fassungGrundText("verkaeufer_korrigiert")).toBe("Verkäuferdaten korrigiert");
    expect(fassungGrundText("sonstwas")).toBe("");
    expect(fassungGrundText(undefined)).toBe("");
    expect(ARCHIV).toMatch(/fassungGrundText\(v\.grund\)/);
  });
  it("M-16: der Klick nach einem Wisch schließt die Galerie nicht", () => {
    const galerie = ARCHIV.slice(ARCHIV.indexOf("function GalleryViewer("));
    expect(galerie).toMatch(/const gewischt = useRef\(false\)/);
    expect(galerie).toMatch(/if \(gewischt\.current\) \{ gewischt\.current = false; return; \}/);
    expect(galerie).toMatch(/onClick=\{hintergrundKlick\}/);
    expect(galerie).toMatch(/onTouchStart = \(e\) => \{ gewischt\.current = false;/);
  });
});

describe("M-07 / M-11 / M-12 / M-13 / M-20: Dialoge", () => {
  it("M-07: alle sechs Dialoge nutzen useModal und tragen role=dialog am Rahmen", () => {
    for (const [name, q] of [["SendDialog", SEND], ["ContractDialog", VERTRAG], ["Bestand", BESTAND],
                             ["VerkaeuferKorrekturDialog", KORREKTUR], ["FolgeMailDialog", FOLGE]]) {
      expect(q, name).toMatch(/import \{ MODAL_ATTRIBUTE, useModal \} from "@\/lib\/useModal"/);
      expect(q, name).toMatch(/ref=\{dialogRef\} \{\.\.\.MODAL_ATTRIBUTE\} aria-labelledby="/);
    }
    // Kaufvertrag: Escape nur über schliessen() (Rückfrage bei eigenen Eingaben)
    expect(VERTRAG).toMatch(/useModal\(schliessen, \{ offen: Boolean\(open\) \}\)/);
    expect(SEND).toMatch(/useModal\(onClose, \{ offen: Boolean\(open\) \}\)/);
    // Bestand/Korrektur/Folge-Mail: nicht während des Speicherns/Versands
    expect(BESTAND).toMatch(/useModal\(\(\) => \{ if \(!busy\) onClose\?\.\(\); \}\)/);
    expect(KORREKTUR).toMatch(/useModal\(\(\) => \{ if \(!arbeitet\) onClose\?\.\(null\); \}/);
    expect(FOLGE).toMatch(/useModal\(\(\) => \{ if \(!sendet\) onClose\?\.\(gesendet\); \}/);
  });
  it("M-11: Schließen-Knöpfe mit 44-px-Trefferfläche und Namen, Kopfzeilen sticky", () => {
    for (const q of [SEND, VERTRAG, BESTAND]) {
      expect(q).toMatch(/className="w-11 h-11 -mr-2 (shrink-0 )?flex items-center justify-center rounded-full/);
      expect(q).toMatch(/sticky top-0/);
    }
    expect(SEND).toMatch(/data-testid="close-send" aria-label="Schließen"/);
    expect(VERTRAG).toMatch(/data-testid="close-contract"\s*\n\s*aria-label="Kaufvertrag schließen"/);
    expect(BESTAND).toMatch(/aria-label="Schließen" data-testid="manuell-schliessen"/);
    expect(BESTAND).not.toMatch(/<button onClick=\{onClose\} className="text-zinc-400 hover:text-white"><X size=\{20\} \/><\/button>/);
  });
  it("M-12: Zifferntastatur und Telefonfelder", () => {
    for (const id of ["contract-veh-ccm", "contract-veh-kw", "contract-veh-ps", "contract-veh-doors",
                      "contract-veh-seats", "contract-seller-zip", "contract-dealer-zip"]) {
      const feld = VERTRAG.slice(VERTRAG.lastIndexOf("<Field", VERTRAG.indexOf(`testid="${id}"`)),
                                 VERTRAG.indexOf(`testid="${id}"`));
      expect(feld, id).toMatch(/inputMode="numeric"/);
    }
    for (const id of ["contract-seller-phone", "contract-dealer-phone", "contract-dealer-wa"]) {
      const feld = VERTRAG.slice(VERTRAG.lastIndexOf("<Field", VERTRAG.indexOf(`testid="${id}"`)),
                                 VERTRAG.indexOf(`testid="${id}"`));
      expect(feld, id).toMatch(/type="tel" autoComplete="tel"/);
    }
    expect(SEND).toMatch(/testid="wa-phone"\s*\n\s*type="tel" inputMode="tel" autoComplete="tel"/);
    expect(SEND).toMatch(/inputMode=\{inputMode\} autoComplete=\{autoComplete\}/);
  });
  it("M-13: keine festen Zwei-/Dreispalter mehr im Kaufvertrag", () => {
    const formular = VERTRAG.slice(VERTRAG.indexOf("<form onSubmit={submit}"));
    expect(formular).not.toMatch(/className="grid grid-cols-2 gap-3"/);
    expect(formular).not.toMatch(/className="grid grid-cols-3 gap-3"/);
    // 26.09.2026: +1 durch den Abschnitt „Nummern“ (Vertrags-/Kundennummer)
    expect((formular.match(/grid grid-cols-1 sm:grid-cols-2 gap-3/g) || []).length).toBe(6);
    expect(formular).toMatch(/grid grid-cols-1 sm:grid-cols-3 gap-3/);
  });
  it("M-20: Versandweg als Reiter", () => {
    expect(SEND).toMatch(/role="tablist" aria-label="Versandweg"/);
    expect(SEND).toMatch(/<button type="button" onClick=\{onClick\} data-testid=\{testid\} role="tab" aria-selected=\{active\}/);
  });
});
