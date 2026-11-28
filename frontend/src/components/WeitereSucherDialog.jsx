import { useState } from "react";
import { toast } from "sonner";
import { Minus, Plus, Send, UserPlus, X } from "lucide-react";
import { api, errMsg } from "@/lib/api";
import { MODAL_ATTRIBUTE, useModal } from "@/lib/useModal";

// Wunsch Ahmad 03.10.2026: "Weitere Sucher anfragen" war nur ein mailto-Link an eine Adresse, die
// niemand las. Jetzt geht die Anfrage an den Betreiber: sie steht in seinem Admin-Bereich
// (Freischaltungen) und kommt per Mail an die Betriebsadresse. Eine offene Anfrage je Firma —
// eine neue Eingabe ändert sie.

const MIN = 1;
const MAX = 50;

function euro(betrag) {
  return `${Math.round(Number(betrag) || 0).toLocaleString("de-DE")} €`;
}

export default function WeitereSucherDialog({ offen, anfrage, preise, onClose, onGesendet }) {
  const [anzahl, setAnzahl] = useState(() => Number(anfrage?.sucher_anzahl) || 1);
  const [plan, setPlan] = useState(() => anfrage?.wanted_plan || "monthly");
  const [nachricht, setNachricht] = useState(() => anfrage?.message || "");
  const [sendet, setSendet] = useState(false);
  const dialogRef = useModal(() => { if (!sendet) onClose?.(); }, { offen: Boolean(offen) });

  if (!offen) return null;
  const monat = preise?.monthly?.price ?? 150;
  const jahr = preise?.yearly?.price ?? 1500;
  const n = Math.min(MAX, Math.max(MIN, Number(anzahl) || MIN));
  const summe = n * (plan === "yearly" ? jahr : monat);

  async function senden() {
    if (sendet) return;
    setSendet(true);
    try {
      const { data } = await api.post("/dealer/sucher-zugaenge-anfrage",
        { anzahl: n, plan, message: nachricht.trim() });
      (data?.bereits_offen ? toast.info : toast.success)(data?.hinweis || "Anfrage gesendet");
      onGesendet?.(data?.anfrage || null);
      onClose?.();
    } catch (e) {
      toast.error(errMsg(e, "Die Anfrage konnte nicht gesendet werden"));
    } finally {
      setSendet(false);
    }
  }

  const planKnopf = (key, titel, preis, zusatz) => {
    const an = plan === key;
    return (
      <button type="button" onClick={() => setPlan(key)} aria-pressed={an}
              data-testid={`weitere-sucher-plan-${key}`}
              className="flex-1 text-left rounded-xl px-4 py-3 transition-colors"
              style={{ background: "var(--wa-03)",
                       border: `2px solid ${an ? "var(--accent-red)" : "var(--border-default)"}` }}>
        <div className="font-semibold" style={{ color: "var(--text-primary)" }}>{titel}</div>
        <div className="text-xs mt-0.5" style={{ color: "var(--text-secondary)" }}>
          {euro(preis)} {zusatz} je Sucher
        </div>
      </button>
    );
  };

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center p-4 bg-black/70 backdrop-blur-sm"
         data-testid="weitere-sucher-dialog">
      <div ref={dialogRef} {...MODAL_ATTRIBUTE} aria-labelledby="weitere-sucher-titel"
           className="w-full max-w-lg rounded-2xl overflow-hidden flex flex-col"
           style={{ background: "var(--bg-elevated)", border: "1px solid var(--border-default)",
                    maxHeight: "90vh" }}>
        <div className="flex items-center justify-between px-5 py-4"
             style={{ borderBottom: "1px solid var(--border-default)" }}>
          <div className="flex items-center gap-2 font-semibold" id="weitere-sucher-titel"
               style={{ color: "var(--text-primary)" }}>
            <UserPlus size={18} /> {anfrage ? "Anfrage ändern" : "Weitere Sucher anfragen"}
          </div>
          <button type="button" onClick={() => onClose?.()} disabled={sendet}
                  className="w-11 h-11 -mr-1 rounded-full flex items-center justify-center hover:bg-white/10"
                  style={{ color: "var(--text-secondary)" }} title="Schließen" aria-label="Schließen">
            <X size={18} />
          </button>
        </div>

        <div className="px-5 py-4 overflow-y-auto flex flex-col gap-5">
          <div>
            <label htmlFor="weitere-sucher-anzahl" className="text-sm font-semibold"
                   style={{ color: "var(--text-primary)" }}>
              Wie viele zusätzliche Sucher-Zugänge?
            </label>
            <div className="mt-2 flex items-center gap-2">
              <button type="button" onClick={() => setAnzahl(Math.max(MIN, n - 1))} disabled={n <= MIN}
                      aria-label="Einen weniger" data-testid="weitere-sucher-minus"
                      className="w-11 h-11 rounded-xl flex items-center justify-center disabled:opacity-40"
                      style={{ background: "var(--apple-btn-secondary-bg)", color: "var(--text-primary)" }}>
                <Minus size={16} />
              </button>
              <input id="weitere-sucher-anzahl" type="number" inputMode="numeric" min={MIN} max={MAX}
                     value={anzahl} data-autofocus data-testid="weitere-sucher-anzahl"
                     onChange={(e) => setAnzahl(e.target.value)}
                     onBlur={() => setAnzahl(n)}
                     className="w-20 h-11 text-center rounded-xl text-lg font-semibold"
                     style={{ background: "var(--bg-input)", border: "1px solid var(--border-default)",
                              color: "var(--text-primary)" }} />
              <button type="button" onClick={() => setAnzahl(Math.min(MAX, n + 1))} disabled={n >= MAX}
                      aria-label="Einen mehr" data-testid="weitere-sucher-plus"
                      className="w-11 h-11 rounded-xl flex items-center justify-center disabled:opacity-40"
                      style={{ background: "var(--apple-btn-secondary-bg)", color: "var(--text-primary)" }}>
                <Plus size={16} />
              </button>
            </div>
          </div>

          <div>
            <div className="text-sm font-semibold mb-2" style={{ color: "var(--text-primary)" }}>Abo</div>
            <div className="flex gap-2">
              {planKnopf("monthly", "Monatlich", monat, "im Monat")}
              {planKnopf("yearly", "Jährlich", jahr, "im Jahr")}
            </div>
            <div className="text-xs mt-2" style={{ color: "var(--text-secondary)" }} data-testid="weitere-sucher-summe">
              Zusammen: {n} × {euro(plan === "yearly" ? jahr : monat)} = <b>{euro(summe)}</b>
              {plan === "yearly" ? " im Jahr" : " im Monat"} — Freischaltung nach Rechnungszahlung.
            </div>
          </div>

          <div>
            <label htmlFor="weitere-sucher-nachricht" className="text-sm font-semibold"
                   style={{ color: "var(--text-primary)" }}>
              Nachricht an uns <span className="font-normal" style={{ color: "var(--text-muted)" }}>(freiwillig)</span>
            </label>
            <textarea id="weitere-sucher-nachricht" rows={3} maxLength={2000} value={nachricht}
                      onChange={(e) => setNachricht(e.target.value)} data-testid="weitere-sucher-nachricht"
                      placeholder="z. B. Namen und E-Mail-Adressen der neuen Sucher"
                      className="mt-2 w-full rounded-xl px-3 py-2 text-sm"
                      style={{ background: "var(--bg-input)", border: "1px solid var(--border-default)",
                               color: "var(--text-primary)" }} />
          </div>
        </div>

        <div className="px-5 py-4 flex justify-end gap-2" style={{ borderTop: "1px solid var(--border-default)" }}>
          <button type="button" onClick={() => onClose?.()} disabled={sendet}
                  className="apple-btn apple-btn-secondary">Abbrechen</button>
          <button type="button" onClick={senden} disabled={sendet} data-testid="weitere-sucher-senden"
                  className="apple-btn apple-btn-primary disabled:opacity-60">
            <Send size={14} /> {sendet ? "Sendet…" : anfrage ? "Anfrage aktualisieren" : "Anfrage senden"}
          </button>
        </div>
      </div>
    </div>
  );
}
