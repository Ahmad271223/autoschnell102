import { Suspense } from "react";
import NachladeFehler from "@/components/NachladeFehler";
import SeiteLaedt from "@/components/SeiteLaedt";
import { Link, NavLink, Navigate, Outlet, useNavigate } from "react-router-dom";
import RechtsLinks from "@/components/RechtsLinks";
import { useDriver } from "@/context/DriverContext";
import { Truck, Calendar, Settings, LogOut } from "lucide-react";
import InstallPWAButton from "@/components/InstallPWAButton";
import VerbindungsFehler from "@/components/VerbindungsFehler";

export default function DriverLayout() {
  const { driver, ready, fehler, logout } = useDriver();
  const nav = useNavigate();

  if (!ready) return null;
  // Runde 22 (11.09.2026): Server nicht erreichbar -> Anmeldung behalten
  // und "Keine Verbindung" zeigen, statt zur Login-Seite zu schicken.
  if (!driver && fehler) return <VerbindungsFehler grund={fehler} />;
  if (!driver) return <Navigate to="/fahrer/login" replace />;

  const onLogout = () => { logout(); nav("/fahrer/login"); };

  // Handy-Ansicht (24.09.2026): jeder Reiter mindestens 44 px hoch.
  const tabBase = "flex-1 flex flex-col items-center justify-center gap-0.5 py-2 min-h-[44px] text-[11px] font-semibold";
  const tabCss = ({ isActive }) =>
    `${tabBase} ${isActive ? "text-white" : "text-zinc-500"}`;

  return (
    // --fahrer-tabs ist die verbindliche Hoehe der unteren Tableiste.
    // Seiten, die eine eigene Leiste darueber legen (Abholprotokoll),
    // rechnen damit — geschaetzte Werte fuehrten zu 1-2 Pixel Ueberlappung
    // und damit zu nicht klickbaren Knoepfen (Befund Ahmad 12.09.2026).
    // Handy-Ansicht (24.09.2026): 100dvh statt 100vh (iOS-Adressleiste), die
    // Kopfzeile beginnt unter der Statusleiste der installierten App, und der
    // Inhalt endet oberhalb von Tableiste + Home-Balken (ein Polster statt
    // zwei gestapelter).
    <div className="hoehe-voll"
         style={{ background: "var(--bg-app)", "--fahrer-tabs": "3.75rem" }}>
      <header className="glass-nav sticky top-0 z-40 border-b kopf-sicher"
              style={{ borderColor: "var(--border-default)" }}>
        <div className="max-w-3xl mx-auto px-3 sm:px-4 h-14 flex items-center justify-between gap-2">
          {/* Name wird bei langem Text abgeschnitten statt umzubrechen — sonst
              wuchs die Kopfzeile auf 375 px auf zwei Zeilen. */}
          <Link to="/fahrer" className="flex items-center gap-2 min-w-0 flex-1 tipp-h">
            <span className="w-8 h-8 rounded-sm flex items-center justify-center shrink-0"
                  style={{ background: "var(--accent-red)" }}>
              <Truck size={16} className="text-white" />
            </span>
            <div className="min-w-0">
              <div className="text-[10px] uppercase tracking-[0.2em] text-zinc-500">Fahrer</div>
              <div className="text-sm font-bold tracking-tight -mt-0.5 truncate"
                   data-testid="driver-header-name">
                {driver.display_name || driver.kontonummer || "—"}
              </div>
            </div>
          </Link>
          <div className="flex items-center gap-1.5 shrink-0">
            <InstallPWAButton variante="kompakt" />
            <button onClick={onLogout} data-testid="driver-logout-btn" aria-label="Abmelden"
              className="flex items-center gap-1.5 text-xs text-zinc-400 hover:text-white px-2.5 tipp-h rounded-sm whitespace-nowrap">
              <LogOut size={16} /> <span className="max-[359px]:hidden">Abmelden</span>
            </button>
          </div>
        </div>
      </header>

      <main className="max-w-3xl mx-auto px-4 pt-5"
            style={{ paddingBottom: "calc(var(--fahrer-tabs) + 2rem + env(safe-area-inset-bottom, 0px))" }}>
        <NachladeFehler>
          <Suspense fallback={<SeiteLaedt />}>
            <Outlet />
            <RechtsLinks className="py-3" />
          </Suspense>
        </NachladeFehler>
      </main>

      {/* Bottom Tab Bar */}
      <nav className="fixed bottom-0 inset-x-0 border-t z-40"
           data-testid="fahrer-tableiste"
           style={{ borderColor: "var(--border-default)", background: "var(--bg-elevated)",
                    backdropFilter: "blur(12px)",
                    // Gegenpruefung 12.09.2026: ohne safe-area lag die Leiste
                    // auf dem iPhone unter dem Home-Balken.
                    minHeight: "var(--fahrer-tabs)",
                    paddingBottom: "env(safe-area-inset-bottom, 0px)" }}>
        <div className="max-w-3xl mx-auto flex">
          <NavLink to="/fahrer" end className={tabCss} data-testid="tab-termine">
            <Calendar size={18} />
            <span>Fahrten</span>
          </NavLink>
          <NavLink to="/fahrer/einstellungen" className={tabCss} data-testid="tab-einstellungen">
            <Settings size={18} />
            <span>Einstellungen</span>
          </NavLink>
        </div>
      </nav>
    </div>
  );
}
