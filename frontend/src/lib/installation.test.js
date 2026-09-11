import {
  _zuruecksetzenFuerTests, installationsStand, installieren, istEdge, istMac, plattform,
} from "./installation";

const CHROME_WIN = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36";
const SAFARI_MAC = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.5 Safari/605.1.15";
const FIREFOX_WIN = "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:130.0) Gecko/20100101 Firefox/130.0";

describe("plattform", () => {
  test.each([
    ["iPhone Safari", "Mozilla/5.0 (iPhone; CPU iPhone OS 17_5 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.5 Mobile/15E148 Safari/604.1", 5, "ios"],
    ["iPhone Chrome", "Mozilla/5.0 (iPhone; CPU iPhone OS 17_5 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) CriOS/128.0.6613.98 Mobile/15E148 Safari/604.1", 5, "ios"],
    ["iPad (meldet sich als Mac, hat Touch)", SAFARI_MAC, 5, "ios"],
    ["Mac Safari", SAFARI_MAC, 0, "mac-safari"],
    ["Mac Chrome", "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36", 0, "desktop"],
    ["Windows Chrome", CHROME_WIN, 0, "windows"],
    ["Windows Edge", `${CHROME_WIN} Edg/128.0.0.0`, 0, "windows"],
    ["Windows Firefox", FIREFOX_WIN, 0, "keine"],
    ["Android Chrome", "Mozilla/5.0 (Linux; Android 14; Pixel 8) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/128.0.0.0 Mobile Safari/537.36", 5, "android"],
    ["Samsung Internet", "Mozilla/5.0 (Linux; Android 14; SM-S921B) AppleWebKit/537.36 (KHTML, like Gecko) SamsungBrowser/25.0 Chrome/121.0.0.0 Mobile Safari/537.36", 5, "android"],
    ["Android Firefox", "Mozilla/5.0 (Android 14; Mobile; rv:130.0) Gecko/130.0 Firefox/130.0", 5, "android-firefox"],
  ])("%s", (_name, userAgent, maxTouchPoints, erwartet) => {
    expect(plattform({ userAgent, maxTouchPoints })).toBe(erwartet);
  });

  test("Edge und Mac werden erkannt (eigene Hinweise)", () => {
    expect(istEdge({ userAgent: `${CHROME_WIN} Edg/128.0.0.0` })).toBe(true);
    expect(istEdge({ userAgent: CHROME_WIN })).toBe(false);
    expect(istMac({ userAgent: SAFARI_MAC, maxTouchPoints: 0 })).toBe(true);
    expect(istMac({ userAgent: SAFARI_MAC, maxTouchPoints: 5 })).toBe(false);   // iPad
  });
});

// jsdom meldet sich weder als Chrome noch als Safari — fuer die Zweige
// "anleitung"/Merker den Browser gezielt vorgeben.
function browser(userAgent, maxTouchPoints = 0) {
  Object.defineProperty(window.navigator, "userAgent", { value: userAgent, configurable: true });
  Object.defineProperty(window.navigator, "maxTouchPoints", { value: maxTouchPoints, configurable: true });
}

function meldung(outcome) {
  const e = new Event("beforeinstallprompt", { cancelable: true });
  e.prompt = vi.fn(() => Promise.resolve());
  e.userChoice = Promise.resolve({ outcome });
  return e;
}

describe("Installations-Stand", () => {
  const matchMediaVorher = window.matchMedia;

  beforeEach(() => {
    _zuruecksetzenFuerTests();
    window.localStorage.clear();
  });

  afterEach(() => {
    delete window.navigator.userAgent;         // eigene Eigenschaft weg -> jsdom-Standard
    delete window.navigator.maxTouchPoints;
    window.matchMedia = matchMediaVorher;
    window.localStorage.clear();
  });

  test("Firefox am PC ohne Meldung: kein Knopf", async () => {
    browser(FIREFOX_WIN);
    expect(installationsStand().art).toBe(null);
    await expect(installieren()).resolves.toBe(null);
  });

  test("Chrome ohne Meldung (noch keine Nutzung): Anleitung", () => {
    browser(CHROME_WIN);
    expect(installationsStand().art).toBe("anleitung");
  });

  test("hier schon installiert (Merker): kein Knopf im Browser-Tab", () => {
    browser(CHROME_WIN);
    window.localStorage.setItem("ah_app_installiert", "1");
    expect(installationsStand().art).toBe(null);
  });

  test("laeuft als App (eigenes Fenster): kein Knopf", () => {
    browser(CHROME_WIN);
    window.matchMedia = (q) => ({ matches: q === "(display-mode: standalone)" });
    expect(installationsStand().art).toBe(null);
  });

  test("Browser meldet 'installierbar' -> sein Dialog; nach der Installation kein Knopf mehr", async () => {
    browser(CHROME_WIN);
    const e = meldung("accepted");
    window.dispatchEvent(e);
    expect(e.defaultPrevented).toBe(true);        // keine eigene Leiste des Browsers
    expect(installationsStand().art).toBe("direkt");
    await expect(installieren()).resolves.toBe("angenommen");
    expect(e.prompt).toHaveBeenCalledTimes(1);
    window.dispatchEvent(new Event("appinstalled"));
    expect(window.localStorage.getItem("ah_app_installiert")).toBe("1");
    expect(installationsStand().art).toBe(null);  // Chrome-UA: ohne Merker waere es "anleitung"
  });

  test("abgelehnt: Meldung verbraucht, meldet sich der Browser neu, gilt sie wieder", async () => {
    browser(CHROME_WIN);
    window.localStorage.setItem("ah_app_installiert", "1");
    const e = meldung("dismissed");
    window.dispatchEvent(e);
    // Der Browser bietet die Installation an -> hier ist sie NICHT installiert.
    expect(window.localStorage.getItem("ah_app_installiert")).toBe(null);
    await expect(installieren()).resolves.toBe("abgelehnt");
    await expect(installieren()).resolves.toBe(null);
    expect(installationsStand().art).toBe("anleitung");
    window.dispatchEvent(meldung("accepted"));
    expect(installationsStand().art).toBe("direkt");
  });
});
