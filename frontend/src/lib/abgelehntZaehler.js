/*
 * Zähler für Fahrten, die ein Fahrer abgelehnt hat (Rollenprüfung
 * 22.09.2026, RP-464).
 *
 * Lehnte ein Fahrer eine Zuteilung ab, sah der Chef das nur am Termin selbst
 * (Terminplaner, Grund nur als Tooltip) — auf jeder anderen Seite gar nicht.
 * Der Termin stand dann ohne Fahrer da, bis jemand zufällig hinsah. Jetzt
 * steht die Zahl im Menü beim Terminplaner (wie die Freigaben-Zahl).
 *
 * Datenquelle: GET /appointments/fahrer-abgelehnt/anzahl -> { anzahl }
 * (offene Termine mit zuteilung "abgelehnt" und ohne driver_id, nur Chef).
 * Gibt es den Endpunkt (noch) nicht oder darf das Konto ihn nicht (404, 405,
 * 403), fragt dieser Tab nicht weiter — der Zähler ist eine Hilfe, kein Muss.
 *
 * Ein gemeinsamer Stand je Tab und Konto (wie lib/anfragenZaehler.js): die
 * Seitenleiste wird bei jedem Seitenwechsel neu aufgebaut — ist der Stand
 * jünger als ABGELEHNT_FRISCH_MS, gibt es keinen neuen Abruf.
 */
import { useEffect, useState } from "react";
import { api } from "@/lib/api";

export const ABGELEHNT_PFAD = "/appointments/fahrer-abgelehnt/anzahl";
export const ABGELEHNT_TAKT_MS = 60000;
export const ABGELEHNT_FRISCH_MS = 30000;

const LEER = Object.freeze({ anzahl: 0, geladen: false, zeit: 0, konto: null });
let stand = LEER;
let aus = false;             // Endpunkt fehlt / nicht erlaubt: in diesem Tab nicht mehr fragen
const abonnenten = new Set();
let laeuft = false;

/** Anzahl aus der Antwort lesen (rein, damit testbar); Unsinn -> 0. */
export function abgelehntAnzahl(daten) {
  const n = Number(daten?.anzahl);
  return Number.isFinite(n) && n > 0 ? Math.floor(n) : 0;
}

/** Heißt dieser Fehler "gibt es hier nicht" (dann nicht weiter fragen)? */
export function abgelehntDauerhaftAus(err) {
  return [403, 404, 405].includes(err?.response?.status);
}

async function holen(konto, erzwingen = false) {
  if (laeuft || aus) return;
  if (!erzwingen && stand.geladen && stand.konto === konto
      && Date.now() - stand.zeit < ABGELEHNT_FRISCH_MS) return;
  laeuft = true;
  try {
    const r = await api.get(ABGELEHNT_PFAD);
    stand = { anzahl: abgelehntAnzahl(r.data), geladen: true, zeit: Date.now(), konto };
    abonnenten.forEach((setzen) => setzen(stand));
  } catch (e) {
    if (abgelehntDauerhaftAus(e)) aus = true;
  } finally {
    laeuft = false;
  }
}

export function useAbgelehntZaehler(aktiv, konto) {
  const [wert, setWert] = useState(stand);
  useEffect(() => {
    if (!aktiv) return undefined;
    if (stand.konto !== konto) stand = { ...LEER, konto };
    abonnenten.add(setWert);
    setWert(stand);
    holen(konto, false);
    const takt = setInterval(() => {
      // Im verdeckten Tab nicht abfragen — beim Zurückkommen sofort.
      if (document.visibilityState !== "hidden") holen(konto, true);
    }, ABGELEHNT_TAKT_MS);
    const sichtbar = () => { if (document.visibilityState === "visible") holen(konto, false); };
    document.addEventListener("visibilitychange", sichtbar);
    return () => {
      abonnenten.delete(setWert);
      clearInterval(takt);
      document.removeEventListener("visibilitychange", sichtbar);
    };
  }, [aktiv, konto]);
  return aktiv && wert.konto === konto ? wert.anzahl : 0;
}

/** Nur für Tests: Zustand zurücksetzen. */
export function abgelehntZuruecksetzen() {
  stand = LEER;
  aus = false;
  laeuft = false;
}
