/**
 * Rollenprüfung 22.09.2026, Welle 2 — Fahrer-Anmeldung (DriverContext.jsx).
 *  RP-500  Abmelden schickt das Token ausdrücklich mit (der Interceptor läuft
 *          asynchron und fand es sonst schon gelöscht).
 *  RP-546  X-Neues-Token wird abgelegt — aber nur für die Sitzung, die gerade gilt.
 *  RP-557  Anmeldung schickt den Geräte-Schlüssel mit und legt den neuen ab.
 *  RP-546  Rücksprung ins Abholprotokoll nach der Neuanmeldung.
 */
import { act, createElement } from "react";
import { createRoot } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, it } from "vitest";
import {
  LETZTE_ANMELDUNG, TOKEN_APP, TOKEN_FAHRER, tokenLesen, tokenSetzen,
} from "@/lib/sitzung";
import {
  DriverAuthProvider, FAHRER_RUECKSPRUNG, driverApi, fahrerRuecksprungHolen,
  neuesFahrerTokenAblegen, useDriver,
} from "./DriverContext";
import { GERAET_SCHLUESSEL } from "./AuthContext";

globalThis.IS_REACT_ACT_ENVIRONMENT = true;

let anfragen;
let antworten;
let wurzel;
let behaelter;
let ctx;

function Fuehler() {
  ctx = useDriver();
  return null;
}

async function warten() {
  await act(async () => { await new Promise((r) => setTimeout(r, 0)); });
  await act(async () => { await new Promise((r) => setTimeout(r, 0)); });
}

beforeEach(() => {
  window.sessionStorage.clear();
  window.localStorage.clear();
  anfragen = [];
  antworten = {};
  // Netz im Speicher: jede Anfrage merken, Antwort je "METHODE url".
  driverApi.defaults.adapter = async (config) => {
    anfragen.push(config);
    const schluessel = `${config.method.toUpperCase()} ${config.url}`;
    const a = antworten[schluessel] || { data: {} };
    return { data: a.data, status: 200, statusText: "OK", headers: a.headers || {}, config };
  };
  behaelter = document.createElement("div");
  document.body.appendChild(behaelter);
});

afterEach(async () => {
  if (wurzel) await act(async () => { wurzel.unmount(); });
  wurzel = null;
  behaelter.remove();
});

async function rendern() {
  wurzel = createRoot(behaelter);
  await act(async () => {
    wurzel.render(createElement(DriverAuthProvider, null, createElement(Fuehler)));
  });
  await warten();
}

const kopf = (config) => String(config?.headers?.Authorization || config?.headers?.get?.("Authorization") || "");

describe("RP-500: Abmelden", () => {
  it("schickt das Token ausdrücklich mit und löscht es danach", async () => {
    tokenSetzen(TOKEN_FAHRER, "tok-alt");
    antworten["GET /driver/me"] = { data: { id: "f1", dealers: [] } };
    await rendern();
    await act(async () => { ctx.logout(); });
    await warten();
    const abmelden = anfragen.find((c) => c.url === "/driver/logout");
    expect(abmelden).toBeTruthy();
    expect(kopf(abmelden)).toBe("Bearer tok-alt");
    expect(tokenLesen(TOKEN_FAHRER)).toBeNull();
  });
});

// Rollenprüfung 22.09.2026 (RP-546, Nachtrag): Das verlängerte Token läuft
// über dieselbe Regel wie in Händler- und Käufer-App (tokenErneuern) — nur
// JWT-förmige Werte, nur wo noch das alte Token liegt, LETZTE_ANMELDUNG bleibt.
const ALT = "eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiJmMSJ9.alt-signatur";
const NEU = "eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiJmMSJ9.neu-signatur";
const ANDERES_KONTO = "eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiJmMiJ9.andere-signatur";

describe("RP-546: verlängertes Token", () => {
  it("wird abgelegt, wenn die Anfrage mit dem geltenden Token lief", async () => {
    tokenSetzen(TOKEN_FAHRER, ALT);
    antworten["GET /driver/appointments"] = { data: [], headers: { "x-neues-token": NEU } };
    await driverApi.get("/driver/appointments");
    expect(tokenLesen(TOKEN_FAHRER)).toBe(NEU);
    expect(window.localStorage.getItem(TOKEN_FAHRER)).toBe(NEU);
    // die nächste Anfrage trägt das neue Token
    antworten["GET /driver/appointments"] = { data: [] };
    await driverApi.get("/driver/appointments");
    expect(kopf(anfragen.at(-1))).toBe(`Bearer ${NEU}`);
  });

  it("ist keine neue Anmeldung: LETZTE_ANMELDUNG bleibt, wie sie war", async () => {
    tokenSetzen(TOKEN_FAHRER, ALT);
    // danach zuletzt in einem anderen Tab als Händler angemeldet
    window.localStorage.setItem(LETZTE_ANMELDUNG, TOKEN_APP);
    antworten["GET /driver/appointments"] = { data: [], headers: { "x-neues-token": NEU } };
    await driverApi.get("/driver/appointments");
    expect(tokenLesen(TOKEN_FAHRER)).toBe(NEU);
    expect(window.localStorage.getItem(LETZTE_ANMELDUNG)).toBe(TOKEN_APP);
  });

  it("überschreibt keine neuere Anmeldung aus einem anderen Tab", async () => {
    tokenSetzen(TOKEN_FAHRER, ALT);
    // anderer Tab: anderes Fahrerkonto angemeldet -> steht in localStorage
    window.localStorage.setItem(TOKEN_FAHRER, ANDERES_KONTO);
    antworten["GET /driver/appointments"] = { data: [], headers: { "x-neues-token": NEU } };
    await driverApi.get("/driver/appointments");
    expect(window.sessionStorage.getItem(TOKEN_FAHRER)).toBe(NEU);      // dieser Tab arbeitet weiter
    expect(window.localStorage.getItem(TOKEN_FAHRER)).toBe(ANDERES_KONTO);
  });

  it("späte Antwort nach dem Abmelden oder einer anderen Anmeldung bringt nichts zurück", () => {
    const antwort = (gesendet, neu = NEU) => ({
      headers: { "x-neues-token": neu },
      config: { headers: { Authorization: `Bearer ${gesendet}` } },
    });
    expect(neuesFahrerTokenAblegen(antwort(ALT))).toBe(false);      // kein Token mehr da
    expect(tokenLesen(TOKEN_FAHRER)).toBeNull();
    tokenSetzen(TOKEN_FAHRER, ANDERES_KONTO);
    expect(neuesFahrerTokenAblegen(antwort(ALT))).toBe(false);
    expect(tokenLesen(TOKEN_FAHRER)).toBe(ANDERES_KONTO);
    expect(neuesFahrerTokenAblegen({ headers: {}, config: {} })).toBe(false);
    // ohne Authorization gesendet: nichts zu verlängern
    expect(neuesFahrerTokenAblegen({ headers: { "x-neues-token": NEU }, config: {} })).toBe(false);
    expect(tokenLesen(TOKEN_FAHRER)).toBe(ANDERES_KONTO);
  });

  it("legt nur JWT-förmige Werte ab", () => {
    tokenSetzen(TOKEN_FAHRER, ALT);
    const antwort = (neu) => ({
      headers: { "x-neues-token": neu },
      config: { headers: { Authorization: `Bearer ${ALT}` } },
    });
    for (const muell of ["tok-neu", "a.b", "<script>.x.y", "a.b.c.d"]) {
      expect(neuesFahrerTokenAblegen(antwort(muell))).toBe(false);
      expect(tokenLesen(TOKEN_FAHRER)).toBe(ALT);
    }
  });
});

describe("RP-557: bekanntes Gerät", () => {
  it("Anmeldung schickt den Schlüssel und legt den vom Server ab", async () => {
    window.localStorage.setItem(GERAET_SCHLUESSEL, "Altes-Geraet_12345678");
    antworten["POST /driver/login"] = { data: { token: "tok-1", geraet_id: "Neues-Geraet_87654321" } };
    antworten["GET /driver/me"] = { data: { id: "f1", dealers: [] } };
    await rendern();
    await act(async () => { await ctx.login("FD-7K2M9QX4", "geheim"); });
    const anmelden = anfragen.find((c) => c.url === "/driver/login");
    expect(JSON.parse(anmelden.data)).toMatchObject({
      kontonummer: "FD-7K2M9QX4", geraet_id: "Altes-Geraet_12345678" });
    expect(window.localStorage.getItem(GERAET_SCHLUESSEL)).toBe("Neues-Geraet_87654321");
    expect(tokenLesen(TOKEN_FAHRER)).toBe("tok-1");
  });
});

describe("RP-546: Rücksprung nach der Neuanmeldung", () => {
  it("nur Protokollseiten, nur einmal", () => {
    window.sessionStorage.setItem(FAHRER_RUECKSPRUNG, "/fahrer/protokoll/t9");
    expect(fahrerRuecksprungHolen()).toBe("/fahrer/protokoll/t9");
    expect(fahrerRuecksprungHolen()).toBe("");
    window.sessionStorage.setItem(FAHRER_RUECKSPRUNG, "https://fremd.example/x");
    expect(fahrerRuecksprungHolen()).toBe("");
  });
});
