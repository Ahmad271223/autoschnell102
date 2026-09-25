import { useEffect, useState } from "react";
import { api, errMsg } from "@/lib/api";
import { toast } from "sonner";
import { AlertTriangle, Copy, MessageCircleQuestion, RefreshCw, Sparkles } from "lucide-react";
import {
  argumenteText, eur, frageSchluessel, kiStatusText, mitRueckfrageAntwort, prozent, schaedenStand,
  schadenZeile, schwereOffen,
} from "@/lib/kiSchaden";

const LANGSAM_MS = 8000;

/**
 * KI-Schadennachlass im Vertragsdialog (Wunsch Ahmad 25.09.2026, Stufe 3).
 * Erst "Sind das alle Schäden?" — dann EIN KI-Aufruf für alle Schäden, die
 * Karte rechts neben der Skizze. Rein beratend: "als Kaufpreis übernehmen"
 * füllt nur das Preisfeld. Rückfragen der KI beantwortet der Sucher per
 * Knopf; die Antwort landet am Schaden (severity_data) und die Karte gilt
 * als veraltet, bis neu bewertet wurde.
 */
export default function KiSchadenKarte({
  vehicleId, damages, onDamagesChange, preisBetrag, onPreis, onBewertungId, onWeitere, disabled,
}) {
  const [daten, setDaten] = useState(null);
  const [laeuft, setLaeuft] = useState(false);
  const [langsam, setLangsam] = useState(false);
  const [bewertetStand, setBewertetStand] = useState(null);
  const liste = damages || [];
  const anzahl = liste.length;
  const stand = schaedenStand(liste);
  const veraltet = Boolean(daten) && bewertetStand !== null && bewertetStand !== stand;
  const rahmen = { background: "var(--wa-03)", border: "1px solid var(--border-default)" };

  useEffect(() => {
    if (!laeuft) { setLangsam(false); return undefined; }
    const t = setTimeout(() => setLangsam(true), LANGSAM_MS);
    return () => clearTimeout(t);
  }, [laeuft]);

  // Alle Schäden entfernt: alte Karte weg.
  useEffect(() => { if (anzahl === 0 && daten) { setDaten(null); setBewertetStand(null); } }, [anzahl, daten]);

  const bewerten = async () => {
    if (!vehicleId || anzahl === 0 || laeuft) return;
    setLaeuft(true);
    try {
      const r = await api.post("/contracts/ki-schadennachlass", {
        vehicle_id: vehicleId, damages: liste,
        purchase_price: preisBetrag && preisBetrag > 0 ? preisBetrag : undefined,
      });
      setDaten(r.data);
      setBewertetStand(stand);
      if (r.data?.status === "ok" && r.data?.id) onBewertungId?.(r.data.id);
    } catch (e) {
      // Netz/Server nicht erreichbar: eigener Status, der Grund bleibt sichtbar
      setDaten({ status: "netz", grund: errMsg(e, "") });
      setBewertetStand(stand);
    } finally {
      setLaeuft(false);
    }
  };

  const kopieren = async (text) => {
    try { await navigator.clipboard.writeText(text); toast.success("Argumente kopiert"); }
    catch { toast.error("Kopieren nicht möglich"); }
  };

  if (anzahl === 0) return null;

  if (laeuft) {
    return (
      <div className="rounded-lg p-3 text-[12px]" style={rahmen} data-testid="ki-vertrag-laeuft">
        <div className="inline-flex items-center gap-1.5 font-semibold">
          <RefreshCw size={13} className="animate-spin" style={{ color: "var(--accent-red)" }} /> KI bewertet die Schäden …
        </div>
        <div className="mt-1" style={{ color: "var(--text-dim)" }}>
          {langsam ? "Die Einschätzung dauert etwas länger – du kannst währenddessen weiter ausfüllen."
                   : "Ein Aufruf für alle Schäden, meist 15–25 Sekunden."}
        </div>
      </div>
    );
  }

  // Noch keine Bewertung oder Schäden seitdem geändert: erst bestätigen
  if (!daten || veraltet) {
    return (
      <div className="rounded-lg p-3" style={rahmen} data-testid="ki-vertrag-frage" data-veraltet={veraltet ? "1" : "0"}>
        <div className="text-[12px] font-semibold inline-flex items-center gap-1.5">
          <Sparkles size={13} style={{ color: "var(--accent-red)" }} />
          {veraltet ? "Schäden geändert – neu bewerten?" : "Sind das alle bekannten Schäden?"}
        </div>
        <div className="text-[11px] mt-0.5" style={{ color: "var(--text-dim)" }}>
          {anzahl === 1 ? "1 Schaden erfasst" : `${anzahl} Schäden erfasst`}
        </div>
        <ul className="mt-1.5 space-y-0.5 text-[12px]">
          {liste.map((d, i) => {
            const offen = schwereOffen(d);
            return (
              <li key={d.id || i} data-testid={`ki-vertrag-schaden-${d.id || i}`}>
                • {schadenZeile(d)}
                {offen.length > 0 && (
                  <span className="ml-1" style={{ color: "var(--st-amber)" }}>(fehlt: {offen.join(", ")})</span>
                )}
              </li>
            );
          })}
        </ul>
        <div className="mt-2.5 flex flex-wrap gap-2">
          <button type="button" onClick={() => onWeitere?.()} disabled={disabled}
                  data-testid="ki-vertrag-weitere"
                  className="apple-btn apple-btn-secondary !py-2 !text-[12px] disabled:opacity-50">
            Weitere Schäden hinzufügen
          </button>
          <button type="button" onClick={bewerten} disabled={disabled || !vehicleId}
                  data-testid="ki-vertrag-bewerten"
                  className="apple-btn apple-btn-primary !py-2 !text-[12px] disabled:opacity-50">
            {veraltet ? "Ja, neu bewerten" : "Ja, Schäden bewerten"}
          </button>
        </div>
        <div className="mt-1.5 text-[11px]" style={{ color: "var(--text-dim)" }}>
          Ein KI-Aufruf für alle Schäden zusammen, rein beratend. Die Zusatzangaben
          (Größe, Lack, Länge) machen die Schätzung deutlich genauer.
        </div>
      </div>
    );
  }

  const status = daten.status;
  const erg = daten.ergebnis || null;
  if (status !== "ok" || !erg) {
    const text = (kiStatusText(status) || "KI-Einschätzung momentan nicht verfügbar.")
      + (status === "netz" && daten.grund ? ` (${daten.grund})` : "");
    return (
      <div className="rounded-lg p-3 text-[12px] flex flex-wrap items-center gap-2" style={rahmen}
           data-testid="ki-vertrag-status" data-status={status}>
        <Sparkles size={13} style={{ color: "var(--accent-red)" }} />
        <span className="flex-1 min-w-[160px]" style={{ color: "var(--text-secondary)" }}>{text}</span>
        {status !== "aus" && status !== "keine" && (
          <button type="button" onClick={bewerten} disabled={disabled} data-testid="ki-vertrag-neu"
                  className="inline-flex items-center gap-1 min-h-[36px] px-2 rounded-lg hover:bg-white/10 disabled:opacity-50">
            <RefreshCw size={12} /> Erneut versuchen
          </button>
        )}
      </div>
    );
  }

  const c = erg.combined || {};
  const zielpreis = c.recommended_purchase_price_eur;
  const basisText = daten.basis === "kaufpreis" ? "Kaufpreis" : "Inseratspreis";

  return (
    <div className="rounded-lg p-3" style={rahmen} data-testid="ki-vertrag-karte" data-status="ok">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <div className="text-[12px] font-semibold inline-flex items-center gap-1.5">
          <Sparkles size={13} style={{ color: "var(--accent-red)" }} /> KI-Schadeneinschätzung
          <span className="font-normal" style={{ color: "var(--text-dim)" }}>
            · Sicherheit {prozent(c.confidence)} · beratend
          </span>
        </div>
        <button type="button" onClick={bewerten} disabled={disabled} data-testid="ki-vertrag-neu"
                className="inline-flex items-center gap-1 text-[11px] min-h-[36px] px-2 rounded-lg hover:bg-white/10 disabled:opacity-50"
                style={{ color: "var(--text-secondary)" }}>
          <RefreshCw size={11} /> Neu bewerten
        </button>
      </div>

      <ul className="mt-2 space-y-2">
        {(erg.items || []).map((it, i) => (
          <li key={`${it.source_id}-${i}`} className="text-[12px] rounded-lg p-2" style={{ background: "var(--wa-06)" }}
              data-testid={`ki-vertrag-position-${it.source_id}`}>
            <div className="flex flex-wrap items-baseline justify-between gap-x-3">
              <span className="font-medium min-w-0">
                {it.manual_review_required && <AlertTriangle size={11} className="inline mr-1" style={{ color: "var(--st-rot)" }} />}
                {it.title}
              </span>
              {it.manual_review_required ? (
                <span style={{ color: "var(--st-rot)" }}>manuelle Entscheidung</span>
              ) : (
                <span className="text-right">
                  Nachlass <b className="text-sm">{eur(it.recommended_discount_eur)}</b>
                </span>
              )}
            </div>
            {!it.manual_review_required && (
              <div className="mt-0.5 flex flex-wrap gap-x-3" style={{ color: "var(--text-dim)" }}>
                {it.repair_estimate_eur > 0 && <span>Reparatur ca. {eur(it.repair_estimate_eur)}</span>}
                <span>Bereich {eur(it.discount_min_eur)}–{eur(it.discount_max_eur)}</span>
                <span>Sicherheit {prozent(it.confidence)}</span>
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

      <div className="mt-2 rounded-lg p-2.5" style={{ background: "var(--wa-06)" }} data-testid="ki-vertrag-gesamt">
        <div className="text-[11px]" style={{ color: "var(--text-dim)" }}>
          Einzelwerte {eur(c.sum_of_items_eur)}
          {c.overlap_adjustment_eur > 0 ? ` · Überschneidung −${eur(c.overlap_adjustment_eur)}` : ""}
        </div>
        <div className="text-[11px] mt-1" style={{ color: "var(--text-dim)" }}>Empfohlener Gesamtnachlass</div>
        <div className="text-lg font-semibold leading-tight">{eur(c.recommended_discount_eur)}</div>
        <div className="text-[11px]" style={{ color: "var(--text-dim)" }}>
          Bereich {eur(c.discount_min_eur)}–{eur(c.discount_max_eur)}
          {c.negotiation_start_eur > 0 ? ` · Verhandlung starten bei ${eur(c.negotiation_start_eur)}` : ""}
        </div>
        {c.manual_review_required && (
          <div className="mt-1 text-[11px]" style={{ color: "var(--st-rot)" }}>
            Mindestens ein Schaden braucht deine eigene Entscheidung – die Zahlen decken ihn nicht ab.
          </div>
        )}
        {daten.kaufpreis > 0 && (
          <div className="mt-1.5 text-[11px]" style={{ color: "var(--text-dim)" }}>
            Basis: {basisText} {eur(daten.kaufpreis)}
            {zielpreis != null ? ` → Zielpreis ${eur(zielpreis)}` : ""}
          </div>
        )}
        {zielpreis != null && (
          <button type="button" disabled={disabled} onClick={() => onPreis?.(zielpreis)}
                  data-testid="ki-vertrag-preis"
                  className="apple-btn apple-btn-secondary !py-2 !text-[12px] mt-1.5 disabled:opacity-50">
            {eur(zielpreis)} als Kaufpreis übernehmen
          </button>
        )}
      </div>

      {(erg.needs_information || []).length > 0 && (
        <div className="mt-2 text-[12px] space-y-1.5" data-testid="ki-vertrag-fragen">
          {erg.needs_information.map((f, i) => {
            const schluessel = frageSchluessel(f.question);
            const schaden = liste.find((d) => String(d.id) === String(f.source_id));
            const gewaehlt = schaden?.severity_data?.[schluessel] || "";
            const optionen = f.options?.length ? f.options : ["Ja", "Nein", "Unbekannt"];
            return (
              <div key={i}>
                <div className="inline-flex items-start gap-1.5">
                  <MessageCircleQuestion size={12} className="mt-0.5 shrink-0" style={{ color: "var(--st-amber)" }} />
                  <span>Für eine genauere Einschätzung fehlt: <b>{f.question}</b></span>
                </div>
                <div className="mt-1 flex flex-wrap gap-1.5">
                  {optionen.map((o) => (
                    <button key={o} type="button" disabled={disabled || !schaden}
                            data-testid={`ki-vertrag-antwort-${f.source_id}-${o}`}
                            onClick={() => onDamagesChange?.(mitRueckfrageAntwort(liste, f, o))}
                            className="min-h-[32px] px-2.5 rounded-lg text-[11px] font-semibold border disabled:opacity-50"
                            style={gewaehlt === o
                              ? { background: "var(--accent-red)", color: "#fff", borderColor: "var(--accent-red)" }
                              : { borderColor: "var(--border-default)", color: "var(--text-primary)", background: "var(--wa-03)" }}>
                      {o}
                    </button>
                  ))}
                </div>
              </div>
            );
          })}
        </div>
      )}

      {/* Stufe 5 (26.09.2026): Quellen der Marktrecherche zu diesem Fall */}
      {(erg.quellen || []).length > 0 && (
        <div className="mt-2 text-[11px]" data-testid={`ki-vertrag-quellen`} style={{ color: "var(--text-dim)" }}>
          Marktrecherche: {erg.quellen.slice(0, 6).map((q, i) => (
            <span key={i}>{i > 0 ? " · " : ""}
              <a href={q.url} target="_blank" rel="noopener noreferrer" className="underline">
                {q.titel || q.url}
              </a>
            </span>
          ))}
        </div>
      )}

      {(erg.arguments || []).length > 0 && (
        <div className="mt-2 text-[12px]" data-testid="ki-vertrag-argumente">
          <div className="flex items-center justify-between">
            <span style={{ color: "var(--text-dim)" }}>Argumente für das Gespräch mit dem Verkäufer</span>
            <button type="button" onClick={() => kopieren(argumenteText(erg.arguments))}
                    className="inline-flex items-center gap-1 min-h-[36px] px-2 rounded-lg hover:bg-white/10"
                    style={{ color: "var(--text-secondary)" }} data-testid="ki-vertrag-argumente-kopieren">
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
