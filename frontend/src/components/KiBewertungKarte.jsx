import { useCallback, useEffect, useRef, useState } from "react";
import { api, errMsg } from "@/lib/api";
import { toast } from "sonner";
import { RefreshCw, Sparkles } from "lucide-react";
import { eur, kiStatusText, kiWartet } from "@/lib/kiSchaden";
import KiErgebnis from "./KiErgebnis";

const TAKT_MS = 3000;          // waehrend "laeuft": alle 3 s nachsehen
const WARTE_MAX_MS = 160000;   // Websuche + KI: bis zu gut zwei Minuten

/**
 * KI-Einschätzung auf der Freigabeseite (Wunsch Ahmad 25./26.09.2026).
 * Rein beratend: "Preis übernehmen" trägt den Betrag nur ins bestehende Feld
 * "Neuer Preis" ein, gibt nie frei. Keine Rückfragen mehr — alles kommt aus
 * dem Fahrer-Formular; vier Geldwerte statt Spanne und Prozent.
 */
export default function KiBewertungKarte({ eintrag, onPreis, busy }) {
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

  // Review 26.09.2026 (Nr. 119/120): die Kurzform der Liste weiss, ob die
  // Bewertung zum heutigen Protokollstand passt — bis die Karte selbst
  // geladen hat, zeigt sie "veraltet – wird neu berechnet" statt alter Zahlen.
  const status = daten?.status || (kurz ? (kurz.veraltet ? "veraltet" : kurz.status) : "laeuft");
  const erg = daten?.ergebnis || null;
  const wartetZuLange = kiWartet(status) && Date.now() - start.current > WARTE_MAX_MS;
  const rahmen = { background: "var(--wa-03)", border: "1px solid var(--border-default)" };

  if (!erg || status === "keine" || status === "aus" || status === "freischaltung") {
    // Review 26.09.2026 (Nr. 6): bei einem Fehler nennt der Server den Grund
    // (z. B. "KI hat Position … nicht bewertet — bitte erneut starten").
    const grundServer = ["fehler", "zeitlimit", "ueberlastet", "abgelehnt"].includes(status) && daten?.grund
      ? ` ${daten.grund}` : "";
    const text = wartetZuLange ? "KI-Einschätzung momentan nicht verfügbar." : (fehler || (kiStatusText(status) + grundServer));
    if (!text) return null;
    return (
      <div className="mt-3 rounded-lg p-3 text-[12px] flex flex-wrap items-center gap-2" style={rahmen}
           data-testid={`ki-karte-${id}`} data-status={status}>
        {status === "laeuft" ? <RefreshCw size={13} className="animate-spin" style={{ color: "var(--accent-red)" }} />
                             : <Sparkles size={13} style={{ color: "var(--accent-red)" }} />}
        <span className="flex-1 min-w-[160px]" style={{ color: "var(--text-secondary)" }}>
          {text}{status === "laeuft" ? " Mit Marktrecherche dauert das bis zu zwei Minuten." : ""}
        </span>
        {(["fehler", "zeitlimit", "ueberlastet", "netz"].includes(status) || wartetZuLange) && (
          <button type="button" onClick={neuBerechnen} disabled={rechnet || busy}
                  data-testid={`ki-neu-${id}`}
                  className="inline-flex items-center gap-1 min-h-[36px] px-2 rounded-lg hover:bg-white/10 disabled:opacity-50">
            <RefreshCw size={12} className={rechnet ? "animate-spin" : ""} /> Neu berechnen
          </button>
        )}
      </div>
    );
  }

  const kaufpreis = Number(daten?.kaufpreis ?? eintrag.preis_vertrag ?? 0);
  const fahrer = eintrag.preis_vorschlag_fahrer;
  const kiPreis = erg.combined?.recommended_purchase_price_eur;
  const veraltet = status === "veraltet";

  return (
    <div className="mt-3 rounded-lg p-3" style={rahmen} data-testid={`ki-karte-${id}`} data-status={status}>
      <div className="flex flex-wrap items-center justify-between gap-2">
        <div className="text-[12px] font-semibold inline-flex items-center gap-1.5">
          <Sparkles size={13} style={{ color: "var(--accent-red)" }} /> KI-Einschätzung
          <span className="font-normal" style={{ color: "var(--text-dim)" }}>· beratend</span>
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
        {[["Vertrag", kaufpreis], ["Fahrer schlägt vor", fahrer], ["KI-Zielpreis (fair)", kiPreis]].map(([k, v]) => (
          <div key={k} className="rounded-lg p-2" style={{ background: "var(--wa-06)" }}>
            <div style={{ color: "var(--text-dim)" }}>{k}</div>
            <div className="text-sm font-semibold">{v != null ? eur(v) : "—"}</div>
          </div>
        ))}
      </div>

      <KiErgebnis erg={erg} kaufpreis={kaufpreis} basisText="Vertragspreis" testPrefix={`ki-${id}`}
                  onPreis={(p) => onPreis?.(p)} disabled={busy} />
    </div>
  );
}
