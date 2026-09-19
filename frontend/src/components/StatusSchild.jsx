import { statusFarbe } from "@/lib/fahrzeugStatus";

/**
 * Farbiges Status-Schild (Bestand, Fahrzeugakte). Bricht nie um und wird nie
 * gestaucht — der Text daneben muss weichen, nicht das Schild
 * (11.09.2026: "Abholung geplant" war auf der Bestandskarte abgeschnitten).
 */
export default function StatusSchild({ status, text, className = "", ...rest }) {
  const farbe = statusFarbe(status);
  // 18.09.2026: Farbe als Variable — Rahmen und Flaeche mischt die CSS-Klasse
  // (.status-schild), damit beide Designs stimmen. Vorher wurde an den Hex-Wert
  // "66"/"1a" angehaengt, was mit Token nicht mehr geht.
  return (
    <span className={`status-schild shrink-0 whitespace-nowrap text-[11px] leading-5 px-2 rounded-md border font-medium ${className}`}
          style={{ "--st": farbe }}
          {...rest}>
      {text}
    </span>
  );
}
