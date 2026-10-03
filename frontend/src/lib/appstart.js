/*
 * Einstieg der installierten App (manifest.json: start_url "/start").
 *
 * Wer das AutoSchnell-Symbol auf Taskleiste oder Startbildschirm antippt,
 * soll nicht erst auf der Werbe-Startseite landen: noch angemeldet -> direkt
 * auf die eigene Startseite (Sucher: Vergleich, Chef: Bestand, Fahrer:
 * Fahrten, Marktplatz). Nicht angemeldet -> auf die Anmeldung, die zuletzt
 * benutzt wurde (Firma, Fahrer oder Marktplatz). Ist gar nichts bekannt
 * (neues Geraet; iPhone/iPad: die Home-Bildschirm-App hat einen eigenen
 * Speicher, getrennt von Safari), liefert startZiel null und /start zeigt
 * die Auswahl Firma / Fahrer / Marktplatz — vorher landeten Fahrer so auf
 * dem Firmen-Login (Gegenpruefung 11.09.2026).
 */
import { startseite } from "@/lib/rollen";
import { TOKEN_APP, TOKEN_FAHRER, TOKEN_KAEUFER } from "@/lib/sitzung";

export function startZiel({ user, driver, buyer, letzte } = {}) {
  const ziele = {
    [TOKEN_APP]: user ? startseite(user) : null,
    [TOKEN_FAHRER]: driver ? "/fahrer" : null,
    [TOKEN_KAEUFER]: buyer ? "/markt" : null,
  };
  // Mehrere Anmeldungen im selben Browser: die zuletzt benutzte gewinnt.
  if (letzte && ziele[letzte]) return ziele[letzte];
  const angemeldet = [TOKEN_APP, TOKEN_FAHRER, TOKEN_KAEUFER].find((k) => ziele[k]);
  if (angemeldet) return ziele[angemeldet];
  if (letzte === TOKEN_FAHRER) return "/fahrer/login";
  if (letzte === TOKEN_KAEUFER) return "/markt/login";
  if (letzte === TOKEN_APP) return "/login";
  return null;
}
