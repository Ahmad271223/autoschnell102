import { useEffect, useRef, useState } from "react";
import { createPortal } from "react-dom";
import { useNavigate } from "react-router-dom";
import { Sparkles, X } from "lucide-react";
import { api } from "@/lib/api";
import { appLink, datumText, gelesenGemeldet } from "@/lib/neuigkeiten";

/**
 * Hinweisfenster „Was ist neu“ beim Start (Wunsch Ahmad 24.11.2026): höchstens EIN Hinweis pro
 * Tag — der neueste ungelesene „wichtig“-Eintrag, sonst der neueste ungelesene. Ob heute schon
 * einer kam, entscheidet der Server (je Konto, Europe/Berlin), damit Handy und PC zusammen nur
 * einen zeigen. „Später“ schließt nur; „Gelesen“ merkt den Eintrag als gelesen.
 *
 * Fahrer: basis="/driver/neuigkeiten" mit dem Fahrer-Client — nur Einträge „fuer: fahrer“.
 */
const GEFRAGT = new Set();   // je App-Start und Konto nur einmal fragen (Seitenwechsel bauen das Layout neu)

export default function NeuigkeitenHinweis({ aktiv, kontoId, client = api, basis = "/neuigkeiten", alleLink = "/app/neuigkeiten" }) {
  const [eintrag, setEintrag] = useState(null);
  const nav = useNavigate();
  const knopf = useRef(null);

  useEffect(() => {
    if (!aktiv || !kontoId) return undefined;
    const schluessel = `${basis}:${kontoId}`;
    if (GEFRAGT.has(schluessel)) return undefined;
    GEFRAGT.add(schluessel);
    let laeuft = true;
    client.post(`${basis}/hinweis`)
      .then(({ data }) => { if (laeuft && data?.hinweis?.id) setEintrag(data.hinweis); })
      .catch(() => { GEFRAGT.delete(schluessel); });   // Netzfehler: beim nächsten Seitenaufbau erneut
    return () => { laeuft = false; };
  }, [aktiv, kontoId, client, basis]);

  useEffect(() => {
    if (!eintrag) return undefined;
    knopf.current?.focus();
    const taste = (e) => { if (e.key === "Escape") setEintrag(null); };
    document.addEventListener("keydown", taste);
    return () => document.removeEventListener("keydown", taste);
  }, [eintrag]);

  if (!eintrag) return null;
  const link = appLink(eintrag.link);
  const gelesen = async (weiter) => {
    const e = eintrag;
    setEintrag(null);
    try {
      const { data } = await client.post(`${basis}/${encodeURIComponent(e.id)}/gelesen`);
      gelesenGemeldet(data);
    } catch { /* bleibt ungelesen — kommt an einem anderen Tag wieder */ }
    if (weiter) nav(weiter);
  };

  return createPortal(
    <div className="fixed inset-0 z-[70] flex items-end sm:items-center justify-center p-4 bg-black/60"
         data-testid="neuigkeiten-hinweis" onClick={() => setEintrag(null)}>
      <div role="dialog" aria-modal="true" aria-labelledby="neuigkeiten-hinweis-titel"
           className="w-full max-w-md rounded-2xl p-5 modal-hoehe overflow-y-auto"
           style={{ background: "var(--bg-elevated)", border: "1px solid var(--wa-10)", color: "var(--text-primary)" }}
           onClick={(e) => e.stopPropagation()}>
        <div className="flex items-start gap-3">
          <span className="w-9 h-9 rounded-full flex items-center justify-center shrink-0"
                style={{ background: "color-mix(in srgb, var(--accent-red) 15%, transparent)", color: "var(--accent-red)" }}>
            <Sparkles size={17} />
          </span>
          <div className="min-w-0 flex-1">
            <div className="text-[11px] uppercase tracking-wider" style={{ color: "var(--text-dim)" }}>
              {eintrag.wichtig ? "Wichtig · " : ""}Was ist neu · {datumText(eintrag.datum)}
            </div>
            <h2 id="neuigkeiten-hinweis-titel" className="text-lg font-bold leading-tight mt-0.5">{eintrag.titel}</h2>
          </div>
          <button type="button" onClick={() => setEintrag(null)} aria-label="Später"
                  className="w-10 h-10 -mr-2 -mt-2 flex items-center justify-center" style={{ color: "var(--text-dim)" }}>
            <X size={20} />
          </button>
        </div>
        <p className="mt-3 text-[14px] leading-relaxed">{eintrag.text}</p>
        {eintrag.punkte?.length > 0 && (
          <ul className="mt-2 list-disc pl-5 text-[13px] space-y-0.5">
            {eintrag.punkte.map((p, i) => <li key={i}>{p}</li>)}
          </ul>
        )}
        <div className="mt-5 flex flex-wrap justify-end gap-2">
          <button type="button" onClick={() => setEintrag(null)} data-testid="neuigkeiten-hinweis-spaeter"
                  className="apple-btn apple-btn-secondary !rounded-full !px-4 min-h-[44px] text-sm">
            Später
          </button>
          {(link || alleLink) && (
            <button type="button" onClick={() => gelesen(link || alleLink)} data-testid="neuigkeiten-hinweis-ansehen"
                    className="apple-btn apple-btn-secondary !rounded-full !px-4 min-h-[44px] text-sm">
              Ansehen
            </button>
          )}
          <button type="button" ref={knopf} onClick={() => gelesen(null)} data-testid="neuigkeiten-hinweis-gelesen"
                  className="apple-btn apple-btn-primary !rounded-full !px-4 min-h-[44px] text-sm">
            Gelesen
          </button>
        </div>
      </div>
    </div>,
    document.body,
  );
}
