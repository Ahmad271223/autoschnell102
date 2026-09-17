import { Link } from "react-router-dom";
import { Bolt, KeyRound } from "lucide-react";

/**
 * Kontonummer (13.09.2026): Ein neues Passwort vergibt ausschliesslich der
 * Betreiber — es gibt keinen Link per E-Mail mehr (viele Konten haben gar
 * keine E-Mail-Adresse). Diese Seite ist nur noch ein Hinweis; kein
 * Formular, kein API-Aufruf. Wunsch Ahmad 17.09.2026: Telefon und E-Mail
 * des Inhabers vorerst entfernt.
 */
export default function PasswortVergessen() {
  return (
    <div className="min-h-screen flex items-center justify-center p-6" style={{ background: "var(--bg-deep)" }}>
      <div className="w-full max-w-sm" data-testid="passwort-vergessen-hinweis">
        <Link to="/" className="flex items-center gap-2 mb-10">
          <span className="w-7 h-7 rounded-sm flex items-center justify-center"
                style={{ background: "var(--accent-red)" }}>
            <Bolt size={16} />
          </span>
          <span className="font-display font-black text-lg text-white">
            AUTOHANDEL<span style={{ color: "var(--accent-red)" }}>.</span>
          </span>
        </Link>

        <KeyRound size={34} className="text-zinc-300 mb-4" />
        <h1 className="font-display font-black text-3xl tracking-tight text-white">Passwort vergessen</h1>
        <p className="text-zinc-400 text-sm mt-3 leading-relaxed">
          Ein neues Passwort vergibt der Betreiber. Melde dich bitte mit deiner
          Kontonummer bei deinem Ansprechpartner bei AutoSchnell — wir setzen
          das Passwort neu und geben es dir direkt weiter. Eine vorübergehende
          Sperre nach vielen Fehlversuchen wird dabei ebenfalls aufgehoben.
        </p>

        <div className="mt-8 text-sm text-zinc-400 space-y-2">
          <div><Link to="/login" className="text-white hover:underline">Zurück zur Anmeldung</Link></div>
        </div>
      </div>
    </div>
  );
}
