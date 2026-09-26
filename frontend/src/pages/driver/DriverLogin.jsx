import { neueFassungLaden } from "@/lib/fassung";
import { useEffect, useState } from "react";
import { Link, Navigate, useNavigate, useSearchParams } from "react-router-dom";
import { lesen, sitzungsSpeicher } from "@/lib/speicher";
import { fahrerRuecksprungHolen, useDriver } from "@/context/DriverContext";
import { TOKEN_FAHRER, anmeldeartVormerken } from "@/lib/sitzung";
import { errMsg } from "@/lib/api";
import { toast } from "sonner";
import { Truck, Hash, Lock } from "lucide-react";
import InstallPWAButton from "@/components/InstallPWAButton";
import RechtsLinks from "@/components/RechtsLinks";

export default function DriverLogin() {
  const { driver, ready, login } = useDriver();
  const nav = useNavigate();
  // Kontonummer (13.09.2026): Fahrer melden sich mit ihrer Kontonummer an
  // (ohne Zusatz, deshalb Ziffern-Tastatur). Konten legt der Betreiber an.
  const [kennung, setKennung] = useState("");
  const [password, setPassword] = useState("");
  const [loading, setLoading] = useState(false);
  // R1-27: Grund der Abmeldung (vom 401-Abfaenger der Fahrer-App gemerkt).
  const [params] = useSearchParams();
  const [abmeldegrund] = useState(() => {
    const speicher = sitzungsSpeicher();
    const g = lesen(speicher, "ah_fahrer_abmeldegrund", "") || "";
    try { speicher?.removeItem("ah_fahrer_abmeldegrund"); } catch { /* egal */ }
    return g;
  });
  // Wer von hier aus die App installiert, soll beim Start hier landen.
  useEffect(() => { anmeldeartVormerken(TOKEN_FAHRER); }, []);

  if (!ready) return null;
  if (driver) return <Navigate to="/fahrer" replace />;

  const submit = async (e) => {
    e.preventDefault();
    setLoading(true);
    try {
      await login(kennung.trim(), password);
      toast.success("Willkommen zurück!");
      // Rollenprüfung 22.09.2026 (RP-546): kam die Abmeldung mitten im
      // Abholprotokoll, dorthin zurück (Eingaben/Unterschriften kommen aus
      // der Sicherung im Tab wieder).
      const ziel = fahrerRuecksprungHolen() || "/fahrer";
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
              AutoSchnell<span style={{ color: "var(--accent-red)" }}>.</span>
            </div>
          </div>
        </Link>

        <div className="tactical-card p-5 sm:p-7">
          <h1 className="font-display font-black text-2xl tracking-tighter">Fahrer-Login</h1>
          {params.get("reason") === "session" && (
            <div className="mt-4 text-xs px-3 py-2 rounded-sm border" data-testid="fahrer-abmeldegrund" role="alert"
                 style={{ borderColor: "var(--accent-red)", background: "rgba(255,59,48,0.08)", color: "var(--accent-red)" }}>
              {abmeldegrund || "Du wurdest abgemeldet (neu angemeldet auf einem anderen Gerät, Sperre oder "
                + "neues Passwort). Bitte neu anmelden."}
            </div>
          )}
          <p className="text-sm text-zinc-400 mt-2">
            Mit Kontonummer &amp; Passwort einloggen, um deine Abholfahrten zu sehen.
          </p>
          <form onSubmit={submit} className="mt-6 space-y-4">
            <div>
              <label className="text-xs text-zinc-400 flex items-center gap-2">
                <Hash size={12} /> Fahrer-ID (Kontonummer)
              </label>
              {/* Fahrer-ID (14.09.2026): "FD-7K2M9QX4" — Buchstaben und Ziffern,
                  Gross-/Kleinschreibung egal; aeltere reine Nummern gehen weiter. */}
              <input data-testid="driver-login-kontonummer" type="text" inputMode="text" required
                value={kennung} onChange={(e) => setKennung(e.target.value)}
                placeholder="z. B. FD-7K2M9QX4" autoCapitalize="characters" spellCheck={false}
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

          {/* Handy-Ansicht (24.09.2026): Textlinks mit Polster (gut tippbar). */}
          <div className="mt-2 text-center text-xs">
            <Link to="/passwort-vergessen" data-testid="link-driver-reset"
                  className="inline-block py-2 px-1 text-zinc-400 hover:text-white underline">
              Passwort vergessen? Der Betreiber setzt es neu
            </Link>
          </div>
          <div className="mt-1 text-center text-[11.5px] text-zinc-500 leading-relaxed" data-testid="driver-login-hinweis">
            Hier melden sich nur Fahrer an. Firmen und Sucher:{" "}
            <Link to="/login" className="underline inline-block py-1.5 px-0.5">/login</Link> · Zwischenhändler:{" "}
            <Link to="/markt/login" className="underline inline-block py-1.5 px-0.5">B2B-Marktplatz</Link>
          </div>
          <div className="mt-3 text-center text-sm text-zinc-400">
            Noch kein Zugang?{" "}
            <Link to="/anfrage?art=fahrer" data-testid="link-driver-anfrage"
              className="font-semibold inline-block py-2 px-1" style={{ color: "var(--accent-red)" }}>
              Zugang anfragen
            </Link>
          </div>

          <div className="mt-6 pt-6 border-t" style={{ borderColor: "var(--border-default)" }}>
            <InstallPWAButton />
          </div>
        </div>
        {/* Rollenprüfung 22.09.2026 (RP-563): Impressum, Datenschutz und AGB
            auch vor der Anmeldung erreichbar (§ 5 DDG, Art. 13 DSGVO). */}
        <RechtsLinks className="mt-8" />
      </div>
    </div>
  );
}
