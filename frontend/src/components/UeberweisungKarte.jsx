import { useCallback, useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { AlertTriangle, CheckCircle2, Landmark, RotateCcw } from "lucide-react";
import { toast } from "sonner";
import { api, errMsg } from "@/lib/api";
import KopierKnopf from "@/components/KopierKnopf";
import { datumZeit } from "@/lib/datumZeit";

export const BETRAG_OFFEN = "offen – Preis vor Ort noch nicht bestätigt";

/** Kurzer Stand für Listen/Akte (eine Zeile). */
export function ueberweisungKurz(d) {
  if (!d || !d.zahlungsart?.ueberweisung) return "";
  if (d.ueberwiesen) return `Überwiesen am ${datumZeit(d.ueberwiesen.am)} · ${d.ueberwiesen.betrag_text}`;
  if (!d.kontodaten) return "Kontodaten fehlen noch (trägt der Fahrer vor Ort ein)";
  if (!d.betrag_fest) return `Kontodaten liegen vor · Betrag ${BETRAG_OFFEN}`;
  return `Jetzt überweisen: ${d.betrag_text}`;
}

function Zeile({ label, wert, kopieren, testid, mono = false }) {
  return (
    <div className="flex items-start justify-between gap-2 py-1.5 border-b last:border-b-0"
         style={{ borderColor: "var(--divider)" }}>
      <div className="min-w-0">
        <div className="text-[10.5px] uppercase tracking-wider" style={{ color: "var(--text-muted)" }}>{label}</div>
        <div className={`text-[13px] break-words ${mono ? "font-mono tracking-wide" : ""}`}
             style={{ color: "var(--text-primary)" }} data-testid={testid}>{wert || "—"}</div>
      </div>
      {kopieren && wert && <KopierKnopf text={kopieren} label={label} testid={`${testid}-kopieren`} />}
    </div>
  );
}

/**
 * Karte „Überweisung“ im Abholauftrag (Wunsch Ahmad 24.11.2026).
 * Alle Werte kommen vom Server — auch der Betrag. Das Frontend schickt beim
 * Markieren nur den angezeigten Stand zurück; hat sich inzwischen etwas
 * geändert, lehnt der Server ab (nie ein alter Preis).
 */
export default function UeberweisungKarte({ terminId, kompakt = false }) {
  const [d, setD] = useState(null);
  const [fehler, setFehler] = useState("");
  const [frage, setFrage] = useState(null);        // "markieren" | "zurueck"
  const [arbeitet, setArbeitet] = useState(false);

  const laden = useCallback(async () => {
    try {
      const { data } = await api.get(`/appointments/${terminId}/ueberweisung`);
      setD(data);
      setFehler("");
    } catch (e) {
      setFehler(errMsg(e, "Überweisung konnte nicht geladen werden"));
    }
  }, [terminId]);
  useEffect(() => { if (terminId) laden(); }, [terminId, laden]);

  if (!d) {
    return fehler && !kompakt
      ? <div className="text-[12px]" style={{ color: "var(--text-muted)" }} data-testid="ueberweisung-fehler">{fehler}</div>
      : null;
  }
  if (!d.zahlungsart?.ueberweisung) {
    return kompakt ? null : (
      <div className="text-[12px]" style={{ color: "var(--text-muted)" }} data-testid="ueberweisung-bar">
        Laut Kaufvertrag Barzahlung — keine Überweisung.
      </div>
    );
  }

  if (kompakt) {
    return (
      <div className="text-[12px] flex items-center gap-2 flex-wrap" data-testid="ueberweisung-kompakt">
        <Landmark size={13} />
        <span style={{ color: d.ueberwiesen ? "var(--tx-gruen)" : "var(--text-secondary)" }}>{ueberweisungKurz(d)}</span>
        <Link to={`/app/termine?termin=${encodeURIComponent(terminId)}`} className="underline">Zur Abholung</Link>
      </div>
    );
  }

  const ausfuehren = async () => {
    setArbeitet(true);
    try {
      const { data } = frage === "zurueck"
        ? await api.delete(`/appointments/${terminId}/ueberweisung/ueberwiesen`)
        : await api.post(`/appointments/${terminId}/ueberweisung/ueberwiesen`, { stand: d.stand });
      setD(data);
      toast.success(frage === "zurueck" ? "Markierung zurückgenommen" : "Als überwiesen markiert — der Fahrer sieht es in seiner App.");
    } catch (e) {
      toast.error(errMsg(e, "Das hat nicht geklappt"));
      laden();
    } finally {
      setArbeitet(false);
      setFrage(null);
    }
  };

  const ue = d.ueberwiesen;
  return (
    <div className="rounded-xl p-3" style={{ background: "var(--hover-bg)", border: "1px solid var(--divider)" }}
         data-testid="ueberweisung-karte">
      <div className="flex items-center gap-2 mb-2">
        <Landmark size={14} className="text-[var(--accent-red)]" />
        <div className="text-[11px] font-bold uppercase tracking-wider" style={{ color: "var(--text-primary)" }}>
          Überweisung{d.zahlungsart?.echtzeit ? " (Echtzeit)" : ""}
        </div>
      </div>

      {(d.warnungen || []).map((w) => (
        <div key={w.art} className="flex items-start gap-2 rounded-lg px-2.5 py-2 mb-2 text-[12px]"
             data-testid={`ueberweisung-warnung-${w.art}`}
             style={{ background: "color-mix(in srgb, var(--st-amber) 14%, transparent)", color: "var(--tx-amber)" }}>
          <AlertTriangle size={14} className="shrink-0 mt-0.5" /> <span>{w.text}</span>
        </div>
      ))}

      {!d.kontodaten ? (
        <div className="text-[12px]" style={{ color: "var(--text-secondary)" }} data-testid="ueberweisung-ohne-konto">
          Noch keine Kontodaten — der Fahrer trägt IBAN und Kontoinhaber vor Ort ein.
          {!d.betrag_fest && <> Betrag: {BETRAG_OFFEN}.</>}
          {d.betrag_fest && <> Betrag: {d.betrag_text}.</>}
        </div>
      ) : (
        <div className="flex flex-col sm:flex-row gap-3">
          <div className="flex-1 min-w-0">
            <Zeile label="Empfänger" wert={d.empfaenger} kopieren={d.empfaenger} testid="ueberweisung-empfaenger" />
            <Zeile label="IBAN" wert={d.iban_formatiert} kopieren={d.iban} testid="ueberweisung-iban" mono />
            <Zeile label="Betrag" wert={d.betrag_fest ? d.betrag_text : BETRAG_OFFEN}
                   kopieren={d.betrag_fest ? (d.betrag_cents / 100).toFixed(2).replace(".", ",") : ""}
                   testid="ueberweisung-betrag" />
            <Zeile label="Verwendungszweck" wert={d.verwendungszweck} kopieren={d.verwendungszweck}
                   testid="ueberweisung-zweck" />
          </div>
          {d.girocode_svg && (
            <div className="shrink-0 self-center text-center" data-testid="ueberweisung-girocode">
              {/* SVG kommt fertig vom eigenen Server (segno, nur Pfade) — Schwarz auf Weiß zum Scannen */}
              <div className="w-[164px] h-[164px] [&>svg]:w-full [&>svg]:h-full rounded-lg overflow-hidden"
                   dangerouslySetInnerHTML={{ __html: d.girocode_svg }} />
              <div className="text-[10.5px] mt-1" style={{ color: "var(--text-muted)" }}>
                {d.betrag_fest ? "Mit der Banking-App scannen" : "Ohne Betrag — Betrag noch offen"}
              </div>
            </div>
          )}
        </div>
      )}

      <div className="mt-3">
        {ue ? (
          <div className="flex flex-wrap items-center justify-between gap-2">
            <div className="flex items-center gap-2 text-[13px]" style={{ color: "var(--tx-gruen)" }}
                 data-testid="ueberweisung-ueberwiesen">
              <CheckCircle2 size={16} />
              Überwiesen am {datumZeit(ue.am)} · {ue.betrag_text}{ue.von_name ? ` · ${ue.von_name}` : ""}
            </div>
            {d.darf_zuruecknehmen && frage !== "zurueck" && (
              <button type="button" onClick={() => setFrage("zurueck")} data-testid="ueberweisung-zurueck"
                      className="apple-btn apple-btn-ghost !py-1.5 !text-[11px]">
                <RotateCcw size={12} /> Rückgängig
              </button>
            )}
          </div>
        ) : (
          frage !== "markieren" && (
            <button type="button" disabled={!d.kontodaten || !d.betrag_fest || arbeitet}
                    onClick={() => setFrage("markieren")} data-testid="ueberweisung-markieren"
                    className="apple-btn apple-btn-primary !py-2 !text-[12px] disabled:opacity-50">
              Als überwiesen markieren
            </button>
          )
        )}
        {frage && (
          <div className="mt-2 rounded-lg p-2.5 text-[12px]" data-testid="ueberweisung-frage"
               style={{ background: "var(--bg-elevated)", border: "1px solid var(--divider)" }}>
            <div className="mb-2" style={{ color: "var(--text-primary)" }}>
              {frage === "zurueck"
                ? "Markierung „überwiesen“ wirklich zurücknehmen? Der Fahrer sieht dann wieder „nicht überwiesen“."
                : `Wirklich ${d.betrag_text} an ${d.empfaenger} überwiesen?`}
            </div>
            <div className="flex gap-2">
              <button type="button" onClick={ausfuehren} disabled={arbeitet} data-testid="ueberweisung-frage-ja"
                      className="apple-btn apple-btn-primary !py-1.5 !text-[11px] disabled:opacity-60">
                {arbeitet ? "Speichert…" : frage === "zurueck" ? "Ja, zurücknehmen" : "Ja, überwiesen"}
              </button>
              <button type="button" onClick={() => setFrage(null)} disabled={arbeitet}
                      className="apple-btn apple-btn-ghost !py-1.5 !text-[11px]">Abbrechen</button>
            </div>
          </div>
        )}
      </div>
    </div>
  );
}
