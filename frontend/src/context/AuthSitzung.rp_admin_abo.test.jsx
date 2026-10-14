/*
 * Rollenpruefung 22.09.2026 (Review): Nach einer Token-Verlaengerung
 * (X-Neues-Token, RP-546) liegt im Tab ein NEUES Token derselben Sitzung.
 * Ein spaeterer refresh() mit Netzfehler darf die geladene Seite dann nicht
 * verwerfen (Runde-22-Schutz) — nur eine wirklich andere Anmeldung (andere
 * Sitzungs-ID) verwirft die alten Daten.
 */
import { act, createElement as h } from "react";
import { createRoot } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

globalThis.IS_REACT_ACT_ENVIRONMENT = true;

const antworten = [];
vi.mock("@/lib/api", () => ({
  api: {
    get: vi.fn(async () => {
      const naechste = antworten.shift();
      if (naechste instanceof Error) throw naechste;
      return naechste;
    }),
    post: vi.fn(async () => ({ data: {} })),
  },
  aboNeuLadenAnmelden: vi.fn(),
}));

const { AuthProvider, useAuth, sitzungVonToken } = await import("./AuthContext");

function b64u(obj) {
  return btoa(JSON.stringify(obj)).replace(/\+/g, "-").replace(/\//g, "_").replace(/=+$/, "");
}
function jwt(inhalt) {
  return `${b64u({ alg: "HS256", typ: "JWT" })}.${b64u(inhalt)}.signatur`;
}
function netzfehler() {
  const e = new Error("Network Error");
  e.code = "ERR_NETWORK";
  return e;
}
const ME = { data: { user: { id: "u1", role: "sucher" }, dealer: { id: "d1" },
                     subscription: { active: true } } };

let root;
let host;
let ctx;
function Fang() { ctx = useAuth(); return null; }

beforeEach(() => {
  antworten.length = 0;
  try { sessionStorage.clear(); localStorage.clear(); } catch { /* egal */ }
});
afterEach(async () => {
  if (root) await act(async () => { root.unmount(); });
  host?.remove();
  root = null;
});

async function starten(token) {
  sessionStorage.setItem("ah_token", token);
  antworten.push(ME);
  host = document.createElement("div");
  document.body.appendChild(host);
  root = createRoot(host);
  await act(async () => { root.render(h(AuthProvider, null, h(Fang))); });
  expect(ctx.user?.id).toBe("u1");
}

describe("sitzungVonToken", () => {
  it("erkennt dieselbe Sitzung trotz neuem Ablauf, eine andere sid nicht", () => {
    const a = jwt({ sub: "u1", sid: "s1", exp: 100, seit: 1 });
    const b = jwt({ sub: "u1", sid: "s1", exp: 999999, seit: 1 });
    const c = jwt({ sub: "u1", sid: "s2", exp: 100, seit: 1 });
    expect(a).not.toBe(b);
    expect(sitzungVonToken(a)).toBe(sitzungVonToken(b));
    expect(sitzungVonToken(a)).not.toBe(sitzungVonToken(c));
  });

  it("faellt bei unlesbaren Tokens auf das Token selbst zurueck", () => {
    expect(sitzungVonToken(null)).toBeNull();
    expect(sitzungVonToken("")).toBeNull();
    expect(sitzungVonToken("kein-jwt")).toBe("t:kein-jwt");
    expect(sitzungVonToken("a.%%%.c")).toBe("t:a.%%%.c");
    const ohneSid = jwt({ sub: "u1", exp: 1 });
    expect(sitzungVonToken(ohneSid)).toBe(`t:${ohneSid}`);
  });
});

describe("refresh() nach einer Token-Verlaengerung", () => {
  it("Netzfehler mit verlaengertem Token derselben Sitzung: Seite bleibt", async () => {
    await starten(jwt({ sub: "u1", sid: "s1", exp: 100 }));
    // X-Neues-Token: der Interceptor legt ein frisches Token derselben Sitzung ab.
    sessionStorage.setItem("ah_token", jwt({ sub: "u1", sid: "s1", exp: 999999 }));
    antworten.push(netzfehler());
    await act(async () => { await ctx.refresh(); });
    expect(ctx.user?.id).toBe("u1");
    expect(ctx.dealer?.id).toBe("d1");
    expect(ctx.verbindungsfehler).not.toBeNull();
  });

  it("Netzfehler nach einer ANDEREN Anmeldung (neue sid): alte Daten weg", async () => {
    await starten(jwt({ sub: "u1", sid: "s1", exp: 100 }));
    sessionStorage.setItem("ah_token", jwt({ sub: "u1", sid: "s2", exp: 100 }));
    antworten.push(netzfehler());
    await act(async () => { await ctx.refresh(); });
    expect(ctx.user).toBeNull();
    expect(ctx.verbindungsfehler).not.toBeNull();
  });
});
