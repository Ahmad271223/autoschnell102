import { neueFassungLaden } from "@/lib/fassung";
import { useEffect, useState } from "react";
import { Link, useNavigate, useSearchParams } from "react-router-dom";
import { useBuyer } from "@/context/BuyerContext";
import { TOKEN_KAEUFER, anmeldeartVormerken } from "@/lib/sitzung";
import { errMsg } from "@/lib/api";
import { toast } from "sonner";
import { Store, ArrowRight } from "lucide-react";
import InstallPWAButton from "@/components/InstallPWAButton";

export default function BuyerLogin() {
  const { login } = useBuyer();
  const nav = useNavigate();
  const [sp] = useSearchParams();
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [busy, setBusy] = useState(false);
  // Wer von hier aus die App installiert, soll beim Start hier landen.
  useEffect(() => { anmeldeartVormerken(TOKEN_KAEUFER); }, []);

  const submit = async (e) => {
    e.preventDefault();
    setBusy(true);
    try {
      await login(email.trim(), password);
      // Einladung aus dem Registrierungs-Link ueberlebt den Umweg ueber
      // den Login und wird im Marktplatz eingeloest (Review 09/2026).
      const invite = sp.get("invite");
      const ziel = invite ? `/markt?invite=${encodeURIComponent(invite)}` : (sp.get("next") || "/markt");
      // Runde 31: Gibt es inzwischen eine neue Fassung, jetzt vollstaendig
      // laden — direkt nach der Anmeldung geht dabei nichts verloren.
      if (!neueFassungLaden(ziel)) nav(ziel);
    } catch (err) {
      toast.error(errMsg(err, "Anmeldung fehlgeschlagen"));
    } finally {
      setBusy(false);
    }
  };

  const inputCls = "w-full rounded-xl border bg-transparent px-4 py-3 text-sm outline-none focus:border-white/40";
  const st = { borderColor: "var(--border-default)" };

  return (
    // Die Seite ist immer dunkel: die Design-Variablen hier auf die dunklen
    // Werte setzen, sonst waere der Installieren-Knopf im hellen Design
    // dunkel auf dunkel (Gegenpruefung 11.09.2026).
    <div className="min-h-screen flex items-center justify-center p-4"
         style={{ background: "#0a0a0a", "--text-primary": "#f4f4f5", "--text-secondary": "#a1a1aa",
                  "--border-default": "rgba(255,255,255,0.12)" }}>
      <div className="w-full max-w-sm">
        <div className="flex items-center gap-2 mb-6">
          <div className="w-9 h-9 rounded-lg flex items-center justify-center text-white"
               style={{ background: "var(--accent-red)" }}>
            <Store size={18} />
          </div>
          <div className="font-black tracking-tight text-lg text-white">B2B-MARKTPLATZ</div>
        </div>
        <h1 className="font-display font-black text-3xl tracking-tighter text-white">Händler-Login</h1>
        <p className="text-sm text-zinc-500 mt-1 mb-6">Zugang für Zwischenhändler.</p>
        <form onSubmit={submit} className="space-y-3">
          <div>
            <label className="text-[11px] text-zinc-500 uppercase tracking-wide">E-Mail</label>
            <input value={email} onChange={(e) => setEmail(e.target.value)} type="email"
                   placeholder="haendler@firma.de" className={inputCls} style={st} />
          </div>
          <div>
            <label className="text-[11px] text-zinc-500 uppercase tracking-wide">Passwort</label>
            <input value={password} onChange={(e) => setPassword(e.target.value)} type="password"
                   placeholder="••••••••" className={inputCls} style={st} />
          </div>
          <button type="submit" disabled={busy}
                  className="w-full inline-flex items-center justify-center gap-2 rounded-xl py-3 font-semibold text-white disabled:opacity-50"
                  style={{ background: "var(--accent-red)" }}>
            {busy ? "Anmelden…" : <>Anmelden <ArrowRight size={16} /></>}
          </button>
        </form>
        <div className="mt-5 text-center text-sm text-zinc-500">
          Noch kein Zugang?{" "}
          <Link to={sp.get("invite") ? `/markt/registrieren?invite=${encodeURIComponent(sp.get("invite"))}` : "/markt/registrieren"} className="text-white font-semibold">Jetzt registrieren</Link>
        </div>
        <div className="mt-8">
          <InstallPWAButton />
        </div>
      </div>
    </div>
  );
}
