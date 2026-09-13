import { useEffect, useState } from "react";
import { Link, useSearchParams } from "react-router-dom";
import axios from "axios";
import { API_BASE } from "@/lib/api";
import { errMsg } from "@/lib/api";
import { toast } from "sonner";
import { Bolt, Check, ArrowRight } from "lucide-react";

/**
 * Zugangs-Anfrage (Beschluss 09/2026): Firmen registrieren sich nicht mehr
 * selbst. Sie stellen hier eine Anfrage — der Betreiber meldet sich, legt
 * das Firmen-Konto samt Sucher-Zugängen an und schaltet frei.
 *
 * Kontonummer (13.09.2026): Auch Zwischenhändler (?art=kaeufer) und Fahrer
 * (?art=fahrer) fragen hier an — Konten legt ausschließlich der Betreiber
 * an. Zwischenhändler können die USt-IdNr. angeben. Die E-Mail bleibt
 * Pflicht: über sie bekommt der Anfragende Kontonummer und Passwort.
 *
 * Kontonummer (13.09.2026, Gegenpruefung): Die Pflicht-Checkbox
 * (Unternehmer, AGB, Datenschutzerklärung — AGB §1) gilt für ALLE Arten,
 * nicht nur für Zwischenhändler; der Server hält den Zeitpunkt fest.
 */
const ARTEN = [
  { key: "firma", label: "Autohändler-Firma" },
  { key: "kaeufer", label: "Zwischenhändler" },
  { key: "fahrer", label: "Fahrer" },
];
const artAus = (wert) => (ARTEN.some((a) => a.key === wert) ? wert : "firma");

const feldCls = "w-full mt-1 px-3 py-2.5 rounded-sm bg-[var(--bg-input)] border text-sm outline-none";
const feldStil = { borderColor: "var(--border-default)" };

export default function Anfrage() {
  const [sp, setSp] = useSearchParams();
  const art = artAus(sp.get("art"));
  const [f, setF] = useState({
    company_name: "", contact_person: "", email: "", phone: "",
    sucher_anzahl: 1, message: "", ust_id: "",
  });
  const [gewerblich, setGewerblich] = useState(false);
  const [busy, setBusy] = useState(false);
  const [done, setDone] = useState(false);
  // Ob der Marktplatz-Zugang Geld kostet, sagt der Server (kein fester Preis im Text).
  const [marktplatzKostenlos, setMarktplatzKostenlos] = useState(true);

  useEffect(() => {
    if (art !== "kaeufer") return undefined;
    let aktiv = true;
    axios.get(`${API_BASE}/payments/config`).then((r) => {
      if (aktiv && r.data) setMarktplatzKostenlos(r.data.marktplatz_kostenlos !== false);
    }).catch(() => {});
    return () => { aktiv = false; };
  }, [art]);

  const set = (k) => (e) => setF({ ...f, [k]: e.target.value });
  const artWaehlen = (key) => {
    const neu = new URLSearchParams(sp);
    if (key === "firma") neu.delete("art"); else neu.set("art", key);
    setSp(neu, { replace: true });
  };

  const submit = async (e) => {
    e.preventDefault();
    if (!gewerblich) {
      toast.error("Bitte bestätige die gewerbliche Nutzung und die AGB");
      return;
    }
    setBusy(true);
    try {
      await axios.post(`${API_BASE}/zugang-anfrage`, {
        art,
        company_name: f.company_name, contact_person: f.contact_person,
        email: f.email, phone: f.phone, message: f.message,
        sucher_anzahl: art === "firma" ? (parseInt(f.sucher_anzahl, 10) || 0) : 0,
        gewerblich_bestaetigt: gewerblich,
        ...(art === "kaeufer" ? { ust_id: f.ust_id.trim() } : {}),
      });
      setDone(true);
    } catch (err) {
      toast.error(errMsg(err, "Anfrage konnte nicht gesendet werden"));
    } finally {
      setBusy(false);
    }
  };

  const anmeldeLink = art === "kaeufer" ? "/markt/login" : art === "fahrer" ? "/fahrer/login" : "/login";

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
              Danke! Wir melden uns zeitnah bei dir und richten deinen Zugang ein.
              Kontonummer und Passwort bekommst du von uns.
            </p>
            <Link to="/" className="inline-block mt-6 text-sm text-zinc-300 hover:text-white">Zur Startseite</Link>
          </div>
        ) : (
          <form onSubmit={submit} className="tactical-card p-8" data-testid="anfrage-form">
            <div className="flex flex-wrap gap-1.5 mb-4" role="tablist" aria-label="Art des Zugangs">
              {ARTEN.map((a) => (
                <button key={a.key} type="button" role="tab" aria-selected={art === a.key}
                        onClick={() => artWaehlen(a.key)} data-testid={`anfrage-art-${a.key}`}
                        className={`px-3 py-1.5 rounded-sm text-xs border ${art === a.key ? "bg-white/10 text-white font-semibold" : "text-zinc-400 hover:text-white"}`}
                        style={feldStil}>
                  {a.label}
                </button>
              ))}
            </div>
            <h1 className="font-display font-black text-2xl tracking-tighter">Zugang anfragen</h1>
            <p className="text-zinc-400 text-sm mt-2 leading-relaxed">
              {art === "firma" && (
                <>
                  Kein Formular-Konto, kein Zahlungsanbieter: Du fragst an, wir
                  melden uns, legen dein Firmen-Konto und deine Sucher an und
                  schalten frei. Sucher-Zugang: 150&nbsp;€/Monat oder
                  1.500&nbsp;€/Jahr je Nutzer, Abrechnung per Rechnung.
                </>
              )}
              {art === "kaeufer" && (
                <>
                  Für gewerbliche Zwischenhändler: Du fragst an, wir prüfen die
                  Angaben und legen dein Marktplatz-Konto an.
                  {marktplatzKostenlos ? " Der Zugang zum Marktplatz ist kostenlos." : " Die aktuellen Preise siehst du auf der Startseite."}
                </>
              )}
              {art === "fahrer" && (
                <>
                  Für Abholfahrer: Du fragst an, wir legen dein Fahrer-Konto an.
                  Die Fahrer-App ist kostenlos; die Firma verknüpft dich danach
                  über deine Fahrer-ID.
                </>
              )}
              {" "}Kontonummer und Passwort bekommst du von uns.
            </p>

            <div className="mt-6 space-y-4">
              <div>
                <label className="overline">{art === "fahrer" ? "Firma / Auftraggeber *" : "Firma *"}</label>
                <input required value={f.company_name} onChange={set("company_name")}
                       data-testid="anfrage-firma"
                       placeholder={art === "fahrer" ? "Für wen fährst du? (oder dein Name)" : "Autohaus Beispiel GmbH"}
                       className={feldCls} style={feldStil} />
              </div>
              <div className="grid grid-cols-2 gap-3">
                <div>
                  <label className="overline">{art === "fahrer" ? "Vor- und Nachname *" : "Ansprechpartner *"}</label>
                  <input required value={f.contact_person} onChange={set("contact_person")}
                         data-testid="anfrage-name" placeholder="Vor- und Nachname"
                         className={feldCls} style={feldStil} />
                </div>
                <div>
                  <label className="overline">Telefon</label>
                  <input value={f.phone} onChange={set("phone")}
                         data-testid="anfrage-telefon" placeholder="+49 …"
                         className={feldCls} style={feldStil} />
                </div>
              </div>
              <div>
                <label className="overline">E-Mail *</label>
                <input required type="email" value={f.email} onChange={set("email")}
                       data-testid="anfrage-email" placeholder="name@firma.de"
                       className={feldCls} style={feldStil} />
                <div className="text-xs text-zinc-500 mt-1">
                  Nur für unsere Rückmeldung — angemeldet wird später mit der Kontonummer.
                </div>
              </div>
              {art === "firma" && (
                <div>
                  <label className="overline">Gewünschte Sucher-Zugänge</label>
                  <input type="number" min="0" max="50" value={f.sucher_anzahl}
                         onChange={set("sucher_anzahl")} data-testid="anfrage-sucher"
                         className={feldCls} style={feldStil} />
                  <div className="text-xs text-zinc-500 mt-1">
                    Wie viele Mitarbeiter sollen suchen &amp; vergleichen? Lässt sich später jederzeit erweitern.
                  </div>
                </div>
              )}
              {art === "kaeufer" && (
                <div>
                  <label className="overline">USt-IdNr. oder Handelsregister-Nr.</label>
                  <input value={f.ust_id} onChange={set("ust_id")} maxLength={40}
                         data-testid="anfrage-ust-id" placeholder="z. B. DE123456789 (optional)"
                         className={feldCls} style={feldStil} />
                </div>
              )}
              <label className="flex items-start gap-3 text-sm text-zinc-300 cursor-pointer select-none">
                <input type="checkbox" checked={gewerblich}
                       onChange={(e) => setGewerblich(e.target.checked)}
                       data-testid="anfrage-b2b"
                       className="mt-0.5 h-4 w-4 shrink-0"
                       style={{ accentColor: "var(--accent-red)" }} />
                <span>
                  {art === "fahrer"
                    ? "Ich nutze die Fahrer-App beruflich — als selbstständiger Unternehmer oder im Auftrag eines Unternehmens —"
                    : art === "kaeufer"
                      ? "Ich handle als Unternehmer/gewerblicher Kfz-Händler (B2B),"
                      : "Ich handle als Unternehmer (B2B) für die oben genannte Firma,"}
                  {" "}akzeptiere die{" "}
                  <Link to="/agb" target="_blank" rel="noopener noreferrer"
                        className="text-white font-semibold underline">AGB</Link>{" "}
                  und habe die{" "}
                  <Link to="/datenschutz" target="_blank" rel="noopener noreferrer"
                        className="text-white font-semibold underline">Datenschutzerklärung</Link>{" "}
                  zur Kenntnis genommen. *
                </span>
              </label>
              <div>
                <label className="overline">Nachricht</label>
                <textarea rows={3} value={f.message} onChange={set("message")}
                          data-testid="anfrage-nachricht" placeholder="Kurz zu euch: Standort, Team, was ihr braucht …"
                          className={`${feldCls} resize-none`} style={feldStil} />
              </div>
            </div>

            <button type="submit" disabled={busy} data-testid="anfrage-senden"
                    className="kinetic-button w-full mt-6 py-3 rounded-sm flex items-center justify-center gap-2 disabled:opacity-60">
              {busy ? "Sende…" : <>Anfrage senden <ArrowRight size={15} /></>}
            </button>
            <div className="mt-4 text-center text-xs text-zinc-500">
              Schon freigeschaltet? <Link to={anmeldeLink} className="text-zinc-300 hover:text-white">Anmelden</Link>
            </div>
          </form>
        )}
      </div>
    </div>
  );
}
