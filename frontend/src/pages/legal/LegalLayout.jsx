import { Link } from "react-router-dom";
import { Bolt, AlertTriangle } from "lucide-react";

/** Gemeinsames Layout für Impressum/Datenschutz. */
export default function LegalLayout({ title, children, draft = false }) {
  return (
    <div className="min-h-screen" style={{ background: "#0a0a0a", color: "#e4e4e7" }}>
      <div className="max-w-3xl mx-auto px-6 py-10">
        <Link to="/" className="inline-flex items-center gap-2 mb-10">
          <span className="w-7 h-7 rounded-sm flex items-center justify-center"
                style={{ background: "var(--accent-red)" }}>
            <Bolt size={16} className="text-white" />
          </span>
          <span className="font-display font-black text-lg text-white">
            AUTOHANDEL<span style={{ color: "var(--accent-red)" }}>.</span>
          </span>
        </Link>

        {draft && (
          <div className="mb-8 rounded-xl border px-4 py-3 flex items-start gap-2 text-sm"
               style={{ borderColor: "#f59e0b55", background: "#f59e0b14", color: "#fbbf24" }}>
            <AlertTriangle size={16} className="mt-0.5 shrink-0" />
            <span>
              <b>Entwurf:</b> Die Angaben in [eckigen Klammern] müssen vor dem
              Live-Gang mit den echten Firmendaten ersetzt werden. Für die
              Datenschutzerklärung empfiehlt sich zusätzlich eine anwaltliche
              Prüfung oder ein Generator (z.B. e-recht24, activeMind).
            </span>
          </div>
        )}

        <h1 className="font-display font-black text-3xl tracking-tight text-white mb-8">{title}</h1>
        <div className="legal-content space-y-6 text-[15px] leading-relaxed text-zinc-300">
          {children}
        </div>

        <div className="mt-14 pt-6 border-t text-sm text-zinc-500 flex gap-5"
             style={{ borderColor: "rgba(255,255,255,0.08)" }}>
          <Link to="/" className="hover:text-white">Startseite</Link>
          <Link to="/impressum" className="hover:text-white">Impressum</Link>
          <Link to="/datenschutz" className="hover:text-white">Datenschutz</Link>
        </div>
      </div>
    </div>
  );
}

export function H2({ children }) {
  return <h2 className="font-display font-bold text-xl text-white pt-4">{children}</h2>;
}
