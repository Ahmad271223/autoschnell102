import { Component } from "react";
import { useLocation } from "react-router-dom";

// Fehlergrenze fuer nachgeladene Seiten (Vite, 09/2026): scheitert eine Seite
// endgueltig (z. B. waehrend eines Updates), erscheint ein klarer Hinweis
// mit "Neu laden" statt einer leeren Oberflaeche.
//
// Runde 31 (12.09.2026, Vorfall Super-Admin): Die Grenze setzte sich nie
// zurueck. Einmal ausgeloest, zeigte der ganze Bereich nur noch die Meldung —
// auch nach einem Klick auf "Uebersicht" oder "Nutzer", deren Dateien
// einwandfrei da waren. Jetzt beginnt jeder Seitenwechsel neu.
//
// Pruefbericht 20.09.2026 (U-150): Fuer JEDEN Fehler stand "Das passiert
// kurz nach einem Update" + "Neu laden" — auch fuer einen Fehler beim
// Zeichnen, bei dem Neuladen selten hilft. Jetzt werden Nachladefehler
// (fehlende Datei) von Render-Fehlern getrennt; Letztere bekommen einen
// neutralen Text und "Zur Startseite". Die Klasse ist als FehlerGrenze
// exportiert, damit index.jsx eine zweite Grenze OHNE Router um <App/>
// legen kann (Kontexte, Router, Toaster lagen sonst ausserhalb jeder Grenze).

const DATEI_IN_FEHLER = /https?:[/][/][^ "')]+[.](?:js|css)/i;

/** Nachladefehler (Datei einer Seite fehlt) oder Fehler beim Zeichnen? Rein. */
export function istNachladefehler(fehler) {
  const name = String(fehler?.name || "");
  const text = String(fehler?.message || "");
  return name === "ChunkLoadError"
    || /ChunkLoadError|dynamically imported module|Importing a module script failed|Loading (CSS )?chunk/i.test(text)
    || DATEI_IN_FEHLER.test(text);
}

export class FehlerGrenze extends Component {
  constructor(props) {
    super(props);
    this.state = { fehler: null };
  }

  static getDerivedStateFromError(fehler) {
    return { fehler };
  }

  componentDidUpdate(vorher) {
    if (this.state.fehler && vorher.pfad !== this.props.pfad) {
      this.setState({ fehler: null });
    }
  }

  render() {
    const { fehler } = this.state;
    if (!fehler) return this.props.children;
    const nachladen = istNachladefehler(fehler);
    return (
      <div className="min-h-[60vh] flex flex-col items-center justify-center gap-3 px-6 text-center"
           data-testid="nachlade-fehler" data-art={nachladen ? "nachladen" : "fehler"}>
        <div className="text-sm" style={{ color: "var(--text-secondary)" }}>
          {nachladen
            ? "Diese Seite konnte gerade nicht geladen werden."
            : "In dieser Ansicht ist ein Fehler aufgetreten."}
        </div>
        <div className="text-[13px] max-w-sm" style={{ color: "var(--text-muted)" }}>
          {nachladen
            ? "Das passiert kurz nach einem Update. „Neu laden“ holt die aktuelle Version — du bleibst auf dieser Seite."
            : "Gespeicherte Daten sind nicht betroffen. Über „Zur Startseite“ geht es weiter; hilft das nicht, bitte neu laden."}
        </div>
        <div className="flex flex-wrap items-center justify-center gap-2">
          {!nachladen && (
            <button type="button" className="apple-btn apple-btn-primary"
                    data-testid="nachlade-fehler-start"
                    onClick={() => window.location.assign("/start")}>
              Zur Startseite
            </button>
          )}
          <button type="button" className={nachladen ? "apple-btn apple-btn-primary" : "apple-btn apple-btn-secondary"}
                  data-testid="nachlade-fehler-neu"
                  onClick={() => window.location.reload()}>
            Neu laden
          </button>
        </div>
      </div>
    );
  }
}

export default function NachladeFehler({ children }) {
  const { pathname } = useLocation();
  return <FehlerGrenze pfad={pathname}>{children}</FehlerGrenze>;
}
