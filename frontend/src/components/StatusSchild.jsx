import { statusFarbe } from "@/lib/fahrzeugStatus";

/**
 * Farbiges Status-Schild (Bestand, Fahrzeugakte). Bricht nie um und wird nie
 * gestaucht — der Text daneben muss weichen, nicht das Schild
 * (11.09.2026: "Abholung geplant" war auf der Bestandskarte abgeschnitten).
 */
export default function StatusSchild({ status, text, className = "", ...rest }) {
  const farbe = statusFarbe(status);
  return (
    <span className={`shrink-0 whitespace-nowrap text-[11px] leading-5 px-2 rounded-md border font-medium ${className}`}
          style={{ borderColor: `${farbe}66`, color: farbe, background: `${farbe}1a` }}
          {...rest}>
      {text}
    </span>
  );
}
