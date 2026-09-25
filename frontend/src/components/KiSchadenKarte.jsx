import { useEffect, useRef, useState } from "react";
import { api, errMsg } from "@/lib/api";
import { RefreshCw, Sparkles } from "lucide-react";
import { alleVollstaendig, kiStatusText, schaedenStand, schadenZeile, schwereOffen } from "@/lib/kiSchaden";
import KiErgebnis from "./KiErgebnis";

const TAKT_MS = 3000;
const WARTE_MAX_MS = 160000;

/**
 * KI-Schadennachlass im Vertragsdialog (Wunsch Ahmad 25./26.09.2026).
 * Erst "Sind das alle Schäden?" — der Knopf ist erst aktiv, wenn jeder
 * Schaden vollständig beschrieben ist ("unbekannt" zählt). Dann EIN Start:
 * sofort eine vorläufige Einschätzung aus unseren Referenzen, die KI (mit
 * Marktrecherche) rechnet im Hintergrund, die Karte fragt nach und ersetzt
 * die Vorschau. Keine Rückfragen mehr. Rein beratend: "als Preis übernehmen"
 * füllt nur das Preisfeld.
 */
export default function KiSchadenKarte({ vehicleId, damages, preisBetrag, onPreis, onBewertungId, onWeitere, disabled }) {
  const [daten, setDaten] = useState(null);
  const [startet, setStartet] = useState(false);
  const [bewertetStand, setBewertetStand] = useState(null);
  const start = useRef(0);
  const liste = damages || [];
  const anzahl = liste.length;
  const stand = schaedenStand(liste);
  const vollstaendig = alleVollstaendig(liste);
  const veraltet = Boolean(daten) && bewertetStand !== null && bewertetStand !== stand;
  const rahmen = { background: "var(--wa-03)", border: "1px solid var(--border-default)" };

  useEffect(() => { if (anzahl === 0 && daten) { setDaten(null); setBewertetStand(null); } }, [anzahl, daten]);

  // Nachfragen, solange die KI rechnet
  useEffect(() => {
    if (!daten || daten.status !== "laeuft" || !daten.id) return undefined;
    if (Date.now() - start.current > WARTE_MAX_MS) {
      setDaten((d) => ({ ...d, status: "zeitlimit" }));
      return undefined;
    }
    const t = setTimeout(async () => {
      try {
        const r = await api.get(`/contracts/ki-schadennachlass/${daten.id}`);
        setDaten(r.data);
        if (r.data?.status === "ok" && r.data?.id) onBewertungId?.(r.data.id);
      } catch (e) {
        setDaten((d) => ({ ...d, status: "netz", grund: errMsg(e, "") }));
      }
    }, TAKT_MS);
    return () => clearTimeout(t);
  }, [daten, onBewertungId]);

  const bewerten = async () => {
    if (!vehicleId || anzahl === 0 || startet || !vollstaendig) return;
    setStartet(true);
    start.current = Date.now();
    try {
      const r = await api.post("/contracts/ki-schadennachlass", {
        vehicle_id: vehicleId, damages: liste,
        purchase_price: preisBetrag && preisBetrag > 0 ? preisBetrag : undefined,
      });
      setDaten(r.data);
      setBewertetStand(stand);
      if (r.data?.status === "ok" && r.data?.id) onBewertungId?.(r.data.id);
    } catch (e) {
      setDaten({ status: "netz", grund: errMsg(e, "") });
      setBewertetStand(stand);
    } finally {
      setStartet(false);
    }
  };

  if (anzahl === 0) return null;

  // Noch keine Bewertung oder Schäden seitdem geändert: erst bestätigen
  if (!daten || veraltet) {
    return (
      <div className="rounded-lg p-3" style={rahmen} data-testid="ki-vertrag-frage" data-veraltet={veraltet ? "1" : "0"}
           data-vollstaendig={vollstaendig ? "1" : "0"}>
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
                  <span className="ml-1" style={{ color: "var(--st-amber)" }}>(bitte angeben: {offen.join(", ")})</span>
                )}
              </li>
            );
          })}
        </ul>
        {!vollstaendig && (
          <div className="mt-1.5 text-[11px]" style={{ color: "var(--st-amber)" }} data-testid="ki-vertrag-unvollstaendig">
            Bitte bei jedem Schaden alle Angaben wählen („unbekannt“ ist erlaubt) – dann kann die KI ohne Rückfragen bewerten.
          </div>
        )}
        <div className="mt-2.5 flex flex-wrap gap-2">
          <button type="button" onClick={() => onWeitere?.()} disabled={disabled}
                  data-testid="ki-vertrag-weitere"
                  className="apple-btn apple-btn-secondary !py-2 !text-[12px] disabled:opacity-50">
            Weitere Schäden hinzufügen
          </button>
          <button type="button" onClick={bewerten} disabled={disabled || !vehicleId || !vollstaendig || startet}
                  data-testid="ki-vertrag-bewerten"
                  className="apple-btn apple-btn-primary !py-2 !text-[12px] disabled:opacity-50">
            {startet ? "Startet …" : veraltet ? "Ja, neu bewerten" : "Ja, Schäden bewerten"}
          </button>
        </div>
        <div className="mt-1.5 text-[11px]" style={{ color: "var(--text-dim)" }}>
          Ein Aufruf für alle Schäden, rein beratend. Sofort erscheint eine vorläufige Einschätzung, die KI
          verfeinert sie mit aktuellen Marktpreisen (etwa eine Minute).
        </div>
      </div>
    );
  }

  const status = daten.status;
  const erg = daten.ergebnis || null;
  const vorschau = daten.vorschau || null;
  const basisText = daten.basis === "kaufpreis" ? "Kaufpreis" : "Inseratspreis";

  if (status !== "ok" || !erg) {
    const text = kiStatusText(status) || daten.grund || "KI-Einschätzung momentan nicht verfügbar.";
    return (
      <div className="rounded-lg p-3" style={rahmen} data-testid="ki-vertrag-status" data-status={status}>
        <div className="text-[12px] flex flex-wrap items-center gap-2">
          {status === "laeuft" ? <RefreshCw size={13} className="animate-spin" style={{ color: "var(--accent-red)" }} />
                               : <Sparkles size={13} style={{ color: "var(--accent-red)" }} />}
          <span className="flex-1 min-w-[160px]" style={{ color: "var(--text-secondary)" }}>
            {status === "laeuft" ? "KI rechnet mit aktuellen Marktpreisen … (vorläufige Werte unten)" : text}
            {status === "netz" && daten.grund ? ` (${daten.grund})` : ""}
          </span>
          {status !== "aus" && status !== "keine" && status !== "laeuft" && status !== "budget" && (
            <button type="button" onClick={bewerten} disabled={disabled} data-testid="ki-vertrag-neu"
                    className="inline-flex items-center gap-1 min-h-[36px] px-2 rounded-lg hover:bg-white/10 disabled:opacity-50">
              <RefreshCw size={12} /> Erneut versuchen
            </button>
          )}
        </div>
        {vorschau && (
          <div data-testid="ki-vertrag-vorschau">
            <KiErgebnis erg={{ combined: vorschau, items: [], datenlage: vorschau.datenlage }} kaufpreis={daten.kaufpreis}
                        basisText={basisText} testPrefix="ki-vertrag-vorschau" onPreis={onPreis} disabled={disabled} vorlaeufig />
          </div>
        )}
      </div>
    );
  }

  return (
    <div className="rounded-lg p-3" style={rahmen} data-testid="ki-vertrag-karte" data-status="ok">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <div className="text-[12px] font-semibold inline-flex items-center gap-1.5">
          <Sparkles size={13} style={{ color: "var(--accent-red)" }} /> KI-Schadeneinschätzung
          <span className="font-normal" style={{ color: "var(--text-dim)" }}>· beratend</span>
        </div>
        <button type="button" onClick={bewerten} disabled={disabled} data-testid="ki-vertrag-neu"
                className="inline-flex items-center gap-1 text-[11px] min-h-[36px] px-2 rounded-lg hover:bg-white/10 disabled:opacity-50"
                style={{ color: "var(--text-secondary)" }}>
          <RefreshCw size={11} /> Neu bewerten
        </button>
      </div>
      <KiErgebnis erg={erg} kaufpreis={daten.kaufpreis} basisText={basisText} testPrefix="ki-vertrag"
                  onPreis={onPreis} disabled={disabled} />
    </div>
  );
}
