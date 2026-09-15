/*
 * 11.09.2026 (Befund Ahmad): "abholung_geplant" stand als Kennung in der
 * Fahrzeugakte; der Untertitel der Bestandskarte brach nach "i…" ab.
 */
import {
  aktionText, beschreibungLesbar, datumDE, inseratText, kaufvorgangText, lesbar,
  lifecycleText, statusFarbe,
} from "./fahrzeugStatus";

describe("Status-Texte", () => {
  it("zeigt bekannte Kennungen als deutschen Text", () => {
    expect(lifecycleText("abholung_geplant")).toBe("Abholung geplant");
    expect(lifecycleText("bestand")).toBe("Im Bestand");
    expect(lifecycleText("geloescht")).toBe("Gelöscht");
    expect(lifecycleText("gefunden")).toBe("Gefunden");
    expect(kaufvorgangText("gesendet")).toBe("Vertrag gesendet");
    expect(kaufvorgangText("abholung_geplant")).toBe("Abholung geplant");
    expect(inseratText("entwurf")).toBe("Entwurf");
    expect(inseratText("zurueckgezogen")).toBe("Zurückgezogen");
  });

  it("macht unbekannte Kennungen lesbar statt sie roh zu zeigen", () => {
    expect(lifecycleText("neuer_zustand")).toBe("Neuer zustand");
    expect(kaufvorgangText("irgend_was__neues")).toBe("Irgend was neues");
    expect(lesbar("nicht abgeholt")).toBe("Nicht abgeholt");
  });

  it("macht Historie-Kennungen lesbar", () => {
    expect(aktionText("fahrzeug.status.abholung_geplant")).toBe("Status: Abholung geplant");
    expect(aktionText("fahrzeug.status.geloescht")).toBe("Status: Gelöscht");
    expect(aktionText("inserat.veroeffentlicht")).toBe("Inserat: Veröffentlicht");
    expect(aktionText("inserat.foto.entfernt")).toBe("Foto aus dem Inserat entfernt");
    expect(aktionText("termin.erstellt")).toBe("Abholtermin angelegt");
    expect(aktionText("abholung.bericht")).toBe("Abholbericht vom Fahrer eingereicht");
    expect(aktionText("fahrzeug.manuell.angelegt")).toBe("Fahrzeug manuell angelegt");
    expect(aktionText("vertrag.geloescht.frist")).toBe("Kaufvertrag gelöscht");
    expect(aktionText("etwas.ganz_neues")).toBe("Etwas ganz neues");
    expect(aktionText("vertrag..erstellt_neu")).toBe("Vertrag erstellt neu");
    expect(aktionText("")).toBe("—");
  });

  it("zeigt fuer leere Werte einen Strich", () => {
    expect(lesbar(null)).toBe("—");
    expect(lesbar(undefined)).toBe("—");
    expect(lifecycleText("")).toBe("—");
  });

  it("hat fuer jeden Status eine Farbe", () => {
    expect(statusFarbe("abholung_geplant")).toBe("#0a84ff");
    expect(statusFarbe("unbekannt")).toBe("#71717a");
    expect(statusFarbe(undefined)).toBe("#71717a");
  });
});

describe("beschreibungLesbar", () => {
  it("ersetzt die Sternchen von mobile.de durch Trenner mit Leerzeichen", () => {
    expect(beschreibungLesbar("i ADVANTAGE*AUTOM*5TRG*PDC*KLIM*TEMP*2xSH*EU6"))
      .toBe("i ADVANTAGE · AUTOM · 5TRG · PDC · KLIM · TEMP · 2xSH · EU6");
  });

  it("laesst leere Stuecke und Randsternchen weg", () => {
    expect(beschreibungLesbar("*320d**Touring * M Sport*")).toBe("320d · Touring · M Sport");
  });

  it("laesst normale Beschreibungen unveraendert", () => {
    expect(beschreibungLesbar("320d Touring M Sport")).toBe("320d Touring M Sport");
    expect(beschreibungLesbar(null)).toBe("");
  });
});

describe("datumDE", () => {
  it("wandelt ISO-Datum in deutsches Format", () => {
    expect(datumDE("2026-09-25")).toBe("25.09.2026");
  });

  it("laesst andere Werte unveraendert und zeigt fuer leer einen Strich", () => {
    expect(datumDE("25.09.2026")).toBe("25.09.2026");
    expect(datumDE("")).toBe("—");
    expect(datumDE(null)).toBe("—");
  });
});
