/*
 * Rollenprüfung 22.09.2026, Welle 2 — Käufer-Anmeldung und Sitzung
 * (Team markt_kaeufer, context/BuyerContext.jsx).
 *
 * RP-557  Anmeldung schickt den Geräte-Schlüssel mit und legt den neuen ab
 * RP-546  X-Neues-Token wird übernommen — aber nur für das aktuelle Token
 *         (Welle 3: dieselbe Regel wie Händler-/Fahrer-App, lib/api
 *         neuesTokenUebernehmen -> sitzung.tokenErneuern)
 */
import { act, createElement as h } from "react";
import { createRoot } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

globalThis.IS_REACT_ACT_ENVIRONMENT = true;

const { BuyerAuthProvider, buyerApi, useBuyer } = await import("./BuyerContext");
const { GERAET_SCHLUESSEL } = await import("./AuthContext");
const {
  LETZTE_ANMELDUNG, TOKEN_APP, TOKEN_KAEUFER, tokenLesen, tokenLoeschen, tokenSetzen,
} = await import("@/lib/sitzung");

// Token-förmig (drei Teile) — tokenErneuern legt nichts anderes ab.
const ALT = "aaa.bbb.alt";
const FRISCH = "aaa.bbb.frisch";
const FRUEHER = "aaa.bbb.frueher";

let root;
let host;

beforeEach(() => {
  window.localStorage.clear();
  window.sessionStorage.clear();
});

afterEach(async () => {
  if (root) await act(async () => { root.unmount(); });
  host?.remove();
  root = null;
  vi.restoreAllMocks();
});

/** Alle Antwort-Abfänger der Käufer-Verbindung der Reihe nach (wie axios). */
function antwortDurchAbfaenger(antwort) {
  return buyerApi.interceptors.response.handlers
    .filter(Boolean)
    .reduce((r, hd) => (hd.fulfilled ? hd.fulfilled(r) : r), antwort);
}

describe("BuyerContext (Welle 2)", () => {
  it("RP-557: Anmeldung sendet den gemerkten Geräte-Schlüssel und merkt den neuen", async () => {
    window.localStorage.setItem(GERAET_SCHLUESSEL, "alterSchluessel_1234567");
    const post = vi.spyOn(buyerApi, "post").mockResolvedValue({
      data: { token: "t1", user: { id: "k1", role: "b2b_buyer" }, geraet_id: "neuerSchluessel_abcdefg" },
    });
    vi.spyOn(buyerApi, "get").mockResolvedValue({ data: { id: "k1", role: "b2b_buyer" } });
    let ctx;
    function Fang() { ctx = useBuyer(); return null; }
    host = document.createElement("div");
    document.body.appendChild(host);
    root = createRoot(host);
    await act(async () => { root.render(h(BuyerAuthProvider, null, h(Fang))); });
    await act(async () => { await ctx.login("6FE7K2M", "geheim"); });
    expect(post).toHaveBeenCalledWith("/buyer/login", {
      kontonummer: "6FE7K2M", password: "geheim", geraet_id: "alterSchluessel_1234567",
    });
    expect(window.localStorage.getItem(GERAET_SCHLUESSEL)).toBe("neuerSchluessel_abcdefg");
    expect(tokenLesen(TOKEN_KAEUFER)).toBe("t1");
  });

  it("RP-546: X-Neues-Token ersetzt das aktuelle Token", () => {
    tokenSetzen(TOKEN_KAEUFER, ALT);
    antwortDurchAbfaenger({
      data: {}, headers: { "x-neues-token": FRISCH },
      config: { headers: { Authorization: `Bearer ${ALT}` } },
    });
    expect(tokenLesen(TOKEN_KAEUFER)).toBe(FRISCH);
    expect(window.localStorage.getItem(TOKEN_KAEUFER)).toBe(FRISCH);
  });

  it("RP-546: späte Antwort einer früheren Anmeldung überschreibt nichts", () => {
    tokenSetzen(TOKEN_KAEUFER, ALT);
    antwortDurchAbfaenger({
      data: {}, headers: { "x-neues-token": FRISCH },
      config: { headers: { Authorization: `Bearer ${FRUEHER}` } },
    });
    expect(tokenLesen(TOKEN_KAEUFER)).toBe(ALT);
    // ohne Anmeldung (kein Token) wird nichts angelegt
    window.sessionStorage.clear();
    window.localStorage.clear();
    antwortDurchAbfaenger({ data: {}, headers: { "x-neues-token": FRISCH }, config: { headers: {} } });
    expect(tokenLesen(TOKEN_KAEUFER)).toBeNull();
  });

  it("RP-546 (Welle 3): kein Müll, kein Wiederbeleben, fremde letzte Anmeldung bleibt", () => {
    tokenSetzen(TOKEN_KAEUFER, ALT);
    // Kein Token-förmiger Wert -> nichts ablegen
    antwortDurchAbfaenger({ data: {}, headers: { "x-neues-token": "kaputt" },
                            config: { headers: { Authorization: `Bearer ${ALT}` } } });
    expect(tokenLesen(TOKEN_KAEUFER)).toBe(ALT);
    // Ein anderer Tab hat sich zuletzt als Firma angemeldet: die Verlängerung
    // des Käufer-Tokens lässt "letzte Anmeldung" stehen (vorher tokenSetzen).
    window.localStorage.setItem(LETZTE_ANMELDUNG, TOKEN_APP);
    antwortDurchAbfaenger({ data: {}, headers: { "x-neues-token": FRISCH },
                            config: { headers: { Authorization: `Bearer ${ALT}` } } });
    expect(tokenLesen(TOKEN_KAEUFER)).toBe(FRISCH);
    expect(window.localStorage.getItem(LETZTE_ANMELDUNG)).toBe(TOKEN_APP);
    // Abgemeldeter Tab: eine späte Antwort mit neuem Token meldet nicht wieder an.
    tokenLoeschen(TOKEN_KAEUFER);
    antwortDurchAbfaenger({ data: {}, headers: { "x-neues-token": "aaa.bbb.spaet" },
                            config: { headers: { Authorization: `Bearer ${FRISCH}` } } });
    expect(tokenLesen(TOKEN_KAEUFER)).toBeNull();
  });
});
