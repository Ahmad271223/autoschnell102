import { useState } from "react";
import { Link, useNavigate, useSearchParams } from "react-router-dom";
import { useAuth } from "@/context/AuthContext";
import { errMsg } from "@/lib/api";
import { toast } from "sonner";
import { Bolt, ArrowRight } from "lucide-react";

export default function Register() {
  const { register } = useAuth();
  const nav = useNavigate();
  const [params] = useSearchParams();
  const planParam = params.get("plan");
  const [form, setForm] = useState({
    email: "", password: "", company_name: "", contact_person: "", phone: "",
  });
  const [loading, setLoading] = useState(false);

  const set = (k, v) => setForm({ ...form, [k]: v });

  const submit = async (e) => {
    e.preventDefault();
    setLoading(true);
    try {
      await register(form);
      toast.success("Konto erstellt — willkommen!");
      // Händler-Hauptaccount ist kostenlos: direkt in den Bestand, NICHT auf
      // die Abo-Seite (das Sucher-Abo braucht er nur fürs Vergleichen/Suchen
      // und kann es jederzeit über die Team-Seite oder /abo buchen).
      nav(planParam ? `/abo?plan=${planParam}` : "/app");
    } catch (err) {
      toast.error(errMsg(err, "Registrierung fehlgeschlagen"));
    } finally {
      setLoading(false);
    }
  };

  return (
    <div className="min-h-screen flex">
      <div className="hidden lg:block lg:w-1/2 relative" style={{ background: "#0c0c0c" }}>
        <div className="absolute inset-0 opacity-30 bg-cover bg-center"
             style={{ backgroundImage: "url(https://images.pexels.com/photos/4173191/pexels-photo-4173191.jpeg)" }} />
        <div className="absolute inset-0 flex items-end p-12">
          <div>
            <h2 className="font-display font-black text-4xl tracking-tighter">
              In 60 Sekunden<br/>einsatzbereit.
            </h2>
            <p className="text-zinc-400 mt-4 max-w-sm">
              Konto erstellen → Abo wählen → URL einfügen → fertig.
            </p>
          </div>
        </div>
      </div>

      <div className="flex-1 flex items-center justify-center p-6">
        <form onSubmit={submit} className="w-full max-w-md">
          <Link to="/" className="flex items-center gap-2 mb-8">
            <span className="w-7 h-7 rounded-sm flex items-center justify-center"
                  style={{ background: "var(--accent-red)" }}>
              <Bolt size={16} />
            </span>
            <span className="font-display font-black text-lg">AUTOHANDEL<span style={{color:"var(--accent-red)"}}>.</span></span>
          </Link>

          <h1 className="font-display font-black text-3xl tracking-tight">Konto erstellen</h1>
          <p className="text-zinc-400 text-sm mt-1">Kostenlos registrieren – Abo im nächsten Schritt.</p>

          <div className="mt-6 space-y-3">
            <div>
              <label className="overline">Firmenname</label>
              <input data-testid="reg-company" required value={form.company_name}
                     onChange={(e) => set("company_name", e.target.value)}
                     className="input-base w-full mt-1" placeholder="Müller Automobile GmbH" />
            </div>
            <div className="grid grid-cols-2 gap-3">
              <div>
                <label className="overline">Ansprechpartner</label>
                <input data-testid="reg-contact" value={form.contact_person}
                       onChange={(e) => set("contact_person", e.target.value)}
                       className="input-base w-full mt-1" placeholder="Max Müller" />
              </div>
              <div>
                <label className="overline">Telefon</label>
                <input data-testid="reg-phone" value={form.phone}
                       onChange={(e) => set("phone", e.target.value)}
                       className="input-base w-full mt-1" placeholder="+49 …" />
              </div>
            </div>
            <div>
              <label className="overline">E-Mail</label>
              <input data-testid="reg-email" type="email" required value={form.email}
                     onChange={(e) => set("email", e.target.value)}
                     className="input-base w-full mt-1" placeholder="info@firma.de" />
            </div>
            <div>
              <label className="overline">Passwort</label>
              <input data-testid="reg-password" type="password" required minLength={6}
                     value={form.password} onChange={(e) => set("password", e.target.value)}
                     className="input-base w-full mt-1" placeholder="min. 6 Zeichen" />
            </div>
          </div>

          <button data-testid="reg-submit" type="submit" disabled={loading}
                  className="kinetic-button w-full mt-6 py-3 rounded-sm flex items-center justify-center gap-2 disabled:opacity-60">
            {loading ? "..." : <>Konto erstellen <ArrowRight size={15} /></>}
          </button>

          <div className="mt-6 text-sm text-zinc-400 text-center">
            Schon Kunde? <Link to="/login" className="text-white hover:underline">Anmelden</Link>
          </div>
        </form>
      </div>
    </div>
  );
}
