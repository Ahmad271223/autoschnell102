import { useCallback, useEffect, useRef, useState } from "react";
import { api, errMsg } from "@/lib/api";
import { toast } from "sonner";
import { AlertTriangle, Copy, MessageCircleQuestion, RefreshCw, Sparkles } from "lucide-react";
import {
  PRIO_FARBE, PRIO_TEXT, argumenteText, eur, kiStatusText, kiWartet, nachPrioritaet, prozent,
} from "@/lib/kiSchaden";

const TAKT_MS = 3000;          // waehrend "laeuft": alle 3 s nachsehen
const WARTE_MAX_MS = 20000;    // danach: "momentan nicht verfuegbar"

/**
 * KI-Einschätzung auf der Freigabeseite (Wunsch Ahmad 25.09.2026).
 * Rein beratend: "Preis übernehmen" trägt den Betrag nur ins bestehende Feld
 * "Neuer Preis" ein, gibt nie frei. "Fahrer fragen" schickt das Protokoll
 * mit der konkreten Frage zurück (bestehender Weg "Zurück an den Fahrer").
 */
export default function KiBewertungKarte({ eintrag, onPreis, onFrage, busy }) {
  const id = eintrag.protocol_id;
  const kurz = eintrag.ki_bewertung || null;
  const [daten, setDaten] = useState(null);
  const [fehler, setFehler] = useState("");
  const [rechnet, setRechnet] = useState(false);
  const start = useRef(Date.now());
  const timer = useRef(null);

  const laden = useCallback(async () => {
    try {
      const r = await api.get(`/protocols/${id}/ki-bewertung`);
      setDaten(r.data);
      setFehler("");
    } catch (e) {
      setFehler(errMsg(e, "KI-Einschätzung konnte nicht geladen werden"));
    }
  }, [id]);

  useEffect(() => { start.current = Date.now(); laden(); }, [laden, kurz?.input_hash]);

  // Nachfragen, solange die Bewertung laeuft — hoechstens WARTE_MAX_MS.
  useEffect(() => {
    const status = daten?.status;
    if (!kiWartet(status)) return undefined;
    if (Date.now() - start.current > WARTE_MAX_MS) return undefined;
    timer.current = setTimeout(laden, TAKT_MS);
    return () => clearTimeout(timer.current);
  }, [daten, laden]);

  const neuBerechnen = async () => {
    setRechnet(true);
    try {
      const r = await api.post(`/protocols/${id}/ki-bewertung/neu`);
      setDaten(r.data);
      start.current = Date.now();
    } catch (e) {
      toast.error(errMsg(e, "Neu berechnen fehlgeschlagen"));
    } finally { setRechnet(false); }
  };

  const kopieren = async (text) => {
    try { await navigator.clipboard.writeText(text); toast.success("Argumente kopiert"); }
    catch { toast.error("Kopieren nicht möglich"); }
  };

  const status = daten?.status || (kurz ? kurz.status : "laeuft");
  const erg = daten?.ergebnis || null;
  const wartetZuLange = kiWartet(status) && Date.now() - start.current > WARTE_MAX_MS;
  const rahmen = { background: "var(--wa-03)", border: "1px solid var(--border-default)" };

  // Ohne Ergebnis: Statuszeile
  if (!erg || status === "keine" || status === "aus") {
    const text = wartetZuLange ? "KI-Einschätzung momentan nicht verfügbar." : (fehler || kiStatusText(status));
    if (!text) return null;
    return (
      <div className="mt-3 rounded-lg p-3 text-[12px] flex flex-wrap items-center gap-2" style={rahmen}
           data-testid={`ki-karte-${id}`} data-status={status}>
        <Sparkles size={13} style={{ color: "var(--accent-red)" }} />
        <span className="flex-1 min-w-[160px]" style={{ color: "var(--text-secondary)" }}>{text}</span>
        {(status === "fehler" || status === "zeitlimit" || status === "ueberlastet" || wartetZuLange) && (
          <button type="button" onClick={neuBerechnen} disabled={rechnet || busy}
                  data-testid={`ki-neu-${id}`}
                  className="inline-flex items-center gap-1 min-h-[36px] px-2 rounded-lg hover:bg-white/10 disabled:opacity-50">
            <RefreshCw size={12} className={rechnet ? "animate-spin" : ""} /> Neu berechnen
          </button>
        )}
      </div>
    );
  }

  const gruppen = nachPrioritaet(erg.items);
  const c = erg.combined || {};
  const kaufpreis = Number(daten?.kaufpreis ?? eintrag.preis_vertrag ?? 0);
  const fahrer = eintrag.preis_vorschlag_fahrer;
  const kiPreis = c.recommended_purchase_price_eur;
  const veraltet = status === "veraltet";

  return (
    <div className="mt-3 rounded-lg p-3" style={rahmen} data-testid={`ki-karte-${id}`} data-status={status}>
      <div className="flex flex-wrap items-center justify-between gap-2">
        <div className="text-[12px] font-semibold inline-flex items-center gap-1.5">
          <Sparkles size={13} style={{ color: "var(--accent-red)" }} /> KI-Einschätzung
          <span className="font-normal" style={{ color: "var(--text-dim)" }}>
            · Sicherheit {prozent(c.confidence)} · beratend
          </span>
        </div>
        <button type="button" onClick={neuBerechnen} disabled={rechnet || busy}
                data-testid={`ki-neu-${id}`}
                className="inline-flex items-center gap-1 text-[11px] min-h-[36px] px-2 rounded-lg hover:bg-white/10 disabled:opacity-50"
                style={{ color: "var(--text-secondary)" }}>
          <RefreshCw size={11} className={rechnet ? "animate-spin" : ""} /> Neu berechnen
        </button>
      </div>
      {veraltet && (
        <div className="mt-1 text-[11px]" style={{ color: "var(--st-amber)" }} data-testid={`ki-veraltet-${id}`}>
          {kiStatusText("veraltet")}
        </div>
      )}

      {/* Preisvergleich Vertrag / Fahrer / KI */}
      <div className="mt-2 grid grid-cols-3 gap-2 text-[11px]">
        {[["Vertrag", kaufpreis], ["Fahrer schlägt vor", fahrer], ["KI-Zielpreis", kiPreis]].map(([k, v]) => (
          <div key={k} className="rounded-lg p-2" style={{ background: "var(--wa-06)" }}>
            <div style={{ color: "var(--text-dim)" }}>{k}</div>
            <div className="text-sm font-semibold">{v != null ? eur(v) : "—"}</div>
          </div>
        ))}
      </div>

      {/* Positionen nach Prioritaet */}
      {["rot", "orange", "gelb"].map((p) => gruppen[p].length > 0 && (
        <div key={p} className="mt-2">
          <div className="text-[10px] uppercase tracking-wider font-semibold" style={{ color: PRIO_FARBE[p] }}>
            {PRIO_TEXT[p]}
          </div>
          <ul className="mt-0.5 space-y-1">
            {gruppen[p].map((it, i) => (
              <li key={`${it.source_id}-${i}`} className="text-[12px] flex flex-wrap items-baseline justify-between gap-x-3"
                  data-testid={`ki-position-${id}-${it.source_id}`}>
                <span className="min-w-0">
                  {it.manual_review_required && <AlertTriangle size={11} className="inline mr-1" style={{ color: "var(--st-rot)" }} />}
                  <span className="font-medium">{it.title}</span>
                  {it.reason && <span className="ml-1" style={{ color: "var(--text-dim)" }}>— {it.reason}</span>}
                </span>
                <span className="shrink-0 text-right">
                  {it.manual_review_required ? (
                    <span style={{ color: "var(--st-rot)" }}>manuelle Entscheidung</span>
                  ) : (
                    <>
                      <b>−{eur(it.recommended_discount_eur)}</b>
                      <span className="ml-1" style={{ color: "var(--text-dim)" }}>
                        ({eur(it.discount_min_eur)}–{eur(it.discount_max_eur)}
                        {it.repair_estimate_eur > 0 ? ` · Reparatur ca. ${eur(it.repair_estimate_eur)}` : ""})
                      </span>
                    </>
                  )}
                </span>
              </li>
            ))}
          </ul>
        </div>
      ))}

      {/* Gesamt */}
      <div className="mt-3 rounded-lg p-2.5 flex flex-wrap items-end justify-between gap-3"
           style={{ background: "var(--wa-06)" }} data-testid={`ki-gesamt-${id}`}>
        <div className="text-[11px]">
          <div style={{ color: "var(--text-dim)" }}>Empfohlener Gesamtnachlass</div>
          <div className="text-lg font-semibold leading-tight">{eur(c.recommended_discount_eur)}</div>
          <div style={{ color: "var(--text-dim)" }}>
            Bereich {eur(c.discount_min_eur)}–{eur(c.discount_max_eur)}
            {c.overlap_adjustment_eur > 0 ? ` · Überschneidung −${eur(c.overlap_adjustment_eur)}` : ""}
            {c.negotiation_start_eur > 0 ? ` · Verhandlung starten bei ${eur(c.negotiation_start_eur)}` : ""}
          </div>
          {c.manual_review_required && (
            <div className="mt-1" style={{ color: "var(--st-rot)" }}>
              Mindestens ein Punkt braucht deine manuelle Entscheidung – die Zahlen decken ihn nicht ab.
            </div>
          )}
        </div>
        {kiPreis != null && (
          <button type="button" disabled={busy} onClick={() => onPreis?.(kiPreis)}
                  data-testid={`ki-preis-uebernehmen-${id}`}
                  className="apple-btn apple-btn-secondary !py-2 disabled:opacity-50">
            {eur(kiPreis)} als neuen Preis übernehmen
          </button>
        )}
      </div>

      {/* Rueckfragen an den Fahrer */}
      {(erg.needs_information || []).length > 0 && (
        <div className="mt-2 text-[12px] space-y-1" data-testid={`ki-fragen-${id}`}>
          {erg.needs_information.map((f, i) => (
            <div key={i} className="flex flex-wrap items-center gap-2">
              <MessageCircleQuestion size={12} style={{ color: "var(--st-amber)" }} />
              <span className="flex-1 min-w-[160px]">Für eine genauere Einschätzung fehlt: <b>{f.question}</b></span>
              <button type="button" disabled={busy} onClick={() => onFrage?.(f)}
                      data-testid={`ki-fahrer-fragen-${id}-${i}`}
                      className="inline-flex items-center gap-1 min-h-[36px] px-2 rounded-lg hover:bg-white/10 disabled:opacity-50"
                      style={{ color: "var(--text-secondary)" }}>
                Fahrer fragen
              </button>
            </div>
          ))}
        </div>
      )}

      {/* Argumente */}
      {(erg.arguments || []).length > 0 && (
        <div className="mt-2 text-[12px]" data-testid={`ki-argumente-${id}`}>
          <div className="flex items-center justify-between">
            <span style={{ color: "var(--text-dim)" }}>Argumente für das Gespräch mit dem Verkäufer</span>
            <button type="button" onClick={() => kopieren(argumenteText(erg.arguments))}
                    className="inline-flex items-center gap-1 min-h-[36px] px-2 rounded-lg hover:bg-white/10"
                    style={{ color: "var(--text-secondary)" }} data-testid={`ki-argumente-kopieren-${id}`}>
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
