import { useEffect, useState } from "react";
import { Navigate, useLocation } from "react-router-dom";
import { useAuth } from "@/context/AuthContext";
import { bereichVonPfad, darfBereich, startseite } from "@/lib/rollen";
import VerbindungsFehler from "@/components/VerbindungsFehler";

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
  const { user, subscription, loading, verbindungsfehler, refresh } = useAuth();
  const loc = useLocation();
  if (loading) return <div className="min-h-screen flex items-center justify-center text-zinc-500">Lade…</div>;
  // Runde 22 (11.09.2026): Server nicht erreichbar (Funkloch, Rollout) ->
  // Anmeldung bleibt, "Keine Verbindung" statt Umleitung auf /login.
  // Pruefbericht 20.09.2026 (K-21): refresh() als Wiederholung — die Meldung
  // versucht es dann von selbst erneut (5/10/20 s, "online"), ohne Neuladen.
  if (!user && verbindungsfehler) return <VerbindungsFehler grund={verbindungsfehler} onRetry={refresh} />;
  // U-151: Query und Fragment gehoeren zum Rueckweg (z. B. ?tab=…, #termin-…).
  if (!user) return <Navigate to={`/login?next=${encodeURIComponent(loc.pathname + loc.search + loc.hash)}`} replace />;

  // adminOnly bleibt als ausdrueckliche Kennzeichnung erhalten; die
  // eigentliche Pruefung macht der Bereichsabgleich.
  const bereich = adminOnly ? "admin" : bereichVonPfad(loc.pathname);
  if (!darfBereich(user, bereich)) return <Navigate to={startseite(user)} replace />;

  if (requireSub && !subscription?.active) return <AboNachpruefen />;
  return children;
};

/**
 * Rollenprüfung 22.09.2026 (RP-006/RP-105/RP-109, Welle 2): Der Abo-Stand im
 * Anmelde-Kontext stammt vom Laden der App. Hat der Betreiber inzwischen
 * freigeschaltet, schickte die Sperre trotzdem nach /abo — bis zum Neuladen.
 * Jetzt wird vor der Umleitung EINMAL frisch nachgefragt: ist das Abo aktiv,
 * rendert ProtectedRoute danach die Seite (der Kontext ist neu), sonst geht
 * es wie bisher nach /abo. Kein Kreislauf: je Aufruf genau ein refresh().
 */
function AboNachpruefen() {
  const { refresh } = useAuth();
  const [geprueft, setGeprueft] = useState(false);
  useEffect(() => {
    let aktiv = true;
    Promise.resolve(typeof refresh === "function" ? refresh() : null)
      .catch(() => null)
      .finally(() => { if (aktiv) setGeprueft(true); });
    return () => { aktiv = false; };
  }, [refresh]);
  if (!geprueft) {
    return <div className="min-h-screen flex items-center justify-center text-zinc-500">Lade…</div>;
  }
  return <Navigate to="/abo" replace />;
}
