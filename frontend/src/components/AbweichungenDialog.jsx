import { useEffect, useState } from "react";
import { createPortal } from "react-dom";
import { X } from "lucide-react";
import { api, errMsg } from "@/lib/api";
import AbweichungenListe from "@/components/AbweichungenListe";

/**
 * Wunsch Ahmad 24.11.2026: Klick auf die Zahl der Abweichungen in der Liste der
 * Abholaufträge öffnet direkt diese Ansicht — dieselben festen Nummern wie in
 * den Freigaben und in der Fahrer-App. Als Portal wie der Abholbericht; Klicks
 * laufen nicht zum Termin durch.
 */
export default function AbweichungenDialog({ appt, onClose }) {
  const [daten, setDaten] = useState(null);
  const [fehler, setFehler] = useState("");

  useEffect(() => {
    let aktiv = true;
    api.get(`/appointments/${appt.id}/abweichungen`)
      .then((r) => { if (aktiv) setDaten(r.data || {}); })
      .catch((e) => { if (aktiv) setFehler(errMsg(e, "Abweichungen konnten nicht geladen werden")); });
    return () => { aktiv = false; };
  }, [appt.id]);

  useEffect(() => {
    const taste = (e) => { if (e.key === "Escape") onClose(); };
    document.addEventListener("keydown", taste);
    return () => document.removeEventListener("keydown", taste);
  }, [onClose]);

  const inhalt = (
    <div className="fixed inset-0 z-[60] flex items-center justify-center p-4 bg-black/70"
         onClick={(e) => { e.stopPropagation(); onClose(); }}
         data-testid="abweichungen-dialog">
      <div className="w-full max-w-lg modal-hoehe overflow-y-auto rounded-2xl p-5"
           role="dialog" aria-modal="true" aria-label="Abweichungen"
           style={{ background: "var(--bg-elevated)", border: "1px solid var(--wa-10)", color: "var(--text-primary)" }}
           onClick={(e) => e.stopPropagation()}>
        <div className="flex items-start justify-between gap-3">
          <div className="min-w-0">
            <div className="text-[11px] uppercase tracking-wider" style={{ color: "var(--text-dim)" }}>Abweichungen</div>
            <div className="text-lg font-bold leading-tight [overflow-wrap:anywhere]">{appt.title}</div>
          </div>
          <button type="button" onClick={(e) => { e.stopPropagation(); onClose(); }}
                  className="w-10 h-10 -mr-2 -mt-2 flex items-center justify-center" style={{ color: "var(--text-dim)" }}
                  aria-label="Schließen">
            <X size={20} />
          </button>
        </div>
        <div className="mt-4">
          {!daten && !fehler && <div className="text-sm" style={{ color: "var(--text-dim)" }}>Lädt…</div>}
          {fehler && <div className="text-sm" style={{ color: "var(--st-rot)" }}>{fehler}</div>}
          {daten && (
            <AbweichungenListe daten={daten} testId="termin-abweichungen"
                               leerText={daten.protocol_id ? "Keine Abweichungen im Protokoll" : "Noch kein Abholprotokoll"} />
          )}
        </div>
      </div>
    </div>
  );
  return createPortal(inhalt, document.body);
}
