/*
 * Programme zum Herunterladen (03.10.2026): welche Programme die Firma des
 * angemeldeten Kontos freigeschaltet hat (GET /api/werkzeuge). Name, Beschreibung
 * und Schritte kommen NUR vom Server — Firmen ohne Freischaltung bekommen eine
 * leere Liste und sehen weder Menüpunkt noch Seite; im App-Code steht kein Name.
 */
import { useEffect, useState } from "react";
import { api } from "@/lib/api";

const ZWISCHENSPEICHER = {};

/** null = (noch) nichts geladen bzw. aus, [] = keine Programme freigeschaltet. */
export function useProgramme(aktiv, userId) {
  const [liste, setListe] = useState(() => (aktiv && userId ? ZWISCHENSPEICHER[userId] ?? null : null));
  useEffect(() => {
    if (!aktiv || !userId) { setListe(null); return undefined; }
    let laeuft = true;
    if (ZWISCHENSPEICHER[userId]) setListe(ZWISCHENSPEICHER[userId]);
    api.get("/werkzeuge")
      .then(({ data }) => {
        const l = Array.isArray(data?.werkzeuge) ? data.werkzeuge : [];
        ZWISCHENSPEICHER[userId] = l;
        if (laeuft) setListe(l);
      })
      .catch(() => { if (laeuft) setListe((vorher) => vorher ?? []); });
    return () => { laeuft = false; };
  }, [aktiv, userId]);
  return liste;
}

/** Nur fuer Tests. */
export function programmeVergessen() {
  for (const k of Object.keys(ZWISCHENSPEICHER)) delete ZWISCHENSPEICHER[k];
}

/** Dateigroesse lesbar: 57671680 -> "55,0 MB". */
export function groesseText(bytes) {
  const n = Number(bytes);
  if (!Number.isFinite(n) || n <= 0) return "";
  if (n < 1024 * 1024) return `${Math.max(1, Math.round(n / 1024))} KB`;
  return `${(n / 1024 / 1024).toFixed(1).replace(".", ",")} MB`;
}
