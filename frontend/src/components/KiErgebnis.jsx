import { Copy, AlertTriangle } from "lucide-react";
import { toast } from "sonner";
import {
  DATENLAGE_FARBE, DATENLAGE_TEXT, PRIO_FARBE, PRIO_TEXT, RISIKO_TEXT, argumenteText, eur, nachPrioritaet,
  vierText,
} from "@/lib/kiSchaden";

/**
 * Gemeinsame Anzeige eines KI-Ergebnisses (Abholung und Vertrag, Umbau
 * 26.09.2026): je Position und insgesamt vier Geldwerte — Mindestens sinnvoll,
 * Fairer Nachlass (Hauptwert), Sehr gutes Ergebnis, Verhandlung starten —,
 * Datenlage statt Prozent-"Sicherheit", Kaufrisiko, Quellen, Argumente.
 */
export default function KiErgebnis({ erg, kaufpreis, basisText, testPrefix, onPreis, disabled, vorlaeufig }) {
  const c = erg?.combined || {};
  const zielpreis = c.recommended_purchase_price_eur;
  const gruppen = nachPrioritaet(erg?.items);
  const risiko = RISIKO_TEXT[c.deal_risk] || "";
  const kopieren = async (text) => {
    try { await navigator.clipboard.writeText(text); toast.success("Argumente kopiert"); }
    catch { toast.error("Kopieren nicht möglich"); }
  };
  return (
    <div>
      {["rot", "orange", "gelb"].map((p) => gruppen[p].length > 0 && (
        <div key={p} className="mt-2">
          <div className="text-[10px] uppercase tracking-wider font-semibold" style={{ color: PRIO_FARBE[p] }}>
            {PRIO_TEXT[p]}
          </div>
          <ul className="mt-0.5 space-y-1.5">
            {gruppen[p].map((it, i) => (
              <li key={`${it.source_id}-${i}`} className="text-[12px] rounded-lg p-2" style={{ background: "var(--wa-06)" }}
                  data-testid={`${testPrefix}-position-${it.source_id}`}>
                <div className="flex flex-wrap items-baseline justify-between gap-x-3">
                  <span className="font-medium min-w-0">
                    {it.manual_review_required && <AlertTriangle size={11} className="inline mr-1" style={{ color: "var(--st-rot)" }} />}
                    {it.title}
                  </span>
                  {it.manual_review_required ? (
                    <span style={{ color: "var(--st-rot)" }}>manuelle Entscheidung</span>
                  ) : (
                    <span className="text-right">Fair <b className="text-sm">{eur(it.fair_discount_eur)}</b></span>
                  )}
                </div>
                {!it.manual_review_required && (
                  <div className="mt-0.5 flex flex-wrap gap-x-3" style={{ color: "var(--text-dim)" }}>
                    <span>{vierText(it)}</span>
                    {it.repair_estimate_eur > 0 && <span>Reparatur ca. {eur(it.repair_estimate_eur)}</span>}
                  </div>
                )}
                {(it.repair_method || it.reason) && (
                  <div className="mt-0.5" style={{ color: "var(--text-dim)" }}>
                    {[it.repair_method, it.reason].filter(Boolean).join(" — ")}
                  </div>
                )}
              </li>
            ))}
          </ul>
        </div>
      ))}

      <div className="mt-3 rounded-lg p-2.5" style={{ background: "var(--wa-06)" }} data-testid={`${testPrefix}-gesamt`}>
        <div className="flex flex-wrap items-center gap-2 text-[11px]" style={{ color: "var(--text-dim)" }}>
          <span>{vorlaeufig ? "Vorläufige Einschätzung (ohne KI)" : "Gesamtempfehlung"}</span>
          {erg?.datenlage && (
            <span className="rounded-full px-2 py-0.5 border" data-testid={`${testPrefix}-datenlage`}
                  style={{ borderColor: DATENLAGE_FARBE[erg.datenlage], color: DATENLAGE_FARBE[erg.datenlage] }}>
              {DATENLAGE_TEXT[erg.datenlage] || erg.datenlage}
            </span>
          )}
          {c.sum_fair_eur > 0 && c.overlap_adjustment_eur > 0 && (
            <span>Einzelwerte {eur(c.sum_fair_eur)} · Überschneidung −{eur(c.overlap_adjustment_eur)}</span>
          )}
        </div>
        <div className="mt-1.5 grid grid-cols-2 sm:grid-cols-4 gap-2 text-[11px]">
          {[["Mindestens sinnvoll", c.minimum_justified_eur], ["Fairer Nachlass", c.fair_discount_eur],
            ["Sehr gutes Ergebnis", c.best_realistic_eur], ["Verhandlung starten", c.negotiation_start_eur]].map(([k, v], i) => (
            <div key={k} className="rounded-lg p-2" style={{ background: i === 1 ? "var(--wa-10)" : "var(--wa-03)" }}>
              <div style={{ color: "var(--text-dim)" }}>{k}</div>
              <div className={i === 1 ? "text-lg font-semibold leading-tight" : "text-sm font-semibold"}>{eur(v)}</div>
            </div>
          ))}
        </div>
        {c.manual_review_required && (
          <div className="mt-1.5 text-[11px]" style={{ color: "var(--st-rot)" }}>
            Mindestens ein Punkt braucht deine eigene Entscheidung – die Zahlen decken ihn nicht ab.
          </div>
        )}
        {risiko && (
          <div className="mt-1.5 text-[11px] font-semibold" style={{ color: "var(--st-rot)" }}
               data-testid={`${testPrefix}-risiko`}>
            {risiko}
          </div>
        )}
        {kaufpreis > 0 && (
          <div className="mt-1.5 text-[11px]" style={{ color: "var(--text-dim)" }}>
            Basis: {basisText} {eur(kaufpreis)}{zielpreis != null ? ` → Zielpreis ${eur(zielpreis)}` : ""}
          </div>
        )}
        {zielpreis != null && onPreis && (
          <button type="button" disabled={disabled} onClick={() => onPreis(zielpreis)}
                  data-testid={`${testPrefix}-preis`}
                  className="apple-btn apple-btn-secondary !py-2 !text-[12px] mt-1.5 disabled:opacity-50">
            {eur(zielpreis)} als Preis übernehmen
          </button>
        )}
        {erg?.market?.median_price_eur && (
          <div className="mt-1.5 text-[11px]" style={{ color: "var(--text-dim)" }} data-testid={`${testPrefix}-markt`}>
            Marktvergleich: {erg.market.comparable_count} ähnliche Fahrzeuge, Median {eur(erg.market.median_price_eur)}
            {erg.market.agreed_vs_median_percent != null ? ` (Preis ${erg.market.agreed_vs_median_percent > 0 ? "+" : ""}${erg.market.agreed_vs_median_percent} %)` : ""}
          </div>
        )}
      </div>

      {(erg?.quellen || []).length > 0 && (
        <div className="mt-2 text-[11px]" data-testid={`${testPrefix}-quellen`} style={{ color: "var(--text-dim)" }}>
          Marktrecherche: {erg.quellen.slice(0, 6).map((q, i) => (
            <span key={i}>{i > 0 ? " · " : ""}
              <a href={q.url} target="_blank" rel="noopener noreferrer" className="underline">{q.titel || q.url}</a>
            </span>
          ))}
        </div>
      )}

      {(erg?.arguments || []).length > 0 && (
        <div className="mt-2 text-[12px]" data-testid={`${testPrefix}-argumente`}>
          <div className="flex items-center justify-between">
            <span style={{ color: "var(--text-dim)" }}>Argumente für das Gespräch mit dem Verkäufer</span>
            <button type="button" onClick={() => kopieren(argumenteText(erg.arguments))}
                    className="inline-flex items-center gap-1 min-h-[36px] px-2 rounded-lg hover:bg-white/10"
                    style={{ color: "var(--text-secondary)" }} data-testid={`${testPrefix}-argumente-kopieren`}>
              <Copy size={11} /> kopieren
            </button>
          </div>
          <ol className="list-decimal pl-5 space-y-0.5">
            {erg.arguments.map((a, i) => <li key={i}>{a}</li>)}
          </ol>
        </div>
      )}
    </div>
  );
}
