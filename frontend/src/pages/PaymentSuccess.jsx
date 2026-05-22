import { useEffect, useState, useRef } from "react";
import { useNavigate, useSearchParams } from "react-router-dom";
import { api } from "@/lib/api";
import { useAuth } from "@/context/AuthContext";
import { Check, X, Loader2 } from "lucide-react";

export default function PaymentSuccess() {
  const [params] = useSearchParams();
  const sid = params.get("session_id");
  const [state, setState] = useState({ status: "checking" });
  const nav = useNavigate();
  const { refresh } = useAuth();
  const tries = useRef(0);

  useEffect(() => {
    if (!sid) { setState({ status: "error" }); return; }
    let cancelled = false;
    const poll = async () => {
      try {
        // First refresh /auth/me — webhook may have already activated subscription
        const me = await refresh();
        if (cancelled) return;
        if (me?.subscription?.active) {
          setState({ status: "paid" });
          setTimeout(() => nav("/app/vergleich"), 1500);
          return;
        }
        // Then check our payments/status endpoint (also looks for matching subscription)
        const { data } = await api.get(`/payments/status/${sid}`);
        if (cancelled) return;
        if (data.payment_status === "paid") {
          await refresh();
          setState({ status: "paid" });
          setTimeout(() => nav("/app/vergleich"), 1500);
          return;
        }
        if (data.status === "expired") {
          setState({ status: "expired" });
          return;
        }
        tries.current += 1;
        if (tries.current >= 15) {
          setState({ status: "timeout" });
          return;
        }
        setTimeout(poll, 2000);
      } catch {
        tries.current += 1;
        if (tries.current >= 15) { setState({ status: "error" }); return; }
        setTimeout(poll, 2000);
      }
    };
    poll();
    return () => { cancelled = true; };
  }, [sid, nav, refresh]);

  return (
    <div className="min-h-screen flex items-center justify-center p-6">
      <div className="tactical-card p-10 max-w-md w-full text-center">
        {state.status === "checking" && (
          <>
            <Loader2 size={42} className="mx-auto animate-spin" style={{ color: "var(--accent-red)" }} />
            <h1 className="font-display font-black text-2xl mt-5">Zahlung wird geprüft …</h1>
            <p className="text-zinc-400 text-sm mt-2">Einen Moment bitte – wir warten auf Stripe-Bestätigung.</p>
          </>
        )}
        {state.status === "paid" && (
          <>
            <div className="w-14 h-14 mx-auto rounded-full flex items-center justify-center"
                 style={{ background: "rgba(52,199,89,0.12)" }}>
              <Check size={26} style={{ color: "var(--accent-green)" }} />
            </div>
            <h1 className="font-display font-black text-2xl mt-5">Abo aktiviert</h1>
            <p className="text-zinc-400 text-sm mt-2">Du wirst direkt weitergeleitet…</p>
          </>
        )}
        {state.status === "timeout" && (
          <>
            <Loader2 size={42} className="mx-auto" style={{ color: "var(--accent-red)" }} />
            <h1 className="font-display font-black text-2xl mt-5">Bestätigung dauert länger</h1>
            <p className="text-zinc-400 text-sm mt-2">
              Stripe braucht etwas länger als gewohnt. Sobald wir die Bestätigung erhalten, wird dein Abo automatisch freigeschaltet.
            </p>
            <div className="flex gap-2 mt-6">
              <button data-testid="retry-status" onClick={() => window.location.reload()}
                      className="kinetic-button flex-1 px-4 py-2 rounded-sm">
                Erneut prüfen
              </button>
              <button onClick={() => nav("/app/vergleich")}
                      className="flex-1 px-4 py-2 rounded-sm border" style={{ borderColor: "var(--border-default)" }}>
                Zur App
              </button>
            </div>
          </>
        )}
        {(state.status === "expired" || state.status === "error") && (
          <>
            <div className="w-14 h-14 mx-auto rounded-full flex items-center justify-center"
                 style={{ background: "rgba(255,59,48,0.12)" }}>
              <X size={26} style={{ color: "var(--accent-red)" }} />
            </div>
            <h1 className="font-display font-black text-2xl mt-5">
              {state.status === "expired" ? "Sitzung abgelaufen" : "Zahlungsstatus konnte nicht geprüft werden"}
            </h1>
            <button data-testid="back-to-paywall" onClick={() => nav("/abo")}
                    className="kinetic-button mt-6 px-5 py-2 rounded-sm">
              Zurück zur Abo-Seite
            </button>
          </>
        )}
      </div>
    </div>
  );
}
