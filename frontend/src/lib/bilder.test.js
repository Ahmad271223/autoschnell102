import { thumbSrc, thumbFehler } from "./bilder";

describe("thumbSrc", () => {
  test("Proxy-Pfad wird absolut, Original bleibt Ersatz", () => {
    const backend = process.env.REACT_APP_BACKEND_URL || "";
    expect(thumbSrc("/api/bild?u=x&exp=1&sig=2", "https://img.example/a.jpg"))
      .toBe(`${backend}/api/bild?u=x&exp=1&sig=2`);
    expect(thumbSrc(undefined, "https://img.example/a.jpg")).toBe("https://img.example/a.jpg");
    expect(thumbSrc("", "https://img.example/a.jpg")).toBe("https://img.example/a.jpg");
    expect(thumbSrc("https://cdn.example/t.jpg", "x")).toBe("https://cdn.example/t.jpg");
    expect(thumbSrc(null, null)).toBe("");
  });
});

describe("thumbFehler", () => {
  test("schaltet einmal auf das Original um, dann nie wieder", () => {
    const el = { src: "http://localhost/api/bild?u=x" };
    thumbFehler({ currentTarget: el }, "https://img.example/a.jpg");
    expect(el.src).toBe("https://img.example/a.jpg");
    thumbFehler({ currentTarget: el }, "https://img.example/a.jpg");
    expect(el.src).toBe("https://img.example/a.jpg");
    expect(() => thumbFehler(null, "x")).not.toThrow();
    expect(() => thumbFehler({ currentTarget: el }, "")).not.toThrow();
  });
});
