/*
 * Zähler ungelesener Chat-Nachrichten je Abholtermin (Chat Büro/Fahrer, Wunsch Ahmad 24.11.2026).
 *
 * EIN Abruf je Browser-Tab und Seite ("buero" oder "fahrer"), egal wie viele Karten den Zähler zeigen:
 * alle 60 s, solange der Tab sichtbar ist, sofort beim Zurückkehren und nach jedem Öffnen/Schreiben
 * im Chat (chatZaehlerNeuLaden). Ohne Abonnenten läuft nichts.
 */
import { useEffect, useState } from "react";
import { api } from "@/lib/api";
import { driverApi } from "@/context/DriverContext";

export const CHAT_ZAEHLER_TAKT_MS = 60000;

const LEER = Object.freeze({ termine: {}, gesamt: 0 });

function neuerSpeicher(rolle) {
  return { rolle, abonnenten: new Set(), stand: LEER, takt: null, laeuft: false };
}
const speicher = { buero: neuerSpeicher("buero"), fahrer: neuerSpeicher("fahrer") };

export function chatClient(rolle) {
  return rolle === "fahrer" ? driverApi : api;
}

export function zaehlerPfad(rolle) {
  return rolle === "fahrer" ? "/driver/chat/ungelesen" : "/chat/ungelesen";
}

/** Antwort des Servers in einen sauberen Stand (kaputte Werte = 0). */
export function standAusAntwort(data) {
  const termine = {};
  const roh = data && typeof data.termine === "object" && data.termine ? data.termine : {};
  Object.entries(roh).forEach(([k, v]) => {
    const n = Number(v);
    if (k && Number.isFinite(n) && n > 0) termine[k] = Math.floor(n);
  });
  return { termine, gesamt: Object.values(termine).reduce((a, b) => a + b, 0) };
}

async function laden(s) {
  if (s.laeuft) return;
  s.laeuft = true;
  try {
    const { data } = await chatClient(s.rolle).get(zaehlerPfad(s.rolle));
    s.stand = standAusAntwort(data);
    s.abonnenten.forEach((fn) => fn(s.stand));
  } catch {
    // Funkloch / abgemeldet: alten Stand behalten
  } finally {
    s.laeuft = false;
  }
}

function sichtbarLaden() {
  if (typeof document !== "undefined" && document.visibilityState !== "visible") return;
  Object.values(speicher).forEach((s) => { if (s.abonnenten.size) laden(s); });
}

export function chatZaehlerNeuLaden(rolle) {
  const s = speicher[rolle];
  if (s && s.abonnenten.size) laden(s);
}

function abonnieren(rolle, fn) {
  const s = speicher[rolle];
  s.abonnenten.add(fn);
  fn(s.stand);
  if (s.abonnenten.size === 1) {
    laden(s);
    s.takt = setInterval(() => {
      if (typeof document === "undefined" || document.visibilityState === "visible") laden(s);
    }, CHAT_ZAEHLER_TAKT_MS);
    if (typeof document !== "undefined") document.addEventListener("visibilitychange", sichtbarLaden);
  }
  return () => {
    s.abonnenten.delete(fn);
    if (!s.abonnenten.size) {
      clearInterval(s.takt);
      s.takt = null;
      const keiner = Object.values(speicher).every((x) => !x.abonnenten.size);
      if (keiner && typeof document !== "undefined") document.removeEventListener("visibilitychange", sichtbarLaden);
    }
  };
}

/** Stand {termine: {id: n}, gesamt} für "buero" oder "fahrer"; aktiv=false = kein Abruf. */
export function useChatZaehler(rolle, aktiv = true) {
  const [stand, setStand] = useState(LEER);
  useEffect(() => {
    if (!aktiv || !speicher[rolle]) return undefined;
    return abonnieren(rolle, setStand);
  }, [rolle, aktiv]);
  return stand;
}

/** Nur für Tests: alles zurücksetzen. */
export function _zuruecksetzen() {
  Object.keys(speicher).forEach((k) => {
    clearInterval(speicher[k].takt);
    speicher[k] = neuerSpeicher(k);
  });
}
