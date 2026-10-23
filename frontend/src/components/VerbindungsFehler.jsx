import { useCallback, useEffect, useRef, useState } from "react";
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

/** K-21: Abstaende der automatischen Wiederholung in Sekunden; der letzte bleibt. */
export const WIEDERHOLUNG_S = [5, 10, 20];

/*
 * Pruefbericht 20.09.2026 (K-21): Der einzige Ausweg war "Erneut versuchen"
 * = Seite neu laden. Jetzt:
 *  - `onRetry` (refresh() aus dem Anmelde-Kontext): die Anmeldung wird ohne
 *    Neuladen erneut geprueft — von selbst nach 5, 10, 20 s (dann alle 20 s,
 *    mit Countdown) und auf Klick.
 *  - Kommt das Netz zurueck ("online"), sofort — auch ohne onRetry (dann
 *    Neuladen wie bisher).
 *  Ohne onRetry KEIN Takt: ein Neuladen im Takt wuerde die Meldung bei
 *  jedem Versuch ersetzen und den Zaehler zuruecksetzen.
 */
export default function VerbindungsFehler({ grund = null, onRetry = null }) {
  const antwort = Boolean(grund?.status);
  const Icon = antwort ? ServerOff : WifiOff;
  const automatisch = typeof onRetry === "function";
  const [versuch, setVersuch] = useState(0);
  const [rest, setRest] = useState(WIEDERHOLUNG_S[0]);
  const laeuft = useRef(false);

  // Nie zwei Versuche gleichzeitig (Takt + "online" + Klick).
  const erneut = useCallback(async () => {
    if (laeuft.current) return;
    laeuft.current = true;
    try {
      if (typeof onRetry === "function") await onRetry();
      else window.location.reload();
    } catch { /* den Grund meldet der Kontext selbst (verbindungsfehler) */ }
    finally { laeuft.current = false; }
  }, [onRetry]);

  useEffect(() => {
    if (!automatisch) return undefined;
    const warte = WIEDERHOLUNG_S[Math.min(versuch, WIEDERHOLUNG_S.length - 1)];
    setRest(warte);
    const takt = setInterval(() => setRest((r) => Math.max(0, r - 1)), 1000);
    const timer = setTimeout(async () => {
      await erneut();
      setVersuch((v) => v + 1);      // naechste Stufe (nach Erfolg ist die Meldung ohnehin weg)
    }, warte * 1000);
    return () => { clearInterval(takt); clearTimeout(timer); };
  }, [automatisch, versuch, erneut]);

  useEffect(() => {
    const zurueck = () => { erneut(); };
    window.addEventListener("online", zurueck);
    return () => window.removeEventListener("online", zurueck);
  }, [erneut]);

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
        <button type="button" onClick={() => { erneut(); }}
                data-testid="verbindungsfehler-erneut"
                className="kinetic-button mt-6 inline-flex items-center gap-2 px-5 py-2.5 rounded-sm text-sm"
                style={{ background: "var(--accent-red)", color: "var(--text-primary)" }}>
          <RotateCw size={14} aria-hidden="true" /> Erneut versuchen
        </button>
        {automatisch && (
          <p className="mt-3 text-xs" style={{ color: "var(--text-muted)" }}
             data-testid="verbindungsfehler-countdown" aria-live="polite">
            Nächster Versuch von selbst in {rest} s
          </p>
        )}
      </div>
    </div>
  );
}
