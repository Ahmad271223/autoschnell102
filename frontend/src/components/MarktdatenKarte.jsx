import { useEffect, useState } from "react";
import { BarChart3 } from "lucide-react";
import { api } from "@/lib/api";
import { DATENLAGE, bestandText, datumZeit, eur, medianLabel, medianSample, seitErstbeobachtung, trendFarbe, trendText } from "@/lib/markt";

const ZEITLIMIT_MS = 8000;

/**
 * Karte "AutoSchnell Marktdaten" im Vergleich (Auftrag Ahmad 25.09.2026).
 * Lädt NACH dem fertigen Vergleich getrennt (GET /market-intelligence/vehicle/{id}),
 * kurzes Zeitlimit, und verschwindet still bei 404/Timeout/Fehler — der
 * Vergleich bleibt in jedem Fall vollständig benutzbar. Nur die N günstigsten
 * Vergleichsangebote (N je Suchauftrag), kein Marktmedian.
 * Reparaturwelle 6 Nr. 146: die Differenz "dieses Inserat zum Median" rechnet die
 * Karte selbst aus dem übergebenen Preis (der Vergleich kennt den aktuellen Preis);
 * die Backend-Differenz ist nur der Rückfall ohne Preis.
 */
function differenz(preis, median, minPreis, daten) {
  const p = Number(preis);
  if (preis && median && Number.isFinite(p)) {
    const d = Math.round((p - median) * 100) / 100;
    return { eur: d, pct: Math.round((d / median) * 1000) / 10, unterMin: minPreis != null && p < Number(minPreis) };
  }
  if (daten?.preis_vs_median_eur == null) return null;
  return { eur: daten.preis_vs_median_eur, pct: daten.preis_vs_median_pct, unterMin: !!(daten.unter_sample_min ?? daten.unter_top20_min) };
}
export default function MarktdatenKarte({ vehicleId, preis }) {
  const [daten, setDaten] = useState(null);
  useEffect(() => {
    if (!vehicleId) return undefined;
    let aktiv = true;
    const abbruch = new AbortController();
    api.get(`/market-intelligence/vehicle/${vehicleId}`, { timeout: ZEITLIMIT_MS, signal: abbruch.signal })
      .then((r) => { if (aktiv && (r?.data?.sample_size || r?.data?.kein_segment_grund)) setDaten(r.data); })
      .catch(() => { /* still: keine Marktdaten, keine Karte */ });
    return () => { aktiv = false; abbruch.abort(); };
  }, [vehicleId]);
  if (!daten) return null;
  if (!daten.sample_size && daten.kein_segment_grund) {
    // Reparaturwelle 5 Nr. 28: kein exaktes Segment (z. B. Getriebe am Fahrzeug unbekannt) — sagen statt verschwinden
    return (
      <div className="apple-surface p-4" data-testid="marktdaten-karte-hinweis">
        <div className="overline inline-flex items-center gap-1.5"><BarChart3 size={13} /> AutoSchnell Marktdaten</div>
        <div className="mt-1 text-[12px]" style={{ color: "var(--text-secondary)" }}>{daten.kein_segment_grund}</div>
      </div>
    );
  }
  const dl = DATENLAGE[daten.datenlage] || DATENLAGE.keine;
  const l = daten.listing;
  const median = medianSample(daten);
  const maxPreis = daten.max_sample_price ?? daten.max_top20_price;
  const diff = differenz(preis, median, daten.min_price, daten);
  return (
    <div className="apple-surface p-5" data-testid="marktdaten-karte">
      <div className="flex items-center justify-between gap-2">
        <div className="overline inline-flex items-center gap-1.5"><BarChart3 size={13} /> AutoSchnell Marktdaten</div>
        <span className="text-[10px] rounded-full px-2 py-0.5 border" style={{ color: dl.farbe, borderColor: dl.farbe }}
              data-testid="marktdaten-datenlage">{dl.text}</span>
      </div>
      <div className="mt-2 text-sm font-semibold">{daten.label}</div>
      <div className="text-[11px]" style={{ color: "var(--text-secondary)" }}>
        {daten.km_label}{daten.ez_label ? ` · ${daten.ez_label}` : ""} · {daten.sample_size} günstigste Vergleichsangebote
      </div>
      {daten.sortierung_unsicher && (
        <div className="mt-2 text-[11px] rounded-lg px-2 py-1" style={{ color: "var(--st-amber)", background: "var(--wa-06)" }} data-testid="marktdaten-sortierung">
          Sortierung des letzten Abrufs unsicher — die Werte sind nicht sicher die günstigsten Angebote.
        </div>
      )}
      {daten.top_n_bewiesen === false && !daten.sortierung_unsicher && (
        <div className="mt-2 text-[11px] rounded-lg px-2 py-1" style={{ color: "var(--st-amber)", background: "var(--wa-06)" }} data-testid="marktdaten-top-n">
          Top-N nicht bewiesen — der letzte Abruf kam ohne Positionsnummern (nur monoton sortiert).
        </div>
      )}
      <div className="mt-3 grid grid-cols-3 gap-2 text-[11px]">
        {[["Günstigstes", daten.min_price], [medianLabel(daten.sample_size), median], ["Spanne günstigste", `${eur(daten.min_price)}–${eur(maxPreis)}`]].map(([k, v], i) => (
          <div key={k} className="rounded-lg p-2" style={{ background: "var(--wa-06)" }}>
            <div style={{ color: "var(--text-dim)" }}>{k}</div>
            <div className={i === 1 ? "text-base font-semibold" : "text-sm font-semibold"} data-testid={i === 1 ? "marktdaten-median" : undefined}>{typeof v === "string" ? v : eur(v)}</div>
          </div>
        ))}
      </div>
      <div className="mt-2 text-[12px] flex flex-wrap gap-x-4 gap-y-1">
        <span>30-Tage-Trend: <b style={{ color: trendFarbe(daten.trend_30d_eur) }} data-testid="marktdaten-trend">{trendText(daten.trend_30d_eur, daten.trend_30d_pct)}</b></span>
        {daten.trend_7d_eur != null && <span>7 Tage: <b style={{ color: trendFarbe(daten.trend_7d_eur) }}>{trendText(daten.trend_7d_eur, daten.trend_7d_pct)}</b></span>}
      </div>
      {(daten.anzahl_gemeinsam_30d || daten.anzahl_gemeinsam) ? (
        <div className="text-[11px]" style={{ color: "var(--text-secondary)" }} data-testid="marktdaten-bestand">
          {daten.anzahl_gemeinsam_30d ? `30 Tage, ${bestandText(daten.trend_30d_bestand_eur, daten.trend_30d_bestand_pct, daten.anzahl_gemeinsam_30d)}` : ""}
          {daten.anzahl_gemeinsam_30d && daten.anzahl_gemeinsam ? " · " : ""}
          {daten.anzahl_gemeinsam ? `7 Tage, ${bestandText(daten.trend_7d_bestand_eur, daten.trend_7d_bestand_pct, daten.anzahl_gemeinsam)}` : ""}
        </div>
      ) : null}
      {(preis || diff) && (
        <div className="mt-2 text-[12px]" data-testid="marktdaten-dieses">
          Dieses Inserat: <b>{eur(preis)}</b>
          {diff && (
            <span style={{ color: trendFarbe(diff.eur) }} data-testid="marktdaten-differenz"> · {trendText(diff.eur, diff.pct)} zum {medianLabel(daten.sample_size)}</span>
          )}
          {diff?.unterMin && <span style={{ color: "var(--st-gruen)" }}> · unter dem günstigsten beobachteten Angebot</span>}
        </div>
      )}
      {l && (
        <div className="mt-1 text-[12px]" data-testid="marktdaten-verlauf">
          Seit Erstbeobachtung ({datumZeit(l.first_seen_at).slice(0, 6)}): {seitErstbeobachtung(l) || "unverändert"}
          {l.rank_today ? ` · heute Platz ${l.rank_today} von ${daten.sample_size}` : ""}
        </div>
      )}
      <div className="mt-2 text-[10px]" style={{ color: "var(--text-dim)" }}>
        Datenstand {datumZeit(daten.datenstand)} · {daten.sample_size} Fahrzeuge · untere Marktpreisspanne, kein Marktmedian
      </div>
    </div>
  );
}

/** Kleiner Hinweis für den Vertragsdialog (nur lesend, nie Pflicht). */
export function useMarktHinweis(vehicleId, offen) {
  const [daten, setDaten] = useState(null);
  useEffect(() => {
    if (!offen || !vehicleId) { setDaten(null); return undefined; }
    let aktiv = true;
    api.get(`/market-intelligence/vehicle/${vehicleId}`, { timeout: ZEITLIMIT_MS })
      .then((r) => { if (aktiv && r?.data?.sample_size) setDaten(r.data); })
      .catch(() => {});
    return () => { aktiv = false; };
  }, [vehicleId, offen]);
  return daten;
}
