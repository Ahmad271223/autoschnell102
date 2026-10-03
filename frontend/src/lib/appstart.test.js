import { startZiel } from "./appstart";
import {
  LETZTE_ANMELDUNG, TOKEN_APP, TOKEN_FAHRER, TOKEN_KAEUFER, anmeldeartVormerken, letzteAnmeldung, tokenSetzen,
} from "./sitzung";

const sucher = { role: "sucher" };
const chef = { role: "dealer" };
const superAdmin = { role: "admin", is_super_admin: true };

describe("startZiel — Einstieg der installierten App (/start)", () => {
  test("nicht angemeldet: die Anmeldung, die zuletzt benutzt wurde", () => {
    expect(startZiel({ letzte: TOKEN_APP })).toBe("/login");
    expect(startZiel({ letzte: TOKEN_FAHRER })).toBe("/fahrer/login");
    expect(startZiel({ letzte: TOKEN_KAEUFER })).toBe("/markt/login");
  });

  test("nichts bekannt (neues Geraet, iPhone-App): keine Vermutung, /start zeigt die Auswahl", () => {
    expect(startZiel()).toBe(null);
    expect(startZiel({ letzte: "unbekannt" })).toBe(null);
  });

  test("angemeldet: direkt auf die eigene Startseite", () => {
    expect(startZiel({ user: sucher })).toBe("/app/vergleich");
    expect(startZiel({ user: chef })).toBe("/app/bestand");
    expect(startZiel({ user: superAdmin })).toBe("/admin");
    expect(startZiel({ driver: { id: "f1" } })).toBe("/fahrer");
    expect(startZiel({ buyer: { id: "k1" } })).toBe("/markt");
  });

  test("mehrere Anmeldungen im selben Browser: die zuletzt benutzte gewinnt", () => {
    expect(startZiel({ user: chef, driver: { id: "f1" }, letzte: TOKEN_FAHRER })).toBe("/fahrer");
    expect(startZiel({ user: chef, driver: { id: "f1" }, letzte: TOKEN_APP })).toBe("/app/bestand");
    // Zuletzt als Fahrer angemeldet, der ist aber abgemeldet: die noch aktive Firma.
    expect(startZiel({ user: chef, letzte: TOKEN_FAHRER })).toBe("/app/bestand");
  });
});

describe("letzteAnmeldung", () => {
  afterEach(() => {
    window.localStorage.clear();
    window.sessionStorage.clear();
  });

  test("jede Anmeldung merkt sich ihre Art — nie den Token", () => {
    tokenSetzen(TOKEN_FAHRER, "geheim-1");
    expect(letzteAnmeldung()).toBe(TOKEN_FAHRER);
    expect(window.localStorage.getItem(LETZTE_ANMELDUNG)).toBe("ah_driver_token");
    tokenSetzen(TOKEN_APP, "geheim-2");
    expect(letzteAnmeldung()).toBe(TOKEN_APP);
  });

  test("Anmeldeseite nur aufgerufen: vorgemerkt, eine echte Anmeldung gewinnt aber", () => {
    anmeldeartVormerken(TOKEN_FAHRER);
    expect(letzteAnmeldung()).toBe(TOKEN_FAHRER);
    anmeldeartVormerken(TOKEN_KAEUFER);                // ueberschreibt nichts
    expect(letzteAnmeldung()).toBe(TOKEN_FAHRER);
    tokenSetzen(TOKEN_APP, "geheim-3");
    expect(letzteAnmeldung()).toBe(TOKEN_APP);
  });
});
