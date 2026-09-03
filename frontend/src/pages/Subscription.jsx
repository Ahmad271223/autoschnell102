import { useEffect } from "react";
import { useNavigate } from "react-router-dom";
import { useAuth } from "@/context/AuthContext";
import { Check, Bolt, ShieldCheck, Mail, Clock } from "lucide-react";

/**
 * /abo — seit 09/2026 KEIN Stripe-Checkout mehr für Firmen/Sucher:
 * Der Betreiber schaltet Sucher-Zugänge nach Rechnungsstellung manuell
 * frei (150 € / 30 Tage oder 1.500 € / 365 Tage je Nutzer, zzgl. USt).
 * Das Abo schaltet NUR Suche & Vergleich frei — Kaufverträge, Versand,
 * Terminplaner, Bestand und Inserate bleiben für die Firma kostenlos.
 * Diese Seite erklärt das und zeigt den eigenen Abo-Stand.
 */
export default function Subscription() {
  const nav = useNavigate();
  const { user, subscription, logout } = useAuth();

  useEffect(() => {
    if (subscription?.active) {
      nav("/app/vergleich");
    }
  }, [subscription, nav]);

  const istSucher = user?.role === "sucher";

  return (
    <div className="min-h-screen p-6">
      <div className="max-w-3xl mx-auto">
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
          <div className="overline mb-3">Sucher-Zugang noch nicht freigeschaltet</div>
          <h1 className="font-display font-black text-4xl lg:text-5xl tracking-tighter">
            Freischaltung läuft über uns.
          </h1>
          <p className="text-zinc-400 mt-3 max-w-xl mx-auto">
            Suche &amp; Vergleich brauchen einen freigeschalteten
            Sucher-Zugang. Kaufverträge, Versand, Terminplaner, Bestand und
            Inserate bleiben für die Firma kostenlos. Die Abrechnung läuft
            per Rechnung — es gibt hier nichts online zu bezahlen.
          </p>
        </div>

        <div className="tactical-card p-8" data-testid="abo-info">
          <div className="grid sm:grid-cols-2 gap-6">
            <div>
              <div className="overline">30 Tage</div>
              <div className="mt-2 flex items-baseline gap-2">
                <span className="font-display font-black text-4xl">150 €</span>
                <span className="text-zinc-400 text-sm">/ 30 Tage je Sucher</span>
              </div>
            </div>
            <div>
              <div className="overline">365 Tage</div>
              <div className="mt-2 flex items-baseline gap-2">
                <span className="font-display font-black text-4xl">1.500 €</span>
                <span className="text-zinc-400 text-sm">/ 365 Tage je Sucher</span>
              </div>
            </div>
          </div>
          <div className="mt-3 text-xs text-zinc-500" data-testid="abo-ust-hinweis">
            Preise zzgl. gesetzlicher Umsatzsteuer · Abrechnung per Rechnung.
          </div>

          <div className="mt-8 space-y-3 text-sm text-zinc-300">
            <div className="flex items-start gap-3">
              <Mail size={16} className="mt-0.5 shrink-0" style={{ color: "var(--accent-red)" }} />
              <div>
                {istSucher
                  ? "Sag deinem Chef Bescheid — er meldet sich beim Betreiber, der deinen Zugang nach Zahlungseingang freischaltet."
                  : "Melde dich beim Betreiber (oder warte auf unsere Rechnung) — nach Zahlungseingang schalten wir deinen Zugang frei."}
              </div>
            </div>
            <div className="flex items-start gap-3">
              <Clock size={16} className="mt-0.5 shrink-0" style={{ color: "var(--accent-red)" }} />
              <div>
                Die Freischaltung gilt ab Zahlungseingang für 30 Tage (monatlich)
                bzw. 365 Tage (jährlich). Wann die nächste Zahlung fällig ist,
                siehst du danach in den Einstellungen unter „Abo“.
              </div>
            </div>
            <div className="flex items-start gap-3">
              <Check size={16} className="mt-0.5 shrink-0" style={{ color: "var(--accent-green)" }} />
              <div>
                Das Sucher-Abo schaltet ausschließlich Suche &amp; Vergleich
                frei. Kaufverträge, Versand, Terminplaner, Bestand und
                Inserate bleiben für die Firma kostenlos — dafür ist keine
                Freischaltung nötig.
              </div>
            </div>
          </div>

          <a href="mailto:info@autoschnell.de?subject=Sucher-Zugang%20freischalten"
             data-testid="abo-kontakt"
             className="kinetic-button w-full mt-8 py-3 rounded-sm flex items-center justify-center gap-2">
            Betreiber kontaktieren
          </a>
        </div>

        <div className="mt-8 text-center text-xs text-zinc-500 flex items-center justify-center gap-2">
          <ShieldCheck size={14} /> Abrechnung per Rechnung · Freischaltung &amp; Sperrung durch den Betreiber
        </div>
      </div>
    </div>
  );
}
