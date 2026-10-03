import { useCallback, useEffect, useState } from "react";
import { useNavigate } from "react-router-dom";
import { Bell, CheckCheck, FileSignature } from "lucide-react";
import { toast } from "sonner";
import { api, errMsg } from "@/lib/api";

/**
 * Meldungen (Kundenportal, 29.09.2026): "Kaufvertrag bestätigt" — der Kunde hat über die
 * Firmenseite unterschrieben. Chef und der Sucher des Vertrags sehen es hier und im Menü
 * (Zähler, Hinweis). Gelesen wird je Konto.
 */
export default function Meldungen() {
  const nav = useNavigate();
  const [liste, setListe] = useState(null);
  const [fehler, setFehler] = useState("");
  const laden = useCallback(async () => {
    try {
      const { data } = await api.get("/meldungen", { params: { limit: 100 } });
      setListe(Array.isArray(data) ? data : []);
      setFehler("");
    } catch (e) {
      setFehler(errMsg(e, "Meldungen konnten nicht geladen werden"));
      setListe([]);
    }
  }, []);
  useEffect(() => { laden(); }, [laden]);

  const gelesen = async (m) => {
    if (m.gelesen) return;
    try {
      await api.post(`/meldungen/${m.id}/gelesen`);
      setListe((l) => (l || []).map((x) => (x.id === m.id ? { ...x, gelesen: true } : x)));
    } catch (e) { toast.error(errMsg(e, "Konnte nicht als gelesen markieren")); }
  };
  const alleGelesen = async () => {
    try {
      await api.post("/meldungen/alle-gelesen");
      setListe((l) => (l || []).map((x) => ({ ...x, gelesen: true })));
    } catch (e) { toast.error(errMsg(e, "Konnte nicht als gelesen markieren")); }
  };
  const ungelesen = (liste || []).filter((m) => !m.gelesen).length;

  return (
    <div className="max-w-3xl mx-auto">
      <div className="flex items-center justify-between gap-3 mb-5">
        <div>
          <h1 className="font-display font-black text-2xl tracking-tight">Meldungen</h1>
          <p className="text-sm text-zinc-500 mt-1">
            Bestätigungen aus dem Kundenportal — sobald ein Kunde einen Kaufvertrag auf eurer Firmenseite unterschrieben hat.
          </p>
        </div>
        {ungelesen > 0 && (
          <button onClick={alleGelesen} data-testid="meldungen-alle-gelesen"
                  className="apple-btn apple-btn-secondary !rounded-full !px-4 !py-2 text-sm inline-flex items-center gap-2">
            <CheckCheck size={15} /> Alle gelesen
          </button>
        )}
      </div>
      {fehler && <div className="text-sm text-red-400 mb-3" data-testid="meldungen-fehler">{fehler}</div>}
      {liste === null && <div className="text-sm text-zinc-500">Lade…</div>}
      {liste && liste.length === 0 && !fehler && (
        <div className="apple-surface p-8 text-center text-sm text-zinc-500" data-testid="meldungen-leer">
          <Bell size={22} className="mx-auto mb-2 text-zinc-600" />
          Noch keine Meldungen. Sobald ein Kunde über das Kundenportal unterschreibt, steht es hier.
        </div>
      )}
      <div className="space-y-2">
        {(liste || []).map((m) => (
          <div key={m.id} data-testid={`meldung-${m.id}`}
               className="apple-surface p-4 flex items-start gap-3"
               style={{ borderLeft: m.gelesen ? "3px solid transparent" : "3px solid var(--accent-red)" }}>
            <div className="w-9 h-9 rounded-full flex items-center justify-center shrink-0"
                 style={{ background: "rgba(48,209,88,0.14)", color: "var(--st-gruen, #30d158)" }}>
              <FileSignature size={17} />
            </div>
            <div className="min-w-0 flex-1">
              <div className="text-[14px] leading-snug">{m.text}</div>
              <div className="text-[12px] text-zinc-500 mt-1">
                {m.erstellt_am ? new Date(m.erstellt_am).toLocaleString("de-DE", { day: "2-digit", month: "2-digit", year: "numeric", hour: "2-digit", minute: "2-digit" }) : ""}
                {m.contract_no ? ` · ${m.contract_no}` : ""}
              </div>
            </div>
            <div className="flex items-center gap-2 shrink-0">
              {m.ref && (
                <button onClick={() => { gelesen(m); nav("/app/vertraege"); }}
                        className="text-[12px] font-semibold px-3 py-1.5 rounded-full"
                        style={{ background: "var(--apple-btn-secondary-bg)", color: "var(--text-primary)" }}
                        data-testid={`meldung-oeffnen-${m.id}`}>
                  Vertrag öffnen
                </button>
              )}
              {!m.gelesen && (
                <button onClick={() => gelesen(m)} title="Als gelesen markieren" aria-label="Als gelesen markieren"
                        className="w-8 h-8 rounded-full flex items-center justify-center hover:bg-white/10"
                        style={{ color: "var(--text-secondary)" }} data-testid={`meldung-gelesen-${m.id}`}>
                  <CheckCheck size={15} />
                </button>
              )}
            </div>
          </div>
        ))}
      </div>
    </div>
  );
}
