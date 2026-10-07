import { useState } from "react";
import { toast } from "sonner";
import { ArrowRight, FileText, Send } from "lucide-react";
import { errMsg } from "@/lib/api";
import BeweisCard from "@/components/BeweisCard";
import MarktdatenKarte from "@/components/MarktdatenKarte";
import { openContractPdf } from "@/lib/pdf";

/**
 * Rechte Spalte des Ergebnisses: LIVE-Zähler, Kaufvertrag/PDF/Versand, Beweisdokument, Marktdaten
 * (08.10.2026 aus Vergleich.jsx herausgezogen — nur Anzeige; Abläufe kommen als Rückrufe von der Seite).
 */
export default function AktionenSpalte({
  result, counter, contract, loading, onNeuVergleichen, onVertragErstellen, onVersenden,
}) {
  const [pdfLaeuft, setPdfLaeuft] = useState(false);
  return (
    <div className="lg:col-span-4 space-y-5">
      <div className="apple-surface p-5" data-testid="live-counter-card">
        <div className="flex items-center justify-between">
          <div className="flex items-center gap-2">
            {/* M8/U-07: den Live-Punkt nur mit echtem Zaehlerstand */}
            {counter && <span className="live-dot" />}
            <span className="overline">{counter ? "live" : "Zähler nicht verfügbar"}</span>
          </div>
          <span className="text-[10px]" style={{ color: "var(--text-muted)" }}>aktualisiert alle 30s</span>
        </div>
        <div className="font-display font-black text-4xl mt-3 tracking-tight">
          {counter ? (counter.active_now ?? 0) : "—"}
        </div>
        {/* Runde 27: Gezaehlt werden VERGLEICHE, nicht Haendler — ein
            Sucher kann mehrfach vergleichen. Und das Fenster steht dabei. */}
        <div className="text-sm mt-0.5 font-medium" style={{ color: "var(--text-primary)" }}>
          {counter?.active_now
            ? `${counter.active_now === 1 ? "Vergleich" : "Vergleiche"} in den letzten ${counter?.fenster_minuten ?? 10} Minuten`
            : `Keine Vergleiche in den letzten ${counter?.fenster_minuten ?? 10} Minuten`}
        </div>
        <div className="text-[11px] mt-3 pt-3 border-t" style={{ color: "var(--text-muted)", borderColor: "var(--hairline)" }}>
          Heute insg.: <span className="font-semibold" style={{ color: "var(--text-primary)" }}>{counter ? (counter.today ?? 0) : "—"}</span> Vergleiche
        </div>
      </div>

      <div className="apple-surface p-5">
        <div className="overline mb-3">Aktionen</div>
        {/* Rollenprüfung 22.09.2026 (RP-048/RP-147): Den Hinweis bekommt
            nur noch der Chef (Sucher erfahren seit Runde 29 keine
            Kollegen) — deshalb in seiner Sicht formuliert. */}
        {result.kollege && (
          <div className="text-sm rounded-xl p-3 mb-3" data-testid="kollege-hinweis"
               style={{ background: "#f59e0b1c", color: "var(--tx-amber)" }}>
            Dieses Fahrzeug bearbeitet bereits <b>{result.kollege.name}</b>.
            Legst du selbst einen Kaufvertrag an, bekommt er einen eigenen
            Abholtermin — der Vorgang von {result.kollege.name} bleibt unberührt.
          </div>
        )}
        {/* RP-210/RP-361: in der Firma gelöschtes Fahrzeug — kein neuer
            Vertrag (vorher endete der Klick mit 404). */}
        {result.fahrzeug_geloescht ? (
          <div className="text-sm rounded-xl p-3" data-testid="fahrzeug-geloescht-hinweis"
               style={{ background: "#f59e0b1c", color: "var(--tx-amber)" }}>
            Dieses Fahrzeug wurde in deiner Firma gelöscht. Ein neuer
            Kaufvertrag ist dafür nicht möglich — die Vergleichslinks
            funktionieren trotzdem.
          </div>
        ) : result.fahrzeug_weg ? (
          <div className="space-y-2" data-testid="fahrzeug-weg-hinweis">
            <div className="text-sm rounded-xl p-3"
                 style={{ background: "#f59e0b1c", color: "var(--tx-amber)" }}>
              Dieses Fahrzeug ist nicht mehr in deinem Fahrzeugpool (ältere
              Vergleiche werden aussortiert). Bitte den Link neu vergleichen —
              das kostet nichts.
            </div>
            <button type="button" data-testid="neu-vergleichen-btn"
                    disabled={loading}
                    onClick={onNeuVergleichen}
                    className="apple-btn apple-btn-primary w-full !py-3 disabled:opacity-60">
              <ArrowRight size={15} /> Neu vergleichen
            </button>
          </div>
        ) : (
          <>
            {/* Rollenprüfung 22.09.2026 (RP-416): Steht der eben erstellte
                Vertrag schon da, sagt die Seite das VOR dem Formular —
                vorher kam die Rückfrage (409 vertrag_vorhanden) erst nach
                dem Ausfüllen. Verträge aus früheren Sitzungen fängt
                weiter ContractDialog mit seiner Rückfrage ab. */}
            {contract && (
              <div className="text-sm rounded-xl p-3 mb-3" data-testid="vertrag-vorhanden-hinweis"
                   style={{ background: "#f59e0b1c", color: "var(--tx-amber)" }}>
                Für dieses Fahrzeug hast du schon einen Kaufvertrag erstellt
                {contract.contract_no ? <> (Nr. <b>{contract.contract_no}</b>)</> : null}.
                Ein weiterer Vertrag ergibt einen zweiten Kauf mit eigenem
                Abholtermin — der erste läuft mit seinem Preis weiter.
              </div>
            )}
            <button onClick={onVertragErstellen} data-testid="create-contract-btn"
                    className={`apple-btn ${contract ? "apple-btn-secondary" : "apple-btn-primary"} w-full !py-3`}>
              <FileText size={15} /> {contract ? "Weiteren Kaufvertrag erstellen" : "Kaufvertrag erstellen"}
            </button>
          </>
        )}
        {contract && (
          <div className="mt-3 space-y-2">
            <button
              onClick={async () => {
                if (pdfLaeuft) return;
                setPdfLaeuft(true);
                try { await openContractPdf(contract.id); }
                catch (err) { toast.error(errMsg(err, "Kaufvertrag konnte nicht geladen werden")); }
                finally { setPdfLaeuft(false); }
              }}
              disabled={pdfLaeuft}
              data-testid="open-pdf-btn"
              className="apple-btn apple-btn-secondary w-full disabled:opacity-60"
            >
              <FileText size={14} /> {pdfLaeuft ? "Lädt…" : "PDF öffnen"}
            </button>
            <button onClick={onVersenden} data-testid="send-pdf-btn"
                    className="apple-btn apple-btn-secondary w-full">
              <Send size={14} /> Versenden
            </button>
          </div>
        )}
      </div>

      {/* 18.09.2026: Gibt es zum Inserat noch kein Dokument, steht hier
          der Knopf "Beweisdokument erstellen" (frueher entstand es
          automatisch bei jedem Vergleich). */}
      {/* Prüfbericht 20.09. U-11: beweis_moeglich === false (Daten aus der
          Browser-Erweiterung) — die Karte zeigt statt des Knopfs den Hinweis. */}
      {(result.beweis?.id || result.cache_key) && (
        <BeweisCard key={result.beweis?.id || result.cache_key}
                    beweis={result.beweis} cacheKey={result.cache_key}
                    moeglich={result.beweis_moeglich !== false} />
      )}
      {/* Market Intelligence (25.09.2026): laedt NACH dem fertigen Vergleich
          getrennt, kurzes Zeitlimit, verschwindet still ohne Daten. */}
      {result.vehicle_id && (
        <MarktdatenKarte key={`markt-${result.cache_key || result.vehicle_id}`}
                         vehicleId={result.vehicle_id} preis={result.vehicle.list_price} />
      )}

      <div className="text-[11px] leading-relaxed px-1" style={{ color: "var(--text-muted)" }}>
        <strong style={{ color: "var(--text-primary)" }}>Hinweis:</strong> Der Kaufpreis wird nie automatisch übernommen.
        Verhandelten Preis im nächsten Schritt manuell eintragen.
      </div>
    </div>
  );
}
