import { useState } from "react";
import { Link, Navigate, useNavigate } from "react-router-dom";
import { useDriver } from "@/context/DriverContext";
import { errMsg } from "@/lib/api";
import { toast } from "sonner";
import { Truck, Mail, Lock, User } from "lucide-react";

export default function DriverRegister() {
  const { driver, ready, register } = useDriver();
  const nav = useNavigate();
  const [form, setForm] = useState({ email: "", password: "", display_name: "" });
  const [loading, setLoading] = useState(false);

  if (!ready) return null;
  if (driver) return <Navigate to="/fahrer" replace />;

  const submit = async (e) => {
    e.preventDefault();
    if (form.password.length < 6) return toast.error("Passwort min. 6 Zeichen");
    if (form.display_name.trim().length < 2) return toast.error("Name zu kurz");
    setLoading(true);
    try {
      await register(form.email, form.password, form.display_name.trim());
      toast.success("Account erstellt!");
      nav("/fahrer");
    } catch (err) {
      toast.error(errMsg(err, "Registrierung fehlgeschlagen"));
    } finally {
      setLoading(false);
    }
  };

  const up = (k) => (e) => setForm({ ...form, [k]: e.target.value });

  return (
    <div className="min-h-screen flex items-center justify-center px-4 py-12"
         style={{ background: "var(--bg-app)" }} data-testid="driver-register-page">
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
          <h1 className="font-display font-black text-2xl tracking-tighter">Fahrer registrieren</h1>
          <p className="text-sm text-zinc-400 mt-2">
            Nach der Registrierung bekommst du deine <b>Fahrer-ID</b> – gib sie
            einem oder mehreren Händlern, damit sie dich Fahrten zuweisen können.
          </p>
          <form onSubmit={submit} className="mt-6 space-y-4">
            <div>
              <label className="text-xs text-zinc-400 flex items-center gap-2">
                <User size={12} /> Dein Name (wird dem Händler angezeigt)
              </label>
              <input data-testid="driver-reg-name" required minLength={2}
                value={form.display_name} onChange={up("display_name")}
                className="input-base w-full mt-1" autoComplete="name" />
            </div>
            <div>
              <label className="text-xs text-zinc-400 flex items-center gap-2">
                <Mail size={12} /> E-Mail
              </label>
              <input data-testid="driver-reg-email" type="email" required
                value={form.email} onChange={up("email")}
                className="input-base w-full mt-1" autoComplete="username" />
            </div>
            <div>
              <label className="text-xs text-zinc-400 flex items-center gap-2">
                <Lock size={12} /> Passwort (min. 6 Zeichen)
              </label>
              <input data-testid="driver-reg-password" type="password" required minLength={6}
                value={form.password} onChange={up("password")}
                className="input-base w-full mt-1" autoComplete="new-password" />
            </div>
            <button type="submit" data-testid="driver-reg-submit" disabled={loading}
              className="kinetic-button w-full px-5 py-3 rounded-sm font-bold disabled:opacity-50">
              {loading ? "Anlegen …" : "Account erstellen"}
            </button>
          </form>

          <div className="mt-5 text-center text-sm text-zinc-400">
            Schon registriert?{" "}
            <Link to="/fahrer/login" data-testid="link-driver-login"
              className="font-semibold" style={{ color: "var(--accent-red)" }}>
              Anmelden
            </Link>
          </div>
        </div>
      </div>
    </div>
  );
}
