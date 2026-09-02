import { useState } from "react";
import { Link, Navigate, useNavigate } from "react-router-dom";
import { useDriver } from "@/context/DriverContext";
import { errMsg } from "@/lib/api";
import { toast } from "sonner";
import { Truck, Mail, Lock } from "lucide-react";
import InstallPWAButton from "@/components/InstallPWAButton";

export default function DriverLogin() {
  const { driver, ready, login } = useDriver();
  const nav = useNavigate();
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [loading, setLoading] = useState(false);

  if (!ready) return null;
  if (driver) return <Navigate to="/fahrer" replace />;

  const submit = async (e) => {
    e.preventDefault();
    setLoading(true);
    try {
      await login(email, password);
      toast.success("Willkommen zurück!");
      nav("/fahrer");
    } catch (err) {
      toast.error(errMsg(err, "Login fehlgeschlagen"));
    } finally {
      setLoading(false);
    }
  };

  return (
    <div className="min-h-screen flex items-center justify-center px-4"
         style={{ background: "var(--bg-app)" }} data-testid="driver-login-page">
      <div className="w-full max-w-md">
        <Link to="/" className="flex items-center justify-center gap-3 mb-8">
          <span className="w-10 h-10 rounded-sm flex items-center justify-center"
                style={{ background: "var(--accent-red)" }}>
            <Truck size={20} className="text-white" />
          </span>
          <div>
            <div className="overline">Fahrer-App</div>
            <div className="font-display font-black text-2xl tracking-tighter">
              AUTOHANDEL<span style={{ color: "var(--accent-red)" }}>.</span>
            </div>
          </div>
        </Link>

        <div className="tactical-card p-7">
          <h1 className="font-display font-black text-2xl tracking-tighter">Fahrer-Login</h1>
          <p className="text-sm text-zinc-400 mt-2">
            Mit E-Mail & Passwort einloggen, um deine Abholfahrten zu sehen.
          </p>
          <form onSubmit={submit} className="mt-6 space-y-4">
            <div>
              <label className="text-xs text-zinc-400 flex items-center gap-2">
                <Mail size={12} /> E-Mail
              </label>
              <input data-testid="driver-login-email" type="email" required
                value={email} onChange={(e) => setEmail(e.target.value)}
                className="input-base w-full mt-1" autoComplete="username" />
            </div>
            <div>
              <label className="text-xs text-zinc-400 flex items-center gap-2">
                <Lock size={12} /> Passwort
              </label>
              <input data-testid="driver-login-password" type="password" required
                value={password} onChange={(e) => setPassword(e.target.value)}
                className="input-base w-full mt-1" autoComplete="current-password" />
            </div>
            <button type="submit" data-testid="driver-login-submit" disabled={loading}
              className="kinetic-button w-full px-5 py-3 rounded-sm font-bold disabled:opacity-50">
              {loading ? "Anmelden …" : "Einsteigen"}
            </button>
          </form>

          <div className="mt-3 text-center text-xs">
            <Link to="/passwort-vergessen" data-testid="link-driver-reset" className="text-zinc-400 hover:text-white underline">
              Passwort vergessen?
            </Link>
          </div>
          <div className="mt-5 text-center text-sm text-zinc-400">
            Noch kein Fahrer-Account?{" "}
            <Link to="/fahrer/register" data-testid="link-driver-register"
              className="font-semibold" style={{ color: "var(--accent-red)" }}>
              Registrieren
            </Link>
          </div>

          <div className="mt-6 pt-6 border-t" style={{ borderColor: "var(--border-default)" }}>
            <InstallPWAButton />
          </div>
        </div>
      </div>
    </div>
  );
}
