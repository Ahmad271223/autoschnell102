import { useCallback, useEffect, useRef, useState } from "react";
import { RefreshCw, Sparkles } from "lucide-react";
import { driverApi } from "@/context/DriverContext";
import { errMsg } from "@/lib/api";
import { eur, kiStatusText, kiWartet } from "@/lib/kiSchaden";
import KiErgebnis from "./KiErgebnis";

const TAKT_MS = 3000;          // waehrend "laeuft": alle 3 s nachsehen
const WARTE_MAX_MS = 160000;   // Websuche + KI: bis zu gut zwei Minuten

/**
 * KI-Auswertung in der Fahrer-App (Wunsch Ahmad 25.09.2026, abends): Nach dem
 * Abschicken sieht der Fahrer dieselbe Auswertung wie der Chef — Nachlass je
 * Abweichung und gesamt, vier Geldwerte, Datenlage. Nur lesend: kein
 * "Preis übernehmen" (das Protokoll ist ab "zur Freigabe" gesperrt), keine
 * Kosten- oder Budgetangaben (die gehoeren dem Chef). Die Bewertung selbst
 * startet der Server beim Abschicken; hier wird nur nachgesehen.
 */
export default function KiFahrerKarte({ apptId, preisVertrag, preisVorschlag }) {
  const [offen, setOffen] = useState(false);
  const [daten, setDaten] = useState(null);
  const [fehler, setFehler] = useState("");
  const [laedt, setLaedt] = useState(false);
  const start = useRef(Date.now());
  const timer = useRef(null);

  const laden = useCallback(async () => {
    setLaedt(true);
    try {
      const r = await driverApi.get(`/driver/appointments/${apptId}/ki-bewertung`);
      setDaten(r.data);
      setFehler("");
    } catch (e) {
      setFehler(errMsg(e, "KI-Auswertung konnte nicht geladen werden"));
    } finally { setLaedt(false); }
  }, [apptId]);

  const oeffnen = () => { setOffen(true); start.current = Date.now(); laden(); };

  useEffect(() => {
    if (!offen || !kiWartet(daten?.status)) return undefined;
    if (Date.now() - start.current > WARTE_MAX_MS) return undefined;
    timer.current = setTimeout(laden, TAKT_MS);
    return () => clearTimeout(timer.current);
  }, [offen, daten, laden]);

  const rahmen = { borderColor: "var(--border-default)", background: "var(--wa-03)" };

  if (!offen) {
    return (
      <div className="mt-3">
        <button type="button" onClick={oeffnen} data-testid="protokoll-ki-oeffnen"
                className="inline-flex items-center gap-1.5 rounded-lg px-3 tipp-h text-xs border font-semibold"
                style={rahmen}>
          <Sparkles size={13} style={{ color: "var(--accent-red)" }} /> KI-Auswertung anzeigen
        </button>
        <div className="mt-1 text-[11px] text-zinc-500">
          Nachlass je Abweichung und gesamt — dieselbe Einschätzung, die der Händler sieht.
        </div>
      </div>
    );
  }

  const status = daten?.status || "laeuft";
  const erg = daten?.ergebnis || null;
  const wartetZuLange = kiWartet(status) && Date.now() - start.current > WARTE_MAX_MS;
  const kaufpreis = Number(daten?.kaufpreis ?? preisVertrag ?? 0);
  const vorschlag = daten?.preis_vorschlag ?? preisVorschlag ?? null;
  const kiPreis = erg?.combined?.recommended_purchase_price_eur;

  return (
    <div className="mt-3 rounded-xl border px-3 py-3" style={rahmen}
         data-testid="protokoll-ki-karte" data-status={status}>
      <div className="flex flex-wrap items-center justify-between gap-2">
        <div className="text-[12px] font-semibold inline-flex items-center gap-1.5">
          <Sparkles size={13} style={{ color: "var(--accent-red)" }} /> KI-Auswertung
          <span className="font-normal text-zinc-500">· beratend</span>
        </div>
        <button type="button" onClick={laden} disabled={laedt} data-testid="protokoll-ki-aktualisieren"
                className="inline-flex items-center gap-1 text-[11px] min-h-[36px] px-2 rounded-lg hover:bg-white/10 disabled:opacity-50 text-zinc-400">
          <RefreshCw size={11} className={laedt || kiWartet(status) ? "animate-spin" : ""} /> Aktualisieren
        </button>
      </div>

      {!erg ? (
        <div className="mt-2 text-[12px] text-zinc-400" data-testid="protokoll-ki-status">
          {wartetZuLange ? "KI-Auswertung momentan nicht verfügbar — später noch einmal „Aktualisieren“ tippen."
            : (fehler || (status === "keine" && daten?.grund) || kiStatusText(status))}
          {kiWartet(status) && !wartetZuLange ? " Mit Marktrecherche dauert das bis zu zwei Minuten." : ""}
        </div>
      ) : (
        <>
          {status === "veraltet" && (
            <div className="mt-1 text-[11px]" style={{ color: "var(--st-amber)" }} data-testid="protokoll-ki-veraltet">
              {kiStatusText("veraltet")}
            </div>
          )}
          <div className="mt-2 grid grid-cols-3 gap-2 text-[11px]">
            {[["Vertrag", kaufpreis], ["Dein Vorschlag", vorschlag], ["KI-Zielpreis (fair)", kiPreis]].map(([k, v]) => (
              <div key={k} className="rounded-lg p-2" style={{ background: "var(--wa-06)" }}>
                <div className="text-zinc-500">{k}</div>
                <div className="text-sm font-semibold">{v != null ? eur(v) : "—"}</div>
              </div>
            ))}
          </div>
          <KiErgebnis erg={erg} kaufpreis={kaufpreis} basisText="Vertragspreis" testPrefix="protokoll-ki" />
        </>
      )}
    </div>
  );
}
