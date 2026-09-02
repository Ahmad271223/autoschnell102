import { NavLink, Outlet, useNavigate } from "react-router-dom";
import { useAuth } from "@/context/AuthContext";
import {
  LayoutDashboard, Users, GitCompareArrows, Activity, Settings, LogOut,
  ScrollText, AlertTriangle, KeyRound, Car,
} from "lucide-react";

/**
 * Dark Admin-Layout — passend zum Rest der App.
 * - Schwarz/Zinc Hintergrund, rote Akzente, weiße Schrift
 * - Sticky Sidebar mit Navigation
 * - Outlet für die Sub-Pages
 */

const NAV = [
  { to: "/admin",             label: "Übersicht",     icon: LayoutDashboard, end: true },
  { to: "/admin/users",       label: "Nutzer",        icon: Users },
  { to: "/admin/comparisons", label: "Vergleiche",    icon: GitCompareArrows },
  { to: "/admin/urls",        label: "URL-Statistik", icon: Activity },
  { to: "/admin/audit",       label: "Audit-Log",     icon: ScrollText },
  { to: "/admin/errors",      label: "Fehler",        icon: AlertTriangle },
  { to: "/admin/freischaltungen", label: "Freischaltungen", icon: KeyRound },
  // Nur der Super-Admin sieht die anonymen Auto-Daten (Backend blockt
  // normale Admins zusätzlich mit 403).
  { to: "/admin/auto-daten",  label: "Auto-Daten",    icon: Car, superOnly: true },
  { to: "/admin/settings",    label: "Einstellungen", icon: Settings },
];

export default function AdminLayout() {
  const { user, logout } = useAuth();
  const nav = useNavigate();
  const NAV_SICHTBAR = NAV.filter((item) => !item.superOnly || user?.is_super_admin);

  const handleLogout = async () => {
    await logout();
    nav("/login");
  };

  return (
    <div
      data-theme="dark"
      className="admin-shell min-h-screen w-full"
      style={{
        background: "#0a0a0a",
        color: "#ffffff",
      }}
    >
      <div className="flex min-h-screen">
        {/* Sidebar */}
        <aside
          className="hidden md:flex md:w-64 lg:w-72 flex-col sticky top-0 h-screen"
          style={{
            background: "#0f0f10",
            borderRight: "1px solid rgba(255,255,255,0.08)",
          }}
        >
          <div className="px-6 pt-7 pb-4">
            <div className="flex items-center gap-2.5">
              <div
                className="w-9 h-9 rounded-sm flex items-center justify-center text-white text-sm font-bold shadow-sm"
                style={{ background: "var(--accent-red)" }}
              >
                A
              </div>
              <div>
                <div className="text-[15px] font-semibold tracking-tight text-white">Admin</div>
                <div className="text-[11px] text-zinc-500 -mt-0.5">Cash Car Hannover</div>
              </div>
            </div>
          </div>
          <nav className="px-3 mt-2 flex-1 space-y-1">
            {NAV_SICHTBAR.map((item) => (
              <NavLink
                key={item.to}
                to={item.to}
                end={item.end}
                className={({ isActive }) =>
                  `flex items-center gap-3 px-3 py-2.5 rounded-xl text-[14px] transition-all ${
                    isActive
                      ? "bg-white/10 text-white font-semibold"
                      : "text-zinc-400 hover:bg-white/5 hover:text-white"
                  }`
                }
              >
                <item.icon size={17} strokeWidth={2.2} />
                <span>{item.label}</span>
              </NavLink>
            ))}
          </nav>
          <div
            className="px-3 pb-5 pt-4 mt-2"
            style={{ borderTop: "1px solid rgba(255,255,255,0.08)" }}
          >
            <div className="px-3 mb-3">
              <div className="text-[12px] text-zinc-500">Angemeldet als</div>
              <div className="text-[13px] font-medium text-white truncate">
                {user?.username || user?.email}
              </div>
            </div>
            <button
              onClick={handleLogout}
              className="w-full flex items-center gap-3 px-3 py-2.5 rounded-xl text-[14px] text-zinc-400 hover:bg-white/5 hover:text-white transition-all"
            >
              <LogOut size={17} strokeWidth={2.2} />
              <span>Abmelden</span>
            </button>
          </div>
        </aside>

        {/* Main */}
        <main className="flex-1 min-w-0">
          {/* Mobile-Header */}
          <div
            className="md:hidden sticky top-0 z-10 px-4 py-3 flex items-center justify-between"
            style={{
              background: "rgba(15,15,16,0.92)",
              backdropFilter: "blur(12px)",
              borderBottom: "1px solid rgba(255,255,255,0.08)",
            }}
          >
            <div className="flex items-center gap-2">
              <div
                className="w-8 h-8 rounded-sm flex items-center justify-center text-white text-xs font-bold"
                style={{ background: "var(--accent-red)" }}
              >
                A
              </div>
              <span className="font-semibold text-[15px] text-white">Admin</span>
            </div>
            <button onClick={handleLogout} className="text-zinc-400 hover:text-white">
              <LogOut size={18} />
            </button>
          </div>
          {/* Mobile-Tabs */}
          <div
            className="md:hidden overflow-x-auto"
            style={{
              background: "rgba(15,15,16,0.85)",
              borderBottom: "1px solid rgba(255,255,255,0.08)",
            }}
          >
            <div className="flex gap-1 px-2 py-1.5 whitespace-nowrap">
              {NAV_SICHTBAR.map((item) => (
                <NavLink
                  key={item.to}
                  to={item.to}
                  end={item.end}
                  className={({ isActive }) =>
                    `px-3 py-1.5 rounded-lg text-[13px] flex items-center gap-1.5 ${
                      isActive ? "bg-white/10 text-white font-semibold" : "text-zinc-400"
                    }`
                  }
                >
                  <item.icon size={14} strokeWidth={2.2} />
                  {item.label}
                </NavLink>
              ))}
            </div>
          </div>

          <div className="px-5 md:px-8 lg:px-10 py-6 md:py-8 max-w-[1200px] mx-auto">
            <Outlet />
          </div>
        </main>
      </div>
    </div>
  );
}
