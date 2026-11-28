/*
 * Zaehler fuer wartende Abholprotokolle (Wunsch Ahmad, 12.09.2026).
 *
 * Vorher merkte der Chef nur auf der Termine-Seite, dass ein Fahrer beim
 * Verkaeufer auf seine Freigabe wartet. Jetzt zeigt das Menue die Zahl, der
 * Browser-Tab "(2) AutoSchnell", und kommt ein neues Protokoll dazu, kommt
 * ein Hinweis — egal auf welcher Seite er gerade arbeitet.
 *
 * Gegenpruefung 12.09.2026:
 *   - EIN Abruf je Browser-Tab, egal wie viele Stellen die Zahl zeigen
 *     (Menue und Termine-Seite fragten vorher doppelt).
 *   - Im verdeckten Tab weiter abfragen, nur seltener — sonst stand "(1)"
 *     genau dann nicht im Tab-Titel, wenn der Chef in einem anderen Tab war.
 *   - Neu ist, was eine neue Protokoll-ID hat — nicht nur eine hoehere Zahl
 *     (gibt ein Kollege eins frei und ein Fahrer schickt eins ab, bleibt die
 *     Zahl gleich).
 */
import { useEffect, useState } from "react";
import { api } from "@/lib/api";

export const FREIGABE_TAKT_MS = 20000;
export const FREIGABE_TAKT_VERDECKT_MS = 60000;

const LEER = Object.freeze({ wartet: 0, freigegeben: 0, ids: null, geladen: false });

/** "(2) AutoSchnell" — eine vorhandene Zahl wird ersetzt, 0 entfernt sie. */
export function titelMitZahl(titel, anzahl) {
  const basis = String(titel || "").replace(/^\(\d+\)\s*/, "");
  return anzahl > 0 ? `(${anzahl}) ${basis}` : basis;
}

/** Wie viele sind seit dem letzten Blick NEU dazugekommen? (aelteres Backend ohne IDs) */
export function neuHinzugekommen(vorher, jetzt) {
  if (vorher == null) return 0;          // erster Abruf: kein Hinweis fuer Altbestand
  return Math.max(0, Number(jetzt || 0) - Number(vorher || 0));
}

/**
 * Welche wartenden Protokolle sind seit dem letzten Abruf neu?
 * `merk` haelt den letzten Stand je Konto; der erste Abruf eines Kontos
 * meldet nichts (Altbestand).
 */
export function neueWartende(merk, konto, ids) {
  const liste = Array.isArray(ids) ? ids : [];
  if (merk.konto !== konto || !merk.ids) {
    merk.konto = konto;
    merk.ids = new Set(liste);
    return [];
  }
  const neu = liste.filter((id) => !merk.ids.has(id));
  merk.ids = new Set(liste);
  return neu;
}

// ---- ein gemeinsamer Abruf je Tab ----
const abonnenten = new Set();
let stand = LEER;
let takt = null;
let zuletzt = 0;
let laeuft = false;

function verteilen() {
  abonnenten.forEach((setzen) => setzen(stand));
}

async function holen(sofort = false) {
  if (laeuft || !abonnenten.size) return;
  const verdeckt = typeof document !== "undefined" && document.visibilityState !== "visible";
  if (!sofort && verdeckt && Date.now() - zuletzt < FREIGABE_TAKT_VERDECKT_MS) return;
  laeuft = true;
  try {
    const { data } = await api.get("/protocols/zur-freigabe/anzahl");
    zuletzt = Date.now();
    if (data && abonnenten.size) {
      stand = {
        wartet: Number(data.wartet) || 0,
        freigegeben: Number(data.freigegeben) || 0,
        ids: Array.isArray(data.ids) ? data.ids : null,
        geladen: true,
      };
      verteilen();
    }
  } catch {
    /* still — der Zaehler ist eine Hilfe, kein Muss */
  } finally {
    laeuft = false;
  }
}

function sichtbar() {
  if (document.visibilityState === "visible") holen(true);
}

function starten() {
  takt = setInterval(() => holen(false), FREIGABE_TAKT_MS);
  document.addEventListener("visibilitychange", sichtbar);
  holen(true);
}

function stoppen() {
  clearInterval(takt);
  takt = null;
  document.removeEventListener("visibilitychange", sichtbar);
  // Kein Rest fuer das naechste Konto im selben Tab.
  stand = LEER;
  zuletzt = 0;
}

/** Nach einer eigenen Freigabe sofort neu zaehlen (nicht erst im naechsten Takt). */
export function freigabeZaehlerAktualisieren() {
  return holen(true);
}

export function useFreigabeZaehler(aktiv) {
  const [anzahl, setAnzahl] = useState(stand);
  useEffect(() => {
    if (!aktiv) return undefined;
    abonnenten.add(setAnzahl);
    if (abonnenten.size === 1) starten();
    else setAnzahl(stand);
    return () => {
      abonnenten.delete(setAnzahl);
      if (!abonnenten.size) stoppen();
    };
  }, [aktiv]);
  return aktiv ? anzahl : LEER;
}
