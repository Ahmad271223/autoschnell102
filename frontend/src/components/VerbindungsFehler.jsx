import { RotateCw, ServerOff, WifiOff } from "lucide-react";

/*
 * Runde 22 (11.09.2026): Ganzseitige Meldung, wenn beim Laden der
 * Anmeldung (/auth/me bzw. /driver/me) keine Antwort kommt — Funkloch,
 * Timeout, 502/520 waehrend eines Rollouts.
 *
 * Vorher wurde in diesem Fall der Token geloescht und auf die
 * Login-Seite umgeleitet. Fahrer und Sucher mussten sich neu anmelden,
 * und wegen Single-Session flog dabei ihre andere Sitzung raus. Jetzt
 * bleibt der Token liegen; "Erneut versuchen" laedt die Seite neu und
 * die Anmeldung wird mit demselben Token noch einmal geprueft.
 *
 * Runde 22 (11.09.2026, Gegenpruefung): Hat der Server geantwortet
 * (Wartungsmodus 503, Serverfehler 500, 429), liegt es NICHT am Netz des
 * Nutzers — dann nicht "Prüfe die Internetverbindung", sondern die
 * Meldung des Servers. Einen Link "Zur Anmeldung" gibt es bewusst nicht:
 * eine neue Anmeldung wuerde wegen Single-Session die andere Sitzung
 * beenden, und bei gestoertem Server scheitert sie ohnehin.
 *
 * Farben nur ueber die Design-Variablen — lesbar in hellem und dunklem
 * Design.
 */

/** Grund aus einem axios-Fehler fuer die Meldung: status fehlt (null),
 *  wenn gar keine Antwort kam; detail nur, wenn der Server Text schickt
 *  (ein 502 vom Proxy liefert HTML — das zeigen wir nicht an). */
export function verbindungsGrund(e) {
  const status = e?.response?.status || null;
  const d = e?.response?.data?.detail;
  return { status, detail: typeof d === "string" ? d : "" };
}

export default function VerbindungsFehler({ grund = null }) {
  const antwort = Boolean(grund?.status);
  const Icon = antwort ? ServerOff : WifiOff;
  return (
    <div data-testid="verbindungsfehler" role="alert"
         className="min-h-screen flex items-center justify-center px-6"
         style={{ background: "var(--bg-app)", color: "var(--text-primary)" }}>
      <div className="w-full max-w-sm text-center">
        <span className="mx-auto mb-5 w-12 h-12 rounded-full flex items-center justify-center border"
              style={{ borderColor: "var(--border-default)" }}>
          <Icon size={20} style={{ color: "var(--text-secondary)" }} aria-hidden="true" />
        </span>
        <h1 className="text-lg font-bold tracking-tight">
          {antwort ? "Server gerade nicht verfügbar" : "Keine Verbindung zum Server"}
        </h1>
        {antwort && grund.detail && (
          <p className="mt-2 text-sm leading-relaxed" data-testid="verbindungsfehler-detail">
            {grund.detail}
          </p>
        )}
        <p className="mt-2 text-sm leading-relaxed" style={{ color: "var(--text-secondary)" }}>
          {antwort
            ? "Deine Anmeldung bleibt erhalten. Bitte versuche es gleich noch einmal."
            : "Deine Anmeldung bleibt erhalten. Prüfe die Internetverbindung."}
        </p>
        <button type="button" onClick={() => window.location.reload()}
                data-testid="verbindungsfehler-erneut"
                className="kinetic-button mt-6 inline-flex items-center gap-2 px-5 py-2.5 rounded-sm text-sm"
                style={{ background: "var(--accent-red)", color: "#fff" }}>
          <RotateCw size={14} aria-hidden="true" /> Erneut versuchen
        </button>
      </div>
    </div>
  );
}
