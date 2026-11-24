import { useCallback, useEffect, useState } from "react";
import { CheckCircle2, Landmark } from "lucide-react";
import { toast } from "sonner";
import { driverApi } from "@/context/DriverContext";
import { errMsg } from "@/lib/api";
import { ibanGruppiert, ibanHinweis, ibanNormal } from "@/lib/iban";
import { datumZeit } from "@/lib/datumZeit";

// Solange nicht überwiesen ist, schaut die App alle 30 s nach (nur sichtbar).
export const UEBERWEISUNG_NACHLADEN_MS = 30000;


/**
 * Wunsch Ahmad 24.11.2026: Der Fahrer trägt vor Ort IBAN und Kontoinhaber des
 * Verkäufers ein (nur wenn laut Vertrag per Überweisung bezahlt wird). Das Büro
 * überweist dann aus dem Abholauftrag. Sobald das Büro „überwiesen“ markiert,
 * steht hier „Überwiesen am …, Betrag …“ — das kann der Fahrer dem Verkäufer zeigen.
 *
 * `start`: der Stand aus der Protokoll-Antwort (GET …/protocol, Feld "ueberweisung").
 * Ohne Startwert (ältere Server) zeigt die Karte nichts und fragt nichts ab.
 * Danach fragt sie selbst nach, solange noch nicht überwiesen ist.
 */
export default function UeberweisungFahrer({ terminId, start = null }) {
  const [daten, setDaten] = useState(start);
  useEffect(() => { if (start) setDaten(start); }, [start]);
  const [fehler, setFehler] = useState("");
  const [iban, setIban] = useState("");
  const [inhaber, setInhaber] = useState("");
  const [bearbeiten, setBearbeiten] = useState(false);
  const [speichert, setSpeichert] = useState(false);

  const laden = useCallback(async () => {
    try {
      const { data } = await driverApi.get(`/driver/appointments/${terminId}/ueberweisung`);
      setDaten(data);
      setFehler("");
    } catch (e) {
      setFehler(errMsg(e, "Überweisung konnte nicht geladen werden"));
    }
  }, [terminId]);
  const offen = !!daten && !daten.ueberwiesen;
  useEffect(() => {
    if (!offen) return undefined;
    const t = setInterval(() => {
      if (document.visibilityState === "visible") laden();
    }, UEBERWEISUNG_NACHLADEN_MS);
    return () => clearInterval(t);
  }, [offen, laden]);

  if (!daten) {
    return fehler ? <div className="text-[12px] text-zinc-500" data-testid="ueberweisung-fahrer-fehler">{fehler}</div> : null;
  }
  if (!daten.zahlungsart?.ueberweisung) return null;

  const ue = daten.ueberwiesen;
  const hatKonto = !!daten.iban_formatiert;
  const zeigeFormular = !ue && (!hatKonto || bearbeiten);
  const hinweis = ibanHinweis(iban);

  const speichern = async () => {
    if (!ibanNormal(iban) || !inhaber.trim()) {
      toast.error("Bitte IBAN und Kontoinhaber eintragen.");
      return;
    }
    setSpeichert(true);
    try {
      const { data } = await driverApi.put(`/driver/appointments/${terminId}/ueberweisung`,
        { iban: ibanNormal(iban), kontoinhaber: inhaber.trim() });
      setDaten(data);
      setBearbeiten(false);
      toast.success("Kontodaten gespeichert — das Büro kann jetzt überweisen.");
    } catch (e) {
      toast.error(errMsg(e, "Kontodaten konnten nicht gespeichert werden"));
      if (e?.response?.status === 409) laden();
    } finally {
      setSpeichert(false);
    }
  };

  const st = { borderColor: "var(--border-default)" };
  const inputCls = "w-full rounded-lg border bg-transparent px-3 py-2 text-sm outline-none";

  return (
    <div className="rounded-xl border p-3 mb-3" style={st} data-testid="ueberweisung-fahrer">
      <div className="flex items-center gap-2 mb-2 text-[12px] font-semibold">
        <Landmark size={14} /> Bezahlung per Überweisung
        {daten.zahlungsart?.echtzeit && <span className="text-[11px] text-zinc-500 font-normal">(Echtzeit)</span>}
      </div>

      {ue && (
        <div className="rounded-lg px-3 py-3 mb-2" data-testid="ueberweisung-fahrer-ueberwiesen"
             style={{ background: "color-mix(in srgb, var(--st-gruen) 14%, transparent)", color: "var(--tx-gruen)" }}>
          <div className="flex items-center gap-2 font-semibold text-[15px]">
            <CheckCircle2 size={18} /> Überwiesen
          </div>
          <div className="text-[14px] mt-1">am {datumZeit(ue.am)}</div>
          <div className="text-[18px] font-bold mt-1">{ue.betrag_text}</div>
          <div className="text-[11px] mt-1 opacity-80">Diese Anzeige kannst du dem Verkäufer zeigen.</div>
        </div>
      )}

      {hatKonto && !zeigeFormular && (
        <div className="text-[13px] space-y-1" data-testid="ueberweisung-fahrer-konto">
          <div><span className="text-zinc-500">Kontoinhaber:</span> {daten.kontoinhaber}</div>
          <div className="font-mono tracking-wide"><span className="text-zinc-500 font-sans">IBAN:</span> {daten.iban_formatiert}</div>
          {!ue && (
            <>
              <div className="text-[11px] text-zinc-500">Gespeichert {datumZeit(daten.erfasst_am)} — das Büro überweist von hier aus.</div>
              <button type="button" className="text-[12px] underline mt-1" data-testid="ueberweisung-fahrer-aendern"
                      onClick={() => { setIban(daten.iban_formatiert); setInhaber(daten.kontoinhaber); setBearbeiten(true); }}>
                Kontodaten ändern
              </button>
            </>
          )}
        </div>
      )}

      {zeigeFormular && (
        <div className="space-y-2">
          <div>
            <label className="text-[11px] text-zinc-500" htmlFor="ue-inhaber">Kontoinhaber (wie auf der Bankkarte)</label>
            <input id="ue-inhaber" value={inhaber} onChange={(e) => setInhaber(e.target.value)}
                   maxLength={70} autoComplete="off" data-testid="ueberweisung-fahrer-inhaber"
                   className={inputCls} style={st} placeholder="z. B. Max Mustermann" />
          </div>
          <div>
            <label className="text-[11px] text-zinc-500" htmlFor="ue-iban">IBAN</label>
            <input id="ue-iban" value={ibanGruppiert(iban)} autoComplete="off" autoCapitalize="characters"
                   onChange={(e) => setIban(ibanNormal(e.target.value).slice(0, 34))}
                   spellCheck={false} data-testid="ueberweisung-fahrer-iban"
                   className={`${inputCls} font-mono tracking-wide`} style={st} placeholder="DE00 0000 0000 0000 0000 00" />
            {hinweis && (
              <div className="mt-1 text-[11px]" style={{ color: "var(--st-rot)" }} data-testid="ueberweisung-fahrer-hinweis">
                {hinweis}
              </div>
            )}
          </div>
          <div className="flex gap-2">
            <button type="button" onClick={speichern} disabled={speichert}
                    data-testid="ueberweisung-fahrer-speichern"
                    className="apple-btn apple-btn-primary !py-2 !text-[12px] disabled:opacity-60">
              {speichert ? "Speichert…" : "Kontodaten speichern"}
            </button>
            {bearbeiten && (
              <button type="button" onClick={() => setBearbeiten(false)}
                      className="apple-btn apple-btn-ghost !py-2 !text-[12px]">Abbrechen</button>
            )}
          </div>
        </div>
      )}
    </div>
  );
}
