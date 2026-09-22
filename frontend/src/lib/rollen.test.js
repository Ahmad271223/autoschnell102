/*
 * Bereichstrennung im Browser (Befund 05.09.2026).
 *
 * Ausloeser: Ein Super-Admin sah "Fahrzeugbestand & Weiterverkauf" — die
 * Haendler-Seite — mit der Admin-Seitenleiste daneben und der Meldung
 * "Nur fuer Haendler-Accounts". Diese Tests halten fest, wer wohin darf.
 */
import {
  bereichVonPfad, bereichVonRolle, startseite, darfBereich, sicheresZiel, nichtGefundenZiel,
  BEREICH_FIRMA, BEREICH_ADMIN, BEREICH_MARKT, BEREICH_FAHRER,
} from "./rollen";

const CHEF = { role: "dealer" };
const SUCHER = { role: "sucher" };
const ADMIN = { role: "admin" };
const SUPER = { role: "admin", is_super_admin: true };
const KAEUFER = { role: "b2b_buyer" };

describe("Bereich einer Adresse", () => {
  test("jede geschuetzte Adresse hat einen Bereich", () => {
    expect(bereichVonPfad("/app/bestand")).toBe(BEREICH_FIRMA);
    expect(bereichVonPfad("/app")).toBe(BEREICH_FIRMA);
    expect(bereichVonPfad("/abo")).toBe(BEREICH_FIRMA);
    expect(bereichVonPfad("/admin")).toBe(BEREICH_ADMIN);
    expect(bereichVonPfad("/admin/users/5")).toBe(BEREICH_ADMIN);
    expect(bereichVonPfad("/markt")).toBe(BEREICH_MARKT);
    expect(bereichVonPfad("/fahrer/protokoll/7")).toBe(BEREICH_FAHRER);
  });

  test("oeffentliche Seiten haben keinen Bereich", () => {
    for (const p of ["/", "/login", "/impressum", "/agb", "/anfrage"]) {
      expect(bereichVonPfad(p)).toBeNull();
    }
  });

  test("aehnlich benannte Adressen zaehlen nicht als Bereich", () => {
    // "/appartements" faengt zwar mit "/app" an, ist aber keine App-Seite.
    expect(bereichVonPfad("/appartements")).toBeNull();
    expect(bereichVonPfad("/administration")).toBeNull();
  });
});

describe("Wohin gehoert wer", () => {
  test("jede Rolle hat genau einen Bereich", () => {
    expect(bereichVonRolle(CHEF)).toBe(BEREICH_FIRMA);
    expect(bereichVonRolle(SUCHER)).toBe(BEREICH_FIRMA);
    expect(bereichVonRolle(ADMIN)).toBe(BEREICH_ADMIN);
    expect(bereichVonRolle(SUPER)).toBe(BEREICH_ADMIN);
    expect(bereichVonRolle(KAEUFER)).toBe(BEREICH_MARKT);
  });

  test("Startseite je Rolle", () => {
    expect(startseite(CHEF)).toBe("/app/bestand");
    expect(startseite(SUCHER)).toBe("/app/vergleich");
    expect(startseite(ADMIN)).toBe("/admin");
    expect(startseite(SUPER)).toBe("/admin");
    expect(startseite(KAEUFER)).toBe("/markt");
  });

  test("U-154: eine unbekannte Rolle gehoert nirgends hin", () => {
    // Vorher: jede nicht genannte Rolle landete im Firmen-Bereich (/app/bestand).
    for (const fremd of [{ role: "driver" }, { role: "" }, { role: undefined }, {}]) {
      expect(bereichVonRolle(fremd)).toBeNull();
      expect(startseite(fremd)).toBe("/login");
      expect(darfBereich(fremd, BEREICH_FIRMA)).toBe(false);
      expect(darfBereich(fremd, BEREICH_ADMIN)).toBe(false);
      expect(sicheresZiel(fremd, "/app/bestand")).toBe("/login");
    }
  });
});

describe("U-148: Ausweg von einer unbekannten Adresse", () => {
  test("Angemeldete kommen auf ihre Startseite, nicht auf die Werbeseite", () => {
    expect(nichtGefundenZiel(SUCHER, "/app/vergleichh")).toBe("/app/vergleich");
    expect(nichtGefundenZiel(CHEF, "/app/vertraeg")).toBe("/app/bestand");
    expect(nichtGefundenZiel(SUPER, "/admin/nutzer")).toBe("/admin");
    expect(nichtGefundenZiel(CHEF, "/irgendwas")).toBe("/app/bestand");
  });

  test("Fahrer- und Marktplatz-Adressen fuehren zu deren Einstieg", () => {
    // Beide haben eine eigene Anmeldung — auch ohne Firmen-Konto nicht auf "/".
    expect(nichtGefundenZiel(null, "/fahrer/protokol/7")).toBe("/fahrer");
    expect(nichtGefundenZiel(CHEF, "/fahrer/x")).toBe("/fahrer");
    expect(nichtGefundenZiel(null, "/markt/merklist")).toBe("/markt");
  });

  test("ohne Anmeldung: Firmen-/Admin-Adressen zur Anmeldung, sonst Startseite", () => {
    expect(nichtGefundenZiel(null, "/app/termin")).toBe("/login");
    expect(nichtGefundenZiel(null, "/admin/x")).toBe("/login");
    expect(nichtGefundenZiel(null, "/abo/x")).toBe("/login");
    expect(nichtGefundenZiel(null, "/tippfehler")).toBe("/");
  });
});

describe("Der eigentliche Befund: Admin darf nicht auf Haendler-Seiten", () => {
  test("Admin und Super-Admin haben im Firmen-Bereich nichts zu suchen", () => {
    expect(darfBereich(ADMIN, BEREICH_FIRMA)).toBe(false);
    expect(darfBereich(SUPER, BEREICH_FIRMA)).toBe(false);
  });

  test("und umgekehrt Haendler nicht im Admin-Bereich", () => {
    expect(darfBereich(CHEF, BEREICH_ADMIN)).toBe(false);
    expect(darfBereich(SUCHER, BEREICH_ADMIN)).toBe(false);
  });

  test("Zwischenhaendler bleibt auf dem Marktplatz", () => {
    expect(darfBereich(KAEUFER, BEREICH_FIRMA)).toBe(false);
    expect(darfBereich(KAEUFER, BEREICH_ADMIN)).toBe(false);
    expect(darfBereich(KAEUFER, BEREICH_MARKT)).toBe(true);
  });

  test("im eigenen Bereich ist alles erlaubt", () => {
    expect(darfBereich(CHEF, BEREICH_FIRMA)).toBe(true);
    expect(darfBereich(SUPER, BEREICH_ADMIN)).toBe(true);
  });
});

describe("Anmeldung folgt keinem fremden Ziel", () => {
  test("Super-Admin auf /login?next=/app/bestand landet im Admin-Bereich", () => {
    // Genau der Weg aus dem Befund: abgemeldet auf einer Haendler-Seite,
    // danach meldet sich der Betreiber an.
    expect(sicheresZiel(SUPER, "/app/bestand")).toBe("/admin");
  });

  test("Haendler auf /login?next=/admin landet in seinem Bestand", () => {
    expect(sicheresZiel(CHEF, "/admin/users")).toBe("/app/bestand");
  });

  test("passendes Ziel wird beibehalten", () => {
    expect(sicheresZiel(CHEF, "/app/termine")).toBe("/app/termine");
    expect(sicheresZiel(SUPER, "/admin/betrieb")).toBe("/admin/betrieb");
    expect(sicheresZiel(SUCHER, "/app/vergleich")).toBe("/app/vergleich");
  });

  test("ohne Ziel geht es auf die Startseite", () => {
    expect(sicheresZiel(SUCHER, null)).toBe("/app/vergleich");
    expect(sicheresZiel(SUCHER, "")).toBe("/app/vergleich");
  });

  test("fremde Adressen werden verworfen", () => {
    // Sonst waere die Anmeldung eine Weiterleitung auf beliebige Seiten.
    for (const boes of ["https://beispiel.invalid/phishing",
                        "//beispiel.invalid/phishing",
                        "javascript:alert(1)",
                        "http://app.auto-schnellkauf.de.beispiel.invalid/"]) {
      expect(sicheresZiel(CHEF, boes)).toBe("/app/bestand");
    }
  });

  test("oeffentliche Ziele bleiben erlaubt", () => {
    expect(sicheresZiel(CHEF, "/impressum")).toBe("/impressum");
  });

  test("Backslash, Tab und Zeilenumbruch werden verworfen", () => {
    // Kontonummer (13.09.2026): Browser lesen "/\\x" und "/\t/x" wie "//x".
    for (const boes of ["/\\fremd.example", "/\t/fremd.example", "/\n/fremd.example", "/markt\\..\\x"]) {
      expect(sicheresZiel(CHEF, boes)).toBe("/app/bestand");
    }
  });

  test("Kaeufer-Anmeldung: nur eigene Marktplatz- oder oeffentliche Ziele", () => {
    // Kontonummer (13.09.2026): BuyerLogin prueft ?next= ueber diese Funktion.
    const KAEUFER = { role: "b2b_buyer" };
    expect(sicheresZiel(KAEUFER, "https://fremd.example")).toBe("/markt");
    expect(sicheresZiel(KAEUFER, "//fremd.example")).toBe("/markt");
    expect(sicheresZiel(KAEUFER, "/app/bestand")).toBe("/markt");
    expect(sicheresZiel(KAEUFER, null)).toBe("/markt");
    expect(sicheresZiel(KAEUFER, "/markt/merkliste")).toBe("/markt/merkliste");
  });
});
