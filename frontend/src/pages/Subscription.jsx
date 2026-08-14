import { useEffect, useState } from "react";
import { useNavigate, useSearchParams } from "react-router-dom";
import { api, errMsg } from "@/lib/api";
import { useAuth } from "@/context/AuthContext";
import { toast } from "sonner";
import { Check, ArrowRight, Bolt, ShieldCheck } from "lucide-react";

export default function Subscription() {
  const nav = useNavigate();
  const { user, subscription, logout } = useAuth();
  const [params] = useSearchParams();
  const [busy, setBusy] = useState("");

  useEffect(() => {
    if (subscription?.active) {
      nav("/app/vergleich");
    }
  }, [subscription, nav]);

  const startCheckout = async (plan) => {
    setBusy(plan);
    try {
      const { data } = await api.post("/payments/checkout", {
        plan, origin_url: window.location.origin,
      });
      window.location.href = data.url;
    } catch (err) {
      toast.error(errMsg(err, "Checkout fehlgeschlagen"));
      setBusy("");
    }
  };

  return (
    <div className="min-h-screen p-6">
      <div className="max-w-5xl mx-auto">
        <div className="flex items-center justify-between mb-12">
          <div className="flex items-center gap-2">
            <span className="w-7 h-7 rounded-sm flex items-center justify-center"
                  style={{ background: "var(--accent-red)" }}>
              <Bolt size={16} />
            </span>
            <span className="font-display font-black text-lg">AUTOHANDEL<span style={{color:"var(--accent-red)"}}>.</span></span>
          </div>
          <div className="flex items-center gap-3 text-sm">
            <span className="text-zinc-400">{user?.email}</span>
            <button data-testid="logout-paywall" onClick={async () => { await logout(); nav("/"); }}
                    className="px-3 py-1 rounded-sm border hover:bg-white/5"
                    style={{ borderColor: "var(--border-default)" }}>
              Abmelden
            </button>
          </div>
        </div>

        <div className="text-center mb-10">
          <div className="overline mb-3">Sucher-Abo erforderlich</div>
          <h1 className="font-display font-black text-4xl lg:text-6xl tracking-tighter">
            Dein Sucher-Abo.
          </h1>
          <p className="text-zinc-400 mt-3 max-w-xl mx-auto">
            Gilt persönlich für deinen Account: Vergleich, Suche, Kaufverträge,
            Versand &amp; Terminplaner. Verkaufen &amp; Verwalten sind für den
            Händler-Hauptaccount kostenlos.
          </p>
        </div>

        <div className="grid md:grid-cols-2 gap-6 mt-10">
          <div className="tactical-card p-8" data-testid="plan-monthly">
            <div className="overline">Monatsabo</div>
            <div className="mt-3 flex items-baseline gap-2">
              <span className="font-display font-black text-5xl">160 €</span>
              <span className="text-zinc-400 text-sm">/ Monat</span>
            </div>
            <p className="text-zinc-400 text-sm mt-3">Monatlich kündbar.</p>
            <ul className="mt-6 space-y-2 text-sm">
              {["Alle Funktionen","WhatsApp & E-Mail Versand","Terminplaner & Fahrer","Live-Zähler","PDF-Archiv"].map(t => (
                <li key={t} className="flex items-center gap-2 text-zinc-300">
                  <Check size={14} style={{ color: "var(--accent-green)" }} /> {t}
                </li>
              ))}
            </ul>
            <button data-testid="checkout-monthly" disabled={busy} onClick={() => startCheckout("monthly")}
                    className="w-full mt-7 py-3 rounded-sm border hover:bg-white/5 disabled:opacity-50"
                    style={{ borderColor: "var(--border-default)" }}>
              {busy === "monthly" ? "Lade…" : "Monatlich starten"}
            </button>
          </div>

          <div className="tactical-card p-8 relative" style={{ borderColor: "rgba(255,59,48,0.4)" }} data-testid="plan-yearly">
            <div className="absolute -top-3 left-6 px-2 py-0.5 text-[10px] uppercase tracking-[0.2em] font-bold rounded-sm"
                 style={{ background: "var(--accent-red)" }}>Spart 120 €</div>
            <div className="overline">Jahresabo</div>
            <div className="mt-3 flex items-baseline gap-2">
              <span className="font-display font-black text-5xl">1.800 €</span>
              <span className="text-zinc-400 text-sm">/ Jahr</span>
            </div>
            <p className="text-zinc-400 text-sm mt-3">Spart 120 € gegenüber monatlich (1.920 €).</p>
            <ul className="mt-6 space-y-2 text-sm">
              {["Alle Funktionen","Priority Support","Volle Daten-Kontrolle","Updates inklusive","Einmal zahlen, ein Jahr nutzen"].map(t => (
                <li key={t} className="flex items-center gap-2 text-zinc-300">
                  <Check size={14} style={{ color: "var(--accent-green)" }} /> {t}
                </li>
              ))}
            </ul>
            <button data-testid="checkout-yearly" disabled={busy} onClick={() => startCheckout("yearly")}
                    className="kinetic-button w-full mt-7 py-3 rounded-sm flex items-center justify-center gap-2 disabled:opacity-60">
              {busy === "yearly" ? "Lade…" : <>Jahresabo wählen <ArrowRight size={15} /></>}
            </button>
          </div>
        </div>

        <div className="mt-10 text-center text-xs text-zinc-500 flex items-center justify-center gap-2">
          <ShieldCheck size={14} /> Zahlung über Stripe · DSGVO-konform · Jederzeit kündbar
        </div>
      </div>
    </div>
  );
}
