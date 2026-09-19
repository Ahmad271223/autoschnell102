import { neueFassungLaden } from "@/lib/fassung";
import { useState } from "react";
import { Link, useNavigate, useSearchParams } from "react-router-dom";
import { useAuth } from "@/context/AuthContext";
import { errMsg } from "@/lib/api";
import { toast } from "sonner";
import { sicheresZiel } from "@/lib/rollen";
import { Bolt, ArrowRight } from "lucide-react";
import InstallPWAButton from "@/components/InstallPWAButton";

export default function Login() {
  const { login, loginMfa } = useAuth();
  const [mfaToken, setMfaToken] = useState(null);   // Zwei-Faktor-Schritt (Admin)
  const [mfaCode, setMfaCode] = useState("");
  const nav = useNavigate();
  const [params] = useSearchParams();
  // Kontonummer (13.09.2026): Anmeldung mit der Kontonummer (Chef '10023',
  // Sucher '10023-2'). Das Feld bleibt Freitext — der Betreiber meldet sich
  // hier mit seinem Benutzernamen an, ohne dass die Maske darauf hinweist.
  const [kennung, setKennung] = useState("");
  const [pw, setPw] = useState("");
  const [loading, setLoading] = useState(false);

  const reason = params.get("reason");
  const next = params.get("next") || "/app";
  // Runde 19: genauer Grund der Abmeldung aus diesem Tab (api.js legt ihn
  // bei einer 401 ab). Nur einmal anzeigen, dann wieder vergessen.
  const [abmeldegrund] = useState(() => {
    try {
      const g = window.sessionStorage.getItem("ah_abmeldegrund") || "";
      window.sessionStorage.removeItem("ah_abmeldegrund");
      return g;
    } catch { return ""; }
  });

  const submit = async (e) => {
    e.preventDefault();
    setLoading(true);
    try {
      let u = mfaToken ? await loginMfa(mfaToken, mfaCode) : await login(kennung.trim(), pw);
      if (u?.mfa_erforderlich) {
        setMfaToken(u.mfa_token);
        toast.message("Bitte den Code aus deiner Authenticator-App eingeben");
        return;
      }
      toast.success("Willkommen zurück");
      // Dem ?next aus der Adresszeile wird nur gefolgt, wenn das Ziel zur
      // Rolle passt. Sonst landete ein Super-Admin, der sich auf
      // /login?next=/app/bestand anmeldet, auf einer Haendler-Seite —
      // mit Admin-Seitenleiste und der Meldung "Nur fuer Haendler-
      // Accounts" (Befund 05.09.2026).
      const ziel = sicheresZiel(u, params.get("next"));
      // Runde 31: Gibt es inzwischen eine neue Fassung, jetzt vollstaendig
      // laden — direkt nach der Anmeldung geht dabei nichts verloren.
      if (!neueFassungLaden(ziel)) nav(ziel);
    } catch (err) {
      toast.error(errMsg(err, "Login fehlgeschlagen"));
    } finally {
      setLoading(false);
    }
  };

  return (
    <div className="min-h-screen flex">
      {/* Linke Haelfte: dunkles Foto mit hellem Text — bleibt in beiden
          Designs dunkel (18.09.2026; vorher war die Schrift dort unlesbar). */}
      <div className="bleibt-dunkel hidden lg:block lg:w-1/2 relative" data-theme="dark"
           style={{ background: "#0c0c0c" }}>
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
            <span className="font-display font-black text-lg">AutoSchnell<span style={{color:"var(--accent-red)"}}>.</span></span>
          </Link>

          <h1 className="font-display font-black text-3xl tracking-tight">Anmelden</h1>
          <p className="text-zinc-400 text-sm mt-1">Willkommen zurück.</p>

          {reason === "session" && (
            <div className="mt-5 text-xs px-3 py-2 rounded-sm border" data-testid="login-abmeldegrund"
                 style={{ borderColor: "var(--accent-red)", background: "rgba(255,59,48,0.08)", color: "var(--accent-red)" }}>
              {abmeldegrund
                ? abmeldegrund
                : "Du wurdest abgemeldet (Sitzung abgelaufen, neu angemeldet auf einem anderen Gerät oder in einem anderen Tab, Abmeldung oder Sperre). Bitte neu anmelden."}
            </div>
          )}

          <div className="mt-6 space-y-3">
            <div>
              <label className="overline">Kontonummer</label>
              {/* Kein inputMode="numeric": der Zusatz '-2' und der Benutzername
                  des Betreibers muessen eingebbar bleiben. */}
              <input data-testid="login-kontonummer" type="text" required value={kennung} onChange={(e) => setKennung(e.target.value)}
                     autoComplete="username" autoCapitalize="none" spellCheck={false}
                     className="input-base w-full mt-1" placeholder="z. B. 10023 oder 10023-2" />
            </div>
            <div>
              <label className="overline">Passwort</label>
              <input data-testid="login-password" type="password" required value={pw} onChange={(e) => setPw(e.target.value)}
                     autoComplete="current-password"
                     className="input-base w-full mt-1" placeholder="••••••••" />
            </div>
            {mfaToken && (
              <div data-testid="mfa-schritt">
                <label className="overline">Code aus der Authenticator-App</label>
                <input value={mfaCode} onChange={(e) => setMfaCode(e.target.value)} inputMode="numeric" autoComplete="one-time-code"
                       placeholder="123456 oder Wiederherstellungscode" autoFocus data-testid="mfa-code"
                       className="input-base w-full mt-1" />
                <button type="button" onClick={() => { setMfaToken(null); setMfaCode(""); }}
                        className="mt-2 text-xs text-zinc-500 underline underline-offset-2">Zurück zum Passwort</button>
              </div>
            )}
          </div>

          <button data-testid="login-submit" type="submit" disabled={loading}
                  className="kinetic-button w-full mt-6 py-3 rounded-sm flex items-center justify-center gap-2 disabled:opacity-60">
            {loading ? "..." : <>Anmelden <ArrowRight size={15} /></>}
          </button>

          <div className="mt-6 text-sm text-zinc-400 text-center space-y-2">
            <div>
              <Link to="/passwort-vergessen" className="hover:text-white hover:underline">Passwort vergessen?</Link>
            </div>
            <div>
              Noch kein Konto? <Link to="/anfrage" className="text-white hover:underline">Zugang anfragen — wir schalten dich frei</Link>
            </div>
            <div className="text-xs text-zinc-500 space-x-3">
              <Link to="/fahrer/login" data-testid="link-zur-fahrer-app" className="hover:text-white hover:underline">Fahrer? Zur Fahrer-App</Link>
              <Link to="/markt/login" data-testid="link-zum-marktplatz" className="hover:text-white hover:underline">Zwischenhändler? Zum Marktplatz</Link>
            </div>
          </div>

          <div className="mt-8">
            <InstallPWAButton />
          </div>
        </form>
      </div>
    </div>
  );
}
