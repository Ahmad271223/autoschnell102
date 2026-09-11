import { Link, Navigate } from "react-router-dom";
import { Bolt, Building2, Store, Truck } from "lucide-react";
import { useAuth } from "@/context/AuthContext";
import { useDriver } from "@/context/DriverContext";
import { useBuyer } from "@/context/BuyerContext";
import SeiteLaedt from "@/components/SeiteLaedt";
import VerbindungsFehler from "@/components/VerbindungsFehler";
import { startZiel } from "@/lib/appstart";
import { letzteAnmeldung } from "@/lib/sitzung";

/** Hat api.js in diesem Tab eine beendete Sitzung vermerkt (Runde 19:
 *  Grund "neue Anmeldung am … von …")? Dann zeigt die Anmeldung ihn an. */
function sitzungBeendet() {
  try { return window.sessionStorage.getItem("ah_abmeldegrund") !== null; } catch { return false; }
}

const WEGE = [
  { to: "/login", titel: "Firma / Sucher", text: "Vergleich, Verträge, Termine, Bestand", icon: Building2, testid: "start-firma" },
  { to: "/fahrer/login", titel: "Fahrer", text: "Abholfahrten und Protokolle", icon: Truck, testid: "start-fahrer" },
  { to: "/markt/login", titel: "Marktplatz", text: "Zugang für Zwischenhändler", icon: Store, testid: "start-markt" },
];

function Auswahl() {
  return (
    <div className="min-h-screen flex items-center justify-center p-6"
         style={{ background: "var(--bg-app)", color: "var(--text-primary)" }} data-testid="start-auswahl">
      <div className="w-full max-w-sm">
        <div className="flex items-center gap-2 mb-8">
          <span className="w-9 h-9 rounded-md flex items-center justify-center" style={{ background: "var(--accent-red)" }}>
            <Bolt size={18} className="text-white" />
          </span>
          <span className="font-display font-black text-xl tracking-tight">AutoSchnell</span>
        </div>
        <h1 className="font-display font-black text-2xl tracking-tight">Wie meldest du dich an?</h1>
        <p className="text-sm mt-1" style={{ color: "var(--text-secondary)" }}>
          Nach der ersten Anmeldung startet die App direkt an der richtigen Stelle.
        </p>
        <div className="mt-6 space-y-2.5">
          {WEGE.map(({ to, titel, text, icon: Icon, testid }) => (
            <Link key={to} to={to} replace data-testid={testid}
                  className="flex items-center gap-3 rounded-xl border px-4 py-3.5 hover:bg-white/5"
                  style={{ borderColor: "var(--border-default)" }}>
              <span className="w-10 h-10 rounded-lg flex items-center justify-center shrink-0"
                    style={{ background: "var(--accent-red)" }}>
                <Icon size={18} className="text-white" />
              </span>
              <span className="min-w-0">
                <span className="block font-semibold">{titel}</span>
                <span className="block text-xs" style={{ color: "var(--text-secondary)" }}>{text}</span>
              </span>
            </Link>
          ))}
        </div>
      </div>
    </div>
  );
}

/** /start — Einstieg der installierten App (Erklaerung in lib/appstart.js). */
export default function AppStart() {
  const { user, loading, verbindungsfehler } = useAuth();
  const { driver, ready: fahrerBereit, fehler: fahrerFehler } = useDriver();
  const { buyer, ready: kaeuferBereit } = useBuyer();
  if (loading || !fahrerBereit || !kaeuferBereit) return <SeiteLaedt ganzeSeite />;
  // Runde 22: Server nicht erreichbar (Funkloch, Rollout) — die Anmeldung
  // bleibt erhalten; nicht auf Anmeldung/Auswahl raten, sondern das sagen.
  if (!user && !driver && !buyer && (verbindungsfehler || fahrerFehler)) {
    return <VerbindungsFehler grund={verbindungsfehler || fahrerFehler} />;
  }
  const ziel = startZiel({ user, driver, buyer, letzte: letzteAnmeldung() });
  // Sitzung beendet (z. B. neue Anmeldung auf einem anderen Geraet): die
  // Anmeldung mit dem Grund zeigen statt kommentarlos.
  if ((!ziel || ziel === "/login") && sitzungBeendet()) return <Navigate to="/login?reason=session" replace />;
  if (ziel) return <Navigate to={ziel} replace />;
  return <Auswahl />;
}
