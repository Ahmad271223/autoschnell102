/*
 * Rollenprüfung 22.09.2026 — Team markt_kaeufer (Marktplatz der Zwischenhändler).
 *
 * RP-501/RP-512  deutsche Zahlen in Filtern und Angeboten ("150.000" km, "12.500" €)
 * RP-504         "Unfallfrei" nur aus der Angabe des Händlers
 * RP-505         Händler-Titel statt Privatverkäufer-Text
 * RP-507         leerer km-Stand ist nicht "0 km"
 * RP-511         Einladung eines Partners ohne Konto wird gemerkt
 * RP-520         Zähler/Neu-Markierung für "Meine Anfragen"
 * RP-531         Entwurf überlebt die Weiterleitung zur Anmeldung
 * RP-500         Abmelden schickt den Token mit (Server-Sitzung endet)
 */
import { act, createElement as h } from "react";
import { createRoot } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import {
  angebotsFrage, beendetText, betragPruefen, einladungMerken, einladungVergessen, entwurfLesen,
  entwurfLoeschen, entwurfMerken, fahrzeugTitel, filterParameter, gemerkteEinladung, istNeu,
  kmAnzeige, neuigkeitenZahl, psAusText, unfallZeile, verlaufZeile,
} from "./marktHilfen";

globalThis.IS_REACT_ACT_ENVIRONMENT = true;

beforeEach(() => {
  window.localStorage.clear();
  window.sessionStorage.clear();
});

describe("RP-501/RP-512: Filter deutsch lesen", () => {
  it("km mit Tausenderpunkt und Tkm, Preise deutsch, PS ganzzahlig", () => {
    const { params, fehler } = filterParameter({
      make: "VW", km_min: "", km_max: "150.000", ps_min: "150 PS", price_max: "15.000", price_min: "9.999,50",
    });
    expect(fehler).toBeNull();
    expect(params).toEqual({ make: "VW", km_max: "150000", ps_min: "150", price_max: "15000", price_min: "9999.5" });
    expect(filterParameter({ km_max: "150 Tkm" }).params.km_max).toBe("150000");
  });
  it("Unsinn wird gemeldet statt still falsch gefiltert", () => {
    expect(filterParameter({ km_max: "95.5" }).fehler).toMatch(/Kilometer bis.*ganze Zahl/);
    expect(filterParameter({ ps_min: "viel" }).fehler).toMatch(/PS von/);
    expect(filterParameter({ price_min: "abc" }).fehler).toMatch(/Preis von/);
    expect(filterParameter({ km_max: "95.5" }).params).toEqual({});
  });
  it("psAusText", () => {
    expect(psAusText("")).toBeNull();
    expect(psAusText("1.000")).toBe(1000);
    expect(Number.isNaN(psAusText("1,5"))).toBe(true);
  });
});

describe("RP-501: Angebotsbetrag", () => {
  it("12.500 ist zwölftausendfünfhundert, nicht 12,50", () => {
    expect(betragPruefen("12.500")).toEqual({ betrag: 12500, fehler: null });
    expect(betragPruefen("20.900,00")).toEqual({ betrag: 20900, fehler: null });
    expect(betragPruefen("12,50").betrag).toBe(12.5);
  });
  it("leer nur, wenn optional; Unsinn und 0 werden gemeldet", () => {
    expect(betragPruefen("", { optional: true })).toEqual({ betrag: null, fehler: null });
    expect(betragPruefen("").fehler).toBeTruthy();
    expect(betragPruefen("zwölf").fehler).toMatch(/12\.500/);
    expect(betragPruefen("0").fehler).toMatch(/größer als 0/);
  });
  it("Rückfrage nennt den gelesenen Betrag deutsch", () => {
    expect(angebotsFrage(12500)).toMatch(/Dein Angebot: 12\.500,00/);
    expect(angebotsFrage(18000, "Gegenangebot")).toMatch(/Dein Gegenangebot: 18\.000,00/);
  });
});

describe("RP-504/RP-505/RP-507: Anzeige", () => {
  it("Unfallfrei nur aus accident_free, Portal-Schaden nur als Hinweis", () => {
    expect(unfallZeile({ accident_free: "Ja" })).toEqual(["Unfallfrei", "Ja"]);
    expect(unfallZeile({ accident_free: false })).toEqual(["Unfallfrei", "Nein"]);
    expect(unfallZeile({ accident_damaged: false })).toBeNull();
    expect(unfallZeile({ unfallschaden_laut_einkauf: true })).toEqual(["Unfallschaden", "laut Einkaufsinserat"]);
    expect(unfallZeile({})).toBeNull();
  });
  it("Titel des Händlers, Marke/Modell als Unterzeile", () => {
    expect(fahrzeugTitel({ title: "BMW 320d Touring M Sport", data: { make_label: "BMW", model_label: "320" } }))
      .toEqual({ titel: "BMW 320d Touring M Sport", unterzeile: "BMW 320" });
    expect(fahrzeugTitel({ title: "", data: { make_label: "VW", model_label: "Golf" } }))
      .toEqual({ titel: "VW Golf", unterzeile: "" });
    expect(fahrzeugTitel({ data: {} }).titel).toBe("Fahrzeug");
  });
  it("leerer km-Stand ist nicht 0 km", () => {
    expect(kmAnzeige("")).toBeNull();
    expect(kmAnzeige(null)).toBeNull();
    expect(kmAnzeige("abc")).toBeNull();
    expect(kmAnzeige(0)).toBe("0 km");
    expect(kmAnzeige(85120)).toBe("85.120 km");
  });
  it("Verlauf und Abschluss lesbar", () => {
    expect(verlaufZeile({ von: "system", aktion: "netzwerk_entfernt" })).toMatch(/^System · Beendet — der Händler hat dich aus seinem Netzwerk entfernt/);
    expect(verlaufZeile({ von: "kaeufer", aktion: "gegenangebot", angebot: 12500 })).toMatch(/^Du · Gegenangebot · 12\.500,00/);
    expect(beendetText({ status: "abgelehnt", beendet_grund: "kaeufer_zurueckgezogen" })).toBe("Von dir zurückgezogen");
    expect(beendetText({ status: "abgelehnt" })).toBe("Abgelehnt");
    expect(beendetText({ status: "offen" })).toBeNull();
  });
});

describe("RP-511: Einladung merken", () => {
  it("merken, lesen, vergessen, Ablauf", () => {
    const jetzt = Date.now();
    expect(gemerkteEinladung()).toBe("");
    einladungMerken("tok123", jetzt);
    expect(gemerkteEinladung(jetzt + 1000)).toBe("tok123");
    expect(gemerkteEinladung(jetzt + 31 * 24 * 3600 * 1000)).toBe("");
    expect(gemerkteEinladung()).toBe("");      // abgelaufen wird entfernt
    einladungMerken("tok456");
    einladungVergessen();
    expect(gemerkteEinladung()).toBe("");
  });
  it("kaputter Speicherinhalt wirft nicht", () => {
    window.localStorage.setItem("ah_kaeufer_einladung", "{kaputt");
    expect(gemerkteEinladung()).toBe("");
  });
});

describe("RP-531: Entwurf", () => {
  it("je Art getrennt, nur in dieser Sitzung", () => {
    entwurfMerken("anfrage", { listing_id: "l1", betrag: "12.500", nachricht: "Hallo" });
    expect(entwurfLesen("anfrage")).toMatchObject({ listing_id: "l1", betrag: "12.500", nachricht: "Hallo" });
    expect(entwurfLesen("gegenangebot")).toBeNull();
    entwurfLoeschen();
    expect(entwurfLesen("anfrage")).toBeNull();
  });
});

describe("RP-520: Neuigkeiten", () => {
  it("Summe aus am_zug und neu", () => {
    expect(neuigkeitenZahl({ am_zug: 2, neu: 1 })).toBe(3);
    expect(neuigkeitenZahl(null)).toBe(0);
  });
  it("neu nur, wenn zuletzt Händler/System gehandelt hat und später als gesehen", () => {
    const gesehen = "2026-09-22T10:00:00.000Z";
    const it1 = { updated_at: "2026-09-22T10:05:00.123456+00:00", history: [{ von: "haendler" }] };
    expect(istNeu(it1, gesehen)).toBe(true);
    expect(istNeu({ ...it1, history: [{ von: "kaeufer" }] }, gesehen)).toBe(false);
    expect(istNeu({ ...it1, updated_at: "2026-09-22T09:00:00+00:00" }, gesehen)).toBe(false);
    expect(istNeu(it1, "")).toBe(false);
  });
});

describe("RP-500: Abmelden beendet die Server-Sitzung", () => {
  let root;
  let host;
  afterEach(async () => {
    if (root) await act(async () => { root.unmount(); });
    host?.remove();
    root = null;
    vi.restoreAllMocks();
  });

  it("schickt den Token ausdrücklich mit, bevor er gelöscht wird", async () => {
    const { BuyerAuthProvider, buyerApi, useBuyer } = await import("@/context/BuyerContext");
    const { TOKEN_KAEUFER, tokenLesen, tokenSetzen } = await import("@/lib/sitzung");
    tokenSetzen(TOKEN_KAEUFER, "tok-abc");
    vi.spyOn(buyerApi, "get").mockResolvedValue({ data: { id: "k1", role: "b2b_buyer" } });
    const post = vi.spyOn(buyerApi, "post").mockResolvedValue({ data: { ok: true } });
    let ctx;
    function Fuehler() { ctx = useBuyer(); return null; }
    host = document.createElement("div");
    document.body.appendChild(host);
    root = createRoot(host);
    await act(async () => { root.render(h(BuyerAuthProvider, null, h(Fuehler))); });
    await act(async () => { ctx.logout(); });
    expect(post).toHaveBeenCalledWith("/auth/logout", null,
      { headers: { Authorization: "Bearer tok-abc" } });
    expect(tokenLesen(TOKEN_KAEUFER)).toBeFalsy();
    expect(ctx.buyer).toBeNull();
  });
});
