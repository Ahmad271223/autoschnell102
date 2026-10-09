import { MODAL_ATTRIBUTE, useModal } from "@/lib/useModal";
import { ExternalLink, X } from "lucide-react";

/*
 * Prüfung 09.10.2026 (Befund Ahmad "Kaufvertrag aus dem Programm hängt, wenn in der App schon einer arbeitet"):
 * Kommt ein Auto aus dem Windows-Programm oder der Erweiterung, während hier etwas ungespeichert ist (offener
 * Vertragsdialog, Abhol-Check, geänderte Einstellungen …), gab es nur einen kleinen Hinweis oben rechts für
 * 20 Sekunden — wer ihn verpasste, bekam nichts; das Programm hielt die Sache für erledigt. Jetzt ein Dialog, der
 * stehen bleibt, bis der Sucher entscheidet: hier öffnen (die ungespeicherte Arbeit geht verloren — der Vertrags-
 * entwurf liegt gesichert im sessionStorage), in einem neuen Fenster öffnen (nichts geht verloren; der Klick ist
 * die Nutzergeste, die window.open braucht) oder später (das Ziel bleibt gemerkt, ein Hinweis mit Knopf bleibt).
 */

/** Kurzer Name des Ziels für die Anzeige („mobile.de · 487000201“). */
export function zielBeschreibung(ziel, origin = "https://app.auto-schnellkauf.de") {
  if (typeof ziel !== "string" || !ziel.startsWith("/app/")) return "";
  try {
    const u = new URL(ziel, origin);
    const link = u.searchParams.get("url");
    if (!link) return u.pathname;
    const l = new URL(link);
    const host = l.hostname.replace(/^(www|suchen)\./, "");
    const id = l.searchParams.get("id") || l.pathname.split("/").filter(Boolean).pop() || "";
    return id ? `${host} · ${id}` : host;
  } catch {
    return "";
  }
}

/**
 * @param {object} p
 * @param {string|null} p.ziel          Pfad in der App (/app/vergleich?url=…); null = zu
 * @param {() => void} p.onHier         hier öffnen (ungespeicherte Arbeit geht verloren)
 * @param {() => void} p.onNeuesFenster in einem neuen Fenster öffnen
 * @param {() => void} p.onSpaeter      später — das Ziel bleibt gemerkt
 */
export default function StartZielDialog({ ziel, onHier, onNeuesFenster, onSpaeter }) {
  const dialogRef = useModal(() => onSpaeter?.(), { offen: Boolean(ziel) });
  if (!ziel) return null;
  const was = zielBeschreibung(ziel, typeof window !== "undefined" ? window.location.origin : undefined);
  return (
    <div className="fixed inset-0 z-[60] flex items-center justify-center p-4 bg-black/70 backdrop-blur-sm"
         data-testid="start-ziel-dialog">
      <div ref={dialogRef} {...MODAL_ATTRIBUTE} aria-labelledby="start-ziel-titel"
           className="w-full max-w-md rounded-2xl overflow-hidden flex flex-col"
           style={{ background: "var(--bg-elevated)", border: "1px solid var(--border-default)" }}>
        <div className="flex items-center justify-between px-5 py-4" style={{ borderBottom: "1px solid var(--border-default)" }}>
          <div id="start-ziel-titel" className="font-semibold">Neues Auto aus dem Programm</div>
          <button type="button" onClick={() => onSpaeter?.()} data-testid="start-ziel-spaeter-x"
                  className="p-1 rounded-md" style={{ color: "var(--text-secondary)" }} title="Später" aria-label="Später">
            <X size={18} />
          </button>
        </div>
        <div className="px-5 py-4 text-sm space-y-3">
          <p>
            Das Programm möchte den Kaufvertrag {was ? <>für <span className="font-semibold">{was}</span> </> : ""}öffnen —
            hier ist aber noch etwas <span className="font-semibold">ungespeichert</span>.
          </p>
          <p className="text-xs" style={{ color: "var(--text-secondary)" }}>
            „In neuem Fenster öffnen“ lässt deine Arbeit hier unberührt. „Hier öffnen“ ersetzt die aktuelle Seite
            (ein angefangener Kaufvertrag bleibt als Entwurf gesichert).
          </p>
        </div>
        <div className="px-5 py-4 flex flex-wrap gap-2 justify-end" style={{ borderTop: "1px solid var(--border-default)" }}>
          <button type="button" onClick={() => onSpaeter?.()} data-testid="start-ziel-spaeter"
                  className="apple-btn apple-btn-secondary !rounded-full !px-4">Später</button>
          <button type="button" onClick={() => onHier?.()} data-testid="start-ziel-hier"
                  className="apple-btn apple-btn-secondary !rounded-full !px-4">Hier öffnen</button>
          <button type="button" onClick={() => onNeuesFenster?.()} data-testid="start-ziel-neu" data-autofocus
                  className="apple-btn apple-btn-primary !rounded-full !px-4 inline-flex items-center gap-1.5">
            <ExternalLink size={14} /> In neuem Fenster öffnen
          </button>
        </div>
      </div>
    </div>
  );
}
