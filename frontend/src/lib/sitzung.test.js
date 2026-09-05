/*
 * Token je Tab (Befund 05.09.2026): Zwei Tabs, zwei Konten, keine
 * Vermischung. jsdom gibt jedem Test frische Storages; ein zweiter Tab
 * wird hier durch Leeren von sessionStorage bei erhaltenem localStorage
 * nachgestellt — genau so verhaelt sich ein neuer Browser-Tab.
 */
const { tokenLesen, tokenSetzen, tokenLoeschen, TOKEN_APP } = require("./sitzung");

beforeEach(() => {
  window.sessionStorage.clear();
  window.localStorage.clear();
});

function neuerTab() {
  // Ein neuer Tab hat leeren sessionStorage, teilt aber localStorage.
  window.sessionStorage.clear();
}

test("Anmeldung landet im Tab und als letzte Anmeldung", () => {
  tokenSetzen(TOKEN_APP, "admin");
  expect(window.sessionStorage.getItem(TOKEN_APP)).toBe("admin");
  expect(window.localStorage.getItem(TOKEN_APP)).toBe("admin");
  expect(tokenLesen()).toBe("admin");
});

test("Der eigentliche Befund: zweiter Tab meldet anderes Konto an, erster behaelt seins", () => {
  tokenSetzen(TOKEN_APP, "admin");            // Tab A
  const tabA = window.sessionStorage.getItem(TOKEN_APP);
  neuerTab();                                  // Tab B
  tokenSetzen(TOKEN_APP, "firma");
  expect(tokenLesen()).toBe("firma");          // Tab B ist Firma
  // Zurueck in Tab A: dessen sessionStorage ist unveraendert.
  window.sessionStorage.setItem(TOKEN_APP, tabA);
  expect(tokenLesen()).toBe("admin");          // Tab A bleibt Admin
  expect(window.localStorage.getItem(TOKEN_APP)).toBe("firma"); // letzte Anmeldung
});

test("Ein neuer Tab macht bei der letzten Anmeldung weiter", () => {
  tokenSetzen(TOKEN_APP, "firma");
  neuerTab();
  expect(tokenLesen()).toBe("firma");
  expect(window.sessionStorage.getItem(TOKEN_APP)).toBe("firma");
});

test("Abmelden in Tab B loescht Tab A nicht", () => {
  tokenSetzen(TOKEN_APP, "admin");            // Tab A
  const tabA = window.sessionStorage.getItem(TOKEN_APP);
  neuerTab();
  tokenSetzen(TOKEN_APP, "firma");            // Tab B
  tokenLoeschen();                             // Tab B meldet sich ab
  expect(tokenLesen()).toBeNull();             // Tab B ist leer
  expect(window.localStorage.getItem(TOKEN_APP)).toBeNull(); // letzte war Firma -> weg
  window.sessionStorage.setItem(TOKEN_APP, tabA);
  expect(tokenLesen()).toBe("admin");          // Tab A unveraendert
});

test("Abmelden loescht die letzte Anmeldung nur, wenn sie das eigene Konto war", () => {
  tokenSetzen(TOKEN_APP, "admin");            // Tab A
  neuerTab();
  tokenSetzen(TOKEN_APP, "firma");            // Tab B, letzte Anmeldung = firma
  window.sessionStorage.setItem(TOKEN_APP, "admin"); // zurueck in Tab A
  tokenLoeschen();                             // Tab A meldet Admin ab
  expect(window.localStorage.getItem(TOKEN_APP)).toBe("firma"); // Firma bleibt letzte
});

test("Ohne Storage bricht nichts", () => {
  const orig = window.sessionStorage;
  Object.defineProperty(window, "sessionStorage", {
    configurable: true,
    get() { throw new Error("gesperrt"); },
  });
  expect(() => tokenSetzen(TOKEN_APP, "x")).not.toThrow();
  expect(tokenLesen()).toBe("x");              // faellt auf localStorage zurueck
  expect(() => tokenLoeschen()).not.toThrow();
  Object.defineProperty(window, "sessionStorage", { configurable: true, value: orig });
});
