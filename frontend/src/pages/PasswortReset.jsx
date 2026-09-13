import { useState } from "react";
import { Link, useNavigate, useSearchParams } from "react-router-dom";
import { api, errMsg } from "@/lib/api";
import { toast } from "sonner";
import { Bolt, ArrowRight } from "lucide-react";

export default function PasswortReset() {
  const [sp] = useSearchParams();
  const nav = useNavigate();
  const token = sp.get("token") || "";
  const [pw, setPw] = useState("");
  const [pw2, setPw2] = useState("");
  const [busy, setBusy] = useState(false);

  const submit = async (e) => {
    e.preventDefault();
    if (pw !== pw2) { toast.error("Die Passwörter stimmen nicht überein"); return; }
    setBusy(true);
    try {
      await api.post("/auth/password-reset/confirm", { token, new_password: pw });
      toast.success("Passwort geändert – bitte neu anmelden");
      nav("/login");
    } catch (err) {
      toast.error(errMsg(err, "Zurücksetzen fehlgeschlagen"));
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="min-h-screen flex items-center justify-center p-6" style={{ background: "#0a0a0a" }}>
      <div className="w-full max-w-sm">
        <Link to="/" className="flex items-center gap-2 mb-10">
          <span className="w-7 h-7 rounded-sm flex items-center justify-center"
                style={{ background: "var(--accent-red)" }}>
            <Bolt size={16} />
          </span>
          <span className="font-display font-black text-lg text-white">
            AUTOHANDEL<span style={{ color: "var(--accent-red)" }}>.</span>
          </span>
        </Link>

        {!token ? (
          <div>
            <h1 className="font-display font-black text-3xl tracking-tight text-white">Link ungültig</h1>
            <p className="text-zinc-400 text-sm mt-3">
              Diesem Link fehlt der Sicherheits-Token. Bitte fordere einen neuen an.
            </p>
            <Link to="/passwort-vergessen" className="inline-block mt-6 text-sm text-white hover:underline">
              Neuen Link anfordern
            </Link>
          </div>
        ) : (
          <form onSubmit={submit}>
            <h1 className="font-display font-black text-3xl tracking-tight text-white">Neues Passwort</h1>
            <p className="text-zinc-400 text-sm mt-1">
              Mindestens 8 Zeichen, mit Ziffer oder Sonderzeichen.
            </p>
            <div className="mt-6 space-y-3">
              <div>
                <label className="overline">Neues Passwort</label>
                <input type="password" required minLength={8} value={pw}
                       onChange={(e) => setPw(e.target.value)} autoComplete="new-password"
                       className="input-base w-full mt-1" placeholder="••••••••" />
              </div>
              <div>
                <label className="overline">Passwort wiederholen</label>
                <input type="password" required minLength={8} value={pw2}
                       onChange={(e) => setPw2(e.target.value)} autoComplete="new-password"
                       className="input-base w-full mt-1" placeholder="••••••••" />
              </div>
            </div>
            <button type="submit" disabled={busy}
                    className="kinetic-button w-full mt-6 py-3 rounded-sm flex items-center justify-center gap-2 disabled:opacity-60">
              {busy ? "..." : <>Passwort ändern <ArrowRight size={15} /></>}
            </button>
          </form>
        )}
      </div>
    </div>
  );
}
