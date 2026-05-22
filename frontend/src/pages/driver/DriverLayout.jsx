import { Link, NavLink, Navigate, Outlet, useNavigate } from "react-router-dom";
import { useDriver } from "@/context/DriverContext";
import { Truck, Calendar, Settings, LogOut } from "lucide-react";
import InstallPWAButton from "@/components/InstallPWAButton";

export default function DriverLayout() {
  const { driver, ready, logout } = useDriver();
  const nav = useNavigate();

  if (!ready) return null;
  if (!driver) return <Navigate to="/fahrer/login" replace />;

  const onLogout = () => { logout(); nav("/fahrer/login"); };

  const tabBase = "flex-1 flex flex-col items-center justify-center gap-0.5 py-2.5 text-[11px] font-semibold";
  const tabCss = ({ isActive }) =>
    `${tabBase} ${isActive ? "text-white" : "text-zinc-500"}`;

  return (
    <div className="min-h-screen pb-20" style={{ background: "var(--bg-app)" }}>
      <header className="glass-nav sticky top-0 z-40 border-b"
              style={{ borderColor: "var(--border-default)" }}>
        <div className="max-w-3xl mx-auto px-4 h-14 flex items-center justify-between">
          <Link to="/fahrer" className="flex items-center gap-2">
            <span className="w-8 h-8 rounded-sm flex items-center justify-center"
                  style={{ background: "var(--accent-red)" }}>
              <Truck size={16} className="text-white" />
            </span>
            <div>
              <div className="text-[10px] uppercase tracking-[0.2em] text-zinc-500">Fahrer</div>
              <div className="text-sm font-bold tracking-tight -mt-0.5"
                   data-testid="driver-header-name">
                {driver.display_name || driver.email || "—"}
              </div>
            </div>
          </Link>
          <div className="flex items-center gap-2">
            <InstallPWAButton compact />
            <button onClick={onLogout} data-testid="driver-logout-btn"
              className="flex items-center gap-1.5 text-xs text-zinc-400 hover:text-white px-3 py-2">
              <LogOut size={14} /> Abmelden
            </button>
          </div>
        </div>
      </header>

      <main className="max-w-3xl mx-auto px-4 pt-5 pb-24">
        <Outlet />
      </main>

      {/* Bottom Tab Bar */}
      <nav className="fixed bottom-0 inset-x-0 border-t z-40"
           style={{ borderColor: "var(--border-default)", background: "rgba(10,10,10,0.92)",
                    backdropFilter: "blur(12px)" }}>
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
