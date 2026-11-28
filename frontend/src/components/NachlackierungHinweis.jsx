import { PaintBucket } from "lucide-react";

/**
 * Nachlackierung aus der Lackdicke-Messung (Wunsch Ahmad 24.11.2026).
 *
 * befund kommt vom Server (zur-freigabe → "nachlackierung"):
 *   { text: "Ja, Tür vorne links (210 µm)", vertrag: "Ja" | "Nein" | "", abweichend: bool, schwelle_um: 160 }
 * Liegt ein Messwert über der Schwelle, ist das Auto nicht nachlackierungsfrei. Steht im Vertrag
 * „Nachlackierungsfrei: Ja“, ist das eine Abweichung — sie kommt in den Abänderungsvertrag.
 */
export default function NachlackierungHinweis({ befund, testid }) {
  if (!befund?.text) return null;
  const abweichend = Boolean(befund.abweichend);
  return (
    <div className="mt-3 rounded-lg p-2.5 text-[12px] flex items-start gap-2" role="note" data-testid={testid}
         style={{
           border: `1px solid ${abweichend ? "rgba(255,159,10,0.45)" : "var(--border-default)"}`,
           background: abweichend ? "rgba(255,159,10,0.10)" : "transparent",
           color: "var(--text-primary)",
         }}>
      <PaintBucket size={15} className="mt-0.5 shrink-0" style={{ color: abweichend ? "var(--tx-amber)" : "var(--text-secondary)" }} />
      <div>
        <div className="font-semibold">Nachlackierung: {befund.text}</div>
        <div style={{ color: "var(--text-secondary)" }}>
          {abweichend
            ? "Im Vertrag steht „Nachlackierungsfrei: Ja“ — das wird geändert und steht im Abänderungsvertrag."
            : `Über ${befund.schwelle_um || 160} µm gilt als nachlackiert.`}
        </div>
      </div>
    </div>
  );
}
