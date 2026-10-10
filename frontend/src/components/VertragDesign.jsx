import { useState } from "react";
import { toast } from "sonner";
import { Check, Eye, Loader2, Send } from "lucide-react";
import { api, errMsg } from "@/lib/api";
import { blobOeffnen } from "@/lib/dateiOeffnen";
import {
  VERTRAG_FARBEN, VERTRAG_LAYOUTS, vertragFarbe, vertragFarbeHex, vertragLayout,
} from "@/lib/vertragDesign";

// Wunsch Ahmad 03.10.2026: Der Chef wählt Layout und Farbe des Kaufvertrags. Gilt für alle NEUEN
// Verträge der Firma (auch die der Sucher); bestehende Verträge behalten ihr Aussehen.
// Die Vorschau zeigt einen Mustervertrag mit den echten Firmendaten — auch vor dem Speichern.

/** Kleine Skizze eines Vertragsblatts — weißes Papier in beiden Designs. */
function Skizze({ layout, hex }) {
  const linie = { background: "#d8dee5", height: 3, borderRadius: 2 };
  const punkt = { borderBottom: "1.5px dotted #c9d3dc", height: 7 };
  return (
    <div aria-hidden="true" className="relative w-full overflow-hidden rounded-md"
         style={{ background: "#ffffff", aspectRatio: "210 / 150", border: "1px solid #e4e4e7" }}>
      <div style={{ background: hex, height: 4 }} />
      {layout === "formular" ? (
        <div className="px-3 pt-2 space-y-2">
          <div className="flex justify-center"><div style={{ background: hex, height: 7, width: "46%", borderRadius: 2 }} /></div>
          <div className="grid grid-cols-2 gap-3">
            {[0, 1].map((i) => (
              <div key={i} className="space-y-1">
                <div style={{ background: hex, height: 4, width: "45%", borderRadius: 2 }} />
                <div style={{ background: hex, opacity: 0.35, height: 1 }} />
                <div style={punkt} /><div style={punkt} />
              </div>
            ))}
          </div>
          <div className="rounded flex items-center justify-around py-1.5"
               style={{ background: "#f5f8fb", border: "1px solid #e4e4e7" }}>
            <div style={{ background: hex, height: 6, width: "28%", borderRadius: 2 }} />
            <div style={{ ...linie, width: "22%" }} />
          </div>
          <div style={{ background: hex, height: 4, width: "35%", borderRadius: 2 }} />
          <div style={{ background: hex, opacity: 0.35, height: 1 }} />
          <div className="grid grid-cols-2 gap-3"><div style={punkt} /><div style={punkt} /></div>
        </div>
      ) : (
        <div className="px-3 pt-2 space-y-2">
          <div style={{ background: hex, height: 3, width: "30%", borderRadius: 2 }} />
          <div style={{ background: "#18181b", height: 7, width: "55%", borderRadius: 2 }} />
          <div style={{ background: hex, height: 2 }} />
          <div className="grid grid-cols-2 gap-3">
            {[0, 1].map((i) => (
              <div key={i} className="rounded" style={{ border: "1px solid #e4e4e7" }}>
                <div style={{ background: "#f4f4f5", height: 6 }} />
                <div className="p-1 space-y-1"><div style={linie} /><div style={linie} /></div>
              </div>
            ))}
          </div>
          <div className="flex" style={{ background: "#f4f4f5", height: 7 }}>
            <div style={{ background: hex, width: 3 }} />
          </div>
          <div className="flex items-center justify-end px-2"
               style={{ background: "#0a0a0a", height: 14, borderLeft: `3px solid ${hex}` }}>
            <div style={{ background: "#ffffff", height: 5, width: "26%", borderRadius: 2 }} />
          </div>
        </div>
      )}
    </div>
  );
}

export default function VertragDesign({ farbe, layout, onChange }) {
  const f = vertragFarbe(farbe);
  const l = vertragLayout(layout);
  const hex = vertragFarbeHex(f);
  const [laedt, setLaedt] = useState("");

  async function vorschau(variante) {
    if (laedt) return;
    setLaedt(variante);
    const startMs = Date.now();
    try {
      const r = await api.post("/dealer/vertrag-vorschau", { farbe: f, layout: l, variante },
                               { responseType: "blob" });
      blobOeffnen(r.data, { startMs, titel: "Die Vorschau", mime: "application/pdf",
                            dateiname: "Kaufvertrag-Muster.pdf" });
    } catch (e) {
      toast.error(errMsg(e, "Die Vorschau konnte nicht geladen werden"));
    } finally {
      setLaedt("");
    }
  }

  return (
    <div className="space-y-6" data-testid="vertrag-design">
      <div>
        <div className="text-sm font-semibold mb-2" style={{ color: "var(--text-primary)" }}>Layout</div>
        <div className="grid sm:grid-cols-2 gap-3">
          {VERTRAG_LAYOUTS.map((v) => {
            const an = v.key === l;
            return (
              <button key={v.key} type="button" onClick={() => onChange({ vertrag_layout: v.key })}
                      data-testid={`vertrag-layout-${v.key}`} aria-pressed={an}
                      className="text-left rounded-2xl p-3 transition-colors"
                      style={{ background: "var(--wa-03)",
                               border: `2px solid ${an ? "var(--accent-red)" : "var(--border-default)"}` }}>
                <Skizze layout={v.key} hex={hex} />
                <div className="mt-3 flex items-center gap-2">
                  <span className="font-semibold" style={{ color: "var(--text-primary)" }}>{v.label}</span>
                  {an && <Check size={15} style={{ color: "var(--accent-red)" }} />}
                </div>
                <div className="text-xs mt-1" style={{ color: "var(--text-secondary)" }}>{v.text}</div>
              </button>
            );
          })}
        </div>
      </div>

      <div>
        <div className="text-sm font-semibold mb-2" style={{ color: "var(--text-primary)" }}>Farbe</div>
        <div className="flex flex-wrap gap-2.5" role="radiogroup" aria-label="Farbe des Vertrags">
          {VERTRAG_FARBEN.map((c) => {
            const an = c.key === f;
            return (
              <button key={c.key} type="button" role="radio" aria-checked={an} title={c.label}
                      aria-label={c.label} onClick={() => onChange({ vertrag_farbe: c.key })}
                      data-testid={`vertrag-farbe-${c.key}`}
                      className="w-10 h-10 rounded-full flex items-center justify-center transition-transform hover:scale-105"
                      style={{ background: c.hex,
                               boxShadow: an ? "0 0 0 3px var(--bg-surface), 0 0 0 5px var(--text-primary)" : "none" }}>
                {an && <Check size={16} className="text-white" />}
              </button>
            );
          })}
        </div>
        <div className="text-xs mt-2" style={{ color: "var(--text-secondary)" }}>
          Gewählt: <b>{VERTRAG_FARBEN.find((c) => c.key === f)?.label}</b> — für Überschriften, Linien und den Kaufpreis.
        </div>
      </div>

      <div className="rounded-2xl p-4 text-sm"
           style={{ background: "var(--wa-03)", border: "1px solid var(--border-default)",
                    color: "var(--text-secondary)" }}>
        Gilt für alle <b style={{ color: "var(--text-primary)" }}>neuen</b> Kaufverträge deiner Firma, auch für die
        deiner Sucher. Bestehende Verträge behalten ihr Aussehen. Zum Übernehmen oben auf „Speichern“ tippen —
        die Vorschau geht auch vorher.
      </div>

      <div className="flex flex-wrap gap-2">
        <button type="button" onClick={() => vorschau("druck")} disabled={!!laedt}
                data-testid="vertrag-vorschau-druck" className="apple-btn apple-btn-secondary disabled:opacity-60">
          {laedt === "druck" ? <Loader2 size={14} className="animate-spin" /> : <Eye size={14} />}
          Vorschau ansehen
        </button>
        <button type="button" onClick={() => vorschau("digital")} disabled={!!laedt}
                data-testid="vertrag-vorschau-digital" className="apple-btn apple-btn-secondary disabled:opacity-60">
          {laedt === "digital" ? <Loader2 size={14} className="animate-spin" /> : <Send size={14} />}
          Kundenfassung (E-Mail/WhatsApp)
        </button>
      </div>
    </div>
  );
}
