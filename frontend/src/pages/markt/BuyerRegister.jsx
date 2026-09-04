import { useEffect, useState } from "react";
import { Link, useNavigate, useSearchParams } from "react-router-dom";
import { useBuyer } from "@/context/BuyerContext";
import { api, errMsg } from "@/lib/api";
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
  // Ob der Zugang Geld kostet, sagt der Server — sonst steht hier ein
  // Preis, den seit der Umstellung auf kostenlos niemand mehr zahlt.
  const [kostenlos, setKostenlos] = useState(true);
  const [preis, setPreis] = useState(20);
  useEffect(() => {
    let aktiv = true;
    api.get("/payments/config").then((r) => {
      if (!aktiv || !r.data) return;
      setKostenlos(r.data.marktplatz_kostenlos !== false);
      setPreis(r.data.preis ?? 20);
    }).catch(() => {});
    return () => { aktiv = false; };
  }, []);

  const [f, setF] = useState({
    company_name: "", contact_name: "", email: "", password: "", phone: "",
    ust_id: "",
  });
  // Pflicht-Bestaetigung: Unternehmer (B2B) + AGB/Datenschutz akzeptiert
  // (AGB §1, Pruefbericht 09/2026). Das Backend lehnt false mit 400 ab.
  const [gewerblich, setGewerblich] = useState(false);
  const [busy, setBusy] = useState(false);
  const set = (k) => (e) => setF((s) => ({ ...s, [k]: e.target.value }));

  const submit = async (e) => {
    e.preventDefault();
    if (!f.company_name || !f.contact_name || !f.email || f.password.length < 8) {
      toast.error("Bitte Firma, Name, E-Mail und Passwort (min. 8 Zeichen) angeben");
      return;
    }
    if (!gewerblich) {
      toast.error("Bitte bestätige, dass du als Unternehmer handelst und AGB sowie Datenschutzerklärung akzeptierst");
      return;
    }
    setBusy(true);
    try {
      const res = await register({ ...f, ust_id: f.ust_id.trim(),
                                   gewerblich_bestaetigt: gewerblich,
                                   invite_token: invite || undefined });
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
            : kostenlos
              ? "Der Zugang zum Marktplatz ist kostenlos. Nach der Registrierung siehst du sofort alle angebotenen Fahrzeuge."
              : `Zugang zu angebotenen Fahrzeugen erfordert ein Marktplatz-Abo (${Number(preis).toLocaleString("de-DE")} € je 30 Tage, inkl. USt).`}
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
          <div className="col-span-2">
            <input value={f.ust_id} onChange={set("ust_id")} maxLength={40}
                   placeholder="USt-IdNr. oder Handelsregister-Nr. (optional)"
                   data-testid="buyer-ust-id" className={inputCls} style={st} />
          </div>
          <label className="col-span-2 flex items-start gap-3 text-sm text-zinc-300 cursor-pointer select-none">
            <input type="checkbox" checked={gewerblich}
                   onChange={(e) => setGewerblich(e.target.checked)}
                   data-testid="buyer-b2b-consent"
                   className="mt-0.5 h-4 w-4 shrink-0"
                   style={{ accentColor: "var(--accent-red)" }} />
            <span>
              Ich handle als Unternehmer/gewerblicher Kfz-Händler (B2B) und
              akzeptiere die{" "}
              <Link to="/agb" target="_blank" rel="noopener noreferrer"
                    className="text-white font-semibold underline">AGB</Link>{" "}
              und die{" "}
              <Link to="/datenschutz" target="_blank" rel="noopener noreferrer"
                    className="text-white font-semibold underline">Datenschutzerklärung</Link>. *
            </span>
          </label>
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
