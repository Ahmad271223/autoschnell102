import { neueFassungLaden } from "@/lib/fassung";
import { useEffect, useRef, useState } from "react";
import { Link, useNavigate, useSearchParams } from "react-router-dom";
import { buyerApi, useBuyer } from "@/context/BuyerContext";
import { TOKEN_KAEUFER, anmeldeartVormerken } from "@/lib/sitzung";
import { sicheresZiel } from "@/lib/rollen";
import { errMsg } from "@/lib/api";
import { toast } from "sonner";
import { Store, ArrowRight } from "lucide-react";
import InstallPWAButton from "@/components/InstallPWAButton";

export default function BuyerLogin() {
  const { buyer, ready, login } = useBuyer();
  const nav = useNavigate();
  const [sp] = useSearchParams();
  const invite = sp.get("invite") || "";
  // Kontonummer (13.09.2026): Zwischenhaendler melden sich mit ihrer
  // Kontonummer an; Konten legt der Betreiber nach einer Anfrage an.
  const [kennung, setKennung] = useState("");
  const [password, setPassword] = useState("");
  const [busy, setBusy] = useState(false);
  const hierAngemeldet = useRef(false);
  // Wer von hier aus die App installiert, soll beim Start hier landen.
  useEffect(() => { anmeldeartVormerken(TOKEN_KAEUFER); }, []);

  // Einladungslinks fuehren seit dem Wegfall der Registrierung hierher
  // (/markt/login?invite=). Schon angemeldete Kaeufer gehen direkt in den
  // Marktplatz, der die Einladung einloest — nicht nach einer Anmeldung auf
  // DIESER Seite, die loest sie selbst ein (sonst zweimal).
  useEffect(() => {
    if (ready && buyer && invite && !hierAngemeldet.current) {
      nav(`/markt?invite=${encodeURIComponent(invite)}`, { replace: true });
    }
  }, [ready, buyer, invite, nav]);

  const submit = async (e) => {
    e.preventDefault();
    setBusy(true);
    hierAngemeldet.current = true;
    try {
      await login(kennung.trim(), password);
    } catch (err) {
      hierAngemeldet.current = false;
      toast.error(errMsg(err, "Anmeldung fehlgeschlagen"));
      setBusy(false);
      return;
    }
    // Kontonummer (13.09.2026, Gegenpruefung): ?next= wie in Login.jsx pruefen —
    // neueFassungLaden ruft window.location.assign, fremde Adressen waeren
    // sonst eine offene Weiterleitung.
    let ziel = sicheresZiel({ role: "b2b_buyer" }, sp.get("next"));
    if (invite) {
      // Einladung direkt nach der Anmeldung einloesen. Ein Fehler hier ist
      // KEIN Anmeldefehler — die Sitzung steht, nur der Beitritt fehlt.
      try {
        const { data } = await buyerApi.post(`/invites/${encodeURIComponent(invite)}/redeem`);
        toast.success(`Netzwerk beigetreten: ${data?.dealer || ""}`, { duration: 6000 });
      } catch (err) {
        toast.warning(`${errMsg(err, "Einladung konnte nicht eingelöst werden")} — `
          + "bitte den Händler um einen neuen Link.", { duration: 8000 });
      }
      ziel = "/markt";
    }
    setBusy(false);
    // Runde 31: Gibt es inzwischen eine neue Fassung, jetzt vollstaendig
    // laden — direkt nach der Anmeldung geht dabei nichts verloren.
    if (!neueFassungLaden(ziel)) nav(ziel);
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
        <p className="text-sm text-zinc-500 mt-1 mb-6">
          {invite ? "Du wurdest in ein Händler-Netzwerk eingeladen — nach der Anmeldung trittst du bei."
                  : "Zugang für Zwischenhändler."}
        </p>
        <form onSubmit={submit} className="space-y-3">
          <div>
            <label className="text-[11px] text-zinc-500 uppercase tracking-wide">Käufer-Code</label>
            {/* Kaeufer-Code (14.09.2026): Buchstaben und Ziffern wie 6FE7K2M — keine
                Zifferntastatur; Gross-/Kleinschreibung ist egal (Server normalisiert). */}
            <input value={kennung} onChange={(e) => setKennung(e.target.value)} type="text" inputMode="text"
                   autoComplete="username" autoCapitalize="characters" spellCheck={false} required
                   data-testid="buyer-login-kontonummer"
                   placeholder="z. B. 6FE7K2M" className={inputCls} style={st} />
          </div>
          <div>
            <label className="text-[11px] text-zinc-500 uppercase tracking-wide">Passwort</label>
            <input value={password} onChange={(e) => setPassword(e.target.value)} type="password"
                   autoComplete="current-password" required data-testid="buyer-login-password"
                   placeholder="••••••••" className={inputCls} style={st} />
          </div>
          <button type="submit" disabled={busy} data-testid="buyer-login-submit"
                  className="w-full inline-flex items-center justify-center gap-2 rounded-xl py-3 font-semibold text-white disabled:opacity-50"
                  style={{ background: "var(--accent-red)" }}>
            {busy ? "Anmelden…" : <>Anmelden <ArrowRight size={16} /></>}
          </button>
        </form>
        <div className="mt-4 text-center text-xs">
          <Link to="/passwort-vergessen" className="text-zinc-500 hover:text-white underline">
            Passwort vergessen? Der Betreiber setzt es neu
          </Link>
        </div>
        <div className="mt-3 text-center text-[11.5px] text-zinc-600" data-testid="buyer-login-hinweis">
          Hier melden sich nur Zwischenhändler an. Firmen und Sucher:{" "}
          <Link to="/login" className="underline">/login</Link> · Fahrer:{" "}
          <Link to="/fahrer/login" className="underline">Fahrer-App</Link>
        </div>
        <div className="mt-4 text-center text-sm text-zinc-500">
          Noch kein Zugang?{" "}
          <Link to="/anfrage?art=kaeufer" data-testid="buyer-link-anfrage" className="text-white font-semibold">Zugang anfragen</Link>
        </div>
        <div className="mt-8">
          <InstallPWAButton />
        </div>
      </div>
    </div>
  );
}
