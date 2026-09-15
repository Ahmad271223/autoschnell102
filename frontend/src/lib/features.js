import { useEffect, useState } from "react";
import { api } from "@/lib/api";

/**
 * Go-Live-Schalter (15.09.2026, Wunsch Ahmad): welche Bereiche der Server
 * freigeschaltet hat (GET /api/features). Solange nichts geladen ist oder
 * der Aufruf scheitert, gilt "aus" — abgeschaltete Bereiche zeigen
 * "Demnaechst verfuegbar" statt einer halben Seite.
 */
const STANDARD = { marktplatz: false };
let cache = null;
let laufend = null;

export async function featuresLaden() {
  if (cache) return cache;
  if (!laufend) {
    laufend = api.get("/features")
      .then((r) => { cache = { ...STANDARD, ...(r.data || {}) }; return cache; })
      .catch(() => { cache = { ...STANDARD }; return cache; })
      .finally(() => { laufend = null; });
  }
  return laufend;
}

export function useFeatures() {
  const [f, setF] = useState(cache);
  useEffect(() => {
    let aktiv = true;
    featuresLaden().then((x) => { if (aktiv) setF(x); });
    return () => { aktiv = false; };
  }, []);
  return { ...(f || STANDARD), geladen: !!f };
}

/** Nur fuer Tests: Zwischenspeicher setzen oder leeren. */
export function featuresSetzen(wert) {
  cache = wert ? { ...STANDARD, ...wert } : null;
}
