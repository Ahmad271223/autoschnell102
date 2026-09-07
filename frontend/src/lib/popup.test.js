import { openInPopup, openMultiple } from "./popup";

function fensterAttrappe() {
  return { closed: false, opener: {}, focus: jest.fn(), location: { href: "" }, close: jest.fn() };
}

describe("openInPopup", () => {
  beforeEach(() => { window.innerWidth = 1400; });
  afterEach(() => { jest.restoreAllMocks(); });

  test("kappt opener VOR der Navigation und oeffnet nur EIN Fenster", () => {
    const w = fensterAttrappe();
    const open = jest.spyOn(window, "open").mockReturnValue(w);
    const r = openInPopup("https://suchen.mobile.de/x", "mobileFilterWindow");
    expect(open).toHaveBeenCalledTimes(1);
    expect(open.mock.calls[0][0]).toBe("");                     // erst leer ...
    expect(open.mock.calls[0][1]).toBe("mobileFilterWindow");   // benannter Reuse bleibt
    expect(w.opener).toBeNull();
    expect(w.location.href).toBe("https://suchen.mobile.de/x"); // ... dann navigieren
    expect(w.focus).toHaveBeenCalled();
    expect(r).toBe(w);
  });

  test("faellt bei Blocker auf _blank mit noopener,noreferrer zurueck", () => {
    const open = jest.spyOn(window, "open").mockReturnValue(null);
    expect(openInPopup("https://www.autoscout24.de/x", "autoscoutFilterWindow")).toBeNull();
    expect(open).toHaveBeenCalledTimes(2);
    expect(open.mock.calls[1][1]).toBe("_blank");
    expect(open.mock.calls[1][2]).toContain("noopener");
  });

  test("wirft nicht, wenn der opener-Setter cross-origin SecurityError liefert", () => {
    const w = fensterAttrappe();
    Object.defineProperty(w, "opener", { set() { throw new Error("SecurityError"); } });
    jest.spyOn(window, "open").mockReturnValue(w);
    expect(() => openInPopup("https://suchen.mobile.de/x", "mobileFilterWindow")).not.toThrow();
    expect(w.location.href).toBe("https://suchen.mobile.de/x");
  });

  test("kleine Bildschirme: direkt _blank mit noopener", () => {
    window.innerWidth = 500;
    const open = jest.spyOn(window, "open").mockReturnValue(null);
    expect(openInPopup("https://suchen.mobile.de/x")).toBeNull();
    expect(open).toHaveBeenCalledTimes(1);
    expect(open.mock.calls[0][2]).toContain("noopener");
  });
});

describe("openMultiple", () => {
  afterEach(() => { jest.restoreAllMocks(); });

  test("mehrere URLs: je ein _blank ohne Namen, mit noopener", () => {
    const open = jest.spyOn(window, "open").mockReturnValue(fensterAttrappe());
    openMultiple([{ url: "https://a/1", name: "a" }, { url: "https://b/2", name: "b" }]);
    expect(open).toHaveBeenCalledTimes(2);
    for (const call of open.mock.calls) {
      expect(call[1]).toBe("_blank");
      expect(call[2]).toContain("noopener");
    }
  });
});
