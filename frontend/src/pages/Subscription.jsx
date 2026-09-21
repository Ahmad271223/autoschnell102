import { useEffect } from "react";
import { Link, useNavigate } from "react-router-dom";
import { useAuth } from "@/context/AuthContext";
import RechtsLinks from "@/components/RechtsLinks";
import { Check, Bolt, ShieldCheck, Mail, Clock } from "lucide-react";

/**
 * /abo — seit 09/2026 KEIN Stripe-Checkout mehr für Firmen/Sucher:
 * Der Betreiber schaltet Sucher-Zugänge nach Rechnungsstellung manuell
 * frei (150 € / 30 Tage oder 1.500 € / 365 Tage je Nutzer, zzgl. USt).
 * Das Abo schaltet Suche, Vergleich und Kaufverträge (samt Versand) frei —
 * Terminplaner, Freigaben, Bestand und Inserate bleiben für die Firma kostenlos.
 * (19.09.2026: Text an den Server angeglichen — Verträge verlangen das Abo.)
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
            <span className="font-display font-black text-lg">AutoSchnell<span style={{color:"var(--accent-red)"}}>.</span></span>
          </div>
          <div className="flex items-center gap-3 text-sm">
            {/* Pruefbericht 20.09.2026 (U-144/H3): Die Seite war eine Sackgasse —
                auch fuer den kostenlosen Chef, der ueber das Logo oder die
                App-Verknuepfung hier landete. Jetzt geht es zurueck in die
                kostenlosen Bereiche (Vertraege, Termine, Bestand). */}
            <Link to={istSucher ? "/app/vertraege" : "/app/bestand"} data-testid="abo-zurueck"
                  className="px-3 py-1 rounded-sm border hover:bg-white/5"
                  style={{ borderColor: "var(--border-default)" }}>
              Zurück zur App
            </Link>
            {/* Kontonummer (13.09.2026): Anmeldekennung statt E-Mail */}
            <span className="text-zinc-400">{user?.kontonummer ? `Konto ${user.kontonummer}` : (user?.username || user?.email)}</span>
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
            Suche, Vergleich und Kaufverträge brauchen einen freigeschalteten
            Sucher-Zugang. Terminplaner, Freigaben, Bestand und Inserate
            bleiben für die Firma kostenlos. Die Abrechnung läuft per
            Rechnung — es gibt hier nichts online zu bezahlen.
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
                Das Sucher-Abo schaltet Suche, Vergleich und Kaufverträge
                (samt Versand) frei. Terminplaner, Freigaben, Bestand und
                Inserate bleiben für die Firma kostenlos — dafür ist keine
                Freischaltung nötig.
              </div>
            </div>
          </div>

          {/* Wunsch Ahmad 17.09.2026: Knopf "Betreiber kontaktieren" (E-Mail des
              Inhabers) vorerst entfernt — die Hinweise oben nennen den Weg. */}
        </div>

        <div className="mt-8 text-center text-xs text-zinc-500 flex items-center justify-center gap-2">
          <ShieldCheck size={14} /> Abrechnung per Rechnung · Freischaltung &amp; Sperrung durch den Betreiber
        </div>
        <RechtsLinks className="mt-6" />
      </div>
    </div>
  );
}
