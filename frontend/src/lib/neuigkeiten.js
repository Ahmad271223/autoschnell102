/*
 * „Was ist neu“ (Wunsch Ahmad 24.11.2026): Einträge und Gelesen-Stand kommen vom Server
 * (backend/neuigkeiten.json + routes/neuigkeiten.py). Hier: Zähler fürs Menü, kleine Helfer.
 * Ändert eine Seite den Gelesen-Stand, meldet sie das über ein Fenster-Ereignis — der Zähler
 * im Menü zieht sofort nach, ohne auf den nächsten Abruf zu warten.
 */
import { useEffect, useState } from "react";
import { api } from "@/lib/api";

export const NEUIGKEITEN_EREIGNIS = "neuigkeiten-geaendert";
export const NEUIGKEITEN_TAKT_MS = 5 * 60 * 1000;

export const ART_TEXT = { neu: "Neu", verbessert: "Verbessert", behoben: "Behoben" };

/** "2026-11-24" -> "24.11.2026" (ohne Zeitzonen-Rechnung). */
export function datumText(iso) {
  const m = /^(\d{4})-(\d{2})-(\d{2})$/.exec(String(iso || ""));
  return m ? `${m[3]}.${m[2]}.${m[1]}` : String(iso || "");
}

/** Nur Wege in die App (der Server prüft das schon beim Start). */
export function appLink(link) {
  return typeof link === "string" && (link.startsWith("/app/") || link.startsWith("/fahrer")) ? link : null;
}

export function gelesenGemeldet(stand) {
  if (typeof window === "undefined") return;
  window.dispatchEvent(new CustomEvent(NEUIGKEITEN_EREIGNIS, { detail: stand || null }));
}

/** Zahl der ungelesenen Einträge (Menü-Zähler). */
export function useNeuigkeitenZaehler(aktiv, userId, { client = api, pfad = "/neuigkeiten/stand" } = {}) {
  const [ungelesen, setUngelesen] = useState(0);
  useEffect(() => {
    if (!aktiv || !userId) { setUngelesen(0); return undefined; }
    let laeuft = true;
    let timer = null;
    const laden = async () => {
      try {
        const { data } = await client.get(pfad);
        if (laeuft) setUngelesen(Number(data?.ungelesen || 0));
      } catch {
        // Netz weg / abgemeldet: alten Stand behalten
      }
      if (laeuft) timer = setTimeout(laden, NEUIGKEITEN_TAKT_MS);
    };
    laden();
    const geaendert = (e) => {
      const n = e?.detail?.ungelesen;
      if (typeof n === "number") setUngelesen(n);
      else { clearTimeout(timer); laden(); }
    };
    window.addEventListener(NEUIGKEITEN_EREIGNIS, geaendert);
    return () => {
      laeuft = false;
      clearTimeout(timer);
      window.removeEventListener(NEUIGKEITEN_EREIGNIS, geaendert);
    };
  }, [aktiv, userId, client, pfad]);
  return ungelesen;
}
