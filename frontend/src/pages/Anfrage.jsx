import { useState } from "react";
import { Link } from "react-router-dom";
import axios from "axios";
import { API_BASE } from "@/lib/api";
import { errMsg } from "@/lib/api";
import { toast } from "sonner";
import { Bolt, Check, ArrowRight } from "lucide-react";

/**
 * Zugangs-Anfrage (Beschluss 09/2026): Firmen registrieren sich nicht mehr
 * selbst. Sie stellen hier eine Anfrage — der Betreiber meldet sich, legt
 * das Firmen-Konto samt Sucher-Zugängen an und schaltet frei.
 */
export default function Anfrage() {
  const [f, setF] = useState({
    company_name: "", contact_person: "", email: "", phone: "",
    sucher_anzahl: 1, message: "",
  });
  const [busy, setBusy] = useState(false);
  const [done, setDone] = useState(false);

  const set = (k) => (e) => setF({ ...f, [k]: e.target.value });

  const submit = async (e) => {
    e.preventDefault();
    setBusy(true);
    try {
      await axios.post(`${API_BASE}/zugang-anfrage`, {
        ...f, sucher_anzahl: parseInt(f.sucher_anzahl, 10) || 0,
      });
      setDone(true);
    } catch (err) {
      toast.error(errMsg(err, "Anfrage konnte nicht gesendet werden"));
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="min-h-screen flex items-center justify-center p-6">
      <div className="w-full max-w-lg">
        <Link to="/" className="flex items-center gap-2 mb-8 justify-center">
          <span className="w-7 h-7 rounded-sm flex items-center justify-center"
                style={{ background: "var(--accent-red)" }}>
            <Bolt size={16} />
          </span>
          <span className="font-display font-black text-lg">AUTOHANDEL<span style={{ color: "var(--accent-red)" }}>.</span></span>
        </Link>

        {done ? (
          <div className="tactical-card p-8 text-center" data-testid="anfrage-done">
            <div className="w-12 h-12 mx-auto rounded-full flex items-center justify-center mb-4"
                 style={{ background: "var(--accent-green)" }}>
              <Check size={22} />
            </div>
            <h1 className="font-display font-black text-2xl tracking-tighter">Anfrage eingegangen</h1>
            <p className="text-zinc-400 text-sm mt-3 leading-relaxed">
              Danke! Wir melden uns zeitnah bei dir, besprechen die Details und
              schalten dein Firmen-Konto samt Sucher-Zugängen frei. Die
              Zugangsdaten bekommst du direkt von uns.
            </p>
            <Link to="/" className="inline-block mt-6 text-sm text-zinc-300 hover:text-white">Zur Startseite</Link>
          </div>
        ) : (
          <form onSubmit={submit} className="tactical-card p-8" data-testid="anfrage-form">
            <div className="overline mb-2">Für Autohändler-Firmen</div>
            <h1 className="font-display font-black text-2xl tracking-tighter">Zugang anfragen</h1>
            <p className="text-zinc-400 text-sm mt-2 leading-relaxed">
              Kein Formular-Konto, kein Zahlungsanbieter: Du fragst an, wir
              melden uns, legen dein Firmen-Konto und deine Sucher an und
              schalten frei. Sucher-Zugang: 150&nbsp;€/Monat oder
              1.500&nbsp;€/Jahr je Nutzer, Abrechnung per Rechnung.
            </p>

            <div className="mt-6 space-y-4">
              <div>
                <label className="overline">Firma *</label>
                <input required value={f.company_name} onChange={set("company_name")}
                       data-testid="anfrage-firma" placeholder="Autohaus Beispiel GmbH"
                       className="w-full mt-1 px-3 py-2.5 rounded-sm bg-[var(--bg-input)] border text-sm outline-none"
                       style={{ borderColor: "var(--border-default)" }} />
              </div>
              <div className="grid grid-cols-2 gap-3">
                <div>
                  <label className="overline">Ansprechpartner *</label>
                  <input required value={f.contact_person} onChange={set("contact_person")}
                         data-testid="anfrage-name" placeholder="Vor- und Nachname"
                         className="w-full mt-1 px-3 py-2.5 rounded-sm bg-[var(--bg-input)] border text-sm outline-none"
                         style={{ borderColor: "var(--border-default)" }} />
                </div>
                <div>
                  <label className="overline">Telefon</label>
                  <input value={f.phone} onChange={set("phone")}
                         data-testid="anfrage-telefon" placeholder="+49 …"
                         className="w-full mt-1 px-3 py-2.5 rounded-sm bg-[var(--bg-input)] border text-sm outline-none"
                         style={{ borderColor: "var(--border-default)" }} />
                </div>
              </div>
              <div>
                <label className="overline">E-Mail *</label>
                <input required type="email" value={f.email} onChange={set("email")}
                       data-testid="anfrage-email" placeholder="name@firma.de"
                       className="w-full mt-1 px-3 py-2.5 rounded-sm bg-[var(--bg-input)] border text-sm outline-none"
                       style={{ borderColor: "var(--border-default)" }} />
              </div>
              <div>
                <label className="overline">Gewünschte Sucher-Zugänge</label>
                <input type="number" min="0" max="50" value={f.sucher_anzahl}
                       onChange={set("sucher_anzahl")} data-testid="anfrage-sucher"
                       className="w-full mt-1 px-3 py-2.5 rounded-sm bg-[var(--bg-input)] border text-sm outline-none"
                       style={{ borderColor: "var(--border-default)" }} />
                <div className="text-xs text-zinc-500 mt-1">
                  Wie viele Mitarbeiter sollen suchen &amp; vergleichen? Lässt sich später jederzeit erweitern.
                </div>
              </div>
              <div>
                <label className="overline">Nachricht</label>
                <textarea rows={3} value={f.message} onChange={set("message")}
                          data-testid="anfrage-nachricht" placeholder="Kurz zu euch: Standort, Team, was ihr braucht …"
                          className="w-full mt-1 px-3 py-2.5 rounded-sm bg-[var(--bg-input)] border text-sm outline-none resize-none"
                          style={{ borderColor: "var(--border-default)" }} />
              </div>
            </div>

            <button type="submit" disabled={busy} data-testid="anfrage-senden"
                    className="kinetic-button w-full mt-6 py-3 rounded-sm flex items-center justify-center gap-2 disabled:opacity-60">
              {busy ? "Sende…" : <>Anfrage senden <ArrowRight size={15} /></>}
            </button>
            <div className="mt-4 text-center text-xs text-zinc-500">
              Schon freigeschaltet? <Link to="/login" className="text-zinc-300 hover:text-white">Anmelden</Link>
            </div>
          </form>
        )}
      </div>
    </div>
  );
}
