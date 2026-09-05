/*
 * Wer gehoert wohin? Eine einzige Quelle der Wahrheit fuer die Trennung
 * der Bereiche im Browser.
 *
 * Anlass (05.09.2026): Ein Super-Admin landete auf "Fahrzeugbestand &
 * Weiterverkauf" — einer reinen Haendler-Seite. Die Seitenleiste zeigte
 * dabei den Admin-Bereich, der Inhalt die Haendler-Seite, und die API
 * antwortete "Nur fuer Haendler-Accounts". Ein halb-halb-Bildschirm, der
 * niemanden weiterbringt.
 *
 * Der Weg dorthin: Wer abgemeldet wird, landet auf
 * /login?next=/app/bestand. Meldet sich dort ein ANDERER Nutzer an,
 * folgte die Anmeldung blind diesem next — auch wenn das Ziel gar nicht
 * zu seiner Rolle gehoert. Und die Routen-Sperre kannte nur "Admin-Seite
 * fuer Nicht-Admins verboten", nicht den umgekehrten Fall.
 *
 * Der Server hat nie Daten herausgegeben — er lehnte korrekt mit 403 ab.
 * Es war eine Anzeige-Vermischung. Trotzdem gilt: jeder sieht nur seinen
 * eigenen Bereich.
 */

// Die vier Bereiche der Anwendung.
export const BEREICH_FIRMA = "firma";     // /app/*   Haendler-Chef und Sucher
export const BEREICH_ADMIN = "admin";     // /admin/* Betreiber
export const BEREICH_MARKT = "markt";     // /markt/* Zwischenhaendler
export const BEREICH_FAHRER = "fahrer";   // /fahrer/* Fahrer (eigener Token)

/** Zu welchem Bereich gehoert diese Adresse? null = frei zugaenglich. */
export function bereichVonPfad(pfad) {
  const p = String(pfad || "");
  if (p === "/admin" || p.startsWith("/admin/")) return BEREICH_ADMIN;
  if (p === "/app" || p.startsWith("/app/")) return BEREICH_FIRMA;
  // Die Abo-Seiten gehoeren fachlich zur Firma: ein Betreiber hat kein Abo.
  if (p === "/abo" || p.startsWith("/abo/")) return BEREICH_FIRMA;
  if (p === "/markt" || p.startsWith("/markt/")) return BEREICH_MARKT;
  if (p === "/fahrer" || p.startsWith("/fahrer/")) return BEREICH_FAHRER;
  return null;
}

/** In welchen Bereich gehoert dieses Konto? */
export function bereichVonRolle(user) {
  if (!user) return null;
  if (user.role === "admin" || user.is_super_admin) return BEREICH_ADMIN;
  if (user.role === "b2b_buyer") return BEREICH_MARKT;
  return BEREICH_FIRMA;                   // dealer und sucher
}

/** Wo gehoert dieses Konto nach der Anmeldung hin? */
export function startseite(user) {
  if (!user) return "/login";
  if (user.role === "admin" || user.is_super_admin) return "/admin";
  if (user.role === "b2b_buyer") return "/markt";
  if (user.role === "sucher") return "/app/vergleich";
  return "/app/bestand";                  // Haendler-Chef
}

/** Darf dieses Konto diesen Bereich betreten? */
export function darfBereich(user, bereich) {
  if (!bereich) return true;              // oeffentliche Seite
  return bereichVonRolle(user) === bereich;
}

/**
 * Ein ?next=... aus der Adresszeile pruefen.
 *
 * Gibt das Ziel zurueck, wenn es zur Rolle passt — sonst die Startseite
 * des Kontos. Fremde Adressen (http://…, //…) werden immer verworfen:
 * sonst waere die Anmeldung eine Weiterleitung auf beliebige Seiten.
 */
export function sicheresZiel(user, next) {
  const heim = startseite(user);
  if (!next || typeof next !== "string") return heim;
  if (!next.startsWith("/") || next.startsWith("//")) return heim;
  const bereich = bereichVonPfad(next);
  if (bereich && !darfBereich(user, bereich)) return heim;
  return next;
}
