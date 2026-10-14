import AbholFoto from "@/components/AbholFoto";
import { datumZeit } from "@/lib/markt";

/**
 * Fotos des Fahrers zu EINER Markierung (Schaden oder Lackdicke-Messung) für den Chef — Wunsch Ahmad
 * 06.10.2026. Der Server liefert nur Fotos innerhalb der Sichtfrist (7 Tage ab dem Hochladen); danach
 * sind sie hier einfach nicht mehr da (gespeichert bleiben sie trotzdem).
 */
export default function SchadenFotos({ protokollId, schadenId, fotos, size = 56 }) {
  const eigene = (fotos || []).filter((f) => f.schaden_id === schadenId);
  if (!protokollId || !eigene.length) return null;
  const bis = eigene.map((f) => f.sichtbar_bis).filter(Boolean).sort()[0];
  return (
    <div className="mt-1 mb-1.5 flex flex-wrap items-center gap-1.5" data-testid={`schadenfotos-${schadenId}`}>
      {eigene.map((f) => (
        <AbholFoto key={f.id} pfad={`/protocols/${protokollId}/schaden-fotos/${f.id}`} size={size}
                   label="Foto des Fahrers" />
      ))}
      {bis && (
        <span className="text-[10px] text-zinc-500" data-testid={`schadenfotos-bis-${schadenId}`}>
          sichtbar bis {datumZeit(bis)}
        </span>
      )}
    </div>
  );
}
