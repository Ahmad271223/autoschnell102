import { toast } from "sonner";
import { openInPopup, openMultiple } from "./popup";
import { filterOeffnen, FILTER_TOAST_ID } from "./filterOeffnen";

vi.mock("sonner", () => ({ toast: { warning: vi.fn(), dismiss: vi.fn() } }));

function fensterAttrappe() {
  return { closed: false, opener: {}, focus: vi.fn(), location: { href: "" }, close: vi.fn() };
}

describe("openInPopup", () => {
  beforeEach(() => { window.innerWidth = 1400; });
  afterEach(() => { vi.restoreAllMocks(); });

  test("kappt opener VOR der Navigation und oeffnet nur EIN Fenster", () => {
    const w = fensterAttrappe();
    const open = vi.spyOn(window, "open").mockReturnValue(w);
    const r = openInPopup("https://suchen.mobile.de/x", "mobileFilterWindow");
    expect(open).toHaveBeenCalledTimes(1);
    expect(open.mock.calls[0][0]).toBe("");                     // erst leer ...
    expect(open.mock.calls[0][1]).toBe("mobileFilterWindow");   // benannter Reuse bleibt
    expect(w.opener).toBeNull();
    expect(w.location.href).toBe("https://suchen.mobile.de/x"); // ... dann navigieren
    expect(w.focus).toHaveBeenCalled();
    expect(r).toBe(w);
  });

  test("Popup blockiert: Ersatz benannter Tab ohne noopener", () => {
    const tab = fensterAttrappe();
    const open = vi.spyOn(window, "open").mockReturnValueOnce(null).mockReturnValueOnce(tab);
    const r = openInPopup("https://www.autoscout24.de/x", "autoscoutFilterWindow");
    expect(open).toHaveBeenCalledTimes(2);
    expect(open.mock.calls[1]).toEqual(["", "autoscoutFilterWindow"]);
    expect(tab.opener).toBeNull();
    expect(tab.location.href).toBe("https://www.autoscout24.de/x");
    expect(r).toBe(tab);
  });

  test("alles blockiert: Rueckgabe null (erkennbar, nicht still)", () => {
    const open = vi.spyOn(window, "open").mockReturnValue(null);
    expect(openInPopup("https://www.autoscout24.de/x", "autoscoutFilterWindow")).toBeNull();
    expect(open).toHaveBeenCalledTimes(2);
    for (const call of open.mock.calls) expect(call[2] || "").not.toContain("noopener");
  });

  test("wirft nicht, wenn der opener-Setter cross-origin SecurityError liefert", () => {
    const w = fensterAttrappe();
    Object.defineProperty(w, "opener", { set() { throw new Error("SecurityError"); } });
    vi.spyOn(window, "open").mockReturnValue(w);
    expect(() => openInPopup("https://suchen.mobile.de/x", "mobileFilterWindow")).not.toThrow();
    expect(w.location.href).toBe("https://suchen.mobile.de/x");
  });

  test("kleine Bildschirme: benannter Tab, opener vor Navigation gekappt", () => {
    window.innerWidth = 500;
    const reihenfolge = [];
    const w = { closed: false, focus: vi.fn(), location: {} };
    Object.defineProperty(w, "opener", { set(v) { reihenfolge.push(`opener=${v}`); } });
    Object.defineProperty(w.location, "href", { set(v) { reihenfolge.push(`href=${v}`); } });
    const open = vi.spyOn(window, "open").mockReturnValue(w);
    expect(openInPopup("https://suchen.mobile.de/x", "mobileFilterWindow")).toBe(w);
    expect(open).toHaveBeenCalledTimes(1);
    expect(open.mock.calls[0]).toEqual(["", "mobileFilterWindow"]);
    expect(reihenfolge).toEqual(["opener=null", "href=https://suchen.mobile.de/x"]);
  });

  test("kleine Bildschirme blockiert: null", () => {
    window.innerWidth = 500;
    vi.spyOn(window, "open").mockReturnValue(null);
    expect(openInPopup("https://suchen.mobile.de/x")).toBeNull();
  });
});

describe("openMultiple", () => {
  beforeEach(() => { window.innerWidth = 1400; });
  afterEach(() => { vi.restoreAllMocks(); });

  test("zwei URLs: zwei benannte Tabs, zweiter blockiert -> Rueckgabe enthaelt ihn", () => {
    const a = fensterAttrappe();
    const open = vi.spyOn(window, "open").mockReturnValueOnce(a).mockReturnValueOnce(null);
    const b = { url: "https://b/2", name: "b" };
    const r = openMultiple([{ url: "https://a/1", name: "a" }, b]);
    expect(open).toHaveBeenCalledTimes(2);
    expect(open.mock.calls[0]).toEqual(["", "a"]);
    expect(open.mock.calls[1]).toEqual(["", "b"]);
    expect(a.opener).toBeNull();
    expect(a.location.href).toBe("https://a/1");
    expect(r).toEqual([b]);
  });

  test("alle offen: leere Liste; leere Eingabe: leere Liste ohne Aufruf", () => {
    const open = vi.spyOn(window, "open").mockImplementation(() => fensterAttrappe());
    expect(openMultiple([{ url: "https://a/1", name: "a" }, { url: "https://b/2", name: "b" }])).toEqual([]);
    open.mockClear();
    expect(openMultiple([{ url: "", name: "a" }])).toEqual([]);
    expect(open).not.toHaveBeenCalled();
  });

  test("ein Eintrag: blockiert -> Eintrag zurueck", () => {
    vi.spyOn(window, "open").mockReturnValue(null);
    const e = { url: "https://a/1", name: "a" };
    expect(openMultiple([e])).toEqual([e]);
  });

  // Wunsch Ahmad 18.09.2026: "nicht komplett neues Fenster" — ein einzelnes
  // Portal geht als benannter TAB auf (window.open OHNE Groessenangaben).
  test("ein Eintrag: benannter Tab statt eigenem Fenster", () => {
    const tab = fensterAttrappe();
    const open = vi.spyOn(window, "open").mockReturnValue(tab);
    expect(openMultiple([{ url: "https://suchen.mobile.de/x", name: "mobileFilterWindow" }])).toEqual([]);
    expect(open).toHaveBeenCalledTimes(1);
    // Zwei Argumente = Tab; ein drittes (features) waere ein Popup-Fenster.
    expect(open.mock.calls[0]).toEqual(["", "mobileFilterWindow"]);
    expect(tab.opener).toBeNull();
    expect(tab.location.href).toBe("https://suchen.mobile.de/x");
  });
});

describe("filterOeffnen", () => {
  const mobile = { url: "https://suchen.mobile.de/x", name: "mobileFilterWindow", label: "mobile.de" };
  const as24 = { url: "https://www.autoscout24.de/x", name: "autoscoutFilterWindow", label: "AutoScout24" };

  beforeEach(() => { window.innerWidth = 1400; toast.warning.mockClear(); toast.dismiss.mockClear(); });
  afterEach(() => { vi.restoreAllMocks(); });

  test("alles offen: kein Hinweis, ein alter Hinweis wird geschlossen", () => {
    vi.spyOn(window, "open").mockImplementation(() => fensterAttrappe());
    expect(filterOeffnen([mobile, as24])).toEqual([]);
    expect(toast.warning).not.toHaveBeenCalled();
    // Gegenpruefung Runde 22: sonst boete ein stehender Hinweis weiter die
    // Links des vorherigen Fahrzeugs an.
    expect(toast.dismiss).toHaveBeenCalledWith(FILTER_TOAST_ID);
  });

  test("Blockade: EIN Hinweis mit Aktion, die den blockierten Eintrag oeffnet", () => {
    const open = vi.spyOn(window, "open")
      .mockReturnValueOnce(fensterAttrappe())   // mobile.de geht auf
      .mockReturnValueOnce(null);               // AutoScout24 blockiert
    expect(filterOeffnen([mobile, as24])).toEqual([as24]);
    expect(toast.warning).toHaveBeenCalledTimes(1);
    expect(toast.dismiss).not.toHaveBeenCalled();         // Hinweis ersetzt, nicht geschlossen
    const [titel, opts] = toast.warning.mock.calls[0];
    expect(titel).toBe("AutoScout24 vom Browser blockiert");
    expect(opts.id).toBe(FILTER_TOAST_ID);
    expect(opts.description).toContain("Immer zulassen");
    expect(opts.action.label).toBe("AutoScout24 öffnen");

    // Klick auf die Aktion = neue Geste -> oeffnet AutoScout24
    const tab = fensterAttrappe();
    open.mockReset().mockReturnValue(tab);
    const event = { preventDefault: vi.fn() };
    opts.action.onClick(event);
    expect(open.mock.calls[0][1]).toBe("autoscoutFilterWindow");
    expect(tab.location.href).toBe(as24.url);
    expect(event.preventDefault).not.toHaveBeenCalled();  // alles offen -> Hinweis darf schliessen
    expect(toast.warning).toHaveBeenCalledTimes(1);
    expect(toast.dismiss).toHaveBeenCalledWith(FILTER_TOAST_ID);
  });

  test("automatisch, beide blockiert: Aktion oeffnet das erste, Hinweis kommt fuer den Rest wieder", () => {
    const open = vi.spyOn(window, "open").mockReturnValue(null);
    expect(filterOeffnen([mobile, as24], { automatisch: true })).toEqual([mobile, as24]);
    const [titel, opts] = toast.warning.mock.calls[0];
    // K-22: Portale im Titel, am Knopf steht, dass danach noch eines aussteht.
    expect(titel).toBe("Automatisches Öffnen vom Browser blockiert: mobile.de und AutoScout24");
    expect(opts.action.label).toBe("mobile.de öffnen (1 von 2)");

    open.mockReset().mockReturnValueOnce(fensterAttrappe()).mockReturnValueOnce(null);
    const event = { preventDefault: vi.fn() };
    opts.action.onClick(event);
    expect(open.mock.calls[0][1]).toBe("mobileFilterWindow");
    expect(event.preventDefault).toHaveBeenCalled();       // Hinweis bleibt stehen ...
    const [titel2, opts2] = toast.warning.mock.calls[1];   // ... aktualisiert fuer den Rest
    expect(titel2).toBe("AutoScout24 vom Browser blockiert");
    expect(opts2.id).toBe(FILTER_TOAST_ID);
    expect(opts2.action.label).toBe("AutoScout24 öffnen");   // nur noch eines: kein "1 von"
  });
});

// 15.09.2026 (Wunsch Ahmad): Filter-Fenster neben der App / zweiter Bildschirm
describe("fensterPlatz", () => {
  const app = { screenX: 0, screenY: 0, outerWidth: 1200, outerHeight: 1000 };
  const screen = { availWidth: 2560, availHeight: 1400, availLeft: 0, availTop: 0 };

  test("ohne Schalter: zentriert ueber der App (bisheriges Verhalten)", async () => {
    const { fensterPlatz } = await import("./popup");
    const p = fensterPlatz({ width: 1280, height: 1000, app, screen, daneben: false });
    expect(p.left).toBe(0);                       // (1200-1280)/2 < 0 -> 0
    expect(p.width).toBe(1280);
  });

  test("App links, rechts ist Platz: Fenster rechts daneben", async () => {
    const { fensterPlatz } = await import("./popup");
    const p = fensterPlatz({ width: 1280, height: 1000, app, screen, daneben: true });
    expect(p.left).toBe(1200);
    expect(p.width).toBe(1280);
    expect(p.top).toBe(0);
  });

  test("App rechts: Fenster links daneben; kein Platz: wieder zentriert", async () => {
    const { fensterPlatz } = await import("./popup");
    const rechts = { screenX: 1400, screenY: 20, outerWidth: 1100, outerHeight: 900 };
    const p = fensterPlatz({ width: 1280, height: 1000, app: rechts, screen, daneben: true });
    expect(p.left).toBe(1400 - 1280);
    const eng = fensterPlatz({ width: 1280, height: 1000,
      app: { screenX: 0, screenY: 0, outerWidth: 2560, outerHeight: 1400 }, screen, daneben: true });
    expect(eng.left).toBe(Math.floor((2560 - 1280) / 2));
  });

  test("zweiter Bildschirm: ganzer anderer Bildschirm", async () => {
    const { fensterPlatz } = await import("./popup");
    const anderer = { availLeft: 2560, availTop: 0, availWidth: 1920, availHeight: 1050 };
    const p = fensterPlatz({ width: 1280, height: 1000, app, screen, anderer, daneben: true });
    expect(p).toEqual({ left: 2560, top: 0, width: 1920, height: 1050 });
  });
});
