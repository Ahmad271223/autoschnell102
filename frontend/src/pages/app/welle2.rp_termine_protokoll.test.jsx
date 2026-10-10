/*
 * Rollenprüfung 22.09.2026 — Team termine_protokoll, Welle 2 (Übergaben):
 *  RP-464      Terminliste lädt still nach; „Vom Fahrer abgelehnt (n)“ mit Grund
 *  RP-542      Fahrer-Telefon als tel:-Link
 *  RP-146      Freigabe-Vermerk steht im Feld und lässt sich leeren
 *  RP-080/179  neuer Fahrer-Vorschlag ersetzt einen früheren Fahrer-Preis (Hinweis)
 */
import { describe, expect, it, vi } from "vitest";

vi.mock("sonner", () => ({ toast: { success: vi.fn(), error: vi.fn(), info: vi.fn(), warning: vi.fn() } }));

const { vomFahrerAbgelehnt, telHref, TERMINE_NACHLADEN_MS } = await import("./Termine");
const { default: TERMINE } = await import("./Termine.jsx?raw");
const { notizFuerSenden, vorschlagHinweis, entwurfUngespeichert } = await import("./Freigaben");
const { default: FREIGABEN } = await import("./Freigaben.jsx?raw");

describe("RP-464: Ablehnungen des Fahrers", () => {
  it("nur offene, nicht neu zugeteilte Ablehnungen, neueste zuerst", () => {
    const liste = vomFahrerAbgelehnt([
      { id: "a", zuteilung: "abgelehnt", status: "offen", zuteilung_beantwortet_am: "2026-09-22T08:00:00Z" },
      { id: "b", zuteilung: "abgelehnt", status: "offen", zuteilung_beantwortet_am: "2026-09-22T09:00:00Z" },
      { id: "c", zuteilung: "abgelehnt", status: "storniert" },
      { id: "d", zuteilung: "abgelehnt", status: "offen", driver_id: "neu" },
      { id: "e", zuteilung: "offen", status: "offen", driver_id: "x" },
    ]);
    expect(liste.map((a) => a.id)).toEqual(["b", "a"]);
    expect(vomFahrerAbgelehnt(null)).toEqual([]);
  });
  it("Grund im Klartext, Liste lädt still nach", () => {
    expect(TERMINE).toMatch(/data-testid="termine-abgelehnt-hinweis"/);
    expect(TERMINE).toMatch(/Vom Fahrer abgelehnt \(\{liste\.length\}\)/);
    expect(TERMINE).toMatch(/Grund: \$\{a\.zuteilung_abgelehnt_grund\}/);
    // nicht mehr NUR als Tooltip
    expect(TERMINE).not.toMatch(/title=\{a\.zuteilung_abgelehnt_grund \|\| ""\}/);
    expect(TERMINE_NACHLADEN_MS).toBe(60000);
    expect(TERMINE).toMatch(/addEventListener\("visibilitychange", sichtbar\)/);
    expect(TERMINE).toMatch(/window\.addEventListener\("focus", sichtbar\)/);
    expect(TERMINE).toMatch(/clearInterval\(t\)/);
  });
});

describe("RP-542: Fahrer anrufen", () => {
  it("tel:-Link nur mit wählbarer Nummer", () => {
    expect(telHref("0171 234 56-78")).toBe("tel:01712345678");
    expect(telHref("+49 (171) 2345678")).toBe("tel:+491712345678");
    expect(telHref("12+34+5")).toBe("tel:12345");
    expect(telHref("")).toBeNull();
    expect(telHref(null)).toBeNull();
    expect(telHref("abc")).toBeNull();
  });
  it("in der Zeile kein <a> im Knopf, im Dialog ein echter Link", () => {
    expect(TERMINE).toMatch(/data-testid=\{`fahrer-tel-\$\{a\.id\}`\}/);
    expect(TERMINE).toMatch(/<a href=\{telHref\(appt\.driver\.phone\)\} data-testid="edit-driver-tel"/);
  });
});

describe("RP-146: Vermerk leeren", () => {
  const e = { preis_notiz: "Rost am Schweller" };
  it("unberührt: nichts senden (Vermerk bleibt)", () => {
    expect(notizFuerSenden({}, e)).toBeUndefined();
    expect(notizFuerSenden(undefined, e)).toBeUndefined();
    expect(notizFuerSenden({ preis: "15.000" }, e)).toBeUndefined();
  });
  it("geleert: \"\" senden — nur wenn einer gespeichert ist, nie als Rückfrage", () => {
    expect(notizFuerSenden({ notiz: "  " }, e)).toBe("");
    expect(notizFuerSenden({ notiz: "" }, {})).toBeUndefined();
    expect(notizFuerSenden({ notiz: "" }, e, { zurueck: true })).toBeUndefined();
  });
  it("getippt: getrimmter Text", () => {
    expect(notizFuerSenden({ notiz: " Delle " }, e)).toBe("Delle");
    expect(notizFuerSenden({ notiz: "km?" }, e, { zurueck: true })).toBe("km?");
  });
  it("geleerter Vermerk zählt als ungespeichert, unverändert nicht", () => {
    const liste = [{ protocol_id: "p1", preis_notiz: "Rost" }];
    expect(entwurfUngespeichert({ p1: { notiz: "" } }, liste)).toBe(true);
    expect(entwurfUngespeichert({ p1: { notiz: " Rost " } }, liste)).toBe(false);
    expect(entwurfUngespeichert({ p1: {} }, liste)).toBe(false);
    expect(entwurfUngespeichert({ p1: { preis: "9.000" } }, liste)).toBe(true);
    expect(entwurfUngespeichert({ p2: { notiz: "neu" } }, liste)).toBe(true);
    expect(entwurfUngespeichert({}, null)).toBe(false);
  });
  it("das Feld zeigt den gespeicherten Vermerk", () => {
    expect(FREIGABEN).toMatch(/"notiz" in meinEntwurf \? \(meinEntwurf\.notiz \?\? ""\) : \(e\.preis_notiz \|\| ""\)/);
    expect(FREIGABEN).toMatch(/notizFuerSenden\(eigener, e, \{ zurueck \}\)/);
  });
});

describe("RP-080/179: Fahrer-Preis wird ersetzt", () => {
  it("Hinweis nur, wenn der geltende Preis vom Fahrer stammt und der neue Vorschlag abweicht", () => {
    const basis = { preis_quelle: "fahrer", neuer_preis: 9000, preis_vorschlag_fahrer: 9500 };
    expect(vorschlagHinweis(basis, false)).toMatch(/ersetzt den bisherigen Fahrer-Preis/);
    expect(vorschlagHinweis({ ...basis, preis_vorschlag_fahrer: 9000 }, false)).toBe("");
    expect(vorschlagHinweis({ ...basis, preis_quelle: "chef" }, false)).toBe("");
    expect(vorschlagHinweis({ ...basis, preis_vorschlag_verworfen: true }, false)).toMatch(/verworfen/);
  });
});
