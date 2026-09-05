import { Navigate, useLocation } from "react-router-dom";
import { useAuth } from "@/context/AuthContext";
import { bereichVonPfad, darfBereich, startseite } from "@/lib/rollen";

/*
 * Zugangssperre fuer die geschuetzten Seiten.
 *
 * Der Bereich wird aus der Adresse abgeleitet, nicht von Hand gesetzt —
 * eine neue Route ist damit automatisch geschuetzt. Wer im falschen
 * Bereich landet, wird auf seine eigene Startseite geschickt, statt eine
 * halb fremde Seite zu sehen.
 *
 * Vorher gab es nur die Sperre "Admin-Seite fuer Nicht-Admins". Der
 * umgekehrte Fall fehlte: ein Admin konnte /app/bestand oeffnen, sah die
 * Haendler-Seite mit der Admin-Seitenleiste, und die API antwortete
 * "Nur fuer Haendler-Accounts" (Befund 05.09.2026).
 */
export const ProtectedRoute = ({ children, requireSub = true, adminOnly = false }) => {
  const { user, subscription, loading } = useAuth();
  const loc = useLocation();
  if (loading) return <div className="min-h-screen flex items-center justify-center text-zinc-500">Lade…</div>;
  if (!user) return <Navigate to={`/login?next=${encodeURIComponent(loc.pathname)}`} replace />;

  // adminOnly bleibt als ausdrueckliche Kennzeichnung erhalten; die
  // eigentliche Pruefung macht der Bereichsabgleich.
  const bereich = adminOnly ? "admin" : bereichVonPfad(loc.pathname);
  if (!darfBereich(user, bereich)) return <Navigate to={startseite(user)} replace />;

  if (requireSub && !subscription?.active) return <Navigate to="/abo" replace />;
  return children;
};
