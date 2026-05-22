import { useState } from "react";
import { Link, useNavigate, useSearchParams } from "react-router-dom";
import { useAuth } from "@/context/AuthContext";
import { errMsg } from "@/lib/api";
import { toast } from "sonner";
import { Bolt, ArrowRight } from "lucide-react";

export default function Login() {
  const { login } = useAuth();
  const nav = useNavigate();
  const [params] = useSearchParams();
  const [email, setEmail] = useState("");
  const [pw, setPw] = useState("");
  const [loading, setLoading] = useState(false);

  const reason = params.get("reason");
  const next = params.get("next") || "/app/vergleich";

  const submit = async (e) => {
    e.preventDefault();
    setLoading(true);
    try {
      const u = await login(email, pw);
      toast.success("Willkommen zurück");
      const isAdmin = u?.role === "admin" || u?.is_super_admin;
      const fallback = isAdmin ? "/admin" : "/app/vergleich";
      nav(params.get("next") || fallback);
    } catch (err) {
      toast.error(errMsg(err, "Login fehlgeschlagen"));
    } finally {
      setLoading(false);
    }
  };

  return (
    <div className="min-h-screen flex">
      <div className="hidden lg:block lg:w-1/2 relative" style={{ background: "#0c0c0c" }}>
        <div className="absolute inset-0 opacity-30 bg-cover bg-center"
             style={{ backgroundImage: "url(https://static.prod-images.emergentagent.com/jobs/a1ceceb6-7b86-4add-b1a2-2ba09adbd577/images/bc1425c15b101d82928a736d8d5885c8173800a2867499223e36b183b11097eb.png)" }} />
        <div className="absolute inset-0 flex items-end p-12">
          <div>
            <h2 className="font-display font-black text-4xl tracking-tighter">
              Schneller Ankauf.<br />
              Klare Verträge.
            </h2>
            <p className="text-zinc-400 mt-4 max-w-sm">
              Die schnellste Vertragsplattform für Autohändler.
            </p>
          </div>
        </div>
      </div>

      <div className="flex-1 flex items-center justify-center p-6">
        <form onSubmit={submit} className="w-full max-w-sm">
          <Link to="/" className="flex items-center gap-2 mb-10">
            <span className="w-7 h-7 rounded-sm flex items-center justify-center"
                  style={{ background: "var(--accent-red)" }}>
              <Bolt size={16} />
            </span>
            <span className="font-display font-black text-lg">AUTOHANDEL<span style={{color:"var(--accent-red)"}}>.</span></span>
          </Link>

          <h1 className="font-display font-black text-3xl tracking-tight">Anmelden</h1>
          <p className="text-zinc-400 text-sm mt-1">Willkommen zurück.</p>

          {reason === "session" && (
            <div className="mt-5 text-xs px-3 py-2 rounded-sm border" style={{ borderColor: "var(--accent-red)", background: "rgba(255,59,48,0.08)", color: "var(--accent-red)" }}>
              Du wurdest abgemeldet, weil dein Account auf einem anderen Gerät verwendet wurde.
            </div>
          )}

          <div className="mt-6 space-y-3">
            <div>
              <label className="overline">E-Mail oder Benutzername</label>
              <input data-testid="login-email" type="text" required value={email} onChange={(e) => setEmail(e.target.value)}
                     autoComplete="username"
                     className="input-base w-full mt-1" placeholder="haendler@firma.de" />
            </div>
            <div>
              <label className="overline">Passwort</label>
              <input data-testid="login-password" type="password" required value={pw} onChange={(e) => setPw(e.target.value)}
                     autoComplete="current-password"
                     className="input-base w-full mt-1" placeholder="••••••••" />
            </div>
          </div>

          <button data-testid="login-submit" type="submit" disabled={loading}
                  className="kinetic-button w-full mt-6 py-3 rounded-sm flex items-center justify-center gap-2 disabled:opacity-60">
            {loading ? "..." : <>Anmelden <ArrowRight size={15} /></>}
          </button>

          <div className="mt-6 text-sm text-zinc-400 text-center">
            Noch kein Konto? <Link to="/register" className="text-white hover:underline">Jetzt registrieren</Link>
          </div>
        </form>
      </div>
    </div>
  );
}
