import { useEffect, useRef, useState } from "react";
import { Link, useNavigate, useSearchParams } from "react-router-dom";
import { buyerApi } from "@/context/BuyerContext";
import { Bolt, Check, Loader2, TriangleAlert } from "lucide-react";

/**
 * Rücksprung nach dem Stripe-Checkout des Marktplatz-Zugangs (20 €/Monat).
 * Pollt den Zahlungsstatus, bis Stripe bestätigt hat, und schickt den
 * Käufer dann in den Marktplatz.
 */
export default function ZahlungErfolg() {
  const [sp] = useSearchParams();
  const nav = useNavigate();
  const sid = sp.get("session_id");
  const [state, setState] = useState("checking"); // checking|paid|timeout|error
  const tries = useRef(0);

  useEffect(() => {
    if (!sid) { setState("error"); return; }
    let cancelled = false;
    const poll = async () => {
      try {
        const { data } = await buyerApi.get(`/payments/status/${sid}`);
        if (cancelled) return;
        if (data.payment_status === "paid") {
          setState("paid");
          setTimeout(() => nav("/markt"), 1800);
          return;
        }
      } catch (_) { /* weiter pollen */ }
      tries.current += 1;
      if (tries.current >= 20) { setState("timeout"); return; }
      setTimeout(poll, 2000);
    };
    poll();
    return () => { cancelled = true; };
  }, [sid, nav]);

  return (
    <div className="min-h-screen flex items-center justify-center p-6">
      <div className="w-full max-w-md tactical-card p-8 text-center" data-testid="markt-zahlung-status">
        <div className="flex items-center gap-2 justify-center mb-6">
          <span className="w-7 h-7 rounded-sm flex items-center justify-center"
                style={{ background: "var(--accent-red)" }}>
            <Bolt size={16} />
          </span>
          <span className="font-display font-black text-lg">MARKTPLATZ</span>
        </div>

        {state === "checking" && (
          <>
            <Loader2 size={28} className="mx-auto animate-spin text-zinc-400" />
            <h1 className="font-display font-bold text-xl mt-4">Zahlung wird bestätigt…</h1>
            <p className="text-zinc-400 text-sm mt-2">
              Einen Moment — Stripe meldet die Zahlung gleich zurück.
            </p>
          </>
        )}
        {state === "paid" && (
          <>
            <div className="w-12 h-12 mx-auto rounded-full flex items-center justify-center"
                 style={{ background: "var(--accent-green)" }}>
              <Check size={22} />
            </div>
            <h1 className="font-display font-bold text-xl mt-4">Zugang freigeschaltet</h1>
            <p className="text-zinc-400 text-sm mt-2">
              30 Tage Marktplatz — du wirst weitergeleitet…
            </p>
          </>
        )}
        {(state === "timeout" || state === "error") && (
          <>
            <TriangleAlert size={28} className="mx-auto" style={{ color: "var(--accent-red)" }} />
            <h1 className="font-display font-bold text-xl mt-4">
              {state === "error" ? "Keine Zahlungs-Referenz gefunden" : "Noch keine Bestätigung"}
            </h1>
            <p className="text-zinc-400 text-sm mt-2">
              {state === "error"
                ? "Bitte starte die Zahlung erneut aus dem Marktplatz."
                : "Die Zahlung kann ein paar Minuten brauchen. Dein Zugang wird automatisch aktiv, sobald Stripe bestätigt — schau gleich im Marktplatz nach."}
            </p>
            <Link to="/markt" className="kinetic-button inline-block mt-6 px-5 py-2.5 rounded-sm text-sm">
              Zum Marktplatz
            </Link>
          </>
        )}
      </div>
    </div>
  );
}
