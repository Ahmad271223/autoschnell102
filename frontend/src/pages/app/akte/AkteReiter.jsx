import { createContext, useCallback, useContext } from "react";
import { useLocation, useNavigate } from "react-router-dom";

/*
 * Wunsch Ahmad 24.11.2026: Reiter der Fahrzeugakte, gemerkt im URL-Hash
 * (#vertrag, #abholung …) — Zurück/Vor und ein geteilter Link landen auf
 * demselben Reiter.
 *
 * NEUE KARTEN EINHÄNGEN: eine eigene Karte als <Section title="…"> (oder
 * <AkteAbschnitt reiter="…">) in FahrzeugAkte.jsx einfügen. Ohne `reiter`
 * steht sie unter „Übersicht“; mit reiter="vertrag" | "abholung" |
 * "notizen" | "dokumente" unter dem jeweiligen Reiter. Inaktive Reiter
 * bleiben im DOM (nur `hidden`) — Formulareingaben gehen beim Wechsel nicht
 * verloren.
 */
export const REITER = [
  { key: "uebersicht", label: "Übersicht" },
  { key: "vertrag", label: "Vertrag" },
  { key: "abholung", label: "Abholung" },
  { key: "notizen", label: "Notizen" },
  { key: "dokumente", label: "Dokumente" },
];
export const REITER_STANDARD = "uebersicht";

/** "#abholung" -> "abholung"; Unbekanntes -> Übersicht. */
export function reiterAusHash(hash) {
  const k = String(hash || "").replace(/^#/, "").trim().toLowerCase();
  return REITER.some((r) => r.key === k) ? k : REITER_STANDARD;
}

export const ReiterContext = createContext(null);

/** Aktiver Reiter + Wechsel (schreibt den Hash, ohne neuen Verlaufseintrag je Klick). */
export function useAkteReiter() {
  const loc = useLocation();
  const nav = useNavigate();
  const aktiv = reiterAusHash(loc.hash);
  const wechseln = useCallback((key) => {
    nav({ pathname: loc.pathname, search: loc.search, hash: key === REITER_STANDARD ? "" : `#${key}` },
      { replace: true });
  }, [nav, loc.pathname, loc.search]);
  return [aktiv, wechseln];
}

/** Steht ein Abschnitt dieses Reiters gerade sichtbar? Ohne Reiter-Rahmen immer ja. */
export function useReiterSichtbar(reiter = REITER_STANDARD) {
  const aktiv = useContext(ReiterContext);
  return aktiv == null || aktiv === reiter;
}

/** Ein Abschnitt, der nur unter seinem Reiter erscheint (für neue Karten). */
export function AkteAbschnitt({ reiter = REITER_STANDARD, className = "", children, ...rest }) {
  const sichtbar = useReiterSichtbar(reiter);
  return (
    <div className={className} hidden={!sichtbar} data-reiter={reiter} {...rest}>
      {children}
    </div>
  );
}

/** Reiterleiste — auf dem Handy waagerecht wischbar. */
export function AkteReiterLeiste({ aktiv, onWechsel, zaehler = {} }) {
  return (
    <div role="tablist" aria-label="Bereiche der Fahrzeugakte" data-testid="akte-reiter"
         className="mt-5 flex gap-1 overflow-x-auto overflow-y-hidden border-b -mx-3 px-3 sm:mx-0 sm:px-0"
         style={{ borderColor: "var(--border-default)" }}>
      {REITER.map((r) => {
        const an = r.key === aktiv;
        return (
          <button key={r.key} type="button" role="tab" aria-selected={an}
                  data-testid={`akte-reiter-${r.key}`} onClick={() => onWechsel(r.key)}
                  className="shrink-0 px-3.5 py-2.5 text-sm min-h-[44px] -mb-px border-b-2 transition"
                  style={{
                    borderColor: an ? "var(--accent-red)" : "transparent",
                    color: an ? "var(--text-primary)" : "var(--text-secondary)",
                    fontWeight: an ? 700 : 500,
                  }}>
            {r.label}
            {zaehler[r.key] ? (
              <span className="ml-1.5 text-[11px] rounded-full px-1.5 py-0.5"
                    style={{ background: "var(--wa-08)", color: "var(--text-secondary)" }}>
                {zaehler[r.key]}
              </span>
            ) : null}
          </button>
        );
      })}
    </div>
  );
}
