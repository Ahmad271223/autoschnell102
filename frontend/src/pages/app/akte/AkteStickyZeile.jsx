import { useEffect, useState } from "react";
import StatusSchild from "@/components/StatusSchild";

/*
 * Wunsch Ahmad 24.11.2026: Beim Scrollen bleibt oben eine schmale Zeile mit
 * Foto, Modell und Preis stehen. position: sticky greift hier nicht (das
 * <main> des AppLayout hat overflow-x:auto und scrollt selbst nicht) — die
 * Zeile erscheint deshalb fest oben, sobald die Kopfleiste ganz aus dem Bild ist,
 * genau über der Spalte der Seite (Breite der Seite gemessen).
 */
export default function AkteStickyZeile({ kopfRef, seiteRef, foto, titel, preis, status, statusText }) {
  const [sichtbar, setSichtbar] = useState(false);
  const [lage, setLage] = useState(null);

  // Scroll-Abfrage statt IntersectionObserver: die Kopfleiste entsteht erst
  // nach dem Laden, der Ref ist beim ersten Lauf noch leer. Hier wird er bei
  // jedem Scrollen neu gelesen (capture: auch scrollende Container zählen).
  useEffect(() => {
    let rahmen = 0;
    const pruefen = () => {
      rahmen = 0;
      const r = kopfRef?.current?.getBoundingClientRect();
      setSichtbar(!!r && r.bottom < 0);
    };
    const beimScrollen = () => { if (!rahmen) rahmen = window.requestAnimationFrame(pruefen); };
    window.addEventListener("scroll", beimScrollen, { passive: true, capture: true });
    pruefen();
    return () => {
      window.removeEventListener("scroll", beimScrollen, { capture: true });
      if (rahmen) window.cancelAnimationFrame(rahmen);
    };
  }, [kopfRef]);

  useEffect(() => {
    if (!sichtbar) return undefined;
    const messen = () => {
      const r = seiteRef?.current?.getBoundingClientRect();
      if (r) setLage({ left: r.left, width: r.width });
    };
    messen();
    window.addEventListener("resize", messen);
    return () => window.removeEventListener("resize", messen);
  }, [sichtbar, seiteRef]);

  if (!sichtbar) return null;
  return (
    <div data-testid="akte-sticky" className="fixed z-40 px-3 sm:px-6 lg:px-10"
         style={{ top: "env(safe-area-inset-top, 0px)", left: lage?.left ?? 0, width: lage?.width ?? "100%" }}>
      <div className="flex items-center gap-3 rounded-b-xl px-3 py-1.5 shadow-lg"
           style={{ background: "var(--bg-elevated)", border: "1px solid var(--wa-10)", borderTop: "none" }}>
        {foto
          ? <img src={foto} alt="" className="w-10 h-8 rounded object-cover shrink-0" />
          : <div className="w-10 h-8 rounded shrink-0" style={{ background: "var(--wa-06)" }} />}
        <div className="min-w-0 flex-1 truncate text-sm font-semibold" style={{ color: "var(--text-primary)" }}>{titel}</div>
        {preis && <div className="shrink-0 text-sm font-bold" style={{ color: "var(--text-primary)" }}>{preis}</div>}
        <span className="hidden sm:inline-flex shrink-0"><StatusSchild status={status} text={statusText} /></span>
        <button type="button" aria-label="Nach oben" onClick={() => window.scrollTo({ top: 0, behavior: "smooth" })}
                className="shrink-0 text-xs px-2 py-1 rounded-lg" style={{ color: "var(--text-secondary)" }}>
          ▲
        </button>
      </div>
    </div>
  );
}
