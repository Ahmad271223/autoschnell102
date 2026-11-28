/*
 * Meldungen in der App (Kundenportal, 29.09.2026): "Kaufvertrag bestätigt", sobald der Kunde
 * über die Firmenseite unterschrieben hat — für Chef UND den Sucher, der den Vertrag angelegt hat.
 *
 * Gleiches Muster wie der Freigabe-Zähler (lib/freigaben.js): ein Abruf je Browser-Tab, im
 * verdeckten Tab seltener, "neu" ist eine neue Meldungs-ID (nicht nur eine höhere Zahl), der
 * erste Abruf eines Kontos meldet den Altbestand nicht als neu.
 */
import { useEffect, useState } from "react";
import { api } from "@/lib/api";

export const MELDUNG_TAKT_MS = 20000;
export const MELDUNG_TAKT_VERDECKT_MS = 60000;

const LEER = Object.freeze({ ungelesen: 0, ids: null, geladen: false });

/** Welche Meldungen sind seit dem letzten Abruf dieses Kontos neu? (erster Abruf: keine) */
export function neueMeldungen(merk, userId, ids) {
  if (!userId || !Array.isArray(ids)) return [];
  const vorher = merk[userId];
  merk[userId] = new Set(ids);
  if (!vorher) return [];
  return ids.filter((id) => !vorher.has(id));
}

export function useMeldungenZaehler(aktiv, userId) {
  const [stand, setStand] = useState(LEER);
  useEffect(() => {
    if (!aktiv || !userId) { setStand(LEER); return undefined; }
    let laeuft = true;
    let timer = null;
    const laden = async () => {
      try {
        const { data } = await api.get("/meldungen/anzahl");
        if (laeuft) setStand({ ungelesen: Number(data?.ungelesen || 0), ids: Array.isArray(data?.ids) ? data.ids : [], geladen: true });
      } catch {
        // Netz weg / abgemeldet: alten Stand behalten, nicht nerven
      }
      if (laeuft) {
        const takt = typeof document !== "undefined" && document.hidden ? MELDUNG_TAKT_VERDECKT_MS : MELDUNG_TAKT_MS;
        timer = setTimeout(laden, takt);
      }
    };
    laden();
    const sichtbar = () => { if (laeuft && typeof document !== "undefined" && !document.hidden) { clearTimeout(timer); laden(); } };
    if (typeof document !== "undefined") document.addEventListener("visibilitychange", sichtbar);
    return () => {
      laeuft = false;
      clearTimeout(timer);
      if (typeof document !== "undefined") document.removeEventListener("visibilitychange", sichtbar);
    };
  }, [aktiv, userId]);
  return stand;
}
