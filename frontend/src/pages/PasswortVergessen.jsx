import { useState } from "react";
import { Link } from "react-router-dom";
import { api, errMsg } from "@/lib/api";
import { toast } from "sonner";
import { Bolt, ArrowRight, MailCheck } from "lucide-react";

export default function PasswortVergessen() {
  const [email, setEmail] = useState("");
  const [busy, setBusy] = useState(false);
  const [sent, setSent] = useState(false);

  const submit = async (e) => {
    e.preventDefault();
    setBusy(true);
    try {
      await api.post("/auth/password-reset/request", { email: email.trim() });
      setSent(true);
    } catch (err) {
      toast.error(errMsg(err, "Anfrage fehlgeschlagen"));
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

        {sent ? (
          <div>
            <MailCheck size={34} className="text-emerald-400 mb-4" />
            <h1 className="font-display font-black text-3xl tracking-tight text-white">E-Mail unterwegs</h1>
            <p className="text-zinc-400 text-sm mt-3">
              Falls die Adresse registriert ist, haben wir dir einen Link zum
              Zurücksetzen geschickt (gültig 60 Minuten). Bitte auch den
              Spam-Ordner prüfen.
            </p>
            <Link to="/login" className="inline-block mt-6 text-sm text-white hover:underline">
              Zurück zur Anmeldung
            </Link>
          </div>
        ) : (
          <form onSubmit={submit}>
            <h1 className="font-display font-black text-3xl tracking-tight text-white">Passwort vergessen</h1>
            <p className="text-zinc-400 text-sm mt-1">
              Wir schicken dir einen Link zum Zurücksetzen.
            </p>
            <div className="mt-6">
              <label className="overline">E-Mail</label>
              <input type="email" required value={email} onChange={(e) => setEmail(e.target.value)}
                     autoComplete="email"
                     className="input-base w-full mt-1" placeholder="haendler@firma.de" />
            </div>
            <button type="submit" disabled={busy}
                    className="kinetic-button w-full mt-6 py-3 rounded-sm flex items-center justify-center gap-2 disabled:opacity-60">
              {busy ? "..." : <>Link anfordern <ArrowRight size={15} /></>}
            </button>
            <div className="mt-6 text-sm text-zinc-400 text-center">
              <Link to="/login" className="text-white hover:underline">Zurück zur Anmeldung</Link>
            </div>
            <p className="mt-6 text-[11px] text-zinc-600">
              Hinweis: Sucher-Accounts werden vom Händler-Hauptaccount verwaltet —
              bitte wende dich an deinen Chef, wenn du Sucher bist.
            </p>
          </form>
        )}
      </div>
    </div>
  );
}
