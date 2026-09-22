import { useEffect, useState } from "react";
import { api } from "@/lib/api";

/**
 * Go-Live-Schalter (15.09.2026, Wunsch Ahmad): welche Bereiche der Server
 * freigeschaltet hat (GET /api/features). Solange nichts geladen ist oder
 * der Aufruf scheitert, gilt "aus" — abgeschaltete Bereiche zeigen
 * "Demnaechst verfuegbar" statt einer halben Seite.
 */
const STANDARD = { marktplatz: false };
/** Nach einem Fehlschlag fragt eine offene Seite nach dieser Zeit erneut. */
export const FEATURES_NEU_VERSUCH_MS = 30000;
let cache = null;
let laufend = null;

export async function featuresLaden() {
  if (cache) return cache;
  if (!laufend) {
    laufend = api.get("/features")
      .then((r) => { cache = { ...STANDARD, ...(r.data || {}) }; return cache; })
      // Rollenprüfung 22.09.2026 (RP-045/RP-144): Ein einziger Fehlschlag
      // (Funkloch, Neustart des Servers) blieb bis zum Neuladen der Seite
      // als "Marktplatz aus" stehen. Jetzt gilt "aus" nur für DIESEN Aufruf;
      // der Zwischenspeicher bleibt leer, der nächste Aufruf fragt neu.
      .catch(() => ({ ...STANDARD }))
      .finally(() => { laufend = null; });
  }
  return laufend;
}

export function useFeatures() {
  const [f, setF] = useState(cache);
  useEffect(() => {
    let aktiv = true;
    let timer = null;
    const holen = () => featuresLaden().then((x) => {
      if (!aktiv) return;
      setF(x);
      // Gescheitert (nichts zwischengespeichert): auf einer offenen Seite
      // später noch einmal fragen, statt bis zum Neuladen "aus" zu zeigen.
      if (!cache) timer = setTimeout(holen, FEATURES_NEU_VERSUCH_MS);
    });
    holen();
    return () => { aktiv = false; clearTimeout(timer); };
  }, []);
  return { ...(f || STANDARD), geladen: !!f };
}

/** Nur fuer Tests: Zwischenspeicher setzen oder leeren. */
export function featuresSetzen(wert) {
  cache = wert ? { ...STANDARD, ...wert } : null;
}
