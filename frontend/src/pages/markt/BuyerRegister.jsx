import { useEffect, useState } from "react";
import { Link, useNavigate, useSearchParams } from "react-router-dom";
import { useBuyer } from "@/context/BuyerContext";
import { errMsg } from "@/lib/api";
import { toast } from "sonner";
import { Store, ArrowRight } from "lucide-react";

export default function BuyerRegister() {
  const { register, buyer, ready } = useBuyer();
  const nav = useNavigate();
  const [sp] = useSearchParams();
  const invite = sp.get("invite") || "";

  // Bereits eingeloggte Kaeufer landen mit Einladungslink NICHT im
  // Registrierungsformular (Sackgasse: eigene E-Mail -> 409), sondern
  // direkt im Marktplatz, der die Einladung einloest (Review 09/2026).
  useEffect(() => {
    if (ready && buyer) {
      nav(invite ? `/markt?invite=${encodeURIComponent(invite)}` : "/markt",
          { replace: true });
    }
  }, [ready, buyer, invite, nav]);
  const [f, setF] = useState({
    company_name: "", contact_name: "", email: "", password: "", phone: "",
  });
  const [busy, setBusy] = useState(false);
  const set = (k) => (e) => setF((s) => ({ ...s, [k]: e.target.value }));

  const submit = async (e) => {
    e.preventDefault();
    if (!f.company_name || !f.contact_name || !f.email || f.password.length < 8) {
      toast.error("Bitte Firma, Name, E-Mail und Passwort (min. 8 Zeichen) angeben");
      return;
    }
    setBusy(true);
    try {
      const res = await register({ ...f, invite_token: invite || undefined });
      if (invite && !res?.network_joined) {
        toast.warning("Registriert — aber die Einladung ist ungültig, abgelaufen oder "
          + "bereits aufgebraucht. Bitte den Händler um einen neuen Link.", { duration: 8000 });
      } else {
        toast.success(invite ? "Registriert & Netzwerk beigetreten" : "Registriert");
      }
      nav("/markt");
    } catch (err) {
      toast.error(errMsg(err, "Registrierung fehlgeschlagen"));
    } finally {
      setBusy(false);
    }
  };

  const inputCls = "w-full rounded-xl border bg-transparent px-4 py-3 text-sm outline-none focus:border-white/40";
  const st = { borderColor: "var(--border-default)" };

  return (
    <div className="min-h-screen flex items-center justify-center p-4" style={{ background: "#0a0a0a" }}>
      <div className="w-full max-w-md">
        <div className="flex items-center gap-2 mb-6">
          <div className="w-9 h-9 rounded-lg flex items-center justify-center text-white"
               style={{ background: "var(--accent-red)" }}>
            <Store size={18} />
          </div>
          <div className="font-black tracking-tight text-lg text-white">B2B-MARKTPLATZ</div>
        </div>
        <h1 className="font-display font-black text-3xl tracking-tighter text-white">
          Als Zwischenhändler registrieren
        </h1>
        <p className="text-sm text-zinc-500 mt-1 mb-6">
          {invite
            ? "Du wurdest in ein Händler-Netzwerk eingeladen."
            : "Zugang zu angebotenen Fahrzeugen erfordert ein Marktplatz-Abo (20 € / Monat)."}
        </p>
        <form onSubmit={submit} className="grid grid-cols-2 gap-3">
          <div className="col-span-2">
            <input value={f.company_name} onChange={set("company_name")} placeholder="Firma *" className={inputCls} style={st} />
          </div>
          <input value={f.contact_name} onChange={set("contact_name")} placeholder="Ansprechpartner *" className={inputCls} style={st} />
          <input value={f.phone} onChange={set("phone")} placeholder="Telefon" className={inputCls} style={st} />
          <div className="col-span-2">
            <input value={f.email} onChange={set("email")} type="email" placeholder="E-Mail *" className={inputCls} style={st} />
          </div>
          <div className="col-span-2">
            <input value={f.password} onChange={set("password")} type="password" placeholder="Passwort (min. 8 Zeichen) *" className={inputCls} style={st} />
          </div>
          <button type="submit" disabled={busy}
                  className="col-span-2 inline-flex items-center justify-center gap-2 rounded-xl py-3 font-semibold text-white disabled:opacity-50"
                  style={{ background: "var(--accent-red)" }}>
            {busy ? "Registrieren…" : <>Registrieren <ArrowRight size={16} /></>}
          </button>
        </form>
        <div className="mt-5 text-center text-sm text-zinc-500">
          Bereits registriert?{" "}
          <Link to={invite ? `/markt/login?invite=${encodeURIComponent(invite)}` : "/markt/login"}
                className="text-white font-semibold">Anmelden</Link>
        </div>
      </div>
    </div>
  );
}
