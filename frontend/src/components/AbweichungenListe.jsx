import { AlertTriangle, Check } from "lucide-react";

/**
 * Nummerierte Abweichungen eines Abholprotokolls (Wunsch Ahmad 24.11.2026).
 *
 * Die Nummern vergibt der Server (backend/abweichung_nummern.py) — fest, sie
 * verschieben sich nicht, wenn später eine Abweichung dazukommt oder wegfällt.
 * Büro (Freigaben, Terminplaner) und Fahrer-App zeigen dieselbe Liste:
 * „Nr. 3 · Kratzer · Tür hinten links“. Fehlende Ausstattung steht gesammelt
 * unter „Ausstattung fehlt“.
 */
export function abweichungZeile(e) {
  return `Nr. ${e.nr} · ${e.titel}`;
}

export default function AbweichungenListe({ daten, testId = "abweichungen", leerText = "" }) {
  if (!daten || !Array.isArray(daten.gruppen)) return null;     // älteres Backend
  const anzahl = Number(daten.anzahl) || 0;
  if (anzahl === 0) {
    if (!leerText) return null;
    return (
      <div className="text-[12px] inline-flex items-center gap-1.5" style={{ color: "var(--st-gruen)" }}
           data-testid={`${testId}-keine`}>
        <Check size={13} /> {leerText}
      </div>
    );
  }
  return (
    <div className="rounded-lg p-2.5"
         style={{ background: "color-mix(in srgb, var(--st-amber) 7%, transparent)",
                  border: "1px solid color-mix(in srgb, var(--st-amber) 30%, transparent)" }}
         data-testid={testId}>
      <div className="text-[12px] font-semibold mb-1 inline-flex items-center gap-1" style={{ color: "var(--st-amber)" }}>
        <AlertTriangle size={12} /> {anzahl} Abweichung{anzahl === 1 ? "" : "en"}
      </div>
      {daten.gruppen.map((g) => (
        <div key={g.key} className="mt-1.5" data-testid={`${testId}-gruppe-${g.key}`}>
          <div className="text-[10px] uppercase tracking-wider" style={{ color: "var(--text-dim)" }}>{g.titel}</div>
          <ul className="text-[13px] space-y-0.5">
            {(g.eintraege || []).map((e) => (
              <li key={e.schluessel} className="[overflow-wrap:anywhere]" data-testid={`${testId}-nr-${e.nr}`}>
                <b style={{ color: "var(--text-strong)" }}>Nr. {e.nr}</b>
                <span style={{ color: "var(--text-strong)" }}> · {e.titel}</span>
                {e.text && <span className="block sm:inline text-[12px] sm:ml-1.5" style={{ color: "var(--text-dim)" }}>{e.text}</span>}
              </li>
            ))}
          </ul>
        </div>
      ))}
    </div>
  );
}
