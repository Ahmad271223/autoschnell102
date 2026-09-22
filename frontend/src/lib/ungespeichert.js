/*
 * Ungespeicherte Arbeit schuetzen (Runde 31, 12.09.2026).
 *
 * Die Pruefung der Formulare ergab: Unterschriften im Abholprotokoll, der
 * Kaufvertrag-Dialog, der Abhol-Check und die einmaligen
 * Wiederherstellungscodes leben NUR im Speicher. Ein Neuladen — von der
 * App ausgeloest, per F5 oder beim Schliessen des Fensters — vernichtete
 * sie ohne jede Rueckfrage; eine Warnung vor dem Verlassen gab es nirgends.
 *
 * Solange eine Stelle "ungespeichert" meldet, fragt der Browser vor dem
 * Verlassen nach, und die App laedt nicht von sich aus neu.
 */
import { useEffect } from "react";

const offen = new Set();

function warnen(e) {
  e.preventDefault();
  // Aeltere Browser verlangen einen gesetzten returnValue.
  e.returnValue = "";
  return "";
}

/** Meldet eine Stelle als ungespeichert; die Rueckgabe hebt das wieder auf. */
export function ungespeichertMelden(schluessel = {}) {
  if (offen.size === 0 && typeof window !== "undefined") {
    window.addEventListener("beforeunload", warnen);
  }
  offen.add(schluessel);
  return () => {
    offen.delete(schluessel);
    if (offen.size === 0 && typeof window !== "undefined") {
      window.removeEventListener("beforeunload", warnen);
    }
  };
}

export function hatUngespeichert() {
  return offen.size > 0;
}

export const VERLASSEN_FRAGE = "Es gibt ungespeicherte Änderungen auf dieser Seite.\n"
  + "Seite trotzdem verlassen? Die Eingaben gehen dann verloren.";

/**
 * Rollenprüfung 22.09.2026 (RP-143): beforeunload greift nur beim Neuladen
 * oder Schließen des Fensters. Ein Klick in der Seitenleiste (Wechsel
 * innerhalb der App) verwarf Eingaben still. Die Navigation der App fragt
 * deshalb hier vorher nach. true = darf weiter.
 */
export function verlassenBestaetigen(frage = VERLASSEN_FRAGE) {
  if (!hatUngespeichert()) return true;
  try {
    return window.confirm(frage);
  } catch {
    return true;               // kein Dialog möglich (z. B. eingebettet): nicht blockieren
  }
}

/** Hook: solange `aktiv` wahr ist, gilt die Komponente als ungespeichert. */
export function useUngespeichert(aktiv) {
  useEffect(() => {
    if (!aktiv) return undefined;
    return ungespeichertMelden({});
  }, [aktiv]);
}
