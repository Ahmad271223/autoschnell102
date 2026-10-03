import { useFeatures } from "@/lib/features";
import DemnaechstVerfuegbar from "@/components/DemnaechstVerfuegbar";

/**
 * Zeigt den Inhalt nur, wenn der Server den Bereich freigeschaltet hat
 * (Go-Live-Schalter 15.09.2026) — sonst "Demnaechst verfuegbar".
 */
export default function FeatureGate({ feature = "marktplatz", bereich, zurueck = "/", children }) {
  const f = useFeatures();
  if (!f.geladen) return null;
  if (!f[feature]) return <DemnaechstVerfuegbar bereich={bereich} zurueck={zurueck} />;
  return children;
}
