/*
 * Prüfbericht 20.09.2026 (M-07): gemeinsames Dialog-Verhalten für die selbst
 * gebauten Fenster (Kaufvertrag, Versand, Termin, Bestand, Verkäufer-
 * Korrektur, Folge-Mail). Sie waren bisher nur ein <div className="fixed
 * inset-0">: ohne role/aria-modal, ohne Escape, ohne Anfangsfokus und ohne
 * Fokusfalle — Tastatur- und Vorlese-Nutzer landeten hinter dem Fenster auf
 * der Seite und kamen ohne Maus nicht mehr heraus.
 *
 * Der Hook liefert die Referenz für den DIALOGRAHMEN (nicht den abgedunkelten
 * Hintergrund) und übernimmt:
 *   - Escape -> onClose. Wer bei ungespeicherten Eingaben erst fragen will,
 *     gibt seine Schließen-Funktion MIT Rückfrage mit (Kaufvertrag, Termin).
 *     Liegt im Dialog ein weiteres Overlay offen (Lightbox der Fotos: selbst
 *     ein "fixed inset-0" mit eigenem Escape), bleibt der Dialog stehen.
 *     Bei mehreren offenen Dialogen schließt nur der oberste.
 *   - Anfangsfokus: [data-autofocus] im Dialog, sonst das erste
 *     Bedienelement, sonst der Rahmen selbst (bekommt tabindex=-1).
 *   - Fokusfalle: Tab / Umschalt+Tab bleiben im Dialog.
 *   - Fokus-Rückgabe an das Element, das den Dialog geöffnet hat.
 *
 * role="dialog" und aria-modal="true" setzt der Aufrufer selbst am Rahmen
 * (MODAL_ATTRIBUTE), damit sie im JSX sichtbar bleiben.
 */
import { useEffect, useRef } from "react";

export const MODAL_ATTRIBUTE = { role: "dialog", "aria-modal": "true" };

const FOKUSSIERBAR = [
  "a[href]", "button:not([disabled])", "input:not([disabled]):not([type=hidden])",
  "select:not([disabled])", "textarea:not([disabled])", "[tabindex]:not([tabindex='-1'])",
].join(",");

// Offene Dialoge — Escape und Tab treffen nur den obersten.
const offene = [];

/**
 * Der oberste Dialog: einer, der keinen anderen offenen Dialog ENTHAELT
 * (React fuehrt Kind-Effekte vor Eltern-Effekten aus — die Reihenfolge im
 * Stapel sagt bei geschachtelten Dialogen nichts). Unter Geschwistern der
 * zuletzt geoeffnete.
 */
function oberster() {
  const frei = offene.filter((e) => !offene.some((o) => o !== e && e.el.contains(o.el)));
  return frei[frei.length - 1] || null;
}

/** Bedienelemente im Dialog in Dokumentreihenfolge (versteckte ausgenommen). */
export function fokussierbare(el) {
  if (!el || typeof el.querySelectorAll !== "function") return [];
  return Array.from(el.querySelectorAll(FOKUSSIERBAR))
    .filter((e) => !e.closest("[hidden],[aria-hidden='true']"));
}

/** Ein weiteres Overlay (Lightbox) liegt IM Dialog offen — Escape gehört ihm. */
function overlayOffen(el) {
  return Boolean(el.querySelector(".fixed.inset-0"));
}

/**
 * Tastendruck im obersten Dialog verarbeiten. Getrennt vom Hook, damit es
 * sich ohne React prüfen lässt. Rückgabe: was passiert ist ("schliessen",
 * "fokus", null).
 */
export function dialogTaste(e, el, schliessen, aktiv = document.activeElement) {
  if (e.key === "Escape") {
    if (e.defaultPrevented || overlayOffen(el)) return null;
    e.preventDefault();
    schliessen();
    return "schliessen";
  }
  if (e.key !== "Tab") return null;
  const liste = fokussierbare(el);
  if (liste.length === 0) {
    e.preventDefault();
    el.focus();
    return "fokus";
  }
  const erstes = liste[0];
  const letztes = liste[liste.length - 1];
  const drinnen = el.contains(aktiv);
  if (e.shiftKey) {
    if (!drinnen || aktiv === erstes || aktiv === el) {
      e.preventDefault();
      letztes.focus();
      return "fokus";
    }
  } else if (!drinnen || aktiv === letztes) {
    e.preventDefault();
    erstes.focus();
    return "fokus";
  }
  return null;
}

export function useModal(onClose, { offen = true } = {}) {
  const ref = useRef(null);
  const onCloseRef = useRef(onClose);
  onCloseRef.current = onClose;
  useEffect(() => {
    if (!offen) return undefined;
    const el = ref.current;
    if (!el) return undefined;
    const vorher = document.activeElement;
    const eintrag = { el, schliessen: () => onCloseRef.current?.() };
    offene.push(eintrag);
    const start = el.querySelector("[data-autofocus]") || fokussierbare(el)[0] || el;
    if (start === el && !el.hasAttribute("tabindex")) el.setAttribute("tabindex", "-1");
    try { start.focus({ preventScroll: true }); } catch { /* kein Fokus möglich: egal */ }
    const onKey = (e) => {
      if (oberster() !== eintrag) return;
      dialogTaste(e, el, eintrag.schliessen);
    };
    document.addEventListener("keydown", onKey);
    return () => {
      document.removeEventListener("keydown", onKey);
      const i = offene.indexOf(eintrag);
      if (i >= 0) offene.splice(i, 1);
      if (vorher && typeof vorher.focus === "function" && document.contains(vorher)) {
        try { vorher.focus({ preventScroll: true }); } catch { /* egal */ }
      }
    };
  }, [offen]);
  return ref;
}
