/*
 * App installieren (09/2026): AutoSchnell als Symbol auf Taskleiste,
 * Startmenue, Dock oder Startbildschirm — Sucher, Chefs und Fahrer muessen
 * nicht jedes Mal die Adresse eintippen. Das Symbol oeffnet /start: wer
 * noch angemeldet ist, landet direkt auf seiner Startseite, sonst bei der
 * Anmeldung (siehe lib/appstart.js).
 *
 * Wege je Browser (useInstallation().art):
 *   "direkt"     Chrome, Edge, Samsung Internet (Android, Windows, Mac,
 *                Linux) melden "installierbar" (beforeinstallprompt) — ein
 *                Klick oeffnet ihren eigenen Installieren-Dialog.
 *   "anleitung"  Der Browser kann es, bietet es der Seite aber nicht an:
 *                iPhone/iPad (Teilen -> Zum Home-Bildschirm), Safari am Mac
 *                (Ablage -> Zum Dock hinzufuegen), Firefox auf Android, und
 *                Chrome/Edge, solange sie sich noch nicht gemeldet haben
 *                (Chrome meldet erst nach etwas Nutzung der Seite).
 *   null         Laeuft schon als App, ist hier schon installiert, oder der
 *                Browser bietet keinen Weg (Firefox am PC) — kein Knopf.
 */
import { useSyncExternalStore } from "react";

// Merker "hier installiert": wirkt nur in Chrome/Edge/Android — dort teilt
// das App-Fenster den Speicher mit dem Browser. iPhone/iPad und Safari am
// Mac trennen den Speicher der installierten App vom Browser (von Apple so
// gewollt); dort bleibt der Knopf im Browser sichtbar, was nicht stoert.
const MERKER = "ah_app_installiert";

function merken(wert) {
  try {
    if (wert) window.localStorage.setItem(MERKER, "1");
    else window.localStorage.removeItem(MERKER);
  } catch { /* Speicher gesperrt: dann eben ohne Merker */ }
}

function gemerkt() {
  try { return window.localStorage.getItem(MERKER) === "1"; } catch { return false; }
}

/** Laeuft die Seite gerade als installierte App (eigenes Fenster)? */
export function laeuftAlsApp() {
  if (typeof window === "undefined") return false;
  const passt = (q) => {
    try { return !!window.matchMedia?.(q)?.matches; } catch { return false; }
  };
  return passt("(display-mode: standalone)")
    || passt("(display-mode: window-controls-overlay)")
    || window.navigator?.standalone === true;
}

const NAV = () => (typeof navigator === "undefined" ? {} : navigator);

/** Welcher Weg zur Installation passt zu diesem Geraet/Browser? */
export function plattform(nav = NAV()) {
  const ua = String(nav.userAgent || "");
  // iPadOS meldet sich als Mac — erkennbar am Touchscreen.
  if (/iPhone|iPad|iPod/i.test(ua) || (/Macintosh/.test(ua) && Number(nav.maxTouchPoints) > 1)) return "ios";
  if (/Android/i.test(ua)) return /Firefox\//.test(ua) ? "android-firefox" : "android";
  if (/Firefox\//.test(ua)) return "keine";          // Firefox am PC: kein Weg, den wir anleiten
  if (/Chrome\/|Chromium\/|Edg\//.test(ua)) return /Windows/.test(ua) ? "windows" : "desktop";
  if (/Macintosh/.test(ua) && /Safari\//.test(ua)) return "mac-safari";
  return "keine";
}

export function istEdge(nav = NAV()) {
  return /Edg(A|iOS)?\//.test(String(nav.userAgent || ""));
}

/** Mac (kein iPad) — fuer den Hinweis "Im Dock behalten". */
export function istMac(nav = NAV()) {
  return /Macintosh/.test(String(nav.userAgent || "")) && !(Number(nav.maxTouchPoints) > 1);
}

// --- Zustand ---------------------------------------------------------------
let ereignis = typeof window === "undefined" ? null : window.__ahInstallation || null;
let stand = null;
const hoerer = new Set();

function geaendert() {
  stand = null;
  hoerer.forEach((f) => f());
}

if (typeof window !== "undefined") {
  // Einmal als App gestartet, zeigt der Browser-Tab den Knopf nicht mehr
  // (Chrome/Edge/Android, siehe MERKER).
  if (laeuftAlsApp()) merken(true);
  // Kam das Ereignis erst nach boot.js (oder ist boot.js noch alt), hier fangen.
  window.addEventListener("beforeinstallprompt", (e) => {
    e.preventDefault();
    ereignis = e;
    merken(false);   // der Browser bietet die Installation an -> sie fehlt hier
    geaendert();
  });
  window.addEventListener("appinstalled", () => {
    ereignis = null;
    merken(true);
    geaendert();
  });
}

const OHNE = { art: null, plattform: "keine", edge: false, mac: false };

/** Aktueller Stand (fuer Tests und den Hook). */
export function installationsStand() {
  if (stand) return stand;
  const p = plattform();
  let art = null;
  if (laeuftAlsApp()) art = null;
  else if (ereignis) art = "direkt";
  else if (!gemerkt() && p !== "keine") art = "anleitung";
  stand = { art, plattform: p, edge: istEdge(), mac: istMac() };
  return stand;
}

/** Nur fuer Tests: Zustand auf Modulebene zuruecksetzen. */
export function _zuruecksetzenFuerTests() {
  ereignis = null;
  stand = null;
}

function abonnieren(f) {
  hoerer.add(f);
  return () => hoerer.delete(f);
}

export function useInstallation() {
  return useSyncExternalStore(abonnieren, installationsStand, () => OHNE);
}

/**
 * Oeffnet den Installieren-Dialog des Browsers.
 * Ergebnis: "angenommen" | "abgelehnt" | null (nicht moeglich -> Anleitung zeigen).
 */
export async function installieren() {
  const e = ereignis;
  if (!e) return null;
  // Ein Ereignis laesst sich nur einmal verwenden; der Browser meldet sich
  // bei Bedarf spaeter neu.
  ereignis = null;
  if (typeof window !== "undefined") window.__ahInstallation = null;
  geaendert();
  try {
    await e.prompt();
    const wahl = await e.userChoice;
    return wahl?.outcome === "accepted" ? "angenommen" : "abgelehnt";
  } catch {
    return null;
  }
}
