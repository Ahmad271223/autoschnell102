import { Link, useLocation, useNavigate } from "react-router-dom";
import {
  Car, FileText, Calendar, Users, Settings as SettingsIcon, ShieldCheck,
  Layers, LogOut, Activity, Search, Warehouse,
} from "lucide-react";
import { useAuth } from "@/context/AuthContext";
import ThemeToggle from "@/components/ThemeToggle";

const NAV = [
  { to: "/app/vergleich", label: "Vergleich", icon: Activity },
  { to: "/app/suche", label: "Manuelle Suche", icon: Search },
  { to: "/app/vertraege", label: "Verträge / PDFs", icon: FileText },
  { to: "/app/termine", label: "Terminplaner", icon: Calendar },
  { to: "/app/fahrzeuge", label: "Fahrzeugpool", icon: Car },
  { to: "/app/bestand", label: "Bestand & Verkauf", icon: Warehouse, haendlerOnly: true },
  { to: "/app/team", label: "Mitarbeiter / Sucher", icon: Users, haendlerOnly: true },
  { to: "/app/fahrer", label: "Fahrer", icon: Users },
  { to: "/app/einstellungen", label: "Einstellungen", icon: SettingsIcon },
];

/**
 * Icon-Only Rail — konstant 64px breit auf allen Bildschirmgrößen.
 * Labels sind im Tooltip (`title`) für Maus-Hover und verstecken sich
 * sonst komplett. So sieht es auf Handy und Desktop gleich aus.
 */
export default function AppLayout({ children }) {
  const { pathname } = useLocation();
  const nav = useNavigate();
  const { user, subscription, logout } = useAuth();

  // Sucher-Unteraccounts sehen keine Händler-Funktionen (Bestand, Team).
  // Strikte Rollentrennung: Admin-Konten verwalten nur — die Händler-/
  // Sucher-Funktionen würden im Backend ohnehin blockiert.
  const items = user?.role === "admin"
    ? [{ to: "/admin", label: "Admin", icon: ShieldCheck }]
    : NAV.filter((it) => !(it.haendlerOnly && user?.role === "sucher"));

  return (
    <div className="min-h-screen flex" style={{ background: "var(--bg-app)", color: "var(--text-primary)" }}>
      <aside
        data-testid="app-sidebar"
        className="w-16 border-r flex flex-col shrink-0 sticky top-0 h-screen"
        style={{ borderColor: "var(--border-default)", background: "var(--bg-surface)" }}
      >
        <Link to="/app/vergleich"
              className="h-16 border-b flex items-center justify-center shrink-0"
              style={{ borderColor: "var(--border-default)" }}
              title="Autohandel">
          <span className="w-9 h-9 rounded-md flex items-center justify-center"
                style={{ background: "var(--accent-red)" }}>
            <Layers size={18} className="text-white" />
          </span>
        </Link>

        <nav className="flex-1 py-3 px-1.5 space-y-1.5 overflow-y-auto">
          {items.map((it) => {
            const Active = pathname.startsWith(it.to);
            const Icon = it.icon;
            return (
              <Link
                key={it.to}
                to={it.to}
                data-testid={`nav-${it.to.split("/").pop()}`}
                title={it.label}
                className={`relative flex items-center justify-center w-full py-3 rounded-lg sidebar-link ${
                  Active ? "sidebar-link-active" : ""
                }`}
              >
                <Icon size={20} className={Active ? "text-[var(--accent-red)]" : ""} />
                {Active && (
                  <span className="absolute right-0.5 top-1/2 -translate-y-1/2 w-1 h-5 rounded-full"
                        style={{ background: "var(--accent-red)" }} />
                )}
              </Link>
            );
          })}
        </nav>

        <div className="flex flex-col items-center gap-2 pb-2 border-t pt-2"
             style={{ borderColor: "var(--border-default)" }}>
          <ThemeToggle />
          <span
            className="w-2 h-2 rounded-full"
            style={{
              background: subscription?.active ? "var(--accent-green)" : "var(--accent-red)",
            }}
            title={subscription?.plan === "lifetime" ? "Lifetime"
                   : subscription?.active ? "Aktiv" : "Kein Abo"}
            data-testid="sub-status-badge"
          />
          <button
            onClick={async () => { await logout(); nav("/"); }}
            data-testid="logout-btn"
            className="p-2 rounded-md hover:bg-white/5"
            style={{ color: "var(--text-secondary)" }}
            title="Abmelden"
          >
            <LogOut size={16} />
          </button>
        </div>
      </aside>

      <main className="flex-1 overflow-x-hidden min-w-0">
        {children}
      </main>

    </div>
  );
}
