import { DATENQUALITAET, MARKTTIEFE, VOLLSTAENDIGKEIT, qualitaetsText } from "@/lib/markt";

/**
 * Master-Auftrag 26.09.2026, Phase C: zwei getrennte Anzeigen statt einer "Datenlage" —
 * Datenqualität (war der Abruf technisch verlässlich?) und Markttiefe (wie viele Angebote gibt
 * es überhaupt?). Ein dünner oder leerer Markt ist eine Marktlücke, kein Fehler.
 * Vollständigkeit optional (UNKNOWN, solange der Scraper keine verlässliche Gesamtzahl liefert).
 */
export default function MarktQualitaet({ q, klein = false, mitVollstaendigkeit = false, testid = "markt-qualitaet" }) {
  if (!q || (!q.data_quality && !q.market_depth)) return null;
  const dq = DATENQUALITAET[q.data_quality] || DATENQUALITAET.UNKNOWN;
  const mt = MARKTTIEFE[q.market_depth] || MARKTTIEFE.UNKNOWN;
  const cls = `${klein ? "text-[10px] px-1.5" : "text-[11px] px-2 py-0.5"} rounded-full border whitespace-nowrap`;
  return (
    <span className="inline-flex flex-wrap items-center gap-1" data-testid={testid}>
      <span className={cls} style={{ color: dq.farbe, borderColor: dq.farbe }} title={qualitaetsText(q)} data-testid={`${testid}-dq`}>
        {klein ? dq.kurz : qualitaetsText(q)}
      </span>
      <span className={cls} style={{ color: mt.farbe, borderColor: mt.farbe }} title={mt.text} data-testid={`${testid}-tiefe`}>
        {klein ? mt.kurz : mt.text}
      </span>
      {mitVollstaendigkeit && q.sample_completeness && (
        <span className={cls} style={{ color: "var(--text-dim)", borderColor: "var(--wa-12)" }} data-testid={`${testid}-vollstaendigkeit`}>
          {VOLLSTAENDIGKEIT[q.sample_completeness] || q.sample_completeness}
        </span>
      )}
    </span>
  );
}

const DQ_REIHE = ["GOOD", "MEDIUM", "POOR", "UNKNOWN"];
const TIEFE_REIHE = ["FULL", "NORMAL", "THIN", "EMPTY", "UNKNOWN"];

/** Modellübersicht: Zähler je Stufe über die aktiven Segmente eines Modells ("22 gut · 1 schlecht"). */
export function QualitaetZaehler({ qz, tz, testid = "markt-qualitaet-zaehler" }) {
  const teile = (z, reihe, tab) => reihe.filter((k) => z?.[k]).map((k) => (
    <span key={k} style={{ color: tab[k].farbe }}>{z[k]} {tab[k].zaehler}</span>
  ));
  const dq = teile(qz, DQ_REIHE, DATENQUALITAET);
  const tiefe = teile(tz, TIEFE_REIHE, MARKTTIEFE);
  if (!dq.length && !tiefe.length) return <span className="text-zinc-500">—</span>;
  return (
    <div className="text-[11px] leading-snug" data-testid={testid}>
      <div className="flex flex-wrap gap-x-1.5"><span className="text-zinc-500">Qualität:</span>{dq}</div>
      <div className="flex flex-wrap gap-x-1.5"><span className="text-zinc-500">Tiefe:</span>{tiefe}</div>
    </div>
  );
}
