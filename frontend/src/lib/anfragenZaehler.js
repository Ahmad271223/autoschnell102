/*
 * Zähler für Kaufanfragen, die auf den Chef warten (Rollenprüfung
 * 22.09.2026, RP-472).
 *
 * Neue Anfragen, Gegenangebote der Käufer und Annahmen erfuhr der Chef nur,
 * wenn er die Seite "Kaufanfragen" selbst öffnete — eine Mail an die Firma
 * gibt es nicht, das Menü zeigte keine Zahl. Jetzt steht im Menü, wie viele
 * Anfragen eine Antwort brauchen ("offen" = neu, "gegenangebot_kaeufer" = der
 * Käufer hat nachverhandelt).
 *
 * Ein gemeinsamer Stand je Tab und Konto: die Seitenleiste wird bei jedem
 * Seitenwechsel neu aufgebaut — ist der Stand jünger als ANFRAGEN_FRISCH_MS,
 * gibt es keinen neuen Abruf. Ein anderes Konto im selben Tab beginnt bei 0.
 */
import { useEffect, useState } from "react";
import { api } from "@/lib/api";

export const ANFRAGEN_TAKT_MS = 60000;
export const ANFRAGEN_FRISCH_MS = 30000;
/** Diese Zustände brauchen eine Antwort des Chefs. */
export const ANFRAGEN_WARTEN = ["offen", "gegenangebot_kaeufer"];

const LEER = Object.freeze({ anzahl: 0, geladen: false, zeit: 0, konto: null });
let stand = LEER;
const abonnenten = new Set();
let laeuft = false;

/** Anzahl aus den Antworten der Statusabfragen (rein, damit testbar). */
export function anfragenZaehlen(antworten) {
  return (Array.isArray(antworten) ? antworten : [])
    .reduce((n, liste) => n + (Array.isArray(liste) ? liste.length : 0), 0);
}

/**
 * Rollenprüfung 22.09.2026, Welle 2 (RP-472): der Server zählt selbst
 * (GET /dealer/interessen/anzahl, routes/marketplace.py) — vorher holte das
 * Menü je Minute zwei komplette Anfragelisten. Antwortet ein Server noch ohne
 * diesen Endpunkt (404/405, z. B. während des Rollouts prod2 -> prod1), zählt
 * der alte Weg über die Listen.
 */
async function anzahlHolen() {
  try {
    const r = await api.get("/dealer/interessen/anzahl");
    const n = Number(r?.data?.anzahl);
    if (Number.isFinite(n) && n >= 0) return Math.floor(n);
  } catch (e) {
    if (![404, 405].includes(e?.response?.status)) throw e;
  }
  const antworten = await Promise.all(ANFRAGEN_WARTEN.map((status) =>
    api.get("/dealer/interessen", { params: { status } }).then((r) => r.data)));
  return anfragenZaehlen(antworten);
}

async function holen(konto, erzwingen = false) {
  if (laeuft) return;
  if (!erzwingen && stand.geladen && stand.konto === konto
      && Date.now() - stand.zeit < ANFRAGEN_FRISCH_MS) return;
  laeuft = true;
  try {
    stand = { anzahl: await anzahlHolen(), geladen: true, zeit: Date.now(), konto };
    abonnenten.forEach((setzen) => setzen(stand));
  } catch {
    /* still — der Zähler ist eine Hilfe, kein Muss */
  } finally {
    laeuft = false;
  }
}

export function useAnfragenZaehler(aktiv, konto) {
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
    }, ANFRAGEN_TAKT_MS);
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
