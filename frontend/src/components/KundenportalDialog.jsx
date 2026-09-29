import { useCallback, useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { Copy, ExternalLink, FileSignature, RefreshCw, X } from "lucide-react";
import { toast } from "sonner";
import { api, errMsg, openAuthedFile } from "@/lib/api";

/**
 * Kundenportal (29.09.2026): Der Sucher/Chef gibt einen Kaufvertrag zur digitalen Unterschrift
 * frei. Das System erzeugt einen 6-stelligen Code (gilt nur für diese Vertragsfassung, läuft ab);
 * der Kunde gibt ihn auf der Firmenseite ein, sieht den Vertrag und unterschreibt. Danach steht
 * hier "digital unterschrieben" und der unterschriebene Vertrag lässt sich öffnen.
 */
const STATUS_TEXT = {
  keiner: "Noch kein Code erzeugt.",
  offen: "Code ist aktiv — der Kunde kann ihn jetzt auf der Firmenseite eingeben.",
  unterschrieben: "Der Kunde hat den Vertrag digital unterschrieben.",
  abgelaufen: "Der Code ist abgelaufen — bitte einen neuen erzeugen.",
  fassung_veraltet: "Der Vertrag wurde seitdem neu erstellt — der alte Code gilt nicht mehr, bitte einen neuen erzeugen.",
  zurueckgezogen: "Der Code wurde zurückgezogen.",
};

function datum(iso) {
  if (!iso) return "";
  const d = new Date(iso);
  return Number.isNaN(d.getTime()) ? String(iso) : d.toLocaleString("de-DE", { day: "2-digit", month: "2-digit", year: "numeric", hour: "2-digit", minute: "2-digit" });
}

export async function inZwischenablage(text) {
  try {
    await navigator.clipboard.writeText(text);
    return true;
  } catch {
    return false;
  }
}

export default function KundenportalDialog({ open, contract, onClose }) {
  const [stand, setStand] = useState(null);
  const [fehler, setFehler] = useState("");
  const [busy, setBusy] = useState(false);
  const laden = useCallback(async () => {
    if (!contract?.id) return;
    try {
      const { data } = await api.get(`/contracts/${contract.id}/portal`);
      setStand(data);
      setFehler("");
    } catch (e) {
      setFehler(errMsg(e, "Stand konnte nicht geladen werden"));
    }
  }, [contract?.id]);
  useEffect(() => { if (open) laden(); }, [open, laden]);

  const erzeugen = async () => {
    setBusy(true);
    try {
      const { data } = await api.post(`/contracts/${contract.id}/portal`);
      setStand(data);
      setFehler("");
      toast.success("Code erzeugt — bitte dem Kunden geben");
    } catch (e) {
      setFehler(errMsg(e, "Code konnte nicht erzeugt werden"));
    } finally { setBusy(false); }
  };
  const zurueckziehen = async () => {
    if (!window.confirm("Code zurückziehen? Der Kunde kommt damit nicht mehr in den Vertrag.")) return;
    setBusy(true);
    try {
      const { data } = await api.delete(`/contracts/${contract.id}/portal`);
      setStand(data);
      toast.success("Code zurückgezogen");
    } catch (e) {
      setFehler(errMsg(e, "Konnte nicht zurückziehen"));
    } finally { setBusy(false); }
  };
  const kopieren = async (text, was) => {
    const ok = await inZwischenablage(text);
    if (ok) toast.success(`${was} kopiert`);
    else toast.error(`${was} konnte nicht kopiert werden — bitte markieren und von Hand kopieren`);
  };

  if (!open) return null;
  const s = stand || {};
  const status = s.status || "keiner";
  const nachricht = s.url
    ? `Hallo ${contract?.seller_name || ""}, Ihren Kaufvertrag ${contract?.contract_no || ""} können Sie hier ansehen und digital unterschreiben: ${s.url} — Ihr Code: ${s.code || "______"} (gültig bis ${datum(s.laeuft_ab)}).`
    : "";
  const ohneSeite = /Firmenseite einrichten/i.test(fehler);

  return (
    <div className="fixed inset-0 z-50 flex items-end sm:items-center justify-center p-3" role="dialog" aria-modal="true"
         style={{ background: "rgba(0,0,0,0.55)" }} data-testid="kundenportal-dialog">
      <div className="w-full max-w-lg rounded-2xl p-5 space-y-4" style={{ background: "var(--bg-elevated, #1c1c1e)", color: "var(--text-primary)" }}>
        <div className="flex items-start justify-between gap-3">
          <div>
            <div className="font-display font-bold text-lg flex items-center gap-2"><FileSignature size={18} /> Kundenportal — digital unterschreiben</div>
            <div className="text-[12.5px] text-zinc-500 mt-0.5">
              {contract?.make} {contract?.model} · {contract?.contract_no} · Fassung {contract?.version || 1}
            </div>
          </div>
          <button onClick={onClose} aria-label="Schließen" className="w-8 h-8 rounded-full flex items-center justify-center hover:bg-white/10"><X size={16} /></button>
        </div>

        {fehler && (
          <div className="text-sm rounded-xl px-3 py-2" style={{ background: "rgba(255,69,58,0.12)", color: "var(--tx-rot, #ff6b6b)" }} data-testid="kundenportal-fehler">
            {fehler}
            {ohneSeite && (
              <div className="mt-1"><Link to="/app/einstellungen" className="underline" onClick={onClose}>Zu den Einstellungen → Firmenseite &amp; Kundenportal</Link></div>
            )}
          </div>
        )}

        <div className="text-sm" data-testid="kundenportal-status">{STATUS_TEXT[status] || STATUS_TEXT.keiner}</div>

        {status === "offen" && (
          <div className="rounded-2xl p-4 text-center" style={{ background: "var(--wa-05)", border: "1px solid var(--divider)" }}>
            <div className="text-[11px] uppercase tracking-wider text-zinc-500">Code für den Kunden</div>
            <div className="font-display font-black text-4xl tracking-[0.35em] mt-1 select-all" data-testid="kundenportal-code">{s.code}</div>
            <div className="text-[12px] text-zinc-500 mt-1">gültig bis {datum(s.laeuft_ab)} · nur für diese Vertragsfassung</div>
            <div className="text-[12.5px] mt-2 break-all">Firmenseite: <a href={s.url} target="_blank" rel="noreferrer" className="underline" data-testid="kundenportal-url">{s.url}</a></div>
            <div className="flex flex-wrap justify-center gap-2 mt-3">
              <button onClick={() => kopieren(s.code, "Code")} className="apple-btn apple-btn-secondary !rounded-full !px-3 !py-1.5 text-xs inline-flex items-center gap-1" data-testid="kundenportal-code-kopieren"><Copy size={13} /> Code kopieren</button>
              <button onClick={() => kopieren(s.url, "Adresse")} className="apple-btn apple-btn-secondary !rounded-full !px-3 !py-1.5 text-xs inline-flex items-center gap-1"><Copy size={13} /> Adresse kopieren</button>
              <button onClick={() => kopieren(nachricht, "Nachricht")} className="apple-btn apple-btn-secondary !rounded-full !px-3 !py-1.5 text-xs inline-flex items-center gap-1" data-testid="kundenportal-nachricht-kopieren"><Copy size={13} /> Nachricht für WhatsApp/SMS</button>
            </div>
          </div>
        )}

        {status === "unterschrieben" && (
          <div className="rounded-2xl p-4" style={{ background: "rgba(48,209,88,0.10)", border: "1px solid rgba(48,209,88,0.35)" }} data-testid="kundenportal-unterschrieben">
            <div className="font-semibold">✓ Unterschrieben am {datum(s.unterschrieben_am)}{s.name ? ` von ${s.name}` : ""}</div>
            <div className="text-[12.5px] text-zinc-500 mt-1">Das PDF enthält die Unterschrift des Kunden{s.pdf_signiert ? " und – falls hinterlegt – die des Chefs" : ""}.</div>
            <button onClick={() => openAuthedFile(`/contracts/${contract.id}/portal/pdf`)}
                    className="apple-btn apple-btn-secondary !rounded-full !px-4 !py-2 text-sm inline-flex items-center gap-2 mt-3"
                    data-testid="kundenportal-pdf">
              <ExternalLink size={14} /> Unterschriebenen Vertrag öffnen
            </button>
          </div>
        )}

        <div className="flex flex-wrap gap-2 justify-end pt-1">
          {status === "offen" && (
            <button onClick={zurueckziehen} disabled={busy} className="apple-btn apple-btn-secondary !rounded-full !px-4 !py-2 text-sm" data-testid="kundenportal-zurueckziehen">Code zurückziehen</button>
          )}
          {status !== "unterschrieben" && (
            <button onClick={erzeugen} disabled={busy} className="apple-btn !rounded-full !px-4 !py-2 text-sm inline-flex items-center gap-2" data-testid="kundenportal-erzeugen">
              <RefreshCw size={14} /> {status === "offen" ? "Code anzeigen / erneuern" : "Code erzeugen"}
            </button>
          )}
        </div>
        <div className="text-[11.5px] text-zinc-500 leading-relaxed">
          Der Kunde öffnet die Firmenseite, gibt den Code ein, liest den Vertrag und unterschreibt mit Finger oder Maus.
          Danach bekommt ihr eine Meldung in der App. Der Code gilt {s.code_tage || 7} Tage und nur für diese Fassung — nach einer
          Korrektur des Vertrags bitte einen neuen Code erzeugen.
        </div>
      </div>
    </div>
  );
}
