/**
 * Firmenseite + Kundenportal (Wunsch Ahmad 29.09.2026): Logo/Name/Über uns/Bilder/Kontakt aus
 * /public/firma, Code-Eingabe → Vertrag → Unterschrift → Bestätigung; Fehler bei falschem Code und
 * bei Drosselung; Vorprüfung der Adressen (lib/firmenHost).
 */
import { act, createElement } from "react";
import { createRoot } from "react-dom/client";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { afterEach, describe, expect, it, vi } from "vitest";

const get = vi.fn();
const post = vi.fn();
vi.mock("@/lib/api", () => ({
  api: { get: (...a) => get(...a), post: (...a) => post(...a), put: vi.fn(), delete: vi.fn() },
  errMsg: (e, f) => e?.response?.data?.detail || e?.message || f,
}));
const blobOeffnen = vi.fn();
vi.mock("@/lib/dateiOeffnen", () => ({ blobOeffnen: (...a) => blobOeffnen(...a) }));
vi.mock("@/lib/pdfAnzeige", () => ({ pdfSeitenRendern: vi.fn(async () => [document.createElement("canvas")]) }));
// Das echte Unterschriftenfeld braucht ein Canvas mit 2D-Kontext (jsdom hat keins) — hier ein Knopf,
// der "unterschreibt".
vi.mock("@/components/SignaturePad", () => ({
  default: ({ onChange, label }) => createElement("button", { type: "button", "data-testid": "pad", onClick: () => onChange("data:image/png;base64,AAAA") }, label),
}));

globalThis.IS_REACT_ACT_ENVIRONMENT = true;

const { default: FirmenSeite } = await import("./FirmenSeite");
const { istFirmenHostKandidat } = await import("@/lib/firmenHost");

const FIRMA = {
  slug: "kfz-mueller", firma: "KFZ Müller GmbH", logo_url: "/api/files/logo/d1/x.png", ueber_uns: "Wir kaufen Ihr Auto.",
  bilder: ["/api/files/firma/d1/a.jpg", "/api/files/firma/d1/b.jpg"],
  kontakt: { adresse: "Hauptstraße 1", plz: "12345", ort: "Berlin", telefon: "030 123", email: "info@example.org", oeffnungszeiten: "Mo–Fr 9–18" },
  url: "https://kfz-mueller.auto-schnellkauf.de", portal_aktiv: true,
  titelbild: "/api/files/firma/d1/a.jpg", titel: "Ihr Partner für den Autoankauf in Berlin", untertitel: "Schnell, sicher & fair",
  social: { facebook: "", instagram: "https://www.instagram.com/kfz" }, plattform_url: "https://app.auto-schnellkauf.de",
};
const VERTRAG = { contract_no: "KV-1", marke: "VW", modell: "Golf", verkaeufer: "Erika Mustermann", kaufpreis: 12500, firma: "KFZ Müller GmbH", version: 1, status: "offen" };

let wurzel = null;
let behaelter = null;
async function rendern(pfad = "/firma/kfz-mueller") {
  behaelter = document.createElement("div");
  document.body.appendChild(behaelter);
  wurzel = createRoot(behaelter);
  await act(async () => {
    wurzel.render(createElement(MemoryRouter, { initialEntries: [pfad] },
      createElement(Routes, null,
        createElement(Route, { path: "/firma/:slug", element: createElement(FirmenSeite) }),
        createElement(Route, { path: "*", element: createElement(FirmenSeite) }))));
  });
  return behaelter;
}
const el = (t) => behaelter.querySelector(`[data-testid="${t}"]`);
async function tippen(t, wert) {
  const feld = el(t);
  const setter = Object.getOwnPropertyDescriptor(feld.__proto__, "value").set;
  await act(async () => { setter.call(feld, wert); feld.dispatchEvent(new Event("input", { bubbles: true })); });
}
async function klick(t) { await act(async () => { el(t).click(); }); }

afterEach(() => {
  if (wurzel) act(() => wurzel.unmount());
  behaelter?.remove();
  wurzel = null;
  behaelter = null;
  vi.clearAllMocks();
});

describe("Firmenseite", () => {
  it("zeigt Firma, Über uns, Bilder, Kontakt und den Portal-Kasten", async () => {
    get.mockImplementation(async (url, cfg) => {
      if (url === "/public/firma") { expect(cfg.params).toEqual({ slug: "kfz-mueller" }); return { data: FIRMA }; }
      throw new Error("unerwartet " + url);
    });
    await rendern();
    expect(el("firmenseite-name").textContent).toBe("KFZ Müller GmbH");
    expect(el("firmenseite-logo").getAttribute("src")).toContain("/api/files/logo/d1/x.png");
    expect(el("firmenseite-ueber-uns").textContent).toContain("Wir kaufen Ihr Auto.");
    // Vorlage: erstes Bild = Titelbild, die weiteren unter "Über uns"; Überschrift/Unterzeile; Fußzeile mit Follow Us
    expect(el("firmenseite-titel").querySelector("img").getAttribute("src")).toContain("/api/files/firma/d1/a.jpg");
    expect(el("firmenseite-ueberschrift").textContent).toBe("Ihr Partner für den Autoankauf in Berlin");
    expect(el("firmenseite-unterzeile").textContent).toBe("Schnell, sicher & fair");
    expect(el("firmenseite-bilder").querySelectorAll("img").length).toBe(1);
    expect(el("firmenseite-kontakt").textContent).toContain("Hauptstraße 1, 12345 Berlin");
    expect(el("firmenseite-kontakt").textContent).toContain("Mo–Fr 9–18");
    expect(el("firmenseite-kontakt").textContent).toContain("Impressum");
    expect(el("firmenseite-social").querySelector('a[aria-label="Instagram"]').getAttribute("href")).toBe("https://www.instagram.com/kfz");
    expect(el("firmenseite-social").querySelector('a[aria-label="Facebook"]')).toBeNull();
    expect(el("firmenseite-datenschutz").getAttribute("href")).toBe("https://app.auto-schnellkauf.de/datenschutz");
    expect(el("nav-kundenportal").getAttribute("href")).toBe("#kundenportal");
    expect(behaelter.textContent).toContain("Vertrag digital einsehen und unterschreiben – sicher und bequem.");
    expect(el("kundenportal")).toBeTruthy();
    expect(el("portal-oeffnen").disabled).toBe(true);           // ohne 6 Zeichen kein Absenden
    expect(document.title).toContain("KFZ Müller GmbH");
  });

  it("unbekannte Adresse: klare Meldung statt Seite", async () => {
    get.mockRejectedValue({ response: { status: 404 } });
    await rendern("/irgendwas");
    expect(el("firmenseite-fehler").textContent).toContain("keine Firmenseite");
  });

  it("Code → Vertrag → Unterschrift → Bestätigung; falscher Code und Drossel melden sich", async () => {
    const blob = new Blob(["%PDF-1.4"], { type: "application/pdf" });
    get.mockImplementation(async (url, cfg) => {
      if (url === "/public/firma") return { data: FIRMA };
      if (url === "/public/portal/vertrag/pdf") {
        // Sitzung in der Kopfzeile, nicht in der Adresse (30.09.2026)
        expect(cfg.headers).toEqual({ "X-Portal-Sitzung": "tok" });
        return { data: blob };
      }
      throw new Error("unerwartet " + url);
    });
    post.mockImplementation(async (url, body, cfg) => {
      if (url === "/public/portal/oeffnen") {
        if (body.code === "ABCDEF") throw { response: { status: 404, data: { detail: "Code ungültig oder abgelaufen. Bitte den Code vom Autohaus prüfen." } } };
        if (body.code === "ZZZZZZ") throw { response: { status: 429 } };
        expect(body).toEqual({ code: "K7M3XP", slug: "kfz-mueller" });
        return { data: { sitzung: "tok", vertrag: VERTRAG, firma: FIRMA, sitzung_minuten: 45 } };
      }
      if (url === "/public/portal/vertrag/unterschreiben") {
        expect(body).toEqual({ signature_b64: "data:image/png;base64,AAAA", name: "Erika Mustermann", einverstanden: true });
        expect(cfg.headers).toEqual({ "X-Portal-Sitzung": "tok" });
        return { data: { ok: true, unterschrieben_am: "2026-09-29T10:00:00+00:00", contract_no: "KV-1" } };
      }
      throw new Error("unerwartet " + url);
    });
    await rendern();
    // Kleinbuchstaben und Sonderzeichen werden bereinigt, falscher Code -> Fehlertext
    await tippen("portal-code", "ab-cd ef");
    expect(el("portal-code").value).toBe("ABCDEF");
    await klick("portal-oeffnen");
    expect(el("portal-fehler").textContent).toContain("Code ungültig");
    await tippen("portal-code", "zzzzzz");
    await klick("portal-oeffnen");
    expect(el("portal-fehler").textContent).toContain("Zu viele Versuche");
    // richtiger Code: Vertrag mit Kurzdaten, Seiten gerendert, Unterschrift-Bereich
    await tippen("portal-code", "k7m3xp");
    await klick("portal-oeffnen");
    expect(el("portal-vertrag").textContent).toContain("KV-1");
    expect(el("portal-vertrag").textContent).toContain("VW Golf");
    expect(el("portal-vertrag").textContent).toContain("12.500");
    expect(el("portal-seiten").querySelectorAll("canvas").length).toBe(1);
    expect(el("portal-name").value).toBe("Erika Mustermann");
    // ohne Unterschrift/Zustimmung: Hinweis, kein Aufruf
    await klick("portal-absenden");
    expect(el("portal-fehler").textContent).toContain("unterschreiben");
    expect(post.mock.calls.some(([u]) => u === "/public/portal/vertrag/unterschreiben")).toBe(false);
    await klick("pad");
    await klick("portal-absenden");
    expect(el("portal-fehler").textContent).toContain("gelesen");
    await klick("portal-einverstanden");
    await klick("portal-absenden");
    expect(el("portal-fertig").textContent).toContain("Vielen Dank");
    expect(el("portal-unterschrift")).toBeNull();               // kein zweites Mal
    await klick("portal-pdf-fertig");
    expect(blobOeffnen).toHaveBeenCalled();
    // PDF-Vorschau lief nach der Unterschrift erneut (unterschriebene Fassung)
    expect(get.mock.calls.filter(([u]) => u === "/public/portal/vertrag/pdf").length).toBe(2);
    // kein Aufruf trägt die Sitzung in der Adresse
    expect([...get.mock.calls, ...post.mock.calls].some(([u]) => String(u).includes("tok"))).toBe(false);
  });
});

describe("Firmen-Host-Vorprüfung", () => {
  it("Hauptadresse, localhost und IPs sind nie Firmenseiten — Unterdomains und Kundendomains schon", () => {
    for (const h of ["app.auto-schnellkauf.de", "localhost", "127.0.0.1", "10.0.0.5", "kfz.localhost", "[::1]", ""]) {
      expect(istFirmenHostKandidat(h)).toBe(false);
    }
    for (const h of ["kfz-mueller.auto-schnellkauf.de", "KFZ-Mueller.de", "www.kfz-mueller.de."]) {
      expect(istFirmenHostKandidat(h)).toBe(true);
    }
  });
});
