import { Link, useNavigate } from "react-router-dom";
import { useEffect, useState } from "react";
import { ArrowRight, Bolt, Check, FileText, Send, Calendar, ShieldCheck, Sparkles, Menu, X } from "lucide-react";

const HERO_BG = "https://static.prod-images.emergentagent.com/jobs/a1ceceb6-7b86-4add-b1a2-2ba09adbd577/images/bc1425c15b101d82928a736d8d5885c8173800a2867499223e36b183b11097eb.png";
const SECTION_BG = "https://static.prod-images.emergentagent.com/jobs/a1ceceb6-7b86-4add-b1a2-2ba09adbd577/images/dd3f3682a6a7806bf9c8c9664b184e0728edf5f897bc17bdf2836ddfb7e76644.png";
const CAR_IMG = "https://images.pexels.com/photos/18320398/pexels-photo-18320398.jpeg";
const KEYS_IMG = "https://images.pexels.com/photos/4173191/pexels-photo-4173191.jpeg";

export default function Landing() {
  const nav = useNavigate();
  const [menuOpen, setMenuOpen] = useState(false);

  // Schließt das Mobile-Menü, wenn der Browser gross wird oder Escape kommt
  useEffect(() => {
    const onKey = (e) => { if (e.key === "Escape") setMenuOpen(false); };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, []);

  // Body-Scroll sperren, wenn das Menü offen ist
  useEffect(() => {
    document.body.style.overflow = menuOpen ? "hidden" : "";
    return () => { document.body.style.overflow = ""; };
  }, [menuOpen]);

  const closeMenu = () => setMenuOpen(false);

  return (
    <div className="min-h-screen text-white" style={{ background: "var(--bg-app)" }}>
      {/* NAV */}
      <header className="glass-nav fixed top-0 inset-x-0 z-50">
        <div className="max-w-7xl mx-auto px-6 h-16 flex items-center justify-between">
          <Link to="/" className="flex items-center gap-2" data-testid="brand-logo">
            <span className="w-7 h-7 rounded-sm flex items-center justify-center"
                  style={{ background: "var(--accent-red)" }}>
              <Bolt size={16} className="text-white" />
            </span>
            <span className="font-display font-black text-lg tracking-tighter">
              AUTOHANDEL<span style={{ color: "var(--accent-red)" }}>.</span>
            </span>
          </Link>

          {/* Desktop-Navigation */}
          <nav className="hidden md:flex items-center gap-8 text-sm text-zinc-300">
            <a href="#features" className="hover:text-white">Funktionen</a>
            <a href="#flow" className="hover:text-white">Ablauf</a>
            <a href="#pricing" className="hover:text-white">Preise</a>
            <a href="#contact" className="hover:text-white">Kontakt</a>
          </nav>

          {/* Desktop-CTAs */}
          <div className="hidden md:flex items-center gap-2">
            <Link to="/markt/login" data-testid="nav-b2b"
                  className="px-3 py-1.5 text-sm text-zinc-300 hover:text-white">
              B2B-Marktplatz
            </Link>
            <Link to="/fahrer/login" data-testid="nav-driver-login"
                  className="px-3 py-1.5 text-sm text-zinc-300 hover:text-white">
              Fahrer-App
            </Link>
            <Link to="/login" data-testid="nav-login" className="px-3 py-1.5 text-sm text-zinc-200 hover:text-white">
              Anmelden
            </Link>
            <Link to="/register" data-testid="nav-register"
                  className="kinetic-button px-4 py-1.5 text-sm rounded-sm">
              Jetzt starten
            </Link>
          </div>

          {/* Burger (mobile only) */}
          <button
            type="button"
            data-testid="nav-burger"
            aria-label={menuOpen ? "Menü schließen" : "Menü öffnen"}
            aria-expanded={menuOpen}
            onClick={() => setMenuOpen((v) => !v)}
            className="md:hidden w-10 h-10 -mr-2 inline-flex items-center justify-center rounded-sm text-zinc-200 hover:text-white hover:bg-white/[0.06] transition"
          >
            {menuOpen ? <X size={22} /> : <Menu size={22} />}
          </button>
        </div>
      </header>

      {/* MOBILE-MENÜ (Drawer-Overlay) */}
      <div
        data-testid="nav-mobile-overlay"
        className={`md:hidden fixed inset-0 z-40 transition-opacity ${menuOpen ? "opacity-100 pointer-events-auto" : "opacity-0 pointer-events-none"}`}
        style={{ background: "rgba(0,0,0,0.5)", backdropFilter: "blur(4px)" }}
        onClick={closeMenu}
      />
      <aside
        data-testid="nav-mobile-drawer"
        className={`md:hidden fixed top-0 right-0 z-50 h-full w-[82%] max-w-[340px] transform transition-transform duration-300 ${menuOpen ? "translate-x-0" : "translate-x-full"}`}
        style={{ background: "#0a0a0a", borderLeft: "1px solid rgba(255,255,255,0.08)" }}
      >
        <div className="h-16 px-5 flex items-center justify-between" style={{ borderBottom: "1px solid rgba(255,255,255,0.08)" }}>
          <span className="font-display font-black text-base tracking-tighter">
            MENÜ
          </span>
          <button
            type="button"
            aria-label="Menü schließen"
            onClick={closeMenu}
            className="w-9 h-9 inline-flex items-center justify-center rounded-sm text-zinc-300 hover:text-white hover:bg-white/[0.06]"
            data-testid="nav-burger-close"
          >
            <X size={20} />
          </button>
        </div>

        <nav className="px-5 py-4 flex flex-col gap-1">
          <a href="#features" onClick={closeMenu} data-testid="nav-mobile-features"
             className="px-3 py-3 rounded-sm text-[15px] text-zinc-200 hover:bg-white/[0.06] hover:text-white">
            Funktionen
          </a>
          <a href="#flow" onClick={closeMenu} data-testid="nav-mobile-flow"
             className="px-3 py-3 rounded-sm text-[15px] text-zinc-200 hover:bg-white/[0.06] hover:text-white">
            Ablauf
          </a>
          <a href="#pricing" onClick={closeMenu} data-testid="nav-mobile-pricing"
             className="px-3 py-3 rounded-sm text-[15px] text-zinc-200 hover:bg-white/[0.06] hover:text-white">
            Preise
          </a>
          <a href="#contact" onClick={closeMenu} data-testid="nav-mobile-contact"
             className="px-3 py-3 rounded-sm text-[15px] text-zinc-200 hover:bg-white/[0.06] hover:text-white">
            Kontakt
          </a>
          <Link to="/markt/login" onClick={closeMenu} data-testid="nav-mobile-b2b"
                className="px-3 py-3 rounded-sm text-[15px] text-zinc-200 hover:bg-white/[0.06] hover:text-white">
            B2B-Marktplatz
          </Link>
          <Link to="/fahrer/login" onClick={closeMenu} data-testid="nav-mobile-driver"
                className="px-3 py-3 rounded-sm text-[15px] text-zinc-200 hover:bg-white/[0.06] hover:text-white">
            Fahrer-App
          </Link>
        </nav>

        <div className="mt-2 px-5 pt-4 flex flex-col gap-2"
             style={{ borderTop: "1px solid rgba(255,255,255,0.08)" }}>
          <Link to="/login" onClick={closeMenu} data-testid="nav-mobile-login"
                className="w-full text-center px-4 py-3 rounded-sm text-[15px] text-zinc-200 hover:text-white"
                style={{ background: "rgba(255,255,255,0.04)", border: "1px solid rgba(255,255,255,0.10)" }}>
            Anmelden
          </Link>
          <Link to="/register" onClick={closeMenu} data-testid="nav-mobile-register"
                className="kinetic-button w-full text-center px-4 py-3 text-[15px] rounded-sm">
            Jetzt starten
          </Link>
        </div>
      </aside>

      {/* HERO */}
      <section className="relative pt-32 pb-24 overflow-hidden">
        <div
          className="absolute inset-0 opacity-50 bg-cover bg-center"
          style={{ backgroundImage: `url(${HERO_BG})` }}
        />
        <div className="absolute inset-0" style={{ background: "linear-gradient(180deg, rgba(10,10,10,0.55) 0%, rgba(10,10,10,0.95) 100%)" }} />
        <div className="relative max-w-7xl mx-auto px-6">
          <div className="grid lg:grid-cols-12 gap-10 items-center">
            <div className="lg:col-span-7">
              <div className="inline-flex items-center gap-2 px-3 py-1 rounded-sm mb-6"
                   style={{ background: "rgba(255,59,48,0.12)", border: "1px solid rgba(255,59,48,0.3)" }}>
                <span className="live-dot" />
                <span className="text-xs uppercase tracking-[0.2em] font-semibold" style={{ color: "var(--accent-red)" }}>
                  Made for Speed · Built for Dealers
                </span>
              </div>
              <h1 className="font-display font-black tracking-tighter text-4xl sm:text-5xl lg:text-7xl leading-[0.95]">
                URL rein.<br/>
                <span style={{ color: "var(--accent-red)" }}>Vertrag raus.</span><br/>
                Termin gemacht.
              </h1>
              <p className="mt-6 text-lg text-zinc-300 max-w-2xl leading-relaxed">
                Die schnellste Plattform für Autohändler: mobile.de-Link einfügen, Marktvergleich in unter 1&nbsp;Sekunde, Kaufvertrag in 2&nbsp;Sekunden, Versand per WhatsApp oder E-Mail – Abholtermin mit Fahrer inklusive.
              </p>

              <div className="mt-10 max-w-2xl">
                <div className="overline mb-2">Demo-Eingabe — sofort testen nach Login</div>
                <div className="tracing-beam-input rounded-md">
                  <div className="flex items-stretch gap-0 bg-[var(--bg-input)] rounded-md border" style={{ borderColor: "var(--border-default)" }}>
                    <input
                      data-testid="hero-url-input"
                      readOnly
                      value="https://m.mobile.de/fahrzeuge/details.html?id=448228023"
                      className="flex-1 bg-transparent px-4 py-3.5 text-sm font-mono text-zinc-300 outline-none truncate"
                    />
                    <button
                      data-testid="hero-cta-btn"
                      onClick={() => nav("/register")}
                      className="kinetic-button px-6 flex items-center gap-2 text-sm font-bold whitespace-nowrap rounded-r-md"
                    >
                      Vergleich starten <ArrowRight size={16} />
                    </button>
                  </div>
                </div>
                <div className="mt-3 text-xs text-zinc-500">
                  Kein Tippen. Kein Suchen. Kein Copy-Paste-Wahn.
                </div>
              </div>

              <div className="mt-10 grid grid-cols-3 max-w-xl gap-6">
                <div>
                  <div className="font-display font-black text-3xl">~1s</div>
                  <div className="overline mt-1">Filter offen</div>
                </div>
                <div>
                  <div className="font-display font-black text-3xl">2-3s</div>
                  <div className="overline mt-1">PDF erstellt</div>
                </div>
                <div>
                  <div className="font-display font-black text-3xl">0</div>
                  <div className="overline mt-1">Doppel-Eingaben</div>
                </div>
              </div>
            </div>

            <div className="lg:col-span-5 hidden lg:block">
              <div className="relative">
                <div className="aspect-[4/5] rounded-lg overflow-hidden bg-cover bg-center"
                     style={{ backgroundImage: `url(${CAR_IMG})` }}>
                  <div className="w-full h-full" style={{ background: "linear-gradient(180deg, transparent 50%, rgba(10,10,10,0.85) 100%)" }} />
                </div>
                <div className="absolute -bottom-6 -left-6 tactical-card p-4 w-64">
                  <div className="overline">live · jetzt</div>
                  <div className="font-display font-bold text-2xl mt-1">3 Händler</div>
                  <div className="text-xs text-zinc-400">prüfen dieses Fahrzeug gerade</div>
                </div>
                <div className="absolute -top-4 -right-4 tactical-card p-3">
                  <div className="overline" style={{ color: "var(--accent-red)" }}>Speed</div>
                  <div className="font-display font-black text-xl">1.0s</div>
                </div>
              </div>
            </div>
          </div>
        </div>
      </section>

      {/* FLOW */}
      <section id="flow" className="py-24 border-t" style={{ borderColor: "var(--border-default)" }}>
        <div className="max-w-7xl mx-auto px-6">
          <div className="overline mb-3">Der eine Flow, der zählt</div>
          <h2 className="font-display font-black text-3xl lg:text-5xl tracking-tighter max-w-3xl">
            Vom Inserat zum unterschriebenen Vertrag in unter 60 Sekunden.
          </h2>
          <div className="mt-12 grid md:grid-cols-3 lg:grid-cols-6 gap-4">
            {[
              { n: "01", t: "URL einfügen", d: "mobile.de Link – nichts mehr." },
              { n: "02", t: "Vergleich", d: "Filter mit deinen Regeln öffnet auto." },
              { n: "03", t: "Vertrag", d: "PDF mit allen Daten + Ausstattung." },
              { n: "04", t: "Versand", d: "WhatsApp & E-Mail in einem Klick." },
              { n: "05", t: "Speichern", d: "Sicheres Archiv pro Händler." },
              { n: "06", t: "Termin", d: "Fahrer + Abholung verknüpft." },
            ].map((s) => (
              <div key={s.n} className="tactical-card p-5">
                <div className="font-mono text-xs" style={{ color: "var(--accent-red)" }}>{s.n}</div>
                <div className="font-display font-bold text-lg mt-1">{s.t}</div>
                <div className="text-sm text-zinc-400 mt-1 leading-relaxed">{s.d}</div>
              </div>
            ))}
          </div>
        </div>
      </section>

      {/* FEATURES */}
      <section id="features" className="py-24 relative">
        <div className="absolute inset-0 opacity-20 bg-cover bg-center"
             style={{ backgroundImage: `url(${SECTION_BG})` }} />
        <div className="relative max-w-7xl mx-auto px-6">
          <div className="grid lg:grid-cols-12 gap-6">
            <div className="lg:col-span-12 mb-4">
              <div className="overline">Funktionen</div>
              <h2 className="font-display font-black text-3xl lg:text-5xl tracking-tighter mt-2">
                Alles, was ein Händler täglich braucht. Nichts, was ablenkt.
              </h2>
            </div>

            <div className="tactical-card p-7 lg:col-span-7">
              <FileText size={24} style={{ color: "var(--accent-red)" }} />
              <div className="font-display font-bold text-2xl mt-3">Ein Vertrag in 2 Sekunden</div>
              <p className="text-zinc-400 text-sm mt-2 leading-relaxed">
                Fahrzeugdaten kommen direkt aus dem Inserat. Komplette Ausstattung wird in den Vertrag übernommen. Kaufpreis bleibt bei dir.
              </p>
            </div>
            <div className="tactical-card p-7 lg:col-span-5">
              <Send size={24} style={{ color: "var(--accent-red)" }} />
              <div className="font-display font-bold text-2xl mt-3">WhatsApp & E-Mail</div>
              <p className="text-zinc-400 text-sm mt-2 leading-relaxed">
                Direkt aus der App – Telefonnummer und Mail werden automatisch übernommen. Vorlagen mit Platzhaltern.
              </p>
            </div>

            <div className="tactical-card p-7 lg:col-span-5">
              <Calendar size={24} style={{ color: "var(--accent-red)" }} />
              <div className="font-display font-bold text-2xl mt-3">Terminplaner mit Fahrer</div>
              <p className="text-zinc-400 text-sm mt-2 leading-relaxed">
                PDF + Termin in einem Klick. Datum verschoben? Neue Vertragsversion auf Knopfdruck.
              </p>
            </div>
            <div className="tactical-card p-7 lg:col-span-7 flex gap-6">
              <div className="flex-1">
                <ShieldCheck size={24} style={{ color: "var(--accent-red)" }} />
                <div className="font-display font-bold text-2xl mt-3">Mandantensicher & DSGVO-konform</div>
                <p className="text-zinc-400 text-sm mt-2 leading-relaxed">
                  Jede Händler-Datenbank ist getrennt. Verschlüsseltes Login, eine aktive Session pro Account, anonyme Live-Zähler.
                </p>
              </div>
              <div className="hidden md:block w-32 h-32 rounded-sm bg-cover bg-center"
                   style={{ backgroundImage: `url(${KEYS_IMG})` }} />
            </div>

            <div className="tactical-card p-7 lg:col-span-12">
              <Sparkles size={24} style={{ color: "var(--accent-red)" }} />
              <div className="font-display font-bold text-2xl mt-3">Live-Zähler – ohne Daten zu zeigen</div>
              <p className="text-zinc-400 text-sm mt-2 leading-relaxed max-w-3xl">
                Anonym sichtbar wie viele Händler dieselbe URL gerade prüfen. Keine Namen, keine Firmen. Daten werden nach 14 Tagen automatisch gelöscht.
              </p>
            </div>
          </div>
        </div>
      </section>

      {/* PRICING */}
      <section id="pricing" className="py-24 border-t" style={{ borderColor: "var(--border-default)" }}>
        <div className="max-w-5xl mx-auto px-6">
          <div className="text-center mb-12">
            <div className="overline">Preise · Klar · Fair</div>
            <h2 className="font-display font-black text-3xl lg:text-5xl tracking-tighter mt-3">
              Eine Lizenz. Alle Funktionen.
            </h2>
          </div>
          <div className="grid md:grid-cols-2 gap-6">
            <div className="tactical-card p-8" data-testid="pricing-monthly">
              <div className="overline">Monatsabo</div>
              <div className="mt-3 flex items-baseline gap-2">
                <span className="font-display font-black text-5xl">160 €</span>
                <span className="text-zinc-400 text-sm">/ Monat</span>
              </div>
              <p className="text-zinc-400 text-sm mt-3">Flexibel, monatlich kündbar.</p>
              <Link to="/register?plan=monthly" data-testid="cta-monthly"
                    className="block text-center w-full mt-7 px-5 py-3 rounded-sm bg-white/5 border hover:bg-white/10"
                    style={{ borderColor: "var(--border-default)" }}>
                Monatlich starten
              </Link>
              <ul className="mt-6 space-y-2 text-sm">
                {["Alle Funktionen freigeschaltet", "PDF-Archiv", "WhatsApp & E-Mail", "Terminplaner & Fahrer", "Live-Zähler"].map(t => (
                  <li key={t} className="flex items-center gap-2 text-zinc-300"><Check size={14} style={{ color: "var(--accent-green)" }} /> {t}</li>
                ))}
              </ul>
            </div>
            <div className="tactical-card p-8 relative" style={{ borderColor: "rgba(255,59,48,0.4)" }} data-testid="pricing-yearly">
              <div className="absolute -top-3 left-6 px-2 py-0.5 text-[10px] uppercase tracking-[0.2em] font-bold rounded-sm"
                   style={{ background: "var(--accent-red)" }}>Spart 120 €</div>
              <div className="overline">Jahresabo</div>
              <div className="mt-3 flex items-baseline gap-2">
                <span className="font-display font-black text-5xl">1.800 €</span>
                <span className="text-zinc-400 text-sm">/ Jahr</span>
              </div>
              <p className="text-zinc-400 text-sm mt-3">Spart 120 € im Vergleich zum Monatsabo (1.920 €).</p>
              <Link to="/register?plan=yearly" data-testid="cta-yearly"
                    className="block text-center w-full mt-7 kinetic-button px-5 py-3 rounded-sm">
                Jahresabo wählen
              </Link>
              <ul className="mt-6 space-y-2 text-sm">
                {["Alle Funktionen freigeschaltet", "Priorisierter Support", "Kein Aufpreis bei Updates", "Volle Daten-Kontrolle", "Spart 120 € pro Jahr"].map(t => (
                  <li key={t} className="flex items-center gap-2 text-zinc-300"><Check size={14} style={{ color: "var(--accent-green)" }} /> {t}</li>
                ))}
              </ul>
            </div>
          </div>
        </div>
      </section>

      {/* CONTACT FOOTER */}
      <footer id="contact" className="py-12 border-t" style={{ borderColor: "var(--border-default)" }}>
        <div className="max-w-7xl mx-auto px-6 flex flex-col md:flex-row items-center justify-between gap-4">
          <div className="text-zinc-500 text-sm">© {new Date().getFullYear()} Autohandel SaaS · Alle Rechte vorbehalten.</div>
          <div className="flex gap-6 text-sm text-zinc-400">
            <Link to="/datenschutz" className="hover:text-white">Datenschutz</Link>
            <Link to="/impressum" className="hover:text-white">Impressum</Link>
            <a href="mailto:support@autohandel.app" className="hover:text-white">Support</a>
          </div>
        </div>
      </footer>
    </div>
  );
}
